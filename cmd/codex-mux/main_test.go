package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestInteractiveAppServerDetection(t *testing.T) {
	tests := []struct {
		args []string
		want bool
	}{
		{args: []string{"-c", "features.code_mode_host=true", "app-server", "--analytics-default-enabled"}, want: true},
		{args: []string{"app-server", "daemon", "version"}, want: false},
		{args: []string{"app-server", "generate-ts", "--out", "/tmp/schema"}, want: false},
		{args: []string{"exec", "hello"}, want: false},
	}
	for _, test := range tests {
		if got := isInteractiveAppServer(test.args); got != test.want {
			t.Fatalf("isInteractiveAppServer(%q)=%v, want %v", test.args, got, test.want)
		}
	}
}

func TestValidateControlToken(t *testing.T) {
	valid := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	if got, err := validateControlToken("\n" + valid + "\t"); err != nil || got != valid {
		t.Fatalf("validateControlToken(valid) = %q, %v", got, err)
	}
	for _, invalid := range []string{"short", valid + "00", valid[:63] + "z"} {
		if _, err := validateControlToken(invalid); err == nil {
			t.Fatalf("validateControlToken(%q) unexpectedly succeeded", invalid)
		}
	}
}

func TestMultiplexerLockAdmitsOneHolder(t *testing.T) {
	root := t.TempDir()
	first, err := acquireMultiplexerLock(root, 0)
	if err != nil || first == nil {
		t.Fatalf("expected the first caller to hold the lock, got %v err=%v", first, err)
	}
	second, err := acquireMultiplexerLock(root, 0)
	if err != nil || second != nil {
		t.Fatalf("expected the second caller to be refused, got %v err=%v", second, err)
	}
	first.Close()
	third, err := acquireMultiplexerLock(root, 0)
	if err != nil || third == nil {
		t.Fatalf("expected the slot to free once released, got %v err=%v", third, err)
	}
	third.Close()
}

func TestMultiplexerLockWaitsForAFreshClaimToEnd(t *testing.T) {
	root := t.TempDir()
	preflight, err := acquireMultiplexerLock(root, 0)
	if err != nil || preflight == nil {
		t.Fatalf("expected the preflight to claim the slot, got %v err=%v", preflight, err)
	}
	go func() {
		time.Sleep(300 * time.Millisecond)
		preflight.Close()
	}()
	started := time.Now()
	chat, err := acquireMultiplexerLock(root, 5*time.Second)
	if err != nil || chat == nil {
		t.Fatalf("expected the next connection to take over the slot, got %v err=%v", chat, err)
	}
	if waited := time.Since(started); waited < 250*time.Millisecond || waited > 3*time.Second {
		t.Fatalf("waited %v for the slot", waited)
	}
	defer chat.Close()

	stale := time.Now().Add(-time.Minute)
	if err := os.Chtimes(filepath.Join(root, "multiplexer.lock"), stale, stale); err != nil {
		t.Fatal(err)
	}
	started = time.Now()
	nested, err := acquireMultiplexerLock(root, 5*time.Second)
	if err != nil || nested != nil {
		t.Fatalf("expected a long-held slot to refuse at once, got %v err=%v", nested, err)
	}
	if waited := time.Since(started); waited > time.Second {
		t.Fatalf("waited %v on a slot claimed long ago", waited)
	}
}
