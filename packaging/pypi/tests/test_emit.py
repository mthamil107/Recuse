"""Tests for recuse.emit: server-side emission of RECUSE signals.

These make NO network calls and use NO third-party packages: the ASGI and WSGI
middleware are driven by hand over their raw protocols with fake ``send`` /
``receive`` callables and a fake ``start_response``. The load-bearing assertion
throughout is a ROUND TRIP — whatever an emitter puts on the wire is fed back
through :func:`recuse.parse_signal` and must recover the original directive and
parameters.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from recuse import parse_signal
from recuse.emit import (
    HEADER_NAME,
    RecuseASGIMiddleware,
    RecuseWSGIMiddleware,
    banner_text,
    fastapi_dependency,
    flask_after_request,
    signal_header,
)


# --------------------------------------------------------------------------- helpers
def run(coro):
    """Run a coroutine on a fresh event loop (no pytest-asyncio dependency)."""
    return asyncio.new_event_loop().run_until_complete(coro)


def header_map(messages):
    """Lowercased ``{name: value}`` from a captured ASGI start message list."""
    for msg in messages:
        if msg["type"] == "http.response.start":
            return {k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in msg["headers"]}
    raise AssertionError("no http.response.start was sent")


def body_of(messages):
    """Concatenated body bytes from a captured ASGI message list."""
    return b"".join(m.get("body", b"") or b""
                    for m in messages if m["type"] == "http.response.body")


def make_asgi_app(body=b"ok", content_type="text/plain", status=200,
                  chunks=None, extra_headers=()):
    """A minimal ASGI3 application that returns a fixed response."""
    async def app(scope, receive, send):
        pieces = chunks if chunks is not None else [body]
        headers = [(b"content-type", content_type.encode("latin-1")),
                   (b"content-length",
                    str(sum(len(p) for p in pieces)).encode("latin-1"))]
        headers.extend(extra_headers)
        await send({"type": "http.response.start", "status": status,
                    "headers": headers})
        for i, piece in enumerate(pieces):
            await send({"type": "http.response.body", "body": piece,
                        "more_body": i < len(pieces) - 1})
    return app


async def drive_asgi(app, scope=None):
    """Call an ASGI app and capture every message it sends."""
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope or {"type": "http", "path": "/", "method": "GET"},
              receive, send)
    return sent


def make_wsgi_app(body=b"ok", content_type="text/plain", status="200 OK",
                  chunks=None):
    """A minimal WSGI application that returns a fixed response."""
    def app(environ, start_response):
        pieces = chunks if chunks is not None else [body]
        start_response(status, [
            ("Content-Type", content_type),
            ("Content-Length", str(sum(len(p) for p in pieces))),
        ])
        return list(pieces)
    return app


class FakeStartResponse:
    """Records the (status, headers, exc_info) a WSGI app finally commits to."""

    def __init__(self):
        self.calls = []

    def __call__(self, status, headers, exc_info=None):
        self.calls.append((status, list(headers), exc_info))
        return lambda data: None

    @property
    def headers(self):
        assert self.calls, "start_response was never called"
        return {k.lower(): v for k, v in self.calls[-1][1]}

    @property
    def status(self):
        return self.calls[-1][0]


class FakeResponse:
    """Duck-type stand-in for a Flask/Starlette response object."""

    def __init__(self):
        self.headers = {}


# --------------------------------------------------------------------------- signal_header
def test_signal_header_name_and_round_trip():
    name, value = signal_header("deny", reason="production",
                                scope="all-automation",
                                ref="https://example.com/ai-policy",
                                id="7f3a9c2e")
    assert name == HEADER_NAME == "Recuse-Signal"
    assert value.startswith("RECUSE/0.3 deny;")
    sig = parse_signal(value)
    assert sig is not None
    assert sig.directive == "deny"
    assert sig.malformed is False
    assert sig.version == (0, 3)
    assert sig.reason == "production"
    assert sig.scope == "all-automation"
    assert sig.ref == "https://example.com/ai-policy"
    assert sig.id == "7f3a9c2e"


def test_signal_header_explicit_version():
    _, value = signal_header("halt", reason="budget-exceeded", version="0.2")
    assert value == "RECUSE/0.2 halt; reason=budget-exceeded"
    assert parse_signal(value).version == (0, 2)


def test_signal_header_all_four_directives_round_trip():
    for directive in ("deny", "throttle", "warn", "halt"):
        _, value = signal_header(directive, reason="test")
        sig = parse_signal(value)
        assert sig.directive == directive and sig.malformed is False


def test_signal_header_rejects_unknown_directive():
    with pytest.raises(ValueError):
        signal_header("shutdown", reason="nope")


def test_signal_header_percent_encodes_control_characters():
    # A newline in a free-text reason must never reach the wire as a real CRLF,
    # or it becomes a header-injection vector.
    _, value = signal_header("warn", reason="line1\r\nX-Evil: yes")
    assert "\r" not in value and "\n" not in value
    assert "%0D%0A" in value


def test_signal_header_percent_encodes_non_latin1():
    _, value = signal_header("warn", reason="café-中")
    value.encode("latin-1")  # must not raise
    assert "%" in value


# --------------------------------------------------------------------------- banner_text
def test_banner_text_sentinel_is_first_line():
    text = banner_text("deny", "No bots here.", reason="production")
    first, second = text.split("\n")
    assert first == "RECUSE/0.3 deny; reason=production"
    assert second == "No bots here."
    assert parse_signal(text).directive == "deny"


def test_banner_text_default_notice_per_directive():
    for directive in ("deny", "throttle", "warn", "halt"):
        text = banner_text(directive)
        assert len(text.split("\n")) == 2
        assert text.split("\n")[1]  # a non-empty default notice
        assert parse_signal(text).directive == directive


def test_banner_text_empty_message_is_bare_sentinel():
    text = banner_text("halt", "", id="abc-123")
    assert text == "RECUSE/0.3 halt; id=abc-123"
    assert "\n" not in text


def test_banner_text_rejects_unknown_directive():
    with pytest.raises(ValueError):
        banner_text("stop-please")


# --------------------------------------------------------------------------- ASGI
def test_asgi_adds_header_and_round_trips():
    app = RecuseASGIMiddleware(make_asgi_app(), "deny", reason="production",
                               id="7f3a9c2e")
    sent = run(drive_asgi(app))
    value = header_map(sent)["recuse-signal"]
    sig = parse_signal(value)
    assert sig.directive == "deny"
    assert sig.reason == "production"
    assert sig.id == "7f3a9c2e"
    assert body_of(sent) == b"ok"  # header-only: body untouched


def test_asgi_preserves_status_and_other_headers():
    app = RecuseASGIMiddleware(make_asgi_app(status=403), "deny")
    sent = run(drive_asgi(app))
    start = [m for m in sent if m["type"] == "http.response.start"][0]
    assert start["status"] == 403
    assert header_map(sent)["content-type"] == "text/plain"


def test_asgi_replaces_an_existing_recuse_header():
    upstream = make_asgi_app(
        extra_headers=[(b"recuse-signal", b"RECUSE/0.1 warn; reason=stale")])
    app = RecuseASGIMiddleware(upstream, "halt", reason="operator-request")
    sent = run(drive_asgi(app))
    start = [m for m in sent if m["type"] == "http.response.start"][0]
    values = [v for k, v in start["headers"] if k.lower() == b"recuse-signal"]
    assert len(values) == 1
    assert parse_signal(values[0].decode()).directive == "halt"


def test_asgi_predicate_suppresses_signal():
    def only_api(scope):
        return scope.get("path", "").startswith("/api/")

    app = RecuseASGIMiddleware(make_asgi_app(), "deny", should_signal=only_api)

    quiet = run(drive_asgi(app, {"type": "http", "path": "/index.html"}))
    assert "recuse-signal" not in header_map(quiet)
    assert body_of(quiet) == b"ok"

    loud = run(drive_asgi(app, {"type": "http", "path": "/api/items"}))
    assert parse_signal(header_map(loud)["recuse-signal"]).directive == "deny"


def test_asgi_passes_through_non_http_scopes():
    seen = {}

    async def lifespan_app(scope, receive, send):
        seen["type"] = scope["type"]
        await send({"type": "lifespan.startup.complete"})

    app = RecuseASGIMiddleware(lifespan_app, "warn")
    sent = run(drive_asgi(app, {"type": "lifespan"}))
    assert seen["type"] == "lifespan"
    assert sent == [{"type": "lifespan.startup.complete"}]


def test_asgi_header_only_by_default_leaves_json_intact():
    payload = json.dumps({"items": [1, 2, 3]}).encode()
    app = RecuseASGIMiddleware(make_asgi_app(payload, "application/json"),
                               "warn", reason="production")
    sent = run(drive_asgi(app))
    assert json.loads(body_of(sent)) == {"items": [1, 2, 3]}
    assert "recuse-signal" in header_map(sent)


def test_asgi_include_body_text_plain_prepends_sentinel():
    app = RecuseASGIMiddleware(make_asgi_app(b"welcome\n"), "deny",
                               reason="production", include_body=True)
    sent = run(drive_asgi(app))
    body = body_of(sent).decode()
    assert body.startswith("RECUSE/0.3 deny; reason=production\n")
    assert body.endswith("welcome\n")
    assert int(header_map(sent)["content-length"]) == len(body_of(sent))
    assert parse_signal(body).directive == "deny"


def test_asgi_include_body_streams_prefix_only_once():
    app = RecuseASGIMiddleware(
        make_asgi_app(chunks=[b"aaa", b"bbb", b"ccc"]), "warn",
        include_body=True)
    body = body_of(run(drive_asgi(app))).decode()
    assert body.count("RECUSE/") == 1
    assert body.endswith("aaabbbccc")


def test_asgi_include_body_json_adds_key_without_corrupting():
    payload = json.dumps({"items": [1, 2], "ok": True}).encode()
    app = RecuseASGIMiddleware(make_asgi_app(payload, "application/json"),
                               "halt", reason="budget-exceeded",
                               include_body=True)
    sent = run(drive_asgi(app))
    raw = body_of(sent)
    parsed = json.loads(raw)  # still valid JSON
    assert parsed["items"] == [1, 2] and parsed["ok"] is True
    assert parse_signal(parsed["recuse_signal"]).directive == "halt"
    assert int(header_map(sent)["content-length"]) == len(raw)


def test_asgi_include_body_json_custom_key():
    payload = json.dumps({"data": None}).encode()
    app = RecuseASGIMiddleware(make_asgi_app(payload, "application/json"),
                               "warn", include_body=True, body_key="_recuse")
    parsed = json.loads(body_of(run(drive_asgi(app))))
    assert parse_signal(parsed["_recuse"]).directive == "warn"


def test_asgi_include_body_leaves_json_array_alone():
    # A top-level array has nowhere safe to add a key: the body must be untouched.
    payload = json.dumps([1, 2, 3]).encode()
    app = RecuseASGIMiddleware(make_asgi_app(payload, "application/json"),
                               "deny", include_body=True)
    sent = run(drive_asgi(app))
    assert body_of(sent) == payload
    assert "recuse-signal" in header_map(sent)  # header still emitted


def test_asgi_include_body_leaves_invalid_json_alone():
    app = RecuseASGIMiddleware(make_asgi_app(b"{not json", "application/json"),
                               "deny", include_body=True)
    assert body_of(run(drive_asgi(app))) == b"{not json"


def test_asgi_include_body_leaves_other_content_types_alone():
    app = RecuseASGIMiddleware(make_asgi_app(b"\x89PNG\r\n", "image/png"),
                               "deny", include_body=True)
    sent = run(drive_asgi(app))
    assert body_of(sent) == b"\x89PNG\r\n"
    assert "recuse-signal" in header_map(sent)


def test_asgi_rejects_unknown_directive_at_wiring_time():
    with pytest.raises(ValueError):
        RecuseASGIMiddleware(make_asgi_app(), "please-stop")


# --------------------------------------------------------------------------- WSGI
def test_wsgi_adds_header_and_round_trips():
    app = RecuseWSGIMiddleware(make_wsgi_app(), "deny", reason="production",
                               id="7f3a9c2e")
    sr = FakeStartResponse()
    body = b"".join(app({"PATH_INFO": "/"}, sr))
    sig = parse_signal(sr.headers["recuse-signal"])
    assert sig.directive == "deny" and sig.id == "7f3a9c2e"
    assert sr.status == "200 OK"
    assert body == b"ok"


def test_wsgi_preserves_other_headers():
    app = RecuseWSGIMiddleware(make_wsgi_app(), "warn")
    sr = FakeStartResponse()
    list(app({}, sr))
    assert sr.headers["content-type"] == "text/plain"
    assert sr.headers["content-length"] == "2"


def test_wsgi_replaces_an_existing_recuse_header():
    def upstream(environ, start_response):
        start_response("200 OK", [
            ("Content-Type", "text/plain"),
            ("Recuse-Signal", "RECUSE/0.1 warn; reason=stale"),
        ])
        return [b"ok"]

    app = RecuseWSGIMiddleware(upstream, "halt")
    sr = FakeStartResponse()
    list(app({}, sr))
    names = [k for k, _ in sr.calls[-1][1] if k.lower() == "recuse-signal"]
    assert len(names) == 1
    assert parse_signal(sr.headers["recuse-signal"]).directive == "halt"


def test_wsgi_predicate_suppresses_signal():
    def only_api(environ):
        return environ.get("PATH_INFO", "").startswith("/api/")

    app = RecuseWSGIMiddleware(make_wsgi_app(), "deny", should_signal=only_api)

    quiet = FakeStartResponse()
    assert b"".join(app({"PATH_INFO": "/index.html"}, quiet)) == b"ok"
    assert "recuse-signal" not in quiet.headers

    loud = FakeStartResponse()
    list(app({"PATH_INFO": "/api/items"}, loud))
    assert parse_signal(loud.headers["recuse-signal"]).directive == "deny"


def test_wsgi_header_only_passes_iterable_through():
    app = RecuseWSGIMiddleware(make_wsgi_app(chunks=[b"a", b"b"]), "warn")
    sr = FakeStartResponse()
    assert b"".join(app({}, sr)) == b"ab"
    assert "recuse-signal" in sr.headers


def test_wsgi_include_body_text_plain_prepends_sentinel():
    app = RecuseWSGIMiddleware(make_wsgi_app(b"welcome\n"), "deny",
                               reason="production", include_body=True)
    sr = FakeStartResponse()
    body = b"".join(app({}, sr))
    assert body.decode().startswith("RECUSE/0.3 deny; reason=production\n")
    assert body.endswith(b"welcome\n")
    assert int(sr.headers["content-length"]) == len(body)
    assert parse_signal(body.decode()).directive == "deny"


def test_wsgi_include_body_json_adds_key_without_corrupting():
    payload = json.dumps({"items": [1, 2]}).encode()
    app = RecuseWSGIMiddleware(make_wsgi_app(payload, "application/json"),
                               "throttle", reason="load", include_body=True)
    sr = FakeStartResponse()
    raw = b"".join(app({}, sr))
    parsed = json.loads(raw)
    assert parsed["items"] == [1, 2]
    assert parse_signal(parsed["recuse_signal"]).directive == "throttle"
    assert int(sr.headers["content-length"]) == len(raw)


def test_wsgi_include_body_leaves_json_array_alone():
    payload = json.dumps([1, 2]).encode()
    app = RecuseWSGIMiddleware(make_wsgi_app(payload, "application/json"),
                               "deny", include_body=True)
    sr = FakeStartResponse()
    assert b"".join(app({}, sr)) == payload
    assert "recuse-signal" in sr.headers


def test_wsgi_closes_the_upstream_iterable():
    closed = {"yes": False}

    class Closing:
        def __iter__(self):
            return iter([b"ok"])

        def close(self):
            closed["yes"] = True

    def upstream(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return Closing()

    app = RecuseWSGIMiddleware(upstream, "warn", include_body=True)
    list(app({}, FakeStartResponse()))
    assert closed["yes"] is True


def test_wsgi_rejects_unknown_directive_at_wiring_time():
    with pytest.raises(ValueError):
        RecuseWSGIMiddleware(make_wsgi_app(), "abort")


# --------------------------------------------------------------------------- adapters
def test_flask_after_request_sets_header():
    hook = flask_after_request("warn", reason="production", id="abc")
    response = FakeResponse()
    assert hook(response) is response
    sig = parse_signal(response.headers[HEADER_NAME])
    assert sig.directive == "warn" and sig.id == "abc"


def test_flask_after_request_rejects_unknown_directive():
    with pytest.raises(ValueError):
        flask_after_request("nope")


def test_fastapi_dependency_sets_header():
    dep = fastapi_dependency("halt", reason="operator-request")
    response = FakeResponse()
    assert dep(response) is response
    assert parse_signal(response.headers[HEADER_NAME]).directive == "halt"


def test_fastapi_dependency_annotates_when_response_class_given():
    dep = fastapi_dependency("deny", response_class=FakeResponse)
    assert dep.__annotations__["response"] is FakeResponse


def test_fastapi_dependency_unannotated_without_a_framework():
    # Nothing named starlette/fastapi is imported by these tests, so the
    # dependency stays unannotated rather than importing anything.
    dep = fastapi_dependency("warn")
    assert "response" not in getattr(dep, "__annotations__", {})


def test_emit_imports_no_web_frameworks():
    import sys

    import recuse.emit  # noqa: F401  (re-import is a no-op; asserts the effect)

    for banned in ("flask", "fastapi", "starlette", "quart", "werkzeug"):
        assert banned not in sys.modules, f"recuse.emit pulled in {banned}"
