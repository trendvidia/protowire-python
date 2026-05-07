# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Test fixtures — compile testdata/test.proto at session start, build runtime
message factories from the descriptor pool so tests don't depend on generated
Python bindings.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows: editable installs don't bundle DLLs and Python 3.8+ ignores
# PATH for extension-module DLL deps — `os.add_dll_directory` is the
# only knob. Add vcpkg's bin dir before the test modules import
# `protowire` (and trigger `_protowire.pyd`'s DLL resolution). conftest.py
# runs before any test-file collection, which is when the import attempt
# happens.
if sys.platform == "win32":
    _vcpkg_root = os.environ.get("VCPKG_INSTALLATION_ROOT")
    if _vcpkg_root:
        _vcpkg_bin = Path(_vcpkg_root) / "installed" / "x64-windows" / "bin"
        if _vcpkg_bin.is_dir():
            os.add_dll_directory(str(_vcpkg_bin))

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

TESTDATA_DIR = Path(__file__).parent.parent / "testdata"


def _protoc_to_fds(proto_files: list[str], include_dirs: list[str]) -> bytes:
    protoc = shutil.which("protoc")
    if not protoc:
        pytest.skip("protoc not found on PATH")
    inc: list[str] = []
    for d in include_dirs:
        inc += ["-I", d]
    out = TESTDATA_DIR / ".pytest_schema.fds"
    cmd = [
        protoc,
        *inc,
        "--include_imports",
        f"--descriptor_set_out={out}",
        *proto_files,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        raise RuntimeError(f"protoc failed: {res.stderr}")
    data = out.read_bytes()
    out.unlink(missing_ok=True)
    return data


@pytest.fixture(scope="session")
def test_fds() -> bytes:
    """Serialized FileDescriptorSet for testdata/test.proto + WKT deps."""
    return _protoc_to_fds(["test.proto"], [str(TESTDATA_DIR)])


@pytest.fixture(scope="session")
def test_pool(test_fds: bytes) -> descriptor_pool.DescriptorPool:
    pool = descriptor_pool.DescriptorPool()
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(test_fds)
    for f in fds.file:
        pool.Add(f)
    return pool


@pytest.fixture(scope="session")
def all_types_cls(test_pool):
    desc = test_pool.FindMessageTypeByName("test.v1.AllTypes")
    return message_factory.GetMessageClass(desc)


@pytest.fixture(scope="session")
def nested_cls(test_pool):
    desc = test_pool.FindMessageTypeByName("test.v1.Nested")
    return message_factory.GetMessageClass(desc)
