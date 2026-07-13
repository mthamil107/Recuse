package main

// telemetry.go — OPT-IN, privacy-preserving emission telemetry (default OFF).
//
// When RECUSE_TELEMETRY is enabled, the webhook writes one coarse, anonymized
// JSON line per governed signal to stdout (captured by the cluster log
// pipeline; redirect `kubectl logs ... | grep recuse.telemetry` to a file to
// aggregate). It records ONLY an hour-bucketed timestamp, the protocol
// ("kubernetes"), the directive, a coded outcome, and count=1. It NEVER records
// the user, groups, namespace, resource, or object name — no PII. See
// ../telemetry/README.md.
//
// This is additive and best-effort: any failure is swallowed and never affects
// the admission response.

import (
	"encoding/json"
	"os"
	"strings"
	"sync"
	"time"
)

// telemetrySchema is the marker the aggregator filters on, so telemetry lines
// can safely coexist with the decision log on the same stdout stream.
const telemetrySchema = "recuse.telemetry/v1"

// telemetryEvent is the coarse, non-identifying record. No user/ns/resource/PII.
type telemetryEvent struct {
	Schema    string `json:"schema"`
	Timestamp string `json:"timestamp"`
	Protocol  string `json:"protocol"`
	Directive string `json:"directive"`
	Outcome   string `json:"outcome"`
	Count     int    `json:"count"`
}

// telemetryMu serializes writes so concurrent requests don't interleave bytes
// within a single JSON line on stdout.
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

// sanitizeDirective folds the configured directive to a known token so no
// arbitrary operator-supplied text is written into telemetry.
func sanitizeDirective(d string) string {
	switch strings.ToLower(strings.TrimSpace(d)) {
	case "deny", "throttle", "warn":
		return strings.ToLower(strings.TrimSpace(d))
	default:
		return "other"
	}
}

// emitTelemetry writes one coarse JSON line for a governed decision. Best-effort:
// disabled or non-governed decisions are no-ops, and any failure is swallowed so
// telemetry never affects the admission response. Records NO user/group/
// namespace/resource/name — only protocol/directive/outcome + an hour bucket.
func emitTelemetry(d Decision, cfg Config) {
	if !telemetryEnabled() || !d.Governed {
		return
	}
	ev := telemetryEvent{
		Schema:    telemetrySchema,
		Timestamp: time.Now().UTC().Truncate(time.Hour).Format(time.RFC3339),
		Protocol:  "kubernetes",
		Directive: sanitizeDirective(cfg.Directive),
		// A governed decision means the signal was emitted (mode=warn attaches the
		// sentinel warning; mode=deny returns it in the 403). An *enforced* deny is
		// not a voluntary withdrawal, so it is recorded as "emitted", not
		// "withdrawn" (see ../telemetry/README.md).
		Outcome: "emitted",
		Count:   1,
	}
	line, err := json.Marshal(ev)
	if err != nil {
		return
	}
	line = append(line, '\n')

	telemetryMu.Lock()
	defer telemetryMu.Unlock()
	_, _ = os.Stdout.Write(line)
}
