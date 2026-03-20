"""GitHub REST API client for legit — index and fetch reviewer activity."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from rich.console import Console

from legit.config import GitHubConfig, legit_path
from legit.models import CursorFile, CursorState, IndexEntry

console = Console(stderr=True)

BASE = "https://api.github.com"
PER_PAGE = 100
# Retry / backoff
MAX_RETRIES = 5
BACKOFF_BASE = 2.0  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repo(repo: str) -> tuple[str, str]:
    """Split 'owner/repo' into (owner, repo)."""
    parts = repo.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Expected 'owner/repo', got: {repo!r}")
    return parts[0], parts[1]


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Extract (owner, repo, pull_number) from a GitHub PR URL.

    Accepts forms like:
        https://github.com/owner/repo/pull/123
        https://github.com/owner/repo/pull/123/files
        https://api.github.com/repos/owner/repo/pulls/123
    """
    m = re.match(
        r"https?://(?:api\.)?github\.com/(?:repos/)?([^/]+)/([^/]+)/pulls?/(\d+)",
        url,
    )
    if not m:
        raise ValueError(f"Cannot parse PR URL: {url!r}")
    return m.group(1), m.group(2), int(m.group(3))


def _data_dir(owner: str, repo: str, username: str) -> Path:
    return legit_path() / "data" / f"{owner}_{repo}" / username


def _load_json(path: Path) -> list | dict:
    if path.exists():
        return json.loads(path.read_text())
    return []


def _save_json(path: Path, data: list | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def _load_index(path: Path) -> list[IndexEntry]:
    raw = _load_json(path)
    if not isinstance(raw, list):
        return []
    return [IndexEntry.model_validate(e) for e in raw]


def _save_index(path: Path, entries: list[IndexEntry]) -> None:
    _save_json(path, [e.model_dump(mode="json") for e in entries])


def _load_cursors(path: Path) -> CursorFile:
    if path.exists():
        return CursorFile.model_validate(json.loads(path.read_text()))
    return CursorFile()


def _save_cursors(path: Path, cf: CursorFile) -> None:
    _save_json(path, cf.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# HTTP transport with pagination, rate-limit handling, and retries
# ---------------------------------------------------------------------------


class GitHubTransport:
    """Low-level wrapper around httpx that handles auth, pagination, rate limits."""

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    # -- core request with retry + rate-limit backoff -----------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        for attempt in range(MAX_RETRIES):
            resp = self._client.request(method, path, params=params, headers=headers)

            # Success
            if resp.status_code < 400:
                return resp

            # Rate-limited (primary or secondary)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                wait = self._rate_limit_wait(resp, attempt)
                console.print(f"[yellow]Rate limited. Waiting {wait:.0f}s (attempt {attempt + 1})…[/]")
                time.sleep(wait)
                continue

            # 5xx — transient
            if resp.status_code >= 500:
                wait = BACKOFF_BASE ** attempt
                console.print(f"[yellow]Server error {resp.status_code}. Retrying in {wait:.0f}s…[/]")
                time.sleep(wait)
                continue

            # Hard error
            resp.raise_for_status()

        # Exhausted retries
        resp.raise_for_status()
        return resp  # unreachable but satisfies type checker

    def get(
        self,
        path: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        return self.request("GET", path, params=params, headers=headers)

    # -- paginated GET ------------------------------------------------------

    def get_paginated(
        self,
        path: str,
        *,
        params: dict | None = None,
        start_page: int = 1,
        per_page: int = PER_PAGE,
        on_page: None | type[None] = None,  # reserved for future callback
    ) -> tuple[list[dict], bool]:
        """Fetch all pages starting from *start_page*.

        Returns (items, exhausted).  *exhausted* is True when there are no
        more pages.
        """
        p = dict(params or {})
        p["per_page"] = per_page
        p["page"] = start_page
        items: list[dict] = []

        while True:
            resp = self.get(path, params=p)
            page_items = resp.json()
            if not isinstance(page_items, list):
                # Some endpoints wrap in an object; bail.
                break
            items.extend(page_items)

            next_url = _next_link(resp)
            if next_url is None or len(page_items) < per_page:
                return items, True  # no more pages

            p["page"] = p["page"] + 1

        return items, True

    # -- rate-limit helpers -------------------------------------------------

    @staticmethod
    def _rate_limit_wait(resp: httpx.Response, attempt: int) -> float:
        # Try Retry-After first (secondary rate limit)
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            return float(retry_after) + 1.0

        # Primary: use X-RateLimit-Reset
        reset_str = resp.headers.get("X-RateLimit-Reset")
        if reset_str:
            reset_at = int(reset_str)
            now = int(time.time())
            wait = max(reset_at - now, 0) + 1
            return float(wait)

        # Fallback: exponential backoff
        return BACKOFF_BASE ** (attempt + 1)

    def close(self) -> None:
        self._client.close()


def _next_link(resp: httpx.Response) -> str | None:
    """Parse the 'next' URL from the Link header."""
    link = resp.headers.get("Link", "")
    for part in link.split(","):
        if 'rel="next"' in part:
            m = re.search(r"<([^>]+)>", part)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def get_token(cfg: GitHubConfig) -> str:
    """Read the PAT from the environment variable named in config."""
    token = os.environ.get(cfg.token_env, "")
    if not token:
        raise EnvironmentError(
            f"GitHub token not found. Set the {cfg.token_env} environment variable."
        )
    return token


def validate_token(cfg: GitHubConfig) -> dict:
    """Validate the token and return the authenticated user payload."""
    token = get_token(cfg)
    transport = GitHubTransport(token)
    try:
        resp = transport.get("/user")
        resp.raise_for_status()
        return resp.json()
    finally:
        transport.close()


# ---------------------------------------------------------------------------
# GitHubClient — high-level operations
# ---------------------------------------------------------------------------


class GitHubClient:
    """Index and fetch a user's activity in a GitHub repo."""

    def __init__(self, cfg: GitHubConfig) -> None:
        self.cfg = cfg
        self._transport = GitHubTransport(get_token(cfg))

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -----------------------------------------------------------------------
    # Index all activity
    # -----------------------------------------------------------------------

    def index_activity(self, repo: str, username: str, skip_reviews: bool = False) -> list[IndexEntry]:
        """Index every discoverable activity type for *username* in *repo*.

        Resumes from cursor state if a previous run was interrupted.
        Returns the full, deduplicated index.
        """
        owner, name = _parse_repo(repo)
        ddir = _data_dir(owner, name, username)
        index_path = ddir / "index.json"
        cursor_path = ddir / "cursor.json"

        index = _load_index(index_path)
        seen: set[str] = {f"{e.type}:{e.id}" for e in index}
        cursors = _load_cursors(cursor_path)

        def _cursor(key: str) -> CursorState:
            if key not in cursors.cursors:
                cursors.cursors[key] = CursorState()
            return cursors.cursors[key]

        # -- PR review comments ---------------------------------------------
        self._index_endpoint(
            key="pr_comments",
            endpoint=f"/repos/{owner}/{name}/pulls/comments",
            params={"sort": "created", "direction": "asc"},
            username=username,
            user_field="user.login",
            entry_type="pr_comment",
            cursor=_cursor("pr_comments"),
            index=index,
            seen=seen,
        )

        # -- Issue comments -------------------------------------------------
        self._index_endpoint(
            key="issue_comments",
            endpoint=f"/repos/{owner}/{name}/issues/comments",
            params={"sort": "created", "direction": "asc"},
            username=username,
            user_field="user.login",
            entry_type="issue_comment",
            cursor=_cursor("issue_comments"),
            index=index,
            seen=seen,
        )

        # -- Commits --------------------------------------------------------
        self._index_endpoint(
            key="commits",
            endpoint=f"/repos/{owner}/{name}/commits",
            params={"author": username},
            username=username,
            user_field=None,  # already filtered by API param
            entry_type="commit",
            cursor=_cursor("commits"),
            index=index,
            seen=seen,
            id_field="sha",
            created_field="commit.author.date",
        )

        # -- Issues created by user -----------------------------------------
        self._index_endpoint(
            key="issues",
            endpoint=f"/repos/{owner}/{name}/issues",
            params={"creator": username, "sort": "created", "direction": "asc", "state": "all"},
            username=username,
            user_field=None,  # filtered by creator param
            entry_type="issue",
            cursor=_cursor("issues"),
            index=index,
            seen=seen,
        )

        # Persist after fast endpoints (reviews are slow — save progress first)
        _save_index(index_path, index)
        _save_cursors(cursor_path, cursors)

        # -- PR reviews (requires listing PRs first — slow on large repos) ---
        if skip_reviews:
            console.print("[dim]Skipping reviews (--skip-reviews)[/]")
        else:
            self._index_reviews(owner, name, username, _cursor("reviews"), index, seen)

        # Persist again after reviews
        _save_index(index_path, index)
        _save_cursors(cursor_path, cursors)

        console.print(f"[green]Indexed {len(index)} items for {username} in {repo}[/]")
        return index

    # -----------------------------------------------------------------------
    # Download full content for indexed items
    # -----------------------------------------------------------------------

    def download_content(self, repo: str, username: str) -> None:
        """Download full JSON blobs for every indexed-but-not-fetched item."""
        owner, name = _parse_repo(repo)
        ddir = _data_dir(owner, name, username)
        index_path = ddir / "index.json"
        index = _load_index(index_path)

        pending = [e for e in index if not e.fetched]
        if not pending:
            console.print("[dim]Nothing to download — all items already fetched.[/]")
            return

        # Group by type → bucket files
        buckets: dict[str, list[dict]] = {}
        for entry in pending:
            bucket_name = self._bucket_for_type(entry.type)
            if bucket_name not in buckets:
                existing = _load_json(ddir / f"{bucket_name}.json")
                buckets[bucket_name] = existing if isinstance(existing, list) else []

        fetched_count = 0
        for entry in pending:
            try:
                resp = self._transport.get(entry.url)
                payload = resp.json()
            except Exception as exc:
                console.print(f"[red]Failed to fetch {entry.type} {entry.id}: {exc}[/]")
                continue

            bucket_name = self._bucket_for_type(entry.type)
            buckets[bucket_name].append(payload)
            entry.fetched = True
            fetched_count += 1

            if fetched_count % 50 == 0:
                console.print(f"  fetched {fetched_count}/{len(pending)}…")

        # Save bucket files and updated index
        for bucket_name, items in buckets.items():
            _save_json(ddir / f"{bucket_name}.json", items)
        _save_index(index_path, index)

        console.print(f"[green]Downloaded {fetched_count} items for {username} in {repo}[/]")

    # -----------------------------------------------------------------------
    # Fetch PR data for review
    # -----------------------------------------------------------------------

    def fetch_pr_for_review(self, pr_url: str) -> dict:
        """Fetch everything needed to review a pull request.

        Returns a dict with keys: metadata, diff, files, comments, reviews,
        linked_issues.
        """
        owner, repo, pull_number = parse_pr_url(pr_url)
        base = f"/repos/{owner}/{repo}/pulls/{pull_number}"

        # Metadata
        meta_resp = self._transport.get(base)
        metadata = meta_resp.json()

        # Diff (raw text)
        diff_resp = self._transport.get(
            base,
            headers={"Accept": "application/vnd.github.diff"},
        )
        diff_text = diff_resp.text

        # Changed files
        files_items, _ = self._transport.get_paginated(f"{base}/files")

        # Inline / review comments
        comments_items, _ = self._transport.get_paginated(f"{base}/comments")

        # Reviews
        reviews_items, _ = self._transport.get_paginated(f"{base}/reviews")

        # Linked issues (parse body for #NNN references)
        linked_issues = self._resolve_linked_issues(owner, repo, metadata.get("body", "") or "")

        return {
            "metadata": metadata,
            "diff": diff_text,
            "files": files_items,
            "comments": comments_items,
            "reviews": reviews_items,
            "linked_issues": linked_issues,
        }

    # -----------------------------------------------------------------------
    # Internal: generic endpoint indexer
    # -----------------------------------------------------------------------

    def _index_endpoint(
        self,
        *,
        key: str,
        endpoint: str,
        params: dict,
        username: str,
        user_field: str | None,
        entry_type: str,
        cursor: CursorState,
        index: list[IndexEntry],
        seen: set[str],
        id_field: str = "id",
        created_field: str = "created_at",
    ) -> None:
        if cursor.complete:
            console.print(f"[dim]Skipping {key} (already complete)[/]")
            return

        console.print(f"Indexing [bold]{key}[/] from page {cursor.page}…")

        items, exhausted = self._transport.get_paginated(
            endpoint,
            params=params,
            start_page=cursor.page,
            per_page=cursor.per_page,
        )

        added = 0
        for item in items:
            # Filter by user if needed
            if user_field and not self._field_matches(item, user_field, username):
                continue

            item_id = self._resolve_field(item, id_field)
            uid = f"{entry_type}:{item_id}"
            if uid in seen:
                continue

            created_raw = self._resolve_field(item, created_field)
            created_at = _parse_dt(created_raw) if created_raw else datetime.now(tz=timezone.utc)
            updated_raw = item.get("updated_at")
            updated_at = _parse_dt(updated_raw) if updated_raw else None

            # Determine the API URL for fetching full content later
            api_url = item.get("url", "")

            pr_number: int | None = None
            if entry_type == "pr_comment":
                pr_number = self._extract_pr_number(item)
            elif entry_type == "review":
                pr_number = item.get("pull_request_number")

            entry = IndexEntry(
                id=item_id,
                type=entry_type,
                url=api_url,
                created_at=created_at,
                updated_at=updated_at,
                pr_number=pr_number,
            )
            index.append(entry)
            seen.add(uid)
            added += 1

        cursor.complete = exhausted
        if items:
            last_created = self._resolve_field(items[-1], created_field)
            if last_created:
                cursor.last_timestamp = _parse_dt(last_created)
        # Advance page bookmark for next resumption
        if not exhausted:
            total_pages = len(items) // cursor.per_page
            cursor.page += max(total_pages, 1)

        console.print(f"  {key}: +{added} new entries (complete={exhausted})")

    # -----------------------------------------------------------------------
    # Internal: reviews indexer (needs PR listing first)
    # -----------------------------------------------------------------------

    def _index_reviews(
        self,
        owner: str,
        repo: str,
        username: str,
        cursor: CursorState,
        index: list[IndexEntry],
        seen: set[str],
    ) -> None:
        if cursor.complete:
            console.print("[dim]Skipping reviews (already complete)[/]")
            return

        console.print(f"Indexing [bold]reviews[/] from page {cursor.page}…")

        # List PRs (we can't filter reviews by user globally, so list PRs then
        # fetch reviews per PR and filter by user)
        prs, exhausted = self._transport.get_paginated(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "all", "sort": "created", "direction": "asc"},
            start_page=cursor.page,
        )

        added = 0
        for pr in prs:
            pr_number = pr.get("number")
            if pr_number is None:
                continue

            reviews_resp = self._transport.get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            )
            reviews = reviews_resp.json()
            if not isinstance(reviews, list):
                continue

            for rev in reviews:
                if (rev.get("user") or {}).get("login", "").lower() != username.lower():
                    continue

                rid = rev.get("id")
                uid = f"review:{rid}"
                if uid in seen:
                    continue

                created_raw = rev.get("submitted_at") or rev.get("created_at")
                created_at = _parse_dt(created_raw) if created_raw else datetime.now(tz=timezone.utc)

                entry = IndexEntry(
                    id=rid,
                    type="review",
                    url=rev.get("url", ""),
                    created_at=created_at,
                    pr_number=pr_number,
                )
                index.append(entry)
                seen.add(uid)
                added += 1

        cursor.complete = exhausted
        if not exhausted:
            total_pages = len(prs) // cursor.per_page
            cursor.page += max(total_pages, 1)

        console.print(f"  reviews: +{added} new entries (complete={exhausted})")

    # -----------------------------------------------------------------------
    # Internal: linked-issue resolution
    # -----------------------------------------------------------------------

    def _resolve_linked_issues(self, owner: str, repo: str, body: str) -> list[dict]:
        """Pull full issue data for every #NNN reference in the PR body."""
        refs = set(re.findall(r"#(\d+)", body))
        issues = []
        for num in refs:
            try:
                resp = self._transport.get(f"/repos/{owner}/{repo}/issues/{num}")
                if resp.status_code < 400:
                    issues.append(resp.json())
            except Exception:
                pass
        return issues

    # -----------------------------------------------------------------------
    # Internal: field helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _resolve_field(item: dict, dotted_key: str) -> str | int | None:
        """Resolve 'user.login' style dotted keys."""
        parts = dotted_key.split(".")
        obj: dict | str | int | None = item
        for p in parts:
            if isinstance(obj, dict):
                obj = obj.get(p)
            else:
                return None
        return obj  # type: ignore[return-value]

    @staticmethod
    def _field_matches(item: dict, dotted_key: str, value: str) -> bool:
        resolved = GitHubClient._resolve_field(item, dotted_key)
        if isinstance(resolved, str):
            return resolved.lower() == value.lower()
        return False

    @staticmethod
    def _extract_pr_number(item: dict) -> int | None:
        """Extract PR number from a pull-request review comment."""
        pr_url = item.get("pull_request_url", "")
        if pr_url:
            m = re.search(r"/pulls/(\d+)", pr_url)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _bucket_for_type(entry_type: str) -> str:
        mapping = {
            "pr_comment": "pr_comments",
            "issue_comment": "issue_comments",
            "commit": "commits",
            "review": "reviews",
            "issue": "issues",
        }
        return mapping.get(entry_type, entry_type)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _parse_dt(raw: str | None) -> datetime:
    """Parse an ISO-8601 datetime string from the GitHub API."""
    if not raw:
        return datetime.now(tz=timezone.utc)
    # GitHub returns e.g. '2024-01-15T08:30:00Z'
    cleaned = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)
