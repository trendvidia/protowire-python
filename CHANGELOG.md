# Changelog

All notable changes to `protowire-python` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number is kept aligned with the rest of the `protowire-*`
stack — releases bump in lockstep across language ports when the wire
format changes.

## [Unreleased]

### Changed

- **`pxf.bind_row` and `DatasetReader.scan` bind a row as protowire-go does**
  (#24), through protowire-cpp's `BindRow` rather than writing the row out as
  text and decoding it with `unmarshal`. On all 43 of the spec's
  `testdata/dataset/` fixtures, `scan` now gives protowire-go v1.17.0's
  message or error, text and position included. Before, most failed or bound
  differently. What changes:
  - An absent cell takes the field's `(pxf.default)`. An absent
    `(pxf.required)` field is an error, as in `unmarshal_full`.
  - A cell may hold a list or a block (`scan` only; see #47 for rows read as
    cells), and a column may be a dotted path into a sub-message.
  - **A row binds into what the message already holds,** which is no longer
    cleared first. In a reused message, a field an earlier row set keeps its
    value under an empty or `null` cell, as in Go. Bind each row into a new or
    cleared message.
  - **The schema is checked by default** (`skip_validate=False`), as Go and
    `unmarshal` check it. `scan` checks it on the first row of each type;
    `bind_row` on every call. Pass `skip_validate=True` for the old behaviour.
  - A `scan` decode error carries the row's line and column in the input,
    and names the column (`8:1: column int32_field: ...`).
  - Both take `discard_unknown`.
- **Built on protowire-cpp v2.1.0** (#45), up from v2.0.0. Its fixes to the
  `@dataset` streaming reader reach `pxf.DatasetReader`; they are listed under
  Fixed below. See protowire-cpp's [2.1.0 release notes](https://github.com/trendvidia/protowire-cpp/releases/tag/v2.1.0).
- **Built on protowire-cpp v2.0.0** (#31), up from v1.2.0. This port decodes
  and encodes PXF and SBE through it, so the changes below reach Python
  callers with no Python API change. Several narrow what decodes, encodes or
  binds. See protowire-cpp's [2.0.0 changelog](https://github.com/trendvidia/protowire-cpp/blob/main/CHANGELOG.md)
  for each one.
  - **Decoding rejects more.** `null` is rejected where the spec rejects it:
    on a plain proto3 scalar, nested or not, a repeated or map field, a proto2
    `required` field, an editions IMPLICIT scalar, and any field with
    `(pxf.required)`. A keyed entry whose block sets its key field to `null`,
    or another member of the key's oneof, is rejected. A raw line feed in a
    simple string is rejected; write it as `\n`. A `BigFloat` literal is
    refused in protowire-go's words and order of checks. Built on v1.2.0,
    this port accepted 11 of the spec's `nulls/` and `keyed/` reject fixtures.
  - **More schemas are refused at bind time:** a default that cannot be used
    (on a repeated or map field, two on one oneof, `(pxf.default)` and
    `@default` disagreeing), `(pxf.required)` on a oneof member, an option at
    a retired extension number (recompile the descriptor), and a message
    redefining a well-known type with the wrong fields.
  - **`pxf.marshal` writes what reads back:** an unset field with
    `(pxf.default)` or `(pxf.required)` is now written, so that decoding with
    `unmarshal_full` gives the same message. An unset `optional` field that
    is `(pxf.required)` is an error. A message nested past 100 levels is
    refused rather than written.
  - **SBE refuses** a value its `(sbe.encoding)` type cannot hold, in both
    directions, a header value past 16 bits, two messages sharing a
    `(sbe.template_id)` in one schema, a header whose schema id is not the
    target's, and a oneof field.
  - **Error text** matches protowire-go's. Text a caller matched on changes.

- **`Violation.name` for `(pxf.required)` with `(pxf.default)` on one field is
  the default's literal** (#30, trendvidia/protowire-go#211). It was the bare
  field name, which `element` already holds. An empty default gives an empty
  `name`. This matches the other ports. For `(pxf.required)` on a oneof member,
  `name` is the oneof's name, as protowire-cpp reports it.

### Added

- **`scripts/check_decode.py --format pxf-pb` and `--format pb-text`** (#35,
  #37), so the spec's write gate and text gate run this port.
  - `pxf-pb` decodes with `unmarshal_full` and writes the PB bytes, with map
    entries in key order as protowire-go writes them.
  - `pb-text` reads the bytes with the protobuf runtime and writes
    `pxf.marshal`'s text.
  - Both pass every case of both gates, with no exclusions: 87 write cases
    and 60 text cases.
  - The protobuf runtime (upb, protobuf 7.36.2) ignores `deterministic=True`
    for map order, so the harness writes map entries itself.

- **`Violation.kind` values `"default_option"`, `"retired_number"` and
  `"well_known_shape"`,** for the bind-time checks above. They came back as
  `""`.
- **Per-call decode limits** (#36), the six of HARDENING.md § Mandatory
  limits, as keyword arguments on `pxf.unmarshal`, `unmarshal_full`, their
  `_bytes` forms, `bind_row` and `DatasetReader.from_bytes`:
  `max_message_size`, `max_nesting_depth`, `max_numeric_literal_digits`,
  `max_bytes_literal_length`, `max_repeated_count` and
  `max_big_float_exponent`. 0, the default, keeps protowire-cpp's limit; a
  negative value is a `ValueError`. `sbe.Codec` and `Codec.from_message` take
  `max_message_size` and `max_repeated_count` for every call, as
  protowire-cpp's `sbe::CodecOptions` does. `DatasetReader.scan` binds each
  row under the reader's limits. Two caveats: the decoded message still loads
  through the protobuf runtime, which stops at 100 levels of submessages (#40),
  and the reader parses rows under the default `max_bytes_literal_length`
  until protowire-cpp#160 is fixed (`scan` still applies it when binding).
- **`scripts/check_decode.py --limit Name=N`**, so the corpus rows that lower
  a limit run on this port. pb rows take none: the protobuf runtime has no
  such settings, and such a row exits 2.
- **`max_nesting_depth` on `pxf.marshal` and `pxf.marshal_bytes`** (#34,
  protowire#502), the deepest PXF they write, counted as a decoder counts it.
  0, the default, means 100. Raise it only together with the limit of every
  reader of the output. A negative value is a `ValueError`.

### Fixed

- **`DatasetReader` refuses a row cut short instead of ending the dataset
  there** (#45). A last row whose `)` never came, a broken row followed by
  more rows, or junk between rows read as a shorter, complete dataset, with no
  error. Each is now the error `unmarshal_full` gives, at the same position,
  as protowire-go's reader does. The rows end only at the end of the input or
  at the next directive.
- **`DatasetReader` reads rows under its `max_bytes_literal_length`**
  (protowire-cpp#160). Rows were parsed under the default, so a lowered limit
  was only enforced when `scan` bound the row, and `next_or_none` returned it.
- **`DatasetReader` caps the bytes it holds while looking for a row's end at
  `max_message_size`,** as protowire-go does: `dataset input holds N bytes
  without a row boundary, MaxMessageSize=M`. The cap counts every byte the
  reader holds, the header included, and the reader reads 4 KiB at a time.
- **`DatasetReader.scan` no longer applies `max_message_size` to the row it
  binds** (#45), as protowire-go binds it. The body a row is bound as repeats
  the column names, so it is wider than the row: a row the reader accepted
  could still be refused when bound. The other limits apply as before.
- **`DatasetReader` positions count from the start of the input.** Every
  row's error was at `1:1`.
- **`bind_row` and `DatasetReader.scan` escape a line feed in a string
  cell.** On protowire-cpp v2.0.0 a cell holding one failed with `newline in
  string literal`.
- **`pxf.marshal` refuses a message nested one level too deep with the
  encoder's error,** `nesting depth exceeds MaxNestingDepth=100`. It failed
  earlier, with `proto parse failed`, because the bridge read the message back
  at protobuf's own limit before the encoder ran. A message two or more levels
  too deep still fails in that read, and its error now names the depth. A
  Timestamp or other literal at the limit is written: it is one protobuf
  level but no PXF level. Reading such a document back fails in upb (#40).
- **`scripts/check_decode.py --format pb-render` no longer reads the input
  with the stock protobuf reader first.** That reader stops at 100 levels, so
  the corpus's 101-level rows never reached the encoder. A read that fails
  still exits 2.

## [1.1.0] — 2026-10-31

### Added

- **`scripts/check_decode.py`**, the harness `cross_security_check.sh` looks
  for in this port. Without it the HARDENING corpus
  reported Python as "not run". PXF and SBE rows run through the wrapped
  protowire-cpp decoder and PB rows through the protobuf runtime. A row the
  harness could not run exits 2, not 1, so it cannot pass as a reject.
  It also handles `pb-render`, the corpus rows that test the encoder (#28).
  
### Changed

- **Built against protowire-cpp v1.2.0** in CI and the wheels (#23). v1.0.0
  segfaulted on deeply nested PXF and read the PXF and SBE options at their
  old numbers. `Violation.kind` gains `"key_option"`, which v1.1.0 reports.
  v1.2.0 makes `pxf.marshal` refuse a `pxf.Decimal` past the bounds, where it
  used to hang (protowire#448), and it checks `(pxf.required)` with
  `(pxf.default)` itself, so the bridge's copy is gone (#25).

### Fixed

- **`(pxf.required)` with `(pxf.default)` on one field is a bind-time
  violation** (#22), as draft -01 now requires (protowire#453). Such a schema
  used to bind. `unmarshal` and `unmarshal_full` raise `ValueError` unless
  `skip_validate` is set, and `validate_descriptor` reports a
  `"required_default"` violation, over the file and its imports.
  `(pxf.required) = false` beside a default stays accepted.

## [1.0.0] — 2026-05-13

First major-version cut. Implements the three one-time spec changes
from the [protowire v1.0 freeze line](https://github.com/trendvidia/protowire/releases/tag/v1.0.0)
in lockstep with the other v1.0 ports, by re-pinning to
[`protowire-cpp` v1.0.0](https://github.com/trendvidia/protowire-cpp/releases/tag/v1.0.0).
**Breaking** — there is no alias period; v1.0 is itself the major
bump.

### Python API rename

- `pxf.TableDirective` → `pxf.DatasetDirective`
- `pxf.TableReader` → `pxf.DatasetReader`
- `pxf.Result.tables` → `pxf.Result.datasets`
- Tuple cell type aliases are unchanged.

Source files renamed:

- `tests/test_pxf_table_reader.py` → `tests/test_pxf_dataset_reader.py`

### Python API additions

- `pxf.ProtoDirective` (frozen dataclass with `shape`, `type_name`,
  `body`) — exposed in `Result.protos`. The `shape` field is the
  string literal `"anonymous" | "named" | "source" | "descriptor"`,
  matching the cpp enum.
- `pxf.ProtoShape` type alias for the literal union.

### FFI shape

`_protowire.pxf_unmarshal_full` now returns a 6-tuple (raw bytes,
set_paths, null_paths, directives, datasets, **protos**). Python's
`Result` gains a `protos: tuple[ProtoDirective, ...]` field.

### Build

- `pyproject.toml` version `0.75.0` → `1.0.0`.
- `__version__` in `src/protowire/__init__.py` bumped accordingly.
- Sibling-checkout dependency on `../protowire-cpp` resolves at the
  v1.0.0 tag.

### Tests

- New `tests/test_pxf_proto_directive.py` with 16 cases covering all
  four `@proto` body shapes via the FFI roundtrip, multi-`@proto`,
  nested-brace bodies, three error paths, parametrized reserved-
  directive-name rejection, and a `ProtoDirective` dataclass check.
- pytest: 100 tests, 0 failures.

## [0.75.0] — 2026-05-12

First release after the v0.70.0 baseline. Wraps the
[`protowire-cpp` v0.75.0](https://github.com/trendvidia/protowire-cpp/releases/tag/v0.75.0)
PXF v0.72-series feature set into the Python port, bringing the
`@<name>` / `@entry` / `@table` directive grammar, schema reserved-name
validator, `Result.directives` / `Result.tables` accessors, and the
`TableReader` streaming row reader through the nanobind FFI. The
Python port skips intermediate version numbers and lands the bundled
feature set directly on v0.75.0 to match the active wire revision
across the `protowire-*` stack.

### Added

- **`pxf.TableReader` and `pxf.bind_row`** (draft §3.4.4). Streaming
  consumption for the `@table` directive, alternative to materializing
  every row into `Result.tables` up front. Construct via
  `pxf.TableReader.from_bytes(data)`; iterate with the standard for
  loop or call `next_or_none()` until it returns `None`. The reader
  exposes the header `type` / `columns` / `directives` properties and
  a `tail()` method that returns the unconsumed buffer for chaining a
  second reader on multi-`@table` documents. `bind_row(msg, columns,
  row)` is the per-row binder used by `scan()` and exposed as a
  free function for callers iterating `Result.tables[i].rows` from
  the materializing path. Strategy is format-and-reparse, matching
  the C++ port: cells are rendered as a synthetic PXF body and run
  through `unmarshal`, reusing every branch of the existing decoder
  (WKT timestamps / durations, wrapper-nullability, enum-by-name,
  `pxf.required` / `pxf.default`, oneof). PR-2 takes input as bytes;
  a file-like / chunked-IO bridge is a possible follow-up.

### Changed

- **CI pin to protowire-cpp v0.75.0.** The cpp sibling now ships the
  PXF v0.72-series feature set (directive grammar, schema validator,
  Result accessors, TableReader streaming). The pin moves from the
  pre-v0.72 commit `9af2ec0` to the `v0.75.0` tag so the Python
  wrapper exposes the new surface.

### Added

- **`pxf.Result.directives` / `pxf.Result.tables`** — the document-root
  directives the decoder saw at `unmarshal_full` time, exposed as
  immutable dataclasses:
  - `pxf.Directive(name, prefixes, type, body, has_body, line, column)`
    for generic `@<name> *(prefix) [{ ... }]` blocks. `body` is the
    raw bytes between `{` and `}` (verbatim), suitable for handing to
    a follow-up `pxf.unmarshal` against the consumer's message type.
    `type` keeps the v0.72.0 single-prefix back-compat shape.
  - `pxf.TableDirective(type, columns, rows)` for `@table` directives,
    with cells modeled as `None` (absent) or a `(kind, value)` 2-tuple
    where kind ∈ {`"null"`, `"string"`, `"int"`, `"float"`, `"bool"`,
    `"bytes"`, `"ident"`, `"timestamp"`, `"duration"`} — faithful to
    the three-state cell grammar (absent / present-but-null /
    present-with-value, draft §3.4.4).
- **`pxf.validate_descriptor(msg)` + `pxf.Violation`** — schema
  reserved-name check (draft §3.13). Returns the list of fields,
  oneofs, and enum values whose names case-sensitively match a PXF
  value keyword (`null` / `true` / `false`). Sorted by element FQN.
- **`skip_validate` keyword** on `pxf.unmarshal` and
  `pxf.unmarshal_full` (and the `_bytes` variants) — opt-out of the
  per-call schema validator when the caller has already validated the
  descriptor at registry-load time.

## [0.70.0]

Initial public release. The version number aligns this port with the rest
of the `protowire-*` stack, which targets the 0.70.x series for the first
coordinated public release. The wire codec is provided by
[`protowire-cpp`](https://github.com/trendvidia/protowire-cpp) and reaches
Python through a [nanobind](https://github.com/wjakob/nanobind) FFI; this
port's behaviour follows the C++ port's at every wire-level question.

### Added

- **PyPI distribution** as the `protowire-python` package (the bare
  `protowire` was taken by an unrelated 2021 CLI; the import name stays
  `import protowire`). Binary wheels built by CI for CPython 3.10–3.13
  on Linux × {x86_64, aarch64}, macOS × {x86_64, arm64}, and Windows ×
  x86_64. Wheels are published through PyPI OIDC trusted publishing
  with Sigstore provenance attestations.
- **Comprehensive CI matrix**: build + test on Python 3.10/3.11/3.12/3.13
  across Linux/macOS/Windows, plus a `cibuildwheel` smoke build on every
  PR to catch packaging regressions early. Weekly CodeQL SAST.
- **Governance scaffolding**: `LICENSE` (MIT), `CONTRIBUTING.md`,
  `SECURITY.md` (security@trendvidia.com), `GOVERNANCE.md`,
  `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS`, issue + PR templates,
  Dependabot for GitHub Actions and pip.

### Changed (breaking)

- **PXF parser stricter on key forms**, mirroring the upstream grammar
  tightening in
  [`trendvidia/protowire@8262bbb`](https://github.com/trendvidia/protowire/commit/8262bbb)
  (`docs/grammar.ebnf`, `docs/draft-trendvidia-protowire-00.txt`):
  - `=` (field assignment) and `{ … }` (submessage) now require an
    identifier key. Inputs like `123 = 234` or `child { 123 = 123 }`
    now raise `pxf.ParseError` with
    `"field assignment with '=' requires an identifier key, got integer
    (\"123\"); use ':' for map entries"`.
  - `:` (map entry) is rejected at document top level — the document
    represents a proto message, never a `map<K,V>`. Use `=` for
    top-level field assignments. Map literals (`field = { 1: "x" }`)
    still work because `:` remains valid inside `{ … }` blocks.
