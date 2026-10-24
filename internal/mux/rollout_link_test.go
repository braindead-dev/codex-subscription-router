package mux

import (
	"os"
	"path/filepath"
	"strings"
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

func TestAncestorRolloutReusesOnlyMatchingCopies(t *testing.T) {
	sourceText := strings.Repeat("history\n", 20_000)
	for _, tc := range []struct {
		name, targetText string
		wantOK           bool
	}{
		{"identical", sourceText, true},
		{"newer", sourceText + "later turn\n", true},
		{"shorter", sourceText[:len(sourceText)-1], false},
		{"diverged", sourceText[:len(sourceText)-2] + "x\n", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			source := filepath.Join(root, "source", "sessions", "2026", "08", "12", "rollout.jsonl")
			target := filepath.Join(root, "target", "sessions", "2026", "08", "12", "rollout.jsonl")
			for path, body := range map[string]string{source: sourceText, target: tc.targetText} {
				if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			before, _ := os.Stat(target)
			path, created, err := shareRolloutIntoHome(source, filepath.Join(root, "target"), true)
			if (err == nil) != tc.wantOK || created || tc.wantOK && path != target {
				t.Fatalf("got path=%q created=%v err=%v", path, created, err)
			}
			after, _ := os.Stat(target)
			body, _ := os.ReadFile(target)
			if !os.SameFile(before, after) || string(body) != tc.targetText {
				t.Fatal("ancestor file was replaced or changed")
			}
			if _, err := linkRolloutIntoHome(source, filepath.Join(root, "target")); err == nil {
				t.Fatal("a writable moving thread must still use a shared file")
			}
		})
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
