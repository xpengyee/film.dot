# CLAUDE.md

This file provides guidance to WorkBuddy AI when working with code in this repository.

## 项目是什么

复现 **FilmAgent**（多智能体端到端电影生成）的工程。仓库分两半：

- **`FilmAgent/`（V0.1）**：冻结基准，是一套已跑通的串行流水线（casting → scenes_plan → lines_generate → … → clean_script），依赖 DeepSeek（`LLMCaller.py` + `Prompt/`）。**任何情况下不得修改。**
- **`v2/`（V0.2）**：全新重写的基础设施——可测试、可扩展、有人工审核的多智能体框架，按阶段推进，每阶段由独立的"开工指令"文件（位于 `D:\AI\FilmAgent\`）控制。

**当前进度：阶段 8（Canonical Schema）、阶段 9（Provider Architecture）、阶段 10（Visual Asset Layer）、阶段 11（StoryboardAgent）均已完成并封存。**
阶段 8：Canonical Schema 落地——`Project → Scene → Beat → Shot → Asset`（`v2/core/shot.py`），
V0.1 经 `v2/core/adapter.py` 双向无损转换，新增 4 个校验器，Agent 公共骨架抽到 `v2/agents/base.py`。
阶段 9：Multi-Modal **Provider Architecture** —— 把"用哪个模型、怎么调用"从业务代码剥离进
`v2/llm/providers/`：Text/Vision/Multimodal/Embedding 接口族 + Mock 四件套 + Registry/Factory +
Router 真入口 + Config Provider 注册表（deepseek/bailian/local…）+ Bailian 文本实现位。
职责划分：Agent 说"要什么"、Router 说"用哪家哪个模型"、Provider 执行调用并做瞬时错误重试与
usage 上报、Model 只是 Config 里的数据。阶段 9 测试 26/26 + 历史回归全绿 + 真实 API 0 次 + ¥0.00。
阶段 10：**Visual Asset Layer** —— 语义与视觉分离：`core/visual.py`（五类资产/七态/参考图/来源词表）+
`core/visual_registry.py`（`(kind,name)` 永久绑定、版本单调递增绝不覆盖、参考图 sha256 checksum、
生命周期=Review 三态的确定性派生）+ `core/visual_compose.py`（VisualComposer 在调用模型前一次性展开
profile）+ `agents/visual_asset.py`（plan=TextProvider / extract=VisionProvider）。
**Token 硬证据**：注册视觉资产前后 `Storyboard.to_dict()` 逐字节不变；Shot 只存名字引用 + 本镜头增量。
阶段 10 测试 76/76 + 历史回归全绿；真实 Vision 冒烟 3 次（`qwen3.7-plus`，14,433 tokens）。
阶段 11：**StoryboardAgent** —— `agents/storyboard.py`（`StoryboardAgent`/`StoryboardStep` + §7.2 八条确定性
规范化）+ `prompts/v0.2/storyboard_generation.txt`。Scene（`inputs["scene"]`）→ Storyboard → Beat → Shot，
Shot 落 Stage 8 受控词表内、只存名字级引用 + 本镜头视觉增量，可被 `VisualComposer` 直接消费。
受控词表从 `core.shot` 常量渲染进 user payload + prompt 词表一致性运行时自检。
阶段 11 测试 65/65（含 T19 历史回归 8 套）+ 0 次真实 API；真实冒烟脚本 `run_real_storyboard.py` 已就位（未执行）。
协作规范与会话管理体系已落地（COMPLETE）：A/B/C/D = 不同 **WorkBuddy 会话**
（见下文「协作与会话管理（长期规则）」），项目状态与交接沉淀到 `v2/docs/PROJECT_STATUS.md`
与 `v2/docs/HANDOFF.md`；总控工作手册见 `v2/docs/ORCHESTRATION.md`，路线图见 `v2/docs/ROADMAP.md`。

## 铁律（违反即失控）

1. **V0.1 冻结**：`FilmAgent/` 下任何文件（尤其 `LLMCaller.py`、`main.py`、`Prompt/`、`Locations/`、`Script/`、`api_key.txt`）不得修改。验收时 `git diff -- FilmAgent/` 必须为空。
2. **一切新工作只进 `v2/`**。
3. **API key 绝不硬编码**进 Python，绝不进入日志/异常/报告；只显示脱敏形式（`config.mask_api_key`，如 `sk-****6509`）。
4. **每阶段完成即停下**等人工验收，不自动 commit、不自动 tag、不自动进入下一阶段。

## 协作与会话管理（长期规则）

1. **阶段是第一层边界**：默认 `Stage N → 完成 → Git commit → 必要时 tag → 阶段结束
   → 新 WorkBuddy 会话 → Stage N+1`。不要在一个长期会话里连续完成多个 Stage。
2. **A/B/C/D 默认 = 不同的 WorkBuddy 会话**，不是四个必须同时存在的子 Agent：
   - **A 分析 / 架构**：读项目状态、理解任务、分析代码、研究依赖、定架构方案，
     输出 PLAN / ARCHITECTURE / 风险 / 测试方案。
   - **B 代码实现**：按 A 留下的方案实际编码、修改必要文件、跑基础测试；
     不重复重新分析整个项目。
   - **C 测试 / Debug**：测试、边界检查、回归、问题定位、修复、重新验证；
     不做无意义的全项目重构。
   - **D 文档 / 交付**：整理成果、更新项目状态（PROJECT_STATUS / HANDOFF / 必要时
     CLAUDE.md）、生成阶段报告、确认 Git、commit / tag，形成最终交付。
3. **按复杂度选会话数**：小任务一个会话（A+B+C+D）；中型任务 A→B→C→D；
   大型任务可加 A2/A3 专项分析。不要因为形式而机械开大量会话。
4. **子 Agent 只是辅助工具**：专项代码侦察、独立研究、局部测试、并行检查。
   A/B/C/D 本身不是四个 subagent；阶段较大时优先用不同会话 / 终端，
   真正切断长期上下文、重建干净上下文、降低 Token 累积。
5. **跨会话记忆靠文档**：重要信息写入 `CLAUDE.md`、`v2/docs/PROJECT_STATUS.md`、
   `v2/docs/HANDOFF.md`、Stage Report、Git。聊天记录不作为项目长期记忆。

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
python v2/logs/stage9_provider/run_tests.py                         # 26/26（Provider 接口族/Mock/工厂/Router/Config/E2E + 内嵌回归），mock，0 次真实调用
python v2/logs/stage10_visual_asset/run_tests.py                    # 76/76（Visual Asset 五类七态/Registry/参考图 sha256/Composer/计数器/E2E + 内嵌回归），mock，0 次真实调用
python v2/logs/stage11_storyboard/run_tests.py                     # 65/65（Storyboard 契约/词表兜底/名字回锚/id 重排/降级/Step 壳/版本化/Review/Retry/Composer 衔接/边界 + 内嵌历史回归 8 套），mock，0 次真实调用
python v2/logs/stage10_visual_asset/run_real_visual_extraction.py   # 真实 Vision 冒烟（qwen3.7-plus，计费，人工批准）
python v2/logs/stage11_storyboard/run_real_storyboard.py            # 真实文本冒烟 T31~T35（≤2 次调用，计费，人工批准；已就位未执行）
```

> **跑测试务必加 `env -u PYTHONPATH`**（宿主经 `PYTHONPATH` 注入 `sitecustomize` shim 劫持
> `shutil.rmtree`，会导致历史套件假失败；判据=独立复跑 rc=0。详见 `v2/docs/ORCHESTRATION.md` §5.1）。

- **日常回归策略**：跑 stage11 套件即可（其内嵌子进程已覆盖 stage1/3/4/6/7/8/9/10）；也可单独逐套跑 stage4 + stage6 + stage7 + stage8 + stage9 + stage10 + stage11 核对。
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
| core | `v2/core/` | `models.py`（Scene/Character/Line/…/CastingResult）、`shot.py`（阶段8 Canonical：Shot/Beat/Storyboard/ShotAsset/受控词表）、`adapter.py`（阶段8：V0.1↔Canonical 无损转换）、`validator.py`、`io.py`（JSON 读写 + V0.1 字段容错）、`assets.py`、`token_log.py`（JSONL，不记 key 不记 prompt 正文）、`visual.py` / `visual_registry.py` / `visual_compose.py`（阶段10：视觉身份+版本+参考图 / Registry / Composer） |
| llm | `v2/llm/` | `client.py`（`LLMClient.chat` → `LLMResponse`；`LLMError` 错误家族带 `retryable`、重试、token log；transport 可注入）+ `providers/`（阶段 9：`Provider` 接口族 Text/Vision/Multimodal/Embedding + Mock 四件套 + `OpenAICompatibleTextProvider` + `BailianTextProvider` + `OpenAICompatibleVisionProvider`（阶段10）+ `registry.create_provider` 工厂）+ `router.py`（`get_route` → `ProviderRoute`、`get_provider` 产出实例）+ `request_counter.py`（阶段10：真实请求计数 + 预算硬上限） |
| memory | `v2/memory/store.py` | `MemoryStore(project_id)`；facts 三态 candidate/approved/rejected、characters、world、history |
| context | `v2/context/assembler.py` | `ContextAssembler` 按 Step 声明的 `needs` 组装 `AssembledContext`（每条带来源信息） |
| workflow | `v2/workflow/` | `Step`（run/validate）、`WorkflowEngine.run(project_id, workflow_id, inputs, max_attempts, resume_from)`、`ArtifactStore`（版本化落盘）、状态机 |
| review | `v2/review/service.py` | submit / approve / approve_with_edit / reject；记录持久化到项目 `reviews/` |
| agents | `v2/agents/` | 公共骨架 `base.py`（`LLMAgent`/`LLMStep`，阶段8抽取）+ `casting.py` / `scene_generation.py` / `visual_asset.py`（阶段10）/ `storyboard.py`（阶段11：`StoryboardAgent`/`StoryboardStep` + `normalize_storyboard`） |

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

阶段 11 分镜特有约定：`StoryboardAgent.needs` 与 `SceneGenerationAgent` **同集合**；
Scene 由调度层放入 `WorkflowContext.inputs["scene"]`（`Scene` 对象或 `to_v01()` dict），
**缺则抛 `StepError`**（绝不静默产出空 Storyboard），Agent 绝不自己读磁盘；
`artifact_name = storyboard_{scene_id}`、`step_id = storyboard_generation_{scene_id}`；
受控词表**从 `core.shot` 常量渲染进 user payload**（不手抄进 prompt 文件）+ prompt 词表
一致性运行时自检；确定性规范化 §7.2 全部 **8 条**（含 `shot_type` 安全化、`dialogue/move` 降级）；
warnings 统一放 `Storyboard.metadata["normalize_warnings"]`。

### Agent 边界（`v2/AGENTS.md` 铁律）

Agent 只做"一个明确的业务任务"，**绝不**：自己找 API key、自己调 requests/openai、自己读磁盘 JSON、自己生成文件名/版本号、自己写 approved Memory、自己决定 Workflow 下一步。输入只来自 `AssembledContext`，LLM 只走 `LLMClient`，产出只交 `StepOutput(artifact_name=, data=)`。

### 关键跨文件约定

- **Memory 三态**：事实先 `add_candidate_fact`，必须人工 `approve_fact/reject_fact` 才能进 approved；`get_approved_facts()` 是 Context 唯一事实来源。candidate **绝不自动**升 approved。
- **Artifact 版本化**：第 1 次 `casting.json`，之后自动探测磁盘最大版本写 `casting_v2.json`、`casting_v3.json`…，**绝不覆盖**。跨 run 重试/审核编辑同样递增。
- **Review 三态**：`APPROVED` 继续 / `APPROVED_WITH_EDIT` 写编辑后新版本（原文件不动）/ `REJECTED` 停止。
- **Casting 跨集决策**（确定性规则，不是 LLM 说了算）：名字命中已有角色 → `existing`（复用，忽略 LLM 自创档案防改档）；否则 → `new`（必须带完整档案）。`CastingValidator` 兜底（INVALID_STATUS / UNKNOWN_CHARACTER / DUPLICATE_ROLE / MISSING_FIELD / WRONG_TYPE）。
- **Character.age 默认 `None`**（落盘为 null，不是空串）；`Character` 序列化方法是 `to_v01()` 而非 `to_dict()`。
- **V0.1 兼容逻辑只允许在边界层**（`core/models.py` 的 `from_v01_*`/`to_v01_*`、`core/io.py` 的容错、`core/adapter.py` 的 Canonical↔V0.1 转换），禁止散落到业务代码。
- **语义与视觉分离（阶段 10，长期遵守）**：`Character`/`Location` 语义模型 0 改、不加视觉字段；视觉身份独立在 `VisualAsset`（`(kind,name)` 永久绑定、`asset_id` 永不变、版本单调递增绝不覆盖、参考图挂**版本**并记 sha256）。
- **Token 纪律（阶段 10，Stage 11+ 必须延续）**：Shot/Artifact **只存名字级引用 + 本镜头视觉增量**，绝不复制角色外貌/地点环境；调用模型前由 `VisualComposer.compose()` 一次性展开。硬证据：注册视觉资产前后 `Storyboard.to_dict()` **逐字节不变**。
- **缺口宁停不错**：`VisualComposer` 缺 approved 档案时默认 `allow_partial=False` → 明确报错列缺口，绝不静默残缺生成。
- **真实 API 计数**：`llm/request_counter.py` 程序化计数 + 预算硬上限（不靠人工数数）；真实调用只走独立 `run_real_*.py`（默认 `FILMAGENT_PROVIDER=mock` → 0 API）。

## 测试约定

- 每个阶段测试在 `v2/logs/stageN_*/run_tests.py`，内部 `check(name, ok, detail)` 收集 `_results`，`sys.exit(1 if FAIL else 0)`。
- 测试产物分类：`stage*_report.md/json`（验收证据）与 `run_tests.py` 保留；`console.txt`、`tmp_projects/`、`tmp_logs/`、`llm_calls.jsonl` 被 `.gitignore` 忽略。
- **禁止**删测试、降低断言、注释掉 FAIL；禁止为"漂亮"做大重构（最小必要修改）；无证据不删死代码；禁止 `except: pass`。
- 错误必须"能区分来源"：LLM 层 `LLMError` 家族 / Workflow 层 `WorkflowError` 家族，未知异常包成 `StepError` 后再失败，绝不静默。

## 当前状态与下一步

- 当前阶段：V0.2 Stage 8 / 9 / 10 / 11 均 **PASS + 已封存**；
  V0.2 Stage 12 **NOT STARTED**（尚未设计开工指令）。当前没有任何进行中的业务代码任务。
- 权威状态文档：`v2/docs/PROJECT_STATUS.md`；给新会话的交接文档：`v2/docs/HANDOFF.md`；
  总控工作手册：`v2/docs/ORCHESTRATION.md`；路线图：`v2/docs/ROADMAP.md`。
- Git：版本锚点 `v0.1` = `344c0c6…`、`v0.2-stage7` = `300aae4…`、`v0.2-stage8` = `abf8e88…`、
  `v0.2-stage9` = `82a89b5…`、`v0.2-stage10` = `c8a3ed8…`、`v0.2-stage11` = 本次 Stage 11 commit；
  六个 tag 均未移动；`FilmAgent/` 0 diff；仓库根 `.gitignore` 0 diff。最近 commit 见 `git log`（不在此写死 HEAD）。
- 已交付：阶段 0~11 全部完成并封存。阶段 11 = StoryboardAgent（`agents/storyboard.py` +
  `prompts/v0.2/storyboard_generation.txt`），测试 65/65 + 历史回归 8 套全绿 + 真实 API 0 次。
- 已知遗留：见 `v2/docs/PROJECT_STATUS.md` §6（openai 库安装；人工审核是程序化模拟，
  交互式 UI 未做；Image/Video/VLM Agent 未实现；image/video/audio 仅 Schema +
  Provider 预留接口，尚无真实生成；`structured=` 真实后端支持未验证；`bailian_vision`
  `pricing=None` → est_cost 为 null，属配置护栏；Stage 11 真实冒烟脚本已就位未执行）。
- 下一步：**新开一个 WorkBuddy 会话执行 Stage 12**（工作目录 `D:\AI\FilmAgent-main`；
  **不要用 `claude` CLI** → 读 CLAUDE.md → `v2/docs/PROJECT_STATUS.md` → `v2/docs/HANDOFF.md`
  → `v2/docs/ORCHESTRATION.md` → 最近 Stage Report `stage11_report.md` → Git 状态
  → Stage 12 开工指令（**待总控设计**）→ 相关代码）。
  Stage 12 = **Project 生命周期 + 第一条完整业务链**（Casting → Scene → Storyboard → 组装 → 审核），
  为 Stage 13 真实出图做调度准备。
  路线图方向：第一集真实生成 → V0.2.0-alpha。
