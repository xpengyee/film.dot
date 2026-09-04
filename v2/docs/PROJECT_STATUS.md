# PROJECT_STATUS.md —— FilmAgent 项目状态

> FilmAgent 的权威项目状态文档，任何新 Claude Code 会话开始时都应先读本文件。
> 最后更新：V0.2 Stage 9 封存后（Provider Architecture 文档/交付沉淀完成）。

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
当前阶段：Provider Architecture 完成并封存 —— COMPLETE（本次文档沉淀完成）

V0.2 Stage 8：PASS + 已封存
V0.2 Stage 9：PASS + 已封存（Multi-Modal Provider Architecture）
V0.2 Stage 10：NOT STARTED（等待下一份正式开工指令）
```

## 3. 历史 Stage 0–9（里程碑）

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
| V0.2 Stage 9 Provider Architecture | `v0.2-stage9`（本次交付 commit；`git rev-parse v0.2-stage9` 查精确 hash） | `v0.2-stage9` |

版本 tag（`v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9`）一经创建**永不移动、永不 force**。

## 5. 当前架构

### 5.1 已落地的核心基础设施

```text
V0.2
│
├── core      models / validator / assets / shot / adapter / token_log
├── llm       client / router / token & cost / providers（Stage 9）
├── workflow  step / engine / artifact / errors
├── memory
├── context
├── review
└── agents    base / casting / scene generation
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

### 5.5 当前百炼策略

- **阿里云百炼属于当前主要云端 Provider 候选，但 FilmAgent 核心架构不得绑定百炼。**
  百炼只是 `PROVIDERS` 里的一个 Config 档（`bailian_text` / `bailian_vision` /
  `bailian_embedding`），换个厂商 / 本地模型只是换配置，不改业务 Agent。
- 当前主要免费能力方向：大语言模型 / 视觉模型 / 语言模型 / 向量模型 / 全模态模型
  （只作为 Config 示例方向出现）。
- **不要把具体模型名称硬编码为项目架构事实**：模型名与免费策略可能变化，
  具体模型放在 Provider / Config 层（`PROVIDERS[*]["default_model"]`），不进业务代码。

### 5.6 当前本地模型策略

```text
本地模型属于未来可接入 Provider（未来 LocalTextProvider 大概率是 OpenAI 兼容文本的一档配置）。
当前：未下载 / 未部署 / 未接入真实本地模型
架构：已经预留 Local Text Provider 路径（PROVIDERS["local_text"] Config 档）
```

本阶段（含 Stage 9）不得下载 / 部署：Ollama、vLLM、任何本地模型权重。

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
- Storyboard / Image / Video / VLM Agent 未实现；image / video / audio 仅 Schema +
  Provider 预留接口可表达，**尚无真实生成**。
- 真实异步"暂停等人工"审核流程、UI / 数据库 / 生产部署均未做。

## 7. 下一阶段

```text
Stage 10：NOT STARTED
```

路线图方向（方向性参考，非 Stage 10 正式方案）：

```text
StoryboardAgent / 接入下一个业务 Agent（Dialogue / Shot / Script）
→ Project 生命周期 → 第一条完整业务链 → 第一集真实生成 → V0.2.0-alpha
```

**Stage 10 尚未正式制定开工方案，不要虚构其具体实现细节**——等待下一份正式开工指令
（放于 `D:\AI\FilmAgent\`）。进入 Stage 10 前：开全新 Claude Code 会话，先读
CLAUDE.md → 本文件 → `HANDOFF.md` → 最近 Stage Report（`stage9_report.md`）→
当前 Git 状态 → 与 Stage 10 直接相关的代码。

## 8. V0.1 冻结规则

- `FilmAgent/` 下任何文件（尤其 `LLMCaller.py`、`main.py`、`Prompt/`、`Locations/`、
  `Script/`、`api_key.txt`）不得修改。
- 验收时 `git diff -- FilmAgent/` 必须为空；`FilmAgent/` 无 dirty 行。
- 自动化断言：`v0.1^{commit}` 固定 `344c0c6…`、`v0.2-stage7` / `v0.2-stage8` tag 存在、
  `git diff -- FilmAgent/` 为空。

## 9. 协作与会话管理规范

详见 `CLAUDE.md`「协作与会话管理（长期规则）」一节。要点：

- 阶段是第一层边界：一个 Claude Code 会话通常只做一个 Stage；完成后 commit
  （必要时 tag），开新会话进入下一 Stage，不在一个超长会话连续堆多个 Stage。
- A/B/C/D 默认 = 不同的 Claude Code 会话 / 终端（A 分析架构、B 实现、C 测试调试、
  D 文档交付），**不是**四个并存的子 Agent。
- 按任务复杂度选择会话数（小任务单会话 A+B+C+D；中型 A→B→C→D；大型可加专项分析），
  不机械开大量会话。
- 子 Agent 仅用于专项代码侦察 / 独立研究 / 局部测试 / 并行检查，不替代会话边界。
- 跨会话记忆靠文档（CLAUDE.md / 本文件 / HANDOFF.md / Stage Report / Git）；
  聊天记录不作为项目长期记忆。
