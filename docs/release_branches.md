# Release branches

Table of contents:

* [About release branches](#about-release-branches)
  * [Release branch naming](#release-branch-naming)
  * [Release branch protections and policies](#release-branch-protections-and-policies)
  * [Repositories in scope for release branching](#repositories-in-scope-for-release-branching)
* [Creating release branches](#creating-release-branches)
  * [Selecting a branch point](#selecting-a-branch-point)
  * [Creating named release branches](#creating-named-release-branches)
  * [Initializing release branches](#initializing-release-branches)
    * [(Stable only) Bump the rocm-version in TheRock](#stable-only-bump-the-rocm-version-in-therock)
    * [(BKC only) Set the version base date in TheRock](#bkc-only-set-the-version-base-date-in-therock)
    * [Pin workflow files in rockrel](#pin-workflow-files-in-rockrel)
    * [Pin workflow files in component repositories](#pin-workflow-files-in-component-repositories)

## About release branches

We create _release branches_ in git for each directly managed release.

These release branches are used to
1. Preserve the source code for each release so developers and users can reproduce our releases
1. Accept cherry-picks and other fixes while iterating on release candidate builds
1. Configure the CI/CD workflows where behavior should differ from mainline builds

> [!NOTE]
> Regular nightly releases run off the unmodified `main` branches of TheRock and
> this rockrel repository.

### Release branch naming

All release branches are named with a `release/` prefix, for example:

* [TheRock - `release/therock-7.9`](https://github.com/ROCm/TheRock/tree/release/therock-7.9)
* [TheRock - `release/therock-7.10`](https://github.com/ROCm/TheRock/tree/release/therock-7.10)
* [TheRock - `release/therock-10.0`](https://github.com/ROCm/TheRock/tree/release/therock-10.0)
* [TheRock - `release/bkc/therock-10.1-20260811`](https://github.com/ROCm/TheRock/tree/release/bkc/therock-10.1-20260811)
* [TheRock - `release/bkc/therock-10.2-20260922`](https://github.com/ROCm/TheRock/tree/release/bkc/therock-10.2-20260922)
* [TheRock - `release/bkc/therock-10.2-20261013`](https://github.com/ROCm/TheRock/tree/release/bkc/therock-10.2-20261013)

The text after that prefix will depend on the release type and version used. See also
[TheRock - `docs/packaging/versioning.md`](https://github.com/ROCm/TheRock/blob/main/docs/packaging/versioning.md).

### Release branch protections and policies

Release branches are protected by
[GitHub branch protection rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets).
The specific policies differ depending on the release type, but in general we
enforce required code review by release managers and engineers, not just project developers.

### Repositories in scope for release branching

We create release branches with matching name across key repositories where we
expect to receive cherry-picks and other fixes:

Repository | Search query | Example branch | Notes
-- | --: | -- | --
[ROCm/rockrel](https://github.com/ROCm/rockrel)        | [rockrel `release/` branches](https://github.com/ROCm/rockrel/branches/all?query=release%2F) | [`release/therock-10.0`](https://github.com/ROCm/rockrel/tree/release/therock-10.0) | Trigger workflows here
[ROCm/TheRock](https://github.com/ROCm/TheRock)        | [TheRock `release/` branches](https://github.com/ROCm/TheRock/branches/all?query=release%2F) | [`release/therock-10.0`](https://github.com/ROCm/TheRock/tree/release/therock-10.0) | Central repository for code
[ROCm/llvm-project](https://github.com/ROCm/llvm-project)   | [llvm-project `release/` branches](https://github.com/ROCm/llvm-project/branches/all?query=release%2F) | [`release/therock-10.0`](https://github.com/ROCm/llvm-project/tree/release/therock-10.0)
[ROCm/rocm-systems](https://github.com/ROCm/rocm-systems)   | [rocm-systems `release/` branches](https://github.com/ROCm/rocm-systems/branches/all?query=release%2F) | [`release/therock-10.0`](https://github.com/ROCm/rocm-systems/tree/release/therock-10.0) | Branches include CI setup
[ROCm/rocm-libraries](https://github.com/ROCm/rocm-libraries) | [rocm-libraries `release/` branches](https://github.com/ROCm/rocm-libraries/branches/all?query=release%2F) | [`release/therock-10.0`](https://github.com/ROCm/rocm-libraries/tree/release/therock-10.0) | Branches include CI setup
[ROCm/rocgdb](https://github.com/ROCm/rocgdb)         | [rocgdb `release/` branches](https://github.com/ROCm/rocgdb/branches/all?query=release%2F) | [`release/therock-10.0`](https://github.com/ROCm/rocgdb/tree/release/therock-10.0)
[ROCm/libhipcxx](https://github.com/ROCm/libhipcxx)      | [libhipcxx `release/` branches](https://github.com/ROCm/libhipcxx/branches/all?query=release%2F) | [`release/therock-7.12`](https://github.com/ROCm/libhipcxx/tree/release/therock-7.12) | Now only created if needed
(Others)                               | | | Now only created if needed

Additional repositories from
[TheRock's `.gitmodules` file](https://github.com/ROCm/TheRock/blob/main/.gitmodules)
may be included as needed.

## Creating release branches

> [!IMPORTANT]
> We are automating more of these steps as our release process matures.
>
> This documentation should explain both the *intent* and the *mechanics*.

Branch creation overview:

1. Start from a specific commit in TheRock, typically a "last known good" commit.
2. Create named release branches for each repository.
3. Initialize those branches as needed for the release type.

### Selecting a branch point

We typically select a "last known good" commit by selecting the most recent
successful build of the nightly `multi_arch_release.yml` workflow on rockrel's
`main` branch:
https://github.com/ROCm/rockrel/actions/workflows/multi_arch_release.yml?query=branch%3Amain.

### Creating named release branches

To branch from the nightly release with ROCm version `10.1.0a20260811`:
1. Find the workflow run that produced that release: https://github.com/ROCm/rockrel/actions/runs/31444849807
2. Check the commit manifest or logs to find the associated TheRock commit: https://github.com/ROCm/TheRock/commit/03e3f2eba205f9a8f554a24d5e76925b39efab7a
    * For example: https://therock-nightly-artifacts.s3.amazonaws.com/31444849807-linux/manifests/therock_manifest.json

        ```jsonc
        {
          "the_rock_commit": "03e3f2eba205f9a8f554a24d5e76925b39efab7a",
          "github_job": "build_stage",
          "github_run_id": "31444849807",
          "rocm_package_version": "10.1.0a20260811",
          "rocm_version": "10.1.0",
          "submodules": [
            {
              "submodule_name": "llvm-project",
              "submodule_path": "compiler/amd-llvm",
              "submodule_url": "https://github.com/ROCm/llvm-project.git",
              "pin_sha": "0240d7087e183944016616b505eba935efe59157",
              "patches": []
            },
            // ...
          ]
        }
        ```
3. Checkout sources, without applying patches:

    ```bash
    cd TheRock
    git checkout 03e3f2eba205f9a8f554a24d5e76925b39efab7a
    python build_tools/fetch_sources.py --no-apply-patches

    # Note: explicitly include rocgdb on Windows
    python build_tools/fetch_sources.py --no-apply-patches --debug-tools=rocgdb
    ```
4. (Optional) Check that each submodule is set to the commits you expect from the manifest/logs

    ```bash
    # Check all at once:
    git submodule status
    #  207ee58595a64b5c4a70df221f1e6e704b807811 base/half (rocm-6.4.1-1-g207ee58)
    #  10155d7272ea1bf79f6b5a9dbc339657af1aa372 base/rocm-cmake (mock-tag-test-6-g10155d7)
    #  0240d7087e183944016616b505eba935efe59157 compiler/amd-llvm (llvmorg-23-init-567091-g0240d7087e18)
    #  1c0fdf11502f06c4b4e978e35a1135240ea38e6f compiler/hipify (therock-7.14-2-g1c0fdf11)
    #  ddeda6468d45cf1e888a0e81e50bc170756a335b compiler/spirv-llvm-translator (therock-7.14-66-gddeda646)
    #  5a74aaa2a1fcd1437cb5f97d35f5b88e53e97043 debug-tools/rocgdb/source (therock-7.14-46-g5a74aaa2a1f)
    #  34e124071332781a6968d765afa5dbd9f8aac7d4 math-libs/libhipcxx (therock-7.14-7-g34e124071)
    #  67811f1ee5242e82539446e5fdcb87fb87100061 rocm-libraries (therock-7.13-7473-g67811f1ee52)
    #  5bc651a82683b2ae21acf14ffc9af35f5c2722ae rocm-systems (hip-version_10.1.62220-42-g5bc651a8268)
    # -75b4d6b0b4bdd0a8d61468f8e6faf4aa7309342f third-party/sysdeps/linux/amd-mesa/mesa-fork

    # Or check individually:
    git -C compiler/amd-llvm rev-parse HEAD
    # 0240d7087e183944016616b505eba935efe59157
    ```

    Note how compiler/amd-llvm uses `0240d7087e183944016616b505eba935efe59157`.

5. Choose your new branch name, e.g. `release/bkc/therock-10.1-20260811`
6. (Optional) Check that you have sufficient permissions for pushing using
    [`scripts/check_github_permissions.py`](../scripts/check_github_permissions.py):
    ```bash
    python scripts/check_github_permissions.py ${THEROCK_COMMIT_REF}
    ```
7. Create branches in each repository and push them:

    The basic procedure is:

    ```bash
    # From TheRock
    BRANCH_NAME="release/bkc/therock-10.1-20260811"
    git -C . branch "${BRANCH_NAME}" && git -C . push -u origin "${BRANCH_NAME}:${BRANCH_NAME}"
    git -C rocm-libraries branch "${BRANCH_NAME}" && git -C rocm-libraries push -u origin "${BRANCH_NAME}:${BRANCH_NAME}"
    git -C rocm-systems branch "${BRANCH_NAME}" && git -C rocm-systems push -u origin "${BRANCH_NAME}:${BRANCH_NAME}"
    git -C compiler/amd-llvm branch "${BRANCH_NAME}" && git -C compiler/amd-llvm push -u origin "${BRANCH_NAME}:${BRANCH_NAME}"
    git -C debug-tools/rocgdb/source branch "${BRANCH_NAME}" && git -C debug-tools/rocgdb/source push -u origin "${BRANCH_NAME}:${BRANCH_NAME}"
    ```

    We have a script that can do this for you at [`scripts/create_release_branches.sh`](../scripts/create_release_branches.sh):

    ```bash
    # From TheRock
    bash ../rockrel/scripts/create_release_branches.sh release/bkc/therock-10.1-20260811
    ```

    You'll then also need to create the branch in this rockrel repository,
    branched off from `main`.

### Initializing release branches

After branch creation, some setup is required on each release branch to make it
ready for release package building and testing pull requests.

#### (Stable only) Bump the rocm-version in TheRock

For stable releases increase the version on `main` after branching so future
nightly builds are treated as alpha versions of the next release, as on
[TheRock#9440](https://github.com/ROCm/TheRock/pull/9440):

```diff
{
- "rocm-version": "10.2.0",
+ "rocm-version": "10.3.0",
  "release-metadata": {
    "base-date": ""
  }
}
```

#### (BKC only) Set the version base date in TheRock

For BKC releases, set the version base date in `version.json` on the BKC release
branch like in [TheRock#7634](https://github.com/ROCm/TheRock/pull/7634):

```diff
{
  "rocm-version": "10.1.0",
  "release-metadata": {
-   "base-date": ""
+   "base-date": "20260825"
  }
}
```

This is used to produce BKC versions like `10.1.0a20260825+bkc.20260831`
(base date, current date). See
https://github.com/ROCm/TheRock/blob/main/docs/packaging/versioning.md#release-branch-metadata
for more details.

#### Pin workflow files in rockrel

Set workflow files in rockrel to use the release branch from TheRock,
like in [rockrel#112](https://github.com/ROCm/rockrel/pull/112):

```diff
  release:
-   uses: ROCm/TheRock/.github/workflows/multi_arch_release.yml@main
+   uses: ROCm/TheRock/.github/workflows/multi_arch_release.yml@release/bkc/therock-10.1-20260908
```

This causes release workflows triggered from the release branch to use the
release branch from TheRock instead of the latest code (used by default for
nightly releases).


#### Pin workflow files in component repositories

<!-- TODO: replace with stable release branch names not BKC? -->

> [!WARNING]
> Some of these changes are not obvious and may need careful review, as the CI
> workflows have multiple configuration points.

Set workflow files in component repositories to use the release branch from
TheRock like in
[rocm-libraries#11772](https://github.com/ROCm/rocm-libraries/pull/11772) and
[rocm-systems#11301](https://github.com/ROCm/rocm-systems/pull/11301):

```diff
outputs:
  therock-ref:
    description: "TheRock commit hash to use"
-   value: "b45a06045cbb29c85e221748e653ac2521c6e1bf" # 2026-08-26
+   value: "release/bkc/therock-10.1-20260908"
```

```diff
        with:
          repository: "ROCm/TheRock"
          path: "TheRock"
-         ref: 648a501e56a1e0919976d6f540b7853e63d44998 # 2026-09-02
+         ref: release/bkc/therock-10.1-20260908
```

This causes CI workflows triggered for pull requests to the release branches to
use workflow code and component code (e.g. LLVM, rocm-systems) from the release
branch.
