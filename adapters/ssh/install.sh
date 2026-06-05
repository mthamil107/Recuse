#!/usr/bin/env bash
#
# Recuse SSH adapter — installer (Debian/Ubuntu, OpenSSH + Linux-PAM)
#
# Config-driven, idempotent, safe to re-run. Conforms to the Recuse Signal v0.1
# (see ../../spec/recuse-signal-v0.1.md, §7.1 SSH binding).
#
# This installs the COOPERATIVE signaling layer only. It is NOT a security
# control (spec §9). It announces a policy to well-behaved agents; it does
# not enforce anything against a malicious or non-conforming client. The
# optional behavioral throttle only DELAYS (never blocks) and is OFF by default.
#
# Expects these files to sit next to it:
#   banner.txt.template   -> rendered to /etc/recuse/banner.txt using the config
#   recuse.conf.example   -> seeds /etc/recuse/recuse.conf on first install
#   recuse-pam-hook.sh    -> /usr/local/bin/recuse-pam-hook.sh (per-session)
#   sshd_config.snippet   (appended to /etc/ssh/sshd_config)
#   pam-sshd.snippet      (appended to /etc/pam.d/sshd)
#
set -euo pipefail

# --- locate ourselves so we can find the sibling files -----------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TEMPLATE_SRC="${SCRIPT_DIR}/banner.txt.template"
CONF_EXAMPLE_SRC="${SCRIPT_DIR}/recuse.conf.example"
HOOK_SRC="${SCRIPT_DIR}/recuse-pam-hook.sh"
SSHD_SNIPPET_SRC="${SCRIPT_DIR}/sshd_config.snippet"
PAM_SNIPPET_SRC="${SCRIPT_DIR}/pam-sshd.snippet"

CONF_DIR="/etc/recuse"
CONF_DST="${CONF_DIR}/recuse.conf"
BANNER_DST="${CONF_DIR}/banner.txt"
HOOK_DST="/usr/local/bin/recuse-pam-hook.sh"
SSHD_CONFIG="/etc/ssh/sshd_config"
PAM_SSHD="/etc/pam.d/sshd"
LOG_DIR="/var/log/recuse"
LOG_FILE="${LOG_DIR}/ssh.json"

EXAMPLE_REF_DEFAULT="https://example.com/ai-policy"

# Marker fencing so we can find/remove our additions later, idempotently.
MARK_BEGIN="# >>> recuse-ssh adapter (managed) >>>"
MARK_END="# <<< recuse-ssh adapter (managed) <<<"

# --- defaults (used only if neither conf nor flags supply a value) -----------
RECUSE_REF="${EXAMPLE_REF_DEFAULT}"
RECUSE_REASON="production"
RECUSE_SCOPE="all-automation"
RECUSE_DIRECTIVE="deny"
RECUSE_THROTTLE_ENABLED="false"
RECUSE_THROTTLE_MAX_CONN="5"
RECUSE_THROTTLE_WINDOW_SECONDS="10"
RECUSE_THROTTLE_DELAY_SECONDS="2"
RECUSE_THROTTLE_ALLOW_IPS=""

usage() {
  cat <<'EOF'
Recuse SSH adapter installer

Usage: sudo ./install.sh [flags]

Flags:
  --ref=URL            Absolute URL to YOUR AI-access policy (strongly recommended).
  --reason=VALUE       production|pii|compliance|change-freeze|unowned|other
  --scope=VALUE        all-automation|llm-agents|unattended
  --directive=VALUE    deny|throttle|warn
  --throttle           Enable the OPT-IN behavioral throttle (delay-only, never blocks).
  --allow-ip=IP        IP never throttled (repeatable; put your admin IP here).
  --help               Show this help and exit.

Config precedence:
  /etc/recuse/recuse.conf (if present) is loaded first; CLI flags override it;
  the effective values are persisted back to the conf file. On a fresh install
  the conf is seeded from recuse.conf.example.

The cooperative signal (banner + JSON log) works WITHOUT the throttle. The
throttle, when enabled, only adds a bounded delay (hard-capped at 10s) to
rapid repeat connections and NEVER denies a login. Allow-listed IPs are exempt.
EOF
}

# --- collect CLI overrides (empty = "not set on CLI") ------------------------
CLI_REF=""
CLI_REASON=""
CLI_SCOPE=""
CLI_DIRECTIVE=""
CLI_THROTTLE=""
CLI_ALLOW_IPS=""

for arg in "$@"; do
  case "${arg}" in
    --ref=*)        CLI_REF="${arg#*=}" ;;
    --reason=*)     CLI_REASON="${arg#*=}" ;;
    --scope=*)      CLI_SCOPE="${arg#*=}" ;;
    --directive=*)  CLI_DIRECTIVE="${arg#*=}" ;;
    --throttle)     CLI_THROTTLE="true" ;;
    --allow-ip=*)
      ip="${arg#*=}"
      if [ -n "${ip}" ]; then
        if [ -n "${CLI_ALLOW_IPS}" ]; then
          CLI_ALLOW_IPS="${CLI_ALLOW_IPS} ${ip}"
        else
          CLI_ALLOW_IPS="${ip}"
        fi
      fi
      ;;
    --help|-h)      usage; exit 0 ;;
    *)
      echo "recuse-install: unknown flag: ${arg}" >&2
      echo "recuse-install: try --help" >&2
      exit 1
      ;;
  esac
done

# --- preconditions -----------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
  echo "recuse-install: must run as root (try: sudo $0)" >&2
  exit 1
fi

for f in "${TEMPLATE_SRC}" "${CONF_EXAMPLE_SRC}" "${HOOK_SRC}" "${SSHD_SNIPPET_SRC}" "${PAM_SNIPPET_SRC}"; do
  if [[ ! -f "${f}" ]]; then
    echo "recuse-install: missing required file: ${f}" >&2
    exit 1
  fi
done

echo "recuse-install: starting (idempotent)..."

install -d -m 755 "${CONF_DIR}"

# --- 1. config: load existing, else seed from example ------------------------
if [[ -f "${CONF_DST}" ]]; then
  echo "recuse-install: loading existing config ${CONF_DST}"
  # shellcheck source=/dev/null
  . "${CONF_DST}"
else
  echo "recuse-install: no config yet; seeding from recuse.conf.example"
  install -m 644 "${CONF_EXAMPLE_SRC}" "${CONF_DST}"
  # shellcheck source=/dev/null
  . "${CONF_DST}"
fi

# Re-assert defaults for any key the conf did not define (set -u safety).
RECUSE_REF="${RECUSE_REF:-${EXAMPLE_REF_DEFAULT}}"
RECUSE_REASON="${RECUSE_REASON:-production}"
RECUSE_SCOPE="${RECUSE_SCOPE:-all-automation}"
RECUSE_DIRECTIVE="${RECUSE_DIRECTIVE:-deny}"
RECUSE_THROTTLE_ENABLED="${RECUSE_THROTTLE_ENABLED:-false}"
RECUSE_THROTTLE_MAX_CONN="${RECUSE_THROTTLE_MAX_CONN:-5}"
RECUSE_THROTTLE_WINDOW_SECONDS="${RECUSE_THROTTLE_WINDOW_SECONDS:-10}"
RECUSE_THROTTLE_DELAY_SECONDS="${RECUSE_THROTTLE_DELAY_SECONDS:-2}"
RECUSE_THROTTLE_ALLOW_IPS="${RECUSE_THROTTLE_ALLOW_IPS:-}"

# --- 2. apply CLI overrides --------------------------------------------------
[ -n "${CLI_REF}" ]       && RECUSE_REF="${CLI_REF}"
[ -n "${CLI_REASON}" ]    && RECUSE_REASON="${CLI_REASON}"
[ -n "${CLI_SCOPE}" ]     && RECUSE_SCOPE="${CLI_SCOPE}"
[ -n "${CLI_DIRECTIVE}" ] && RECUSE_DIRECTIVE="${CLI_DIRECTIVE}"
[ -n "${CLI_THROTTLE}" ]  && RECUSE_THROTTLE_ENABLED="true"
if [ -n "${CLI_ALLOW_IPS}" ]; then
  if [ -n "${RECUSE_THROTTLE_ALLOW_IPS}" ]; then
    RECUSE_THROTTLE_ALLOW_IPS="${RECUSE_THROTTLE_ALLOW_IPS} ${CLI_ALLOW_IPS}"
  else
    RECUSE_THROTTLE_ALLOW_IPS="${CLI_ALLOW_IPS}"
  fi
fi
# De-duplicate the allow-list (order-preserving) so repeated re-runs with
# --allow-ip do not accumulate the same IP over and over.
if [ -n "${RECUSE_THROTTLE_ALLOW_IPS}" ]; then
  _dedup=""
  for _ip in ${RECUSE_THROTTLE_ALLOW_IPS}; do
    _seen=""
    for _k in ${_dedup}; do
      [ "${_k}" = "${_ip}" ] && { _seen="y"; break; }
    done
    [ -n "${_seen}" ] && continue
    if [ -n "${_dedup}" ]; then _dedup="${_dedup} ${_ip}"; else _dedup="${_ip}"; fi
  done
  RECUSE_THROTTLE_ALLOW_IPS="${_dedup}"
  unset _dedup _ip _seen _k
fi

# --- 3. validate values (warn, do not hard-fail on enumerations) -------------
case "${RECUSE_DIRECTIVE}" in
  deny|throttle|warn) ;;
  *) echo "recuse-install: WARNING — directive '${RECUSE_DIRECTIVE}' is not deny|throttle|warn; using anyway." >&2 ;;
esac
case "${RECUSE_REASON}" in
  production|pii|compliance|change-freeze|unowned|other) ;;
  *) echo "recuse-install: WARNING — reason '${RECUSE_REASON}' is not a known token; using anyway." >&2 ;;
esac
case "${RECUSE_SCOPE}" in
  all-automation|llm-agents|unattended) ;;
  *) echo "recuse-install: WARNING — scope '${RECUSE_SCOPE}' is not a known token; using anyway." >&2 ;;
esac

# REF must look like an absolute URL; warn but proceed otherwise.
case "${RECUSE_REF}" in
  http://*|https://*) ;;
  *) echo "recuse-install: WARNING — RECUSE_REF '${RECUSE_REF}' does not look like an http(s) URL." >&2 ;;
esac

# A value containing newlines or '@' would corrupt the banner render; reject the
# pathological cases early (these would never be legitimate field values).
for v in "${RECUSE_DIRECTIVE}" "${RECUSE_REASON}" "${RECUSE_SCOPE}" "${RECUSE_REF}"; do
  case "${v}" in
    *@*) echo "recuse-install: ERROR — config value contains '@' which collides with template placeholders: ${v}" >&2; exit 1 ;;
  esac
done

# Loud warning if still broadcasting the example.com default.
if [ "${RECUSE_REF}" = "${EXAMPLE_REF_DEFAULT}" ]; then
  echo "" >&2
  echo "============================================================================" >&2
  echo "  recuse-install: WARNING — RECUSE_REF is still the example.com DEFAULT." >&2
  echo "  Your banner will point agents at ${EXAMPLE_REF_DEFAULT}," >&2
  echo "  a domain you do not control. This is almost certainly WRONG." >&2
  echo "" >&2
  echo "  Fix it by re-running with:   --ref=https://YOURDOMAIN/your-ai-policy" >&2
  echo "  or edit ${CONF_DST} and re-run install.sh." >&2
  echo "  Proceeding anyway so you can test, but DO set your real policy URL." >&2
  echo "============================================================================" >&2
  echo "" >&2
fi

# --- 4. persist effective config back to /etc/recuse/recuse.conf -------------
# Rewrite atomically, preserving the explanatory comments from the example file
# but with the effective KEY="VALUE" lines substituted in.
persist_conf() {
  local tmp
  tmp="$(mktemp "${CONF_DIR}/.recuse.conf.XXXXXX")"
  # Start from the example (comments + structure), then overwrite each known key.
  # Use awk for line replacement so values containing sed-special characters
  # (& | / etc.) are handled literally. Each KEY's line is replaced in place;
  # any KEY not already present is appended.
  set_kv() {
    local key="$1" val="$2" out
    out="$(awk -v key="${key}" -v val="${val}" '
      BEGIN { done = 0 }
      # Match a leading "KEY=" assignment (ignoring leading whitespace).
      {
        line = $0
        s = line
        sub(/^[ \t]+/, "", s)
        if (done == 0 && index(s, key "=") == 1) {
          # Replace the whole line with KEY="val" (val printed literally).
          printf "%s=\"%s\"\n", key, val
          done = 1
          next
        }
        print line
      }
      END { if (done == 0) printf "%s=\"%s\"\n", key, val }
    ' "${tmp}")"
    printf '%s\n' "${out}" > "${tmp}"
  }
  cp "${CONF_EXAMPLE_SRC}" "${tmp}"
  set_kv RECUSE_REF                     "${RECUSE_REF}"
  set_kv RECUSE_REASON                  "${RECUSE_REASON}"
  set_kv RECUSE_SCOPE                   "${RECUSE_SCOPE}"
  set_kv RECUSE_DIRECTIVE               "${RECUSE_DIRECTIVE}"
  set_kv RECUSE_THROTTLE_ENABLED        "${RECUSE_THROTTLE_ENABLED}"
  set_kv RECUSE_THROTTLE_MAX_CONN       "${RECUSE_THROTTLE_MAX_CONN}"
  set_kv RECUSE_THROTTLE_WINDOW_SECONDS "${RECUSE_THROTTLE_WINDOW_SECONDS}"
  set_kv RECUSE_THROTTLE_DELAY_SECONDS  "${RECUSE_THROTTLE_DELAY_SECONDS}"
  set_kv RECUSE_THROTTLE_ALLOW_IPS      "${RECUSE_THROTTLE_ALLOW_IPS}"
  chmod 644 "${tmp}"
  mv "${tmp}" "${CONF_DST}"
}
persist_conf
echo "recuse-install: config persisted -> ${CONF_DST}"

# --- 5. render the banner from the template ----------------------------------
render_banner() {
  local tmp
  tmp="$(mktemp "${CONF_DIR}/.banner.XXXXXX")"
  # Substitute placeholders LITERALLY using awk index/substr, so values that
  # contain characters special to sed/regex (& | / ? etc.) are inserted exactly.
  # We already rejected '@' in the values, so the placeholders cannot collide.
  awk \
    -v d="${RECUSE_DIRECTIVE}" \
    -v r="${RECUSE_REASON}" \
    -v s="${RECUSE_SCOPE}" \
    -v f="${RECUSE_REF}" '
    function rep(line, ph, val,   out, idx, n) {
      out = ""; n = length(ph)
      while ((idx = index(line, ph)) > 0) {
        out = out substr(line, 1, idx - 1) val
        line = substr(line, idx + n)
      }
      return out line
    }
    {
      $0 = rep($0, "@DIRECTIVE@", d)
      $0 = rep($0, "@REASON@", r)
      $0 = rep($0, "@SCOPE@", s)
      $0 = rep($0, "@REF@", f)
      print
    }
  ' "${TEMPLATE_SRC}" > "${tmp}"
  chmod 644 "${tmp}"
  mv "${tmp}" "${BANNER_DST}"
}
render_banner
echo "recuse-install: banner rendered -> ${BANNER_DST}"
echo "recuse-install:   sentinel: RECUSE/0.1 ${RECUSE_DIRECTIVE}; reason=${RECUSE_REASON}; scope=${RECUSE_SCOPE}; ref=${RECUSE_REF}"

# --- 6. per-session PAM hook -------------------------------------------------
install -m 755 "${HOOK_SRC}" "${HOOK_DST}"
echo "recuse-install: pam hook -> ${HOOK_DST} (0755)"

# --- 6b. install the uninstaller to PATH as `recuse-uninstall` (best-effort) --
UNINSTALL_SRC="${SCRIPT_DIR}/uninstall.sh"
if [[ -f "${UNINSTALL_SRC}" ]]; then
  install -m 755 "${UNINSTALL_SRC}" /usr/local/bin/recuse-uninstall
  echo "recuse-install: uninstaller -> /usr/local/bin/recuse-uninstall  (run: sudo recuse-uninstall)"
fi

# --- 7. append-only JSON connection log directory ----------------------------
install -d -m 700 "${LOG_DIR}"
if [[ ! -e "${LOG_FILE}" ]]; then
  ( umask 177; : > "${LOG_FILE}" )
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

# --- 8. sshd_config: Banner directive ----------------------------------------
cp -a "${SSHD_CONFIG}" "${SSHD_CONFIG}.recuse.bak.$(date +%s)" 2>/dev/null || true
append_managed_block "${SSHD_CONFIG}" "${SSHD_SNIPPET_SRC}"

# --- 9. pam.d/sshd: per-session hook -----------------------------------------
cp -a "${PAM_SSHD}" "${PAM_SSHD}.recuse.bak.$(date +%s)" 2>/dev/null || true
append_managed_block "${PAM_SSHD}" "${PAM_SNIPPET_SRC}"

# --- 10. validate sshd config BEFORE reloading -------------------------------
echo "recuse-install: validating sshd config (sshd -t)..."
if ! sshd -t; then
  echo "recuse-install: ERROR — sshd -t failed; not reloading. Review ${SSHD_CONFIG}." >&2
  exit 1
fi

# --- 11. reload ssh (reload preferred; restart only as last resort) ----------
if command -v systemctl >/dev/null 2>&1; then
  systemctl reload ssh 2>/dev/null \
    || systemctl reload sshd 2>/dev/null \
    || systemctl restart ssh
else
  service ssh reload || service ssh restart
fi
echo "recuse-install: ssh reloaded."

echo "recuse-install: done."
echo "recuse-install: verify with:  ssh user@<host>   (banner shows pre-auth)"
echo "recuse-install: connection log:   ${LOG_FILE}"
if [ "${RECUSE_THROTTLE_ENABLED}" = "true" ]; then
  echo "recuse-install: throttle ENABLED (delay-only, max ${RECUSE_THROTTLE_MAX_CONN}/${RECUSE_THROTTLE_WINDOW_SECONDS}s, never blocks)."
  echo "recuse-install: allow-listed IPs (never throttled): ${RECUSE_THROTTLE_ALLOW_IPS:-<none>}"
else
  echo "recuse-install: throttle OFF (default). Enable with --throttle or RECUSE_THROTTLE_ENABLED=true in ${CONF_DST}."
fi
