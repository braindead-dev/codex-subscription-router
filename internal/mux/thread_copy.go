package mux

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
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
// derived rows apply unchanged.
func syncThreadCopy(sourceHome, targetHome, threadID string) error {
	if !threadIDPattern.MatchString(threadID) {
		return fmt.Errorf("unexpected thread id %q", threadID)
	}
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
		return fmt.Errorf("source does not index thread %s", threadID)
	}
	streams := []string{threadID}
	for _, rollout := range threadRollouts(sourceHome, threadID) {
		if _, err := linkRolloutIntoHome(rollout, targetHome); err != nil {
			return err
		}
		if stream := projectionStream(rollout, threadID); stream != threadID {
			streams = append(streams, stream)
		}
	}
	current, err := linkRolloutIntoHome(path, targetHome)
	if err != nil {
		return err
	}
	if err := upsertThreadRow(stateDatabase(sourceHome), stateDatabase(targetHome), threadID, current, mode); err != nil {
		return err
	}
	sourceHistory := historyDatabase(sourceHome)
	if !fileExists(sourceHistory) {
		return nil
	}
	if err := ensureHistorySchema(sourceHistory, historyDatabase(targetHome)); err != nil {
		return err
	}
	script := []string{
		fmt.Sprintf("attach database '%s' as source;", escapeSQLLiteral(sourceHistory)),
		"begin;",
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
// defaults), then the current rollout path and history mode in either case.
func upsertThreadRow(sourceDB, targetDB, threadID, rollout, mode string) error {
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
	return runSQLite(targetDB, strings.Join([]string{
		"PRAGMA busy_timeout=5000;",
		fmt.Sprintf("ATTACH DATABASE '%s' AS src;", escapeSQLLiteral(sourceDB)),
		fmt.Sprintf("INSERT OR IGNORE INTO threads (%s) SELECT %s FROM src.threads WHERE id = '%s';", list, list, threadID),
		fmt.Sprintf(
			"UPDATE threads SET rollout_path = '%s', history_mode = '%s' WHERE id = '%s';",
			escapeSQLLiteral(rollout), escapeSQLLiteral(mode), threadID,
		),
		"DETACH DATABASE src;",
	}, "\n"))
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
		"PRAGMA busy_timeout=5000;",
		schema,
		fmt.Sprintf("ATTACH DATABASE '%s' AS src;", escapeSQLLiteral(sourceDB)),
		"INSERT OR IGNORE INTO _sqlx_migrations SELECT * FROM src._sqlx_migrations;",
		"DETACH DATABASE src;",
	}, "\n"))
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
