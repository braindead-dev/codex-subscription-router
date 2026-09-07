package mux

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func TestModelSupportGatesOnlyNativeModels(t *testing.T) {
	root := t.TempDir()
	store, err := state.Open(filepath.Join(root, "mux"), filepath.Join(root, "primary"))
	if err != nil {
		t.Fatal(err)
	}
	work, err := store.AddAccount("Work")
	if err != nil {
		t.Fatal(err)
	}
	later := time.Now().Add(time.Hour)
	m := &Multiplexer{store: store, modelsCache: map[string]modelCatalog{
		"primary": {names: map[string]string{"gpt-6-astra": "GPT-6-Astra", "gpt-daybreak-blue-latest": "Daybreak Blue"}, expiresAt: later},
		work.ID:   {names: map[string]string{"gpt-6-astra": "GPT-6-Astra"}, expiresAt: later},
	}}
	ctx := context.Background()

	daybreak := m.modelSupportFor(ctx, "gpt-daybreak-blue-latest")
	if !daybreak.native || daybreak.supportsAccount(work.ID) || !daybreak.supportsAccount("primary") {
		t.Fatalf("expected Daybreak to be limited to primary, got %#v", daybreak)
	}
	want := "Daybreak Blue is only available on Primary, but this chat runs on Work. Start a new chat to use it."
	if got := daybreak.message("Work"); got != want {
		t.Fatalf("message %q, want %q", got, want)
	}
	if astra := m.modelSupportFor(ctx, "gpt-6-astra"); !astra.native || len(astra.unsupported) != 0 {
		t.Fatalf("expected Astra on every account, got %#v", astra)
	}
	if proxied := m.modelSupportFor(ctx, "combo/grok-4.6"); proxied.native || len(proxied.unsupported) != 0 {
		t.Fatalf("expected a proxied model to pass through ungated, got %#v", proxied)
	}
	if none := m.modelSupportFor(ctx, ""); none.native {
		t.Fatal("expected no gating without a model")
	}
	if got := modelFromParams(json.RawMessage(`{"threadId":"t","model":"gpt-6-astra"}`)); got != "gpt-6-astra" {
		t.Fatalf("modelFromParams returned %q", got)
	}
	if got := modelFromParams(json.RawMessage(`{"threadId":"t","model":null}`)); got != "" {
		t.Fatalf("expected a null model to be ignored, got %q", got)
	}
}
