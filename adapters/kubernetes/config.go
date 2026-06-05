package main

import (
	"os"
	"strings"
)

// Config is the policy configuration for the Recuse admission webhook. It is
// loaded from environment variables (typically sourced from a ConfigMap) with
// sane, safe defaults. See manifests/configmap.yaml for the documented surface.
//
// SAFETY: the defaults are deliberately non-blocking (Mode=warn) and the
// exemption lists always include system identities (see decide.go). Nothing
// here can mutate the cluster.
type Config struct {
	// Ref is the absolute URL of the human-readable governing policy
	// (sentinel `ref=`). RECUSE_REF.
	Ref string
	// Reason is the machine token for *why* access is governed (sentinel
	// `reason=`): production, pii, compliance, change-freeze, unowned, other.
	// RECUSE_REASON.
	Reason string
	// Scope is who the signal targets (sentinel `scope=`): all-automation,
	// llm-agents, unattended. RECUSE_SCOPE.
	Scope string
	// Directive informs the sentinel text (deny|throttle|warn). It is the
	// graduated cooperative directive from spec §4.2. RECUSE_DIRECTIVE.
	//
	// NOTE: Directive is the *advisory* token written into the sentinel line.
	// Mode (below) is the *operational* behavior of the webhook. They are
	// independent: e.g. Directive=deny with Mode=warn emits a "deny" sentinel
	// as a non-blocking warning so an agent recuses while humans are unaffected.
	Directive string
	// Mode is the operational behavior: "warn" (default; allow + warning) or
	// "deny" (block with 403 + sentinel in the status message). RECUSE_MODE.
	Mode string

	// ExemptUsers are additional usernames that are always allowed with no
	// signal (in addition to the built-in system exemptions). RECUSE_EXEMPT_USERS
	// (comma/space separated).
	ExemptUsers []string
	// ExemptGroups are additional groups that are always allowed with no signal.
	// RECUSE_EXEMPT_GROUPS (comma/space separated).
	ExemptGroups []string

	// OwnServiceAccount is the fully-qualified username of the adapter's own
	// service account (system:serviceaccount:<ns>:<name>), always exempt so the
	// webhook never governs itself. RECUSE_OWN_SERVICE_ACCOUNT.
	OwnServiceAccount string
}

const (
	defaultRef       = "https://example.com/ai-policy"
	defaultReason    = "production"
	defaultScope     = "all-automation"
	defaultDirective = "deny"
	defaultMode      = "warn"
)

// ModeWarn allows the operation but attaches the sentinel as a warning.
const ModeWarn = "warn"

// ModeDeny blocks the operation with a 403 carrying the sentinel.
const ModeDeny = "deny"

// LoadConfig builds a Config from the environment, applying defaults. It never
// fails: an unset or empty variable falls back to its default so the webhook
// always starts with a safe, non-blocking policy.
func LoadConfig() Config {
	c := Config{
		Ref:               envOr("RECUSE_REF", defaultRef),
		Reason:            envOr("RECUSE_REASON", defaultReason),
		Scope:             envOr("RECUSE_SCOPE", defaultScope),
		Directive:         envOr("RECUSE_DIRECTIVE", defaultDirective),
		Mode:              normalizeMode(os.Getenv("RECUSE_MODE")),
		ExemptUsers:       splitList(os.Getenv("RECUSE_EXEMPT_USERS")),
		ExemptGroups:      splitList(os.Getenv("RECUSE_EXEMPT_GROUPS")),
		OwnServiceAccount: os.Getenv("RECUSE_OWN_SERVICE_ACCOUNT"),
	}
	return c
}

// normalizeMode coerces the RECUSE_MODE value to a known mode, defaulting to
// the non-blocking "warn" for any empty or unrecognized value (fail-safe: an
// operator typo must never silently block the cluster).
func normalizeMode(v string) string {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case ModeDeny:
		return ModeDeny
	case ModeWarn:
		return ModeWarn
	default:
		return defaultMode
	}
}

func envOr(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

// splitList parses a comma- and/or whitespace-separated list into a slice,
// dropping empty entries. Returns nil for an empty input.
func splitList(s string) []string {
	fields := strings.FieldsFunc(s, func(r rune) bool {
		return r == ',' || r == ' ' || r == '\t' || r == '\n' || r == '\r'
	})
	if len(fields) == 0 {
		return nil
	}
	out := make([]string, 0, len(fields))
	for _, f := range fields {
		if f = strings.TrimSpace(f); f != "" {
			out = append(out, f)
		}
	}
	return out
}
