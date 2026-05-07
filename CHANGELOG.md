# Changelog

All notable changes to `protowire-python` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number is kept aligned with the rest of the `protowire-*`
stack — releases bump in lockstep across language ports when the wire
format changes.

## [Unreleased]

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
