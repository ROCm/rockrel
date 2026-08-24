# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Opaque program invariant paired with the Action job's scoped token boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_ACTION_KEY = object()
_LOCAL_KEY = object()
_TEST_KEY = object()
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class DraftWriteAuthority:
    """Represent explicit, unforgeable authority for draft-only writes."""

    plan_fingerprint: str
    _key: object = field(repr=False)


def _action_authority(plan_fingerprint: str) -> DraftWriteAuthority:
    """Grant Action-only draft authority after validating its SHA-256 plan binding."""

    if DIGEST_RE.fullmatch(plan_fingerprint) is None:
        raise ValueError("plan fingerprint must be a SHA-256 digest")
    return DraftWriteAuthority(plan_fingerprint, _ACTION_KEY)


def _local_authority(plan_fingerprint: str) -> DraftWriteAuthority:
    """Create the explicit local-only draft write authority."""

    if DIGEST_RE.fullmatch(plan_fingerprint) is None:
        raise ValueError("plan fingerprint must be a SHA-256 digest")
    return DraftWriteAuthority(plan_fingerprint, _LOCAL_KEY)


def test_draft_write_authority(
    plan_fingerprint: str = "f" * 64,
) -> DraftWriteAuthority:
    """Create an unmistakable draft write authority for unit tests."""

    return DraftWriteAuthority(plan_fingerprint, _TEST_KEY)


def is_valid_authority(
    authority: object | None, plan_fingerprint: str | None = None
) -> bool:
    """Return whether an object is a valid draft write authority."""

    if not isinstance(authority, DraftWriteAuthority):
        return False
    if authority._key not in {_ACTION_KEY, _LOCAL_KEY, _TEST_KEY}:
        return False
    return plan_fingerprint is None or authority.plan_fingerprint == plan_fingerprint
