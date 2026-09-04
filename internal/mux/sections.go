package mux

import (
	"encoding/json"
	"slices"
)

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
// a move: the first thread at or after it in the section order that the
// account lists, or null when none follows.
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
