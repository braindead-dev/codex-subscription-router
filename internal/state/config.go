package state

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
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
	managed, bundledMarketplaces := relocateBundledMarketplaces(managed, primaryCodexHome, isolatedCodexHome)
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
	if err := linkBundledMarketplaces(primaryCodexHome, isolatedCodexHome, bundledMarketplaces); err != nil {
		return err
	}
	return linkSharedPluginCache(primaryCodexHome, isolatedCodexHome)
}

// bundledMarketplacesDir is where the desktop app materializes the plugin
// marketplaces it ships with (`openai-bundled` and friends). Codex only
// trusts such a marketplace when its source lives under the app-server's own
// home, so an isolated account handed the Primary path silently drops the
// marketplace along with every plugin it provides, including the browser and
// computer-use runtimes.
const bundledMarketplacesDir = ".tmp/bundled-marketplaces"

// relocateBundledMarketplaces rewrites marketplace sources that point into the
// Primary home's bundled marketplaces so they point into the isolated home
// instead, returning the marketplace directory names that must be linked.
func relocateBundledMarketplaces(contents, primaryCodexHome, isolatedCodexHome string) (string, []string) {
	primaryRoot := filepath.Join(primaryCodexHome, bundledMarketplacesDir)
	isolatedRoot := filepath.Join(isolatedCodexHome, bundledMarketplacesDir)
	var builder strings.Builder
	var names []string
	section := ""
	for _, line := range strings.Split(contents, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if strings.HasPrefix(section, "marketplaces.") {
			if source, ok := tomlStringValue(trimmed, "source"); ok {
				if name, ok := marketplaceUnder(primaryRoot, source); ok {
					line = fmt.Sprintf("source = %q", filepath.Join(isolatedRoot, name))
					names = append(names, name)
				}
			}
		}
		builder.WriteString(line)
		builder.WriteByte('\n')
	}
	return builder.String(), names
}

// tomlStringValue extracts the quoted value of `key = "..."` from a config
// line, when the line assigns that key a plain string.
func tomlStringValue(line, key string) (string, bool) {
	rest, ok := strings.CutPrefix(line, key)
	if !ok {
		return "", false
	}
	rest = strings.TrimSpace(rest)
	rest, ok = strings.CutPrefix(rest, "=")
	if !ok {
		return "", false
	}
	value, err := strconv.Unquote(strings.TrimSpace(rest))
	if err != nil {
		return "", false
	}
	return value, true
}

// marketplaceUnder returns the marketplace directory name when source is a
// direct child of root.
func marketplaceUnder(root, source string) (string, bool) {
	relative, err := filepath.Rel(root, filepath.Clean(source))
	if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", false
	}
	if strings.ContainsRune(relative, filepath.Separator) {
		return "", false
	}
	return relative, true
}

// linkBundledMarketplaces mirrors each named bundled marketplace of the
// Primary home into the isolated home as a symlink, so the relocated
// marketplace source resolves to the copy the desktop app maintains. A real
// directory the isolated home already holds is its own materialization and
// is kept.
func linkBundledMarketplaces(primaryCodexHome, isolatedCodexHome string, names []string) error {
	if len(names) == 0 {
		return nil
	}
	root := filepath.Join(isolatedCodexHome, bundledMarketplacesDir)
	if err := os.MkdirAll(root, 0o700); err != nil {
		return fmt.Errorf("create bundled marketplaces directory: %w", err)
	}
	for _, name := range names {
		source := filepath.Join(primaryCodexHome, bundledMarketplacesDir, name)
		target := filepath.Join(root, name)
		info, err := os.Lstat(target)
		switch {
		case err == nil && info.Mode()&os.ModeSymlink != 0:
			if current, readErr := os.Readlink(target); readErr == nil && current == source {
				continue
			}
			if err := os.Remove(target); err != nil {
				return fmt.Errorf("replace bundled marketplace link %s: %w", name, err)
			}
		case err == nil:
			continue
		case !errors.Is(err, os.ErrNotExist):
			return fmt.Errorf("inspect bundled marketplace %s: %w", name, err)
		}
		if err := os.Symlink(source, target); err != nil {
			return fmt.Errorf("link bundled marketplace %s: %w", name, err)
		}
	}
	return nil
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
