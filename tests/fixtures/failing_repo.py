"""Build a tiny repository with a test that fails until its bug is fixed."""

from __future__ import annotations

from pathlib import Path

MODULE = '''"""A tiny calculator with an off-by-one bug."""


def total(values: list[int]) -> int:
    """Sum the values."""
    return sum(values[:-1])
'''

TEST = """from calc import total


def test_total_sums_every_value() -> None:
    assert total([1, 2, 3]) == 6
"""

INSTRUCTIONS = """# AGENTS.md

- Run `python -m pytest -q` before claiming a fix.
- `calc.total` must sum every value it is given.
"""


def build(root: Path) -> Path:
    """Create the failing repository and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(INSTRUCTIONS, encoding="utf-8")
    (root / "calc.py").write_text(MODULE, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    return root
