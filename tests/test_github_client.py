"""Tests for legit.github_client — URL parsing, transport, pagination, indexing."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from legit.config import GitHubConfig
from legit.github_client import (
    GitHubClient,
    GitHubTransport,
    _next_link,
    _parse_dt,
    _parse_repo,
    get_token,
    parse_pr_url,
)


# ---------------------------------------------------------------------------
# parse_pr_url()
# ---------------------------------------------------------------------------


class TestParsePrUrl:
    def test_standard_url(self):
        owner, repo, num = parse_pr_url("https://github.com/octocat/hello-world/pull/42")
        assert owner == "octocat"
        assert repo == "hello-world"
        assert num == 42

    def test_url_with_files_suffix(self):
        owner, repo, num = parse_pr_url(
            "https://github.com/octocat/hello-world/pull/123/files"
        )
        assert owner == "octocat"
        assert repo == "hello-world"
        assert num == 123

    def test_api_url(self):
        owner, repo, num = parse_pr_url(
            "https://api.github.com/repos/octocat/hello-world/pulls/99"
        )
        assert owner == "octocat"
        assert repo == "hello-world"
        assert num == 99

    def test_http_url(self):
        owner, repo, num = parse_pr_url("http://github.com/org/repo/pull/1")
        assert owner == "org"
        assert repo == "repo"
        assert num == 1

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse PR URL"):
            parse_pr_url("https://github.com/octocat/hello-world")

    def test_random_string_raises(self):
        with pytest.raises(ValueError, match="Cannot parse PR URL"):
            parse_pr_url("not a url at all")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Cannot parse PR URL"):
            parse_pr_url("")


# ---------------------------------------------------------------------------
# _parse_repo()
# ---------------------------------------------------------------------------


class TestParseRepo:
    def test_valid(self):
        assert _parse_repo("octocat/hello-world") == ("octocat", "hello-world")

    def test_invalid_no_slash(self):
        with pytest.raises(ValueError, match="Expected 'owner/repo'"):
            _parse_repo("noslash")

    def test_invalid_empty_parts(self):
        with pytest.raises(ValueError, match="Expected 'owner/repo'"):
            _parse_repo("/repo")


# ---------------------------------------------------------------------------
# _parse_dt()
# ---------------------------------------------------------------------------


class TestParseDt:
    def test_github_format(self):
        dt = _parse_dt("2025-01-15T08:30:00Z")
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15

    def test_none_returns_now(self):
        dt = _parse_dt(None)
        assert dt is not None  # should return current time

    def test_empty_returns_now(self):
        dt = _parse_dt("")
        assert dt is not None


# ---------------------------------------------------------------------------
# get_token()
# ---------------------------------------------------------------------------


class TestGetToken:
    def test_reads_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        cfg = GitHubConfig(token_env="GITHUB_TOKEN")
        assert get_token(cfg) == "ghp_test123"

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        cfg = GitHubConfig(token_env="GITHUB_TOKEN")
        with pytest.raises(EnvironmentError, match="GitHub token not found"):
            get_token(cfg)

    def test_custom_env_var(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_GH_TOKEN", "ghp_custom")
        cfg = GitHubConfig(token_env="MY_GH_TOKEN")
        assert get_token(cfg) == "ghp_custom"


# ---------------------------------------------------------------------------
# _next_link() — Link header parsing
# ---------------------------------------------------------------------------


class TestNextLink:
    def test_with_next_link(self):
        resp = MagicMock()
        resp.headers = {
            "Link": '<https://api.github.com/repos/o/r/pulls?page=2>; rel="next", '
                    '<https://api.github.com/repos/o/r/pulls?page=10>; rel="last"'
        }
        assert _next_link(resp) == "https://api.github.com/repos/o/r/pulls?page=2"

    def test_without_next_link(self):
        resp = MagicMock()
        resp.headers = {
            "Link": '<https://api.github.com/repos/o/r/pulls?page=1>; rel="prev"'
        }
        assert _next_link(resp) is None

    def test_no_link_header(self):
        resp = MagicMock()
        resp.headers = {}
        assert _next_link(resp) is None

    def test_empty_link_header(self):
        resp = MagicMock()
        resp.headers = {"Link": ""}
        assert _next_link(resp) is None


# ---------------------------------------------------------------------------
# GitHubTransport — rate limit handling (mocked httpx)
# ---------------------------------------------------------------------------


class TestGitHubTransportRateLimit:
    def test_rate_limit_wait_retry_after(self):
        resp = MagicMock()
        resp.headers = {"Retry-After": "60"}
        wait = GitHubTransport._rate_limit_wait(resp, attempt=0)
        assert wait == 61.0  # 60 + 1

    def test_rate_limit_wait_reset_header(self):
        now = int(time.time())
        resp = MagicMock()
        resp.headers = {"X-RateLimit-Reset": str(now + 30)}
        wait = GitHubTransport._rate_limit_wait(resp, attempt=0)
        assert 29 <= wait <= 32  # approximately 30 + 1

    def test_rate_limit_wait_fallback_backoff(self):
        resp = MagicMock()
        resp.headers = {}
        wait = GitHubTransport._rate_limit_wait(resp, attempt=2)
        # BACKOFF_BASE ** (attempt + 1) = 2.0 ** 3 = 8.0
        assert wait == 8.0


# ---------------------------------------------------------------------------
# GitHubTransport — pagination (mocked)
# ---------------------------------------------------------------------------


class TestGitHubTransportPagination:
    @patch("legit.github_client.GitHubTransport.get")
    def test_single_page(self, mock_get: MagicMock):
        items = [{"id": 1}, {"id": 2}]
        resp = MagicMock()
        resp.json.return_value = items
        resp.headers = {}
        mock_get.return_value = resp

        transport = GitHubTransport.__new__(GitHubTransport)
        result, exhausted = transport.get_paginated("/test", per_page=100)
        assert result == items
        assert exhausted is True

    @patch("legit.github_client.GitHubTransport.get")
    def test_multi_page(self, mock_get: MagicMock):
        page1 = [{"id": i} for i in range(100)]
        page2 = [{"id": i} for i in range(100, 150)]

        resp1 = MagicMock()
        resp1.json.return_value = page1
        resp1.headers = {"Link": '<https://api.github.com/test?page=2>; rel="next"'}

        resp2 = MagicMock()
        resp2.json.return_value = page2
        resp2.headers = {}

        mock_get.side_effect = [resp1, resp2]

        transport = GitHubTransport.__new__(GitHubTransport)
        result, exhausted = transport.get_paginated("/test", per_page=100)
        assert len(result) == 150
        assert exhausted is True

    @patch("legit.github_client.GitHubTransport.get")
    def test_empty_response(self, mock_get: MagicMock):
        resp = MagicMock()
        resp.json.return_value = []
        resp.headers = {}
        mock_get.return_value = resp

        transport = GitHubTransport.__new__(GitHubTransport)
        result, exhausted = transport.get_paginated("/test")
        assert result == []
        assert exhausted is True


# ---------------------------------------------------------------------------
# GitHubClient — index_activity saves to correct paths (mocked)
# ---------------------------------------------------------------------------


class TestGitHubClientIndexActivity:
    @patch("legit.github_client.GitHubTransport.get_paginated")
    @patch("legit.github_client.GitHubTransport.get")
    @patch("legit.github_client.get_token", return_value="ghp_test")
    def test_index_activity_creates_files(
        self,
        mock_token: MagicMock,
        mock_get: MagicMock,
        mock_paginated: MagicMock,
        legit_dir: Path,
    ):
        # All paginated endpoints return empty lists (no activity)
        mock_paginated.return_value = ([], True)

        cfg = GitHubConfig(token_env="GITHUB_TOKEN")
        client = GitHubClient(cfg)

        entries = client.index_activity("octocat/hello-world", "testuser")

        # Should have created data directory and index file
        data_dir = legit_dir / "data" / "octocat_hello-world" / "testuser"
        assert data_dir.exists()
        index_file = data_dir / "index.json"
        assert index_file.exists()

        # With no data returned, index should be empty
        assert len(entries) == 0

    @patch("legit.github_client.GitHubTransport.get_paginated")
    @patch("legit.github_client.GitHubTransport.get")
    @patch("legit.github_client.get_token", return_value="ghp_test")
    def test_index_activity_with_items(
        self,
        mock_token: MagicMock,
        mock_get: MagicMock,
        mock_paginated: MagicMock,
        legit_dir: Path,
    ):
        # Simulate pr_comments returning some items, everything else empty
        def paginated_side_effect(endpoint, **kwargs):
            if "pulls/comments" in endpoint:
                return (
                    [
                        {
                            "id": 1001,
                            "user": {"login": "testuser"},
                            "created_at": "2025-01-15T10:00:00Z",
                            "updated_at": "2025-01-15T10:00:00Z",
                            "url": "https://api.github.com/repos/o/r/pulls/comments/1001",
                            "pull_request_url": "https://api.github.com/repos/o/r/pulls/42",
                        }
                    ],
                    True,
                )
            return ([], True)

        mock_paginated.side_effect = paginated_side_effect

        cfg = GitHubConfig(token_env="GITHUB_TOKEN")
        client = GitHubClient(cfg)
        entries = client.index_activity("octocat/hello-world", "testuser", skip_reviews=True)

        # Should have indexed the PR comment
        pr_entries = [e for e in entries if e.type == "pr_comment"]
        assert len(pr_entries) == 1
        assert pr_entries[0].id == 1001
        assert pr_entries[0].pr_number == 42


# ---------------------------------------------------------------------------
# GitHubClient — field helpers
# ---------------------------------------------------------------------------


class TestFieldHelpers:
    def test_resolve_field_simple(self):
        item = {"id": 42, "name": "test"}
        assert GitHubClient._resolve_field(item, "id") == 42

    def test_resolve_field_nested(self):
        item = {"user": {"login": "alice"}}
        assert GitHubClient._resolve_field(item, "user.login") == "alice"

    def test_resolve_field_missing(self):
        item = {"user": {"login": "alice"}}
        assert GitHubClient._resolve_field(item, "user.email") is None

    def test_resolve_field_deeply_nested(self):
        item = {"commit": {"author": {"date": "2025-01-01"}}}
        assert GitHubClient._resolve_field(item, "commit.author.date") == "2025-01-01"

    def test_field_matches_case_insensitive(self):
        item = {"user": {"login": "Alice"}}
        assert GitHubClient._field_matches(item, "user.login", "alice") is True
        assert GitHubClient._field_matches(item, "user.login", "ALICE") is True

    def test_field_matches_false(self):
        item = {"user": {"login": "alice"}}
        assert GitHubClient._field_matches(item, "user.login", "bob") is False

    def test_extract_pr_number(self):
        item = {"pull_request_url": "https://api.github.com/repos/o/r/pulls/42"}
        assert GitHubClient._extract_pr_number(item) == 42

    def test_extract_pr_number_missing(self):
        item = {}
        assert GitHubClient._extract_pr_number(item) is None

    def test_bucket_for_type(self):
        assert GitHubClient._bucket_for_type("pr_comment") == "pr_comments"
        assert GitHubClient._bucket_for_type("issue_comment") == "issue_comments"
        assert GitHubClient._bucket_for_type("commit") == "commits"
        assert GitHubClient._bucket_for_type("review") == "reviews"
        assert GitHubClient._bucket_for_type("issue") == "issues"
        assert GitHubClient._bucket_for_type("unknown_type") == "unknown_type"
