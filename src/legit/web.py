"""Web UI for legit — paste a PR URL, get a review."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import random
import threading
import time
import traceback
from functools import partial
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from legit.config import LegitConfig, load_config
from legit.review import generate_review

logger = logging.getLogger(__name__)

app = FastAPI(title="legit", description="PR reviews in a learned reviewer's voice")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


# ---------------------------------------------------------------------------
# Sample PRs — real K8s PRs with substantial review discussion
# ---------------------------------------------------------------------------

SAMPLE_PRS: list[dict] = [
    # --- thockin ---
    {"number": 125488, "title": "DRA for 1.31", "reviewer": "thockin", "repo": "kubernetes/kubernetes", "comments": 731},
    {"number": 124012, "title": "Coordinated Leader Election", "reviewer": "thockin", "repo": "kubernetes/kubernetes", "comments": 401},
    {"number": 136589, "title": "Add Workload-Aware Preemption fields", "reviewer": "thockin", "repo": "kubernetes/kubernetes", "comments": 198},
    {"number": 128407, "title": "Pod Level Resources Feature Alpha", "reviewer": "thockin", "repo": "kubernetes/kubernetes", "comments": 278},
    {"number": 102884, "title": "In-place Pod Vertical Scaling feature", "reviewer": "thockin", "repo": "kubernetes/kubernetes", "comments": 594},
    # --- liggitt ---
    {"number": 128152, "title": "Multi-tenancy in accessing node images via Pod API", "reviewer": "liggitt", "repo": "kubernetes/kubernetes", "comments": 241},
    {"number": 128190, "title": "Add ExternalJWTSigner integration", "reviewer": "liggitt", "repo": "kubernetes/kubernetes", "comments": 150},
    {"number": 128010, "title": "Pod Certificates: KEP-4317 implementation", "reviewer": "liggitt", "repo": "kubernetes/kubernetes", "comments": 180},
    {"number": 125230, "title": "Introduce kuberc for kubectl customization", "reviewer": "liggitt", "repo": "kubernetes/kubernetes", "comments": 230},
    {"number": 132919, "title": "Pod level in-place resize — alpha", "reviewer": "liggitt", "repo": "kubernetes/kubernetes", "comments": 179},
    # --- lavalamp ---
    {"number": 113985, "title": "Propagate HasSynced properly", "reviewer": "lavalamp", "repo": "kubernetes/kubernetes", "comments": 120},
    {"number": 112377, "title": "Refactor sets to use generics", "reviewer": "lavalamp", "repo": "kubernetes/kubernetes", "comments": 86},
    {"number": 112858, "title": "CEL Admission Plugin", "reviewer": "lavalamp", "repo": "kubernetes/kubernetes", "comments": 95},
    {"number": 111978, "title": "Aggregated discovery types", "reviewer": "lavalamp", "repo": "kubernetes/kubernetes", "comments": 72},
    {"number": 115402, "title": "Add API for watch list", "reviewer": "lavalamp", "repo": "kubernetes/kubernetes", "comments": 88},
    {"number": 115620, "title": "client-go/cache: fix missing delete event on replace", "reviewer": "lavalamp", "repo": "kubernetes/kubernetes", "comments": 26},
    # --- bgrant0607 ---
    {"number": 18016, "title": "Proposal for StatefulSets (nominal services)", "reviewer": "bgrant0607", "repo": "kubernetes/kubernetes", "comments": 210},
    {"number": 20273, "title": "Proportionally scale paused/rolling deployments", "reviewer": "bgrant0607", "repo": "kubernetes/kubernetes", "comments": 145},
    {"number": 6477, "title": "Config Resource Proposal", "reviewer": "bgrant0607", "repo": "kubernetes/kubernetes", "comments": 130},
    {"number": 1325, "title": "Proposal for new kubecfg design (kubectl)", "reviewer": "bgrant0607", "repo": "kubernetes/kubernetes", "comments": 95},
    {"number": 18215, "title": "Initial template and parameterization proposal", "reviewer": "bgrant0607", "repo": "kubernetes/kubernetes", "comments": 171},
    {"number": 5093, "title": "Downward API volume plugin", "reviewer": "bgrant0607", "repo": "kubernetes/kubernetes", "comments": 198},
]

# Map reviewer GitHub handles to profile names
_REVIEWER_TO_PROFILE = {
    "thockin": "thockin",
    "liggitt": "liggitt",
    "lavalamp": "lavalamp",
    "bgrant0607": "bgrant0607",
}


def _render(context: dict) -> HTMLResponse:
    """Render the template with Jinja2 directly (avoids Starlette cache issues)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_PATH.parent)))
    template = env.get_template("index.html")
    return HTMLResponse(template.render(**context))


def _load_cfg() -> LegitConfig:
    return load_config()


def _sample_prs_for_template() -> list[dict]:
    """Return the sample PR list enriched with URLs for the template."""
    enriched = []
    for pr in SAMPLE_PRS:
        enriched.append({
            **pr,
            "pr_url": f"https://github.com/{pr['repo']}/pull/{pr['number']}",
            "profile_name": _REVIEWER_TO_PROFILE.get(pr["reviewer"], pr["reviewer"]),
        })
    return enriched


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    cfg = _load_cfg()
    profiles = [p.name for p in cfg.profiles]
    # Pick 6 random samples to show on the landing page
    samples = random.sample(SAMPLE_PRS, min(6, len(SAMPLE_PRS)))
    for s in samples:
        s["pr_url"] = f"https://github.com/{s['repo']}/pull/{s['number']}"
        s["profile_name"] = _REVIEWER_TO_PROFILE.get(s["reviewer"], s["reviewer"])
    return _render({
        "profiles": profiles,
        "review": None,
        "error": None,
        "pr_url": "",
        "sample_prs": samples,
        "all_sample_prs": _sample_prs_for_template(),
    })


@app.get("/review", response_class=HTMLResponse)
async def review_get(
    request: Request,
    pr_url: str = Query(...),
    profile_name: str = Query(...),
) -> HTMLResponse:
    """GET-based review — used by sample PR links to auto-trigger a review."""
    return await _do_review(pr_url, profile_name)


@app.post("/review", response_class=HTMLResponse)
async def review(request: Request, pr_url: str = Form(...), profile_name: str = Form(...)) -> HTMLResponse:
    return await _do_review(pr_url, profile_name)


async def _do_review(pr_url: str, profile_name: str) -> HTMLResponse:
    """Shared review logic for both GET and POST endpoints."""
    cfg = _load_cfg()
    profiles = [p.name for p in cfg.profiles]

    # Set GITHUB_TOKEN if not in env — try gh auth token
    if not os.environ.get(cfg.github.token_env):
        try:
            import subprocess
            token = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if token:
                os.environ[cfg.github.token_env] = token
        except Exception:
            pass

    # Find the original PR discussion URL for sample PRs
    original_pr_url = pr_url  # link to the PR itself for comparison

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            partial(
                generate_review,
                config=cfg,
                profile_name=profile_name,
                pr_url=pr_url,
                dry_run=True,
            ),
        )

        review_data = {
            "summary": result.summary,
            "inline_comments": [
                {
                    "file": c.file,
                    "hunk_header": c.hunk_header,
                    "diff_snippet": c.diff_snippet,
                    "comment": c.comment,
                    "confidence": c.confidence,
                    "side": c.side,
                }
                for c in result.inline_comments
            ],
            "abstained_files": result.abstained_files,
            "abstention_reason": result.abstention_reason,
        }

        return _render({
            "profiles": profiles,
            "review": review_data,
            "error": None,
            "pr_url": pr_url,
            "profile_name": profile_name,
            "original_pr_url": original_pr_url,
            "sample_prs": [],
            "all_sample_prs": _sample_prs_for_template(),
        })
    except Exception as exc:
        tb = traceback.format_exc()
        return _render({
            "profiles": profiles,
            "review": None,
            "error": f"{exc}\n\n{tb}",
            "pr_url": pr_url,
            "profile_name": profile_name,
            "sample_prs": [],
            "all_sample_prs": _sample_prs_for_template(),
        })


# ---------------------------------------------------------------------------
# SSE streaming review endpoint
# ---------------------------------------------------------------------------


def _ensure_github_token(cfg: LegitConfig) -> None:
    """Set GITHUB_TOKEN from gh CLI if not already in env."""
    if not os.environ.get(cfg.github.token_env):
        try:
            import subprocess
            token = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if token:
                os.environ[cfg.github.token_env] = token
        except Exception:
            pass


def _run_review_with_progress(
    cfg: LegitConfig,
    profile_name: str,
    pr_url: str,
    progress_q: queue.Queue,
) -> None:
    """Run the review pipeline, posting progress events to *progress_q*.

    Each event is a dict: {"step": str, "detail": str, "data": dict|None}
    The final event has step="done" or step="error".
    """
    from legit.github_client import GitHubClient
    from legit.model_runner import run_inference
    from legit.models import ReviewOutput
    from legit.retrieval import construct_queries, format_examples, retrieve
    from legit.review import (
        _apply_filters,
        _build_system_prompt,
        _build_user_prompt,
        _format_existing_threads,
        _parse_diff_hunks,
        _run_self_critique,
        load_profile,
    )

    try:
        # Step 1: Load profile + expertise index
        progress_q.put({"step": "profile", "status": "running", "detail": f"Loading reviewer profile: {profile_name}"})
        t0 = time.time()
        profile_document = load_profile(profile_name)

        from legit.expertise import format_expertise_context, load_expertise_index, lookup_expertise
        expertise_index = load_expertise_index(profile_name)
        idx_detail = f", expertise index ({len(expertise_index.entries)} dirs)" if expertise_index else ""
        progress_q.put({"step": "profile", "status": "done", "detail": f"Profile loaded ({len(profile_document)//1024}KB){idx_detail}", "elapsed": round(time.time() - t0, 1)})

        # Step 2: Fetch PR
        progress_q.put({"step": "fetch", "status": "running", "detail": "Fetching PR from GitHub..."})
        t0 = time.time()
        with GitHubClient(cfg.github) as gh:
            pr_data = gh.fetch_pr_for_review(pr_url)
            pr_title = pr_data["metadata"].get("title", "(untitled)")
            file_count = len(pr_data.get("files", []))
            diff_kb = len(pr_data.get("diff", "")) // 1024
            progress_q.put({"step": "fetch", "status": "done", "detail": f"{pr_title} — {file_count} files, {diff_kb}KB diff", "elapsed": round(time.time() - t0, 1)})

            # Step 2b: Fetch codebase context
            progress_q.put({"step": "context", "status": "running", "detail": f"Fetching full source files for {file_count} changed files..."})
            t0 = time.time()
            context_files = gh.fetch_pr_context_files(pr_url, pr_data)
            ctx_kb = sum(len(v) for v in context_files.values()) // 1024
            progress_q.put({"step": "context", "status": "done", "detail": f"Fetched {len(context_files)} files ({ctx_kb}KB) for codebase awareness", "elapsed": round(time.time() - t0, 1)})

        # Step 3: BM25 retrieval
        progress_q.put({"step": "retrieve", "status": "running", "detail": "Searching reviewer's past comments..."})
        t0 = time.time()
        diff_hunks = _parse_diff_hunks(pr_data.get("diff", ""))
        queries = construct_queries(diff_hunks)

        temporal_half_life = 730
        for pc in cfg.profiles:
            if pc.name == profile_name:
                temporal_half_life = pc.temporal_half_life
                break

        pr_changed_files = [f.get("filename", "") for f in pr_data.get("files", [])]
        retrieved_docs = retrieve(
            profile_name=profile_name,
            queries=queries,
            top_k=cfg.retrieval.top_k,
            type_weights=cfg.retrieval.type_weights,
            temporal_half_life=temporal_half_life,
            pr_changed_files=pr_changed_files,
        )
        examples_text = format_examples(retrieved_docs)
        progress_q.put({"step": "retrieve", "status": "done", "detail": f"Found {len(retrieved_docs)} similar past comments", "elapsed": round(time.time() - t0, 1)})

        # Step 3b: Expertise lookup
        expertise_context = ""
        if expertise_index:
            expertise_entries = lookup_expertise(expertise_index, pr_changed_files)
            expertise_context = format_expertise_context(expertise_entries)

        # Step 4: Build prompts
        system_prompt = _build_system_prompt(profile_name, profile_document, examples_text)
        user_prompt = _build_user_prompt(profile_name, pr_data, context_files=context_files, expertise_context=expertise_context)

        # Step 5: Generate review (LLM call #1 — the big one)
        prompt_kb = (len(system_prompt) + len(user_prompt)) // 1024
        progress_q.put({"step": "generate", "status": "running", "detail": f"Generating review with Claude ({prompt_kb}KB prompt)... this takes 1-2 minutes"})
        t0 = time.time()
        result = run_inference(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            config=cfg.model,
            response_model=ReviewOutput,
        )

        if isinstance(result, ReviewOutput):
            review = result
        else:
            review = ReviewOutput(summary=str(result))

        progress_q.put({
            "step": "generate", "status": "done",
            "detail": f"Generated {len(review.inline_comments)} comments, {len(review.abstained_files)} abstentions",
            "elapsed": round(time.time() - t0, 1),
        })

        # Step 6: Self-critique (LLM call #2)
        if review.inline_comments:
            progress_q.put({"step": "critique", "status": "running", "detail": f"Self-critique: filtering {len(review.inline_comments)} comments for authenticity..."})
            t0 = time.time()
            existing_threads = _format_existing_threads(
                pr_data.get("comments", []),
                pr_data.get("reviews", []),
            )
            review = _run_self_critique(cfg, review, examples_text, existing_threads)
            progress_q.put({"step": "critique", "status": "done", "detail": f"{len(review.inline_comments)} comments passed critique", "elapsed": round(time.time() - t0, 1)})
        else:
            progress_q.put({"step": "critique", "status": "done", "detail": "No comments to critique — reviewer would approve as-is", "elapsed": 0})

        # Step 7: Filter
        progress_q.put({"step": "filter", "status": "running", "detail": "Applying confidence threshold..."})
        review = _apply_filters(review, cfg)
        progress_q.put({"step": "filter", "status": "done", "detail": f"{len(review.inline_comments)} final comments (threshold: {cfg.review.abstention_threshold})", "elapsed": 0})

        # Done — send the final result
        review_data = {
            "summary": review.summary,
            "inline_comments": [
                {
                    "file": c.file,
                    "hunk_header": c.hunk_header,
                    "diff_snippet": c.diff_snippet,
                    "comment": c.comment,
                    "confidence": c.confidence,
                    "side": c.side,
                }
                for c in review.inline_comments
            ],
            "abstained_files": review.abstained_files,
            "abstention_reason": review.abstention_reason,
        }
        progress_q.put({"step": "done", "status": "done", "detail": "Review complete", "review": review_data})

    except Exception as exc:
        tb = traceback.format_exc()
        progress_q.put({"step": "error", "status": "error", "detail": str(exc), "traceback": tb})


@app.get("/review/stream")
async def review_stream(
    pr_url: str = Query(...),
    profile_name: str = Query(...),
) -> StreamingResponse:
    """SSE endpoint that streams real progress events during review generation."""
    cfg = _load_cfg()
    _ensure_github_token(cfg)

    progress_q: queue.Queue = queue.Queue()

    # Run the pipeline in a background thread
    thread = threading.Thread(
        target=_run_review_with_progress,
        args=(cfg, profile_name, pr_url, progress_q),
        daemon=True,
    )
    thread.start()

    async def event_stream():
        while True:
            try:
                # Check for events without blocking the event loop
                event = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: progress_q.get(timeout=0.5)
                )
                yield f"data: {json.dumps(event)}\n\n"
                if event["step"] in ("done", "error"):
                    break
            except queue.Empty:
                # Send a keepalive comment to prevent connection timeout
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def serve(host: str = "0.0.0.0", port: int = 8142) -> None:
    """Start the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)
