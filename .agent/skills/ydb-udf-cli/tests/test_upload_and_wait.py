import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / "scripts/upload_and_wait.py"
FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
cfg = json.loads(os.environ['FAKE_CONFIG'])
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
if '--help' in args:
    sys.exit(0 if ('experimental' in args) == cfg.get('experimental', False) else 2)
if 'upload' in args:
    if cfg.get('upload_error'):
        print('Access denied', file=sys.stderr)
        sys.exit(7)
    print(json.dumps({'name': cfg.get('name', 'Hello'), 'uid': 'new-uid'}))
elif 'describe' in args:
    time.sleep(cfg.get('delay', 0))
    counter = Path(os.environ['FAKE_COUNTER'])
    index = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(index + 1))
    states = cfg.get('states', [[{'cpu_spec': 'cpu-a', 'status': 'ready'}]])
    print(json.dumps({'module': {'uid': cfg.get('uid', 'new-uid'), 'module_kind': 'wasm'},
                      'platforms': states[min(index, len(states) - 1)]}))
else:
    sys.exit(3)
'''


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cli = self.root / "fake ydb"
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(0o755)
        self.binary = self.root / "module with spaces.so"
        self.binary.write_bytes(b"\0asm\x01\0\0\0")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"module_type": "module", "module_kind": "wasm", "module_name": "Hello"}))
        self.log = self.root / "calls.jsonl"

    def invoke(self, config=None, extra=(), wait=False, connection=True):
        env = dict(os.environ)
        for key in ("YDB_ENDPOINT", "YDB_DATABASE", "YDB_BIN"):
            env.pop(key, None)
        env.update(YDB_BIN=str(self.cli), FAKE_CONFIG=json.dumps(config or {}),
                   FAKE_LOG=str(self.log), FAKE_COUNTER=str(self.root / "counter"))
        command = [sys.executable, str(HELPER), "--poll", "0.01", "--timeout", "5"]
        if connection:
            command += ["--profile", "test profile"]
        command += ["--wait-only", "--name", "Hello", "--uid", "new-uid"] if wait else [
            "--file", str(self.binary), "--manifest", str(self.manifest)]
        return subprocess.run(command + list(extra), env=env, capture_output=True, text=True, timeout=10)

    def calls(self, verb):
        return [args for line in self.log.read_text().splitlines()
                if verb in (args := json.loads(line)) and '--help' not in args] if self.log.exists() else []

    def test_upload_waits_for_ready_without_reupload(self):
        result = self.invoke({"states": [[], [{"cpu_spec": "cpu-a", "status": "compiling"}],
                                         [{"cpu_spec": "cpu-a", "status": "ready"}]]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["module"]["uid"], "new-uid")
        self.assertEqual(len(self.calls("upload")), 1)
        self.assertEqual(len(self.calls("describe")), 3)
        self.assertIn("create-only", self.calls("upload")[0])
        self.assertIn(str(self.binary), self.calls("upload")[0])
        self.assertIn("test profile", self.calls("upload")[0])

    def test_experimental_prefix_and_replacement_guards(self):
        result = self.invoke({"experimental": True}, extra=("--replace-only", "--expected-uid", "old-uid", "--expected-md5", "digest"))
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.calls("upload")[0]
        self.assertIn("experimental", call)
        self.assertIn("replace-only", call)
        self.assertIn("old-uid", call)
        self.assertIn("digest", call)

    def test_wait_only_never_uploads(self):
        result = self.invoke(wait=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("upload"), [])

    def test_library_upload_uses_manifest(self):
        self.manifest.write_text(json.dumps({"module_type": "library", "module_kind": "wasm", "module_name": "sdk"}))
        result = self.invoke({"name": "sdk"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--manifest", self.calls("upload")[0])
        self.assertNotIn("--kind", self.calls("upload")[0])

    def test_unknown_compile_status_is_not_success(self):
        result = self.invoke({"states": [[{"cpu_spec": "cpu-a", "status": "unexpected"}]]}, wait=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown compilation status", result.stderr)

    def test_uid_mismatch_stops(self):
        result = self.invoke({"uid": "someone-else"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UID mismatch", result.stderr)
        self.assertEqual(len(self.calls("describe")), 1)

    def test_failure_preserves_compile_diagnostic(self):
        result = self.invoke({"states": [[{"cpu_spec": "cpu-a", "status": "failed", "compile_error": "unknown import"}]]})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown import", result.stderr)
        self.assertEqual(len(self.calls("upload")), 1)

    def test_required_cpu_does_not_accept_other_ready_cpu(self):
        result = self.invoke(extra=("--cpu-spec", "missing", "--timeout", "0.6"), wait=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timed out", result.stderr)

    def test_selected_cpu_can_ignore_unrequested_failed_cpu(self):
        result = self.invoke({"states": [[{"cpu_spec": "cpu-a", "status": "ready"},
                                          {"cpu_spec": "cpu-b", "status": "failed"}]]},
                             extra=("--cpu-spec", "cpu-a"), wait=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_platforms_timeout(self):
        result = self.invoke({"states": [[]]}, extra=("--timeout", "0.6"), wait=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Timed out", result.stderr)

    def test_hanging_cli_is_bounded(self):
        result = self.invoke({"delay": 3}, extra=("--command-timeout", "0.3"), wait=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLI call timed out", result.stderr)

    def test_upload_error_is_not_retried(self):
        result = self.invoke({"upload_error": True})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Access denied", result.stderr)
        self.assertEqual(len(self.calls("upload")), 1)
        self.assertEqual(self.calls("describe"), [])

    def test_invalid_binary_never_calls_cli(self):
        self.binary.write_bytes(b"not wasm")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_missing_target_never_calls_cli(self):
        result = self.invoke(connection=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_replace_requires_expected_uid(self):
        result = self.invoke(extra=("--replace-only",))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
