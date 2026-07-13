# Recuse Telemetry (OPT-IN, privacy-preserving) — default OFF

This directory documents the **optional** telemetry that the Recuse adapters can
emit so an operator can count how often the cooperative signal fires — and,
where the protocol makes it observable, how often an agent actually withdraws.

Aggregated across deployments, these counts are intended to become the **first
field data of real agents responding to a governance signal**. Nothing here is
required for the signal to work: the banner / NOTICE / admission warning all
function fully with telemetry OFF.

> The Recuse Signal is a COOPERATIVE policy channel, **not** a security control
> (see `../../spec/recuse-signal-v0.1.md` §9). Telemetry is likewise advisory:
> it counts signal emissions, it does not enforce anything.

---

## Privacy stance (read this first)

Telemetry is designed so that a captured log can be shared or published without
a privacy review. It **never** records:

- IP addresses, hostnames, or reverse-DNS of any client or server;
- usernames, DB users, Kubernetes identities, groups, service accounts;
- resource names, namespaces, databases, queries, commands, or arguments;
- session ids, request UIDs, or any other per-connection correlator;
- anything that could be joined back to a person, host, or session (no PII).

What it **does** record is a coarse, non-identifying count of *emissions*:

- **default OFF** — every adapter ships with telemetry disabled; an operator
  must explicitly opt in;
- the **timestamp is truncated to the hour** (UTC) so events cannot be used to
  time-correlate a specific session;
- each event is a single unit `count: 1` — a coarse, anonymized counter that the
  aggregator sums. There is no per-event identity to aggregate *by*.

This satisfies the repo-wide rule: **no real IPs / hostnames / usernames in
anything published or logged for telemetry.**

---

## Opt-in flag (default OFF) per adapter

| Adapter    | Flag                 | Where set                                   | Default |
|------------|----------------------|---------------------------------------------|---------|
| SSH (PAM)  | `RECUSE_TELEMETRY`   | `/etc/recuse/recuse.conf` (`="true"`)       | `false` |
| Postgres   | `RECUSE_TELEMETRY`   | environment / systemd unit (`true`/`1`/`on`)| off     |
| Kubernetes | `RECUSE_TELEMETRY`   | environment / ConfigMap (`true`/`1`/`on`)   | off     |

Any value other than the accepted true-tokens leaves telemetry OFF (fail-safe).

- **SSH** accepts exactly `true`.
- **Postgres / Kubernetes** accept `true`, `1`, `on`, or `yes` (case-sensitive,
  lower-case); anything else is OFF.

---

## Event schema (`recuse.telemetry/v1`)

One JSON object per line (JSON Lines). Every field is coarse and non-identifying:

```json
{"schema":"recuse.telemetry/v1","timestamp":"2026-07-13T10:00:00Z","protocol":"ssh","directive":"deny","outcome":"emitted","count":1}
```

| Field       | Type   | Meaning                                                                 |
|-------------|--------|-------------------------------------------------------------------------|
| `schema`    | string | Always `recuse.telemetry/v1`. The aggregator matches on this marker so telemetry lines can safely coexist with other JSON logs. |
| `timestamp` | string | RFC3339 UTC, **truncated to the hour** (`...:00:00Z`). Coarse on purpose. |
| `protocol`  | string | `ssh` \| `postgres` \| `kubernetes`.                                     |
| `directive` | string | The advisory directive carried by the signal: `deny` \| `throttle` \| `warn` (\| `other` if a non-standard token was configured). |
| `outcome`   | string | Coded outcome, **only where observable** — see below.                   |
| `count`     | int    | Always `1`. A coarse unit the aggregator sums; there is no identity to key on. |

### `outcome` — coded, and honest about what is observable

| Value       | Meaning                                                                                          |
|-------------|--------------------------------------------------------------------------------------------------|
| `emitted`   | The signal was sent to the client/caller. All three adapters emit this at the signal site.       |
| `withdrawn` | The agent was observed to withdraw *after* seeing the signal. **Reserved** — see the note below.  |

At the signal site the adapters record `emitted`. A **voluntary withdrawal**
(an agent choosing to disconnect/abort *because of* the notice) is not cleanly
observable at the point the signal is sent, so the adapters do **not** guess:

- **SSH**: the PAM session-open hook sees the connection; whether the agent then
  disconnects is a separate PAM `close_session` with no link to intent.
- **Postgres**: the proxy could, in principle, observe a client that closes the
  connection after the NOTICE without issuing any query. This is left as a
  documented, optional extension (emit a second `withdrawn` event on such a
  close) rather than baked in, to keep the change minimal.
- **Kubernetes**: `mode=deny` is an *enforced* block, not a voluntary
  withdrawal, so it is still recorded as `emitted` (the operation was denied, the
  agent did not choose to recuse).

The `withdrawn` outcome is therefore part of the schema so aggregation is
forward-compatible, but adapters emit it only if an operator wires up an
explicit, observable withdrawal signal. The aggregator already counts it when
present.

---

## Local append-only log location

Telemetry is written **append-only**, one JSON line per event, mode `0600`, to a
file separate from the adapter's normal connection/decision log:

| Adapter    | Telemetry log location                                                        |
|------------|-------------------------------------------------------------------------------|
| SSH        | `/var/log/recuse/telemetry.json`                                              |
| Postgres   | `RECUSE_TELEMETRY_LOG` (default `/var/log/recuse/telemetry.json`)             |
| Kubernetes | **stdout** (JSON line, captured by the cluster log pipeline — `kubectl logs`). Redirect to a file to aggregate, e.g. `kubectl logs deploy/recuse-webhook | grep recuse.telemetry > telemetry.json`. |

Keeping telemetry in its own file/stream means the coarse counts can be shared
without also exposing the operational connection logs (which *do* contain IPs and
usernames and must **not** be published).

---

## Aggregating

`aggregate.py` (stdlib only, no external deps) reads one or more telemetry logs
and prints summary counts:

```sh
python3 aggregate.py /var/log/recuse/telemetry.json
python3 aggregate.py sample-telemetry.json          # bundled sample
python3 aggregate.py log1.json log2.json            # multiple files summed
```

It filters strictly on `schema == "recuse.telemetry/v1"`, so pointing it at a
mixed log (e.g. a Kubernetes stdout capture that also contains decision lines) is
safe — non-telemetry lines are ignored.

Output: emissions per `protocol` × `directive`, plus any observed `withdrawn`
outcomes.

---

## Threat model / non-goals

- Telemetry is **not** authenticated or tamper-evident; a hostile client cannot
  be counted honestly (it can ignore the signal silently). These counts describe
  the behavior of *cooperating* agents, which is exactly the population of
  interest.
- Telemetry adds **no** latency-sensitive path: the SSH hook's emit is fully
  wrapped and never changes the login exit code (see below); the Go emits are
  best-effort and swallow all errors.
- Telemetry never captures secrets, credentials, IPs, hostnames, or usernames.

### SSH safety note

The SSH PAM hook is safety-critical: it **must always `exit 0`** and never block
a login. The telemetry emit there is wrapped so any failure is swallowed
(fail-open), it runs only on `open_session`, it writes at most one short line,
and it cannot change the hook's exit status. Verify with `sshd -t` after install.
