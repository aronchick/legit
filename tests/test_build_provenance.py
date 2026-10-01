"""Build boundaries invalidate cached observations and attest to generated artifacts."""

import pytest

from legit.config import LegitConfig, ProfileConfig, ProfileSource
from legit.profile import (
    _filter_items_before,
    _prepare_map_cache,
    build_profile,
    save_build_provenance,
    verify_build_provenance,
)


def config():
    return LegitConfig(
        profiles=[
            ProfileConfig(
                name="reviewer",
                sources=[ProfileSource(repo="o/r", username="reviewer")],
            )
        ]
    )


@pytest.mark.parametrize("change", ["corpus", "cutoff", "model", "chunking", "refresh"])
def test_map_cache_invalidated_when_training_inputs_change(legit_dir, change):
    cfg = config()
    items = [{"body": "old", "created_at": "2025-01-01"}]
    cutoff = "2026-01-01"
    _prepare_map_cache(cfg, cfg.profiles[0], items, cutoff, False)
    cached = legit_dir / "cache/chunks/reviewer/chunk_000.json"
    cached.write_text("{}")
    _prepare_map_cache(cfg, cfg.profiles[0], items, cutoff, False)
    assert cached.exists()
    if change == "corpus":
        items[0]["body"] = "different"
    elif change == "cutoff":
        cutoff = "2025-12-01"
    elif change == "model":
        cfg.model.name = "different-model"
    elif change == "chunking":
        cfg.profiles[0].chunk_size = 1
    _prepare_map_cache(cfg, cfg.profiles[0], items, cutoff, change == "refresh")
    assert not cached.exists()


def test_unfingerprinted_legacy_map_cache_is_not_trusted(legit_dir):
    cached = legit_dir / "cache/chunks/reviewer/chunk_000.json"
    cached.parent.mkdir(parents=True)
    cached.write_text("{}")
    cfg = config()
    _prepare_map_cache(cfg, cfg.profiles[0], [], None, False)
    assert not cached.exists()


def artifacts(root):
    paths = [
        "profiles/reviewer.md",
        "index/reviewer/bm25.json",
        "expertise/reviewer/expertise.json",
    ]
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")
    return paths


def test_provenance_rejects_changed_artifacts_and_new_semantic_index(legit_dir):
    paths = artifacts(legit_dir)
    save_build_provenance("reviewer", "2026-01-01")
    verify_build_provenance("reviewer")
    (legit_dir / paths[0]).write_text("changed")
    with pytest.raises(ValueError, match="unverified"):
        verify_build_provenance("reviewer")
    (legit_dir / paths[0]).write_text("content")
    semantic = legit_dir / "embeddings/reviewer/vectors.npz"
    semantic.parent.mkdir(parents=True)
    semantic.write_text("stale")
    with pytest.raises(ValueError, match="missing"):
        verify_build_provenance("reviewer")


def test_provenance_requires_all_build_outputs(legit_dir):
    with pytest.raises(ValueError, match="missing"):
        save_build_provenance("reviewer", "2026-01-01")
    assert not (legit_dir / "profiles/reviewer.meta.json").exists()


def test_rebuild_without_semantics_removes_stale_generated_index(
    legit_dir,
    monkeypatch,
    mock_chunk_observation,
):
    cfg = config()
    monkeypatch.setattr(
        "legit.profile._load_all_items", lambda _: [{"body": "comment", "created_at": "2025-01-01"}]
    )
    monkeypatch.setattr("legit.profile._run_map", lambda *args: mock_chunk_observation)
    monkeypatch.setattr("legit.profile._run_reduce", lambda *args: "profile")
    monkeypatch.setattr("legit.embeddings.is_available", lambda: False)
    directory = legit_dir / "embeddings/reviewer"
    directory.mkdir(parents=True)
    for name in ("vectors.npz", "metadata.json", "model_info.json", "user-notes.txt"):
        (directory / name).write_text("old")
    meta = legit_dir / "profiles/reviewer.meta.json"
    meta.write_text("{}")
    build_profile(cfg, "reviewer", before="2026-01-01")
    assert sorted(path.name for path in directory.iterdir()) == ["user-notes.txt"]
    assert not meta.exists()  # CLI stamps only once BM25 also succeeds


@pytest.mark.parametrize(
    "item",
    [
        {"body": "undated"},
        {"created_at": "invalid"},
        {"created_at": "2025-01-01", "updated_at": "2026-01-01"},
        {"created_at": "2025-01-01", "updated_at": None},
        {"created_at": "2025-01-01", "updated_at": "invalid"},
        {"created_at": "2025-12-31T23:30:00-02:00"},
        {"created_at": "2025-01-01", "_source_file": "pr_comments.json"},
        {"created_at": "2025-01-01", "_source_file": "authored_prs.json"},
        {"commit": {"author": {"date": "2025-01-01"}, "committer": {"date": "2026-01-01"}}},
    ],
)
def test_temporal_filter_rejects_unknown_or_post_cutoff_evidence(item):
    assert _filter_items_before([item], "2026-01-01") == []
    assert _filter_items_before([item], None) == [item]


def test_temporal_filter_keeps_verified_pre_cutoff_comment():
    item = {
        "created_at": "2025-01-01",
        "updated_at": "2025-12-31T23:59:59Z",
        "_source_file": "pr_comments.json",
    }
    assert _filter_items_before([item], "2026-01-01") == [item]


def test_temporal_cutoff_invalid_date_fails():
    with pytest.raises(ValueError, match="Invalid temporal"):
        _filter_items_before([], "unknown")
