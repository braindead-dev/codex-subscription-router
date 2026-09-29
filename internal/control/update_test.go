package control

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUpdateSettingKeepsTheUpdaterFields(t *testing.T) {
	root := t.TempDir()
	server := New("127.0.0.1:0", "token", root, "", nil, false)
	call := func(method, body string) (int, map[string]any) {
		request := httptest.NewRequest(method, "/v1/update", strings.NewReader(body))
		request.Header.Set("X-Codex-Mux-Token", "token")
		recorder := httptest.NewRecorder()
		server.http.Handler.ServeHTTP(recorder, request)
		var payload map[string]any
		_ = json.Unmarshal(recorder.Body.Bytes(), &payload)
		return recorder.Code, payload
	}

	if code, payload := call(http.MethodGet, ""); code != http.StatusOK || payload["enabled"] != false {
		t.Fatalf("GET before enable = %d %v", code, payload)
	}
	if code, _ := call(http.MethodPatch, `{"auto":true}`); code != http.StatusConflict {
		t.Fatalf("PATCH before enable = %d, want 409", code)
	}

	directory := filepath.Join(root, "update")
	if err := os.MkdirAll(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	settings := `{"app":"/Applications/Router.app","python":"/usr/bin/python3","auto":false}`
	if err := os.WriteFile(filepath.Join(directory, "settings.json"), []byte(settings), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "state.json"), []byte(`{"status":"ready"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if code, _ := call(http.MethodPatch, `{"auto":true}`); code != http.StatusOK {
		t.Fatalf("PATCH = %d", code)
	}
	code, payload := call(http.MethodGet, "")
	state, _ := payload["state"].(map[string]any)
	if code != http.StatusOK || payload["auto"] != true || state["status"] != "ready" {
		t.Fatalf("GET after PATCH = %d %v", code, payload)
	}
	written := readJSONFile(filepath.Join(directory, "settings.json"))
	if written["app"] != "/Applications/Router.app" || written["python"] != "/usr/bin/python3" {
		t.Fatalf("settings lost the updater's fields: %v", written)
	}
}
