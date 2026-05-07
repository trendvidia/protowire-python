# Security Policy

## Reporting a vulnerability

Email **security@trendvidia.com** with a description, reproduction steps,
and the affected version(s) or commit(s). PGP key on request.

Please do **not** file public GitHub issues for vulnerabilities, and do
**not** post details in pull request comments.

You can expect:

- An acknowledgement within **3 business days**.
- A triage decision (accepted / not-a-vulnerability / needs-more-info)
  within **10 business days**.
- A coordinated fix on the timeline below.

## Scope

This policy covers `protowire-python` — the Python port of the `protowire`
stack. It is a [nanobind](https://github.com/wjakob/nanobind) wrapper
around [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp), so
**vulnerabilities in the wire codec itself are typically C++ bugs and are
also in scope at [`trendvidia/protowire-cpp`](https://github.com/trendvidia/protowire-cpp/blob/main/SECURITY.md)**.
You can file at either repo and we will coordinate.

In scope here:

- Decoder crashes, hangs, infinite loops, unbounded memory, or OOMs
  triggered by adversarial PXF / PB / SBE / envelope input through the
  Python API.
- **FFI boundary issues**: refcount mismanagement, GIL violations, buffer
  lifetime bugs, type confusion, and any path where an untrusted Python
  caller can corrupt C++ heap state.
- Wire-format divergences from other ports for the same input that could
  be exploited (e.g. authorization bypass via parser disagreement).
- Schema-validation bypasses that let invalid messages reach application
  code.
- Wheel supply-chain issues: published wheels not matching the tagged
  source commit, missing or invalid Sigstore provenance.

Out of scope:

- Denial-of-service via legitimately large inputs that respect the limits
  in the upstream
  [`docs/HARDENING.md`](https://github.com/trendvidia/protowire/blob/main/docs/HARDENING.md).
- Issues in `protobuf` itself — file those upstream at
  [`protocolbuffers/protobuf`](https://github.com/protocolbuffers/protobuf)
  and CC us.
- Issues in `nanobind` — file at
  [`wjakob/nanobind`](https://github.com/wjakob/nanobind) and CC us.

## Hardening floor

The C++ codec is built and tested in CI with **AddressSanitizer +
UndefinedBehaviorSanitizer** against the upstream adversarial corpus
([`testdata/adversarial/`](https://github.com/trendvidia/protowire/tree/main/testdata/adversarial))
in [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp). A
sanitizer fail there blocks release here too.

Wheels published to PyPI are built by GitHub Actions with **OIDC trusted
publishing** and Sigstore provenance attestations. Verify with
`pip install protowire` and `gh attestation verify`.

## Coordinated disclosure

For vulnerabilities affecting **more than one port** (typically C++ and
Python share the same codec bug), a **30-day embargo** applies from the
date we acknowledge your report (per the upstream project's policy),
extendable by mutual agreement when a fix needs more time.

Single-port issues follow this port's own disclosure timeline, typically
7–14 days, but always at least long enough for a fix to be released.

## Hall of fame

Reporters who follow coordinated disclosure are credited in
`SECURITY-ADVISORY-*.md` advisories on the upstream repo and (with
permission) in the release notes. We do not currently run a paid
bug-bounty program.
