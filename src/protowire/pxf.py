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

from . import _protowire, _upb
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
    """A schema that must not be bound for PXF, draft -01.

    Returned by `validate_descriptor`. `kind` is one of `"field"` / `"oneof"`
    / `"enum_value"` (a reserved name, §3.13), `"key_option"` (a misplaced
    `(pxf.key)`), `"required_default"` (a misplaced `(pxf.required)`: beside
    `(pxf.default)` on one field, or on a oneof member), `"default_option"` (a
    default that cannot be used, such as one on a repeated field),
    `"retired_number"` (an option at an extension number protowire retired;
    recompile the descriptor) or `"well_known_shape"` (a message redefining a
    well-known type without the fields the codec reads).
    """

    kind: Literal[
        "field",
        "oneof",
        "enum_value",
        "key_option",
        "required_default",
        "default_option",
        "retired_number",
        "well_known_shape",
    ]
    element: str  # fully-qualified protobuf name, e.g. "trades.v1.Side.null"
    # The reserved identifier, the (pxf.key) value, the (pxf.default) literal
    # ("default_option", and "required_default" beside a default), the oneof's
    # name ("required_default" on a oneof member), or the retired number
    # ("retired_number"); empty for "well_known_shape".
    name: str
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
    """Return the bind-time violations of `msg`'s descriptor and its imports.

    An empty list means the schema is conformant. The reserved-name check is
    case-sensitive: `NULL` / `True` lex as identifiers and are accepted.
    """
    fds = fds_for_message(msg)
    raw = _protowire.pxf_validate_descriptor(fds, msg.DESCRIPTOR.full_name)
    return [Violation(kind=k, element=e, name=n, file=f) for (k, e, n, f) in raw]


# --- Encoders --------------------------------------------------------------


def marshal(msg: Message, *, max_nesting_depth: int = 0) -> str:
    """Encode `msg` as PXF text. Mirrors Go pxf.Marshal.

    A message nested deeper than `max_nesting_depth` raises ValueError rather
    than being written, because a decoder at the same limit would refuse the
    output (protowire#502). Depth is counted as a decoder counts it: the root
    is depth 0, and every `{` or `[` is one more. 0 selects the default, 100.
    Raise it only together with the limit of every reader of the output.
    """
    fds = fds_for_message(msg)
    return _protowire.pxf_marshal(
        msg.SerializeToString(), fds, msg.DESCRIPTOR.full_name, max_nesting_depth
    )


# --- Decode limits ----------------------------------------------------------
#
# Every decoder takes the limits of HARDENING.md § Mandatory limits as keyword
# arguments, passed to protowire-cpp's pxf::UnmarshalOptions. 0 selects the
# default; a negative value is a ValueError.
#
#   max_message_size            input length in bytes (default 64 MiB)
#   max_nesting_depth           `{` / `[` nesting, the root is depth 0 (100)
#   max_numeric_literal_digits  digits of a literal bound to pxf.BigInt,
#                               Decimal or BigFloat, and a Decimal's scale (4096)
#   max_bytes_literal_length    decoded length of a b"..." literal (64 MiB)
#   max_repeated_count          elements of one repeated or map field (64 Mi)
#   max_big_float_exponent      a pxf.BigFloat's binary exponent (16384)
#
# The decoded message reaches Python through the protobuf runtime, which stops
# at 100 levels of submessages whatever max_nesting_depth says (#40).


def _limits(
    max_message_size: int,
    max_nesting_depth: int,
    max_numeric_literal_digits: int,
    max_bytes_literal_length: int,
    max_repeated_count: int,
    max_big_float_exponent: int,
) -> tuple[int, int, int, int, int, int]:
    return (
        max_message_size, max_nesting_depth, max_numeric_literal_digits,
        max_bytes_literal_length, max_repeated_count, max_big_float_exponent,
    )


# --- bytes-only helpers (used by the CLI / advanced callers) -------------


def marshal_bytes(
    msg_bytes: bytes, fds: bytes, full_name: str, *, max_nesting_depth: int = 0
) -> str:
    """Encode raw proto-binary bytes (against an explicit FDS) as PXF text.

    `max_nesting_depth` is as for `marshal`. Bytes that do not parse as
    `full_name`, or nest more than one level past the limit, raise a
    ValueError starting "pxf.marshal: proto parse failed".
    """
    return _protowire.pxf_marshal(bytes(msg_bytes), bytes(fds), full_name, max_nesting_depth)


def unmarshal_bytes(
    data: str | bytes,
    fds: bytes,
    full_name: str,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
    max_message_size: int = 0,
    max_nesting_depth: int = 0,
    max_numeric_literal_digits: int = 0,
    max_bytes_literal_length: int = 0,
    max_repeated_count: int = 0,
    max_big_float_exponent: int = 0,
) -> bytes:
    """Decode PXF text into raw proto-binary bytes against an explicit FDS.

    The limits are described under "Decode limits" above.
    """
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return _protowire.pxf_unmarshal(
        text, bytes(fds), full_name, discard_unknown, skip_validate,
        _limits(
            max_message_size, max_nesting_depth, max_numeric_literal_digits,
            max_bytes_literal_length, max_repeated_count, max_big_float_exponent,
        ),
    )


def unmarshal_full_bytes(
    data: str | bytes,
    fds: bytes,
    full_name: str,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
    max_message_size: int = 0,
    max_nesting_depth: int = 0,
    max_numeric_literal_digits: int = 0,
    max_bytes_literal_length: int = 0,
    max_repeated_count: int = 0,
    max_big_float_exponent: int = 0,
) -> tuple[bytes, Result]:
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    raw, set_paths, null_paths, dirs, tables, protos = _protowire.pxf_unmarshal_full(
        text, bytes(fds), full_name, discard_unknown, skip_validate,
        _limits(
            max_message_size, max_nesting_depth, max_numeric_literal_digits,
            max_bytes_literal_length, max_repeated_count, max_big_float_exponent,
        ),
    )
    return raw, _wrap_result(set_paths, null_paths, dirs, tables, protos)


# --- Decoders --------------------------------------------------------------


def unmarshal(
    data: str | bytes,
    msg: Message,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
    max_message_size: int = 0,
    max_nesting_depth: int = 0,
    max_numeric_literal_digits: int = 0,
    max_bytes_literal_length: int = 0,
    max_repeated_count: int = 0,
    max_big_float_exponent: int = 0,
) -> None:
    """Decode PXF text into `msg` (in place). Mirrors Go pxf.Unmarshal.

    The limits are described under "Decode limits" above.
    """
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fds = fds_for_message(msg)
    raw = _protowire.pxf_unmarshal(
        text, fds, msg.DESCRIPTOR.full_name, discard_unknown, skip_validate,
        _limits(
            max_message_size, max_nesting_depth, max_numeric_literal_digits,
            max_bytes_literal_length, max_repeated_count, max_big_float_exponent,
        ),
    )
    _upb.load(msg, raw)


def unmarshal_full(
    data: str | bytes,
    msg: Message,
    *,
    discard_unknown: bool = False,
    skip_validate: bool = False,
    max_message_size: int = 0,
    max_nesting_depth: int = 0,
    max_numeric_literal_digits: int = 0,
    max_bytes_literal_length: int = 0,
    max_repeated_count: int = 0,
    max_big_float_exponent: int = 0,
) -> Result:
    """Decode PXF + return per-field presence (set/null) metadata and any
    `@<name>` / `@dataset` directives the decoder saw at the document root.

    Mirrors Go pxf.UnmarshalFull. The limits are described under "Decode
    limits" above.
    """
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fds = fds_for_message(msg)
    raw, set_paths, null_paths, dirs, tables, protos = _protowire.pxf_unmarshal_full(
        text, fds, msg.DESCRIPTOR.full_name, discard_unknown, skip_validate,
        _limits(
            max_message_size, max_nesting_depth, max_numeric_literal_digits,
            max_bytes_literal_length, max_repeated_count, max_big_float_exponent,
        ),
    )
    _upb.load(msg, raw)
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
        # only; type/columns/directives are forwarded on demand. The native
        # reader keeps the from_bytes limits, which scan binds each row under.
        self._native = native
        # scan's descriptor set, for the last message type it bound.
        self._fds: tuple[object, bytes] | None = None

    @classmethod
    def from_bytes(
        cls,
        data: bytes | str,
        *,
        max_message_size: int = 0,
        max_nesting_depth: int = 0,
        max_numeric_literal_digits: int = 0,
        max_bytes_literal_length: int = 0,
        max_repeated_count: int = 0,
        max_big_float_exponent: int = 0,
    ) -> "DatasetReader":
        """Read `data`'s `@dataset` header. The limits are described under
        "Decode limits" above. `scan` binds each row under the same ones,
        except `max_message_size`, which caps the bytes of a row as it is read."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        native = _protowire.PxfDatasetReader.from_bytes(bytes(data), _limits(
            max_message_size, max_nesting_depth, max_numeric_literal_digits,
            max_bytes_literal_length, max_repeated_count, max_big_float_exponent,
        ))
        return cls(native)

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

    def scan(
        self, msg: Message, *, skip_validate: bool = False, discard_unknown: bool = False,
    ) -> bool:
        """Read the next row and bind it to `msg`, as `bind_row` binds it.
        Returns True on success; returns False at EOF (callers check
        `reader.done`).

        This is protowire-cpp's `DatasetReader::Scan`, which binds a row as
        protowire-go's `Scan` does: under the limits given to `from_bytes`
        except `max_message_size`, which caps the bytes of a row as it is
        read (the body a row is bound as repeats the column names, so it is
        wider than the row). A decode error carries the row's position in
        the input. Unless `skip_validate` is set, `msg`'s schema is checked
        against the bind-time rules, as `unmarshal` checks it, on the first
        row of its type rather than on every row.

        As with `bind_row`, the row binds into what `msg` already holds: pass
        a new or cleared message for each row.
        """
        desc = type(msg).DESCRIPTOR
        if self._fds is None or self._fds[0] is not desc:
            self._fds = (desc, fds_for_message(msg))
        raw = self._native.scan(
            msg.SerializePartialToString(), self._fds[1], desc.full_name,
            discard_unknown, skip_validate,
        )
        if raw is None:
            return False
        _upb.load(msg, raw)
        return True


# --- bind_row (per-row proto binding) ------------------------------------


def bind_row(
    msg: Message,
    columns: tuple[str, ...] | list[str],
    row: tuple[Cell, ...] | list[Cell],
    *,
    skip_validate: bool = False,
    discard_unknown: bool = False,
    max_message_size: int = 0,
    max_nesting_depth: int = 0,
    max_numeric_literal_digits: int = 0,
    max_bytes_literal_length: int = 0,
    max_repeated_count: int = 0,
    max_big_float_exponent: int = 0,
) -> None:
    """Bind a `@dataset` row's cells to `msg`'s fields by column name.

    This is protowire-cpp's `BindRow`, which binds a row as protowire-go's
    does: as the body `<column> = <cell>` for each non-empty cell, decoded
    as `unmarshal_full` decodes it. A column may be a dotted path into a
    sub-message. `columns` and `row` MUST have the same length. Cell-state
    semantics:

      - `None`        — field absent. (pxf.default) applies if declared;
                        (pxf.required) errors if neither default nor
                        value is present.
      - `("null", _)` — field present-but-null (draft §3.9): it stays unset
                        and keeps (pxf.default) off. Only a field with
                        presence may be null.
      - any other     — field set to the cell's value.

    The row binds into what `msg` already holds, as in protowire-go: `msg`
    is not cleared first. So bind each row into a new or cleared message. In
    a reused one, a field an earlier row set keeps its value under an empty
    or `null` cell.

    Unless `skip_validate` is set, `msg`'s schema is checked against the
    bind-time rules on every call, as `unmarshal` checks it. A caller
    binding many rows of one type can check once and pass
    `skip_validate=True`; `DatasetReader.scan` does that itself. A decode
    error is positioned in the body the row is bound as, since a row passed
    as cells has no position in its input. The limits apply to the body, as
    for `unmarshal`.
    """
    raw = _protowire.pxf_bind_row(
        msg.SerializePartialToString(), list(columns), [_native_cell(c) for c in row],
        fds_for_message(msg), msg.DESCRIPTOR.full_name, discard_unknown, skip_validate,
        _limits(
            max_message_size, max_nesting_depth, max_numeric_literal_digits,
            max_bytes_literal_length, max_repeated_count, max_big_float_exponent,
        ),
    )
    _upb.load(msg, raw)


def _native_cell(cell: Cell | None):
    """A cell in the shape the native bind_row takes: None, or (kind, value)
    with value a str, or a bool for "bool" and bytes for "bytes"."""
    if cell is None:
        return None
    kind, value = cell
    if kind == "null":
        return ("null", None)
    if kind in ("string", "int", "float", "ident", "timestamp", "duration"):
        return (kind, str(value))
    if kind == "bool":
        return (kind, bool(value))
    if kind == "bytes":
        return (kind, bytes(value))
    raise ValueError(f"bind_row: unknown cell kind {kind!r}")
