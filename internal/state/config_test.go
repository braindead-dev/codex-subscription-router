package state

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSyncIsolatedConfigSharesProjectTrust(t *testing.T) {
	primary := t.TempDir()
	isolated := t.TempDir()
	writeFile(t, filepath.Join(primary, "config.toml"), `model = "gpt"

[projects."/shared"]
trust_level = "trusted"

[projects."/both"]
trust_level = "trusted"
`)
	writeFile(t, filepath.Join(isolated, "config.toml"), `[projects."/local"]
trust_level = "trusted"

[projects."/both"]
trust_level = "untrusted"
`)

	if err := syncIsolatedConfig(primary, isolated); err != nil {
		t.Fatal(err)
	}
	merged, err := os.ReadFile(filepath.Join(isolated, "config.toml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(merged)
	for _, want := range []string{
		`[projects."/local"]`,
		`[projects."/shared"]`,
		`cli_auth_credentials_store = "file"`,
		`model = "gpt"`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("expected %q in merged config:\n%s", want, text)
		}
	}
	if strings.Count(text, `[projects."/both"]`) != 1 {
		t.Fatalf("expected one /both section:\n%s", text)
	}
	if !strings.Contains(text, "[projects.\"/both\"]\ntrust_level = \"untrusted\"") {
		t.Fatalf("expected the isolated trust level to win:\n%s", text)
	}
}

func writeFile(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}
}
