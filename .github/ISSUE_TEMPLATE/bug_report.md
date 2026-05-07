---
name: Bug report
about: Report a defect — wrong output, crash, parse error on valid input, etc.
title: "bug: "
labels: bug
---

<!--
Cross-port issues (the same input behaves differently on multiple ports)
belong upstream at trendvidia/protowire, not here. See CONTRIBUTING.md.

Codec-level bugs that also reproduce in protowire-cpp directly belong
in trendvidia/protowire-cpp.

Security issues (decoder crash/hang/OOM on adversarial input, FFI
refcount errors, GIL violations) go to security@trendvidia.com
instead. See SECURITY.md.
-->

## What happened

A clear description of the bug.

## How to reproduce

Smallest possible PXF / PB / SBE / envelope input + Python snippet that
triggers it.

```python
from protowire import pxf
# ...
```

## What you expected

What you thought should happen.

## Versions

- `protowire` version (`pip show protowire`):
- Python version (`python --version`):
- OS / arch:
- Installed via wheel or source build (`pip install -v` last lines):
- If source build: protobuf + CMake + compiler versions

## Reproduces in protowire-cpp directly?

If yes, please file at https://github.com/trendvidia/protowire-cpp/issues
instead — the codec lives there. (Skip if unknown.)
