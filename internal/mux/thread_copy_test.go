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

func TestSyncThreadCopyBringsForkedFromThreadAlong(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("sqlite3 is not available")
	}
	root := t.TempDir()
	source, target := filepath.Join(root, "source"), filepath.Join(root, "target")
	parentID := "019ff1f7-e91a-7fa3-bfd7-272e790235f6"
	forkID := "01a08c71-52c7-7651-b40e-929694f82b26"
	parent := filepath.Join(source, "sessions", "2026", "08", "11", "rollout-parent-"+parentID+".jsonl")
	fork := filepath.Join(source, "sessions", "2026", "09", "10", "rollout-fork-"+forkID+".jsonl")
	writeRollout := func(path, meta string) {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(meta+"\n{\"type\":\"turn\"}\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	writeRollout(parent, `{"type":"session_meta","payload":{"id":"`+parentID+`","history_mode":"paginated"}}`)
	writeRollout(fork, `{"type":"session_meta","payload":{"id":"`+forkID+`","forked_from_id":"`+parentID+`","history_base":{"thread_id":"`+parentID+`","end_ordinal_exclusive":2,"end_byte_offset":90}}}`)
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
	if err := runSQLite(stateDatabase(source), "insert into threads values ('"+parentID+"', '"+parent+"', 'paginated'), ('"+forkID+"', '"+fork+"', 'paginated');"); err != nil {
		t.Fatal(err)
	}
	if err := runSQLite(historyDatabase(source), "insert into thread_history_projection_state values ('"+parentID+"', 90, 2), ('"+forkID+"', 120, 2);"); err != nil {
		t.Fatal(err)
	}

	if err := syncThreadCopy(source, target, forkID); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{forkID, parentID} {
		if rollouts := threadRollouts(target, id); len(rollouts) != 1 {
			t.Fatalf("expected %s to have one rollout on the target, got %v", id, rollouts)
		}
		row, err := querySQLite(stateDatabase(target), "select rollout_path from threads where id = '"+id+"';")
		if err != nil || !strings.HasPrefix(strings.TrimSpace(row), target) {
			t.Fatalf("expected %s to be indexed under the target home, got %q err=%v", id, row, err)
		}
		offset, err := querySQLite(historyDatabase(target), "select next_rollout_byte_offset from thread_history_projection_state where thread_id = '"+id+"';")
		if err != nil || strings.TrimSpace(offset) == "" {
			t.Fatalf("expected %s to have a projection on the target, got %q err=%v", id, offset, err)
		}
	}
}

func TestHistoryBaseThreadIgnoresOwnHistory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rollout.jsonl")
	if err := os.WriteFile(path, []byte(`{"type":"session_meta","payload":{"id":"x","history_mode":"paginated"}}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if base := historyBaseThread(path); base != "" {
		t.Fatalf("expected no base thread, got %q", base)
	}
}

func TestRolloutOwnerResolvesContinuationStreams(t *testing.T) {
	home := t.TempDir()
	thread := "01a04238-6090-7e01-b2c6-24c757a32b10"
	link := "01a07dfe-ac02-7393-b113-81a48193c591"
	path := filepath.Join(home, "sessions", "2026", "09", "07", "rollout-2026-09-07T15-30-37-"+thread+"_"+link+".jsonl")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := rolloutOwner(home, link); got != thread {
		t.Fatalf("expected the link to resolve to its thread, got %q", got)
	}
	if got := rolloutOwner(home, thread); got != thread {
		t.Fatalf("expected a thread id to resolve to itself, got %q", got)
	}
	if got := rolloutOwner(home, "01a00000-0000-7000-8000-000000000000"); got != "01a00000-0000-7000-8000-000000000000" {
		t.Fatalf("expected an unknown stream to pass through, got %q", got)
	}
}

func TestSyncThreadCopyCarriesAnUnindexedHistoryBase(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("sqlite3 is not available")
	}
	root := t.TempDir()
	source, target := filepath.Join(root, "source"), filepath.Join(root, "target")
	baseID := "01a04238-6090-7e01-b2c6-24c757a32b10"
	linkID := "01a07dfe-ac02-7393-b113-81a48193c591"
	forkID := "01a08c71-52c7-7651-b40e-929694f82b26"
	base := filepath.Join(source, "sessions", "2026", "09", "07", "rollout-base-"+baseID+"_"+linkID+".jsonl")
	fork := filepath.Join(source, "sessions", "2026", "09", "11", "rollout-fork-"+forkID+".jsonl")
	for path, meta := range map[string]string{
		base: `{"type":"session_meta","payload":{"id":"` + baseID + `","history_mode":"paginated"}}`,
		fork: `{"type":"session_meta","payload":{"id":"` + forkID + `","history_base":{"thread_id":"` + linkID + `","end_ordinal_exclusive":2,"end_byte_offset":90}}}`,
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(meta+"\n"), 0o600); err != nil {
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
	if err := runSQLite(stateDatabase(source), "insert into threads values ('"+forkID+"', '"+fork+"', 'paginated');"); err != nil {
		t.Fatal(err)
	}
	if err := runSQLite(historyDatabase(source), "insert into thread_history_projection_state values ('"+linkID+"', 90, 2), ('"+forkID+"', 120, 2);"); err != nil {
		t.Fatal(err)
	}

	if err := syncThreadCopy(source, target, forkID); err != nil {
		t.Fatal(err)
	}
	if rollouts := threadRollouts(target, linkID); len(rollouts) != 1 {
		t.Fatalf("expected the base continuation to be linked, got %v", rollouts)
	}
	offset, err := querySQLite(historyDatabase(target), "select next_rollout_byte_offset from thread_history_projection_state where thread_id = '"+linkID+"';")
	if err != nil || strings.TrimSpace(offset) != "90" {
		t.Fatalf("expected the base stream's projection on the target, got %q err=%v", offset, err)
	}
}
