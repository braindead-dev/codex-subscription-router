package mux

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

const threadsSchema = `
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    rollout_path TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at_ms INTEGER,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    thread_section_id TEXT REFERENCES thread_sections(id)
);
CREATE TABLE thread_sections (id TEXT PRIMARY KEY);
CREATE TRIGGER threads_created_at_ms_after_insert
AFTER INSERT ON threads WHEN NEW.created_at_ms IS NULL
BEGIN UPDATE threads SET created_at_ms = NEW.created_at*1000 WHERE id = NEW.id; END;`

func makeHome(t *testing.T, seed string) catalogHome {
	t.Helper()
	dir := t.TempDir()
	run := threadsSchema
	if seed != "" {
		run += "\n" + seed
	}
	cmd := exec.Command(sqlite3Binary, filepath.Join(dir, "state_5.sqlite"))
	cmd.Stdin = stringsNewReader(run)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("seed home: %v: %s", err, out)
	}
	return catalogHome{id: filepath.Base(dir), path: dir}
}

func stringsNewReader(s string) *os.File {
	f, _ := os.CreateTemp("", "seed-*.sql")
	f.WriteString(s)
	f.Seek(0, 0)
	return f
}

func rows(t *testing.T, home catalogHome, query string) []string {
	t.Helper()
	out, err := querySQLite(stateDatabase(home.path), query)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	return splitLines(out)
}

func TestReconcileUnifiedCatalogMirrorsForeignThreads(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("system sqlite3 unavailable")
	}
	a := makeHome(t, `INSERT INTO threads (id,rollout_path,created_at,updated_at,source,title)
		VALUES ('t-a','/home-a/a.jsonl',1,1,'vscode','A thread'),
		       ('t-arch','/home-a/x.jsonl',1,1,'vscode','archived');
		UPDATE threads SET archived=1 WHERE id='t-arch';`)
	b := makeHome(t, `INSERT INTO threads (id,rollout_path,created_at,updated_at,source,title,is_pinned)
		VALUES ('t-b','/home-b/b.jsonl',2,2,'vscode','B thread',1);`)

	if err := reconcileUnifiedCatalog([]catalogHome{a, b}); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	// Each home now lists both live threads, not the archived one.
	for _, home := range []catalogHome{a, b} {
		got := rows(t, home, "SELECT id FROM threads WHERE archived=0 ORDER BY id;")
		if len(got) != 2 || got[0] != "t-a" || got[1] != "t-b" {
			t.Fatalf("home %s: expected [t-a t-b], got %v", home.id, got)
		}
		if arch := rows(t, home, "SELECT count(*) FROM threads WHERE id='t-arch';"); arch[0] != countIn(home, a) {
			t.Fatalf("home %s: archived thread mirrored unexpectedly", home.id)
		}
	}
	// The mirrored row points at the owner's path and is not pinned locally.
	path := rows(t, a, "SELECT rollout_path FROM threads WHERE id='t-b';")
	if len(path) != 1 || path[0] != "/home-b/b.jsonl" {
		t.Fatalf("expected mirror to keep owner path, got %v", path)
	}
	pinned := rows(t, a, "SELECT is_pinned FROM threads WHERE id='t-b';")
	if pinned[0] != "0" {
		t.Fatalf("expected local pin default, got %v", pinned)
	}
}

func countIn(home, owner catalogHome) string {
	if home.path == owner.path {
		return "1"
	}
	return "0"
}

func TestReconcileUnifiedCatalogNeverModifiesExistingRows(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("system sqlite3 unavailable")
	}
	a := makeHome(t, `INSERT INTO threads (id,rollout_path,created_at,updated_at,source,title)
		VALUES ('shared','/home-a/s.jsonl',1,1,'vscode','A owns it');`)
	b := makeHome(t, `INSERT INTO threads (id,rollout_path,created_at,updated_at,source,title)
		VALUES ('shared','/home-b/s.jsonl',9,9,'vscode','B already has it');`)

	if err := reconcileUnifiedCatalog([]catalogHome{a, b}); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	// b's own row for the shared id must be untouched (INSERT OR IGNORE).
	title := rows(t, b, "SELECT title FROM threads WHERE id='shared';")
	if len(title) != 1 || title[0] != "B already has it" {
		t.Fatalf("expected b's row untouched, got %v", title)
	}
	path := rows(t, b, "SELECT rollout_path FROM threads WHERE id='shared';")
	if path[0] != "/home-b/s.jsonl" {
		t.Fatalf("expected b's path untouched, got %v", path)
	}
}

func TestReconcileUnifiedCatalogToleratesUnreachableHome(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("system sqlite3 unavailable")
	}
	a := makeHome(t, `INSERT INTO threads (id,rollout_path,created_at,updated_at,source,title)
		VALUES ('t-a','/home-a/a.jsonl',1,1,'vscode','A thread');`)
	missing := catalogHome{id: "gone", path: filepath.Join(t.TempDir(), "does-not-exist")}
	if err := reconcileUnifiedCatalog([]catalogHome{a, missing}); err != nil {
		t.Fatalf("a missing peer must not fail: %v", err)
	}
	if got := rows(t, a, "SELECT count(*) FROM threads;"); got[0] != "1" {
		t.Fatalf("expected a untouched, got %v", got)
	}
}

func TestReconcileUnifiedCatalogRefusesPaginatedThreadStore(t *testing.T) {
	if _, err := os.Stat(sqlite3Binary); err != nil {
		t.Skip("system sqlite3 unavailable")
	}
	a := makeHome(t, `ALTER TABLE threads ADD COLUMN history_mode TEXT NOT NULL DEFAULT 'legacy';
		INSERT INTO threads (id,rollout_path,created_at,updated_at,source,title)
		VALUES ('t-a','/home-a/a.jsonl',1,1,'vscode','A thread');`)
	b := makeHome(t, `ALTER TABLE threads ADD COLUMN history_mode TEXT NOT NULL DEFAULT 'legacy';
		INSERT INTO threads (id,rollout_path,created_at,updated_at,source,title)
		VALUES ('t-b','/home-b/b.jsonl',2,2,'vscode','B thread');`)

	err := reconcileUnifiedCatalog([]catalogHome{a, b})
	if !errors.Is(err, errPaginatedThreadStore) {
		t.Fatalf("expected the paginated store to be refused, got %v", err)
	}
	for _, home := range []catalogHome{a, b} {
		if got := rows(t, home, "SELECT count(*) FROM threads;"); got[0] != "1" {
			t.Fatalf("home %s: rows were mirrored into a paginated store: %v", home.id, got)
		}
	}
}
