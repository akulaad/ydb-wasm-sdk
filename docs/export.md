# Export and synchronization

## Source of truth

The upstream is `https://github.com/ydb-platform/ydb.git`. The SDK now imports
the official repository, including the WASM UDF implementation and the guest
`yexception` example. `sdk.lock.json` pins the exact imported revision.

For bootstrap, the export specification and exporter live in this SDK repository;
no change to the upstream YDB checkout is required. Shared sources remain owned
by YDB. A future upstream CI trigger can dispatch this repository's sync workflow.

## Dependency closure

1. Require a clean tracked checkout at the exact requested commit.
2. Run `ya dump files` and `ya dump src-deps --with-yamakes` for the WASM roots.
3. Inspect `ya dump build-plan` for executable host generators. Dump their source
   dependencies with the host configuration, and include actual action inputs.
4. Include tracked files directly in reported include directories. This covers
   extensionless STL headers and imperfect scanner results without recursively
   copying whole contrib trees.
5. Add the conservative `build/` infrastructure set, bootstrap files, explicit
   guest-interface trees, manifests, and component license notices.
6. Remove recursion into absent directories from exported `ya.make` files.
   Keep original source paths; refuse unsupported dynamic recurse expressions.
7. Preflight destination ownership and hashes, then update managed files and
   record provenance. No generated binaries or source symlinks are exported.

The build-plan executable detection assumes guest roots are DLLs/libraries.
Adding a standalone guest executable or a new host platform requires extending
this logic and testing that configuration. This is an intentionally bounded
Linux x86_64 SDK, not a universal YDB exporter.

## Pull-request flow

`workflow_dispatch` accepts an exact SHA, fetches that revision in a temporary
checkout, exports it, verifies source hashes, and runs exporter tests. A container
gets only the SDK directory, so its build cannot read the upstream checkout or
reuse the maintainer's cache. A successful build is committed on
`sync/ydb-<sha>`. The workflow summary links to the comparison against `main`;
the maintainer opens the PR manually.

The workflow only has `contents: write` permission to publish its branch. GitHub
Actions permission to create and approve PRs remains disabled. A manually opened
PR also triggers the independent SDK checks workflow.

If a branch for a revision already exists, the workflow refuses to overwrite it.
Review that existing PR instead, or dispatch a different revision. No force pushes
are performed.

## Current validation boundary

The smoke build checks that all expected module outputs exist and have the WASM
binary header. It does not prove manifest correctness, runtime ABI compatibility,
or query execution. Exporter tests cover path containment, collisions, local edits,
owned deletions, deterministic metadata, and recursion pruning.

The initial bootstrap and platform-resource descriptors are pinned as exported
source files. This is not yet a complete SHA-256 lock for every downloaded tool.
The repository requires online tool downloads and makes no offline guarantee.

## Follow-up work

- Validate imports, exports, signatures, and WASM features against runtime/host ABI.
- Run query tests against explicitly versioned YDB binaries, including object
  lifecycle, nested values, errors, and memory ownership.
- Publish matching runtime binaries with checksums and a tested server matrix.
- Add complete tool-resource checksums, public mirrors, and an offline bundle.
- Support a separate user module repository through a materialized source workspace.
- Add server-side runtime/ABI version binding rather than relying solely on the
  mutable `sdk` library name.
- Expand supported contrib libraries and platforms only with separate cold builds.
