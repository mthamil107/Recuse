#!/usr/bin/env bash
#
# gen-certs.sh — generate a self-signed CA + server certificate for the Recuse
# admission webhook, create the TLS Secret, and inject the CA bundle into the
# ValidatingWebhookConfiguration.
#
# Admission webhooks REQUIRE TLS, and the apiserver must trust the serving cert
# via the webhook's `caBundle`. This script wires that up with no cert-manager
# dependency. (cert-manager is a fine alternative — see the README.)
#
# Usage:
#   ./hack/gen-certs.sh                 # generate certs + print caBundle
#   APPLY=1 ./hack/gen-certs.sh         # also create the Secret + patch the webhook
#
# Env:
#   NAMESPACE   (default: recuse-system)
#   SERVICE     (default: recuse-webhook)
#   SECRET      (default: recuse-webhook-tls)
#   WEBHOOK     (default: recuse)            ValidatingWebhookConfiguration name
#   OUTDIR      (default: ./hack/certs)
#   DAYS        (default: 3650)
#   APPLY       (default: unset)             when set, runs kubectl create/patch
#
# Requires: openssl. APPLY also requires kubectl with a working kubeconfig.
set -euo pipefail

NAMESPACE="${NAMESPACE:-recuse-system}"
SERVICE="${SERVICE:-recuse-webhook}"
SECRET="${SECRET:-recuse-webhook-tls}"
WEBHOOK="${WEBHOOK:-recuse}"
OUTDIR="${OUTDIR:-./hack/certs}"
DAYS="${DAYS:-3650}"

DNS1="${SERVICE}"
DNS2="${SERVICE}.${NAMESPACE}"
DNS3="${SERVICE}.${NAMESPACE}.svc"
DNS4="${SERVICE}.${NAMESPACE}.svc.cluster.local"

mkdir -p "${OUTDIR}"
cd "${OUTDIR}"

echo ">> Generating self-signed CA ..."
openssl genrsa -out ca.key 2048 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days "${DAYS}" \
  -subj "/CN=recuse-webhook-ca" -out ca.crt 2>/dev/null

echo ">> Generating server key + CSR (SANs: ${DNS3}) ..."
openssl genrsa -out server.key 2048 2>/dev/null

cat >csr.conf <<EOF
[req]
req_extensions = v3_req
distinguished_name = dn
prompt = no
[dn]
CN = ${DNS3}
[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names
[alt_names]
DNS.1 = ${DNS1}
DNS.2 = ${DNS2}
DNS.3 = ${DNS3}
DNS.4 = ${DNS4}
EOF

openssl req -new -key server.key -subj "/CN=${DNS3}" -out server.csr -config csr.conf 2>/dev/null

echo ">> Signing server cert with the CA ..."
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days "${DAYS}" -sha256 -extensions v3_req -extfile csr.conf 2>/dev/null

# Base64 of the CA cert, single line (no wrapping) — this is the caBundle the
# apiserver uses to trust the webhook's serving cert.
if base64 --help 2>&1 | grep -q -- '-w'; then
  CA_BUNDLE="$(base64 -w0 ca.crt)"   # GNU coreutils
else
  CA_BUNDLE="$(base64 ca.crt | tr -d '\n')"  # BSD/macOS
fi

echo
echo "================================================================"
echo "caBundle (base64 of the CA cert):"
echo
echo "${CA_BUNDLE}"
echo
echo "================================================================"
echo "Files written to: ${OUTDIR}"
echo "  ca.crt / ca.key       — self-signed CA"
echo "  server.crt / server.key — webhook serving cert (mounted via Secret)"
echo

if [[ -n "${APPLY:-}" ]]; then
  echo ">> APPLY set: creating/replacing Secret ${NAMESPACE}/${SECRET} ..."
  kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "${NAMESPACE}" create secret tls "${SECRET}" \
    --cert=server.crt --key=server.key \
    --dry-run=client -o yaml | kubectl apply -f -

  echo ">> Patching caBundle into ValidatingWebhookConfiguration/${WEBHOOK} ..."
  # Patch every webhook entry's caBundle (there is one in this config).
  kubectl patch validatingwebhookconfiguration "${WEBHOOK}" \
    --type='json' \
    -p="[{\"op\":\"replace\",\"path\":\"/webhooks/0/clientConfig/caBundle\",\"value\":\"${CA_BUNDLE}\"}]"

  echo ">> Done. The webhook is now wired with a trusted CA."
else
  echo "Next steps (manual):"
  echo "  1. Create the Secret:"
  echo "       kubectl -n ${NAMESPACE} create secret tls ${SECRET} \\"
  echo "         --cert=${OUTDIR}/server.crt --key=${OUTDIR}/server.key"
  echo "  2. Apply the manifests:"
  echo "       kubectl apply -k manifests/"
  echo "  3. Inject the caBundle above into the webhook:"
  echo "       kubectl patch validatingwebhookconfiguration ${WEBHOOK} --type='json' \\"
  echo "         -p='[{\"op\":\"replace\",\"path\":\"/webhooks/0/clientConfig/caBundle\",\"value\":\"<caBundle above>\"}]'"
  echo
  echo "  (Or re-run with APPLY=1 to do steps 1 and 3 automatically.)"
fi
