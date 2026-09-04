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
		homes:  map[string]string{"moved": "primary"},
		copies: map[string]map[string]any{"moved": {"section": pinned, "sectionEnteredAt": 5.0}},
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
