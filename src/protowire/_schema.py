# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Helpers to extract a serialized FileDescriptorSet from a Python protobuf
Message subclass — needed because the C++ side speaks FileDescriptorSet bytes,
not Python descriptors.
"""

from __future__ import annotations

from typing import Iterable

from google.protobuf import descriptor_pb2
from google.protobuf.descriptor import Descriptor, FileDescriptor
from google.protobuf.message import Message


def fds_for_descriptor(desc: Descriptor) -> bytes:
    """Build a FileDescriptorSet covering desc's file plus all transitive deps.

    Files are emitted in dependency order so DescriptorPool.BuildFile() succeeds
    on the C++ side.
    """
    visited: dict[str, FileDescriptor] = {}
    order: list[FileDescriptor] = []

    def walk(fd: FileDescriptor) -> None:
        if fd.name in visited:
            return
        for dep in fd.dependencies:
            walk(dep)
        visited[fd.name] = fd
        order.append(fd)

    walk(desc.file)

    fds = descriptor_pb2.FileDescriptorSet()
    for fd in order:
        proto = descriptor_pb2.FileDescriptorProto()
        fd.CopyToProto(proto)
        fds.file.append(proto)
    return fds.SerializeToString()


def fds_for_message(msg: Message) -> bytes:
    return fds_for_descriptor(type(msg).DESCRIPTOR)


def fds_for_files(files: Iterable[FileDescriptor]) -> bytes:
    """Build a FileDescriptorSet covering all given files plus transitive deps."""
    visited: dict[str, FileDescriptor] = {}
    order: list[FileDescriptor] = []

    def walk(fd: FileDescriptor) -> None:
        if fd.name in visited:
            return
        for dep in fd.dependencies:
            walk(dep)
        visited[fd.name] = fd
        order.append(fd)

    for f in files:
        walk(f)

    fds = descriptor_pb2.FileDescriptorSet()
    for fd in order:
        proto = descriptor_pb2.FileDescriptorProto()
        fd.CopyToProto(proto)
        fds.file.append(proto)
    return fds.SerializeToString()
