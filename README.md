# rockrel (TheRock Releases)

This repository contains code and actions workflow runs for stable [TheRock](https://github.com/ROCm/TheRock) releases:

| ROCm release type             | Repository where workflows run                                                                                                                             | Process notes                               |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Stable releases               | [rockrel](https://github.com/ROCm/rockrel) (_This repository_)                                                                                             | 🟢 Manual promotion, exhaustive QA          |
| Stable prereleases            | [rockrel](https://github.com/ROCm/rockrel) (_This repository_)                                                                                             | 🔵 Manual branching, automated tests        |
| Nightly releases (multi-arch) | [rockrel](https://github.com/ROCm/rockrel) (_This repository_)                                                                                             | 🔵 Nightly snapshots, all GPU architectures |
| Nightly releases (per-family) | [TheRock](https://github.com/ROCm/TheRock)                                                                                                                 | 🔵 Nightly snapshots, per GPU family        |
| Per-commit builds             | [TheRock](https://github.com/ROCm/TheRock), [rocm-libraries](https://github.com/ROCm/rocm-libraries), [rocm-systems](https://github.com/ROCm/rocm-systems) | 🟠 Development builds, automated tests      |

_The name of this repo has been shortened to workaround this [known Windows path length issue](https://github.com/ROCm/rocm-libraries/issues/2096)._

## Label-driven cherry-pick automation

Start with the [ROCm cherry-pick user guide](docs/cherry-pick-automation/README.md)
for local CLI and agent-skill quick starts. The complete workflow is documented
in the [user manual](docs/cherry-pick-automation/user-manual.md),
[product requirements](docs/cherry-pick-automation/product-requirements.md),
[technical design](docs/cherry-pick-automation/technical-design.md), and
[operator runbook](docs/cherry-pick-automation/runbook.md). The current local
review status and red/green record are in the
[implementation audit](docs/cherry-pick-automation/implementation-report.md)
and [TDD evidence](docs/cherry-pick-automation/tdd-evidence.md). The reviewed
historical results and remaining gaps are in the
[replay analysis](docs/cherry-pick-automation/historical-replay-analysis.md).
The project-local [ROCm cherry-pick skill](skills/rocm-cherry-pick/SKILL.md)
documents the guarded CLI workflow for read-only planning and push-disabled
local materialization using local `gh` credentials only for GitHub reads.
The SLAI Marketplace surface is deliberately limited to `auth`, `plan`, and
`materialize`; it is separate from the disabled GitHub Actions mutation path
and cannot create a branch or draft pull request.
After a clean reviewed-commit bundle and the separately reviewed Developer
Central read-only endpoint are deployed, a fresh engineer can use the
standard-library CLI plus Git and an existing `gh` login to create a verified
local-only cherry-pick checkout in one `materialize` invocation. The source checkout is not mutated, the output checkout has
pushing disabled, and no branch or PR is created remotely. See the
[operator runbook](docs/cherry-pick-automation/runbook.md#local-only-materialization-from-a-fresh-checkout)
for the complete bootstrap and command.

Public GitHub work is held in the local-only
[operator TODO](docs/cherry-pick-automation/REMOTE_ACTIONS_TODO.md).
ROCm Release Hub `config/release-trains.json` is the Git-reviewed train source
projected by Developer Central through `GET /api/v1/cherry-pick/config`. That
complete authenticated response is the only runtime train authority; Jira,
bundled catalogs, string-derived branches, and last-known-good fallbacks are
excluded. This repository's
[`config/cherry-pick-trains.json`](config/cherry-pick-trains.json) is a
fixture-only parser and local-review artifact, never runtime authority.

The checked-in automation is not deployed. Its remote-write jobs are
deliberately impossible to enter during local review, and no committed train is
in `create-draft` mode. The final local matrix passes 1,036 tests with no
deselections on Python 3.10.20, 3.11.15, and 3.12.13. All three measure
5,470/5,703 lines (95.9144%), 1,818/1,982 branches (91.7255%), and all 25
critical modules at or above 90% in both dimensions. The rebuilt review bundle
passes equality and structural validation but is deliberately nonpublishable
`dirty_worktree_review` provenance. A clean reviewed-commit rebuild, immutable
OIDC-subject opt-in verification, and all private-sandbox, hosted-CI,
human-review, provisioning, and deployment gates remain outstanding; this is
not a production-readiness claim.

The offline historical regression runner is
[`scripts/replay_cherry_pick_history.py`](scripts/replay_cherry_pick_history.py).
It can generate a candidate inventory outside the tracked golden, compare the
two, run 17-case fast or 77-case deep repository lanes in parallel, and quickly
roll persistent disk-backed worktrees back to verified clean state without
rebuilding their indexes. See the
[operator runbook](docs/cherry-pick-automation/runbook.md#historical-replay-suite)
for commands and safety boundaries.

## Release FAQ (Frequently Asked Questions)

### Why are some packages included in nightly releases but missing from stable releases?

We maintain a high quality bar for what we promote to "stable". If packages for
a particular library, gfx target/family, or operating system do not meet this
bar then the packages are not called "stable" yet.

### Why are some features or subprojects missing from a particular release?

Releases must continue to be published regularly. Feature and subprojects will
be included in releases only when they are ready, and the release schedule will
not accept delays. Releases should be frequent enough that missing one release
is not too disruptive.

The bar for "ready" is context-dependent but usually involves:

1. A test plan that is sufficiently implemented
1. Some incubation period in nightly releases
1. Associated documentation and release notes

## Installation instructions

### Installing Prereleases

This provides a brief overview on how to install prereleases triggered with the workflows in this repository.
For general and more detailed information on releases, see [`RELEASES.md` in TheRock](https://github.com/ROCm/TheRock/blob/main/RELEASES.md).

#### Installing ROCm Python packages

Multi-arch releases use a single index URL for all GPU architectures. Use
`device-all` for all supported GPUs, or one family with a `[device-*]` extra:

```bash
pip install --index-url https://rocm.prereleases.amd.com/whl-multi-arch/ --pre \
  "rocm[libraries,devel,device-all]"
# For a specific GPU family instead, e.g.:
# "rocm[libraries,devel,device-gfx942]"
```

See the
[multi-arch releases section of RELEASES.md](https://github.com/ROCm/TheRock/blob/main/RELEASES.md#installing-multi-arch-rocm-python-packages)
for the device extras table and full install instructions.

#### Installing from tarballs

Prerelease tarballs can be downloaded from
<https://rocm.prereleases.amd.com/tarball-multi-arch/>.

After downloading, extract the release tarball into place:

```bash
mkdir therock-tarball && cd therock-tarball

# Multiarch (all GPUs):
wget https://rocm.prereleases.amd.com/tarball-multi-arch/therock-dist-linux-multiarch-7.14.0rc1.tar.gz

# Per-family (one GPU family):
# wget https://rocm.prereleases.amd.com/tarball-multi-arch/therock-dist-linux-gfx94X-dcgpu-7.14.0rc1.tar.gz

mkdir install
tar -xf *.tar.gz -C install
```

Pick the tarball that matches your GPU family and ROCm version from the index
(e.g. `gfx1151`, `gfx950-dcgpu`). Use the `multiarch` tarball to install all
supported GPU families at once.

#### Installing from Native Linux Packages

AMD provides prerelease ROCm packages for both Debian-based and RPM-based Linux distributions.

Repository base URL:

```
https://rocm.prereleases.amd.com/packages-multi-arch/
```

______________________________________________________________________

##### Installing Packages on Debian-Based Systems

###### Import the ROCm GPG Key

```bash
sudo mkdir --parents --mode=0755 /etc/apt/keyrings
wget https://rocm.prereleases.amd.com/packages/gpg/rocm.gpg -O - \
| gpg --dearmor | sudo tee /etc/apt/keyrings/amdrocm.gpg > /dev/null
```

______________________________________________________________________

###### Add the ROCm Repository

The example below uses the `ubuntu2404` profile; change it to match your
distribution (e.g. `debian12`, `ubuntu2204`, `ubuntu2604`).

```bash
sudo tee /etc/apt/sources.list.d/rocm.list << EOF
deb [arch=amd64 signed-by=/etc/apt/keyrings/amdrocm.gpg] https://rocm.prereleases.amd.com/packages-multi-arch/ubuntu2404/ stable main
EOF
sudo apt update
```

______________________________________________________________________

###### Install ROCm

```bash
# Installs the full ROCm core SDK.
sudo apt install amdrocm-core
# For a specific ROCm version and GPU arch instead, e.g.:
# sudo apt install amdrocm7.14-gfx942
```

______________________________________________________________________

##### Installing Packages on RPM-Based Systems

###### Add the ROCm Repository

The example below uses the `rhel10` profile; change it to match your
distribution (e.g. `rhel8`, `rhel9`, `sles15`, `sles16`).

```bash
sudo tee /etc/yum.repos.d/rocm.repo << EOF
[rocm]
name=ROCm Prerelease Repository
baseurl=https://rocm.prereleases.amd.com/packages-multi-arch/rhel10/x86_64/
enabled=1
gpgcheck=1
gpgkey=https://rocm.prereleases.amd.com/packages/gpg/rocm.gpg
EOF
sudo dnf clean all
```

______________________________________________________________________

###### Install ROCm

```bash
# Installs the full ROCm core SDK.
sudo dnf install amdrocm-core
# For a specific ROCm version and GPU arch instead, e.g.:
# sudo dnf install amdrocm7.14-gfx942
```
