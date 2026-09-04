# PROJECT_STATUS.md —— FilmAgent 项目状态

> FilmAgent 的权威项目状态文档，任何新 Claude Code 会话开始时都应先读本文件。
> 最后更新：V0.2 Stage 8 封存后（协作规范与会话管理体系落地完成）。

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
当前阶段：协作规范与会话管理体系建设 —— COMPLETE（本次文档沉淀完成）

V0.2 Stage 8：PASS + 已封存
V0.2 Stage 9：NOT STARTED

当前没有任何新的业务代码任务。
```

## 3. 历史 Stage 0–8（里程碑）

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
| 8 | 完成 Canonical Schema：`Project → Scene → Beat → Shot → Asset`；建立 Shot / Beat / Storyboard / Asset / Adapter / Agent Base / 相关 Validator。Stage 8 测试 27/27 + 历史回归 19/19+38/38+25/25+24/24+29/29 全绿 + V0.1 无损往返 + 0 次真实 LLM + 0 API 成本 | PASS + 已封存 |

## 4. Git 里程碑

| 里程碑 | commit | tag |
|---|---|---|
| V0.1 冻结基准 | `344c0c6b7c48497be934957c29fcae029cca6752` | `v0.1` |
| V0.2 Stage 0–7 基线 | `300aae44ad1d704219822efcf55249d1d59ad118` | `v0.2-stage7` |
| V0.2 Stage 8 Canonical Schema | `abf8e88a983f13c437abe2b719c920d3c5c3c144` | `v0.2-stage8` |
| 文档 commit（阶段 8 后） | `494a86a`（docs: update project context after stage 8） | — |
| 文档沉淀 commit（本次） | docs: establish project handoff and Claude session workflow | — |

版本 tag（`v0.1` / `v0.2-stage7` / `v0.2-stage8`）一经创建**永不移动、永不 force**。

## 5. 当前架构

已落地的核心基础设施：

```text
V0.2
│
├── core      models / validator / assets / shot / adapter
├── llm       client / router / token & cost
├── workflow  step / engine / artifact / errors
├── memory
├── context
├── review
└── agents    base / casting / scene generation
```

依赖方向：`core → (llm | memory | context) → workflow → review → agents`
（只允许向上，禁止反向/循环）。

未来系统方向（尚未实现）：

```text
Director → Script → Casting → Scene → Beat → Shot → Storyboard → Visual QA
→ Image → Video → Audio → Assembly
```

注意：后续图像、视频、音频生成都通过 **Provider 层**接入，不允许把 FilmAgent
设计成某一家平台的硬编码系统。

## 6. 已知问题 / 遗留

- `openai` 库需按 CLAUDE.md 安装（唯一非标准库依赖；仅真实 LLM 调用时用）。
- 人工审核目前是程序化 decision 模拟，交互式审核界面未做。
- `v2/README.md` 过时（仍写"尚未实现任何业务 Agent"；实际 Stage 6/7/8 已实现
  Casting、SceneGeneration、Canonical Schema）。非本次指令范围，列为遗留待更新。
- Storyboard / Image / Video / VLM Agent 未实现；image / video / audio 资产仅 Schema 可表达。
- 真实异步"暂停等人工"审核流程、UI / 数据库 / 生产部署均未做。

## 7. 下一阶段

```text
Stage 9：NOT STARTED
```

路线图方向：

```text
StoryboardAgent / 接入第三个业务 Agent（Dialogue / Shot / Script）
→ Project 生命周期 → 第一条完整业务链 → 第一集真实生成 → V0.2.0-alpha
```

**进入 Stage 9 前**：开全新 Claude Code 会话，先读 CLAUDE.md → 本文件 →
`HANDOFF.md` → 最近 Stage Report → 当前 Git 状态 → 与 Stage 9 直接相关的代码。

## 8. V0.1 冻结规则

- `FilmAgent/` 下任何文件（尤其 `LLMCaller.py`、`main.py`、`Prompt/`、`Locations/`、
  `Script/`、`api_key.txt`）不得修改。
- 验收时 `git diff -- FilmAgent/` 必须为空；`FilmAgent/` 无 dirty 行。
- 自动化断言：`v0.1^{commit}` 固定 `344c0c6…`、`v0.2-stage7` tag 存在、
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
