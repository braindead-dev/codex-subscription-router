package mux

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLinkRolloutIntoHomeMirrorsSessionsLayout(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "primary", "sessions", "2026", "09", "04", "rollout-a.jsonl")
	if err := os.MkdirAll(filepath.Dir(source), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(source, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	home := filepath.Join(root, "work")

	linked, err := linkRolloutIntoHome(source, home)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(home, "sessions", "2026", "09", "04", "rollout-a.jsonl")
	if linked != want {
		t.Fatalf("linked %q, want %q", linked, want)
	}
	sourceInfo, _ := os.Stat(source)
	linkedInfo, _ := os.Stat(linked)
	if !os.SameFile(sourceInfo, linkedInfo) {
		t.Fatal("link does not share the source file")
	}

	again, err := linkRolloutIntoHome(source, home)
	if err != nil || again != want {
		t.Fatalf("relinking returned %q, %v", again, err)
	}
	if own, err := linkRolloutIntoHome(source, filepath.Join(root, "primary")); err != nil || own != source {
		t.Fatalf("own-home rollout returned %q, %v", own, err)
	}
}

func TestLinkRolloutIntoHomeRejectsConflicts(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "primary", "sessions", "2026", "09", "04", "rollout-a.jsonl")
	other := filepath.Join(root, "work", "sessions", "2026", "09", "04", "rollout-a.jsonl")
	for _, path := range []string{source, other} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(path), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := linkRolloutIntoHome(source, filepath.Join(root, "work")); err == nil {
		t.Fatal("expected a conflict for a different existing rollout")
	}
	if _, err := linkRolloutIntoHome(filepath.Join(root, "archived_sessions", "rollout-b.jsonl"), filepath.Join(root, "work")); err == nil {
		t.Fatal("expected an error for a rollout outside sessions")
	}
}
