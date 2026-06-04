package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
)

// connectEvent is the JSON record appended to RECUSE_LOG on each connection.
type connectEvent struct {
	Timestamp  string `json:"timestamp"`
	ID         string `json:"id"`
	DBUser     string `json:"db_user"`
	Database   string `json:"database"`
	ClientAddr string `json:"client_addr"`
	Event      string `json:"event"`
}

// logMu serializes appends so concurrent connections don't interleave bytes
// within a single JSON line.
var logMu sync.Mutex

// logConnect appends one JSON line to path. It is best-effort: any failure
// (permission, missing dir we can't create, etc.) is swallowed so logging never
// fails a connection. encoding/json handles safe escaping of the string fields.
func logConnect(path string, ev connectEvent) {
	line, err := json.Marshal(ev)
	if err != nil {
		return
	}
	line = append(line, '\n')

	logMu.Lock()
	defer logMu.Unlock()

	// Best-effort create of the log directory (0700).
	if dir := filepath.Dir(path); dir != "" {
		_ = os.MkdirAll(dir, 0o700)
	}

	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.Write(line)
}
