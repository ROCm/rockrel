# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
from enum import Enum, auto

from scripts.cherry_pick.compat import StrEnum


class ExampleValue(StrEnum):
    EXPLICIT = "wire_value"
    GENERATED = auto()


def test_str_enum_preserves_stdlib_value_string_json_and_enum_behavior():
    assert issubclass(ExampleValue, str)
    assert issubclass(ExampleValue, Enum)
    assert ExampleValue.EXPLICIT.value == "wire_value"
    assert str(ExampleValue.EXPLICIT) == "wire_value"
    assert f"{ExampleValue.EXPLICIT}" == "wire_value"
    assert json.dumps({"value": ExampleValue.EXPLICIT}) == '{"value": "wire_value"}'
    assert ExampleValue("wire_value") is ExampleValue.EXPLICIT
    assert ExampleValue.GENERATED.value == "generated"
    assert list(ExampleValue) == [ExampleValue.EXPLICIT, ExampleValue.GENERATED]
