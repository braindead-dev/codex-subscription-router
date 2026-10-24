package mux

import (
	"bytes"
	"errors"
	"fmt"
	"io"
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
	target, _, err := shareRolloutIntoHome(sourcePath, codexHome, false)
	return target, err
}

// An ancestor is a read-only dependency of the moving chat. It may already
// have an independent, newer copy on the destination. Reuse that copy only
// when it contains the entire source as a byte-identical prefix; never replace
// its file or derived history. The bool reports a newly created hard link.
func shareRolloutIntoHome(sourcePath, codexHome string, ancestor bool) (string, bool, error) {
	marker := string(filepath.Separator) + "sessions" + string(filepath.Separator)
	index := strings.LastIndex(sourcePath, marker)
	if index < 0 {
		return "", false, fmt.Errorf("rollout is not in a sessions directory: %s", sourcePath)
	}
	target := filepath.Join(codexHome, "sessions", sourcePath[index+len(marker):])
	if target == sourcePath {
		return target, false, nil
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return "", false, fmt.Errorf("create sessions directory: %w", err)
	}
	err := os.Link(sourcePath, target)
	if err == nil {
		return target, true, nil
	}
	if !errors.Is(err, fs.ErrExist) {
		return "", false, fmt.Errorf("link rollout: %w", err)
	}
	sourceInfo, sourceErr := os.Stat(sourcePath)
	targetInfo, targetErr := os.Stat(target)
	if sourceErr == nil && targetErr == nil && os.SameFile(sourceInfo, targetInfo) {
		return target, false, nil
	}
	if ancestor && rolloutPrefixMatches(sourcePath, target) {
		return target, false, nil
	}
	return "", false, fmt.Errorf("a different rollout already exists at %s", target)
}

func rolloutPrefixMatches(sourcePath, targetPath string) bool {
	source, err := os.Open(sourcePath)
	if err != nil {
		return false
	}
	defer source.Close()
	target, err := os.Open(targetPath)
	if err != nil {
		return false
	}
	defer target.Close()
	sourceInfo, sourceErr := source.Stat()
	targetInfo, targetErr := target.Stat()
	if sourceErr != nil || targetErr != nil || !sourceInfo.Mode().IsRegular() || !targetInfo.Mode().IsRegular() || targetInfo.Size() < sourceInfo.Size() {
		return false
	}
	left, right := make([]byte, 64*1024), make([]byte, 64*1024)
	for remaining := sourceInfo.Size(); remaining > 0; {
		size := min(int64(len(left)), remaining)
		if _, err := io.ReadFull(source, left[:size]); err != nil {
			return false
		}
		if _, err := io.ReadFull(target, right[:size]); err != nil || !bytes.Equal(left[:size], right[:size]) {
			return false
		}
		remaining -= size
	}
	// Fail closed if either file changed while it was compared.
	sourceAfter, sourceErr := os.Stat(sourcePath)
	targetAfter, targetErr := os.Stat(targetPath)
	return sourceErr == nil && targetErr == nil &&
		os.SameFile(sourceInfo, sourceAfter) && os.SameFile(targetInfo, targetAfter) &&
		sourceAfter.Size() == sourceInfo.Size() && sourceAfter.ModTime() == sourceInfo.ModTime() &&
		targetAfter.Size() == targetInfo.Size() && targetAfter.ModTime() == targetInfo.ModTime()
}
