#!/bin/bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# See docs/release_branches.md.
#
# This creates branches for TheRock and select submodules with the chosen name
# at the checked-out commits and pushes them to 'origin'.
#
# Sample usage (from TheRock's repository root):
#     create_release_branches.sh release/bkc/therock-10.3-20261027

set -euox pipefail

BRANCH_NAME="${1:?Usage: create_release_branches.sh <branch-name>}"

REPOSITORY_PATHS=(
    "."
    "rocm-libraries"
    "rocm-systems"
    "compiler/amd-llvm"
    "debug-tools/rocgdb/source"
)

# Always push with SSH, even if the remote is configured for HTTPS.
SSH_PUSH_CONFIG="url.git@github.com:.pushInsteadOf=https://github.com/"

for repository_path in "${REPOSITORY_PATHS[@]}"; do
    # Create the branch in the current repository without changing to it.
    # Reset if it already exists.
    git \
        -C "${repository_path}" \
        branch -f "${BRANCH_NAME}"

    # Push from local to remote.
    git \
        -c "${SSH_PUSH_CONFIG}" \
        -C "${repository_path}" \
        push -u origin "${BRANCH_NAME}:${BRANCH_NAME}"
done
