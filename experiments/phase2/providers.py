"""Vendor-neutral LLM provider layer for the Recuse agent experiments.

One interface, three back-ends (OpenAI, Anthropic, Gemini). The experiment loop
drives a Provider without knowing the vendor, so the SSH session, tool execution,
and outcome classification stay byte-for-byte identical across models — apples to
apples.

Interface:
    p = make_provider(vendor, model, system, TOOLS, temperature=1.0)
    p.add_user(task_text)                 # seed the task
    while ...:
        text, calls = p.step()            # one model turn; calls = [ToolCall]
        for c in calls:
            result = run_the_tool(c)
            p.add_tool_result(c, result)  # feed the tool output back
        # optionally: p.add_user(halt_text)   # inject a prompt-channel message

TOOLS is a neutral schema: a list of
    {"name": str, "description": str, "parameters": <JSON-Schema object>}
each provider translates it to its own function-calling format.

Keys are loaded from ~/.claude/servers/llm-Keys.env first, then secrets.local.json,
without ever printing values.
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


def load_keys(secrets_cfg=None):
    """Return {'openai','anthropic','gemini': key|None} from the env file then
    secrets.local.json. Never prints values."""
    env = _parse_env(os.path.expanduser("~/.claude/servers/llm-Keys.env"))
    cfg = secrets_cfg or {}

    def pick(env_name, cfg_name):
        v = env.get(env_name) or cfg.get(cfg_name)
        bad = (None, "", "sk-...", "CHANGE_ME")
        return v if v not in bad else None

    return {
        "openai": pick("OPENAI_API_KEY", "openai_api_key"),
        "anthropic": pick("ANTHROPIC_API_KEY", "anthropic_api_key"),
        "gemini": pick("GEMINI_API_KEY", "gemini_api_key"),
    }


# ---------------------------------------------------------------- normalized types
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


# The canonical model roster (vendor, model_id). Smoke-test IDs before a big run.
MODEL_ROSTER = [
    ("openai", "gpt-4o-mini"),
    ("openai", "gpt-4.1"),
    ("openai", "gpt-5.1"),
    ("openai", "gpt-5.5"),
    ("anthropic", "claude-opus-4-8"),
    ("anthropic", "claude-sonnet-5"),
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("gemini", "gemini-2.5-flash"),
    ("gemini", "gemini-2.5-pro"),
]


def _is_openai_reasoning(model):
    # gpt-5.* reasoning models accept only temperature=1 -> we omit temperature.
    m = model.lower()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")


# ---------------------------------------------------------------- OpenAI
class OpenAIProvider:
    vendor = "openai"

    def __init__(self, model, system, tools, key, temperature=1.0, max_tokens=4096):
        from openai import OpenAI
        self.client = OpenAI(api_key=key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": t["parameters"]}} for t in tools]
        self.messages = [{"role": "system", "content": system}]

    def add_user(self, content):
        self.messages.append({"role": "user", "content": content})

    def add_tool_result(self, call, content):
        self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

    def step(self):
        kwargs = dict(model=self.model, messages=self.messages,
                      tools=self.tools, tool_choice="auto")
        if self.temperature is not None and not _is_openai_reasoning(self.model):
            kwargs["temperature"] = self.temperature
        try:
            resp = self.client.chat.completions.create(
                max_completion_tokens=self.max_tokens, **kwargs)
        except Exception as e:
            if "max_completion_tokens" in str(e) or "max_tokens" in str(e):
                resp = self.client.chat.completions.create(
                    max_tokens=self.max_tokens, **kwargs)
            else:
                raise
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

    def __init__(self, model, system, tools, key, temperature=1.0, max_tokens=4096):
        import anthropic
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tools = [{"name": t["name"], "description": t["description"],
                       "input_schema": t["parameters"]} for t in tools]
        self.messages = []
        self._pending_user = []  # content blocks for the next user turn

    def add_user(self, content):
        # A plain text block; batched into the next user turn (Anthropic requires
        # tool_results + any following user text to share one user turn).
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
        blocks = []
        text_parts, calls = [], []
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

    def __init__(self, model, system, tools, key, temperature=1.0, max_tokens=4096):
        from google import genai
        from google.genai import types
        self._t = types
        self.client = genai.Client(api_key=key)
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"], description=t["description"],
                parameters=t["parameters"]) for t in tools])]
        self.contents = []
        self._pending = []  # Parts for the next user turn

    def add_user(self, content):
        self._pending.append(self._t.Part(text=content))

    def add_tool_result(self, call, content):
        # Gemini matches function responses by name, not id.
        self._pending.append(self._t.Part.from_function_response(
            name=call.name, response={"result": content}))

    def _flush(self):
        if self._pending:
            self.contents.append(self._t.Content(role="user", parts=self._pending))
            self._pending = []

    def step(self):
        self._flush()
        cfg = self._t.GenerateContentConfig(
            system_instruction=self.system, tools=self.tools,
            temperature=self.temperature, max_output_tokens=self.max_tokens,
            automatic_function_calling=self._t.AutomaticFunctionCallingConfig(disable=True),
            tool_config=self._t.ToolConfig(
                function_calling_config=self._t.FunctionCallingConfig(mode="AUTO")))
        resp = self.client.models.generate_content(
            model=self.model, contents=self.contents, config=cfg)
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
              "gemini": GeminiProvider}


def make_provider(vendor, model, system, tools, keys, temperature=1.0, max_tokens=4096):
    """Construct a provider. `keys` is the dict from load_keys()."""
    key = keys.get(vendor)
    if not key:
        raise RuntimeError(f"no API key for vendor '{vendor}' "
                           f"(add it to ~/.claude/servers/llm-Keys.env)")
    return _PROVIDERS[vendor](model, system, tools, key,
                              temperature=temperature, max_tokens=max_tokens)
