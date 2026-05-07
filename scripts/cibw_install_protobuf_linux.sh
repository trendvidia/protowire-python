#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
#
# Install a recent protobuf inside the cibuildwheel manylinux container.
#
# Why: the base image's protobuf-devel rpm is too old (AlmaLinux 8 →
# 3.5, AlmaLinux 9 → 3.14). Protoc only started escaping C++
# reserved-word field names (e.g. `default` in pxf/annotations.proto)
# in 3.21, so 3.x rpms generate broken `.pb.cc` for our annotations.
#
# Pinned to v25.3 — last stable in the v25 line, before v26's abseil
# API churn. Static + PIC so the eventual nanobind shared module can
# absorb it without ldconfig drift.

set -euxo pipefail

PROTOBUF_VERSION=25.3
WORKDIR=/tmp/protobuf-build

mkdir -p "$WORKDIR"
cd "$WORKDIR"
curl -fsSL "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOBUF_VERSION}/protobuf-${PROTOBUF_VERSION}.tar.gz" \
  -o protobuf.tar.gz
tar -xzf protobuf.tar.gz
cd "protobuf-${PROTOBUF_VERSION}"

cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -Dprotobuf_BUILD_TESTS=OFF \
  -Dprotobuf_BUILD_SHARED_LIBS=OFF \
  -Dprotobuf_ABSL_PROVIDER=module \
  -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build build -j"$(nproc)"
cmake --install build
ldconfig 2>/dev/null || true

# Sanity check: the installed protoc must be 3.21+.
/usr/local/bin/protoc --version
