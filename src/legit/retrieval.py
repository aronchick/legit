"""BM25 lexical retrieval index for reviewer comment history."""

from __future__ import annotations

import json
import math
import re
import string
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from legit.config import LEGIT_DIR, RetrievalConfig
from legit.models import RetrievalDocument

# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------

_STOP_WORDS: set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for",
    "from", "had", "has", "have", "he", "her", "his", "how", "i", "if",
    "in", "into", "is", "it", "its", "just", "me", "my", "no", "not",
    "of", "on", "or", "our", "out", "own", "say", "she", "so", "some",
    "than", "that", "the", "them", "then", "there", "these", "they",
    "this", "to", "too", "up", "us", "very", "was", "we", "were", "what",
    "when", "which", "who", "will", "with", "would", "you", "your",
}

_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric runs, drop stop words."""
    tokens = _SPLIT_RE.split(text.lower())
    return [t for t in tokens if t and t not in _STOP_WORDS]


# ---------------------------------------------------------------------------
# BM25 Index
# ---------------------------------------------------------------------------

class BM25Index:
    """Pure-Python BM25 index backed by a JSON file on disk.

    Parameters ``k1`` and ``b`` follow the standard Okapi BM25 defaults.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

        # Corpus statistics
        self.doc_count: int = 0
        self.avg_dl: float = 0.0
        self.doc_lengths: list[int] = []

        # Inverted index: term -> {doc_id: term_frequency}
        self.inverted: dict[str, dict[int, int]] = defaultdict(dict)

        # Original documents stored alongside the index
        self.documents: list[dict] = []

    # -- construction -------------------------------------------------------

    def build(self, documents: list[RetrievalDocument]) -> None:
        """Build the index from a list of :class:`RetrievalDocument`."""
        self.documents = [doc.model_dump() for doc in documents]
        self.doc_count = len(documents)
        self.doc_lengths = []
        self.inverted = defaultdict(dict)

        total_length = 0
        for doc_id, doc in enumerate(documents):
            text = f"{doc.comment_text} {doc.code_context}"
            tokens = tokenize(text)
            self.doc_lengths.append(len(tokens))
            total_length += len(tokens)

            # Count term frequencies for this document
            tf: dict[str, int] = defaultdict(int)
            for token in tokens:
                tf[token] += 1
            for term, freq in tf.items():
                self.inverted[term][doc_id] = freq

        self.avg_dl = total_length / self.doc_count if self.doc_count else 0.0

    # -- scoring ------------------------------------------------------------

    def _idf(self, term: str) -> float:
        """Inverse document frequency for *term* (Robertson-Sparck-Jones)."""
        df = len(self.inverted.get(term, {}))
        if df == 0:
            return 0.0
        return math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query: str) -> list[tuple[int, float]]:
        """Return ``(doc_id, score)`` pairs for every document, sorted descending."""
        query_tokens = tokenize(query)
        scores: dict[int, float] = defaultdict(float)

        for term in query_tokens:
            idf = self._idf(term)
            postings = self.inverted.get(term, {})
            for doc_id, tf in postings.items():
                dl = self.doc_lengths[doc_id]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl)
                scores[doc_id] += idf * numerator / denominator

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked

    # -- persistence --------------------------------------------------------

    def save(self, profile_name: str) -> Path:
        """Serialise the index to ``.legit/index/{profile_name}/bm25.json``."""
        index_dir = Path(LEGIT_DIR) / "index" / profile_name
        index_dir.mkdir(parents=True, exist_ok=True)
        path = index_dir / "bm25.json"

        # Convert defaultdict / int-keyed dicts to plain JSON-safe structures
        inverted_json: dict[str, dict[str, int]] = {
            term: {str(doc_id): freq for doc_id, freq in postings.items()}
            for term, postings in self.inverted.items()
        }

        payload = {
            "k1": self.k1,
            "b": self.b,
            "doc_count": self.doc_count,
            "avg_dl": self.avg_dl,
            "doc_lengths": self.doc_lengths,
            "inverted": inverted_json,
            "documents": self.documents,
        }
        path.write_text(json.dumps(payload, default=str))
        return path

    @classmethod
    def load(cls, profile_name: str) -> BM25Index:
        """Deserialise an index from disk."""
        path = Path(LEGIT_DIR) / "index" / profile_name / "bm25.json"
        if not path.exists():
            raise FileNotFoundError(f"No BM25 index for profile '{profile_name}' at {path}")

        raw = json.loads(path.read_text())

        idx = cls(k1=raw["k1"], b=raw["b"])
        idx.doc_count = raw["doc_count"]
        idx.avg_dl = raw["avg_dl"]
        idx.doc_lengths = raw["doc_lengths"]
        idx.documents = raw["documents"]

        idx.inverted = defaultdict(dict)
        for term, postings in raw["inverted"].items():
            idx.inverted[term] = {int(doc_id): freq for doc_id, freq in postings.items()}

        return idx


# ---------------------------------------------------------------------------
# Index construction helper (called by ``legit build``)
# ---------------------------------------------------------------------------

def build_index(profile_name: str, documents: list[RetrievalDocument]) -> Path:
    """Build and persist a BM25 index for *profile_name*.

    Returns the path to the saved JSON file.
    """
    idx = BM25Index()
    idx.build(documents)
    return idx.save(profile_name)


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def construct_queries(diff_hunks: list[dict]) -> list[str]:
    """Build retrieval queries from PR diff hunks.

    Each hunk dict is expected to have at least:
    - ``file_path``: the changed file
    - ``content``:   the diff text (added/removed lines)

    The query combines the file path with the hunk content so that BM25 can
    match on both naming conventions and code patterns the reviewer has
    commented on before.
    """
    queries: list[str] = []
    for hunk in diff_hunks:
        file_path = hunk.get("file_path", "")
        content = hunk.get("content", "")
        # Use the filename (without directories) plus the diff body
        file_stem = Path(file_path).name if file_path else ""
        query = f"{file_stem} {content}".strip()
        if query:
            queries.append(query)
    return queries


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _recency_weight(timestamp_str: str, half_life_days: int) -> float:
    """Exponential decay weight: ``exp(-ln(2) * age_days / half_life)``."""
    if not timestamp_str:
        return 1.0
    try:
        ts = datetime.fromisoformat(timestamp_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_days = max((now - ts).total_seconds() / 86400, 0.0)
    except (ValueError, TypeError):
        return 1.0
    if half_life_days <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days / half_life_days)


def retrieve(
    profile_name: str,
    queries: list[str],
    top_k: int = 10,
    type_weights: dict[str, float] | None = None,
    temporal_half_life: int = 730,
) -> list[RetrievalDocument]:
    """Retrieve the most relevant past comments for the given queries.

    Algorithm:
    1. For each query, score all documents and take the top 5 candidates.
    2. Pool all candidates globally.
    3. Rerank each candidate by ``bm25_score * type_weight * recency_weight``.
    4. Deduplicate (by comment_text).
    5. Return the top *top_k* results.
    """
    if type_weights is None:
        type_weights = {"pr_review": 2.0, "issue_comment": 1.0, "commit_comment": 0.5}

    idx = BM25Index.load(profile_name)

    # Collect (doc_id, combined_score) across all queries
    candidates: dict[int, float] = {}
    per_query_k = 5

    for query in queries:
        ranked = idx.score(query)
        for doc_id, bm25_score in ranked[:per_query_k]:
            doc_dict = idx.documents[doc_id]
            tw = type_weights.get(doc_dict.get("comment_type", ""), 1.0)
            rw = _recency_weight(doc_dict.get("timestamp", ""), temporal_half_life)
            combined = bm25_score * tw * rw
            # Keep the best score if a document appears across queries
            if doc_id not in candidates or combined > candidates[doc_id]:
                candidates[doc_id] = combined

    # Sort by combined score, deduplicate, take top_k
    sorted_ids = sorted(candidates, key=candidates.get, reverse=True)  # type: ignore[arg-type]

    seen_texts: set[str] = set()
    results: list[RetrievalDocument] = []
    for doc_id in sorted_ids:
        doc_dict = idx.documents[doc_id]
        text = doc_dict.get("comment_text", "")
        if text in seen_texts:
            continue
        seen_texts.add(text)
        results.append(RetrievalDocument.model_validate(doc_dict))
        if len(results) >= top_k:
            break

    return results


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def format_examples(docs: list[RetrievalDocument]) -> str:
    """Format retrieved documents as few-shot examples for the review prompt.

    Each example shows the code context (when available) followed by the
    reviewer's actual comment, labelled with the comment type.
    """
    if not docs:
        return ""

    parts: list[str] = []
    for i, doc in enumerate(docs, 1):
        lines: list[str] = [f"--- Example {i} [{doc.comment_type}] ---"]
        if doc.code_context:
            lines.append("Code:")
            lines.append(doc.code_context)
            lines.append("")
        lines.append("Comment:")
        lines.append(doc.comment_text)
        parts.append("\n".join(lines))

    return "\n\n".join(parts) + "\n"
