#!/usr/bin/env bash
#
# Recuse SSH adapter — uninstaller (Debian/Ubuntu, OpenSSH + Linux-PAM)
#
# Cleanly reverses install.sh. Idempotent / safe to re-run.
# Removes the managed config blocks (fenced by markers), the generated banner,
# the rendered config, and the PAM hook, then reloads ssh.
#
# The connection log (/var/log/recuse/ssh.json) is INTENTIONALLY LEFT IN PLACE
# as an audit artifact — see note at the end.
#
set -euo pipefail

CONF_DIR="/etc/recuse"
CONF_DST="${CONF_DIR}/recuse.conf"
BANNER_DST="${CONF_DIR}/banner.txt"
HOOK_DST="/usr/local/bin/recuse-pam-hook.sh"
SSHD_CONFIG="/etc/ssh/sshd_config"
PAM_SSHD="/etc/pam.d/sshd"
LOG_FILE="/var/log/recuse/ssh.json"

MARK_BEGIN="# >>> recuse-ssh adapter (managed) >>>"
MARK_END="# <<< recuse-ssh adapter (managed) <<<"

if [[ "${EUID}" -ne 0 ]]; then
  echo "recuse-uninstall: must run as root (try: sudo $0)" >&2
  exit 1
fi

echo "recuse-uninstall: starting..."

# --- remove the fenced managed block from a config file ----------------------
remove_managed_block() {
  local target="$1"
  if [[ ! -f "${target}" ]]; then
    echo "recuse-uninstall: ${target} not found (skip)"
    return 0
  fi
  if ! grep -qF "${MARK_BEGIN}" "${target}"; then
    echo "recuse-uninstall: no managed block in ${target} (skip)"
    return 0
  fi
  cp -a "${target}" "${target}.recuse.bak.$(date +%s)"
  # Delete everything between (and including) the begin/end markers.
  # The leading blank line that install.sh inserted before MARK_BEGIN is
  # harmless; we leave file otherwise untouched.
  sed -i "\|${MARK_BEGIN}|,\|${MARK_END}|d" "${target}"
  echo "recuse-uninstall: removed managed block from ${target}"
}

remove_managed_block "${SSHD_CONFIG}"
remove_managed_block "${PAM_SSHD}"

# --- remove the generated banner ---------------------------------------------
if [[ -f "${BANNER_DST}" ]]; then
  rm -f "${BANNER_DST}"
  echo "recuse-uninstall: removed ${BANNER_DST}"
fi

# --- remove the rendered config ----------------------------------------------
if [[ -f "${CONF_DST}" ]]; then
  rm -f "${CONF_DST}"
  echo "recuse-uninstall: removed ${CONF_DST}"
fi

# Drop /etc/recuse only if now empty (it never holds the log).
rmdir --ignore-fail-on-non-empty "${CONF_DIR}" 2>/dev/null || true

# --- remove the PAM hook -----------------------------------------------------
if [[ -f "${HOOK_DST}" ]]; then
  rm -f "${HOOK_DST}"
  echo "recuse-uninstall: removed ${HOOK_DST}"
fi

# --- validate + reload ssh ---------------------------------------------------
echo "recuse-uninstall: validating sshd config (sshd -t)..."
if ! sshd -t; then
  echo "recuse-uninstall: ERROR — sshd -t failed after edits; review ${SSHD_CONFIG}." >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || systemctl restart ssh
else
  service ssh reload || service ssh restart
fi
echo "recuse-uninstall: ssh reloaded."

# --- log is preserved --------------------------------------------------------
if [[ -f "${LOG_FILE}" ]]; then
  echo "recuse-uninstall: NOTE — connection log left in place: ${LOG_FILE}"
  echo "recuse-uninstall:        remove it manually if you no longer need the audit trail."
fi

# --- self-cleanup: remove the installed `recuse-uninstall` command ------------
# (Safe even while running: the inode persists until this process exits.)
rm -f /usr/local/bin/recuse-uninstall 2>/dev/null || true

echo "recuse-uninstall: done."
