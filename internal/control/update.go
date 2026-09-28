package control

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
)

// update reports what scripts/update.py last found and holds the one setting
// the app changes: whether a ready update installs when the app quits.
func (s *Server) update(response http.ResponseWriter, request *http.Request) {
	if !s.authorized(request) {
		writeJSON(response, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	directory := filepath.Join(s.root, "update")
	settings := readJSONFile(filepath.Join(directory, "settings.json"))
	switch request.Method {
	case http.MethodGet:
		writeJSON(response, http.StatusOK, map[string]any{
			"enabled": settings != nil,
			"auto":    settings["auto"] == true,
			"state":   readJSONFile(filepath.Join(directory, "state.json")),
		})
	case http.MethodPatch:
		var input struct {
			Auto bool `json:"auto"`
		}
		if err := decodeJSON(request, &input); err != nil {
			writeJSON(response, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		if settings == nil {
			writeJSON(response, http.StatusConflict, map[string]any{
				"error": "updates are not enabled; run scripts/update.py enable",
			})
			return
		}
		settings["auto"] = input.Auto
		if err := writeJSONFile(filepath.Join(directory, "settings.json"), settings); err != nil {
			writeJSON(response, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(response, http.StatusOK, map[string]any{"auto": input.Auto})
	default:
		methodNotAllowed(response)
	}
}

func readJSONFile(path string) map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var value map[string]any
	if json.Unmarshal(data, &value) != nil {
		return nil
	}
	return value
}

func writeJSONFile(path string, value map[string]any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	temporary := filepath.Join(filepath.Dir(path), "."+filepath.Base(path)+".tmp")
	if err := os.WriteFile(temporary, append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}
