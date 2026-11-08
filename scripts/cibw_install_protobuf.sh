#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
#
# Build protobuf (and its bundled abseil) from source for a cibuildwheel
# leg, as static PIC libraries that the native module absorbs.
#
# Linux: runs inside the manylinux container. The base image's
# protobuf-devel rpm is too old (AlmaLinux 8 → 3.5, AlmaLinux 9 → 3.14).
# Protoc only started escaping C++ reserved-word field names (e.g.
# `default` in pxf/annotations.proto) in 3.21, so 3.x rpms generate
# broken `.pb.cc` for our annotations.
#
# macOS: runs on the runner itself. Homebrew's bottles are built for the
# runner's macOS version, and delocate refuses to bundle a dylib whose
# minimum target is newer than the wheel's, so with bottles the wheel's
# support floor was tied to the runner image (#18, #20). Built here at
# MACOSX_DEPLOYMENT_TARGET and linked statically, there is no dylib for
# delocate to check, and the floor is whatever MACOSX_DEPLOYMENT_TARGET
# says. The libraries are built for the runner's architecture.
#
# Versions differ by OS:
# - Linux: v25.3, last stable in the v25 line, before v26's abseil API
#   churn. Its bundled abseil is a git submodule.
# - macOS: v36.2, the version Homebrew ships, which the macOS test legs
#   already build against. v25.3 cannot build here: its bundled abseil
#   (LTS 20230802) passes `-Xarch_x86_64 -msse4.1` as two separate
#   options, CMake drops the repeated `-Xarch_x86_64`, and clang refuses
#   the bare `-msse4.1` on arm64. Abseil 20240722 fixed it; protobuf v30+
#   fetches a fixed abseil (20250512.1 for v36.2) instead of bundling one.
#   It is always fetched, never taken from Homebrew, whose abseil is a
#   dylib built for the runner's macOS.
#
# Installs into PROTOBUF_PREFIX (default /usr/local). A prefix other than
# a system one must also be on the build's CMAKE_PREFIX_PATH.

set -euxo pipefail

PROTOBUF_VERSION=25.3
PREFIX="${PROTOBUF_PREFIX:-/usr/local}"
WORKDIR="${TMPDIR:-/tmp}/protobuf-build"

cmake_args=()
if [[ "$(uname -s)" == Darwin ]]; then
  PROTOBUF_VERSION=36.2
  cmake_args+=("-Dprotobuf_FORCE_FETCH_DEPENDENCIES=ON")
  # Abseil pins absl::string_view to std::string_view at install time only
  # if a test compile at the default standard is C++17 or later. Apple
  # clang's default is older, so without this the installed abseil uses
  # its own string_view and protowire-cpp fails to compile against it.
  # Homebrew builds abseil at 17 too.
  cmake_args+=("-DCMAKE_CXX_STANDARD=17")
  # The deployment target is the wheel's support floor; building without
  # one would quietly target the runner's own macOS again.
  : "${MACOSX_DEPLOYMENT_TARGET:?set MACOSX_DEPLOYMENT_TARGET to the macOS floor of the wheel}"
  cmake_args+=("-DCMAKE_OSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET}")
fi

# The release-page tarball excludes git submodules (abseil-cpp,
# utf8_range), so a curl|tar pipeline gives an empty third_party/.
# Use a shallow clone with --recurse-submodules instead.
rm -rf "$WORKDIR"
git clone --depth 1 --branch "v${PROTOBUF_VERSION}" \
  --recurse-submodules --shallow-submodules \
  https://github.com/protocolbuffers/protobuf.git "$WORKDIR"
cd "$WORKDIR"

cmake -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -Dprotobuf_BUILD_TESTS=OFF \
  -Dprotobuf_BUILD_SHARED_LIBS=OFF \
  -Dprotobuf_ABSL_PROVIDER=module \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  ${cmake_args[@]+"${cmake_args[@]}"}
cmake --build build -j"$(getconf _NPROCESSORS_ONLN)"
cmake --install build
if [[ "$(uname -s)" == Linux ]]; then
  ldconfig 2>/dev/null || true
fi

# Sanity check: the installed protoc must be 3.21+.
"$PREFIX/bin/protoc" --version
