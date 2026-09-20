# PluginManager 重设计方案

## 目标

按照新的身份模型重写 `PluginManager`：

- discovery 只发现并缓存 `PluginDescriptor`，不持久化；
- `module` 标识 Pelix bundle；
- `factory` 是全局 plugin key，标识插件组件定义；
- instance 使用自动生成的 UUID 标识；
- 同一个 plugin 可以在同一个 scope 创建多个 instance；
- instance 的 effective properties 独立保存和恢复；
- scope 由 `ScopeTree` 管理，不写入 `PluginDescriptor`；
- 对外只暴露插件、实例和 scope 三组清晰的管理接口。

## 身份模型

```text
module                         -> 一个 Pelix bundle
factory                        -> 一个 plugin definition
(factory, module)              -> 一个已安装 plugin definition
(factory, module, scope_id)    -> 一个 scope 内的注册关系
instance UUID                  -> 一个具体运行实例
```

`name` 是展示名称，不作为唯一 key。`version`、`specification`、`description` 和
`swap_policy` 是 descriptor 元数据。

数据库和内存索引都使用独立字段，不拼接 `factory@module@scope` 字符串：

```python
PluginRegistrationKey(
    factory: str,
    module: str,
    scope_id: ScopeId,
)
```

一个 registration key 可以对应多个 instance UUID。

## 对外 API

以下方法是 `PluginManager` 的稳定公开接口。具体返回值建议使用不可变的
snapshot dataclass，避免调用方修改 manager 内部状态。

### Discovery

```python
def discover(self) -> DiscoverySnapshot: ...
```

行为：

- 扫描 `langharness.plugins` entry points；
- entry point 指向 class 时直接读取 class metadata；
- entry point 指向 callable 时调用 callable，要求返回 class，再读取 class metadata；
- 生成并校验 `PluginDescriptor`；
- 按 `(module, factory)` 去重，重复时保留稳定排序后的第一个；
- module 与 class 的真实 `__module__` 不一致时 warning 并忽略；
- descriptor 固定字段为空或非法时 warning 并忽略；
- 不安装 bundle，不创建 instance，不写入持久化存储；
- 返回本次发现的 snapshot，同时替换内存中的 discovery catalog。

`PluginManager.__init__` 完成一次 `discover()`。后续只有主动调用 `discover()` 才会
重新扫描；重新发现不会自动安装、升级、卸载或修改现有 instance。

```python
@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    descriptors: tuple[PluginDescriptor, ...]
    warnings: tuple[str, ...]
    discovered_at: datetime
```

### Plugin definition 管理

```python
def install_plugin(
    self,
    factory: str,
    *,
    module: str | None = None,
) -> PluginDefinitionSnapshot: ...

def uninstall_plugin(
    self,
    factory: str,
    *,
    module: str | None = None,
) -> None: ...

def list_plugin(self) -> tuple[PluginDefinitionSnapshot, ...]: ...

def show_plugin(
    self,
    factory: str,
    *,
    module: str | None = None,
) -> PluginDefinitionSnapshot: ...

def get_service(
    self,
    specification: str,
    *,
    filter: str | None = None,
) -> Any | None: ...
```

`install_plugin` 的语义是安装 plugin definition 和其 bundle，不是创建 instance：

1. 从 discovery catalog 按 factory 查找 descriptor；
2. factory 不存在时报 `PluginNotFoundError`；
3. factory 存在但 module 不匹配时报 `PluginIdentityConflictError`；
4. 检查 factory 是否已被另一个 module 注册；重复 factory 直接报错；
5. 以 module 查询 `_bundles`，没有时安装并启动 Pelix bundle；
6. 在 `_plugins` 中记录 `(factory, module, version, descriptor)`；
7. 安装成功后持久化 definition installation state。

不同 module 使用同名 factory 可以出现在 discovery 结果中，但不能同时 install。
`module` 参数用于解决 discovery 中的歧义；factory 唯一时可以省略。

`uninstall_plugin` 只允许在该 plugin 没有 instance 时执行。若仍有 instance，抛出
`PluginHasInstancesError`；调用方必须先逐个删除 instance。卸载成功后，如果 module
不再被任何 plugin definition 引用，则停止并卸载 bundle。

`list_plugin` 返回所有已安装 definition；`show_plugin` 返回 descriptor 元数据、
安装状态、bundle 状态和 instance 数量，但不内嵌 instance 的完整 properties。

`get_service` 查询 Pelix service registry，而不是 instance registry：

1. 按 `specification` 和可选 LDAP `filter` 查找 service references；
2. 没有匹配的 service registration 时返回 `None`；
3. 有多个匹配时返回 ranking 最高的 service；
4. 找到 reference 后调用 `context.get_service(reference)` 返回服务对象。

它不会创建 plugin 或 instance，也不会因为服务尚未实例化而抛出异常。需要查看或
操作具体 instance 时，使用 `get_instance(instance_uuid)`；需要获取全部匹配服务时，
后续可以增加 `list_service`，但不改变 `get_service` 返回单个最佳服务的语义。

```python
@dataclass(frozen=True, slots=True)
class PluginDefinitionSnapshot:
    descriptor: PluginDescriptor
    installed: bool
    bundle_module: str
    instance_count: int
    scopes: tuple[ScopeId, ...]
```

### Instance 管理

```python
def create_instance(
    self,
    factory: str,
    module: str,
    scope_id: ScopeId,
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
```

`create_instance` 只允许针对已 install 的 plugin definition 创建实例；它不安装
bundle，也不修改 discovery catalog。创建流程必须遵守以下规则：

1. plugin definition 已 install；
2. `factory`、`module` 和 `scope_id` 都是必填项；校验 module 与已安装 definition
   一致，并校验 scope 存在；
3. 合并 descriptor 默认值与调用方 properties，得到完整
   `effective_properties`；
4. 生成 `uuid.uuid4()` 作为 instance id；
5. 注入 scope 派生的运行时属性；
6. 调用 `ipopo.instantiate(descriptor.factory, instance_uuid, properties)`；
7. 通过 specification 对应的 Protocol 校验组件；
8. 在同一事务中保存 instance record。

`PluginInstanceSnapshot` 必须包含恢复所需的完整配置：

```python
@dataclass(frozen=True, slots=True)
class PluginInstanceSnapshot:
    instance: str
    factory: str
    module: str
    scope_id: ScopeId
    properties: Mapping[str, Any]       # 实际注入的 effective properties
    enabled: bool
    ranking: int
    status: Literal["active", "disabled", "failed", "missing"]
```

`update_instance` 只影响指定 UUID。它不会隐式修改同一个 plugin 的其他 instance。
properties 更新按 descriptor.swap_policy 执行：`hot` 立即替换并保留 UUID，`restart`
标记 restart required 或由上层安排重启。更新失败时恢复旧组件和旧 snapshot。

`delete_instance` 停止组件、删除 instance record 和 properties；plugin definition
仍保留，其他 scope 或 instance 不受影响。

### Requires / RequiresBest 的自动配置

`@Requires` 和 `@RequiresBest` 是插件类上的静态依赖声明；它们不能在创建 instance
时动态改变。instance 创建时由 `PluginManager` 通过 iPOPO 的 `requires.filters`
属性配置每个依赖字段的可见服务范围。

插件类必须显式声明哪些依赖字段是 scoped，哪些依赖是全局的。例如可以使用类级
metadata：

```python
@ScopedDependencies(
    "_llm_provider",
    "_tool_providers",
)
class AgentLoop:
    ...
```

未声明为 scoped 的 `Requires` / `RequiresBest` 字段不自动添加 scope filter，继续
使用全局服务查找。这样不会误伤 root 级 coordinator、日志或配置服务。

创建 instance 时，manager 自动加入以下运行时属性：

```python
properties["plugin.scope_id"] = str(scope_id)
properties["plugin.scope_chain"] = [
    str(item) for item in visible_scopes(scope_id)
]
properties["plugin.key"] = descriptor.factory
properties["plugin.instance_id"] = instance_uuid
properties["requires.filters"][field] = scope_filter(scope_id)
```

scope filter 的形式为：

```text
(|(plugin.scope_id=<self>)(plugin.scope_id=<parent>)...(plugin.scope_id=<root>))
```

它只限制候选服务的可见范围。`Requires` 的 aggregate 行为和 `RequiresBest` 的
最佳服务选择仍由 iPOPO 及现有 scope policy 负责；manager 不把两者混为同一种
注入语义。

如果调用方或插件自身已经提供同一字段的用户 filter，manager 必须做 AND 合并：

```text
(&(<scope-filter>)(<user-filter>))
```

不能静默覆盖用户 filter。所有生成和合并后的 LDAP filter 在 instantiate 前校验；
非法 filter 使本次 instance 创建失败并回滚，不留下半创建组件。

scope 规则属于 instance 的 effective properties，必须持久化；恢复时使用同一个
scope_id 和 instance UUID 重新生成确定性的 scope metadata，再与持久化的用户配置
合并后调用 iPOPO。若 scoped 字段集合或 filter 规则发生变化，应按 plugin 的
`swap_policy` 执行热替换或要求重启。

### Scope 管理

```python
def list_scope(self) -> tuple[Scope, ...]: ...

def add_scope(
    self,
    scope_id: ScopeId,
    *,
    name: str,
    parent_id: ScopeId | None = None,
) -> Scope: ...

def remove_scope(
    self,
    scope_id: ScopeId,
    *,
    recursive: bool = False,
) -> None: ...
```

- `list_scope` 返回 scope tree snapshot；
- `add_scope` 校验 parent 存在，缺省 parent 为 root；重复 scope 或 parent 不一致时报错；
- `remove_scope` 默认拒绝包含子 scope 或 instance 的 scope；`recursive=True` 时先
  删除目标 scope 及子 scope 中的所有 instance，再删除 scope；
- scope tree 与 instance 状态在同一个 runtime state transaction 中持久化。

## 内存结构

```python
class PluginManager:
    _discovered: dict[tuple[str, str], PluginDescriptor]
    _plugins: dict[tuple[str, str], PluginDefinitionSnapshot]
    _bundles: dict[str, Bundle]                         # module -> bundle
    _module_refs: dict[str, set[tuple[str, str]]]
    _instances: dict[str, PluginInstanceSnapshot]       # UUID -> instance
    _registration_instances: dict[
        tuple[str, str, ScopeId], set[str]
    ]
```

不再使用以下结构：

```python
_bundles[name]
descriptor.instance
descriptor.scope
descriptor.properties
(scope_id, plugin_key) -> single instance
```

## 持久化

Discovery catalog 不持久化。持久化内容分为两类：

1. **installed plugin definitions**：factory、module、version、enabled/installed
   状态；
2. **instance records**：instance UUID、factory、module、scope_id、完整 properties、
   enabled、ranking、status。

启动顺序：

```text
初始化 manager
  -> discover()
  -> 加载 scope tree
  -> 加载 installed definitions
  -> 按 factory + module 匹配 discovery descriptor
  -> 安装 bundle
  -> 按 instance record 恢复 instance UUID 和 effective_properties
```

发现不到已持久化 descriptor 时，不删除记录，将 plugin/instance 标记为 `missing`。
发现到但 version 不同，标记 `upgrade_available`，不在启动时自动替换。

所有 install、uninstall、create、update、delete、scope mutation 使用同一个带版本号
的 CAS transaction。组件变更失败时先回滚运行时组件，再提交持久化状态。

## 并发与错误

Manager 内部使用一个 `RLock` 串行化 bundle、instance、scope 的组合变更；外部存储
使用 expected version 防止多进程覆盖。

建议错误类型：

- `PluginNotFoundError`
- `PluginAlreadyInstalledError`
- `PluginIdentityConflictError`
- `PluginHasInstancesError`
- `InstanceNotFoundError`
- `InstanceStateError`
- `ScopeNotFoundError`
- `ScopeHasChildrenError`
- `ScopeHasInstancesError`
- `ContractViolationError`
- `PluginStateConflictError`

## 迁移与测试

迁移旧 descriptor 时：

- 丢弃 descriptor 上的 instance、scope、scope_parent、properties、enabled 和 ranking；
- 将旧 properties、enabled、ranking 移入 instance record；
- 为每条旧实例记录生成 UUID；
- 旧 scope 缺省映射 root；
- 旧 name 索引迁移为 `(factory, module)`；若 factory 冲突，保留运行时记录并标记
  `PluginIdentityConflictError`，不覆盖任何已注册 definition。

测试必须覆盖：

- discovery 初始化一次与主动 rescan；
- class entry point、callable entry point、invalid return；
- module mismatch 和空 descriptor warning/skip；
- 同 module/factory discovery first-wins；
- 不同 module 同 factory discovery 成功、install 冲突；
- 同一 module 多 factory 共享 bundle；
- 同一 plugin/scope 多 instance；
- instance properties 独立更新和重启恢复；
- 删除最后一个 instance 后才能 uninstall plugin；
- scope 删除、递归删除和持久化 CAS 冲突。
