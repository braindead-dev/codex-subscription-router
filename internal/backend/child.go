package backend

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

type Inbound struct {
	AccountID string
	Message   protocol.Message
	Raw       []byte
}

type response struct {
	message protocol.Message
	err     error
}

// Child owns one real Codex app-server process and one isolated CODEX_HOME.
type Child struct {
	accountID string
	exe       string
	args      []string
	env       []string
	inbound   chan<- Inbound

	command   *exec.Cmd
	stdin     io.WriteCloser
	writeMu   sync.Mutex
	pendingMu sync.Mutex
	pending   map[string]chan response
	sequence  atomic.Uint64
	closed    chan struct{}
	closeOnce sync.Once
}

func Start(accountID, codexHome, executable string, args, baseEnv []string, inbound chan<- Inbound) (*Child, error) {
	env := withEnvironment(baseEnv, "CODEX_HOME", codexHome)
	env = withEnvironment(env, "CODEX_SQLITE_HOME", codexHome)
	command := exec.Command(executable, args...)
	command.Env = env
	stdin, err := command.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("open Codex stdin: %w", err)
	}
	stdout, err := command.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("open Codex stdout: %w", err)
	}
	command.Stderr = os.Stderr

	child := &Child{
		accountID: accountID,
		exe:       executable,
		args:      append([]string(nil), args...),
		env:       env,
		inbound:   inbound,
		command:   command,
		stdin:     stdin,
		pending:   make(map[string]chan response),
		closed:    make(chan struct{}),
	}
	if err := command.Start(); err != nil {
		return nil, fmt.Errorf("start Codex app-server for %s: %w", accountID, err)
	}
	go child.readLoop(stdout)
	go child.waitLoop()
	return child, nil
}

func (c *Child) Send(message protocol.Message) error {
	encoded, err := protocol.Encode(message)
	if err != nil {
		return err
	}
	return c.SendRaw(encoded)
}

func (c *Child) SendRaw(encoded []byte) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	select {
	case <-c.closed:
		return errors.New("Codex app-server is closed")
	default:
	}
	if _, err := c.stdin.Write(append(encoded, '\n')); err != nil {
		return fmt.Errorf("write Codex app-server request: %w", err)
	}
	return nil
}

func (c *Child) Request(ctx context.Context, method string, params json.RawMessage) (protocol.Message, error) {
	id := protocol.StringID("__codex_mux_" + strconv.FormatUint(c.sequence.Add(1), 10))
	key := protocol.RequestIDKey(id)
	responses := make(chan response, 1)
	c.pendingMu.Lock()
	c.pending[key] = responses
	c.pendingMu.Unlock()

	if err := c.Send(protocol.Request(method, id, params)); err != nil {
		c.removePending(key)
		return protocol.Message{}, err
	}
	select {
	case received := <-responses:
		if received.err != nil {
			return protocol.Message{}, received.err
		}
		if received.message.Error != nil {
			return received.message, fmt.Errorf("%s: %s", method, received.message.Error.Message)
		}
		return received.message, nil
	case <-ctx.Done():
		c.removePending(key)
		return protocol.Message{}, ctx.Err()
	case <-c.closed:
		c.removePending(key)
		return protocol.Message{}, errors.New("Codex app-server closed while awaiting response")
	}
}

func (c *Child) Close() error {
	if c.command.Process == nil {
		return nil
	}
	return c.command.Process.Signal(os.Interrupt)
}

func (c *Child) readLoop(stdout io.ReadCloser) {
	if err := protocol.ReadLines(stdout, c.dispatch); err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: read %s app-server: %v\n", c.accountID, err)
	}
	// The app-server answers only through this reader. Once it stops, end the
	// app-server too, so requests fail instead of waiting on replies that can
	// no longer arrive; closing the pipe frees it from a blocked write.
	_ = stdout.Close()
	_ = c.Close()
}

func (c *Child) dispatch(raw []byte) {
	message, err := protocol.Parse(raw)
	if err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: %s emitted invalid JSON: %v\n", c.accountID, err)
		return
	}
	if message.Method == "" && len(message.ID) > 0 {
		key := protocol.RequestIDKey(message.ID)
		c.pendingMu.Lock()
		responses := c.pending[key]
		if responses != nil {
			delete(c.pending, key)
		}
		c.pendingMu.Unlock()
		if responses != nil {
			responses <- response{message: message}
			return
		}
	}
	c.inbound <- Inbound{AccountID: c.accountID, Message: message, Raw: raw}
}

func (c *Child) waitLoop() {
	err := c.command.Wait()
	c.closeOnce.Do(func() { close(c.closed) })
	c.pendingMu.Lock()
	defer c.pendingMu.Unlock()
	for key, responses := range c.pending {
		responses <- response{err: fmt.Errorf("Codex app-server exited: %w", err)}
		delete(c.pending, key)
	}
}

func (c *Child) removePending(key string) {
	c.pendingMu.Lock()
	delete(c.pending, key)
	c.pendingMu.Unlock()
}

func withEnvironment(environment []string, key, value string) []string {
	prefix := key + "="
	result := make([]string, 0, len(environment)+1)
	for _, entry := range environment {
		if !strings.HasPrefix(entry, prefix) {
			result = append(result, entry)
		}
	}
	return append(result, prefix+value)
}
