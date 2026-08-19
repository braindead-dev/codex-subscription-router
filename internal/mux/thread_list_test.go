package mux

import "testing"

func neverOriginates(string, map[string]any) bool { return false }

func TestMergeThreadListingsKeepsFreshestCopy(t *testing.T) {
	owners := map[string]string{"moved": "primary"}
	listings := []threadListing{
		{accountID: "primary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 1.0, "source": "primary"},
			{"id": "own", "updatedAt": 2.0},
		}},
		{accountID: "secondary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 3.0, "source": "secondary"},
		}},
	}
	threads, learned := mergeThreadListings(listings, func(id string) (string, bool) {
		owner, ok := owners[id]
		return owner, ok
	}, neverOriginates)
	if len(threads) != 2 {
		t.Fatalf("expected the moved thread to be listed once, got %d threads", len(threads))
	}
	for _, thread := range threads {
		if thread["id"] == "moved" && thread["source"] != "secondary" {
			t.Fatalf("expected the most recently updated copy of the moved thread, got %#v", thread)
		}
	}
	if _, relearned := learned["moved"]; relearned {
		t.Fatal("an assigned thread must not be reassigned by a listing")
	}
	if learned["own"] != "primary" {
		t.Fatalf("expected the unassigned thread to be learned as primary, got %q", learned["own"])
	}
}

func TestMergeThreadListingsAttributesUnknownThreadOnce(t *testing.T) {
	listings := []threadListing{
		{accountID: "secondary", threads: []map[string]any{{"id": "shared", "source": "secondary"}}},
		{accountID: "primary", threads: []map[string]any{{"id": "shared", "source": "primary"}}},
	}
	threads, learned := mergeThreadListings(listings, func(string) (string, bool) { return "", false }, neverOriginates)
	if len(threads) != 1 || threads[0]["source"] != "secondary" {
		t.Fatalf("expected equally fresh copies to keep the first listing, got %#v", threads)
	}
	if learned["shared"] != "secondary" {
		t.Fatalf("expected the unknown thread to be learned from its first listing, got %q", learned["shared"])
	}
}

func TestMergeThreadListingsKeepsTitleFromOriginatingAccount(t *testing.T) {
	listings := []threadListing{
		{accountID: "primary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 1.0, "path": "/home/primary/sessions/moved.jsonl", "preview": "clickhouse search", "name": nil},
		}},
		{accountID: "secondary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 3.0, "path": "/home/primary/sessions/moved.jsonl", "preview": "look into our search service", "name": "CH", "status": "active"},
		}},
	}
	threads, _ := mergeThreadListings(
		listings,
		func(string) (string, bool) { return "primary", true },
		func(accountID string, thread map[string]any) bool {
			return accountID == "primary" && thread["path"] == "/home/primary/sessions/moved.jsonl"
		},
	)
	if len(threads) != 1 {
		t.Fatalf("expected one merged thread, got %d", len(threads))
	}
	merged := threads[0]
	if merged["updatedAt"] != 3.0 || merged["status"] != "active" {
		t.Fatalf("expected activity from the freshest copy, got %#v", merged)
	}
	if merged["preview"] != "clickhouse search" {
		t.Fatalf("expected the preview from the originating account, got %#v", merged)
	}
	if merged["name"] != "CH" {
		t.Fatalf("expected the user-assigned name from whichever copy holds it, got %#v", merged)
	}
	if listings[1].threads[0]["preview"] != "look into our search service" {
		t.Fatal("merging must not mutate the source listing")
	}
}

func TestMergeThreadListingsKeepsNameSetOnOriginatingCopy(t *testing.T) {
	listings := []threadListing{
		{accountID: "primary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 1.0, "path": "/home/primary/x.jsonl", "preview": "generated", "name": "Renamed"},
		}},
		{accountID: "secondary", threads: []map[string]any{
			{"id": "moved", "updatedAt": 2.0, "path": "/home/primary/x.jsonl", "preview": "raw", "name": nil},
		}},
	}
	threads, _ := mergeThreadListings(
		listings,
		func(string) (string, bool) { return "primary", true },
		func(accountID string, _ map[string]any) bool { return accountID == "primary" },
	)
	if threads[0]["name"] != "Renamed" || threads[0]["preview"] != "generated" || threads[0]["updatedAt"] != 2.0 {
		t.Fatalf("unexpected merge: %#v", threads[0])
	}
}
