"""Declarative package format used by third-party plugin entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from langharness_plugin.registry import PluginDescriptor

PluginTarget = Literal["root", "ui", "server", "agent", "agent_instance"]


@dataclass(frozen=True, slots=True)
class ToolExport:
    name: str
    description: str
    method: str
    args_schema: type[Any]
    target_scope: Literal["agent", "agent_instance"] = "agent"
    destructive: bool = False
    #: Template for how a call reads in the CLI transcript, e.g.
    #: ``"echo({text})"``. Empty means the call shows as its bare name.
    headline: str = ""


@dataclass(frozen=True, slots=True)
class PluginContribution:
    id: str
    target: PluginTarget
    descriptor: PluginDescriptor
    tool_exports: tuple[ToolExport, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginPackage:
    id: str
    version: str
    contributions: tuple[PluginContribution, ...]

