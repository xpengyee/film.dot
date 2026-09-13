# ROADMAP.md —— FilmAgent V0.2 路线图与阶段依赖

> 回答"后面要去哪、以及**为什么是这个顺序**"。总控据此判断下一阶段；
> 执行会话不需要读本文件（读 `CLAUDE.md` + `HANDOFF.md` + 本阶段开工指令即可）。
>
> 最后更新：Stage 10-D 交付后（Stage 11 开工指令已就位，等待执行会话）。

---

## 1. 北极星目标

> 复现 **FilmAgent**：多智能体端到端电影生成。
> 终极链路：`剧本 → Casting → Scene/Shot → Storyboard → 图像生成 → 视频生成 → 配音 → 成片`

V0.2 的策略是**先建可测试、可扩展、有人工审核的基础设施**，再逐步接上真实生成。
V0.1 是"已跑通但不可维护的串行脚本"，V0.2 是"工程化的多智能体框架"。

---

## 2. 路线图全景（当前视角）

```text
                    ┌─── 已完成（Stage 0–10）────────────────────────────┐
                    │ 0-1 骨架 / 数据模型 / Validator / Assets           │
                    │ 2   LLMClient / Token·Cost                         │
                    │ 3   Workflow Engine / Step / Artifact 版本化        │
                    │ 4   Memory 三态 / Context / Review 三态             │
                    │ 5   依赖整理 / 静态检查 / 基础文档                  │
                    │ 6   CastingAgent（跨集角色复用）                    │
                    │ 7   SceneGenerationAgent（场景剧本）                │
                    │ 8   Canonical Schema（Stage 架构核心架构定稿）       │
                    │ 9   Provider Architecture（模型调用解耦）           │
                    │ 10  Visual Asset 层（视觉身份 / 版本 / 参考图 / 组装）│
                    └───────────────────────────────────────────────────┘
                     ↓ 当前位置
                    ┌─── 规划中（Stage 11+）─────────────────────────────┐
                    │ 11  StoryboardAgent（Shot 规划）  ← 已设计，待执行  │
                    │ 12  Project 生命周期 + 第一条完整业务链             │
                    │ 13  Image Generation（真实出图 + VLM QA）           │
                    │ 14  Video Generation（图 → 视频）                   │
                    │ 15  Audio / TTS（配音）                             │
                    │ 16  Assembly（合成成片）→ V0.2.0-alpha              │
                    └───────────────────────────────────────────────────┘
```

> 说明：Stage 11 之后为**方向性规划**，具体设计在进入该阶段前才定稿。
> 禁止把本文件的远期条目当作已定方案实现。

---

## 3. 阶段依赖链（为什么是这个顺序）

```text
Stage 8  Canonical Schema          Project→Scene→Beat→Shot→Asset
   ↓  （Shot 是业务最小单位）
Stage 9  Provider Architecture     模型调用与业务解耦
   ↓  （业务 Agent 只认抽象 Provider）
Stage 10 Visual Asset 层           被引用的角色/地点/道具/风格具备"视觉身份+版本"
   ↓  （Shot 引用的名字能解析成稳定视觉身份 + 参考图）
Stage 11 StoryboardAgent           Shot 从"手工声明"变成"由剧本规划产出"
   ↓  （有稳定的 Shot 序列，才有可组装的图像任务）
Stage 12 Project 生命周期          多集项目从 0 到成片的完整编排
   ↓
Stage 13 Image Generation          ComposedVisualInput → 真实图像
   ↓
Stage 14 Video Generation          Shot 序列 + CameraPlan → 视频
   ↓
Stage 15 Audio / TTS               →  Stage 16 Assembly → V0.2.0-alpha
```

**依赖理由（关键）**：
- Stage 10 必须在 Stage 11 之前，因为 Storyboard 规划出的 Shot 一旦要出图，
  就必须能解析到 approved 的视觉档案；否则 Storyboard 会退化成"只有文字"。
- Stage 11 必须在 Stage 13 之前，因为真实出图需要**稳定的 Shot 序列**作为调度单位。
- 真实生成类阶段（13+）必须在视觉身份层（10）稳定之后，否则每换一次资产架构
  都要重做已生成的图。

---

## 4. 当前格位

```text
当前：Stage 10 已封存（v0.2-stage10）；Stage 11 开工指令已就位
下一阶段：Stage 11 —— StoryboardAgent（`D:\AI\FilmAgent\v0.2开工指令11`）
阻塞：无技术阻塞。可随时开新会话执行 Stage 11。
```

---

## 5. 技术债登记（总控维护，按优先级）

| # | 项 | 影响 | 建议处理时机 | 状态 |
|---|---|---|---|---|
| T-1 | 从仓库根跑测试会在根目录生成 `llm_calls.jsonl` 并**可能进 git**（`client.py:134` 默认 `TokenLogger(Path("."))`；仓库根 `.gitignore` 属 V0.1 基线，无该规则） | 每轮从根跑测试都需人工清理，漏删即污染版本库 | **Stage 12 前**；根治方式需先取得用户对"是否可动仓库根 `.gitignore`"的授权 | 已登记，未修 |
| T-2 | `prompt_length` 对多模态消息语义失真（content-parts 列表按 `len()` 计 part 数） | JSONL 统计字段误导；不影响 `prompt_hash` 与计费 | 需改 `v2/llm/client.py`（禁改清单）→ 需总控授权 | 已登记，未修 |
| T-3 | 成本为 `null`（`PROVIDERS["bailian_vision"]["pricing"]=None` 护栏） | 真实视觉调用无成本记账 | 在 `config.PRICING` 登记 qwen3.7-plus 价格即可（无需改业务代码） | 已登记，未修 |
| T-4 | 真实冒烟脚本 `run_real_visual_extraction.py` 未调用 `add_reference_image()` | 真实链路不落 checksum 锚点（0 API 本地测试 T06b 已完整证明该链路） | 可选增强，一行改动 | 已登记，未修 |
| T-5 | 人工审核目前是程序化 decision 模拟，交互式审核界面未做 | 无法真人实操审核 | 待业务链打通后立项 | 已登记，未修 |
| T-6 | `stage4/stage6/stage7_report.json` 每次回归都会被重写（含随机 id）产生 Git dirty | 交付前需 `git checkout --` 复位，易误提交 | 可让套件写固定/可复现 id，或把报告移出跟踪 | 已登记，未修（Stage 10-D 已按要求复位） |

---

## 6. 未实现能力清单（诚实边界）

**接口/ Schema 已就位但无真实实现**：

```text
ImageGenerationProvider / VideoGenerationProvider / AudioProvider   预留接口，调用即 NotImplementedError
真实图片生成 / 真实视频生成 / TTS                                   未实现
VLM QA Agent（生成图 vs 参考图对比）                                未实现（Visual Asset 已备好 ground truth）
StoryboardAgent                                                    未实现（**Stage 11 目标，指令已就位**）
向量库 / Embedding 检索接入                                        未做
本地模型（Ollama / vLLM / 权重）                                   未下载、未部署
交互式人工审核 UI / 数据库 / 生产部署                               未做
```

**已有真实实现**：

```text
Mock Provider 四件套（Text/Vision/Multimodal/Embedding）           ✅ 测试用，0 成本
OpenAICompatibleTextProvider（deepseek 档）                        ✅ 接口就位（真实调用需配置）
BailianTextProvider                                                ✅ 接口就位（default_model 空 = 护栏）
OpenAICompatibleVisionProvider（bailian 档）                       ✅ **已真实跑通 3 次**（qwen3.7-plus，2026-09-13）
```

---

## 7. 成本与预算纪律（跨阶段长期规则）

```text
默认 FILMAGENT_PROVIDER=mock → 一切测试 0 API / 0 成本
真实调用只走独立 run_real_*.py 脚本，需人工明确批准
每次真实调用的次数必须由程序计数（APIRequestCounter）+ 预算硬上限护栏
真实调用必须记 TokenLogger（与 mock 分账，绝不混记）
禁止用"看起来像"的结果替代真实结果；无配置时明确报告阻塞，绝不伪造
```

各阶段实际消耗（累计）：

| 阶段 | 真实视觉 | 真实文本 | 成本 |
|---|---|---|---|
| 0–9 | 0 | 0（测试） | ¥0.00 |
| 10-B | **3**（qwen3.7-plus，14,433 tokens，Retry=0） | 0 | null（价格未登记，T-3） |
| 10-C | 0 | 0 | ¥0.00 |

---

*本文件由总控维护：每完成一个 Stage 更新 §4 与 §5；每次真实调用后更新 §7。*
