"""Robust extraction of a JSON object from free-form LLM output."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)


class JSONExtractionError(ValueError):
    pass


def _balanced_slice(text: str, start: int) -> str | None:
    """Return the substring from `start` to its matching closing brace, string-aware."""
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _strip_trailing_commas(s: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", s)


def extract_json(text: str) -> Any:
    """Find and parse the most likely JSON payload in `text`.

    Order of attempts: fenced ```json blocks (last one wins), then the largest
    balanced {...} object in the text. Trailing commas are tolerated.
    """
    if not text or not text.strip():
        raise JSONExtractionError("Empty response")

    candidates: list[str] = []
    fenced = _FENCE_RE.findall(text)
    candidates.extend(reversed(fenced))

    # Balanced objects, largest first.
    objs = []
    for m in re.finditer(r"[{\[]", text):
        sl = _balanced_slice(text, m.start())
        if sl:
            objs.append(sl)
    objs.sort(key=len, reverse=True)
    candidates.extend(objs[:5])

    for cand in candidates:
        for attempt in (cand, _strip_trailing_commas(cand)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    raise JSONExtractionError("No valid JSON object found in model output")
