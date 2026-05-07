# protowire-python

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/protowire-python.svg)](https://pypi.org/project/protowire-python/)
[![Python](https://img.shields.io/pypi/pyversions/protowire-python.svg)](https://pypi.org/project/protowire-python/)
[![CI](https://github.com/trendvidia/protowire-python/actions/workflows/ci.yml/badge.svg)](https://github.com/trendvidia/protowire-python/actions/workflows/ci.yml)

Python port of [protowire](https://protowire.org) — a protobuf-backed wire-format
toolkit. CPython 3.10+, MIT, [nanobind](https://github.com/wjakob/nanobind) FFI
over [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp). Verified
for byte-equivalence against the canonical Go reference and seven other
sibling ports.

The native extension uses [nanobind](https://github.com/wjakob/nanobind) with
[scikit-build-core](https://github.com/scikit-build/scikit-build-core) as the
build backend. The FFI boundary is intentionally narrow: Python sends a
serialized `FileDescriptorSet` plus a fully-qualified message name; binary
proto bytes flow back. `google.protobuf.Message` objects never cross the
language boundary.

## Install

```sh
pip install protowire-python
```

The PyPI distribution is named `protowire-python` (the bare `protowire`
name was taken). The import name stays `protowire`:

```python
from protowire import pxf, sbe, envelope
```

Wheels are published for CPython 3.10–3.13 on Linux × {x86_64, aarch64},
macOS × {x86_64, arm64}, and Windows × x86_64. On other platforms `pip`
will fall back to a source build (requires CMake ≥ 3.20 and a C++20
compiler).

## API

```python
from protowire import pxf, sbe, envelope

# PXF — schema implicit in the message type.
text = pxf.marshal(my_msg)
pxf.unmarshal(text, my_msg)
result = pxf.unmarshal_full(text, my_msg)
result.is_set("nested.value"), result.is_null("flag")

# SBE — codec built from one or more FileDescriptors with sbe annotations.
codec = sbe.Codec.from_message(OrderType)
data = codec.marshal(order)
codec.unmarshal(data, order_out)
view = codec.view(data); view.uint("order_id")

# Envelope — wire-compatible with the Go envelope package.
e = envelope.OK(200, b"payload")
e = envelope.Err(400, "VALIDATION", "bad input").error.with_field(
    "name", "REQUIRED", "missing"
)
```

## Build from source

```sh
git clone https://github.com/trendvidia/protowire-cpp.git ../protowire-cpp

python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

pytest
```

The build looks for [`protowire-cpp`](https://github.com/trendvidia/protowire-cpp)
at `../protowire-cpp` by default. Override with
`PROTOWIRE_CPP_DIR=/abs/path pip install -e .` or
`pip install -e . --config-settings=cmake.define.PROTOWIRE_CPP_DIR=/abs/path`.

Required: CMake ≥ 3.20, a C++20 compiler, protobuf headers + libs.

- Linux: `apt-get install protobuf-compiler libprotobuf-dev libprotoc-dev`
- macOS: `brew install protobuf`
- Windows: `vcpkg install protobuf` and pass the toolchain file via
  `CMAKE_TOOLCHAIN_FILE`

## Command-line tool

The `protowire` CLI is shared across every port and lives in the spec repo at
[github.com/trendvidia/protowire/cmd/protowire](https://github.com/trendvidia/protowire/tree/main/cmd/protowire).
Install:

```sh
go install github.com/trendvidia/protowire/cmd/protowire@latest
```

Python users use this library for in-process encode/decode and the shared CLI
for command-line operations. There is no separate Python CLI binary.

## Wire compatibility

Verified manually against the Go module:

- Go `pxf.Marshal` → file → Python `pxf.unmarshal` round-trips a representative AllTypes message.
- Python `pxf.marshal` → file → Go `pxf.Unmarshal` round-trips equally.

Because the wire codec is the C++ one, this port inherits all of
[`protowire-cpp`](https://github.com/trendvidia/protowire-cpp)'s
cross-port equivalence guarantees.

## Limitations & open gaps

- **No pure-Python fallback.** A C++ toolchain (clang or gcc, plus CMake) is
  required at install time on platforms where we don't ship a wheel.
  Pure-`google.protobuf`-Python encode/decode without C++ is not available —
  opening that up is a meaningful refactor and would need a separate decoder
  path.
- **The FFI is narrow on purpose.** `google.protobuf.Message` objects never
  cross the boundary — Python sends a `FileDescriptorSet` + fully-qualified
  message name and bytes flow back. This keeps the C++ side type-stable but
  means Python callers serialize their messages once before each call. A
  `MessageView`-style zero-copy path would be welcome.
- **No standalone Python CLI.** The shared CLI lives in
  [trendvidia/protowire/cmd/protowire](https://github.com/trendvidia/protowire/tree/main/cmd/protowire);
  Python callers either invoke that binary or use the in-process API.
- **Free-threaded Python (PEP 703 / 3.13t)** is untested. nanobind supports
  it but the build hasn't been validated against `--disable-gil` interpreters.

## Repository layout

```
protowire-python/
├── LICENSE                                  # MIT
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md, SECURITY.md,
│   GOVERNANCE.md, CODE_OF_CONDUCT.md
├── pyproject.toml                           # scikit-build-core + nanobind
├── CMakeLists.txt                           # links protowire-cpp
├── src/_protowire/module.cc                 # FFI entry point (nanobind)
├── src/protowire/                           # pure-Python public API
├── tests/                                   # pytest suites
├── testdata/                                # .proto fixtures
├── scripts/                                 # cross-port test harnesses
└── .github/                                 # CI: build matrix + cibuildwheel + CodeQL
```
