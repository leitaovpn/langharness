# PluginDescriptor/Instance 分离 + PluginManager 重设计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/designs/2026-09-19-plugin-descriptor-instance-design.md` 与 `docs/designs/2026-09-19-plugin-manager-redesign-design.md` 两份设计,将插件的静态定义(descriptor 七字段)与运行时实例(UUID 标识)彻底分离,重写 PluginManager 及整个插件子系统的身份模型与持久化。

**Architecture:** `module` 标识一个 Pelix bundle,`factory` 是全局 plugin key(进程内唯一注册),`(factory, module, scope_id)` 是 scope 内的注册关系(可对应多个实例),`instance UUID` 是唯一运行时实例标识。discovery 只发现不持久化;scope 由 ScopeTree 管理、不写入 descriptor;descriptor 严格七字段(`name/version/module/factory/specification/swap_policy/description`),properties 属于实例并在注册时传入。持久化继续用 RuntimeStateStore 单行 JSON 快照 + CAS,schema 升到 v3,另加 append-only history。

**Tech Stack:** Python ≥ 3.13,Pelix/iPOPO,dataclasses(frozen+slots),sqlite3,importlib.metadata entry points。质量门禁:`make check`(ruff + mypy + pyright + pytest,覆盖率 ≥ 95%)。

## Global Constraints

- **无存量迁移**:项目处于开发阶段,不读旧 schema。`runtime_state.sqlite3` 旧文件直接删除后测试;加载 schema ≠ 3 的文件报清晰错误。
- **严格七字段**:`PluginDescriptor` 只允许 `name, version, module, factory, specification, swap_policy, description` 七个字段,禁止 properties/instance/scope/enabled/ranking。
- **内部 API 缺省 scope = root;用户可见的 CLI/API 命令必须显式传 scope**。
- **UUID**:实例标识一律 `uuid.uuid4().hex`;升级生成新 UUID,`replaced_instance` 记入 history(保留 append-only lifecycle history)。
- **factory 命名**:新插件遵循 `<fully-qualified-module>.<ClassName>-factory`;现有 builtin factory 名不改(builtin 后续会被丢弃,不做重命名 churn)。
- **TDD**:每个任务先写失败测试再实现,每任务一次提交;测试代码用英文,与现有风格一致。
- **事务语义**:运行时变更失败 → 回滚运行时并保持旧快照;持久化失败 → kill 刚创建的组件并回滚内存索引。
- 测试/质量命令统一使用仓库 venv:`.venv/bin/python -m pytest`。

---

## 决策记录(用户已确认)

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | properties 归属 | 严格七字段;默认值由装配/注册方在 install/create_instance 时传入;`apply_overrides` 改为 properties 级合并 |
| 2 | 存量数据 | 不迁移;旧状态文件删除后进入测试 |
| 3 | 卸载历史 | 保留 append-only `plugin_history`(与现有 PluginConfigStore 哲学一致) |
| 4 | CLI/API scope | 用户可见命令显式传 scope;内部 API 缺省 root |
| 5 | 升级 UUID | 生成新 UUID,history 记录 `replaced_instance` |
| 6 | 两条发现通道 | 类式 entry point 发现(manager.discover())与包式 PluginDiscovery(coordinator/装配)并存 |
| 7 | 装配直达 | manager 增加 `install_descriptor(descriptor)` 供 builtin 装配与 coordinator 使用;`install_plugin(factory, module)` 只查发现目录 |
| 8 | swap_policy | `hot` → `ipopo.reconfigure`;`restart` → kill + 同 UUID 重新实例化;两者都保留 UUID,失败恢复旧组件 |

## File Structure

**新建:**
- `src/langharness_plugin/errors.py` — 全部插件管理错误类型
- `src/langharness_plugin/scoped_dependencies.py` — `@ScopedDependencies` 类装饰器与读取助手
- `tests/test_scoped_dependencies.py` — 装饰器与 filter 合并测试

**重写:**
- `src/langharness_plugin/registry.py` — 七字段 `PluginDescriptor`、`PluginInstanceRecord`、`PluginRegistrationKey`、三个 snapshot dataclass、`PluginMetadata`、工厂键目录 `PluginRegistry`
- `src/langharness_plugin/plugin_manager.py` — 新身份模型的 PluginManager(discovery / definition / instance / scope 四组 API + 持久化恢复)
- `src/langharness_plugin/state_store.py` — schema v3 快照(scopes/descriptors/instances/registrations)+ `plugin_history` 表
- `src/langharness_plugin/coordinator.py` — 注册记录按 instance UUID 建模,组合 manager API

**修改:**
- `src/langharness_plugin/discovery.py` — 新增类式发现;包式校验适配静态 descriptor
- `src/langharness_plugin/contracts.py` — `ScopedPluginRegistrar`/`DynamicPluginManager` 协议更新
- `src/langharness_plugin/config_store.py` — `apply_overrides` → properties 级 `merge_overrides`
- `src/langharness_plugin/scope_const.py` — 新增 `PLUGIN_INSTANCE_ID`
- `src/langharness/bootstrap.py` — 装配改为 install_descriptor + create_instance
- `src/langharness_core/plugin.py`、`src/langharness_core/plugins/agents/directory.py`、`src/langharness_core/plugins/loop/agent_loop.py` — 七字段 descriptor + `@ScopedDependencies`
- `src/langharness_api/plugin.py`、`src/langharness_cli/plugin.py`、`src/langharness_config/plugin.py`、`src/langharness_logging/plugin.py` — 七字段合规
- `src/langharness_api/plugins/routes/plugins.py`、CLI plugin 命令 — 显式 scope + 新输出字段

**测试重写/新增:**
- `tests/test_registry.py`、`tests/test_plugin_discovery.py`、`tests/test_runtime_state_store.py`、`tests/test_plugin_manager_unit.py`、`tests/test_plugin_manager_scoped.py`、`tests/test_runtime_mutation_coordinator.py`、`tests/test_scoped_dependencies.py`

---

## Task 1: errors.py — 插件管理错误类型

**Files:**
- Create: `src/langharness_plugin/errors.py`
- Test: `tests/test_plugin_errors.py`

**Interfaces:**
- Produces: `PluginError`(基类)、`PluginNotFoundError`、`PluginAlreadyInstalledError`、`PluginIdentityConflictError`、`PluginHasInstancesError`、`AmbiguousPluginError(name, candidates)`、`InstanceNotFoundError`、`InstanceStateError`、`ScopeHasChildrenError`、`ScopeHasInstancesError`。`ScopeNotFoundError` 从 `langharness_scope.errors` 复用。后续所有任务 import 这些类型。

- [ ] **Step 1: 写失败测试**

```python
"""Tests for plugin management error types."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from langharness_plugin.errors import (
    AmbiguousPluginError,
    InstanceNotFoundError,
    PluginError,
    PluginHasInstancesError,
    PluginIdentityConflictError,
    PluginNotFoundError,
    ScopeHasChildrenError,
    ScopeHasInstancesError,
)
from langharness_scope.errors import ScopeNotFoundError


def test_all_plugin_errors_subclass_plugin_error() -> None:
    errors = (
        PluginNotFoundError,
        PluginHasInstancesError,
        PluginIdentityConflictError,
        AmbiguousPluginError,
        InstanceNotFoundError,
        ScopeHasChildrenError,
        ScopeHasInstancesError,
    )
    for error in errors:
        assert issubclass(error, PluginError)


def test_scope_not_found_error_is_reusable_from_scope_module() -> None:
    with pytest.raises(ScopeNotFoundError):
        raise ScopeNotFoundError("missing")


def test_ambiguous_error_carries_candidates() -> None:
    error = AmbiguousPluginError("llm", ["a-factory", "b-factory"])
    assert error.name == "llm"
    assert error.candidates == ["a-factory", "b-factory"]
    assert "a-factory" in str(error) and "b-factory" in str(error)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_plugin_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'langharness_plugin.errors'`

- [ ] **Step 3: 实现**

```python
"""Error types raised by plugin management and discovery."""

from __future__ import annotations


class PluginError(RuntimeError):
    """Base class for plugin management errors."""


class PluginNotFoundError(PluginError):
    """A plugin definition is not discovered or installed."""


class PluginAlreadyInstalledError(PluginError):
    """A plugin definition is already installed."""


class PluginIdentityConflictError(PluginError):
    """Two contributions claim the same factory from different modules."""


class PluginHasInstancesError(PluginError):
    """An operation requires a plugin definition without live instances."""


class AmbiguousPluginError(PluginError):
    """A name lookup matched more than one descriptor or instance."""

    def __init__(self, name: str, candidates: list[str]) -> None:
        self.name = name
        self.candidates = candidates
        super().__init__(
            f"Plugin name {name!r} is ambiguous; provide a factory, "
            f"registration key, or instance UUID. Candidates: "
            f"{', '.join(candidates)}"
        )


class InstanceNotFoundError(PluginError):
    """An instance UUID is unknown."""


class InstanceStateError(PluginError):
    """An operation is invalid for the instance's current state."""


class ScopeHasChildrenError(PluginError):
    """A scope cannot be removed while it has child scopes."""


class ScopeHasInstancesError(PluginError):
    """A scope cannot be removed while it has plugin instances."""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_plugin_errors.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/errors.py tests/test_plugin_errors.py
git commit -m "feat(plugin): add plugin management error taxonomy"
```

---

## Task 2: registry.py — 身份模型数据类重写

**Files:**
- Rewrite: `src/langharness_plugin/registry.py`
- Test: `tests/test_registry.py`(重写)

**Interfaces:**
- Produces:
  - `SWAP_POLICIES = ("hot", "restart")`
  - `PluginDescriptor(name, version, module, factory, specification, description, swap_policy="restart")` — frozen+slots,`to_dict()`/`from_dict()`
  - `validate_descriptor(descriptor) -> None` — 六字段非空字符串、swap_policy 合法、description 非空(固定策略);违规抛 `ValueError`
  - `PluginInstanceRecord(instance, factory, module, scope_id, properties, enabled=True, ranking=0, status="active")` — `to_dict()`/`from_dict()`;status 为 `Literal["active","disabled","failed","missing"]`
  - `PluginRegistrationKey(factory, module, scope_id)` — frozen+slots
  - `DiscoverySnapshot(descriptors, warnings, discovered_at)`
  - `PluginDefinitionSnapshot(descriptor, installed, bundle_module, instance_count, scopes, status="installed")` — status 为 `Literal["installed","upgrade_available","missing"]`(对设计文档 dataclass 的已记录扩展,承载其"标记 upgrade_available"要求)
  - `PluginInstanceSnapshot(instance, factory, module, scope_id, properties, enabled, ranking, status)` — `properties` 构造时包成 `MappingProxyType`
  - `PluginMetadata(name, version, factory, specification, description, swap_policy="restart", module=None)` + `plugin_metadata(**kwargs)` 类装饰器(写入 `__plugin_metadata__`)+ `PLUGIN_METADATA_ATTR`

- [ ] **Step 1: 写失败测试**(替换 `tests/test_registry.py` 全文)

```python
"""Tests for the plugin identity model in registry.py."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from types import MappingProxyType

import pytest

from langharness_plugin.errors import PluginIdentityConflictError
from langharness_plugin.registry import (
    PLUGIN_METADATA_ATTR,
    PluginDescriptor,
    PluginInstanceRecord,
    PluginMetadata,
    PluginRegistrationKey,
    SWAP_POLICIES,
    plugin_metadata,
    validate_descriptor,
)
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Provides an LLM service for agents. Implements agent.plugin.llm. "
    "Use it whenever an agent loop needs a model. Properties: plugin.model.name. "
    "Requires a restart for property changes. Uninstall when no agent needs it."
)


def descriptor(**overrides) -> PluginDescriptor:
    defaults = dict(
        name="llm",
        version="1.0.0",
        module="langharness_core.plugins.llm.llm",
        factory="llm-plugin-factory",
        specification="agent.plugin.llm",
        description=DESCRIPTION,
    )
    defaults.update(overrides)
    return PluginDescriptor(**defaults)


class TestDescriptorFields:
    def test_descriptor_has_exactly_seven_fields(self) -> None:
        assert len(PluginDescriptor.__dataclass_fields__) == 7
        assert descriptor().to_dict() == {
            "name": "llm",
            "version": "1.0.0",
            "module": "langharness_core.plugins.llm.llm",
            "factory": "llm-plugin-factory",
            "specification": "agent.plugin.llm",
            "description": DESCRIPTION,
            "swap_policy": "restart",
        }

    def test_from_dict_rejects_runtime_fields(self) -> None:
        data = descriptor().to_dict()
        data["instance"] = "llm"
        data["scope"] = "server"
        data["properties"] = {}
        with pytest.raises(ValueError, match="Unknown descriptor fields"):
            PluginDescriptor.from_dict(data)

    def test_from_dict_requires_description(self) -> None:
        data = descriptor().to_dict()
        del data["description"]
        with pytest.raises(ValueError, match="Missing descriptor fields"):
            PluginDescriptor.from_dict(data)

    def test_descriptor_is_frozen(self) -> None:
        with pytest.raises(Exception):
            descriptor().name = "other"  # type: ignore[misc]


class TestDescriptorValidation:
    def test_valid_descriptor_passes(self) -> None:
        validate_descriptor(descriptor())

    @pytest.mark.parametrize("field", ["name", "version", "module", "factory", "specification", "description"])
    def test_empty_text_field_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            validate_descriptor(descriptor(**{field: "  "}))

    def test_invalid_swap_policy_rejected(self) -> None:
        with pytest.raises(ValueError, match="swap_policy"):
            validate_descriptor(descriptor(swap_policy="instant"))


class TestInstanceRecord:
    def test_record_round_trip(self) -> None:
        record = PluginInstanceRecord(
            "abc123",
            "llm-plugin-factory",
            "langharness_core.plugins.llm.llm",
            ScopeId("agent:a"),
            {"plugin.model.name": "gpt"},
            enabled=False,
            ranking=3,
            status="disabled",
        )
        assert PluginInstanceRecord.from_dict(record.to_dict()) == record

    def test_record_defaults(self) -> None:
        record = PluginInstanceRecord("u", "f", "m", ROOT_SCOPE_ID, {})
        assert record.enabled is True
        assert record.ranking == 0
        assert record.status == "active"


class TestRegistrationKey:
    def test_key_is_structured_not_joined(self) -> None:
        key = PluginRegistrationKey("f", "m", ScopeId("server"))
        assert key.factory == "f" and key.module == "m"
        assert key.scope_id == ScopeId("server")
        assert key == PluginRegistrationKey("f", "m", ScopeId("server"))
        assert hash(key) == hash(PluginRegistrationKey("f", "m", ScopeId("server")))


class TestSnapshots:
    def test_instance_snapshot_properties_are_read_only(self) -> None:
        from langharness_plugin.registry import PluginInstanceSnapshot

        snapshot = PluginInstanceSnapshot(
            "u", "f", "m", ROOT_SCOPE_ID, {"k": "v"}, True, 0, "active"
        )
        assert isinstance(snapshot.properties, MappingProxyType)
        assert snapshot.properties["k"] == "v"

    def test_definition_snapshot_defaults_to_installed(self) -> None:
        from langharness_plugin.registry import PluginDefinitionSnapshot

        snapshot = PluginDefinitionSnapshot(descriptor(), True, descriptor().module, 0, ())
        assert snapshot.status == "installed"


class TestPluginMetadata:
    def test_decorator_stores_metadata_on_class(self) -> None:
        @plugin_metadata(
            name="echo",
            version="1.0.0",
            factory="echo-factory",
            specification="test.echo",
            description="Echo plugin.",
        )
        class Echo:
            pass

        metadata = getattr(Echo, PLUGIN_METADATA_ATTR)
        assert isinstance(metadata, PluginMetadata)
        assert metadata.factory == "echo-factory"

    def test_metadata_module_defaults_to_none(self) -> None:
        metadata = PluginMetadata(
            name="e", version="1", factory="f", specification="s", description="d"
        )
        assert metadata.module is None
        assert SWAP_POLICIES == ("hot", "restart")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_registry.py -v`
Expected: FAIL(AttributeError: 旧 PluginDescriptor 无 description / 构造参数不匹配)

- [ ] **Step 3: 重写 registry.py**

```python
"""Plugin identity model: descriptors, instance records, and catalogs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Mapping

from langharness_scope import ScopeId

SWAP_POLICIES = ("hot", "restart")

PLUGIN_METADATA_ATTR = "__plugin_metadata__"

InstanceStatus = Literal["active", "disabled", "failed", "missing"]
DefinitionStatus = Literal["installed", "upgrade_available", "missing"]

_DESCRIPTOR_FIELDS = (
    "name",
    "version",
    "module",
    "factory",
    "specification",
    "description",
    "swap_policy",
)


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    """Static definition of one discoverable, installable plugin.

    Strictly seven fields: runtime state (instance UUID, scope, properties,
    enabled, ranking) never lives here. ``description`` is an AI-facing
    operation manual and does not participate in identity.
    """

    name: str
    version: str
    module: str
    factory: str
    specification: str
    description: str
    swap_policy: Literal["hot", "restart"] = "restart"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "module": self.module,
            "factory": self.factory,
            "specification": self.specification,
            "description": self.description,
            "swap_policy": self.swap_policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginDescriptor:
        unknown = set(data) - set(_DESCRIPTOR_FIELDS)
        if unknown:
            raise ValueError(f"Unknown descriptor fields: {sorted(unknown)}")
        required = {
            "name",
            "version",
            "module",
            "factory",
            "specification",
            "description",
        }
        missing = required - set(data)
        if missing:
            raise ValueError(f"Missing descriptor fields: {sorted(missing)}")
        return cls(
            name=data["name"],
            version=data["version"],
            module=data["module"],
            factory=data["factory"],
            specification=data["specification"],
            description=data["description"],
            swap_policy=data.get("swap_policy", "restart"),
        )


def validate_descriptor(descriptor: PluginDescriptor) -> None:
    """Reject descriptors with empty, missing, or illegal fixed fields."""
    for field in (
        descriptor.name,
        descriptor.version,
        descriptor.module,
        descriptor.factory,
        descriptor.specification,
        descriptor.description,
    ):
        if not isinstance(field, str) or not field.strip():
            raise ValueError("Plugin descriptor text fields must be non-empty strings")
    if descriptor.swap_policy not in SWAP_POLICIES:
        raise ValueError(
            f"Plugin descriptor 'swap_policy' must be one of {SWAP_POLICIES}"
        )


@dataclass(frozen=True, slots=True)
class PluginInstanceRecord:
    """Runtime record binding one component instance to its definition."""

    instance: str  # UUID string, iPOPO component name
    factory: str  # from PluginDescriptor
    module: str  # from PluginDescriptor
    scope_id: ScopeId  # normalized scope; default root
    properties: dict[str, Any]  # effective properties actually injected
    enabled: bool = True
    ranking: int = 0
    status: InstanceStatus = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "factory": self.factory,
            "module": self.module,
            "scope_id": str(self.scope_id),
            "properties": self.properties,
            "enabled": self.enabled,
            "ranking": self.ranking,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginInstanceRecord:
        return cls(
            instance=str(data["instance"]),
            factory=str(data["factory"]),
            module=str(data["module"]),
            scope_id=ScopeId(str(data["scope_id"])),
            properties=dict(data.get("properties", {})),
            enabled=bool(data.get("enabled", True)),
            ranking=int(data.get("ranking", 0)),
            status=str(data.get("status", "active")),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class PluginRegistrationKey:
    """Structured scoped registration identity; never joined into a string."""

    factory: str
    module: str
    scope_id: ScopeId


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    descriptors: tuple[PluginDescriptor, ...]
    warnings: tuple[str, ...]
    discovered_at: datetime


@dataclass(frozen=True, slots=True)
class PluginDefinitionSnapshot:
    descriptor: PluginDescriptor
    installed: bool
    bundle_module: str
    instance_count: int
    scopes: tuple[ScopeId, ...]
    status: DefinitionStatus = "installed"


@dataclass(frozen=True, slots=True)
class PluginInstanceSnapshot:
    instance: str
    factory: str
    module: str
    scope_id: ScopeId
    properties: Mapping[str, Any]  # effective properties, read-only
    enabled: bool
    ranking: int
    status: InstanceStatus

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "properties", MappingProxyType(dict(self.properties))
        )


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """Class-level discovery metadata attached via ``@plugin_metadata``."""

    name: str
    version: str
    factory: str
    specification: str
    description: str
    swap_policy: Literal["hot", "restart"] = "restart"
    module: str | None = None  # when set, must equal the class's __module__


def plugin_metadata(**kwargs: Any) -> Any:
    """Class decorator storing ``PluginMetadata`` under PLUGIN_METADATA_ATTR."""

    def decorate(cls: Any) -> Any:
        setattr(cls, PLUGIN_METADATA_ATTR, PluginMetadata(**kwargs))
        return cls

    return decorate
```

注意:本任务只重写数据类与校验;`PluginRegistry` 移入 Task 3,所以若 `registry.py` 中其他模块 import 了 `PluginRegistry`,先保留一个占位(原样保留旧类或延迟到 Task 3 一并替换,取决于执行顺序;建议本任务内保留旧 `PluginRegistry` 原代码不动,Task 3 再替换)。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/registry.py tests/test_registry.py
git commit -m "feat(plugin): seven-field PluginDescriptor and runtime identity records"
```

---

## Task 3: PluginRegistry — 工厂键目录与歧义检测

**Files:**
- Modify: `src/langharness_plugin/registry.py`(替换旧 PluginRegistry)
- Test: `tests/test_registry.py`(追加)

**Interfaces:**
- Consumes: Task 2 的数据类、Task 1 的 `AmbiguousPluginError`/`PluginIdentityConflictError`
- Produces:
  - `PluginRegistry(descriptors=())` — `add(descriptor)`(factory 重复 → `PluginIdentityConflictError`;校验失败 → ValueError)、`get(factory) -> PluginDescriptor | None`(工厂键)、`get_by_name(name) -> PluginDescriptor`(name 唯一才返回,多个匹配抛 `AmbiguousPluginError` 并携带候选 factory)、`list()`(按 factory 排序)、`remove(factory)`、`save(path)`/`load(path)`(JSON,version 1,仅静态定义)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_registry.py`)

```python
def test_registry_keyed_by_factory_allows_duplicate_names() -> None:
    from langharness_plugin.registry import PluginRegistry

    registry = PluginRegistry(
        [
            descriptor(factory="f-a", module="m.a"),
            descriptor(factory="f-b", module="m.b"),
        ]
    )
    assert registry.get("f-a") is not None
    assert registry.get("f-b") is not None
    assert len(registry.list()) == 2


def test_registry_rejects_duplicate_factory() -> None:
    from langharness_plugin.registry import PluginRegistry

    registry = PluginRegistry([descriptor(factory="f", module="m.a")])
    with pytest.raises(PluginIdentityConflictError, match="f"):
        registry.add(descriptor(factory="f", module="m.b"))


def test_registry_get_by_name_raises_ambiguity() -> None:
    from langharness_plugin.errors import AmbiguousPluginError
    from langharness_plugin.registry import PluginRegistry

    registry = PluginRegistry(
        [
            descriptor(factory="f-a", module="m.a"),
            descriptor(factory="f-b", module="m.b"),
        ]
    )
    with pytest.raises(AmbiguousPluginError) as caught:
        registry.get_by_name("llm")
    assert set(caught.value.candidates) == {"f-a", "f-b"}


def test_registry_get_by_name_unique_returns_descriptor() -> None:
    from langharness_plugin.registry import PluginRegistry

    registry = PluginRegistry([descriptor(factory="f-a", module="m.a")])
    assert registry.get_by_name("llm").factory == "f-a"
    with pytest.raises(KeyError):
        registry.get_by_name("missing")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_registry.py -v`
Expected: FAIL(旧 add 按 name 判重/无 get_by_name)

- [ ] **Step 3: 实现**(替换 registry.py 中的 `PluginRegistry` 类)

```python
class PluginRegistry:
    """Validated catalog of plugin definitions, keyed by factory."""

    def __init__(
        self,
        descriptors: list[PluginDescriptor] | tuple[PluginDescriptor, ...] = (),
    ) -> None:
        self._descriptors: dict[str, PluginDescriptor] = {}
        for descriptor in descriptors:
            self.add(descriptor)

    def add(self, descriptor: PluginDescriptor) -> None:
        validate_descriptor(descriptor)
        existing = self._descriptors.get(descriptor.factory)
        if existing is not None:
            raise PluginIdentityConflictError(
                f"Factory {descriptor.factory!r} is already registered by "
                f"module {existing.module!r}"
            )
        self._descriptors[descriptor.factory] = descriptor

    def get(self, factory: str) -> PluginDescriptor | None:
        return self._descriptors.get(factory)

    def get_by_name(self, name: str) -> PluginDescriptor:
        matches = [
            descriptor
            for descriptor in self._descriptors.values()
            if descriptor.name == name
        ]
        if not matches:
            raise KeyError(name)
        if len(matches) > 1:
            raise AmbiguousPluginError(
                name, sorted(descriptor.factory for descriptor in matches)
            )
        return matches[0]

    def list(self) -> list[PluginDescriptor]:
        return [self._descriptors[factory] for factory in sorted(self._descriptors)]

    def remove(self, factory: str) -> PluginDescriptor:
        if factory not in self._descriptors:
            raise KeyError(factory)
        return self._descriptors.pop(factory)

    def save(self, path: Path) -> None:
        data = {
            "version": 1,
            "plugins": [descriptor.to_dict() for descriptor in self.list()],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> PluginRegistry:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != 1:
            raise ValueError("Unsupported plugin registry version")
        return cls(
            [PluginDescriptor.from_dict(item) for item in raw.get("plugins", [])]
        )
```

同时删除旧类中的 `set_enabled` 与 instance/scope 相关校验(这些职责移入 instance 生命周期)。顶部 import 增加 `from pathlib import Path`、`import json`、`from langharness_plugin.errors import AmbiguousPluginError, PluginIdentityConflictError`(注意循环 import:errors.py 不 import registry,安全)。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/registry.py tests/test_registry.py
git commit -m "feat(plugin): factory-keyed descriptor catalog with name ambiguity detection"
```

---

## Task 4: discovery.py — 类式发现与包式校验适配

**Files:**
- Modify: `src/langharness_plugin/discovery.py`
- Test: `tests/test_plugin_discovery.py`(重写)

**Interfaces:**
- Consumes: Task 2 的 `PluginDescriptor`/`validate_descriptor`/`PluginMetadata`/`PLUGIN_METADATA_ATTR`
- Produces:
  - `descriptor_discovery(loader) -> tuple[dict[tuple[str, str], PluginDescriptor], tuple[str, ...]]` — 扫描 entry points:class 直接读 metadata;callable 调用后必须返回 class;module 校验(`metadata.module` 存在且 ≠ `cls.__module__` → warning 跳过);字段校验失败 → warning 跳过;`(module, factory)` first-wins(重复保留第一个并 warning)
  - 包式 `PluginDiscovery._validate_package/_validate_catalog` 更新:contribution descriptor 只允许七字段(validate_descriptor);去掉 descriptor name/instance 重复检查,改为 `(module, factory)` 去重

- [ ] **Step 1: 写失败测试**(替换 `tests/test_plugin_discovery.py`)

```python
"""Tests for class-based descriptor discovery and package validation."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from langharness_plugin.discovery import (
    DiscoveryResult,
    PluginDiscovery,
    descriptor_discovery,
)
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import plugin_metadata, validate_descriptor

DESCRIPTION = (
    "Echoes text. Implements test.echo. Use for round-trip checks. "
    "No properties. Uninstall when no longer needed."
)


@plugin_metadata(
    name="echo",
    version="1.0.0",
    factory="echo-factory",
    specification="test.echo",
    description=DESCRIPTION,
)
class Echo:
    pass


@plugin_metadata(
    name="echo2",
    version="1.0.0",
    factory="echo2-factory",
    specification="test.echo",
    description=DESCRIPTION,
    module="wrong.module.path",
)
class MismatchedModule:
    pass


@plugin_metadata(
    name="broken",
    version="",
    factory="broken-factory",
    specification="test.echo",
    description="",
)
class Broken:
    pass


class MissingMetadata:
    pass


def entries(*values):
    return [
        EntryPoint(item, f"ep-{index}", "langharness.plugins")
        for index, item in enumerate(values)
    ]


def test_class_entry_point_produces_descriptor() -> None:
    descriptors, warnings = descriptor_discovery(entries(Echo))
    assert len(descriptors) == 1
    descriptor = descriptors[("__main__", "echo-factory")]
    assert descriptor.name == "echo"
    assert descriptor.module == Echo.__module__
    validate_descriptor(descriptor)  # must not raise


def test_callable_entry_point_returning_class() -> None:
    descriptors, warnings = descriptor_discovery(entries(lambda: Echo))
    assert ("__main__", "echo-factory") in descriptors


def test_duplicate_module_factory_first_wins() -> None:
    other = type("Other", (Echo,), {})
    descriptors, warnings = descriptor_discovery(entries(Echo, other))
    assert len(descriptors) == 1
    assert any("duplicate" in warning for warning in warnings)


def test_module_mismatch_skipped_with_warning() -> None:
    descriptors, warnings = descriptor_discovery(entries(MismatchedModule))
    assert descriptors == {}
    assert any("module" in warning for warning in warnings)


def test_invalid_fields_skipped_with_warning() -> None:
    descriptors, warnings = descriptor_discovery(entries(Broken))
    assert descriptors == {}
    assert warnings


def test_missing_metadata_skipped_with_warning() -> None:
    descriptors, warnings = descriptor_discovery(entries(MissingMetadata))
    assert descriptors == {}
    assert warnings


def test_non_class_callable_result_skipped() -> None:
    descriptors, warnings = descriptor_discovery(entries(lambda: 42))
    assert descriptors == {}
    assert warnings


def test_one_bad_entry_point_does_not_block_others() -> None:
    descriptors, warnings = descriptor_discovery(entries(Broken, Echo))
    assert ("__main__", "echo-factory") in descriptors


def test_package_validation_requires_static_descriptor() -> None:
    from langharness_plugin.registry import PluginDescriptor

    bad = PluginDescriptor(
        name="x",
        version="1.0.0",
        module="m",
        factory="f",
        specification="s",
        description="d",  # empty policy is checked by validate_descriptor
    )
    package = PluginPackage("p", "1.0.0", (PluginContribution("c", "server", bad),))
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert isinstance(result, DiscoveryResult)


def test_package_catalog_allows_duplicate_names_different_factories() -> None:
    from langharness_plugin.registry import PluginDescriptor

    first = PluginDescriptor(
        name="same", version="1", module="m.a", factory="f-a",
        specification="s", description=DESCRIPTION,
    )
    second = PluginDescriptor(
        name="same", version="1", module="m.b", factory="f-b",
        specification="s", description=DESCRIPTION,
    )
    package = PluginPackage(
        "p", "1.0.0",
        (
            PluginContribution("a", "server", first),
            PluginContribution("b", "server", second),
        ),
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert len(result.packages) == 1
    assert len(result.packages[0].contributions) == 2


def test_package_catalog_rejects_duplicate_module_factory() -> None:
    from langharness_plugin.registry import PluginDescriptor

    first = PluginDescriptor(
        name="a", version="1", module="m", factory="f",
        specification="s", description=DESCRIPTION,
    )
    second = PluginDescriptor(
        name="b", version="1", module="m", factory="f",
        specification="s", description=DESCRIPTION,
    )
    package = PluginPackage(
        "p", "1.0.0",
        (
            PluginContribution("a", "server", first),
            PluginContribution("b", "server", second),
        ),
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    with pytest.raises(Exception, match="[Dd]uplicate"):
        discovery.scan()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_plugin_discovery.py -v`
Expected: FAIL(`descriptor_discovery` 不存在 / 旧校验逻辑)

- [ ] **Step 3: 实现**

在 discovery.py 中新增类式发现:

```python
from langharness_plugin.registry import (
    PLUGIN_METADATA_ATTR,
    PluginDescriptor,
    PluginMetadata,
    validate_descriptor,
)


def descriptor_discovery(
    loader: Callable[[], Iterable[Any]],
) -> tuple[dict[tuple[str, str], PluginDescriptor], tuple[str, ...]]:
    """Scan entry points for plugin classes and build static descriptors.

    Returns a (module, factory)-keyed map plus per-problem warnings. A bad
    entry point never blocks the rest.
    """
    warnings: list[str] = []
    descriptors: dict[tuple[str, str], PluginDescriptor] = {}
    for entry_point in loader():
        try:
            loaded = entry_point.load()
            cls = loaded if isinstance(loaded, type) else loaded() if callable(loaded) else None
            if not isinstance(cls, type):
                warnings.append(
                    f"{entry_point.name!r}: entry point must be a class or a "
                    f"callable returning a class, got {type(loaded).__name__}"
                )
                continue
            metadata = getattr(cls, PLUGIN_METADATA_ATTR, None)
            if not isinstance(metadata, PluginMetadata):
                warnings.append(
                    f"{entry_point.name!r}: class is missing @plugin_metadata"
                )
                continue
            if metadata.module is not None and metadata.module != cls.__module__:
                warnings.append(
                    f"{entry_point.name!r}: metadata module "
                    f"{metadata.module!r} does not match {cls.__module__!r}"
                )
                continue
            descriptor = PluginDescriptor(
                name=metadata.name,
                version=metadata.version,
                module=cls.__module__,
                factory=metadata.factory,
                specification=metadata.specification,
                description=metadata.description,
                swap_policy=metadata.swap_policy,
            )
            validate_descriptor(descriptor)
            key = (descriptor.module, descriptor.factory)
            if key in descriptors:
                warnings.append(
                    f"{entry_point.name!r}: duplicate (module, factory) {key}; "
                    "keeping the first entry point"
                )
                continue
            descriptors[key] = descriptor
        except Exception as exc:
            warnings.append(f"{entry_point.name!r}: {exc}")
    return descriptors, tuple(warnings)
```

修改包式校验:

```python
    @staticmethod
    def _validate_package(package: Any) -> None:
        if not isinstance(package, PluginPackage):
            raise PluginDiscoveryError("Entry point did not return PluginPackage")
        if not package.id.strip() or not package.version.strip():
            raise PluginDiscoveryError("Plugin package id and version must be non-empty")
        contribution_ids: set[str] = set()
        for contribution in package.contributions:
            if contribution.id in contribution_ids:
                raise PluginDiscoveryError(
                    f"Duplicate contribution {contribution.id!r} in {package.id!r}"
                )
            contribution_ids.add(contribution.id)
            if contribution.target not in VALID_TARGETS:
                raise PluginDiscoveryError(
                    f"Invalid contribution target: {contribution.target!r}"
                )
            validate_descriptor(contribution.descriptor)  # 静态定义校验
            if (
                contribution.tool_exports
                and contribution.descriptor.specification != SPEC_TOOL_EXPORT_TARGET
            ):
                raise PluginDiscoveryError(
                    "Tool exports require the plugin.tool_export.target specification"
                )

    @staticmethod
    def _validate_catalog(packages: list[PluginPackage]) -> None:
        package_ids: set[str] = set()
        descriptor_keys: set[tuple[str, str]] = set()
        for package in packages:
            if package.id in package_ids:
                raise PluginDiscoveryError(f"Duplicate plugin package: {package.id!r}")
            package_ids.add(package.id)
            for contribution in package.contributions:
                descriptor = contribution.descriptor
                key = (descriptor.module, descriptor.factory)
                if key in descriptor_keys:
                    raise PluginDiscoveryError(
                        f"Duplicate plugin descriptor: {key!r}"
                    )
                descriptor_keys.add(key)
```

删除对 `descriptor.name`/`descriptor.instance` 的重复检查(引用设计文档:discovery 按 `(module, factory)` 去重,name 允许重复,instance 不再存在于 descriptor)。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_plugin_discovery.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/discovery.py tests/test_plugin_discovery.py
git commit -m "feat(plugin): class-based descriptor discovery with first-wins dedupe"
```

---

## Task 5: state_store.py — schema v3 快照与 append-only history

**Files:**
- Rewrite: `src/langharness_plugin/state_store.py`
- Test: `tests/test_runtime_state_store.py`(重写)

**Interfaces:**
- Consumes: Task 2 的 `PluginDescriptor`/`PluginInstanceRecord`
- Produces:
  - `RUNTIME_SCHEMA = 3`、`RuntimeStateSchemaError(ValueError)`(带路径提示删除旧文件)
  - `PersistedDescriptorRecord(descriptor, source)` — source: `Literal["discovered", "assembly"]`
  - `PersistedPluginRegistration(package_id, contribution_id, package_version, instance, factory, module, scope_id, enabled, status)` — 新形状:instance UUID 替代 name/descriptor
  - `RuntimeStateSnapshot(version, scopes, descriptors, instances, registrations)` — 持久化 payload 带 `"schema": 3`
  - `HistoryEntry(ts, action, factory=None, module=None, scope_id=None, instance=None, detail)`、`PluginHistoryStore(Protocol)`、`InMemoryPluginHistoryStore`、`SqlitePluginHistoryStore`(同一 sqlite 文件内 `plugin_history` 表)
  - `InMemoryRuntimeStateStore`/`SqliteRuntimeStateStore` 更新至 v3;`load()` 遇到 schema ≠ 3 抛 `RuntimeStateSchemaError`;保留 CAS

- [ ] **Step 1: 写失败测试**(替换 `tests/test_runtime_state_store.py`)

```python
"""Tests for runtime state persistence (schema v3) and history."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import sqlite3

import pytest

from langharness_plugin.registry import (
    PluginDescriptor,
    PluginInstanceRecord,
    validate_descriptor,
)
from langharness_plugin.state_store import (
    HistoryEntry,
    InMemoryPluginHistoryStore,
    InMemoryRuntimeStateStore,
    PersistedDescriptorRecord,
    PersistedPluginRegistration,
    PluginStateConflictError,
    RuntimeStateSchemaError,
    RuntimeStateSnapshot,
    SqlitePluginHistoryStore,
    SqliteRuntimeStateStore,
)
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Test plugin. Implements test.spec. Use in tests only. "
    "No properties. Never uninstall while a test runs."
)


def descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="test-plugin",
        version="1.0.0",
        module="tests.test_module",
        factory="test-plugin-factory",
        specification="test.spec",
        description=DESCRIPTION,
    )


def instance_record() -> PluginInstanceRecord:
    return PluginInstanceRecord(
        "uuid-1", "test-plugin-factory", "tests.test_module",
        ScopeId("server"), {"plugin.model.name": "gpt"},
    )


def snapshot(version: int = 0) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        version,
        ({"id": "root", "parent_id": None, "name": "root"},
         {"id": "server", "parent_id": "root", "name": "Server"}),
        (PersistedDescriptorRecord(descriptor(), "assembly"),),
        (instance_record(),),
        (PersistedPluginRegistration(
            "pkg", "contrib", "1.0.0", "uuid-1",
            "test-plugin-factory", "tests.test_module",
            ScopeId("server"), True, "installed",
        ),),
    )


def test_in_memory_save_load_round_trip() -> None:
    store = InMemoryRuntimeStateStore()
    store.save(snapshot(), expected_version=0)
    loaded = store.load()
    assert loaded is not None
    assert loaded.descriptors[0].descriptor == descriptor()
    assert loaded.instances[0] == instance_record()
    assert loaded.registrations[0].instance == "uuid-1"
    validate_descriptor(loaded.descriptors[0].descriptor)


def test_cas_conflict_raises() -> None:
    store = InMemoryRuntimeStateStore()
    store.save(snapshot(), expected_version=0)
    with pytest.raises(PluginStateConflictError):
        store.save(snapshot(), expected_version=0)


def test_sqlite_round_trip_and_conflict(tmp_path) -> None:
    path = tmp_path / "runtime_state.sqlite3"
    store = SqliteRuntimeStateStore(path)
    version = store.save(snapshot(), expected_version=0)
    loaded = store.load()
    assert loaded is not None and loaded.version == version
    assert loaded.scopes[1]["id"] == "server"
    with pytest.raises(PluginStateConflictError):
        store.save(snapshot(), expected_version=1)


def test_old_schema_file_raises_clear_error(tmp_path) -> None:
    path = tmp_path / "runtime_state.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE runtime_state (singleton_id INTEGER PRIMARY KEY, "
            "version INTEGER NOT NULL, snapshot_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO runtime_state VALUES (1, 4, ?)",
            ('{"schema": 2, "scopes": [], "plugins": []}',),
        )
    store = SqliteRuntimeStateStore(path)
    with pytest.raises(RuntimeStateSchemaError, match="delete"):
        store.load()


def test_history_append_and_read(tmp_path) -> None:
    memory = InMemoryPluginHistoryStore()
    memory.append(HistoryEntry("2026-09-19T00:00:00Z", "uninstall",
                               factory="f", module="m", scope_id="server",
                               instance="uuid-1", detail={"reason": "test"}))
    entries = memory.entries()
    assert len(entries) == 1
    assert entries[0].action == "uninstall"
    assert entries[0].detail == {"reason": "test"}

    sqlite = SqlitePluginHistoryStore(tmp_path / "history.sqlite3")
    sqlite.append(HistoryEntry("2026-09-19T00:00:01Z", "create_instance",
                               factory="f", instance="uuid-2"))
    assert len(sqlite.entries()) == 1
    assert sqlite.entries()[0].instance == "uuid-2"


def test_empty_store_load_returns_none(tmp_path) -> None:
    assert SqliteRuntimeStateStore(tmp_path / "empty.sqlite3").load() is None
    assert InMemoryRuntimeStateStore().load() is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_runtime_state_store.py -v`
Expected: FAIL(RuntimeStateSchemaError/PersistedDescriptorRecord 不存在)

- [ ] **Step 3: 重写 state_store.py**

```python
"""Persistent runtime state (schema v3) and append-only lifecycle history."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from langharness_plugin.registry import PluginDescriptor, PluginInstanceRecord
from langharness_scope import ScopeId

RUNTIME_SCHEMA = 3

PluginStatus = Literal[
    "installed", "disabled", "upgrade_available", "missing", "failed"
]
DescriptorSource = Literal["discovered", "assembly"]


class PluginStateConflictError(ValueError):
    pass


class RuntimeStateSchemaError(ValueError):
    """The persisted state file uses an unsupported schema.

    This development build does not migrate legacy data: delete the file and
    restart to start from a clean state.
    """


@dataclass(frozen=True, slots=True)
class PersistedDescriptorRecord:
    descriptor: PluginDescriptor
    source: DescriptorSource


@dataclass(frozen=True, slots=True)
class PersistedPluginRegistration:
    """Package provenance of one dynamically installed instance."""

    package_id: str
    contribution_id: str
    package_version: str
    instance: str  # instance UUID created for this contribution
    factory: str
    module: str
    scope_id: ScopeId
    enabled: bool
    status: PluginStatus
    registration_key: str | None = None  # caller-supplied idempotency key


@dataclass(frozen=True, slots=True)
class RuntimeStateSnapshot:
    version: int
    scopes: tuple[dict[str, str | None], ...]
    descriptors: tuple[PersistedDescriptorRecord, ...]
    instances: tuple[PluginInstanceRecord, ...]
    registrations: tuple[PersistedPluginRegistration, ...]


class RuntimeStateStore(Protocol):
    def load(self) -> RuntimeStateSnapshot | None: ...

    def save(
        self, snapshot: RuntimeStateSnapshot, *, expected_version: int
    ) -> int: ...


def _encode_snapshot(snapshot: RuntimeStateSnapshot) -> str:
    return json.dumps(
        {
            "schema": RUNTIME_SCHEMA,
            "scopes": list(snapshot.scopes),
            "descriptors": [
                {
                    "descriptor": record.descriptor.to_dict(),
                    "source": record.source,
                }
                for record in snapshot.descriptors
            ],
            "instances": [item.to_dict() for item in snapshot.instances],
            "registrations": [_encode_registration(item) for item in snapshot.registrations],
        }
    )


def _decode_snapshot(raw: dict[str, Any]) -> tuple[
    tuple[dict[str, str | None], ...],
    tuple[PersistedDescriptorRecord, ...],
    tuple[PluginInstanceRecord, ...],
    tuple[PersistedPluginRegistration, ...],
]:
    return (
        tuple(raw["scopes"]),
        tuple(
            PersistedDescriptorRecord(
                PluginDescriptor.from_dict(item["descriptor"]),
                str(item["source"]),  # type: ignore[arg-type]
            )
            for item in raw["descriptors"]
        ),
        tuple(PluginInstanceRecord.from_dict(item) for item in raw["instances"]),
        tuple(_decode_registration(item) for item in raw["registrations"]),
    )


def _encode_registration(item: PersistedPluginRegistration) -> dict[str, Any]:
    return {
        "package_id": item.package_id,
        "contribution_id": item.contribution_id,
        "package_version": item.package_version,
        "instance": item.instance,
        "factory": item.factory,
        "module": item.module,
        "scope_id": str(item.scope_id),
        "enabled": item.enabled,
        "status": item.status,
        "registration_key": item.registration_key,
    }


def _decode_registration(data: dict[str, Any]) -> PersistedPluginRegistration:
    return PersistedPluginRegistration(
        package_id=str(data["package_id"]),
        contribution_id=str(data["contribution_id"]),
        package_version=str(data["package_version"]),
        instance=str(data["instance"]),
        factory=str(data["factory"]),
        module=str(data["module"]),
        scope_id=ScopeId(str(data["scope_id"])),
        enabled=bool(data["enabled"]),
        status=str(data["status"]),  # type: ignore[arg-type]
        registration_key=(
            str(data["registration_key"])
            if data.get("registration_key") is not None
            else None
        ),
    )


class InMemoryRuntimeStateStore:
    def __init__(self) -> None:
        self.snapshot: RuntimeStateSnapshot | None = None

    def load(self) -> RuntimeStateSnapshot | None:
        return self.snapshot

    def save(self, snapshot: RuntimeStateSnapshot, *, expected_version: int) -> int:
        current = self.snapshot.version if self.snapshot is not None else 0
        if current != expected_version:
            raise PluginStateConflictError(
                f"Runtime state version changed: expected {expected_version}, got {current}"
            )
        version = current + 1
        self.snapshot = replace(snapshot, version=version)
        return version


class SqliteRuntimeStateStore:
    """Atomically persists scopes, definitions, instances, and registrations."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runtime_state ("
                "singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1), "
                "version INTEGER NOT NULL, snapshot_json TEXT NOT NULL)"
            )

    def load(self) -> RuntimeStateSnapshot | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT version, snapshot_json FROM runtime_state WHERE singleton_id = 1"
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(row[1])
        if raw.get("schema") != RUNTIME_SCHEMA:
            raise RuntimeStateSchemaError(
                f"Runtime state file {self.path} uses schema "
                f"{raw.get('schema')!r}; this build only supports schema "
                f"{RUNTIME_SCHEMA} and does not migrate legacy data. "
                f"Delete the file to reset."
            )
        scopes, descriptors, instances, registrations = _decode_snapshot(raw)
        return RuntimeStateSnapshot(
            int(row[0]), scopes, descriptors, instances, registrations
        )

    def save(self, snapshot: RuntimeStateSnapshot, *, expected_version: int) -> int:
        payload = _encode_snapshot(snapshot)
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version FROM runtime_state WHERE singleton_id = 1"
            ).fetchone()
            current = int(row[0]) if row is not None else 0
            if current != expected_version:
                raise PluginStateConflictError(
                    f"Runtime state version changed: expected {expected_version}, got {current}"
                )
            version = current + 1
            connection.execute(
                "INSERT INTO runtime_state VALUES (1, ?, ?) "
                "ON CONFLICT(singleton_id) DO UPDATE SET "
                "version=excluded.version, snapshot_json=excluded.snapshot_json",
                (version, payload),
            )
        return version


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    ts: str
    action: str
    factory: str | None = None
    module: str | None = None
    scope_id: str | None = None
    instance: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class PluginHistoryStore(Protocol):
    def append(self, entry: HistoryEntry) -> None: ...

    def entries(self) -> tuple[HistoryEntry, ...]: ...


class InMemoryPluginHistoryStore:
    def __init__(self) -> None:
        self._entries: list[HistoryEntry] = []

    def append(self, entry: HistoryEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._entries)


class SqlitePluginHistoryStore:
    """Append-only lifecycle history inside the runtime state database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS plugin_history ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ts TEXT NOT NULL, action TEXT NOT NULL, "
                "factory TEXT, module TEXT, scope_id TEXT, instance TEXT, "
                "detail TEXT NOT NULL)"
            )

    def append(self, entry: HistoryEntry) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO plugin_history "
                "(ts, action, factory, module, scope_id, instance, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.ts,
                    entry.action,
                    entry.factory,
                    entry.module,
                    entry.scope_id,
                    entry.instance,
                    json.dumps(entry.detail),
                ),
            )

    def entries(self) -> tuple[HistoryEntry, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT ts, action, factory, module, scope_id, instance, detail "
                "FROM plugin_history ORDER BY seq"
            ).fetchall()
        return tuple(
            HistoryEntry(
                str(row[0]), str(row[1]), str(row[2]) if row[2] else None,
                str(row[3]) if row[3] else None, str(row[4]) if row[4] else None,
                str(row[5]) if row[5] else None, json.loads(row[6]),
            )
            for row in rows
        )
```

删除旧 `SqlitePluginStateStore`/`PluginStateSnapshot`(coordinator 的注册记录现在走 RuntimeStateSnapshot.registrations)。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_runtime_state_store.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/state_store.py tests/test_runtime_state_store.py
git commit -m "feat(plugin): schema v3 runtime state with instance records and lifecycle history"
```

---

## Task 6: scoped_dependencies.py — Requires/RequiresBest 自动配置声明

**Files:**
- Create: `src/langharness_plugin/scoped_dependencies.py`
- Test: `tests/test_scoped_dependencies.py`

**Interfaces:**
- Produces:
  - `SCOPED_FIELDS_ATTR = "__scoped_dependencies__"`
  - `ScopedDependencies(*fields)` — 类装饰器,`setattr(cls, SCOPED_FIELDS_ATTR, frozenset(fields))`
  - `scoped_fields(cls) -> frozenset[str]`
  - `scoped_fields_from_module(module_name: str) -> frozenset[str]` — import 模块、找模块内声明了 SCOPED_FIELDS_ATTR 的类;import 失败或未找到返回空集

- [ ] **Step 1: 写失败测试**

```python
"""Tests for scoped dependency declarations and module introspection."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from langharness_plugin.scoped_dependencies import (
    SCOPED_FIELDS_ATTR,
    ScopedDependencies,
    scoped_fields,
    scoped_fields_from_module,
)


@ScopedDependencies("_llm_provider", "_tool_providers")
class AgentLoop:
    pass


class PlainPlugin:
    pass


def test_decorator_records_fields_as_frozenset() -> None:
    assert getattr(AgentLoop, SCOPED_FIELDS_ATTR) == frozenset(
        {"_llm_provider", "_tool_providers"}
    )
    assert scoped_fields(AgentLoop) == frozenset(
        {"_llm_provider", "_tool_providers"}
    )


def test_undecorated_class_has_no_scoped_fields() -> None:
    assert not hasattr(PlainPlugin, SCOPED_FIELDS_ATTR)
    assert scoped_fields(PlainPlugin) == frozenset()


def test_module_introspection_finds_decorated_class() -> None:
    assert scoped_fields_from_module(__name__) == frozenset(
        {"_llm_provider", "_tool_providers"}
    )


def test_module_introspection_tolerates_bad_module() -> None:
    assert scoped_fields_from_module("no.such.module.anywhere") == frozenset()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_scoped_dependencies.py -v`
Expected: FAIL(`ModuleNotFoundError: langharness_plugin.scoped_dependencies`)

- [ ] **Step 3: 实现**

```python
"""Class-level declaration of scoped iPOPO dependency fields."""

from __future__ import annotations

import importlib
from typing import Any

SCOPED_FIELDS_ATTR = "__scoped_dependencies__"


def ScopedDependencies(*fields: str) -> Any:
    """Class decorator marking which Requires/RequiresBest fields are scoped.

    Only declared fields receive the automatic scope visibility filter at
    instance creation; everything else keeps global service lookup.
    """

    def decorate(cls: Any) -> Any:
        setattr(cls, SCOPED_FIELDS_ATTR, frozenset(fields))
        return cls

    return decorate


def scoped_fields(cls: Any) -> frozenset[str]:
    """The scoped field names declared on one class."""
    return frozenset(getattr(cls, SCOPED_FIELDS_ATTR, ()))


def scoped_fields_from_module(module_name: str) -> frozenset[str]:
    """Discover scoped fields by importing the plugin's module.

    iPOPO decorators wrap the class in place, so scanning ``vars(module)``
    for a type carrying SCOPED_FIELDS_ATTR finds the decorated class.
    """
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return frozenset()
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == module_name:
            fields = getattr(value, SCOPED_FIELDS_ATTR, None)
            if fields is not None:
                return frozenset(fields)
    return frozenset()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_scoped_dependencies.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/scoped_dependencies.py tests/test_scoped_dependencies.py
git commit -m "feat(plugin): ScopedDependencies declaration for automatic scope filters"
```

---

## Task 7: PluginManager 骨架 — start/stop/discover/scope API

**Files:**
- Rewrite: `src/langharness_plugin/plugin_manager.py`(本任务先落地骨架,定义/实例生命周期在 Task 8/9/10 补齐——执行时建议按本任务代码整体替换文件,后续任务增量修改)
- Test: `tests/test_plugin_manager_unit.py`(重写)

**Interfaces:**
- Consumes: Task 1 errors、Task 2 数据类、Task 4 `descriptor_discovery`、Task 6 模块内省、现有 `PluginScopePolicy`/`scope_const`/`validation`/`langharness_scope`
- Produces:
  - `PluginManager(registry=None, *, scope_tree=None, discovery=None)` — 内存结构:`_discovered: dict[tuple[str,str], PluginDescriptor]`、`_plugins: dict[tuple[str,str], PluginDefinitionSnapshot]`、`_bundles: dict[str, Bundle]`、`_module_refs: dict[str, set[tuple[str,str]]]`、`_instances: dict[str, PluginInstanceSnapshot]`、`_registration_instances: dict[tuple[str,str,ScopeId], set[str]]`、`_sources: dict[tuple[str,str], DescriptorSource]`、`_provenance: dict[str, PersistedPluginRegistration]`、`_user_properties: dict[str, dict]`、`_state_store/_history/_state_version`
  - `start()`/`stop()`/`started` — 保持现有框架创建、服务注册与 builtin scope 播种
  - `discover() -> DiscoverySnapshot` — 类式发现、替换 `_discovered`
  - `list_scope() -> tuple[Scope, ...]`、`add_scope(scope_id, *, name, parent_id=None) -> Scope`、`remove_scope(scope_id, *, recursive=False) -> None`(非递归且有实例 → `ScopeHasInstancesError`,有子 scope → `ScopeHasChildrenError`;递归先删实例再删 scope)
  - `ensure_scope` 移除,调用方全部迁移到 `add_scope`

- [ ] **Step 1: 写失败测试**(替换 `tests/test_plugin_manager_unit.py`)

```python
"""Unit tests for PluginManager start, discovery, and scope APIs."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from importlib.metadata import EntryPoint
from unittest.mock import Mock

import pytest

from langharness_plugin.errors import ScopeHasChildrenError, ScopeHasInstancesError
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry, plugin_metadata
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Test plugin. Implements test.spec. Use in unit tests only. "
    "No properties. Uninstall when tests finish."
)


@plugin_metadata(
    name="echo",
    version="1.0.0",
    factory="echo-factory",
    specification="test.echo",
    description=DESCRIPTION,
)
class Echo:
    pass


def manager_with(entry_points=()) -> PluginManager:
    manager = PluginManager(
        PluginRegistry(),
        discovery=lambda: [EntryPoint(item, f"ep-{i}", "langharness.plugins")
                           for i, item in enumerate(entry_points)],
    )
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    return manager


class TestLifecycle:
    def test_not_started_errors_and_stop_is_noop(self) -> None:
        manager = PluginManager(PluginRegistry())
        assert manager.started is False
        manager.stop()
        with pytest.raises(RuntimeError, match="not started"):
            manager.discover()

    def test_start_twice_raises(self) -> None:
        manager = PluginManager(PluginRegistry())
        manager._framework = Mock()
        with pytest.raises(RuntimeError, match="already started"):
            manager.start()


class TestDiscovery:
    def test_discover_builds_catalog_without_installing(self) -> None:
        manager = manager_with([Echo])
        snapshot = manager.discover()
        assert len(snapshot.descriptors) == 1
        assert snapshot.descriptors[0].factory == "echo-factory"
        assert manager._discovered == {(Echo.__module__, "echo-factory"): snapshot.descriptors[0]}
        manager._context.install_bundle.assert_not_called()

    def test_rediscover_replaces_catalog(self) -> None:
        manager = manager_with([Echo])
        manager.discover()
        manager.discover()
        assert len(manager._discovered) == 1

    def test_discovery_warnings_surface_in_snapshot(self) -> None:
        manager = manager_with([42])
        snapshot = manager.discover()
        assert snapshot.descriptors == ()
        assert snapshot.warnings


class TestScopes:
    def test_builtin_scopes_are_seeded_on_real_start(self) -> None:
        manager = PluginManager(PluginRegistry())
        manager.start()
        try:
            scopes = manager.list_scope()
            assert {scope.id for scope in scopes} >= {
                ROOT_SCOPE_ID, ScopeId("ui"), ScopeId("server"), ScopeId("agent")
            }
        finally:
            manager.stop()

    def test_add_scope_requires_existing_parent(self) -> None:
        manager = manager_with()
        with pytest.raises(Exception):
            manager.add_scope(ScopeId("agent:a"), name="A",
                              parent_id=ScopeId("missing"))
        manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))
        assert manager.scope_tree.get(ScopeId("agent:a")) is not None

    def test_remove_scope_rejects_children_and_instances(self) -> None:
        manager = manager_with()
        manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))
        with pytest.raises(ScopeHasChildrenError):
            manager.remove_scope(ScopeId("agent"))

        manager._instances["uuid-1"] = Mock(scope_id=ScopeId("agent:a"))
        manager._registration_instances[("m", "f", ScopeId("agent:a"))] = {"uuid-1"}
        with pytest.raises(ScopeHasInstancesError):
            manager.remove_scope(ScopeId("agent:a"))

    def test_remove_scope_recursive_deletes_instances(self) -> None:
        manager = manager_with()
        manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))
        instance = Mock(spec=["instance", "scope_id"])
        instance.instance = "uuid-1"
        instance.scope_id = ScopeId("agent:a")
        manager._instances["uuid-1"] = instance
        manager._ipopo.kill.return_value = None
        manager.remove_scope(ScopeId("agent:a"), recursive=True)
        assert manager._instances == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_unit.py -v`
Expected: FAIL(`discover`/`list_scope`/`add_scope` 不存在)

- [ ] **Step 3: 实现骨架**(plugin_manager.py 整体重写;Task 8/9 会继续加方法,本任务先含完整框架)

```python
"""Lifecycle manager for Pelix/iPOPO plugin bundles."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from pelix import ldapfilter
from pelix.framework import BundleContext, Framework, FrameworkFactory, create_framework
from pelix.ipopo.constants import SERVICE_IPOPO

from langharness_plugin.config_store import apply_overrides  # Task 12 替换为 merge_overrides
from langharness_plugin.contracts import PluginRegistrar, ScopedPluginRegistrar
from langharness_plugin.discovery import _installed_entry_points, descriptor_discovery
from langharness_plugin.errors import (
    PluginAlreadyInstalledError,
    PluginHasInstancesError,
    PluginIdentityConflictError,
    PluginNotFoundError,
    ScopeHasChildrenError,
    ScopeHasInstancesError,
)
from langharness_plugin.registry import (
    DescriptorSource,
    DiscoverySnapshot,
    PluginDefinitionSnapshot,
    PluginDescriptor,
    PluginInstanceRecord,
    PluginInstanceSnapshot,
    PluginRegistry,
    validate_descriptor,
)
from langharness_plugin.scope_const import (
    BUILTIN_SCOPES,
    PLUGIN_KEY,
    PLUGIN_SCOPE_CHAIN,
    PLUGIN_SCOPE_ID,
)
from langharness_plugin.scope_policy import PluginScopePolicy
from langharness_plugin.state_store import (
    HistoryEntry,
    PersistedPluginRegistration,
    PluginHistoryStore,
    RuntimeStateSnapshot,
    RuntimeStateStore,
)
from langharness_plugin.validation import ContractViolationError, contract_for, validate
from langharness_scope import ROOT_SCOPE_ID, Scope, ScopeId, ScopeTree

FILTERS_PROPERTY = "requires.filters"
SERVICE_RANKING = "service.ranking"
SCOPE_RANKING_STRIDE = 1_000_000
PLUGIN_RANKING = "plugin.ranking"
PLUGIN_INSTANCE_ID = "plugin.instance_id"

_RUNTIME_KEYS = (
    PLUGIN_SCOPE_ID,
    PLUGIN_SCOPE_CHAIN,
    PLUGIN_KEY,
    PLUGIN_INSTANCE_ID,
    PLUGIN_RANKING,
    SERVICE_RANKING,
)


def _validate_filters(properties: dict[str, Any]) -> None:
    """Reject malformed requires.filters: iPOPO silently ignores them."""
    filters = properties.get(FILTERS_PROPERTY)
    if filters is None:
        return
    if not isinstance(filters, dict):
        raise ValueError(f"{FILTERS_PROPERTY} must be a mapping of field to filter")
    for field, value in filters.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Invalid filter for {field}: {value!r}")
        try:
            ldapfilter.get_ldap_filter(value)
        except ValueError as exc:
            raise ValueError(f"Invalid filter for {field}: {value!r}") from exc


class PluginManager:
    """Discovers, installs, instantiates, and persists plugin components."""

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        *,
        scope_tree: ScopeTree | None = None,
        discovery: Callable[[], Iterable[Any]] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else PluginRegistry()
        self.scope_tree = scope_tree if scope_tree is not None else ScopeTree()
        self._scope_policy = PluginScopePolicy(self.scope_tree)
        self._discovery_loader = discovery or _installed_entry_points
        self._lock = RLock()
        self._framework: Framework | None = None
        self._context: BundleContext | None = None
        self._ipopo: Any = None
        self._discovered: dict[tuple[str, str], PluginDescriptor] = {}
        self._plugins: dict[tuple[str, str], PluginDefinitionSnapshot] = {}
        self._bundles: dict[str, Any] = {}
        self._module_refs: dict[str, set[tuple[str, str]]] = {}
        self._instances: dict[str, PluginInstanceSnapshot] = {}
        self._registration_instances: dict[tuple[str, str, ScopeId], set[str]] = {}
        self._sources: dict[tuple[str, str], DescriptorSource] = {}
        self._provenance: dict[str, PersistedPluginRegistration] = {}
        self._user_properties: dict[str, dict[str, Any]] = {}
        self._state_store: RuntimeStateStore | None = None
        self._history: PluginHistoryStore | None = None
        self._state_version = 0
        self._registration: Any = None
        self._scope_registration: Any = None
        self._dynamic_registration: Any = None

    @property
    def started(self) -> bool:
        return self._framework is not None

    def start(self) -> None:
        if self.started:
            raise RuntimeError("PluginManager is already started")
        self._framework = create_framework(["pelix.ipopo.core"])
        self._framework.start()
        self._context = self._framework.get_bundle_context()
        ipopo_reference: Any = self._context.get_service_reference(SERVICE_IPOPO)
        assert ipopo_reference is not None
        self._ipopo = self._context.get_service(ipopo_reference)
        self._registration = self._context.register_service(PluginRegistrar, self, {})
        self._scope_registration = self._context.register_service(
            ScopedPluginRegistrar, self, {}
        )
        try:
            self._seed_builtin_scopes()
        except Exception:
            self.stop()
            raise

    def _seed_builtin_scopes(self) -> None:
        for scope_id, name, parent_id in BUILTIN_SCOPES:
            self.add_scope(scope_id, name=name, parent_id=parent_id)

    def stop(self) -> None:
        if self._framework is None:
            return
        FrameworkFactory.delete_framework(self._framework)
        self._framework = None
        self._context = None
        self._ipopo = None
        self._registration = None
        self._scope_registration = None
        self._dynamic_registration = None
        self._bundles.clear()
        self._module_refs.clear()
        self._instances.clear()
        self._registration_instances.clear()
        self._plugins.clear()
        self._discovered.clear()
        self._provenance.clear()
        self._user_properties.clear()

    def _require_started(self) -> None:
        if not self.started or self._context is None or self._ipopo is None:
            raise RuntimeError("PluginManager is not started")

    # ------------------------------------------------------------------ discovery

    def discover(self) -> DiscoverySnapshot:
        self._require_started()
        descriptors, warnings = descriptor_discovery(self._discovery_loader)
        self._discovered = descriptors
        return DiscoverySnapshot(
            tuple(
                sorted(descriptors.values(), key=lambda d: (d.module, d.factory))
            ),
            warnings,
            datetime.now(UTC),
        )

    # --------------------------------------------------------------------- scopes

    def list_scope(self) -> tuple[Scope, ...]:
        return self.scope_tree.snapshot().scopes

    def add_scope(
        self,
        scope_id: ScopeId,
        *,
        name: str,
        parent_id: ScopeId | None = None,
    ) -> Scope:
        wanted_parent = parent_id if parent_id is not None else ROOT_SCOPE_ID
        existing = self.scope_tree.get(scope_id)
        if existing is None:
            return self.scope_tree.create(scope_id, name, wanted_parent)
        if existing.parent_id != wanted_parent:
            raise ValueError(f"Scope {scope_id!r} already has a different parent")
        return existing

    def remove_scope(self, scope_id: ScopeId, *, recursive: bool = False) -> None:
        with self._lock:
            targets = {scope_id}
            if recursive:
                targets.update(item.id for item in self.scope_tree.descendants(scope_id))
            affected = [
                instance for instance in self._instances.values()
                if instance.scope_id in targets
            ]
            if not recursive:
                children = self.scope_tree.children(scope_id)
                if children:
                    raise ScopeHasChildrenError(
                        f"Scope {scope_id!r} has child scopes: "
                        f"{[str(item.id) for item in children]}"
                    )
                if affected:
                    raise ScopeHasInstancesError(
                        f"Scope {scope_id!r} has plugin instances"
                    )
            for instance in reversed(affected):
                self.delete_instance(instance.instance)
            self.scope_tree.remove(scope_id, recursive=recursive)

    # (Task 8 增加 definition 生命周期;Task 9 增加 instance 生命周期;
    #  Task 10 增加 bind_state/restore/_persist_state)

    # ------------------------------------------------------------ service helpers

    def scope_filter(self, scope_id: ScopeId) -> str:
        return self._scope_policy.visibility_filter(scope_id)

    def register_runtime_service(self, specification: type[Any], service: Any) -> None:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        if self._dynamic_registration is not None:
            raise RuntimeError("Dynamic plugin manager is already registered")
        self._dynamic_registration = self._context.register_service(
            specification,
            service,
            {PLUGIN_SCOPE_ID: str(ROOT_SCOPE_ID), PLUGIN_KEY: "dynamic-plugin-manager"},
        )

    def installed_modules(self) -> set[str]:
        return set(self._bundles)

    def find_service(
        self, specification: str, filter: str | None = None
    ) -> Any | None:
        """Return the highest-ranked service matching specification and filter."""
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(
            specification, filter
        ) or []
        if not references:
            return None
        reference = max(
            references,
            key=lambda item: int(item.get_property(SERVICE_RANKING) or 0),
        )
        return self._context.get_service(reference)

    def find_services(
        self, specification: str, filter: str | None = None
    ) -> list[Any]:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(
            specification, filter
        ) or []
        return [self._context.get_service(reference) for reference in references]

    def get_service(self, specification: str, filter: str | None = None) -> Any | None:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        reference: Any = self._context.get_service_reference(specification, filter)
        if reference is None:
            return None
        return self._context.get_service(reference)

    def get_services(self, specification: str) -> list[Any]:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(specification) or []
        return [self._context.get_service(reference) for reference in references]

    def service_properties(self, specification: str) -> list[dict[str, Any]]:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(specification) or []
        return [dict(reference.get_properties()) for reference in references]
```

注意:`scope_const.py` 增加 `PLUGIN_INSTANCE_ID = "plugin.instance_id"` 并导出(本任务一并修改)。`ScopedPluginRegistrar` 协议(Task 12)更新前,`start()` 的协议注册会在类型检查时报错——Task 12 前可临时保持旧协议名,执行时若 mypy 报错,先跑 pytest。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_unit.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/plugin_manager.py src/langharness_plugin/scope_const.py tests/test_plugin_manager_unit.py
git commit -m "feat(plugin): manager scaffold with discovery and scope APIs"
```

---

## Task 8: 定义生命周期 — install/uninstall/list/show

**Files:**
- Modify: `src/langharness_plugin/plugin_manager.py`
- Test: `tests/test_plugin_manager_unit.py`(追加)

**Interfaces:**
- Produces:
  - `install_descriptor(descriptor, *, source="assembly") -> PluginDefinitionSnapshot` — 直接安装定义(装配/协调器路径);factory 已被其他 module 注册 → `PluginIdentityConflictError`;重复 → `PluginAlreadyInstalledError`;bundle 按 module 去重共享
  - `install_plugin(factory, *, module=None) -> PluginDefinitionSnapshot` — 从 `_discovered` 解析;找不到 → `PluginNotFoundError`;多 module 同名 factory 且未给 module → `PluginIdentityConflictError`
  - `uninstall_plugin(factory, *, module=None) -> None` — 有实例 → `PluginHasInstancesError`;module 引用清空才卸载 bundle
  - `list_plugin() -> tuple[PluginDefinitionSnapshot, ...]`、`show_plugin(factory, *, module=None) -> PluginDefinitionSnapshot`
  - 私有 `_install_bundle(module)`、`_unload_bundle_if_unused(module)`、`_record_history(action, **fields)`

- [ ] **Step 1: 写失败测试**(追加到 unit 测试文件)

```python
class TestDefinitions:
    def make_definition_manager(self) -> tuple[PluginManager, Any]:
        manager = manager_with([Echo])
        manager.discover()
        manager._context.install_bundle.return_value = Mock()
        return manager, manager._context.install_bundle.return_value

    def test_install_plugin_from_discovery(self) -> None:
        manager, bundle = self.make_definition_manager()
        snapshot = manager.install_plugin("echo-factory")
        assert snapshot.descriptor.factory == "echo-factory"
        assert snapshot.installed is True
        bundle.start.assert_called_once()
        assert manager.registry.get("echo-factory") is not None

    def test_install_plugin_unknown_factory_raises(self) -> None:
        manager, _ = self.make_definition_manager()
        with pytest.raises(PluginNotFoundError, match="unknown-factory"):
            manager.install_plugin("unknown-factory")

    def test_install_descriptor_identity_conflict(self) -> None:
        manager, _ = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        other = replace_descriptor(
            manager.registry.get("echo-factory"), module="other.module"
        )
        with pytest.raises(PluginIdentityConflictError):
            manager.install_descriptor(other)

    def test_double_install_raises(self) -> None:
        manager, _ = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        with pytest.raises(PluginAlreadyInstalledError):
            manager.install_plugin("echo-factory")

    def test_bundle_shared_by_two_definitions_in_one_module(self) -> None:
        manager, bundle = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        second = replace_descriptor(
            manager.registry.get("echo-factory"), factory="echo2-factory"
        )
        manager.install_descriptor(second)
        assert manager._context.install_bundle.call_count == 1
        assert manager._bundles[Echo.__module__] is bundle

    def test_uninstall_with_instances_raises(self) -> None:
        manager, _ = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        manager._instances["uuid-1"] = Mock()
        manager._registration_instances[("m", "echo-factory", ROOT_SCOPE_ID)] = {"uuid-1"}
        with pytest.raises(PluginHasInstancesError):
            manager.uninstall_plugin("echo-factory")

    def test_uninstall_releases_bundle_when_module_unused(self) -> None:
        manager, bundle = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        manager.uninstall_plugin("echo-factory")
        bundle.stop.assert_called_once()
        bundle.uninstall.assert_called_once()
        assert manager._bundles == {}

    def test_list_and_show_plugin(self) -> None:
        manager, _ = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        assert len(manager.list_plugin()) == 1
        shown = manager.show_plugin("echo-factory")
        assert shown.descriptor.name == "echo"
        assert shown.instance_count == 0
        with pytest.raises(PluginNotFoundError):
            manager.show_plugin("missing-factory")
```

(辅助函数 `replace_descriptor`/`PLUGIN_METADATA_NAME` 用 dataclasses.replace 与 registry 常量;测试文件顶部 import `from dataclasses import replace` 并定义 `replace_descriptor = lambda d, **kw: replace(d, **kw)`。)

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_unit.py -v -k TestDefinitions`
Expected: FAIL(`install_plugin` 不存在)

- [ ] **Step 3: 实现**(在 PluginManager 内追加)

```python
    # ------------------------------------------------------------- definitions

    def install_descriptor(
        self,
        descriptor: PluginDescriptor,
        *,
        source: DescriptorSource = "assembly",
    ) -> PluginDefinitionSnapshot:
        with self._lock:
            self._require_started()
            return self._install_definition(descriptor, source=source)

    def _install_definition(
        self,
        descriptor: PluginDescriptor,
        *,
        source: DescriptorSource,
        persist: bool = True,
    ) -> PluginDefinitionSnapshot:
        validate_descriptor(descriptor)
        key = (descriptor.module, descriptor.factory)
        if key in self._plugins:
            raise PluginAlreadyInstalledError(
                f"Plugin definition {descriptor.factory!r} from "
                f"{descriptor.module!r} is already installed"
            )
        existing = self.registry.get(descriptor.factory)
        if existing is not None and existing.module != descriptor.module:
            raise PluginIdentityConflictError(
                f"Factory {descriptor.factory!r} is already registered by "
                f"module {existing.module!r}; cannot install from "
                f"{descriptor.module!r}"
            )
        bundle = self._install_bundle(descriptor.module)
        self._module_refs.setdefault(descriptor.module, set()).add(key)
        self.registry.add(descriptor)
        self._sources[key] = source
        snapshot = PluginDefinitionSnapshot(
            descriptor, True, descriptor.module, 0, ()
        )
        self._plugins[key] = snapshot
        if persist:
            try:
                self._persist_state()
            except Exception:
                self._rollback_definition(key, bundle)
                raise
            self._record_history(
                "install_definition", factory=descriptor.factory,
                module=descriptor.module,
            )
        return snapshot

    def _install_bundle(self, module: str) -> Any:
        bundle = self._bundles.get(module)
        if bundle is not None:
            return bundle
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        bundle = self._context.install_bundle(module)
        bundle.start()
        self._bundles[module] = bundle
        return bundle

    def _rollback_definition(self, key: tuple[str, str], bundle: Any) -> None:
        self._plugins.pop(key, None)
        self._sources.pop(key, None)
        self.registry.remove(key[1])
        self._module_refs.get(key[0], set()).discard(key)
        self._unload_bundle_if_unused(key[0], _rollback_bundle=bundle)

    def _unload_bundle_if_unused(
        self, module: str, *, _rollback_bundle: Any | None = None
    ) -> None:
        refs = self._module_refs.get(module, set())
        if refs:
            return
        bundle = self._bundles.pop(module, None) or _rollback_bundle
        if bundle is None:
            return
        try:
            bundle.stop()
            bundle.uninstall()
        except Exception:
            pass

    def install_plugin(
        self, factory: str, *, module: str | None = None
    ) -> PluginDefinitionSnapshot:
        with self._lock:
            self._require_started()
            matches = [
                descriptor
                for descriptor in self._discovered.values()
                if descriptor.factory == factory
            ]
            if not matches:
                raise PluginNotFoundError(
                    f"Plugin factory {factory!r} is not discovered"
                )
            if module is not None:
                matches = [
                    descriptor for descriptor in matches if descriptor.module == module
                ]
                if not matches:
                    raise PluginIdentityConflictError(
                        f"No discovered plugin with factory {factory!r} "
                        f"in module {module!r}"
                    )
            if len(matches) > 1:
                raise PluginIdentityConflictError(
                    f"Factory {factory!r} is discovered in multiple modules: "
                    f"{sorted(item.module for item in matches)}; pass module="
                )
            return self._install_definition(matches[0], source="discovered")

    def uninstall_plugin(self, factory: str, *, module: str | None = None) -> None:
        with self._lock:
            self._require_started()
            key = self._definition_key(factory, module)
            affected = [
                snapshot
                for snapshot in self._instances.values()
                if (snapshot.module, snapshot.factory) == key
            ]
            if affected:
                raise PluginHasInstancesError(
                    f"Plugin {factory!r} still has {len(affected)} instance(s); "
                    "delete them first"
                )
            self.registry.remove(factory)
            self._plugins.pop(key)
            self._sources.pop(key, None)
            self._module_refs.get(key[0], set()).discard(key)
            self._unload_bundle_if_unused(key[0])
            self._persist_state()
            self._record_history("uninstall_definition", factory=factory,
                                module=key[0])

    def _definition_key(
        self, factory: str, module: str | None
    ) -> tuple[str, str]:
        definition = self.registry.get(factory)
        if definition is None:
            raise PluginNotFoundError(f"Plugin factory {factory!r} is not installed")
        if module is not None and definition.module != module:
            raise PluginIdentityConflictError(
                f"Factory {factory!r} is installed from {definition.module!r}, "
                f"not {module!r}"
            )
        return (definition.module, factory)

    def list_plugin(self) -> tuple[PluginDefinitionSnapshot, ...]:
        return tuple(
            sorted(self._plugins.values(), key=lambda item: item.descriptor.factory)
        )

    def show_plugin(
        self, factory: str, *, module: str | None = None
    ) -> PluginDefinitionSnapshot:
        key = self._definition_key(factory, module)
        snapshot = self._plugins.get(key)
        if snapshot is None:
            raise PluginNotFoundError(f"Plugin factory {factory!r} is not installed")
        return snapshot
```

注:`_persist_state`/`_record_history` 在 Task 10 落地;本任务先加两个占位方法(空实现,store 未绑定时直接 return),Task 10 替换为真实实现。`_definition_key` 对已发现但未安装的 factory 抛 `PluginNotFoundError`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_unit.py -v -k TestDefinitions`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/plugin_manager.py tests/test_plugin_manager_unit.py
git commit -m "feat(plugin): definition lifecycle with shared module bundles"
```

---

## Task 9: 实例生命周期 — create/get/update/delete/list

**Files:**
- Modify: `src/langharness_plugin/plugin_manager.py`
- Test: `tests/test_plugin_manager_scoped.py`(重写)

**Interfaces:**
- Produces:
  - `create_instance(factory, module, scope_id=None, *, properties=None, enabled=True, ranking=0) -> PluginInstanceSnapshot` — scope 缺省 root、校验存在;definition 必须已安装;UUID 生成;运行时属性注入(`plugin.scope_id/scope_chain/key/instance_id/ranking/service.ranking`);scoped filter 自动合并(AND 用户 filter,canonical 形式:scope filter 打头);filter 校验;contract 校验;事务回滚
  - `get_instance(instance)`、`delete_instance(instance)`、`list_instance(*, factory=None, module=None, scope_id=None, enabled=None)`
  - `update_instance(instance, *, properties=None, enabled=None, ranking=None)` — hot: `ipopo.reconfigure`;restart/ranking/enabled: kill + 同 UUID 重建;失败恢复旧组件与旧快照
  - `ensure_instance(factory, module, scope_id, *, properties, enabled)` — 装配幂等:存在活动实例则 reconcile(相等跳过),否则创建

- [ ] **Step 1: 写失败测试**(替换 `tests/test_plugin_manager_scoped.py`)

```python
"""Tests for scoped instance lifecycle on the rewritten PluginManager."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from unittest.mock import Mock

import pytest

from langharness_plugin.errors import (
    InstanceNotFoundError,
    InstanceStateError,
    PluginNotFoundError,
)
from langharness_plugin.plugin_manager import (
    FILTERS_PROPERTY,
    PLUGIN_INSTANCE_ID,
    PLUGIN_KEY,
    PLUGIN_SCOPE_CHAIN,
    PLUGIN_SCOPE_ID,
    PluginManager,
)
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scoped_dependencies import scoped_fields_from_module
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Test plugin. Implements test.spec. Use in scoped tests only. "
    "Properties: plugin.mode. Uninstall when tests finish."
)


def descriptor(swap_policy="hot", **overrides) -> PluginDescriptor:
    fields = dict(
        name="test-plugin",
        version="1.0.0",
        module="langharness_core.test_module",
        factory="test-factory",
        specification="test.spec",
        description=DESCRIPTION,
        swap_policy=swap_policy,
    )
    fields.update(overrides)
    return PluginDescriptor(**fields)


def started_manager() -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    return manager


def installed_manager() -> PluginManager:
    manager = started_manager()
    manager._context.install_bundle.return_value = Mock()
    manager.install_descriptor(descriptor())
    manager._ipopo.instantiate.return_value = Mock()
    return manager


class TestCreateInstance:
    def test_create_generates_uuid_and_returns_snapshot(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        assert len(snapshot.instance) == 32
        assert snapshot.factory == "test-factory"
        assert snapshot.scope_id == ScopeId("server")
        assert snapshot.status == "active"
        assert manager.get_instance(snapshot.instance) is snapshot

    def test_create_defaults_to_root_scope(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module"
        )
        assert snapshot.scope_id == ROOT_SCOPE_ID

    def test_create_rejects_unknown_scope(self) -> None:
        manager = installed_manager()
        with pytest.raises(Exception):
            manager.create_instance(
                "test-factory", "langharness_core.test_module", ScopeId("missing")
            )

    def test_create_rejects_uninstalled_definition(self) -> None:
        manager = started_manager()
        with pytest.raises(PluginNotFoundError):
            manager.create_instance("test-factory", "langharness_core.test_module")

    def test_injected_runtime_properties(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={"plugin.mode": "fast"}, ranking=2,
        )
        properties = dict(snapshot.properties)
        assert properties[PLUGIN_SCOPE_ID] == "server"
        assert properties[PLUGIN_KEY] == "test-factory"
        assert properties[PLUGIN_INSTANCE_ID] == snapshot.instance
        assert properties["plugin.ranking"] == 2
        assert properties["service.ranking"] > 0
        assert properties[PLUGIN_SCOPE_CHAIN] == ["server", "root"]
        assert properties["plugin.mode"] == "fast"

    def test_multiple_instances_same_key(self) -> None:
        manager = installed_manager()
        first = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        second = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        assert first.instance != second.instance
        assert len(manager.list_instance()) == 2

    def test_disabled_instance_not_instantiated(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            enabled=False,
        )
        manager._ipopo.instantiate.assert_not_called()
        assert snapshot.status == "disabled"
        assert snapshot.enabled is False

    def test_contract_violation_kills_component(self) -> None:
        manager = installed_manager()
        import langharness_plugin.plugin_manager as module
        module.contract_for = lambda spec: Mock(__name__="P")
        module.validate = lambda instance, protocol: ["missing method"]
        with pytest.raises(Exception):
            manager.create_instance(
                "test-factory", "langharness_core.test_module", ScopeId("server")
            )
        manager._ipopo.kill.assert_called_once()


class TestUpdateInstance:
    def test_update_properties_hot_reconfigures_in_place(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={"plugin.mode": "fast"},
        )
        updated = manager.update_instance(
            snapshot.instance, properties={"plugin.mode": "slow"}
        )
        manager._ipopo.reconfigure.assert_called_once()
        assert updated.instance == snapshot.instance  # UUID preserved
        assert updated.properties["plugin.mode"] == "slow"

    def test_update_properties_restart_kills_and_recreates_same_uuid(self) -> None:
        manager = installed_manager()
        manager._plugins[("langharness_core.test_module", "test-factory")] = Mock(
            descriptor=descriptor(swap_policy="restart")
        )
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        manager._ipopo.instantiate.reset_mock()
        updated = manager.update_instance(
            snapshot.instance, properties={"plugin.mode": "slow"}
        )
        manager._ipopo.kill.assert_called_once_with(snapshot.instance)
        manager._ipopo.instantiate.assert_called_once()
        assert updated.instance == snapshot.instance

    def test_update_rejects_unknown_instance(self) -> None:
        manager = installed_manager()
        with pytest.raises(InstanceNotFoundError):
            manager.update_instance("no-such-uuid", properties={})

    def test_update_failure_restores_old_state(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={"plugin.mode": "fast"},
        )
        manager._ipopo.reconfigure.side_effect = RuntimeError("boom")
        manager._ipopo.instantiate.return_value = Mock()
        with pytest.raises(RuntimeError, match="boom"):
            manager.update_instance(
                snapshot.instance, properties={"plugin.mode": "slow"}
            )
        restored = manager.get_instance(snapshot.instance)
        assert restored.properties["plugin.mode"] == "fast"

    def test_disable_then_enable_reuses_uuid(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        disabled = manager.update_instance(snapshot.instance, enabled=False)
        assert disabled.status == "disabled"
        manager._ipopo.kill.assert_called_with(snapshot.instance)
        enabled = manager.update_instance(snapshot.instance, enabled=True)
        assert enabled.instance == snapshot.instance
        assert enabled.status == "active"


class TestDeleteAndList:
    def test_delete_instance_removes_record_and_kills(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        manager.delete_instance(snapshot.instance)
        manager._ipopo.kill.assert_called_with(snapshot.instance)
        with pytest.raises(InstanceNotFoundError):
            manager.get_instance(snapshot.instance)

    def test_list_instance_filters(self) -> None:
        manager = installed_manager()
        first = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        manager.create_instance(
            "test-factory", "langharness_core.test_module", ROOT_SCOPE_ID
        )
        assert len(manager.list_instance(scope_id=ScopeId("server"))) == 1
        assert len(manager.list_instance(scope_id=ROOT_SCOPE_ID)) == 1
        assert manager.list_instance(scope_id=ScopeId("server"))[0].instance == first.instance

    def test_scoped_fields_introspection_hits_agent_loop(self) -> None:
        fields = scoped_fields_from_module("langharness_core.plugins.loop.agent_loop")
        assert "_llm_provider" in fields
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_scoped.py -v`
Expected: FAIL(`create_instance` 不存在;最后一条测试要求 Task 13 的 `@ScopedDependencies` 落在 AgentLoop 上——本任务先注释掉该条,Task 13 启用)

- [ ] **Step 3: 实现**(在 PluginManager 内追加;`contract_for`/`validate` 顶层引用,便于测试 monkeypatch)

```python
    # -------------------------------------------------------------- instances

    def create_instance(
        self,
        factory: str,
        module: str,
        scope_id: ScopeId | None = None,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool = True,
        ranking: int = 0,
    ) -> PluginInstanceSnapshot:
        with self._lock:
            self._require_started()
            scope_id = scope_id if scope_id is not None else ROOT_SCOPE_ID
            self.scope_tree.require(scope_id)
            key = (module, factory)
            definition = self._plugins.get(key)
            if definition is None:
                raise PluginNotFoundError(
                    f"Plugin definition {factory!r} from {module!r} is not installed"
                )
            user = dict(properties or {})
            instance_uuid = uuid.uuid4().hex
            effective = self._effective_properties(
                definition.descriptor, scope_id, user,
                ranking=ranking, instance_uuid=instance_uuid,
            )
            if enabled:
                self._instantiate_uuid(definition.descriptor, instance_uuid, effective)
                status: InstanceStatus = "active"
            else:
                status = "disabled"
            snapshot = PluginInstanceSnapshot(
                instance_uuid, factory, module, scope_id, effective,
                enabled, ranking, status,
            )
            self._instances[instance_uuid] = snapshot
            self._user_properties[instance_uuid] = user
            registration_key = (module, factory, scope_id)
            self._registration_instances.setdefault(
                registration_key, set()
            ).add(instance_uuid)
            try:
                self._persist_state()
            except Exception:
                if enabled:
                    self._ipopo.kill(instance_uuid)
                self._instances.pop(instance_uuid, None)
                self._user_properties.pop(instance_uuid, None)
                self._registration_instances.get(
                    registration_key, set()
                ).discard(instance_uuid)
                raise
            self._record_history(
                "create_instance", factory=factory, module=module,
                scope_id=str(scope_id), instance=instance_uuid,
            )
            return snapshot

    def ensure_instance(
        self,
        factory: str,
        module: str,
        scope_id: ScopeId | None,
        *,
        properties: Mapping[str, Any] | None,
        enabled: bool,
    ) -> PluginInstanceSnapshot:
        """Idempotent assembly helper: reconcile-or-create one instance."""
        scope_id = scope_id if scope_id is not None else ROOT_SCOPE_ID
        for snapshot in self._instances.values():
            if (
                snapshot.factory == factory
                and snapshot.module == module
                and snapshot.scope_id == scope_id
            ):
                user = self._user_properties.get(snapshot.instance, {})
                if snapshot.enabled != enabled or user != dict(properties or {}):
                    return self.update_instance(
                        snapshot.instance,
                        properties=dict(properties or {}) or None,
                        enabled=enabled,
                    )
                return snapshot
        return self.create_instance(
            factory, module, scope_id, properties=properties, enabled=enabled
        )

    def get_instance(self, instance: str) -> PluginInstanceSnapshot:
        snapshot = self._instances.get(instance)
        if snapshot is None:
            raise InstanceNotFoundError(instance)
        return snapshot

    def delete_instance(self, instance: str) -> None:
        with self._lock:
            self._require_started()
            snapshot = self._instances.get(instance)
            if snapshot is None:
                raise InstanceNotFoundError(instance)
            if snapshot.status == "active":
                self._ipopo.kill(instance)
            self._instances.pop(instance)
            self._user_properties.pop(instance, None)
            self._registration_instances.get(
                (snapshot.module, snapshot.factory, snapshot.scope_id), set()
            ).discard(instance)
            try:
                self._persist_state()
            except Exception:
                if snapshot.status == "active":
                    self._instantiate_uuid(
                        self._plugins[(snapshot.module, snapshot.factory)].descriptor,
                        instance,
                        dict(snapshot.properties),
                    )
                self._instances[instance] = snapshot
                self._registration_instances.setdefault(
                    (snapshot.module, snapshot.factory, snapshot.scope_id), set()
                ).add(instance)
                raise
            self._record_history(
                "delete_instance", factory=snapshot.factory,
                module=snapshot.module, scope_id=str(snapshot.scope_id),
                instance=instance,
            )

    def update_instance(
        self,
        instance: str,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        ranking: int | None = None,
    ) -> PluginInstanceSnapshot:
        with self._lock:
            self._require_started()
            current = self._instances.get(instance)
            if current is None:
                raise InstanceNotFoundError(instance)
            definition = self._plugins.get((current.module, current.factory))
            if definition is None:
                raise InstanceStateError(
                    f"Definition for instance {instance!r} is not installed"
                )
            descriptor = definition.descriptor
            user = dict(self._user_properties.get(instance, {}))
            if properties is not None:
                user.update(properties)
            new_enabled = current.enabled if enabled is None else enabled
            new_ranking = current.ranking if ranking is None else ranking
            effective = self._effective_properties(
                descriptor, current.scope_id, user, ranking=new_ranking
            )
            new_snapshot = PluginInstanceSnapshot(
                instance, current.factory, current.module, current.scope_id,
                effective, new_enabled, new_ranking,
                "active" if new_enabled else "disabled",
            )
            needs_rebind = (
                new_enabled != current.enabled
                or new_ranking != current.ranking
                or descriptor.swap_policy != "hot"
            )
            try:
                if not new_enabled:
                    if current.status == "active":
                        self._ipopo.kill(instance)
                elif current.status == "disabled":
                    self._instantiate_uuid(descriptor, instance, effective)
                elif needs_rebind:
                    self._ipopo.kill(instance)
                    self._instantiate_uuid(descriptor, instance, effective)
                else:
                    self._ipopo.reconfigure(instance, effective or None)
            except Exception:
                if current.status == "active" and new_enabled:
                    self._ipopo.kill(instance)
                    self._instantiate_uuid(
                        descriptor, instance, dict(current.properties)
                    )
                raise
            self._instances[instance] = new_snapshot
            self._user_properties[instance] = user
            try:
                self._persist_state()
            except Exception:
                self._instances[instance] = current
                raise
            self._record_history(
                "update_instance", factory=current.factory,
                module=current.module, scope_id=str(current.scope_id),
                instance=instance,
            )
            return new_snapshot

    def list_instance(
        self,
        *,
        factory: str | None = None,
        module: str | None = None,
        scope_id: ScopeId | None = None,
        enabled: bool | None = None,
    ) -> tuple[PluginInstanceSnapshot, ...]:
        result = [
            snapshot
            for snapshot in self._instances.values()
            if (factory is None or snapshot.factory == factory)
            and (module is None or snapshot.module == module)
            and (scope_id is None or snapshot.scope_id == scope_id)
            and (enabled is None or snapshot.enabled == enabled)
        ]
        return tuple(
            sorted(result, key=lambda item: (item.factory, str(item.scope_id), item.instance))
        )

    def _effective_properties(
        self,
        descriptor: PluginDescriptor,
        scope_id: ScopeId,
        user: dict[str, Any],
        *,
        ranking: int,
        instance_uuid: str | None = None,
    ) -> dict[str, Any]:
        effective = dict(user)
        effective[PLUGIN_SCOPE_ID] = str(scope_id)
        effective[PLUGIN_SCOPE_CHAIN] = [
            str(item) for item in self._scope_policy.visible_scopes(scope_id)
        ]
        effective[PLUGIN_KEY] = descriptor.factory
        if instance_uuid is not None:
            effective[PLUGIN_INSTANCE_ID] = instance_uuid
        effective[PLUGIN_RANKING] = ranking
        effective[SERVICE_RANKING] = (
            self.scope_tree.depth(scope_id) * SCOPE_RANKING_STRIDE + ranking
        )
        self._apply_scoped_filters(effective, descriptor, scope_id)
        _validate_filters(effective)
        return effective

    def _apply_scoped_filters(
        self,
        properties: dict[str, Any],
        descriptor: PluginDescriptor,
        scope_id: ScopeId,
    ) -> None:
        fields = scoped_fields_from_module(descriptor.module)
        if not fields:
            return
        filters = dict(properties.get(FILTERS_PROPERTY, {}))
        scope_filter = self._scope_policy.visibility_filter(scope_id)
        for field in fields:
            user = filters.get(field)
            if user and user.startswith(scope_filter):
                continue  # already in canonical form (scope filter first)
            filters[field] = (
                scope_filter if not user else f"(&{scope_filter}({user}))"
            )
        properties[FILTERS_PROPERTY] = filters

    def _instantiate_uuid(
        self,
        descriptor: PluginDescriptor,
        instance_uuid: str,
        properties: dict[str, Any],
    ) -> Any:
        instance = self._ipopo.instantiate(
            descriptor.factory, instance_uuid, properties or None
        )
        protocol = contract_for(descriptor.specification)
        if protocol is not None:
            violations = validate(instance, protocol)
            if violations:
                self._ipopo.kill(instance_uuid)
                raise ContractViolationError(
                    plugin=descriptor.name,
                    specification=descriptor.specification,
                    protocol=protocol.__name__,
                    violations=violations,
                )
        setattr(instance, "_plugin_scope_id", properties.get(PLUGIN_SCOPE_ID, ""))
        setattr(instance, "_plugin_key", properties.get(PLUGIN_KEY, ""))
        setattr(instance, "_plugin_ranking", int(properties.get(PLUGIN_RANKING, 0)))
        return instance
```

顶部 import 增加:`import uuid`、`from typing import Mapping`、`from langharness_plugin.errors import InstanceNotFoundError, InstanceStateError`、`from langharness_plugin.registry import InstanceStatus`、`from langharness_plugin.scoped_dependencies import scoped_fields_from_module`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_scoped.py -v -k "not scoped_fields_introspection"`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/plugin_manager.py tests/test_plugin_manager_scoped.py
git commit -m "feat(plugin): UUID-keyed instance lifecycle with hot/restart property updates"
```

---

## Task 10: 持久化集成与启动恢复

**Files:**
- Modify: `src/langharness_plugin/plugin_manager.py`
- Test: `tests/test_plugin_manager_unit.py`(追加)

**Interfaces:**
- Produces:
  - `bind_state(store: RuntimeStateStore, history: PluginHistoryStore) -> None`
  - `registrations() -> tuple[PersistedPluginRegistration, ...]`、`attach_provenance(registration)`、`detach_provenance(instance)`(供 coordinator)
  - `restore() -> tuple[PluginInstanceSnapshot, ...]` — 按设计文档顺序:加载 scope tree(调用方在 manager 创建前已由 ScopeTree 加载)→ 恢复 definitions(装配 source 状态 installed;discovered source 缺失 → missing、版本不同 → upgrade_available)→ 恢复 instance records(UUID、effective properties 重注入确定性运行时键、contract 校验;失败标 failed;definition 缺失标 missing)
  - 私有 `_persist_state()`/`_record_history()` 真实实现替换占位

- [ ] **Step 1: 写失败测试**(追加到 unit 测试文件)

```python
class TestPersistence:
    def test_bind_state_persists_after_mutation(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
        )

        manager, _ = TestDefinitions().make_definition_manager()
        store = InMemoryRuntimeStateStore()
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.install_plugin("echo-factory")
        loaded = store.load()
        assert loaded is not None
        assert loaded.descriptors[0].descriptor.factory == "echo-factory"
        assert loaded.descriptors[0].source == "discovered"

    def test_persistence_failure_rolls_back_definition(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            PluginStateConflictError,
            RuntimeStateSnapshot,
        )

        manager, bundle = TestDefinitions().make_definition_manager()
        store = InMemoryRuntimeStateStore()
        store.save(RuntimeStateSnapshot(0, (), (), (), ()), expected_version=0)
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager._state_version = 0  # stale version forces CAS conflict
        with pytest.raises(PluginStateConflictError):
            manager.install_plugin("echo-factory")
        assert manager.registry.get("echo-factory") is None
        bundle.stop.assert_called_once()

    def test_history_records_uninstall(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
        )

        manager, _ = TestDefinitions().make_definition_manager()
        history = InMemoryPluginHistoryStore()
        manager.bind_state(InMemoryRuntimeStateStore(), history)
        manager.install_plugin("echo-factory")
        manager.uninstall_plugin("echo-factory")
        actions = [entry.action for entry in history.entries()]
        assert "install_definition" in actions
        assert "uninstall_definition" in actions

    def test_restore_rebuilds_instances_with_persisted_uuid(self) -> None:
        from langharness_plugin.registry import PluginInstanceRecord
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            PersistedDescriptorRecord,
            RuntimeStateSnapshot,
        )

        manager, _ = TestDefinitions().make_definition_manager()
        store = InMemoryRuntimeStateStore()
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.install_plugin("echo-factory")
        manager._ipopo.instantiate.return_value = Mock()
        snapshot = manager.create_instance(
            "echo-factory", Echo.__module__, ScopeId("server"),
            properties={"plugin.mode": "fast"},
        )
        manager.stop_state_only_for_test = None

        second = manager_with([Echo])
        second.discover()
        second._context.install_bundle.return_value = Mock()
        second.bind_state(store, InMemoryPluginHistoryStore())
        second._ipopo.instantiate.return_value = Mock()
        restored = second.restore()
        assert [item.instance for item in restored] == [snapshot.instance]
        second._ipopo.instantiate.assert_called_once()
        _, instance_name, properties = second._ipopo.instantiate.call_args.args
        assert instance_name == snapshot.instance
        assert properties["plugin.scope_id"] == "server"
        assert properties["plugin.mode"] == "fast"

    def test_restore_marks_missing_definition_instance(self) -> None:
        from langharness_plugin.registry import PluginInstanceRecord
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            RuntimeStateSnapshot,
        )

        manager, _ = TestDefinitions().make_definition_manager()
        store = InMemoryRuntimeStateStore()
        record = PluginInstanceRecord(
            "uuid-x", "gone-factory", "gone.module", ScopeId("server"), {}
        )
        store.save(
            RuntimeStateSnapshot(0, (), (), (record,), ()), expected_version=0
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager._state_version = 0
        restored = manager.restore()
        assert len(restored) == 1
        assert restored[0].status == "missing"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_unit.py -v -k TestPersistence`
Expected: FAIL(`bind_state` 不存在)

- [ ] **Step 3: 实现**(替换 `_persist_state`/`_record_history` 占位并追加)

```python
    # ------------------------------------------------------------ persistence

    def bind_state(
        self, store: RuntimeStateStore, history: PluginHistoryStore
    ) -> None:
        self._state_store = store
        self._history = history

    def registrations(self) -> tuple[PersistedPluginRegistration, ...]:
        return tuple(self._provenance.values())

    def attach_provenance(self, registration: PersistedPluginRegistration) -> None:
        self._provenance[registration.instance] = registration

    def detach_provenance(self, instance: str) -> None:
        self._provenance.pop(instance, None)

    def _persist_state(self) -> None:
        if self._state_store is None:
            return
        scopes = self.scope_tree.snapshot().scopes
        descriptors = tuple(
            PersistedDescriptorRecord(descriptor, self._sources.get(
                (descriptor.module, descriptor.factory), "assembly"
            ))
            for descriptor in self.registry.list()
        )
        instances = tuple(
            PluginInstanceRecord(
                snapshot.instance, snapshot.factory, snapshot.module,
                snapshot.scope_id, dict(snapshot.properties),
                snapshot.enabled, snapshot.ranking, snapshot.status,
            )
            for snapshot in self._instances.values()
        )
        snapshot = RuntimeStateSnapshot(
            self._state_version,
            tuple(
                {
                    "id": str(scope.id),
                    "parent_id": (
                        str(scope.parent_id) if scope.parent_id is not None else None
                    ),
                    "name": scope.name,
                }
                for scope in scopes
            ),
            descriptors,
            instances,
            tuple(self._provenance.values()),
        )
        self._state_version = self._state_store.save(
            snapshot, expected_version=self._state_version
        )

    def _record_history(
        self,
        action: str,
        *,
        factory: str | None = None,
        module: str | None = None,
        scope_id: str | None = None,
        instance: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._history is None:
            return
        try:
            self._history.append(
                HistoryEntry(
                    datetime.now(UTC).isoformat(), action, factory, module,
                    scope_id, instance, dict(detail or {}),
                )
            )
        except Exception:
            LOGGER.warning("Could not append lifecycle history for %s", action)

    def restore(self) -> tuple[PluginInstanceSnapshot, ...]:
        """Rebuild persisted definitions and instances after a restart."""
        if self._state_store is None:
            return ()
        loaded = self._state_store.load()
        if loaded is None:
            return ()
        self._state_version = loaded.version
        for record in loaded.descriptors:
            descriptor = record.descriptor
            key = (descriptor.module, descriptor.factory)
            if key in self._plugins:
                continue
            if record.source == "assembly":
                self._install_definition(descriptor, source="assembly", persist=False)
                continue
            discovered = self._discovered.get(key)
            status: DefinitionStatus = "installed"
            if discovered is None:
                status = "missing"
            elif discovered.version != descriptor.version:
                status = "upgrade_available"
            self._install_definition(descriptor, source="discovered", persist=False)
            self._plugins[key] = replace(self._plugins[key], status=status)
        restored: list[PluginInstanceSnapshot] = []
        for record in loaded.instances:
            key = (record.module, record.factory)
            definition = self._plugins.get(key)
            if definition is None:
                snapshot = PluginInstanceSnapshot(
                    record.instance, record.factory, record.module,
                    record.scope_id, record.properties, record.enabled,
                    record.ranking, "missing",
                )
                self._instances[record.instance] = snapshot
                restored.append(snapshot)
                continue
            if not record.enabled:
                snapshot = PluginInstanceSnapshot(
                    record.instance, record.factory, record.module,
                    record.scope_id, record.properties, False,
                    record.ranking, "disabled",
                )
                self._instances[record.instance] = snapshot
                restored.append(snapshot)
                continue
            try:
                effective = self._rehydrate_properties(record, definition.descriptor)
                self._instantiate_uuid(
                    definition.descriptor, record.instance, effective
                )
                snapshot = PluginInstanceSnapshot(
                    record.instance, record.factory, record.module,
                    record.scope_id, effective, True, record.ranking, "active",
                )
            except Exception:
                snapshot = PluginInstanceSnapshot(
                    record.instance, record.factory, record.module,
                    record.scope_id, record.properties, record.enabled,
                    record.ranking, "failed",
                )
            self._instances[record.instance] = snapshot
            self._registration_instances.setdefault(
                (record.module, record.factory, record.scope_id), set()
            ).add(record.instance)
            restored.append(snapshot)
        return tuple(restored)

    def _rehydrate_properties(
        self, record: PluginInstanceRecord, descriptor: PluginDescriptor
    ) -> dict[str, Any]:
        """Recompute deterministic scope metadata over persisted user config."""
        user = {
            key: value for key, value in record.properties.items()
            if key not in _RUNTIME_KEYS
        }
        return self._effective_properties(
            descriptor, record.scope_id, user,
            ranking=record.ranking, instance_uuid=record.instance,
        )
```

顶部 import 增加:`from dataclasses import replace`、`import logging`、`LOGGER = logging.getLogger("langharness.plugin_manager")`、`from langharness_plugin.registry import DefinitionStatus`、`from langharness_plugin.state_store import PersistedDescriptorRecord`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_plugin_manager_unit.py -v -k TestPersistence`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/plugin_manager.py tests/test_plugin_manager_unit.py
git commit -m "feat(plugin): runtime state persistence and startup restore"
```

---

## Task 11: coordinator 重写 — 包式动态安装走实例模型

**Files:**
- Rewrite: `src/langharness_plugin/coordinator.py`
- Test: `tests/test_runtime_mutation_coordinator.py`(重写)

**Interfaces:**
- Consumes: Task 8/9/10 的 manager API(`install_descriptor`/`create_instance`/`update_instance`/`delete_instance`/`attach_provenance`/`detach_provenance`/`registrations`/`find_service`)、Task 5 的 `PersistedPluginRegistration`
- Produces:
  - `RuntimeMutationCoordinator(manager, discovery=None)` — 不再持有 store(manager 已绑定)
  - `install(package_id, contribution_id, *, scope_id=None, registration_key=None) -> PersistedPluginRegistration` — scope 缺省 root;agent_instance target 要求 `agent:<id>` 并注入 `plugin.agent_id`;同 (registration_key, scope) 幂等;无 key 时重复安装创建新实例
  - `set_enabled(name, enabled, *, scope_id)`、`update_properties(name, properties, *, scope_id)`、`upgrade(name, *, scope_id)`(新 UUID + history `replaced_instance`)、`uninstall(name, *, scope_id)`、`restore()`
  - 返回记录含 `instance/factory/module/scope_id`;tool export adapters 改为静态 adapter 定义 + `create_instance`

- [ ] **Step 1: 写失败测试**(替换 `tests/test_runtime_mutation_coordinator.py`)

```python
"""Tests for package-based dynamic mutations over the instance model."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from importlib.metadata import EntryPoint
from unittest.mock import Mock

import pytest

from langharness_plugin.coordinator import RuntimeMutationCoordinator
from langharness_plugin.discovery import PluginDiscovery
from langharness_plugin.errors import PluginNotFoundError
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.state_store import (
    InMemoryPluginHistoryStore,
    InMemoryRuntimeStateStore,
)
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Dynamic test plugin. Implements test.dynamic. Use in coordinator tests. "
    "No properties. Uninstall when tests finish."
)


def descriptor(**overrides) -> PluginDescriptor:
    fields = dict(
        name="dynamic",
        version="1.0.0",
        module="dynamic.module",
        factory="dynamic-factory",
        specification="test.dynamic",
        description=DESCRIPTION,
    )
    fields.update(overrides)
    return PluginDescriptor(**fields)


def package(version="1.0.0") -> PluginPackage:
    return PluginPackage(
        "dynamic.package", version,
        (PluginContribution("dynamic", "server", descriptor(version=version)),),
    )


def coordinator() -> tuple[RuntimeMutationCoordinator, PluginManager]:
    manager = PluginManager(PluginRegistry())
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    manager._context.install_bundle.return_value = Mock()
    manager._ipopo.instantiate.return_value = Mock()
    store = InMemoryRuntimeStateStore()
    manager.bind_state(store, InMemoryPluginHistoryStore())
    discovery = PluginDiscovery(lambda: [EntryPoint(lambda: package(), "ep", "langharness.plugins")])
    coordinator = RuntimeMutationCoordinator(manager, discovery=discovery)
    coordinator.rescan()
    return coordinator, manager


def test_install_creates_instance_and_persists_provenance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    assert registration.factory == "dynamic-factory"
    assert registration.module == "dynamic.module"
    assert registration.scope_id == ScopeId("server")
    assert len(registration.instance) == 32
    assert registration.status == "installed"
    assert manager.get_instance(registration.instance) is not None
    assert manager.registrations() == (registration,)
    assert manager._state_store.load().registrations == (registration,)


def test_install_defaults_to_root_scope() -> None:
    mutations, manager = coordinator()
    registration = mutations.install("dynamic.package", "dynamic")
    assert registration.scope_id == ROOT_SCOPE_ID


def test_reinstall_creates_second_instance() -> None:
    mutations, manager = coordinator()
    first = mutations.install("dynamic.package", "dynamic", scope_id=ScopeId("server"))
    second = mutations.install("dynamic.package", "dynamic", scope_id=ScopeId("server"))
    assert first.instance != second.instance
    assert len(manager.list_instance(scope_id=ScopeId("server"))) == 2


def test_install_with_registration_key_is_idempotent() -> None:
    mutations, manager = coordinator()
    first = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server"),
        registration_key="my-key",
    )
    second = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server"),
        registration_key="my-key",
    )
    assert first.instance == second.instance
    assert len(manager.list_instance()) == 1


def test_agent_instance_requires_agent_scope() -> None:
    from langharness_plugin.package import PluginContribution, PluginPackage

    agent_package = PluginPackage(
        "agent.package", "1.0.0",
        (PluginContribution("c", "agent_instance", descriptor()),),
    )
    mutations, _ = coordinator()
    mutations._catalog["agent.package"] = agent_package
    with pytest.raises(Exception, match="agent"):
        mutations.install("agent.package", "c", scope_id=ScopeId("server"))
    registration = mutations.install(
        "agent.package", "c", scope_id=ScopeId("agent:a")
    )
    assert registration.scope_id == ScopeId("agent:a")


def test_set_enabled_toggles_instance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install("dynamic.package", "dynamic",
                                     scope_id=ScopeId("server"))
    disabled = mutations.set_enabled("dynamic", False, scope_id=ScopeId("server"))
    assert disabled.enabled is False
    assert disabled.status == "disabled"
    assert disabled.instance == registration.instance  # UUID preserved
    enabled = mutations.set_enabled("dynamic", True, scope_id=ScopeId("server"))
    assert enabled.enabled is True
    assert enabled.instance == registration.instance


def test_update_properties_updates_instance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install("dynamic.package", "dynamic",
                                     scope_id=ScopeId("server"))
    updated = mutations.update_properties(
        "dynamic", {"plugin.mode": "fast"}, scope_id=ScopeId("server")
    )
    assert updated.instance == registration.instance
    assert manager.get_instance(registration.instance).properties["plugin.mode"] == "fast"


def test_upgrade_creates_new_uuid_and_records_replaced() -> None:
    mutations, manager = coordinator()
    registration = mutations.install("dynamic.package", "dynamic",
                                     scope_id=ScopeId("server"))
    upgraded_package = PluginPackage(
        "dynamic.package", "2.0.0",
        (PluginContribution("dynamic", "server", descriptor(version="2.0.0")),),
    )
    mutations._catalog["dynamic.package"] = upgraded_package
    upgraded = mutations.upgrade("dynamic", scope_id=ScopeId("server"))
    assert upgraded.package_version == "2.0.0"
    assert upgraded.instance != registration.instance
    with pytest.raises(Exception):
        manager.get_instance(registration.instance)  # old instance is gone
    history = manager._history.entries()
    replaced = [entry for entry in history if entry.action == "upgrade"]
    assert replaced and replaced[-1].detail["replaced_instance"] == registration.instance


def test_uninstall_removes_instance_and_provenance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install("dynamic.package", "dynamic",
                                     scope_id=ScopeId("server"))
    mutations.uninstall("dynamic", scope_id=ScopeId("server"))
    assert manager.registrations() == ()
    with pytest.raises(Exception):
        manager.get_instance(registration.instance)
    with pytest.raises(KeyError):
        mutations._registration("dynamic", ScopeId("server"))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_runtime_mutation_coordinator.py -v`
Expected: FAIL(构造函数/接口不匹配)

- [ ] **Step 3: 重写 coordinator.py**

```python
"""Package-based dynamic plugin mutations over the instance model."""

from __future__ import annotations

from threading import RLock

from langharness_plugin.discovery import PluginDiscovery
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.state_store import PersistedPluginRegistration
from langharness_scope import ROOT_SCOPE_ID, Scope, ScopeId

TOOL_ADAPTER_MODULE = "langharness_core.plugins.tools.export_adapter"
TOOL_ADAPTER_FACTORY = "tool-export-adapter-factory"
TOOL_SPECIFICATION = "agent.plugin.tools"
BUILTIN_PACKAGE_PREFIX = "builtin."
DYNAMIC_CORE_PACKAGE = "dynamic.core"


class RuntimeMutationError(RuntimeError):
    pass


class RuntimeMutationCoordinator:
    """Dynamic plugin mutations over the manager's instance lifecycle."""

    def __init__(
        self,
        manager: PluginManager,
        discovery: PluginDiscovery | None = None,
    ) -> None:
        self.manager = manager
        self.discovery = discovery if discovery is not None else PluginDiscovery()
        self._lock = RLock()
        self._catalog: dict[str, PluginPackage] = {}
        self._failures: tuple[object, ...] = ()
        self._adapter_instances: dict[str, list[str]] = {}
        self._adapter_definition_installed = False

    def rescan(self):
        result = self.discovery.scan()
        self._catalog = {package.id: package for package in result.packages}
        self._failures = result.failures
        return result

    def discovered(self) -> tuple[PluginPackage, ...]:
        return tuple(self._catalog[key] for key in sorted(self._catalog))

    def registrations(self) -> tuple[PersistedPluginRegistration, ...]:
        return self.manager.registrations()

    def scopes(self) -> tuple[Scope, ...]:
        return self.manager.scope_tree.snapshot().scopes

    def install(
        self,
        package_id: str,
        contribution_id: str,
        *,
        scope_id: ScopeId | None = None,
        registration_key: str | None = None,
    ) -> PersistedPluginRegistration:
        with self._lock:
            if package_id.startswith(BUILTIN_PACKAGE_PREFIX):
                raise RuntimeMutationError(
                    "Built-in plugins are managed by server/CLI assembly "
                    "and cannot be installed dynamically"
                )
            package, contribution = self._find(package_id, contribution_id)
            scope_id = scope_id if scope_id is not None else ROOT_SCOPE_ID
            properties = self._contribution_properties(contribution, scope_id)
            if registration_key is not None:
                existing = next(
                    (
                        item for item in self.manager.registrations()
                        if item.package_id == package_id
                        and item.contribution_id == contribution_id
                        and item.scope_id == scope_id
                        and item.registration_key == registration_key  # see note
                    ),
                    None,
                )
                if existing is not None:
                    return existing
            snapshot = self.manager.create_instance(
                contribution.descriptor.factory,
                contribution.descriptor.module,
                scope_id,
                properties=properties,
                enabled=True,
            )
            registration = PersistedPluginRegistration(
                package.id, contribution.id, package.version,
                snapshot.instance, snapshot.factory, snapshot.module,
                scope_id, True, "installed",
            )
            self.manager.attach_provenance(registration)
            try:
                self._install_adapters(registration, contribution)
                self.manager._persist_state()
            except Exception:
                self._kill_adapters(registration)
                self.manager.detach_provenance(snapshot.instance)
                self.manager.delete_instance(snapshot.instance)
                raise
            return registration
```

**注意**:`registration_key` 幂等需要持久化——将 `PersistedPluginRegistration` 增加可选字段 `registration_key: str | None = None`(state_store.py 的 encode/decode 同步增加)。无 key 时重复 install 直接创建新实例(设计文档:同一结构化 key 可多个实例)。

其余方法:

```python
    def set_enabled(
        self, name: str, enabled: bool, *, scope_id: ScopeId
    ) -> PersistedPluginRegistration:
        with self._lock:
            registration = self._registration(name, scope_id)
            if registration.enabled == enabled:
                return registration
            snapshot = self.manager.update_instance(
                registration.instance, enabled=enabled
            )
            if enabled:
                package, contribution = self._find(
                    registration.package_id, registration.contribution_id
                )
                self._install_adapters(registration, contribution)
            else:
                self._kill_adapters(registration)
            self.manager._persist_state()
            updated = PersistedPluginRegistration(
                registration.package_id, registration.contribution_id,
                registration.package_version, snapshot.instance,
                snapshot.factory, snapshot.module, snapshot.scope_id,
                snapshot.enabled,
                "installed" if snapshot.enabled else "disabled",
                registration.registration_key,
            )
            self.manager.attach_provenance(updated)
            return updated

    def update_properties(
        self, name: str, properties: dict[str, object], *, scope_id: ScopeId
    ) -> PersistedPluginRegistration:
        with self._lock:
            registration = self._registration(name, scope_id)
            snapshot = self.manager.update_instance(
                registration.instance,
                properties={str(key): value for key, value in properties.items()},
            )
            self.manager._persist_state()
            return self.manager.registrations()[  # refreshed provenance
                tuple(self.manager.registrations()).index(registration)
            ]

    def upgrade(self, name: str, *, scope_id: ScopeId) -> PersistedPluginRegistration:
        with self._lock:
            registration = self._registration(name, scope_id)
            package, contribution = self._find(
                registration.package_id, registration.contribution_id
            )
            if package.version == registration.package_version:
                return registration
            properties = self._contribution_properties(contribution, scope_id)
            replacement = self.manager.create_instance(
                contribution.descriptor.factory,
                contribution.descriptor.module,
                scope_id,
                properties=properties,
                enabled=registration.enabled,
            )
            updated = PersistedPluginRegistration(
                registration.package_id, registration.contribution_id,
                package.version, replacement.instance,
                replacement.factory, replacement.module, scope_id,
                replacement.enabled,
                "installed" if replacement.enabled else "disabled",
                registration.registration_key,
            )
            self._kill_adapters(registration)
            self.manager.delete_instance(registration.instance)
            self.manager.attach_provenance(updated)
            self.manager._persist_state()
            self.manager._record_history(
                "upgrade", factory=updated.factory, module=updated.module,
                scope_id=str(scope_id), instance=updated.instance,
                detail={"replaced_instance": registration.instance},
            )
            return updated

    def uninstall(self, name: str, *, scope_id: ScopeId) -> None:
        with self._lock:
            registration = self._registration(name, scope_id)
            self._kill_adapters(registration)
            self.manager.delete_instance(registration.instance)
            self.manager.detach_provenance(registration.instance)
            self.manager._persist_state()

    def restore(self) -> tuple[PersistedPluginRegistration, ...]:
        """Reattach adapters after manager-level restore."""
        with self._lock:
            if not self._catalog:
                self.rescan()
            restored = self.manager.restore()
            for registration in tuple(self.manager.registrations()):
                package = self._catalog.get(registration.package_id)
                if package is None:
                    continue
                if registration.status == "missing":
                    continue
                contribution = next(
                    (
                        item for item in package.contributions
                        if item.id == registration.contribution_id
                    ),
                    None,
                )
                if contribution is not None and registration.enabled:
                    try:
                        self._install_adapters(registration, contribution)
                    except Exception:
                        continue
            return tuple(registration for registration in self.manager.registrations())

    def _find(
        self, package_id: str, contribution_id: str
    ) -> tuple[PluginPackage, PluginContribution]:
        package = self._catalog.get(package_id)
        if package is None:
            raise KeyError(package_id)
        for contribution in package.contributions:
            if contribution.id == contribution_id:
                return package, contribution
        raise KeyError(contribution_id)

    @staticmethod
    def _contribution_properties(
        contribution: PluginContribution, scope_id: ScopeId
    ) -> dict[str, object]:
        properties: dict[str, object] = {}
        if contribution.target == "agent_instance":
            if not str(scope_id).startswith("agent:"):
                raise RuntimeMutationError(
                    "agent_instance contribution requires agent:<id> scope"
                )
            properties["plugin.agent_id"] = str(scope_id).removeprefix("agent:")
        return properties

    def _registration(
        self, name: str, scope_id: ScopeId
    ) -> PersistedPluginRegistration:
        matches = [
            item for item in self.manager.registrations()
            if item.scope_id == scope_id
            and self._registration_name(item) == name
        ]
        if not matches:
            raise KeyError(f"plugin {name} not found in scope {scope_id}")
        if len(matches) > 1:
            raise RuntimeMutationError(
                f"plugin {name!r} is ambiguous in scope {scope_id!r}; "
                "use the instance UUID"
            )
        return matches[0]

    def _registration_name(self, registration: PersistedPluginRegistration) -> str:
        package = self._catalog.get(registration.package_id)
        if package is None:
            return registration.contribution_id
        for contribution in package.contributions:
            if contribution.id == registration.contribution_id:
                return contribution.descriptor.name
        return registration.contribution_id

    def _install_adapters(
        self,
        registration: PersistedPluginRegistration,
        contribution: PluginContribution,
    ) -> None:
        if not contribution.tool_exports:
            return
        if not self._adapter_definition_installed:
            from langharness_plugin.registry import PluginDescriptor

            self.manager.install_descriptor(
                PluginDescriptor(
                    name="tool-export-adapter",
                    version=registration.package_version,
                    module=TOOL_ADAPTER_MODULE,
                    factory=TOOL_ADAPTER_FACTORY,
                    specification=TOOL_SPECIFICATION,
                    description=(
                        "Adapts a tool export target into scoped tool services. "
                        "Implements agent.plugin.tools. Properties: "
                        "plugin.tool_export.target and plugin.tool_export.exports. "
                        "Managed by the dynamic plugin coordinator."
                    ),
                ),
                source="assembly",
            )
            self._adapter_definition_installed = True
        target = self.manager.find_service(
            contribution.descriptor.specification,
            f"(plugin.scope_id={registration.scope_id})",
        )
        if target is None:
            raise RuntimeMutationError("Tool export target service is unavailable")
        groups: dict[str, list[object]] = {}
        for export in contribution.tool_exports:
            target_scope = (
                str(registration.scope_id)
                if export.target_scope == "agent_instance"
                else "agent"
            )
            groups.setdefault(target_scope, []).append(export)
        created: list[str] = []
        for target_scope, exports in groups.items():
            snapshot = self.manager.create_instance(
                TOOL_ADAPTER_FACTORY, TOOL_ADAPTER_MODULE,
                ScopeId(target_scope),
                properties={
                    "plugin.tool_export.target": target,
                    "plugin.tool_export.exports": exports,
                },
            )
            created.append(snapshot.instance)
        self._adapter_instances[registration.instance] = created

    def _kill_adapters(self, registration: PersistedPluginRegistration) -> None:
        for instance in reversed(self._adapter_instances.pop(registration.instance, [])):
            try:
                self.manager.delete_instance(instance)
            except Exception:
                pass
```

注意:`update_properties` 里 `registrations()[...]` 写法别扭——改为 `self.manager.registrations()` 后按 instance 匹配:

```python
            self.manager._persist_state()
            return next(
                item for item in self.manager.registrations()
                if item.instance == registration.instance
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_runtime_mutation_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/coordinator.py src/langharness_plugin/state_store.py tests/test_runtime_mutation_coordinator.py
git commit -m "feat(plugin): coordinator mutations over the instance model with upgrade history"
```

---

## Task 12: contracts 协议与 config_store 合并

**Files:**
- Modify: `src/langharness_plugin/contracts.py`
- Modify: `src/langharness_plugin/config_store.py`
- Test: `tests/test_config_store.py`(适配)

**Interfaces:**
- Produces:
  - `ScopedPluginRegistrar`: `create_instance/get_instance/delete_instance/update_instance/list_instance/find_service/find_services/installed_modules/add_scope/remove_scope/scope_filter/apply_config`
  - `PluginRegistrar`: `install_descriptor`
  - `DynamicPluginManager`: `install(..., registration_key=None)`,返回值新形状;其余不变
  - `merge_overrides(defaults: Mapping[str, Any], override: dict[str, Any]) -> tuple[bool, dict[str, Any]]` — 返回 (enabled, 合并后 properties),替代 `apply_overrides`

- [ ] **Step 1: 写失败测试**(适配 `tests/test_config_store.py`)

```python
def test_merge_overrides_returns_enabled_and_properties() -> None:
    from langharness_plugin.config_store import merge_overrides

    enabled, properties = merge_overrides(
        {"plugin.model.name": "default"},
        {"enabled": False, "properties": {"plugin.model.name": "stored"}},
    )
    assert enabled is False
    assert properties == {"plugin.model.name": "stored"}

    enabled, properties = merge_overrides(
        {"plugin.model.name": "default"}, {}
    )
    assert enabled is True
    assert properties == {"plugin.model.name": "default"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_config_store.py -v -k merge_overrides`
Expected: FAIL(`merge_overrides` 不存在)

- [ ] **Step 3: 实现**

contracts.py:

```python
from langharness_plugin.registry import (
    PluginDescriptor,
    PluginInstanceSnapshot,
)
from langharness_plugin.state_store import PersistedPluginRegistration
from langharness_scope import Scope, ScopeId
from typing import Any, Mapping, Protocol, runtime_checkable


@service_contract(SPEC_PLUGIN_REGISTRAR)
@runtime_checkable
class PluginRegistrar(Protocol):
    def install_descriptor(self, descriptor: PluginDescriptor) -> Any: ...


@service_contract(SPEC_PLUGIN_SCOPE)
@runtime_checkable
class ScopedPluginRegistrar(Protocol):
    """Creates and manages scoped component instances."""

    def create_instance(
        self,
        factory: str,
        module: str,
        scope_id: ScopeId | None = None,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool = True,
        ranking: int = 0,
    ) -> PluginInstanceSnapshot: ...

    def get_instance(self, instance: str) -> PluginInstanceSnapshot: ...

    def delete_instance(self, instance: str) -> None: ...

    def update_instance(
        self,
        instance: str,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        ranking: int | None = None,
    ) -> PluginInstanceSnapshot: ...

    def list_instance(
        self,
        *,
        factory: str | None = None,
        module: str | None = None,
        scope_id: ScopeId | None = None,
        enabled: bool | None = None,
    ) -> tuple[PluginInstanceSnapshot, ...]: ...

    def find_service(self, specification: str, filter: str | None = None) -> Any: ...

    def find_services(self, specification: str, filter: str | None = None) -> list[Any]: ...

    def installed_modules(self) -> set[str]: ...

    def add_scope(
        self, scope_id: ScopeId, *, name: str, parent_id: ScopeId | None = None
    ) -> Scope: ...

    def remove_scope(self, scope_id: ScopeId, *, recursive: bool = False) -> None: ...

    def scope_filter(self, scope_id: ScopeId) -> str: ...

    def apply_config(
        self, overrides: dict[str, dict[str, Any]]
    ) -> dict[str, list[str]]: ...


@service_contract(SPEC_DYNAMIC_PLUGIN_MANAGER)
@runtime_checkable
class DynamicPluginManager(Protocol):
    def rescan(self) -> Any: ...

    def discovered(self) -> tuple[Any, ...]: ...

    def registrations(self) -> tuple[PersistedPluginRegistration, ...]: ...

    def install(
        self,
        package_id: str,
        contribution_id: str,
        *,
        scope_id: ScopeId | None = None,
        registration_key: str | None = None,
    ) -> PersistedPluginRegistration: ...

    def set_enabled(
        self, name: str, enabled: bool, *, scope_id: ScopeId
    ) -> PersistedPluginRegistration: ...

    def update_properties(
        self, name: str, properties: dict[str, Any], *, scope_id: ScopeId
    ) -> PersistedPluginRegistration: ...

    def uninstall(self, name: str, *, scope_id: ScopeId) -> None: ...

    def upgrade(
        self, name: str, *, scope_id: ScopeId
    ) -> PersistedPluginRegistration: ...

    def scopes(self) -> tuple[Scope, ...]: ...
```

config_store.py:`apply_overrides` 替换为:

```python
def merge_overrides(
    defaults: Mapping[str, Any], override: Mapping[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Merge one stored config entry into code-built defaults.

    Returns (enabled, properties): enabled defaults to True; stored
    properties override defaults key by key.
    """
    enabled = bool(override.get("enabled", True))
    properties = dict(defaults)
    stored = override.get("properties")
    if isinstance(stored, dict):
        properties.update(stored)
    return enabled, properties
```

删除旧 `apply_overrides`(调用方在 Task 13/14 迁移)。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_config_store.py -v`
Expected: PASS(该文件其余用例若引用 apply_overrides 需同步改为 merge_overrides 语义)

- [ ] **Step 5: 提交**

```bash
git add src/langharness_plugin/contracts.py src/langharness_plugin/config_store.py tests/test_config_store.py
git commit -m "refactor(plugin): update service contracts and config override merge"
```

---

## Task 13: bootstrap 装配 + core builtin 七字段化 + AgentLoop ScopedDependencies + agent directory

**Files:**
- Modify: `src/langharness/bootstrap.py`
- Modify: `src/langharness_core/plugin.py`
- Modify: `src/langharness_core/plugins/loop/agent_loop.py`
- Modify: `src/langharness_core/plugins/agents/directory.py`
- Test: `tests/test_bootstrap.py`、`tests/test_core_descriptors.py`、`tests/test_plugin_scope_seed.py`、`tests/test_agent_scoping.py`(适配)

**Interfaces:**
- Consumes: Task 8/9/12(install_descriptor/create_instance/ensure_instance/merge_overrides)
- Produces:
  - bootstrap `AssemblyRequest(descriptor, scope_id, properties, enabled)` 与 `_assembly_requests(...)`、`_run` 装配流程(先 install 定义 → restore → ensure_instance 幂等装配)
  - core/plugin.py 全部 descriptor 工厂七字段化(含 description),properties 由独立函数返回
  - `@ScopedDependencies(...)` 挂在 AgentLoop 上;`agent_loop_descriptor` 的用户 filter 保留(manager AND 合并)
  - directory 改为 create_instance/delete_instance,UUID 追踪

- [ ] **Step 1: 写失败测试**(`tests/test_core_descriptors.py` 重写)

```python
"""Tests for seven-field builtin descriptors and assembly requests."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from langharness_core.plugin import (
    agent_directory_descriptor,
    agent_loop_descriptor,
    agent_plugin_descriptor,
    builtin_package,
)
from langharness_plugin.registry import validate_descriptor


def test_every_builtin_descriptor_passes_static_validation() -> None:
    for package in (builtin_package(),):
        for contribution in package.contributions:
            validate_descriptor(contribution.descriptor)
            assert contribution.descriptor.description.strip()


def test_agent_plugin_descriptor_is_static_only() -> None:
    descriptor = agent_plugin_descriptor("llm")
    assert descriptor.name == "llm"
    assert descriptor.module == "langharness_core.plugins.llm.llm"
    assert descriptor.factory == "llm-plugin-factory"
    validate_descriptor(descriptor)
    assert not hasattr(descriptor, "scope")
    assert not hasattr(descriptor, "instance")


def test_agent_loop_properties_keep_user_filters() -> None:
    from langharness_core.plugin import agent_loop_properties

    descriptor = agent_loop_descriptor()
    validate_descriptor(descriptor)
    properties = agent_loop_properties(
        "a", ["agent.plugin.llm"], visibility_filter="(plugin.agent_id=a)"
    )
    assert properties["plugin.agent_id"] == "a"
    assert properties["requires.filters"]["_llm_provider"] == "(plugin.agent_id=a)"
```

(properties 已不在 descriptor 上;用户 filter 由 `agent_loop_properties` 返回,Task 9 的 manager 会在实例化时与 scope filter 做 AND 合并。)

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_core_descriptors.py -v`
Expected: FAIL(构造参数不匹配/属性不存在)

- [ ] **Step 3: 实现**

core/plugin.py 关键改造(每个工厂拆成 descriptor + properties 函数):

```python
"""Descriptors and assembly helpers for the built-in core plugins."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langharness_core.contracts import (
    SPEC_AGENT_DIRECTORY, SPEC_AGENT_LOOP, SPEC_AGENT_REGISTRY,
    SPEC_AGENT_SERVER, SPEC_CHECKPOINTER, SPEC_LLM, SPEC_SESSION_INDEX,
    SPEC_TOOL,  # ... 其余按需
)
from langharness_plugin.contracts import SPEC_TOOL_EXPORT_TARGET
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import PluginDescriptor

AGENT_PLUGIN_CATALOG: dict[str, tuple[str, str, str]] = {
    "llm": ("langharness_core.plugins.llm.llm", "llm-plugin-factory", SPEC_LLM),
    "tools": ("langharness_core.plugins.tools.workspace", "workspace-tools-plugin-factory", SPEC_TOOL),
    "name": ("langharness_core.plugins.name.template_name", "agent-name-plugin-factory", SPEC_NAME),
}

# 其余 catalog(AGENT_PLUGIN_CATALOG/DYNAMIC_PLUGIN_CATALOG)保持不变。

AGENT_PLUGIN_DESCRIPTIONS: dict[str, str] = {
    "llm": (
        "Provides the LLM service consumed by an agent loop. Implements "
        "agent.plugin.llm. Use it whenever an agent must call a model; skip it "
        "when the agent should inherit the scope default. Properties: "
        "plugin.model.name, plugin.model.instance, plugin.model.api_key, "
        "plugin.model.base_url, plugin.model.protocol, plugin.agent_id. "
        "Requires a restart for property changes. Uninstall when the agent is "
        "removed."
    ),
    "tools": (
        "Provides tool services for one agent scope. Implements agent.plugin.tools. "
        "Properties: plugin.tools.root_dir, plugin.agent_id. Requires a restart "
        "for property changes. Uninstall when the agent is removed."
    ),
    "name": (
        "Names the agent in UI and prompts. Implements agent.plugin.name. "
        "Properties: plugin.agent_name, plugin.agent_id. Requires a restart "
        "for property changes. Uninstall when the agent is removed."
    ),
    "agent-loop": (
        "Runs the agent loop for one agent scope. Implements agent.plugin.agent_loop. "
        "Properties: plugin.agent_id and requires.filters for scoped dependency "
        "fields. Requires a restart for property changes. Uninstall when the "
        "agent is removed."
    ),
    # ... 其余 builtin 按同结构补全
}
```

descriptor 工厂(示例,全部照此模式):

```python
def agent_plugin_descriptor(plugin: str) -> PluginDescriptor:
    """Static definition of one agent plugin from the catalog."""
    module, factory, specification = AGENT_PLUGIN_CATALOG[plugin]
    return PluginDescriptor(
        name=plugin,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=AGENT_PLUGIN_DESCRIPTIONS[plugin],
    )


def agent_binding_properties(
    agent_id: str, plugin: str, properties: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = dict(AGENT_PLUGIN_SETTINGS.get(plugin, {}))
    merged.update(properties or {})
    merged["plugin.agent_id"] = agent_id
    return merged


def agent_plugin_template_descriptor(plugin: str) -> PluginDescriptor:
    """Static definition used to pre-install a catalog bundle."""
    return agent_plugin_descriptor(plugin)


def default_llm_properties(properties: dict[str, Any]) -> dict[str, Any]:
    return dict(properties)


def agent_loop_properties(
    agent_id: str,
    scoped_specifications: Iterable[str],
    *,
    visibility_filter: str | None = None,
) -> dict[str, Any]:
    scoped_specifications = tuple(scoped_specifications)
    filters = {
        AGENT_LOOP_FIELDS[specification]: visibility_filter or agent_filter(agent_id)
        for specification in scoped_specifications
        if specification in AGENT_LOOP_FIELDS
    }
    if SPEC_LLM in scoped_specifications:
        filters["_scoped_llm_providers"] = visibility_filter or agent_filter(agent_id)
    return {"plugin.agent_id": agent_id, "requires.filters": filters}


def agent_loop_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="agent-loop",
        version="1.0.0",
        module=AGENT_LOOP_MODULE,
        factory="agent-loop-factory",
        specification=SPEC_AGENT_LOOP,
        description=AGENT_PLUGIN_DESCRIPTIONS["agent-loop"],
    )
```

其余 `sqlite_checkpointer_descriptor` 等改为 `xxx_properties(directory)` 返回 dict + `xxx_descriptor()` 静态定义;`agent_directory_descriptor()`、`agent_server_descriptor()`、`tool_export_adapter_template_descriptor()` 同理。`dynamic_template_descriptor`/`dynamic_package` 同样拆为静态 descriptor + 属性由 coordinator 注入(contribution.target 决定 scope/agent_id,见 Task 11)。

agent_loop.py 增加:

```python
from langharness_plugin.scoped_dependencies import ScopedDependencies


@ScopedDependencies(
    "_llm_provider",
    "_scoped_llm_providers",
    "_tool_providers",
    "_middleware_providers",
    "_system_prompt_providers",
    "_name_provider",
    "_response_format_provider",
    "_cache_provider",
    "_transformers_providers",
    "_interrupt_before_providers",
    "_interrupt_after_providers",
)
class AgentLoop:
    ...
```

(装饰器放在最内层、`@ComponentFactory` 之下或之上均可——setattr 在同一个类对象上。推荐放在 `@Provides` 上方。)

directory.py 改造:

```python
    def ensure_plugin_instance(self, agent_id, plugin, properties) -> None:
        agent = self._require_agent(agent_id)
        self._materialize_all()
        if plugin not in AGENT_PLUGIN_CATALOG:
            raise ValueError(f"Unknown agent plugin: {plugin}")
        merged = dict(self.binding_properties(agent["id"], plugin))
        if plugin == "llm":
            merged = {**self._llm_defaults(), **merged, **properties}
            if not merged.get("plugin.model.name") and not merged.get(
                "plugin.model.instance"
            ):
                return
        else:
            merged.update(properties)
        descriptor = agent_plugin_descriptor(plugin)
        scope_id = agent_instance_scope_id(agent["id"])
        current = self._instances.get(agent["id"], {}).get(plugin)
        if current is not None:
            self._scope.update_instance(current.instance, properties=merged)
            return
        snapshot = self._scope.create_instance(
            descriptor.factory, descriptor.module, scope_id,
            properties=agent_binding_properties(agent["id"], plugin, merged),
        )
        self._instances.setdefault(agent["id"], {})[plugin] = snapshot
```

`_materialize`/`_teardown`/`_ensure_default_llm` 同模式:create_instance/delete_instance,`_instances`/`_loops` 追踪 snapshot(UUID)。

bootstrap.py 装配:

```python
@dataclass(frozen=True, slots=True)
class AssemblyRequest:
    descriptor: PluginDescriptor
    scope_id: ScopeId
    properties: dict[str, Any]
    enabled: bool = True


TARGET_SCOPES = {
    "root": ROOT_SCOPE_ID,
    "ui": UI_SCOPE_ID,
    "server": SERVER_SCOPE_ID,
    "agent": AGENT_SCOPE_ID,
}


def _assembly_requests(packages, *, config_dir, locale, override_scope, base_url) -> list[AssemblyRequest]:
    overrides = load_overrides(config_dir, override_scope)
    requests: list[AssemblyRequest] = []
    seen: set[str] = set()
    for package in packages:
        for contribution in package.contributions:
            descriptor = contribution.descriptor
            properties = _default_properties(
                descriptor, config_dir=config_dir, locale=locale, base_url=base_url
            )
            enabled = True
            if descriptor.name in overrides:
                enabled, properties = merge_overrides(properties, overrides[descriptor.name])
            if (override_scope == "cli" and descriptor.name == "server-log"
                    or override_scope == "api" and descriptor.name == "cli-log"):
                continue
            if descriptor.name in seen:
                continue
            seen.add(descriptor.name)
            if descriptor.name in {"cli-health", "cli-plugins"}:
                properties["plugin.base_url"] = base_url
            scope_id = TARGET_SCOPES.get(contribution.target, ROOT_SCOPE_ID)
            requests.append(AssemblyRequest(descriptor, scope_id, properties, enabled))
    return requests
```

(`_default_properties` 由旧 `_configured_descriptor` 改造:返回 properties dict,不再 replace descriptor。`_select_packages` 的临时 manager 流程同步改为 install_descriptor + create_instance。)

`_run` 装配顺序:

```python
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        coordinator: RuntimeMutationCoordinator | None = None
        if options.mode == "server":
            store = SqliteRuntimeStateStore(Path(options.config_dir) / "runtime_state.sqlite3")
            manager.bind_state(store, SqlitePluginHistoryStore(
                Path(options.config_dir) / "runtime_state.sqlite3"))
            coordinator = RuntimeMutationCoordinator(manager, discovery=PluginDiscovery())
            manager.register_runtime_service(DynamicPluginManager, coordinator)
        requests = _assembly_requests(packages, ...)
        for request in requests:
            manager.install_descriptor(request.descriptor, source="assembly")
        if coordinator is not None:
            coordinator.rescan()
            coordinator.restore()
            _apply_agent_configs(manager, options.config_dir)
        for request in requests:
            manager.ensure_instance(
                request.descriptor.factory, request.descriptor.module,
                request.scope_id, properties=request.properties,
                enabled=request.enabled,
            )
        ...  // 后续 server/ui 启动逻辑不变
    finally:
        manager.stop()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_core_descriptors.py tests/test_bootstrap.py tests/test_plugin_scope_seed.py -v`
Expected: PASS(其余关联用例在 Task 14 收口)

- [ ] **Step 5: 提交**

```bash
git add src/langharness/bootstrap.py src/langharness_core/plugin.py src/langharness_core/plugins/loop/agent_loop.py src/langharness_core/plugins/agents/directory.py tests/test_core_descriptors.py tests/test_bootstrap.py
git commit -m "refactor(core): seven-field builtin descriptors and scoped dependency declarations"
```

---

## Task 14: API 路由与 CLI 命令 — 显式 scope 与新输出

**Files:**
- Modify: `src/langharness_api/plugins/routes/plugins.py`
- Modify: `src/langharness_cli/plugins/commands/`(plugin 命令)
- Modify: `src/langharness_core/plugins/tools/management.py`(管理工具走新 coordinator 签名)
- Test: `tests/test_api_plugins.py`、`tests/test_plugin_commands.py`、`tests/test_management_tools.py`(适配)

**Interfaces:**
- Consumes: Task 11 coordinator(新 PersistedPluginRegistration 形状)
- Produces: 安装/启停/卸载/更新命令显式要求 scope;响应/输出包含 `instance`、`factory`、`module`、`scope_id`、`enabled`、`status`

- [ ] **Step 1: 写失败测试**(适配现有路由测试)

```python
def test_install_route_requires_scope(client) -> None:
    response = client.post("/plugins/install", json={
        "package_id": "dynamic.package", "contribution_id": "dynamic",
    })
    assert response.status_code == 422  # scope missing -> validation error


def test_install_route_returns_instance_identity(client) -> None:
    response = client.post("/plugins/install", json={
        "package_id": "dynamic.package", "contribution_id": "dynamic",
        "scope_id": "server",
    })
    payload = response.json()
    assert response.status_code == 200
    assert payload["instance"]
    assert payload["factory"] == "dynamic-factory"
    assert payload["module"] == "dynamic.module"
    assert payload["scope_id"] == "server"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api_plugins.py -v -k install`
Expected: FAIL(响应形状不符)

- [ ] **Step 3: 实现**

路由 `install`/`set_enabled`/`uninstall`/`upgrade`/`update_properties` 的请求体增加必填 `scope_id`(校验缺省报 422),响应统一为:

```python
def _registration_payload(registration: PersistedPluginRegistration) -> dict[str, Any]:
    return {
        "package_id": registration.package_id,
        "contribution_id": registration.contribution_id,
        "package_version": registration.package_version,
        "instance": registration.instance,
        "factory": registration.factory,
        "module": registration.module,
        "scope_id": str(registration.scope_id),
        "enabled": registration.enabled,
        "status": registration.status,
    }
```

CLI plugin 命令:`install` 增加必填 `--scope` 参数,输出表新增 `INSTANCE/FACTORY/SCOPE` 列;`disable/enable/uninstall/update` 命令要求 `--scope`。management.py 的 `install_plugin` 工具调用增加 scope 参数(缺省 root,但 API 层要求显式)。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api_plugins.py tests/test_plugin_commands.py tests/test_management_tools.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_api/plugins/routes/plugins.py src/langharness_cli/plugins/commands src/langharness_core/plugins/tools/management.py tests/test_api_plugins.py tests/test_plugin_commands.py tests/test_management_tools.py
git commit -m "refactor(api,cli): explicit scope and instance identity in plugin commands"
```

---

## Task 15: 其余 builtin 包七字段合规

**Files:**
- Modify: `src/langharness_api/plugin.py`、`src/langharness_cli/plugin.py`、`src/langharness_config/plugin.py`、`src/langharness_logging/plugin.py`
- Test: `tests/test_api_contracts.py`、`tests/test_imports.py`、`tests/test_logging_plugin.py`(适配)

**Interfaces:**
- Consumes: Task 13 模式(静态 descriptor + properties 函数)
- Produces: 全部 builtin descriptor 七字段 + description 非空;装配路径无 descriptor.properties 残留

- [ ] **Step 1: 写失败测试**

```python
"""Every builtin package descriptor conforms to the seven-field contract."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from langharness_api.plugin import builtin_package as api_package
from langharness_cli.plugin import builtin_package as cli_package
from langharness_config.plugin import builtin_package as config_package
from langharness_core.plugin import builtin_package as core_package
from langharness_logging.plugin import builtin_package as log_package
from langharness_plugin.registry import validate_descriptor


@pytest.mark.parametrize(
    "package",
    [api_package(), cli_package(), config_package(), core_package(), log_package()],
)
def test_all_builtin_descriptors_are_valid_static_definitions(package) -> None:
    for contribution in package.contributions:
        descriptor = contribution.descriptor
        validate_descriptor(descriptor)
        assert descriptor.description.strip()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_builtin_descriptors.py -v`(新建文件)
Expected: FAIL

- [ ] **Step 3: 实现**

按 Task 13 模式逐一改造:每个 `PluginDescriptor(...)` 删除 `instance/scope/scope_parent/properties/enabled/ranking`,补充 `description`;需要默认配置的插件提供 `xxx_properties(...)` 函数,由 bootstrap `_default_properties` 调用(注意 api/cli 包的 `plugin.base_url`、`plugin.ui.locale` 等默认值迁移到 `_default_properties`)。各包 description 参照:"Provides the X service for the server/UI. Implements <spec>. Properties: ... . Requires a restart for property changes. Uninstall when the feature is not needed."

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_builtin_descriptors.py tests/test_imports.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_api/plugin.py src/langharness_cli/plugin.py src/langharness_config/plugin.py src/langharness_logging/plugin.py tests/test_builtin_descriptors.py
git commit -m "refactor(builtin): conform all builtin packages to the seven-field descriptor"
```

---

## Task 16: 验证方案

### 16.1 自动化验证矩阵(设计需求 → 测试)

| 设计需求 | 验证位置 | 关键断言 |
|---|---|---|
| descriptor 七字段固定 | `tests/test_registry.py::TestDescriptorFields` | `len(__dataclass_fields__) == 7`;`from_dict` 拒绝 instance/scope/properties |
| description 缺失策略 | `tests/test_registry.py::TestDescriptorValidation`、`tests/test_plugin_discovery.py::test_invalid_fields_skipped_with_warning` | 空 description 校验失败,发现时 warning 跳过 |
| 缺省 scope → root | `tests/test_plugin_manager_scoped.py::test_create_defaults_to_root_scope` | `scope_id == ROOT_SCOPE_ID` |
| UUID 唯一/多实例 | `tests/test_plugin_manager_scoped.py::TestCreateInstance` | 同 key 两实例 UUID 不同 |
| disable/enable 复用 UUID | `test_disable_then_enable_reuses_uuid` | 同 UUID |
| upgrade 新 UUID + replaced_instance | `tests/test_runtime_mutation_coordinator.py::test_upgrade_creates_new_uuid_and_records_replaced` | history detail 记录 |
| hot/restart 更新语义 | `TestUpdateInstance` | hot→reconfigure;restart→kill+同 UUID 重建;失败恢复旧快照 |
| 同名多 descriptor 歧义 | `tests/test_registry.py::test_registry_get_by_name_raises_ambiguity` | `AmbiguousPluginError` + 候选 factory |
| 同 module 多 factory 共享 bundle | `tests/test_plugin_manager_unit.py::test_bundle_shared_by_two_definitions_in_one_module` | `install_bundle` 调用 1 次 |
| 同 factory 异 module install 冲突 | `test_install_descriptor_identity_conflict` | `PluginIdentityConflictError` |
| 有实例禁 uninstall | `test_uninstall_with_instances_raises` | `PluginHasInstancesError` |
| 类/callable 入口、first-wins、module mismatch | `tests/test_plugin_discovery.py` | 全套 |
| 实例 properties 独立持久化与恢复 | `tests/test_plugin_manager_unit.py::TestPersistence::test_restore_rebuilds_instances_with_persisted_uuid` | 持久化 UUID + effective properties 重注入 |
| 发现不到 → missing | `test_restore_marks_missing_definition_instance` | status missing |
| CAS 冲突 | `tests/test_runtime_state_store.py::test_cas_conflict_raises` | `PluginStateConflictError` |
| 旧 schema 拒绝 | `test_old_schema_file_raises_clear_error` | `RuntimeStateSchemaError` + 提示删除 |
| scope 删除/递归删除 | `tests/test_plugin_manager_unit.py::TestScopes` | `ScopeHasChildrenError`/`ScopeHasInstancesError` |
| ScopedDependencies 自动 filter | `tests/test_scoped_dependencies.py` + Task 13 启用的 `test_scoped_fields_introspection_hits_agent_loop` | AgentLoop 的 `_llm_provider` 在声明集中 |
| filter AND 合并/非法拒绝 | `test_injected_runtime_properties`(runtime keys)+ Task 9 `_effective_properties` 路径 | canonical 形式 scope filter 打头;非法 filter 创建失败 |
| iPOPO 真框架多实例 | 见 16.3 E2E 场景 3 | 同 factory 同 scope 两实例同时存活 |

### 16.2 全量质量门禁(每任务后 + 收口时)

```bash
# 1. 删除旧状态文件(开发期,无存量)
rm -f ~/.langharness/runtime_state.sqlite3

# 2. 全量测试 + 覆盖率
.venv/bin/python -m pytest -q --cov=langharness --cov=langharness_scope \
  --cov=langharness_config --cov=langharness_logging --cov=langharness_core \
  --cov=langharness_plugin --cov=langharness_api --cov=langharness_cli \
  --cov-report=term-missing --cov-fail-under=95

# 3. 静态检查
make check
```

预期:全部通过;若覆盖缺口出现在新分支路径,按缺口补测试(不允许降门槛)。

### 16.3 手动 E2E 验证(真实 Pelix 框架)

前置:`rm -f ~/.langharness/runtime_state.sqlite3`,启动 `make` 构建后的 server 模式。

1. **启动即装配**:server 启动日志显示 builtin 定义安装 + 实例创建;`~/.langharness/runtime_state.sqlite3` 生成,`plugin_history` 表有 `install_definition`/`create_instance` 记录。
2. **动态安装显式 scope**:`langharness plugin install <pkg> <contrib> --scope server` → 输出含 INSTANCE/FACTORY/SCOPE;不带 `--scope` 报参数错误。
3. **同 scope 多实例**:对同一 contribution 在 server scope 安装两次 → 两个不同 UUID,`list_instance` 可见两条;`service_properties` 看到两个服务注册(真框架验证 iPOPO 同名工厂多实例)。
4. **启停与更新**:`enable/disable` 保持 UUID;`update --properties '{"plugin.mode": "x"}'` 后查询生效;hot 插件(如描述为 hot 的动态插件)更新不中断服务。
5. **重启恢复**:`Ctrl-C` 后重启 server → 实例以持久化 UUID 恢复(`plugin_history` 无新 `create_instance` 记录,组件运行中)。
6. **升级**:修改动态包版本后 `upgrade` → 新 UUID,history 出现 `upgrade` 且 `detail.replaced_instance` 指向旧 UUID。
7. **卸载**:删除实例后 `uninstall` 成功;仍有实例时报 `PluginHasInstancesError`;卸载后 history 出现 `uninstall_definition`。
8. **scope 管理**:`scope add agent:a --parent agent`;带实例的 scope 直接删除报错,`--recursive` 删除成功且实例随之消失。
9. **旧文件行为**:手工构造 schema=2 的 runtime_state.sqlite3 后启动 → 报 `RuntimeStateSchemaError` 且提示删除文件。

### 16.4 验收清单

- [x] `make check` 全绿(ruff/mypy/pyright/pytest ≥95%,实测 95.12%)
- [x] 全部设计文档"测试必须覆盖"条目在 16.1 矩阵中可指认
- [x] 手动 E2E 场景 1/2/3/5/9 通过(服务器装配、显式 scope、同 scope 多实例、重启 UUID 恢复、旧 schema 拒绝);4/6/7/8 由自动化测试覆盖(coordinator/e2e)
- [x] 无 `descriptor.properties/instance/scope` 残留引用
- [x] 16 个任务全部提交(分支 `feature/plugin_manager`,commit 3bde073 → 3c232bd)

**实施偏差记录(相对计划):**
1. `hot` 更新改为 kill+同 UUID 重建:该 Pelix 版本 iPOPO 服务无 `reconfigure` 方法;swap_policy 分支保留供未来 iPOPO 升级。
2. `coordinator.install` 缺省 scope = contribution.target 声明的 scope(而非 root):保持旧行为与 UX 一致;CLI/API 用户命令仍强制显式 scope。
3. `PluginPackage` 目录不做 (module, factory) 跨包去重:同一定义的多个安装请求(server + agent_instance)是合法用法;真实冲突由 registry/install_descriptor 把关。
4. 恢复时丢弃 agent:* 作用域实例(目录派生状态,重启重建),动态注册的 agent:* 实例由 coordinator 从 provenance 重建。
5. `_install_definition` 对相同 descriptor 的重装幂等(装配在 restore 之后运行)。
6. 持久化时对非 JSON 属性值做 repr 净化(服务对象注入 adapter properties)。

---

## Self-Review 记录

- **Spec coverage**:两份设计的每个章节均可指认任务——身份模型(T2/T3)、发现(T4)、持久化(T5/T10)、管理器四组 API(T7-T9)、Requires 自动配置(T6/T9)、coordinator(T11)、builtin(T13/T15)、API/CLI(T14)、验证(T16)。
- **Placeholder scan**:所有方法体给出完整代码;消费者改造给出精确模式与签名。
- **Type consistency**:`PersistedPluginRegistration` 新形状在 T5 定义、T11/T14 使用一致;`PluginInstanceSnapshot.status` 四值 union 在 T2 定义、T9/T10 使用一致;`install_descriptor(source=...)` 参数在 T8/T10/T13 一致。
