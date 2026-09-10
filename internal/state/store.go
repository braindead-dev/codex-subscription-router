package state

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
)

const stateVersion = 1

type Account struct {
	ID         string `json:"id"`
	Label      string `json:"label"`
	CodexHome  string `json:"codexHome"`
	Enabled    bool   `json:"enabled"`
	Controller bool   `json:"controller"`
	CreatedAt  int64  `json:"createdAt"`
}

type persistedState struct {
	Version      int                 `json:"version"`
	Accounts     []Account           `json:"accounts"`
	ThreadOwner  map[string]string   `json:"threadOwner"`
	SectionOrder map[string][]string `json:"sectionOrder,omitempty"`
	// PreferredAccount is the subscription new chats start on when the user
	// picked one in the composer; empty means the router chooses.
	PreferredAccount string `json:"preferredAccount,omitempty"`
}

// Store persists only routing metadata. OAuth credentials and conversation
// databases remain inside each account's isolated Codex home.
type Store struct {
	mu               sync.RWMutex
	root             string
	path             string
	primaryCodexHome string
	accounts         []Account
	owners           map[string]string
	sections         map[string][]string
	preferred        string
}

func Open(root, primaryCodexHome string) (*Store, error) {
	if root == "" {
		return nil, errors.New("state root is required")
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, fmt.Errorf("create state root: %w", err)
	}
	if err := os.Chmod(root, 0o700); err != nil {
		return nil, fmt.Errorf("secure state root: %w", err)
	}

	store := &Store{
		root:             root,
		path:             filepath.Join(root, "state.json"),
		primaryCodexHome: primaryCodexHome,
		owners:           make(map[string]string),
		sections:         make(map[string][]string),
	}
	data, err := os.ReadFile(store.path)
	switch {
	case err == nil:
		var persisted persistedState
		if err := json.Unmarshal(data, &persisted); err != nil {
			return nil, fmt.Errorf("read state: %w", err)
		}
		if persisted.Version != stateVersion {
			return nil, fmt.Errorf("unsupported state version %d", persisted.Version)
		}
		store.accounts = persisted.Accounts
		if persisted.ThreadOwner != nil {
			store.owners = persisted.ThreadOwner
		}
		if persisted.SectionOrder != nil {
			store.sections = persisted.SectionOrder
		}
		store.preferred = persisted.PreferredAccount
	case errors.Is(err, os.ErrNotExist):
		store.accounts = []Account{{
			ID:         "primary",
			Label:      "Primary",
			CodexHome:  primaryCodexHome,
			Enabled:    true,
			Controller: true,
			CreatedAt:  time.Now().Unix(),
		}}
		if err := store.saveLocked(); err != nil {
			return nil, err
		}
	default:
		return nil, fmt.Errorf("read state: %w", err)
	}
	for _, account := range store.accounts {
		if !store.isolatedHome(account.CodexHome) {
			continue
		}
		if err := syncIsolatedConfig(primaryCodexHome, account.CodexHome); err != nil {
			return nil, fmt.Errorf("sync account %q config: %w", account.ID, err)
		}
	}
	return store, nil
}

func (s *Store) Root() string {
	return s.root
}

// SyncManagedConfig propagates desktop-managed configuration (including
// plugins, marketplaces, skills, and MCP server definitions) to every
// isolated subscription. Credential stores and project trust remain local to
// each account; syncIsolatedConfig deliberately excludes both.
func (s *Store) SyncManagedConfig() error {
	s.mu.RLock()
	accounts := slices.Clone(s.accounts)
	primaryCodexHome := s.primaryCodexHome
	s.mu.RUnlock()

	for _, account := range accounts {
		if !s.isolatedHome(account.CodexHome) {
			continue
		}
		if err := syncIsolatedConfig(primaryCodexHome, account.CodexHome); err != nil {
			return fmt.Errorf("sync account %q config: %w", account.ID, err)
		}
	}
	return nil
}

func (s *Store) Accounts() []Account {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return slices.Clone(s.accounts)
}

func (s *Store) Account(id string) (Account, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.accountLocked(id)
}

func (s *Store) accountLocked(id string) (Account, bool) {
	for _, account := range s.accounts {
		if account.ID == id {
			return account, true
		}
	}
	return Account{}, false
}

func (s *Store) Controller() (Account, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, account := range s.accounts {
		if account.Controller {
			return account, true
		}
	}
	if len(s.accounts) == 0 {
		return Account{}, false
	}
	return s.accounts[0], true
}

func (s *Store) AddAccount(label string) (Account, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	label = strings.TrimSpace(label)
	if label == "" {
		label = fmt.Sprintf("Subscription %d", len(s.accounts)+1)
	}
	id, err := randomID()
	if err != nil {
		return Account{}, err
	}
	codexHome := filepath.Join(s.root, "accounts", id, "codex-home")
	if err := os.MkdirAll(codexHome, 0o700); err != nil {
		return Account{}, fmt.Errorf("create account home: %w", err)
	}
	if err := os.Chmod(codexHome, 0o700); err != nil {
		return Account{}, fmt.Errorf("secure account home: %w", err)
	}
	if err := syncIsolatedConfig(s.primaryCodexHome, codexHome); err != nil {
		return Account{}, fmt.Errorf("write account config: %w", err)
	}

	account := Account{
		ID:        id,
		Label:     label,
		CodexHome: codexHome,
		Enabled:   true,
		CreatedAt: time.Now().Unix(),
	}
	s.accounts = append(s.accounts, account)
	if err := s.saveLocked(); err != nil {
		return Account{}, err
	}
	return account, nil
}

// abandonedAccountAge is how long an added subscription may stay signed out
// before it is treated as an abandoned sign-in and removed.
const abandonedAccountAge = time.Hour

// PruneAbandonedAccounts removes subscriptions whose sign-in never
// completed: no credentials in their home after abandonedAccountAge. Their
// homes hold only managed config, so they are deleted with the record.
func (s *Store) PruneAbandonedAccounts(now time.Time) ([]Account, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	kept := s.accounts[:0:0]
	var pruned []Account
	for _, account := range s.accounts {
		signedIn := fileExists(filepath.Join(account.CodexHome, "auth.json"))
		recent := now.Sub(time.Unix(account.CreatedAt, 0)) < abandonedAccountAge
		if account.Controller || account.CodexHome == s.primaryCodexHome || signedIn || recent {
			kept = append(kept, account)
			continue
		}
		pruned = append(pruned, account)
	}
	if len(pruned) == 0 {
		return nil, nil
	}
	s.accounts = kept
	if err := s.saveLocked(); err != nil {
		return nil, err
	}
	for _, account := range pruned {
		if within(account.CodexHome, filepath.Join(s.root, "accounts")) {
			_ = os.RemoveAll(filepath.Dir(account.CodexHome))
		}
	}
	return pruned, nil
}

// isolatedHome reports whether a Codex home is one this store created under
// its accounts directory. Only such homes are ever rewritten from the
// Primary home; a user's own home is never treated as isolated, whatever
// CODEX_HOME the process was started with.
func (s *Store) isolatedHome(codexHome string) bool {
	return within(codexHome, filepath.Join(s.root, "accounts"))
}

func within(path, root string) bool {
	relative, err := filepath.Rel(root, path)
	return err == nil && relative != "." && !strings.HasPrefix(relative, "..")
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func (s *Store) UpdateAccount(id string, label *string, enabled *bool) (Account, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for index := range s.accounts {
		if s.accounts[index].ID != id {
			continue
		}
		if label != nil {
			trimmed := strings.TrimSpace(*label)
			if trimmed == "" {
				return Account{}, errors.New("account label cannot be empty")
			}
			s.accounts[index].Label = trimmed
		}
		if enabled != nil {
			s.accounts[index].Enabled = *enabled
		}
		if err := s.saveLocked(); err != nil {
			return Account{}, err
		}
		return s.accounts[index], nil
	}
	return Account{}, fmt.Errorf("account %q not found", id)
}

func (s *Store) ThreadOwner(threadID string) (string, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	owner, ok := s.owners[threadID]
	return owner, ok
}

func (s *Store) SetThreadOwner(threadID, accountID string) error {
	if threadID == "" || accountID == "" {
		return errors.New("thread and account IDs are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.owners[threadID] == accountID {
		return nil
	}
	s.owners[threadID] = accountID
	return s.saveLocked()
}

// PreferredAccount is the subscription the user chose for new chats, or
// empty when the router should choose.
func (s *Store) PreferredAccount() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.preferred
}

// SetPreferredAccount records the subscription new chats should start on.
// An empty id returns the choice to the router.
func (s *Store) SetPreferredAccount(accountID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if accountID != "" {
		if _, ok := s.accountLocked(accountID); !ok {
			return fmt.Errorf("unknown account %q", accountID)
		}
	}
	if s.preferred == accountID {
		return nil
	}
	s.preferred = accountID
	return s.saveLocked()
}

// SectionOrder is the pinned order of a section across every subscription.
// Each account's index orders only its own threads, so the multiplexer keeps
// the one order the sidebar shows.
func (s *Store) SectionOrder(sectionID string) []string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return append([]string(nil), s.sections[sectionID]...)
}

// SetSectionOrder replaces a section's order with the threads a listing
// actually contains.
func (s *Store) SetSectionOrder(sectionID string, threadIDs []string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if slices.Equal(s.sections[sectionID], threadIDs) {
		return nil
	}
	s.sections[sectionID] = append([]string(nil), threadIDs...)
	return s.saveLocked()
}

// MoveInSection places a thread before another thread of a section, or at
// its end when beforeThreadID is empty, removing it from every other section.
func (s *Store) MoveInSection(sectionID, threadID, beforeThreadID string) error {
	if sectionID == "" || threadID == "" {
		return errors.New("section and thread IDs are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.removeFromSectionsLocked(threadID)
	order := s.sections[sectionID]
	index := len(order)
	if beforeThreadID != "" {
		if at := slices.Index(order, beforeThreadID); at >= 0 {
			index = at
		}
	}
	s.sections[sectionID] = slices.Insert(order, index, threadID)
	return s.saveLocked()
}

// RemoveFromSections drops an unpinned thread from every section order.
func (s *Store) RemoveFromSections(threadID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.removeFromSectionsLocked(threadID) {
		return nil
	}
	return s.saveLocked()
}

func (s *Store) removeFromSectionsLocked(threadID string) bool {
	removed := false
	for sectionID, order := range s.sections {
		if at := slices.Index(order, threadID); at >= 0 {
			s.sections[sectionID] = slices.Delete(order, at, at+1)
			removed = true
		}
	}
	return removed
}

func (s *Store) ThreadCounts() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	counts := make(map[string]int)
	for _, accountID := range s.owners {
		counts[accountID]++
	}
	return counts
}

func (s *Store) saveLocked() error {
	persisted := persistedState{
		Version:          stateVersion,
		Accounts:         s.accounts,
		ThreadOwner:      s.owners,
		SectionOrder:     s.sections,
		PreferredAccount: s.preferred,
	}
	data, err := json.MarshalIndent(persisted, "", "  ")
	if err != nil {
		return fmt.Errorf("encode state: %w", err)
	}
	temporary := s.path + ".tmp"
	if err := os.WriteFile(temporary, append(data, '\n'), 0o600); err != nil {
		return fmt.Errorf("write state: %w", err)
	}
	if err := os.Chmod(temporary, 0o600); err != nil {
		return fmt.Errorf("secure state: %w", err)
	}
	if err := os.Rename(temporary, s.path); err != nil {
		return fmt.Errorf("commit state: %w", err)
	}
	return nil
}

func randomID() (string, error) {
	bytes := make([]byte, 8)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate account ID: %w", err)
	}
	return hex.EncodeToString(bytes), nil
}
