"""Packaging configuration and script tests."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_console_script_and_packaging_dependencies_are_declared() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["scripts"]["langharness"] == "langharness.__main__:main"
    assert project["entry-points"]["langharness.config"]["config"] == (
        "langharness_config.plugin:builtin_package"
    )
    plugins = project["entry-points"]["langharness.plugins"]
    assert plugins["builtin-core"] == "langharness_core.plugin:builtin_package"
    assert plugins["dynamic-core"] == "langharness_core.plugin:dynamic_package"
    assert plugins["agent-tools"] == "langharness_core.plugin:agent_tools_package"
    assert {"build>=1.3.0", "pyinstaller>=6.16.0"} <= set(
        project["optional-dependencies"]["packaging"]
    )
    assert {"prompt-toolkit>=3.0.52", "rich>=14.2.0"} <= set(project["dependencies"])
    assert "langchain-anthropic>=1.7.2" in project["dependencies"]
    package_data = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"][
        "setuptools"
    ]["package-data"]
    assert "config/langharness.toml" in package_data["langharness_config"]
    assert "prompts/code_agent/*.md" in package_data["langharness_core"]


def test_packaging_scripts_are_executable_and_valid_bash() -> None:
    for relative_path in ("scripts/build_packages.sh", "scripts/install.sh"):
        script = ROOT / relative_path
        assert os.access(script, os.X_OK)
        subprocess.run(["bash", "-n", script], check=True)
    installer = (ROOT / "scripts/install.sh").read_text()
    assert 'CONFIG_DIR=${LANG_HARNESS_HOME:-"$HOME/.langharness"}' in installer
    assert 'CONFIG_FILE="$CONFIG_DIR/langharness.toml"' in installer
    assert "[providers.default]" in installer
    assert "[plugins.ui]" in installer
    assert "langharness_api.sdk:package" in installer
    template = (ROOT / "src/langharness_config/config/langharness.toml").read_text()
    assert "log_file" not in template
    builder = (ROOT / "scripts/build_packages.sh").read_text()
    assert "--collect-submodules langharness" in builder
    assert "--collect-submodules langharness_logging" in builder
    assert '"$PROJECT_ROOT/src/langharness/__main__.py"' in builder


def test_package_workflow_builds_all_supported_platforms() -> None:
    workflow = (ROOT / ".github/workflows/packages.yml").read_text()
    assert "macos:" in workflow
    assert "ubuntu:" in workflow
    assert "centos:" in workflow
    assert "dist/*.pkg" in workflow
    assert "dist/*.deb" in workflow
    assert "dist/*.rpm" in workflow
