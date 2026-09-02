# FilmAgent V0.2 阶段 3 —— 验收报告

**任务**：Workflow 流水线执行引擎 + 重试不覆盖轨迹落地
**日期**：2026-09-01
**性质**：纯本地基础设施（真实 LLM 调用 **0 次**）

---

## 1. Git 基准

| 项目 | 值 |
|---|---|
| HEAD | `344c0c6b7c48497be934957c29fcae029cca6752` |
| v0.1^{commit} | `344c0c6b7c48497be934957c29fcae029cca6752` |
| git status --short | 仅 `?? v2/` |
| git diff -- FilmAgent/ | 空 |
| git diff --stat | 空 |

开工前、收工后均核验。**V0.1 未发生任何修改。**

## 2. 新增文件及职责

```text
v2/workflow/__init__.py     包入口（公开 API 汇总）
v2/workflow/errors.py       WorkflowError / StepError / ArtifactError / RetryError
v2/workflow/models.py       运行时模型：StepStatus / StepOutput / AttemptResult /
                            StepResult / WorkflowContext / WorkflowRun + 桥接
v2/workflow/step.py         Step 抽象基类（run / validate 插槽）
v2/workflow/artifact.py     Artifact / ArtifactStore（版本化命名 + 轨迹保留）
v2/workflow/engine.py       WorkflowEngine（线性执行器：执行/记录/重试/轨迹/恢复）
v2/logs/stage3_workflow_test/run_tests.py     测试套件（38 项断言）
v2/logs/stage3_workflow_test/stage3_report.json 测试报告
v2/logs/stage3_workflow_test/stage3_console.txt 控制台输出
v2/logs/stage3_workflow_test/stage3_report.md   本报告
```

没有修改阶段 0~2 的任何产物（core/、llm/、config.py 均未动）。

## 3. Workflow 架构说明

遵循架构报告 §10.1「线性执行器」：第一版不实现 DAG 调度，把引擎做成
**支持状态管理、断点恢复、重试的线性执行器**。职责完全分离：

```text
WorkflowEngine（骨架：只管执行/记录/重试/轨迹）
    └── Step（业务插槽：run 产出 + validate 校验）
        └── LLMClient（未来 Agent 内部调用；本阶段不接入，0 次调用）
```

## 4. Step 生命周期说明

状态常量（`workflow/models.py StepStatus`，对应架构报告 §10.2）：

```text
PENDING → RUNNING → SUCCESS
              ↓
          FAILED → RETRYING →（下一次）RUNNING → SUCCESS
              ↓
          FAILED（不可重试 / 重试耗尽）
SKIPPED（resume 模式下已成功的 Step 不重跑）
NEEDS_REVIEW / APPROVED（架构报告定义，本阶段仅声明常量，审核节点留阶段 4）
```

每个 Step 明确表达：名称（step_id/name）、是否可重试（retryable）、
执行（run → StepOutput）、质量校验（validate → 错误列表）。

## 5. WorkflowContext 说明

```text
WorkflowContext
├── project_id / workflow_id / run_id     谁在跑
├── inputs                                输入数据
├── step_outputs / artifacts              已成功 Step 的产出与 artifact 引用
└── metadata / 时间戳                     运行元数据
```

Step 之间通过 `context.get_output("上游step")` 传递数据。
**不硬编码整个项目的数据**；ContextAssembler 等复杂业务规则明确留到后续阶段，
本阶段只建最小上下文。

## 6. Artifact 机制说明

命名规则（对应开工指令 §十/§十一，第四十七章问题三方案 A）：

```text
同一个 Step（基础名 scenes_5）：
    第 1 次尝试 → scenes_5.json
    第 2 次尝试 → scenes_5_v2.json
    第 3 次尝试 → scenes_5_v3.json
```

- version 由 ArtifactStore 写盘前**探测磁盘已存在的版本数 +1** 决定，
  因此单次 run 内重试 与 整个 workflow 重新运行 都**绝不覆盖**历史文件。
- Artifact 记录：`artifact_id / step_id / attempt / name / version / filename /
  path / type / created_at / approved / metadata`，回答
  **"这个结果来自哪个 Step、哪个 run、哪个 attempt"**（开工指令 §九）。

## 7. Retry / Attempt 机制说明

```text
Workflow Run
    └── Step
        └── Attempt 1 → 写 scenes_5.json → FAILED（文件保留）
            └── Attempt 2 → 写 scenes_5_v2.json → FAILED（文件保留）
                └── Attempt 3 → 写 scenes_5_v3.json → SUCCESS（approved）
```

每次尝试记录 `AttemptResult`（attempt / status / artifact / errors / error /
duration_ms），Step 全过程记录在 `StepResult`。**绝不是** `while retry < 3` 的
简单循环——重试与 Artifact / Run / Step 状态强关联（开工指令 §十三）。

可重试判定：
- 错误类型驱动：`StepError`（默认可重试）、`ArtifactError`（不可重试）、
  `RetryError`（不可重试）；Step 自身也可声明 `retryable=False`。
- 失败后：先落盘保留轨迹 → 再决定是否重试 → 重试耗尽或不可重试则 FAILED。

## 8. "重试不覆盖"验证结果（核心验收目标 ✅）

磁盘实况（测试 T16 产物，`stage3_workflow_test/project_001/`）：

```json
scenes_5.json    = {"attempt": "attempt-1"}    ← 第一次失败，保留
scenes_5_v2.json = {"attempt": "attempt-2"}    ← 第二次失败，保留
scenes_5_v3.json = {"attempt": "attempt-3"}    ← 第三次成功，保留
```

三份内容互不相同、全部保留，**最终 approved 指向 scenes_5_v3.json**。
不存在任何覆盖旧版的行为。

跨 run 场景（测试 T17）：StepA 成功产物在 StepB 重跑后**内容不变、不被删除**；
StepB 重跑生成 `stepB_v2.json`，旧版 `stepB.json` 保留 —— 版本号递增而非覆盖。

## 9. 测试结果

| 编号 | 覆盖点（开工指令 §十七/§十八/§十九） | 结果 |
|---|---|---|
| T1  | 单 Step 成功，产出落盘可读回 | PASS |
| T2  | 多 Step 顺序执行 | PASS |
| T3  | Step 状态正确 | PASS |
| T4  | Step 输出正确传递（下游读到上游产出） | PASS |
| T5  | Step 失败能够被捕获（不崩、失败即停） | PASS |
| T6  | 不可重试错误不会无限重试（max=3 只试 1 次） | PASS |
| T7  | 可重试错误能够重试并成功 | PASS |
| T8  | retry 次数正确（attempts=[FAILED,SUCCESS]，approved=第2版） | PASS |
| T9  | 第一次失败 artifact 保留 | PASS |
| T10 | 第二次失败 artifact 保留 | PASS |
| T11 | 第三次成功 artifact 保留 | PASS |
| T12 | 最终 artifact 指向正确（v3） | PASS |
| T13 | Workflow Run 可以恢复/读取历史 | PASS |
| T14 | 错误信息完整 | PASS |
| T15 | V0.1 未发生任何修改 | PASS |
| T16 | **专门重试轨迹测试**：attempt1 FAIL→attempt2 FAIL→attempt3 SUCCESS，磁盘三份文件内容分别=attempt-1/2/3 | PASS |
| T17 | 异常恢复：StepA artifact 不被删除；StepB 重跑不覆盖 | PASS |
| T18 | WorkflowRun→core.WorkflowState 桥接（attempts/approved_output） | PASS |
| T19 | resume 模式已成功 Step 标记 SKIPPED 且不重跑 | PASS |

## 10. 测试数量

38 项断言，19 个测试组。

## 11. PASS / FAIL

**38 / 38 全部通过**。开发过程中一度出现 1 项 FAIL，为**测试断言笔误**
（T17 期望 v2 内容为 attempt-1，实际因复用同一 Step 实例 calls 累加应为
attempt-2 —— 这恰好证明版本递增机制工作正常），已修正断言并重跑全绿。
**没有隐藏任何 FAIL。**

## 12. 是否进行了真实 LLM 调用

**否**。本阶段严格遵循"尽可能 0 LLM"原则（开工指令 §十六）：
全部用 FakeStep / MockStep 纯本地 Step 验证 Workflow 行为。

## 13. LLM 调用次数

**0 次**

## 14. token

**0**

## 15. cost

**¥0**（LLMClient 能力已在阶段 2 验收；本阶段不涉及 LLM 调用）

## 16. V0.1 Git 完整性检查

```text
git rev-parse v0.1^{commit} = 344c0c6b7c48497be934957c29fcae029cca6752  （不变）
git rev-parse HEAD         = 344c0c6b7c48497be934957c29fcae029cca6752  （= v0.1）
git diff -- FilmAgent/     = 空
```

## 17. 是否修改 FilmAgent/

**没有。** 未触碰 `FilmAgent/` 任何文件（LLMCaller / main.py / Prompt / Locations / Script）。
git 仅新增 `?? v2/`。

## 18. 阶段 3 遗留问题

1. **NEEDS_REVIEW / APPROVED 状态已声明但引擎未自动进入**：架构报告 §10.2 定义
   "多次失败 → needs_review"，本阶段无人工审核设施，因此重试耗尽后置 `FAILED`
   （错误信息完整可查）。人工审核节点（`approved_with_edit`）留到阶段 4 接入。
2. **跨 run 的"重做已成功 Step"命令未实现**：本阶段支持 resume（跳过已成功 Step）
   与失败 Step 重跑；`retry --step xxx` 显式重做命令属 CLI/编排层，后续阶段实现。
3. **Artifact 目录暂由调用方指定**：`projects/<project_id>/` 目录结构待阶段 4
   Project 层统一，届时 engine 挂到 Project 生命周期下。

## 19. 与架构报告是否存在偏差

| 项 | 架构报告 | 本阶段实际 | 说明 |
|---|---|---|---|
| 执行模型 | 线性执行器（§10.1） | 线性执行器，失败即停 | 一致 |
| Step 状态机 | pending/running/success/failed/retrying/needs_review/approved（§10.2） | 一致 + SKIPPED | 多实现 SKIPPED（指令 §七要求）；needs_review/approved 预留未启用 |
| 重试轨迹 | 第四十七章问题三 → 方案 A 版本号文件名 | `scenes_5.json` / `_v2` / `_v3` 全保留 | 一致，已落地 |
| WorkflowState | §10.3 JSON 结构（steps{status,attempts,output}） | 桥接到 core.WorkflowState（attempts 文件名列表 + approved_output） | 一致，由 `to_core_workflow_state()` 提供 |
| Resume | §10.4 跳过已成功 Step | resume_from 跳过已成功 Step（SKIPPED），失败 Step 重跑 | 一致 |
| DAG | 数据结构按 DAG 设计但 MVP 线性 | 线性 | 一致，未提前实现 DAG |
| ContextAssembler | §八 不提前实现复杂上下文组装 | 最小 WorkflowContext | 一致 |

无实质性偏差；差异均为"预留但未启用"的最小实现取舍。

## 20. 下一阶段建议

```text
阶段 4 —— Memory + ContextAssembler（+ 人工审核节点接入）
```

按架构报告第四十六章路线图，V0.2-Alpha 里程碑（阶段 3）已达成；
下一步进入 V0.2-Beta 前的 Memory / Context 阶段。**不自动进入，等待批准。**

---

**总评**：阶段 3 的唯一目标——"失败重试绝不覆盖历史轨迹"——已通过
专门的磁盘级测试证明（T16 三份版本文件内容分别为 attempt-1/2/3、全保留）。
Workflow 骨架（Step/Context/Artifact/Engine/错误体系）已可为后续 Agent 提供
**可执行、可记录、可恢复、可追踪**的接入基础。
