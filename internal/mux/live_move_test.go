package mux

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

// TestLiveMoveKeepsHistoryOnBothAccounts drives two real Codex app-servers.
// It needs CODEX_MUX_LIVE_HOME, a Codex home holding auth.json, config.toml,
// and a file named tid with a thread id whose rollouts live in that home, and
// CODEX_MUX_LIVE_CODEX, the codex binary. It runs two short model turns.
func TestLiveMoveKeepsHistoryOnBothAccounts(t *testing.T) {
	seed := os.Getenv("CODEX_MUX_LIVE_HOME")
	codex := os.Getenv("CODEX_MUX_LIVE_CODEX")
	if seed == "" || codex == "" {
		t.Skip("set CODEX_MUX_LIVE_HOME and CODEX_MUX_LIVE_CODEX to run against real Codex processes")
	}
	root := t.TempDir()
	primaryHome := filepath.Join(root, "primary")
	copyTree(t, seed, primaryHome)
	if err := runSQLite(stateDatabase(primaryHome), "update threads set rollout_path = replace(rollout_path, '"+escapeSQLLiteral(seed)+"', '"+escapeSQLLiteral(primaryHome)+"');"); err != nil {
		t.Fatal(err)
	}
	threadID := strings.TrimSpace(readFile(t, filepath.Join(primaryHome, "tid")))
	store, err := state.Open(filepath.Join(root, "mux"), primaryHome)
	if err != nil {
		t.Fatal(err)
	}
	work, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"auth.json", "config.toml", "opencodex-catalog.json"} {
		if data, err := os.ReadFile(filepath.Join(primaryHome, name)); err == nil {
			if err := os.WriteFile(filepath.Join(work.CodexHome, name), data, 0o600); err != nil {
				t.Fatal(err)
			}
		}
	}
	output := &lockedBuffer{}
	m, err := New(Options{
		RealExecutable: codex,
		RealArgs:       []string{"-c", "features.code_mode_host=true", "app-server"},
		Environment:    os.Environ(),
		Store:          store,
		Output:         output,
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := m.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer m.Close()
	client := &liveClient{m: m, output: output, t: t}
	client.request("initialize", map[string]any{"clientInfo": map[string]any{"name": "Codex Desktop", "version": "test"}, "capabilities": map[string]any{"experimentalApi": true}})
	m.HandleClient(protocol.Message{Method: "initialized"})
	if err := store.SetThreadOwner(threadID, "primary"); err != nil {
		t.Fatal(err)
	}
	before := projectedTurns(t, primaryHome, threadID)
	t.Logf("before: primary %s", describeHome(t, primaryHome, threadID))

	// Primary -> Work: Work has never indexed the thread; its row, files, and
	// projection streams are copied before it resumes by id.
	if err := m.resumeThreadOnAccount(ctx, threadID, "primary", work.ID); err != nil {
		t.Fatalf("move to work: %v", err)
	}
	t.Logf("after move: work %s", describeHome(t, work.CodexHome, threadID))
	if err := store.SetThreadOwner(threadID, work.ID); err != nil {
		t.Fatal(err)
	}
	client.turn(threadID, "Reply with exactly: moved-to-work")
	t.Logf("after work turn: work %s", describeHome(t, work.CodexHome, threadID))
	if onWork := projectedTurns(t, work.CodexHome, threadID); onWork != before+1 {
		t.Fatalf("work projects %d turns after its turn, want %d", onWork, before+1)
	}
	if got := client.turnCount(threadID); got == 0 {
		t.Fatal("work lists no turns for the chat")
	}

	// Work -> Primary: Primary indexes the thread and never loaded it, so its
	// copy is refreshed from Work, including every link's stream.
	if err := m.resumeThreadOnAccount(ctx, threadID, work.ID, "primary"); err != nil {
		t.Fatalf("move back to primary: %v", err)
	}
	if err := store.SetThreadOwner(threadID, "primary"); err != nil {
		t.Fatal(err)
	}
	client.turn(threadID, "Reply with exactly: back-on-primary")
	t.Logf("after primary turn: primary %s", describeHome(t, primaryHome, threadID))
	if onPrimary := projectedTurns(t, primaryHome, threadID); onPrimary != before+2 {
		t.Fatalf("primary projects %d turns after moving back, want %d", onPrimary, before+2)
	}
	// The owner's projection covers every file; the previous owner lags by
	// the turn that ran elsewhere, which is why a move back refreshes it.
	for _, rollout := range threadRollouts(primaryHome, threadID) {
		covered, err := projectionCoversRolloutFile(primaryHome, threadID, rollout)
		if err != nil || !covered {
			t.Fatalf("primary: projection does not cover %s (err=%v)", filepath.Base(rollout), err)
		}
	}
	if primaryRollouts, workRollouts := threadRollouts(primaryHome, threadID), threadRollouts(work.CodexHome, threadID); len(primaryRollouts) != len(workRollouts) {
		t.Fatalf("rollout sets differ: primary %d files, work %d files", len(primaryRollouts), len(workRollouts))
	}
}

type liveClient struct {
	m      *Multiplexer
	output *lockedBuffer
	t      *testing.T
	seq    int
}

func (c *liveClient) request(method string, params any) map[string]any {
	c.seq++
	id := protocol.StringID("live-" + itoa(c.seq))
	encoded, _ := json.Marshal(params)
	c.m.HandleClient(protocol.Request(method, id, encoded))
	return c.output.await(c.t, func(message map[string]any) bool {
		raw, _ := json.Marshal(message["id"])
		return string(raw) == string(id)
	}, 120*time.Second)
}

func (c *liveClient) turn(threadID, text string) {
	c.request("turn/start", map[string]any{"threadId": threadID, "input": []map[string]any{{"type": "text", "text": text}}})
	c.output.await(c.t, func(message map[string]any) bool {
		return message["method"] == "turn/completed"
	}, 300*time.Second)
}

func (c *liveClient) turnCount(threadID string) int {
	response := c.request("thread/turns/list", map[string]any{"threadId": threadID, "limit": 500})
	result, _ := response["result"].(map[string]any)
	data, _ := result["data"].([]any)
	if result == nil {
		c.t.Fatalf("thread/turns/list failed: %v", response["error"])
	}
	return len(data)
}

type lockedBuffer struct {
	mu   sync.Mutex
	buf  bytes.Buffer
	seen int
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) await(t *testing.T, match func(map[string]any) bool, timeout time.Duration) map[string]any {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		b.mu.Lock()
		lines := strings.Split(b.buf.String(), "\n")
		b.mu.Unlock()
		for i := b.seen; i < len(lines); i++ {
			var message map[string]any
			if json.Unmarshal([]byte(lines[i]), &message) != nil {
				continue
			}
			if match(message) {
				b.seen = i + 1
				return message
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for a router message")
	return nil
}

func projectionCoversRolloutFile(home, threadID, rollout string) (bool, error) {
	info, err := os.Stat(rollout)
	if err != nil {
		return false, err
	}
	offset, err := querySQLite(
		historyDatabase(home),
		"select next_rollout_byte_offset from thread_history_projection_state where thread_id = '"+projectionStream(rollout, threadID)+"'",
	)
	if err != nil {
		return false, err
	}
	return strings.TrimSpace(offset) == itoa(int(info.Size())), nil
}

// projectedTurns counts a chat's turns across every projection stream: the
// original rollout and each continuation link.
func projectedTurns(t *testing.T, home, threadID string) int {
	total := 0
	for _, rollout := range threadRollouts(home, threadID) {
		count, err := querySQLite(historyDatabase(home), "select count(*) from thread_turns where thread_id = '"+projectionStream(rollout, threadID)+"';")
		if err != nil {
			t.Fatal(err)
		}
		n, _ := strconv.Atoi(strings.TrimSpace(count))
		total += n
	}
	return total
}

func describeHome(t *testing.T, home, threadID string) string {
	parts := make([]string, 0)
	for _, rollout := range threadRollouts(home, threadID) {
		info, _ := os.Stat(rollout)
		stream := projectionStream(rollout, threadID)
		offset, _ := querySQLite(historyDatabase(home), "select next_rollout_byte_offset from thread_history_projection_state where thread_id = '"+stream+"';")
		turns, _ := querySQLite(historyDatabase(home), "select count(*) from thread_turns where thread_id = '"+stream+"';")
		parts = append(parts, filepath.Base(rollout)[len(filepath.Base(rollout))-16:]+" size="+strconv.FormatInt(info.Size(), 10)+" offset="+strings.TrimSpace(offset)+" turns="+strings.TrimSpace(turns))
	}
	return strings.Join(parts, "; ")
}

func copyTree(t *testing.T, from, to string) {
	if err := filepath.Walk(from, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		target := filepath.Join(to, strings.TrimPrefix(path, from))
		if info.IsDir() {
			return os.MkdirAll(target, 0o700)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o600)
	}); err != nil {
		t.Fatal(err)
	}
}

func readFile(t *testing.T, path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func itoa(value int) string {
	return strconv.Itoa(value)
}
