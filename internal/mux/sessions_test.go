package mux

import (
	"io"
	"path/filepath"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/peers"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func TestSessionDiscoveryMatchesTopicAndSortsByActivity(t *testing.T) {
	sessions := []peers.Session{
		{Name: "older", UpdatedAt: 1000},
		{Name: "sixtyfour-monorepo-31", CWD: "/work/Sixtyfour-Monorepo", Topic: "Make a CLI for forum.exploit.in", UpdatedAt: 3000},
		{Name: "middle", UpdatedAt: 2000},
	}
	if !sessionMatches(sessions[1], "forum.exploit.in") || !sessionMatches(sessions[1], "sixtyfour") || sessionMatches(sessions[1], "unrelated") {
		t.Fatal("session topic, cwd, or title search failed")
	}
	sortSessions(sessions)
	if sessions[0].Name != "sixtyfour-monorepo-31" || sessions[1].Name != "middle" || sessions[2].Name != "older" {
		t.Fatalf("sessions not sorted by recent activity: %+v", sessions)
	}
}

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
