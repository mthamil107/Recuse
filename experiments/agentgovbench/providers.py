"""AgentGovBench — vendor-neutral LLM provider layer.

One interface, four back-ends:

  * ``openai``         — OpenAI Chat Completions.
  * ``anthropic``      — Anthropic Messages.
  * ``gemini``         — Google Gemini (google-genai).
  * ``openai_compat``  — any OpenAI-compatible HTTP endpoint (vLLM, Ollama,
                         llama.cpp server, TGI, LM Studio, …) selected purely by
                         a ``base_url``. This is how open-weights models
                         (Llama, Qwen, Mistral, …) enter the benchmark.

The benchmark loop drives a ``Provider`` without knowing the vendor, so the
protocol environment, tool execution, and outcome coding stay byte-for-byte
identical across every agent — apples to apples.

Interface
---------
    p = make_provider(vendor, model, system, TOOLS, keys,
                      temperature=1.0, seed=1234, base_url=None)
    p.add_user(task_text)                 # seed the task
    while ...:
        text, calls = p.step()            # one model turn; calls = [ToolCall]
        for c in calls:
            result = run_the_tool(c)
            p.add_tool_result(c, result)  # feed the tool output back
        # optionally: p.add_user(halt_text)   # inject a prompt-channel message

    p.response_ids   -> list[str]  (per-turn API request/response ids)
    p.usage          -> {"input_tokens": int, "output_tokens": int}

``TOOLS`` is a neutral schema — a list of
    {"name": str, "description": str, "parameters": <JSON-Schema object>}
each provider translates to its own function-calling format.

Keys are loaded from a gitignored ``secrets.local.json`` (preferred) and/or the
``~/.claude/servers/llm-Keys.env`` env file, without ever printing values.
"""
import json
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------- key loading
def _parse_env(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_BAD = (None, "", "sk-...", "CHANGE_ME", "EMPTY_OR_CHANGE_ME")


def load_keys(secrets_path=None):
    """Return a keys dict for every supported vendor.

    Reads ``secrets.local.json`` (if it exists next to the harness or at the
    given path) and the ``llm-Keys.env`` env file. Values are NEVER printed.

    Returns::

        {
          "openai":        key|None,
          "anthropic":     key|None,
          "gemini":        key|None,
          "openai_compat": {"api_key": key|None, "base_url": str|None},
        }
    """
    env = _parse_env(os.path.expanduser("~/.claude/servers/llm-Keys.env"))

    cfg = {}
    if secrets_path is None:
        secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "secrets.local.json")
    if os.path.exists(secrets_path):
        with open(secrets_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    def pick(env_name, cfg_name):
        v = env.get(env_name) or cfg.get(cfg_name)
        return v if v not in _BAD else None

    compat_cfg = cfg.get("openai_compat", {}) or {}
    compat = {
        "api_key": (compat_cfg.get("api_key") or env.get("OPENAI_COMPAT_API_KEY")
                    or "EMPTY"),
        "base_url": (compat_cfg.get("base_url") or env.get("OPENAI_COMPAT_BASE_URL")
                     or None),
    }

    return {
        "openai": pick("OPENAI_API_KEY", "openai_api_key"),
        "anthropic": pick("ANTHROPIC_API_KEY", "anthropic_api_key"),
        "gemini": pick("GEMINI_API_KEY", "gemini_api_key"),
        "openai_compat": compat,
    }


# ---------------------------------------------------------------- normalized types
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


def _is_openai_reasoning(model):
    # gpt-5.* / o-series reasoning models accept only temperature=1 -> we omit it.
    m = (model or "").lower()
    return (m.startswith("gpt-5") or m.startswith("o1")
            or m.startswith("o3") or m.startswith("o4"))


# ---------------------------------------------------------------- OpenAI (+ compatible)
class OpenAIProvider:
    """OpenAI Chat Completions. With ``base_url`` set this also drives any
    OpenAI-compatible server (vLLM / Ollama / TGI / LM Studio)."""
    vendor = "openai"

    def __init__(self, model, system, tools, key, temperature=1.0,
                 max_tokens=4096, seed=None, base_url=None):
        from openai import OpenAI
        self.client = OpenAI(api_key=key or "EMPTY", base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed
        self.base_url = base_url
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": t["parameters"]}} for t in tools]
        self.messages = [{"role": "system", "content": system}]
        self.response_ids = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def add_user(self, content):
        self.messages.append({"role": "user", "content": content})

    def add_tool_result(self, call, content):
        self.messages.append({"role": "tool", "tool_call_id": call.id,
                              "content": content})

    def _record(self, resp):
        rid = getattr(resp, "id", None)
        if rid:
            self.response_ids.append(rid)
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage["input_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "completion_tokens", 0) or 0

    def step(self):
        kwargs = dict(model=self.model, messages=self.messages,
                      tools=self.tools, tool_choice="auto")
        if self.temperature is not None and not _is_openai_reasoning(self.model):
            kwargs["temperature"] = self.temperature
        if self.seed is not None:
            kwargs["seed"] = self.seed
        try:
            resp = self.client.chat.completions.create(
                max_completion_tokens=self.max_tokens, **kwargs)
        except Exception as e:
            if "max_completion_tokens" in str(e) or "max_tokens" in str(e):
                resp = self.client.chat.completions.create(
                    max_tokens=self.max_tokens, **kwargs)
            else:
                raise
        self._record(resp)
        m = resp.choices[0].message
        self.messages.append(m.model_dump(exclude_none=True))
        text = m.content or ""
        calls = []
        for tc in (m.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        return text, calls


# ---------------------------------------------------------------- Anthropic
class AnthropicProvider:
    vendor = "anthropic"

    def __init__(self, model, system, tools, key, temperature=1.0,
                 max_tokens=4096, seed=None, base_url=None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed  # recorded only; Messages API has no seed param
        self.tools = [{"name": t["name"], "description": t["description"],
                       "input_schema": t["parameters"]} for t in tools]
        self.messages = []
        self._pending_user = []
        self.response_ids = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def add_user(self, content):
        self._pending_user.append({"type": "text", "text": content})

    def add_tool_result(self, call, content):
        self._pending_user.append({"type": "tool_result",
                                   "tool_use_id": call.id, "content": content})

    def _flush(self):
        if self._pending_user:
            self.messages.append({"role": "user", "content": self._pending_user})
            self._pending_user = []

    def step(self):
        self._flush()
        kwargs = dict(model=self.model, system=self.system, messages=self.messages,
                      tools=self.tools, max_tokens=self.max_tokens)
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self.client.messages.create(**kwargs)
        rid = getattr(resp, "id", None)
        if rid:
            self.response_ids.append(rid)
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        blocks, text_parts, calls = [], [], []
        for b in resp.content:
            bt = getattr(b, "type", None)
            if bt == "text":
                text_parts.append(b.text)
                blocks.append({"type": "text", "text": b.text})
            elif bt == "tool_use":
                calls.append(ToolCall(b.id, b.name, dict(b.input or {})))
                blocks.append({"type": "tool_use", "id": b.id,
                               "name": b.name, "input": b.input})
        self.messages.append({"role": "assistant", "content": blocks})
        return "".join(text_parts), calls


# ---------------------------------------------------------------- Gemini
class GeminiProvider:
    vendor = "gemini"

    def __init__(self, model, system, tools, key, temperature=1.0,
                 max_tokens=4096, seed=None, base_url=None):
        from google import genai
        from google.genai import types
        self._t = types
        self.client = genai.Client(api_key=key)
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed
        self.tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"], description=t["description"],
                parameters=t["parameters"]) for t in tools])]
        self.contents = []
        self._pending = []
        self.response_ids = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def add_user(self, content):
        self._pending.append(self._t.Part(text=content))

    def add_tool_result(self, call, content):
        self._pending.append(self._t.Part.from_function_response(
            name=call.name, response={"result": content}))

    def _flush(self):
        if self._pending:
            self.contents.append(self._t.Content(role="user", parts=self._pending))
            self._pending = []

    def step(self):
        self._flush()
        cfg_kwargs = dict(
            system_instruction=self.system, tools=self.tools,
            temperature=self.temperature, max_output_tokens=self.max_tokens,
            automatic_function_calling=self._t.AutomaticFunctionCallingConfig(disable=True),
            tool_config=self._t.ToolConfig(
                function_calling_config=self._t.FunctionCallingConfig(mode="AUTO")))
        if self.seed is not None:
            cfg_kwargs["seed"] = self.seed
        cfg = self._t.GenerateContentConfig(**cfg_kwargs)
        resp = self.client.models.generate_content(
            model=self.model, contents=self.contents, config=cfg)
        rid = getattr(resp, "response_id", None)
        if rid:
            self.response_ids.append(rid)
        u = getattr(resp, "usage_metadata", None)
        if u is not None:
            self.usage["input_tokens"] += getattr(u, "prompt_token_count", 0) or 0
            self.usage["output_tokens"] += getattr(u, "candidates_token_count", 0) or 0
        cand = resp.candidates[0]
        self.contents.append(cand.content)
        text_parts, calls = [], []
        for p in (cand.content.parts or []):
            if getattr(p, "text", None):
                text_parts.append(p.text)
            fc = getattr(p, "function_call", None)
            if fc:
                calls.append(ToolCall(getattr(fc, "id", None) or fc.name,
                                      fc.name, dict(fc.args or {})))
        return "".join(text_parts), calls


_PROVIDERS = {"openai": OpenAIProvider, "anthropic": AnthropicProvider,
              "gemini": GeminiProvider, "openai_compat": OpenAIProvider}


def make_provider(vendor, model, system, tools, keys, temperature=1.0,
                  max_tokens=4096, seed=None, base_url=None):
    """Construct a provider. ``keys`` is the dict from ``load_keys()``.

    ``openai_compat`` routes through the OpenAI client with a ``base_url`` taken
    from the passed ``base_url`` arg or from ``keys['openai_compat']``.
    """
    if vendor == "openai_compat":
        compat = keys.get("openai_compat") or {}
        url = base_url or compat.get("base_url")
        if not url:
            raise RuntimeError(
                "openai_compat vendor needs a base_url "
                "(set secrets.local.json -> openai_compat.base_url, "
                "e.g. http://localhost:8000/v1 for vLLM/Ollama)")
        return OpenAIProvider(model, system, tools, compat.get("api_key") or "EMPTY",
                              temperature=temperature, max_tokens=max_tokens,
                              seed=seed, base_url=url)

    if vendor not in _PROVIDERS:
        raise RuntimeError(f"unknown vendor '{vendor}'")
    key = keys.get(vendor)
    if not key:
        raise RuntimeError(
            f"no API key for vendor '{vendor}' "
            f"(add it to secrets.local.json or ~/.claude/servers/llm-Keys.env)")
    return _PROVIDERS[vendor](model, system, tools, key, temperature=temperature,
                              max_tokens=max_tokens, seed=seed, base_url=base_url)
