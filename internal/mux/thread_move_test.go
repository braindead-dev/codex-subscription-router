package mux

import (
	"encoding/json"
	"testing"
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
