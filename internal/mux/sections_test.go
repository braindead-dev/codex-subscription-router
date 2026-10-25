package mux

import (
	"encoding/json"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func TestOrderSectionFollowsKnownOrderThenListing(t *testing.T) {
	threads := []map[string]any{{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}}
	arranged, order := orderSection(threads, []string{"c", "gone", "a"})
	got := make([]string, 0, len(arranged))
	for _, thread := range arranged {
		got = append(got, thread["id"].(string))
	}
	want := []string{"c", "a", "b", "d"}
	for i := range want {
		if got[i] != want[i] || order[i] != want[i] {
			t.Fatalf("arranged %v with order %v, want %v", got, order, want)
		}
	}
}

func TestScopeBeforeThreadPicksNextThreadTheAccountLists(t *testing.T) {
	params := json.RawMessage(`{"threadId":"x","sectionId":"pinned","beforeThreadId":"other"}`)
	move, ok := parseSectionMove(params)
	if !ok || move.BeforeThreadID != "other" || move.SectionID != "pinned" {
		t.Fatalf("unexpected move %#v", move)
	}
	own := map[string]bool{"x": true, "mine": true}
	scoped := scopeBeforeThread(params, move, []string{"a", "other", "x", "mine"}, func(id string) bool { return own[id] })
	var decoded map[string]any
	if err := json.Unmarshal(scoped, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["beforeThreadId"] != "mine" {
		t.Fatalf("expected the next listed thread, got %#v", decoded["beforeThreadId"])
	}
	scoped = scopeBeforeThread(params, move, []string{"a", "other"}, func(id string) bool { return own[id] })
	if err := json.Unmarshal(scoped, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["beforeThreadId"] != nil {
		t.Fatalf("expected a null anchor when nothing follows, got %#v", decoded["beforeThreadId"])
	}
	if string(scopeBeforeThread(params, move, []string{"other"}, func(string) bool { return true })) != string(params) {
		t.Fatal("expected untouched params when the account lists the anchor")
	}
	unpin, ok := parseSectionMove(json.RawMessage(`{"threadId":"x","sectionId":null,"beforeThreadId":null}`))
	if !ok || unpin.SectionID != "" || unpin.BeforeThreadID != "" {
		t.Fatalf("unexpected unpin %#v", unpin)
	}
}

func TestApplySectionViewReportsTheSidebarPin(t *testing.T) {
	pinned := map[string]any{"id": "sec", "name": "Pinned"}
	m := &Multiplexer{sections: sectionView{
		accounts: []string{"primary", "secondary"},
		copies: map[string]map[string]map[string]any{"moved": {
			"primary":   {"section": pinned, "sectionEnteredAt": 5.0},
			"secondary": {"section": nil, "sectionEnteredAt": nil},
		}},
	}}
	route := externalRoute{method: "thread/read", message: protocol.Request("thread/read", protocol.StringID("1"), json.RawMessage(`{"threadId":"moved"}`))}
	answer := protocol.Message{Result: json.RawMessage(`{"thread":{"id":"moved","section":null,"sectionEnteredAt":null}}`)}
	patched, ok := m.applySectionView(route, "secondary", answer)
	if !ok {
		t.Fatal("expected the owner's answer to be patched")
	}
	var decoded struct {
		Thread map[string]any `json:"thread"`
	}
	if err := json.Unmarshal(patched.Result, &decoded); err != nil {
		t.Fatal(err)
	}
	if section, _ := decoded.Thread["section"].(map[string]any); section["id"] != "sec" || decoded.Thread["sectionEnteredAt"] != 5.0 {
		t.Fatalf("expected the sidebar's section, got %#v", decoded.Thread)
	}
	if _, ok := m.applySectionView(route, "primary", answer); ok {
		t.Fatal("the account holding the pin answers for itself")
	}
}

// The sidebar lists all chats, the pinned section and the archived chats, in
// any order. Each listing must leave every pin where it is.
func TestSectionViewKeepsPinsWhateverTheLastListing(t *testing.T) {
	pinned := map[string]any{"id": "pin"}
	all := sectionViewOf([]threadListing{
		{accountID: "primary", threads: []map[string]any{
			{"id": "held-elsewhere", "section": nil},
			{"id": "own", "section": pinned},
		}},
		{accountID: "work", threads: []map[string]any{
			{"id": "held-elsewhere", "section": pinned},
		}},
	})
	archived := sectionViewOf([]threadListing{
		{accountID: "primary", threads: nil},
		{accountID: "old", threads: []map[string]any{{"id": "own", "section": nil}}},
	})
	var view sectionView
	view.merge(all)
	view.merge(archived)
	view.accounts = []string{"primary", "work", "old"}
	if home, _ := view.home("held-elsewhere"); home != "work" {
		t.Fatalf("an unpinned copy listed first took the pin's place: home %q", home)
	}
	if home, _ := view.home("own"); home != "primary" {
		t.Fatalf("an archived listing moved the pin: home %q", home)
	}
	if !view.inSection("own", "primary", "pin") || view.inSection("held-elsewhere", "primary", "pin") {
		t.Fatal("section membership must follow each account's own copy")
	}
}

func TestScopeBeforeThreadSkipsCopiesOutsideTheSection(t *testing.T) {
	pinned := map[string]any{"id": "pin"}
	view := sectionViewOf([]threadListing{
		{accountID: "primary", threads: []map[string]any{
			{"id": "moving", "section": pinned},
			{"id": "unpinned-here", "section": nil},
			{"id": "next-pin", "section": pinned},
		}},
		{accountID: "work", threads: []map[string]any{{"id": "unpinned-here", "section": pinned}}},
	})
	params := json.RawMessage(`{"threadId":"moving","sectionId":"pin","beforeThreadId":"unpinned-here"}`)
	move, _ := parseSectionMove(params)
	scoped := scopeBeforeThread(params, move, []string{"unpinned-here", "moving", "next-pin"}, func(id string) bool {
		return view.inSection(id, "primary", "pin")
	})
	var decoded map[string]any
	if err := json.Unmarshal(scoped, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["beforeThreadId"] != "next-pin" {
		t.Fatalf("anchored on %v; Codex refuses an anchor outside the section", decoded["beforeThreadId"])
	}
}

func TestSectionViewRecordsAcceptedMoves(t *testing.T) {
	view := sectionViewOf([]threadListing{{accountID: "primary", threads: []map[string]any{{"id": "chat", "section": nil}}}})
	view.record("primary", sectionMove{ThreadID: "chat", SectionID: "pin"})
	if !view.inSection("chat", "primary", "pin") {
		t.Fatal("a pin the account accepted must count before the next listing")
	}
	view.record("primary", sectionMove{ThreadID: "chat"})
	if view.inSection("chat", "primary", "pin") {
		t.Fatal("an unpin the account accepted must count before the next listing")
	}
}
