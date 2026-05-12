# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""PXF text ↔ protobuf Message — Python mirror of github.com/trendvidia/protowire/encoding/pxf.

The boundary with C++ is FileDescriptorSet bytes + binary proto bytes; Message
objects never cross. The wrapper handles conversion on each side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

from google.protobuf.message import Message

from . import _protowire
from ._schema import fds_for_message


# --- Directive surface (PXF v0.72+) --------------------------------------


# A single `@table` row cell. `None` denotes an absent cell (no value between
# two commas, draft §3.4.4); a non-None Cell is a (kind, value) pair where
# kind is one of the strings below.
#
#   kind        value type        notes
#   ----        ----------        -----
#   "null"      None              present-but-null (draft §3.9)
#   "string"    str               escape-decoded UTF-8
#   "int"       str               raw text — Python wrapper leaves parse to caller
#   "float"     str               raw text
#   "bool"      bool              true / false
#   "bytes"     bytes             base64-decoded
#   "ident"     str               unquoted identifier (typically an enum tag name)
#   "timestamp" str               raw RFC3339
#   "duration"  str               raw duration text
CellKind = Literal[
    "null", "string", "int", "float", "bool", "bytes", "ident", "timestamp", "duration"
]
Cell = Union[None, tuple[CellKind, object]]


@dataclass(frozen=True)
class Directive:
    """A generic `@<name> *(prefix) [{ ... }]` directive at document root.

    See draft §3.4.2. The body bytes are preserved verbatim — consumers
    typically re-decode them against their own message type via
    `pxf.unmarshal(directive.body, ...)`.
    """

    name: str
    prefixes: tuple[str, ...]
    type: str  # back-compat: single prefix populates this; empty otherwise
    body: bytes
    has_body: bool
    line: int
    column: int


@dataclass(frozen=True)
class TableDirective:
    """An `@table TYPE ( cols ) row*` directive at document root.

    Per draft §3.4.4 a document with any TableDirective MUST NOT have a
    @type directive or top-level field entries — the @table header IS
    the document's type declaration.
    """

    type: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]


@dataclass(frozen=True)
class Violation:
    """A schema reserved-name violation, draft §3.13.

    Returned by `validate_descriptor`. `kind` is one of
    `"field"` / `"oneof"` / `"enum_value"`.
    """

    kind: Literal["field", "oneof", "enum_value"]
    element: str  # fully-qualified protobuf name, e.g. "trades.v1.Side.null"
    name: str  # the bare reserved identifier ("null" / "true" / "false")
    file: str  # .proto file path the element is declared in


# --- Result ---------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    """Field-level presence metadata + parsed document-root directives.

    Mirror of `protowire-cpp`'s `Result` (and Go's `pxf.Result`).
    """

    set_paths: frozenset[str]
    null_paths: frozenset[str]
    directives: tuple[Directive, ...] = field(default_factory=tuple)
    tables: tuple[TableDirective, ...] = field(default_factory=tuple)

    def is_set(self, path: str) -> bool:
        return path in self.set_paths and path not in self.null_paths

    def is_null(self, path: str) -> bool:
        return path in self.null_paths

    def is_absent(self, path: str) -> bool:
        return path not in self.set_paths and path not in self.null_paths

    def null_fields(self) -> list[str]:
        return sorted(self.null_paths)


# --- Schema validation (draft §3.13) -------------------------------------


def validate_descriptor(msg: Message) -> list[Violation]:
    """Return schema reserved-name violations on `msg`'s descriptor.

    An empty list means the schema is conformant. The check is
    case-sensitive: `NULL` / `True` lex as identifiers and are accepted.
    """
    fds = fds_for_message(msg)
    raw = _protowire.pxf_validate_descriptor(fds, msg.DESCRIPTOR.full_name)
    return [Violation(kind=k, element=e, name=n, file=f) for (k, e, n, f) in raw]


# --- Encoders --------------------------------------------------------------


def marshal(msg: Message) -> str:
    """Encode `msg` as PXF text. Mirrors Go pxf.Marshal."""
    fds = fds_for_message(msg)
    return _protowire.pxf_marshal(msg.SerializeToString(), fds, msg.DESCRIPTOR.full_name)


# --- bytes-only helpers (used by the CLI / advanced callers) -------------


def marshal_bytes(msg_bytes: bytes, fds: bytes, full_name: str) -> str:
    """Encode raw proto-binary bytes (against an explicit FDS) as PXF text."""
    return _protowire.pxf_marshal(bytes(msg_bytes), bytes(fds), full_name)


def unmarshal_bytes(
    data: str | bytes,
    fds: bytes,
    full_name: str,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
) -> bytes:
    """Decode PXF text into raw proto-binary bytes against an explicit FDS."""
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return _protowire.pxf_unmarshal(
        text, bytes(fds), full_name, discard_unknown, skip_validate
    )


def unmarshal_full_bytes(
    data: str | bytes,
    fds: bytes,
    full_name: str,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
) -> tuple[bytes, Result]:
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    raw, set_paths, null_paths, dirs, tables = _protowire.pxf_unmarshal_full(
        text, bytes(fds), full_name, discard_unknown, skip_validate
    )
    return raw, _wrap_result(set_paths, null_paths, dirs, tables)


# --- Decoders --------------------------------------------------------------


def unmarshal(
    data: str | bytes,
    msg: Message,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
) -> None:
    """Decode PXF text into `msg` (in place). Mirrors Go pxf.Unmarshal."""
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fds = fds_for_message(msg)
    raw = _protowire.pxf_unmarshal(
        text, fds, msg.DESCRIPTOR.full_name, discard_unknown, skip_validate
    )
    msg.Clear()
    msg.MergeFromString(raw)


def unmarshal_full(
    data: str | bytes,
    msg: Message,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
) -> Result:
    """Decode PXF + return per-field presence (set/null) metadata and any
    `@<name>` / `@table` directives the decoder saw at the document root.

    Mirrors Go pxf.UnmarshalFull.
    """
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fds = fds_for_message(msg)
    raw, set_paths, null_paths, dirs, tables = _protowire.pxf_unmarshal_full(
        text, fds, msg.DESCRIPTOR.full_name, discard_unknown, skip_validate
    )
    msg.Clear()
    msg.MergeFromString(raw)
    return _wrap_result(set_paths, null_paths, dirs, tables)


# --- Internal helpers ----------------------------------------------------


def _wrap_result(set_paths, null_paths, raw_dirs, raw_tables) -> Result:
    dirs = tuple(
        Directive(
            name=name,
            prefixes=tuple(prefixes),
            type=type_,
            body=bytes(body),
            has_body=has_body,
            line=line,
            column=column,
        )
        for (name, prefixes, type_, body, has_body, line, column) in raw_dirs
    )
    tables = tuple(
        TableDirective(
            type=type_,
            columns=tuple(columns),
            rows=tuple(
                tuple(_normalize_cell(c) for c in row) for row in rows
            ),
        )
        for (type_, columns, rows) in raw_tables
    )
    return Result(
        set_paths=frozenset(set_paths),
        null_paths=frozenset(null_paths),
        directives=dirs,
        tables=tables,
    )


def _normalize_cell(c) -> Cell:
    """Convert the FFI cell shape to the Cell type alias.

    The FFI hands cells over as either None (absent) or a 2-tuple
    `(kind, value)`. We pass them through unchanged but type-cast — the
    only normalization is `("bytes", bytes)` which arrives as nb::bytes
    and stays as Python `bytes` after the round-trip.
    """
    return c  # already in the right shape
