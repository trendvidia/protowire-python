# Governance

`protowire-python` is governed under the same constitution as the rest of
the `protowire-*` stack. The machine-readable source of truth lives in
the upstream spec repo at
[`governance.pxf`](https://github.com/trendvidia/protowire/blob/main/governance.pxf);
the human-readable preamble is at
[`GOVERNANCE.md`](https://github.com/trendvidia/protowire/blob/main/GOVERNANCE.md).

This file is a short pointer-doc. If anything below disagrees with the
upstream constitution, the upstream wins.

## Domain ownership

This repo's only domain vector is
[`protowire-python`](https://github.com/trendvidia/protowire/blob/main/governance.pxf)
under the upstream `port-libraries` umbrella. Approval requirements:

| Path | Reviewer authority |
|---|---|
| `src/protowire/`, `src/_protowire/` | port maintainers (`@trendvidia/maintainers`); FFI-boundary changes get extra scrutiny |
| `testdata/`, `tests/` | port maintainers |
| `pyproject.toml`, `CMakeLists.txt` | port maintainers — wheel-build / FFI surface |
| `.github/workflows/publish.yml` | maintainers only — controls PyPI release surface |
| `.github/` (other) | port maintainers |

## What's enforced today vs (roadmap)

The Steward agent that enforces the constitution programmatically is
**rolling out**. Until it is live:

- Pull requests are reviewed by human maintainers.
- The `0.70.x` release line implements the wire contract documented in
  [`docs/grammar.ebnf`](https://github.com/trendvidia/protowire/blob/main/docs/grammar.ebnf)
  + [`docs/HARDENING.md`](https://github.com/trendvidia/protowire/blob/main/docs/HARDENING.md);
  the C++ port's ASan + UBSan job is the local enforcement of the
  hardening invariants — this port inherits those guarantees through the
  FFI.
- Reputation-weighted voting, automatic escrow for risky changes, and
  the `manifesto.blocked_module_globs` restriction are all `(roadmap)`
  per the upstream `governance.pxf`.

## Stable surfaces

Everything in these public modules is part of this port's SemVer
contract:

- `protowire.pxf`
- `protowire.sbe`
- `protowire.envelope`

Anything imported from `protowire._schema`, `protowire._protowire` (the
nanobind extension), or any module starting with `_` is internal and not
stable.

The wire contract — what bytes a given proto message produces — is
governed by the **upstream** spec, not this port. Bumping the wire
contract requires a coordinated PR landing in every sibling port; see
[`STABILITY.md`](https://github.com/trendvidia/protowire/blob/main/STABILITY.md)
upstream.

## FFI particulars

The Python ↔ C++ boundary touches a class of bugs the pure-Python ports
do not (refcount errors, GIL violations, buffer lifetime confusion). The
constitution treats those as a higher-severity tier:

- Changes to `src/_protowire/module.cc` need explicit maintainer approval
  and a manual smoke test against the C++ reference fixtures.
- New cross-boundary types beyond bytes / strings / ints need a
  maintainer conversation before the PR. Today the only types that cross
  are `bytes` and `str`.
- The released wheel matrix (Python × OS) is a stable surface — dropping
  a tier needs a deprecation cycle and a `CHANGELOG.md` entry.
