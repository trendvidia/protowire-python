# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""pb.marshal refuses a value deeper than a decoder at the same limit accepts.

Ports protowire-go's encoding/pb/marshal_depth_test.go (protowire-go#234):
nothing marshal writes is refused for depth when read back.

The depths here (at most 151 levels) stay well inside Python's default
recursion limit: each level costs pb two or three frames.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Annotated, Optional

import pytest

from protowire import pb


@dataclass
class DepthNode:
    next: Annotated[Optional[DepthNode], pb.Field(1)] = None
    kids: Annotated[list[Optional[DepthNode]], pb.Field(2)] = field(default_factory=list)
    by_key: Annotated[dict[str, Optional[DepthNode]], pb.Field(3)] = field(default_factory=dict)
    scalar: Annotated[dict[str, int], pb.Field(4, pb.INT32)] = field(default_factory=dict)
    v: Annotated[int, pb.Field(5, pb.INT32)] = 0


_WRAPS = {
    "singular": lambda c: DepthNode(next=c),
    "list": lambda c: DepthNode(kids=[c]),
    "map": lambda c: DepthNode(by_key={"k": c}),
}
_LEAVES = {
    "scalar": lambda: DepthNode(v=1),
    "scalar map": lambda: DepthNode(scalar={"a": 1}),
}


@pytest.mark.parametrize("wrap_name", list(_WRAPS))
@pytest.mark.parametrize("leaf_name", list(_LEAVES))
def test_depth_agrees_with_unmarshal(wrap_name, leaf_name):
    """Ports TestMarshal_DepthAgreesWithUnmarshal.

    Each wrap adds levels to the chain; each leaf sits at its bottom. Every
    shape marshal writes, unmarshal reads; and some shapes are refused.
    """
    wrap, leaf = _WRAPS[wrap_name], _LEAVES[leaf_name]
    written = refused = 0
    for n in range(1, pb.MAX_NESTING_DEPTH + 3):
        m = leaf()
        for _ in range(n):
            m = wrap(m)
        try:
            out = pb.marshal(m)
        except ValueError as e:
            assert "nesting depth exceeds MaxNestingDepth=100" in str(e), f"{wrap_name}/{leaf_name} {n}: {e}"
            refused += 1
            continue
        written += 1
        pb.unmarshal(out, DepthNode())
    assert written > 0, f"{wrap_name}/{leaf_name}"
    assert refused > 0, f"{wrap_name}/{leaf_name}"


def deep_node(n: int) -> DepthNode:
    m = DepthNode(v=1)
    for _ in range(n):
        m = DepthNode(next=m)
    return m


def test_depth_bound():
    """Ports TestMarshal_DepthBound."""
    out = pb.marshal(deep_node(pb.MAX_NESTING_DEPTH))
    pb.unmarshal(out, DepthNode())
    with pytest.raises(ValueError, match="nesting depth exceeds MaxNestingDepth=100"):
        pb.marshal(deep_node(pb.MAX_NESTING_DEPTH + 1))


def test_marshal_options_max_nesting_depth():
    """Ports TestMarshalOptions_MaxNestingDepth."""
    m = deep_node(150)
    out = pb.marshal(m, max_nesting_depth=150)
    pb.unmarshal(out, DepthNode(), max_nesting_depth=150)
    with pytest.raises(ValueError, match="MaxNestingDepth=149"):
        pb.marshal(m, max_nesting_depth=149)
    with pytest.raises(ValueError, match="MaxNestingDepth=2"):
        pb.marshal(deep_node(3), max_nesting_depth=2)


def test_encoder_depth():
    """Ports TestEncoder_Depth: a refused message writes no frame."""
    buf = io.BytesIO()
    with pytest.raises(ValueError, match="MaxNestingDepth=100"):
        pb.Encoder(buf).encode(deep_node(101))
    assert buf.getvalue() == b"", "a refused message writes no frame"
    pb.Encoder(buf, max_nesting_depth=101).encode(deep_node(101))
    assert buf.getvalue() != b""


def test_pointer_cycle():
    """Ports TestMarshal_PointerCycle: a cycle used to overflow Go's stack, which
    no recover catches. Here it is refused by the depth bound, before
    Python's recursion limit is reached."""
    m = DepthNode()
    m.next = m
    with pytest.raises(ValueError, match="nesting depth exceeds MaxNestingDepth=100"):
        pb.marshal(m)
