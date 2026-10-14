// Package peers speaks the local protocol Claude Code sessions use to message
// each other. A live session records itself in its sessions directory and
// reads JSON lines from a Unix socket; a message is a user turn wrapped in a
// <cross-session-message> envelope that names the sender's address, so the
// receiver can answer it.
package peers

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"
)

// Session is one conversation another agent can message: a Codex chat or a
// live Claude Code session. Messages for it are delivered to Address.
type Session struct {
	Address string `json:"address"`
	Name    string `json:"name"`
	Kind    string `json:"kind"`
	Status  string `json:"status"`
	CWD     string `json:"cwd,omitempty"`
}

// Message is one user or assistant turn of a conversation, as text.
type Message struct {
	Role string `json:"role"`
	Text string `json:"text"`
}

// Address is how a session is named in a message envelope: its socket path
// with anything outside Claude Code's address alphabet percent-encoded.
func Address(socket string) string {
	var encoded strings.Builder
	encoded.WriteString("uds:")
	for _, b := range []byte(socket) {
		if 'a' <= b && b <= 'z' || 'A' <= b && b <= 'Z' || '0' <= b && b <= '9' || strings.IndexByte(":_/.-", b) >= 0 {
			encoded.WriteByte(b)
		} else {
			fmt.Fprintf(&encoded, "%%%02X", b)
		}
	}
	return encoded.String()
}

// Socket is the socket path an address names.
func Socket(address string) (string, bool) {
	encoded, ok := strings.CutPrefix(address, "uds:")
	if !ok || encoded == "" {
		return "", false
	}
	socket, err := url.PathUnescape(encoded)
	return socket, err == nil
}

const envelopeTag = "cross-session-message"

var closingTag = regexp.MustCompile(`(?i)</` + envelopeTag)

// Envelope wraps a message the way Claude Code marks one from another
// session. Mode is the sender's permission mode, "bypass" or "prompting";
// a session that bypasses prompts holds messages from senders that do not.
func Envelope(from, name, mode, body string) string {
	attributes := ""
	if from != "" {
		attributes += fmt.Sprintf(` from="%s"`, from)
	}
	if name = displayName(name); name != "" {
		attributes += fmt.Sprintf(` from-name="%s"`, name)
	}
	if mode != "" {
		attributes += fmt.Sprintf(` from-mode="%s"`, mode)
	}
	return fmt.Sprintf("<%s%s>\n%s\n</%s>", envelopeTag, attributes, closingTag.ReplaceAllString(body, `<\/`+envelopeTag), envelopeTag)
}

// displayName is a sender name an envelope attribute can carry: no quotes,
// angle brackets, or control characters, and at most 64 characters.
func displayName(name string) string {
	name = strings.Map(func(r rune) rune {
		if r == '"' || r == '<' || r == '>' || unicode.In(r, unicode.Cc, unicode.Cf, unicode.Cs, unicode.Zl, unicode.Zp) {
			return -1
		}
		return r
	}, name)
	runes := []rune(strings.TrimSpace(name))
	if len(runes) > 64 {
		return string(runes[:64]) + "…"
	}
	return string(runes)
}

type record struct {
	PID       int    `json:"pid"`
	SessionID string `json:"sessionId"`
	CWD       string `json:"cwd"`
	Name      string `json:"name"`
	Status    string `json:"status"`
	Socket    string `json:"messagingSocketPath"`
}

var recordName = regexp.MustCompile(`^\d+\.json$`)

// ClaudeSessions lists the live Claude Code sessions with an inbox.
func ClaudeSessions() []Session {
	records := claudeRecords()
	sessions := make([]Session, 0, len(records))
	for _, r := range records {
		sessions = append(sessions, Session{Address: Address(r.Socket), Name: r.Name, Kind: "claude", Status: r.Status, CWD: r.CWD})
	}
	return sessions
}

func claudeRecords() []record {
	directory := sessionsDirectory()
	entries, _ := os.ReadDir(directory)
	records := make([]record, 0, len(entries))
	for _, entry := range entries {
		if !recordName.MatchString(entry.Name()) {
			continue
		}
		data, err := os.ReadFile(filepath.Join(directory, entry.Name()))
		if err != nil {
			continue
		}
		var r record
		if json.Unmarshal(data, &r) != nil || r.Socket == "" || !alive(r.PID) {
			continue
		}
		records = append(records, r)
	}
	return records
}

// sessionsDirectory is where live Claude Code sessions record themselves.
func sessionsDirectory() string {
	if configured := os.Getenv("CLAUDE_CONFIG_DIR"); configured != "" {
		return filepath.Join(configured, "sessions")
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".claude", "sessions")
}

func alive(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

// ClaudeSessionName is the name of the live Claude Code session at address.
func ClaudeSessionName(address string) string {
	if r, ok := claudeRecord(address); ok {
		return r.Name
	}
	return ""
}

func claudeRecord(address string) (record, bool) {
	socket, ok := Socket(address)
	if !ok {
		return record{}, false
	}
	for _, r := range claudeRecords() {
		if r.Socket == socket {
			return r, true
		}
	}
	return record{}, false
}

// Deliver writes one enveloped message into the inbox at socket, signing in
// with the token its session published for that socket when it has one.
// From is the sender's address, where a reply goes.
func Deliver(socket, from, content string) error {
	id, err := uuid()
	if err != nil {
		return err
	}
	message, err := json.Marshal(map[string]any{
		"msgV":     1,
		"msg_id":   id,
		"type":     "user",
		"message":  map[string]any{"role": "user", "content": content},
		"priority": "next",
		"from":     from,
	})
	if err != nil {
		return err
	}
	var frames bytes.Buffer
	if token, ok := inboxToken(socket); ok {
		auth, _ := json.Marshal(map[string]string{"type": "auth", "token": token})
		frames.Write(append(auth, '\n'))
	}
	frames.Write(append(message, '\n'))
	connection, err := net.DialTimeout("unix", socket, 5*time.Second)
	if err != nil {
		return fmt.Errorf("no session is listening at %s: %w", socket, err)
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(5 * time.Second))
	_, err = connection.Write(frames.Bytes())
	return err
}

// inboxToken is the token a live session published for its inbox, in a key
// file named after the session's pid and the hash of the socket path.
func inboxToken(socket string) (string, bool) {
	digest := sha256.Sum256([]byte(socket))
	suffix := "." + hex.EncodeToString(digest[:]) + ".key"
	directory := sessionsDirectory()
	entries, _ := os.ReadDir(directory)
	for _, entry := range entries {
		pid, ok := strings.CutSuffix(entry.Name(), suffix)
		if !ok {
			continue
		}
		if number, err := strconv.Atoi(pid); err != nil || !alive(number) {
			continue
		}
		data, err := os.ReadFile(filepath.Join(directory, entry.Name()))
		if err != nil {
			continue
		}
		var key struct {
			PeerToken string `json:"peerToken"`
		}
		if json.Unmarshal(data, &key) == nil && key.PeerToken != "" {
			return key.PeerToken, true
		}
	}
	return "", false
}

// Inbound reads the user messages a session sent to an inbox, skipping its
// sign-in and control frames.
func Inbound(reader io.Reader) []string {
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	var contents []string
	for scanner.Scan() {
		var frame struct {
			Type    string `json:"type"`
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		}
		if json.Unmarshal(scanner.Bytes(), &frame) == nil && frame.Type == "user" && frame.Message.Content != "" {
			contents = append(contents, frame.Message.Content)
		}
	}
	return contents
}

// ClaudeTranscript returns the last limit text messages of the live Claude
// Code session at address, read from the end of its transcript.
func ClaudeTranscript(address string, limit int) ([]Message, error) {
	r, ok := claudeRecord(address)
	if !ok {
		return nil, fmt.Errorf("no live session at %s", address)
	}
	matches, _ := filepath.Glob(filepath.Join(filepath.Dir(sessionsDirectory()), "projects", "*", r.SessionID+".jsonl"))
	if len(matches) == 0 {
		return nil, fmt.Errorf("no transcript for session %s", r.SessionID)
	}
	tail, err := readTail(matches[0], 8<<20)
	if err != nil {
		return nil, err
	}
	var messages []Message
	for _, line := range bytes.Split(tail, []byte("\n")) {
		var entry struct {
			Type        string `json:"type"`
			IsMeta      bool   `json:"isMeta"`
			IsSidechain bool   `json:"isSidechain"`
			Message     struct {
				Content json.RawMessage `json:"content"`
			} `json:"message"`
		}
		if json.Unmarshal(line, &entry) != nil || entry.IsMeta || entry.IsSidechain ||
			(entry.Type != "user" && entry.Type != "assistant") {
			continue
		}
		if text := contentText(entry.Message.Content); text != "" {
			messages = append(messages, Message{Role: entry.Type, Text: text})
		}
	}
	if len(messages) > limit {
		messages = messages[len(messages)-limit:]
	}
	return messages, nil
}

// contentText is the text of a message's content: a string, or the text
// blocks of a block list (tool calls and results are left out).
func contentText(content json.RawMessage) string {
	var text string
	if json.Unmarshal(content, &text) == nil {
		return strings.TrimSpace(text)
	}
	var blocks []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if json.Unmarshal(content, &blocks) != nil {
		return ""
	}
	var parts []string
	for _, block := range blocks {
		if block.Type == "text" && strings.TrimSpace(block.Text) != "" {
			parts = append(parts, strings.TrimSpace(block.Text))
		}
	}
	return strings.Join(parts, "\n\n")
}

// readTail reads at most size bytes from the end of a file, starting at a
// line boundary.
func readTail(path string, size int64) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	offset := max(info.Size()-size, 0)
	data := make([]byte, info.Size()-offset)
	if _, err := file.ReadAt(data, offset); err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}
	if offset > 0 {
		if newline := bytes.IndexByte(data, '\n'); newline >= 0 {
			data = data[newline+1:]
		}
	}
	return data, nil
}

func uuid() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	h := hex.EncodeToString(b[:])
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:], nil
}
