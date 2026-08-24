# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import ast
import hashlib
import json
import re
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts.build_cherry_pick_skill import (
    RUNTIME_FILES,
    STATIC_FILES,
    _copy,
    _current_revision,
    _packaged_source_changes,
    _packaged_source_revision,
    build_skill,
    main,
)

ROOT = Path(__file__).parents[2]


def files(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_skill_bundle_is_deterministic_self_contained_and_read_only(tmp_path):
    first = tmp_path / "first" / "rocm-cherry-pick"
    second = tmp_path / "second" / "rocm-cherry-pick"
    build_skill(ROOT, first, allow_dirty_review=True)
    build_skill(ROOT, second, allow_dirty_review=True)

    assert files(first) == files(second)
    for required in (
        "SKILL.md",
        "DESCRIPTION.md",
        "LICENSE",
        "agents/openai.yaml",
        "references/operator-guide.md",
        "scripts/rocm_cherry_pick.py",
        "scripts/cherry_pick/marketplace_cli.py",
        "scripts/cherry_pick/release_hub.py",
        "scripts/cherry_pick/release_hub_auth.py",
        "bundle-manifest.json",
    ):
        assert (first / required).is_file(), required

    assert not (first / "assets/cherry-pick-trains.json").exists()

    for forbidden in (
        "scripts/cherry_pick/action_runtime.py",
        "scripts/cherry_pick/feedback.py",
        "scripts/cherry_pick/writer.py",
        "scripts/cherry_pick/__main__.py",
    ):
        assert not (first / forbidden).exists(), forbidden

    manifest = json.loads((first / "bundle-manifest.json").read_text())
    assert manifest["schema_version"] == "rocm-cherry-pick-bundle.v2"
    assert (
        manifest["source_provenance"]["base_revision"]
        == _packaged_source_revision(ROOT)
    )
    expected_state = (
        "dirty_worktree_review" if _packaged_source_changes(ROOT) else "clean_commit"
    )
    assert manifest["source_provenance"]["state"] == expected_state
    assert re.fullmatch(
        r"[0-9a-f]{64}", manifest["source_provenance"]["source_content_sha256"]
    )
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((first / relative).read_bytes()).hexdigest() == expected
    assert "bundle-manifest.json" not in manifest["files"]

    completed = subprocess.run(
        ["python3", str(first / "scripts/rocm_cherry_pick.py"), "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0
    assert "materialize" in completed.stdout
    assert "create-draft" not in completed.stdout
    assert not list(first.rglob("__pycache__"))


def test_skill_bundle_python_closure_has_no_remote_write_surface(tmp_path):
    """Prove every packaged Python module is closed and structurally read-only."""

    bundle = tmp_path / "rocm-cherry-pick"
    build_skill(ROOT, bundle, allow_dirty_review=True)
    package = bundle / "scripts/cherry_pick"
    modules = {path.stem for path in package.glob("*.py")}
    forbidden_modules = {
        "action_runtime",
        "clients",
        "feedback",
        "local_runtime",
        "write_authority",
        "writer",
    }
    forbidden_functions = {
        "create_pull",
        "ensure_label",
        "request",
        "upsert_check_run",
        "upsert_comment",
    }
    write_verbs = {"DELETE", "PATCH", "POST", "PUT"}

    assert modules.isdisjoint(forbidden_modules)
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                imported = (
                    {node.module.split(".", 1)[0]}
                    if node.module
                    else {alias.name.split(".", 1)[0] for alias in node.names}
                )
                assert imported <= modules, (
                    f"{path.name} imports an unpackaged local module: "
                    f"{sorted(imported - modules)}"
                )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert (
                    node.name not in forbidden_functions
                ), f"{path.name} exposes forbidden write surface {node.name}"
            if not isinstance(node, ast.Call):
                continue
            literals = [
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            ]
            literals.extend(
                keyword.value.value
                for keyword in node.keywords
                if keyword.arg == "method"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            )
            assert write_verbs.isdisjoint(literals), (
                f"{path.name} contains a remote write verb: "
                f"{sorted(write_verbs.intersection(literals))}"
            )


def test_marketplace_collateral_documents_first_login_and_no_rockrel_checkout():
    skill = (ROOT / "skills/rocm-cherry-pick/SKILL.md").read_text()
    description = (ROOT / "skills/rocm-cherry-pick/DESCRIPTION.md").read_text()
    operator = (
        ROOT / "skills/rocm-cherry-pick/references/operator-guide.md"
    ).read_text()
    for text in (skill, description):
        assert "https://developer-central.amd.com/settings/api-tokens" in text
        assert "read:evidence" in text
        assert "ROCm Cherry-Pick CLI" in text
    assert "license:" in skill
    assert "author: jusharri" in skill
    assert re.search(r"^\s+version:\s+[\"']?1\.0\.0[\"']?\s*$", skill, re.MULTILINE)
    assert "universal: true" in skill
    assert "without a rockrel checkout" in skill.lower()
    assert "no remote" in skill.lower()
    assert "`covered_by_existing_pr`" in skill
    assert "`covered_by_existing_pr`" in operator
    assert "covering destination pull request" not in operator


def test_skill_frontmatter_uses_only_supported_slai_keys():
    content = (ROOT / "skills/rocm-cherry-pick/SKILL.md").read_text()
    frontmatter = content.split("---", 2)[1]
    top_level = {
        match.group(1)
        for line in frontmatter.splitlines()
        if (match := re.fullmatch(r"([a-z][a-z-]*):(?: .*|)", line))
    }

    assert top_level <= {"allowed-tools", "description", "license", "metadata", "name"}
    assert re.search(r"^  universal: true$", frontmatter, re.MULTILINE)


def test_checked_in_bundle_matches_the_reproducible_builder(tmp_path):
    expected = tmp_path / "rocm-cherry-pick"
    build_skill(ROOT, expected, allow_dirty_review=True)
    assert files(expected) == files(ROOT / "skills/rocm-cherry-pick")


def test_builder_rejects_existing_output_and_missing_source(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        build_skill(ROOT, existing, allow_dirty_review=True)
    with pytest.raises(ValueError, match="missing"):
        _copy(tmp_path / "missing", tmp_path / "destination")


def test_builder_can_refresh_its_exact_skill_directory_and_remove_stale_runtime(
    tmp_path,
):
    root = fake_root(tmp_path)
    skill = root / "skills/rocm-cherry-pick"
    stale = skill / "scripts/cherry_pick/stale.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale")
    legacy_catalog = skill / "assets/cherry-pick-trains.json"
    legacy_catalog.parent.mkdir(parents=True)
    legacy_catalog.write_text('{"schema_version": "release-trains.v4"}\n')

    build_skill(root, skill)

    assert not stale.exists()
    assert not legacy_catalog.exists()
    assert (skill / "scripts/cherry_pick/marketplace_cli.py").is_file()
    _copy(skill / "SKILL.md", skill / "SKILL.md")


def test_builder_reports_missing_static_collateral_in_exact_output(tmp_path):
    root = fake_root(tmp_path)
    missing = root / "skills/rocm-cherry-pick/DESCRIPTION.md"
    missing.unlink()
    with pytest.raises(ValueError, match="required source file is missing"):
        build_skill(root, root / "skills/rocm-cherry-pick", allow_dirty_review=True)


def _write_stale_compliance_result(skill):
    (skill / "SKILL.md").write_text(
        """---
name: rocm-cherry-pick
description: Test bundle
metadata:
  author: test
  compliance_scan:
    status: PASSED
    scan_date: 2026-08-20T18:11:44Z
---
# Test bundle
"""
    )
    (skill / "references/COMPLIANCE_SCAN.md").write_text("stale report")
    (skill / "references/COMPLIANCE_FINDINGS.json").write_text("stale findings")
    (skill / "COMPLIANCE_SCAN_WAIVERS.yaml").write_text("stale waivers")


def _assert_compliance_result_was_stripped(skill):
    assert "compliance_scan:" not in (skill / "SKILL.md").read_text()
    for relative in (
        "references/COMPLIANCE_SCAN.md",
        "references/COMPLIANCE_FINDINGS.json",
        "COMPLIANCE_SCAN_WAIVERS.yaml",
    ):
        assert not (skill / relative).exists(), relative
    manifest = json.loads((skill / "bundle-manifest.json").read_text())
    assert not any("COMPLIANCE_" in path for path in manifest["files"])


def test_builder_strips_stale_validator_result_from_new_bundle(tmp_path):
    root = fake_root(tmp_path)
    _write_stale_compliance_result(root / "skills/rocm-cherry-pick")

    output = tmp_path / "unvalidated-bundle"
    build_skill(root, output, allow_dirty_review=True)

    _assert_compliance_result_was_stripped(output)


def test_builder_strips_stale_validator_result_when_refreshing_in_place(tmp_path):
    root = fake_root(tmp_path)
    skill = root / "skills/rocm-cherry-pick"
    _write_stale_compliance_result(skill)

    build_skill(root, skill, allow_dirty_review=True)

    _assert_compliance_result_was_stripped(skill)


def test_builder_rejects_non_file_validator_output_path_without_partial_write(
    tmp_path,
):
    root = fake_root(tmp_path)
    skill = root / "skills/rocm-cherry-pick"
    _write_stale_compliance_result(skill)
    invalid = skill / "references/COMPLIANCE_SCAN.md"
    invalid.unlink()
    invalid.mkdir()
    original_skill = (skill / "SKILL.md").read_text()

    with pytest.raises(ValueError, match="validation output path is not a file"):
        build_skill(root, skill, allow_dirty_review=True)

    assert (skill / "SKILL.md").read_text() == original_skill
    assert invalid.is_dir()
    assert (skill / "references/COMPLIANCE_FINDINGS.json").is_file()


def test_revision_resolution_rejects_invalid_git_output(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="f" * 40 + "\n"),
    )
    assert _current_revision(ROOT) == "f" * 40
    for result in (
        SimpleNamespace(returncode=1, stdout=""),
        SimpleNamespace(returncode=0, stdout="short\n"),
    ):
        monkeypatch.setattr(
            subprocess, "run", lambda *_args, value=result, **_kwargs: value
        )
        with pytest.raises(ValueError, match="revision"):
            _current_revision(ROOT)


def test_clean_provenance_ignores_manifest_only_commit(tmp_path):
    """Bind clean provenance to source bytes, not a later manifest-only commit."""

    root = fake_root(tmp_path)
    source_revision = _packaged_source_revision(root)
    manifest = root / "skills/rocm-cherry-pick/bundle-manifest.json"
    manifest.write_text("{}\n")
    subprocess.run(["git", "add", str(manifest)], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "record bundle manifest"], cwd=root, check=True
    )
    assert _current_revision(root) != source_revision

    output = tmp_path / "rebuilt"
    build_skill(root, output)
    result = json.loads((output / "bundle-manifest.json").read_text())
    assert result["source_provenance"]["base_revision"] == source_revision
    assert result["source_provenance"]["state"] == "clean_commit"


def test_builder_cli_emits_clean_commit_provenance(tmp_path, monkeypatch, capsys):
    root = fake_root(tmp_path)
    output = tmp_path / "cli-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_cherry_pick_skill.py",
            "--root",
            str(root),
            "--output",
            str(output),
        ],
    )
    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(output)
    assert captured.err == ""
    assert output.is_dir()
    manifest = json.loads((output / "bundle-manifest.json").read_text())
    assert manifest["source_provenance"]["state"] == "clean_commit"


def test_builder_cli_requires_explicit_opt_in_for_dirty_review(
    tmp_path, monkeypatch, capsys
):
    root = fake_root(tmp_path)
    (root / "scripts/cherry_pick/core.py").write_text("# local review\n")
    output = tmp_path / "dirty-cli-output"
    argv = [
        "build_cherry_pick_skill.py",
        "--root",
        str(root),
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(ValueError, match="--allow-dirty-review"):
        main()
    assert not output.exists()

    monkeypatch.setattr(sys, "argv", [*argv, "--allow-dirty-review"])
    assert main() == 0
    captured = capsys.readouterr()
    assert "do not publish" in captured.err
    manifest = json.loads((output / "bundle-manifest.json").read_text())
    assert manifest["source_provenance"]["state"] == "dirty_worktree_review"


def fake_root(tmp_path):
    root = tmp_path / "fake-root"
    skill = root / "skills/rocm-cherry-pick"
    for relative in STATIC_FILES:
        path = skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("static\n")
    for name in RUNTIME_FILES:
        path = root / "scripts/cherry_pick" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# runtime\n")
    config = root / "config/cherry-pick-trains.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n")
    _commit_packaged_sources(root)
    return root


def _commit_packaged_sources(root):
    """Create one local base commit containing the complete packaged closure."""

    if (root / ".git").is_dir():
        return _current_revision(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "bundle-test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Bundle Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "packaged sources"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def test_clean_commit_provenance_rejects_dirty_packaged_source_bytes(tmp_path):
    root = fake_root(tmp_path)
    (root / "scripts/cherry_pick/core.py").write_text("# locally reviewed runtime\n")

    with pytest.raises(ValueError, match="dirty packaged source"):
        build_skill(root, tmp_path / "must-not-exist")

    assert not (tmp_path / "must-not-exist").exists()


def test_dirty_review_provenance_content_digest_changes_with_source_bytes(tmp_path):
    root = fake_root(tmp_path)
    first = tmp_path / "first-review"
    second = tmp_path / "second-review"

    (root / "scripts/cherry_pick/core.py").write_text("# first review bytes\n")
    build_skill(root, first, allow_dirty_review=True)
    (root / "scripts/cherry_pick/core.py").write_text("# changed review bytes\n")
    build_skill(root, second, allow_dirty_review=True)

    first_manifest = json.loads((first / "bundle-manifest.json").read_text())
    second_manifest = json.loads((second / "bundle-manifest.json").read_text())
    assert first_manifest["source_provenance"]["state"] == "dirty_worktree_review"
    assert "rockrel_revision" not in first_manifest
    assert (
        first_manifest["source_provenance"]["source_content_sha256"]
        != second_manifest["source_provenance"]["source_content_sha256"]
    )


def test_source_digest_includes_compliance_metadata_before_bundle_stripping(tmp_path):
    root = fake_root(tmp_path)
    skill = root / "skills/rocm-cherry-pick/SKILL.md"
    baseline = """---
name: rocm-cherry-pick
metadata:
  author: test
---
# Test
"""
    with_scan = """---
name: rocm-cherry-pick
metadata:
  compliance_scan:
    status: PASSED
  author: test
---
# Test
"""
    first = tmp_path / "without-scan"
    second = tmp_path / "with-scan"

    skill.write_text(baseline)
    build_skill(root, first, allow_dirty_review=True)
    skill.write_text(with_scan)
    build_skill(root, second, allow_dirty_review=True)

    first_manifest = json.loads((first / "bundle-manifest.json").read_text())
    second_manifest = json.loads((second / "bundle-manifest.json").read_text())
    assert (first / "SKILL.md").read_bytes() == (second / "SKILL.md").read_bytes()
    assert (
        first_manifest["source_provenance"]["source_content_sha256"]
        != second_manifest["source_provenance"]["source_content_sha256"]
    )
