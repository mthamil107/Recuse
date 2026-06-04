#!/usr/bin/env bash
#
# Recuse SSH adapter — installer (Ubuntu 22.04 LTS, OpenSSH + Linux-PAM)
#
# Idempotent. Safe to re-run. Conforms to the Recuse Signal v0.1
# (see ../../spec/recuse-signal-v0.1.md, §7.1 SSH binding).
#
# This installs the COOPERATIVE signaling layer only. It is NOT a security
# control (spec §9). It announces a policy to well-behaved agents; it does
# not enforce anything against a malicious or non-conforming client.
#
# Expects these files to sit next to it:
#   banner.txt            -> /etc/recuse/banner.txt   (static pre-auth banner)
#   recuse-pam-hook.sh    -> /usr/local/bin/recuse-pam-hook.sh (per-session)
#   sshd_config.snippet   (appended to /etc/ssh/sshd_config)
#   pam-sshd.snippet      (appended to /etc/pam.d/sshd)
#
set -euo pipefail

# --- locate ourselves so we can find the sibling files -----------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BANNER_SRC="${SCRIPT_DIR}/banner.txt"
HOOK_SRC="${SCRIPT_DIR}/recuse-pam-hook.sh"
SSHD_SNIPPET_SRC="${SCRIPT_DIR}/sshd_config.snippet"
PAM_SNIPPET_SRC="${SCRIPT_DIR}/pam-sshd.snippet"

BANNER_DST="/etc/recuse/banner.txt"
HOOK_DST="/usr/local/bin/recuse-pam-hook.sh"
SSHD_CONFIG="/etc/ssh/sshd_config"
PAM_SSHD="/etc/pam.d/sshd"
LOG_DIR="/var/log/recuse"
LOG_FILE="${LOG_DIR}/ssh.json"

# Marker fencing so we can find/remove our additions later, idempotently.
MARK_BEGIN="# >>> recuse-ssh adapter (managed) >>>"
MARK_END="# <<< recuse-ssh adapter (managed) <<<"

# --- preconditions -----------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
  echo "recuse-install: must run as root (try: sudo $0)" >&2
  exit 1
fi

for f in "${BANNER_SRC}" "${HOOK_SRC}" "${SSHD_SNIPPET_SRC}" "${PAM_SNIPPET_SRC}"; do
  if [[ ! -f "${f}" ]]; then
    echo "recuse-install: missing required file: ${f}" >&2
    exit 1
  fi
done

echo "recuse-install: starting (idempotent)..."

# --- 1. static pre-auth banner ----------------------------------------------
install -d -m 755 /etc/recuse
install -m 644 "${BANNER_SRC}" "${BANNER_DST}"
echo "recuse-install: banner -> ${BANNER_DST}"

# --- 2. per-session PAM hook -------------------------------------------------
install -m 755 "${HOOK_SRC}" "${HOOK_DST}"
echo "recuse-install: pam hook -> ${HOOK_DST} (0755)"

# --- 3. append-only JSON connection log directory ----------------------------
install -d -m 700 "${LOG_DIR}"
if [[ ! -e "${LOG_FILE}" ]]; then
  : > "${LOG_FILE}"
fi
chmod 600 "${LOG_FILE}"
echo "recuse-install: log dir -> ${LOG_DIR} (0700), log -> ${LOG_FILE}"

# --- helper: append a fenced snippet to a config file if not already present -
append_managed_block() {
  local target="$1" snippet="$2"
  if grep -qF "${MARK_BEGIN}" "${target}" 2>/dev/null; then
    echo "recuse-install: managed block already present in ${target} (skip)"
    return 0
  fi
  {
    printf '\n%s\n' "${MARK_BEGIN}"
    cat "${snippet}"
    printf '%s\n' "${MARK_END}"
  } >> "${target}"
  echo "recuse-install: appended managed block to ${target}"
}

# --- 4. sshd_config: Banner + post-auth PAM relay ----------------------------
cp -a "${SSHD_CONFIG}" "${SSHD_CONFIG}.recuse.bak.$(date +%s)" 2>/dev/null || true
append_managed_block "${SSHD_CONFIG}" "${SSHD_SNIPPET_SRC}"

# --- 5. pam.d/sshd: per-session hook -----------------------------------------
cp -a "${PAM_SSHD}" "${PAM_SSHD}.recuse.bak.$(date +%s)" 2>/dev/null || true
append_managed_block "${PAM_SSHD}" "${PAM_SNIPPET_SRC}"

# --- 6. validate sshd config BEFORE reloading --------------------------------
echo "recuse-install: validating sshd config (sshd -t)..."
if ! sshd -t; then
  echo "recuse-install: ERROR — sshd -t failed; not reloading. Review ${SSHD_CONFIG}." >&2
  exit 1
fi

# --- 7. reload ssh -----------------------------------------------------------
if command -v systemctl >/dev/null 2>&1; then
  # Ubuntu 22.04 uses the 'ssh' unit (ssh.service).
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || systemctl restart ssh
else
  service ssh reload || service ssh restart
fi
echo "recuse-install: ssh reloaded."

echo "recuse-install: done. Test with:  ssh user@<host>   (banner shows pre-auth)"
echo "recuse-install: connection log:   ${LOG_FILE}"
