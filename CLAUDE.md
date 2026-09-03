# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目是什么

复现 **FilmAgent**（多智能体端到端电影生成）的工程。仓库分两半：

- **`FilmAgent/`（V0.1）**：冻结基准，是一套已跑通的串行流水线（casting → scenes_plan → lines_generate → … → clean_script），依赖 DeepSeek（`LLMCaller.py` + `Prompt/`）。**任何情况下不得修改。**
- **`v2/`（V0.2）**：全新重写的基础设施——可测试、可扩展、有人工审核的多智能体框架，按阶段推进，每阶段由独立的"开工指令"文件（位于 `D:\AI\FilmAgent\`）控制。

**当前进度：阶段 8 已完成并封存**——Canonical Schema（镜头级数据模型）落地：数据结构升级为
`Project → Scene → Beat → Shot → Asset`（`v2/core/shot.py`，含受控镜头词表），V0.1 通过 `v2/core/adapter.py`
双向无损转换，纯代码校验器新增 4 个（Asset/Shot/Beat/Storyboard），Agent 公共骨架抽到 `v2/agents/base.py`
（`LLMAgent`/`LLMStep`，Casting/SceneGeneration 已改继承、行为不变）。阶段 8 测试 27/27、历史回归
19/19+38/38+25/25+24/24+29/29 全绿、真实 LLM 调用 0 次。
当前处于 **Stage 8 与 Stage 9 之间**：Stage 9 尚未开始，等待下一份开工指令。

## 铁律（违反即失控）

1. **V0.1 冻结**：`FilmAgent/` 下任何文件（尤其 `LLMCaller.py`、`main.py`、`Prompt/`、`Locations/`、`Script/`、`api_key.txt`）不得修改。验收时 `git diff -- FilmAgent/` 必须为空。
2. **一切新工作只进 `v2/`**。
3. **API key 绝不硬编码**进 Python，绝不进入日志/异常/报告；只显示脱敏形式（`config.mask_api_key`，如 `sk-****6509`）。
4. **每阶段完成即停下**等人工验收，不自动 commit、不自动 tag、不自动进入下一阶段。

## 常用命令

环境：Python 3.9+（实测为 Anaconda 环境）。唯一非标准库依赖是 `openai`（仅真实 LLM 调用时用；V0.1 的 `LLMCaller.py` 也依赖它）：

```bash
pip install openai -i https://pypi.tuna.tsinghua.edu.cn/simple
```

API key 配置（优先环境变量，其次 V0.1 的 `FilmAgent/api_key.txt`，两者都不打印 key 内容）：

```bash
export DEEPSEEK_API_KEY="sk-xxxx"     # 方式一
# 方式二：把 key 写进 FilmAgent/api_key.txt（已被 .gitignore 忽略）
```

**跑测试**（每个阶段一个目录，脚本自算 `V2_ROOT`，从仓库根或 `cd v2` 都能跑；退出码 0=全过）：

```bash
python v2/logs/stage1_validation_test/run_tests.py              # 19/19，纯本地 mock
python v2/logs/stage3_workflow_test/run_tests.py                # 38/38，纯本地 mock
python v2/logs/stage4_memory_context_review_test/run_tests.py   # 25/25，内部复验阶段1/2(mock)/3
python v2/logs/stage6_casting/run_tests.py                      # 24/24，mock，0 次真实调用
python v2/logs/stage6_casting/run_real_casting.py               # T21~T25，最小真实 DeepSeek 调用（计费）
python v2/logs/stage7_scene_generation/run_tests.py             # 29/29（T01~T19+T26+回归），mock，0 次真实调用
python v2/logs/stage7_scene_generation/run_real_scene_generation.py  # T21~T25，最小真实 DeepSeek 调用（计费）
python v2/logs/stage8_canonical_schema/run_tests.py                 # 27/27（词表/模型/校验/适配器/骨架），mock，0 次真实调用
```

- **日常回归策略**：跑 stage4 套件（内含 stage1/2(mock)/3 复验）+ stage6 套件 + stage7 套件即可覆盖全部。
- **真实 LLM 调用只走独立脚本**（stage2 完整套件、stage6 的 `run_real_casting.py`），日常回归 0 计费。
- **静态检查**（阶段 5 方式，环境无 ruff/mypy）：`compileall` + 全模块 import 冒烟 + 用正则扫 v2/ 源码里的真实 key 与硬编码绝对路径。
- V0.1 冻结的自动化断言（stage3/4/6/7 各套件的 V0.1 完整性测试）：`v0.1^{commit}` 固定 `344c0c6…`、`v0.2-stage7` tag 存在、`git diff -- FilmAgent/` 为空、`FilmAgent/` 无 dirty 行。

## 架构（大图）

### 依赖方向（只允许向上，禁止反向/循环）

```text
core → (llm | memory | context) → workflow → review → agents
```

- `core` 不依赖任何上层；`workflow` 不得依赖 `review`（`ReviewDecision` 常量定义在 `workflow/models.py`，方向是 `review → workflow`）。新增模块先定所属层再写 import。
- 导入方式：脚本/测试开头 `sys.path.insert(0, str(V2_ROOT))` 后**绝对导入**（`from core.models import ...`）。

### 各层职责

| 层 | 位置 | 职责 |
|---|---|---|
| core | `v2/core/` | `models.py`（Scene/Character/Line/…/CastingResult）、`shot.py`（阶段8 Canonical：Shot/Beat/Storyboard/ShotAsset/受控词表）、`adapter.py`（阶段8：V0.1↔Canonical 无损转换）、`validator.py`、`io.py`（JSON 读写 + V0.1 字段容错）、`assets.py`、`token_log.py`（JSONL，不记 key 不记 prompt 正文） |
| llm | `v2/llm/client.py` | `LLMClient.chat(messages, step=, agent=)` → `LLMResponse`；统一错误家族（`LLMError` 子类带 `retryable`）、重试、自动写 token log；transport 可注入（测试用 mock） |
| memory | `v2/memory/store.py` | `MemoryStore(project_id)`；facts 三态 candidate/approved/rejected、characters、world、history |
| context | `v2/context/assembler.py` | `ContextAssembler` 按 Step 声明的 `needs` 组装 `AssembledContext`（每条带来源信息） |
| workflow | `v2/workflow/` | `Step`（run/validate）、`WorkflowEngine.run(project_id, workflow_id, inputs, max_attempts, resume_from)`、`ArtifactStore`（版本化落盘）、状态机 |
| review | `v2/review/service.py` | submit / approve / approve_with_edit / reject；记录持久化到项目 `reviews/` |
| agents | `v2/agents/` | 公共骨架 `base.py`（`LLMAgent`/`LLMStep`，阶段8抽取，Casting/SceneGeneration 已改继承）+ 业务 Agent 见下 |

### 数据流闭环（阶段 6/7 验证成立）

```text
Project → Memory → ContextAssembler(按 needs 组装) → Agent.run(context)
   → LLMClient(真实/mock) → 解析 → 确定性规范化 → Validator
   → Workflow 落盘 Artifact(版本化不覆盖) → Review(三态) → 继续/停止/重试
```

阶段 7 场景生成特有约定：`SceneGenerationAgent.needs = [project_info, approved_facts,
characters, locations, actions, shots]`（需注入 AssetRegistry）；场景规范由调度层放入
`WorkflowContext.inputs["scene_spec"]`（不新增 ContextAssembler provider）；多场景用
`scene_id` 区分（`scene_1.json` / `scene_2.json` 独立 step，互不覆盖）；`who`/`where`
缺失时 Agent 用 scene_spec 兜底（权威输入），内容合法性交给 Validator。

### Agent 边界（`v2/AGENTS.md` 铁律）

Agent 只做"一个明确的业务任务"，**绝不**：自己找 API key、自己调 requests/openai、自己读磁盘 JSON、自己生成文件名/版本号、自己写 approved Memory、自己决定 Workflow 下一步。输入只来自 `AssembledContext`，LLM 只走 `LLMClient`，产出只交 `StepOutput(artifact_name=, data=)`。

### 关键跨文件约定

- **Memory 三态**：事实先 `add_candidate_fact`，必须人工 `approve_fact/reject_fact` 才能进 approved；`get_approved_facts()` 是 Context 唯一事实来源。candidate **绝不自动**升 approved。
- **Artifact 版本化**：第 1 次 `casting.json`，之后自动探测磁盘最大版本写 `casting_v2.json`、`casting_v3.json`…，**绝不覆盖**。跨 run 重试/审核编辑同样递增。
- **Review 三态**：`APPROVED` 继续 / `APPROVED_WITH_EDIT` 写编辑后新版本（原文件不动）/ `REJECTED` 停止。
- **Casting 跨集决策**（确定性规则，不是 LLM 说了算）：名字命中已有角色 → `existing`（复用，忽略 LLM 自创档案防改档）；否则 → `new`（必须带完整档案）。`CastingValidator` 兜底（INVALID_STATUS / UNKNOWN_CHARACTER / DUPLICATE_ROLE / MISSING_FIELD / WRONG_TYPE）。
- **Character.age 默认 `None`**（落盘为 null，不是空串）；`Character` 序列化方法是 `to_v01()` 而非 `to_dict()`。
- **V0.1 兼容逻辑只允许在边界层**（`core/models.py` 的 `from_v01_*`/`to_v01_*`、`core/io.py` 的容错、`core/adapter.py` 的 Canonical↔V0.1 转换），禁止散落到业务代码。

## 测试约定

- 每个阶段测试在 `v2/logs/stageN_*/run_tests.py`，内部 `check(name, ok, detail)` 收集 `_results`，`sys.exit(1 if FAIL else 0)`。
- 测试产物分类：`stage*_report.md/json`（验收证据）与 `run_tests.py` 保留；`console.txt`、`tmp_projects/`、`tmp_logs/`、`llm_calls.jsonl` 被 `.gitignore` 忽略。
- **禁止**删测试、降低断言、注释掉 FAIL；禁止为"漂亮"做大重构（最小必要修改）；无证据不删死代码；禁止 `except: pass`。
- 错误必须"能区分来源"：LLM 层 `LLMError` 家族 / Workflow 层 `WorkflowError` 家族，未知异常包成 `StepError` 后再失败，绝不静默。

## 当前状态与下一步

- git：HEAD = `abf8e88a983f13c437abe2b719c920d3c5c3c144`（= `v0.2-stage8` tag）；`v0.1` = `344c0c6…`（未动）、`v0.2-stage7` = `300aae4…`（未动）、`v0.2-stage8` = `abf8e88…`（阶段 8 封存）。
- 已交付：阶段 0~8 全部完成并封存。阶段 8 = Canonical Schema（`core/shot.py` + `core/adapter.py` + `agents/base.py` + Asset/Shot/Beat/Storyboard 4 个新校验器），测试 27/27，真实 LLM 0 次。
- 已知遗留：openai 库需按上文安装；人工审核目前是程序化 decision 模拟，交互式审核界面未做；`v2/README.md` 过时（仍写"尚未实现任何业务 Agent"）；Storyboard/Image/Video/VLM Agent 未实现；image/video/audio 资产仅 Schema 可表达。
- 下一步：Stage 9 尚未开始，等下一份开工指令。路线图方向：StoryboardAgent / 接入第三个业务 Agent（Dialogue / Shot / Script）→ Project 生命周期 → 第一条完整业务链 → 第一集真实生成 → V0.2.0-alpha。
