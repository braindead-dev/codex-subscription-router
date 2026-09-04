package mux

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// linkRolloutIntoHome gives an account its own path to another account's
// rollout. Codex resumes a thread only from a rollout inside the account's
// sessions directory, so the file is hard-linked there under the source's
// sessions/YYYY/MM/DD layout. Both accounts keep appending to the one file,
// and the link outlives the source deleting its own name.
func linkRolloutIntoHome(sourcePath, codexHome string) (string, error) {
	marker := string(filepath.Separator) + "sessions" + string(filepath.Separator)
	index := strings.LastIndex(sourcePath, marker)
	if index < 0 {
		return "", fmt.Errorf("rollout is not in a sessions directory: %s", sourcePath)
	}
	target := filepath.Join(codexHome, "sessions", sourcePath[index+len(marker):])
	if target == sourcePath {
		return target, nil
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return "", fmt.Errorf("create sessions directory: %w", err)
	}
	err := os.Link(sourcePath, target)
	if err == nil {
		return target, nil
	}
	if !errors.Is(err, fs.ErrExist) {
		return "", fmt.Errorf("link rollout: %w", err)
	}
	sourceInfo, sourceErr := os.Stat(sourcePath)
	targetInfo, targetErr := os.Stat(target)
	if sourceErr != nil || targetErr != nil || !os.SameFile(sourceInfo, targetInfo) {
		return "", fmt.Errorf("a different rollout already exists at %s", target)
	}
	return target, nil
}
