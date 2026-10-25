package mux

import (
	"encoding/json"
	"slices"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

// sectionView is what listings have shown of each account's copy of a
// thread: the copy's section fields, by thread and then account, and the order
// accounts are consulted in (the controller first). A listing replaces only
// the copies it lists, so one that leaves pinned threads out, such as the
// archived chats, does not forget where they are pinned.
type sectionView struct {
	accounts []string
	copies   map[string]map[string]map[string]any
}

var sectionFieldKeys = []string{"section", "sectionEnteredAt"}

// sectionViewOf records the section fields of every copy in listings, which
// arrive in consultation order.
func sectionViewOf(listings []threadListing) sectionView {
	view := sectionView{copies: make(map[string]map[string]map[string]any)}
	for _, listing := range listings {
		view.accounts = append(view.accounts, listing.accountID)
		for _, thread := range listing.threads {
			threadID, _ := thread["id"].(string)
			if threadID == "" {
				continue
			}
			fields := make(map[string]any, len(sectionFieldKeys))
			for _, key := range sectionFieldKeys {
				if value, ok := thread[key]; ok {
					fields[key] = value
				}
			}
			if view.copies[threadID] == nil {
				view.copies[threadID] = make(map[string]map[string]any)
			}
			view.copies[threadID][listing.accountID] = fields
		}
	}
	return view
}

// merge takes in a newer listing's copies and keeps every copy it did not list.
func (v *sectionView) merge(newer sectionView) {
	if len(newer.accounts) > 0 {
		v.accounts = newer.accounts
	}
	if v.copies == nil {
		v.copies = make(map[string]map[string]map[string]any)
	}
	for threadID, copies := range newer.copies {
		if v.copies[threadID] == nil {
			v.copies[threadID] = make(map[string]map[string]any)
		}
		for accountID, fields := range copies {
			v.copies[threadID][accountID] = fields
		}
	}
}

// home is the account whose copy the sidebar shows: the first whose copy is in
// a section, or else the first that lists the thread.
func (v sectionView) home(threadID string) (string, bool) {
	fallback := ""
	for _, accountID := range v.accounts {
		fields, ok := v.copies[threadID][accountID]
		if !ok {
			continue
		}
		if copySection(fields) != "" {
			return accountID, true
		}
		if fallback == "" {
			fallback = accountID
		}
	}
	return fallback, fallback != ""
}

// inSection reports whether an account's copy of a thread is in a section.
func (v sectionView) inSection(threadID, accountID, sectionID string) bool {
	return sectionID != "" && copySection(v.copies[threadID][accountID]) == sectionID
}

// record notes a move an account accepted, until a listing shows the copy.
func (v *sectionView) record(accountID string, move sectionMove) {
	if v.copies == nil {
		v.copies = make(map[string]map[string]map[string]any)
	}
	if v.copies[move.ThreadID] == nil {
		v.copies[move.ThreadID] = make(map[string]map[string]any)
	}
	fields := v.copies[move.ThreadID][accountID]
	if fields == nil {
		fields = make(map[string]any, len(sectionFieldKeys))
		v.copies[move.ThreadID][accountID] = fields
	}
	switch {
	case move.SectionID == "":
		fields["section"] = nil
	case copySection(fields) != move.SectionID:
		fields["section"] = map[string]any{"id": move.SectionID}
	}
}

func copySection(fields map[string]any) string {
	section, _ := fields["section"].(map[string]any)
	id, _ := section["id"].(string)
	return id
}

// sectionMove is the desktop's thread/section/move request. A null sectionId
// unpins the thread; a null beforeThreadId places it last.
type sectionMove struct {
	ThreadID       string
	SectionID      string
	BeforeThreadID string
}

func parseSectionMove(params json.RawMessage) (sectionMove, bool) {
	var decoded struct {
		ThreadID       string  `json:"threadId"`
		SectionID      *string `json:"sectionId"`
		BeforeThreadID *string `json:"beforeThreadId"`
	}
	if json.Unmarshal(params, &decoded) != nil || decoded.ThreadID == "" {
		return sectionMove{}, false
	}
	move := sectionMove{ThreadID: decoded.ThreadID}
	if decoded.SectionID != nil {
		move.SectionID = *decoded.SectionID
	}
	if decoded.BeforeThreadID != nil {
		move.BeforeThreadID = *decoded.BeforeThreadID
	}
	return move, true
}

// sectionListingID returns the section a thread/list request is scoped to.
func sectionListingID(params json.RawMessage) string {
	var decoded struct {
		SectionID string `json:"sectionId"`
	}
	if json.Unmarshal(params, &decoded) != nil {
		return ""
	}
	return decoded.SectionID
}

// orderSection arranges a section listing by the multiplexer's order. Threads
// the order does not know keep their listing position after the known ones,
// and the returned order holds exactly the listed threads.
func orderSection(threads []map[string]any, order []string) ([]map[string]any, []string) {
	byID := make(map[string]map[string]any, len(threads))
	for _, thread := range threads {
		if id, ok := thread["id"].(string); ok {
			byID[id] = thread
		}
	}
	arranged := make([]map[string]any, 0, len(threads))
	listed := make([]string, 0, len(threads))
	seen := make(map[string]struct{}, len(threads))
	for _, id := range order {
		if thread, ok := byID[id]; ok {
			arranged = append(arranged, thread)
			listed = append(listed, id)
			seen[id] = struct{}{}
		}
	}
	for _, thread := range threads {
		id, _ := thread["id"].(string)
		if _, ok := seen[id]; ok {
			continue
		}
		arranged = append(arranged, thread)
		if id != "" {
			listed = append(listed, id)
			seen[id] = struct{}{}
		}
	}
	return arranged, listed
}

// scopeBeforeThread keeps beforeThreadId meaningful for the account applying
// a move: the first thread at or after it in the section order whose copy on
// that account is in the section, or null when none follows. Codex refuses an
// anchor outside the section.
func scopeBeforeThread(
	params json.RawMessage,
	move sectionMove,
	order []string,
	lists func(threadID string) bool,
) json.RawMessage {
	if move.BeforeThreadID == "" {
		return params
	}
	scoped := ""
	if start := slices.Index(order, move.BeforeThreadID); start >= 0 {
		for _, candidate := range order[start:] {
			if candidate != move.ThreadID && lists(candidate) {
				scoped = candidate
				break
			}
		}
	} else if lists(move.BeforeThreadID) {
		scoped = move.BeforeThreadID
	}
	if scoped == move.BeforeThreadID {
		return params
	}
	var decoded map[string]any
	if json.Unmarshal(params, &decoded) != nil {
		return params
	}
	if scoped == "" {
		decoded["beforeThreadId"] = nil
	} else {
		decoded["beforeThreadId"] = scoped
	}
	encoded, err := json.Marshal(decoded)
	if err != nil {
		return params
	}
	return encoded
}

// applySectionView makes a thread/read answer agree with the sidebar. The
// desktop reads a thread before moving it and only moves one whose copy is
// pinned, but the account that owns a thread's turns may not be the one
// holding its pin.
func (m *Multiplexer) applySectionView(route externalRoute, accountID string, message protocol.Message) (protocol.Message, bool) {
	if route.method != "thread/read" || message.Error != nil {
		return message, false
	}
	threadID := threadIDFromParams(route.message.Params)
	fields, ok := m.sectionFields(threadID, accountID)
	if !ok {
		return message, false
	}
	var result map[string]any
	if json.Unmarshal(message.Result, &result) != nil {
		return message, false
	}
	thread, _ := result["thread"].(map[string]any)
	if thread == nil {
		return message, false
	}
	for _, key := range sectionFieldKeys {
		if value, ok := fields[key]; ok {
			thread[key] = value
		}
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		return message, false
	}
	message.Result = encoded
	return message, true
}
