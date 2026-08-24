# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Process-scoped Git environment for trusted GitHub Actions adapters."""

from __future__ import annotations

import base64
from collections.abc import Mapping


def action_git_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Build the noninteractive Git environment for an Actions token."""

    environment = dict(base)
    token = environment.pop("GITHUB_TOKEN", None)
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"})
    if environment.get("GITHUB_ACTIONS") == "true" and token:
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        environment.update(
            {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
                "GIT_CONFIG_KEY_1": "credential.interactive",
                "GIT_CONFIG_VALUE_1": "never",
            }
        )
    return environment


def gh_git_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Use the authenticated gh credential helper without copying its token."""

    environment = dict(base)
    environment.pop("GITHUB_TOKEN", None)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
            "GIT_CONFIG_VALUE_0": "!gh auth git-credential",
            "GIT_CONFIG_KEY_1": "credential.interactive",
            "GIT_CONFIG_VALUE_1": "never",
        }
    )
    return environment
