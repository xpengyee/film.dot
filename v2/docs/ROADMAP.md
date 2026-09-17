# ROADMAP.md —— FilmAgent V0.2 路线图与阶段依赖

> 回答"后面要去哪、以及**为什么是这个顺序**"。总控据此判断下一阶段；
> 执行会话不需要读本文件（读 `CLAUDE.md` + `HANDOFF.md` + 本阶段开工指令即可）。
>
> 最后更新：Stage 13 交付后（图像链路代码/测试/报告已就位，待人工验收；VLM QA 为独立 Stage 14）。

---

## 1. 北极星目标

> 复现 **FilmAgent**：多智能体端到端电影生成。
> 终极链路：`剧本 → Casting → Scene/Shot → Storyboard → 图像生成 → 视频生成 → 配音 → 成片`

V0.2 的策略是**先建可测试、可扩展、有人工审核的基础设施**，再逐步接上真实生成。
V0.1 是"已跑通但不可维护的串行脚本"，V0.2 是"工程化的多智能体框架"。

---

## 2. 路线图全景（当前视角）

```text
                    ┌─── 已完成（Stage 0–13）────────────────────────────┐
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
                    │ 11  StoryboardAgent（Shot 规划：Scene→Beat→Shot）    │
                    │ 12  编排层 ProjectOrchestrator（第一条端到端业务链） │
                    │ 13  Image Generation（真实出图：image 模态 + 预算护栏）│
                    └───────────────────────────────────────────────────┘
                     ↓ 当前位置
                    ┌─── 规划中（Stage 14+）─────────────────────────────┐
                    │ 14  VLM QA（生成图 vs 参考图 → 逐字段裁决）★见下注  │
                    │ 15  Video Generation（图 → 视频）                   │
                    │ 16  Audio / TTS（配音）                             │
                    │ 17  Assembly（合成成片）→ V0.2.0-alpha              │
                    └───────────────────────────────────────────────────┘
```

> 说明：Stage 13 之后为**方向性规划**，具体设计在进入该阶段前才定稿。
> 禁止把本文件的远期条目当作已定方案实现。
>
> **★注（总控裁决 2026-09-15）：VLM QA 从 Stage 13 拆出，独立为 Stage 14。**
> 原规划把"真实出图 + VLM QA"合并为 Stage 13。拆分理由：
> ① Stage 13 真正的风险面在"新模态的协议 / 成本 / 预算护栏"，本身已是一个完整阶段；
> ② QA 需要**真实生成的图**作为被测对象 —— 先有图，QA 才有意义；
> ③ 合并会让验收标准模糊（出图失败还是 QA 判错？难以归因）。
> Stage 13 仍必须为 QA 备好输入契约（manifest 记录 `profile_versions` 与
> `reference_images` 的 sha256 锚点），否则 Stage 14 会返工。

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
Stage 12 编排层 ProjectOrchestrator 把四个 Agent 串成第一条端到端业务链
   ↓  （Casting → [每场景] Scene → Storyboard → 视觉资产齐备；多场景分发就位、可 resume）
Stage 13 Image Generation          ComposedVisualInput → 真实图像（+ 成本记账 / 预算硬护栏）
   ↓  （有真实生成的图，QA 才有被测对象）
Stage 14 VLM QA                    生成图 vs 参考图 → 按 approved 档案字段逐项裁决
   ↓  （单帧先验合格，才值得投入更贵的视频生成）
Stage 15 Video Generation          Shot 序列 + CameraPlan → 视频
   ↓
Stage 16 Audio / TTS               →  Stage 17 Assembly → V0.2.0-alpha
```

**依赖理由（关键）**：
- Stage 10 必须在 Stage 11 之前，因为 Storyboard 规划出的 Shot 一旦要出图，
  就必须能解析到 approved 的视觉档案；否则 Storyboard 会退化成"只有文字"。
- Stage 11 必须在 Stage 13 之前，因为真实出图需要**稳定的 Shot 序列**作为调度单位。
- Stage 12 必须在 Stage 13 之前，因为出图需要"谁决定按什么顺序出、出哪些镜头、
  失败怎么续跑"的调度层；否则每加一个生成阶段都要重写一遍流程控制。
- Stage 14（VLM QA）必须在 Stage 13 之后，因为 QA 的被测对象是**真实生成的图** ——
  先有图，QA 才有意义（这也是 2026-09-15 把 QA 从 Stage 13 拆出的直接理由）。
- Stage 15（Video）必须在 Stage 14 之后，因为视频按帧/秒计费远贵于单图；应先用便宜的
  QA 把"视觉一致性"验在单帧上，避免把不合格的图直接烧进视频。
- 真实生成类阶段（13+）必须在视觉身份层（10）稳定之后，否则每换一次资产架构
  都要重做已生成的图。

---

## 4. 当前格位

```text
当前：Stage 13（Image Generation 真实出图）**代码/测试/报告已交付，待人工验收 + commit/tag**
      —— `image` 模态已开通（mock + 真实 OpenAI 兼容档）；编排层新增 image 段；
      三重护栏（默认不出图 / 预算硬上限 / 绝不伪造出图）与图像独立记账就位。
      ★ 真实出图**未执行**（人工四项前置未齐 → 如实报告阻塞，不用占位图冒充）。
下一阶段：Stage 14 —— VLM QA（生成图 vs 参考图 → 按 approved 档案字段逐项裁决）
          Stage 13 已备好输入契约：manifest 记录 profile_versions + reference_images(sha256)
阻塞：无技术阻塞。
      Stage 13 遗留（不影响交付，详见 stage13_report.md §遗留）：
        ① 目标厂商（百炼兼容模式）是否完整支持 OpenAI `/images/generations` **未能从公开文档核实**
           → 首次真实调用前需人工确认；若协议差异过大，注入厂商 transport 即可（不动架构）。
        ② `config.IMAGE_PRICING` 为空表 → 真实出图的 estimated_cost 记 null（护栏，非缺陷）。
        ③ 单次 run 出图数被收紧为 min(max_images, MAX_REAL_IMAGE_REQUESTS=3)（见技术债 T-8）。
        ④ Stage 12 遗留的 `run_real_lifecycle.py` 已并入本阶段真实冒烟脚本
           `v2/logs/stage13_image/run_real_image.py`（已就位，未执行）。
```

---

## 5. 技术债登记（总控维护，按优先级）

| # | 项 | 影响 | 建议处理时机 | 状态 |
|---|---|---|---|---|
| T-1 | 从仓库根跑测试会在根目录生成 `llm_calls.jsonl` 并**可能进 git**（`client.py:134` 默认 `TokenLogger(Path("."))`；仓库根 `.gitignore` 属 V0.1 基线，无该规则） | 每轮从根跑测试都需人工清理，漏删即污染版本库 | **仍未根治**；根治方式需先取得用户对"是否可动仓库根 `.gitignore`"的授权 | 已登记，未修（Stage 12 仍靠"跑完 `rm -f llm_calls.jsonl v2/llm_calls.jsonl`"人工复位） |
| T-2 | `prompt_length` 对多模态消息语义失真（content-parts 列表按 `len()` 计 part 数） | JSONL 统计字段误导；不影响 `prompt_hash` 与计费 | 需改 `v2/llm/client.py`（禁改清单）→ 需总控授权 | 已登记，未修 |
| T-3 | 成本为 `null`（`PROVIDERS["bailian_vision"]["pricing"]=None` 护栏） | 真实视觉调用无成本记账 | 在 `config.PRICING` 登记 qwen3.7-plus 价格即可（无需改业务代码） | 已登记，未修 |
| T-4 | 真实冒烟脚本 `run_real_visual_extraction.py` 未调用 `add_reference_image()` | 真实链路不落 checksum 锚点（0 API 本地测试 T06b 已完整证明该链路） | 可选增强，一行改动 | 已登记，未修 |
| T-5 | 人工审核目前是程序化 decision 模拟，交互式审核界面未做 | 无法真人实操审核 | 待业务链打通后立项（Stage 12 已打通链路，仍未立项） | 已登记，未修 |
| T-6 | `stage3/stage4/stage6/stage7_report.json`（及 `stage3/stage4_console.txt`）每次回归都会被重写（含随机 id）产生 Git dirty | 交付前需 `git checkout --` 复位**6 个文件**，易误提交 | 可让套件写固定/可复现 id，或把报告移出跟踪 | 已登记；★**Stage 13 已缓解**：stage13 套件在 `main()` 起始与 `run_history()` 前后**自动复位**这 6 个文件（`_reset_history_products()`），跑完工作区保持干净 |
| T-7 | ★**图像是二进制大文件**：生成的真实图片一旦入库，仓库迅速膨胀且无法回退 | 单次误 `git add` 即污染版本库 | Stage 13 已补 `v2/.gitignore` 规则 + 交付前二进制扫描 | ★**Stage 13 已落地防护**：§8.4 六条规则 + 套件 T15（`git check-ignore` 命中 / `git status` 无图片 / `v2/` 无二进制入库）；仓库根 `.gitignore` 仍 0 改 |
| T-8 | 单次 run 出图数被收紧为 `min(run_spec["max_images"], MAX_REAL_IMAGE_REQUESTS=3)`：**`max_images` 只能调小、不能调大** | 需要一次出图 >3 张时无法通过 run_spec 提高（须改 `request_counter.py` 的常量） | 若业务确需更高上限，由总控评估后调整常量（并同步更新 stage13 套件 T06f 断言） | 已登记（属**安全默认**，非缺陷；run_report 同时记 `max_images_requested` 与 `budget_cap`，不误导） |
| T-9 | ★**目标厂商协议未核实**：无法从公开文档确认百炼兼容模式完整支持 OpenAI `POST /images/generations`（返回 b64/url、是否异步轮询） | 首次真实出图可能因协议差异失败 | 首次真实调用前人工确认；若差异过大，**注入厂商 transport**（不动接口签名/架构） | 已登记（已写入 `run_real_image.py` 体检项与 `stage13_report.md` 遗留） |
| T-10 | `config.IMAGE_PRICING` 为空表 → 真实出图的 `estimated_cost` 恒为 `null` | 真实出图无成本记账数字 | 人工在 `config.IMAGE_PRICING` 登记所选模型的单价即可（无需改业务代码） | 已登记（属**护栏**，非缺陷：绝不用 token 价冒充图像价） |
| T-11 | `manifest.json` 落在 `v2/projects/<pid>/generated_images/`，而 `v2/.gitignore` 的 `projects/` 整目录规则使其**不会入库**（与 §8.4「manifest 保留为结构化证据」的表述有张力） | 真实运行的 manifest 不作为仓库证据留存 | 真实冒烟脚本已把 manifest **副本（无二进制）** 落到 `v2/logs/stage13_image/real_run/manifest_copy.json` | 已登记（处置见 stage13_report.md §裁决点） |
| T-12 | 图像审核遇 `approved_with_edit` 直接抛 `OrchestrationError`（图像是二进制，无"编辑后内容"的确定性来源） | 无法对生成图做"编辑后通过" | 图像编辑属未立项能力（§4.4）；如需，应先有"图像编辑 Provider" | 已登记（fail-loud 优于静默猜，与 Stage 12 视觉资产裁决 2 同源） |
| T-13 | ★**历史回归的嵌套成本呈指数级**：每个后期套件的 `run_history()` 会跑全部前序套件，而前序套件自身也在内嵌（stage12→stage1..11，stage11→stage1..10，stage10→stage1..9…）。stage13 的 T18 内嵌 10 套 → 一次全量复跑实测 **≈58 分钟**（约 70 次套件执行） | 日常回归慢；CI 化不可行 | 可选：①各套件加"跳过自身嵌套回归"的环境开关（需改历史套件，属禁改 → 需总控授权）；②T18 改为并行子进程（各套件目录互不重叠，但共享 `v2/llm_calls.jsonl` 与 6 个报告产物 → 有竞态风险）；③把历史回归改为 CI 分层 | 已登记，未修（Stage 13 如实记录实测耗时） |

---

## 6. 未实现能力清单（诚实边界）

**接口/ Schema 已就位但无真实实现**：

```text
VideoGenerationProvider / AudioProvider                            预留接口，调用即 NotImplementedError
真实图片生成（真实调用）                                             ★Stage 13 已实现代码，**真实调用未执行**
                                                                    （需人工四项前置；Mock 路径已跑通）
真实视频生成 / TTS                                                  未实现（Stage 15 / 16）
VLM QA Agent（生成图 vs 参考图对比）                                未实现（Stage 14；Stage 13 已备好输入契约）
图生图 / 局部重绘 / 多图合成 / 图像编辑                              未立项
向量库 / Embedding 检索接入                                        未做
本地模型（Ollama / vLLM / 权重）                                   未下载、未部署
交互式人工审核 UI / 数据库 / 生产部署                               未做
跨集 / 全项目批量编排                                              未做（编排层当前只编排一集）
```

**已有真实实现**：

```text
Mock Provider 四件套（Text/Vision/Multimodal/Embedding）           ✅ 测试用，0 成本
MockImageGenerationProvider（Stage 13）                            ✅ 确定性最小合法 PNG（纯 stdlib），0 网络 0 成本
OpenAICompatibleTextProvider（deepseek 档）                        ✅ 接口就位（真实调用需配置）
BailianTextProvider                                                ✅ 接口就位（default_model 空 = 护栏）
OpenAICompatibleVisionProvider（bailian 档）                       ✅ **已真实跑通 3 次**（qwen3.7-plus，2026-09-13）
OpenAICompatibleImageProvider（Stage 13，bailian_image 档）         ✅ 接口就位（b64/url 两路 + 计数点 + transport 可注入）
                                                                    ❌ 真实调用 0 次（default_model 空 = 护栏）
StoryboardAgent                                                    ✅ **已实现（Stage 11）**：agents/storyboard.py
编排层 ProjectOrchestrator                                          ✅ **已实现（Stage 12）**：v2/orchestration/
                                                                      （preflight / state / pipeline；★ v2/workflow/ 0 diff + 0 新增）
图像段 ImageStage                                                   ✅ **已实现（Stage 13）**：v2/orchestration/image_stage.py
                                                                      （compose→出图→落盘→manifest→记账→Review→挂回→新版本）
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

| 阶段 | 真实图像 | 真实视觉 | 真实文本 | 成本 |
|---|---|---|---|---|
| 0–9 | 0 | 0 | 0（测试） | ¥0.00 |
| 10-B | 0 | **3**（qwen3.7-plus，14,433 tokens，Retry=0） | 0 | null（价格未登记，T-3） |
| 10-C | 0 | 0 | 0 | ¥0.00 |
| 11（A–D） | 0 | 0 | **0**（全 Mock；真实脚本已就位未执行） | ¥0.00 |
| 12（A–D） | 0 | 0 | **0**（全 Mock；`run_real_lifecycle.py` 未创建未执行） | ¥0.00 |
| 13（A–D） | **0**（`run_real_image.py` 已就位未执行；人工四项前置未齐 → 如实报告阻塞） | 0 | **0**（全 Mock，`FILMAGENT_PROVIDER` 未设） | ¥0.00 |

★ Stage 13 是**第一个图像计费入口**：一旦人工批准真实出图，成本将按所选模型单价计
（须先在 `config.IMAGE_PRICING` 登记，否则 `estimated_cost` 记 null）。三重护栏：
默认不出图 / 预算硬上限（单 run ≤ `min(max_images, 3)`）/ 绝不伪造出图。

---

*本文件由总控维护：每完成一个 Stage 更新 §4 与 §5；每次真实调用后更新 §7。*
