# CLAUDE.md

This file provides guidance to WorkBuddy AI when working with code in this repository.

## 项目是什么

复现 **FilmAgent**（多智能体端到端电影生成）的工程。仓库分两半：

- **`FilmAgent/`（V0.1）**：冻结基准，是一套已跑通的串行流水线（casting → scenes_plan → lines_generate → … → clean_script），依赖 DeepSeek（`LLMCaller.py` + `Prompt/`）。**任何情况下不得修改。**
- **`v2/`（V0.2）**：全新重写的基础设施——可测试、可扩展、有人工审核的多智能体框架，按阶段推进，每阶段由独立的"开工指令"文件（位于 `D:\AI\FilmAgent\`）控制。

**当前进度：阶段 8 ～ 14（Canonical Schema / Provider Architecture / Visual Asset Layer / StoryboardAgent / Project 生命周期编排层 / Image Generation / VLM Image QA）均已完成并独立封存；
阶段 15（Video Generation）尚未开工。Stage 14 封存锚点：`git rev-parse v0.2-stage14^{commit}`。**
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
阶段 12：**Project 生命周期编排层** —— 新增最顶层 `v2/orchestration/`：`preflight.py`
（调度前置校验门，5 条规则，**返回缺口清单不抛异常**；★先 `Scene.from_v01` 还原 `Line.type`）、
`state.py`（`OrchestrationState` 编排进度 + **自实现原子写** tempfile+`os.replace` + resume 判定含磁盘存在性校验）、
`pipeline.py`（`ProjectOrchestrator`：**两段式**多场景编排 + 集级 Casting（含 `set_characters`/`set_world`，
**严禁 `approve_fact`**）+ 视觉资产齐备 + 三种 review 策略 + resume + run_report）、`__init__.py`。
把 Casting / SceneGeneration / Storyboard / VisualAsset 四个已封存 Agent 串成第一条端到端业务链：
一个项目的一集（N 个场景）从 0 走到"分镜齐备 + 视觉资产齐备"。**零改禁改清单**：
★ `v2/workflow/` **0 diff + 0 新增**（上层编排未污染引擎）。阶段 12 测试 **125/125**
（总控独立复跑 rc=0）+ 历史回归 **9 套**全绿 + 真实 API **0 次**（真实冒烟脚本未创建未执行）。
阶段 13：**Image Generation（真实出图）** —— 第一次真的把画面生成出来。打开 `image` 模态
（`registry.create_provider` 只开 image 分支；★video/audio 仍被拒绝）+ 真实文生图 Provider
（`openai_compatible_image.py`：`POST {base_url}/images/generations`，b64_json / url 两路都处理，
错误翻译/瞬时重试/`transport` 注入沿用既有模式）+ 确定性 Mock 图像 Provider
（`mock_image.py`：纯 stdlib 最小合法 PNG，0 网络 0 成本）+ 图像预算桶
（`MAX_REAL_IMAGE_REQUESTS=3`）+ 图像价格表（`config.IMAGE_PRICING`，**与 token 价物理分开**）
+ 编排层 image 段（`orchestration/image_stage.py`：compose → 出图 → 落盘 → manifest →
记账 → Review 三态 → `ShotAsset(kind="image")` 挂回 → **Storyboard 新版本**）。
**三重护栏**：①默认不出图（`generate_images` 缺省 `False`）②预算硬上限
（`cap = min(max_images, MAX_REAL_IMAGE_REQUESTS)`，达到即拒、**绝不发出第 N+1 次**，retry 也计预算）
③绝不伪造出图（无 key / 未配置 / 预算耗尽 → 如实记 failed/rejected，**绝不用占位图冒充**）。
**零改禁改清单**：`v2/core/` 0 diff、`v2/workflow/` 0 diff、仓库根 `.gitignore` 0 diff、
`FilmAgent/` 0 diff。★ 真实出图**未执行**（人工四项前置未齐 → 如实报告阻塞）。
阶段 13-P（补丁，2026-09-18）：**百炼 DashScope 原生档** —— 新增 `bailian_image.py`
（千问同步 + 万相异步 `X-DashScope-Async` + `task_id` 轮询；纯 stdlib 无新依赖；`transport` 可注入；
★多图明确报错、绝不静默丢图；usage 回传）。图像 Provider 现为**两档并存**，由 Config 的
`protocol` 字段分派（`registry._create_image_provider`）：缺省 `"openai"` →
`OpenAICompatibleImageProvider`（火山方舟 / OpenAI / 硅基流动 等）；`"dashscope_native"` →
`BailianImageProvider`（百炼原生）。**换回来只改 Config，代码 0 行改动**。
★ 预算语义：`_invoke()` 包住整次业务尝试（含内部 N 次轮询）⇒ **一张图 = 1 次计费调用**。
详见 `v2/logs/stage13_image/stage13p_native_provider.md`。
阶段 14：**VLM Image QA（生成图 vs 参考图 → 逐字段裁决）** —— 在 Stage 13 与 Stage 15 之间
插入一条**只读质量门**：`agents/image_qa.py`（`ImageQAAgent`/`ImageQAStep` + 纯函数
`expected_checks` / `validate_provider_payload` / `validate_artifact` / `qa_decision`）
+ `prompts/v0.2/image_qa.txt`（只含 checks+summary 的稳定输出契约）
+ `orchestration/image_qa_stage.py`（`ImageQAStage`：manifest 解析 → 路径/SHA/精确版本/
  reference 归属预检 → 逐图 Workflow run → 二次校验 → `quality/qa_manifest.json` 索引 → resume）
+ pipeline 默认关闭接线（`_stage_image_qa`，段顺序 `… → image → image_qa → completeness`）。
**关键契约**：①读 manifest 指定的**精确版本**，绝不漂移 `active_version()`；
②送模顺序固定 `images[0]=生成图`，`images[1:]=参考图按 manifest 顺序`；
③`qa_decision` 只由**本地确定性规则**计算（任一 mismatch→fail；否则有 uncertain/not_visible→
needs_review；全 match→pass），忽略模型自报值；④`downstream_eligible` 是**可重算派生值**，
不信 index 自报；⑤Provider 惰性构造、预算共用 `vision` 桶、`cap=min(max_qa_images, 3)`。
★ **D14-REF-01**：独立 `reference` 资产图片按 registry 全局 `image_id` **恰好一处命中**反查，
零/多命中或猜归属一律 Provider 调用前 fail-loud。
Stage 14 测试 **198/198**（T01~T13）rc=0、真实 API/网络/模型切换 **0/0/0**；
★ 真实多图 VLM 连通性**未验证**（`run_real_image_qa.py` 只是 preflight/guard scaffold，
默认 rc=2、0 调用；且 bailian key 环境变量与项目配置不一致，属开放问题）。
协作规范与会话管理体系已落地（COMPLETE）：A/B/C/D = 不同 **AI 编码会话**（不绑定特定工具）
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

## 工作区清理与回收站（长期规则）

- **删除语义 = 回收站**：本环境由宿主经 `PYTHONPATH` 注入的 `sitecustomize` shim 把
  `shutil.rmtree` 劫持为"移入回收站"（见 `v2/docs/ORCHESTRATION.md` §5.1）。测试脚本里的
  `_hard_rmtree` 是**例外**——它走 `os` 层递归删除以便清理 `tmp_projects/`，不受本约定约束。
- **回收站阈值 = 100**：把文件放进回收站后，当回收站内文件数**达到 100** 时即**彻底删除**
  （永久清空该回收站），而不是继续堆积。目的：防止回收站无界增长占满磁盘。
- **边界（安全）**：本规则**只作用于回收站里的已删除文件**，**绝不**用于删除仍在项目目录中的
  活文件；仓库内脚本不得据此对项目文件做永久删除。清空回收站属环境/人工动作。

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
python v2/logs/stage12_lifecycle/run_tests.py                      # 125/125（编排层 preflight 5 规则/state 原子写+resume/pipeline 两段式多场景/视觉齐备/Review 三态/resume/Token 硬证据/完整性 + 内嵌历史回归 9 套），mock，0 次真实调用
python v2/logs/stage13_image/run_tests.py                          # 143/143（T01~T19 + Stage 13-P 的 T20a~T20q：Mock 确定性出图/★默认不出图/★预算硬上限/审核三态/sha256 一致性/缺档案不硬出图/图像记账/★二进制泄漏扫描/静态检查/禁改 0 diff/端到端+resume/★百炼原生档（同步·异步轮询·预算语义·两档分派） + 内嵌历史回归 10 套），mock，0 次真实调用
python v2/logs/stage10_visual_asset/run_real_visual_extraction.py   # 真实 Vision 冒烟（qwen3.7-plus，计费，人工批准）
python v2/logs/stage11_storyboard/run_real_storyboard.py            # 真实文本冒烟 T31~T35（≤2 次调用，计费，人工批准；已就位未执行）
python v2/logs/stage13_image/run_real_image.py                      # ★真实出图冒烟（并入 Stage 12 遗留 run_real_lifecycle；默认只体检 0 真实调用；
                                                                    #  需四项人工前置 + --approve；≤3 张图像 + ≤5 次文本；已就位未执行）
python v2/logs/stage14_image_qa/run_tests.py                        # 198/198（T01~T13：静态契约/选择前失败/精确版本/D14-REF-01 resolver/路径+SHA/
                                                                    #  Prompt 顺序/严格校验/Workflow+Review 三态/index 完整性/resume/预算/默认关闭/
                                                                    #  冒烟脚本零调用护栏），mock，0 网络 0 真实 API
python v2/logs/stage14_image_qa/run_real_image_qa.py                # ★真实多图 VLM QA 冒烟 preflight/guard scaffold（默认拒绝 rc=2、0 调用；
                                                                    #  全前置满足也只打印 PRECONDITIONS SATISFIED 返回 rc=3，不发真实请求；未连通）
```

> **★ stage13 套件是当前最重的套件**：T18 内嵌 10 套历史回归，而这些历史套件**自身也在内嵌**
> （stage12→stage1..11→stage11→stage1..10→stage10→…），总执行次数呈指数级增长，
> 一次全量复跑约需 **40~60 分钟**（实测 ~58 分钟）。若只想快速自检本阶段改动，
> 可直接跑 stage10/stage11 这两套较轻的历史套件做抽样核对。

> **跑测试务必加 `env -u PYTHONPATH`**（宿主经 `PYTHONPATH` 注入 `sitecustomize` shim 劫持
> `shutil.rmtree`，会导致历史套件假失败；判据=独立复跑 rc=0。详见 `v2/docs/ORCHESTRATION.md` §5.1）。

- **日常回归策略**：跑 stage13 套件即可（其内嵌子进程已覆盖 stage1/3/4/6/7/8/9/10/11/12 十套；
  ★耗时约 40~60 分钟）；也可单独逐套跑 stage4 + stage6 + stage7 + stage8 + stage9 + stage10 + stage11 + stage12 核对。
  ★ stage13 套件在起始与历史回归前后会**自动复位**被回归重写的已跟踪产物
  （按 `git status` 扫历史套件目录后精确 `git checkout --`），跑完工作区保持干净（技术债 T-6 已缓解）。
- **真实 LLM 调用只走独立脚本**（stage2 完整套件、stage6 的 `run_real_casting.py`），日常回归 0 计费。
- **静态检查**（阶段 5 方式，环境无 ruff/mypy）：`compileall` + 全模块 import 冒烟 + 用正则扫 v2/ 源码里的真实 key 与硬编码绝对路径。
- V0.1 冻结的自动化断言（stage3/4/6/7 各套件的 V0.1 完整性测试）：`v0.1^{commit}` 固定 `344c0c6…`、`v0.2-stage7` tag 存在、`git diff -- FilmAgent/` 为空、`FilmAgent/` 无 dirty 行。

## 架构（大图）

### 依赖方向（只允许向下，禁止反向/循环）

```text
core → (llm | memory | context) → workflow → review → agents → orchestration
```

- `core` 不依赖任何上层；`workflow` 不得依赖 `review`（`ReviewDecision` 常量定义在 `workflow/models.py`，方向是 `review → workflow`）。新增模块先定所属层再写 import。
- `orchestration`（阶段 12，★最顶层）可以 import 下面所有层；**下面任何层不得 import orchestration**（禁止反向/循环）。
- 导入方式：脚本/测试开头 `sys.path.insert(0, str(V2_ROOT))` 后**绝对导入**（`from core.models import ...`）。

### 各层职责

| 层 | 位置 | 职责 |
|---|---|---|
| core | `v2/core/` | `models.py`（Scene/Character/Line/…/CastingResult）、`shot.py`（阶段8 Canonical：Shot/Beat/Storyboard/ShotAsset/受控词表）、`adapter.py`（阶段8：V0.1↔Canonical 无损转换）、`validator.py`、`io.py`（JSON 读写 + V0.1 字段容错）、`assets.py`、`token_log.py`（JSONL，不记 key 不记 prompt 正文）、`visual.py` / `visual_registry.py` / `visual_compose.py`（阶段10：视觉身份+版本+参考图 / Registry / Composer） |
| llm | `v2/llm/` | `client.py`（`LLMClient.chat` → `LLMResponse`；`LLMError` 错误家族带 `retryable`、重试、token log；transport 可注入）+ `providers/`（阶段 9：`Provider` 接口族 Text/Vision/Multimodal/Embedding + Mock 四件套 + `OpenAICompatibleTextProvider` + `BailianTextProvider` + `OpenAICompatibleVisionProvider`（阶段10）+ ★阶段13：`MockImageGenerationProvider` / `OpenAICompatibleImageProvider` / `ImageGenerationError` + `registry.create_provider` 工厂）+ `router.py`（`get_route` → `ProviderRoute`、`get_provider` 产出实例）+ `request_counter.py`（阶段10：真实请求计数 + 预算硬上限；★阶段13 新增 `image` 桶与 `set_budget`） |
| memory | `v2/memory/store.py` | `MemoryStore(project_id)`；facts 三态 candidate/approved/rejected、characters、world、history |
| context | `v2/context/assembler.py` | `ContextAssembler` 按 Step 声明的 `needs` 组装 `AssembledContext`（每条带来源信息） |
| workflow | `v2/workflow/` | `Step`（run/validate）、`WorkflowEngine.run(project_id, workflow_id, inputs, max_attempts, resume_from)`、`ArtifactStore`（版本化落盘）、状态机 |
| review | `v2/review/service.py` | submit / approve / approve_with_edit / reject；记录持久化到项目 `reviews/` |
| agents | `v2/agents/` | 公共骨架 `base.py`（`LLMAgent`/`LLMStep`，阶段8抽取）+ `casting.py` / `scene_generation.py` / `visual_asset.py`（阶段10）/ `storyboard.py`（阶段11：`StoryboardAgent`/`StoryboardStep` + `normalize_storyboard`） |
| orchestration | `v2/orchestration/` | 阶段12 编排层（★最顶层）：`preflight.py`（`preflight_check` 调度前置校验门，5 规则，返回缺口清单不抛异常）+ `state.py`（`OrchestrationState` 编排进度 + 自实现原子写 + `should_skip`；★阶段13 纯追加 `record_image_stage`/`image_entry`）+ `pipeline.py`（`ProjectOrchestrator`：两段式多场景编排 + 集级 Casting + 视觉资产齐备 + 三种 review 策略 + resume + run_report；★阶段13 新增 image 段接线 `_stage_images` 与 `run_spec["style_spec"]`）+ ★阶段13 `image_stage.py`（`ImageStage`：compose→出图→落盘→manifest→记账→Review→`ShotAsset(kind="image")` 挂回→Storyboard 新版本；`resolve_image_bytes` / `shot_ref_of`）+ ★阶段14 `image_qa_stage.py`（`ImageQAStage`：manifest→路径/SHA/精确版本/reference 全局反查预检→逐图 Workflow→二次校验→`quality/qa_manifest.json` 索引→resume；`safe_project_file` / `build_reference_index` / `prepare_image_qa_input`） |

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

阶段 12 编排特有约定（Stage 13+ 必须延续）：**两段式** —— `scene_gen_{sid}` 与
`storyboard_{sid}` **各一次 run**（`WorkflowEngine.run()` 的 `inputs` 是整次 run 共享的一个 dict，
而 `StoryboardStep` 只读 `inputs["scene"]`、**完全忽略上游 Step 产出** → 同 run 装两 step 必然串场）；
`inputs["scene"]` 必须**显式放入**。编排层是**唯一"决定顺序"的地方**，但它同样不得绕过 Agent
直接调 Provider、不得自己解析 LLM 输出；集级 Casting 通过后编排层 `set_characters`/`set_world`
（**严禁 `approve_fact`** —— 事实批准是"人"的动作）；视觉资产只支持 approved / rejected
（遇 `approved_with_edit` → 抛 `OrchestrationError`）；resume 对已 approved 单元
**直接跳过、不调用引擎**（`state.json` 唯一事实源，跳过前校验磁盘 artifact 仍存在）；
`preflight_check` 必须在喂 Stage 11 之前跑（★先把落盘 dict 经 `Scene.from_v01` 还原 `Line.type`，
否则 move 行漏检）；`run_report["review_mode"]` 必含 `"programmatic, NOT human review"`
（**绝不**把 `auto_approve` 的产物描述成"已人工审核通过"）。

阶段 13 图像特有约定（Stage 14+ 必须延续）：

- **三重护栏**：① `run_spec["generate_images"]` 缺省 `False`（**不显式打开就一张都不生成**）；
  ② 预算硬上限 `cap = min(run_spec["max_images"], MAX_REAL_IMAGE_REQUESTS=3)`，达到即拒
  （`APIBudgetExceededError`），**绝不发出第 N+1 次**；同一 cap 经
  `APIRequestCounter.set_budget("image", cap)` 下推到真实 HTTP 层（纵深防御）；**retry 也计预算**；
  ③ 无 key / 未配置模型 / 预算耗尽 → 如实记 `failed`/`rejected`，**绝不用占位图冒充真实出图**。
- **出图 prompt 唯一来源**：`ComposedVisualInput.render_prompt()`（经 `VisualComposer.compose()`）；
  **不得**绕过 Composer 自己拼 prompt，否则视觉一致性设计失效。
- **★ STYLE 是硬前置**：`core.shot.ASSET_KINDS` 里没有 `style`，STYLE 无法从 Storyboard 自动收集，
  必须由 `run_spec["style_spec"]` 显式提供（走同一套 VisualAssetAgent + Review + registry）；
  不提供 → `compose()` 明确报错列缺口，**不硬出图**。
- **引用串契约**：`ImageGenerationProvider.generate_image()` 的返回用 `ProviderResult.text`
  携带引用串（`data:…base64` / `http(s)` / 本地路径）——接口签名 0 改，落盘归编排层（Artifact 铁律）。
- **三本账分离**：token 账（`TokenLogger`）/ 请求计数（`APIRequestCounter` 分桶）/
  图像账（`image_generation_calls.jsonl`，字段风格与 `TokenLogger` 对齐、**不含 token 概念**）。
  **绝不**把图像成本塞进 token 账（计价单位不同）；未登记图像价 → `estimated_cost` 记 `null`。
- **落盘纪律**：`GEN_%03d` 单调递增、**绝不复用 ID、绝不覆盖文件**；`sha256` 记进
  `manifest.json`；挂回 Shot 走 **Artifact 新版本**（原 Storyboard 文件字节不变）；
  `manifest` 必须记 `profile_versions` + `reference_images`（★Stage 14 VLM QA 的输入契约）。
- **resume 保护成本**：manifest 中已 `generated` 的 `shot_ref` 重跑时**直接跳过**
  （零 Provider 调用、零新产物），只把已有引用补挂回 Shot。
- **图像审核遇 `approved_with_edit`** → 明确抛 `OrchestrationError`（图像是二进制，
  无"编辑后内容"的确定性来源；fail-loud 优于静默猜）。
- **★ 二进制泄漏防护**：生成的真实图片绝不进版本库；`v2/.gitignore` 已补 §8.4 规则
  （仓库根 `.gitignore` **仍 0 改**）；交付前必做二进制扫描（stage13 套件 T15）。

阶段 14 图像 QA 特有约定（Stage 15+ 必须延续）：

- **只读质量门**：QA 绝不修改 Stage 13 `manifest.json`、生成图、Storyboard、`ShotAsset`
  或 `VisualAsset` registry；`quality/qa_manifest.json` 只是 Artifact/Review 的**只读索引**，
  **不是**第二状态机。
- **精确版本不漂移**：一律读 manifest `profile_versions` 指定的版本，只接受
  `approved` / `superseded`；**禁止**回落 `asset.active_version()`（否则拿新档案审旧图）。
- **★ D14-REF-01（长期裁决）**：manifest `reference_images` 允许含不在 `profile_versions`
  中的独立 `reference` 资产图片。Resolver 必须遍历 registry **全部资产的全部不可变版本**，
  以 `image_id` 做**全局反查并要求恰好一处命中**，再绑定 `asset_id+version+path+checksum`；
  零命中、多命中、`active_version()` fallback、按路径/checksum 猜归属、静默跳过**全部禁止**。
  仍须 manifest sha256 == Registry checksum == 磁盘 sha256 三方一致 + 项目目录 containment。
- **送模顺序唯一合法**：`images[0]=generated`，`images[1:]=reference` 严格按 manifest 顺序；
  不排序、不去重；registry 扫描顺序不得影响输入顺序。
- **本地裁决唯一事实**：`qa_decision` 只由本地规则计算（mismatch→fail；否则 uncertain/
  not_visible→needs_review；全 match→pass），**忽略**模型自报的 `qa_decision` 与
  provider/model/request_id/retry_count/created_at（后者由 ProviderResult 注入）。
  严格 Schema：check 集合与 `FIELD_ORDER` 推导集合**完全相等**，缺失/额外/重复/改写
  expected/非法 verdict 一律 `StepError`，**绝不静默补齐为 match**。
- **★ 下游准入（Stage 15 门槛）**：同时满足 `Review ∈ approved-like` **且**
  `qa_decision == "pass"` **且** resume 时全部锚点仍有效，才可下游消费；
  `downstream_eligible` 是**可重算派生值**，下游不得仅凭该布尔值绕过条件。
- **默认关闭 + 惰性 Provider**：`run_image_qa` 缺省 `False` → 零调用/零 Artifact/零索引变更，
  Stage 13 行为逐字节保持；QA Provider 只有开启且预检/resume 后仍有待处理项时才构造。
- **预算**：`cap = min(max_qa_images, MAX_REAL_VISION_REQUESTS=3)`，下推 `vision` 桶；
  真实 QA Provider 必须 `budget_kind == "vision"`（`multimodal` 等调用前拒绝）；
  Provider retry 与 Workflow retry 共用同一 cap，**不逐图重置**；第 N+1 次发送前拒绝。
- **resume 锚点含顺序**：`profile_versions` 按**有序** `(asset_id, version)` 比较（顺序影响
  checks 与 Prompt 语义）；generated path/sha、reference 列表（含顺序）、Artifact sha、
  Workflow/Review 批准态、Artifact 重验、`provider+model` 身份锚点全部一致才 skip。
- **★ 真实多图 VLM 连通性未验证**：`run_real_image_qa.py` 只是 **preflight/guard scaffold**
  （默认 rc=2、0 调用；全前置满足也只打印 `PRECONDITIONS SATISFIED` 返回 rc=3，不构造
  Provider）；且它对 `bailian` 检查 `DASHSCOPE_API_KEY`，而 `config.PROVIDERS["bailian_vision"]`
  用 `BAILIAN_API_KEY` —— **两处环境变量不一致，真实冒烟实现前必须先统一**。

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

- 当前阶段：V0.2 Stage 8 / 9 / 10 / 11 / 12 均 **PASS + 已封存**；
  V0.2 Stage 13（Image Generation 真实出图）**已完成并封存**（`v0.2-stage13`）。
  ★ 真实出图**未执行**（人工四项前置未齐 → 如实报告阻塞）。
  V0.2 Stage 14（VLM Image QA）**已完成并独立封存**（`v0.2-stage14`；198/198，rc=0，真实 API/网络/
  模型切换 0/0/0）。
  ★ 真实多图 VLM 连通性**未验证**；`run_real_image_qa.py` 只是 preflight/guard scaffold。
  阶段报告：`v2/logs/stage14_image_qa/stage14_report.md`；契约笔记：
  `v2/logs/stage14_image_qa/stage14_contract_notes.md`。
- 权威状态文档：`v2/docs/PROJECT_STATUS.md`；给新会话的交接文档：`v2/docs/HANDOFF.md`；
  总控工作手册：`v2/docs/ORCHESTRATION.md`；路线图：`v2/docs/ROADMAP.md`。
- Git：版本锚点 `v0.1` = `344c0c6…`、`v0.2-stage7` = `300aae4…`、`v0.2-stage8` = `abf8e88…`、
  `v0.2-stage9` = `82a89b5…`、`v0.2-stage10` = `c8a3ed8…`、`v0.2-stage11` / `v0.2-stage12` =
  各自 tag 指向的 commit（`git rev-parse <tag>^{commit}` 查精确 hash，不写死）；
  **九个 tag**（`v0.1` + `v0.2-stage7`…`v0.2-stage14`）均不移动；`FilmAgent/` 0 diff；
  仓库根 `.gitignore` 0 diff。最近 commit 见 `git log`。
  ★ `v0.2-stage13` 与 `v0.2-stage14` 均用 `git rev-parse <tag>^{commit}` 查精确 hash。
- 已交付：阶段 0~12 全部完成并封存。阶段 12 = 编排层 `v2/orchestration/`
  （`preflight.py` / `state.py` / `pipeline.py` / `__init__.py`），把四个已封存 Agent 串成第一条
  端到端业务链（Casting → [每场景] Scene → Storyboard → 视觉资产齐备），
  测试 **125/125**（总控独立复跑）+ 历史回归 **9 套**全绿 + 真实 API 0 次；
  ★ `v2/workflow/` 0 diff + 0 新增。
  阶段 13 = 图像链路：`image` 模态开通（mock + 真实 OpenAI 兼容档，video/audio 仍拒绝）
  + `v2/orchestration/image_stage.py`（compose→出图→落盘→manifest→记账→Review→挂回→新版本）
  + 三重护栏（默认不出图 / 预算硬上限 / 绝不伪造出图）+ 图像独立记账；
  ★ `v2/core/` 0 diff、`v2/workflow/` 0 diff、仓库根 `.gitignore` 0 diff。
  阶段 14 = 图像 QA 只读质量门：`v2/agents/image_qa.py` + `v2/prompts/v0.2/image_qa.txt`
  + `v2/orchestration/image_qa_stage.py` + pipeline 默认关闭接线；
  测试 **198/198**（T01~T13）rc=0 + 真实 API/网络/模型切换 0/0/0；
  ★ `v2/core/` 0 diff、`v2/workflow/` 0 diff、`v2/orchestration/image_stage.py` 0 diff、
  仓库根 `.gitignore` 0 diff、历史断言一个未动。
  历史回归边界：Stage 13 套件 **144/146、rc=1**（两个失败是合法未提交 Stage 14 变更触发的
  pre-commit 清洁度护栏，非功能回归）。
- 已知遗留：见 `v2/docs/PROJECT_STATUS.md` §6 与 `v2/docs/ROADMAP.md` §5（openai 库安装；
  人工审核是程序化模拟，交互式 UI 未做；Video / Audio Agent 未实现；
  `structured=` 真实后端支持未验证；`bailian_vision` `pricing=None` → est_cost 为 null，属配置护栏；
  ★ Stage 13 新增：目标厂商是否支持 OpenAI `/images/generations` **未能从公开文档核实**（T-9）；
  `config.IMAGE_PRICING` 空表 → 图像成本记 null（T-10）；单 run 出图上限固定
  `min(max_images, 3)`（T-8）；`manifest.json` 因 `projects/` 整目录忽略而不入库（T-11，
  真实冒烟脚本已把副本落到 `v2/logs/stage13_image/real_run/`）；Stage 12 遗留的
  `run_real_lifecycle.py` **已并入** `v2/logs/stage13_image/run_real_image.py`（已就位未执行）；
  ★ Stage 14 新增：**真实多图 VLM 连通性未验证**；`run_real_image_qa.py` 只是
  preflight/guard scaffold（默认 rc=2、0 调用，全前置满足也只打印计划并返回 rc=3）；
  该脚本对 `bailian` 检查 `DASHSCOPE_API_KEY` 而 `config.PROVIDERS["bailian_vision"]` 用
  `BAILIAN_API_KEY` —— **环境变量名不一致，真实冒烟前必须先统一**（开放问题，未修代码）；
  `test_report.json` 的 `regressions` 为空、未承载 Stage 13 历史回归详情（覆盖边界见阶段报告 §9）。
- 下一步：Stage 14 已单独 commit + tag `v0.2-stage14`；Stage 15（Video Generation）须按
  `v2/docs/stages/Stage_15.md` 的独立准入、实现和验收纪律推进。**Stage 15 尚未开工。**
  验收要点：本报告 §11 的 14 条 Acceptance Criteria；`stage14_report.md` +
  六份长期文档 + 冻结边界/泄漏扫描 + intended commit file list。
  启动后续会话（工作目录 `D:\AI\FilmAgent-main`；**不要用 `claude` CLI** → 读 CLAUDE.md →
  `v2/docs/PROJECT_STATUS.md` → `v2/docs/HANDOFF.md` → `v2/docs/ORCHESTRATION.md` →
  最近 Stage Report `v2/logs/stage14_image_qa/stage14_report.md`（含契约笔记
  `stage14_contract_notes.md`）→ Git 状态 → 下一阶段开工指令 → 相关代码）。
  Stage 14 已完成 = **VLM Image QA**：生成图 vs 参考图 → 按 approved 档案字段**逐项裁决**，
  产出可审核、可版本化、可恢复的 QA 报告，并为 Stage 15 提供准入条件
  （`Review approved-like` ∧ `qa_decision == "pass"` ∧ resume 锚点有效）。
  路线图方向：第一集真实生成 → V0.2.0-alpha。
