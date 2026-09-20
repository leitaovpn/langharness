# PluginDescriptor 固定属性与实例关系设计方案

## 目标

将插件的静态定义和运行时实例分离：

- `PluginDescriptor` 只描述一个可发现、可安装的插件定义，字段固定为：
  `name`、`version`、`module`、`factory`、`specification`、`swap_policy`、
  `description`。
- `scope` 在注册/实例化时指定；缺省为 `root`。scope 的父子关系继续由
  `ScopeTree` 管理，不写入 `PluginDescriptor`。
- 每次实例化自动生成 UUID 作为运行时 instance id，并持久化 instance 与
  descriptor、scope 的关联。
- AI 能够仅通过 `description` 理解插件用途、properties 配置方式、适用时机
  和卸载条件。

## 核心模型

### 1. 静态插件定义

```python
@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    name: str
    version: str
    module: str
    factory: str
    specification: str
    description: str
    swap_policy: Literal["hot", "restart"] = "restart"
```

`PluginDescriptor` 不包含以下运行时内容：

- `instance`：由实例化流程生成；
- `scope`、`scope_parent`：由注册操作和 `ScopeTree` 决定；
- `properties`：属于某个实例的配置；
- `enabled`、`ranking`：属于注册/实例状态或服务发布属性。

`description` 是面向 AI 的操作说明，建议使用结构化但可读的自然语言，至少
包含以下内容：

1. 插件提供什么能力，以及实现哪个 `specification`；
2. 何时应该使用它，何时不应该使用它；
3. `properties` 支持哪些参数、类型、默认值、示例和约束；
4. 哪些配置修改需要重启（由 `swap_policy` 决定）；
5. 什么情况下应禁用或卸载，以及卸载后的影响。

描述只作为提示和发现信息，不能替代运行时 schema 校验。插件需要对
properties 做程序化校验，并在错误时返回明确原因。

### 2. 运行时注册与实例

新增独立的运行时记录（名称可按实现选用 `PluginInstanceRecord`）：

```python
@dataclass(frozen=True, slots=True)
class PluginInstanceRecord:
    instance: str                 # UUID 字符串，iPOPO component name
    factory: str                  # 来自 PluginDescriptor
    module: str                   # 来自 PluginDescriptor
    scope_id: ScopeId             # 规范化后的 scope，缺省 root
    properties: dict[str, Any]    # 实例化时实际注入的 effective properties
    enabled: bool = True
    ranking: int = 0
    status: Literal["active", "disabled", "failed"] = "active"
```

注册接口接收 `PluginDescriptor` 和可选的 `scope_id`，先将缺省值归一化为
`ROOT_SCOPE_ID`，再创建实例记录。一个 descriptor 可以在多个 scope 创建多个实例；同一个结构化注册 key 在同一个 scope
也可以创建多个实例。每个实例通过 UUID 独立寻址，且每个实例持久化实例化时实际注入的
`effective_properties`；properties、enabled、ranking 和
生命周期彼此独立。若调用方需要幂等安装，应额外提供调用方生成的 `registration_key`；它不是 descriptor identity。

scope 的父子关系只通过 `ScopeTree` 查询和校验：注册时要求 scope 已存在，或
由现有的 scope 创建流程先创建；descriptor 本身不携带 parent 信息。

### 3. descriptor 与 instance 的稳定关联键

采用如下结构化 identity：

```python
PluginRegistrationKey(
    factory=descriptor.factory,
    module=descriptor.module,
    scope_id=scope_id,
)
```

其中：

- `factory` 是全局 plugin key；
- `module` 是承载该 factory 的 bundle 模块；
- 最后一段是规范化后的 scope id，缺省为 `root`；
- 三个字段分别保存和查询，不拼接成字符串；数据库中分别使用 `factory`、`module`
  和 `scope_id` 列。

逻辑关系为：

```text
(factory + module + scope) <-> one or more instance UUIDs
```

数据库中只保存拆分字段和必要的索引，不保存依赖 `@` 拼接的 canonical string。
`(factory, module, scope_id)` 用于 scoped definition 的匹配、审计和导出；它本身不唯一
标识 instance，需要幂等创建时另加 `registration_key` 或直接使用 instance UUID。
`description` 不参与 identity；修改
描述属于 descriptor metadata 更新，不应导致新 instance。

## 生命周期

### 注册/实例化

1. discovery 返回 descriptor；校验七个固定属性及 `description` 非空策略。
2. 调用方传入 `scope_id`，未传时使用 `root`；校验 scope 存在。
3. 计算结构化注册 key；如果调用方提供 `registration_key`，仅检查
   `(registration_key, scope_id)` 是否已有活动实例，否则直接创建新 UUID。
4. 使用 `uuid.uuid4()` 生成 instance id；不要再由包目录或 factory 名称拼接
   instance 名称。
5. 调用 iPOPO `instantiate(factory, instance, properties)`。
6. 校验组件是否满足 `specification` 对应的 Protocol。
7. 组件成功后，在同一事务中保存 descriptor、scope、实际注入的
`effective_properties`、instance 和
   状态；保存失败必须 kill 刚创建的组件并回滚内存索引。

实例 UUID 是稳定的运行时标识。重启恢复时优先使用持久化 UUID 重新实例化，
只有记录不存在、记录损坏或明确执行了“重新创建实例”操作时才生成新 UUID。

### 启停、属性更新和卸载

- disable：保留 descriptor key 与 instance 记录，停止组件并标记 `disabled`；
  再次 enable 默认复用原 UUID。
- properties 更新：按 `swap_policy` 决定热替换或标记 `restart_required`；热
  替换成功后保留 UUID，失败时恢复旧 properties 和旧组件。
- upgrade：descriptor version/module/factory 变化会生成新的 descriptor key。迁移
  过程先停止旧实例，再按目标版本创建实例；建议默认生成新的 UUID，并在审计中
  保存 `replaced_instance`，避免把不同版本误认为同一个组件。
- uninstall：停止并删除实例注册关系及其 properties；同时删除本地索引。若
  需要审计，写入 append-only lifecycle history，而不是继续保留可恢复的活动记录。
- scope 删除：沿用现有 `ScopeTree` 递归规则，先卸载该 scope 中的实例，再删
  scope；descriptor 定义本身只有在没有任何实例和安装引用时才可移除。

## 持久化设计

### 推荐存储结构

继续复用 `RuntimeStateStore` 的版本号和 compare-and-swap 保存语义，但将快照中的
plugin registration 从“descriptor 内嵌 instance”迁移为独立实例记录：

```json
{
  "version": 3,
  "scopes": [...],
  "descriptors": [
    {
      "name": "...",
      "version": "...",
      "module": "...",
      "factory": "...",
      "specification": "...",
      "swap_policy": "hot",
      "description": "..."
    }
  ],
  "instances": [
    {
      "instance": "uuid",
      "factory": "...",
      "module": "...",
      "scope_id": "root",
      "properties": {},
      "enabled": true,
      "ranking": 0,
      "status": "active"
    }
  ]
}
```

SQLite 实现可以继续使用单行 JSON 快照，也可以在第二阶段拆成
`plugin_descriptors`、`plugin_instances`、`plugin_history` 三张表。无论选哪种
物理实现，都必须满足：

- descriptor 和 instance 关系与 scope 一起原子保存；
- 保存带 expected version 的 CAS，避免两个进程覆盖状态；
- 启动恢复先加载 descriptor，再按 instance 记录恢复组件；恢复时必须使用记录中的
`effective_properties`，不能只读取 descriptor 或重新使用当前默认值；
- instance UUID 必须全局唯一；descriptor key 和 scope 允许一对多。数据库只对
  instance UUID 建唯一约束；若引入 registration_key，则对
  `(registration_key, scope_id)` 建可选唯一约束。

### 失败恢复

若组件创建成功但持久化失败，立即 kill 组件并回滚内存状态。若持久化成功但进程
在组件创建后崩溃，下一次启动按快照恢复；若 descriptor/module 不再可用，将实例
标为 `missing` 或 `failed`，保留记录供诊断和后续恢复，不静默删除关联关系。

## 现有代码影响

1. `langharness_plugin.registry.PluginDescriptor`：改为七个固定属性；移除
   `instance`、`scope`、`scope_parent`、`properties`、`enabled`、`ranking`。
2. `PluginRegistry`：从“按 descriptor.name 存储运行实例”调整为 descriptor
   catalog；允许同一 descriptor 在多个 scope 引用，也允许同一 scope 创建多个实例；
   仅 instance UUID 全局唯一，`registration_key`（若启用）按 scope 可选唯一。
3. `PluginManager`：
   - `instantiate_instance` 改为接收 scope 和 properties，返回生成的 UUID/实例
     记录；未提供 scope 时使用 root；
   - `_instantiate`、`_unbind`、`kill_instance` 不再从 descriptor 读取 instance；
   - 内存索引改为 `instance -> PluginInstanceRecord` 和
     `(factory, module, scope_id) -> list[instance]`；
   - iPOPO bundle 仍按 module 去重安装，组件实例按 UUID 创建。
4. `state_store.py`：新增 descriptor catalog 与 instance records 的序列化字段，
   提升 schema version，并保留旧版本读取迁移。
5. `coordinator.py` / `package.py` / `discovery.py`：descriptor contribution
   只携带静态定义；scope、properties、enabled 等在注册时产生。动态注册返回值
   应包含 `instance`、`factory`、`module` 和 `scope_id`，供 API/CLI 展示。
6. 内建 plugin assembly：所有静态 descriptor 补齐 `description`；原本写死的
   instance 名称迁移为注册时生成。
7. API/CLI：安装或实例化操作显式接收 scope（缺省按本方案归一化到 root 的
   内部 API 可保留，但用户可见的变更命令建议继续要求显式 scope）；输出同时
   展示 descriptor key、scope、instance、enabled/status。

## 迁移策略

- 读取现有 schema 时，将旧 descriptor 的 `instance` 作为一次性迁移提示，不把
  它当作新的稳定 UUID；为每个旧注册生成 UUID，并写入新的 descriptor/instance
  结构。
- 旧 `scope=None` 统一映射为 `root`；旧 `scope_parent` 丢弃，由现有 scope tree
  快照或 builtin scope 初始化恢复。
- 旧 `properties`、`enabled`、`ranking` 移入 instance record。
- 迁移采用“读旧、写新”并在成功保存后提升 schema version；写入新格式后不再
  回写旧格式。迁移失败保留原文件，并将错误报告为启动失败，避免部分状态。
- 迁移期间允许同一旧 descriptor 在同一 scope 生成多个 instance 记录；每条记录都
  必须获得独立 UUID。只有旧 instance 标识本身重复时才报告具体冲突。

## 校验与测试计划

- descriptor 七字段类型、swap policy、description 校验；description 缺失/过短
  的策略固定后加入 discovery 测试。
- 缺省 scope 映射 root；未知 scope、重复 instance UUID、非法字段均拒绝。
- UUID 每次新实例唯一；disable/enable 复用 UUID；upgrade 按规则生成新 UUID。
- SQLite/InMemory store 的保存、CAS 冲突、进程重启恢复和损坏记录处理。
- iPOPO 真框架测试覆盖同 module 多实例、同 descriptor 多 scope、kill/restore
  和 contract validation。
- API/CLI 输出验证 instance 与 descriptor key，并覆盖旧状态迁移。

## 需要确认的决策

1. **properties 的归属**：本方案将它定义为实例注册配置，不放进
   `PluginDescriptor`。如果你希望 properties 仍作为“默认配置模板”随 descriptor
   发布，需要再增加单独的 `property_schema` 或 `defaults` 字段；这会改变“固定七
   字段”的约束。
2. **scope 的默认值**：本方案内部 API 缺省使用 `root`，但建议用户可见的安装、
   启停、卸载命令继续要求显式 scope，以避免误操作。
3. **升级的 UUID 语义**：本方案默认版本升级生成新 UUID，并记录替换关系；若希望
   监控和外部引用跨版本保持不变，可以改为复用旧 UUID，但需要处理 iPOPO 旧组件
   与新组件的原子替换。
4. **descriptor identity 的字段编码**：示例使用 `@` 便于展示；实现需确认是
   采用转义后的字符串，还是以结构化 JSON/hash 作为内部 key、以 `@` 字符串仅作
   展示值。
5. **卸载后的历史**：本方案删除可恢复的活动实例记录，但建议另存 append-only
   lifecycle history。若不需要审计，可以省略 history。

### properties 恢复约束

`PluginInstanceRecord.properties` 保存的是传给 iPOPO `instantiate()` 的完整有效参数，
而不是仅保存用户最后一次提交的 patch。它至少包括：

- descriptor 或插件定义提供的默认值合并结果；
- 用户在注册或更新时提供的覆盖值；
- 实例需要的可恢复运行时配置。

由 scope 派生、且每次启动都能确定性重算的服务元数据（例如 scope chain、service
ranking）可以在恢复时重新注入，但必须在恢复前完成同样的规范化；不能让恢复后的
properties 与首次实例化时产生不同语义。若某个运行时字段由随机值或外部状态决定，
则必须一并持久化，不能依赖重算。

恢复流程应为：加载 descriptor → 加载 instance record → 读取该 record 的
`effective_properties` → 注入可重算的 scope 元数据 → 使用同一个 instance UUID
调用 iPOPO `instantiate()` → 校验 contract → 成功后标记 active。

## 动态发现

动态发现继续使用 Python `importlib.metadata` entry points，不扫描源码目录。entry
point 指向 class 或 callable：

1. 如果加载对象是 class，直接读取 `PluginMetadata` 和
   `@ComponentFactory` metadata，生成 `PluginDescriptor`；
2. 如果加载对象是函数，调用函数；函数必须返回插件 class，再按同一流程生成
   `PluginDescriptor`；返回 descriptor、package 或其他对象时记录 discovery failure。

发现阶段使用 `(module, factory)` 去重：同一 `(module, factory)` 重复时保留第一个
entry point，后续项忽略并记录 duplicate。不同 module 可以暂时拥有相同 factory。
真正注册到 iPOPO 时再执行全局 factory 校验；若 factory 已注册，则抛出异常，不能
覆盖已有定义。

如果生成的 descriptor.module 与插件 class 的真实 `__module__` 不一致，发现阶段
直接忽略该 entry point，并记录可诊断的 failure。其他 entry point 继续处理。

生成 descriptor 后必须立即执行完整字段校验。`name`、`version`、`module`、
`factory`、`specification` 和 `description` 必须是非空字符串；`swap_policy` 必须
是支持的策略值。任一字段为空、缺失或非法时，发现器只打印 warning，并跳过该
entry point：不加入 `DiscoveryResult.packages`、catalog 或持久化状态。该问题不应
阻止其他 entry point 继续发现。

## bundle、descriptor、instance 的身份边界

`_bundles` 不应使用 descriptor `name` 作为 key。`name` 是人类可读名称，允许出现
相同名称；它不能唯一标识 Pelix bundle，也不能唯一标识 descriptor。

iPOPO 的 factory 注册表是进程级全局命名空间，因此本方案将 `factory` 定义为
运行时的 plugin key：同一个 factory 只能注册一个组件定义。发现阶段允许不同
module 暂时出现同名 factory；注册阶段发现冲突时必须直接报错，不能让后加载的
bundle 覆盖前一个定义。

推荐 factory 命名规则：

```text
<fully-qualified-module>.<ClassName>-factory
```

例如：

```text
langharness_core.plugins.middleware.human_approval.HumanApprovalPlugin-factory
```

该名称是合法且可行的。它应作为 `@ComponentFactory(...)` 的固定值，并写入
`PluginDescriptor.factory`。命名规则要求模块路径、类名和后缀稳定；重命名类或模块
应视为新的 factory/descriptor，并按升级或迁移流程处理。`version` 不建议放入
factory 名称，否则同一组件跨版本升级会产生不同运行时 key；版本仍由 descriptor
字段记录。

建议使用三层索引：

```python
_bundles: dict[str, Bundle]                         # module -> Pelix bundle
_descriptors: dict[str, PluginDescriptor]           # factory -> definition
_instances: dict[str, PluginInstanceRecord]        # instance UUID -> record
```

其中：

- **bundle identity**：`module`。同一个 module 只安装一个 Pelix bundle；多个
  descriptor 可以共享它。只有当该 module 没有 descriptor 或 instance 引用时才卸载
  bundle。
- **descriptor definition identity**：静态 descriptor 的 key，优先使用全局唯一的
  `factory`；其余字段用于校验和版本元数据。
- **scoped registration identity**：结构化的 `(factory, module, scope_id)`。它标识
  一个 scope 内的 descriptor 注册关系，但仍然可以对应多个 instance UUID；不能用它
  作为 bundle key，也不拼接为字符串存储。
- **instance identity**：自动生成的 UUID。所有 enable、disable、属性更新、kill
  和恢复操作最终都应能落到 instance UUID。

因此，下面两项可以同时存在：

```text
same-name@1@package.alpha@factory-a@spec-a
same-name@1@package.beta @factory-b@spec-b
```

它们是两个不同 descriptor，共享 name 但不共享 module、factory 或 specification。
如果 module 相同而 factory 不同，也应共享一个已安装 bundle，只分别实例化各自的
factory。

### 同名插件的管理规则

- discovery/catalog 按 descriptor key 去重，不按 name 全局去重；完全相同的
  descriptor key 才视为重复贡献。
- 通过 package id + contribution id 安装时，不存在 name 歧义，因为贡献已唯一定位
  descriptor。
- 运行时按 name 查找时，如果匹配到多个 descriptor 或 instance，必须返回歧义错误，
  要求调用方提供 descriptor key、registration key 或 instance UUID；不能随机取第一个。
- API/CLI 展示应同时显示 name、descriptor key、module、scope 和 instance UUID。
- 兼容旧 API 的 `get(name)` 可以保留为仅在 name 唯一时返回结果；出现多个匹配时抛
  `AmbiguousPluginError`，并提供候选 descriptor key。

### bundle 生命周期

安装 descriptor 时：

1. 以 `descriptor.module` 查询 `_bundles`；不存在才调用
   `context.install_bundle(module)` 并启动 bundle；
2. 将 descriptor key 加入该 module 的引用集合；
3. 创建一个或多个 instance UUID。

卸载 descriptor 或 instance 时只删除对应引用。module 的最后一个 descriptor 和
instance 都消失后，才停止并卸载 bundle。这样同一个 module 不会被重复安装，也不会
因为卸载其中一个 descriptor 而破坏同 module 的其他 descriptor。
