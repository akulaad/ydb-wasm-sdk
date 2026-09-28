#!/usr/bin/env python3
"""Stage only export-owned files, including previously owned deletions."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
manifest = json.loads((root / "export-manifest.json").read_text())
previous = json.loads(subprocess.check_output(["git", "show", "HEAD:export-manifest.json"], cwd=root))
paths = sorted(set(manifest["files"]) | set(previous["files"]) | {"export-manifest.json", "sdk.lock.json"})
for offset in range(0, len(paths), 100):
    subprocess.run(["git", "add", "--", *paths[offset:offset + 100]], cwd=root, check=True)
