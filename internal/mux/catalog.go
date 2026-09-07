package mux

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// sqlite3Binary is macOS's bundled SQLite. Shelling out to it keeps the
// multiplexer free of a third-party SQLite dependency.
const sqlite3Binary = "/usr/bin/sqlite3"

// unifiedCatalogFlag enables the cross-account thread catalog when present.
// Toggling is a file touch, so it needs no rebuild and defaults to off.
const unifiedCatalogFlag = "unified-catalog.enabled"

// catalogExcludedColumns are per-account thread columns that must not be copied
// into another account's index: the section foreign key would dangle, and pins
// are a local preference. Omitted columns fall back to their schema defaults.
var catalogExcludedColumns = map[string]struct{}{
	"thread_section_id":     {},
	"section_position":      {},
	"section_entered_at_ms": {},
	"is_pinned":             {},
}

type catalogHome struct {
	id   string
	path string
}

func (m *Multiplexer) unifiedCatalogEnabled() bool {
	_, err := os.Stat(filepath.Join(m.store.Root(), unifiedCatalogFlag))
	return err == nil
}

func (m *Multiplexer) reconcileUnifiedCatalogLoop(ctx context.Context) {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if !m.unifiedCatalogEnabled() {
				continue
			}
			err := reconcileUnifiedCatalog(m.connectedCatalogHomes())
			if errors.Is(err, errPaginatedThreadStore) {
				fmt.Fprintf(os.Stderr, "codex-mux: %v\n", err)
				return
			}
			if err != nil {
				fmt.Fprintf(os.Stderr, "codex-mux: reconcile unified catalog: %v\n", err)
			}
		}
	}
}

func (m *Multiplexer) connectedCatalogHomes() []catalogHome {
	ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
	defer cancel()
	homes := make([]catalogHome, 0)
	seen := make(map[string]struct{})
	for _, snapshot := range m.routingSnapshots(ctx) {
		if !snapshot.Enabled || !snapshot.Connected || snapshot.AuthType != "chatgpt" {
			continue
		}
		account, ok := m.store.Account(snapshot.ID)
		if !ok || account.CodexHome == "" {
			continue
		}
		clean := filepath.Clean(account.CodexHome)
		if _, dup := seen[clean]; dup {
			continue
		}
		seen[clean] = struct{}{}
		homes = append(homes, catalogHome{id: snapshot.ID, path: clean})
	}
	return homes
}

// reconcileUnifiedCatalog makes every connected account's thread index list the
// threads owned by the others. It only ever inserts rows that clone an existing
// row from the owning account, never modifies or deletes a row, and skips any
// account whose database cannot be reached, so a failure degrades to "a thread
// is missing from one list" rather than touching another process's data.
func reconcileUnifiedCatalog(homes []catalogHome) error {
	if len(homes) < 2 {
		return nil
	}
	var failures []string
	for _, target := range homes {
		targetDB := stateDatabase(target.path)
		if !fileExists(targetDB) {
			continue
		}
		targetColumns, err := threadColumns(targetDB)
		if err != nil || len(targetColumns) == 0 {
			failures = append(failures, fmt.Sprintf("%s: read columns: %v", target.id, err))
			continue
		}
		if err := catalogSupported(targetColumns); err != nil {
			return err
		}
		for _, owner := range homes {
			if owner.path == target.path {
				continue
			}
			ownerDB := stateDatabase(owner.path)
			if !fileExists(ownerDB) {
				continue
			}
			if err := mirrorThreads(ownerDB, targetDB, targetColumns); err != nil {
				failures = append(failures, fmt.Sprintf("%s<-%s: %v", target.id, owner.id, err))
			}
		}
	}
	if len(failures) > 0 {
		return errors.New(strings.Join(failures, "; "))
	}
	return nil
}

// errPaginatedThreadStore is why mirroring stays off on Codex 0.153 and later
// even with the flag present. That Codex keeps a per-account history
// projection and numbers rollout records per session, so a mirrored row is
// unusable on the other account and, if resumed there, corrupts the rollout
// for both. The paginated store announces itself with the history_mode column.
var errPaginatedThreadStore = errors.New(
	"unified catalog is unavailable on this Codex: its thread store keeps a " +
		"per-account history projection, and mirrored threads would be unusable " +
		"or corrupted; the flag is ignored",
)

func catalogSupported(columns map[string]struct{}) error {
	if _, paginated := columns["history_mode"]; paginated {
		return errPaginatedThreadStore
	}
	return nil
}

func mirrorThreads(ownerDB, targetDB string, targetColumns map[string]struct{}) error {
	ownerColumns, err := threadColumns(ownerDB)
	if err != nil {
		return fmt.Errorf("read owner columns: %w", err)
	}
	copyColumns := make([]string, 0, len(targetColumns))
	for column := range targetColumns {
		if _, excluded := catalogExcludedColumns[column]; excluded {
			continue
		}
		if _, shared := ownerColumns[column]; shared {
			copyColumns = append(copyColumns, column)
		}
	}
	sort.Strings(copyColumns)
	required, err := requiredThreadColumns(targetDB)
	if err != nil {
		return fmt.Errorf("read required columns: %w", err)
	}
	present := make(map[string]struct{}, len(copyColumns))
	for _, column := range copyColumns {
		present[column] = struct{}{}
	}
	for _, column := range required {
		if _, ok := present[column]; !ok {
			// The target needs a column the owner cannot supply; skip rather
			// than build an invalid row.
			return nil
		}
	}
	list := strings.Join(quoteIdentifiers(copyColumns), ", ")
	script := strings.Join([]string{
		"PRAGMA busy_timeout=5000;",
		fmt.Sprintf("ATTACH DATABASE '%s' AS src;", escapeSQLLiteral(ownerDB)),
		fmt.Sprintf(
			"INSERT OR IGNORE INTO threads (%s) SELECT %s FROM src.threads WHERE archived=0;",
			list, list,
		),
		"DETACH DATABASE src;",
	}, "\n")
	return runSQLite(targetDB, script)
}

func threadColumns(database string) (map[string]struct{}, error) {
	out, err := querySQLite(database, "SELECT name FROM pragma_table_info('threads');")
	if err != nil {
		return nil, err
	}
	columns := make(map[string]struct{})
	for _, name := range splitLines(out) {
		columns[name] = struct{}{}
	}
	return columns, nil
}

func requiredThreadColumns(database string) ([]string, error) {
	out, err := querySQLite(
		database,
		"SELECT name FROM pragma_table_info('threads') WHERE \"notnull\"=1 AND dflt_value IS NULL;",
	)
	if err != nil {
		return nil, err
	}
	return splitLines(out), nil
}

func runSQLite(database, script string) error {
	command := exec.Command(sqlite3Binary, database)
	command.Stdin = strings.NewReader(script)
	output, err := command.CombinedOutput()
	if err != nil {
		return fmt.Errorf("sqlite3: %v: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func querySQLite(database, query string) (string, error) {
	output, err := exec.Command(sqlite3Binary, database, query).CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("sqlite3: %v: %s", err, strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func stateDatabase(home string) string {
	return filepath.Join(home, "state_5.sqlite")
}

func splitLines(text string) []string {
	lines := make([]string, 0)
	for _, line := range strings.Split(text, "\n") {
		if trimmed := strings.TrimSpace(line); trimmed != "" {
			lines = append(lines, trimmed)
		}
	}
	return lines
}

func quoteIdentifiers(names []string) []string {
	quoted := make([]string, len(names))
	for index, name := range names {
		quoted[index] = `"` + strings.ReplaceAll(name, `"`, `""`) + `"`
	}
	return quoted
}

func escapeSQLLiteral(value string) string {
	return strings.ReplaceAll(value, "'", "''")
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
