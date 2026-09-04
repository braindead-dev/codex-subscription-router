package mux

import (
	"context"
	"encoding/json"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func (m *Multiplexer) aggregateThreadList(request protocol.Message) {
	entries := m.childEntries()
	type result struct {
		accountID string
		threads   []map[string]any
	}
	results := make(chan result, len(entries))
	var wait sync.WaitGroup
	for _, entry := range entries {
		wait.Add(1)
		go func(entry childEntry) {
			defer wait.Done()
			results <- result{accountID: entry.account.ID, threads: m.listAllThreads(entry, request.Params)}
		}(entry)
	}
	wait.Wait()
	close(results)

	listings := make([]threadListing, 0, len(entries))
	for accountResult := range results {
		listings = append(listings, threadListing(accountResult))
	}
	controllerID := ""
	if controller, ok := m.store.Controller(); ok {
		controllerID = controller.ID
	}
	sort.SliceStable(listings, func(i, j int) bool {
		if (listings[i].accountID == controllerID) != (listings[j].accountID == controllerID) {
			return listings[i].accountID == controllerID
		}
		return listings[i].accountID < listings[j].accountID
	})
	threads, learned, view := mergeThreadListings(listings, m.store.ThreadOwner, m.threadOriginatesFrom)
	for threadID, accountID := range learned {
		_ = m.store.SetThreadOwner(threadID, accountID)
	}
	view.listed = listedThreads(listings)
	m.rememberListings(view)
	switch sectionID := sectionListingID(request.Params); {
	case sectionID != "" && listsBySectionPosition(request.Params):
		var order []string
		threads, order = orderSection(threads, m.store.SectionOrder(sectionID))
		_ = m.store.SetSectionOrder(sectionID, order)
	case !listsBySectionPosition(request.Params):
		sortThreads(threads)
	}
	encoded, err := json.Marshal(map[string]any{"data": threads, "nextCursor": nil})
	if err != nil {
		m.write(protocol.Failure(request.ID, -32603, "failed to merge thread list"))
		return
	}
	m.write(protocol.Success(request.ID, encoded))
}

// listsBySectionPosition reports a listing whose order is the answer: the
// desktop reads a pinned section with sortKey section_position and shows the
// threads in the order returned, which the multiplexer keeps across accounts.
func listsBySectionPosition(params json.RawMessage) bool {
	var decoded struct {
		SortKey string `json:"sortKey"`
	}
	return json.Unmarshal(params, &decoded) == nil && decoded.SortKey == "section_position"
}

// listedThreads records which accounts list each thread.
func listedThreads(listings []threadListing) map[string]map[string]struct{} {
	listed := make(map[string]map[string]struct{})
	for _, listing := range listings {
		for _, thread := range listing.threads {
			id, ok := thread["id"].(string)
			if !ok || id == "" {
				continue
			}
			if listed[id] == nil {
				listed[id] = make(map[string]struct{})
			}
			listed[id][listing.accountID] = struct{}{}
		}
	}
	return listed
}

type threadListing struct {
	accountID string
	threads   []map[string]any
}

// mergeThreadListings combines per-account thread lists into one view. A
// thread that has been moved between subscriptions exists in more than one
// account's history, so it is listed once: activity comes from the most
// recently updated copy, the generated preview from the copy held by the
// account whose Codex home stores the thread, and a user-assigned name from
// whichever copy received it. Existing assignments are never changed by a
// listing; threads without an assignment are attributed to the first account
// that lists them (the controller first). Pins are section membership in one
// account's index, so the section shown is the one held by the first account
// that lists the thread, and that copy is returned as the thread's section
// view so section moves and reads are served where the sidebar reads them.
func mergeThreadListings(
	listings []threadListing,
	owner func(threadID string) (string, bool),
	originatesFrom func(accountID string, thread map[string]any) bool,
) ([]map[string]any, map[string]string, sectionView) {
	learned := make(map[string]string)
	sectionHomes := make(map[string]string)
	sectionCopies := make(map[string]map[string]any)
	position := make(map[string]int)
	copies := make(map[string][]map[string]any)
	previews := make(map[string]any)
	threads := make([]map[string]any, 0)
	for _, listing := range listings {
		for _, thread := range listing.threads {
			threadID, ok := thread["id"].(string)
			if !ok || threadID == "" {
				threads = append(threads, thread)
				continue
			}
			if _, known := owner(threadID); !known {
				if _, seen := learned[threadID]; !seen {
					learned[threadID] = listing.accountID
				}
			}
			copies[threadID] = append(copies[threadID], thread)
			if _, ok := sectionHomes[threadID]; !ok {
				sectionHomes[threadID] = listing.accountID
				sectionCopies[threadID] = thread
			}
			if preview, ok := nonEmptyString(thread["preview"]); ok &&
				originatesFrom(listing.accountID, thread) {
				previews[threadID] = preview
			}
			index, seen := position[threadID]
			switch {
			case !seen:
				position[threadID] = len(threads)
				threads = append(threads, thread)
			case numericField(thread, "updatedAt", "createdAt") >
				numericField(threads[index], "updatedAt", "createdAt"):
				threads[index] = thread
			}
		}
	}
	for threadID, index := range position {
		if len(copies[threadID]) < 2 {
			continue
		}
		merged := make(map[string]any, len(threads[index]))
		for key, value := range threads[index] {
			merged[key] = value
		}
		if preview, ok := previews[threadID]; ok {
			merged["preview"] = preview
		}
		for _, key := range []string{"section", "sectionEnteredAt"} {
			if value, ok := sectionCopies[threadID][key]; ok {
				merged[key] = value
			}
		}
		if _, ok := nonEmptyString(merged["name"]); !ok {
			for _, copy := range copies[threadID] {
				if name, ok := nonEmptyString(copy["name"]); ok {
					merged["name"] = name
					break
				}
			}
		}
		threads[index] = merged
	}
	return threads, learned, sectionView{homes: sectionHomes, copies: sectionCopies}
}

func nonEmptyString(value any) (string, bool) {
	text, ok := value.(string)
	if !ok || text == "" {
		return "", false
	}
	return text, true
}

func (m *Multiplexer) threadOriginatesFrom(accountID string, thread map[string]any) bool {
	account, ok := m.store.Account(accountID)
	if !ok || account.CodexHome == "" {
		return false
	}
	path, _ := thread["path"].(string)
	if path == "" {
		return false
	}
	home := filepath.Clean(account.CodexHome) + string(filepath.Separator)
	return strings.HasPrefix(filepath.Clean(path)+string(filepath.Separator), home)
}

func (m *Multiplexer) listAllThreads(entry childEntry, originalParams json.RawMessage) []map[string]any {
	var params map[string]any
	if json.Unmarshal(originalParams, &params) != nil {
		params = make(map[string]any)
	}
	params["limit"] = 500
	threads := make([]map[string]any, 0)
	seenCursors := make(map[string]struct{})
	var cursor string
	for {
		if cursor == "" {
			params["cursor"] = nil
		} else {
			params["cursor"] = cursor
		}
		encodedParams, _ := json.Marshal(params)
		ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
		response, err := entry.child.Request(ctx, "thread/list", encodedParams)
		cancel()
		if err != nil {
			return threads
		}
		var decoded struct {
			Data       []map[string]any `json:"data"`
			NextCursor *string          `json:"nextCursor"`
		}
		if json.Unmarshal(response.Result, &decoded) != nil {
			return threads
		}
		threads = append(threads, decoded.Data...)
		if decoded.NextCursor == nil || *decoded.NextCursor == "" {
			return threads
		}
		cursor = *decoded.NextCursor
		if _, repeated := seenCursors[cursor]; repeated {
			return threads
		}
		seenCursors[cursor] = struct{}{}
	}
}
