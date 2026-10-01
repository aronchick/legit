"""Exercise the actual web handler with live inference; save review evidence."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from legit.web import app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--pr", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    client = TestClient(app)
    response = client.post("/review", data={"profile_name": args.profile, "pr_url": args.pr})
    response.raise_for_status()
    if "Unable to generate this review" in response.text:
        raise RuntimeError("Live review failed; see application log")
    with sqlite3.connect(".legit/preview/feedback.sqlite3") as db:
        row = db.execute(
            "SELECT id, persona, pr_url, output FROM reviews WHERE persona=? AND pr_url=? "
            "ORDER BY rowid DESC LIMIT 1",
            (args.profile, args.pr),
        ).fetchone()
        if row is None:
            raise RuntimeError("No persisted review")
        marker = "AUTOMATED persistence smoke; not human quality feedback"
        feedback = client.post(
            "/feedback",
            json={
                "review_id": row[0],
                "rating": "useful",
                "note": marker,
            },
        )
        feedback.raise_for_status()
        stored = db.execute(
            "SELECT rating,note FROM feedback WHERE review_id=? AND note=?",
            (row[0], marker),
        ).fetchone()
        if stored != ("useful", marker):
            raise RuntimeError("Feedback read-back failed")
        # Do not leave synthetic feedback in the tester's quality dataset.
        db.execute("DELETE FROM feedback WHERE review_id=? AND note=?", (row[0], marker))
    output = {
        "review_id": row[0],
        "persona": row[1],
        "pr_url": row[2],
        "output": json.loads(row[3]),
        "handler_status": response.status_code,
        "synthetic_feedback_readback": True,
        "synthetic_feedback_removed": True,
        "human_quality_verified": False,
    }
    stem = f"smoke-{args.profile}-{args.pr.rsplit('/', 1)[-1]}"
    Path("evidence").mkdir(exist_ok=True)
    (Path("evidence") / f"{stem}.json").write_text(json.dumps(output, indent=2))
    (Path("evidence") / f"{stem}.html").write_text(response.text)
    print(json.dumps(output), flush=True)


if __name__ == "__main__":
    main()
