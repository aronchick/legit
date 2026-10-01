"""Package the five inspected personas with matching artifacts and source manifests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml

from legit.config import load_config
from legit.profile import verify_build_provenance

PERSONAS = ("mitchellh", "thockin", "liggitt", "lavalamp", "bgrant0607")


def main() -> None:
    config = load_config()
    config.profiles = [p for p in config.profiles if p.name in PERSONAS]
    for name in PERSONAS:
        verify_build_provenance(name)
    output = Path("evidence/preview-runtime.zip")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(".legit/config.yaml", yaml.safe_dump(config.model_dump(mode="json")))
        for name in PERSONAS:
            for path in (
                Path(f".legit/profiles/{name}.md"),
                Path(f".legit/profiles/{name}.meta.json"),
            ):
                archive.write(path)
            for category in ("index", "embeddings", "expertise"):
                for path in sorted((Path(".legit") / category / name).glob("*")):
                    if path.is_file():
                        archive.write(path)
            archive.write(f"evidence/corpus-{name}.json")
            profile = next(p for p in config.profiles if p.name == name)
            for source in profile.sources:
                directory = Path(".legit/data") / source.repo.replace("/", "_") / source.username
                for filename in ("pr_comments.json", "issue_comments.json"):
                    archive.write(directory / filename)
        archive.write("evidence/persona-inspection.md")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Archive corruption detected")
        for name in PERSONAS:
            meta = json.loads(archive.read(f".legit/profiles/{name}.meta.json"))
            import hashlib

            for relative, digest in meta["artifact_hashes"].items():
                assert hashlib.sha256(archive.read(f".legit/{relative}")).hexdigest() == digest
    print(f"Verified {output} ({output.stat().st_size} bytes), all five artifact hash sets match")


if __name__ == "__main__":
    main()
