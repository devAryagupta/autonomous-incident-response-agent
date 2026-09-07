from __future__ import annotations

import json

import httpx

from incident_agent.contracts import LLMSuggestionRequest
from incident_agent.llm.openai_provider import (
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_BASE_URL,
    OpenAILLMSuggestionProvider,
)


def _request(stage: str) -> LLMSuggestionRequest:
    return LLMSuggestionRequest(
        incident_id="inc-openai-1",
        stage=stage,  # type: ignore[arg-type]
        diagnosis_category="OOMKilled",
        diagnosis_summary="Container OOMKilled during startup",
        hypotheses=["Memory leak", "Memory limit too low"],
        evidence=["Back-off restarting failed container"],
        max_suggestions=3,
    )


def test_openai_provider_returns_empty_when_api_key_missing() -> None:
    provider = OpenAILLMSuggestionProvider(api_key=None, client=httpx.Client())
    out = provider.suggest(_request("hypothesize"))
    assert out.suggestions == []
    assert "llm_api_key_missing" in out.warnings


def test_openai_provider_returns_empty_on_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, json={"error": {"message": "boom"}})

    provider = OpenAILLMSuggestionProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    out = provider.suggest(_request("hypothesize"))
    assert out.suggestions == []
    assert any(item.startswith("llm_error:") for item in out.warnings)


def test_openai_provider_parses_hypothesis_suggestions() -> None:
    content = json.dumps(
        {
            "suggestions": [
                {
                    "summary": "Memory leak is likely",
                    "rationale": "Repeated restart cycles and memory growth indicators.",
                }
            ]
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            },
        )

    provider = OpenAILLMSuggestionProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    out = provider.suggest(_request("hypothesize"))
    assert len(out.suggestions) == 1
    assert out.suggestions[0].kind == "hypothesis"
    assert out.suggestions[0].summary == "Memory leak is likely"
    assert out.prompt_tokens == 123
    assert out.completion_tokens == 45


def test_openai_provider_parses_evidence_suggestions() -> None:
    content = json.dumps(
        {
            "suggestions": [
                {
                    "type": "log",
                    "query": "previous_container_logs",
                    "target": "deployment/demo-app",
                    "rationale": "Differentiate app crash from config failure.",
                }
            ]
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": content}}]},
        )

    provider = OpenAILLMSuggestionProvider(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    out = provider.suggest(_request("collect_evidence"))
    assert len(out.suggestions) == 1
    assert out.suggestions[0].kind == "evidence"
    assert out.suggestions[0].metadata["query"] == "previous_container_logs"
    assert out.suggestions[0].metadata["type"] == "log"
    assert out.suggestions[0].metadata["target"] == "deployment/demo-app"


def test_openai_provider_rejects_non_integrated_stage() -> None:
    provider = OpenAILLMSuggestionProvider(api_key="test-key", client=httpx.Client())
    out = provider.suggest(_request("plan_fix"))
    assert out.suggestions == []
    assert "unsupported_stage:plan_fix" in out.warnings


def test_from_env_prefers_openrouter_qwen(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAILLMSuggestionProvider.from_env()
    assert provider._provider_name == "openrouter"
    assert provider._model == DEFAULT_OPENROUTER_MODEL
    assert provider._base_url == OPENROUTER_BASE_URL
    assert provider._structured_output == "json_object"


def test_provider_parses_fenced_json_from_qwen_style_response() -> None:
    content = """```json
{"suggestions":[{"summary":"Memory leak is likely","rationale":"Restart loop."}]}
```"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": content}}]},
        )

    provider = OpenAILLMSuggestionProvider(
        api_key="or-test-key",
        base_url=OPENROUTER_BASE_URL,
        model=DEFAULT_OPENROUTER_MODEL,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    out = provider.suggest(_request("hypothesize"))
    assert out.provider == "openrouter"
    assert out.model == DEFAULT_OPENROUTER_MODEL
    assert out.suggestions[0].summary == "Memory leak is likely"
