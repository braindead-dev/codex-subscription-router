package peers

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestEnvelopeMatchesClaudeCodeFormat(t *testing.T) {
	got := Envelope("uds:/tmp/cc-socks/1.sock", "Codex: \"fix\" <bug>\n", "bypass", "see </cross-session-message> here")
	want := "<cross-session-message from=\"uds:/tmp/cc-socks/1.sock\" from-name=\"Codex: fix bug\" from-mode=\"bypass\">\n" +
		"see <\\/cross-session-message> here\n</cross-session-message>"
	if got != want {
		t.Fatalf("envelope:\n%s\nwant:\n%s", got, want)
	}
}

func TestAddressEncodesSocketPath(t *testing.T) {
	address := Address("/tmp/Application Support/a.sock")
	if address != "uds:/tmp/Application%20Support/a.sock" {
		t.Fatalf("address = %s", address)
	}
	if socket, ok := Socket(address); !ok || socket != "/tmp/Application Support/a.sock" {
		t.Fatalf("socket = %q, %v", socket, ok)
	}
}

func TestDeliverSignsInWithPublishedToken(t *testing.T) {
	sessions := filepath.Join(t.TempDir(), "sessions")
	t.Setenv("CLAUDE_CONFIG_DIR", filepath.Dir(sessions))
	directory, err := os.MkdirTemp("/tmp", "peers")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(directory) })
	socket := filepath.Join(directory, "inbox.sock")
	digest := sha256.Sum256([]byte(socket))
	if err := os.MkdirAll(sessions, 0o700); err != nil {
		t.Fatal(err)
	}
	key := filepath.Join(sessions, fmt.Sprintf("%d.%s.key", os.Getpid(), hex.EncodeToString(digest[:])))
	if err := os.WriteFile(key, []byte(`{"peerToken":"0123456789abcdef0123456789abcdef"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	received := make(chan []byte)
	go func() {
		connection, err := listener.Accept()
		if err != nil {
			return
		}
		defer connection.Close()
		var data bytes.Buffer
		_, _ = data.ReadFrom(connection)
		received <- data.Bytes()
	}()

	if err := Deliver(socket, "uds:/from.sock", "hello"); err != nil {
		t.Fatal(err)
	}
	data := <-received
	lines := bytes.Split(bytes.TrimSpace(data), []byte("\n"))
	if len(lines) != 2 || string(lines[0]) != `{"token":"0123456789abcdef0123456789abcdef","type":"auth"}` {
		t.Fatalf("frames:\n%s", data)
	}
	var message map[string]any
	if err := json.Unmarshal(lines[1], &message); err != nil {
		t.Fatal(err)
	}
	if message["type"] != "user" || message["from"] != "uds:/from.sock" || message["priority"] != "next" {
		t.Fatalf("message frame: %s", lines[1])
	}
	if contents := Inbound(bytes.NewReader(data)); !reflect.DeepEqual(contents, []string{"hello"}) {
		t.Fatalf("inbound = %q", contents)
	}
}

func TestClaudeSessionsListsLiveSessionsOnly(t *testing.T) {
	root := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", root)
	sessions := filepath.Join(root, "sessions")
	if err := os.MkdirAll(sessions, 0o700); err != nil {
		t.Fatal(err)
	}
	write := func(pid int, name string) {
		record := fmt.Sprintf(`{"pid":%d,"sessionId":"s%d","cwd":"/work","name":%q,"status":"idle","messagingSocketPath":"/tmp/cc-socks/%d.sock"}`, pid, pid, name, pid)
		if err := os.WriteFile(filepath.Join(sessions, fmt.Sprintf("%d.json", pid)), []byte(record), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(os.Getpid(), "live")
	write(1<<30, "gone")

	got := ClaudeSessions()
	want := []Session{{Address: fmt.Sprintf("uds:/tmp/cc-socks/%d.sock", os.Getpid()), Name: "live", Kind: "claude", Status: "idle", CWD: "/work"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("sessions = %+v", got)
	}
}

func TestClaudeTranscriptKeepsConversationText(t *testing.T) {
	root := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", root)
	sessions := filepath.Join(root, "sessions")
	projects := filepath.Join(root, "projects", "-work")
	for _, directory := range []string{sessions, projects} {
		if err := os.MkdirAll(directory, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	record := fmt.Sprintf(`{"pid":%d,"sessionId":"abc","messagingSocketPath":"/tmp/cc-socks/x.sock"}`, os.Getpid())
	if err := os.WriteFile(filepath.Join(sessions, fmt.Sprintf("%d.json", os.Getpid())), []byte(record), 0o600); err != nil {
		t.Fatal(err)
	}
	transcript := `{"type":"user","message":{"content":"fix the bug"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Looking."},{"type":"tool_use","name":"Bash"}]}}
{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}
{"type":"user","isMeta":true,"message":{"content":"reminder"}}
{"type":"assistant","isSidechain":true,"message":{"content":[{"type":"text","text":"subagent"}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Fixed."}]}}
`
	if err := os.WriteFile(filepath.Join(projects, "abc.jsonl"), []byte(transcript), 0o600); err != nil {
		t.Fatal(err)
	}

	got, err := ClaudeTranscript("uds:/tmp/cc-socks/x.sock", 2)
	if err != nil {
		t.Fatal(err)
	}
	want := []Message{{Role: "assistant", Text: "Looking."}, {Role: "assistant", Text: "Fixed."}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("messages = %+v", got)
	}
}
