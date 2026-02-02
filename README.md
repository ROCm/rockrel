# rockrel (TheRock Releases)

This repository contains code and actions workflow runs for stable [TheRock](https://github.com/ROCm/TheRock) releases:

ROCm release type | Repository where workflows run | Process notes
-- | -- | --
Stable releases  | [rockrel](https://github.com/ROCm/rockrel) (_This repository_) | 🟢 Manual promotion, exhaustive QA
Stable prereleases | [rockrel](https://github.com/ROCm/rockrel) (_This repository_) | 🔵 Manual branching, automated tests
Nightly releases | [TheRock](https://github.com/ROCm/TheRock) | 🔵 Nightly snapshots, automated tests
Per-commit builds | [TheRock](https://github.com/ROCm/TheRock), [rocm-libraries](https://github.com/ROCm/rocm-libraries), [rocm-systems](https://github.com/ROCm/rocm-systems) | 🟠 Development builds, automated tests

_The name of this repo has been shortened to workaround this [known Windows path length issue](https://github.com/ROCm/rocm-libraries/issues/2096)._

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
2. Some incubation period in nightly releases
3. Associated documentation and release notes

## Installation instructions

### Installing Prereleases

This provides a brief overview on how to install prereleases triggered with the workflows in this repository.
For general and more detailed information on releases, see [`RELEASES.md` in TheRock](https://github.com/ROCm/TheRock/blob/main/RELEASES.md).

#### Installing ROCm Python packages

To install ROCm and PyTorch Python packages, use `pip` with the `--index-url` option pointing to prereleases index page for your GPU architecture.
The packages are published to GPU-architecture-specific index pages.

| Product Name        | GFX Target | GFX Family   | Release Index                                      |
| --------------------| ---------- | ------------ | -------------------------------------------------- |
| MI300A/MI300X       | gfx942     | gfx94X-dcgpu | https://rocm.prereleases.amd.com/whl/gfx94X-dcgpu/ |
| MI350X/MI355X       | gfx950     | gfx950-dcgpu | https://rocm.prereleases.amd.com/whl/gfx950-dcgpu/ |
| AMD Strix Halo iGPU | gfx1151    | gfx1151      | https://rocm.prereleases.amd.com/whl/gfx1151/      |

Install instructions:
```bash
python -m pip install --index-url ${Release_Index} "rocm[libraries,devel]"
```

For more detailed instructions see TheRock's instructions on [installing releases using pip
](https://github.com/ROCm/TheRock/blob/main/RELEASES.md#installing-releases-using-pip).

#### Installing from tarballs

Prerelease tarballs can be downloaded from https://rocm.prereleases.amd.com/tarball/.

After downloading, simply extract the release tarball into place:

```bash
mkdir therock-tarball && cd therock-tarball
# For example...
wget https://rocm.prereleases.amd.com/tarball/therock-dist-linux-gfx1151-7.9.0rc1.tar.gz

mkdir install
tar -xf *.tar.gz -C install
```
#### Installing from Native Linux Packages

AMD provides prerelease ROCm packages for both Debian-based and RPM-based Linux distributions.

Repository base URL:

```
https://rocm.prereleases.amd.com/packages/
```

---

##### Installing Packages on Debian-Based Systems

###### Import the ROCm GPG Key

```bash
curl -fsSL https://rocm.prereleases.amd.com/packages/gpg/rocm.gpg \
| sudo gpg --dearmor -o /usr/share/keyrings/rocm-archive-keyring.gpg
```

---

###### Add the ROCm Repository

Replace `<os_profile>` with the appropriate distribution profile  
(e.g. `debian12`, `ubuntu22.04`).

```bash
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/rocm-archive-keyring.gpg] \
https://rocm.prereleases.amd.com/packages/<os_profile>/ main" \
| sudo tee /etc/apt/sources.list.d/rocm.list
```

---

###### Install ROCm

```bash
sudo apt update
sudo apt install amdrocm-gfx94x # Change the gfx arch based on your machine.
```

---

##### Installing Packages on RPM-Based Systems

###### Import the ROCm GPG Key

```bash
sudo rpm --import https://rocm.prereleases.amd.com/packages/gpg/rocm.gpg
```

---

###### Add the ROCm Repository

```bash
sudo tee /etc/yum.repos.d/rocm.repo <<'EOF'
[rocm]
name=ROCm Prerelease Repository
baseurl=https://rocm.prereleases.amd.com/packages/rpm/
enabled=1
gpgcheck=1
gpgkey=https://rocm.prereleases.amd.com/packages/gpg/rocm.gpg
EOF
```

---

###### Install ROCm

```bash
sudo dnf install amdrocm-gfx94x # Change the gfx arch based on your machine.
```

