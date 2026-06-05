package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	admissionv1 "k8s.io/api/admission/v1"
)

// postReview sends an AdmissionReview to the validate handler and decodes the
// response review.
func postReview(t *testing.T, cfg Config, in admissionv1.AdmissionReview) admissionv1.AdmissionReview {
	t.Helper()
	body, err := json.Marshal(in)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/validate", bytes.NewReader(body))
	rec := httptest.NewRecorder()
	validateHandler(cfg)(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body=%s", rec.Code, rec.Body.String())
	}
	var out admissionv1.AdmissionReview
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("unmarshal response: %v; body=%s", err, rec.Body.String())
	}
	return out
}

// TestHandlerWarnEndToEnd: a governed warn request returns allowed=true with the
// sentinel warning and the UID echoed.
func TestHandlerWarnEndToEnd(t *testing.T) {
	in := review("uid-warn", "bob@example.com", []string{"dev"},
		admissionv1.Create, "apps", "v1", "deployments", "", "team-a", "web")
	out := postReview(t, testCfg(ModeWarn), in)

	if out.Response == nil {
		t.Fatal("response missing")
	}
	if string(out.Response.UID) != "uid-warn" {
		t.Errorf("UID = %q, want uid-warn", out.Response.UID)
	}
	if !out.Response.Allowed {
		t.Errorf("warn must allow")
	}
	if len(out.Response.Warnings) == 0 || !strings.HasPrefix(out.Response.Warnings[0], "RECUSE/0.1") {
		t.Errorf("expected RECUSE warning, got %v", out.Response.Warnings)
	}
}

// TestHandlerDenyEndToEnd: a governed deny request returns allowed=false, 403,
// sentinel in message.
func TestHandlerDenyEndToEnd(t *testing.T) {
	in := review("uid-deny", "bob@example.com", []string{"dev"},
		admissionv1.Delete, "apps", "v1", "deployments", "", "team-a", "web")
	out := postReview(t, testCfg(ModeDeny), in)

	if out.Response.Allowed {
		t.Errorf("deny must not allow")
	}
	if out.Response.Result == nil || out.Response.Result.Code != 403 {
		t.Fatalf("expected 403 Result, got %+v", out.Response.Result)
	}
	if !strings.HasPrefix(out.Response.Result.Message, "RECUSE/0.1") {
		t.Errorf("message must start with sentinel: %q", out.Response.Result.Message)
	}
}

// TestHandlerExemptEndToEnd: a system user gets allowed, no warning, UID echoed.
func TestHandlerExemptEndToEnd(t *testing.T) {
	in := review("uid-sys", "system:kube-scheduler", nil,
		admissionv1.Create, "apps", "v1", "deployments", "", "team-a", "web")
	out := postReview(t, testCfg(ModeDeny), in)

	if !out.Response.Allowed {
		t.Errorf("system user must be allowed even in deny mode")
	}
	if len(out.Response.Warnings) != 0 {
		t.Errorf("exempt must have no warnings, got %v", out.Response.Warnings)
	}
	if string(out.Response.UID) != "uid-sys" {
		t.Errorf("UID = %q, want uid-sys", out.Response.UID)
	}
}

// TestHandlerMalformedBodyFailsOpen: a non-JSON body fails open (200 + allow).
func TestHandlerMalformedBodyFailsOpen(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/validate", strings.NewReader("{not json"))
	rec := httptest.NewRecorder()
	validateHandler(testCfg(ModeDeny))(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (fail-open)", rec.Code)
	}
	var out admissionv1.AdmissionReview
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("response not valid review: %v", err)
	}
	if out.Response == nil || !out.Response.Allowed {
		t.Errorf("malformed body must fail open (allowed=true)")
	}
}

// TestHandlerRejectsGET: non-POST is 405.
func TestHandlerRejectsGET(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/validate", nil)
	rec := httptest.NewRecorder()
	validateHandler(testCfg(ModeWarn))(rec, req)
	if rec.Code != http.StatusMethodNotAllowed {
		t.Errorf("GET status = %d, want 405", rec.Code)
	}
}

// TestLoadConfigDefaults: empty env yields safe defaults (warn mode).
func TestLoadConfigDefaults(t *testing.T) {
	// Clear the relevant env so we observe defaults.
	for _, k := range []string{"RECUSE_REF", "RECUSE_REASON", "RECUSE_SCOPE",
		"RECUSE_DIRECTIVE", "RECUSE_MODE", "RECUSE_EXEMPT_USERS",
		"RECUSE_EXEMPT_GROUPS", "RECUSE_OWN_SERVICE_ACCOUNT"} {
		t.Setenv(k, "")
	}
	c := LoadConfig()
	if c.Mode != ModeWarn {
		t.Errorf("default mode = %q, want warn", c.Mode)
	}
	if c.Ref != defaultRef || c.Reason != defaultReason || c.Scope != defaultScope ||
		c.Directive != defaultDirective {
		t.Errorf("defaults wrong: %+v", c)
	}
}
