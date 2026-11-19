# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""A tag's wire type, as protobuf reads it (#57, protowire#614).

A PB decoder rejects wire type 6 or 7 on any field, and handles a known field
whose wire type does not match its type as an unknown field: skipped by its
wire type, with the field left as it was. ``pb.unmarshal`` used to read a
known field by its declared type whatever the tag said, so ``0a01`` on an
int64 read the length byte as the value 1, and ``0a0101`` was refused as
corrupt.

Two kinds of test, as in protowire-go#308 and protowire-cpp#186:

- the oracle tests port protowire-go's encoding/pb/wire_type_test.go. Every
  field of ``ConfAll`` gets one record of every wire type, well formed and cut
  short, at the top level and inside each message and map-entry field: 606
  inputs. Each must get the protobuf runtime's verdict, and on accept the same
  values. The same is done for the fields of the big-number messages. The
  runtime is its pure-Python implementation, for the reason given at
  ``_ORACLE``; upb's verdicts are checked too.
- the table tests port protowire-cpp's test/pb_wire_type_test.cc: an input,
  protowire-go's error text, and what protowire-go's Marshal writes for the
  struct the input decoded to. The rows were made by running protowire-go at
  a0a78c2, and the dataclasses' attribute names are the Go structs' member
  names, since an error names the member.
"""

from __future__ import annotations

import copy
import decimal
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Annotated, Optional

import pytest
from google.protobuf import message_factory
from test_pb_conformance import ConfAll, ConfInner

from protowire import pb

VARINT, FIXED64, BYTES, START_GROUP, END_GROUP, FIXED32 = 0, 1, 2, 3, 4, 5


def _varint(v: int) -> bytes:
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def _tag(num: int, typ: int) -> bytes:
    return _varint(num << 3 | typ)


def wire_records(num: int) -> list[bytes]:
    """Go's wireRecords: single records for field num, every wire type, each
    with a well-formed value and a value cut short."""
    t = lambda typ: _tag(num, typ)  # noqa: E731
    return [
        t(VARINT) + b"\x01",
        t(VARINT) + b"\x80",  # cut short
        t(FIXED64) + bytes([1, 0, 0, 0, 0, 0, 0, 0]),
        t(FIXED64) + bytes([1, 0, 0]),  # cut short
        t(BYTES) + b"\x00",
        t(BYTES) + b"\x01\x01",
        t(BYTES) + b"\x01",  # cut short
        t(START_GROUP) + t(END_GROUP),
        t(START_GROUP),  # never ended
        t(END_GROUP),  # ends nothing
        t(FIXED32) + bytes([1, 0, 0, 0]),
        t(FIXED32) + bytes([1, 0]),  # cut short
        t(6) + b"\x01",
        t(7) + b"\x01",
    ]


def _wrapped(outer: int, rec: bytes) -> bytes:
    return _tag(outer, BYTES) + _varint(len(rec)) + rec


def wire_type_inputs() -> list[bytes]:
    """Go's wireTypeInputs: every record for every field of ConfAll, alone and
    carried inside each message field (12, 13, 22: Inner's fields 1, 2 and an
    unknown 3) and map field (18, 19, 25: key 1, value 2, unknown 3)."""
    out = []
    for num in range(1, 26):
        out += wire_records(num)
    for outer in (12, 13, 18, 19, 22, 25):
        for inner in (1, 2, 3):
            out += [_wrapped(outer, rec) for rec in wire_records(inner)]
    # A matching record after a mismatched one: the mismatch leaves the field
    # as it was, and the match still sets it.
    out += [
        bytes.fromhex("0a0161" "0801"),  # s: "a", then s as a varint
        bytes.fromhex("1007" "1501000000"),  # i64: 7, then i64 as fixed32
        bytes.fromhex("720101" "7001"),  # nums packed [1], then nums as a varint
        bytes.fromhex("7001" "7501000000"),  # nums [1], then nums as fixed32
    ]
    return out


# The oracle runs in a child process on the runtime's pure-Python
# implementation, which the environment variable selects at import. upb, the
# default one, reads a map entry that carries an unknown field (a key or value
# of another wire type, or a field 3) by keeping the whole entry among the
# parent's unknown fields, so the map stays empty. protobuf-go, libprotobuf and
# the pure-Python implementation all insert the entry, with the unknown field
# dropped, and that is what protowire-go does. On these inputs the two
# implementations give the same verdicts and differ only in those 46 map
# values; test_upb_verdicts_agree checks the verdicts against upb as well.
_ORACLE = r"""
import json, sys
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.internal import api_implementation
req = json.load(sys.stdin)
fds = descriptor_pb2.FileDescriptorSet.FromString(bytes.fromhex(req["fds"]))
pool = descriptor_pool.DescriptorPool()
for f in fds.file:
    pool.Add(f)
cls = message_factory.GetMessageClass(pool.FindMessageTypeByName(req["message"]))
out = []
for h in req["inputs"]:
    try:
        m = cls.FromString(bytes.fromhex(h))
    except Exception:
        out.append(None)
        continue
    m.DiscardUnknownFields()
    out.append(m.SerializeToString(deterministic=True).hex())
json.dump({"impl": api_implementation.Type(), "known": out}, sys.stdout)
"""


def oracle_known(fds: bytes, message: str, inputs: list[bytes]) -> list[Optional[bytes]]:
    """For each input, None if the runtime rejects it, or else the message it
    reads, without its unknown fields, written again."""
    env = dict(os.environ, PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION="python")
    req = json.dumps({"fds": fds.hex(), "message": message, "inputs": [d.hex() for d in inputs]})
    res = subprocess.run([sys.executable, "-c", _ORACLE], input=req, capture_output=True, text=True, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    # A positive marker that the oracle is the implementation meant.
    assert got["impl"] == "python"
    return [None if k is None else bytes.fromhex(k) for k in got["known"]]


def check_against_oracle(known: list[Optional[bytes]], inputs: list[bytes], decode) -> tuple[int, int]:
    """Each input must get the oracle's verdict, and on accept the same values:
    what the oracle read, without its unknown fields, must decode to what the
    input decoded to. Returns the counts accepted and rejected."""
    accepted = rejected = 0
    wrong = []
    for data, k in zip(inputs, known):
        try:
            got = decode(data)
        except ValueError as e:
            got, err = None, e
        else:
            err = None
        if k is None:
            rejected += 1
            if err is None:
                wrong.append(f"{data.hex()}: the runtime rejects it, pb accepts it as {got!r}")
            continue
        accepted += 1
        if err is not None:
            wrong.append(f"{data.hex()}: the runtime accepts it, pb rejects it: {err}")
            continue
        want = decode(k)
        if got != want:
            wrong.append(f"{data.hex()}: values differ from the runtime's: got {got!r}, want {want!r}")
    assert not wrong, f"{len(wrong)} of {len(inputs)} inputs differ:\n" + "\n".join(wrong)
    return accepted, rejected


def _empty_map_messages(m: ConfAll) -> ConfAll:
    """Stand an empty ConfInner in mm for the None that an entry with no value
    decodes to here, where the runtime reads an empty message. That difference
    is #58, not a wire type's; this goes when #58 is fixed, as Go's
    emptyMapMessages went with protowire-go#309."""
    m = copy.deepcopy(m)
    for k, v in m.mm.items():
        if v is None:
            m.mm[k] = ConfInner()
    return m


def _decode_all(data: bytes) -> ConfAll:
    return _empty_map_messages(pb.unmarshal(data, ConfAll))


def test_wire_type_matches_protobuf(pb_oracle_fds):
    """Ports TestUnmarshal_WireTypeMatchesProtobuf: 606 inputs."""
    inputs = wire_type_inputs()
    assert len(inputs) == 606
    accepted, rejected = check_against_oracle(oracle_known(pb_oracle_fds, "conf.All", inputs), inputs, _decode_all)
    # A positive marker that both verdicts were exercised, and not only one.
    # protobuf-go accepts 254 and rejects 352 of the same inputs.
    assert (accepted, rejected) == (254, 352)


def test_upb_verdicts_agree(pb_oracle_pool):
    """The default implementation, upb, gives every input the same verdict."""
    cls = message_factory.GetMessageClass(pb_oracle_pool.FindMessageTypeByName("conf.All"))
    inputs = wire_type_inputs()
    wrong = []
    accepted = 0
    for data in inputs:
        try:
            cls.FromString(data)
            want = True
        except Exception:  # the runtime's DecodeError
            want = False
        accepted += want
        try:
            pb.unmarshal(data, ConfAll)
            got = True
        except ValueError:
            got = False
        if got != want:
            wrong.append(f"{data.hex()}: upb {'accepts' if want else 'rejects'} it, pb does not")
    assert not wrong, "\n".join(wrong)
    assert accepted == 254


@dataclass
class BigIntHolder:
    v: Annotated[int, pb.Field(1, pb.BIGINT)] = 0


@dataclass
class DecimalHolder:
    v: Annotated[decimal.Decimal, pb.Field(1)] = field(default_factory=decimal.Decimal)


@dataclass
class BigFloatHolder:
    v: Annotated[pb.BigFloat, pb.Field(1)] = field(default_factory=pb.BigFloat)


@pytest.mark.parametrize(
    "name,holder,fields",
    [("pxf.BigInt", BigIntHolder, 2), ("pxf.Decimal", DecimalHolder, 3), ("pxf.BigFloat", BigFloatHolder, 4)],
)
def test_big_number_wire_type_matches_protobuf(pb_oracle_fds, name, holder, fields):
    """The big-number messages by the same rule: every record of every wire
    type for each of their fields and one unknown field, read by the runtime
    as the message itself and by pb inside a field of that type."""
    inputs = [rec for num in range(1, fields + 2) for rec in wire_records(num)]

    def decode(msg: bytes):
        return pb.unmarshal(_wrapped(1, msg), holder).v

    accepted, rejected = check_against_oracle(oracle_known(pb_oracle_fds, name, inputs), inputs, decode)
    # Per field: the six well-formed records are read or skipped, and the
    # eight malformed ones (four cut short, a group never ended, an end-group
    # ending nothing, wire types 6 and 7) are refused.
    assert (accepted, rejected) == (6 * (fields + 1), 8 * (fields + 1))


# --- protowire-go's texts and values (protowire-cpp's pb_wire_type_test.cc) --


@dataclass
class WtInner:
    Name: Annotated[str, pb.Field(1)] = ""
    N: Annotated[int, pb.Field(2)] = 0


@dataclass
class WtMsg:
    S: Annotated[str, pb.Field(1)] = ""
    I64: Annotated[int, pb.Field(2)] = 0
    P: Annotated[Optional[int], pb.Field(3)] = None
    L: Annotated[list[str], pb.Field(4)] = field(default_factory=list)
    Nums: Annotated[list[int], pb.Field(5, pb.UINT32)] = field(default_factory=list)
    M: Annotated[dict[str, int], pb.Field(6)] = field(default_factory=dict)
    F32: Annotated[float, pb.Field(7, pb.FLOAT)] = 0.0
    F64: Annotated[float, pb.Field(8)] = 0.0
    B: Annotated[Optional[bool], pb.Field(9)] = None
    Inner: Annotated[Optional[WtInner], pb.Field(10)] = None
    Z: Annotated[int, pb.Field(11, pb.SINT32)] = 0
    Buf: Annotated[bytes, pb.Field(12)] = b""


@dataclass
class BigMsg:
    I: Annotated[int, pb.Field(1, pb.BIGINT)] = 0  # noqa: E741
    R: Annotated[decimal.Decimal, pb.Field(2)] = field(default_factory=decimal.Decimal)
    F: Annotated[pb.BigFloat, pb.Field(3)] = field(default_factory=pb.BigFloat)


RESERVED = "proto: cannot parse reserved wire type"

# (input, "ok" or protowire-go's error, what protowire-go writes on accept)
WT_ROWS = [
    # Wire types 6 and 7 on a known field of each kind.
    ("0e01", f"corrupt field 1: {RESERVED}", ""),
    ("0f0161", f"corrupt field 1: {RESERVED}", ""),
    ("1601", f"corrupt field 2: {RESERVED}", ""),
    ("1f01", f"corrupt field 3: {RESERVED}", ""),
    ("3e01", f"corrupt field 7: {RESERVED}", ""),
    ("5701", f"corrupt field 10: {RESERVED}", ""),
    # Another wire type, well formed: skipped, and the field left as it was.
    ("0801", "ok", ""),
    ("0d01000000", "ok", ""),
    ("090100000000000000", "ok", ""),
    ("120101", "ok", ""),
    ("1501000000", "ok", ""),
    ("1b1c", "ok", ""),
    ("1a0101", "ok", ""),
    ("2001", "ok", ""),
    ("2501000000", "ok", ""),
    ("2a0101", "ok", "2a0101"),  # packed
    ("3d00000000", "ok", ""),
    ("3801", "ok", ""),
    ("4001", "ok", ""),
    ("4a0101", "ok", ""),
    ("4801", "ok", "4801"),
    ("5001", "ok", ""),
    ("5a0101", "ok", ""),
    ("6001", "ok", ""),
    # Another wire type, cut short.
    ("1201", "corrupt field 2: unexpected EOF", ""),
    ("1d01", "corrupt field 3: unexpected EOF", ""),
    ("1901", "corrupt field 3: unexpected EOF", ""),
    ("0d01", "corrupt field 1: unexpected EOF", ""),
    ("1b", "corrupt field 3: unexpected EOF", ""),
    ("1c", "corrupt field 3: proto: mismatching end group marker", ""),
    # A match after a mismatch, and a mismatch after a match.
    ("0a01610801", "ok", "0a0161"),
    ("10071501000000", "ok", "1007"),
    ("2a01012801", "ok", "2a020101"),
    ("28012d01000000", "ok", "2a0101"),
    ("18012a0161", "ok", "18012a0161"),
    ("2201612001", "ok", "220161"),
    # A map entry's key or value of another wire type is skipped.
    ("32080a016b1501000000", "ok", "32050a016b1000"),
    ("320408011001", "ok", "32040a001001"),
    ("32050a016b1001", "ok", "32050a016b1001"),
    ("32060a016b150100", "field M: corrupt field 2 in map entry: unexpected EOF", ""),
    ("32031f0101", f"field M: corrupt field 3 in map entry: {RESERVED}", ""),
    ("32040a016b1e", f"field M: corrupt field 3 in map entry: {RESERVED}", ""),
    # So is a nested message's field.
    ("520408011001", "ok", "52021001"),
    ("52030e0100", f"field Inner: corrupt field 1: {RESERVED}", ""),
]

BIG_ROWS = [
    ("0a060a0105120101", "ok", "0a030a0105"),  # BigInt.negative as bytes: skipped
    ("0a020805", "ok", ""),  # BigInt.abs as a varint: skipped
    ("0a040a01051d", "field I: corrupt field 3 in BigInt: unexpected EOF", ""),
    ("12080a010f1501000000", "ok", "12030a010f"),  # Decimal.scale as fixed32
    ("12050a010f1a01", "field R: corrupt field 3 in Decimal: unexpected EOF", ""),
    # BigFloat.exponent as bytes: the value is 1 at prec 53.
    ("1a080a01011201011835", "ok", "1a160a071000000000000010ccffffffffffffffff011835"),
    ("1a070a010118352201", "field F: corrupt field 4 in BigFloat: unexpected EOF", ""),
    ("0a020e01", f"field I: corrupt field 1 in BigInt: {RESERVED}", ""),
    ("12031f0101", f"field R: corrupt field 3 in Decimal: {RESERVED}", ""),
    ("1a022601", f"field F: corrupt field 4 in BigFloat: {RESERVED}", ""),
    ("1a021d01", "field F: corrupt field 3 in BigFloat: unexpected EOF", ""),
    ("1a030a0101", "ok", ""),
    ("0a020d01", "field I: corrupt field 1 in BigInt: unexpected EOF", ""),
]


def _check_row(cls, data: str, status: str, written: str) -> None:
    if status != "ok":
        with pytest.raises(ValueError) as e:
            pb.unmarshal(bytes.fromhex(data), cls)
        assert str(e.value) == status
        return
    m = pb.unmarshal(bytes.fromhex(data), cls)
    assert pb.marshal(m).hex() == written


@pytest.mark.parametrize("data,status,written", WT_ROWS, ids=[r[0] for r in WT_ROWS])
def test_matches_protowire_go(data, status, written):
    _check_row(WtMsg, data, status, written)


@pytest.mark.parametrize("data,status,written", BIG_ROWS, ids=[r[0] for r in BIG_ROWS])
def test_big_numbers_match_protowire_go(data, status, written):
    _check_row(BigMsg, data, status, written)


def test_row_tables_exercise_both_verdicts():
    """A positive marker that both verdicts were exercised, as CheckRows has."""
    for rows in (WT_ROWS, BIG_ROWS):
        ok = sum(1 for r in rows if r[1] == "ok")
        assert ok > 3 and len(rows) - ok > 3


def test_mismatch_leaves_field_alone():
    """A skipped field is not touched: an Optional stays None, and a list gets
    no element."""
    m = pb.unmarshal(
        bytes.fromhex(
            "1d01000000"  # P as fixed32
            "2001"  # L as a varint
            "4d01000000"  # B as fixed32
            "5001"  # Inner as a varint
            "0a0161"  # S, well formed
        ),
        WtMsg,
    )
    assert m.P is None
    assert m.L == []
    assert m.B is None
    assert m.Inner is None
    assert m.S == "a"


def test_mismatch_leaves_merged_value_alone():
    """unmarshal into an instance merges: a mismatched record keeps what the
    instance held."""
    m = WtMsg(S="kept", I64=7, L=["x"])
    pb.unmarshal(bytes.fromhex("0801" "1501000000" "2001"), m)
    assert (m.S, m.I64, m.L) == ("kept", 7, ["x"])
