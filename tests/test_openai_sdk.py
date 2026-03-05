"""Exercise the installed SDK's HTTP/response contract without contacting OpenAI."""

import json

import httpx2
import pytest
from openai import OpenAI

from graph_rag import config
from graph_rag.embedding import embedder
from graph_rag.generation import answerer
from graph_rag.retrieval import RetrievalResult


@pytest.fixture
def sdk_client(monkeypatch):
    clients = []

    def connect(module, handler):
        client = OpenAI(
            api_key="test-only-not-a-real-key",
            base_url="https://codelens.invalid/v1",
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
            max_retries=0,
        )
        clients.append(client)
        # Only replace construction; serialization and response parsing use the real SDK.
        monkeypatch.setattr(module, "OpenAI", lambda **kwargs: client)

    yield connect
    for client in clients:
        client.close()


@pytest.mark.parametrize("model", ["text-embedding-3-small", "text-embedding-ada-002"])
def test_embedding_request_and_response_order(sdk_client, monkeypatch, model):
    calls = []

    def handle(request):
        assert request.url.path == "/v1/embeddings"
        body = json.loads(request.content)
        calls.append(body)
        return httpx2.Response(200, json={
            "object": "list", "model": model,
            "data": [
                {"object": "embedding", "index": 1, "embedding": [0.0, 1.0, 0.0]},
                {"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]},
            ],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        })

    sdk_client(embedder, handle)
    monkeypatch.setattr(config, "EMBEDDING_DIM", 3)
    assert embedder.Embedder(model=model).embed_texts(["first", "second"]) == [
        [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]
    ]
    assert len(calls) == 1
    assert calls[0]["model"] == model and calls[0]["input"] == ["first", "second"]
    assert calls[0].get("dimensions") == (3 if model.startswith("text-embedding-3-") else None)


@pytest.mark.parametrize("reject_temperature", [False, True])
def test_answer_response_and_temperature_fallback(sdk_client, reject_temperature):
    calls = []

    def handle(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        calls.append(body)
        if reject_temperature and len(calls) == 1:
            return httpx2.Response(400, json={"error": {
                "message": "temperature is not supported", "type": "invalid_request_error",
                "param": "temperature", "code": "unsupported_value",
            }})
        return httpx2.Response(200, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
            "model": "gpt-4.1-nano",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "See pricing.py:1.",
            }}],
        })

    sdk_client(answerer, handle)
    result = RetrievalResult(question="How is tax added?", context_text="pricing.py:1: add_tax")
    assert answerer.answer_question(result, model="gpt-4.1-nano") == "See pricing.py:1."
    assert len(calls) == (2 if reject_temperature else 1)
    assert calls[0]["temperature"] == 0
    assert "pricing.py:1" in calls[0]["messages"][1]["content"]
    if reject_temperature:
        assert "temperature" not in calls[1]


def test_authentication_failure_is_not_retried(sdk_client):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx2.Response(401, json={"error": {
            "message": "Invalid test key", "type": "invalid_request_error",
            "code": "invalid_api_key",
        }})

    sdk_client(embedder, handle)
    with pytest.raises(RuntimeError, match="will not be retried"):
        embedder.Embedder(model="text-embedding-3-small").embed_texts(["test"])
    assert calls == ["/v1/embeddings"]
