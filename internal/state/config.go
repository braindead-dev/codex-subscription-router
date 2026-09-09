package state

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const isolatedCredentialConfig = `cli_auth_credentials_store = "file"
mcp_oauth_credentials_store = "file"`

// syncIsolatedConfig shares desktop-managed settings, MCP servers, and project
// trust with an isolated subscription while keeping its credentials local.
// Trust decisions the isolated account recorded itself take precedence.
func syncIsolatedConfig(primaryCodexHome, isolatedCodexHome string) error {
	if isolatedCodexHome == "" {
		return errors.New("isolated Codex home is required")
	}
	if err := os.MkdirAll(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("create isolated Codex home: %w", err)
	}
	if err := os.Chmod(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("secure isolated Codex home: %w", err)
	}

	primaryConfig, err := readConfig(filepath.Join(primaryCodexHome, "config.toml"))
	if err != nil {
		return fmt.Errorf("read primary config: %w", err)
	}
	configPath := filepath.Join(isolatedCodexHome, "config.toml")
	isolatedConfig, err := readConfig(configPath)
	if err != nil {
		return fmt.Errorf("read isolated config: %w", err)
	}

	managed := filterConfig(primaryConfig, func(section string) bool {
		return !isProjectSection(section)
	})
	managed = removeTopLevelCredentialSettings(managed)
	projects := mergeProjectSections(
		filterConfig(isolatedConfig, isProjectSection),
		filterConfig(primaryConfig, isProjectSection),
	)

	parts := []string{isolatedCredentialConfig}
	if managed = strings.TrimSpace(managed); managed != "" {
		parts = append(parts, managed)
	}
	if projects = strings.TrimSpace(projects); projects != "" {
		parts = append(parts, projects)
	}
	contents := []byte(strings.Join(parts, "\n\n") + "\n")
	temporaryPath := configPath + ".tmp"
	if err := os.WriteFile(temporaryPath, contents, 0o600); err != nil {
		return fmt.Errorf("write temporary config: %w", err)
	}
	if err := os.Chmod(temporaryPath, 0o600); err != nil {
		return fmt.Errorf("secure temporary config: %w", err)
	}
	if err := os.Rename(temporaryPath, configPath); err != nil {
		return fmt.Errorf("commit config: %w", err)
	}
	if err := linkSharedHomeContent(primaryCodexHome, isolatedCodexHome); err != nil {
		return err
	}
	return linkSharedPluginCache(primaryCodexHome, isolatedCodexHome)
}

// sharedHomeEntries are user-authored Codex home entries that describe how
// the user works rather than who they are signed in as, so every subscription
// should see the same copy.
var sharedHomeEntries = []string{"AGENTS.md", "agents", "hooks.json", "skills"}

// linkSharedHomeContent points each shared entry of the isolated home at the
// Primary home's copy. An entry the isolated account already has as a real
// file or directory with its own content is left alone; an empty directory or
// one holding only Codex's managed `.system` folder is replaced by the link.
func linkSharedHomeContent(primaryCodexHome, isolatedCodexHome string) error {
	for _, name := range sharedHomeEntries {
		source := filepath.Join(primaryCodexHome, name)
		if _, err := os.Lstat(source); err != nil {
			continue
		}
		target := filepath.Join(isolatedCodexHome, name)
		info, err := os.Lstat(target)
		switch {
		case err == nil && info.Mode()&os.ModeSymlink != 0:
			if current, readErr := os.Readlink(target); readErr == nil && current == source {
				continue
			}
			if err := os.Remove(target); err != nil {
				return fmt.Errorf("replace shared link %s: %w", name, err)
			}
		case err == nil && info.IsDir():
			if !isManagedOnlyDirectory(target) {
				continue
			}
			if err := os.RemoveAll(target); err != nil {
				return fmt.Errorf("replace empty %s: %w", name, err)
			}
		case err == nil:
			continue
		case !errors.Is(err, os.ErrNotExist):
			return fmt.Errorf("inspect %s: %w", name, err)
		}
		if err := os.Symlink(source, target); err != nil {
			return fmt.Errorf("link shared %s: %w", name, err)
		}
	}
	return nil
}

// linkSharedPluginCache makes installed plugin packages available to every
// subscription. Plugin configuration is already copied from the primary home,
// while OAuth credentials and connection state remain in each isolated home.
// Keeping separate package caches can therefore leave a plugin enabled in
// config but unavailable to the account's app-server until it is installed a
// second time. The cache is derived, reinstallable data, so the primary cache
// is the single shared source of truth.
func linkSharedPluginCache(primaryCodexHome, isolatedCodexHome string) error {
	source := filepath.Join(primaryCodexHome, "plugins", "cache")
	info, err := os.Stat(source)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("inspect primary plugin cache: %w", err)
	}
	if !info.IsDir() {
		return fmt.Errorf("primary plugin cache is not a directory: %s", source)
	}

	pluginsRoot := filepath.Join(isolatedCodexHome, "plugins")
	if err := os.MkdirAll(pluginsRoot, 0o700); err != nil {
		return fmt.Errorf("create isolated plugins directory: %w", err)
	}
	target := filepath.Join(pluginsRoot, "cache")
	targetInfo, err := os.Lstat(target)
	switch {
	case err == nil && targetInfo.Mode()&os.ModeSymlink != 0:
		if current, readErr := os.Readlink(target); readErr == nil && current == source {
			return nil
		}
		if err := os.Remove(target); err != nil {
			return fmt.Errorf("replace shared plugin cache link: %w", err)
		}
	case err == nil && targetInfo.IsDir():
		retired := target + ".pre-shared-" + time.Now().Format("20060102-150405")
		if err := os.Rename(target, retired); err != nil {
			return fmt.Errorf("retire isolated plugin cache: %w", err)
		}
	case err == nil:
		return fmt.Errorf("isolated plugin cache is not a directory: %s", target)
	case !errors.Is(err, os.ErrNotExist):
		return fmt.Errorf("inspect isolated plugin cache: %w", err)
	}
	if err := os.Symlink(source, target); err != nil {
		return fmt.Errorf("link shared plugin cache: %w", err)
	}
	return nil
}

func isManagedOnlyDirectory(path string) bool {
	entries, err := os.ReadDir(path)
	if err != nil {
		return false
	}
	for _, entry := range entries {
		if entry.Name() != ".system" {
			return false
		}
	}
	return true
}

func readConfig(path string) ([]byte, error) {
	contents, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	return contents, err
}

func filterConfig(contents []byte, keep func(section string) bool) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(string(contents), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if keep(section) {
			builder.WriteString(line)
			builder.WriteByte('\n')
		}
	}
	return builder.String()
}

func removeTopLevelCredentialSettings(contents string) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(contents, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if section == "" && (strings.HasPrefix(trimmed, "cli_auth_credentials_store =") ||
			strings.HasPrefix(trimmed, "mcp_oauth_credentials_store =")) {
			continue
		}
		builder.WriteString(line)
		builder.WriteByte('\n')
	}
	return builder.String()
}

// mergeProjectSections appends project sections from shared that the local
// configuration does not define. Sections are compared by header, so a
// trust level recorded by the isolated account is never overridden.
func mergeProjectSections(local, shared string) string {
	defined := make(map[string]struct{})
	for _, header := range projectSectionHeaders(local) {
		defined[header] = struct{}{}
	}
	var builder strings.Builder
	builder.WriteString(local)
	section := ""
	for _, line := range strings.Split(shared, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = trimmed
		}
		if section == "" {
			continue
		}
		if _, skip := defined[section]; skip {
			continue
		}
		builder.WriteString(line)
		builder.WriteByte('\n')
	}
	return builder.String()
}

func projectSectionHeaders(contents string) []string {
	headers := make([]string, 0)
	for _, line := range strings.Split(contents, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			headers = append(headers, trimmed)
		}
	}
	return headers
}

func isProjectSection(section string) bool {
	return section == "projects" || strings.HasPrefix(section, "projects.")
}

func samePath(left, right string) bool {
	if left == "" || right == "" {
		return false
	}
	leftAbsolute, leftErr := filepath.Abs(left)
	rightAbsolute, rightErr := filepath.Abs(right)
	if leftErr != nil || rightErr != nil {
		return filepath.Clean(left) == filepath.Clean(right)
	}
	return filepath.Clean(leftAbsolute) == filepath.Clean(rightAbsolute)
}
