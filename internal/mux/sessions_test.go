package mux

import (
	"io"
	"path/filepath"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func TestInboxThreadNamesOnlyChatInboxes(t *testing.T) {
	store, err := state.Open(t.TempDir(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	m, err := New(Options{RealExecutable: "codex", Store: store, Output: io.Discard})
	if err != nil {
		t.Fatal(err)
	}
	const threadID = "01a1393c-de92-7632-875a-21b880d280bc"
	if got, ok := m.inboxThread(InboxAddress(store.Root(), threadID)); !ok || got != threadID {
		t.Fatalf("inboxThread(inbox) = %q, %v", got, ok)
	}
	for _, address := range []string{
		"uds:/tmp/cc-socks/42.sock",
		"uds:" + filepath.Join(store.Root(), "inbox", "not-a-chat.sock"),
		"uds:" + filepath.Join(store.Root(), threadID+".sock"),
		threadID,
	} {
		if got, ok := m.inboxThread(address); ok {
			t.Fatalf("inboxThread(%q) = %q, want none", address, got)
		}
	}
}
