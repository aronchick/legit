"""CLI interface for legit — learn a reviewer's style and generate PR reviews."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="legit",
    help="Learn a GitHub reviewer's style and generate PR reviews in their voice.",
)

console = Console(stderr=True)


# ---------------------------------------------------------------------------
# legit init
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Create the .legit/ directory structure and starter config."""
    from legit.config import legit_path, write_default_config

    root = legit_path()
    config_path = root / "config.yaml"

    if config_path.exists():
        console.print("[yellow]Config already exists at .legit/config.yaml — not overwriting.[/]")
        raise typer.Exit(code=0)

    # Create directory structure
    for subdir in ("profiles", "data", "index", "calibration"):
        (root / subdir).mkdir(parents=True, exist_ok=True)

    write_default_config(config_path)

    console.print("[green]Initialized .legit/ directory.[/]")
    console.print()
    console.print("Created:")
    console.print("  .legit/config.yaml    — edit this with your profile(s)")
    console.print("  .legit/profiles/      — generated reviewer profiles")
    console.print("  .legit/data/          — fetched GitHub activity")
    console.print("  .legit/index/         — BM25 retrieval indexes")
    console.print("  .legit/calibration/   — calibration data")
    console.print()
    console.print("[bold]Next steps:[/]")
    console.print("  1. Set your GitHub token:  export GITHUB_TOKEN=ghp_...")
    console.print("  2. Edit .legit/config.yaml with your profile sources")
    console.print("  3. Run:  legit fetch")
    console.print("  4. Run:  legit build")
    console.print("  5. Run:  legit review --pr <URL>")


# ---------------------------------------------------------------------------
# legit fetch
# ---------------------------------------------------------------------------


@app.command()
def fetch(
    repo: Optional[str] = typer.Option(
        None, "--repo", help="Repository to fetch (owner/repo)."
    ),
    user: Optional[str] = typer.Option(
        None, "--user", help="GitHub username to fetch activity for."
    ),
    index_only: bool = typer.Option(
        False, "--index-only", help="Only build the index; skip downloading content."
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="Only fetch activity since this date (YYYY-MM-DD)."
    ),
    skip_reviews: bool = typer.Option(
        False, "--skip-reviews", help="Skip the slow PR reviews indexing (list PRs → fetch reviews)."
    ),
) -> None:
    """Index and download GitHub activity for configured profiles."""
    from legit.config import load_config
    from legit.github_client import GitHubClient, get_token, validate_token

    try:
        cfg = load_config()
    except FileNotFoundError:
        console.print("[red]No .legit/config.yaml found. Run 'legit init' first.[/]")
        raise typer.Exit(code=1)

    # Validate token
    try:
        user_info = validate_token(cfg.github)
        console.print(f"[dim]Authenticated as {user_info.get('login', 'unknown')}[/]")
    except EnvironmentError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1)

    # Determine which (repo, username) pairs to fetch
    sources: list[tuple[str, str]] = []
    if repo and user:
        sources.append((repo, user))
    elif repo or user:
        # If only one is given, find matching sources from config
        for profile in cfg.profiles:
            for src in profile.sources:
                if repo and src.repo != repo:
                    continue
                if user and src.username != user:
                    continue
                sources.append((src.repo, src.username))
        if not sources:
            console.print("[red]No matching sources found in config for the given --repo/--user.[/]")
            raise typer.Exit(code=1)
    else:
        # Fetch all configured sources
        for profile in cfg.profiles:
            for src in profile.sources:
                sources.append((src.repo, src.username))
        if not sources:
            console.print("[red]No sources configured. Edit .legit/config.yaml first.[/]")
            raise typer.Exit(code=1)

    # Deduplicate
    sources = list(dict.fromkeys(sources))

    with GitHubClient(cfg.github) as client:
        for source_repo, username in sources:
            console.print(f"\n[bold]Fetching {username} @ {source_repo}[/]")
            try:
                client.index_activity(source_repo, username, skip_reviews=skip_reviews, since=since)
                if not index_only:
                    client.download_content(source_repo, username)

                # Also fetch authored PR diffs for coding style analysis
                console.print(f"  [dim]Fetching authored PR diffs for coding style...[/]")
                owner, repo_name = source_repo.split("/", 1)
                authored = client.fetch_authored_pr_diffs(owner, repo_name, username, max_prs=30)
                if authored:
                    from legit.config import legit_path
                    import json
                    ddir = legit_path() / "data" / f"{owner}_{repo_name}" / username
                    ddir.mkdir(parents=True, exist_ok=True)
                    (ddir / "authored_prs.json").write_text(
                        json.dumps(authored, indent=2, default=str) + "\n"
                    )
                    console.print(f"  [green]Saved {len(authored)} authored PR diffs[/]")
                else:
                    console.print(f"  [dim]No authored PRs found[/]")

            except Exception as exc:
                console.print(f"[red]Error fetching {source_repo}/{username}: {exc}[/]")
                continue

    console.print("\n[green]Fetch complete.[/]")


# ---------------------------------------------------------------------------
# legit build
# ---------------------------------------------------------------------------


@app.command()
def build(
    profile: Optional[str] = typer.Option(
        None, "--profile", help="Name of the profile to build."
    ),
    rebuild_map: bool = typer.Option(
        False, "--rebuild-map", help="Force re-run of the map phase."
    ),
    no_overwrite: bool = typer.Option(
        False, "--no-overwrite", help="Skip if profile already exists."
    ),
    max_chunks: Optional[int] = typer.Option(
        None, "--max-chunks", help="Process only the first N chunks (for testing)."
    ),
    concurrency: Optional[int] = typer.Option(
        None, "--concurrency", "-j", help="Number of parallel map workers (overrides config)."
    ),
) -> None:
    """Generate a reviewer profile and BM25 retrieval index."""
    from legit.config import load_config
    from legit.profile import build_profile, load_raw_data_as_retrieval_docs
    from legit.retrieval import build_index

    try:
        cfg = load_config()
    except FileNotFoundError:
        console.print("[red]No .legit/config.yaml found. Run 'legit init' first.[/]")
        raise typer.Exit(code=1)

    # Resolve which profile to build
    if profile:
        matches = [p for p in cfg.profiles if p.name == profile]
        if not matches:
            console.print(f"[red]Profile '{profile}' not found in config.[/]")
            raise typer.Exit(code=1)
        profile_cfg = matches[0]
    else:
        if len(cfg.profiles) == 0:
            console.print("[red]No profiles configured. Edit .legit/config.yaml first.[/]")
            raise typer.Exit(code=1)
        if len(cfg.profiles) > 1:
            names = ", ".join(p.name for p in cfg.profiles)
            console.print(
                f"[red]Multiple profiles configured ({names}). "
                f"Use --profile to specify one.[/]"
            )
            raise typer.Exit(code=1)
        profile_cfg = cfg.profiles[0]

    profile_name = profile_cfg.name

    if no_overwrite:
        profile_path = Path(".legit") / "profiles" / f"{profile_name}.yaml"
        if profile_path.exists():
            console.print(f"[yellow]Profile '{profile_name}' already exists — skipping.[/]")
            raise typer.Exit(code=0)

    # Override concurrency if specified on CLI
    if concurrency is not None:
        profile_cfg.map_concurrency = concurrency

    # Phase 1+2: Map-Reduce (build profile)
    console.print(f"\n[bold]Building profile: {profile_name}[/]")
    console.print(f"[dim]Phase 1/2: Map-Reduce — {profile_cfg.map_concurrency} parallel workers...[/]")
    try:
        build_profile(cfg, profile_name, rebuild_map=rebuild_map, max_chunks=max_chunks)
    except Exception as exc:
        console.print(f"[red]Map-reduce failed: {exc}[/]")
        import traceback
        traceback.print_exc()
        raise typer.Exit(code=1)
    console.print("[green]  Profile generated.[/]")

    # Phase 3: Build BM25 index
    console.print("[dim]Phase 2/2: Building BM25 retrieval index...[/]")
    try:
        docs = load_raw_data_as_retrieval_docs(cfg, profile_name)
        index_path = build_index(profile_name, docs)
        console.print(f"[green]  Index saved to {index_path}[/]")
    except Exception as exc:
        console.print(f"[red]Index build failed: {exc}[/]")
        raise typer.Exit(code=1)

    console.print(f"\n[green]Profile '{profile_name}' built successfully.[/]")


# ---------------------------------------------------------------------------
# legit review
# ---------------------------------------------------------------------------


@app.command()
def review(
    pr: str = typer.Option(
        ..., "--pr", help="GitHub PR URL to review."
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile", help="Name of the reviewer profile to use."
    ),
    dry_run: bool = typer.Option(
        True, "--dry-run/--post", help="Print review to stdout (default) or post to GitHub."
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", help="Write review to a file."
    ),
) -> None:
    """Generate a PR review in a learned reviewer's voice."""
    from legit.config import load_config
    from legit.review import generate_review

    try:
        cfg = load_config()
    except FileNotFoundError:
        console.print("[red]No .legit/config.yaml found. Run 'legit init' first.[/]")
        raise typer.Exit(code=1)

    # Resolve profile
    if profile:
        matches = [p for p in cfg.profiles if p.name == profile]
        if not matches:
            console.print(f"[red]Profile '{profile}' not found in config.[/]")
            raise typer.Exit(code=1)
        profile_cfg = matches[0]
    else:
        if len(cfg.profiles) == 0:
            console.print("[red]No profiles configured. Edit .legit/config.yaml first.[/]")
            raise typer.Exit(code=1)
        if len(cfg.profiles) > 1:
            names = ", ".join(p.name for p in cfg.profiles)
            console.print(
                f"[red]Multiple profiles configured ({names}). "
                f"Use --profile to specify one.[/]"
            )
            raise typer.Exit(code=1)
        profile_cfg = cfg.profiles[0]

    console.print(f"[bold]Reviewing PR:[/] {pr}")
    console.print(f"[bold]Profile:[/] {profile_cfg.name}")
    if dry_run:
        console.print("[dim]Mode: dry-run (use --post to submit to GitHub)[/]")

    try:
        review_output = generate_review(
            config=cfg,
            profile_name=profile_cfg.name,
            pr_url=pr,
            dry_run=dry_run,
            output_path=Path(output) if output else None,
        )
    except Exception as exc:
        console.print(f"[red]Review generation failed: {exc}[/]")
        raise typer.Exit(code=1)

    # Display the review
    stdout = Console()
    stdout.print()
    stdout.print(review_output)

    if output:
        output.write_text(review_output if isinstance(review_output, str) else str(review_output))
        console.print(f"\n[green]Review written to {output}[/]")


# ---------------------------------------------------------------------------
# legit calibrate
# ---------------------------------------------------------------------------


@app.command()
def calibrate(
    profile: Optional[str] = typer.Option(
        None, "--profile", help="Name of the profile to calibrate."
    ),
    auto: bool = typer.Option(
        False, "--auto", help="Run calibration automatically."
    ),
    refresh_holdout: bool = typer.Option(
        False, "--refresh-holdout", help="Resample the holdout set."
    ),
    history: bool = typer.Option(
        False, "--history", help="Show calibration history."
    ),
) -> None:
    """Calibrate review quality against holdout examples."""
    console.print("Calibration not yet implemented.")


# ---------------------------------------------------------------------------
# legit serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    port: int = typer.Option(8142, "--port", "-p", help="Port to listen on."),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to."),
) -> None:
    """Launch the web UI for interactive PR review generation."""
    from legit.web import serve as start_server

    console.print(f"[bold]Starting legit web UI on http://{host}:{port}[/]")
    start_server(host=host, port=port)
