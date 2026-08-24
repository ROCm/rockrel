# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Render every Mermaid diagram in the cherry-pick design with pinned mmdc."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import unquote

MERMAID_CLI_IMAGE = (
    "ghcr.io/mermaid-js/mermaid-cli/mermaid-cli@"
    "sha256:e7fc5c569c039c7d663e875ac0fe70828910f686b9bbee2d064f7e8a096d49f8"
)
MERMAID_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
ACCESSIBLE_TITLE = re.compile(r"^\s*accTitle:\s*(\S.*?)\s*$", re.MULTILINE)
ACCESSIBLE_DESCRIPTION = re.compile(r"^\s*accDescr:\s*(\S.*?)\s*$", re.MULTILINE)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)")
ROOT = Path(__file__).parents[1]
DEFAULT_DOCUMENT = ROOT / "docs/cherry-pick-automation/technical-design.md"
DEFAULT_OUTPUT = ROOT / ".tmp/mermaid-render"


class MermaidRenderError(RuntimeError):
    """Report a deterministic Mermaid extraction or rendering failure."""


def _metadata_value(
    diagram: str, pattern: re.Pattern[str], name: str, index: int
) -> str:
    """Return one required accessibility value or reject ambiguous metadata."""

    values = pattern.findall(diagram)
    if len(values) != 1:
        qualifier = "exactly one" if values else "one"
        raise MermaidRenderError(
            f"diagram {index} must contain {qualifier} non-empty {name}"
        )
    return values[0].strip()


def extract_diagrams(markdown: str) -> tuple[str, ...]:
    """Extract diagrams only when accessibility identities are complete and unique."""

    diagrams = tuple(
        match.group(1).rstrip() + "\n" for match in MERMAID_BLOCK.finditer(markdown)
    )
    if not diagrams:
        raise MermaidRenderError("document contains no Mermaid diagrams")
    titles: set[str] = set()
    for index, diagram in enumerate(diagrams, start=1):
        title = _metadata_value(diagram, ACCESSIBLE_TITLE, "accTitle", index)
        _metadata_value(diagram, ACCESSIBLE_DESCRIPTION, "accDescr", index)
        normalized = title.casefold()
        if normalized in titles:
            raise MermaidRenderError(f"diagram {index} has a duplicate accTitle")
        titles.add(normalized)
    return diagrams


def validate_local_links(document: Path) -> None:
    """Require every relative Markdown link in one document to name a local file."""

    document = Path(document).resolve()
    markdown = document.read_text(encoding="utf-8")
    for match in MARKDOWN_LINK.finditer(markdown):
        target = match.group(1).strip()
        if target.startswith(("#", "https://", "http://", "mailto:")):
            continue
        path_text = unquote(target.split("#", 1)[0])
        if not path_text:
            continue
        destination = (document.parent / path_text).resolve()
        if not destination.is_file():
            raise MermaidRenderError(f"{document}: broken local link {target}")


def _container_command(workdir: Path, source: Path, output: Path) -> list[str]:
    """Build one least-privilege, digest-pinned mmdc container command."""

    return [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--env=HOME=/tmp",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=256m",
        "--volume",
        f"{workdir}:/work:rw",
        MERMAID_CLI_IMAGE,
        "-i",
        f"/work/{source.name}",
        "-o",
        f"/work/{output.name}",
    ]


def _staging_directory(output_dir: Path) -> Path:
    """Create a sticky write-only staging mount for the image's non-root user."""

    staging = Path(
        tempfile.mkdtemp(prefix=".mermaid-render-", dir=output_dir)
    ).resolve()
    # Rootless Docker cannot map every corporate host UID. A short-lived sticky
    # directory lets the image's fixed non-root user create only staged output.
    staging.chmod(0o1733)
    return staging


def render_document(
    document: Path,
    output_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[Path, ...]:
    """Render all diagrams and return SVG paths, removing partial output on failure."""

    document = Path(document).resolve()
    output_dir = Path(output_dir).resolve()
    validate_local_links(document)
    diagrams = extract_diagrams(document.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = _staging_directory(output_dir)
    try:
        return _render_diagrams(diagrams, output_dir, staging, runner)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _render_diagrams(
    diagrams: Sequence[str],
    output_dir: Path,
    staging: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[Path, ...]:
    """Render extracted diagrams inside staging and publish verified SVG files."""

    outputs: list[Path] = []
    for index, diagram in enumerate(diagrams, start=1):
        source = staging / f"diagram-{index:02d}.mmd"
        generated = staging / f"diagram-{index:02d}.svg"
        output = output_dir / generated.name
        source.write_text(diagram, encoding="utf-8")
        output.unlink(missing_ok=True)
        command = _container_command(staging, source, generated)
        try:
            completed = runner(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            _remove_outputs(outputs)
            raise MermaidRenderError(
                f"diagram {index} could not start mmdc: {exc}"
            ) from exc
        if completed.returncode != 0:
            _remove_outputs(outputs)
            detail = (
                completed.stderr.strip() or completed.stdout.strip() or "mmdc failed"
            )
            raise MermaidRenderError(f"diagram {index} failed: {detail}")
        if not generated.is_file() or generated.is_symlink():
            _remove_outputs(outputs)
            raise MermaidRenderError(f"diagram {index} produced no SVG output")
        generated.replace(output)
        outputs.append(output)
    return tuple(outputs)


def _remove_outputs(outputs: Sequence[Path]) -> None:
    """Remove SVG artifacts produced before a failed rendering attempt."""

    for output in outputs:
        output.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for deterministic local rendering."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render the configured design document and return a shell exit status."""

    args = build_parser().parse_args(argv)
    try:
        outputs = render_document(args.document, args.output_dir)
    except (MermaidRenderError, OSError) as exc:
        print(f"error: {exc}")
        return 1
    print(f"rendered {len(outputs)} Mermaid diagrams into {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
