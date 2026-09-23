"""Render a stable TypeScript view of the tools visible in one agent scope."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

MAX_SCHEMA_DEPTH = 16


def _schema_type(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    *,
    depth: int = 0,
    seen_refs: frozenset[str] = frozenset(),
) -> str:
    """Convert supported JSON Schema shapes; uncertain shapes stay unknown."""
    if depth >= MAX_SCHEMA_DEPTH:
        return "unknown"
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in seen_refs or not ref.startswith("#/$defs/"):
            return "unknown"
        definitions = root.get("$defs")
        resolved = (
            definitions.get(ref.removeprefix("#/$defs/"))
            if isinstance(definitions, Mapping)
            else None
        )
        if not isinstance(resolved, Mapping):
            return "unknown"
        return _schema_type(
            resolved, root, depth=depth + 1, seen_refs=seen_refs | {ref}
        )

    for keyword, separator in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        variants = schema.get(keyword)
        if isinstance(variants, list) and variants:
            rendered = [
                _schema_type(item, root, depth=depth + 1, seen_refs=seen_refs)
                for item in variants
                if isinstance(item, Mapping)
            ]
            return separator.join(dict.fromkeys(rendered)) if rendered else "unknown"

    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return " | ".join(
            dict.fromkeys(json.dumps(value, ensure_ascii=False) for value in enum)
        )

    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(
            dict.fromkeys(
                _schema_type(
                    {**schema, "type": item},
                    root,
                    depth=depth + 1,
                    seen_refs=seen_refs,
                )
                for item in kind
                if isinstance(item, str)
            )
        ) or "unknown"
    if kind == "string":
        return "string"
    if kind in {"integer", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        items = schema.get("items")
        item_type = (
            _schema_type(items, root, depth=depth + 1, seen_refs=seen_refs)
            if isinstance(items, Mapping)
            else "unknown"
        )
        return f"Array<{item_type}>"
    if kind == "object" or isinstance(schema.get("properties"), Mapping):
        properties = schema.get("properties")
        required = schema.get("required")
        required_names = set(required) if isinstance(required, list) else set()
        if isinstance(properties, Mapping) and properties:
            fields = [
                f"{json.dumps(name, ensure_ascii=False)}"
                f"{'' if name in required_names else '?'}: "
                f"{_schema_type(value, root, depth=depth + 1, seen_refs=seen_refs)};"
                for name, value in sorted(properties.items())
                if isinstance(name, str) and isinstance(value, Mapping)
            ]
            return "{ " + " ".join(fields) + " }" if fields else "Record<string, unknown>"
        additional = schema.get("additionalProperties")
        value_type = (
            _schema_type(additional, root, depth=depth + 1, seen_refs=seen_refs)
            if isinstance(additional, Mapping)
            else "unknown"
        )
        return f"Record<string, {value_type}>"
    return "unknown"


def _input_schema(tool: Any) -> Mapping[str, Any]:
    schema = getattr(tool, "tool_call_schema", None)
    if isinstance(schema, Mapping):
        return schema
    model_json_schema = getattr(schema, "model_json_schema", None)
    if callable(model_json_schema):
        rendered = model_json_schema()
        if isinstance(rendered, Mapping):
            return rendered
    raise ValueError(f"Tool {getattr(tool, 'name', '<unnamed>')} has no input schema")


def _output_schema(tool: Any) -> Mapping[str, Any] | None:
    metadata = getattr(tool, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    output = metadata.get("output_schema")
    return output if isinstance(output, Mapping) else None


def render_tool_sdk(tools: Iterable[Any]) -> str:
    """Build declarations from the current scope's tools, excluding run_code."""
    visible: dict[str, Any] = {}
    for tool in tools:
        name = getattr(tool, "name", None)
        if name == "run_code":
            continue
        if not isinstance(name, str) or not name:
            raise ValueError("Every SDK tool must have a nonempty name")
        if name in visible:
            raise ValueError(f"Duplicate SDK tool: {name}")
        visible[name] = tool

    args: list[str] = []
    outputs: list[str] = []
    for name, tool in sorted(visible.items()):
        input_schema = _input_schema(tool)
        output_schema = _output_schema(tool)
        key = json.dumps(name, ensure_ascii=False)
        args.append(f"  {key}: {_schema_type(input_schema, input_schema)};")
        output_type = (
            _schema_type(output_schema, output_schema)
            if output_schema is not None
            else "unknown"
        )
        outputs.append(f"  {key}: {output_type};")

    return "\n".join(
        [
            "type ToolArgsMap = {",
            *args,
            "};",
            "type ToolOutputMap = {",
            *outputs,
            "};",
            "type ToolName = keyof ToolArgsMap;",
            "declare const tools: {",
            "  [K in ToolName]: (args: ToolArgsMap[K]) => Promise<ToolOutputMap[K]>;",
            "};",
        ]
    )
