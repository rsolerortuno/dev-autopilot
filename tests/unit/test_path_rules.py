import pytest
from pydantic import ValidationError

from dev_autopilot.models import (
    FilePathRule,
    GlobPathRule,
    JobSpecification,
    TreePathRule,
)
from dev_autopilot.models import TestCommands as Commands


def test_exact_file_tree_and_glob_have_distinct_behavior() -> None:
    file_rule = FilePathRule(kind="file", path="README.md")
    tree_rule = TreePathRule(kind="tree", path="examples/html_reports")
    glob_rule = GlobPathRule(kind="glob", pattern="tests/test_html_*.py")

    assert file_rule.matches("README.md")
    assert not file_rule.matches("docs/README.md")
    assert tree_rule.matches("examples/html_reports")
    assert tree_rule.matches("examples/html_reports/nested/report.html")
    assert not tree_rule.matches("examples/html_reports-old/report.html")
    assert glob_rule.matches("tests/test_html_output.py")
    assert not glob_rule.matches("tests/nested/test_html_output.py")


def test_double_star_crosses_directories() -> None:
    rule = GlobPathRule(kind="glob", pattern="tests/**/test_*.py")

    assert rule.matches("tests/test_one.py")
    assert rule.matches("tests/unit/test_one.py")


@pytest.mark.parametrize(
    ("rule_type", "kwargs"),
    [
        (FilePathRule, {"kind": "file", "path": "/etc/passwd"}),
        (FilePathRule, {"kind": "file", "path": "C:/Windows/system.ini"}),
        (TreePathRule, {"kind": "tree", "path": "docs/../secrets"}),
        (TreePathRule, {"kind": "tree", "path": "docs/"}),
        (FilePathRule, {"kind": "file", "path": ""}),
        (FilePathRule, {"kind": "file", "path": "src/*.py"}),
        (GlobPathRule, {"kind": "glob", "pattern": "README.md"}),
    ],
)
def test_invalid_and_ambiguous_rules_are_rejected(
    rule_type: type[FilePathRule] | type[TreePathRule] | type[GlobPathRule],
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        rule_type.model_validate(kwargs)


def test_duplicate_normalized_rules_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate normalized"):
        JobSpecification(
            name="job",
            objective="objective",
            repository="/repo",
            allowed_paths=(
                FilePathRule(kind="file", path="docs/./index.md"),
                FilePathRule(kind="file", path="docs/index.md"),
            ),
            test_commands=Commands(
                baseline="pytest",
                fast="pytest tests/unit",
                final="pytest",
            ),
        )
