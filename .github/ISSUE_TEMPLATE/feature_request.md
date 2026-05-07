---
name: Feature request
about: Propose a Python-port-only API addition or ergonomics improvement
title: "feat: "
labels: enhancement
---

<!--
Wire-format / spec / annotation proposals belong upstream at
trendvidia/protowire — they affect every port.

Codec-level changes belong in trendvidia/protowire-cpp — this port is a
nanobind wrapper.

This template is for PYTHON-PORT-ONLY changes: better Pythonic
ergonomics, new convenience overloads, packaging improvements, support
for a new Python version or platform.
-->

## Problem

What's awkward to express today, or what's missing?

## Proposal

What you'd like to add. If it's a new public API, sketch the signature
and the typical call-site. If it's a perf change, ideally include a
microbench number from `scripts/bench_pxf.py` or `scripts/bench_sbe.py`.

## Alternatives considered

What else you tried, and why it isn't enough.

## Out of scope (optional)

Things this proposal is **not** trying to do, to keep review focused.
