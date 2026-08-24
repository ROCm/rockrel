# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Compatibility helpers for every Python version supported by CI.

Python 3.11 added :class:`enum.StrEnum`. On newer interpreters this module
exports that standard-library type unchanged. The small Python 3.10 fallback
keeps the behavior relied on by the automation: members are strings, ``auto``
uses lower-case member names, string formatting emits the value, and JSON
encoders serialize members as strings.
"""

from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised by the Python 3.10 CI job
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10 equivalent of the standard-library ``StrEnum``."""

        def __new__(cls, value: str):
            """Construct one string-valued compatibility enum member."""

            if not isinstance(value, str):
                raise TypeError(f"{value!r} is not a string")
            member = str.__new__(cls, value)
            member._value_ = value
            return member

        @staticmethod
        def _generate_next_value_(name, start, count, last_values):
            """Derive the lowercase value used by ``auto``."""

            return name.lower()

        __str__ = str.__str__
        __format__ = str.__format__
