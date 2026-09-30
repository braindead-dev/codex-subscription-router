package mux

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func TestMutedNotificationsCoverOnlyTheReleasedThread(t *testing.T) {
	m := &Multiplexer{}
	m.muteNotifications("work", "thread-1")
	params := json.RawMessage(`{"threadId":"thread-1"}`)
	if !m.mutedNotification("work", params) {
		t.Fatal("expected the released thread's notification to be muted")
	}
	if !m.mutedNotification("work", json.RawMessage(`{"thread":{"id":"thread-1"}}`)) {
		t.Fatal("expected a thread summary notification to be muted")
	}
	if m.mutedNotification("primary", params) {
		t.Fatal("another account's notification must pass")
	}
	if m.mutedNotification("work", json.RawMessage(`{"threadId":"thread-2"}`)) {
		t.Fatal("another thread's notification must pass")
	}
	m.unmuteNotifications("work", "thread-1")
	if m.mutedNotification("work", params) {
		t.Fatal("expected the mute to lift")
	}
}

func TestUnrecordedThreadBelongsToTheHomeHoldingIt(t *testing.T) {
	root := t.TempDir()
	store, err := state.Open(filepath.Join(root, "mux"), filepath.Join(root, "primary"))
	if err != nil {
		t.Fatal(err)
	}
	work, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	const threadID = "01a0ef16-5283-7f32-8373-35b19b92feca"
	day := filepath.Join(work.CodexHome, "sessions", "2026", "09", "29")
	if err := os.MkdirAll(day, 0o700); err != nil {
		t.Fatal(err)
	}
	rollout := filepath.Join(day, "rollout-2026-09-29T14-33-32-"+threadID+".jsonl")
	if err := os.WriteFile(rollout, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	m := &Multiplexer{store: store}
	if owner, ok := m.threadOwner(threadID); !ok || owner != work.ID {
		t.Fatalf("threadOwner = %q, %v; want %q", owner, ok, work.ID)
	}
	if owner, ok := store.ThreadOwner(threadID); !ok || owner != work.ID {
		t.Fatalf("the found owner was not recorded: %q, %v", owner, ok)
	}
	if _, ok := m.threadOwner("01a0ffff-0000-7000-8000-000000000000"); ok {
		t.Fatal("a chat no home holds has no owner")
	}
}

func TestSecondaryAccountsForwardChatAndRequestNotifications(t *testing.T) {
	root := t.TempDir()
	store, err := state.Open(filepath.Join(root, "mux"), filepath.Join(root, "primary"))
	if err != nil {
		t.Fatal(err)
	}
	work, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	m := &Multiplexer{store: store}
	notification := func(method, params string) protocol.Message {
		return protocol.Message{Method: method, Params: json.RawMessage(params)}
	}
	for _, forwarded := range []protocol.Message{
		notification("turn/started", `{"threadId":"t"}`),
		notification("mcpServer/startupStatus/updated", `{"threadId":"t","name":"code-review","status":"ready"}`),
		notification("serverRequest/resolved", `{"threadId":"t","requestId":"r"}`),
		notification("mcpServer/event/stream/notification", `{"streamId":"s"}`),
		notification("process/outputDelta", `{"processId":"p"}`),
	} {
		if !m.shouldForwardNotification(work.ID, forwarded) {
			t.Errorf("%s from a secondary account was dropped", forwarded.Method)
		}
	}
	for _, dropped := range []protocol.Message{
		notification("skills/changed", `{}`),
		notification("mcpServer/startupStatus/updated", `{"threadId":null,"name":"code-review"}`),
		notification("remoteControl/status/changed", `{}`),
	} {
		if m.shouldForwardNotification(work.ID, dropped) {
			t.Errorf("account-wide %s from a secondary account was forwarded", dropped.Method)
		}
	}
	if !m.shouldForwardNotification("primary", notification("skills/changed", `{}`)) {
		t.Error("the controller's account-wide notifications must pass")
	}
}
