# FilmAgent V0.2 阶段 5 —— 验收报告

**任务**：清理、固化与文档（架构报告 §三十四）
**日期**：2026-09-01
**性质**：纯本地基础设施整理（真实 LLM 调用 **0 次**，token=0，cost=¥0）

---

## 1. 阶段 5 完成内容

本阶段**没有开发任何新业务能力**，做的是"把 V0.2 基础框架收拾干净、接口固化、文档补齐"：

```text
✅ 建立 V0.2 架构清单（模块 / 文件 / 职责 / 依赖 / 测试状态）
✅ 死代码检查（无证据不删，检查了 import / 动态引用 / 测试引用）
✅ 硬编码路径清理（1 处测试注释）
✅ 统一 import 与模块边界（修复 workflow → review 反向依赖）
✅ 错误体系检查（无吞错误；分层已明确）
✅ 日志规范检查（稳定模块字段已统一，不强行重写）
✅ 敏感信息检查（key 零泄漏；.gitignore 覆盖 v2 运行产物）
✅ 测试目录整理（区分验收证据 / 运行日志 / 临时产物）
✅ v2/README.md（16 项工程文档）
✅ v2/DEVELOPMENT.md（10 条开发规范 + 边界约定）
✅ v2/AGENTS.md（Agent 接入规范）
✅ 固化 Agent / Step / ContextAssembler / Memory / Review / Artifact / Workflow 接口
✅ 阶段 1~4 完整回归全 PASS + 静态检查 + V0.1 完整性验证
```

## 2. 清理前后的目录结构

**清理前（阶段 4 结束时）**：

```text
v2/
├── config.py
├── core/{io,models,validator,assets,token_log}.py   ← 无 __init__.py
├── llm/{client,router}.py                            ← 无 __init__.py
├── memory/  context/  review/  workflow/             ← 有 __init__.py
├── assets/locations/   prompts/v0.1/
├── projects/                                        （运行时目录，未被 gitignore）
└── logs/  ← llm_calls.jsonl + stage0~4（测试/报告/临时产物混放，临时产物未被 gitignore）
```

**清理后（本阶段结束）**：

```text
v2/
├── config.py
├── .gitignore                                        ← 新增：忽略 v2 运行产物
├── README.md                                         ← 新增：工程文档（16 项）
├── DEVELOPMENT.md                                    ← 新增：开发规范（10 条）
├── AGENTS.md                                         ← 新增：Agent 接入规范
├── core/__init__.py   llm/__init__.py                ← 新增：补齐包入口
├── core/{io,models,validator,assets,token_log}.py    （未动）
├── llm/{client,router}.py                            （未动）
├── memory/  context/  review/  workflow/             （workflow/models、engine、
│                                                       review/models、workflow/__init__ 有最小修改）
├── assets/locations/   prompts/v0.1/                 （只读，未动）
├── projects/                                        （已被 v2/.gitignore 忽略）
└── logs/  ← 验收证据（run_tests.py + stage*_report.md/json）保留；
             运行日志/临时产物（console.txt、tmp_projects、project_*/、runs/、
             test_output_*、llm_calls.jsonl）已被 v2/.gitignore 忽略
```

仓库根 `FilmAgent/`、`.gitignore`、`README.md` **零改动**。

## 3. 删除了哪些代码

**没有删除任何 Python 代码文件。**

唯一删除的"代码定义"：`review/models.py` 里的 `ReviewDecision` 类定义
（被改为从 `workflow.models` 导入同一常量，功能等价、对象同源，见 §7）。

## 4. 为什么删除

`ReviewDecision` 的**定义位置**从 review 层移到 workflow 层，是因为：

- 架构规定依赖方向为 `core → llm/memory/context → workflow → review`（review 在上层）。
- 阶段 4 把审核接进引擎时，`workflow/engine.py` 顶层 `from review.models import ReviewDecision`，
  形成 **workflow 依赖 review 的反向引用**，违反模块边界。
- 把常量定义上移到 workflow 层、让 review 反向引用它之后，方向恢复为
  `review → workflow`（允许），workflow 不再依赖 review。

这是"修边界"，不是删功能（指令 §三任务 4 + §九）。

## 5. 哪些代码没有删除以及原因

| 代码 | 为什么保留 |
|---|---|
| `core/models.py` 的 `Project / World / Episode / CharacterMemory` | 架构报告定义的核心数据模型，预留给阶段 6+ 业务层；当前无消费方但**不是死代码**（无证据不删） |
| `core/validator.py` 的 `ReferenceValidator` | 阶段 1 测试覆盖，属于统一校验体系 |
| `llm/router.py` 的 `Router` | 阶段 2 测试覆盖，未来任务路由接口 |
| `core/io.py` 的 V0.1 容错（errors='ignore' + �替换） | V0.1 迁移的既有行为，集中在一处（兼容层边界），改动会破坏 V0.1 一致性 |
| `core/models.py` 的 `from_v01_* / to_v01_*` 转换函数 | 兼容层（V0.1 → V0.2）的集中出口，未散落 |
| 所有测试的 `run_tests.py` | 验收证据，全部回归 PASS，无一删减 |

判定标准：扫描了全部模块的 import 引用、测试引用、`__init__.py` 导出、动态调用
（`importlib` / `getattr` / 字符串引用），**没有发现无任何引用且无文档说明的死代码**，
因此没有删除任何文件（指令 §六：无证据不删）。

## 6. 路径清理情况

- 全 v2/ 扫描 `D:\`、`C:\`、`FilmAgent-main`、`FilmAgent\` 硬编码路径：
  仅 1 处——`logs/stage3_workflow_test/run_tests.py` 第 40 行**注释**里的
  `# D:/AI/FilmAgent-main`。已改为不含机器路径的注释
  （代码本体用 `V2_ROOT.parent` 动态推导，本就无硬编码）。
- 其余路径全部走 `config.V2_ROOT`（`Path(__file__).resolve().parent` 动态推导）、
  `config.V01_KEY_FILE`（只读 V0.1 密钥文件）、`pathlib`、`project_id`。
- V0.1 的 `Prompt/`、`Locations/` 只读迁移至 `v2/prompts/v0.1/`、`v2/assets/locations/`，
  无任何 V0.2 代码写回它们。

## 7. import / 模块依赖情况

清理前存在 **1 处反向依赖**：`workflow/engine.py` → `review.models`。

修复后最终依赖图（全部只向上依赖，无循环）：

```text
config.py           （无内部依赖）
core/               （io / models / assets / validator / token_log 互相之间同级）
llm/       → core, config
memory/    → core, config
context/   → core, memory
workflow/  → core, config
review/    → workflow, core, config     ← review 依赖 workflow（允许），workflow 不依赖 review
（未来）agents → 所有上层
```

- `ReviewDecision` 现定义于 `workflow/models.py`，`review/models.py` 与 `workflow/__init__.py`
  引用同一对象（冒烟测试断言 `is` 同源通过）。
- `workflow/models.py` ↔ `workflow/artifact.py` 之间的延迟导入（from_dict 内
  `import Artifact`）是**受控的运行时延迟引用**，非循环 import，运行安全，未强行重构
  （指令 §九：最小必要修改）。
- 已用全模块 import 冒烟测试验证（`import core, llm, memory, context, workflow, review`
  及全部公开接口），无 ImportError。

## 8. 错误体系检查

| 层 | 错误类型 | 可重试标记 |
|---|---|---|
| LLM | `LLMError` 家族：Timeout / Connection / RateLimit / Internal / Request / Authentication | ✅ 前 4 类可重试 |
| Workflow | `WorkflowError` 家族：`StepError` / `ArtifactError` / `RetryError` | ✅ StepError 默认可重试 |
| Memory | 标准异常 `KeyError` / `ValueError` + 明确消息 | 业务判定 |
| Context | 标准异常 `ValueError`（未注册 needs 明确报错） | 业务判定 |
| Review | 标准异常 `KeyError` / `ValueError`（重复审核明确报错） | 业务判定 |

- 全 v2/ 扫描 `except Exception: pass` / `except: pass`：**0 处**，无吞错误。
- 未知异常统一包成 `StepError` 记录后失败（engine.py），不静默。
- Memory/Context/Review 未新建错误类：当前标准异常+消息已可区分来源；
  为避免改动已验收模块与测试断言，采用"错误分层文档化"（DEVELOPMENT.md 已写明），
  未来接入真实 Agent 若需专门错误类再补（最小必要修改原则）。

## 9. 日志体系检查

| 日志 | 字段 | 说明 |
|---|---|---|
| `core/token_log.py`（LLM 调用 JSONL） | timestamp / request_id / step / agent / model / tokens / cost / currency / duration_ms / success / retry_count / error / prompt_hash / prompt_length / message_count | 字段齐全；**不记 key、不记 prompt 正文** |
| `workflow`（run 记录 JSON） | run_id / project_id / workflow_id / status / 每 step 的 StepResult（attempts / status / artifact / error / review_id） | 阶段 3 定稿，稳定 |

指令要求"project_id/run_id/step_id/artifact_id/request_id/timestamp/status 字段一致"——
现有实现已满足（TokenUsage 有 request_id，WorkflowRun 有 run_id/project_id/status，
StepResult 有 step_id/artifact，Artifact 有 artifact_id/created_at）。这些是已验收的稳定模块，
**未强行重写**（指令 §三十四：不强行重写稳定模块）。

## 10. 敏感信息检查

- `config.py`：API key 只从环境变量 `DEEPSEEK_API_KEY` 或 `FilmAgent/api_key.txt`（只读）
  读取，**绝不硬编码**；`mask_api_key()` 只输出 `sk-****abcd` 脱敏形式。
- `core/token_log.py`：日志不记 key、不记 prompt 正文（只记 sha256 哈希 + 长度）。
- 全 v2/ 扫描 `sk-` 真值：仅测试文件里的假 key（`sk-test-not-real`）与注释说明文字，无真实 key。
- `.gitignore`：仓库根已覆盖 `FilmAgent/api_key.txt`；本阶段新增 `v2/.gitignore`
  覆盖 `v2/projects/`、`v2/logs/llm_calls.jsonl`、运行日志与临时产物，**防止 v2 运行数据入库**。
- 本报告、测试输出、日志中出现的 key 全部为脱敏或空。

## 11. 测试目录整理情况

按指令 §八 区分四类，并在 `v2/.gitignore` 落实：

| 类别 | 示例 | 处理 |
|---|---|---|
| 永久源码 | `run_tests.py`、`test_io.py` | 保留（验收证据） |
| 验收证据 | `stage*_report.md`、`stage*_report.json` | 保留（验收证据） |
| 运行日志 | `stage*_console.txt`、`logs/llm_calls.jsonl` | gitignore |
| 临时输出 | `tmp_projects/`、`project_*/`、`runs/`、`test_output_*` | gitignore |

测试结构维持**按阶段组织**（`logs/stageN_*/run_tests.py`），不强行迁到 `tests/`，
理由（指令 §十五："如果现有结构合理不强迁"）：

1. 每个阶段的验收报告与对应测试在同一目录，验收可追溯；
2. 阶段 4 套件通过 subprocess 调用阶段 1/3、通过 importlib 调用阶段 2 做回归，
   拆分会破坏这套既有回归机制；
3. 拆分重写是大改动，违背"最小必要修改"。

全部测试每次运行从干净临时目录开始（`rmtree(TMP_PROJECTS)`），不污染真实项目。

## 12. README 内容

`v2/README.md` 已建立，覆盖指令要求的 16 项：

1. V0.2 是什么；2. V0.1 与 V0.2 的关系；3. 目录结构；4. 如何安装依赖；
5. 如何配置 LLM；6. 如何运行测试（含"阶段 2 完整套件会真实计费，日常回归用 mock"的警告）；
7. 如何创建 Project；8. Memory 是什么；9. ContextAssembler 是什么；10. Workflow 是什么；
11. Review 是什么；12. Artifact 版本机制；13. 如何查看 Token/Cost；14. 当前已完成哪些阶段；
15. 当前尚未完成哪些能力；16. 下一阶段是什么。

内容为工程文档，无宣传性文字。

## 13. DEVELOPMENT.md 内容

`v2/DEVELOPMENT.md` 已建立，覆盖指令要求的 10 条 + 补充约定：

1. V0.1 不允许修改；2. V0.2 所有新功能进入 v2/；3. Agent 不直接读取底层 JSON；
4. Agent 必须通过 ContextAssembler 获取 Context；5. Agent 不直接管理 Artifact 文件名；
6. Agent 不直接管理 Workflow 状态；7. LLM 调用统一走 LLMClient；8. Token/Cost 统一记录；
9. Memory 必须区分 candidate / approved；10. 审核修改不能覆盖原始 Artifact。

另含：模块边界（依赖方向图 + `workflow` 不依赖 `review` 的说明）、错误体系分层、
日志规范字段、敏感信息规则、兼容层边界（V0.1 兼容逻辑只允许出现在明确转换函数）。

## 14. Agent 接口规范

`v2/AGENTS.md` 已建立，明确：

```text
Agent → 接收 Context（ContextAssembler 按 needs 组装）→ 执行任务 → 返回结构化结果
     → Workflow → Artifact → Review
```

Agent **绝不能**：自己找 API key / 自己调用 requests 或 openai / 自己生成文件名版本号 /
自己修改 Memory / 自己决定 Workflow 下一步。
Agent **职责**：完成一个明确的业务任务（casting / 场景规划 / 台词生成 / 镜头标注…）。
实现方式：`workflow.step.Step` 子类，声明 `needs`，实现 `run()/validate()`。

## 15. Step 接口最终状态

`v2/workflow/step.py` 已满足指令 §十九 的全部要求，**无需修改**：

| 指令要求 | 现状 |
|---|---|
| Step ID | `step_id`（引擎自动补默认名） |
| Step name | `name` |
| needs | `needs: List[str]`（声明式，交 ContextAssembler） |
| input | `WorkflowContext.inputs`（run 时传入） |
| context | `run(context)` 参数（WorkflowContext，阶段 6 起装配 AssembledContext） |
| execute() | `run()`（执行插槽） |
| output | `StepOutput(data, artifact_name, artifact_type, metadata)` |
| artifact | 由引擎写盘（`ArtifactStore` 版本化），Step 不碰磁盘 |
| review requirement | `needs_review: bool` |

未提前加入 Agent-specific 逻辑（指令要求）。

## 16. ContextAssembler 最终状态

`v2/context/assembler.py` 固化确认：

- **声明式 needs**：Step 声明 → provider 注册表照单取货，无 if/else 按 step 名硬编码。
- **11 个 provider**：project_info / approved_facts / candidate_facts / characters / world /
  history / previous_artifacts / workflow_state / locations / actions / shots。
- **可追踪**：每条 ContextItem 带 `ContextSource(source/source_type/source_id/version)`。
- **只装需要的**：未声明 locations 的 Step，Context 里绝不出现 locations（测试 T10 证明）。
- **明确报错**：声明未注册 needs → ValueError（不静默）。
- 未来 Agent 可完全依赖 ContextAssembler 获得 Context，无需自己读 Memory / Assets / Artifacts /
  Project。现有测试代码未发现违反该原则之处，无需修复。
- `AssembledContext` 与 `WorkflowContext` 的装配关系已在 AGENTS.md / README 写明
  （阶段 6+ 由调度层统一装配后传入 `run()`）。

## 17. Memory 最终状态

`v2/memory/` 固化确认：

- `MemoryStore`：项目隔离（`v2/projects/<project_id>/memory/`），facts / characters / world /
  history 四文件 JSON 持久化，revision 每次写 +1。
- 事实三态：`candidate → (人工 approve/reject) → approved/rejected`；
  `add_candidate_fact` 提出候选，**绝不自动升 approved**；`get_approved_facts()` 只返回 approved。
- 阶段 4 修复的"构造即建目录"行为保留（测试 T1 证明）。

## 18. Review 最终状态

`v2/review/` 固化确认：

- `ReviewService`：submit_for_review / approve / approve_with_edit / reject，记录持久化到
  `v2/projects/<project_id>/reviews/`。
- `approve_with_edit` 用 `ArtifactStore.save()` 写**编辑后的新版本**，原始文件绝不覆盖
  （测试 T15 证明）。
- `ReviewResult.edited_artifact` 让引擎直接采用审核层已写盘的产物，不重复写盘。
- `ReviewDecision` 常量现定义在 workflow 层（本阶段边界修复），review 引用同一对象。

## 19. Artifact 最终状态

`v2/workflow/artifact.py` 固化确认：

- 版本命名：v1 无后缀（`scenes_5.json`），v2+ 带 `_vN`；写盘前探测磁盘最大版本 +1，绝不覆盖。
- 单次 run 内重试、跨 run 重跑都递增版本，历史全保留（测试 T16/T17 证明）。
- `Artifact.to_dict()/from_dict()` 支持序列化恢复，审核编辑产物也走这套机制。

## 20. Workflow 最终状态

`v2/workflow/` 固化确认：

- `WorkflowEngine`：线性执行 / 重试 / 状态记录 / 轨迹保存 / resume（跳过已成功 Step）。
- 状态机：PENDING / RUNNING / SUCCESS / FAILED / RETRYING / SKIPPED +
  NEEDS_REVIEW / APPROVED / APPROVED_WITH_EDIT / REJECTED；
  `is_success` 覆盖 SUCCESS / APPROVED / APPROVED_WITH_EDIT。
- `review_handler` 注入式审核节点；`RetryError` 直接冒泡（不配 handler 时明确报错）。
- 本阶段：import 只依赖 workflow 内部 + core + config，不再依赖 review（边界修复）。

## 21. 阶段 1 回归测试

`logs/stage1_validation_test/run_tests.py` 独立运行 → **19/19 通过**（0 LLM）。
阶段 4 套件内 R1 再次确认 19/19。

## 22. 阶段 2 回归测试

按指令要求不做真实 API 调用，用阶段 4 套件内 R2 **mock 复验** 7 项断言全 PASS：
配置读取（base_url/model、key configured+脱敏、超时/重试可配置）+ 异常处理（无效参数→LLMError、
认证失败→LLMError）+ 重试机制 + 失败重试上限。**0 次真实调用。**
（真实调用能力已在阶段 2 验收时 4 次调用验证，不重复计费。）

## 23. 阶段 3 回归测试

`logs/stage3_workflow_test/run_tests.py` 独立运行 → **38/38 通过**（0 LLM）。
阶段 4 套件内 T19 再次确认。证明本阶段对 workflow 的 import 调整未破坏阶段 3 行为。

## 24. 阶段 4 回归测试

`logs/stage4_memory_context_review_test/run_tests.py` → **25/25 通过**（0 LLM）。
覆盖 Memory / Context / Review / Workflow 集成全部 20 项要求 + 回归。

## 25. 静态检查结果

- `python -m compileall v2` → **无语法错误**。
- 全模块 import 冒烟测试 → 无 ImportError、无循环依赖（含 `ReviewDecision is` 同源断言）。
- 硬编码路径 / 吞错误 / 敏感信息正则扫描 → 均通过。
- 环境无 ruff / mypy / pyright / pytest，按指令"不强装"，以 compileall + import 冒烟替代。

## 26. 真实 LLM 调用次数

**0 次**。本阶段全部使用本地 fixture / mock / 临时 JSON 目录，未做任何真实 API 调用。

## 27. token

**0**。

## 28. cost

**¥0**。

## 29. Git HEAD

`344c0c6b7c48497be934957c29fcae029cca6752`（= v0.1）

## 30. v0.1 commit

`344c0c6b7c48497be934957c29fcae029cca6752`（tag: v0.1）

## 31. V0.1 是否修改

**没有**。`git diff -- FilmAgent/` 为空；`git status --short` 仅 `?? v2/`。
本阶段也未修改仓库根 `.gitignore`（v2 产物忽略规则放在 `v2/.gitignore` 内）。

## 32. 是否创建 tag

**没有**。未创建 `v0.2.0` / `v0.2.0-alpha` 或任何 tag。

> 关于 commit：指令 §二十五 允许 commit，但为保持回归测试 T20 的
> `HEAD == v0.1` 断言在"验收状态"下有效，并延续既有"报告后由用户决策"的约定，
> 本阶段**未 commit**。v2/ 全部改动保留在工作区。如需留存，一条命令即可：
> `git add v2/ && git commit -m "chore: stabilize FilmAgent v0.2 foundation"`
> （**不创建 tag**）。是否执行由你决定。

## 33. 遇到的问题

1. **T20 首跑 FAIL（根 .gitignore 被改）**：初版方案把 v2 产物忽略规则写进仓库根
   `.gitignore`，导致 `git status` 出现 `M .gitignore`，触发 T20 "V0.1 无修改"的保守断言。
   修复：**恢复根 .gitignore 原样**，改为在 `v2/` 内建 `.gitignore`（V0.2 隔离原则，
   连仓库根文件也不动）。修复后 T20 PASS，v2 产物仍被正确忽略（`git check-ignore` 验证）。
2. **workflow/__init__.py 未导出 ReviewDecision**：把常量移入 workflow 层后，包入口漏加导出，
   `from workflow import ReviewDecision` 报 ImportError（import 冒烟测试抓到）。
   修复：在 `workflow/__init__.py` 的 models 导入与 `__all__` 补上 `ReviewDecision`。
3. **静态工具缺失**：环境无 ruff/mypy/pyright/pytest。按指令不强装，用
   `compileall` + import 冒烟 + 正则扫描替代。

没有隐藏任何 FAIL；上述修复均不改变模块行为（全回归再次验证）。

## 34. 遗留问题

1. **真实"暂停等人工"异步审核流程未落地**：同步 `review_handler` 演示最小闭环；
   生产环境的"run 落盘 → 人工审核 → resume 续跑"流程待阶段 6+ 接入
   （Engine 的 resume 已支持跳过已成功 Step）。
2. **memory_writer Agent 未实现**：candidate 事实由 `add_candidate_fact` 数据层接口提出；
   从剧本自动提取候选事实的 Agent 属业务层，且提取结果仍须人工确认。
3. **summary 摘要层只预留概念**：LLM 生成摘要属业务 Agent，未实现。
4. **casting / Final Script Review 挂载点**：等真实 Agent 接入时确定。
5. **needs provider 可扩展**：目前 11 个 key；真实 Agent 需要更细粒度时在注册表加 provider。
6. **Step 的 Context 装配方式**：当前 `run()` 收 `WorkflowContext`，`AssembledContext` 由
   ContextAssembler 单独产出；阶段 6+ 由调度层统一装配后传入，接口已在文档固化。
7. **Project / World / Episode 数据模型未消费**：预留给业务层，未删。

## 35. 是否符合架构报告

| 项 | 架构报告 | 本阶段实际 | 说明 |
|---|---|---|---|
| 清理范围 | §三十四：只在 V2 工作区，V0.1 永远不动 | 全部改动在 v2/；FilmAgent/ 与仓库根零改动 | 一致 |
| 模块边界 | core→llm→memory→context→workflow→review | 已修复 workflow→review 反向依赖 | 一致（本阶段重点） |
| 死代码 | 无证据不删 | 未删除任何文件 | 一致 |
| 硬编码 | 清理旧路径 | 1 处测试注释修复 | 一致 |
| 错误 | 能区分来源、禁止吞错 | 分层确认 + 0 处 `except: pass` | 一致 |
| 日志 | 字段一致、不重写稳定模块 | 已满足，未重写 | 一致 |
| 敏感信息 | key 零泄漏、.gitignore 覆盖 | 零泄漏；v2/.gitignore 补齐 | 一致 |
| 测试 | 区分四类、不强迁 | 分类落实 + 保留阶段结构 | 一致 |
| README/DEVELOPMENT/Agent 规范 | §十六/§十七/§十八 | 三个文档已建立 | 一致 |
| Step / Context / Memory / Review / Artifact / Workflow | 固化 | 均已确认，接口稳定 | 一致 |
| 回归 | 阶段 1~4 全 PASS | 19/19、mock 7 项、38/38、25/25 | 一致 |
| 静态检查 | 至少 compileall | compileall + import 冒烟 | 一致 |
| tag | 不创建 | 未创建任何 tag | 一致 |

无实质性偏差。

## 36. 下一阶段建议

```text
阶段 6+ —— 业务 Agent 接入（架构报告 §四十六 路线图）
    第一个真实 Agent（建议 casting，验证 Memory / Context / 审核 / Artifact 全闭环）
    → Project 生命周期 → 第一条完整业务链 → 第一集真实生成
    → 多集连续生成 → 全面测试 → V0.2.0-alpha（首次重要版本留存点）
```

**V0.2.0-alpha 应代表"基础设施完整 + 至少一条真实业务链跑通 + 测试通过 + V0.1 完全冻结"，
而不是仅仅"阶段 5 做完了"。**

不自动进入阶段 6，等待批准。

---

# 阶段 5 验收结论

**PASS**

V0.2 基础框架已清理干净并固化：

- 模块边界修复（workflow 不再依赖 review），无循环依赖；
- 无死代码删除、无硬编码路径、无吞错误、API key 零泄漏；
- v2/.gitignore 覆盖运行产物，仓库根与 V0.1 零改动；
- README / DEVELOPMENT / AGENTS 三个工程文档建立，Agent/Step/Context 等接口固化；
- **阶段 1~4 全回归 PASS（19/19、mock 7 项、38/38、25/25）**，静态检查通过；
- **真实 LLM 调用 0 次、token 0、cost ¥0；HEAD=v0.1=344c0c6；未创建任何 tag。**
