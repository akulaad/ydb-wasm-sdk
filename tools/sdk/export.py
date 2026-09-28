#!/usr/bin/env python3
"""Export tracked YDB source dependencies; only manifest-owned files may change."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "export-manifest.json"
LOCK = "sdk.lock.json"


def run(args, cwd):
    return subprocess.check_output(args, cwd=cwd).decode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or ".git" in p.parts or str(p) != value:
        raise ValueError(f"Unsafe source path: {value!r}")
    return value


def destination(root, name):
    p = root / safe_path(name)
    if p.is_symlink() or any(x.is_symlink() for x in p.parents if x != root.parent):
        raise ValueError(f"Symlink in destination: {name}")
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"Path escapes destination: {name}")
    return p


def parse_files(output):
    paths = set()
    for line in output.splitlines():
        if " $S/" in line:
            paths.add(safe_path(line.split(" $S/", 1)[1]))
        elif line and not line.startswith("file: "):
            raise ValueError(f"Unexpected ya dump files output: {line}")
    if not paths:
        raise ValueError("Empty source graph")
    return paths


def parse_directories(output):
    return {safe_path(line[len("Directory: $S/"):])
            for line in output.splitlines() if line.startswith("Directory: $S/")}


def prune_recurses(data, name, paths):
    """Keep only exported literal recurse targets; refuse unsupported syntax."""
    text = data.decode()

    def replace(match):
        body = re.sub(r"#[^\n]*", "", match.group(2))
        entries = body.split()
        retained = []
        for entry in entries:
            if not re.fullmatch(r"[a-zA-Z0-9_./+-]+", entry):
                raise ValueError(f"Non-literal RECURSE in {name}: {entry}")
            base = PurePosixPath() if match.group(1) == "RECURSE_ROOT_RELATIVE" else PurePosixPath(name).parent
            target = base / entry / "ya.make"
            normalized = os.path.normpath(str(target))
            if normalized in paths:
                retained.append(entry)
        return match.group(1) + "(" + "\n".join([""] + ["    " + x for x in retained]) + "\n)" if retained else "# SDK export: omitted unavailable recurse targets"

    return re.sub(r"\b(RECURSE(?:_FOR_TESTS|_ROOT_RELATIVE)?)\s*\(([^()]*)\)", replace, text).encode()


def tracked_files(source):
    result = {}
    for record in subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=source).split(b"\0"):
        if not record:
            continue
        info, path = record.decode().split("\t", 1)
        mode, _, stage = info.split()
        if stage != "0":
            raise ValueError("Unmerged upstream index")
        result[safe_path(path)] = mode
    return result


def collect(source, spec):
    common = ["--ignore-recurses", "--target-platform=" + spec["target_platform"],
              "--build=" + spec["build_type"], *spec["targets"]]
    files_output = run(["./ya", "dump", "files", *common], source)
    dirs_output = run(["./ya", "dump", "src-deps", "--with-yamakes", *common], source)
    paths = parse_files(files_output)
    directories = parse_directories(dirs_output)
    plan = json.loads(run(["./ya", "dump", "build-plan", *common], source))
    # dump files reports tool modules using the target configuration. The actual
    # cross-build graph identifies executable generators built for the host.
    host_targets = sorted({node["target_properties"]["module_dir"]
                           for node in plan["graph"]
                           if node.get("target_properties", {}).get("module_type") == "bin"})
    if host_targets:
        host_common = ["--ignore-recurses", "--build=" + spec["build_type"], *host_targets]
        paths.update(parse_files(run(["./ya", "dump", "files", *host_common], source)))
        directories.update(parse_directories(run(["./ya", "dump", "src-deps", "--with-yamakes", *host_common], source)))
    for node in plan["graph"]:
        for path in node.get("inputs", []):
            if path.startswith("$(SOURCE_ROOT)/"):
                paths.add(safe_path(path[len("$(SOURCE_ROOT)/"):]))
    tracked = tracked_files(source)
    paths.update(spec["extra_files"])
    paths.update(p for p in tracked if any(p.startswith(t + "/") for t in spec["extra_trees"]))
    # Header search directories contain extensionless STL headers and scanner misses.
    paths.update(p for p in tracked if str(PurePosixPath(p).parent) in directories)
    paths.update(p for p in tracked if str(PurePosixPath(p).parent) in spec["targets"])
    # Preserve notices throughout each participating contrib component.
    components = {"/".join(p.split("/")[:3]) for p in paths if p.startswith("contrib/")}
    for p in tracked:
        basename = PurePosixPath(p).name.lower()
        notice = any(x in basename for x in ("license", "copying", "copyright", "notice"))
        if notice and any(p.startswith(c + "/") for c in components):
            paths.add(p)
    unknown = paths - tracked.keys()
    if unknown:
        raise ValueError(f"Untracked graph inputs: {sorted(unknown)}")
    result = {}
    for name in sorted(paths):
        if tracked[name] not in ("100644", "100755"):
            raise ValueError(f"Unsupported upstream file mode: {name} ({tracked[name]})")
        data = (source / name).read_bytes()
        original_hash = digest(data)
        if name.endswith("/ya.make") and not name.startswith("build/"):
            data = prune_recurses(data, name, paths)
        result[name] = (data, tracked[name], original_hash)
    return result, host_targets


def verify(root):
    manifest = json.loads((root / MANIFEST).read_text())
    for name, record in manifest["files"].items():
        path = destination(root, name)
        if not path.is_file() or digest(path.read_bytes()) != record["sha256"]:
            raise ValueError(f"Exported file missing or modified: {name}")
        executable = bool(path.stat().st_mode & 0o111)
        if executable != (record["mode"] == "100755"):
            raise ValueError(f"Exported file mode changed: {name}")
    return manifest


def synchronize(root, result, metadata, spec_hash):
    old = verify(root)["files"] if (root / MANIFEST).exists() else {}
    # Preflight every path before the first mutation. Never adopt unowned files.
    for name in result:
        path = destination(root, name)
        if path.exists() and name not in old:
            raise ValueError(f"Refusing to overwrite unowned file: {name}")
    for name in old.keys() - result.keys():
        destination(root, name).unlink()
    records = {}
    for name, (data, mode, original_hash) in result.items():
        path = destination(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if mode == "100755" else 0o644)
        records[name] = {"sha256": digest(data), "upstream_sha256": original_hash, "mode": mode}
    manifest = {"schema_version": 1, "upstream_revision": metadata["upstream_revision"], "files": records}
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    lock = dict(metadata, schema_version=1, spec_sha256=spec_hash,
                export_manifest_sha256=digest((root / MANIFEST).read_bytes()),
                file_count=len(records), source_bytes=sum(len(v[0]) for v in result.values()))
    (root / LOCK).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Clean YDB checkout (maintainers only)")
    parser.add_argument("--revision", help="Expected full upstream commit SHA")
    parser.add_argument("--verify", action="store_true", help="Verify managed source hashes")
    args = parser.parse_args()
    if args.verify:
        manifest = verify(ROOT)
        lock = json.loads((ROOT / LOCK).read_text())
        if lock["export_manifest_sha256"] != digest((ROOT / MANIFEST).read_bytes()):
            raise ValueError("Export manifest does not match lock")
        if lock["spec_sha256"] != digest((ROOT / "export/spec.json").read_bytes()):
            raise ValueError("Export specification changed; re-export required")
        print(f"Verified {len(manifest['files'])} exported files")
        return
    if not args.source or not args.revision:
        parser.error("--source and --revision are required for export")
    source = args.source.resolve()
    revision = run(["git", "rev-parse", "HEAD"], source).strip()
    if revision != args.revision:
        raise ValueError(f"Expected {args.revision}, found {revision}")
    if run(["git", "status", "--porcelain", "--untracked-files=no"], source).strip():
        raise ValueError("Upstream tracked files must be clean")
    spec_data = (ROOT / "export/spec.json").read_bytes()
    spec = json.loads(spec_data)
    result, host_targets = collect(source, spec)
    metadata = {"upstream_url": spec["upstream_url"], "upstream_revision": revision,
                "target_platform": spec["target_platform"], "build_type": spec["build_type"],
                "host_platform": "linux-x86_64", "targets": spec["targets"], "host_targets": host_targets,
                "ya_bootstrap_sha256": digest(result["ya"][0]),
                "runtime_target": "ydb/udfs/wasm/sdk", "runtime_library_name": "sdk"}
    synchronize(ROOT, result, metadata, digest(spec_data))
    print(f"Exported {len(result)} files, {sum(len(v[0]) for v in result.values()) / 1024**2:.1f} MiB")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
