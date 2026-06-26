import json
from pathlib import Path

from senda_argus_hooks import register, shutdown
from senda_argus_hooks.integrations import instrument_rag


def _events(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class FakeRetriever:
    name = "security_kb_retriever"

    def retrieve(self, query):
        return [{"node_id": "chunk-1", "doc_id": "doc-1", "score": 0.9}]


class FakeEmbedModel:
    model_name = "local-test-embedding"

    def get_text_embedding(self, text):
        return [0.1, 0.2, 0.3]

    def get_text_embeddings(self, texts):
        return [[0.1, 0.2], [0.3, 0.4]]


class FakeQueryEngine:
    name = "security_query_engine"

    def query(self, query):
        return {"answer": "ok"}


def test_instrument_rag_patches_components_with_one_call(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    retriever = FakeRetriever()
    embed_model = FakeEmbedModel()
    query_engine = FakeQueryEngine()

    register(project="test", exporters=[{"type": "jsonl", "path": str(path)}], capture_arguments=True, capture_result=False)

    handle = instrument_rag(
        retriever=retriever,
        embed_model=embed_model,
        query_engine=query_engine,
        framework="llamaindex",
        retriever_type="vector",
        index_name="security_knowledge_base",
        vector_store="faiss",
        top_k=1,
        provider="local",
    )

    assert handle.installed()
    assert retriever.retrieve("CVE-2024-3094")
    assert embed_model.get_text_embedding("CVE-2024-3094") == [0.1, 0.2, 0.3]
    assert query_engine.query("CVE-2024-3094") == {"answer": "ok"}

    shutdown()

    assert [event["event_type"] for event in _events(path)] == [
        "retrieval.requested",
        "retrieval.completed",
        "embedding.requested",
        "embedding.completed",
        "rag.query.started",
        "rag.query.completed",
    ]


def test_register_can_instrument_rag_components(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    retriever = FakeRetriever()

    result = register(
        project="test",
        exporters=[{"type": "jsonl", "path": str(path)}],
        rag={
            "retriever": retriever,
            "framework": "llamaindex",
            "retriever_type": "vector",
            "index_name": "security_knowledge_base",
        },
    )

    assert result["instrumentors"]["rag"] is True
    retriever.retrieve("CVE-2024-3094")
    shutdown()

    assert [event["event_type"] for event in _events(path)] == ["retrieval.requested", "retrieval.completed"]
