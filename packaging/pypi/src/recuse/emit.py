"""Server-side RECUSE signal *emission* — the other half of the protocol.

:mod:`recuse.signal` lets an agent **read** a signal. This module lets a Python
server **emit** one, so a resource owner can express access governance in-band on
the channels an agent actually looks at.

Two shapes are provided, matching the spec bindings:

* **HTTP** (spec v0.3 §5.4) — the ``Recuse-Signal`` response header field
  (:func:`signal_header`), plus drop-in :class:`RecuseASGIMiddleware` /
  :class:`RecuseWSGIMiddleware` wrappers and small
  :func:`flask_after_request` / :func:`fastapi_dependency` adapters.
* **Banner-style channels** (spec v0.3 §5.1/§5.2) — SSH pre-auth banners,
  PostgreSQL ``NOTICE`` text, admission warnings, MOTDs: the sentinel line plus
  a human-readable notice (:func:`banner_text`).

Everything here is stdlib-only. The ASGI and WSGI middleware implement their
protocols directly; **no** web framework is imported, and the Flask/FastAPI
adapters work purely by duck-typing on the objects those frameworks hand you.

The header form is RECOMMENDED by the spec because an agent sees it even when the
response body is never inspected, so it is what every helper here does by default.
Body injection is strictly opt-in (``include_body=True``) and deliberately
conservative — see :class:`RecuseASGIMiddleware`.

Quickstart::

    from recuse.emit import signal_header, RecuseASGIMiddleware

    name, value = signal_header("deny", reason="production", id="7f3a9c2e")
    # -> ("Recuse-Signal", "RECUSE/0.3 deny; reason=production; id=7f3a9c2e")

    app = RecuseASGIMiddleware(app, directive="warn", reason="production")
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .signal import build_signal

__all__ = [
    "HEADER_NAME",
    "DEFAULT_VERSION",
    "signal_header",
    "banner_text",
    "RecuseASGIMiddleware",
    "RecuseWSGIMiddleware",
    "flask_after_request",
    "fastapi_dependency",
]

# The IANA-requested HTTP response header field name (spec v0.3 §9.1). Field
# names are case-insensitive on the wire; this is the canonical spelling.
HEADER_NAME = "Recuse-Signal"

# The protocol version emitted by this module. The HTTP binding and the
# ``Recuse-Signal`` field were introduced in v0.3, so emitters default to it.
DEFAULT_VERSION = "0.3"

# Lowercased header name, as bytes — the form ASGI carries headers in.
_HEADER_NAME_BYTES = HEADER_NAME.lower().encode("latin-1")

# Default human-readable notice per directive, used by :func:`banner_text` when
# the caller does not supply one. Kept short: detail belongs behind ``ref``.
_DEFAULT_NOTICES = {
    "deny": "Automated/LLM-agent access to this resource is prohibited.",
    "throttle": "Automated/LLM-agent access is rate-limited on this resource.",
    "warn": "This resource is governed and automated access is observed.",
    "halt": "This operation has been halted. Stop now and take no further action.",
}


def _sentinel(directive: str, params: Dict[str, Any]) -> str:
    """Build a sentinel line, defaulting the version to :data:`DEFAULT_VERSION`.

    Delegates entirely to :func:`recuse.signal.build_signal` so the sentinel
    format (and its directive validation) lives in exactly one place.
    """
    params = dict(params)
    params.setdefault("version", DEFAULT_VERSION)
    return build_signal(directive, **params)


def _encode_header_value(sentinel: str) -> str:
    """Return ``sentinel`` reduced to octets legal in an HTTP field value.

    Sentinel parameter values are already restricted by the spec, but a caller
    may pass a free-text ``reason``. Control characters (which would allow header
    injection) and non-latin-1 octets are percent-encoded per spec v0.3 §5.4.1.
    """
    out: List[str] = []
    for ch in sentinel:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or code > 0xFF:
            out.extend("%%%02X" % b for b in ch.encode("utf-8"))
        else:
            out.append(ch)
    return "".join(out)


def signal_header(directive: str, **params: Any) -> Tuple[str, str]:
    """Return the ``(name, value)`` HTTP response header carrying a RECUSE signal.

    Args:
        directive: one of ``deny``/``throttle``/``warn``/``halt``.
        **params: sentinel parameters — ``reason``, ``scope``, ``ref``, ``id``,
            any extra registry key, and ``version`` (default
            :data:`DEFAULT_VERSION`). Passed straight to
            :func:`recuse.signal.build_signal`.

    Returns:
        ``("Recuse-Signal", "RECUSE/0.3 deny; reason=production")``-shaped tuple,
        ready to hand to any framework's header mapping.

    Raises:
        ValueError: if ``directive`` is not one of the four defined directives.
    """
    return HEADER_NAME, _encode_header_value(_sentinel(directive, params))


def banner_text(directive: str, message: Optional[str] = None,
                **params: Any) -> str:
    """Return the sentinel line plus a human-readable notice, as one block.

    This is the form for channels that carry free text to both humans and
    agents: an SSH pre-auth banner, a PostgreSQL ``NOTICE``, a Kubernetes
    admission warning, a MOTD, or a ``text/plain`` HTTP body.

    Args:
        directive: one of ``deny``/``throttle``/``warn``/``halt``.
        message: the notice text. Defaults to a short per-directive sentence.
            Pass ``""`` for the bare sentinel line with no notice.
        **params: sentinel parameters (see :func:`signal_header`).

    Returns:
        The sentinel line, then the notice, separated by a newline — with the
        sentinel FIRST so a line-oriented agent parser sees it immediately::

            RECUSE/0.3 deny; reason=production; id=7f3a9c2e
            Automated/LLM-agent access to this resource is prohibited.

    Raises:
        ValueError: if ``directive`` is not one of the four defined directives.
    """
    line = _sentinel(directive, params)
    if message is None:
        message = _DEFAULT_NOTICES.get(directive, "")
    if not message:
        return line
    return line + "\n" + message


def _content_type(headers: Iterable[Tuple[str, str]]) -> str:
    """Return the lowercased ``Content-Type`` value from ``(name, value)`` pairs."""
    for name, value in headers:
        if name.lower() == "content-type":
            return value.lower()
    return ""


def _is_text_plain(content_type: str) -> bool:
    return content_type.startswith("text/plain")


def _is_json(content_type: str) -> bool:
    # ``application/json`` and the ``+json`` structured suffix (RFC 6839).
    base = content_type.split(";", 1)[0].strip()
    return base == "application/json" or base.endswith("+json")


def _inject_json(body: bytes, sentinel: str, key: str) -> Optional[bytes]:
    """Return ``body`` with ``{key: sentinel}`` added, or ``None`` if unsafe.

    Only a top-level JSON **object** is modified, and only when ``key`` is not
    already present. Anything else — an array, a scalar, invalid JSON, a key
    collision — returns ``None`` so the caller leaves the body untouched. A
    response body is the application's contract; a signal must never corrupt it.
    """
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict) or key in parsed:
        return None
    parsed[key] = sentinel
    try:
        return json.dumps(parsed).encode("utf-8")
    except (TypeError, ValueError):
        return None


class _EmitterBase:
    """Shared configuration for the ASGI and WSGI middleware.

    Args:
        directive: one of ``deny``/``throttle``/``warn``/``halt``.
        params: sentinel parameters (see :func:`signal_header`). Given as a dict
            rather than ``**kwargs`` so it cannot collide with the middleware's
            own option names.
        should_signal: optional predicate on the request (the ASGI ``scope`` /
            the WSGI ``environ``). Return ``False`` to emit no signal for that
            request — e.g. to signal only on ``/api/`` paths, or only when the
            client looks automated. Default: signal on every request.
        include_body: opt-in body injection (see :class:`RecuseASGIMiddleware`).
        body_key: the JSON object key used when ``include_body`` is set and the
            response is JSON.

    Raises:
        ValueError: if ``directive`` is not one of the four defined directives.
    """

    def __init__(self, directive: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 should_signal: Optional[Callable[[Any], bool]] = None,
                 include_body: bool = False,
                 body_key: str = "recuse_signal",
                 **extra_params: Any):
        merged: Dict[str, Any] = dict(params or {})
        merged.update(extra_params)
        # Build once, at construction time, so a bad directive fails at wiring
        # time rather than on the first request in production.
        self.sentinel = _sentinel(directive, merged)
        self.directive = directive
        self.params = merged
        self.header_value = _encode_header_value(self.sentinel)
        self.should_signal = should_signal
        self.include_body = include_body
        self.body_key = body_key

    def _enabled(self, request: Any) -> bool:
        if self.should_signal is None:
            return True
        return bool(self.should_signal(request))


class RecuseASGIMiddleware(_EmitterBase):
    """Pure-ASGI3 middleware that attaches a RECUSE signal to every response.

    No framework dependency: it speaks the ASGI protocol directly, so it wraps a
    Starlette/FastAPI/Quart/Django-ASGI app, or any hand-rolled ASGI callable::

        app = RecuseASGIMiddleware(app, "warn", reason="production",
                                   ref="https://example.com/ai-policy")

    Non-``http`` scopes (lifespan, websocket) pass through untouched.

    **Header only, by default.** The ``Recuse-Signal`` response header is the
    RECOMMENDED binding (spec v0.3 §5.4.1) and is what this adds. Any header the
    wrapped app already set under that name is replaced, so the middleware's
    signal is unambiguous.

    **Body injection is opt-in and conservative.** With ``include_body=True``:

    * ``text/plain`` responses get the sentinel line prepended, followed by a
      newline (spec v0.3 §5.4.2 body form), and ``Content-Length`` is adjusted.
    * JSON responses (``application/json`` or a ``+json`` suffix) get the
      sentinel added under a single known key (``body_key``, default
      ``recuse_signal``) — and only if the top-level value is an object that does
      not already use that key. The body is buffered, re-serialized, and
      ``Content-Length`` corrected.
    * Every other content type is left byte-for-byte alone.

    A response body is the application's contract with its client; injection can
    only ever *add* a documented key or a leading plain-text line, and bails out
    entirely rather than emit something the client cannot parse. Prefer the
    header-only default unless you know the consuming agent reads bodies only.
    """

    def __init__(self, app: Any, directive: str, **kwargs: Any):
        self.app = app
        super().__init__(directive, **kwargs)

    async def __call__(self, scope: dict, receive: Callable,
                       send: Callable) -> None:
        if scope.get("type") != "http" or not self._enabled(scope):
            await self.app(scope, receive, send)
            return

        prefix = (self.sentinel + "\n").encode("utf-8")
        # Mutable per-request state shared with the send wrapper below.
        state = {"mode": None, "start": None, "buffer": bytearray(),
                 "prefixed": False}

        def rewrite_start(message: dict) -> Tuple[dict, str]:
            """Return the start message with our header set, plus the body mode."""
            raw = list(message.get("headers") or [])
            headers = [(k, v) for k, v in raw
                       if k.lower() != _HEADER_NAME_BYTES]
            headers.append(
                (_HEADER_NAME_BYTES, self.header_value.encode("latin-1")))
            decoded = [(k.decode("latin-1"), v.decode("latin-1"))
                       for k, v in headers]
            ctype = _content_type(decoded)
            mode = "pass"
            if self.include_body:
                if _is_text_plain(ctype):
                    mode = "text"
                elif _is_json(ctype):
                    mode = "json"
            if mode == "text":
                headers = _adjust_content_length_asgi(headers, len(prefix))
            out = dict(message)
            out["headers"] = headers
            return out, mode

        async def send_wrapper(message: dict) -> None:
            mtype = message.get("type")
            if mtype == "http.response.start":
                new_message, mode = rewrite_start(message)
                state["mode"] = mode
                if mode == "json":
                    # Defer: the final Content-Length is unknown until the whole
                    # body has been seen and re-serialized.
                    state["start"] = new_message
                    return
                await send(new_message)
                return

            if mtype != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"") or b""
            if state["mode"] == "text":
                if not state["prefixed"]:  # first body chunk only
                    state["prefixed"] = True
                    message = dict(message)
                    message["body"] = prefix + body
                await send(message)
                return

            if state["mode"] == "json":
                state["buffer"].extend(body)
                if message.get("more_body"):
                    return
                full = bytes(state["buffer"])
                injected = _inject_json(full, self.sentinel, self.body_key)
                start = state["start"] or {}
                if injected is not None:
                    full = injected
                    start = dict(start)
                    start["headers"] = _set_content_length_asgi(
                        list(start.get("headers") or []), len(full))
                await send(start)
                await send({"type": "http.response.body", "body": full,
                            "more_body": False})
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)


def _adjust_content_length_asgi(headers: List[Tuple[bytes, bytes]],
                                delta: int) -> List[Tuple[bytes, bytes]]:
    """Add ``delta`` bytes to a ``Content-Length`` header, if one is present."""
    out: List[Tuple[bytes, bytes]] = []
    for key, value in headers:
        if key.lower() == b"content-length":
            try:
                value = str(int(value) + delta).encode("latin-1")
            except ValueError:
                pass
        out.append((key, value))
    return out


def _set_content_length_asgi(headers: List[Tuple[bytes, bytes]],
                             length: int) -> List[Tuple[bytes, bytes]]:
    """Set ``Content-Length`` to ``length``, but only if it was already sent.

    A response that omitted the header (chunked / streaming) keeps omitting it.
    """
    out: List[Tuple[bytes, bytes]] = []
    for key, value in headers:
        if key.lower() == b"content-length":
            value = str(length).encode("latin-1")
        out.append((key, value))
    return out


class RecuseWSGIMiddleware(_EmitterBase):
    """Pure-WSGI middleware that attaches a RECUSE signal to every response.

    The WSGI counterpart of :class:`RecuseASGIMiddleware`, with the same options
    and the same conservative body rules. No framework dependency::

        application = RecuseWSGIMiddleware(application, "deny",
                                           reason="production")

    The wrapped app's ``start_response`` is intercepted so the
    ``Recuse-Signal`` header is added (replacing any existing one). With
    ``include_body=True`` the response is buffered so ``Content-Length`` stays
    correct; with the header-only default the app's iterable is passed straight
    through and streaming is preserved.
    """

    def __init__(self, app: Any, directive: str, **kwargs: Any):
        self.app = app
        super().__init__(directive, **kwargs)

    def __call__(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        if not self._enabled(environ):
            return self.app(environ, start_response)

        prefix = (self.sentinel + "\n").encode("utf-8")
        captured: Dict[str, Any] = {"status": None, "headers": None,
                                    "exc_info": None, "mode": "pass"}

        def capture(status: str, headers: List[Tuple[str, str]],
                    exc_info: Any = None) -> Callable:
            clean = [(k, v) for k, v in headers
                     if k.lower() != _HEADER_NAME_BYTES.decode("latin-1")]
            clean.append((HEADER_NAME, self.header_value))
            ctype = _content_type(clean)
            mode = "pass"
            if self.include_body:
                if _is_text_plain(ctype):
                    mode = "text"
                elif _is_json(ctype):
                    mode = "json"
            captured["status"] = status
            captured["headers"] = clean
            captured["exc_info"] = exc_info
            captured["mode"] = mode
            if mode == "pass":
                # Nothing about the body changes: start the response now so the
                # app can stream normally.
                return start_response(status, clean, exc_info)
            # Body will change; defer start_response until the length is known.
            return lambda data: None

        result = self.app(environ, capture)
        mode = captured["mode"]
        if mode == "pass":
            return result

        try:
            body = b"".join(result)
        finally:
            close = getattr(result, "close", None)
            if close is not None:
                close()

        if mode == "text":
            body = prefix + body
        else:  # json
            injected = _inject_json(body, self.sentinel, self.body_key)
            if injected is not None:
                body = injected
        headers = _set_content_length_wsgi(captured["headers"], len(body))
        start_response(captured["status"], headers, captured["exc_info"])
        return [body]


def _set_content_length_wsgi(headers: List[Tuple[str, str]],
                             length: int) -> List[Tuple[str, str]]:
    """Set ``Content-Length`` to ``length``, but only if it was already sent."""
    return [(k, str(length) if k.lower() == "content-length" else v)
            for k, v in headers]


def flask_after_request(directive: str, **params: Any) -> Callable[[Any], Any]:
    """Return a Flask ``after_request`` hook that sets the ``Recuse-Signal`` header.

    Flask is **not** imported; the returned function only requires the response
    object to expose a mutable ``headers`` mapping, which is true of Flask,
    Quart, and Werkzeug responses alike::

        app.after_request(flask_after_request("warn", reason="production"))

    Args:
        directive: one of ``deny``/``throttle``/``warn``/``halt``.
        **params: sentinel parameters (see :func:`signal_header`).

    Returns:
        ``f(response) -> response``, suitable for ``after_request``.

    Raises:
        ValueError: if ``directive`` is not one of the four defined directives.
    """
    name, value = signal_header(directive, **params)

    def _after_request(response: Any) -> Any:
        response.headers[name] = value
        return response

    _after_request.__name__ = "recuse_after_request"
    _after_request.__doc__ = f"Set the {name} response header to {value!r}."
    return _after_request


def _response_annotation(response_class: Optional[type]) -> Optional[type]:
    """Resolve the class to annotate a FastAPI ``response`` parameter with.

    FastAPI decides to inject its ``Response`` object based on the parameter's
    type annotation, so the dependency needs one. This module refuses to import
    fastapi/starlette, so instead it reads :data:`sys.modules` — if the caller is
    using FastAPI, they have already imported it, and no import happens here.
    Returns ``None`` when nothing is available, leaving the parameter unannotated.
    """
    if response_class is not None:
        return response_class
    for mod_name in ("starlette.responses", "fastapi"):
        module = sys.modules.get(mod_name)
        candidate = getattr(module, "Response", None) if module else None
        if isinstance(candidate, type):
            return candidate
    return None


def fastapi_dependency(directive: str, *,
                       response_class: Optional[type] = None,
                       **params: Any) -> Callable[[Any], Any]:
    """Return a FastAPI/Starlette dependency that sets the ``Recuse-Signal`` header.

    Neither fastapi nor starlette is imported. The returned callable takes the
    framework's ``Response`` object and duck-types on its ``headers`` mapping::

        dep = fastapi_dependency("throttle", reason="load")

        @app.get("/items", dependencies=[Depends(dep)])
        def list_items():
            ...

    FastAPI injects its ``Response`` based on the parameter's type annotation.
    That annotation is resolved from an already-imported fastapi/starlette in
    :data:`sys.modules` (never by importing one). If your app imports FastAPI
    lazily, pass ``response_class=fastapi.Response`` explicitly.

    Args:
        directive: one of ``deny``/``throttle``/``warn``/``halt``.
        response_class: the ``Response`` class to annotate with. Optional.
        **params: sentinel parameters (see :func:`signal_header`).

    Returns:
        ``f(response) -> response``. Usable via ``Depends(...)`` or called
        directly with any object exposing a ``headers`` mapping.

    Raises:
        ValueError: if ``directive`` is not one of the four defined directives.
    """
    name, value = signal_header(directive, **params)

    def _dependency(response: Any) -> Any:
        response.headers[name] = value
        return response

    _dependency.__name__ = "recuse_signal_dependency"
    _dependency.__doc__ = f"Set the {name} response header to {value!r}."
    annotation = _response_annotation(response_class)
    if annotation is not None:
        _dependency.__annotations__ = {"response": annotation,
                                       "return": annotation}
    else:
        # Leave the parameter genuinely unannotated rather than advertising a
        # bare ``Any``, which a framework would misread as a request parameter.
        _dependency.__annotations__ = {}
    return _dependency
