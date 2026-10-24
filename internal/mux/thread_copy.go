package mux

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

var (
	threadIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	linkIDPattern   = regexp.MustCompile(`_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$`)
)

var historyTables = []string{
	"thread_turns",
	"thread_items",
	"thread_history_projection_state",
	"thread_realtime_items",
}

// projectionStream is the id under which Codex projects a rollout file: the
// thread id for the original rollout, and the link id for a continuation
// file (`<thread>_<link>.jsonl`) that a revert or edit starts.
func projectionStream(rollout, threadID string) string {
	if match := linkIDPattern.FindStringSubmatch(filepath.Base(rollout)); match != nil {
		return match[1]
	}
	return threadID
}

// syncThreadCopy makes the target account's copy of a thread match the
// source's before the thread runs there. Codex 0.153 keeps a per-account
// history projection and may continue a thread in a new rollout link, so a
// copy that stayed behind on the target would resume without the latest
// turns. Every rollout file is hard-linked into the target's sessions tree,
// the target's row is pointed at the source's current rollout, and the
// projection rows of every stream are copied. The files are shared, so the
// derived rows apply unchanged. A forked thread keeps its history in the
// thread it was forked from, so that thread's history is brought along too.
// Existing ancestor files, index paths and projections stay with their owner;
// only missing ancestor streams are imported.
func syncThreadCopy(sourceHome, targetHome, threadID string) error {
	if !threadIDPattern.MatchString(threadID) {
		return fmt.Errorf("unexpected thread id %q", threadID)
	}
	var err error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Second)
		}
		if err = copyThread(sourceHome, targetHome, threadID, map[string]struct{}{}); err == nil || !strings.Contains(err.Error(), "database is locked") {
			return err
		}
	}
	return err
}

func copyThread(sourceHome, targetHome, threadID string, copied map[string]struct{}) error {
	if _, done := copied[threadID]; done {
		return nil
	}
	ancestor := len(copied) > 0
	copied[threadID] = struct{}{}
	row, err := querySQLite(
		stateDatabase(sourceHome),
		fmt.Sprintf(
			"select rollout_path || char(9) || history_mode from threads where id = '%s'",
			threadID,
		),
	)
	if err != nil {
		return err
	}
	path, mode, found := strings.Cut(strings.TrimSpace(row), "\t")
	if !found {
		if len(copied) > 1 {
			// A history base the source never indexed still resumes by
			// file, so its rollouts and streams travel without a row.
			return copyStreams(sourceHome, targetHome, threadID)
		}
		return fmt.Errorf("source does not index thread %s", threadID)
	}
	var streams []string
	for _, rollout := range threadRollouts(sourceHome, threadID) {
		_, created, err := shareRolloutIntoHome(rollout, targetHome, ancestor)
		if err != nil {
			return err
		}
		if !ancestor || created {
			streams = append(streams, projectionStream(rollout, threadID))
		}
		if base := historyBaseThread(rollout); base != "" && base != threadID {
			owner := rolloutOwner(sourceHome, base)
			if owner == threadID {
				continue
			}
			if err := copyThread(sourceHome, targetHome, owner, copied); err != nil {
				return fmt.Errorf("forked-from thread %s: %w", owner, err)
			}
		}
	}
	current, created, err := shareRolloutIntoHome(path, targetHome, ancestor)
	if err != nil {
		return err
	}
	if created {
		streams = append(streams, projectionStream(path, threadID))
	}
	if err := upsertThreadRow(stateDatabase(sourceHome), stateDatabase(targetHome), threadID, current, mode, !ancestor); err != nil {
		return err
	}
	return copyProjection(sourceHome, targetHome, streams)
}

// copyStreams imports missing ancestor streams without touching the target's
// existing files, projections or index.
func copyStreams(sourceHome, targetHome, threadID string) error {
	var streams []string
	for _, rollout := range threadRollouts(sourceHome, threadID) {
		_, created, err := shareRolloutIntoHome(rollout, targetHome, true)
		if err != nil {
			return err
		}
		if created {
			streams = append(streams, projectionStream(rollout, threadID))
		}
	}
	return copyProjection(sourceHome, targetHome, streams)
}

func copyProjection(sourceHome, targetHome string, streams []string) error {
	if len(streams) == 0 {
		return nil
	}
	sourceHistory := historyDatabase(sourceHome)
	if !fileExists(sourceHistory) {
		return nil
	}
	if err := ensureHistorySchema(sourceHistory, historyDatabase(targetHome)); err != nil {
		return err
	}
	script := []string{
		"PRAGMA busy_timeout=10000;",
		fmt.Sprintf("attach database '%s' as source;", escapeSQLLiteral(sourceHistory)),
		"begin immediate;",
	}
	for _, table := range historyTables {
		for _, stream := range streams {
			script = append(script,
				fmt.Sprintf("delete from %s where thread_id = '%s';", table, stream),
				fmt.Sprintf("insert into %s select * from source.%s where thread_id = '%s';", table, table, stream),
			)
		}
	}
	script = append(script, "commit;")
	return runSQLite(historyDatabase(targetHome), strings.Join(script, "\n"))
}

// upsertThreadRow gives the target index a row for the thread: a clone of the
// source's row when the target has none (section columns keep their local
// defaults). Only the moving chat updates an existing current path and mode;
// an ancestor may already be further ahead on the destination.
func upsertThreadRow(sourceDB, targetDB, threadID, rollout, mode string, updateExisting bool) error {
	targetColumns, err := threadColumns(targetDB)
	if err != nil {
		return err
	}
	sourceColumns, err := threadColumns(sourceDB)
	if err != nil {
		return err
	}
	columns := make([]string, 0, len(targetColumns))
	for column := range targetColumns {
		if _, excluded := catalogExcludedColumns[column]; excluded {
			continue
		}
		if _, shared := sourceColumns[column]; shared {
			columns = append(columns, column)
		}
	}
	sort.Strings(columns)
	list := strings.Join(quoteIdentifiers(columns), ", ")
	values := quoteIdentifiers(columns)
	for i, column := range columns {
		if column == "rollout_path" {
			values[i] = "'" + escapeSQLLiteral(rollout) + "'"
		}
	}
	script := []string{
		"PRAGMA busy_timeout=10000;",
		fmt.Sprintf("ATTACH DATABASE '%s' AS src;", escapeSQLLiteral(sourceDB)),
		fmt.Sprintf("INSERT OR IGNORE INTO threads (%s) SELECT %s FROM src.threads WHERE id = '%s';", list, strings.Join(values, ", "), threadID),
	}
	if updateExisting {
		script = append(script, fmt.Sprintf(
			"UPDATE threads SET rollout_path = '%s', history_mode = '%s' WHERE id = '%s';",
			escapeSQLLiteral(rollout), escapeSQLLiteral(mode), threadID,
		))
	}
	script = append(script, "DETACH DATABASE src;")
	return runSQLite(targetDB, strings.Join(script, "\n"))
}

// ensureHistorySchema gives a target home that has not opened its history
// store yet the same tables and migration ledger as the source, so copied
// rows land in a database Codex recognizes as current.
func ensureHistorySchema(sourceDB, targetDB string) error {
	existing, err := querySQLite(targetDB, "select count(*) from sqlite_master where type = 'table' and name = 'thread_turns';")
	if err != nil {
		return err
	}
	if strings.TrimSpace(existing) == "1" {
		return nil
	}
	schema, err := querySQLite(sourceDB, "select sql || ';' from sqlite_master where sql is not null and name not like 'sqlite_%' order by rowid;")
	if err != nil {
		return err
	}
	return runSQLite(targetDB, strings.Join([]string{
		"PRAGMA busy_timeout=10000;",
		schema,
		fmt.Sprintf("ATTACH DATABASE '%s' AS src;", escapeSQLLiteral(sourceDB)),
		"INSERT OR IGNORE INTO _sqlx_migrations SELECT * FROM src._sqlx_migrations;",
		"DETACH DATABASE src;",
	}, "\n"))
}

// historyBaseThread reads the thread whose rollout a fork's history starts
// from, recorded in the fork's session metadata, or "" for a thread that
// carries its own history.
func historyBaseThread(rollout string) string {
	file, err := os.Open(rollout)
	if err != nil {
		return ""
	}
	defer file.Close()
	reader := bufio.NewReaderSize(file, 1<<20)
	line, err := reader.ReadBytes('\n')
	if len(line) == 0 && err != nil {
		return ""
	}
	var record struct {
		Payload struct {
			HistoryBase struct {
				ThreadID string `json:"thread_id"`
			} `json:"history_base"`
		} `json:"payload"`
	}
	if json.Unmarshal(line, &record) != nil {
		return ""
	}
	if !threadIDPattern.MatchString(record.Payload.HistoryBase.ThreadID) {
		return ""
	}
	return record.Payload.HistoryBase.ThreadID
}

var rolloutThreadPattern = regexp.MustCompile(`-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:_[0-9a-f-]{36})?\.jsonl$`)

// rolloutOwner resolves the thread whose rollout carries a history stream.
// A fork's history base names the stream it continues from, which is the
// thread itself for an original rollout and the link id for a continuation
// file (`<thread>_<link>.jsonl`), so the file name settles which thread to
// bring along.
func rolloutOwner(codexHome, streamID string) string {
	for _, rollout := range threadRollouts(codexHome, streamID) {
		if match := rolloutThreadPattern.FindStringSubmatch(filepath.Base(rollout)); match != nil {
			return match[1]
		}
	}
	return streamID
}

// threadRollouts lists every rollout segment of a thread in a Codex home.
func threadRollouts(codexHome, threadID string) []string {
	matches, _ := filepath.Glob(filepath.Join(codexHome, "sessions", "*", "*", "*", "*"+threadID+"*.jsonl"))
	rollouts := make([]string, 0, len(matches))
	for _, match := range matches {
		if info, err := os.Stat(match); err == nil && info.Mode().IsRegular() {
			rollouts = append(rollouts, match)
		}
	}
	return rollouts
}

// projectionCoversRollout reports whether an account's history projection has
// absorbed its current rollout to the last byte, under the stream that file
// is projected as. A session that loads behind the file restarts numbering
// behind it, so a copy is only taken from a projection that is caught up.
func projectionCoversRollout(home, threadID string) (bool, error) {
	row, err := querySQLite(
		stateDatabase(home),
		fmt.Sprintf("select rollout_path from threads where id = '%s'", threadID),
	)
	if err != nil {
		return false, err
	}
	rollout := strings.TrimSpace(row)
	info, err := os.Stat(rollout)
	if err != nil {
		return false, fmt.Errorf("stat rollout: %w", err)
	}
	offset, err := querySQLite(
		historyDatabase(home),
		fmt.Sprintf(
			"select next_rollout_byte_offset from thread_history_projection_state where thread_id = '%s'",
			projectionStream(rollout, threadID),
		),
	)
	if err != nil {
		return false, err
	}
	return strings.TrimSpace(offset) == fmt.Sprint(info.Size()), nil
}

func historyDatabase(home string) string {
	return filepath.Join(home, "thread_history_1.sqlite")
}
