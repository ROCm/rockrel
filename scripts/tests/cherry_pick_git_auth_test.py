# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from scripts.cherry_pick.git_auth import action_git_environment, gh_git_environment


def test_action_git_environment_scopes_auth_without_plaintext_token():
    environment = action_git_environment(
        {"GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "short-lived-token"}
    )

    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_CONFIG_COUNT"] == "2"
    assert environment["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert environment["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")
    assert "short-lived-token" not in environment["GIT_CONFIG_VALUE_0"]
    assert environment["GIT_CONFIG_KEY_1"] == "credential.interactive"
    assert environment["GIT_CONFIG_VALUE_1"] == "never"


def test_non_action_git_environment_never_adds_credentials():
    environment = action_git_environment(
        {"GITHUB_TOKEN": "must-not-be-used", "EXISTING": "preserved"}
    )

    assert environment["EXISTING"] == "preserved"
    assert "GIT_CONFIG_COUNT" not in environment
    assert "must-not-be-used" not in " ".join(environment.values())


def test_gh_git_environment_uses_gh_credential_helper_without_exporting_token():
    environment = gh_git_environment(
        {"GITHUB_TOKEN": "must-not-be-forwarded", "EXISTING": "preserved"}
    )

    assert environment["EXISTING"] == "preserved"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["GIT_CONFIG_COUNT"] == "2"
    assert environment["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert environment["GIT_CONFIG_VALUE_0"] == "!gh auth git-credential"
    assert environment["GIT_CONFIG_KEY_1"] == "credential.interactive"
    assert environment["GIT_CONFIG_VALUE_1"] == "never"
    assert "GITHUB_TOKEN" not in environment
    assert "must-not-be-forwarded" not in " ".join(environment.values())
