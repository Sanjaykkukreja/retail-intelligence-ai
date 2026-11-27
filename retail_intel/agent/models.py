"""Component 1 - Reasoning model. Claude first.

  AnthropicClient        Claude Messages API with tool use  (DEFAULT)
  ScriptedModel          deterministic fake used by the tests (no network, no key)
  OpenAIResponsesClient  optional, kept only so the course's OpenAI examples can be compared; not installed by default

Two Claude models, each doing the job it is priced for (Anthropic's model guide lists Haiku for
"classification, extraction, and routing"):
  answer model  claude-sonnet-5-5  reasons over the evidence and calls tools   (ANTHROPIC_MODEL to change)
  router model  claude-haiku-5-5   classifies a question only when the keyword rules cannot (ANTHROPIC_ROUTER_MODEL)

Production basics ("Building a Production-Ready API Client", "API Best Practices and Cost Optimization"):
  * timeouts on every call; the SDK's own retry is off so OUR policy is the only one
  * retry with exponential backoff + jitter, only for errors worth retrying (429, 5xx, 529 overloaded, timeouts)
  * prompt caching: the system prompt and the tool list are marked cache_control, so repeat calls within
    5 minutes read them at a fraction of the input price instead of paying full price every step
  * usage and cost recorded on every call, including cache writes and cache reads

The Claude adapter follows the documented Messages API shapes and is unit-tested against fakes of them.
It has NOT been run against the live API from the build workspace.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_ROUTER_MODEL = "claude-haiku-5-5"

# USD per million tokens: (input, output, cache-read multiplier). Source: platform.claude.com models overview and
# prompt-caching pages, checked 25 Nov 2026. Cache writes (5-minute) cost 1.25 x input. Haiku 5.5 is listed
# "from" these prices. Prices change: check the pricing page and edit this table.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.05),
    "claude-opus-5-5": (4.00, 20.00, 0.05),
    "claude-haiku-5-5": (0.10, 0.50, 0.10),
}
CACHE_WRITE_MULT = 1.25


# ----------------------------------------------------------------------------- shared types
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    input_tokens: int = 0          # uncached input
    output_tokens: int = 0
    calls: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = 0.0   # None once any call had an unknown price

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens

    def add(self, other: "Usage") -> "Usage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.cache_write_tokens += other.cache_write_tokens
        self.cache_read_tokens += other.cache_read_tokens
        if self.cost_usd is None or other.cost_usd is None:
            self.cost_usd = None
        else:
            self.cost_usd = round(self.cost_usd + other.cost_usd, 6)
        return self


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage


def price_call(model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    p_in, p_out, read_mult = p
    return round((input_tokens * p_in + cache_write * p_in * CACHE_WRITE_MULT + cache_read * p_in * read_mult
                  + output_tokens * p_out) / 1e6, 6)


def cost_usd(usage: Usage) -> float | None:
    return usage.cost_usd if usage.calls else None


# ----------------------------------------------------------------------------- retries
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
RETRYABLE_NAMES = {"RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError", "OverloadedError"}


def is_retryable(e: Exception) -> bool:
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    return status in RETRYABLE_STATUS or type(e).__name__ in RETRYABLE_NAMES


def with_retries(fn: Callable[[], Any], attempts: int = 4, base_delay: float = 1.0, sleep=time.sleep) -> Any:
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised unless retryable
            if i == attempts - 1 or not is_retryable(e):
                raise
            sleep(base_delay * (2 ** i) + random.uniform(0, base_delay))


# ----------------------------------------------------------------------------- interface
class Conversation:
    def step(self, tools: list[dict]) -> ModelTurn: raise NotImplementedError
    def add_tool_results(self, results: list[tuple[ToolCall, str]]) -> None: raise NotImplementedError
    def add_user(self, text: str) -> None: raise NotImplementedError


class ModelClient:
    provider = "base"
    model = "none"
    max_output_tokens = 4000

    def conversation(self, system: str, history: list[dict], user_text: str) -> Conversation:
        raise NotImplementedError

    def complete(self, system: str, user_text: str) -> tuple[str, Usage]:
        """Single question, no tools (used for cheap classification)."""
        t = self.conversation(system, [], user_text).step([])
        return t.text, t.usage


# ----------------------------------------------------------------------------- Claude Messages API (default)
class AnthropicClient(ModelClient):
    provider = "anthropic"

    def __init__(self, model: str | None = None, client: Any = None, max_output_tokens: int = 4000, timeout: float = 90,
                 cache: bool = True):
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.max_output_tokens = max_output_tokens
        self.cache = cache
        if client is None:
            import anthropic  # pip install anthropic
            client = anthropic.Anthropic(timeout=timeout, max_retries=0)
        self.client = client

    def conversation(self, system, history, user_text):
        return _AnthropicConversation(self, system, history, user_text)


class _AnthropicConversation(Conversation):
    def __init__(self, owner: AnthropicClient, system: str, history: list[dict], user_text: str):
        self.o, self.system = owner, system
        self.messages: list = [{"role": h["role"], "content": h["content"]} for h in history]
        self.messages.append({"role": "user", "content": user_text})

    def _request(self, tools: list[dict]) -> dict:
        system_block = {"type": "text", "text": self.system}
        if self.o.cache:
            system_block["cache_control"] = {"type": "ephemeral"}
        kwargs = dict(model=self.o.model, max_tokens=self.o.max_output_tokens, system=[system_block],
                      messages=self._messages_for_request())
        if tools:
            spec = [dict(name=t["name"], description=t["description"], input_schema=t["input_schema"]) for t in tools]
            if self.o.cache:
                spec[-1]["cache_control"] = {"type": "ephemeral"}   # caches every tool up to and including this one
            kwargs["tools"] = spec
        return kwargs

    def _messages_for_request(self) -> list:
        """Mark the end of the conversation so far as cacheable. Each agent step re-sends the whole conversation;
        with this marker the next step reads it from cache (0.05x on Sonnet) instead of paying full price again.
        The marker goes on a copy, so the stored conversation never collects more than the 4 allowed markers."""
        msgs = list(self.messages)
        if not (self.o.cache and msgs):
            return msgs
        last = dict(msgs[-1])
        content = last["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else \
            [dict(b) if isinstance(b, dict) else b for b in content]
        if blocks and isinstance(blocks[-1], dict):
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        last["content"] = blocks
        msgs[-1] = last
        return msgs

    def step(self, tools):
        kwargs = self._request(tools)
        resp = with_retries(lambda: self.o.client.messages.create(**kwargs))
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        self.messages.append({"role": "assistant", "content": resp.content})   # keeps thinking + tool_use blocks
        text = "" if calls else "".join(b.text for b in resp.content if b.type == "text")
        u = getattr(resp, "usage", None)
        it, ot = getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0
        cw, cr = getattr(u, "cache_creation_input_tokens", 0) or 0, getattr(u, "cache_read_input_tokens", 0) or 0
        usage = Usage(it, ot, 1, cw, cr, price_call(self.o.model, it, ot, cw, cr))
        return ModelTurn(text, calls, usage)

    def add_tool_results(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": out} for c, out in results]})

    def add_user(self, text):
        self.messages.append({"role": "user", "content": text})


# ----------------------------------------------------------------------------- scripted test model
class ScriptedModel(ModelClient):
    """A fake model driven by a policy function, so the agent's control flow can be tested without a network."""
    provider = "scripted"

    def __init__(self, policy: Callable[["ScriptedConversation", list[dict]], ModelTurn], model: str = "scripted"):
        self.policy, self.model = policy, model
        self.conversations: list[ScriptedConversation] = []

    def conversation(self, system, history, user_text):
        c = ScriptedConversation(self, system, history, user_text)
        self.conversations.append(c)
        return c


@dataclass
class ScriptedConversation(Conversation):
    owner: ScriptedModel
    system: str
    history: list
    user_text: str
    tool_results: list = field(default_factory=list)
    followups: list = field(default_factory=list)
    offered: list = field(default_factory=list)
    steps: int = 0

    def step(self, tools):
        self.offered.append([t["name"] for t in tools])
        turn = self.owner.policy(self, tools)
        self.steps += 1
        size = len(self.system) + len(self.user_text) + sum(len(o) for _, o in self.tool_results)
        it, ot = size // 4, len(turn.text) // 4 + 20
        turn.usage = Usage(it, ot, 1, 0, 0, price_call(self.owner.model, it, ot))
        return turn

    def add_tool_results(self, results):
        self.tool_results += results

    def add_user(self, text):
        self.followups.append(text)


# ----------------------------------------------------------------------------- optional: OpenAI Responses API
class OpenAIResponsesClient(ModelClient):
    """Optional comparison adapter for the course's OpenAI examples. Needs `pip install openai`. No prices are
    recorded for it, so its cost shows as unknown."""
    provider = "openai"

    def __init__(self, model: str | None = None, client: Any = None, max_output_tokens: int = 4000, timeout: float = 60):
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5")
        self.max_output_tokens = max_output_tokens
        if client is None:
            from openai import OpenAI
            client = OpenAI(timeout=timeout, max_retries=0)
        self.client = client

    def conversation(self, system, history, user_text):
        return _OpenAIConversation(self, system, history, user_text)


class _OpenAIConversation(Conversation):
    def __init__(self, owner: OpenAIResponsesClient, system: str, history: list[dict], user_text: str):
        self.o, self.system = owner, system
        self.input: list = [{"role": h["role"], "content": h["content"]} for h in history]
        self.input.append({"role": "user", "content": user_text})

    def step(self, tools):
        kwargs = dict(model=self.o.model, instructions=self.system, input=list(self.input),
                      max_output_tokens=self.o.max_output_tokens)
        if tools:
            kwargs["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                                "parameters": t["input_schema"]} for t in tools]
        resp = with_retries(lambda: self.o.client.responses.create(**kwargs))
        calls = [ToolCall(i.call_id, i.name, json.loads(i.arguments or "{}")) for i in resp.output
                 if getattr(i, "type", None) == "function_call"]
        self.input += list(resp.output)
        text = "" if calls else (getattr(resp, "output_text", "") or "")
        u = getattr(resp, "usage", None)
        return ModelTurn(text, calls, Usage(getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0, 1,
                                            0, 0, None))

    def add_tool_results(self, results):
        self.input += [{"type": "function_call_output", "call_id": c.id, "output": out} for c, out in results]

    def add_user(self, text):
        self.input.append({"role": "user", "content": text})


# ----------------------------------------------------------------------------- factory
def provider_from_env() -> str:
    """Claude unless AGENT_PROVIDER says otherwise; empty string when no key is set (the app runs without AI)."""
    p = os.environ.get("AGENT_PROVIDER", "").lower()
    if p:
        return p
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else ""


def make_model(role: str = "answer") -> ModelClient | None:
    """role='answer' -> Sonnet (reasoning + tools); role='router' -> Haiku (cheap classification)."""
    p = provider_from_env()
    if p == "anthropic":
        if role == "router":
            return AnthropicClient(model=os.environ.get("ANTHROPIC_ROUTER_MODEL", DEFAULT_ROUTER_MODEL), max_output_tokens=50)
        return AnthropicClient()
    if p == "openai":
        return OpenAIResponsesClient()
    return None
