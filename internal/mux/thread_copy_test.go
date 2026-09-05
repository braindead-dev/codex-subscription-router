package mux

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSyncThreadCopyBringsTargetUpToDate(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("sqlite3 is not available")
	}
	root := t.TempDir()
	source, target := filepath.Join(root, "source"), filepath.Join(root, "target")
	threadID := "01a04238-6090-7e01-b2c6-24c757a32b10"
	first := filepath.Join(source, "sessions", "2026", "08", "27", "rollout-first-"+threadID+".jsonl")
	second := filepath.Join(source, "sessions", "2026", "09", "04", "rollout-second-"+threadID+"_seg.jsonl")
	for _, path := range []string{first, second} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.MkdirAll(target, 0o700); err != nil {
		t.Fatal(err)
	}
	schema := "create table threads (id text primary key, rollout_path text, history_mode text);"
	history := "create table thread_turns (thread_id text, turn_id text, rollout_ordinal integer);" +
		"create table thread_items (thread_id text, turn_id text, item_id text, rollout_ordinal integer);" +
		"create table thread_history_projection_state (thread_id text primary key, next_rollout_byte_offset integer, next_rollout_ordinal integer);" +
		"create table thread_realtime_items (thread_id text, item_id text);"
	for _, home := range []string{source, target} {
		if err := runSQLite(stateDatabase(home), schema); err != nil {
			t.Fatal(err)
		}
		if err := runSQLite(historyDatabase(home), history); err != nil {
			t.Fatal(err)
		}
	}
	if err := runSQLite(stateDatabase(source), "insert into threads values ('"+threadID+"', '"+second+"', 'paginated');"); err != nil {
		t.Fatal(err)
	}
	if err := runSQLite(historyDatabase(source), "insert into thread_turns values ('"+threadID+"', 't1', 1), ('"+threadID+"', 't2', 2);"+
		"insert into thread_history_projection_state values ('"+threadID+"', 40, 2);"); err != nil {
		t.Fatal(err)
	}
	if err := runSQLite(stateDatabase(target), "insert into threads values ('"+threadID+"', '"+filepath.Join(target, "sessions", "old.jsonl")+"', 'legacy');"); err != nil {
		t.Fatal(err)
	}
	if err := runSQLite(historyDatabase(target), "insert into thread_turns values ('"+threadID+"', 't1', 1);"+
		"insert into thread_history_projection_state values ('"+threadID+"', 10, 1);"); err != nil {
		t.Fatal(err)
	}

	if err := syncThreadCopy(source, target, threadID); err != nil {
		t.Fatal(err)
	}
	row, err := querySQLite(stateDatabase(target), "select rollout_path || ' ' || history_mode from threads where id = '"+threadID+"';")
	if err != nil {
		t.Fatal(err)
	}
	wantPath := filepath.Join(target, "sessions", "2026", "09", "04", "rollout-second-"+threadID+"_seg.jsonl")
	if strings.TrimSpace(row) != wantPath+" paginated" {
		t.Fatalf("target row %q, want %q", strings.TrimSpace(row), wantPath+" paginated")
	}
	for _, path := range []string{wantPath, filepath.Join(target, "sessions", "2026", "08", "27", "rollout-first-"+threadID+".jsonl")} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("expected linked rollout %s: %v", path, err)
		}
	}
	turns, err := querySQLite(historyDatabase(target), "select count(*) || ' ' || (select next_rollout_ordinal from thread_history_projection_state where thread_id = '"+threadID+"') from thread_turns where thread_id = '"+threadID+"';")
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(turns) != "2 2" {
		t.Fatalf("expected the source projection on the target, got %q", strings.TrimSpace(turns))
	}
}
