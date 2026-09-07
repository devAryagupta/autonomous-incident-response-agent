"""Minimal OpenAI-compatible provider for advisory suggestions.

Supports native OpenAI and OpenRouter (same Chat Completions shape).
Scope is intentionally narrow:
- hypothesize: suggest plausible hypotheses
- collect_evidence: suggest discriminating evidence requests

Any API/model/parsing failure returns an empty advisory response so incident
handling remains deterministic and uninterrupted.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Literal

import httpx

from incident_agent.contracts import LLMSuggestion, LLMSuggestionRequest, LLMSuggestionResponse

_UNSET = object()
_SUPPORTED_STAGES = frozenset({"hypothesize", "collect_evidence"})
_EVIDENCE_TYPES = ("metric", "log", "event", "describe")

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENROUTER_MODEL = "qwen/qwen-2.5-7b-instruct"

StructuredOutput = Literal["json_schema", "json_object", "prompt"]


class OpenAILLMSuggestionProvider:
    """Single-model OpenAI-compatible provider that returns advisory suggestions."""

    def __init__(
        self,
        *,
        api_key: str | None | object = _UNSET,
        model: str | None = None,
        base_url: str = OPENAI_BASE_URL,
        timeout_seconds: float = 15.0,
        max_suggestions: int | None = None,
        client: httpx.Client | None = None,
        extra_headers: dict[str, str] | None = None,
        structured_output: StructuredOutput | None = None,
        provider_name: str | None = None,
    ) -> None:
        if api_key is _UNSET:
            self._api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        else:
            self._api_key = api_key if isinstance(api_key, str) and api_key.strip() else None
        self._base_url = base_url.rstrip("/")
        self._is_openrouter = "openrouter.ai" in self._base_url
        self._model = model or (
            DEFAULT_OPENROUTER_MODEL if self._is_openrouter else DEFAULT_OPENAI_MODEL
        )
        self._timeout_seconds = timeout_seconds
        self._max_suggestions = max_suggestions
        self._extra_headers = dict(extra_headers or {})
        self._structured_output = structured_output or (
            "json_object" if self._is_openrouter else "json_schema"
        )
        self._provider_name = provider_name or (
            "openrouter" if self._is_openrouter else "openai"
        )
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @classmethod
    def from_env(cls, **overrides: Any) -> OpenAILLMSuggestionProvider:
        """Build from ``.env``: OpenRouter wins when ``OPENROUTER_API_KEY`` is set."""
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

        if os.getenv("OPENROUTER_API_KEY"):
            defaults: dict[str, Any] = {
                "api_key": os.getenv("OPENROUTER_API_KEY"),
                "base_url": os.getenv("LLM_BASE_URL") or OPENROUTER_BASE_URL,
                "model": os.getenv("LLM_MODEL") or DEFAULT_OPENROUTER_MODEL,
                "extra_headers": {
                    "HTTP-Referer": "https://github.com/AryaNamekart/autonomous-incident-response-agent",
                    "X-Title": "incident-response-agent",
                },
                "structured_output": "json_object",
                "provider_name": "openrouter",
            }
        elif os.getenv("OPENAI_API_KEY"):
            defaults = {
                "api_key": os.getenv("OPENAI_API_KEY"),
                "base_url": os.getenv("LLM_BASE_URL") or OPENAI_BASE_URL,
                "model": os.getenv("LLM_MODEL") or DEFAULT_OPENAI_MODEL,
                "structured_output": "json_schema",
                "provider_name": "openai",
            }
        else:
            defaults = {"api_key": None}
        defaults.update(overrides)
        return cls(**defaults)

    def suggest(self, request: LLMSuggestionRequest) -> LLMSuggestionResponse:
        started = time.perf_counter()
        cap = _effective_cap(request.max_suggestions, self._max_suggestions)

        if request.stage not in _SUPPORTED_STAGES:
            return _empty_response(
                request=request,
                provider=self._provider_name,
                model=self._model,
                warnings=[f"unsupported_stage:{request.stage}"],
                started=started,
            )
        if not self._api_key:
            return _empty_response(
                request=request,
                provider=self._provider_name,
                model=self._model,
                warnings=["llm_api_key_missing"],
                started=started,
            )

        try:
            payload = self._client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=_chat_payload(
                    request,
                    model=self._model,
                    cap=cap,
                    structured_output=self._structured_output,
                ),
                timeout=self._timeout_seconds,
            )
            payload.raise_for_status()
            raw = payload.json()
            content = _extract_content_text(raw)
            decoded = _decode_suggestions_json(content)
            suggestions = _to_suggestions(
                request=request,
                decoded=decoded,
                cap=cap,
                provider=self._provider_name,
            )
            usage = raw.get("usage") if isinstance(raw, dict) else {}
            return LLMSuggestionResponse(
                provider=self._provider_name,
                model=self._model,
                stage=request.stage,
                suggestions=suggestions,
                warnings=[],
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=_as_non_negative_int((usage or {}).get("prompt_tokens")),
                completion_tokens=_as_non_negative_int((usage or {}).get("completion_tokens")),
            )
        except Exception as exc:
            return _empty_response(
                request=request,
                provider=self._provider_name,
                model=self._model,
                warnings=[f"llm_error:{type(exc).__name__}"],
                started=started,
            )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self._extra_headers)
        return headers


def _empty_response(
    *,
    request: LLMSuggestionRequest,
    provider: str,
    model: str,
    warnings: list[str],
    started: float,
) -> LLMSuggestionResponse:
    return LLMSuggestionResponse(
        provider=provider,
        model=model,
        stage=request.stage,
        suggestions=[],
        warnings=warnings,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _chat_payload(
    request: LLMSuggestionRequest,
    *,
    model: str,
    cap: int,
    structured_output: StructuredOutput,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": _system_prompt(request.stage)},
            {"role": "user", "content": _user_prompt(request, cap=cap)},
        ],
    }
    if structured_output == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "llm_suggestions",
                "strict": True,
                "schema": _response_schema(request.stage, cap=cap),
            },
        }
    elif structured_output == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _system_prompt(stage: str) -> str:
    if stage == "hypothesize":
        return (
            "You are assisting deterministic incident diagnosis. "
            "Suggest plausible hypotheses only. Do not suggest remediation, "
            "execution, routing, approvals, or policy decisions. "
            'Reply with JSON only: {"suggestions":[{"summary":"...","rationale":"..."}]}'
        )
    return (
        "You are assisting deterministic evidence planning. "
        "Suggest evidence requests that discriminate between hypotheses. "
        "Do not suggest remediation, execution, routing, approvals, or policy decisions. "
        "Allowed evidence types: metric, log, event, describe. "
        "Reply with JSON only: "
        '{"suggestions":[{"type":"log","query":"...","target":"...","rationale":"..."}]}'
    )


def _user_prompt(request: LLMSuggestionRequest, *, cap: int) -> str:
    evidence_lines = "\n".join(f"- {line}" for line in request.evidence) or "- <none>"
    hypotheses = "\n".join(f"- {name}" for name in request.hypotheses) or "- <none>"
    if request.stage == "hypothesize":
        return (
            f"Incident: {request.incident_id}\n"
            f"Diagnosis category: {request.diagnosis_category or '<none>'}\n"
            f"Diagnosis summary: {request.diagnosis_summary or '<none>'}\n"
            "Observations:\n"
            f"{evidence_lines}\n\n"
            f"Task: Given these observations, suggest up to {cap} plausible hypotheses."
        )
    return (
        f"Incident: {request.incident_id}\n"
        f"Diagnosis category: {request.diagnosis_category or '<none>'}\n"
        f"Diagnosis summary: {request.diagnosis_summary or '<none>'}\n"
        "Current hypotheses:\n"
        f"{hypotheses}\n"
        "Observations:\n"
        f"{evidence_lines}\n\n"
        "Task: Given these hypotheses and observations, suggest evidence that would "
        "discriminate between them.\n"
        f"Allowed evidence target (use this exact value): {request.target_ref or '<workload>'}"
    )


def _response_schema(stage: str, *, cap: int) -> dict[str, Any]:
    if stage == "hypothesize":
        item = {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "rationale"],
            "properties": {
                "summary": {"type": "string"},
                "rationale": {"type": "string"},
            },
        }
    else:
        item = {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "query", "target", "rationale"],
            "properties": {
                "type": {"type": "string", "enum": list(_EVIDENCE_TYPES)},
                "query": {"type": "string"},
                "target": {"type": "string"},
                "rationale": {"type": "string"},
                "hypothesis_id": {"type": "string"},
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["suggestions"],
        "properties": {
            "suggestions": {
                "type": "array",
                "maxItems": cap,
                "items": item,
            }
        },
    }


def _extract_content_text(raw: dict[str, Any]) -> str:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenAI response missing choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("OpenAI response missing message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text") for part in content if isinstance(part, dict)]
        text = "".join(piece for piece in parts if isinstance(piece, str))
        if text:
            return text
    raise ValueError("OpenAI response missing textual content")


def _decode_suggestions_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("Model response is not a JSON object")
    decoded = json.loads(text[start : end + 1])
    if not isinstance(decoded, dict):
        raise ValueError("Model JSON root must be an object")
    return decoded


def _to_suggestions(
    *,
    request: LLMSuggestionRequest,
    decoded: dict[str, Any],
    cap: int,
    provider: str,
) -> list[LLMSuggestion]:
    rows = decoded.get("suggestions")
    if not isinstance(rows, list):
        raise ValueError("Model response must contain a suggestions array")
    items = rows[:cap]
    prefix = "or" if provider == "openrouter" else "openai"
    if request.stage == "hypothesize":
        return _to_hypothesis_suggestions(items, stage=request.stage, prefix=prefix)
    return _to_evidence_suggestions(items, stage=request.stage, prefix=prefix)


def _to_hypothesis_suggestions(
    items: list[Any],
    *,
    stage: str,
    prefix: str,
) -> list[LLMSuggestion]:
    out: list[LLMSuggestion] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError("Hypothesis suggestion item must be an object")
        summary = str(item.get("summary", "")).strip()
        rationale = str(item.get("rationale", "")).strip()
        out.append(
            LLMSuggestion(
                suggestion_id=f"{prefix}-h-{idx}",
                stage=stage,  # type: ignore[arg-type]
                kind="hypothesis",
                summary=summary,
                rationale=rationale,
            )
        )
    return out


def _to_evidence_suggestions(
    items: list[Any],
    *,
    stage: str,
    prefix: str,
) -> list[LLMSuggestion]:
    out: list[LLMSuggestion] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError("Evidence suggestion item must be an object")
        query = str(item.get("query", "")).strip()
        kind = str(item.get("type", "")).strip()
        target = str(item.get("target", "")).strip()
        rationale = str(item.get("rationale", "")).strip()
        metadata: dict[str, str] = {
            "query": query,
            "type": kind,
            "target": target,
        }
        hyp = item.get("hypothesis_id")
        if isinstance(hyp, str) and hyp.strip():
            metadata["hypothesis_id"] = hyp.strip()
        out.append(
            LLMSuggestion(
                suggestion_id=f"{prefix}-e-{idx}",
                stage=stage,  # type: ignore[arg-type]
                kind="evidence",
                summary=f"request {query}",
                rationale=rationale,
                target_ref=target,
                metadata=metadata,
            )
        )
    return out


def _effective_cap(request_max: int, provider_max: int | None) -> int:
    if provider_max is None:
        return int(request_max)
    return int(min(request_max, provider_max))


def _as_non_negative_int(value: object) -> int | None:
    if not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value
