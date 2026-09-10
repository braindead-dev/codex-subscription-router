package mux

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

// MoveThread reassigns a chat to another subscription at the user's request,
// carrying its history over and closing the session the previous owner held.
func (m *Multiplexer) MoveThread(ctx context.Context, threadID, accountID string) (AccountSnapshot, error) {
	owner, ok := m.store.ThreadOwner(threadID)
	if !ok {
		return AccountSnapshot{}, fmt.Errorf("thread %q has no subscription assignment", threadID)
	}
	target, ok := m.store.Account(accountID)
	if !ok || !target.Enabled {
		return AccountSnapshot{}, fmt.Errorf("unknown subscription %q", accountID)
	}
	if owner == accountID {
		return m.accountSnapshotWithProfile(ctx, accountID, true)
	}
	if err := m.resumeThreadOnAccount(ctx, threadID, owner, accountID); err != nil {
		if errors.Is(err, errStillOpen) || errors.Is(err, errUnsettled) {
			return AccountSnapshot{}, err
		}
		return AccountSnapshot{}, fmt.Errorf("move chat to %s: %w", target.Label, err)
	}
	if err := m.store.SetThreadOwner(threadID, accountID); err != nil {
		return AccountSnapshot{}, err
	}
	m.releaseThread(ctx, owner, threadID)
	m.publish(Event{
		Type:      "thread-moved",
		AccountID: accountID,
		Message:   fmt.Sprintf("Chat moved to %s", target.Label),
		Data:      map[string]any{"threadId": threadID, "previousAccountId": owner},
	})
	return m.accountSnapshotWithProfile(ctx, accountID, true)
}

// releaseThread closes the session an account still holds for a chat that
// now runs elsewhere. Codex has no unload request, but archiving and
// unarchiving a thread drops its in-memory session while leaving the rollout
// and the index row untouched, so the account can take the chat back later
// without a stale numbering cursor. The desktop must not see the detour, so
// the account's notifications about the chat are swallowed while it runs.
func (m *Multiplexer) releaseThread(ctx context.Context, accountID, threadID string) {
	child, ok := m.child(accountID)
	if !ok || !threadLoadedOn(ctx, child, threadID) {
		return
	}
	m.muteNotifications(accountID, threadID)
	defer time.AfterFunc(5*time.Second, func() {
		m.unmuteNotifications(accountID, threadID)
	})
	params, _ := json.Marshal(map[string]any{"threadId": threadID})
	if _, err := child.Request(ctx, "thread/archive", params); err != nil {
		return
	}
	_, _ = child.Request(ctx, "thread/unarchive", params)
}

type mutedNotification struct {
	accountID string
	threadID  string
}

func (m *Multiplexer) muteNotifications(accountID, threadID string) {
	m.mutedMu.Lock()
	defer m.mutedMu.Unlock()
	if m.muted == nil {
		m.muted = make(map[mutedNotification]struct{})
	}
	m.muted[mutedNotification{accountID, threadID}] = struct{}{}
}

func (m *Multiplexer) unmuteNotifications(accountID, threadID string) {
	m.mutedMu.Lock()
	defer m.mutedMu.Unlock()
	delete(m.muted, mutedNotification{accountID, threadID})
}

// mutedNotification reports whether a child's notification belongs to a
// release in progress and must not reach the desktop.
func (m *Multiplexer) mutedNotification(accountID string, params json.RawMessage) bool {
	m.mutedMu.Lock()
	defer m.mutedMu.Unlock()
	if len(m.muted) == 0 {
		return false
	}
	_, muted := m.muted[mutedNotification{accountID, notificationThreadID(params)}]
	return muted
}

// notificationThreadID reads the thread a notification is about, whether it
// carries the id at the top level or inside a thread summary.
func notificationThreadID(params json.RawMessage) string {
	var decoded struct {
		ThreadID string `json:"threadId"`
	}
	if json.Unmarshal(params, &decoded) == nil && decoded.ThreadID != "" {
		return decoded.ThreadID
	}
	return threadIDFromResult(params)
}

// preferredAccount is the subscription the user picked for new chats when it
// can take one: connected, with capacity, and able to run the model.
func (m *Multiplexer) preferredAccount(ctx context.Context, unsupported map[string]struct{}) (state.Account, bool) {
	id := m.store.PreferredAccount()
	if id == "" {
		return state.Account{}, false
	}
	if _, skip := unsupported[id]; skip {
		return state.Account{}, false
	}
	account, ok := m.store.Account(id)
	if !ok || !account.Enabled {
		return state.Account{}, false
	}
	snapshot, err := m.routingSnapshot(ctx, id)
	if err != nil || !accountHasCapacity(snapshot) {
		return state.Account{}, false
	}
	return account, true
}

// PreferredAccount is the subscription new chats start on, or empty when the
// router chooses.
func (m *Multiplexer) PreferredAccount() string {
	return m.store.PreferredAccount()
}

// SetPreferredAccount pins new chats to a subscription; an empty id returns
// the choice to the router.
func (m *Multiplexer) SetPreferredAccount(accountID string) error {
	if err := m.store.SetPreferredAccount(accountID); err != nil {
		return err
	}
	m.publish(Event{Type: "preferred-account-updated", AccountID: accountID})
	return nil
}
