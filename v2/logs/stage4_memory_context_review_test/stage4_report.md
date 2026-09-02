# FilmAgent V0.2 阶段 4 —— 验收报告

**任务**：Memory + ContextAssembler + 人工审核节点 + 与 Workflow 最小集成 + 完整本地测试
**日期**：2026-09-01
**性质**：纯本地基础设施（真实 LLM 调用 **0 次**）

---

## 1. 本阶段完成内容

```text
已有项目状态
      ↓
Memory（事实层：candidate -> approved/rejected；角色 / 世界观 / 历史产出）
      ↓
ContextAssembler（按 Step 声明的 needs 照单取货，只装需要的内容，带来源信息）
      ↓
为 Step 准备 Context
      ↓
Workflow Step（阶段 3 引擎上扩展 needs_review 审核节点）
      ↓
产出（Artifact 版本化，绝不覆盖）
      ↓
人工审核（approved / approved_with_edit / rejected）
      ↓
approved -> Workflow 继续；rejected -> Workflow 停止
```

最终形成「Project → Memory/Assets → ContextAssembler → Workflow Step →
Artifact → Review → 继续/停止」的**完整基础闭环**（指令 §最终目标）。

本阶段**没有实现任何业务 Agent**（编剧/Casting/Scene/Dialogue/Shot），
符合指令 §二 的要求。

## 2. 新增 / 修改文件清单

**新增（3 个新模块，9 个文件）：**

```text
v2/memory/__init__.py      Memory 模块入口
v2/memory/models.py        FactStatus / Fact（candidate/approved/rejected 三态）
v2/memory/store.py         MemoryStore（JSON 持久化 + 项目隔离 + revision 追踪）
v2/context/__init__.py     Context 模块入口
v2/context/models.py       ContextSource / ContextItem / AssembledContext（可追踪）
v2/context/assembler.py    ContextAssembler（声明式 needs + provider 注册表）
v2/review/__init__.py      人工审核模块入口
v2/review/models.py        ReviewStatus / ReviewDecision / Review / ReviewResult
v2/review/service.py       ReviewService（submit/approve/approve_with_edit/reject + 持久化）
```

**扩展（阶段 3 产物，向后兼容的最小扩展）：**

```text
v2/workflow/step.py     Step 增加 needs（声明式需求清单）、needs_review（人工审核开关）
v2/workflow/models.py   StepStatus 增加 APPROVED_WITH_EDIT / REJECTED；
                        StepResult 增加 review_id；is_success 覆盖 APPROVED 家族状态；
                        to_core_workflow_state 映射新状态
v2/workflow/engine.py   增加 review_handler（审核节点）；RetryError 直接冒泡；
                        成功判定改用 is_success（含审核通过）
v2/workflow/__init__.py 文档说明阶段 4 扩展
```

**测试：**

```text
v2/logs/stage4_memory_context_review_test/run_tests.py   测试套件（25 项断言）
v2/logs/stage4_memory_context_review_test/stage4_report.json  测试报告（机器可读）
v2/logs/stage4_memory_context_review_test/stage4_console.txt  控制台输出
v2/logs/stage4_memory_context_review_test/stage4_report.md    本报告
```

没有修改阶段 0~2 的任何产物（core/、llm/、config.py 均未动）。

## 3. Memory 架构说明

遵循架构报告 §6.5「事实层 + 摘要层」原则：

```text
事实层（known_facts，结构化、高可信 Source of Truth）
    facts.json          candidate / approved / rejected 三态，全保留
    characters.json     角色信息
    world.json          世界观 / 设定
    history.json        历史产出引用（如上一集 artifact）
摘要层（summary.json，LLM 生成、辅助理解）——本阶段不实现生成，仅预留概念
```

**项目隔离**：一个 project 一套目录 `v2/projects/<project_id>/memory/`，
不同 project 物理隔离，互不污染（测试 T6 证明）。

**revision**：每次写操作 +1 并落盘，供 Context 标注"这一版事实是第几版"（可追踪）。

## 4. known_facts 数据流说明

解决第四十七章问题一（known_facts 由谁产出）——结论：**绝不纯 LLM 自动写**：

```text
LLM / Agent 提出候选事实（本阶段用 add_candidate_fact 接口，真实 memory_writer 后续接）
      ↓
candidate facts（status=candidate，未经确认绝不进 approved）
      ↓
人工 approve / reject
      ↓
approved facts（进入 MemoryStore，供 ContextAssembler / 后续 Step 使用）
```

- `add_candidate_fact(...)` 提出候选（默认 source="memory_writer"）
- `approve_fact(fact_id, reviewer, comment)` 人工确认
- `reject_fact(fact_id, reviewer, comment)` 人工拒绝
- `get_approved_facts()` 只有 approved 才会被读取（ContextAssembler 只用这个）

candidate 与 approved 分开存，绝不混在一起（测试 T3/T5 证明）。

## 5. ContextAssembler 架构说明

定位为「资料管理员，而不是 Agent」（架构报告 §七）——纯代码组件，**不调 LLM**。

```text
Step 声明 needs
      ↓
ContextAssembler 按 needs 查 provider 注册表取货
      ↓
Memory（approved_facts / characters / world / history）
    + Assets（locations / actions / shots）
    + Workflow（previous_artifacts / workflow_state）
    + Project（project_info）
      ↓
AssembledContext（只装 Step 需要的，每条带来源信息）
```

同时承担「上下文组织 + 上下文裁剪」：
- 没声明 locations 的 Step，Context 里绝不出现 locations（测试 T10 证明）；
- 每条内容记录 char_count，作为上下文规模的可观测度量
  （真正的 token 统计是 LLMClient/token_log 的职责，本模块不越权）。

## 6. needs 声明机制说明

遵循第四十七章问题五的决策——**声明式配置，禁止 if/else 按 Step 名硬编码**：

```python
class LinesGenerateStep(Step):          # 概念示意（业务 Agent 后续阶段实现）
    needs = ["approved_facts", "characters", "world"]
```

`ContextAssembler.assemble(step, context)` 遍历 `step.needs`，
每个 needs 名对应 provider 注册表里的一个取货函数（`_provide_approved_facts` 等）。
新增"信息种类"只需加一个 provider，不改组装逻辑。

needs 名注册表（11 个）：
`project_info / approved_facts / candidate_facts / characters / world / history /
previous_artifacts / workflow_state / locations / actions / shots`

声明了未注册的 needs 会**明确报错**（宁可报错，不静默跳过）。

## 7. Review 状态机说明

阶段 3 预留了 needs_review；本阶段正式接入 Workflow 状态机（指令 §十四）：

```text
Step 成功（run + validate 通过）
      ↓ Step.needs_review = True
NEEDS_REVIEW
      ├── approved              人工确认通过 -> APPROVED -> 继续下一 Step
      ├── approved_with_edit    人工修改后批准 -> APPROVED_WITH_EDIT -> 继续下一 Step
      └── rejected              人工拒绝 -> REJECTED -> Workflow 停止
```

阶段 3 已有状态（PENDING/RUNNING/SUCCESS/FAILED/RETRYING/SKIPPED）**未破坏**，
`is_success` 扩展覆盖 APPROVED / APPROVED_WITH_EDIT（阶段 3 38/38 回归通过证明兼容）。

## 8. approved_with_edit 实现说明

**这是本阶段重点**（第四十七章问题四：审核必须允许"改"）：

- 不是只改一个 status 字段——`ReviewService.approve_with_edit` 用
  `ArtifactStore.save()` 真正写一个**编辑后的新版本文件**（如 `scene_1_v2.json`）。
- 原始文件 `scene_1.json` 原封不动（测试 T15 证明）。
- Engine 侧直接采用审核层已写盘的编辑产物（`ReviewResult.edited_artifact`），
  不重复写盘；若自定义 handler 只给内容、没写盘，Engine 兜底写新版本。
- 审核记录能回答：原始版本是什么 / 谁审核 / 何时 / 改成了什么 / 最终采用哪一版
  （Review 记录含 original_artifact / reviewer / reviewed_at / edited_artifact / comment）。

## 9. Artifact 与 Review 的版本关系

```text
attempt-1  -> scenes_5.json        （引擎产出，版本 1）
      ↓ needs_review
human edit -> scenes_5_v2.json     （人工编辑产物，版本 2，原始保留）
      ↓ approved_with_edit
最终采用   -> scenes_5_v2.json      （approved_output 指向编辑后的新版本）
```

完全复用阶段 3 的 Artifact 版本机制（版本号 = 磁盘已有版本数 + 1），
**没有重新造一套版本系统**（指令 §十三）。

## 10. Workflow Engine 集成方式

采用**扩展而非重构**（指令 §十七）：

- Engine 构造新增可选参数 `review_handler`（签名 `review_handler(artifact, context) -> ReviewResult`），
  默认 None（不配置时，任何 needs_review Step 会明确报 RetryError，不静默）。
- `_run_step` 成功路径：`step.needs_review=True` 时调用 `_handle_review`；
  按决策继续 / 停止。rejected 后 run 失败即停（后续 Step 不执行，测试 T18 证明）。
- 生产环境若要"暂停等人工"，可把 handler 换成"run 落盘 → 人工审核 → resume 续跑"
  （Engine 的 resume 已支持跳过已成功 Step）。

## 11. 测试数量与结果

**25 项断言全部通过**（指令 §十九要求的 20 项全覆盖，其中几项含多个断言）：

| 编号 | 覆盖点 | 结果 |
|---|---|---|
| T1  | Memory 创建项目（建目录） | PASS |
| T2  | 写入 candidate fact | PASS |
| T3  | candidate 不直接成为 approved | PASS |
| T4  | approve 后可以读取 | PASS |
| T5  | reject 后不进 approved context | PASS |
| T6  | project A/B Memory 隔离 | PASS |
| T7  | Memory 持久化后重读一致 | PASS |
| T8  | Step 可以声明 needs（含默认兼容） | PASS×2 |
| T9  | ContextAssembler 按 needs 组装 | PASS |
| T10 | 不需要的数据不进 Context | PASS |
| T11 | Context 包含来源信息 | PASS |
| T12 | approved review | PASS |
| T13 | approved_with_edit review（产生新版本） | PASS |
| T14 | rejected review | PASS |
| T15 | 人工修改不覆盖原始 artifact | PASS |
| T16 | review 状态进入 Workflow（approved / approved_with_edit / 未配 handler 报错） | PASS×3 |
| T17 | approved 后 Workflow 继续 | PASS |
| T18 | rejected 后 Workflow 停止 | PASS |
| T19 | 阶段 3 原有 38/38 继续通过 | PASS |
| T20 | V0.1 仍然完全没有修改 | PASS |
| R1  | 阶段 1 回归 19/19 | PASS |
| R2  | 阶段 2 回归（纯 mock，7 项断言） | PASS |

## 12. 阶段 1 回归测试结果

重新运行 `v2/logs/stage1_validation_test/run_tests.py` → **19/19 通过**（纯本地，0 LLM）。

## 13. 阶段 2 回归测试结果

按指令 §二十要求**不做真实 API 调用**，改用 mock 复验阶段 2 的核心能力：
配置读取（3 项）+ 异常处理（2 项）+ 重试机制（1 项）+ 失败重试上限（1 项）=
**7 项断言全部通过，0 次真实调用**。真实调用能力已在阶段 2 验收，不重复计费。

## 14. 阶段 3 回归测试结果

重新运行 `v2/logs/stage3_workflow_test/run_tests.py` → **38/38 通过**（0 LLM）。
确认阶段 4 对 workflow 的扩展**没有破坏**阶段 3 的任何行为。

## 15. LLM 调用次数

**0 次**。本阶段严格遵循指令 §十五/§十九：人工审核是 Python/API 层明确操作，
绝不调用 LLM 冒充审核；测试全部用本地 fixture / mock。

## 16. token / cost

**token = 0，cost = ¥0**（无任何真实或 mock 之外的 API 消耗）。

## 17. V0.1 完整性检查

```text
git diff -- FilmAgent/     = 空
FilmAgent/ Prompt/ Locations/ Script/ LLMCaller.py main.py   均未触碰
```

## 18. Git HEAD / v0.1 状态

```text
git rev-parse HEAD          = 344c0c6b7c48497be934957c29fcae029cca6752
git rev-parse v0.1^{commit} = 344c0c6b7c48497be934957c29fcae029cca6752
git status --short          = 仅 ?? v2/
```

## 19. 遇到的问题

阶段 4 测试首次运行 **21/25**，4 项 FAIL，全部定位并修复后 **25/25**：

1. **T1 目录未建**：`MemoryStore` 构造时只设路径、未建目录（write 时才建）。
   修复：构造时 `mkdir`——"创建项目即建 Memory 目录"。
2. **T6 测试断言 bug**（非模块 bug）：project_B 只 add_candidate_fact 未 approve，
   自然 approved 为空。修复测试：B 也 approve 自己的事实。
3. **T16 重复写盘**：Engine 和 ReviewService 各写一次编辑产物（产生冗余的 _v3）。
   设计缺陷。修复：ReviewResult 增加 `edited_artifact`，Engine 直接采用审核层
   已写盘的编辑产物，不重复写盘；自定义 handler 未写盘时兜底。
4. **T16 报错被吞**：`_handle_review` 在 try 块内抛 `RetryError`，
   被 `except WorkflowError` 当作可重试失败记录、未冒泡。修复：`except RetryError: raise`
   先于 `except WorkflowError`，让配置类错误直接报告给调用方。

**没有隐藏任何 FAIL。** 上述修复均不影响阶段 3 行为（回归 38/38 验证）。

## 20. 遗留问题

1. **真实"暂停等人工"未落地**：本阶段用同步 `review_handler` 演示最小闭环
   （handler 返回决策后引擎立即继续/停止）。生产环境的人工异步审核可把 handler
   换成"run 落盘 → 人工审核 → resume 续跑"，Engine 的 resume 已支持，后续接入。
2. **memory_writer Agent 未实现**：candidate 事实由 `add_candidate_fact` 接口提出；
   从剧本自动提取候选事实的 Agent 属业务层（后续阶段），且提取结果仍须人工确认。
3. **摘要层（summary.json）只预留概念**：LLM 生成摘要属业务 Agent，未实现。
4. **casting / Final Script Review 挂载点**：架构报告 §21 的两个默认审核节点，
   等真实 Agent（阶段 5+）接入时确定具体挂载。
5. **needs provider 可扩展**：目前 11 个 key；真实 Agent 可能需要更细粒度
   （如"特定角色记忆"、"上一集摘要"），在 assembler 注册表加 provider 即可。

## 21. 是否完全符合架构报告

| 项 | 架构报告 | 本阶段实际 | 说明 |
|---|---|---|---|
| Memory 分层 | §6.5 事实层+摘要层 | 事实层完整实现；摘要层预留 | 摘要生成属业务，后续阶段 |
| known_facts 来源 | §47 问题一：人工确认/规则兜底 | candidate→人工 approve/reject | 完全一致，已落地 |
| ContextAssembler | §七 资料管理员，纯代码 | 纯代码，不调 LLM | 一致 |
| needs 决策 | §47 问题五：声明式 needs | `needs` 清单 + provider 注册表 | 一致，无 if/else 硬编码 |
| 审核 | §二十一 + §47 问题四 | approved/approved_with_edit/rejected | 一致，approved_with_edit 已落地 |
| 状态机 | §10.2 needs_review/approved | 加入 APPROVED_WITH_EDIT/REJECTED | 一致 + 补充 rejected 分支 |
| 版本保留 | 第四十七章问题三方案 A | 审核复用 Artifact 版本机制 | 一致，不重造 |
| 项目隔离 | 指令 §五 | project 目录物理隔离 | 一致 |
| memory_writer | §三十二 列了 agents/memory_writer.py | 用 add_candidate_fact 数据层接口替代 | 按指令 §四"纯 Python 数据层接口，不需要真实 Agent"处理，真实 Agent 后续接 |

无实质性偏差；差异均为"预留但未启用"或"按指令改为数据层接口"的最小实现取舍。

## 22. 下一阶段建议

```text
阶段 5 —— 清理与文档（架构报告 §三十四）
    处理死代码 / 硬编码 / 旧路径 / README；
    移除 GPTTTS、GenerateAudio.py 危险清目录逻辑、生产流程中的论文消融脚本。
    注意：只在 v2 工作区处理，V0.1 永远不动。
```

或按业务优先级，先接入第一个真实 Agent（casting），验证 Memory/Context/审核的闭环。
**不自动进入，等待批准。**

---

# 阶段 4 验收结论

**PASS**

V0.2 已具备：Project 边界下的 Memory（事实层 + 人工确认）、ContextAssembler
（声明式 needs，可追踪、只装需要的内容）、人工审核（approved / approved_with_edit /
rejected，修改不覆盖原始产物）、与 Workflow 引擎的最小集成（needs_review 状态机）。
测试 25/25 通过；阶段 1/3 回归 19/19、38/38 通过；阶段 2 mock 回归 7/7；
**真实 LLM 调用 0 次、¥0 成本；V0.1 零改动（HEAD=v0.1=344c0c6）。**
