package main

// telemetry.go — OPT-IN, privacy-preserving emission telemetry (default OFF).
//
// When RECUSE_TELEMETRY is enabled, the proxy appends one coarse, anonymized
// JSON line per emitted signal so an operator can count emissions. It records
// ONLY an hour-bucketed timestamp, the protocol ("postgres"), the directive,
// a coded outcome, and count=1. It NEVER records the client IP, DB user,
// database, query, or session id — no PII. See ../telemetry/README.md.
//
// This is additive and best-effort: any failure is swallowed and never affects
// a connection.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// telemetrySchema is the marker the aggregator filters on, so telemetry lines
// can safely coexist with other JSON logs.
const telemetrySchema = "recuse.telemetry/v1"

// defaultTelemetryLog is used when RECUSE_TELEMETRY_LOG is unset.
const defaultTelemetryLog = "/var/log/recuse/telemetry.json"

// telemetryEvent is the coarse, non-identifying record. No IP/host/user/PII.
type telemetryEvent struct {
	Schema    string `json:"schema"`
	Timestamp string `json:"timestamp"`
	Protocol  string `json:"protocol"`
	Directive string `json:"directive"`
	Outcome   string `json:"outcome"`
	Count     int    `json:"count"`
}

// telemetryMu serializes appends so concurrent connections don't interleave
// bytes within a single JSON line.
var telemetryMu sync.Mutex

// telemetryEnabled reports whether opt-in telemetry is on. Default OFF: only an
// explicit true-token turns it on; anything else (including unset) is OFF.
func telemetryEnabled() bool {
	switch os.Getenv("RECUSE_TELEMETRY") {
	case "true", "1", "on", "yes":
		return true
	default:
		return false
	}
}

// emitTelemetry appends one coarse JSON line to path. Best-effort: any failure
// (disabled, permission, marshal) is swallowed so telemetry never affects a
// connection. Records NO client IP, DB user, database, or query — only the
// protocol/directive/outcome and an hour-bucketed timestamp.
func emitTelemetry(path, protocol, directive, outcome string) {
	if !telemetryEnabled() {
		return
	}
	if path == "" {
		path = defaultTelemetryLog
	}
	ev := telemetryEvent{
		Schema:    telemetrySchema,
		Timestamp: time.Now().UTC().Truncate(time.Hour).Format(time.RFC3339),
		Protocol:  protocol,
		Directive: directive,
		Outcome:   outcome,
		Count:     1,
	}
	line, err := json.Marshal(ev)
	if err != nil {
		return
	}
	line = append(line, '\n')

	telemetryMu.Lock()
	defer telemetryMu.Unlock()

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
