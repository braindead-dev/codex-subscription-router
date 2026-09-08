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
	second := filepath.Join(source, "sessions", "2026", "09", "04", "rollout-second-"+threadID+"_01a07dfe-cb5b-77d3-811e-cd12fcf01d50.jsonl")
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
	if err := runSQLite(historyDatabase(source), "insert into thread_turns values ('"+threadID+"', 't1', 1), ('"+threadID+"', 't2', 2), ('01a07dfe-cb5b-77d3-811e-cd12fcf01d50', 't3', 1);"+
		"insert into thread_history_projection_state values ('"+threadID+"', 40, 2), ('01a07dfe-cb5b-77d3-811e-cd12fcf01d50', 4, 1);"); err != nil {
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
	wantPath := filepath.Join(target, "sessions", "2026", "09", "04", "rollout-second-"+threadID+"_01a07dfe-cb5b-77d3-811e-cd12fcf01d50.jsonl")
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
	if link, err := querySQLite(historyDatabase(target), "select count(*) from thread_turns where thread_id = '01a07dfe-cb5b-77d3-811e-cd12fcf01d50';"); err != nil || strings.TrimSpace(link) != "1" {
		t.Fatalf("expected the link stream to be copied, got %q, %v", strings.TrimSpace(link), err)
	}
}

func TestProjectionStreamUsesLinkID(t *testing.T) {
	thread := "01a04238-6090-7e01-b2c6-24c757a32b10"
	link := "01a07dfe-cb5b-77d3-811e-cd12fcf01d50"
	if got := projectionStream("/x/rollout-2026-09-07T15-30-45-"+thread+"_"+link+".jsonl", thread); got != link {
		t.Fatalf("link file projected as %q", got)
	}
	if got := projectionStream("/x/rollout-2026-08-27T00-56-26-"+thread+".jsonl", thread); got != thread {
		t.Fatalf("original rollout projected as %q", got)
	}
}

func TestProjectionCoversRolloutComparesOffsetToFileSize(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("sqlite3 is not available")
	}
	home := t.TempDir()
	threadID := "01a04238-6090-7e01-b2c6-24c757a32b10"
	rollout := filepath.Join(home, "sessions", "2026", "09", "07", "rollout-"+threadID+".jsonl")
	if err := os.MkdirAll(filepath.Dir(rollout), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(rollout, []byte("{}\n{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := runSQLite(stateDatabase(home), "create table threads (id text primary key, rollout_path text, history_mode text);"+
		"insert into threads values ('"+threadID+"', '"+rollout+"', 'paginated');"); err != nil {
		t.Fatal(err)
	}
	if err := runSQLite(historyDatabase(home), "create table thread_history_projection_state (thread_id text primary key, next_rollout_byte_offset integer, next_rollout_ordinal integer);"+
		"insert into thread_history_projection_state values ('"+threadID+"', 3, 1);"); err != nil {
		t.Fatal(err)
	}
	if covered, err := projectionCoversRollout(home, threadID); err != nil || covered {
		t.Fatalf("expected a lagging projection to be reported, got covered=%v err=%v", covered, err)
	}
	if err := runSQLite(historyDatabase(home), "update thread_history_projection_state set next_rollout_byte_offset = 6;"); err != nil {
		t.Fatal(err)
	}
	if covered, err := projectionCoversRollout(home, threadID); err != nil || !covered {
		t.Fatalf("expected a caught-up projection, got covered=%v err=%v", covered, err)
	}
}
