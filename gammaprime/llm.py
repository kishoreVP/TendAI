"""Inference providers behind one interface.

  provider.generate(messages, schema=None, task=...) -> (parsed_json, metadata)

* GroqProvider: Groq API, GPT-OSS models (openai/gpt-oss-120b, openai/gpt-oss-20b) with
  strict json_schema structured outputs (no streaming, as documented by Groq).
* LocalProvider: any OpenAI-compatible local server, intended for llama.cpp `llama-server`
  running a small Qwen GGUF on a Jetson Orin Nano (see docs/JETSON.md).

Routing: GP_TASK_ROUTING maps task -> provider name, GP_DEFAULT_PROVIDER is the fallback
("none" disables the LLM). Bidder documents never go to a cloud provider unless
GP_ALLOW_CLOUD_FOR_BIDDER_DOCS=true. There is no silent fallback to another provider/model.
"""
import json
import time

import httpx
import jsonschema

from . import config


class LLMError(RuntimeError):
    pass


class InferenceProvider:
    name = "base"
    cloud = False
    model = None

    def generate(self, messages, schema=None, task=None):
        raise NotImplementedError

    def _validate(self, text, schema):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"model returned invalid JSON: {e}") from e
        if schema:
            try:
                jsonschema.validate(data, schema)
            except jsonschema.ValidationError as e:
                raise LLMError(f"model output failed schema validation: {e.message}") from e
        return data


class _OpenAICompatible(InferenceProvider):
    """Shared request/retry logic for OpenAI-style /chat/completions endpoints."""
    base_url = None
    retries = 2

    def _headers(self):
        return {}

    def generate(self, messages, schema=None, task=None):
        body = {"model": self.model, "messages": messages, "temperature": 0}
        if schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "strict": True, "schema": schema}}
        last = None
        for attempt in range(self.retries + 1):
            t0 = time.perf_counter()
            try:
                r = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers(),
                               timeout=config.LLM_TIMEOUT_S)
            except httpx.HTTPError as e:
                last = LLMError(f"{self.name}: request failed: {e}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = LLMError(f"{self.name}: HTTP {r.status_code} (rate limited or server error)")
                time.sleep(float(r.headers.get("retry-after", 2 ** attempt)))
                continue
            if r.status_code == 404 or "model_not_found" in r.text:
                raise LLMError(f"{self.name}: model '{self.model}' is not available")
            if r.status_code >= 400:
                raise LLMError(f"{self.name}: HTTP {r.status_code}: {r.text[:300]}")
            payload = r.json()
            content = payload["choices"][0]["message"]["content"] or ""
            meta = {"provider": self.name, "model": payload.get("model", self.model), "task": task,
                    "latency_s": round(time.perf_counter() - t0, 3), "usage": payload.get("usage", {}),
                    "attempt": attempt + 1}
            try:
                data = self._validate(content, schema) if schema else content
            except LLMError as e:  # controlled retry on invalid structured output
                last = e
                continue
            return data, meta
        raise last


class GroqProvider(_OpenAICompatible):
    name = "groq"
    cloud = True
    base_url = "https://api.groq.com/openai/v1"

    def __init__(self, model=None):
        if not config.GROQ_API_KEY:
            raise LLMError("GROQ_API_KEY is not set; Groq provider unavailable")
        self.model = model or config.GROQ_MODEL

    def _headers(self):
        return {"Authorization": f"Bearer {config.GROQ_API_KEY}"}


class LocalProvider(_OpenAICompatible):
    name = "local"
    cloud = False

    def __init__(self, url=None, model=None):
        self.base_url = (url or config.LOCAL_LLM_URL).rstrip("/")
        self.model = model or config.LOCAL_LLM_MODEL

    def available(self):
        try:
            return httpx.get(f"{self.base_url}/models", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False


PROVIDERS = {"groq": GroqProvider, "local": LocalProvider}


def get_provider(task, contains_bidder_data):
    """Provider configured for this task, or None when the LLM is disabled for it."""
    name = config.TASK_ROUTING.get(task, config.DEFAULT_PROVIDER)
    if name in (None, "", "none"):
        return None
    if name not in PROVIDERS:
        raise LLMError(f"unknown provider '{name}' for task '{task}'")
    provider = PROVIDERS[name]()
    if provider.cloud and contains_bidder_data and not config.ALLOW_CLOUD_FOR_BIDDER_DOCS:
        raise LLMError(f"task '{task}' would send bidder documents to cloud provider '{name}', but "
                       "GP_ALLOW_CLOUD_FOR_BIDDER_DOCS is not true")
    return provider
