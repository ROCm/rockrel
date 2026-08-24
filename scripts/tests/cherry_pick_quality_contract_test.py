# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Enforce maintainability and packaging boundaries for cherry-pick code."""

from __future__ import annotations

import ast
from collections import defaultdict
import re
from pathlib import Path

from scripts.build_cherry_pick_skill import RUNTIME_FILES

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "scripts/cherry_pick"
WORKFLOW = ROOT / ".github/workflows/unit_tests.yml"
CALLABLES = (ast.FunctionDef, ast.AsyncFunctionDef)
NAMED_LIMITS = {
    ("scripts/cherry_pick/__main__.py", "main"): (80, 15),
    ("scripts/cherry_pick/orchestrator.py", "Planner.plan"): (80, 15),
    ("scripts/cherry_pick/writer.py", "DraftWriter.create"): (80, 15),
}


def _production_paths():
    """Return feature modules and their build or qualification entry points."""

    paths = set(PACKAGE.glob("*.py"))
    for pattern in (
        "build_cherry_pick_skill.py",
        "check_cherry_pick_*.py",
        "render_cherry_pick_*.py",
        "replay_cherry_pick_history.py",
        "run_cherry_pick_private_sandbox.py",
    ):
        paths.update((ROOT / "scripts").glob(pattern))
    return sorted(paths)


def _parents(tree):
    """Index every parsed node by its direct parent."""

    result = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _qualified_name(node, parents):
    """Build a stable dotted name for a class, method, or nested function."""

    names = [node.name]
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (*CALLABLES, ast.ClassDef)):
            names.append(parent.name)
        parent = parents.get(parent)
    return ".".join(reversed(names))


def _implementation_nodes(node):
    """Walk one callable while excluding nested callable and class bodies."""

    for child in ast.iter_child_nodes(node):
        if isinstance(child, (*CALLABLES, ast.Lambda, ast.ClassDef)):
            continue
        yield child
        yield from _implementation_nodes(child)


def _logical_lines(node, lines):
    """Count nonblank, noncomment implementation lines outside the docstring."""

    excluded = set()
    body = getattr(node, "body", ())
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        excluded.update(range(body[0].lineno, body[0].end_lineno + 1))
    return sum(
        1
        for number in range(node.lineno, node.end_lineno + 1)
        if number not in excluded
        and lines[number - 1].strip()
        and not lines[number - 1].lstrip().startswith("#")
    )


def _decisions(node):
    """Count ordinary branches, boolean branches, and comprehension branches."""

    result = 0
    for child in _implementation_nodes(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.IfExp,
                ast.Assert,
                ast.ExceptHandler,
            ),
        ):
            result += 1
        elif isinstance(child, ast.BoolOp):
            result += max(0, len(child.values) - 1)
        elif isinstance(child, ast.comprehension):
            result += 1 + len(child.ifs)
        elif isinstance(child, ast.Match):
            result += len(child.cases)
    return result


def _meaningful(docstring):
    """Reject empty, fragmentary, or mechanically generated API documentation."""

    if not docstring or len(re.findall(r"[A-Za-z]+", docstring)) < 5:
        return False
    normalized = " ".join(docstring.casefold().split())
    boilerplate = (
        r"\bfor the [a-z0-9 _-]+ workflow\b",
        r"\bperform the [a-z0-9 _-]+ operation\b",
        r"\bworkflow workflow\b",
    )
    return not any(re.search(pattern, normalized) for pattern in boilerplate)


def _local_imports(path):
    """Return package-local module filenames imported by one runtime module."""

    imports = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                imports.add(node.module.split(".", 1)[0] + ".py")
            else:
                imports.update(
                    alias.name.split(".", 1)[0] + ".py" for alias in node.names
                )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            prefix = "scripts.cherry_pick."
            if node.module.startswith(prefix):
                imports.add(node.module[len(prefix) :].split(".", 1)[0] + ".py")
    return {name for name in imports if (PACKAGE / name).is_file()}


def _marketplace_closure():
    """Resolve the complete local import closure rooted at marketplace_cli."""

    closure = {"__init__.py", "marketplace_cli.py"}
    pending = ["marketplace_cli.py"]
    while pending:
        for dependency in sorted(_local_imports(PACKAGE / pending.pop())):
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def _is_parameterized_test(node):
    """Return whether a test function is an intentional parameterized wrapper."""

    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call is not None else decorator
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "parametrize"
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark"
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "pytest"
        ):
            return True
    return False


def _duplicate_test_bodies():
    """Group exact top-level test bodies and retain source/decorator evidence."""

    matches = defaultdict(list)
    for path in sorted((ROOT / "scripts/tests").glob("cherry_pick*_test.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, CALLABLES) or not node.name.startswith("test_"):
                continue
            body = ast.dump(
                ast.Module(body=node.body, type_ignores=[]),
                include_attributes=False,
            )
            matches[body].append(
                (
                    str(path.relative_to(ROOT)),
                    node.lineno,
                    node.name,
                    _is_parameterized_test(node),
                    tuple(
                        ast.dump(item, include_attributes=False)
                        for item in node.decorator_list
                    ),
                )
            )
    return tuple(items for items in matches.values() if len(items) > 1)


def test_production_callables_stay_within_reviewable_structural_limits():
    """Keep orchestration entry points small and all other callables bounded."""

    violations = []
    for path in _production_paths():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        parents = _parents(tree)
        relative = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, CALLABLES):
                continue
            name = _qualified_name(node, parents)
            line_limit, decision_limit = NAMED_LIMITS.get((relative, name), (150, 25))
            lines = _logical_lines(node, source.splitlines())
            decisions = _decisions(node)
            if lines > line_limit or decisions > decision_limit:
                violations.append(
                    f"{relative}:{node.lineno} {name}: "
                    f"{lines}/{line_limit} lines, {decisions}/{decision_limit} decisions"
                )
    assert not violations, "oversized production callables:\n" + "\n".join(violations)


def test_every_production_module_class_function_and_method_has_docstring():
    """Require meaningful docstrings for public and private production APIs."""

    missing = []
    for path in _production_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _parents(tree)
        relative = str(path.relative_to(ROOT))
        if not _meaningful(ast.get_docstring(tree, clean=True)):
            missing.append(f"{relative}:1 <module>")
        for node in ast.walk(tree):
            if isinstance(node, (*CALLABLES, ast.ClassDef)) and not _meaningful(
                ast.get_docstring(node, clean=True)
            ):
                missing.append(
                    f"{relative}:{node.lineno} {_qualified_name(node, parents)}"
                )
    assert not missing, "missing meaningful production docstrings:\n" + "\n".join(
        missing
    )


def test_marketplace_runtime_is_exact_ast_import_closure():
    """Package exactly what the local Marketplace CLI can transitively import."""

    packaged = set(RUNTIME_FILES)
    expected = _marketplace_closure()
    forbidden = {"control_plane.py", "coverage.py", "simulation.py"}
    assert packaged.isdisjoint(forbidden), (
        "Marketplace bundle contains service or qualification modules: "
        f"{sorted(packaged & forbidden)}"
    )
    assert packaged == expected, (
        f"unexpected runtime modules: {sorted(packaged - expected)}; "
        f"missing runtime modules: {sorted(expected - packaged)}"
    )


def test_unit_workflow_uses_exact_supported_python_matrix_and_sha_pins():
    """Run tests on Python 3.10 through 3.12 without mutable action tags."""

    text = WORKFLOW.read_text(encoding="utf-8")
    matrix = re.findall(r"^\s+python-version:\s*\[([^]]+)\]\s*$", text, re.MULTILINE)
    assert len(matrix) == 1, "unit workflow must declare one python-version matrix"
    versions = {value.strip().strip("\"'") for value in matrix[0].split(",")}
    assert versions == {"3.10", "3.11", "3.12"}
    assert 'python-version: "${{ matrix.python-version }}"' in text
    actions = re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.MULTILINE)
    mutable = [
        action
        for action in actions
        if not action.startswith("./")
        and re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) is None
    ]
    assert (
        actions and not mutable
    ), f"workflow actions must use full SHA pins: {mutable}"


def test_cherry_pick_tests_have_no_unintentional_exact_body_duplicates():
    """Report copy-pasted tests while allowing distinct parameterized matrices."""

    violations = []
    for group in _duplicate_test_bodies():
        parameterized = all(item[3] for item in group)
        distinct_decorators = len({item[4] for item in group}) == len(group)
        if not (parameterized and distinct_decorators):
            violations.append(
                ", ".join(f"{path}:{line} {name}" for path, line, name, *_rest in group)
            )
    assert not violations, "exact duplicate test bodies:\n" + "\n".join(violations)
