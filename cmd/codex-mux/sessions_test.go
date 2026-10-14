package main

import (
	"encoding/json"
	"testing"
)

func TestSenderAttestsTheCallingConversation(t *testing.T) {
	t.Setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/cc-socks/42.sock")
	tests := []struct {
		meta        string
		from, mode  string
		description string
	}{
		{`{"threadId":"01a1393c-de92-7632-875a-21b880d280bc","x-codex-turn-metadata":{"sandbox_mode":"danger-full-access"}}`,
			"uds:/r/inbox/01a1393c-de92-7632-875a-21b880d280bc.sock", "bypass", "full-access Codex chat"},
		{`{"threadId":"01a1393c-de92-7632-875a-21b880d280bc","x-codex-turn-metadata":{"sandbox_mode":"workspace-write"}}`,
			"uds:/r/inbox/01a1393c-de92-7632-875a-21b880d280bc.sock", "prompting", "sandboxed Codex chat"},
		{``, "uds:/tmp/cc-socks/42.sock", "", "Claude Code session"},
	}
	for _, test := range tests {
		from, mode := sender(json.RawMessage(test.meta), "/r")
		if from != test.from || mode != test.mode {
			t.Fatalf("%s: sender = %q, %q; want %q, %q", test.description, from, mode, test.from, test.mode)
		}
	}
}
