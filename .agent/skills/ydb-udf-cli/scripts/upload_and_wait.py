#!/usr/bin/env python3
"""Upload one WASM artifact through YDB CLI and wait for its exact revision."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


class UploadError(Exception):
    pass


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ydb-bin", default=os.getenv("YDB_BIN", "ydb"),
                        help="CLI executable path or name on PATH (default: YDB_BIN or ydb)")
    parser.add_argument("--endpoint", default=os.getenv("YDB_ENDPOINT"))
    parser.add_argument("--database", default=os.getenv("YDB_DATABASE"))
    parser.add_argument("--profile", help="Existing CLI connection/authentication profile")
    parser.add_argument("--cli-arg", action="append", default=[],
                        help="Extra global CLI argument; repeat as --cli-arg=VALUE")
    parser.add_argument("--prefix", choices=("auto", "udf", "experimental"), default="auto")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--manifest", type=Path, help="Required for both modules and libraries")
    parser.add_argument("--wait-only", action="store_true")
    parser.add_argument("--name", help="Existing module name for --wait-only")
    parser.add_argument("--uid", help="Exact uploaded revision for --wait-only")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create-only", dest="write_mode", action="store_const", const="create-only")
    mode.add_argument("--replace-only", dest="write_mode", action="store_const", const="replace-only")
    mode.add_argument("--write-mode", choices=("create-only", "replace-only", "create-or-replace"))
    parser.add_argument("--expected-uid", help="Compare-and-swap guard for an intentional replacement")
    parser.add_argument("--expected-md5", help="Expected checksum of the uploaded body")
    parser.add_argument("--cpu-spec", action="append", default=[], help="Required CPU specification; repeatable")
    parser.add_argument("--timeout", type=positive_seconds, default=180,
                        help="Total deadline including CLI calls, in seconds (default: 180)")
    parser.add_argument("--poll", type=positive_seconds, default=2,
                        help="Polling interval in seconds (default: 2)")
    parser.add_argument("--command-timeout", type=positive_seconds, default=60,
                        help="Maximum duration of one CLI call, capped by total deadline")
    args = parser.parse_args()
    if not args.profile and not (args.endpoint and args.database):
        parser.error("provide --profile or both --endpoint and --database (or YDB_ENDPOINT/YDB_DATABASE)")
    if args.wait_only:
        if not args.name or not args.uid:
            parser.error("--wait-only requires --name and --uid")
        if args.file or args.manifest or args.write_mode or args.expected_uid or args.expected_md5:
            parser.error("--wait-only cannot be combined with upload options")
    else:
        if not args.file or not args.manifest:
            parser.error("upload requires both --file and --manifest")
        if args.name or args.uid:
            parser.error("--name and --uid are for --wait-only; upload identity comes from the manifest")
        args.write_mode = args.write_mode or "create-only"
        if args.write_mode == "replace-only" and not args.expected_uid:
            parser.error("--replace-only requires --expected-uid from describe")
        if args.write_mode == "create-only" and args.expected_uid:
            parser.error("--expected-uid cannot be combined with create-only")
    return args


def readiness(description, uid, required):
    module = description.get("module")
    if not isinstance(module, dict):
        raise UploadError("describe returned no module object")
    if module.get("uid") != uid:
        raise UploadError("UID mismatch: the requested revision has been replaced")
    if description["module"].get("module_kind") != "wasm":
        raise UploadError("The described module is not WASM")
    platforms = description.get("platforms")
    if not isinstance(platforms, list):
        raise UploadError("describe returned no platforms array")
    indexed = {}
    for item in platforms:
        if not isinstance(item, dict) or not isinstance(item.get("cpu_spec"), str) or not item["cpu_spec"]:
            raise UploadError("Malformed platform entry")
        if item["cpu_spec"] in indexed:
            raise UploadError("Duplicate cpu_spec in describe")
        indexed[item["cpu_spec"]] = item
    selected = [indexed[cpu] for cpu in required if cpu in indexed] if required else platforms
    for item in selected:
        status = item.get("status")
        if status == "failed":
            raise UploadError(f"Compilation failed for {item['cpu_spec']}: {item.get('compile_error', '(no diagnostic)')}")
        if status not in ("ready", "pending", "compiling"):
            raise UploadError(f"Unknown compilation status for {item['cpu_spec']}: {status!r}")
    return bool(selected) and (not required or all(cpu in indexed for cpu in required)) and all(
        item["status"] == "ready" for item in selected)


def execute(args):
    manifest = None
    if not args.wait_only:
        with args.file.open("rb") as stream:
            if stream.read(8) != b"\0asm\x01\0\0\0":
                raise UploadError("Input file is not a WebAssembly binary")
        manifest = json.loads(args.manifest.read_text())
        if not isinstance(manifest, dict) or manifest.get("module_kind") != "wasm" or manifest.get("module_type") not in ("module", "library"):
            raise UploadError("Expected a WASM module or library manifest")
        if not isinstance(manifest.get("module_name"), str) or not manifest["module_name"]:
            raise UploadError("Manifest must contain module_name")

    cli = [args.ydb_bin]
    if args.profile:
        cli += ["--profile", args.profile]
    if args.endpoint:
        cli += ["-e", args.endpoint]
    if args.database:
        cli += ["-d", args.database]
    cli += args.cli_arg
    deadline = time.monotonic() + args.timeout

    def call(command, probe=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UploadError("Timed out: total deadline exceeded")
        try:
            result = subprocess.run(cli + command, capture_output=True, text=True,
                                    timeout=min(remaining, args.command_timeout))
        except subprocess.TimeoutExpired:
            message = "Timed out: total deadline exceeded" if remaining <= args.command_timeout else "CLI call timed out"
            if "upload" in command and "--help" not in command:
                message += "; upload outcome may be unknown. Inspect state before retrying"
            raise UploadError(message) from None
        if result.returncode and not probe:
            # Do not echo arguments: they may contain authentication settings.
            raise UploadError(f"YDB CLI exited with {result.returncode}: {result.stderr.strip()}")
        return result

    prefixes = {"udf": ["udf"], "experimental": ["experimental", "udf"]}
    candidates = list(prefixes.values()) if args.prefix == "auto" else [prefixes[args.prefix]]
    prefix = None
    for candidate in candidates:
        commands = ["describe"] if args.wait_only else ["upload", "describe"]
        if all(call(candidate + [cmd, "--help"], probe=True).returncode == 0 for cmd in commands):
            prefix = candidate
            break
    if prefix is None:
        raise UploadError("No compatible UDF command group found; check CLI version or experimental build")

    name, uid = args.name, args.uid
    if not args.wait_only:
        command = prefix + ["upload", "--file", str(args.file), "--manifest", str(args.manifest),
                            "--write-mode", args.write_mode, "--format", "json"]
        if args.expected_uid:
            command += ["--expected-uid", args.expected_uid]
        if args.expected_md5:
            command += ["--expected-md5", args.expected_md5]
        response = json.loads(call(command).stdout)
        if not isinstance(response, dict):
            raise UploadError("Upload returned a non-object JSON value; inspect state before retrying")
        name, uid = response.get("name"), response.get("uid")
        if name != manifest["module_name"] or not isinstance(uid, str) or not uid:
            raise UploadError("Upload returned an unexpected name or missing UID; inspect state before retrying")
        print(f"Uploaded name={name} uid={uid}", file=sys.stderr)

    last = None
    while time.monotonic() < deadline:
        try:
            last = json.loads(call(prefix + ["describe", "--name", name, "--format", "json"]).stdout)
            if not isinstance(last, dict):
                raise UploadError("describe returned a non-object JSON value")
            if readiness(last, uid, args.cpu_spec):
                print(f"Ready name={name} uid={uid}; SQL execution not checked", file=sys.stderr)
                print(json.dumps(last))
                return
        except UploadError as error:
            raise UploadError(f"{error}; name={name} uid={uid}; last describe={json.dumps(last)}") from None
        time.sleep(min(args.poll, max(0, deadline - time.monotonic())))
    raise UploadError(f"Timed out: name={name} uid={uid}; last describe={json.dumps(last)}")


if __name__ == "__main__":
    try:
        execute(parse_args())
    except (UploadError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
