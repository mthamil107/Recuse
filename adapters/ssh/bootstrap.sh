#!/usr/bin/env bash
#
# Recuse SSH adapter — one-line web installer (bootstrap)
#
# Intended to be piped from the web:
#
#   curl -fsSL https://raw.githubusercontent.com/mthamil107/Recuse/<ref>/adapters/ssh/bootstrap.sh \
#     | sudo bash -s -- --ref=https://yourco/ai-policy [--throttle] [--allow-ip=1.2.3.4]
#
# It downloads the SSH adapter files from a pinned git ref into a temp dir and
# then runs install.sh with whatever flags you passed through.
#
# This installs the COOPERATIVE Recuse signal (banner + JSON audit log). It is
# NOT a security control (see the spec §9). The optional --throttle adds a
# bounded, delay-only behavioral throttle that is OFF by default and can never
# lock anyone out.
#
set -euo pipefail

REPO="mthamil107/Recuse"
ADAPTER_PATH="adapters/ssh"
RAW_BASE="https://raw.githubusercontent.com"

# Default pinned ref: env RECUSE_VERSION, else v0.1.0, with a fallback to main
# if the tagged ref is not found (404).
DEFAULT_REF="${RECUSE_VERSION:-v0.1.0}"
FALLBACK_REF="main"

# Files that make up the adapter (downloaded into the temp dir).
FILES="
banner.txt.template
recuse.conf.example
recuse-pam-hook.sh
sshd_config.snippet
pam-sshd.snippet
install.sh
uninstall.sh
"

log()  { echo "recuse-bootstrap: $*"; }
err()  { echo "recuse-bootstrap: $*" >&2; }

# --- root check --------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
  err "must run as root. Pipe into 'sudo bash', e.g.:"
  err "  curl -fsSL <url>/bootstrap.sh | sudo bash -s -- --ref=https://yourco/ai-policy"
  exit 1
fi

# --- OS sanity (warn, do not block) ------------------------------------------
if [ -r /etc/os-release ]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  case "${ID:-}:${ID_LIKE:-}" in
    *debian*|*ubuntu*) : ;;
    *)
      err "WARNING — this does not look like a Debian/Ubuntu system (ID='${ID:-?}')."
      err "          The adapter targets OpenSSH + Linux-PAM on Debian/Ubuntu; continuing anyway."
      ;;
  esac
else
  err "WARNING — /etc/os-release not found; cannot confirm OS. Continuing anyway."
fi

# --- need a downloader -------------------------------------------------------
DL=""
if command -v curl >/dev/null 2>&1; then
  DL="curl"
elif command -v wget >/dev/null 2>&1; then
  DL="wget"
else
  err "ERROR — need curl or wget to download the adapter files."
  exit 1
fi

# fetch <url> <dest> ; returns 0 on success (HTTP 200), non-zero otherwise.
fetch() {
  url="$1"; dest="$2"
  if [ "${DL}" = "curl" ]; then
    curl -fsSL "${url}" -o "${dest}"
  else
    wget -q -O "${dest}" "${url}"
  fi
}

# Probe whether a ref exists by checking install.sh under it.
ref_exists() {
  ref="$1"
  url="${RAW_BASE}/${REPO}/${ref}/${ADAPTER_PATH}/install.sh"
  if [ "${DL}" = "curl" ]; then
    curl -fsSL -o /dev/null "${url}" >/dev/null 2>&1
  else
    wget -q -O /dev/null "${url}" >/dev/null 2>&1
  fi
}

# --- pick the ref ------------------------------------------------------------
REF="${DEFAULT_REF}"
if ref_exists "${REF}"; then
  log "using pinned ref: ${REF}"
else
  err "pinned ref '${REF}' not found (404); falling back to '${FALLBACK_REF}'."
  REF="${FALLBACK_REF}"
  if ! ref_exists "${REF}"; then
    err "ERROR — fallback ref '${FALLBACK_REF}' also not reachable. Aborting."
    exit 1
  fi
  log "using fallback ref: ${REF}"
fi

# --- temp dir + cleanup ------------------------------------------------------
TMPDIR_RECUSE="$(mktemp -d 2>/dev/null || mktemp -d -t recuse)"
cleanup() { rm -rf "${TMPDIR_RECUSE}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

log "downloading adapter files from ${REPO}@${REF} ..."
for f in ${FILES}; do
  [ -n "${f}" ] || continue
  url="${RAW_BASE}/${REPO}/${REF}/${ADAPTER_PATH}/${f}"
  dest="${TMPDIR_RECUSE}/${f}"
  if ! fetch "${url}" "${dest}"; then
    err "ERROR — failed to download ${f} from ${url}"
    exit 1
  fi
  log "  fetched ${f}"
done

chmod +x "${TMPDIR_RECUSE}/install.sh" "${TMPDIR_RECUSE}/uninstall.sh" \
         "${TMPDIR_RECUSE}/recuse-pam-hook.sh" 2>/dev/null || true

# --- run the installer with all pass-through flags ---------------------------
log "running install.sh ..."
# Pass every bootstrap argument straight through to install.sh.
bash "${TMPDIR_RECUSE}/install.sh" "$@"

# --- next steps --------------------------------------------------------------
cat <<'EOF'

recuse-bootstrap: install complete.

Next steps
----------
1. Verify the pre-auth banner (from another machine):
     ssh user@<this-host>
   You should see the RECUSE/0.1 sentinel line BEFORE the password/key prompt.

2. Watch the audit log (one JSON object per session):
     sudo tail -f /var/log/recuse/ssh.json

3. Set your real policy URL if you have not yet:
     sudoedit /etc/recuse/recuse.conf      # set RECUSE_REF="https://yourco/ai-policy"
   then re-run the installer to regenerate the banner.

4. (Optional) enable the behavioral throttle — delay-only, never blocks,
   allow-listed IPs exempt, hard-capped at 10s:
     re-run with --throttle --allow-ip=<your-admin-ip>
   or set RECUSE_THROTTLE_ENABLED="true" in /etc/recuse/recuse.conf and re-run.

5. Uninstall later:
     sudo /usr/local/bin/recuse-uninstall 2>/dev/null \
       || (download uninstall.sh from the repo and run: sudo bash uninstall.sh)

Remember: this is a COOPERATIVE signal, not a security control. Do not rely on
it to keep anyone out. See the spec for the honest caveats.
EOF
