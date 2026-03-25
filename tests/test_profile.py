"""Tests for legit.profile — chunking, data loading, profile building."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from legit.config import LegitConfig, ProfileConfig, ProfileSource
from legit.models import ChunkObservation, RetrievalDocument
from legit.profile import (
    _chunk_items,
    _clear_cache,
    _data_dirs_for_profile,
    _date_range,
    _find_profile,
    _load_all_items,
    _load_cached_chunk,
    _save_cached_chunk,
    build_profile,
    load_profile,
    load_raw_data_as_retrieval_docs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(legit_dir: Path, name: str = "testuser") -> ProfileConfig:
    return ProfileConfig(
        name=name,
        sources=[ProfileSource(type="primary", repo="octocat/hello-world", username="testuser")],
        chunk_size=50,
    )


def _make_config(legit_dir: Path, profile_name: str = "testuser") -> LegitConfig:
    return LegitConfig(
        profiles=[_make_profile(legit_dir, profile_name)],
    )


def _populate_data_dir(legit_dir: Path, items: list[dict], filename: str = "pr_comments.json") -> Path:
    """Write sample items into the data directory."""
    data_dir = legit_dir / "data" / "octocat_hello-world" / "testuser"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / filename).write_text(json.dumps(items))
    # Also create index.json and cursor.json to be skipped
    (data_dir / "index.json").write_text("[]")
    (data_dir / "cursor.json").write_text("{}")
    return data_dir


# ---------------------------------------------------------------------------
# _chunk_items()
# ---------------------------------------------------------------------------


class TestChunkItems:
    def test_exact_chunks(self):
        items = list(range(10))
        chunks = _chunk_items(items, chunk_size=5)
        assert len(chunks) == 2
        assert chunks[0] == [0, 1, 2, 3, 4]
        assert chunks[1] == [5, 6, 7, 8, 9]

    def test_partial_last_chunk(self):
        items = list(range(7))
        chunks = _chunk_items(items, chunk_size=3)
        assert len(chunks) == 3
        assert chunks[0] == [0, 1, 2]
        assert chunks[1] == [3, 4, 5]
        assert chunks[2] == [6]

    def test_single_item(self):
        chunks = _chunk_items([42], chunk_size=100)
        assert len(chunks) == 1
        assert chunks[0] == [42]

    def test_empty_list(self):
        chunks = _chunk_items([], chunk_size=100)
        assert chunks == []

    def test_chunk_size_larger_than_list(self):
        items = [1, 2, 3]
        chunks = _chunk_items(items, chunk_size=100)
        assert len(chunks) == 1
        assert chunks[0] == [1, 2, 3]

    def test_chunk_size_one(self):
        items = [1, 2, 3]
        chunks = _chunk_items(items, chunk_size=1)
        assert len(chunks) == 3
        assert all(len(c) == 1 for c in chunks)


# ---------------------------------------------------------------------------
# _data_dirs_for_profile()
# ---------------------------------------------------------------------------


class TestDataDirsForProfile:
    def test_single_source(self, legit_dir: Path):
        profile = _make_profile(legit_dir)
        dirs = _data_dirs_for_profile(profile)
        assert len(dirs) == 1
        assert "octocat_hello-world" in str(dirs[0])
        assert "testuser" in str(dirs[0])

    def test_multiple_sources(self, legit_dir: Path):
        profile = ProfileConfig(
            name="multi",
            sources=[
                ProfileSource(repo="org1/repo1", username="alice"),
                ProfileSource(repo="org2/repo2", username="bob"),
            ],
        )
        dirs = _data_dirs_for_profile(profile)
        assert len(dirs) == 2
        assert "org1_repo1" in str(dirs[0])
        assert "org2_repo2" in str(dirs[1])


# ---------------------------------------------------------------------------
# _load_all_items()
# ---------------------------------------------------------------------------


class TestLoadAllItems:
    def test_loads_and_sorts(self, legit_dir: Path):
        items = [
            {"body": "Second", "created_at": "2025-02-01T00:00:00Z"},
            {"body": "First", "created_at": "2025-01-01T00:00:00Z"},
            {"body": "Third", "created_at": "2025-03-01T00:00:00Z"},
        ]
        _populate_data_dir(legit_dir, items)
        profile = _make_profile(legit_dir)

        loaded = _load_all_items(profile)
        assert len(loaded) == 3
        assert loaded[0]["body"] == "First"
        assert loaded[1]["body"] == "Second"
        assert loaded[2]["body"] == "Third"

    def test_skips_index_and_cursor(self, legit_dir: Path):
        _populate_data_dir(legit_dir, [{"body": "test", "created_at": "2025-01-01T00:00:00Z"}])
        profile = _make_profile(legit_dir)
        loaded = _load_all_items(profile)
        # Should only load from pr_comments.json, not index.json or cursor.json
        assert len(loaded) == 1

    def test_missing_data_dir(self, legit_dir: Path):
        profile = _make_profile(legit_dir)
        loaded = _load_all_items(profile)
        assert loaded == []

    def test_adds_source_file_field(self, legit_dir: Path):
        items = [{"body": "test", "created_at": "2025-01-01T00:00:00Z"}]
        _populate_data_dir(legit_dir, items)
        profile = _make_profile(legit_dir)
        loaded = _load_all_items(profile)
        assert loaded[0]["_source_file"] == "pr_comments.json"

    def test_sorts_by_submitted_at(self, legit_dir: Path):
        items = [
            {"body": "B", "submitted_at": "2025-02-01T00:00:00Z"},
            {"body": "A", "submitted_at": "2025-01-01T00:00:00Z"},
        ]
        _populate_data_dir(legit_dir, items, filename="reviews.json")
        profile = _make_profile(legit_dir)
        loaded = _load_all_items(profile)
        assert loaded[0]["body"] == "A"


# ---------------------------------------------------------------------------
# _date_range()
# ---------------------------------------------------------------------------


class TestDateRange:
    def test_basic(self):
        chunk = [
            {"created_at": "2025-03-01T00:00:00Z"},
            {"created_at": "2025-01-01T00:00:00Z"},
            {"created_at": "2025-02-01T00:00:00Z"},
        ]
        start, end = _date_range(chunk)
        assert start == "2025-01-01T00:00:00Z"
        assert end == "2025-03-01T00:00:00Z"

    def test_single_item(self):
        chunk = [{"created_at": "2025-06-15T12:00:00Z"}]
        start, end = _date_range(chunk)
        assert start == end == "2025-06-15T12:00:00Z"

    def test_no_dates(self):
        chunk = [{"body": "no date"}, {"body": "also no date"}]
        start, end = _date_range(chunk)
        assert start == "unknown"
        assert end == "unknown"

    def test_commit_date_format(self):
        chunk = [
            {"commit": {"author": {"date": "2025-01-01T00:00:00Z"}}},
            {"commit": {"author": {"date": "2025-06-01T00:00:00Z"}}},
        ]
        start, end = _date_range(chunk)
        assert start == "2025-01-01T00:00:00Z"
        assert end == "2025-06-01T00:00:00Z"

    def test_mixed_date_fields(self):
        chunk = [
            {"created_at": "2025-03-01T00:00:00Z"},
            {"submitted_at": "2025-01-01T00:00:00Z"},
        ]
        start, end = _date_range(chunk)
        assert start == "2025-01-01T00:00:00Z"
        assert end == "2025-03-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Chunk caching
# ---------------------------------------------------------------------------


class TestChunkCaching:
    def test_save_and_load(self, legit_dir: Path, mock_chunk_observation: ChunkObservation):
        _save_cached_chunk("testuser", 0, mock_chunk_observation)
        loaded = _load_cached_chunk("testuser", 0)
        assert loaded is not None
        assert loaded.date_range_start == mock_chunk_observation.date_range_start
        assert len(loaded.observations) == len(mock_chunk_observation.observations)

    def test_load_missing_returns_none(self, legit_dir: Path):
        assert _load_cached_chunk("testuser", 999) is None

    def test_clear_cache(self, legit_dir: Path, mock_chunk_observation: ChunkObservation):
        _save_cached_chunk("testuser", 0, mock_chunk_observation)
        _save_cached_chunk("testuser", 1, mock_chunk_observation)
        _clear_cache("testuser")
        assert _load_cached_chunk("testuser", 0) is None
        assert _load_cached_chunk("testuser", 1) is None

    def test_clear_cache_nonexistent_profile(self, legit_dir: Path):
        # Should not raise
        _clear_cache("nonexistent_profile")


# ---------------------------------------------------------------------------
# build_profile() end-to-end (mocked LLM)
# ---------------------------------------------------------------------------


class TestBuildProfile:
    @patch("legit.profile.run_inference")
    def test_end_to_end(self, mock_inference: MagicMock, legit_dir: Path):
        items = [
            {"body": f"Comment {i}", "created_at": f"2025-01-{i+1:02d}T00:00:00Z"}
            for i in range(10)
        ]
        _populate_data_dir(legit_dir, items)
        config = _make_config(legit_dir)

        # First call = map phase (returns ChunkObservation)
        map_result = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-01-10",
            observations=[{"situation": "testing", "behaviors": ["writes tests"]}],
        )
        # Second call = reduce phase (returns raw markdown string)
        reduce_result = "# Reviewer Profile\n\nThis reviewer focuses on testing."

        mock_inference.side_effect = [map_result, reduce_result]

        output_path = build_profile(config, "testuser")
        assert output_path.exists()
        content = output_path.read_text()
        assert "Reviewer Profile" in content

    @patch("legit.profile.run_inference")
    def test_no_data_raises(self, mock_inference: MagicMock, legit_dir: Path):
        config = _make_config(legit_dir)
        with pytest.raises(RuntimeError, match="No data found"):
            build_profile(config, "testuser")

    @patch("legit.profile.run_inference")
    def test_rebuild_map_clears_cache(self, mock_inference: MagicMock, legit_dir: Path):
        items = [{"body": "Test", "created_at": "2025-01-01T00:00:00Z"}]
        _populate_data_dir(legit_dir, items)
        config = _make_config(legit_dir)

        # Pre-populate cache
        obs = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-01-01",
            observations=[{"situation": "cached", "behaviors": ["old"]}],
        )
        _save_cached_chunk("testuser", 0, obs)

        # Map returns new observation, reduce returns profile
        new_obs = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-01-01",
            observations=[{"situation": "fresh", "behaviors": ["new"]}],
        )
        mock_inference.side_effect = [new_obs, "# Fresh Profile"]

        build_profile(config, "testuser", rebuild_map=True)

        # The map phase should have been called (cache was cleared)
        assert mock_inference.call_count == 2

    @patch("legit.profile.run_inference")
    def test_cached_chunks_reused(self, mock_inference: MagicMock, legit_dir: Path):
        items = [{"body": "Test", "created_at": "2025-01-01T00:00:00Z"}]
        _populate_data_dir(legit_dir, items)
        config = _make_config(legit_dir)

        # Pre-populate cache for the single chunk
        obs = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-01-01",
            observations=[{"situation": "cached", "behaviors": ["fast"]}],
        )
        _save_cached_chunk("testuser", 0, obs)

        # Only the reduce phase should be called
        mock_inference.return_value = "# Cached Profile"

        build_profile(config, "testuser")

        # Should only have called run_inference once (reduce only, map was cached)
        assert mock_inference.call_count == 1

    @patch("legit.profile.run_inference")
    def test_max_chunks_limits_processing(self, mock_inference: MagicMock, legit_dir: Path):
        # Create enough items for multiple chunks
        items = [
            {"body": f"Comment {i}", "created_at": f"2025-01-{(i % 28) + 1:02d}T00:00:00Z"}
            for i in range(200)
        ]
        _populate_data_dir(legit_dir, items)
        config = _make_config(legit_dir)
        config.profiles[0].chunk_size = 50  # 200 items / 50 = 4 chunks

        map_obs = ChunkObservation(
            date_range_start="2025-01-01",
            date_range_end="2025-01-28",
            observations=[{"situation": "test", "behaviors": ["test"]}],
        )
        # With max_chunks=2: 2 map calls + 1 reduce call
        mock_inference.side_effect = [map_obs, map_obs, "# Profile"]

        build_profile(config, "testuser", max_chunks=2)

        # 2 map calls + 1 reduce = 3 total
        assert mock_inference.call_count == 3


# ---------------------------------------------------------------------------
# _find_profile()
# ---------------------------------------------------------------------------


class TestFindProfile:
    def test_found(self, legit_dir: Path):
        config = _make_config(legit_dir)
        profile = _find_profile(config, "testuser")
        assert profile.name == "testuser"

    def test_not_found(self, legit_dir: Path):
        config = _make_config(legit_dir)
        with pytest.raises(ValueError, match="Profile 'nonexistent' not found"):
            _find_profile(config, "nonexistent")


# ---------------------------------------------------------------------------
# load_profile()
# ---------------------------------------------------------------------------


class TestLoadProfile:
    def test_loads_existing(self, legit_dir: Path):
        profile_dir = legit_dir / "profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "testuser.md").write_text("# Test Profile\n")

        content = load_profile("testuser")
        assert "# Test Profile" in content

    def test_missing_raises(self, legit_dir: Path):
        with pytest.raises(FileNotFoundError, match="Profile 'nonexistent' not found"):
            load_profile("nonexistent")


# ---------------------------------------------------------------------------
# load_raw_data_as_retrieval_docs()
# ---------------------------------------------------------------------------


class TestLoadRawDataAsRetrievalDocs:
    def test_converts_items(self, legit_dir: Path):
        items = [
            {
                "body": "Please add error handling here.",
                "path": "pkg/handler.go",
                "diff_hunk": "@@ code @@",
                "created_at": "2025-01-10T12:00:00Z",
                "user": {"login": "testuser"},
                "_source_file": "pr_comments.json",
            },
            {
                "body": "Looks good!",
                "created_at": "2025-02-01T00:00:00Z",
                "user": {"login": "testuser"},
                "_source_file": "issue_comments.json",
            },
        ]
        _populate_data_dir(legit_dir, items)
        config = _make_config(legit_dir)

        docs = load_raw_data_as_retrieval_docs(config, "testuser")
        assert len(docs) == 2
        assert all(isinstance(d, RetrievalDocument) for d in docs)
        assert docs[0].comment_text == "Please add error handling here."
        assert docs[0].file_path == "pkg/handler.go"

    def test_skips_empty_body(self, legit_dir: Path):
        items = [
            {"body": "", "created_at": "2025-01-01T00:00:00Z"},
            {"body": "   ", "created_at": "2025-01-02T00:00:00Z"},
            {"body": "Valid", "created_at": "2025-01-03T00:00:00Z"},
        ]
        _populate_data_dir(legit_dir, items)
        config = _make_config(legit_dir)

        docs = load_raw_data_as_retrieval_docs(config, "testuser")
        assert len(docs) == 1
        assert docs[0].comment_text == "Valid"
