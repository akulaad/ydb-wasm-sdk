#!/usr/bin/env python3
"""Build SDK targets, then check that expected outputs are WebAssembly binaries."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
lock = json.loads((ROOT / "sdk.lock.json").read_text())
targets = lock["targets"] + ["examples/hello"]
subprocess.run(["./ya", "make", "--target-platform=" + lock["target_platform"],
                "--build=" + lock["build_type"], *targets], cwd=ROOT, check=True)
for target in targets:
    if not target.startswith(("ydb/udfs/wasm/", "examples/")):
        continue
    artifacts = list((ROOT / target).glob("*.so"))
    if len(artifacts) != 1:
        raise SystemExit(f"Expected one WASM binary in {target}, found {artifacts}")
    with artifacts[0].open("rb") as stream:
        if stream.read(8) != b"\0asm\x01\0\0\0":
            raise SystemExit(f"Not a WebAssembly binary: {artifacts[0]}")
    print(f"WASM OK: {artifacts[0].relative_to(ROOT)} ({artifacts[0].stat().st_size} bytes)")
