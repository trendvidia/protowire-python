#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Cross-port envelope wire-compatibility dumper.

Constructs a canonical envelope, encodes it via :py:mod:`protowire.envelope`,
and prints the bytes as a hex string. The same canonical value is constructed
in every other port; the spec repo's `scripts/cross_envelope_check.sh`
asserts that all ports' hex output is byte-identical.

Mirrors `protowire-go/scripts/dump_envelope/main.go`.
"""

from __future__ import annotations

from protowire.envelope import Envelope


def main() -> None:
    env = Envelope.err(402, "INSUFFICIENT_FUNDS", "balance too low",
                       "$3.50", "$10.00")
    env.data = bytes([0xDE, 0xAD, 0xBE, 0xEF])
    assert env.error is not None
    env.error.with_field("amount", "MIN_VALUE", "below minimum", "10.00")
    env.error.with_meta("request_id", "req-123")

    print(env.encode().hex())


if __name__ == "__main__":
    main()
