"""Web UI for legit — paste a PR URL, get a review."""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from legit.config import LegitConfig, load_config
from legit.review import generate_review

app = FastAPI(title="legit", description="PR reviews in a learned reviewer's voice")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


def _render(context: dict) -> HTMLResponse:
    """Render the template with Jinja2 directly (avoids Starlette cache issues)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_PATH.parent)))
    template = env.get_template("index.html")
    return HTMLResponse(template.render(**context))


def _load_cfg() -> LegitConfig:
    return load_config()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    cfg = _load_cfg()
    profiles = [p.name for p in cfg.profiles]
    return _render({"profiles": profiles, "review": None, "error": None, "pr_url": ""})


@app.post("/review", response_class=HTMLResponse)
async def review(request: Request, pr_url: str = Form(...), profile_name: str = Form(...)) -> HTMLResponse:
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
        })
    except Exception as exc:
        tb = traceback.format_exc()
        return _render({
            "profiles": profiles,
            "review": None,
            "error": f"{exc}\n\n{tb}",
            "pr_url": pr_url,
            "profile_name": profile_name,
        })


def serve(host: str = "0.0.0.0", port: int = 8142) -> None:
    """Start the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)
