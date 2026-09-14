# PROJECT_STATUS.md —— FilmAgent 项目状态

> FilmAgent 的权威项目状态文档，任何新 **WorkBuddy 会话**开始时都应先读本文件。
> 最后更新：V0.2 Stage 11 封存后（StoryboardAgent 文档/交付沉淀完成）。

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
当前阶段：StoryboardAgent 完成并封存 —— COMPLETE

V0.2 Stage 8：PASS + 已封存
V0.2 Stage 9：PASS + 已封存（Multi-Modal Provider Architecture）
V0.2 Stage 10：PASS + 已封存（Visual Asset Layer）
V0.2 Stage 11：PASS + 已封存（StoryboardAgent：Scene → Storyboard → Beat → Shot）
V0.2 Stage 12：NOT STARTED（Project 生命周期 + 第一条完整业务链；
              开工指令：`D:\AI\FilmAgent\v0.2开工指令12`）
```

## 3. 历史 Stage 0–11（里程碑）

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

版本 tag（`v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9` / `v0.2-stage10` /
`v0.2-stage11`）一经创建**永不移动、永不 force**。

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
└── agents    base / casting / scene generation / visual_asset（Stage 10）/ storyboard（Stage 11）
```

依赖方向：`core → (llm | memory | context) → workflow → review → agents`
（只允许向上，禁止反向/循环）。

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
    ❌ 本阶段无真实全模态 API 调用

Embedding Provider
    ✅ 接口 / Mock（MockEmbeddingProvider）
    ❌ 本阶段未接真实向量模型

Image / Video / Audio Provider
    ⚠️ 仅预留接口（类存在，调用 NotImplementedError，工厂拒绝）
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

### 5.7 当前百炼策略

- **阿里云百炼属于当前主要云端 Provider 候选，但 FilmAgent 核心架构不得绑定百炼。**
  百炼只是 `PROVIDERS` 里的一个 Config 档（`bailian_text` / `bailian_vision` /
  `bailian_embedding`），换个厂商 / 本地模型只是换配置，不改业务 Agent。
- 当前主要免费能力方向：大语言模型 / 视觉模型 / 语言模型 / 向量模型 / 全模态模型
  （只作为 Config 示例方向出现）。
- **不要把具体模型名称硬编码为项目架构事实**：模型名与免费策略可能变化，
  具体模型放在 Provider / Config 层（`PROVIDERS[*]["default_model"]`），不进业务代码。

### 5.8 当前本地模型策略

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
- **Image / Video / VLM Agent 未实现**；image / video / audio 仅 Schema +
  Provider 预留接口可表达，**尚无真实生成**。
  （StoryboardAgent 已由 Stage 11 补上 —— 见 §5.6 与 `v2/logs/stage11_storyboard/stage11_report.md`。）
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
  - **多场景调度缺口**：`inputs["scene"]`（裁决 S-A）只支持单 scene/run；同一 workflow 内
    放 N 个 storyboard step 会都读同一个 scene（`step_id` 命名已独立）。跨场景取 scene 的
    调度机制留 Stage 12 设计。
  - `run_real_storyboard.py`（T31~T35，≤2 次真实文本调用）已就位但**未执行**（需人工批准 + 配置 key）。
  - 技术债 **T-6** 本轮又复现一次：跑 stage8/9/10 的嵌套回归会**连带重写 stage3** 的
    `stage3_report.json` / `stage3_console.txt`（指令 §3.2 坑 1 只列了 4 个文件）→
    复位清单应扩为 6 个。
- 真实异步"暂停等人工"审核流程、UI / 数据库 / 生产部署均未做。

## 7. 下一阶段

```text
Stage 12：NOT STARTED（开工指令：`D:\AI\FilmAgent\v0.2开工指令12`，已生成）
         = Project 生命周期 + 第一条完整业务链（Casting → Scene → Storyboard → 组装 → 审核）
```

**Stage 12 目标**：把已封存的四个业务 Agent（Casting / SceneGeneration / Storyboard /
VisualAsset）串成一条端到端的项目生命周期——多集项目从 0 到"分镜齐备 + 视觉资产齐备"，
为 Stage 13 真实出图做调度准备。需一并解决 Stage 11 暴露的**多场景调度缺口**（见 §6）。

打通链路：

```text
Stage 7 剧本 → Stage 11 分镜 → Stage 10 视觉组装 → [Stage 13 出图]
        ↑ 已在 Stage 11 打通；Stage 12 把整条链编排成 Project 生命周期
```

路线图方向（方向性参考）：

```text
Project 生命周期（Stage 12）→ 第一集真实生成 → V0.2.0-alpha
```

**执行方式**：**新开一个 WorkBuddy 会话**（不要用 `claude` CLI / 终端 TUI），
工作目录 `D:\AI\FilmAgent-main`。先读 `CLAUDE.md` → 本文件 → `HANDOFF.md`
→ `ORCHESTRATION.md` → Stage 12 开工指令（待总控设计）
→ 与 Stage 12 直接相关的代码。

## 8. V0.1 冻结规则

- `FilmAgent/` 下任何文件（尤其 `LLMCaller.py`、`main.py`、`Prompt/`、`Locations/`、
  `Script/`、`api_key.txt`）不得修改。
- 验收时 `git diff -- FilmAgent/` 必须为空；`FilmAgent/` 无 dirty 行。
- 自动化断言：`v0.1^{commit}` 固定 `344c0c6…`、`v0.2-stage7` / `v0.2-stage8` /
  `v0.2-stage9` / `v0.2-stage10` / `v0.2-stage11` tag 存在、`git diff -- FilmAgent/` 为空。
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
