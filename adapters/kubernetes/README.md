# Recuse — Kubernetes adapter (`recuse-webhook`)

A Kubernetes **ValidatingAdmissionWebhook** that emits the cooperative **Recuse
Signal** when a non-exempt identity performs a governed API operation, so a
compliant LLM agent **recuses** itself.

This is the Kubernetes binding for the Recuse project. It emits the sentinel
line defined in spec **section 4** of
[`../../spec/recuse-signal-v0.1.md`](../../spec/recuse-signal-v0.1.md).

> **This is a cooperative signal, NOT a security control (spec §9).**
> A non-conforming or malicious caller can ignore the warning and proceed using
> valid credentials. Do not rely on this webhook as the sole protection for any
> resource. Real security rests on not issuing production credentials to agents,
> least-privilege RBAC, network policy, separate read-only clusters for AI
> workloads, and admission/authorization controls that are actual gates.

## What it does

On every governed `CREATE` / `UPDATE` / `DELETE` / `CONNECT` operation by a
non-exempt identity, the webhook produces the Recuse sentinel line:

```
RECUSE/0.1 deny; reason=production; scope=all-automation; ref=https://example.com/ai-policy; id=<per-decision-uuid>
```

- **`mode=warn` (default):** the operation is **allowed** and the sentinel is
  attached as an admission **warning**. `kubectl` and client libraries render it
  as `Warning: RECUSE/0.1 ...`, so a conforming agent sees the signal and can
  recuse — while humans are unaffected. Non-blocking.
- **`mode=deny`:** the operation is **blocked** with HTTP `403` and the sentinel
  in `status.message`. The agent sees the signal in the error. Blocking.

A conforming agent detects the signal by matching `^RECUSE/\d+\.\d+ ` (spec §8)
and, on the `deny` directive, **MUST** recuse (spec §6.1).

## Architecture

```
   LLM agent / kubectl / client
            |  (create/update/delete/connect)
            v
   kube-apiserver  ──(AdmissionReview, admission.k8s.io/v1, TLS)──►  recuse-webhook
            ▲                                                              |
            └──────────  AdmissionResponse  ◄──────────────────────────────┘
                         • allowed=true + warnings:[RECUSE/0.1 ...]   (mode=warn)
                         • allowed=false + status 403 RECUSE/0.1 ...  (mode=deny)
```

The core is a **pure function** `decide(review, config) -> Decision`
(`decide.go`), unit-tested exhaustively in `decide_test.go`. The HTTP layer
(`main.go`) only decodes the request, calls `decide`, logs, and serializes the
response.

Per request the webhook:

1. Echoes `response.uid = request.uid` (REQUIRED by the admission API).
2. **Exemption check** — always allow, no signal — for system identities
   (`system:*` users, `kube-system`/`kube-node-lease`/`kube-public` service
   accounts, the adapter's own service account) and any admin-configured exempt
   users/groups.
3. If not exempt and the operation is governed, builds the sentinel and returns
   either a warning (`warn`) or a `403` (`deny`).
4. Logs one JSON line per governed decision to stdout (visible via
   `kubectl -n recuse-system logs`):
   ```json
   {"timestamp":"2026-06-05T12:00:00Z","id":"...","user":"bob@example.com","groups":["dev"],"operation":"CREATE","resource":"apps/v1/deployments","namespace":"team-a","name":"web","mode":"warn","action":"warned"}
   ```

## Safety — why this cannot deadlock or block a cluster

The design is a chain of independent safeguards. **Any one** of them is enough
to keep the cluster running; together they are belt-and-suspenders:

1. **`failurePolicy: Ignore` (FAIL-OPEN) — the single most important setting.**
   If the webhook is down, slow, unreachable, errors, or its TLS is broken, the
   apiserver **allows** the request anyway. A broken Recuse webhook can never
   block legitimate operations or wedge the control plane. (See the loud comment
   in `manifests/validatingwebhook.yaml`.)
2. **System namespaces excluded.** The `namespaceSelector` excludes
   `kube-system`, `kube-node-lease`, `kube-public`, and the adapter's own
   `recuse-system`, so the control plane and the webhook's own pods are never
   governed.
3. **Identity exemptions.** `decide()` always exempts `system:*` users, the
   system-namespace service accounts, and the adapter's own service account —
   the controllers/scheduler/kubelet that keep the cluster alive never see a
   signal.
4. **Default `mode=warn` (non-blocking).** Out of the box, nothing is blocked;
   only an advisory warning is attached.
5. **Small `timeoutSeconds: 5`.** Even a hung webhook is abandoned quickly, and
   (per #1) the request is then allowed.
6. **Instant kill switch:**
   ```sh
   kubectl delete validatingwebhookconfiguration recuse
   ```
   This removes all governance immediately, cluster-wide, with no restart.

Also: the webhook has **no RBAC permissions** (empty Role) and **cannot mutate**
anything — it is a validating webhook that only ever signals.

## Install

Prerequisites: a cluster you can `kubectl apply` to, `openssl`, and an image
pushed to `ghcr.io/mthamil107/recuse-webhook` (CI builds this on tags; or build
locally with the `Dockerfile`).

```sh
# 1. Generate the self-signed CA + serving cert and create the TLS Secret +
#    inject the caBundle. (APPLY=1 also creates the namespace, Secret, and
#    patches the webhook for you.)
./hack/gen-certs.sh                 # prints the caBundle; or:
APPLY=1 ./hack/gen-certs.sh         # create Secret + patch webhook automatically

# 2. Apply the manifests (namespace, RBAC, ConfigMap, Deployment, Service,
#    ValidatingWebhookConfiguration).
kubectl apply -k manifests/

# 3. If you ran gen-certs.sh WITHOUT APPLY=1, do steps 1+3 manually:
kubectl -n recuse-system create secret tls recuse-webhook-tls \
  --cert=hack/certs/server.crt --key=hack/certs/server.key
kubectl patch validatingwebhookconfiguration recuse --type='json' \
  -p='[{"op":"replace","path":"/webhooks/0/clientConfig/caBundle","value":"<caBundle from gen-certs.sh>"}]'
```

> **Order note:** the Secret must exist before the Deployment pods start (they
> mount it), and the `caBundle` must be patched before the apiserver trusts the
> webhook. `APPLY=1 ./hack/gen-certs.sh` followed by `kubectl apply -k manifests/`
> handles this cleanly (re-run gen-certs with APPLY=1 after apply if the webhook
> object didn't exist yet).

### cert-manager (optional alternative)

Instead of `hack/gen-certs.sh` you can let [cert-manager](https://cert-manager.io)
issue the serving cert into the `recuse-webhook-tls` Secret and auto-inject the
CA with the `cert-manager.io/inject-ca-from` annotation on the
`ValidatingWebhookConfiguration`. The manifests are compatible: point a
`Certificate` at `recuse-webhook.recuse-system.svc` and the same Secret name.

## Configure

Edit `manifests/configmap.yaml` (or set env on the Deployment), then
`kubectl -n recuse-system rollout restart deploy/recuse-webhook`.

| Variable | Default | Meaning |
|---|---|---|
| `RECUSE_REF` | `https://example.com/ai-policy` | Policy URL (sentinel `ref=`). |
| `RECUSE_REASON` | `production` | Why governed (`reason=`). |
| `RECUSE_SCOPE` | `all-automation` | Target (`scope=`). |
| `RECUSE_DIRECTIVE` | `deny` | Advisory directive in the sentinel (`deny`/`throttle`/`warn`). |
| `RECUSE_MODE` | `warn` | Operational behavior: `warn` (allow + warning) or `deny` (block 403). |
| `RECUSE_EXEMPT_USERS` | _(empty)_ | Extra always-allowed usernames (comma/space separated). |
| `RECUSE_EXEMPT_GROUPS` | `system:masters` | Extra always-allowed groups. |
| `RECUSE_OWN_SERVICE_ACCOUNT` | `system:serviceaccount:recuse-system:recuse-webhook` | The webhook's own SA (self-exempt). |

`RECUSE_DIRECTIVE` (what the sentinel *says*) and `RECUSE_MODE` (what the webhook
*does*) are independent. The default — `directive=deny`, `mode=warn` — emits a
`deny` sentinel as a non-blocking warning, so compliant agents recuse while
humans and the cluster are unaffected.

## Switch warn ↔ deny

```sh
# Go blocking (403 with the sentinel):
kubectl -n recuse-system set env deploy/recuse-webhook RECUSE_MODE=deny
# Back to non-blocking warnings:
kubectl -n recuse-system set env deploy/recuse-webhook RECUSE_MODE=warn
```

Do this deliberately: `deny` blocks real operations for every non-exempt
identity in governed namespaces. Validate your exemptions first.

## Test it

As a **non-exempt** user, create something in a governed (non-system) namespace:

```sh
kubectl create namespace team-a
kubectl -n team-a run web --image=nginx
# In warn mode you'll see, before the resource is created:
#   Warning: RECUSE/0.1 deny; reason=production; scope=all-automation; ref=...; id=...
#   Warning: This is a production system. Automated/LLM-agent access is prohibited; ...
```

Try `kubectl -n team-a exec` / `port-forward` against a pod — the `CONNECT`
rule catches those too. In `deny` mode the same operations fail with a `403`
whose message is the sentinel. Watch decisions:

```sh
kubectl -n recuse-system logs deploy/recuse-webhook -f
```

A `system:` user or a `kube-system` service account doing the same thing gets
**no** warning and is never blocked.

## Unit tests (no cluster required)

The admission logic is validated entirely by Go unit tests against the pure
`decide()` function:

```sh
go vet ./...
go test ./...
```

Covered: exempt identity → allowed, no warning; governed + `warn` → allowed
with a warning whose first element starts `RECUSE/0.1`; governed + `deny` →
`allowed=false` with the sentinel in `status.message` (code 403); UID echoed; a
`system:` user is exempt; a `kube-system` service account is exempt;
`pods/exec` CONNECT is governed; non-system-namespace service accounts are
governed; nil request fails open.

## Reads are NOT covered (honest limitation)

Admission webhooks only see **mutating** operations: `CREATE`, `UPDATE`,
`DELETE`, and `CONNECT`. They **do not** see `get` / `list` / `watch`. So an
agent that only **reads** (e.g. `kubectl get secrets`) is **not** signalled by
this adapter.

Full read coverage requires an **authorization webhook**
(`--authorization-webhook-config-file`), which sees every request including
reads. That requires apiserver flags you control on **k3s / kubeadm /
self-managed** clusters but **cannot** be set on managed control planes like
**EKS / GKE / AKS**. A Recuse authorization-webhook binding is **future work**;
this validating-webhook adapter is the broadly-deployable first step.

## EKS vs k3s vs kubeadm

- **EKS / GKE / AKS (managed):** this validating webhook works as-is (you don't
  need control-plane flags to register a webhook). The apiserver reaches the
  webhook Service over the cluster network. Reads remain uncovered (no
  authorization-webhook access on managed control planes).
- **kubeadm (self-managed):** works as-is; you additionally *could* add an
  authorization webhook later for read coverage.
- **k3s:** works as-is; lightweight and a good place to demo. Authorization
  webhook is also available here for future read coverage.

## Remove safely

Always delete the `ValidatingWebhookConfiguration` **first** — this is the kill
switch and stops all governance immediately:

```sh
kubectl delete validatingwebhookconfiguration recuse
kubectl delete -k manifests/        # remove Deployment/Service/etc.
kubectl delete secret recuse-webhook-tls -n recuse-system
kubectl delete namespace recuse-system
```

(Because of `failurePolicy: Ignore`, even if you deleted the Deployment first
the cluster would keep working — the webhook would just fail open.)

## Build

```sh
# Native build for development
go build -o recuse-webhook .

# Static linux/amd64 release binary (distroless/scratch-compatible)
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" \
  -o bin/recuse-webhook-linux-amd64 .

# Container image (multi-stage, distroless nonroot)
docker build -t ghcr.io/mthamil107/recuse-webhook:v0.1.0 .
```

CI (`.github/workflows/build-webhook-image.yml`) runs `go vet` + `go test`, then
builds and pushes the image to `ghcr.io/mthamil107/recuse-webhook` on tags and
manual dispatch using `GITHUB_TOKEN`.

See the spec: [`../../spec/recuse-signal-v0.1.md`](../../spec/recuse-signal-v0.1.md).
