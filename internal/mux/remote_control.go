package mux

import (
	"context"
	"encoding/json"
	"fmt"
)

// RemoteControlStatus reads the remote-control state of one account's
// app-server. Remote control is enrolled per ChatGPT account, so a secondary
// subscription has to be enabled and paired through its own child rather than
// the controller the desktop talks to.
func (m *Multiplexer) RemoteControlStatus(ctx context.Context, accountID string) (json.RawMessage, error) {
	return m.remoteControlRequest(ctx, accountID, "remoteControl/status/read", nil)
}

func (m *Multiplexer) SetRemoteControl(ctx context.Context, accountID string, enabled bool) (json.RawMessage, error) {
	method := "remoteControl/disable"
	if enabled {
		method = "remoteControl/enable"
	}
	return m.remoteControlRequest(ctx, accountID, method, nil)
}

// StartRemoteControlPairing returns a short-lived manual pairing code that a
// phone or another computer enters to control this machine as that account.
func (m *Multiplexer) StartRemoteControlPairing(ctx context.Context, accountID string) (json.RawMessage, error) {
	params, _ := json.Marshal(map[string]any{"manualCode": true})
	return m.remoteControlRequest(ctx, accountID, "remoteControl/pairing/start", params)
}

func (m *Multiplexer) remoteControlRequest(ctx context.Context, accountID, method string, params json.RawMessage) (json.RawMessage, error) {
	account, ok := m.store.Account(accountID)
	if !ok {
		return nil, fmt.Errorf("account %q not found", accountID)
	}
	child, ok := m.child(account.ID)
	if !ok {
		return nil, fmt.Errorf("account %q is unavailable", accountID)
	}
	response, err := child.Request(ctx, method, params)
	if err != nil {
		return nil, err
	}
	if len(response.Result) == 0 {
		return json.RawMessage(`{}`), nil
	}
	return response.Result, nil
}
