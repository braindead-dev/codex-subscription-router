package protocol

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// Message is the JSONL request, response, or notification envelope used by
// Codex app-server. The protocol intentionally omits the jsonrpc field.
type Message struct {
	ID     json.RawMessage `json:"id,omitempty"`
	Method string          `json:"method,omitempty"`
	Params json.RawMessage `json:"params,omitempty"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *RPCError       `json:"error,omitempty"`
}

type RPCError struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data,omitempty"`
}

func Parse(line []byte) (Message, error) {
	var message Message
	if err := json.Unmarshal(line, &message); err != nil {
		return Message{}, fmt.Errorf("parse app-server message: %w", err)
	}
	return message, nil
}

func Encode(message Message) ([]byte, error) {
	return json.Marshal(message)
}

func RequestIDKey(id json.RawMessage) string {
	return string(bytes.TrimSpace(id))
}

func StringID(value string) json.RawMessage {
	encoded, _ := json.Marshal(value)
	return encoded
}

func Request(method string, id json.RawMessage, params json.RawMessage) Message {
	return Message{ID: id, Method: method, Params: params}
}

func Success(id json.RawMessage, result json.RawMessage) Message {
	if result == nil {
		result = json.RawMessage(`{}`)
	}
	return Message{ID: id, Result: result}
}

func Failure(id json.RawMessage, code int, message string) Message {
	return Message{ID: id, Error: &RPCError{Code: code, Message: message}}
}
