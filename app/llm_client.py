"""Tiny LLM client for the hybrid reasoning engine (Step 3).

The service optionally calls an OpenAI-compatible chat-completions endpoint.
When no API key / base URL is configured, ``LLMClient.complete`` returns
``None`` and the caller (reasoning.py) falls back to the rule path. This
keeps the service fully functional on the 10 samples with zero LLM calls.

Features:
  - Single-call by default; one retry on JSON parse failure with a strict
    repair prompt ("respond ONLY with valid JSON, schema: ...").
  - Hard timeout so a stalled LLM cannot block the request past SLA.
  - All inputs and outputs pass through the rule-based verifier downstream;
    we never trust an LLM output blindly.

Environment variables (all optional):
  LLM_API_KEY      - bearer token (or "dummy" to disable calls)
  LLM_BASE_URL     - e.g. https://api.openai.com/v1 or https://openrouter.ai/api/v1
  LLM_MODEL        - e.g. gpt-4o-mini or openai/gpt-4o-mini (OpenRouter slug)
  LLM_TIMEOUT_S    - per-call timeout in seconds (default 8)
  LLM_ENABLED      - "0" to force-disable regardless of other env vars

OpenRouter extras (optional, for attribution/ranking):
  OPENROUTER_APP_NAME  - sent as X-Title header
  OPENROUTER_APP_URL   - sent as HTTP-Referer header
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import urllib.error
import urllib.request


logger = logging.getLogger("app.llm_client")


# ---- minimal JSON fence extractor -------------------------------------------


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Find a JSON object in the response. Tries fenced, then plain."""
    if not text:
        return None
    # Try fenced first (```json { ... } ```).
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try the entire response.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find the first {...} block (greedy across lines).
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


# ---- HTTP client -------------------------------------------------------------


@dataclass
class LLMConfig:
    api_key: Optional[str]
    base_url: str
    model: str
    timeout_s: float
    enabled: bool


def _load_config() -> LLMConfig:
    enabled_env = os.environ.get("LLM_ENABLED", "1").lower() not in {"0", "false", "no"}
    api_key = os.environ.get("LLM_API_KEY") or None
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    # OpenRouter requires "provider/model" slugs (e.g. "openai/gpt-4o-mini").
    # We default to the OpenAI-compatible path but accept whatever the user sets.
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    timeout_s = float(os.environ.get("LLM_TIMEOUT_S", "8"))
    enabled = enabled_env and bool(api_key) and api_key.lower() != "dummy"
    return LLMConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_s=timeout_s,
        enabled=enabled,
    )


def _provider_headers() -> Dict[str, str]:
    """Extra headers for OpenRouter attribution. Harmless on other providers."""
    h: Dict[str, str] = {}
    name = os.environ.get("OPENROUTER_APP_NAME")
    url = os.environ.get("OPENROUTER_APP_URL")
    if name:
        h["X-Title"] = name
    if url:
        h["HTTP-Referer"] = url
    return h


class LLMClient:
    """OpenAI-compatible chat-completions client with retry/repair."""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or _load_config()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_retries: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Return parsed JSON dict or None on failure.

        Strategy:
          1. Call once.
          2. If response is not valid JSON, send a strict repair prompt once
             more (max_retries=1 by default). If still invalid -> None.
        """
        if not self.enabled:
            return None

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        data = self._call(messages)
        if data is None:
            return None
        content = self._extract_content(data)
        parsed = _extract_json(content) if content else None

        for attempt in range(max_retries):
            if parsed is not None:
                return parsed
            # Repair: ask the model to output ONLY valid JSON.
            repair_msg = (
                "Your previous reply could not be parsed as JSON. "
                "Reply with ONLY a single JSON object matching the schema. "
                "No prose, no markdown, no code fences. Schema: "
                "{"
                '"evidence_verdict": string, '
                '"case_type": string, '
                '"severity": string, '
                '"department": string, '
                '"agent_summary": string, '
                '"recommended_next_action": string, '
                '"customer_reply": string, '
                '"human_review_required": boolean, '
                '"confidence": number, '
                '"reason_codes": array of strings'
                "}"
            )
            messages.append(
                {"role": "assistant", "content": content or ""}
            )
            messages.append({"role": "user", "content": repair_msg})
            data = self._call(messages)
            if data is None:
                return None
            content = self._extract_content(data)
            parsed = _extract_json(content) if content else None

        return parsed

    # ---- internals ----------------------------------------------------------

    def _call(self, messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        url = f"{self.config.base_url}/chat/completions"
        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 700,
        }
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        # OpenRouter-friendly attribution headers; ignored by other providers.
        headers.update(_provider_headers())
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            logger.warning("LLM call failed: %s (%.2fs)", e, time.time() - t0)
            return None
        except Exception as e:  # defensive: never let the LLM crash the request
            logger.warning("LLM call unexpected error: %s", e)
            return None
        return payload

    @staticmethod
    def _extract_content(payload: Dict[str, Any]) -> Optional[str]:
        try:
            choices = payload["choices"]
            return choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
