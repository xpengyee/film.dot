# FilmAgent V0.2 阶段 1 —— 验收报告

**任务**：统一数据模型 + V0.1 双向转换 + QA/Validation 校验器骨架 + Asset 注册表骨架
**日期**：2026-08-31
**性质**：纯代码、零 LLM

---

## 1. Git 基准

| 项目 | 值 |
|---|---|
| HEAD | `344c0c6b7c48497be934957c29fcae029cca6752` |
| v0.1^{commit} | `344c0c6b7c48497be934957c29fcae029cca6752` |
| git status --short | 仅 `?? v2/`（未跟踪的新工作区） |
| V0.1 是否修改 | **NO**（`git diff -- FilmAgent/` 为空） |

开工前、收工后均核验，V0.1 冻结状态保持。

## 2. 新增文件

```text
v2/core/models.py                              统一数据模型 + V0.1 双向转换
v2/core/validator.py                           三个校验器（Enum/Reference/Schema）
v2/core/assets.py                              Asset 注册表（locations/actions/shots/positions）
v2/logs/stage1_validation_test/run_tests.py    19 项纯代码测试
v2/logs/stage1_validation_test/stage1_report.json   测试报告（结构化）
v2/logs/stage1_validation_test/stage1_console.txt   测试控制台输出
v2/logs/stage1_validation_test/stage1_report.md     本报告
```

没有修改 `v2/core/io.py`（阶段 0 产物），没有创建数据库/Web 框架/Agent 框架等多余依赖。

## 3. 数据模型（core/models.py）

### 定义了哪些 Model

- 资产：`Asset`（基类）
- 站位点：`Position`（场地资产定义）、`PositionRef`（场景里"谁在哪"）
- 角色：`Character`、`CharacterMemory`（阶段 1 只定义结构，不实现写入）
- 场景：`SceneInfo`（who/where/what）、`Action`、`Move`、`Line`（dialogue/move 两种）、`Scene`
- 跨集：`Episode`、`World`、`Project`
- 工作流：`WorkflowStepState`、`WorkflowState`（只定义结构，不实现 Workflow）

### V0.1 → V0.2 怎么转换（from_v01）

**宽容读入，认识所有旧字段写法**。实测发现 V0.1 字段名有三种变体：

| 含义 | scenes_2 | scenes_8 | script/0 |
|---|---|---|---|
| 场景信息 | `scene_information` | `sceneinformation` | `scene information` |
| 行列表 | `dialogues` | `dialogues` | `scene` |
| 镜头 | （无） | `selected shot` | `shot` |

转换层用 `_first_key()` 逐个候选取值，**不再使用 `return_most_similar()` 模糊匹配**。
scenes_8 里动作自带的 `reason`、走位行顶层的 `reason`/`insertion` 都会读入保存。

### V0.2 → V0.1 怎么转换（to_v01）

**统一按最终剧本格式输出**（script/0.json 的结构：`scene information` / `scene` / `shot` / `current position` / `initial position`）。
可选项（reason/insertion）只在非空时写出，因此最终剧本 round-trip 能做到**与原数据完全相等**。

## 4. Validator（core/validator.py）

| 校验器 | 检查什么 | 错误码示例 |
|---|---|---|
| EnumValidator | 值是否在合法集合：地点 / 动作 / 镜头 / 站位点 / 状态 | INVALID_LOCATION / INVALID_ACTION / INVALID_SHOT / INVALID_POSITION / INVALID_STATE |
| ReferenceValidator | 引用关系：who/speaker/action/position/move 里的角色必须存在；同一位置不能被两人同时占用 | UNKNOWN_CHARACTER / POSITION_CONFLICT |
| SchemaValidator | 结构完整与类型：scene 有 id/info/lines/initial_positions；台词行有 type/speaker/content/actions/shot/positions；走位行有 type/move/shot/positions；字段类型正确 | MISSING_FIELD / WRONG_TYPE / INVALID_STRUCTURE |

关键设计（符合指令要求）：
- **绝不调用 LLM**；返回完整 `ValidationError` 列表而非 True/False（`[]`=通过）。
- **不用 `assert` 做业务校验**；不用 `return_most_similar()` 做核心校验。
- 非法地点报 `INVALID_LOCATION`，绝不抛 `FileNotFoundError`。
- 校验器不从磁盘读文件，只依赖 AssetRegistry 注入的合法资产 → 未来可替换数据来源。

## 5. AssetRegistry（core/assets.py）

| 能力 | 方法 | 说明 |
|---|---|---|
| 全部地点 | `location_names()` | 15 个地点 |
| 地点存在？ | `has_location(name)` | 非法地点在此拦截 |
| 某地点的站位点 | `get_positions(name)` | 不存在时抛 KeyError（明确报错） |
| 站位点存在？ | `has_position(location, pos)` | 按地点判断，不跨地点 |
| 动作存在？ | `has_action(name)` | **自动剥离编号变体**（"Standing Talking 5"→"Standing Talking"） |
| 镜头存在？ | `has_shot(name)` | 同样剥离编号变体（"Long Shot 2"→"Long Shot"） |

数据全部来自 `v2/assets/locations/`（阶段 0 从 V0.1 逐字节迁移，本次只读不写）。
`from_dicts()` 支持内存注入，为未来 Project/World 替换数据来源预留。

## 6. 测试结果（19/19 通过）

| 测试 | 结果 |
|---|---|
| T1 合法地点 | PASS |
| T2 非法地点（必须 INVALID_LOCATION，不能 FileNotFoundError） | PASS |
| T3 非法动作 + 编号变体剥离 | PASS |
| T4 非法镜头 + 编号变体剥离 | PASS |
| T5 非法站位点（含走位目标位置） | PASS |
| T6 非法角色（speaker 与 who） | PASS |
| T7 script/0.json 双向转换 Round-trip | PASS |
| 　　　· 转换后与原数据 **deep-equal 完全相等** | PASS |
| 　　　· 核心字段完整（3 场景 / 49 行 / 61 动作 / 152 位置 / 9 初始站位） | PASS |
| 　　　· 真实最终剧本整体校验 **0 errors** | PASS |
| T8 字段名变体兼容（scenes_2 / scenes_8，含 reason/insertion 保留） | PASS |
| T9 真实数据 state ⊆ {standing, sitting}（实测只有 standing） | PASS |
| T10 重试不覆盖轨迹（attempts 保留 v1/v2/v3，approved_output 指向 v3） | PASS |
| T11 角色清单 round-trip（actors_profile deep-equal） | PASS |

## 7. LLM 调用次数

```text
LLM calls = 0
```

全程只读真实 JSON + 内存构造数据，未读取 api_key，未触碰 Prompt。

## 8. V0.1 完整性

```text
V0.1 modified = NO
```

`git diff -- FilmAgent/` 为空；未写任何文件到 `FilmAgent/Script/` 或 `FilmAgent/Locations/`。

## 9. 两个已锁定的架构决策

**决策① 跨集 Casting**：已有角色复用 + 增量新增。
每集 casting = 复用 `Project.world.characters` 中已有的角色 + 只创建本集需要的新角色，结果合并回角色清单。
绝不是每集重造一套角色，也不是跳过 casting。已记录在 `models.py` 的 `Project` 注释中，阶段 3 实现。

**决策② 重试不覆盖轨迹**：版本化保存。
`scenes_5.json` → `scenes_5_v2.json` → `scenes_5_v3.json`，历史版本全部保留，
`WorkflowStepState.approved_output` 指向最终通过的版本。已实现数据结构并测试（T10），阶段 3 实现 Workflow。

## 10. 发现并记录的 V0.1 细节（future migration，本阶段不修改）

1. 字段名不统一：`scene information` / `scene_information` / `sceneinformation`（空格/下划线/连写）。
2. 动作与镜头存在"基础名 + 编号变体"（`Standing Talking` vs `Standing Talking 5`）。
3. scenes_8 与 script/0 的差异：scenes_8 动作带 `reason`、走位带顶层 `reason`+`insertion`，最终剧本全部去掉。
4. V0.1 曾因编剧幻觉地点 "Billiard room kitchenette" 崩溃（无地点校验）——正是本阶段 INVALID_LOCATION 要拦截的案例。

## 11. 下一阶段（建议，不自动执行）

```text
阶段 2 —— LLMClient + token/成本统计
```

等待用户明确批准后再进入。
