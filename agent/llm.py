"""Thin wrapper around the Anthropic Messages API with server-side web search.

Claude runs the web searches itself (server tool), so there is no tool loop to
manage here beyond resuming `pause_turn` responses on long research turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .jsonutil import JSONExtractionError, extract_json

# Haiku keeps API costs low. To upgrade later, change this one line
# (e.g. "claude-sonnet-5-5") or set the MODEL secret / env var.
DEFAULT_MODEL = "claude-haiku-5-5"
MODEL_LABEL = "Claude Haiku 5.5"
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"

# Current models think before answering, and thinking + web-search results count
# against max_tokens. Small budgets get used up before any JSON is written, so every
# call gets at least this much room (you only pay for tokens actually generated).
MIN_MAX_TOKENS = 16000
# "low" effort = shorter thinking: cheaper and faster, plenty for research/extraction.
EFFORT = "low"

# USD per million tokens (input, output), for the on-screen cost estimate only.
# Verify at https://claude.com/pricing. Haiku 5.5 rates apply to prompts up to 100K tokens.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
PRICE_PER_SEARCH = 0.01  # $10 per 1,000 web searches


def new_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "searches": 0}


def add_usage(*usages: dict[str, int]) -> dict[str, int]:
    total = new_usage()
    for u in usages:
        for k in total:
            total[k] += int((u or {}).get(k, 0) or 0)
    return total


def cost_of(usage: dict[str, int], model: str = DEFAULT_MODEL) -> float:
    p_in, p_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    return (
        usage.get("input_tokens", 0) / 1e6 * p_in
        + usage.get("output_tokens", 0) / 1e6 * p_out
        + usage.get("searches", 0) * PRICE_PER_SEARCH
    )


@dataclass
class LLMResult:
    data: Any
    sources: list[dict[str, str]] = field(default_factory=list)
    raw_text: str = ""
    searches: int = 0
    usage: dict[str, int] = field(default_factory=new_usage)


class LLMError(RuntimeError):
    pass


class ResearchLLM:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, use_web: bool = True):
        if not api_key:
            raise LLMError("Missing Anthropic API key.")
        import anthropic  # imported lazily so pure modules/tests don't need it

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=600)
        self.model = model
        self.use_web = use_web

    # ------------------------------------------------------------------ #
    def run_json(
        self,
        prompt: str,
        system: str,
        *,
        max_tokens: int = 6000,
        max_searches: int = 4,
        web: bool | None = None,
    ) -> LLMResult:
        """Send a prompt, let Claude search if enabled, and parse a JSON reply."""
        use_web = self.use_web if web is None else web
        tools = (
            [{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_searches}]
            if use_web
            else []
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        texts: list[str] = []
        sources: dict[str, dict[str, str]] = {}
        searches = 0
        usage = new_usage()
        max_tokens = max(max_tokens, MIN_MAX_TOKENS)
        stop_reason = None

        for _ in range(4):  # resume up to 3 pause_turns
            kwargs: dict[str, Any] = dict(
                model=self.model, max_tokens=max_tokens, system=system, messages=messages,
                output_config={"effort": EFFORT},
            )
            if tools:
                kwargs["tools"] = tools
            try:
                resp = self.client.messages.create(**kwargs)
            except self._anthropic.APIStatusError as e:  # surface readable errors
                raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e
            except self._anthropic.APIConnectionError as e:
                raise LLMError(f"Could not reach the Anthropic API: {e}") from e

            self._add_resp_usage(usage, resp)
            for block in resp.content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    texts.append(block.text)
                    for c in getattr(block, "citations", None) or []:
                        url = getattr(c, "url", None)
                        if url:
                            sources.setdefault(url, {"url": url, "title": getattr(c, "title", "") or url})
                elif btype == "server_tool_use":
                    searches += 1
                elif btype == "web_search_tool_result":
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for r in content:
                            url = getattr(r, "url", None)
                            if url:
                                sources.setdefault(url, {"url": url, "title": getattr(r, "title", "") or url})

            stop_reason = resp.stop_reason
            if stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break

        raw = "\n".join(texts)
        if stop_reason == "refusal":
            raise LLMError("The model declined this request. Try rephrasing or a different company.")
        if not raw.strip() and stop_reason == "max_tokens":
            raise LLMError(
                f"The model used its whole {max_tokens}-token budget before answering. "
                "Try again, or raise MIN_MAX_TOKENS in agent/llm.py."
            )
        try:
            data = extract_json(raw)
        except JSONExtractionError:
            data = self._repair_json(raw, max_tokens, usage)
        return LLMResult(data=data, sources=list(sources.values()), raw_text=raw, searches=searches, usage=usage)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _add_resp_usage(usage: dict[str, int], resp: Any) -> None:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        usage["input_tokens"] += (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
        usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        if stu is not None:
            usage["searches"] += getattr(stu, "web_search_requests", 0) or 0

    def _repair_json(self, raw: str, max_tokens: int, usage: dict[str, int]) -> Any:
        """One cheap retry: ask the model to re-emit its answer as valid JSON."""
        if not raw.strip():
            raise LLMError("The model returned no text. Try again or switch models.")
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            output_config={"effort": EFFORT},
            system="You convert text into one valid JSON object. Output only JSON, no prose, no code fences.",
            messages=[{"role": "user", "content": f"Convert this into the single JSON object it describes:\n\n{raw[-30000:]}"}],
        )
        self._add_resp_usage(usage, resp)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            return extract_json(text)
        except JSONExtractionError as e:
            raise LLMError("Could not parse the model's response as JSON.") from e
