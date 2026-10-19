"""Milestone 7: inference providers. All HTTP is MOCKED (no API key / Jetson in CI); results
here say nothing about real model quality."""
import json

import httpx
import pytest

from gammaprime import config, llm, requirements
from gammaprime.db import Node


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def chat(content):
    return FakeResponse(200, {"model": "mock-model", "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 5}})


@pytest.fixture
def calls(monkeypatch):
    queue, seen = [], []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append({"url": url, "body": json, "headers": headers})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return queue, seen


def test_disabled_by_default():
    assert llm.get_provider("requirement_extraction", contains_bidder_data=False) is None


def test_missing_groq_key(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", None)
    monkeypatch.setattr(config, "DEFAULT_PROVIDER", "groq")
    with pytest.raises(llm.LLMError, match="GROQ_API_KEY"):
        llm.get_provider("requirement_extraction", contains_bidder_data=False)


def test_cloud_blocked_for_bidder_documents(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(config, "TASK_ROUTING", {"evidence_mapping": "groq"})
    with pytest.raises(llm.LLMError, match="GP_ALLOW_CLOUD_FOR_BIDDER_DOCS"):
        llm.get_provider("evidence_mapping", contains_bidder_data=True)
    monkeypatch.setattr(config, "ALLOW_CLOUD_FOR_BIDDER_DOCS", True)
    assert llm.get_provider("evidence_mapping", contains_bidder_data=True).name == "groq"


def test_routing_per_task(monkeypatch):
    monkeypatch.setattr(config, "TASK_ROUTING", {"evidence_mapping": "local"})
    assert isinstance(llm.get_provider("evidence_mapping", True), llm.LocalProvider)
    assert llm.get_provider("requirement_extraction", False) is None


def test_structured_output_request_and_metadata(calls, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    queue, seen = calls
    queue.append(chat('{"value": "1"}'))
    schema = {"type": "object", "additionalProperties": False, "required": ["value"], "properties": {"value": {"type": "string"}}}
    out, meta = llm.GroqProvider().generate([{"role": "user", "content": "x"}], schema=schema, task="t")
    body = seen[0]["body"]
    assert body["model"] == "openai/gpt-oss-120b" and body["response_format"]["json_schema"]["strict"] is True
    assert seen[0]["headers"]["Authorization"] == "Bearer test-key"
    assert out == {"value": "1"} and meta["provider"] == "groq" and meta["usage"]["total_tokens"] == 5


def test_rate_limit_then_success(calls):
    queue, _ = calls
    queue += [FakeResponse(429, headers={"retry-after": "0"}), chat('{"a": 1}')]
    out, meta = llm.LocalProvider().generate([], schema={"type": "object"})
    assert out == {"a": 1} and meta["attempt"] == 2


def test_invalid_structured_output_retried_then_fails(calls):
    queue, _ = calls
    queue += [chat("not json"), chat('{"wrong": 1}'), chat("{")]
    schema = {"type": "object", "required": ["value"]}
    with pytest.raises(llm.LLMError):
        llm.LocalProvider().generate([], schema=schema)


def test_timeout_and_unavailable_model(calls):
    queue, _ = calls
    queue += [httpx.ConnectTimeout("t")] * 3
    with pytest.raises(llm.LLMError, match="request failed"):
        llm.LocalProvider().generate([])
    queue.append(FakeResponse(404, {"error": {"code": "model_not_found"}}))
    with pytest.raises(llm.LLMError, match="not available"):
        llm.LocalProvider().generate([])


def test_llm_requirements_must_be_grounded(calls, session):
    """MOCKED model output: one item quotes the clause, one invents text - the invented one is dropped."""
    queue, seen = calls
    clause = Node(id=1, doc_id=1, kind="clause", ref="7", page=3, regions=[{"page": 3, "bbox": [0, 0, 1, 1]}],
                  text="The bidder shall have ISO 9001 certification valid on the date of bid opening.")
    item = {"title": "ISO 9001", "category": "quality_standards", "normalised_requirement": "Valid ISO 9001",
            "mandatory_status": "mandatory", "operator": "document", "value": None, "unit": None}
    queue.append(chat(json.dumps({"requirements": [
        {**item, "verbatim_quote": "shall have ISO 9001 certification"},
        {**item, "title": "fake", "verbatim_quote": "must have 20 years experience"}]})))

    class Case:
        name = "c"
    out = requirements.extract_with_llm(Case(), clause, llm.LocalProvider())
    assert [r.title for r in out] == ["ISO 9001"]
    assert out[0].extraction_method.startswith("llm:local/") and "ai_proposed" in out[0].flags[0]
    assert "untrusted" in seen[0]["body"]["messages"][0]["content"]
