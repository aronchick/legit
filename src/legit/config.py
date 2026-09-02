"""Configuration models and loading for legit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    provider: str = "gemini"
    name: str | None = None
    temperature: float = 0.3
    # Non-default endpoint for the 'api' provider (ollama, vLLM, fine-tuned
    # models). Scoped here, not env-global, so a pinned judge model is never
    # routed to the generation model's endpoint.
    api_base: str | None = None


class GitHubConfig(BaseModel):
    token_env: str = "GITHUB_TOKEN"


class ProfileSource(BaseModel):
    type: str = "primary"
    repo: str
    username: str


class ProfileConfig(BaseModel):
    name: str
    sources: list[ProfileSource]
    chunk_size: int = 150
    temporal_half_life: int = 730
    map_concurrency: int = 4


class RetrievalConfig(BaseModel):
    top_k: int = 10
    index_type: str = "bm25"
    type_weights: dict[str, float] = Field(
        default_factory=lambda: {"pr_review": 2.0, "issue_comment": 1.0, "commit_comment": 0.5}
    )


class ReviewConfig(BaseModel):
    post_to_github: bool = False
    review_action: str = "COMMENT"
    max_comments: int | None = None
    abstention_threshold: float = 0.5


class CalibrationConfig(BaseModel):
    holdout_count: int = 15
    max_iterations: int = 5
    target_score: float = 8.0
    # Temporal train/eval split: profile builds only use activity strictly
    # before this date (YYYY-MM-DD); calibration only evaluates PRs created
    # and merged on/after it. Without it, retrieval can surface the reviewer's
    # real comments on the PR being evaluated.
    holdout_after: str | None = None


class LegitConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    profiles: list[ProfileConfig] = Field(default_factory=list)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)


LEGIT_DIR = ".legit"


def legit_path() -> Path:
    return Path(LEGIT_DIR)


def load_config(path: Path | None = None) -> LegitConfig:
    config_path = path or legit_path() / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found at {config_path}. Run 'legit init' first.")
    raw = yaml.safe_load(config_path.read_text())
    if raw is None:
        raw = {}
    cfg = LegitConfig.model_validate(raw)

    # Deployment overrides — lets a server pick its model backend without
    # dirtying the tracked config file.
    import os

    if os.environ.get("LEGIT_MODEL_PROVIDER"):
        cfg.model.provider = os.environ["LEGIT_MODEL_PROVIDER"]
    if os.environ.get("LEGIT_MODEL_NAME"):
        cfg.model.name = os.environ["LEGIT_MODEL_NAME"]
    if os.environ.get("LEGIT_MODEL_API_BASE"):
        cfg.model.api_base = os.environ["LEGIT_MODEL_API_BASE"]
    return cfg


def write_default_config(path: Path) -> None:
    default: dict[str, Any] = {
        "model": {"provider": "gemini"},
        "github": {"token_env": "GITHUB_TOKEN"},
        "profiles": [
            {
                "name": "example",
                "sources": [{"type": "primary", "repo": "owner/repo", "username": "reviewer"}],
            }
        ],
        "retrieval": {"top_k": 10},
        "review": {"post_to_github": False},
    }
    path.write_text(yaml.dump(default, default_flow_style=False, sort_keys=False))
