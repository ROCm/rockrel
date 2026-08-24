# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import pytest

from scripts.cherry_pick.config import ConfigError, load_config
from scripts.tests.cherry_pick_config_test import (
    valid_document,
    valid_train,
    write_config,
)


def test_train_dependency_mode_is_required_and_preserved(tmp_path):
    config = load_config(write_config(tmp_path, valid_document()))
    assert config.train("10.1-20260811").dependency_mode == "gate"

    managed = valid_train(dependency_mode="managed_stack")
    config = load_config(write_config(tmp_path, valid_document(trains=[managed])))
    assert config.train("10.1-20260811").dependency_mode == "managed_stack"


@pytest.mark.parametrize("mode", [None, "", "automatic", True, 1])
def test_train_dependency_mode_fails_closed(tmp_path, mode):
    train = valid_train(dependency_mode=mode)
    with pytest.raises(ConfigError, match="dependency_mode"):
        load_config(write_config(tmp_path, valid_document(trains=[train])))


def test_train_dependency_mode_cannot_be_omitted(tmp_path):
    train = valid_train()
    train.pop("dependency_mode")
    with pytest.raises(ConfigError, match="dependency_mode"):
        load_config(write_config(tmp_path, valid_document(trains=[train])))
