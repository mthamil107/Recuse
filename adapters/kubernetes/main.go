// Command recuse-webhook is a Kubernetes ValidatingAdmissionWebhook for the
// Recuse project. On each governed CREATE/UPDATE/DELETE/CONNECT operation by a
// non-exempt identity it emits the cooperative Recuse Signal (spec §4) so a
// compliant LLM agent recuses itself — either as a non-blocking admission
// warning (mode=warn, the default) or as a 403 denial carrying the sentinel
// (mode=deny).
//
// This is a COOPERATIVE SIGNAL, NOT A SECURITY CONTROL (spec §9). A
// non-conforming or malicious caller can ignore the warning and proceed with
// valid credentials. Do not rely on this webhook as the sole protection for any
// resource. See ../../spec/recuse-signal-v0.1.md.
//
// SAFETY (why this cannot deadlock a cluster — see README.md "Safety"):
//   - The ValidatingWebhookConfiguration uses failurePolicy: Ignore (fail-open):
//     if this server is down, slow, or returns an error, the apiserver ALLOWS
//     the request. A broken webhook never blocks the cluster.
//   - System namespaces (kube-system, kube-node-lease, kube-public, and the
//     adapter's own recuse-system) are excluded via namespaceSelector.
//   - System / node / own-service-account identities are exempted in decide().
//   - The default mode is "warn" (non-blocking).
//   - timeoutSeconds is small (5s).
//   - Instant kill switch: kubectl delete validatingwebhookconfiguration recuse
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	admissionv1 "k8s.io/api/admission/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	defaultAddr    = ":8443"
	defaultCert    = "/etc/recuse/tls/tls.crt"
	defaultKey     = "/etc/recuse/tls/tls.key"
	maxBodyBytes   = 3 << 20 // 3 MiB; admission bodies are small.
	readTimeout    = 10 * time.Second
	writeTimeout   = 10 * time.Second
	idleTimeout    = 60 * time.Second
	handlerTimeout = 5 * time.Second
)

func main() {
	cfg := LoadConfig()

	addr := envOr("RECUSE_ADDR", defaultAddr)
	certFile := envOr("RECUSE_TLS_CERT", defaultCert)
	keyFile := envOr("RECUSE_TLS_KEY", defaultKey)

	mux := http.NewServeMux()
	mux.HandleFunc("/validate", validateHandler(cfg))
	// Liveness/readiness probe endpoint (no TLS client auth, plain 200).
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "ok")
	})

	srv := &http.Server{
		Addr:         addr,
		Handler:      http.TimeoutHandler(mux, handlerTimeout, "timeout"),
		ReadTimeout:  readTimeout,
		WriteTimeout: writeTimeout,
		IdleTimeout:  idleTimeout,
	}

	log.Printf("recuse-webhook: starting on %s (mode=%s, directive=%s, reason=%s, scope=%s, ref=%s)",
		addr, cfg.Mode, cfg.Directive, cfg.Reason, cfg.Scope, cfg.Ref)

	if err := srv.ListenAndServeTLS(certFile, keyFile); err != nil && err != http.ErrServerClosed {
		log.Fatalf("recuse-webhook: server error: %v", err)
	}
}

// validateHandler returns the HTTP handler for /validate. It decodes the
// AdmissionReview, runs the pure decide() function, logs the governed decision,
// and writes the AdmissionReview response.
//
// FAIL-OPEN at the application layer too: any decode error results in an
// allow=true response (with the request UID echoed if we could read it), so a
// malformed request never blocks. This complements the webhook's
// failurePolicy: Ignore.
func validateHandler(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes))
		if err != nil {
			// Cannot read the body; fail open with a bare allow (no UID known).
			writeReview(w, allowFallback(""))
			return
		}

		var review admissionv1.AdmissionReview
		if err := json.Unmarshal(body, &review); err != nil || review.Request == nil {
			// Malformed body: fail open. Echo the UID only if we could parse it.
			uid := ""
			if review.Request != nil {
				uid = string(review.Request.UID)
			}
			writeReview(w, allowFallback(uid))
			return
		}

		uid := string(review.Request.UID)

		// Core, pure decision.
		d := decide(review, cfg)
		logDecision(d, cfg.Mode)

		resp := buildResponse(uid, d)
		out := admissionv1.AdmissionReview{
			TypeMeta: metav1.TypeMeta{
				APIVersion: "admission.k8s.io/v1",
				Kind:       "AdmissionReview",
			},
			Response: resp,
		}
		writeReview(w, out)
	}
}

// allowFallback builds an allow=true AdmissionReview echoing the given UID. Used
// on any path where we cannot evaluate the request, so the webhook fails open.
func allowFallback(uid string) admissionv1.AdmissionReview {
	return admissionv1.AdmissionReview{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "admission.k8s.io/v1",
			Kind:       "AdmissionReview",
		},
		Response: buildResponse(uid, Decision{Allowed: true, Action: actionAllowed}),
	}
}

// writeReview serializes an AdmissionReview as JSON. A marshal/write failure is
// logged but cannot be recovered into a meaningful response at that point; the
// apiserver's failurePolicy: Ignore then treats the empty/failed response as a
// pass (fail-open).
func writeReview(w http.ResponseWriter, review admissionv1.AdmissionReview) {
	w.Header().Set("Content-Type", "application/json")
	body, err := json.Marshal(review)
	if err != nil {
		http.Error(w, fmt.Sprintf("marshal error: %v", err), http.StatusInternalServerError)
		return
	}
	_, _ = w.Write(body)
}
