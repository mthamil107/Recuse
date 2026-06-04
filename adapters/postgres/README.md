# Recuse — PostgreSQL adapter (`recuse-pg-proxy`)

A small PostgreSQL wire-protocol proxy that sits in front of a real Postgres and
injects a single cooperative **Recuse Signal** `NOTICE` on each connection, while
transparently relaying everything else so the session works normally.

This is the Phase 1 PostgreSQL adapter for the Recuse project. It implements the
PostgreSQL binding (spec **section 7.2**) and emits the sentinel line defined in
spec **section 4**. See [`../../spec/recuse-signal-v0.1.md`](../../spec/recuse-signal-v0.1.md).

> **This is a cooperative signal, NOT a security control (spec section 9).**
> A non-conforming or malicious client can ignore the `NOTICE` entirely and
> proceed using valid credentials. Do not rely on this proxy as the sole
> protection for any sensitive resource. Real security rests on not issuing
> production credentials to agents, least-privilege roles, read replicas for AI
> workloads, bastion hosts, and network controls.

## What it does

On every connection the proxy emits, exactly once, a Postgres `NOTICE` whose
message is the Recuse sentinel line:

```
RECUSE/0.1 deny; reason=production; scope=all-automation; ref=https://example.com/ai-policy; id=<per-connection-uuid>
```

Putting the sentinel in the `NOTICE` **message** means any client driver surfaces
it, and a conforming agent can detect it by matching `^RECUSE/\d+\.\d+ ` (spec
section 8). The `deny` directive means a conforming agent **MUST** disconnect and
recuse itself (spec section 6.1). The notice also carries human-readable Detail
and Hint text for the operator.

## Architecture

```
  client (psql / driver / agent)
        |
        v   :6433
  +---------------------+
  |   recuse-pg-proxy   |   <-- injects ONE NoticeResponse immediately
  |                     |       before the first ReadyForQuery, then
  +---------------------+       relays both directions verbatim
        |
        v   :5432
  real PostgreSQL
```

Per connection:

1. The client's startup is read with `pgproto3.Backend`. `SSLRequest` /
   `GSSEncRequest` are answered with a single `'N'` byte (encryption denied) and
   startup is re-read until the real `StartupMessage` arrives. The `user` and
   `database` parameters are captured for logging.
2. The proxy dials the real backend and forwards the `StartupMessage` unchanged.
3. Both directions are relayed concurrently:
   - **client -> backend:** frontend messages (password / SASL / query / …) are
     relayed verbatim, so `scram-sha-256` authentication passes through unchanged.
   - **backend -> client:** backend messages are relayed; the **first**
     `ReadyForQuery` is preceded by the injected `NoticeResponse`. Injection
     happens exactly once per connection.
   - When either side closes, both connections are closed so the proxy unwinds.

A panic or error on one connection is recovered and never crashes the proxy.

## Configuration (environment)

| Variable         | Default               | Meaning                                  |
|------------------|-----------------------|------------------------------------------|
| `RECUSE_LISTEN`  | `127.0.0.1:6433`      | Address the proxy listens on.            |
| `RECUSE_BACKEND` | `127.0.0.1:5432`      | The real PostgreSQL to relay to.         |
| `RECUSE_LOG`     | `/var/log/recuse/pg.json` | JSON connect-log path (one line/conn). |

The log directory is created best-effort (`0700`), the file `0600`. One JSON line
is appended per connection:

```json
{"timestamp":"2026-06-04T12:00:00Z","id":"...","db_user":"alice","database":"app","client_addr":"127.0.0.1:54321","event":"connect"}
```

Logging is best-effort: a logging failure never fails a connection.

## Run locally

```sh
RECUSE_LISTEN=127.0.0.1:6433 \
RECUSE_BACKEND=127.0.0.1:5432 \
RECUSE_LOG=./pg.json \
./bin/recuse-pg-proxy
```

## Build

```sh
# Native build for development
go build -o recuse-pg-proxy .

# Static linux/amd64 release binary (Ubuntu 22.04 / Postgres 14 target)
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -o bin/recuse-pg-proxy .
```

## Test

```sh
go vet ./...
go test ./...
```

The unit test (`proxy_test.go`) uses in-memory `net.Pipe()` connections to drive
the real `relayBackendToClient` logic. It proves a `NoticeResponse` whose message
starts with `RECUSE/0.1 deny` is delivered **before** the first `ReadyForQuery`,
and that injection happens exactly once even when two `ReadyForQuery` messages are
sent.

## Install as a systemd service

```sh
# 1. Create the dedicated, non-login system user
useradd --system --no-create-home --shell /usr/sbin/nologin recuse-pg

# 2. Install binary, log dir, and unit
install -m 0755 bin/recuse-pg-proxy /usr/local/bin/recuse-pg-proxy
install -d -o recuse-pg -g recuse-pg -m 0700 /var/log/recuse
install -m 0644 recuse-pg-proxy.service /etc/systemd/system/

# 3. Enable
systemctl daemon-reload
systemctl enable --now recuse-pg-proxy.service
```

The unit (`recuse-pg-proxy.service`) runs as the `recuse-pg` system user with
`NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths=/var/log/recuse`,
`PrivateTmp`, an empty capability set, and a `@system-service` syscall filter.

## Try it with psql

Point a client at the proxy port (`6433`), not Postgres directly:

```sh
psql "host=127.0.0.1 port=6433 user=alice dbname=app"
```

`psql` prints the injected notice and then drops you at the prompt, e.g.:

```
NOTICE:  RECUSE/0.1 deny; reason=production; scope=all-automation; ref=https://example.com/ai-policy; id=...
DETAIL:  This is a production system. Automated and LLM-agent access is prohibited. ...
HINT:  Authorized humans needing access: see the policy reference above.
psql (14.x)
Type "help" for help.

app=>
```

A human can continue; a conforming agent recuses itself.

## Notes / caveats for live integration

- **Plaintext only.** The proxy denies SSL/GSS (`'N'`) so the wire stays plaintext
  for the proxy to read the `ReadyForQuery` and inject. Run the client-to-proxy hop
  over loopback or an otherwise trusted/network-encrypted path. The proxy-to-Postgres
  hop is also plaintext; keep it local or on a trusted network.
- The sentinel `ref` URL and the systemd `Documentation=` use `https://example.com/ai-policy`.
  Replace with your real policy URL at deploy time.

See the spec: [`../../spec/recuse-signal-v0.1.md`](../../spec/recuse-signal-v0.1.md).
