import json

import pytest

from legit.readiness import inspect_personas


def test_missing_artifacts_are_not_ready(sample_config, legit_dir):
    row = inspect_personas(sample_config, legit_dir)[0]
    assert row["artifact_status"] == "incomplete"
    assert row["quality_status"] == "unverified"


def test_corrupt_index_is_not_ready(sample_config, legit_dir):
    (legit_dir / "profiles/testuser.md").write_text("A profile")
    index = legit_dir / "index/testuser"
    index.mkdir()
    (index / "bm25.json").write_text('{"documents": [], "doc_count": 42}')
    row = inspect_personas(sample_config, legit_dir)[0]
    assert row["artifact_status"] == "incomplete"
    assert row["bm25_documents"] == 0


def test_artifacts_do_not_certify_quality(sample_config, legit_dir):
    (legit_dir / "profiles/testuser.md").write_text("A profile")
    index = legit_dir / "index/testuser"
    index.mkdir()
    (index / "bm25.json").write_text(
        json.dumps(
            {
                "documents": [{"comment_text": "Check the error"}],
                "doc_count": 1,
                "doc_lengths": [3],
                "inverted": {"error": {"0": 1}},
                "k1": 1.5,
                "b": 0.75,
                "avg_dl": 3.0,
            }
        )
    )
    row = inspect_personas(sample_config, legit_dir)[0]
    assert row["artifact_status"] == "present"
    assert row["quality_status"] == "unverified"


@pytest.mark.parametrize(
    "override",
    [
        {"k1": None},
        {"k1": 0},
        {"k1": True},
        {"b": 2},
        {"b": "0.75"},
        {"avg_dl": float("nan")},
        {"avg_dl": 0},
        {"avg_dl": 2},
        {"doc_count": True},
        {"doc_lengths": ["3"]},
        {"doc_lengths": [-3]},
        {"documents": [{}]},
        {"documents": [{"comment_text": 123}]},
        {"inverted": {}},
        {"inverted": {"error": []}},
        {"inverted": {"error": {"1": 1}}},
        {"inverted": {"error": {"-1": 1}}},
        {"inverted": {"error": {"bad": 1}}},
        {"inverted": {"error": {"00": 1}}},
        {"inverted": {"error": {"0": 0}}},
        {"inverted": {"error": {"0": "1"}}},
        {"inverted": {"error": {"0": 4}}},
    ],
)
def test_unusable_counted_index_is_not_ready(sample_config, legit_dir, override):
    (legit_dir / "profiles/testuser.md").write_text("A profile")
    path = legit_dir / "index/testuser"
    path.mkdir()
    index = {
        "documents": [{"comment_text": "Check the error"}],
        "doc_count": 1,
        "doc_lengths": [3],
        "inverted": {"error": {"0": 1}},
        "k1": 1.5,
        "b": 0.75,
        "avg_dl": 3.0,
    }
    index.update(override)
    (path / "bm25.json").write_text(json.dumps(index))
    assert inspect_personas(sample_config, legit_dir)[0]["artifact_status"] == "incomplete"


def test_missing_scoring_parameters_are_not_ready(sample_config, legit_dir):
    (legit_dir / "profiles/testuser.md").write_text("A profile")
    path = legit_dir / "index/testuser"
    path.mkdir()
    (path / "bm25.json").write_text(
        json.dumps(
            {
                "documents": [{"comment_text": "error"}],
                "doc_count": 1,
                "doc_lengths": [1],
                "inverted": {"error": {"0": 1}},
            }
        )
    )
    assert inspect_personas(sample_config, legit_dir)[0]["artifact_status"] == "incomplete"
