---
name: ydb-udf-cli
description: Upload WASM UDF modules and their runtime libraries from this SDK using the YDB CLI, inspect deployment status, and wait for compilation. Use for requests such as "upload a module", "ydb udf upload", "загрузи модуль на кластер", or "проверь готовность UDF". Building a module alone does not require deployment.
---

# Upload SDK modules with the YDB CLI

Use a separately installed `ydb` CLI with the `udf` command group. This SDK does
not contain the CLI or server sources. Run examples from the SDK repository root.

## Establish the target

- Use the endpoint, database, CLI path, and authentication already supplied by
  the user or their selected CLI profile. Ask only for missing connection details;
  do not assume a localhost endpoint or database.
- Check `ydb udf upload --help` and `ydb udf describe --help`. The interface below
  uses a manifest for both modules and libraries. If the installed CLI lacks
  `udf` or has a different interface, report the mismatch and obtain a compatible
  CLI; do not require a full YDB checkout or fall back to internal upload helpers.
- Use existing authentication settings; do not print credentials or commit them.
  The server must enable the UDF service and permit the caller to upload modules.
- Identify the requested binary and manifest. A `.so` suffix is normal for this
  SDK's WASM output. Ensure the file is readable, symlinks resolve, and its first
  eight bytes are `00 61 73 6d 01 00 00 00` before uploading.

For explicit connection settings, use a Bash array, preserving any required
profile/authentication options. The environment variables below must contain the
user's selected values, not example defaults:

```bash
udf_cli=("${YDB_BIN:-ydb}" -e "${YDB_ENDPOINT:?Set the selected endpoint}" \
    -d "${YDB_DATABASE:?Set the selected database}")
"${udf_cli[@]}" udf list --format json
```

## Resolve runtime dependencies first

Read `required_libraries` in the module manifest. For each dependency, use
`udf describe --name NAME --format json` to inspect its identity and readiness.
Do not mistake an authorization or transport failure for a missing library.

SDK examples depend on library `sdk`. Reuse a compatible existing runtime. Do not
replace that shared library merely because a new UDF is being uploaded: its ABI
must remain compatible with other deployed modules. A `ready` status alone does
not establish ABI compatibility. Consult `sdk.lock.json` and the deployment's
runtime provenance; if compatibility is unknown, resolve it before deployment.

If `sdk` is absent and its installation is part of the requested deployment,
upload `ydb/udfs/wasm/sdk/libwasm-sdk.so` as a library and wait for readiness before
uploading the UDF. Use the runtime manifest supplied with the artifact. If none
exists in this SDK version, write this minimal manifest to a temporary file and
set `sdk_manifest` to that file's path:

```json
{
  "module_type": "library",
  "module_kind": "wasm",
  "module_name": "sdk",
  "module_extension": "wasm"
}
```

```bash
"${udf_cli[@]}" udf upload \
    --file ydb/udfs/wasm/sdk/libwasm-sdk.so \
    --manifest "$sdk_manifest" --create-only --format json
```

The manifest supplies the name and type. Do not use the obsolete upload flags
`--kind library --name sdk` with this interface.

## Upload the requested UDF

For the bundled Hello example:

```bash
"${udf_cli[@]}" udf upload \
    --file examples/hello/libexamples-hello.so \
    --manifest examples/hello/manifest.json \
    --create-only --format json
```

For another module, substitute its binary and manifest. Capture the returned
`name` and `uid`; the manifest's `module_name` is the server identity, not the
binary filename. A successful upload is not proof of completed compilation.

`--create-only` prevents an unintended replacement. When the user requests an
update, describe the existing module and use `--replace-only --expected-uid UID`
with its observed UID instead. Do not combine these flags with `--write-mode`.
The default write mode is create-or-replace, so prefer an explicit choice.
`--expected-md5`, when used, validates the uploaded body; it does not lock the
existing module's identity.

## Wait for the uploaded revision

Poll this command at a modest interval, such as two seconds, with a bounded
deadline, such as 180 seconds unless the user supplied another timeout:

```bash
"${udf_cli[@]}" udf describe --name "$uploaded_name" --format json
```

Interpret the JSON as follows:

1. Require `module.uid` to equal the UID returned by upload. Stop on mismatch:
   another upload has replaced this revision.
2. Read compilation status from `platforms[]`, keyed by `cpu_spec`. Do not assume
   a top-level `compile_status` exists. An empty array is not readiness.
3. For explicitly requested CPU specifications, require every requested entry
   to exist and be `ready`. Otherwise require a nonempty array with all reported
   platforms `ready`, and report which CPU specifications were checked.
4. Continue for `pending` or `compiling`. Stop on `failed` for a required platform
   and report its `cpu_spec` and `compile_error`. Report unknown statuses rather
   than treating them as success.
5. On timeout, report the name, UID, and last observed statuses. Resume polling
   that UID if asked; do not re-upload just to restart the wait.

Stop on authentication, authorization, or unsupported-service errors. Do not
change cluster settings, restart the server, delete metadata, or delete modules
as an implicit recovery step.

## Verify and report

When execution verification is included in the deployment request, run the
module's supplied smoke query after readiness. For Hello:

```bash
"${udf_cli[@]}" sql -s 'SELECT Hello::hello(42l) AS answer;'
```

Expect `answer = 42`. A short bounded retry can accommodate metadata refresh
after compilation; persistent lookup or execution failures require diagnosis,
not another upload. Use a module-specific query for other modules.

Report the target database, module name, uploaded UID, observed platform states,
and whether SQL execution was verified. Distinguish "uploaded", "compiled", and
"query verified". Never claim successful execution based only on `ready`.
