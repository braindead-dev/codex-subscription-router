package state

import (
	"errors"
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

func TestSyncIsolatedConfigLinksSharedHomeContent(t *testing.T) {
	primary := t.TempDir()
	isolated := t.TempDir()
	writeFile(t, filepath.Join(primary, "config.toml"), "model = \"gpt\"\n")
	writeFile(t, filepath.Join(primary, "AGENTS.md"), "never attribute yourself\n")
	if err := os.MkdirAll(filepath.Join(primary, "skills", "pr"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(isolated, "skills", ".system"), 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(isolated, "hooks.json"), "{\"local\":true}")

	if err := syncIsolatedConfig(primary, isolated); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"AGENTS.md", "skills"} {
		link, err := os.Readlink(filepath.Join(isolated, name))
		if err != nil || link != filepath.Join(primary, name) {
			t.Fatalf("expected %s to link to the primary copy, got %q err=%v", name, link, err)
		}
	}
	if _, err := os.Stat(filepath.Join(isolated, "skills", "pr")); err != nil {
		t.Fatalf("expected primary skills to be visible through the link: %v", err)
	}
	content, err := os.ReadFile(filepath.Join(isolated, "hooks.json"))
	if err != nil || string(content) != "{\"local\":true}" {
		t.Fatalf("expected the isolated hooks.json to be preserved, got %q err=%v", content, err)
	}
	if _, err := os.Lstat(filepath.Join(isolated, "agents")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("expected no link for an entry the primary home lacks, got %v", err)
	}

	if err := syncIsolatedConfig(primary, isolated); err != nil {
		t.Fatalf("second sync must be idempotent: %v", err)
	}
}

func TestSyncIsolatedConfigRelocatesBundledMarketplaces(t *testing.T) {
	primary := t.TempDir()
	isolated := t.TempDir()
	bundled := filepath.Join(primary, ".tmp", "bundled-marketplaces", "openai-bundled")
	if err := os.MkdirAll(bundled, 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(primary, "config.toml"), `[marketplaces.openai-bundled]
source_type = "local"
source = "`+bundled+`"

[marketplaces.runtime]
source_type = "local"
source = "/elsewhere/runtime"

[marketplaces.official]
source_type = "git"
source = "https://example.com/plugins.git"
`)

	if err := syncIsolatedConfig(primary, isolated); err != nil {
		t.Fatal(err)
	}
	merged, err := os.ReadFile(filepath.Join(isolated, "config.toml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(merged)
	relocated := filepath.Join(isolated, ".tmp", "bundled-marketplaces", "openai-bundled")
	if !strings.Contains(text, "[marketplaces.openai-bundled]\nsource_type = \"local\"\nsource = \""+relocated+"\"") {
		t.Fatalf("expected the bundled marketplace source under the isolated home:\n%s", text)
	}
	if strings.Contains(text, bundled) {
		t.Fatalf("expected no reference to the primary bundled marketplace:\n%s", text)
	}
	for _, untouched := range []string{`source = "/elsewhere/runtime"`, `source = "https://example.com/plugins.git"`} {
		if !strings.Contains(text, untouched) {
			t.Fatalf("expected %q to be left alone:\n%s", untouched, text)
		}
	}
	link, err := os.Readlink(relocated)
	if err != nil || link != bundled {
		t.Fatalf("expected %s to link to the primary marketplace, got %q err=%v", relocated, link, err)
	}
	if err := syncIsolatedConfig(primary, isolated); err != nil {
		t.Fatalf("second sync must be idempotent: %v", err)
	}
}

func TestModelManagerURLFollowsALocalProxy(t *testing.T) {
	for _, tc := range []struct{ config, want string }{
		{"model = \"gpt-6-sol\"\nopenai_base_url = \"http://127.0.0.1:10100/v1\"\n", "http://127.0.0.1:10100/#/models"},
		{"openai_base_url = \"https://api.example.com/v1\"\n", ""},
		{"[model_providers.proxy]\nopenai_base_url = \"http://127.0.0.1:10100/v1\"\n", ""},
		{"model = \"gpt-6-sol\"\n", ""},
	} {
		home := t.TempDir()
		if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(tc.config), 0o600); err != nil {
			t.Fatal(err)
		}
		if got := ModelManagerURL(home); got != tc.want {
			t.Errorf("ModelManagerURL(%q) = %q, want %q", tc.config, got, tc.want)
		}
	}
}
