# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Build the deterministic, local-only ROCm cherry-pick SLAI skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_VERSION = "1.0.0"
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
STATIC_FILES = (
    "SKILL.md",
    "DESCRIPTION.md",
    "LICENSE",
    "agents/openai.yaml",
    "references/operator-guide.md",
    "references/threat-model.md",
    "scripts/rocm_cherry_pick.py",
)
VALIDATION_OUTPUT_FILES = (
    "COMPLIANCE_SCAN_WAIVERS.yaml",
    "references/COMPLIANCE_FINDINGS.json",
    "references/COMPLIANCE_SCAN.md",
)
RUNTIME_FILES = (
    "__init__.py",
    "authorization.py",
    "compat.py",
    "config.py",
    "core.py",
    "core_cli.py",
    "dependencies.py",
    "git.py",
    "git_auth.py",
    "github_read.py",
    "managed_stack.py",
    "marketplace_cli.py",
    "models.py",
    "orchestrator.py",
    "refs.py",
    "release_hub.py",
    "release_hub_auth.py",
)


def _strip_compliance_scan_metadata(content: str) -> str:
    """Remove scanner-owned metadata before building a changed bundle."""

    cleaned = []
    skipping = False
    found = False
    for line in content.splitlines(keepends=True):
        unindented = line.lstrip()
        space_indent = len(line) - len(line.lstrip(" "))
        if skipping:
            if not unindented.strip() or space_indent > 2:
                continue
            skipping = False
        if unindented.rstrip("\r\n") == "compliance_scan:":
            if space_indent != 2 or found:
                raise ValueError("compliance_scan metadata is malformed")
            found = True
            skipping = True
            continue
        cleaned.append(line)
    return "".join(cleaned)


def _remove_validation_outputs(output: Path) -> None:
    """Remove stale scanner-owned outputs before rebuilding the bundle."""

    paths = tuple(output / relative for relative in VALIDATION_OUTPUT_FILES)
    for path in paths:
        if path.exists() and not (path.is_symlink() or path.is_file()):
            raise ValueError(f"validation output path is not a file: {path}")
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()


def build_skill(
    root: Path, output: Path, *, allow_dirty_review: bool = False
) -> dict[str, str]:
    """Build the allowlisted bundle with content-addressed Git provenance."""

    root = Path(root).resolve()
    output = Path(output).resolve()
    source_provenance = _resolve_source_provenance(
        root, allow_dirty_review=allow_dirty_review
    )
    source_provenance["source_content_sha256"] = _source_content_sha256(
        _packaged_source_hashes(root)
    )
    source = root / "skills/rocm-cherry-pick"
    same_output = source == output
    source_skill = source / "SKILL.md"
    skill_content = _strip_compliance_scan_metadata(
        source_skill.read_text(encoding="utf-8")
    )
    if not same_output:
        if output.exists():
            raise ValueError("output directory already exists")
        output.mkdir(parents=True)
        for relative in STATIC_FILES:
            if relative != "SKILL.md":
                _copy(source / relative, output / relative)
    else:
        for relative in STATIC_FILES:
            if not (source / relative).is_file():
                raise ValueError(f"skill collateral is missing: {relative}")
    _remove_validation_outputs(output)
    (output / "SKILL.md").write_text(skill_content, encoding="utf-8")

    legacy_catalog = output / "assets/cherry-pick-trains.json"
    if legacy_catalog.is_file() or legacy_catalog.is_symlink():
        legacy_catalog.unlink()
    elif legacy_catalog.exists():
        raise ValueError(f"legacy catalog path is not a file: {legacy_catalog}")
    try:
        legacy_catalog.parent.rmdir()
    except OSError:
        pass

    runtime = output / "scripts/cherry_pick"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    for name in RUNTIME_FILES:
        _copy(root / "scripts/cherry_pick" / name, runtime / name)
    (output / "scripts/rocm_cherry_pick.py").chmod(0o755)

    file_hashes = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "bundle-manifest.json"
    }
    manifest = {
        "schema_version": "rocm-cherry-pick-bundle.v2",
        "skill_version": SKILL_VERSION,
        "source_provenance": source_provenance,
        "contracts": {
            "core_request": "3",
            "train_catalog": "5",
            "release_hub_config": "cherry-pick-config.v1",
            "release_train_source": "release-trains.v5",
            "credential_store": "rrh-auth.v1",
        },
        "runtime_allowlist": [f"scripts/cherry_pick/{name}" for name in RUNTIME_FILES],
        "files": file_hashes,
    }
    (output / "bundle-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return source_provenance


def _copy(source: Path, destination: Path) -> None:
    """Copy one required bundle file without rewriting its bytes."""

    if not source.is_file():
        raise ValueError(f"required source file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)


def _current_revision(root: Path) -> str:
    """Resolve and validate the current full Git revision."""

    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    revision = completed.stdout.strip()
    if completed.returncode != 0 or REVISION_RE.fullmatch(revision) is None:
        raise ValueError("rockrel revision could not be resolved")
    return revision


def _packaged_source_revision(root: Path) -> str:
    """Return the newest commit that changed any packaged source input."""

    completed = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", *_packaged_source_paths()],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    revision = completed.stdout.strip()
    if completed.returncode != 0 or REVISION_RE.fullmatch(revision) is None:
        raise ValueError("packaged source revision could not be resolved")
    return revision


def main() -> int:
    """Build a provenance-bound skill bundle and print its output path."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-dirty-review",
        action="store_true",
        help="build a review-only bundle when packaged source inputs are dirty",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or root / "skills/rocm-cherry-pick"
    provenance = build_skill(root, output, allow_dirty_review=args.allow_dirty_review)
    if provenance["state"] == "dirty_worktree_review":
        print(
            "warning: built a dirty_worktree_review bundle; do not publish it",
            file=sys.stderr,
        )
    print(output)
    return 0


def _packaged_source_paths() -> tuple[str, ...]:
    """Return every repository-relative input whose bytes enter the bundle."""

    static = tuple(f"skills/rocm-cherry-pick/{name}" for name in STATIC_FILES)
    runtime = tuple(f"scripts/cherry_pick/{name}" for name in RUNTIME_FILES)
    return static + runtime


def _packaged_source_hashes(root: Path) -> dict[str, str]:
    """Hash the exact repository source closure before bundle transformations."""

    hashes = {}
    for relative in _packaged_source_paths():
        path = root / relative
        if not path.is_file():
            raise ValueError(f"required source file is missing: {path}")
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _packaged_source_changes(root: Path) -> tuple[str, ...]:
    """Return Git status records limited to the complete packaged source closure."""

    completed = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *_packaged_source_paths(),
        ],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise ValueError("packaged source Git status could not be resolved")
    return tuple(line for line in completed.stdout.splitlines() if line)


def _resolve_source_provenance(
    root: Path, *, allow_dirty_review: bool
) -> dict[str, str]:
    """Resolve an honest base revision and fail closed on unapproved dirty bytes."""

    head_revision = _current_revision(root)
    changes = _packaged_source_changes(root)
    if changes and not allow_dirty_review:
        raise ValueError(
            "dirty packaged source closure; rerun with --allow-dirty-review "
            "only for a local review bundle"
        )
    return {
        "base_revision": (
            head_revision if changes else _packaged_source_revision(root)
        ),
        "state": "dirty_worktree_review" if changes else "clean_commit",
    }


def _source_content_sha256(file_hashes: dict[str, str]) -> str:
    """Hash the canonical path-to-content-hash map for all bundled source bytes."""

    canonical = json.dumps(file_hashes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess smoke
    raise SystemExit(main())
