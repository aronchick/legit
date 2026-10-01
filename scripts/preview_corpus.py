"""Fetch a bounded, reproducible public-PR sample; never claim full history."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from legit.config import load_config
from legit.github_client import GitHubClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--max-prs", type=int, default=20)
    parser.add_argument("--before", default="2026-04-01")
    args = parser.parse_args()
    if not 1 <= args.max_prs <= 100:
        parser.error("--max-prs must be 1..100")
    datetime.strptime(args.before, "%Y-%m-%d")
    cfg = load_config()
    profile = next(p for p in cfg.profiles if p.name == args.profile)
    if len(profile.sources) != 1:
        raise ValueError("Preview sample currently requires one explicit source")
    source = profile.sources[0]
    source_key = hashlib.sha256(f"{source.repo}:{source.username}".encode()).hexdigest()[:16]
    cache = Path(".legit/cache/preview-corpus") / args.profile / source_key / args.before
    cache.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[dict]] = {"pr_comments": [], "issue_comments": []}
    with GitHubClient(cfg.github) as gh:
        repository = gh._transport.get(f"/repos/{source.repo}").json()
        if repository.get("private") is not False:
            raise ValueError("Only public repository samples are supported")
        query = (
            f"repo:{source.repo} commenter:{source.username} type:pr "
            f"-author:{source.username} updated:<{args.before}"
        )
        discovery = cache / f"discovery-{args.max_prs}.json"
        if discovery.exists():
            found = json.loads(discovery.read_text())
        else:
            found = gh._transport.get(
                "/search/issues",
                params={
                    "q": query,
                    "per_page": args.max_prs,
                    "sort": "updated",
                    "order": "desc",
                },
            ).json()
            if found.get("incomplete_results"):
                raise RuntimeError("GitHub returned incomplete search results")
            discovery.write_text(json.dumps(found))
        numbers = [pr["number"] for pr in found["items"]]
        for number in numbers:
            for bucket, endpoint in (
                ("pr_comments", f"/repos/{source.repo}/pulls/{number}/comments"),
                ("issue_comments", f"/repos/{source.repo}/issues/{number}/comments"),
            ):
                cached = cache / f"{number}-{bucket}.json"
                if cached.exists():
                    comments = json.loads(cached.read_text())
                else:
                    comments, exhausted = gh._transport.get_paginated(endpoint)
                    if not exhausted:
                        raise RuntimeError(f"Incomplete comments for PR {number}")
                    cached.write_text(json.dumps(comments))
                for comment in comments:
                    if (
                        (comment.get("user") or {}).get("login", "").lower()
                        == source.username.lower()
                        and str(comment.get("created_at", "9999"))[:10] < args.before
                        and str(comment.get("updated_at", "9999"))[:10] < args.before
                    ):
                        buckets[bucket].append({**comment, "pr_number": number})
            print(f"{args.profile}: fetched PR {number}", flush=True)
    target = Path(".legit/data") / source.repo.replace("/", "_") / source.username
    target.mkdir(parents=True, exist_ok=True)
    for bucket, comments in buckets.items():
        unique = {comment["id"]: comment for comment in comments}
        (target / f"{bucket}.json").write_text(json.dumps(list(unique.values()), indent=2))
    manifest = {
        "profile": args.profile,
        "repo": source.repo,
        "query": query,
        "scope": "bounded sample, not full history",
        "before": args.before,
        "search_total": found["total_count"],
        "sample_prs": numbers,
        "counts": {key: len(value) for key, value in buckets.items()},
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    Path("evidence").mkdir(exist_ok=True)
    (Path("evidence") / f"corpus-{args.profile}.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
