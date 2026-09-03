"""Chapter 1 smoke test: the package imports and exposes its version.

Deliberately minimal — later chapters add one suite per component
(ingestion, chunking, embeddings, retrieval, generation).
"""

import rag_agent


def test_version_surface() -> None:
    assert isinstance(rag_agent.__version__, str)
    assert rag_agent.__version__ == "0.1.0"


def test_package_importable() -> None:
    assert rag_agent.__doc__ is not None
    assert "RAG" in rag_agent.__doc__
