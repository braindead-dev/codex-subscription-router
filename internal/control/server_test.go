package control

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

func TestPreflightAllowsEveryMethodTheRendererSends(t *testing.T) {
	server := New("127.0.0.1:0", "token", t.TempDir(), "", nil, false)
	request := httptest.NewRequest(http.MethodOptions, "/v1/preferred-account", nil)
	request.Header.Set("Origin", "app://-")
	recorder := httptest.NewRecorder()
	server.http.Handler.ServeHTTP(recorder, request)
	allowed := recorder.Header().Get("Access-Control-Allow-Methods")

	sources, err := filepath.Glob("../../ui/*.js")
	if err != nil || len(sources) == 0 {
		t.Fatalf("no renderer sources: %v", err)
	}
	method := regexp.MustCompile(`method:\s*"([A-Z]+)"`)
	for _, source := range sources {
		contents, err := os.ReadFile(source)
		if err != nil {
			t.Fatal(err)
		}
		for _, match := range method.FindAllStringSubmatch(string(contents), -1) {
			if !strings.Contains(allowed, match[1]) {
				t.Errorf("%s sends %s, which the preflight (%q) blocks", filepath.Base(source), match[1], allowed)
			}
		}
	}
}
