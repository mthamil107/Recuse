# The Recuse Signal — v0.1 (Draft)

**A response framework for cooperative AI-access governance.**

Status: Draft mini-standard
Version: 0.1.0
Date: 2026-06-04

---

## 1. Abstract

The Recuse Signal is a small, protocol-agnostic **response format** that a server
emits in-band to tell a connecting automated agent (an LLM agent, autonomous tool,
or unattended script) that its access is governed and that it is expected to
**voluntarily withdraw** — to *recuse* itself.

It is the access-control analogue of `robots.txt`: a published convention that
**compliant** agents honor by cooperation, not a mechanism that forces compliance.
It is deliberately *not* a security control (see §9). Its purpose is to make
servers legible to well-behaved agents and to create a standard, machine-parseable
channel through which a server can state a policy.

This document defines the signal format, its fields, the normative behavior
expected of a conforming agent, and how the signal is carried over specific
protocols (SSH, PostgreSQL, and others).

## 2. Terminology

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are
to be interpreted as described in RFC 2119.

- **Emitter** — the server (or an adapter on its behalf) that produces a Recuse Signal.
- **Agent** — any automated consumer of a connection: an LLM agent, an autonomous
  tool, or an unattended script.
- **Conforming Agent** — an Agent that recognizes the Recuse Signal and applies the
  normative behavior in §6.
- **Operator** — the human on whose behalf an Agent acts, if any.

## 3. Design goals

1. **Protocol-agnostic.** The same line format is carried over any plaintext-capable
   channel a protocol already exposes (SSH banner, Postgres `NOTICE`, HTTP header, …).
2. **Machine-parseable, human-readable.** A single sentinel line that is trivially
   greppable, followed by free-form human text.
3. **Stable and versioned.** The sentinel is version-bound so parsers never
   misinterpret a future revision.
4. **Recognizable.** A unique, collision-resistant token so an Agent can detect the
   signal with high confidence and low false-positive rate.
5. **Cooperative-first.** The framework expresses a *request* to withdraw, with
   graduated directives (`deny` / `throttle` / `warn`), not a hard block.

## 4. Signal format

A Recuse Signal consists of **one sentinel line** OPTIONALLY followed by one or more
lines of human-readable notice text.

### 4.1 Sentinel line (normative)

```
RECUSE/<major>.<minor> <directive>; <key>=<value>; <key>=<value> ...
```

- The line MUST begin, at column 0, with the literal token `RECUSE/` followed by a
  `<major>.<minor>` version (e.g. `RECUSE/0.1`). This token is the detection anchor.
- A single space separates the version from the `<directive>`.
- The directive is followed by zero or more `; key=value` parameter pairs.
- Parameter keys are lowercase ASCII. Values are ASCII; values containing `;` or
  whitespace MUST be percent-encoded (RFC 3986).
- The sentinel line is terminated by a newline. It MUST be ≤ 998 octets so it
  survives line-oriented transports.

### 4.2 Directives (normative)

| Directive  | Meaning                                                                 | Conforming Agent action |
|------------|-------------------------------------------------------------------------|-------------------------|
| `deny`     | Automated access is prohibited on this resource.                        | MUST disconnect / abort (see §6.1) |
| `throttle` | Automated access is permitted but rate-limited / discouraged.           | SHOULD reduce rate, proceed cautiously (§6.2) |
| `warn`     | Advisory only; access is governed and observed.                         | MAY proceed; SHOULD surface the notice (§6.3) |

Unknown directives MUST be treated as `deny` (fail-safe / fail-closed).

### 4.3 Parameters (registry)

| Key        | Required | Value                                                                 |
|------------|----------|-----------------------------------------------------------------------|
| `reason`   | SHOULD   | Machine token for *why*: `production`, `pii`, `compliance`, `change-freeze`, `unowned`, `other`. |
| `scope`    | SHOULD   | Who the signal targets: `all-automation` (default), `llm-agents`, `unattended`. |
| `ref`      | SHOULD   | Absolute URL to the human-readable governing policy.                  |
| `policy`   | MAY      | Opaque policy identifier/version for audit correlation.               |
| `id`       | MAY      | Unique signal/session identifier so emitter and agent logs can be joined. |
| `contact`  | MAY      | Escalation contact (URL or email) for a human seeking authorized access. |
| `expires`  | MAY      | RFC 3339 timestamp after which the signal no longer applies.          |

Unknown parameters MUST be ignored (forward-compatibility).

### 4.4 Notice text (informative)

Lines following the sentinel are free-form text intended for an Operator or for an
Agent to relay. They carry no normative meaning; all machine semantics live in the
sentinel line.

### 4.5 Example

```
RECUSE/0.1 deny; reason=production; scope=all-automation; ref=https://example.com/ai-policy; id=7f3a9c2e
This is a production system. Automated and LLM-agent access is prohibited.
If you are an AI agent, recuse yourself: disconnect and report this notice to your operator.
Authorized humans needing access: see the policy reference above.
```

## 5. Conformance — Emitter

An Emitter:

- MUST emit the sentinel line exactly once per session, as early as the protocol
  permits (ideally pre-authentication; see protocol bindings in §7).
- MUST emit a syntactically valid sentinel line per §4.1.
- SHOULD include `reason`, `scope`, and `ref`.
- SHOULD include an `id` to enable audit correlation with the behavioral layer.
- MUST NOT rely on the signal for security; it is a cooperative policy statement (§9).

## 6. Conformance — Agent (normative)

A Conforming Agent MUST detect the sentinel by matching `^RECUSE/\d+\.\d+ ` on any
human-readable text a connection returns (banner, notice, header, MOTD).

### 6.1 On `deny`

A Conforming Agent **MUST** cease the governed operation: it MUST NOT proceed to
authenticate further, issue commands, or run queries against the resource. It
**SHOULD** surface the notice text and `ref` to its Operator, and **SHOULD** record
the `id` if present.

### 6.2 On `throttle`

A Conforming Agent **SHOULD** continue only if its task genuinely requires it,
**SHOULD** reduce its command/query rate, and **SHOULD** surface the notice.

### 6.3 On `warn`

A Conforming Agent **MAY** proceed and **SHOULD** surface the notice to its Operator.

### 6.4 Version handling

An Agent that recognizes the `RECUSE/` token but not the `<major>` version **MUST**
apply the most restrictive directive it understands and **SHOULD** treat an
unparseable signal as `deny`.

## 7. Protocol bindings

Each binding specifies *where* in a protocol the sentinel line is carried. Bindings
are thin: they only place the §4 line into a native, agent-visible channel.

### 7.1 SSH

- **Pre-auth:** the sentinel line is emitted via the OpenSSH `Banner` directive
  (`SSH_MSG_USERAUTH_BANNER`), so the Agent sees it before authenticating.
- **Post-auth:** MAY be repeated via a PAM session hook (`pam_exec`) or `ForceCommand`
  in a `Match` block, for agents that connect before reading the banner.

### 7.2 PostgreSQL

- The sentinel line is emitted as a `NOTICE` at session start, via a
  `session_preload_libraries` login hook that issues `RAISE NOTICE '...'`.
- Postgres `NOTICE` messages surface in virtually every client driver, so a
  Conforming Agent receives the signal as part of its normal connection result.

### 7.3 Other protocols (placeholders for later versions)

- **MySQL / SQL Server:** login trigger / connect-time message channel.
- **HTTP:** an `X-Recuse` response header carrying the sentinel line, with the notice
  text in the body or a linked `ref`.

A future version of this document will normatively specify the MySQL, MSSQL, and HTTP
bindings.

## 8. Detection regex (informative)

```
^RECUSE/(?P<major>\d+)\.(?P<minor>\d+)\s+(?P<directive>[a-z-]+)(?P<params>(;[^;\n]*)*)\s*$
```

## 9. Security considerations (normative honesty)

The Recuse Signal is a **cooperative governance control, not a security boundary.**

- A malicious agent, a careless human, or any non-conforming client can ignore the
  signal entirely and proceed using valid credentials.
- The signal MUST NOT be the sole protection for any sensitive resource. Real
  security continues to rest on: not issuing production credentials to agents,
  bastion hosts, least-privilege roles, read replicas for AI workloads, and network
  controls.
- The Recuse framework's value is (a) a standard, legible policy channel for
  compliant agents and (b) an audit/early-warning surface when combined with the
  behavioral enforcement layer (out of scope for this document).

## 10. Relationship to the behavioral layer

This document defines only the **cooperative signaling** layer. A separate
behavioral-enforcement layer (timing/rate/pattern heuristics) MAY consume the same
`id` to correlate a cooperative signal with observed session behavior, but its
mechanics are out of scope here.

## 11. IANA / registry considerations

Should this signal be published as a public mini-standard, a registry SHOULD be
established for `directive` and `reason` tokens to allow controlled extension. Until
then, the tables in §4.2 and §4.3 are authoritative.

## 12. Changelog

- **0.1.0 (2026-06-04):** Initial draft. Sentinel format, directives
  (`deny`/`throttle`/`warn`), parameter registry, SSH and PostgreSQL bindings.
