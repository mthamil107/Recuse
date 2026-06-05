package main

import (
	"encoding/json"
	"os"
	"sync"
	"time"
)

// decisionEvent is the JSON record emitted (one line, to stdout) per governed
// decision so it shows up in `kubectl logs`. Only governed decisions are logged
// (exempt/not-governed allows are silent to avoid drowning the cluster's normal
// traffic in noise).
type decisionEvent struct {
	Timestamp string   `json:"timestamp"`
	ID        string   `json:"id"`
	User      string   `json:"user"`
	Groups    []string `json:"groups"`
	Operation string   `json:"operation"`
	Resource  string   `json:"resource"`
	Namespace string   `json:"namespace"`
	Name      string   `json:"name"`
	Mode      string   `json:"mode"`
	Action    string   `json:"action"` // allowed | warned | denied
}

// logMu serializes writes so concurrent admission requests don't interleave
// bytes within a single JSON line on stdout.
var logMu sync.Mutex

// logDecision writes one JSON line to stdout for a governed decision. It is
// best-effort: a write failure never affects the admission response. Non-
// governed decisions are skipped.
func logDecision(d Decision, mode string) {
	if !d.Governed {
		return
	}
	ev := decisionEvent{
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		ID:        d.ID,
		User:      d.User,
		Groups:    d.Groups,
		Operation: d.Operation,
		Resource:  d.Resource,
		Namespace: d.Namespace,
		Name:      d.Name,
		Mode:      mode,
		Action:    d.Action,
	}
	line, err := json.Marshal(ev)
	if err != nil {
		return
	}
	line = append(line, '\n')

	logMu.Lock()
	defer logMu.Unlock()
	_, _ = os.Stdout.Write(line)
}
