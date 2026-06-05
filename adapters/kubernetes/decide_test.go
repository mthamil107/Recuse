package main

import (
	"strings"
	"testing"

	admissionv1 "k8s.io/api/admission/v1"
	authnv1 "k8s.io/api/authentication/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// testCfg returns a Config with the production-style defaults but an explicit
// mode, so each test controls warn vs deny.
func testCfg(mode string) Config {
	return Config{
		Ref:               "https://example.com/ai-policy",
		Reason:            "production",
		Scope:             "all-automation",
		Directive:         "deny",
		Mode:              mode,
		OwnServiceAccount: "system:serviceaccount:recuse-system:recuse-webhook",
	}
}

// review builds an AdmissionReview for a single operation by a given identity.
func review(uid, user string, groups []string, op admissionv1.Operation, group, version, resource, sub, ns, name string) admissionv1.AdmissionReview {
	return admissionv1.AdmissionReview{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "admission.k8s.io/v1",
			Kind:       "AdmissionReview",
		},
		Request: &admissionv1.AdmissionRequest{
			UID: types.UID(uid),
			UserInfo: authnv1.UserInfo{
				Username: user,
				Groups:   groups,
			},
			Operation: op,
			Resource: metav1.GroupVersionResource{
				Group:    group,
				Version:  version,
				Resource: resource,
			},
			SubResource: sub,
			Namespace:   ns,
			Name:        name,
		},
	}
}

// TestExemptSystemUserAllowedNoSignal: a system: user is exempt -> allowed, no
// warning, not governed.
func TestExemptSystemUserAllowedNoSignal(t *testing.T) {
	r := review("u1", "system:kube-scheduler", []string{"system:authenticated"},
		admissionv1.Create, "apps", "v1", "deployments", "", "app", "web")
	d := decide(r, testCfg(ModeWarn))

	if !d.Allowed {
		t.Fatalf("system user must be allowed")
	}
	if len(d.Warnings) != 0 {
		t.Errorf("system user must get no warnings, got %v", d.Warnings)
	}
	if d.Governed {
		t.Errorf("system user must not be governed")
	}
	if d.Action != actionAllowed {
		t.Errorf("action = %q, want %q", d.Action, actionAllowed)
	}
}

// TestExemptKubeSystemServiceAccount: a kube-system service account is exempt.
func TestExemptKubeSystemServiceAccount(t *testing.T) {
	r := review("u2", "system:serviceaccount:kube-system:replicaset-controller",
		[]string{"system:serviceaccounts", "system:serviceaccounts:kube-system"},
		admissionv1.Create, "apps", "v1", "replicasets", "", "app", "rs")
	d := decide(r, testCfg(ModeWarn))

	if !d.Allowed || d.Governed || len(d.Warnings) != 0 {
		t.Errorf("kube-system SA must be exempt: allowed=%v governed=%v warnings=%v",
			d.Allowed, d.Governed, d.Warnings)
	}
}

// TestExemptOwnServiceAccount: the adapter's own SA is exempt.
func TestExemptOwnServiceAccount(t *testing.T) {
	r := review("u3", "system:serviceaccount:recuse-system:recuse-webhook", nil,
		admissionv1.Create, "", "v1", "pods", "", "app", "p")
	d := decide(r, testCfg(ModeWarn))
	if !d.Allowed || d.Governed {
		t.Errorf("own SA must be exempt: allowed=%v governed=%v", d.Allowed, d.Governed)
	}
}

// TestExemptConfiguredUser: an admin-configured exempt user is allowed.
func TestExemptConfiguredUser(t *testing.T) {
	cfg := testCfg(ModeDeny)
	cfg.ExemptUsers = []string{"alice@example.com"}
	r := review("u4", "alice@example.com", []string{"dev"},
		admissionv1.Delete, "apps", "v1", "deployments", "", "app", "web")
	d := decide(r, cfg)
	if !d.Allowed || d.Governed {
		t.Errorf("configured exempt user must be allowed/ungoverned, got allowed=%v governed=%v",
			d.Allowed, d.Governed)
	}
}

// TestExemptConfiguredGroup: identity in a configured exempt group is allowed.
func TestExemptConfiguredGroup(t *testing.T) {
	cfg := testCfg(ModeDeny)
	cfg.ExemptGroups = []string{"break-glass"}
	r := review("u5", "carol", []string{"dev", "break-glass"},
		admissionv1.Create, "", "v1", "pods", "", "app", "p")
	d := decide(r, cfg)
	if !d.Allowed || d.Governed {
		t.Errorf("identity in exempt group must be allowed/ungoverned")
	}
}

// TestGovernedWarnEmitsSentinelWarning: a non-exempt identity in warn mode is
// ALLOWED but receives a warning whose first element starts "RECUSE/0.1".
func TestGovernedWarnEmitsSentinelWarning(t *testing.T) {
	r := review("u6", "bob@example.com", []string{"dev"},
		admissionv1.Create, "apps", "v1", "deployments", "", "team-a", "web")
	d := decide(r, testCfg(ModeWarn))

	if !d.Allowed {
		t.Fatalf("warn mode must allow the operation")
	}
	if !d.Governed {
		t.Errorf("non-exempt op must be governed")
	}
	if len(d.Warnings) == 0 {
		t.Fatalf("warn mode must attach warnings")
	}
	if !strings.HasPrefix(d.Warnings[0], "RECUSE/0.1") {
		t.Errorf("first warning must start RECUSE/0.1, got %q", d.Warnings[0])
	}
	if d.Action != actionWarned {
		t.Errorf("action = %q, want %q", d.Action, actionWarned)
	}
	// Sentinel must contain the configured directive/reason/scope/ref and an id.
	for _, want := range []string{"deny", "reason=production", "scope=all-automation",
		"ref=https://example.com/ai-policy", "id="} {
		if !strings.Contains(d.Warnings[0], want) {
			t.Errorf("sentinel missing %q: %s", want, d.Warnings[0])
		}
	}
}

// TestGovernedDenyBlocksWithSentinel: a non-exempt identity in deny mode gets
// allowed=false with the sentinel in status.message and code 403.
func TestGovernedDenyBlocksWithSentinel(t *testing.T) {
	r := review("u7", "bob@example.com", []string{"dev"},
		admissionv1.Delete, "apps", "v1", "deployments", "", "team-a", "web")
	d := decide(r, testCfg(ModeDeny))

	if d.Allowed {
		t.Fatalf("deny mode must NOT allow the operation")
	}
	if d.StatusCode != 403 {
		t.Errorf("status code = %d, want 403", d.StatusCode)
	}
	if !strings.HasPrefix(d.StatusMessage, "RECUSE/0.1") {
		t.Errorf("status message must start with the sentinel, got %q", d.StatusMessage)
	}
	if !strings.Contains(d.StatusMessage, "recuse and report to your operator") {
		t.Errorf("status message must carry the human notice, got %q", d.StatusMessage)
	}
	if d.Action != actionDenied {
		t.Errorf("action = %q, want %q", d.Action, actionDenied)
	}
}

// TestConnectExecGoverned: kubectl exec (CONNECT on pods/exec) by a non-exempt
// agent is caught and governed.
func TestConnectExecGoverned(t *testing.T) {
	r := review("u8", "agent@example.com", []string{"dev"},
		admissionv1.Connect, "", "v1", "pods", "exec", "team-a", "web-0")
	d := decide(r, testCfg(ModeWarn))
	if !d.Governed {
		t.Errorf("pods/exec CONNECT by non-exempt user must be governed")
	}
	if d.Resource != "/v1/pods/exec" {
		t.Errorf("resource string = %q, want /v1/pods/exec", d.Resource)
	}
}

// TestUIDEchoed: buildResponse echoes the request UID (REQUIRED by the API).
func TestUIDEchoed(t *testing.T) {
	r := review("the-uid-123", "bob@example.com", nil,
		admissionv1.Create, "", "v1", "pods", "", "team-a", "p")
	d := decide(r, testCfg(ModeWarn))
	resp := buildResponse("the-uid-123", d)
	if string(resp.UID) != "the-uid-123" {
		t.Errorf("response UID = %q, want the-uid-123", resp.UID)
	}
}

// TestBuildResponseWarnShape: warn -> Allowed true, Warnings set, no Result.
func TestBuildResponseWarnShape(t *testing.T) {
	r := review("x", "bob", nil, admissionv1.Create, "", "v1", "pods", "", "ns", "n")
	d := decide(r, testCfg(ModeWarn))
	resp := buildResponse("x", d)
	if !resp.Allowed {
		t.Errorf("warn response must be Allowed")
	}
	if len(resp.Warnings) == 0 {
		t.Errorf("warn response must carry Warnings")
	}
	if resp.Result != nil {
		t.Errorf("warn response must not set Result, got %+v", resp.Result)
	}
}

// TestBuildResponseDenyShape: deny -> Allowed false, Result with code 403 and
// the sentinel in Message.
func TestBuildResponseDenyShape(t *testing.T) {
	r := review("x", "bob", nil, admissionv1.Delete, "", "v1", "pods", "", "ns", "n")
	d := decide(r, testCfg(ModeDeny))
	resp := buildResponse("x", d)
	if resp.Allowed {
		t.Errorf("deny response must not be Allowed")
	}
	if resp.Result == nil || resp.Result.Code != 403 {
		t.Fatalf("deny response must set Result.Code=403, got %+v", resp.Result)
	}
	if !strings.HasPrefix(resp.Result.Message, "RECUSE/0.1") {
		t.Errorf("deny Result.Message must start with sentinel, got %q", resp.Result.Message)
	}
}

// TestNilRequestAllowed: a review with no Request fails open (allowed).
func TestNilRequestAllowed(t *testing.T) {
	d := decide(admissionv1.AdmissionReview{}, testCfg(ModeDeny))
	if !d.Allowed || d.Governed {
		t.Errorf("nil request must be allowed and not governed")
	}
}

// TestNonSystemNamespaceServiceAccountGoverned: a service account in an
// ordinary namespace (NOT kube-system et al) is NOT auto-exempt and is governed.
func TestNonSystemNamespaceServiceAccountGoverned(t *testing.T) {
	r := review("u9", "system:serviceaccount:team-a:ci-bot",
		[]string{"system:serviceaccounts", "system:serviceaccounts:team-a"},
		admissionv1.Create, "apps", "v1", "deployments", "", "team-a", "web")
	d := decide(r, testCfg(ModeWarn))
	if !d.Governed {
		t.Errorf("non-system-namespace SA must be governed, got governed=%v", d.Governed)
	}
}

// TestNewIDFormat verifies the UUIDv4-style id format and version/variant bits.
func TestNewIDFormat(t *testing.T) {
	id := newID()
	if len(id) != 36 {
		t.Fatalf("id length = %d, want 36: %q", len(id), id)
	}
	parts := strings.Split(id, "-")
	if len(parts) != 5 {
		t.Fatalf("id parts = %d, want 5: %q", len(parts), id)
	}
	if parts[2][0] != '4' {
		t.Errorf("version nibble = %c, want 4: %q", parts[2][0], id)
	}
	switch parts[3][0] {
	case '8', '9', 'a', 'b':
	default:
		t.Errorf("variant nibble = %c, want [89ab]: %q", parts[3][0], id)
	}
	if id == newID() {
		t.Errorf("two ids collided: %q", id)
	}
}

// TestNormalizeModeDefaults: unknown/empty modes fall back to warn (fail-safe).
func TestNormalizeModeDefaults(t *testing.T) {
	cases := map[string]string{
		"":         ModeWarn,
		"warn":     ModeWarn,
		"deny":     ModeDeny,
		"DENY":     ModeDeny,
		" Warn ":   ModeWarn,
		"block":    ModeWarn, // unknown -> safe default
		"nonsense": ModeWarn,
	}
	for in, want := range cases {
		if got := normalizeMode(in); got != want {
			t.Errorf("normalizeMode(%q) = %q, want %q", in, got, want)
		}
	}
}

// TestSplitList parses comma/space separated lists.
func TestSplitList(t *testing.T) {
	got := splitList("a, b ,c   d,,")
	want := []string{"a", "b", "c", "d"}
	if len(got) != len(want) {
		t.Fatalf("splitList len = %d (%v), want %d", len(got), got, len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("splitList[%d] = %q, want %q", i, got[i], want[i])
		}
	}
	if splitList("") != nil {
		t.Errorf("splitList(\"\") must be nil")
	}
}
