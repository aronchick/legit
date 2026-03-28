"""Semantic embedding index — dense vector search for reviewer comments.

Replaces BM25 keyword matching with semantic similarity search using a
quantized ONNX sentence transformer (~22MB model, no torch dependency).

Built at `legit build` time from the reviewer's comment history. At review
time, PR diff hunks are embedded and compared against the pre-built index
to find semantically similar past comments — even when they use different
words (e.g., "nil safety" matches "null pointer check").
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

from legit.config import legit_path
from legit.models import RetrievalDocument

logger = logging.getLogger(__name__)

MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_DIR_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
MAX_SEQ_LENGTH = 128

_HAS_DEPS = True
try:
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer
except ImportError:
    _HAS_DEPS = False


def is_available() -> bool:
    """Check if embedding dependencies are installed."""
    return _HAS_DEPS


# ---------------------------------------------------------------------------
# Model loading (cached singleton)
# ---------------------------------------------------------------------------

_tokenizer = None
_session = None


def _model_dir() -> Path:
    return Path(os.path.expanduser("~/.cache/legit/models")) / MODEL_DIR_NAME


def _ensure_model() -> tuple:
    """Load or return cached tokenizer + ONNX session."""
    global _tokenizer, _session

    if _tokenizer is not None and _session is not None:
        return _tokenizer, _session

    if not _HAS_DEPS:
        raise ImportError(
            "Embedding dependencies not installed. "
            "Run: uv pip install legit[embeddings]"
        )

    model_path = _model_dir()
    onnx_path = model_path / "model.onnx"
    tokenizer_path = model_path / "tokenizer.json"

    if not onnx_path.exists() or not tokenizer_path.exists():
        logger.info("Downloading embedding model from %s...", MODEL_REPO)
        _download_model(model_path)

    _tokenizer = Tokenizer.from_file(str(tokenizer_path))
    _tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=MAX_SEQ_LENGTH)
    _tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)

    _session = ort.InferenceSession(
        str(onnx_path),
        providers=["CPUExecutionProvider"],
    )

    return _tokenizer, _session


def _download_model(model_path: Path) -> None:
    """Download the ONNX model files from HuggingFace Hub."""
    from huggingface_hub import hf_hub_download

    model_path.mkdir(parents=True, exist_ok=True)

    files = {
        "onnx/model_quint8_avx2.onnx": "model.onnx",
        "tokenizer.json": "tokenizer.json",
        "tokenizer_config.json": "tokenizer_config.json",
        "config.json": "config.json",
    }

    import shutil
    for remote, local in files.items():
        dl_path = hf_hub_download(
            repo_id=MODEL_REPO,
            filename=remote,
            local_dir=str(model_path),
        )
        target = model_path / local
        if not target.exists() and Path(dl_path).exists():
            shutil.copy2(dl_path, target)
        logger.info("  %s: %.1fMB", local, target.stat().st_size / 1024 / 1024)


# ---------------------------------------------------------------------------
# Embedding computation
# ---------------------------------------------------------------------------


def embed_texts(texts: list[str]) -> "np.ndarray":
    """Embed a batch of texts into 384-dim normalized vectors.

    Returns a numpy array of shape (len(texts), 384).
    """
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

    tokenizer, session = _ensure_model()

    encoded = tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

    outputs = session.run(None, {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "token_type_ids": token_type_ids,
    })

    # Mean pooling over token embeddings
    token_embeddings = outputs[0]
    mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
    embeddings = (token_embeddings * mask_expanded).sum(axis=1) / mask_expanded.sum(axis=1)

    # L2 normalize for cosine similarity via dot product
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    embeddings = embeddings / norms

    return embeddings.astype(np.float32)


# ---------------------------------------------------------------------------
# Embedding index
# ---------------------------------------------------------------------------


class EmbeddingIndex:
    """Pre-built embedding index for a reviewer's comment history."""

    def __init__(
        self,
        vectors: "np.ndarray",
        documents: list[dict],
        profile_name: str = "",
    ):
        self.vectors = vectors          # (N, 384)
        self.documents = documents      # metadata per vector
        self.profile_name = profile_name

    def search(self, query_texts: list[str], top_k: int = 10) -> list[dict]:
        """Find the most similar documents for a set of query texts.

        Returns deduplicated results across all queries, sorted by similarity.
        """
        if len(self.documents) == 0 or not query_texts:
            return []

        query_vecs = embed_texts(query_texts)

        # Cosine similarity: query_vecs (Q, 384) @ vectors.T (384, N) → (Q, N)
        similarities = query_vecs @ self.vectors.T

        # Collect top-K per query, then deduplicate
        seen_texts: set[str] = set()
        results: list[tuple[float, dict]] = []

        per_query_k = min(top_k, len(self.documents))
        for q_idx in range(len(query_texts)):
            top_indices = np.argsort(similarities[q_idx])[::-1][:per_query_k]
            for doc_idx in top_indices:
                sim = float(similarities[q_idx][doc_idx])
                doc = self.documents[doc_idx]
                text = doc.get("comment_text", "")
                if text not in seen_texts:
                    seen_texts.add(text)
                    results.append((sim, doc))

        # Sort by similarity descending, return top_k
        results.sort(key=lambda x: -x[0])
        return [doc for _, doc in results[:top_k]]

    def search_as_retrieval_docs(self, query_texts: list[str], top_k: int = 10) -> list[RetrievalDocument]:
        """Search and return results as RetrievalDocument objects."""
        raw = self.search(query_texts, top_k)
        docs = []
        for d in raw:
            docs.append(RetrievalDocument(
                comment_text=d.get("comment_text", ""),
                file_path=d.get("file_path", ""),
                code_context=d.get("code_context", ""),
                comment_type=d.get("comment_type", "pr_review"),
                severity=d.get("severity", "unknown"),
                timestamp=d.get("timestamp", ""),
                reviewer_username=d.get("reviewer_username", ""),
                pr_number=d.get("pr_number"),
            ))
        return docs


# ---------------------------------------------------------------------------
# Build + persistence
# ---------------------------------------------------------------------------


def _embeddings_dir(profile_name: str) -> Path:
    return legit_path() / "embeddings" / profile_name


def build_embedding_index(
    profile_name: str,
    documents: list[RetrievalDocument],
    batch_size: int = 64,
) -> EmbeddingIndex:
    """Build an embedding index from retrieval documents.

    Embeds each document's concatenated text (file_path + code_context + comment)
    and stores the vectors alongside document metadata.
    """
    # Build text representations for each document
    texts = []
    doc_dicts = []
    for doc in documents:
        text = f"{doc.file_path} {doc.code_context} {doc.comment_text}".strip()
        if not text:
            continue
        texts.append(text)
        doc_dicts.append(doc.model_dump(mode="json"))

    if not texts:
        return EmbeddingIndex(
            vectors=np.zeros((0, EMBEDDING_DIM), dtype=np.float32),
            documents=[],
            profile_name=profile_name,
        )

    # Embed in batches
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_vecs = embed_texts(batch)
        all_embeddings.append(batch_vecs)
        logger.info("  Embedded %d/%d documents", min(i + batch_size, len(texts)), len(texts))

    vectors = np.concatenate(all_embeddings, axis=0)

    return EmbeddingIndex(
        vectors=vectors,
        documents=doc_dicts,
        profile_name=profile_name,
    )


def save_embedding_index(profile_name: str, index: EmbeddingIndex) -> Path:
    """Save the embedding index to disk."""
    path = _embeddings_dir(profile_name)
    path.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(path / "vectors.npz", vectors=index.vectors)
    (path / "metadata.json").write_text(
        json.dumps(index.documents, indent=2, default=str) + "\n"
    )
    (path / "model_info.json").write_text(
        json.dumps({
            "model": MODEL_REPO,
            "embedding_dim": EMBEDDING_DIM,
            "document_count": len(index.documents),
        }, indent=2) + "\n"
    )

    return path


def load_embedding_index(profile_name: str) -> EmbeddingIndex | None:
    """Load a pre-built embedding index, or None if unavailable."""
    if not _HAS_DEPS:
        return None

    path = _embeddings_dir(profile_name)
    vectors_path = path / "vectors.npz"
    metadata_path = path / "metadata.json"

    if not vectors_path.exists() or not metadata_path.exists():
        return None

    try:
        data = np.load(vectors_path)
        vectors = data["vectors"]
        documents = json.loads(metadata_path.read_text())
        return EmbeddingIndex(
            vectors=vectors,
            documents=documents,
            profile_name=profile_name,
        )
    except Exception as exc:
        logger.warning("Failed to load embedding index for %s: %s", profile_name, exc)
        return None
