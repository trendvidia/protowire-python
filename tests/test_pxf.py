# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Smaller PXF tests — surface API basics and unmarshal_full presence tracking."""

from __future__ import annotations

import pytest

from protowire import pxf


def test_marshal_returns_text(all_types_cls):
    msg = all_types_cls()
    msg.string_field = "hi"
    out = pxf.marshal(msg)
    assert isinstance(out, str)
    assert "hi" in out


def test_unmarshal_clears_existing_fields(all_types_cls):
    msg = all_types_cls()
    msg.string_field = "stale"
    msg.int32_field = 99
    pxf.unmarshal('int64_field = 5', msg)
    # unmarshal clears, so string_field shouldn't survive.
    assert msg.string_field == ""
    assert msg.int32_field == 0
    assert msg.int64_field == 5


def test_unmarshal_full_tracks_set_fields(all_types_cls):
    msg = all_types_cls()
    result = pxf.unmarshal_full('string_field = "x"\nint32_field = 7', msg)
    assert result.is_set("string_field")
    assert result.is_set("int32_field")
    assert result.is_absent("int64_field")
    assert not result.is_null("string_field")


def test_unmarshal_invalid_text_raises(all_types_cls):
    msg = all_types_cls()
    with pytest.raises(ValueError, match="pxf"):
        pxf.unmarshal("this = is // not (valid", msg)


def test_unmarshal_unknown_field_errors_by_default(all_types_cls):
    msg = all_types_cls()
    with pytest.raises(ValueError):
        pxf.unmarshal('not_a_field = 1', msg)


def test_unmarshal_discard_unknown(all_types_cls):
    msg = all_types_cls()
    pxf.unmarshal('not_a_field = 1\nstring_field = "ok"', msg, discard_unknown=True)
    assert msg.string_field == "ok"
