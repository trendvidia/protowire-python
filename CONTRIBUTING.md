# Contributing to protowire-python

Welcome — this is the Python port of [protowire](https://protowire.org), a
language-neutral wire-format toolkit. It tracks the canonical specification
in [`trendvidia/protowire`](https://github.com/trendvidia/protowire) and is
one of nine sibling ports (Go, C++, Rust, Java, TypeScript, Python, C#,
Swift, Dart). This port is a thin [nanobind](https://github.com/wjakob/nanobind)
wrapper around [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp);
the wire codec is the C++ one, this layer just marshals serialized proto
bytes across the FFI boundary.

> **Steward integration is rolling out.** The governance described in
> [GOVERNANCE.md](GOVERNANCE.md) is the steady-state model. While Steward
> is being finalised, pull requests are reviewed by human maintainers in
> the conventional way — open a PR, expect review, iterate.

## Where bugs go

| Symptom | File against |
|---|---|
| Python-only crash, packaging issue, FFI binding bug, wheel build issue | `trendvidia/protowire-python` |
| Crash that also reproduces in `protowire-cpp` directly | upstream [`trendvidia/protowire-cpp`](https://github.com/trendvidia/protowire-cpp) |
| The same input produces different output here vs another port | upstream [`trendvidia/protowire`](https://github.com/trendvidia/protowire) (cross-port wire-equivalence regression) |
| Spec / grammar / proto annotation question | upstream [`trendvidia/protowire`](https://github.com/trendvidia/protowire) |
| Decoder crash / hang / OOM on adversarial input | **email security@trendvidia.com**, do not file public issue (see [SECURITY.md](SECURITY.md)) |

## Build matrix

Python ≥ 3.10, CMake ≥ 3.20, a C++20 compiler. Tested in CI on:

- Linux × CPython 3.10–3.13
- macOS × CPython 3.10–3.13
- Windows × CPython 3.10–3.13

Wheels are produced via [cibuildwheel](https://cibuildwheel.pypa.io/) and
published to PyPI through OIDC trusted publishing.

## Local development

`protowire-python` builds the C++ extension on `pip install`. By default it
looks for a sibling [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp)
checkout at `../protowire-cpp`; override with the `PROTOWIRE_CPP_DIR`
environment variable.

```sh
git clone https://github.com/trendvidia/protowire-cpp.git ../protowire-cpp

python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

pytest
```

The C++ side compiles in Release mode by default. Override with
`--config-settings=cmake.build-type=Debug` for sanitizer-friendly local
builds; for full ASan + UBSan coverage, run the C++ port's sanitizer job
directly — its corpus is the upstream source of truth.

## Sending changes

1. Open a draft PR early.
2. **For changes that touch the FFI boundary** (`src/_protowire/module.cc`,
   anything that crosses `nanobind`): include a manual round-trip check
   against the [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp)
   reference test corpus, since wire-level regressions surface here as
   silent decode mismatches.
3. **For changes that touch the wire format itself** — annotation field
   numbers in `proto/`, the PXF grammar, the SBE schema-id semantics —
   open the upstream PR in
   [`trendvidia/protowire`](https://github.com/trendvidia/protowire) first
   and the [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp)
   PR second. This port shouldn't lead spec changes; it implements them.
4. **Anything that adds a new public symbol** must be exported from
   `protowire/__init__.py` and covered by a test under `tests/`.

## Code style

- Python ≥ 3.10 is the floor (we use `match` and PEP 604 union syntax).
- C++20 is the floor on the FFI side, mirroring `protowire-cpp`.
- The FFI is intentionally narrow: Python sends a serialized
  `FileDescriptorSet` plus a fully-qualified message name; binary proto
  bytes flow back. `google.protobuf.Message` objects do not cross the
  language boundary. New cross-boundary types need a maintainer
  conversation before the PR.
- Match the existing zero-copy patterns in `sbe.View` — bytes flow through
  a `memoryview` rather than getting copied into a new `bytes` object.

## What we don't accept

- Changes that break wire-equivalence with another sibling port.
- Pure-Python codec paths bypassing the C++ FFI. The whole point of this
  port is byte-for-byte parity with the C++ reference; a parallel decoder
  would diverge.
- Bundled prebuilt binaries in the repo. Wheels come from CI's
  cibuildwheel matrix only.
- Static analysis suppressions on a whole file or whole function. Keep
  them line-scoped (`# noqa: <code>` with a comment, or
  `# type: ignore[<code>]` with a comment).

## Releases

This port releases in lockstep with the rest of the `protowire-*` stack.
The version line is `0.70.x` for the first coordinated public release;
ports that share a `0.70.x` minor implement the same wire contract.

Cutting a release:

1. Bump `version` in `pyproject.toml`.
2. Add a `## [X.Y.Z]` section to `CHANGELOG.md`.
3. Tag `vX.Y.Z` on `main`.
4. The `.github/workflows/publish.yml` workflow builds the cibuildwheel
   matrix and publishes to PyPI through OIDC trusted publishing.
