package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/backend"
	"github.com/b-nnett/codex-subscription-router/internal/peers"
)

// InboxAddress is the address of a Codex chat's inbox under a state root: a
// socket the router serves for the chat. Claude Code sessions send to it as to
// any other session, so a reply to a message the chat sent arrives there, and
// every message delivered to it starts a turn in the chat.
func InboxAddress(root, threadID string) string {
	return peers.Address(filepath.Join(root, "inbox", threadID+".sock"))
}

// Sessions lists the conversations an agent can message: the most recently
// updated Codex chats and every live Claude Code session, filtered by query.
func (m *Multiplexer) Sessions(ctx context.Context, query string, limit int) []peers.Session {
	var sessions []peers.Session
	for _, thread := range m.recentThreads(ctx, query, limit) {
		id, _ := thread["id"].(string)
		address, err := m.openInbox(id)
		if err != nil {
			continue
		}
		cwd, _ := thread["cwd"].(string)
		sessions = append(sessions, peers.Session{Address: address, Name: threadName(thread), Kind: "codex", Status: threadStatus(thread), CWD: cwd})
	}
	for _, session := range peers.ClaudeSessions() {
		if strings.Contains(strings.ToLower(session.Name), strings.ToLower(query)) {
			sessions = append(sessions, session)
		}
	}
	return sessions
}

// SendMessage delivers message to the conversation at to, an address or a
// session name, from the session at from, whose permission mode is mode.
func (m *Multiplexer) SendMessage(ctx context.Context, to, from, mode, message string) (string, error) {
	address, err := m.resolveSession(ctx, to)
	if err != nil {
		return "", err
	}
	var name string
	if threadID, ok := m.inboxThread(from); ok {
		if _, err := m.openInbox(threadID); err != nil {
			return "", err
		}
		name = "Codex: " + m.threadName(ctx, threadID)
	} else if claude := peers.ClaudeSessionName(from); claude != "" {
		name = "Claude: " + claude
	}
	content := peers.Envelope(from, name, mode, message)
	if threadID, ok := m.inboxThread(address); ok {
		return address, m.deliverToThread(ctx, threadID, content)
	}
	socket, ok := peers.Socket(address)
	if !ok {
		return "", fmt.Errorf("%q is not a session address", address)
	}
	return address, peers.Deliver(socket, from, content)
}

// ReadSession returns the last limit text messages of the conversation at an
// address or with a name.
func (m *Multiplexer) ReadSession(ctx context.Context, session string, limit int) ([]peers.Message, error) {
	address, err := m.resolveSession(ctx, session)
	if err != nil {
		return nil, err
	}
	threadID, ok := m.inboxThread(address)
	if !ok {
		return peers.ClaudeTranscript(address, limit)
	}
	child, err := m.threadChild(threadID)
	if err != nil {
		return nil, err
	}
	params, _ := json.Marshal(map[string]any{"threadId": threadID, "limit": limit, "itemsView": "full", "sortDirection": "desc"})
	response, err := child.Request(ctx, "thread/turns/list", params)
	if err != nil {
		return nil, err
	}
	var turns struct {
		Data []struct {
			Items []struct {
				Type    string          `json:"type"`
				Text    string          `json:"text"`
				Content json.RawMessage `json:"content"`
			} `json:"items"`
		} `json:"data"`
	}
	if err := json.Unmarshal(response.Result, &turns); err != nil {
		return nil, fmt.Errorf("decode chat turns: %w", err)
	}
	var messages []peers.Message
	for _, turn := range slices.Backward(turns.Data) {
		for _, item := range turn.Items {
			switch item.Type {
			case "userMessage":
				var inputs []struct {
					Type string `json:"type"`
					Text string `json:"text"`
				}
				_ = json.Unmarshal(item.Content, &inputs)
				var parts []string
				for _, input := range inputs {
					if input.Type == "text" {
						parts = append(parts, input.Text)
					}
				}
				messages = append(messages, peers.Message{Role: "user", Text: strings.Join(parts, "\n\n")})
			case "agentMessage":
				messages = append(messages, peers.Message{Role: "assistant", Text: item.Text})
			}
		}
	}
	return messages[max(len(messages)-limit, 0):], nil
}

// resolveSession turns a session name into its address; an address is
// returned as given.
func (m *Multiplexer) resolveSession(ctx context.Context, session string) (string, error) {
	if strings.HasPrefix(session, "uds:") {
		return session, nil
	}
	var matches []string
	for _, candidate := range m.Sessions(ctx, session, 50) {
		if strings.EqualFold(candidate.Name, session) {
			matches = append(matches, candidate.Address)
		}
	}
	switch len(matches) {
	case 1:
		return matches[0], nil
	case 0:
		return "", fmt.Errorf("no session is named %q; list sessions for their addresses", session)
	default:
		return "", fmt.Errorf("%d sessions are named %q; use one of their addresses: %s", len(matches), session, strings.Join(matches, ", "))
	}
}

// deliverToThread starts a turn in a chat with content as the user's input,
// opening the chat first if its subscription has not loaded it. A chat in the
// middle of a turn takes the input into that turn.
func (m *Multiplexer) deliverToThread(ctx context.Context, threadID, content string) error {
	child, err := m.threadChild(threadID)
	if err != nil {
		return err
	}
	if !threadLoadedOn(ctx, child, threadID) {
		params, _ := json.Marshal(map[string]any{"threadId": threadID, "excludeTurns": true})
		if _, err := child.Request(ctx, "thread/resume", params); err != nil {
			return fmt.Errorf("open chat: %w", err)
		}
	}
	params, _ := json.Marshal(map[string]any{
		"threadId": threadID,
		"input":    []map[string]any{{"type": "text", "text": content, "text_elements": []any{}}},
	})
	_, err = child.Request(ctx, "turn/start", params)
	return err
}

func (m *Multiplexer) threadChild(threadID string) (*backend.Child, error) {
	owner, ok := m.threadOwner(threadID)
	if !ok {
		return nil, fmt.Errorf("no Codex chat has id %s", threadID)
	}
	child, ok := m.child(owner)
	if !ok {
		return nil, fmt.Errorf("the subscription holding chat %s is unavailable", threadID)
	}
	return child, nil
}

func (m *Multiplexer) threadName(ctx context.Context, threadID string) string {
	child, err := m.threadChild(threadID)
	if err != nil {
		return threadID
	}
	params, _ := json.Marshal(map[string]any{"threadId": threadID})
	response, err := child.Request(ctx, "thread/read", params)
	if err != nil {
		return threadID
	}
	var read struct {
		Thread map[string]any `json:"thread"`
	}
	if json.Unmarshal(response.Result, &read) != nil {
		return threadID
	}
	return threadName(read.Thread)
}

// recentThreads is one page of each subscription's chats, merged and
// newest first.
func (m *Multiplexer) recentThreads(ctx context.Context, query string, limit int) []map[string]any {
	params := map[string]any{"limit": limit, "sortKey": "updated_at", "modelProviders": []string{}}
	if query != "" {
		params["searchTerm"] = query
	}
	encoded, _ := json.Marshal(params)
	entries := m.childEntries()
	listings := make([]threadListing, len(entries))
	var wait sync.WaitGroup
	for index, entry := range entries {
		wait.Go(func() {
			listings[index].accountID = entry.account.ID
			response, err := entry.child.Request(ctx, "thread/list", encoded)
			if err != nil {
				return
			}
			var page struct {
				Data []map[string]any `json:"data"`
			}
			if json.Unmarshal(response.Result, &page) == nil {
				listings[index].threads = page.Data
			}
		})
	}
	wait.Wait()
	threads, _, _ := mergeThreadListings(listings, m.store.ThreadOwner, m.threadOriginatesFrom)
	sortThreads(threads)
	return threads[:min(limit, len(threads))]
}

func threadName(thread map[string]any) string {
	if name, ok := nonEmptyString(thread["name"]); ok {
		return name
	}
	preview, _ := thread["preview"].(string)
	preview, _, _ = strings.Cut(strings.TrimSpace(preview), "\n")
	if runes := []rune(preview); len(runes) > 80 {
		return string(runes[:80]) + "…"
	}
	return preview
}

func threadStatus(thread map[string]any) string {
	status, _ := thread["status"].(map[string]any)
	switch status["type"] {
	case "active":
		return "busy"
	case "systemError":
		return "error"
	default:
		return "idle"
	}
}

func (m *Multiplexer) inboxDirectory() string {
	return filepath.Join(m.store.Root(), "inbox")
}

// inboxThread is the chat whose inbox an address names.
func (m *Multiplexer) inboxThread(address string) (string, bool) {
	socket, ok := peers.Socket(address)
	if !ok || filepath.Dir(socket) != m.inboxDirectory() {
		return "", false
	}
	threadID, ok := strings.CutSuffix(filepath.Base(socket), ".sock")
	return threadID, ok && threadIDPattern.MatchString(threadID)
}

// openInbox serves a chat's inbox and returns its address.
func (m *Multiplexer) openInbox(threadID string) (string, error) {
	if !threadIDPattern.MatchString(threadID) {
		return "", fmt.Errorf("%q is not a Codex chat id", threadID)
	}
	address := InboxAddress(m.store.Root(), threadID)
	m.inboxMu.Lock()
	defer m.inboxMu.Unlock()
	if _, open := m.inboxes[threadID]; open {
		return address, nil
	}
	if err := os.MkdirAll(m.inboxDirectory(), 0o700); err != nil {
		return "", fmt.Errorf("create inbox directory: %w", err)
	}
	socket := filepath.Join(m.inboxDirectory(), threadID+".sock")
	_ = os.Remove(socket)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: socket, Net: "unix"})
	if err != nil {
		return "", fmt.Errorf("open inbox for chat %s: %w", threadID, err)
	}
	// The socket file outlives this process so the next connection reopens
	// the inbox at the address senders already hold.
	listener.SetUnlinkOnClose(false)
	m.inboxes[threadID] = listener
	go m.serveInbox(threadID, listener)
	return address, nil
}

// reopenInboxes serves again every inbox an earlier connection opened.
func (m *Multiplexer) reopenInboxes() {
	sockets, _ := filepath.Glob(filepath.Join(m.inboxDirectory(), "*.sock"))
	for _, socket := range sockets {
		if _, err := m.openInbox(strings.TrimSuffix(filepath.Base(socket), ".sock")); err != nil {
			fmt.Fprintf(os.Stderr, "codex-mux: %v\n", err)
		}
	}
}

func (m *Multiplexer) serveInbox(threadID string, listener net.Listener) {
	for {
		connection, err := listener.Accept()
		if err != nil {
			return
		}
		go func() {
			defer connection.Close()
			_ = connection.SetReadDeadline(time.Now().Add(10 * time.Second))
			for _, content := range peers.Inbound(connection) {
				ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
				if err := m.deliverToThread(ctx, threadID, content); err != nil {
					fmt.Fprintf(os.Stderr, "codex-mux: deliver message to chat %s: %v\n", threadID, err)
				}
				cancel()
			}
		}()
	}
}

func (m *Multiplexer) closeInboxes() {
	m.inboxMu.Lock()
	defer m.inboxMu.Unlock()
	for threadID, listener := range m.inboxes {
		_ = listener.Close()
		delete(m.inboxes, threadID)
	}
}
