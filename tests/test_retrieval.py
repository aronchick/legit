"""Tests for legit.retrieval — BM25 index, tokenization, queries, retrieval."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from legit.models import RetrievalDocument
from legit.retrieval import (
    BM25Index,
    _path_boost,
    _recency_weight,
    build_index,
    construct_queries,
    format_examples,
    retrieve,
    tokenize,
)


# ---------------------------------------------------------------------------
# tokenize()
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_basic(self):
        tokens = tokenize("Hello World")
        assert tokens == ["hello", "world"]

    def test_removes_stop_words(self):
        tokens = tokenize("this is a test of the function")
        # "this", "is", "a", "of", "the" are stop words
        assert tokens == ["test", "function"]

    def test_splits_on_special_chars(self):
        tokens = tokenize("func_name(arg1, arg2)")
        assert "func" in tokens
        assert "name" in tokens
        assert "arg1" in tokens
        assert "arg2" in tokens

    def test_empty_string(self):
        assert tokenize("") == []

    def test_only_stop_words(self):
        assert tokenize("the a an is are") == []

    def test_numbers_preserved(self):
        tokens = tokenize("version 2 release 10")
        assert "2" in tokens
        assert "10" in tokens
        assert "version" in tokens
        assert "release" in tokens

    def test_camel_case_not_split(self):
        # tokenize lowercases but doesn't split camelCase
        tokens = tokenize("handleRequest")
        assert tokens == ["handlerequest"]

    def test_code_snippet(self):
        tokens = tokenize("if err != nil { return err }")
        assert "err" in tokens
        assert "nil" in tokens
        assert "return" in tokens


# ---------------------------------------------------------------------------
# BM25Index.build() and scoring
# ---------------------------------------------------------------------------


class TestBM25Index:
    @pytest.fixture()
    def docs(self) -> list[RetrievalDocument]:
        return [
            RetrievalDocument(
                comment_text="Handle errors explicitly with nil checks",
                code_context="if err != nil { return err }",
            ),
            RetrievalDocument(
                comment_text="Use constants instead of magic numbers",
                code_context="timeout := 30 * time.Second",
            ),
            RetrievalDocument(
                comment_text="Add unit tests for the error handling path",
                code_context="func TestErrorCase(t *testing.T) {",
            ),
        ]

    def test_build_sets_stats(self, docs: list[RetrievalDocument]):
        idx = BM25Index()
        idx.build(docs)
        assert idx.doc_count == 3
        assert idx.avg_dl > 0
        assert len(idx.doc_lengths) == 3
        assert len(idx.documents) == 3

    def test_build_populates_inverted_index(self, docs: list[RetrievalDocument]):
        idx = BM25Index()
        idx.build(docs)
        # "error" appears in docs 0 and 2
        assert "error" in idx.inverted or "errors" in idx.inverted
        # "constants" appears in doc 1
        assert "constants" in idx.inverted
        assert 1 in idx.inverted["constants"]

    def test_score_returns_ranked_results(self, docs: list[RetrievalDocument]):
        idx = BM25Index()
        idx.build(docs)
        results = idx.score("error handling nil")
        assert len(results) > 0
        # First result should be about error handling (doc 0 or 2)
        top_doc_id = results[0][0]
        assert top_doc_id in (0, 2)
        # Scores should be descending
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_score_empty_query(self, docs: list[RetrievalDocument]):
        idx = BM25Index()
        idx.build(docs)
        results = idx.score("")
        assert results == []

    def test_score_no_matches(self, docs: list[RetrievalDocument]):
        idx = BM25Index()
        idx.build(docs)
        results = idx.score("xyzzyspoonunique")
        assert results == []

    def test_build_empty_corpus(self):
        idx = BM25Index()
        idx.build([])
        assert idx.doc_count == 0
        assert idx.avg_dl == 0.0
        results = idx.score("anything")
        assert results == []

    def test_idf_unknown_term(self, docs: list[RetrievalDocument]):
        idx = BM25Index()
        idx.build(docs)
        assert idx._idf("nonexistent_xyzzy") == 0.0


# ---------------------------------------------------------------------------
# BM25Index.save() / load() round-trip
# ---------------------------------------------------------------------------


class TestBM25Persistence:
    def test_save_load_roundtrip(self, legit_dir: Path):
        docs = [
            RetrievalDocument(
                comment_text="Check for nil before dereferencing",
                file_path="pkg/handler.go",
                comment_type="pr_review",
                timestamp="2025-01-15T00:00:00Z",
            ),
            RetrievalDocument(
                comment_text="Good use of mutex here",
                file_path="pkg/server.go",
                comment_type="issue_comment",
                timestamp="2025-02-01T00:00:00Z",
            ),
        ]

        idx = BM25Index()
        idx.build(docs)
        idx.save("test_profile")

        loaded = BM25Index.load("test_profile")
        assert loaded.doc_count == idx.doc_count
        assert loaded.avg_dl == pytest.approx(idx.avg_dl)
        assert loaded.doc_lengths == idx.doc_lengths
        assert loaded.k1 == idx.k1
        assert loaded.b == idx.b
        assert len(loaded.documents) == len(idx.documents)

        # Verify scoring gives same results
        q = "nil check"
        original_results = idx.score(q)
        loaded_results = loaded.score(q)
        assert len(original_results) == len(loaded_results)
        for (oid, oscore), (lid, lscore) in zip(original_results, loaded_results):
            assert oid == lid
            assert oscore == pytest.approx(lscore)

    def test_load_missing_raises(self, legit_dir: Path):
        with pytest.raises(FileNotFoundError, match="No BM25 index"):
            BM25Index.load("nonexistent_profile")


# ---------------------------------------------------------------------------
# construct_queries()
# ---------------------------------------------------------------------------


class TestConstructQueries:
    def test_basic_hunks(self):
        hunks = [
            {"file_path": "pkg/auth/handler.go", "content": "+if user == nil {\n+  return err\n+}"},
        ]
        queries = construct_queries(hunks)
        assert len(queries) >= 1
        # Content query should include filename
        assert "handler.go" in queries[0]
        # Should also have a path query
        assert any("pkg" in q and "auth" in q for q in queries)

    def test_multiple_files_different_dirs(self):
        hunks = [
            {"file_path": "pkg/auth/handler.go", "content": "+check"},
            {"file_path": "pkg/compute/worker.go", "content": "+process"},
            {"file_path": "pkg/auth/middleware.go", "content": "+validate"},
        ]
        queries = construct_queries(hunks)
        # Should have content queries for each file
        assert any("handler.go" in q for q in queries)
        assert any("worker.go" in q for q in queries)
        assert any("middleware.go" in q for q in queries)

    def test_same_directory_dedup(self):
        hunks = [
            {"file_path": "pkg/auth/handler.go", "content": "+a"},
            {"file_path": "pkg/auth/middleware.go", "content": "+b"},
        ]
        queries = construct_queries(hunks)
        # Path query for "pkg/auth" should only appear once
        path_queries = [q for q in queries if "handler.go" not in q and "middleware.go" not in q]
        auth_path_queries = [q for q in path_queries if "pkg" in q and "auth" in q]
        assert len(auth_path_queries) <= 1

    def test_empty_hunks(self):
        assert construct_queries([]) == []

    def test_root_file_no_path_query(self):
        hunks = [{"file_path": "main.go", "content": "+func main()"}]
        queries = construct_queries(hunks)
        # Only content query, no dir query for root files
        assert len(queries) == 1
        assert "main.go" in queries[0]

    def test_missing_file_path(self):
        hunks = [{"content": "+some code"}]
        queries = construct_queries(hunks)
        assert len(queries) == 1
        assert "some code" in queries[0]

    def test_empty_content(self):
        hunks = [{"file_path": "pkg/a.go", "content": ""}]
        queries = construct_queries(hunks)
        # Should still create a content query from filename and a path query
        assert any("a.go" in q for q in queries)


# ---------------------------------------------------------------------------
# _path_boost()
# ---------------------------------------------------------------------------


class TestPathBoost:
    def test_exact_file_match(self):
        boost = _path_boost(
            "pkg/auth/handler.go",
            {"pkg/auth/handler.go"},
            {"pkg/auth"},
        )
        assert boost == 3.0

    def test_same_directory(self):
        boost = _path_boost(
            "pkg/auth/middleware.go",
            {"pkg/auth/handler.go"},
            {"pkg/auth"},
        )
        assert boost == 2.0

    def test_parent_child_dir(self):
        boost = _path_boost(
            "pkg/auth/oauth/provider.go",
            {"pkg/auth/handler.go"},
            {"pkg/auth"},
        )
        # doc_dir is "pkg/auth/oauth", pr_dir is "pkg/auth"
        # "pkg/auth/oauth".startswith("pkg/auth") -> True -> 1.5
        assert boost == 1.5

    def test_child_parent_dir(self):
        boost = _path_boost(
            "pkg/handler.go",
            {"pkg/auth/handler.go"},
            {"pkg/auth"},
        )
        # doc_dir is "pkg", pr_dir is "pkg/auth"
        # "pkg/auth".startswith("pkg") -> True -> 1.5
        assert boost == 1.5

    def test_no_overlap(self):
        boost = _path_boost(
            "lib/utils/helpers.go",
            {"pkg/auth/handler.go"},
            {"pkg/auth"},
        )
        assert boost == 1.0

    def test_empty_doc_path(self):
        boost = _path_boost("", {"pkg/auth/handler.go"}, {"pkg/auth"})
        assert boost == 1.0


# ---------------------------------------------------------------------------
# _recency_weight()
# ---------------------------------------------------------------------------


class TestRecencyWeight:
    def test_recent_gets_high_weight(self):
        # 1 day ago
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=1)).isoformat()
        w = _recency_weight(ts, half_life_days=730)
        assert w > 0.99

    def test_old_gets_low_weight(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=3650)).isoformat()  # 10 years ago
        w = _recency_weight(ts, half_life_days=730)
        assert w < 0.1

    def test_at_half_life(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=730)).isoformat()
        w = _recency_weight(ts, half_life_days=730)
        assert w == pytest.approx(0.5, abs=0.05)

    def test_empty_timestamp(self):
        assert _recency_weight("", half_life_days=730) == 1.0

    def test_invalid_timestamp(self):
        assert _recency_weight("not-a-date", half_life_days=730) == 1.0

    def test_zero_half_life(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=100)).isoformat()
        assert _recency_weight(ts, half_life_days=0) == 1.0

    def test_negative_half_life(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=100)).isoformat()
        assert _recency_weight(ts, half_life_days=-10) == 1.0


# ---------------------------------------------------------------------------
# retrieve() end-to-end (with mocked index)
# ---------------------------------------------------------------------------


class TestRetrieve:
    def test_retrieve_returns_top_k(self, legit_dir: Path, sample_retrieval_docs: list[RetrievalDocument]):
        idx = BM25Index()
        idx.build(sample_retrieval_docs)
        idx.save("testuser")

        results = retrieve(
            profile_name="testuser",
            queries=["error handling nil check"],
            top_k=2,
        )
        assert len(results) <= 2
        assert all(isinstance(r, RetrievalDocument) for r in results)

    def test_retrieve_deduplicates(self, legit_dir: Path):
        docs = [
            RetrievalDocument(comment_text="Duplicate comment", file_path="a.go"),
            RetrievalDocument(comment_text="Duplicate comment", file_path="b.go"),
            RetrievalDocument(comment_text="Unique comment", file_path="c.go"),
        ]
        idx = BM25Index()
        idx.build(docs)
        idx.save("dedup_test")

        results = retrieve(
            profile_name="dedup_test",
            queries=["duplicate comment"],
            top_k=10,
        )
        texts = [r.comment_text for r in results]
        assert texts.count("Duplicate comment") == 1

    def test_retrieve_applies_type_weights(self, legit_dir: Path):
        docs = [
            RetrievalDocument(
                comment_text="error handling suggestion",
                comment_type="pr_review",
            ),
            RetrievalDocument(
                comment_text="error handling tip",
                comment_type="commit_comment",
            ),
        ]
        idx = BM25Index()
        idx.build(docs)
        idx.save("type_weight_test")

        results = retrieve(
            profile_name="type_weight_test",
            queries=["error handling"],
            top_k=2,
            type_weights={"pr_review": 10.0, "commit_comment": 0.1},
        )
        # pr_review should rank first due to much higher type weight
        assert results[0].comment_type == "pr_review"

    def test_retrieve_with_path_boost(self, legit_dir: Path):
        docs = [
            RetrievalDocument(
                comment_text="validate user input carefully",
                file_path="pkg/auth/handler.go",
                comment_type="pr_review",
            ),
            RetrievalDocument(
                comment_text="validate user input before processing",
                file_path="lib/utils/helpers.go",
                comment_type="pr_review",
            ),
        ]
        idx = BM25Index()
        idx.build(docs)
        idx.save("path_boost_test")

        results = retrieve(
            profile_name="path_boost_test",
            queries=["validate user input"],
            top_k=2,
            pr_changed_files=["pkg/auth/handler.go"],
        )
        # The doc matching the changed file should rank first
        assert results[0].file_path == "pkg/auth/handler.go"


# ---------------------------------------------------------------------------
# format_examples()
# ---------------------------------------------------------------------------


class TestFormatExamples:
    def test_empty_docs(self):
        assert format_examples([]) == ""

    def test_single_doc(self):
        docs = [
            RetrievalDocument(
                comment_text="Use constants for magic numbers.",
                file_path="pkg/utils.go",
                code_context="timeout := 30",
                comment_type="pr_review",
                reviewer_username="alice",
            )
        ]
        result = format_examples(docs)
        assert "Example 1" in result
        assert "[pr_review]" in result
        assert "pkg/utils.go" in result
        assert "Code:" in result
        assert "timeout := 30" in result
        assert "Reviewer: alice" in result
        assert "Use constants for magic numbers." in result

    def test_multiple_docs(self):
        docs = [
            RetrievalDocument(comment_text="Comment A", comment_type="pr_review"),
            RetrievalDocument(comment_text="Comment B", comment_type="issue_comment"),
        ]
        result = format_examples(docs)
        assert "Example 1" in result
        assert "Example 2" in result
        assert "Comment A" in result
        assert "Comment B" in result

    def test_no_code_context(self):
        docs = [
            RetrievalDocument(comment_text="Nice work!", comment_type="issue_comment")
        ]
        result = format_examples(docs)
        assert "Code:" not in result

    def test_no_reviewer_username(self):
        docs = [
            RetrievalDocument(comment_text="Fix this.", comment_type="pr_review")
        ]
        result = format_examples(docs)
        assert "Reviewer:" not in result


# ---------------------------------------------------------------------------
# build_index() helper
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_build_index_creates_file(self, legit_dir: Path):
        docs = [
            RetrievalDocument(comment_text="test comment", file_path="a.go"),
        ]
        path = build_index("test_profile", docs)
        assert path.exists()
        assert path.name == "bm25.json"

        # Verify we can load it
        loaded = BM25Index.load("test_profile")
        assert loaded.doc_count == 1
