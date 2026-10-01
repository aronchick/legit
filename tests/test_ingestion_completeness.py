"""Regression tests for complete, refreshable reviewer discovery."""

from unittest.mock import MagicMock

import pytest

from legit.github_client import GitHubClient, GitHubTransport
from legit.models import CursorState


def response(total, numbers, **extra):
    result = MagicMock()
    result.json.return_value = {
        "total_count": total,
        "items": [{"number": n} for n in numbers],
        **extra,
    }
    return result


def client_with_search(pages):
    client = GitHubClient.__new__(GitHubClient)
    client._transport = MagicMock()
    client._transport.get.side_effect = pages
    client._transport.get_paginated.return_value = ([], True)
    return client


def discover(client, cursor=None, since=None):
    cursor = cursor or CursorState()
    index = []
    client._index_pr_comments_via_search("org", "repo", "reviewer", since, cursor, index, set())
    return cursor, index


def test_search_fetches_last_page_and_all_comment_pages():
    client = client_with_search([response(150, range(1, 101)), response(150, range(101, 151))])
    comment = {"id": 1, "user": {"login": "reviewer"}, "created_at": "2025-01-01T00:00:00Z"}
    client._transport.get_paginated.return_value = ([comment], True)
    cursor, index = discover(client)
    assert cursor.complete
    assert len(index) == 1  # repeated comment IDs are deduplicated
    assert client._transport.get.call_count == 2
    assert client._transport.get_paginated.call_count == 150


def test_search_partitions_capped_ranges():
    client = client_with_search([response(1001, []), response(1, [1]), response(1, [2])])
    assert discover(client)[0].complete
    queries = [call.kwargs["params"]["q"] for call in client._transport.get.call_args_list]
    assert len(set(queries)) == 3
    assert client._transport.get_paginated.call_count == 2


def test_dense_single_day_fails_without_marking_complete():
    client = client_with_search(None)
    client._transport.get.side_effect = None
    client._transport.get.return_value = response(1001, [])
    cursor = CursorState()
    with pytest.raises(RuntimeError, match="exceeds 1000"):
        discover(client, cursor)
    assert not cursor.complete


@pytest.mark.parametrize(
    "page",
    [
        response(1, [1], incomplete_results=True),
        response(150, [1]),
        response(2, [1]),
    ],
)
def test_partial_search_fails_without_marking_complete(page):
    client = client_with_search([page])
    cursor = CursorState()
    with pytest.raises(RuntimeError):
        discover(client, cursor)
    assert not cursor.complete


def test_per_pr_failure_is_not_silently_skipped():
    client = client_with_search([response(1, [7])])
    client._transport.get_paginated.side_effect = RuntimeError("unavailable")
    cursor = CursorState()
    with pytest.raises(RuntimeError, match="PR #7"):
        discover(client, cursor)
    assert not cursor.complete


def test_since_includes_recent_activity_on_old_prs():
    client = client_with_search([response(0, [])])
    discover(client, since="2025-01-01")
    query = client._transport.get.call_args.kwargs["params"]["q"]
    assert "updated:>=2025-01-01" in query
    assert "created:>2025-01-01" not in query


def test_invalid_list_endpoint_response_fails():
    transport = GitHubTransport.__new__(GitHubTransport)
    transport.get = MagicMock(return_value=response(0, []))
    with pytest.raises(RuntimeError, match="Expected a list"):
        transport.get_paginated("/comments")


def test_refresh_revisits_complete_cursors_without_losing_index(legit_dir):
    client = GitHubClient.__new__(GitHubClient)
    client._transport = MagicMock()
    comment = {"id": 1, "user": {"login": "reviewer"}, "created_at": "2025-01-01T00:00:00Z"}
    client._transport.get_paginated.side_effect = [([comment], True)] + [([], True)] * 3
    first = client.index_activity("org/repo", "reviewer", skip_reviews=True)
    assert len(first) == 1
    client._transport.get_paginated.reset_mock(side_effect=True)
    client._transport.get_paginated.return_value = ([], True)
    client.index_activity("org/repo", "reviewer", skip_reviews=True)
    assert client._transport.get_paginated.call_count == 0
    refreshed = client.index_activity("org/repo", "reviewer", skip_reviews=True, refresh=True)
    assert client._transport.get_paginated.call_count == 4
    assert refreshed == first


@pytest.mark.parametrize("repository", [{"private": True}, {}, None, {"private": "false"}])
def test_public_only_review_stops_before_fetching_code(repository):
    client = client_with_search(None)
    client._transport.get.side_effect = None
    client._transport.get.return_value.json.return_value = {"base": {"repo": repository}}
    with pytest.raises(ValueError, match="only supports public"):
        client.fetch_pr_for_review("https://github.com/org/repo/pull/1", require_public=True)
    assert client._transport.get.call_count == 1
    client._transport.get_paginated.assert_not_called()


def test_public_only_review_accepts_explicit_public_metadata():
    client = client_with_search(None)
    client._transport.get.side_effect = None
    client._transport.get.return_value.json.return_value = {"base": {"repo": {"private": False}}}
    result = client.fetch_pr_for_review("https://github.com/org/repo/pull/1", require_public=True)
    assert result["metadata"]["base"]["repo"]["private"] is False
    assert client._transport.get.call_count == 2
