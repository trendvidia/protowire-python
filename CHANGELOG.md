# Changelog

All notable changes to `protowire-python` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number is kept aligned with the rest of the `protowire-*`
stack — releases bump in lockstep across language ports when the wire
format changes.

## [Unreleased]

### Added

- **`pxf.TableReader` and `pxf.bind_row`** (draft §3.4.4). Streaming
  consumption for the `@table` directive, alternative to materializing
  every row into `Result.tables` up front. Construct via
  `pxf.TableReader.from_bytes(data)`; iterate with the standard for
  loop or call `next_or_none()` until it returns `None`. The reader
  exposes the header `type` / `columns` / `directives` properties and
  a `tail()` method that returns the unconsumed buffer for chaining a
  second reader on multi-`@table` documents. `bind_row(msg, columns,
  row)` is the per-row binder used by `scan()` and exposed as a
  free function for callers iterating `Result.tables[i].rows` from
  the materializing path. Strategy is format-and-reparse, matching
  the C++ port: cells are rendered as a synthetic PXF body and run
  through `unmarshal`, reusing every branch of the existing decoder
  (WKT timestamps / durations, wrapper-nullability, enum-by-name,
  `pxf.required` / `pxf.default`, oneof). PR-2 takes input as bytes;
  a file-like / chunked-IO bridge is a possible follow-up.

### Changed

- **CI pin to protowire-cpp v0.75.0.** The cpp sibling now ships the
  PXF v0.72-series feature set (directive grammar, schema validator,
  Result accessors, TableReader streaming). The pin moves from the
  pre-v0.72 commit `9af2ec0` to the `v0.75.0` tag so the Python
  wrapper exposes the new surface.

### Added

- **`pxf.Result.directives` / `pxf.Result.tables`** — the document-root
  directives the decoder saw at `unmarshal_full` time, exposed as
  immutable dataclasses:
  - `pxf.Directive(name, prefixes, type, body, has_body, line, column)`
    for generic `@<name> *(prefix) [{ ... }]` blocks. `body` is the
    raw bytes between `{` and `}` (verbatim), suitable for handing to
    a follow-up `pxf.unmarshal` against the consumer's message type.
    `type` keeps the v0.72.0 single-prefix back-compat shape.
  - `pxf.TableDirective(type, columns, rows)` for `@table` directives,
    with cells modeled as `None` (absent) or a `(kind, value)` 2-tuple
    where kind ∈ {`"null"`, `"string"`, `"int"`, `"float"`, `"bool"`,
    `"bytes"`, `"ident"`, `"timestamp"`, `"duration"`} — faithful to
    the three-state cell grammar (absent / present-but-null /
    present-with-value, draft §3.4.4).
- **`pxf.validate_descriptor(msg)` + `pxf.Violation`** — schema
  reserved-name check (draft §3.13). Returns the list of fields,
  oneofs, and enum values whose names case-sensitively match a PXF
  value keyword (`null` / `true` / `false`). Sorted by element FQN.
- **`skip_validate` keyword** on `pxf.unmarshal` and
  `pxf.unmarshal_full` (and the `_bytes` variants) — opt-out of the
  per-call schema validator when the caller has already validated the
  descriptor at registry-load time.

## [0.70.0]

Initial public release. The version number aligns this port with the rest
of the `protowire-*` stack, which targets the 0.70.x series for the first
coordinated public release. The wire codec is provided by
[`protowire-cpp`](https://github.com/trendvidia/protowire-cpp) and reaches
Python through a [nanobind](https://github.com/wjakob/nanobind) FFI; this
port's behaviour follows the C++ port's at every wire-level question.

### Added

- **PyPI distribution** as the `protowire-python` package (the bare
  `protowire` was taken by an unrelated 2021 CLI; the import name stays
  `import protowire`). Binary wheels built by CI for CPython 3.10–3.13
  on Linux × {x86_64, aarch64}, macOS × {x86_64, arm64}, and Windows ×
  x86_64. Wheels are published through PyPI OIDC trusted publishing
  with Sigstore provenance attestations.
- **Comprehensive CI matrix**: build + test on Python 3.10/3.11/3.12/3.13
  across Linux/macOS/Windows, plus a `cibuildwheel` smoke build on every
  PR to catch packaging regressions early. Weekly CodeQL SAST.
- **Governance scaffolding**: `LICENSE` (MIT), `CONTRIBUTING.md`,
  `SECURITY.md` (security@trendvidia.com), `GOVERNANCE.md`,
  `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS`, issue + PR templates,
  Dependabot for GitHub Actions and pip.

### Changed (breaking)

- **PXF parser stricter on key forms**, mirroring the upstream grammar
  tightening in
  [`trendvidia/protowire@8262bbb`](https://github.com/trendvidia/protowire/commit/8262bbb)
  (`docs/grammar.ebnf`, `docs/draft-trendvidia-protowire-00.txt`):
  - `=` (field assignment) and `{ … }` (submessage) now require an
    identifier key. Inputs like `123 = 234` or `child { 123 = 123 }`
    now raise `pxf.ParseError` with
    `"field assignment with '=' requires an identifier key, got integer
    (\"123\"); use ':' for map entries"`.
  - `:` (map entry) is rejected at document top level — the document
    represents a proto message, never a `map<K,V>`. Use `=` for
    top-level field assignments. Map literals (`field = { 1: "x" }`)
    still work because `:` remains valid inside `{ … }` blocks.
