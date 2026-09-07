package mux

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

const (
	modelsURL      = "https://chatgpt.com/backend-api/codex/models?client_version=0.153.0"
	modelsCacheTTL = 10 * time.Minute
	modelsMaxBytes = 4 << 20
)

// modelCatalog is the set of OpenAI models one subscription may run through
// Codex, as the backend reports it. Subscriptions differ: a model can be
// offered to one account and refused for another.
type modelCatalog struct {
	names     map[string]string
	expiresAt time.Time
}

type modelsResponse struct {
	Models []struct {
		Slug        string `json:"slug"`
		DisplayName string `json:"display_name"`
	} `json:"models"`
}

func (m *Multiplexer) accountModels(ctx context.Context, account state.Account) (map[string]string, bool) {
	now := time.Now()
	m.modelsMu.Lock()
	cached, ok := m.modelsCache[account.ID]
	m.modelsMu.Unlock()
	if ok && now.Before(cached.expiresAt) {
		return cached.names, cached.names != nil
	}
	names, err := fetchAccountModels(ctx, m.profileClient, modelsURL, filepath.Join(account.CodexHome, "auth.json"))
	if err != nil {
		names = nil
	}
	m.modelsMu.Lock()
	m.modelsCache[account.ID] = modelCatalog{names: names, expiresAt: now.Add(modelsCacheTTL)}
	m.modelsMu.Unlock()
	return names, names != nil
}

func fetchAccountModels(ctx context.Context, client *http.Client, endpoint, authPath string) (map[string]string, error) {
	credentials, err := readAuthFile(authPath)
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("create models request: %w", err)
	}
	request.Header.Set("Authorization", "Bearer "+credentials.Tokens.AccessToken)
	if credentials.Tokens.AccountID != "" {
		request.Header.Set("ChatGPT-Account-ID", credentials.Tokens.AccountID)
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "Codex Subscription Router")
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("fetch models: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, modelsMaxBytes))
		return nil, fmt.Errorf("fetch models: status %d", response.StatusCode)
	}
	var decoded modelsResponse
	if err := json.NewDecoder(io.LimitReader(response.Body, modelsMaxBytes)).Decode(&decoded); err != nil {
		return nil, fmt.Errorf("decode models: %w", err)
	}
	names := make(map[string]string, len(decoded.Models))
	for _, model := range decoded.Models {
		if model.Slug != "" {
			names[model.Slug] = model.DisplayName
		}
	}
	return names, nil
}

// modelSupport says which connected subscriptions can run a model. A model
// no subscription lists natively is served by another provider through the
// configured proxy and is not gated; an account whose list is unavailable is
// not excluded.
type modelSupport struct {
	name        string
	native      bool
	supporting  []state.Account
	unsupported map[string]struct{}
}

func (m *Multiplexer) modelSupportFor(ctx context.Context, model string) modelSupport {
	support := modelSupport{name: model, unsupported: make(map[string]struct{})}
	if model == "" {
		return support
	}
	var unknown []state.Account
	for _, account := range m.store.Accounts() {
		if !account.Enabled {
			continue
		}
		names, ok := m.accountModels(ctx, account)
		if !ok {
			unknown = append(unknown, account)
			continue
		}
		if display, listed := names[model]; listed {
			support.native = true
			support.supporting = append(support.supporting, account)
			if display != "" {
				support.name = display
			}
			continue
		}
		support.unsupported[account.ID] = struct{}{}
	}
	if !support.native {
		return modelSupport{name: model}
	}
	support.supporting = append(support.supporting, unknown...)
	return support
}

func modelFromParams(params json.RawMessage) string {
	var decoded struct {
		Model *string `json:"model"`
	}
	if json.Unmarshal(params, &decoded) != nil || decoded.Model == nil {
		return ""
	}
	return *decoded.Model
}

func (support modelSupport) supportsAccount(accountID string) bool {
	_, excluded := support.unsupported[accountID]
	return !excluded
}

func (support modelSupport) message(ownerLabel string) string {
	labels := make([]string, 0, len(support.supporting))
	for _, account := range support.supporting {
		labels = append(labels, account.Label)
	}
	sort.Strings(labels)
	if len(labels) == 0 {
		return fmt.Sprintf("%s is not available on any connected subscription.", support.name)
	}
	if ownerLabel == "" {
		return fmt.Sprintf("%s is only available on %s, which is out of usage.", support.name, strings.Join(labels, " and "))
	}
	return fmt.Sprintf(
		"%s is only available on %s, but this chat runs on %s. Start a new chat to use it.",
		support.name, strings.Join(labels, " and "), ownerLabel,
	)
}
