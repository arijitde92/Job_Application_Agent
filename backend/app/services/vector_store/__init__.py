"""
app.services.vector_store
-------------------------
Vector-store and embedding infrastructure for the GitHub RAG pipeline.

Two independent providers, one module each:

* :mod:`voyage_client`  — Voyage AI embeddings (``voyage-code-3``) and the
  second-stage reranker (``rerank-2.5-lite``).
* :mod:`weaviate_store` — Weaviate Cloud collection holding the repo chunks
  with self-provided (bring-your-own) vectors.

Both are consumed by ``app.services.extractors.github_extractor``, which owns
the orchestration (load → split → embed → upsert, and embed → search → rerank).
Neither module imports the other, so either provider can be swapped without
touching the extractor's counterpart.
"""
