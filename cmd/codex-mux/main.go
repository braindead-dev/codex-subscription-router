package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/control"
	"github.com/b-nnett/codex-subscription-router/internal/mux"
	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

const defaultControlPort = 48123

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	realExecutable, err := resolveRealExecutable()
	if err != nil {
		return err
	}
	args := os.Args[1:]
	if !isInteractiveAppServer(args) {
		return passthrough(realExecutable, args)
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("resolve home directory: %w", err)
	}
	root := os.Getenv("CODEX_MUX_HOME")
	if root == "" {
		root = filepath.Join(home, ".codex-mux")
	}
	lock, err := acquireMultiplexerLock(root)
	if err != nil {
		return err
	}
	if lock == nil {
		return passthrough(realExecutable, args)
	}
	defer lock.Close()
	primaryCodexHome := os.Getenv("CODEX_HOME")
	if primaryCodexHome == "" {
		primaryCodexHome = filepath.Join(home, ".codex")
	}
	store, err := state.Open(root, primaryCodexHome)
	if err != nil {
		return err
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	multiplexer, err := mux.New(mux.Options{
		RealExecutable: realExecutable,
		RealArgs:       args,
		Environment:    os.Environ(),
		Store:          store,
		Output:         os.Stdout,
	})
	if err != nil {
		return err
	}
	if err := multiplexer.Start(ctx); err != nil {
		return err
	}
	defer multiplexer.Close()

	token, err := loadOrCreateToken(root)
	if err != nil {
		return err
	}
	port := defaultControlPort
	if value := os.Getenv("CODEX_MUX_CONTROL_PORT"); value != "" {
		if parsed, parseErr := strconv.Atoi(value); parseErr == nil && parsed > 0 && parsed <= 65535 {
			port = parsed
		}
	}
	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: account UI unavailable: %v\n", err)
	} else {
		controlServer := control.New(
			listener.Addr().String(),
			token,
			multiplexer,
			os.Getenv("CODEX_MUX_UI_TESTS") == "1",
		)
		go func() {
			if serveErr := controlServer.Serve(listener); serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
				fmt.Fprintf(os.Stderr, "codex-mux: control server: %v\n", serveErr)
			}
		}()
		defer func() {
			shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer shutdownCancel()
			_ = controlServer.Shutdown(shutdownCtx)
		}()
	}

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64*1024), 64*1024*1024)
	for scanner.Scan() {
		message, parseErr := protocol.Parse(scanner.Bytes())
		if parseErr != nil {
			fmt.Fprintf(os.Stderr, "codex-mux: ignore invalid client JSON: %v\n", parseErr)
			continue
		}
		multiplexer.HandleClient(message)
	}
	cancel()
	return scanner.Err()
}

// acquireMultiplexerLock claims the one multiplexer slot for a state root.
// Plugin runtimes the desktop starts reach Codex through CODEX_CLI_PATH,
// which is this wrapper, and ask it for an app-server of their own. Only the
// desktop's connection may multiplex: a second multiplexer would start a
// second live app-server on every subscription's home. When the slot is
// taken the caller gets nil and hands the request to the real binary on the
// account its environment already names.
func acquireMultiplexerLock(root string) (*os.File, error) {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, fmt.Errorf("create state root: %w", err)
	}
	lock, err := os.OpenFile(filepath.Join(root, "multiplexer.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, fmt.Errorf("open multiplexer lock: %w", err)
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, nil
		}
		return nil, fmt.Errorf("lock multiplexer slot: %w", err)
	}
	return lock, nil
}

func resolveRealExecutable() (string, error) {
	if configured := os.Getenv("CODEX_MUX_REAL_CODEX"); configured != "" {
		return configured, nil
	}
	executable, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("resolve wrapper executable: %w", err)
	}
	realExecutable := filepath.Join(filepath.Dir(executable), "codex.real")
	if _, err := os.Stat(realExecutable); err != nil {
		return "", fmt.Errorf("find bundled codex.real: %w", err)
	}
	return realExecutable, nil
}

func isInteractiveAppServer(args []string) bool {
	for index, argument := range args {
		if argument != "app-server" {
			continue
		}
		if index+1 < len(args) {
			switch args[index+1] {
			case "daemon", "proxy", "generate-ts", "generate-json-schema", "help":
				return false
			}
		}
		return true
	}
	return false
}

func passthrough(realExecutable string, args []string) error {
	command := exec.Command(realExecutable, args...)
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	command.Env = os.Environ()
	if err := command.Run(); err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			os.Exit(exitError.ExitCode())
		}
		return err
	}
	return nil
}

func loadOrCreateToken(root string) (string, error) {
	if configured := os.Getenv("CODEX_MUX_CONTROL_TOKEN"); configured != "" {
		return validateControlToken(configured)
	}
	path := filepath.Join(root, "control-token")
	if data, err := os.ReadFile(path); err == nil {
		token, validateErr := validateControlToken(string(data))
		if validateErr != nil {
			return "", fmt.Errorf("read control token: %w", validateErr)
		}
		if chmodErr := os.Chmod(path, 0o600); chmodErr != nil {
			return "", fmt.Errorf("secure control token: %w", chmodErr)
		}
		return token, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("read control token: %w", err)
	}
	bytes := make([]byte, 32)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate control token: %w", err)
	}
	token := hex.EncodeToString(bytes)
	if err := os.WriteFile(path, []byte(token), 0o600); err != nil {
		return "", fmt.Errorf("write control token: %w", err)
	}
	return token, nil
}

func validateControlToken(value string) (string, error) {
	token := strings.TrimSpace(value)
	decoded, err := hex.DecodeString(token)
	if err != nil || len(decoded) != 32 {
		return "", errors.New("control token must be exactly 32 random bytes encoded as hexadecimal")
	}
	return token, nil
}
