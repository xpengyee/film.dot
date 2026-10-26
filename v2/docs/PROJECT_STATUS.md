# PROJECT_STATUS.md —— FilmAgent 项目状态

> FilmAgent 的权威项目状态文档，任何新 **WorkBuddy 会话**开始时都应先读本文件。
> 最后核对：2026-10-26。Stage 13 与 Stage 14（VLM Image QA）均已独立封存；Stage 14
> 正式套件 198/198、rc=0（封存前机器报告）。`v0.2-stage13` 未移动；Stage 14 锚点
> 使用 `git rev-parse v0.2-stage14^{commit}` 查询。Stage 15 已有 B 实现快照，
> 本地离线套件复跑 85/85、rc=0；C 独立验证和 D 交付验收尚未完成，Stage 15 未封存。

> **Stage 13-P 验收结论**：重复 `/api/v1` 缺陷已修复；T20r–T20t 覆盖公共域同步、
> 公共域异步提交与轮询、业务空间域的最终 URL。现有报告为 146/146、rc=0、真实 API 0 次。
> 本结论验证本地协议实现与回归，不代表真实百炼连通性已验证。

> **Stage 14 封存依据（封存前验收证据）**：Stage 14-C 正式套件 **198/198、rc=0**，
> 真实 API / 网络 / 模型切换 **0 / 0 / 0**；三个已证明缺陷已最小修复（仅
> `orchestration/image_qa_stage.py`）。Stage 13 历史套件为 **144/146、rc=1** ——
> 两个失败（T17a、T18-stage12）均为合法未提交 Stage 14 变更触发的 pre-commit 清洁度护栏，
> 非功能回归。★ **真实多图 VLM 连通性未验证**。详见
> `v2/logs/stage14_image_qa/stage14_report.md`。

## 1. 项目简介

复现 **FilmAgent**（多智能体端到端电影生成）的工程。仓库分两半：

- **`FilmAgent/`（V0.1）**：冻结基准，是一套已跑通的串行流水线
  （casting → scenes_plan → lines_generate → … → clean_script），依赖 DeepSeek
  （`LLMCaller.py` + `Prompt/`）。**任何情况下不得修改。**
- **`v2/`（V0.2）**：全新重写的基础设施——可测试、可扩展、有人工审核的多智能体框架，
  按阶段推进，每阶段由独立的"开工指令"文件（位于 `D:\AI\FilmAgent\`）控制。

## 2. 当前版本与阶段

```text
当前版本：V0.2（重构版基础设施）
当前阶段：Stage 15（Video Generation）B 实现快照待 C 独立验证；Stage 14 已独立封存

V0.2 Stage 8：PASS + 已封存
V0.2 Stage 9：PASS + 已封存（Multi-Modal Provider Architecture）
V0.2 Stage 10：PASS + 已封存（Visual Asset Layer）
V0.2 Stage 11：PASS + 已封存（StoryboardAgent：Scene → Storyboard → Beat → Shot）
V0.2 Stage 12：PASS + 已封存（Project 生命周期 + 第一条完整业务链：
               Casting → Scene → Storyboard → 视觉资产齐备；编排层 `v2/orchestration/`）
               `git rev-parse v0.2-stage12` 查精确 hash
V0.2 Stage 13：代码/测试/报告已交付并封存（`v0.2-stage13`；
               `git rev-parse v0.2-stage13` 查精确 hash）；
               ★ 真实出图**未执行**（人工四项前置未齐 → 如实报告阻塞，不用占位图冒充）
V0.2 Stage 14：VLM Image QA —— 代码/测试/报告已交付且独立 commit/tag（`v0.2-stage14`）；
               封存前正式套件 198/198、rc=0，真实 API/网络/模型切换 0/0/0；
               ★ 真实多图 VLM 连通性**未验证**（run_real_image_qa.py 仅为 preflight/guard scaffold）
V0.2 Stage 15：Image-to-Video Clip Generation —— B 会话代码与测试快照已存在；
               2026-10-26 本地复跑 85/85、rc=0（Mock/fake transport，0 真实 API/网络/模型切换）；
               C 独立验证、Stage 14 历史回归、D 报告与封存验收尚未完成；不宣称 Stage 15 PASS。
```

Stage 14 分四段（各自独立会话）：

```text
Stage 14-A   契约 / 架构分析        → stage14_contract_notes.md（含 D14-REF-01 上报）    PASS（总控已裁决）
Stage 14-B   实现                   → agents/image_qa.py + prompts/v0.2/image_qa.txt
                                      + orchestration/image_qa_stage.py + pipeline 接线  PASS（自检）
Stage 14-C   正式测试 / Debug       → run_tests.py + test_report.json + run_real_image_qa.py
                                      + 3 处最小修复（仅 image_qa_stage.py）→ 198/198    PASS
Stage 14-D   文档 / 交付 / Git 准备 → stage14_report.md + 六份长期文档 + 交付清单     PASS
封存会话     总控静态复核 / Git     → 独立 commit + tag v0.2-stage14                已封存
```

## 3. 历史 Stage 0–13（里程碑）

| Stage | 内容 | 状态 |
|---|---|---|
| 0 | 建立 V0.2 `v2/` 骨架、冻结 V0.1、复制 prompts / location assets、验证 IO | PASS |
| 1 | 统一数据模型、Validator、Assets，验证 V0.1 数据无损往返 | PASS |
| 2 | 建立 LLMClient、Router、Token/Cost 日志与错误处理 | PASS |
| 3 | 建立 Workflow Engine、Step、Artifact、Retry、历史版本机制 | PASS |
| 4 | 建立 Memory、ContextAssembler、Review。核心原则：`candidate facts → 人工确认 → approved facts`；同时保持 Artifact 历史版本 | PASS |
| 5 | 完成依赖方向整理、静态检查、README / DEVELOPMENT / AGENTS 等基础文档 | PASS |
| 6 | 完成第一个真实业务 Agent：`CastingAgent`，验证跨集角色复用 | PASS |
| 7 | 完成第二个真实业务 Agent：Scene / Script Generation Agent，基于已批准事实、角色、地点、动作、镜头资产生成完整场景剧本 | PASS |
| 8 | 完成 Canonical Schema：`Project → Scene → Beat → Shot → Asset`；建立 Shot / Beat / Storyboard / Asset / Adapter / Agent Base / 相关 Validator。Stage 8 测试 27/27 + 历史回归全绿 + V0.1 无损往返 + 0 次真实 LLM + 0 API 成本 | PASS + 已封存 |
| 9 | 完成 Multi-Modal **Provider Architecture**：统一 Provider 接口族（Text/Vision/Multimodal/Embedding + 预留 Image/Video/Audio）+ Mock 四件套 + Registry/Factory + Router 真入口 + Config Provider 注册表（deepseek/bailian/local/vision/embedding 五档）+ Bailian 文本实现位 + `OpenAICompatibleTextProvider`。Agent / Router / Provider / Model 职责边界划清，业务 Agent 只认抽象 Provider。**C 会话发现并修复 1 个真实缺陷**：Mock Provider 写 TokenLogger 丢了 `prompt_hash`（已补）。Stage 9 测试 26/26 + 历史回归全绿 + 真实 API 0 次 + ¥0.00 | PASS + 已封存 |
| 10 | 完成 **Visual Asset Layer**：语义与视觉分离——`core/visual.py`（VisualAsset / VisualAssetVersion / ReferenceImage + 五类/七态/来源词表）、`core/visual_registry.py`（身份分配 CHAR_001… / (kind,name) 永久绑定 / 版本单调递增绝不覆盖 / 参考图 sha256 checksum / 生命周期=Review 三态的确定性派生）、`core/visual_compose.py`（VisualComposer：调用模型前一次性展开 profile，Shot 只存名字引用）、`agents/visual_asset.py`（plan=TextProvider / extract=VisionProvider）。**Token 硬证据**：注册视觉资产前后 `Storyboard.to_dict()` 逐字节不变。Stage 10-B 完成 `qwen3.7-plus` 真实 Vision 冒烟 3 次（14,433 tokens）；Stage 10-C 修复参考图挂版本链路 + Git 污染。Stage 10 测试 76/76 + 历史回归全绿 | PASS + 已封存 |
| 11 | 完成 **StoryboardAgent**：`agents/storyboard.py`（`StoryboardAgent`/`StoryboardStep` 继承 `LLMAgent`/`LLMStep` + §7.2 全部 **8 条**确定性规范化 `normalize_storyboard()`）+ `prompts/v0.2/storyboard_generation.txt`。Scene（调度层经 `inputs["scene"]` 注入，缺则 `StepError`）→ Storyboard → Beat → Shot，Shot 落 Stage 8 受控词表内、只存名字级引用 + 本镜头视觉增量，可被 `VisualComposer` 直接消费；受控词表从 `core.shot` 常量渲染进 user payload + prompt 词表一致性运行时自检；`artifact_name=storyboard_{scene_id}`、`step_id=storyboard_generation_{scene_id}`；走既有 Artifact 版本化 + Review 三态 + Retry，零新机制。Stage 11-C 修复"列表字段非列表静默当空"并补 T20 边界测试。Stage 11 测试 **65/65** + 历史回归 8 套全绿 + 真实 API **0 次**（真实冒烟脚本已就位未执行） | PASS + 已封存 |
| 12 | 完成 **Project 生命周期编排层**：新增最顶层 `v2/orchestration/`——`preflight.py`（调度前置校验门：5 条规则、返回缺口清单不抛异常、★先 `Scene.from_v01` 还原 `Line.type`）、`state.py`（`OrchestrationState` 编排进度 + **自实现原子写** tempfile+`os.replace` + resume 判定含磁盘存在性校验）、`pipeline.py`（`ProjectOrchestrator`：**两段式**多场景编排 + 集级 Casting（含 `set_characters`/`set_world`，**严禁 approve_fact**）+ 视觉资产齐备 + 三种 review 策略 + resume + run_report）、`__init__.py`。把 Casting / SceneGeneration / Storyboard / VisualAsset 四个已封存 Agent 串成端到端业务链：一个项目的一集（N 个场景）从 0 走到"分镜齐备 + 视觉资产齐备"。**零改禁改清单**：★ `v2/workflow/` **0 diff + 0 新增**（上层编排未污染引擎）；依赖方向扩为 `core → (llm\|memory\|context) → workflow → review → agents → orchestration`。Stage 12 测试 **125/125**（T01~T19 + T03b/T04b/T06b/T09b/T10b 增强，**总控独立复跑** rc=0）+ 历史回归 **9 套**全绿 + 真实 API **0 次**（真实冒烟脚本未创建未执行，如实登记为"阻塞：需人工配置"） | PASS + 已封存 |
| 13 | 完成 **Image Generation（真实出图）**：打开 `image` 模态（`registry.create_provider` 只开 image 分支，**video/audio 仍拒绝**）——`llm/providers/mock_image.py`（`MockImageGenerationProvider`：纯 stdlib 确定性最小合法 PNG，**0 网络 0 成本**）+ `llm/providers/openai_compatible_image.py`（`OpenAICompatibleImageProvider`：`POST {base_url}/images/generations`，b64_json / url 两路都处理，错误翻译 / 瞬时重试 / `transport` 注入沿用既有模式，`_invoke` 为**唯一真实计数点**）+ `config.PROVIDERS["bailian_image"]`（`default_model=""` 配置护栏、`size_separator`）+ `IMAGE_PRICING` / `estimate_image_cost`（**与 token 价物理分开**，未登记 → `null`）+ `request_counter` 的 `image` 桶与 `set_budget`；编排层新增 `orchestration/image_stage.py`（compose → 出图 → 落盘 → `manifest.json` → 图像记账 → Review 三态 → `ShotAsset(kind="image")` 挂回 → **Storyboard 新版本**）。**三重护栏**：①默认不出图（`generate_images` 缺省 `False`）②预算硬上限 `min(max_images, MAX_REAL_IMAGE_REQUESTS=3)`，达到即拒、**绝不发第 N+1 次**、retry 也计预算 ③**绝不伪造出图**。**零改禁改清单**：`v2/core/` 0 diff、`v2/workflow/` 0 diff、仓库根 `.gitignore` 0 diff、`FilmAgent/` 0 diff、历史套件断言一个未动。Stage 13 测试 **126/126**（T01~T19 + 历史回归 **10 套**）rc=0 + 真实 API **0 次**；★ 真实出图**未执行**（人工四项前置未齐 → 如实报告阻塞，**未用占位图冒充**）。★ 3 处「指令 vs 既有断言」冲突经总控裁决后落地（保留「生成类」措辞 / 按 pre-commit 护栏理解 / 扩展 visual_assets 段支持 style） | 代码/测试/报告已交付 → **PASS + 已封存**（`v0.2-stage13`） |

| 14 | 完成 **VLM Image QA（生成图 vs 参考图 → 逐字段裁决）**：在 Stage 13 与 Stage 15 之间插入**只读质量门**——`agents/image_qa.py`（`ImageQAAgent`/`ImageQAStep` + 纯函数 `expected_checks` / `validate_provider_payload` / `validate_artifact` / `qa_decision`）+ `prompts/v0.2/image_qa.txt`（只含 `checks`+`summary` 的稳定输出契约）+ `orchestration/image_qa_stage.py`（`ImageQAStage`：manifest 解析 → 路径 containment/SHA256/精确版本/D14-REF-01 reference 全局反查预检 → 逐图 Workflow run → 最终 Artifact 二次校验 → `quality/qa_manifest.json` 只读索引 → resume）+ pipeline 默认关闭接线（`_stage_image_qa`，段顺序 `… → image → image_qa → completeness`）。**关键契约**：①只读 manifest 指定的**精确版本**，禁止漂移 `active_version()`；②送模顺序固定 `images[0]=生成图`、`images[1:]=参考图按 manifest 顺序`；③`qa_decision` 只由本地确定性规则计算（mismatch→fail；否则 uncertain/not_visible→needs_review；全 match→pass），模型自报值被忽略；④严格 Schema：check 集合与 `FIELD_ORDER` 推导集合完全相等，缺失/额外/重复/改写/非法 verdict 一律 `StepError`；⑤预算 `cap=min(max_qa_images, MAX_REAL_VISION_REQUESTS=3)` 下推 `vision` 桶、Provider 惰性构造、`run_image_qa` 缺省 `False`。**★ D14-REF-01（总控 2026-10-01 裁决，方案 B）**：独立 `reference` 资产图片按 registry 全局 `image_id` **恰好一处命中**反查，命中版本只允许 `approved`/`superseded`，零/多命中或猜归属一律 Provider 调用前 fail-loud；不修改 core / Stage 13 manifest / `image_stage.py`。**零改禁改清单**：`FilmAgent/` 0 diff、`v2/core/` 0 diff、`v2/workflow/` 0 diff、`v2/review/` 0 diff、`v2/llm/` 0 diff、`v2/orchestration/image_stage.py` 0 diff、仓库根 `.gitignore` 0 diff、历史断言一个未动。Stage 14 测试 **198/198**（T01~T13）rc=0 + 真实 API/网络/模型切换 **0/0/0**；14-C 修复 3 个已证明缺陷（index 重复 image_id fail-loud / resume 不信任自报 `downstream_eligible` / `profile_versions` 有序比较）。历史回归：Stage 13 套件 **144/146、rc=1**（两个失败均为合法未提交 Stage 14 变更触发的 pre-commit 清洁度护栏，非功能回归）。★ **真实多图 VLM 连通性未验证**；`run_real_image_qa.py` 仅为 preflight/guard scaffold（默认 rc=2、0 调用） | **PASS + 已独立封存**（`v0.2-stage14`；历史回归 144/146 为封存前记录，封存后未重跑） |

Stage 12 分四段（各自独立会话）：

```text
Stage 12-A   分析 / 架构设计        → v2/docs/stages/stage12_plan.md（含总控裁决 §12） PASS（总控独立复核 23/23）
Stage 12-B   编排层代码实现         → v2/orchestration/（4 文件）+ 完整套件 T01~T19   PASS
Stage 12-C   测试 / Debug           → 经总控裁定跳过（B 已交付完整 125 条套件）        SKIPPED
Stage 12-D   文档 / 交付 / Git      → 四文档 + stage12_report.md + commit + tag        PASS
```

Stage 13 本次按 **A→D 全流程**在**单个 WorkBuddy 会话**内完成（总控指定）：

```text
Stage 13-A/A2  分析 / 架构 + 厂商协议专项 → v2/logs/stage13_image/stage13_contract_notes.md
                                          （接入点契约 + 协议调研 + 3 处冲突裁决）      PASS
Stage 13-B     图像 Provider + 预算 + 编排 image 段 → 3 个新文件 + 6 个修改文件         PASS
Stage 13-C     测试 / Debug → run_tests.py T01~T19 + 历史回归 10 套 → 126/126 rc=0      PASS
Stage 13-D     文档 / 交付 → stage13_report.md + 四文档同步；★ commit/tag 等人工批准     PASS
```

Stage 11 分四段（各自独立会话）：

```text
Stage 11-A   分析 / 架构设计        → v2/docs/stages/stage11_plan.md          PASS
Stage 11-B   Storyboard 代码实现    → 45/45（+T15/T19 后 55/55）              PASS
Stage 11-C   全面测试 / 收尾 Debug  → 65/65 + 历史回归 8 套全绿 + 修静默降级  PASS
Stage 11-D   文档 / 交付 / Git      → 文档 + stage11_report + commit + tag    PASS
```

Stage 10 分四段（各自独立会话）：

```text
Stage 10-A   分析 / 架构设计        → v2/docs/stages/stage10_plan.md          PASS
Stage 10-B   Visual Asset 代码实现  → 62/62 + 真实 Vision 冒烟 3 次（qwen3.7-plus） PASS
Stage 10-C   全面测试 / 收尾 Debug  → 76/76 + 历史回归全绿 + 修参考图挂版本链路 PASS
Stage 10-D   文档 / 交付 / Git      → 本文档 + HANDOFF + stage10_report + tag   PASS（本次）
```

Stage 9 分四段（各自独立会话）：

```text
Stage 9-A   分析 / 架构设计        → v2/docs/stages/stage9_plan.md          PASS
Stage 9-B   Provider 代码实现      → 基础测试 26/26                          PASS
Stage 9-C   全面测试 / Debug       → 26/26 + 历史回归全绿 + 修复 mock 缺陷   PASS
Stage 9-D   文档 / 交付 / Git      → 本文档 + HANDOFF + stage9_report + tag   PASS（本次）
```

## 4. Git 里程碑

| 里程碑 | commit | tag |
|---|---|---|
| V0.1 冻结基准 | `344c0c6b7c48497be934957c29fcae029cca6752` | `v0.1` |
| V0.2 Stage 0–7 基线 | `300aae44ad1d704219822efcf55249d1d59ad118` | `v0.2-stage7` |
| V0.2 Stage 8 Canonical Schema | `abf8e88a983f13c437abe2b719c920d3c5c3c144` | `v0.2-stage8` |
| V0.2 Stage 9 Provider Architecture | `82a89b50d337663027e8ecd638f45d7b94eaa882` | `v0.2-stage9` |
| V0.2 Stage 10 Visual Asset Layer | `c8a3ed8282a767c3119ea29e012e7b8148d3ab3b` | `v0.2-stage10` |
| V0.2 Stage 11 StoryboardAgent | `v0.2-stage11`（`git rev-parse v0.2-stage11` 查精确 hash） | `v0.2-stage11` |
| V0.2 Stage 12 Project 生命周期编排 | `v0.2-stage12`（`git rev-parse v0.2-stage12` 查精确 hash） | `v0.2-stage12` |
| V0.2 Stage 13 Image Generation | `3895c6c103f125d458fe648d80a625bf007601af` | `v0.2-stage13` |
| V0.2 Stage 14 VLM Image QA | `git rev-parse v0.2-stage14^{commit}` 查精确 hash | `v0.2-stage14` |

版本 tag（`v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9` / `v0.2-stage10` /
`v0.2-stage11` / `v0.2-stage12` / `v0.2-stage13` / `v0.2-stage14`）一经创建**永不移动、永不 force**。

## 5. 当前架构

### 5.1 已落地的核心基础设施

```text
V0.2
│
├── core      models / validator / assets / shot / adapter / token_log
│             visual / visual_registry / visual_compose   ← Stage 10
├── llm       client / router / token & cost / providers（Stage 9）
│             providers/openai_compatible_vision · request_counter（Stage 10）
├── workflow  step / engine / artifact / errors
├── memory
├── context
├── review
├── agents    base / casting / scene generation / visual_asset（Stage 10）/ storyboard（Stage 11）
│             image_qa（Stage 14：ImageQAAgent / ImageQAStep + 严格 validator + 本地 qa_decision）
└── orchestration  preflight / state / pipeline（Stage 12，★最顶层）
                   image_stage（Stage 13）/ image_qa_stage（Stage 14）
```

依赖方向：`core → (llm | memory | context) → workflow → review → agents → orchestration`
（只允许向下，禁止反向/循环；**下面任何层不得 import orchestration**）。

### 5.2 Agent / Router / Provider / Model 分层（Stage 9 落地）

```text
Agent（Casting / SceneGeneration / 未来业务…）
   ↓  只认抽象 Provider，不认识任何厂商 SDK / endpoint / 模型名
Router
   ↓  决策：某个 task_type/step 走哪个 Provider + 哪个模型（返回路由描述，不发请求）
Provider
   ↓  真正执行某一种模态的模型调用：封装厂商协议、瞬时错误重试、上报 usage/token/cost
Model（具体模型名/参数，是数据不是逻辑，只存在于 Config / 路由结果里）
```

一句话分工：**Agent 说"要什么"，Router 说"用哪家哪个模型"，
Provider 说"这次调用结果怎么样"，Model/Config 说"我是什么、多贵"。**

Provider 类别（`v2/llm/providers/`）：

- 已落地接口族：`TextProvider` / `VisionProvider` / `MultimodalProvider` / `EmbeddingProvider`
- 未来预留接口（仅占位，调用即 `NotImplementedError`，`create_provider` 明确拒绝）：
  `ImageGenerationProvider` / `VideoGenerationProvider` / `AudioProvider`
  （对应未来 Storyboard → Image → Video → Audio → Assembly 链路，与 Stage 8 的
  `ShotAsset(kind=image/video/audio)` 衔接——资产先能表达，再接生成）
- 统一入口：`Registry/Factory`（`create_provider` / `get_default_provider`）+ `Router`
  （`get_route` 返回 `ProviderRoute` / `get_provider` 产出实例）。Mock 四件套走同一体系。

### 5.3 当前实际 Provider 能力（接口预留 ≠ 真实生产，严格区分）

```text
Mock Provider（Text / Vision / Multimodal / Embedding 四件套）
    ✅ 已实现，测试 0 API / 0 成本 / 确定性

OpenAI-compatible Text Provider（OpenAICompatibleTextProvider；deepseek 档复用 LLMClient）
    ✅ 已实现接口 / 配置落位
    ❌ 本阶段真实调用 0 次（需显式开真实脚本才会计费）

Bailian Text Provider（BailianTextProvider）
    ✅ 已实现接口 / 配置落位（OpenAI 兼容文本实现 + 百炼 Config 档）
    ❌ 真实调用前需在 Config 填 default_model + 设 BAILIAN_API_KEY；本阶段未真实调用

Local Text Provider
    ✅ 已准备 Config 档 / 接入路径（大概率是 OpenAICompatibleTextProvider 的一档配置，非新类）
    ❌ 本次未下载 / 未部署 / 未接入任何本地模型（Ollama / vLLM / 权重）

Vision Provider
    ✅ 接口 / Mock（MockVisionProvider）
    ❌ 本阶段无真实视觉模型调用

Multimodal Provider
    ✅ 接口 / Mock（MockMultimodalProvider）
    ✅ **Stage 14 消费方**：VLM Image QA 经 `MultimodalProvider.chat(messages, images=...)`
       一次查看"生成图 + 全部参考图"（`OpenAICompatibleVisionProvider` 已实现该调用面，
       无需修改公共接口）
    ❌ 真实多图 QA 调用 0 次（★ 真实多图 VLM 连通性**未验证**；`run_real_image_qa.py` 仅为
       preflight/guard scaffold，默认拒绝 rc=2、0 调用）

Embedding Provider
    ✅ 接口 / Mock（MockEmbeddingProvider）
    ❌ 本阶段未接真实向量模型

Image / Video / Audio Provider
    Image    ✅ **Stage 13 已开通**：`MockImageGenerationProvider`（确定性最小合法 PNG，
                 data URL 引用，0 网络 0 成本）+ `OpenAICompatibleImageProvider`
                 （`POST {base_url}/images/generations`；b64_json / url 两路都处理；
                 transport 可注入 → 0 网络单测；size 记法差异走 Config 的 size_separator）
                 + `ImageGenerationError`（响应无可取图片载体时明确报错）
                 ❌ 真实出图**未执行**（需人工四项前置：Config 填模型名 + 注入 BAILIAN_API_KEY
                    + 明确批准 + 确认预算；见 `v2/logs/stage13_image/run_real_image.py`）
    Video / Audio
             ⚠️ 仅预留接口（类存在，调用 NotImplementedError，**工厂仍拒绝**）
             ❌ 尚未实现任何真实生成
```

### 5.4 Provider 设计原则（Stage 9 确立，长期遵守）

```text
1. Agent 不依赖具体厂商（只认抽象 Provider / 统一结果形状）
2. Router 负责 Provider / Model 路由（决策但不亲自发请求）
3. Provider 负责模型调用（封装厂商协议、翻译异常、瞬时错误重试、上报 usage/cost）
4. Config 负责 provider / model / endpoint / pricing 等配置
5. Core Validator 负责业务数据合法性（Provider 不做业务判定）
6. Workflow 负责业务级 retry（输出不合格 → 整步重试，每次尝试留版本文件）
7. Provider 只负责瞬时 transport error（网络/超时/限流/5xx 退避；4xx/认证不重试）
8. Token / Cost 继续复用 Stage 2（TokenUsage + estimate_cost + TokenLogger 单一口径，禁止第二套账）
9. Mock 优先（缺省 FILMAGENT_PROVIDER=mock → 一切测试 0 API）
10. 真实 API 默认按阶段控制（只走独立 run_real_*.py 脚本，需人工批准才计费）
11. ★ 模型切换必须单独取得用户明确批准：任何真实调用只允许使用本次已批准的精确
    `provider + model`。若准备从模型 A 切到模型 B（包括自动 fallback、重试改模、同厂商
    不同版本），必须先报告 A/B 名称、切换原因、目标模型免费额度是否已确认、预计调用次数
    与费用；用户明确同意前保持 0 次目标模型调用。额度未知按“可能计费”处理，禁止静默切换。
```

### 5.5 Visual Asset Layer（Stage 10 落地）

**核心决策：语义与视觉分离**。`Character` / `Location`（`core/models.py`）语义模型 0 改、
不加视觉字段；视觉身份独立成层，连接键是**名字**。

```text
VisualAsset（稳定身份：asset_id = CHAR_001 / LOC_001 / PROP_001 / STYLE_001 / REF_001）
  └── VisualAssetVersion（v1、v2… 单调递增，绝不覆盖，号永不复用）
        ├── profile         按 kind 的结构化视觉档案（per-kind Profile 类 + 必填门槛）
        ├── reference_images ReferenceImage(image_id, path, kind, note, checksum=sha256)
        └── visual_prompt   该版本视觉身份的一句话浓缩
```

| 模块 | 职责 |
|---|---|
| `core/visual.py` | 五类资产 / 七态生命周期 / 参考图 / 来源词表 + 数据结构 + 确定性规范化（不调 LLM、不碰磁盘） |
| `core/visual_registry.py` | 视觉身份唯一事实源：kind 前缀计数分配 ID、(kind,name) 永久绑定、版本管理、参考图 sha256、`registry.json` 持久化 |
| `core/visual_compose.py` | `VisualComposer`：**调用模型前**才把 Shot 的名字引用解析并展开为 `ComposedVisualInput`（紧凑 `key: value` 渲染，省 token） |
| `agents/visual_asset.py` | `VisualAssetAgent` + `VisualAssetStep`：plan 模式走 TextProvider、extract 模式走 VisionProvider |
| `llm/providers/openai_compatible_vision.py` | OpenAI 兼容图文 parts 的 Vision Provider 实现（本地文件 base64） |
| `llm/request_counter.py` | 真实 API 请求计数器 + 预算硬上限（程序化计数，不靠人工数数） |

**Token / Context 纪律（Stage 10 确立，Stage 11+ 必须延续）**：

```text
❌ Shot 1/2/3 各自复制完整角色外貌 + 地点环境   → context 爆炸、身份漂移
✅ 视觉身份活在 registry（一份 approved profile）
   Shot 只存「名字引用」+「本镜头视觉增量」
   调用模型前由 VisualComposer 一次性展开
```

**硬证据**：注册视觉资产**前后**，同一 `Storyboard.to_dict()` 序列化结果**逐字节不变**，
Artifact 内不出现任何角色外貌 / 地点环境字段渗漏（Stage 10 套件 T08 断言）。

**生命周期 = Review 三态的确定性派生**（不建第三套状态机，ADR D8）：
`submit_for_review(draft→reviewing)` / `approve(reviewing→approved，旧 approved 自动 superseded)`
/ `reject(reviewing→rejected)` / `archive(资产级退役)`。

**缺口策略（宁停不错）**：参与画面的角色 / 地点 / 道具缺 approved 视觉档案时，
`VisualComposer` 默认 `allow_partial=False` → **明确报错并列出缺口清单**，绝不静默用残缺视觉生成。

### 5.6 StoryboardAgent（Stage 11 落地）

**缺口补齐**：Stage 8 定义了 `Shot`/`Beat`/`Storyboard` 的 Schema 与校验器，Stage 10 备好了
"Shot 引用什么"，但**没有任何 Agent 能从剧本产出它们**。Stage 11 补上这一环。

```text
Scene 剧本（Stage 7 产出，含 info / lines / initial_positions）
   ↓  StoryboardAgent（inputs["scene"]；缺则 StepError）
Storyboard（beats[].shots[]）—— Shot 落 Stage 8 受控词表内，
                               只存名字级引用 + 本镜头视觉增量
   ↓  VisualComposer.compose(shot)（Stage 10，0 改）
ComposedVisualInput →（Stage 13）出图
```

| 模块 | 职责 |
|---|---|
| `agents/storyboard.py` | `StoryboardAgent`（继承 `LLMAgent`）：Scene → Storyboard；`StoryboardStep`（继承 `LLMStep`）：接线 + `validate()`；`normalize_storyboard()`：§7.2 八条确定性规范化 |
| `prompts/v0.2/storyboard_generation.txt` | 角色设定 + JSON 契约（受控词表不手抄，走 user payload） |

**接线约定**：`needs` 与 `SceneGenerationAgent` **同集合**（不新增 ContextAssembler provider）；
Scene 由调度层放进 `WorkflowContext.inputs["scene"]`（`Scene` 对象或 `to_v01()` dict）；
`artifact_name = storyboard_{scene_id}`、`step_id = storyboard_generation_{scene_id}`；
`needs_review = True`（走既有 Review 三态）；`Step.validate()` 用 `StoryboardValidator.validate_dict`。

**§7.2 八条确定性规范化**（LLM 越界在代码层兜底，不改词表）：

```text
1 受控词表兜底（shot_size/camera_angle/camera_movement/composition/kind → MS/eye_level/static/single/general + warning）
2 名字回锚（characters ⊆ Scene.who ∪ known；location ∈ Scene.where ∪ registry；含 positioning/actions/assets 引用）
3 id 重排（beat_id 1..n；shot_id 全局 1..N）
4 时长兜底（dialogue 3.0 / move 2.5 / general 2.0）
5 状态强制（status = "planned"）
6 speaker/dialogue 回锚（按序对齐 Scene.lines）
7 shot_type 安全化（非空但不在注册镜头名/SHOT_SIZES → 清空 + warning）
8 dialogue/move 降级（补不齐 → kind="general" + warning）
```

**词表单一事实来源**：受控词表由 `core.shot` 常量渲染进 user payload（`render_vocab_block()`）；
另加运行时自检 `check_prompt_vocab_consistency()`（prompt 内词表文字与常量不一致 → `StepError`）。

**warnings 归属**：统一放 `Storyboard.metadata["normalize_warnings"]`（不放 `Shot.metadata`，
以免扰动 Token 硬证据的逐字节断言）。

### 5.7 编排层（Stage 12 落地）

**缺口补齐**：Stage 0–11 造齐了零件（四个业务 Agent 各自可跑可测），但**从没有任何代码把零件
连起来跑过一集**——没人决定先跑谁 / 后跑谁 / 跑几次 / 场景怎么分发 / 整集是否齐备。
Stage 12 补上这一层（**第一次让整机通电**）。

```text
ProjectOrchestrator.run(run_spec)
  ① 集级 Casting（casting_ep{N}，一次 run）→ approved 后 set_characters / set_world（★严禁 approve_fact）
  ② 每场景【两段式】：scene_gen_{sid} 一次 run → preflight 前置校验门 → storyboard_{sid} 一次 run
  ③ 视觉资产段：确定性收集被引用实体 → 每实体一次 run → register/new_version/submit/approve 写回 registry
  ④ 齐备判定（与磁盘交叉核对）→ run_report 原子落盘 + state.json 原子落盘
```

| 模块 | 职责 |
|---|---|
| `orchestration/preflight.py` | `preflight_check(scene, known_characters, registry) -> List[str]`：调度**前置校验门**（5 条规则，判据与 `core/validator.py` 同源、只前移不另立）；**返回缺口清单、不抛异常**；★必须先 `Scene.from_v01` 还原 `Line.type`，否则 move 行漏检 |
| `orchestration/state.py` | `OrchestrationState`：编排进度唯一事实源（`state.json`）+ `to_dict`/`from_dict` 往返 + **自实现原子写**（tempfile + `os.replace`，序列化对齐 `core.io.write_json`）+ `should_skip`（含磁盘 artifact 存在性校验） |
| `orchestration/pipeline.py` | `ProjectOrchestrator`：两段式多场景编排 + 集级 Casting + 视觉资产齐备 + 三种 review 策略 + resume + run_report |
| `orchestration/__init__.py` | 导出 `ProjectOrchestrator` / `OrchestrationError` / `OrchestrationState` / `preflight_check` / `format_gaps` / `atomic_write_json` |

**三条硬约束（总控裁决）**：

```text
1. 两段式（裁决 3）：scene_gen_{sid} 与 storyboard_{sid} 各一次 run。
   为什么不能同 run 装两 step：WorkflowEngine.run() 的 inputs 是整次 run 共享的一个 dict，
   而 StoryboardStep 只读 inputs["scene"]、**完全忽略上游 Step 产出**（实测）→ 必然串场或 StepError。
   两段式**零改引擎**、语义更干净（每 run 恰一个 step）、resume 天然可用。
   ★ storyboard 段必须显式 inputs["scene"] = Scene（不得依赖上游产出）。
2. 编排层写 Memory（裁决 1）：Casting 通过后 set_characters（沿用同一 run 的 Casting Artifact）
   + set_world；★严禁 approve_fact（事实批准是"人"的动作，编排层不得代批）。
   不写则 Context characters 为空 → LLM 编造角色名 → Scene 层不回锚 lines → 重试耗尽 FAILED。
3. 视觉资产只支持 approved / rejected（裁决 2）：遇 approved_with_edit → 明确抛 OrchestrationError
   （apply_review_decision 直接报错，且编辑后 profile 缺确定性来源；fail-loud 优于静默猜）。
```

**resume 语义（裁决 6）**：对已 approved 的单元**直接跳过、不调用引擎**（`state.json` 是唯一
事实源，不存第二份 `WorkflowRun`）；跳过前校验磁盘 artifact 仍存在（缺失 → 视为 pending 重跑）。

**审核策略（§7.4，★诚实性）**：`auto_approve` / `scripted:<file>` / `human_stub`；
`run_report["review_mode"]` **必含** `"programmatic, NOT human review"`；
**绝不**把 auto_approve 的产物描述成"已人工审核通过"。

**零侵入**：编排层是最顶层，只向下依赖；`v2/workflow/` **0 diff + 0 新增**（Stage 12 套件 T17 断言）。

### 5.7.1 图像链路（Stage 13 落地：第一次真的把画面生成出来）

```text
Stage 12 编排层（orchestration）—— 段顺序（Stage 13 在 ③ 与 ④ 之间插入 image 段）
  ① casting   ② 每场景(scene_gen → preflight → storyboard)   ③ visual_assets
  ④ ★ image 段（每 Shot 一张图）                              ⑤ 齐备判定 + 落盘
        ↓
   VisualComposer.compose(shot) → ComposedVisualInput        ← 出图 prompt 的唯一来源（★不得绕过）
        ↓  render_prompt() / reference_images(image_id+sha256) / profile_versions / negative_cues
   ImageGenerationProvider.generate_image(prompt, size, reference)
        ↓  引用串（data:…base64 / http(s) URL / 本地路径）→ 解析为二进制
   落盘 v2/projects/<pid>/generated_images/<GEN_ID>.<ext>（sha256 锚点，绝不覆盖/绝不复用 ID）
        ↓
   manifest.json（★Stage 14 VLM QA 的输入契约：sha256 / profile_versions / reference_images）
   image_generation_calls.jsonl（★图像账，与 token 账分开，字段风格对齐）
        ↓
   Review 三态 → approved 才把 ShotAsset(kind="image") 挂回 Shot
        ↓
   Storyboard **新版本**（storyboard_{sid}_vN.json；原文件字节不变）
```

| 模块 | 职责 |
|---|---|
| `llm/providers/mock_image.py` | `MockImageGenerationProvider`：确定性最小合法 PNG（纯 stdlib `zlib`+`struct`，同入参必同字节、不同 prompt 必不同字节）；返回 `data:image/png;base64,…` 引用；**0 网络 0 成本** |
| `llm/providers/openai_compatible_image.py` | `OpenAICompatibleImageProvider`：`POST {base_url}/images/generations`；错误翻译/瞬时重试/`transport` 注入**沿用既有模式**；`_invoke()` 是**唯一真实计数点**（retry 也计预算）；b64_json / url 两路都处理；`ImageGenerationError` 明确报错 |
| `llm/providers/registry.py` | ★**只开 `kind=="image"` 分支**（mock + 真实 Config 档）；video/audio **保持拒绝**；缺省 vendor 仍拒绝（与 vision 既有范式一致） |
| `llm/request_counter.py` | 新增 `MAX_REAL_IMAGE_REQUESTS=3` + `image` 桶 + `set_budget()`（纯追加） |
| `config.py` | `PROVIDERS["bailian_image"]`（`default_model=""` 配置护栏、`size_separator="x"`）+ `IMAGE_PRICING` / `estimate_image_cost`（**与 token 价物理分开**，未登记 → `null`） |
| `orchestration/image_stage.py` | ★图像段：compose → 预算检查 → 生成 → 落盘 → manifest → 记账 → Review → 挂回 → Storyboard 新版本；`resolve_image_bytes()` 三路引用解析；`shot_ref_of()` 与 `ComposedVisualInput.shot_ref` 同源 |
| `orchestration/pipeline.py` | image 段接线（`_stage_images`）+ `run_spec["style_spec"]` 并入 visual_assets 段（★STYLE 不是 Shot 能引用的资产，必须显式提供） |
| `orchestration/state.py` | `record_image_stage()` / `image_entry()`（★纯追加，仍只是对 Artifact/Review 结果的**镜像**） |
| `v2/.gitignore` | §8.4 二进制防护规则（仓库根 `.gitignore` **仍 0 改**） |

**★ Stage 13-P 补丁（百炼原生档，2026-09-18）**：图像 Provider 现有**两档并存**，由 Config 的
`protocol` 字段分派（`registry._create_image_provider`）：

| protocol | 实现 | 适用厂商 | 协议要点 |
|---|---|---|---|
| 缺省 / `"openai"` | `OpenAICompatibleImageProvider` | 火山方舟 / OpenAI / 硅基流动 | `POST {base}/images/generations`；`size_separator="x"`；同步 |
| `"dashscope_native"` | `BailianImageProvider`（新增） | 百炼 DashScope | 千问同步 / 万相异步（`X-DashScope-Async` + `task_id` 轮询）；**`size_separator="*"`**（与 canonical 一致）；返回 URL |

**★ 预算语义（关键）**：`_invoke()` 包住**整次业务尝试**（含内部 1 次创建 + N 次轮询）
⇒ **一次业务调用 = 1 次 image 预算，轮询不额外消耗** ⇒ "一张图 = 1 次计费调用"。
**换档只改 Config，代码 0 行改动**（T20n/T20p/T20q 断言守护）。

**★ Stage 13-P URL 修复（2026-09-28）**：`bailian_native_image.base_url` 统一为
`https://<host>/api/v1`，Provider 默认 path 只使用 `/services/...` / `/tasks/...`，
避免构造出 `/api/v1/api/v1/...`。T20r–T20t 从真实 Config 档读 base URL，直接断言
公共域同步、公共域异步提交+轮询、业务空间域的最终 HTTP URL，且每条只含一段
`/api/v1`。定向 20/20；全量回归 **146/146、rc=0、真实 API 0 次**。
详见 `v2/logs/stage13_image/stage13p_native_provider.md`。

**三重护栏（§7.3，本阶段最高风险项）**：

```text
1. ★默认不出图：run_spec["generate_images"] 缺省 False → 零 Provider 调用 / 零图像文件 / 零记账行。
2. ★预算硬上限：cap = min(run_spec["max_images"], MAX_REAL_IMAGE_REQUESTS=3)；
   达到上限**立即拒绝**（APIBudgetExceededError），绝不发出第 N+1 次；
   同一 cap 经 set_budget() 下推到真实 HTTP 层（纵深防御）。retry 一样消耗预算。
3. ★绝不伪造出图：无 key / 未配置模型 / 预算耗尽 → 如实记 failed / rejected，
   **绝不用占位图、纯色块、mock 结果冒充真实生成**（铁律 11）。
```

**记账边界（§7.5）**：token 账（既有 `TokenLogger`）记文本/视觉 token 与成本；
请求计数（`APIRequestCounter`）记真实 HTTP attempt（image 独立桶）；
图像账 `image_generation_calls.jsonl` 每张图一行，字段风格与 `TokenLogger` 对齐、
**不含 token 概念**——不造第二套账，也不把图像成本塞进 token 账。

**resume（成本保护）**：manifest 中已 `generated` 的 `shot_ref` 在重跑时**直接跳过**
（零 Provider 调用、零新产物），只把已有引用补挂回 Shot → 不会重复花钱。

**STYLE 前置（Stage 13 新增要求）**：`VisualComposer` 出图时必须解析到唯一 approved STYLE
档案；STYLE 不在 `core.shot.ASSET_KINDS` 里、无法从 Storyboard 自动收集，
因此由 `run_spec["style_spec"]` 显式提供（不提供 → `compose()` 明确报错列缺口，**不硬出图**）。

### 5.7.2 图像 QA 只读质量门（Stage 14 落地）

```text
Stage 13 产出（generated image + manifest.json：sha256 / profile_versions / reference_images）
   ↓
ImageQAStage preflight（全部在 Provider 调用前）
   ├─ generated 路径 containment + SHA256
   ├─ manifest 指定精确 profile 版本（approved / superseded，禁止 active fallback）
   └─ D14-REF-01：reference image_id 全 registry 反查，恰好一处命中 + 三方 SHA256 一致
   ↓
ImageQAStep / ImageQAAgent → MultimodalProvider.chat(images=(generated, refs...))
   ↓
严格 Schema 校验 + 本地确定性 qa_decision（pass / fail / needs_review）
   ↓
ArtifactStore（image_qa_<image_id> 版本化）+ Review 三态（edit 后二次校验）
   ↓
quality/qa_manifest.json（只读审计 / resume 索引，原子写）
   ↓
Stage 15 准入：Review approved-like ∧ qa_decision == "pass" ∧ resume 锚点有效
```

| 模块 | 职责 |
|---|---|
| `agents/image_qa.py` | `ImageQAAgent`（组装 messages + `images` tuple、调用 provider、解析 JSON）/ `ImageQAStep`（`needs_review=True`，`artifact_name=image_qa_<image_id.lower()>`）；纯函数 `expected_checks` / `validate_provider_payload` / `validate_artifact` / `qa_decision` |
| `prompts/v0.2/image_qa.txt` | 只允许返回 `{checks, summary}`；verdict 枚举；`negative_cues` 逆语义；禁止内嵌路径 / 凭据 / base64 |
| `orchestration/image_qa_stage.py` | `ImageQAStage`：manifest 解析、选择、`safe_project_file`、`build_reference_index`（D14-REF-01）、`prepare_image_qa_input`、逐图 Workflow、最终 Artifact 二次校验、index、resume、run_report |
| `orchestration/pipeline.py` | `_stage_image_qa()` 接线（段顺序 `… → image → image_qa → completeness`），`run_image_qa` 缺省 `False`、Provider 惰性构造 |

**核心不变量**：

```text
1. 只读质量门：不修改 manifest / 生成图 / Storyboard / ShotAsset / VisualAsset registry；
   quality/qa_manifest.json 只是 Artifact/Review 的只读镜像，不是第二状态机。
2. 精确版本不漂移；字段集合严格来自 PROFILE_CLASSES[kind].FIELD_ORDER。
3. images[0] 永远是 generated；references 严格按 manifest 顺序。
4. qa_decision 只由本地规则计算；模型自报值被忽略；
   downstream_eligible 是可重算派生值，不信 index 自报。
5. 预算 cap = min(max_qa_images, MAX_REAL_VISION_REQUESTS=3) 下推 vision 桶；
   真实 QA Provider 必须 budget_kind == "vision"；第 N+1 次发送前拒绝。
```

**★ D14-REF-01（长期契约）**：独立 `reference` 资产图片不在 `profile_versions` 中，
必须按 registry 全局 `image_id` **恰好一处命中**反查并绑定 `asset_id+version+path+checksum`；
命中版本只允许 `approved` / `superseded`；零命中、多命中、`active_version()` fallback、
按路径/checksum 猜归属、静默跳过**全部禁止**。不修改 core / Stage 13 manifest / `image_stage.py`。

**★ 真实连通性边界**：Stage 14 的真实多图 VLM 调用**从未执行**，连通性**未验证**；
`run_real_image_qa.py` 是 preflight/guard scaffold（默认拒绝 rc=2、0 调用；全前置满足也只打印
`PRECONDITIONS SATISFIED` 返回 rc=3，不构造 Provider）。本地 Mock 全绿不等于真实厂商多图协议可用。

### 5.8 当前百炼策略

- **阿里云百炼属于当前主要云端 Provider 候选，但 FilmAgent 核心架构不得绑定百炼。**
  百炼只是 `PROVIDERS` 里的一个 Config 档（`bailian_text` / `bailian_vision` /
  `bailian_embedding` / `bailian_image`），换个厂商 / 本地模型只是换配置，不改业务 Agent。
- 当前主要免费能力方向：大语言模型 / 视觉模型 / 语言模型 / 向量模型 / 全模态模型
  （只作为 Config 示例方向出现）。
- **不要把具体模型名称硬编码为项目架构事实**：模型名与免费策略可能变化，
  具体模型放在 Provider / Config 层（`PROVIDERS[*]["default_model"]`），不进业务代码。
- ★ **模型使用授权与 API Key 授权分离**（用户裁决，2026-10-01）：同一个百炼 API Key
  可以在多个模型间复用，不代表项目获准任意切换。每次真实运行必须锁定用户本次批准的
  精确模型名；切换前必须重新说明免费额度/成本并取得明确同意。不得把“已开通”“同一 Key”
  或“Provider 自动 fallback”视为模型使用批准。

### 5.9 当前本地模型策略

```text
本地模型属于未来可接入 Provider（未来 LocalTextProvider 大概率是 OpenAI 兼容文本的一档配置）。
当前：未下载 / 未部署 / 未接入真实本地模型
架构：已经预留 Local Text Provider 路径（PROVIDERS["local_text"] Config 档）
```

本阶段（含 Stage 9/10）不得下载 / 部署：Ollama、vLLM、任何本地模型权重。

## 6. 已知问题 / 遗留

- `openai` 库需按 CLAUDE.md 安装（唯一非标准库依赖；仅真实 LLM 调用时用）。
- 人工审核目前是程序化 decision 模拟，交互式审核界面未做。
- **结构化输出的"真实后端支持"未验证**：`structured=` 透传的 contract 已就位
  （Mock 感知、转发路径可开启），但百炼 / DeepSeek 是否真正接受 `response_format`
  未经真实调用证实；确认需最小化、人工批准的真实冒烟（见 `stage9_c_report.md` §19）。
- `bailian_text` / `local_text` / `bailian_vision` / `bailian_embedding` 的
  `default_model` 为空串属**配置护栏**（非缺陷）：真实调用前由用户在 Config 填模型名。
- Router 当前是"架构入口 + 默认档"（一切任务落默认 Provider），**不做按 task_type 的
  智能选模型**——等真实 token/quality 数据积累再演进。
- **Video / Audio Agent 未实现**；`image` 模态已由 Stage 13 开通（mock + 真实 OpenAI 兼容档
  + 百炼 DashScope 原生档），但**真实出图未执行**；video / audio 仍是 Schema + Provider 预留接口
  （调用 `NotImplementedError`，**工厂仍拒绝**），**尚无真实生成**。
  ★ **VLM Image QA Agent 已由 Stage 14 实现**（`agents/image_qa.py` + `orchestration/image_qa_stage.py`），
  本地 Mock 全绿；**真实多图连通性未验证**（见 §5.7.2 与 §6 Stage 14 遗留）。
  （StoryboardAgent 已由 Stage 11 补上 —— 见 §5.6 与 `v2/logs/stage11_storyboard/stage11_report.md`；
   图像链路见 §5.7.1 与 `v2/logs/stage13_image/stage13_report.md`；
   图像 QA 见 §5.7.2 与 `v2/logs/stage14_image_qa/stage14_report.md`。）
- **Stage 10 遗留（不影响封存）**：
  - `PROVIDERS["bailian_vision"]["pricing"] = None` → 真实 Vision 调用 `est_cost=null`
    属配置护栏（未登记 qwen3.7-plus 官方价即不计成本，不以 DeepSeek 价冒充）。
    需真实成本时在 `config.PRICING` 登记即可，不改业务代码。
  - `run_real_visual_extraction.py` 定位是"真实 Vision 连通性 + Profile extraction
    观察"，**不登记参考图**（参考图挂版本链路已由 0 API 本地测试 T06b 完整证明）。
    若希望真实冒烟也落 checksum 锚点，可加一行 `reg.add_reference_image(...)`（可选增强）。
  - `prompt_length` 对多模态消息语义失真（content-parts 列表按 `len()` 计），
    不影响 `prompt_hash` 与计费；`v2/llm/client.py` 属禁改清单，仅登记不修改。
- Stage 10-B 真实 Vision 冒烟：3 次调用 / 14,433 tokens / `est_cost=null`（`qwen3.7-plus`）。
- **Stage 11 遗留（不影响封存）**：
  - **待总控裁决**：`normalize_storyboard()` 含两条**超出 §7.2 八条**的防御性回锚 ——
    `actions`（state 越界 → `standing`；角色未登记 / 动作未注册 → 剔除整条）与
    `assets`（kind 越界或 ref 空 → 剔除；character/location/action 类 ref 未登记 → 剔除）。
    理由：对应 `INVALID_STATE` / `UNKNOWN_CHARACTER` / `INVALID_ACTION` / `INVALID_ASSET_KIND`
    等硬拒，不兜底会"重试耗尽 → 永久 FAILED"。性质与 A 会话 R5 相同（必要补全）。
  - **D-2（已判非缺陷，fail-loud）**：上游 `Scene.info.who` 含未登记角色时，
    `StoryboardValidator` 报 `UNKNOWN_CHARACTER` → 3 次重试后 FAILED（不静默丢角色）。
    建议 Stage 12 在调度层先校验 Scene 再喂 Stage 11。已固化为测试 T20e。
    ★ **已在 Stage 12 解决**：编排层 `preflight_check` 前置校验门把坏 Scene 拦在调度层
    （不再进入 Stage 11 重试风暴），见 §5.7 与 Stage 12 套件 T03/T04。
  - **多场景调度缺口**：`inputs["scene"]`（裁决 S-A）只支持单 scene/run；同一 workflow 内
    放 N 个 storyboard step 会都读同一个 scene（`step_id` 命名已独立）。跨场景取 scene 的
    调度机制留 Stage 12 设计。
    ★ **已在 Stage 12 解决**：编排层改用【两段式】——`scene_gen_{sid}` 与 `storyboard_{sid}`
    各一次 run，`inputs["scene"]` 显式放入，见 §5.7 与 Stage 12 套件 T06/T07。
  - `run_real_storyboard.py`（T31~T35，≤2 次真实文本调用）已就位但**未执行**（需人工批准 + 配置 key）。
  - 技术债 **T-6** 本轮又复现一次：跑 stage8/9/10 的嵌套回归会**连带重写 stage3** 的
    `stage3_report.json` / `stage3_console.txt`（指令 §3.2 坑 1 只列了 4 个文件）→
    复位清单应扩为 6 个。
- **Stage 12 遗留（不影响封存）**：
  - **真实冒烟未执行**：`run_real_lifecycle.py` **未创建、未运行**（需 `DEEPSEEK_API_KEY` +
    人工批准）→ 真实链路连通性**未验证**。属"阻塞：需人工配置"，**未伪造**任何真实调用结果。
    （指令 §8.1 曾将其列为新增文件，实际未产出 —— D 会话已报总控裁决。）
  - **`T18b` 未做**：跑前后 `git status --short` 对比、把技术债 T-6 从"靠人记得"变成
    "程序化发现" —— 属可选增强，非缺陷修复。
  - 编排层当前只支持"编排一集"（`episode` 入参）；跨集 / 全项目批量编排留后续。
  - 交互式人工审核 UI 未做（`auto_approve` 是程序化模拟，已在 `run_report.review_mode` 显式标注）。
  - 真实出图（Stage 13）★**已在 Stage 13 完成代码**：`image` 模态开通 + 编排层 image 段就位；
    **真实调用未执行**（人工四项前置未齐 → 如实报告阻塞）。见 §5.7.1。
- **Stage 13 遗留（不影响封存）**：
  - **真实出图未执行**：`run_real_image.py` 已就位但**未运行**（需 Config 填模型名 +
    `BAILIAN_API_KEY` + 明确批准 + 单价登记四项人工前置）→ 真实图像链路连通性**未验证**。
    脚本以退出码 2 明确报告"阻塞"，**未伪造**任何真实调用结果、**未用占位图冒充**。
  - ★ **T-9 已由 Stage 13-P 解决**：百炼图像生成使用 DashScope 原生协议；
    `bailian_native_image` 档与既有 OpenAI Images 兼容档并存。实现与测试见
    `logs/stage13_image/stage13p_native_provider.md`。2026-09-28 已修复 base/path 双 `/api/v1` 拼接，
    并用 T20r–T20t 覆盖公共域同步/异步及业务空间域的最终 URL。真实出图仍须完成模型、密钥、
    批准与价格四项前置；本地协议测试不能替代真实连通性验证。
  - `config.IMAGE_PRICING` 为**空表**（护栏）→ 真实出图 `estimated_cost` 记 `null`
    （**绝不用 token 价格冒充**）；需真实成本时人工登记单价即可，不改业务代码。
  - 单次 run 出图上限被收紧为 `min(run_spec["max_images"], MAX_REAL_IMAGE_REQUESTS=3)` →
    `max_images` **只能调小**（T-8，安全默认，非缺陷）。
  - `manifest.json` 落在 `v2/projects/<pid>/generated_images/`，因 `v2/.gitignore` 的
    `projects/` 整目录规则**不入库**（T-11）；真实冒烟脚本已把副本（无二进制）落到
    `v2/logs/stage13_image/real_run/manifest_copy.json` 作为入库证据。
  - 图像审核遇 `approved_with_edit` → 明确抛 `OrchestrationError`（T-12；图像是二进制，
    无"编辑后内容"的确定性来源）；图像编辑能力未立项。
  - ★ **实测修正**：回归重写的产物**不止 6 个文件**（各套件还会重写自己的
    `test_report.json`，其中 stage10/11/12 含随机 id）→ stage13 套件已改为
    **按目录扫 dirty 后精确复位**（T-6 已缓解）。
  - ★ **技术债 T-13**：历史回归嵌套成本呈指数级，stage13 一次全量复跑实测 **≈59 分钟**。
  - Stage 12 遗留的 `run_real_lifecycle.py` **已并入** `v2/logs/stage13_image/run_real_image.py`
    （§7.7 裁决落地）。
- **Stage 14 遗留（不影响封存）**：
  - ★ **真实多图 VLM 连通性未验证**：`run_real_image_qa.py` 已就位但**只跑过默认拒绝路径**
    （`rc=2`、0 调用）；即使全部前置满足也只打印 `PRECONDITIONS SATISFIED` 并返回 `rc=3`，
    **不构造 Provider、不发真实请求**。它当前是**授权与参数的 preflight / guard scaffold**，
    不得描述为"已连通"或"可直接运行的真实冒烟入口"。真实多模态 Provider 连通性**未验证**。
  - ★ **开放问题（未修代码）**：`run_real_image_qa.py` 对 `bailian` 检查 `DASHSCOPE_API_KEY`
    （`KEY_ENV_BY_VENDOR`），而 `config.PROVIDERS["bailian_vision"]["api_key_env"]` 是
    `BAILIAN_API_KEY` —— **两处环境变量名不一致**。未来真实冒烟实现前必须先统一，否则
    "前置满足"的判定与实际取 key 来源不一致。
  - `test_report.json` 的 `regressions` 数组为**空**，**未承载** Stage 13 历史回归详情；
    历史回归结论以阶段报告 `v2/logs/stage14_image_qa/stage14_report.md` §9 为准。
    **不伪造、不回填**机器测试结果。
  - `test_report.json` 两条 `detail`（T04-1、T06-1）含工作区绝对路径（指向 gitignored
    `tmp_projects/`，**非**用户个人临时目录；无密钥 / 图片 / base64）。该文件属 14-C 机器报告，
    D 会话不得修改，仅登记；是否脱敏由总控决定。
  - `v2/logs/stage14_image_qa/.test_report.authoritative.json` 为 14-C 期间生成的权威报告备份
    副本（与 `test_report.json` 摘要一致、无敏感信息），**不在允许交付清单内**，建议不纳入 commit。
- 真实异步"暂停等人工"审核流程、UI / 数据库 / 生产部署均未做。

## 7. 下一阶段

```text
最近稳定阶段：Stage 14（VLM Image QA）**已完成并独立封存**（`v0.2-stage14`）
      —— 阶段报告 `v2/logs/stage14_image_qa/stage14_report.md`（含契约笔记
         `stage14_contract_notes.md` 与 AC 逐条对照）
      正式套件 198/198、rc=0、真实 API/网络/模型切换 0/0/0；
      ★ 真实多图 VLM 连通性**未验证**（run_real_image_qa.py 仅为 preflight/guard scaffold）

当前工作：Stage 15（Video Generation）B 实现快照待 C 独立验证，规格见
          `v2/docs/stages/Stage_15.md`；云端交接见 `v2/docs/STAGE15_CLOUD_HANDOFF.md`。
          Stage 15 尚未封存，C/D 不得把 B 自测当成最终验收。
```

**Stage 14 完成内容**：把"生成图 vs 参考图"的逐字段裁决做成只读质量门 —— 按 approved 视觉档案的
**受控字段**逐项比对（hair ✅ / clothing ❌），`qa_decision` 由本地确定性规则计算，结论作为
Review 输入并写入只读索引，为 Stage 15 提供准入门
（`Review approved-like` ∧ `qa_decision == "pass"` ∧ resume 锚点有效）。

★ **Stage 14-A / B / C / D 均已完成（2026-10-01 ~ 2026-10-02）**：
契约笔记位于 `v2/logs/stage14_image_qa/stage14_contract_notes.md`（发现独立 `reference` 资产图片
会进入 Stage 13 `reference_images` 但其版本不进入 `profile_versions`；总控裁决 **D14-REF-01**
采用方案 B：resolver 按 registry 全部版本的全局 `image_id` 反查并要求**恰好一次命中**，
继续执行版本状态、项目路径与 manifest/registry/磁盘三方 SHA 校验；零命中、多命中或任何猜测式
fallback 均在 Provider 调用前失败；Stage 13 manifest/core/image_stage 保持 0 改）。
14-B 完成实现（`ImageQAAgent`/`ImageQAStep`、Prompt、`ImageQAStage`、pipeline 默认关闭接线）；
14-C 完成正式测试（198/198，rc=0）并修复 3 个已证明缺陷（仅 `image_qa_stage.py`）；
14-D 完成阶段报告与长期文档同步；本次封存会话另行静态复核并独立提交、创建
`v0.2-stage14` tag。14-D 原始报告记录的是**封存前快照**，不可把它误读成当前 Git 状态。

Stage 13 已为 QA 备好的输入契约（不返工）：

```text
manifest.json 每条记录：
  sha256                       落盘文件内容寻址（QA 可校验）
  profile_versions             {asset_id: version} —— 逐字段裁决要知道比的是哪一版档案
  reference_images             [{image_id, sha256}] —— QA 的比对锚点
  prompt_hash / prompt_length   不记 prompt 正文（沿用既有纪律）
```

打通链路：

```text
Stage 12 编排（Casting → Scene → Storyboard → 视觉资产齐备）          ✅ 已封存
   ↓
Stage 13 出图（ComposedVisualInput → 真实图像；预算护栏 + 成本记账）   ✅ 已封存（真实出图未执行）
   ↓
Stage 14 VLM QA（生成图 vs 参考图 → 逐字段裁决 + Stage 15 准入门）    ✅ 已独立封存（真实连通性未验证）
   ↓
Stage 15 视频 → Stage 16 配音 → Stage 17 合成 → V0.2.0-alpha          ⏳ Stage 15 待独立验证
```

路线图方向（方向性参考）：

```text
第一集真实生成 → V0.2.0-alpha
```

**执行方式**：**新开一个 WorkBuddy 会话**（不要用 `claude` CLI / 终端 TUI），
工作目录 `D:\AI\FilmAgent-main`。先读 `CLAUDE.md` → 本文件 → `HANDOFF.md`
→ `ORCHESTRATION.md` → 最近阶段报告 `v2/logs/stage14_image_qa/stage14_report.md`
→ `v2/docs/stages/Stage_15.md` → 与下一阶段直接相关的代码。

## 8. V0.1 冻结规则

- `FilmAgent/` 下任何文件（尤其 `LLMCaller.py`、`main.py`、`Prompt/`、`Locations/`、
  `Script/`、`api_key.txt`）不得修改。
- 验收时 `git diff -- FilmAgent/` 必须为空；`FilmAgent/` 无 dirty 行。
- 自动化断言：`v0.1^{commit}` 固定 `344c0c6…`、`v0.2-stage7` / `v0.2-stage8` /
  `v0.2-stage9` / `v0.2-stage10` / `v0.2-stage11` / `v0.2-stage12` / `v0.2-stage13` tag 存在、
  `git diff -- FilmAgent/` 为空。
- 仓库根 `.gitignore` 属 V0.1 基线（commit `344c0c6`），同样 0 改；
  `v2/` 运行产物忽略规则只在 `v2/.gitignore` 内维护。

## 9. 协作与会话管理规范

详见 `CLAUDE.md`「协作与会话管理（长期规则）」一节。要点：

- 阶段是第一层边界：一个 WorkBuddy 会话通常只做一个 Stage；完成后 commit
  （必要时 tag），开新会话进入下一 Stage，不在一个超长会话连续堆多个 Stage。
- A/B/C/D 默认 = 不同的 WorkBuddy 会话（A 分析架构、B 实现、C 测试调试、
  D 文档交付），**不是**四个并存的子 Agent。
- 按任务复杂度选择会话数（小任务单会话 A+B+C+D；中型 A→B→C→D；大型可加专项分析），
  不机械开大量会话。
- 子 Agent 仅用于专项代码侦察 / 独立研究 / 局部测试 / 并行检查，不替代会话边界。
- 跨会话记忆靠文档（CLAUDE.md / 本文件 / HANDOFF.md / ORCHESTRATION.md /
  ROADMAP.md / Stage Report / Git）；聊天记录不作为项目长期记忆。
