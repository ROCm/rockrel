from pathlib import Path

import pytest

from scripts.render_express_train_workflow import RenderError, render_workflow


def test_renders_every_placeholder_to_immutable_sha(tmp_path):
    template = tmp_path / "template.yml"
    template.write_text(
        "uses: ROCm/rockrel/.github/workflows/x.yml@ROCKREL_AUTOMATION_SHA\n"
        "with:\n  automation_ref: ROCKREL_AUTOMATION_SHA\n"
    )
    sha = "a" * 40

    rendered = render_workflow(template, sha)

    assert "ROCKREL_AUTOMATION_SHA" not in rendered
    assert rendered.count(sha) == 2


@pytest.mark.parametrize("sha", ["main", "abc123", "A" * 40, "g" * 40])
def test_rejects_mutable_or_invalid_ref(tmp_path, sha):
    template = tmp_path / "template.yml"
    template.write_text("ROCKREL_AUTOMATION_SHA\n")
    with pytest.raises(RenderError, match="full lowercase"):
        render_workflow(template, sha)


def test_rejects_template_without_placeholder(tmp_path):
    template = tmp_path / "template.yml"
    template.write_text("name: no placeholder\n")
    with pytest.raises(RenderError, match="placeholder"):
        render_workflow(template, "a" * 40)
