package main

import "testing"

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
	first, err := acquireMultiplexerLock(root)
	if err != nil || first == nil {
		t.Fatalf("expected the first caller to hold the lock, got %v err=%v", first, err)
	}
	second, err := acquireMultiplexerLock(root)
	if err != nil || second != nil {
		t.Fatalf("expected the second caller to be refused, got %v err=%v", second, err)
	}
	first.Close()
	third, err := acquireMultiplexerLock(root)
	if err != nil || third == nil {
		t.Fatalf("expected the slot to free once released, got %v err=%v", third, err)
	}
	third.Close()
}
