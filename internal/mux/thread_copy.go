package mux

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var threadIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

var historyTables = []string{
	"thread_turns",
	"thread_items",
	"thread_history_projection_state",
	"thread_realtime_items",
}

// syncThreadCopy makes the target account's copy of a thread match the
// source's before the thread runs there. Codex 0.153 keeps a per-account
// history projection and may continue a thread in a new rollout segment, so
// a copy that stayed behind on the target would resume without the latest
// turns. Every rollout file is hard-linked into the target's sessions tree,
// the target's row is pointed at the source's current rollout, and the
// projection rows are copied. The files are shared, so the derived rows apply
// unchanged.
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
	for _, rollout := range threadRollouts(sourceHome, threadID) {
		if _, err := linkRolloutIntoHome(rollout, targetHome); err != nil {
			return err
		}
	}
	current, err := linkRolloutIntoHome(path, targetHome)
	if err != nil {
		return err
	}
	if err := runSQLite(stateDatabase(targetHome), fmt.Sprintf(
		"update threads set rollout_path = '%s', history_mode = '%s' where id = '%s';",
		escapeSQLLiteral(current),
		escapeSQLLiteral(mode),
		threadID,
	)); err != nil {
		return err
	}
	sourceHistory := historyDatabase(sourceHome)
	if !fileExists(sourceHistory) {
		return nil
	}
	script := []string{
		fmt.Sprintf("attach database '%s' as source;", escapeSQLLiteral(sourceHistory)),
		"begin;",
	}
	for _, table := range historyTables {
		script = append(script,
			fmt.Sprintf("delete from %s where thread_id = '%s';", table, threadID),
			fmt.Sprintf("insert into %s select * from source.%s where thread_id = '%s';", table, table, threadID),
		)
	}
	script = append(script, "commit;")
	return runSQLite(historyDatabase(targetHome), strings.Join(script, "\n"))
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

func historyDatabase(home string) string {
	return filepath.Join(home, "thread_history_1.sqlite")
}
