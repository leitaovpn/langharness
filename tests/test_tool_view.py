"""Filling a tool's headline template, and summarizing its output line."""

from __future__ import annotations

from langharness_cli.common.toolview import render_headline, render_output


def test_template_is_filled_from_the_call_arguments() -> None:
    assert render_headline("Bash({commands})", {"commands": "pwd"}) == "Bash(pwd)"


def test_a_template_may_use_several_arguments() -> None:
    template = "install_plugin({package_id} → {scope_id})"

    rendered = render_headline(
        template, {"package_id": "real.echo", "scope_id": "server"}
    )

    assert rendered == "install_plugin(real.echo → server)"


def test_a_template_without_placeholders_renders_as_written() -> None:
    assert render_headline("list_runtime_plugins()", {}) == "list_runtime_plugins()"


def test_a_missing_argument_declines_rather_than_leaving_a_hole() -> None:
    assert render_headline("Read({file_path})", {"path": "/tmp/a"}) is None


def test_a_none_argument_declines() -> None:
    assert render_headline("list({scope})", {"scope": None}) is None


def test_an_unparsable_template_declines() -> None:
    assert render_headline("Bash({commands)", {"commands": "pwd"}) is None


def test_long_values_are_collapsed_and_cut() -> None:
    rendered = render_headline("Bash({commands})", {"commands": "echo\n" + "x" * 200})

    assert rendered is not None
    assert "\n" not in rendered
    assert rendered.endswith("…)")
    assert len(rendered) < 100


def test_output_that_fits_is_shown_whole() -> None:
    line = render_output("hello world")

    assert line.text == "hello world"
    assert line.truncated is False


def test_multi_line_output_keeps_the_first_line_and_reports_the_rest() -> None:
    line = render_output("first\nsecond\nthird")

    assert line.text.startswith("first")
    assert "3 lines" in line.text
    assert line.truncated is True


def test_a_single_overlong_line_is_cut_and_marked() -> None:
    line = render_output("y" * 400)

    assert line.truncated is True
    assert line.text.endswith("…")


def test_empty_output_says_so() -> None:
    line = render_output("")

    assert line.truncated is False
    assert line.text == ""
