# YDB WASM UDF SDK

Build C++ WebAssembly UDFs with `ya make` without checking out the YDB repository.
This repository vendors a tracked source closure from YDB: guest ABI, runtime,
selected libraries, build configuration, and host-generator dependencies.

**Status: initial developer SDK.** The supported build profile is Linux x86_64,
`clang20-emscripten-wasm64`, release. Runtime/server compatibility is not yet a
versioned contract. Do not replace a cluster's shared `sdk` library without
checking its existing UDFs.

## Build

Requirements: Linux x86_64, Python 3.8+, and internet access to download the pinned
`ya` binary and toolchain resources. Git is needed to clone, not to fetch YDB.

```sh
git clone https://github.com/akulaad/ydb-wasm-sdk.git
cd ydb-wasm-sdk
python3 tools/sdk/export.py --verify
./ya make --build=release --target-platform=clang20-emscripten-wasm64 \
    ydb/udfs/wasm/sdk examples/hello
```

Outputs:

- `ydb/udfs/wasm/sdk/libwasm-sdk.so`: the shared guest runtime.
- `examples/hello/libexamples-hello.so`: an example UDF.
- `examples/hello/manifest.json`: the manifest to upload with the example.

Despite the `.so` suffix, these are WebAssembly binaries, not native libraries.
Both outputs are normally symlinks into the local ya cache; dereference them
when copying to another machine.

The runtime is uploaded as library `sdk`, then the module with its manifest.
Use a separately installed compatible YDB CLI. This repository does not build
the server or the CLI. The example query is `SELECT Hello::hello(42l);`.

For cluster settings, CLI setup, runtime and module uploads, and a SQL smoke
query, follow the [user guide (in Russian)](docs/getting-started.md). It also covers
experimental CLI commands, updates, troubleshooting, and using the agent skill.

Agent instructions for uploading modules, resolving runtime dependencies, and
waiting for compilation are in
[`.agent/skills/ydb-udf-cli/SKILL.md`](.agent/skills/ydb-udf-cli/SKILL.md).

To build all supported targets and check their WASM headers:

```sh
python3 tools/sdk/smoke.py
```

## Write a module

Copy `examples/hello` to `modules/my_module`, edit its C++ entry point and manifest,
and build the new directory with the same flags. `ya.make` includes the shared
WASM rules and declares its guest ABI dependency. Preserve `.Release()` when
returning a bridge value to transfer ownership correctly.

The repository also includes the upstream `text`, `md5`, and `types` modules,
plus the guest object framework. Only the exported library set is supported;
arbitrary YDB `PEERDIR`s are not available. Source paths intentionally match YDB
so existing includes and build files remain usable.

## Update from YDB

Users of the SDK do not perform this step. Maintainers run the **Import YDB
revision** GitHub Actions workflow with a full upstream commit SHA. It exports
the dependency closure, validates it, builds in a clean container, and publishes a
sync branch. Open the PR manually using the link in the workflow summary. The
workflow does not create, approve, or merge PRs, or publish releases.

Local equivalent, using a clean maintainer checkout:

```sh
python3 tools/sdk/export.py --source /path/to/ydb --revision FULL_COMMIT_SHA
python3 -m unittest discover -s tools/sdk -p 'test_*.py' -v
python3 tools/sdk/export.py --verify
python3 tools/sdk/smoke.py
```

`export/spec.json` declares roots and infrastructure. `sdk.lock.json` records the
upstream commit, profile, bootstrap hash, and export manifest hash.
`export-manifest.json` records every managed file's original and exported hash
and executable mode. The exporter refuses manual edits and unowned collisions;
only files in the previous manifest may be deleted during synchronization.

Edit shared code upstream, then re-export. SDK-specific code lives in
`tools/sdk`, `examples/hello`, `export`, `.agent/skills`, and `.github`. Changes to exported
`ya.make` recursion are deterministic exporter transformations.

See [the export design](docs/export.md) for details and remaining work.

## Licensing

YDB's root license is preserved in `LICENSE`. Imported third-party components
retain their own license and copyright files at their original paths. The export
manifest provides file-level provenance; it is not a substitute for the original
third-party notices.
