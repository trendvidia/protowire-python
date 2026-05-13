# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""PXF text ↔ protobuf Message — Python mirror of github.com/trendvidia/protowire/encoding/pxf.

The boundary with C++ is FileDescriptorSet bytes + binary proto bytes; Message
objects never cross. The wrapper handles conversion on each side.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Literal, Union

from google.protobuf.message import Message

from . import _protowire
from ._schema import fds_for_message


# --- Directive surface (PXF v0.72+) --------------------------------------


# A single `@dataset` row cell. `None` denotes an absent cell (no value between
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
class DatasetDirective:
    """An `@dataset TYPE ( cols ) row*` directive at document root.

    Per draft §3.4.4 a document with any DatasetDirective MUST NOT have a
    @type directive or top-level field entries — the @dataset header IS
    the document's type declaration.
    """

    type: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]


ProtoShape = Literal["anonymous", "named", "source", "descriptor"]


@dataclass(frozen=True)
class ProtoDirective:
    """An `@proto <body>` directive at document root (draft §3.4.5).

    Carries an embedded protobuf schema, making the PXF document
    self-describing. `shape` distinguishes the four lexically-determined
    body forms; `type_name` is non-empty only when `shape == "named"`.
    `body` carries raw bytes per shape:

    - `"anonymous"` / `"named"`: bytes between the opening `{` and matching
      `}` (both exclusive). Protobuf message-body source.
    - `"source"`: contents of the triple-quoted string (with leading-LF
      stripping / common-prefix dedent already applied). A complete
      ``.proto`` source file.
    - `"descriptor"`: base64-decoded bytes of the bytes literal. A
      serialised ``google.protobuf.FileDescriptorSet``.
    """

    shape: ProtoShape
    type_name: str
    body: bytes


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
    datasets: tuple[DatasetDirective, ...] = field(default_factory=tuple)
    protos: tuple["ProtoDirective", ...] = field(default_factory=tuple)

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
    raw, set_paths, null_paths, dirs, tables, protos = _protowire.pxf_unmarshal_full(
        text, bytes(fds), full_name, discard_unknown, skip_validate
    )
    return raw, _wrap_result(set_paths, null_paths, dirs, tables, protos)


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
    `@<name>` / `@dataset` directives the decoder saw at the document root.

    Mirrors Go pxf.UnmarshalFull.
    """
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fds = fds_for_message(msg)
    raw, set_paths, null_paths, dirs, tables, protos = _protowire.pxf_unmarshal_full(
        text, fds, msg.DESCRIPTOR.full_name, discard_unknown, skip_validate
    )
    msg.Clear()
    msg.MergeFromString(raw)
    return _wrap_result(set_paths, null_paths, dirs, tables, protos)


# --- Internal helpers ----------------------------------------------------


def _wrap_result(set_paths, null_paths, raw_dirs, raw_tables, raw_protos) -> Result:
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
        DatasetDirective(
            type=type_,
            columns=tuple(columns),
            rows=tuple(
                tuple(_normalize_cell(c) for c in row) for row in rows
            ),
        )
        for (type_, columns, rows) in raw_tables
    )
    protos = tuple(
        ProtoDirective(
            shape=shape,
            type_name=type_name,
            body=bytes(body),
        )
        for (shape, type_name, body) in raw_protos
    )
    return Result(
        set_paths=frozenset(set_paths),
        null_paths=frozenset(null_paths),
        directives=dirs,
        datasets=tables,
        protos=protos,
    )


def _normalize_cell(c) -> Cell:
    """Convert the FFI cell shape to the Cell type alias.

    The FFI hands cells over as either None (absent) or a 2-tuple
    `(kind, value)`. We pass them through unchanged but type-cast — the
    only normalization is `("bytes", bytes)` which arrives as nb::bytes
    and stays as Python `bytes` after the round-trip.
    """
    return c  # already in the right shape


# --- DatasetReader (streaming @dataset consumption, draft §3.4.4) ------------


class DatasetReader:
    """Streaming row reader for a single `@dataset` directive.

    `unmarshal_full` materializes every row of a `@dataset` directive into
    `Result.tables`. That works for small datasets and breaks for the
    CSV-replacement workload `@dataset` was designed for. `DatasetReader`
    pulls one row at a time from an in-memory buffer; working-set memory
    is bounded by the size of the largest single row.

    Usage::

        reader = pxf.DatasetReader.from_bytes(open("trades.pxf", "rb").read())
        for row in reader:
            msg = TradeMsg()
            pxf.bind_row(msg, reader.columns, row)
            handle(msg)

    Per draft §3.4.4: per-row arity and v1 cell-grammar checks happen at
    consume time, not deferred to EOF. The reader header (everything
    from the start of the input through the closing `)` of the column
    list) is capped at 64 KiB to fail-fast on misuse.

    NOTE: this PR-2 implementation reads from a `bytes` buffer. A
    file-like / chunked-IO bridge is a possible follow-up.
    """

    def __init__(self, native: object) -> None:
        # Private — construct via `from_bytes`. We keep the native handle
        # only; type/columns/directives are forwarded on demand.
        self._native = native

    @classmethod
    def from_bytes(cls, data: bytes | str) -> "DatasetReader":
        if isinstance(data, str):
            data = data.encode("utf-8")
        return cls(_protowire.PxfDatasetReader.from_bytes(bytes(data)))

    @property
    def type(self) -> str:
        return self._native.type

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self._native.columns)

    @property
    def directives(self) -> tuple[Directive, ...]:
        return tuple(
            Directive(
                name=name,
                prefixes=tuple(prefixes),
                type=type_,
                body=bytes(body),
                has_body=has_body,
                line=line,
                column=column,
            )
            for (name, prefixes, type_, body, has_body, line, column) in self._native.directives
        )

    @property
    def done(self) -> bool:
        return self._native.done

    def next_or_none(self) -> tuple[Cell, ...] | None:
        """Returns the next row, or None at EOF."""
        cells = self._native.next_or_none()
        return None if cells is None else tuple(cells)

    def __iter__(self) -> "DatasetReader":
        return self

    def __next__(self) -> tuple[Cell, ...]:
        return tuple(self._native.__next__())

    def tail(self) -> bytes:
        """Returns the bytes the reader has buffered but not consumed,
        followed by any remaining bytes from the underlying source.

        Use to chain a second `DatasetReader` for documents containing
        multiple `@dataset` directives::

            tr1 = pxf.DatasetReader.from_bytes(data)
            for _ in tr1: ...
            tr2 = pxf.DatasetReader.from_bytes(tr1.tail())

        MUST only be called after iteration has exhausted (i.e. `done`
        is True). Calling earlier returns bytes the current reader
        still intends to consume.
        """
        return self._native.tail()

    def scan(self, msg: Message, *, skip_validate: bool = True) -> bool:
        """Read the next row and bind its cells to `msg`'s fields by
        column name. Returns True on success; returns False at EOF
        (callers check `reader.done`)."""
        cells = self.next_or_none()
        if cells is None:
            return False
        bind_row(msg, self.columns, cells, skip_validate=skip_validate)
        return True


# --- bind_row (per-row proto binding) ------------------------------------


def bind_row(
    msg: Message,
    columns: tuple[str, ...] | list[str],
    row: tuple[Cell, ...] | list[Cell],
    *,
    skip_validate: bool = True,
) -> None:
    """Bind a `@dataset` row's cells to `msg`'s fields by column name.

    `columns` and `row` MUST have the same length. Cell-state semantics:

      - `None`        — field absent. (pxf.default) applies if declared;
                        (pxf.required) errors if neither default nor
                        value is present.
      - `("null", _)` — field cleared (draft §3.9).
      - any other     — field set to the cell's value.

    Strategy: render the row as a synthetic PXF body (`<col> = <val>`
    per non-None cell) and run it through `unmarshal`. This mirrors
    `protowire-cpp`'s `BindRow` and reuses every branch of the existing
    decoder — WKT timestamps / durations, wrapper-type nullability,
    enum-by-name resolution, oneof handling — instead of growing a
    parallel Cell→FieldDescriptor switch.

    `skip_validate` defaults to True: the descriptor was validated once
    when the caller constructed the message factory, and re-running the
    reserved-name check per row is wasteful in tight loops.
    """
    if len(columns) != len(row):
        raise ValueError(
            f"bind_row: {len(columns)} columns vs {len(row)} cells"
        )
    parts: list[str] = []
    for col, cell in zip(columns, row):
        if cell is None:
            continue
        parts.append(f"{col} = {_cell_to_pxf(cell)}\n")
    unmarshal("".join(parts), msg, skip_validate=skip_validate)


def _cell_to_pxf(cell: tuple[CellKind, object]) -> str:
    kind, value = cell
    if kind == "null":
        return "null"
    if kind == "string":
        # Escape `"` and `\`; other characters round-trip verbatim because
        # the lexer accepts UTF-8 in strings.
        s = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'
    if kind in ("int", "float", "timestamp", "duration"):
        return str(value)  # raw text
    if kind == "bool":
        return "true" if value else "false"
    if kind == "bytes":
        return 'b"' + base64.b64encode(value).decode("ascii") + '"'
    if kind == "ident":
        return str(value)
    raise ValueError(f"bind_row: unknown cell kind {kind!r}")
