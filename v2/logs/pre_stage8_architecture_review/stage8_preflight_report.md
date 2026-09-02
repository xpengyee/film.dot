# FilmAgent V0.2 —— 阶段 8 前技术侦察与架构评估报告（stage8_preflight_report）

> 指令：`D:\AI\FilmAgent\v0.2开工指令6.2`
> 性质：**只研究、只分析、只写报告，不动任何生产代码。**
> 项目根：`D:\AI\FilmAgent-main`（V0.1 冻结 commit = `344c0c6`，V0.2 位于 `v2/`）
> 参考项目：`D:\AI\references\take-main`、`D:\AI\references\movie-Agent-main\movie-Agent-main`
> 日期：2026-09-02

---

## 0. 结论速览（先看这个）

一句话：**V0.2 阶段 0~7 的地基是健康、可保留、方向正确的；阶段 8 不应再加业务 Agent，而应先做两件地基升级——①一套 Canonical Production Schema（把 Scene/Shot/Beat/Storyboard/Asset 的数据结构定死），②把 LLMClient 抽象升级成 Provider 层（Text/Vision/Image/Video/Audio/TTS 都按同一套"能力接口 + 路由 + 回退"接入），让未来接入阿里云百炼的多模态不走回头路。**

| 验收项 | 要求 | 结果 |
|---|---|---|
| V0.1 是否修改 | 必须 NO | ✅ 未改（见 §18） |
| 真实 LLM 调用 | 必须 0 | ✅ 0 次 |
| 新增 API 调用 | 必须 0 | ✅ 0 次 |
| v2 业务代码修改 | 必须 0 | ✅ 0 行改动（仅新增本报告文件） |
| 参考项目是否被修改 | 必须 NO | ✅ 未改（只读研究） |
| 产出 | 仅本报告 | ✅ |

---

## 1. 当前 V0.2 架构总结

（本章描述"现状"，为后面每一章提供共同的事实底座。）

### 1.1 目录与分层

V0.2 全部新工作在 `D:\AI\FilmAgent-main\v2\`，约 21 个 Python 源文件（约 4500 行），分 7 层，依赖只允许自下而上：

```text
core → (llm | memory | context) → workflow → review → agents
```

| 层 | 文件 | 现状职责 |
|---|---|---|
| **core** | `core/models.py`(544行) `validator.py`(532行) `assets.py`(188行) `io.py`(68行) `token_log.py`(222行) | 统一数据模型（含 V0.1 双向转换）、三合一 Validator（Enum/Reference/Schema）、资产注册表、JSON 容错 IO、Token/成本日志 |
| **llm** | `llm/client.py`(292行) `router.py`(41行) | `LLMClient.chat(messages, step=, agent=)`、错误家族、重试、transport 可注入；Router 目前是空壳（全走 deepseek-chat） |
| **memory** | `memory/models.py`(93行) `store.py`(192行) | `MemoryStore(project_id)`：facts 三态 candidate/approved/rejected、characters、world、history，JSON 持久化 + revision |
| **context** | `context/models.py`(132行) `assembler.py`(198行) | `ContextAssembler`：按 Step 声明式 `needs` 组装 `AssembledContext`，每项带来源追踪 |
| **workflow** | `workflow/{models,step,engine,artifact,errors}.py`(1039行) | Step（run/validate 插槽）、Engine（状态机 + retry + resume）、ArtifactStore（版本化不覆盖） |
| **review** | `review/{models,service}.py`(278行) | 人工审核：approve / approve_with_edit（写编辑版新 Artifact）/ reject |
| **agents** | `agents/casting.py`(251行) `agents/scene_generation.py`(317行) | 业务 Agent（目前 2 个真实业务 Agent：角色分配、单场场景/剧本生成） |

+ `config.py`(122行)：API key 安全读取 + 脱敏 + 价格表。
+ `prompts/`：`v0.1/`（19 个，从 FilmAgent/Prompt 原样迁入）+ `v0.2/`（`casting.txt`、`scene_generation.txt`，本阶段新写的 Agent 提示词）。
+ `assets/locations/`：15 个地点的 position、shots、actions（20 动作/9 镜头）。

### 1.2 已跑通的数据流闭环

```text
Project → Memory → ContextAssembler(按 needs 组装)
   → Agent.run(context) → LLMClient(真实 DeepSeek / mock)
   → 解析 JSON → 确定性规范化(_normalize) → Validator(确定性拦截)
   → Workflow 落盘 Artifact(版本化不覆盖) → Review(三态) → 继续 / 停止 / 重试
```

- **真实业务 Agent 2 个**：`CastingAgent`（阶段6）、`SceneGenerationAgent`（阶段7），均已通过真实 DeepSeek 调用验证（Casting 3 次 ≈¥0.01；SceneGeneration 3 次 ≈¥0.06）。
- **关键约定已固化**：Memory 三态（candidate 绝不自动升 approved）；Artifact 版本化（绝不覆盖）；Review 三态；跨集角色一致性靠"确定性规则 + Validator 兜底"；`scene_spec` 经 `WorkflowContext.inputs` 传入（不改 ContextAssembler）。

### 1.3 当前公认的短板（评估的出发点）

1. **数据模型是"V0.1 反向工程"的结果**：`core/models.py` 的 Scene/Line/Action/Move 是为了容纳 V0.1 三种字段变体而设计的，字段偏"叙事剧本"视角，**没有 Beat / Shot / Storyboard / Image / Video / Timeline / Render**——而未来 Text→Video 恰恰要这些。
2. **LLM 层是"单文本模型"的形状**：`LLMClient` + 空壳 Router 只服务 DeepSeek 文本对话，没有"能力维度"（文本/视觉/图像/视频/音频），未来接图像/视频必须重构。
3. **Agent 已出现"重复骨架"苗头**：casting 与 scene_generation 各自实现了 `_parse_json / _normalize / run`，若继续加第三/第四业务 Agent（dialogue、shot、storyboard…），会复制同一套"调 LLM→解析→规范化→Validator→交产物"模板。
4. **prompt 与代码分离得不错，但缺"schema 驱动"**：`prompts/v0.2/*.txt` 靠自然语言要求 LLM 输出，无结构化 schema 绑定输出。
5. **人工审核目前是"程序化 decision 模拟"**：交互式审核界面未做（阶段 4 遗留，非本次范围）。

---

## 2. take 技术分析

（研究方式：只读通读 take 全部源码与文档 `packages/{core,provider,cli,mcp}`、`skills/take`、`docs`、`examples/rain-night`，未修改任何文件。）

### 2.1 一句话定位

take 是 **agent-first 分镜与 AI 电影制作工具**：`script → beat → shot → storyboard → image → video`。"the agent thinks, take renders"——剧本分析、节拍、镜头语言这些**推理**由 coding agent（如 Claude Code）按 take skill 引导完成；take 自己**不含任何 LLM client**，只负责把写好的 `shots.json` **渲染**成图/视频。思考与渲染之间靠"机器可校验的 JSON 文件契约"衔接。

### 2.2 逐问结论（§三 1~13）

| # | 问 | take 的答案（精要） |
|---|---|---|
| 1 | script/beat/shot/storyboard 数据结构 | 全在 `core/schemas.ts`（Zod）。**Script**{title,content}；**Beat**{id:`beat-\d+`, index 从1连续, summary, purpose(须写具体叙事功能,禁空话), emotion?, sceneId?}；**Shot**（字段最多）{id:`shot-\d+`, beatId(引用父beat), index 连续, durationSec, 受控枚举 shotSize(7景别)/angle(8)/movement(14), characters[], location?/lighting?/tone?, **imagePrompt(必填)**, videoPrompt?, visual{characterRefs 角色url/风格}, status(draft→approved→rendering→done/failed 5态), assets{image?,video?} 渲染产物指针}；**Storyboard**{aspectRatio, style?, source, shots[], beats?}。跨字段完整性靠 validate |
| 2 | core 组织方式 | `models/`纯类型 + `schemas.ts`唯一Zod校验源 + `validate.ts`(schema+跨字段完整性+isRenderable) + `io/`(markdown往返/规范路径) + `grid.ts`。core 依赖**只有 zod**，零 I/O 零 AI。推理完全排除在模型之外，模型只承载结果 |
| 3 | provider 抽象 | seam.ts 定义 `Provider` 接口{kind:image\|video, name, model?, generateImage, generateVideo, health}，业务只依赖接口。稳定错误码 `TakeErrorCode`(限流/配额/凭证/不支持/超时/无adapter/空响应/超窗/网络/内部)，路由只按 code 走，`RETRYABLE_CODES={RATE_LIMIT,TIMEOUT,NETWORK}`。transport：两级超时、强制 user-agent attribution 头、独立 retry 执行器(RetryPolicy 是配置)、令牌桶限流 |
| 4 | 图像 provider | GptImageProvider 只是 OpenAI 兼容通用 adapter 的薄配置(默认 baseUrl=api.openai.com/v1, 默认模型 gpt-image-2)。真正逻辑在 openai-compatible.ts：POST /images/generations → data[0].url。**换图像厂商 = 改 baseUrl/model/env，不改代码** |
| 5 | 视频 provider | Seedance/Minimax 同样是 OpenAI 兼容 adapter 薄配置。通用 adapter 提交 POST /videos/generations（多模态 content 数组：text+firstFrame+lastFrame+referenceVideo，即图生视频形态），**自动判别同步/异步**：响应带 url=完成；只有 id=异步 job→轮询 GET /videos/{id}。video 走有序 fallback（seedance-2.5 主、minimax-h3 备） |
| 6 | 路由/fallback | 三层：①capability 预检(注册表声明支持时长/尺寸，**未知模型≠拒绝**，声明了却不满足才 skip)；②failover 遍历 [primary,...fallbacks] 逐个 try，记录 attempts[]；③job registry+输入哈希 resume(`sha256(model\|prompt)前12位`写 append-only 事件日志，崩溃回放跳过已完成) |
| 7 | Agent/Provider 边界 | 文本分析/节拍/镜头语言/prompt 编写全由 agent 自身模型做（skill 引导），packages 零 LLM。产物是**符合 schema 的 shots.json**；provider 只消费 imagePrompt/videoPrompt/status。`take validate` 是闸门：校验不过不得 generate |
| 8 | CLI/workflow | 6 子命令 init/validate/generate/jobs/export/doctor。**CLI 是薄壳**：逻辑全在 orchestration.ts/validate.ts，CLI 只解析参数/打印/退出码；MCP 复用同一层→三消费面零重复。并发用"游标+N worker 协程"，无队列库 |
| 9 | MCP/Skill | MCP 暴露 5 工具(validate_shots/generate_images/generate_video/export_storyboard/doctor)，纯函数分发转发到共享层。Skill 的 SKILL.md = YAML 触发条件 + 7 步工作流 + 硬规则；references/ 五件套(workflow/shot-language 受控词表/output-contract 契约/model-routing/examples 质量自检清单) |
| 10 | artifact/output | "everything is a file"：script.md(入)、take.config.json、shots.json(唯一被校验对象)、storyboard.md(人类可读,可 import 回写)、assets/images\|videos/、.take/jobs.json(事件日志)。Markdown 用固定格式**机器可往返**。渲染产物写**指针文件**(内容存 url)，不存二进制 |
| 11 | prompt/workflow 关系 | 分工=思考 vs 渲染。思考类 prompt 全在 skill 层由 agent 模型执行落到文件；渲染类只有远端图/视频生成，由 provider 消费文件的 prompt 字段。衔接媒介=**双份契约**：机器侧 zod schema + 人/agent 侧 output-contract.md。模型 ID 只出现在 config/env，绝不进业务代码 |
| 12 | 值得 V0.2 借鉴 | ①受控镜头语言词表+shot 生命周期+assets 指针(直接决定哪些镜头能渲染)；②imagePrompt/videoPrompt 分离+visual.characterRefs(角色一致性钩子)；③视频异步 job 协议+同步/异步自动识别(单 adapter 覆盖 90% 厂商)；④能力感知 failover+稳定错误码三分类；⑤RetryPolicy 是配置+独立 transport；⑥append-only JSONL 事件日志+输入哈希 resume；⑦validate 质量闸门+跨字段完整性校验；⑧CLI/MCP/Agent 共享同一 orchestration 层；⑨纯 core 层只依赖校验库 |
| 13 | 不适合照搬/自身短板 | TS→Python(Zod→Pydantic)只借鉴思想不拆包；agent-first(零 LLM client)与 V0.2 的 API-driven 冲突，skill 引导机制无法照搬但**产物契约/词表/自检清单可转写成 V0.2 prompt**；dsh 对齐的通用接缝(seam/disposer/注册机制)是过度设计，全套会拖慢开发；take 短板：视频仅轮询无 webhook、weight 路由解析未启用、图像无 fallback、resume 哈希不含参数(改 size/duration 可能不重跑)、MCP 缺 job 工具、无音频/人声模型 |

### 2.3 重点问题结论：Text→…→Video，take 哪些值得借鉴

**结论：借鉴两样东西——(A) 一套"Script→Shot 逐层可校验、带电影语言词表与生命周期的稳定数据结构"；(B) 一个"能力感知 + OpenAI 兼容单 adapter + failover + 异步 job 轮询"的 Provider 层。不借鉴 skill 驱动方式与 dsh 通用接缝。**

落地到 V0.2 的 5 条可操作建议（详见 §10/§9 的正式设计）：

1. `core/models` 新增 Beat/Shot/Storyboard 模型，直接映射 take 层级（`image_prompt` 必填、`video_prompt` 分离、`visual.character_refs` 留角色一致钩子）。
2. 把"能渲染的镜头"定义成数据：shot.status 5 态门控 + assets 指针，image/video prompt 分开存。
3. Provider 层只复刻 5 个构件：抽象接口(kind/model/generate_image/generate_video/health)、**一个 OpenAI 兼容通用 adapter**(images/videos generations 端点，自动判别同步 url/异步 job 轮询)、能力预检注册表、稳定错误码三分类驱动 retry/failover、JSONL 事件日志+输入哈希 resume。
4. 复制"validate 是闸门"的工程纪律与 output-contract 写法。
5. core 层保持"只做结构校验、零 I/O、零 AI"的硬纪律。

> 大白话：take 最成熟的两处，恰好是 V0.2 未来最缺的两处——(1) 从"剧本推理产物"到"渲染输入"之间缺一个带**镜头语言词表和状态机、能被校验门控**的稳定文件契约；(2) 缺一套**用配置而非代码**接入多家图像/视频厂商、能处理异步生成与断点续跑的 Provider 层。take 因为走 agent-first，做不了 V0.2 擅长的"API 驱动的多阶段文本推理编排"——反过来，V0.2 借它的数据结构和 Provider 抽象，正好互补。

## 3. MovieAgent 技术分析

（研究方式：只读通读 movie-Agent 全部源码 `movie_agent/`、`tools/`、`requirements.txt`、README；对应论文 *Automated Movie Generation via Multi-Agent CoT Planning*，arXiv 2503.07314。未修改任何文件。）

### 3.1 一句话定位

MovieAgent 是论文附带的**多智能体端到端电影生成实验代码**（Python）：输入一段电影 synopsis，依次切分 → 场景规划 → 镜头规划 → 图像/视频生成 → 拼接成片。真正的 LLM Agent 只有 3 个（screenwriter / sceneplanning / shotplotcreate），由单个 `ScriptBreakAgent` 编排类**纯串行**驱动，中间产物是**落盘的 JSON 文件**，生成阶段用 ROICtrl（角色 ED-LoRA 训练 + 文生图）+ I2V 模型。

### 3.2 逐问结论（§四 1~8）

| # | 问 | MovieAgent 的答案（精要） |
|---|---|---|
| 1 | Agent 如何协作 | 3 个真实 Agent（同一 `BaseAgent` 包装 OpenAI 客户端）+ 1 个被注释掉的 supervisor。论文宣称的 Director/Location Manager 只是 prompt 里的一句话人设。**纯串行、无反馈、无显式消息**：`ScriptBreak → ScenePlanning → ShotPlotCreate → VideoAudioGen → Final`。每步结果存 `Results/<movie>/Step_N_*.json`，下一步读回、用 f-string 拼进下一条 query。每个 Agent `use_history=False`（无多轮记忆） |
| 2 | Scene Planning | `sceneplanning_agent` 每次处理一个 Sub-Script，输入 Plot + 全片 Relationships，输出写回子节点 "Scene Annotation"。Scene 字段：Involving Characters[] / Plot / Scene Description / Emotional Tone / Visual Style / Key Props[] / Music and Sound Effects / Cinematography Notes。规划树严格**递归嵌套**：Relationships → Sub-Script → Scene → Shot，无全局唯一 id，靠复合路径定位 |
| 3 | Shot/Cinematography | `shotplotcreate_agent` 一次调用产出一个 Scene 的全部 Shot。Shot 字段：Involving Characters(角色名→归一化包围盒)、Plot/Visual Description、Coarse Plot(≤20词且禁含角色名,给图像模型用)、Emotional Enhancement、Shot Type(自由文本枚举)、Camera Movement(自由文本枚举)、Subtitles{角色:台词}。**机位/景别只是文本、无数值**，构图靠归一化 bbox；每镜≤2~3人、bbox不重叠等约束**只写在 prompt、零代码校验** |
| 4 | Character/Location/Asset | **角色不走 LLM 管理**：每电影每角色一个照片库 `character_list/<Name>/`（best.png 基准图 + txt 训练描述），一致性靠**离线训练 ED-LoRA 身份 token**（`<Deadpool1>` 等）绑定进 UNet，非 prompt 文字。**Location/Asset 无结构化注册表**——地点/道具/音效都是 scene 里的纯文本，每次重新发明，不回填任何 asset 库 |
| 5 | Image/Video 接入 | 顶层 `tools/` = 只有 vendored 的 Mix-of-Show（角色 LoRA 训练）。真调度在 `movie_agent/tools.py`：`GenModel/Image2VideoModel/AudioGenModel/TalkingModel` 四个**巨型 if-elif 注册表** + `ToolCalling.sample()`，懒加载 `models/<Model>/` 下的 diffusers pipeline。链路：每镜 ① ROICtrl 文生图（SD1.5+EDLoRA，角色用 roi token+bbox 注入 UNet）→ ② HunyuanVideo_I2V / CogVideoX-5b / SVD / I2Vgen 图生视频 → mp4；`Final()` 用 moviepy 按文件名排序拼接成 final_video.mp4。**生成结果不回填 JSON**（无文件引用），音频/唇形（VALL-E/Hallo2）是半成品/被注释 |
| 6 | Hierarchical Planning | 三层分解由 3 个**独立 LLM 调用**串成：① screenwriter：synopsis→≤20 个 Sub-Script(+顶层 Relationships)；② director：每 Sub-Script→Scenes；③ storyboard：每 Scene→Shots。论文的"逐步、参考上一切分"循环被代码改写为**一次调用出全部**。层间一致几乎全靠 **prompt 文本约束**（角色名禁修饰词、上层 JSON 整体透传、嵌套 dict 物理挂父节点、把 CoT 输出成 JSON 的一部分）。**无任何校验器/引用检查/bbox 合法性检查**，数量上限只是 prompt 约定 |
| 7 | 值得 V0.2 借鉴 | ①分层规划粒度本身（story→Sub-Script→Scene→Shot 是 V0.2 缺的骨架，可做独立 Agent/Step 而非塞进 scene_generation）；②"每子单元一次 LLM 调用、产物累积"（天然控 token/成本，V0.2 的 scene_id 拆法可推广）；③中间产物 JSON 化逐步落盘（V0.2 版本化 Artifact + resume 是它的"正确版"）；④全局上下文复用（Relationships 顶层透传 → 对应 V0.2 把关系写进 Memory + needs 声明式取）；⑤Shot 结构化字段（Shot Type/Camera Movement/bbox/Subtitles/Coarse Plot）→ 直接做成 core Shot 模型 + Validator 确定性规则；⑥角色名强约束 → V0.2 已用 CastingValidator.UNKNOWN_CHARACTER 落地，应扩展到 scene/shot 层；⑦人物照片库即角色 bank → V0.2 角色档案预留 identity 图/音引用字段 |
| 8 | 不应照搬 | **God 编排类**(ScriptBreakAgent 揉规划/读写/生成/剪辑，不可测)、**一棵可变 JSON 树反复覆写**(无版本/并发不安全/失败重跑重复计费)、**prompt 汤**(567 行堆 11 套模板、含自相矛盾约束"每镜≤2人又≤3人""15词又30词"、强制输出 Internal CoT 浪费 token)、**零校验单点失败**(json.loads 失败抛原文、缺键即中断、无 retry/超时/断点续跑)、**vendored 巨型模型树**(models/ 拷整棵 SD/Hunyuan/CogVideoX… 多数残废，requirements 247 个 pin 整机导出)、**角色一致性靠离线训练 LoRA**(非开箱即用,与 V0.2 纯文本 LLM 路线不匹配)、**生成无引用回填+文件名排序即时间线**、**维护度低**(2025-03 研究代码，需 CUDA+大权重) |

### 3.3 重点问题结论：hierarchical planning 如何映射到 V0.2 的 Memory + Context + Workflow

**总体思路：把 MovieAgent 的"一棵可变 JSON 树 + 零校验 + prompt 自约束"，改成 V0.2 的"每层一个版本化 Artifact、父层产出喂子层、跨层允许集用 Validator 硬约束、跨步持久事实进 Memory 且过审核"。** 借鉴"分层粒度 + 角色人设 + 镜头结构化字段"这套**剧本语义**，丢弃它的整套**实现方式**。具体：

**Memory 槽位**（用 `memory/store.py` 现有接口）：
- synopsis / 既定剧情事实 → 拆成离散故事拍点，`add_candidate_fact(category="plot")` → 人审 approve 后进 approved。
- **角色关系**（对应 MovieAgent 顶层 Relationships）→ 属跨场景持久事实，Casting 阶段就作为 `category="character"` fact 写入审批，禁止 scene 步临时现造；story_break 若"发现"新关系必须先走 candidate → review gate。
- 角色清单 → `set_characters`（沿用 stage6 casting 产物），scene/shot 步 `needs=["characters"]` 读取。
- 世界观/视觉风格 → `set_world`，一次写入全局可用。

**Context + Workflow 映射**（新增 3 个业务 Step，均 `needs_review=True`）：
- `story_break`（Screenwriter）：`needs=["project_info","approved_facts","characters"]` → 产出 `script_break.json`（SubScript 列表），Validator 校验角色 ⊆ characters。
- `scene_plan`（Director）：`needs=[...]+get_output("story_break")`，**每 sub-script 一个 artifact**（复用 stage7 scene_id 拆法），Validator 复用 `INVALID_LOCATION/UNKNOWN_CHARACTER`。
- `shot_plan`（Storyboard）：`needs=[...]+get_output("scene_plan_subN")`，**每 scene 一个 artifact**，Shot 模型带 shot_type/camera_movement/bbox/subtitles/visual_desc，Validator 加确定性规则（bbox∈[0,1]、跨度≤0.5、每镜角色数上限、字幕说话人 ⊆ 镜内角色）。

**层间一致性的正确分工**：父子层布局关系（story→sub→scene→shot）是"当次产出的树"，**不走 Memory**，靠"parent artifact 作为 child 输入 + Validator 允许集"保证；Memory 只承担**跨步、跨场景、需人审的持久事实**（角色/关系/世界观/剧情拍点）。

> 大白话：MovieAgent 证明了"导演想什么 → 分几场 → 每场几个镜头"这种**分层拆解的路子是对的**，但它的实现像"一个人把所有草稿堆在一张会变的纸上、还靠嘴（prompt）反复叮嘱别乱写"。V0.2 要学它的"分层思路"，用自己已有的"每层一个文件、旧的永不覆盖、出界就让校验器拦、长期事实进记忆且人工审批"来收。

## 4. 原始 FilmAgent（V0.1）技术分析

### 4.1 定位与结构

V0.1（冻结 tag `v0.1` = `344c0c6`）是**已跑通的串行单线流水线**，位于 `FilmAgent/`，产出 `Script/script/0.json`（3 场景 / 49 镜头）。核心：

- `main.py`：顶层调度，按固定顺序调用各"阶段"；断点续跑（崩溃后从失败处继续）。
- `LLMCaller.py`：对 DeepSeek（base_url/chat 兼容）的最小封装，从 `api_key.txt` 读密钥。
- `Prompt/`：19 个阶段提示词（director_1~9、screenwriter_1~5、actor、cinematographer、shot_review、COT_Prompt 等）。
- `Locations/`：地点（position）资产。
- 阶段顺序：casting → scenes_plan → lines_generate（分集）→ … → clean_script。

### 4.2 技术特征（对照参考项目最有意义的部分）

| 特征 | V0.1 实情 |
|---|---|
| Agent 边界 | 每个"阶段"一个 prompt + 一次 LLM 调用，**角色是"提示词角色"而非独立代码 Agent**，无显式 Agent 抽象 |
| 数据契约 | JSON 手写契约，**无 schema 校验**——曾因编剧自造地点名 "Billiard room kitchenette" 无校验导致运行崩溃（靠人工改数据续跑） |
| 一致性 | 靠 prompt 约束 + 前一步 JSON 全量喂给下一步，无独立校验器、无 Memory 增量 |
| 失败恢复 | 断点续跑（人工），无版本化 Artifact |
| 质控 | shot_review 是唯一"评审"环节，无三态审核 |

### 4.3 结论

V0.1 的**核心价值 = 业务流程路线图**（cast → scenes → lines → shots → …），以及一批可复用的领域命名。它的**工程形态**（无 schema、无校验、单线、人工断点）正是 V0.2 前 8 个阶段逐项补上的东西，无需再向它学工程。

---

## 5. 四者对照表（take / MovieAgent / V0.1 / V0.2）

下表从"对 V0.2 未来最有意义的维度"横向对比四个项目（take / MovieAgent / 原始 V0.1 / 我们 V0.2）。

| 维度 | take（TS） | MovieAgent（py） | 原始 FilmAgent V0.1 | **FilmAgent V0.2（我们）** |
|---|---|---|---|---|
| 项目定位 | agent-first 分镜+渲染工具 | 论文实验：端到端生成 | 已跑通的端到端文本电影 | 可控/可测/可扩展的多 Agent 框架 |
| 分层数据模型 | script→beat→shot→storyboard（Zod） | synopsis→sub-script→scene→shot（嵌套 dict） | scenes→lines→shots（JSON，手写契约） | scene(+line/action/move) 有模型；**无 beat/shot/storyboard** |
| 镜头语言 | **受控枚举** shotSize(7)/angle(8)/movement(14) + shot.status 5 态 + assets 指针 | 自由文本枚举 + 归一化 bbox（零校验） | 自由文本 | （尚无 shot 概念） |
| Agent 组织 | 无 LLM Agent（推理外包给 coding agent） | 3 个串行 prompt 角色 + 1 个 God 编排类 | 每"阶段"=一个 prompt 角色 | 2 个真实业务 Agent（Casting/SceneGeneration，带代码骨架+needs+validator） |
| Provider / 模型接入 | 抽象 seam + 能力感知 failover + OpenAI 兼容单 adapter + 异步 job 轮询 | 巨型 if-elif 注册表 + vendored diffusers 模型树 | LLMCaller 单厂商 | LLMClient（错误家族/重试/mock）+ **空壳 Router（单文本形状）** |
| 数据/产物组织 | everything is a file + url 指针文件 | 一棵可变 JSON 树反复覆写 | 逐步 JSON 落盘 | **版本化 Artifact 永不覆盖** + resume |
| 校验 / 质量闸门 | Zod schema + validate + 跨字段完整性 | **零校验**（缺键即 KeyError） | 零校验（曾因自造地点崩溃） | **Validator 三合一**（Enum/Reference/Schema）+ 确定性拦截 |
| Memory | 无（靠文件与人工） | 无 | 无 | **Memory 三态**（candidate→人审→approved）+ revision 版本 |
| 一致性保障 | visual.characterRefs 角色引用钩子 | prompt 反复念 + 离线 LoRA 训练 | prompt 约束 + 上一步全量喂 | 确定性规则 + Validator 兜底 + 跨集角色复用 |
| 人工质控 | validate 门禁（无人工审核） | 无 | shot_review 单一评审 | **Review 三态**（approved / approved_with_edit / rejected） |
| 失败恢复 | 事件日志 + 输入哈希 resume | 无（把"已存在跳过"注释掉了） | 断点续跑（人工改数据） | Engine retry/resume + SKIPPED + 版本化 |
| 可测试性 | 强（mock adapter） | 弱 | 弱 | **强**（mock transport，各阶段 19~38 项测试全过） |
| 多模态能力 | 图/视频真接入（图像/video provider） | 图/视频接入（但依赖 LoRA 训练+大权重） | 无 | **无**（纯文本，未来接） |

**这张表讲清楚三件事**：
1. **我们的 V0.2 在"工程可测性、Memory、校验、版本化、审核"上已经领先三个参考项目**——take/MovieAgent/V0.1 都没有 Memory、没有三态审核、没有版本化不覆盖，这是我们阶段 1~7 真正的资产，**绝不能在"学参考项目"的名义下倒退**。
2. **我们与 take 的差距集中在两点**：①没有"beat/shot/storyboard + 镜头语言受控词表 + shot 生命周期"这套**结构化中间层**；②没有"多模态 Provider 抽象 + 能力感知路由 + 异步 job"这套**接入层**。这两点正是 §9/§10 要补的。
3. **我们与 MovieAgent 的关系是"语义同源、实现胜出"**：MovieAgent 的"synopsis→scene→shot 分层规划"与我们的未来路线同构，但它的实现（无校验/无 memory/无审核/可变树）恰好是我们已解决的问题；我们要的是它的**分层粒度与角色人设**，不要它的实现方式。

## 6. 当前 V0.2 保留项（A）

### 6.1 逐模块判定总表（对照指令 §五）

| 模块 | 判定 | 一句话理由 |
|---|---|---|
| `core/models.py`（现有 Scene/Line/Action/Move/Character/Casting…） | **A 保留** | 已被真实调用验证、V0.1 双向转换是硬资产；未来加的是"新模型"不是改旧的 |
| `core/validator.py` | **A 保留** | 三合一（Enum/Reference/Schema）正是 take "validate 是闸门"的 Python 版，且已被 6 类错误码真实拦截验证 |
| `core/assets.py` | **A 保留** | 地点/动作/镜头资产注册表是 V0.2 独有优势（take/MovieAgent 都没有资产库） |
| `core/token_log.py` | **A 保留 + 小幅扩展** | JSONL 不记 key/不记 prompt 的设计正确；未来多 provider 记费是增量 |
| `llm/client.py` | **A 保留**（接口），**未来 E** | LLMClient 的错误家族/重试/mock 注入全对；它未来是"TextProvider 的实现"，不是被推翻 |
| `llm/router.py` | **C 借鉴后升级** | 现在是单文本空壳，要升级成 §9 的 ModelRouter 一员 |
| `memory/` | **A 保留** | 三态 + revision 正是 MovieAgent 缺的"跨步持久事实 + 人审"；未来只加 summary 摘要层 |
| `context/` | **A 保留** | 声明式 needs + 11 个 provider 注册表已足够支撑未来所有新 Agent |
| `workflow/` | **A 保留** | Engine/Artifact/Step/状态机是 V0.2 对参考项目最大的优势（MovieAgent 完全无此层）；只预留"异步 job 步骤"扩展点 |
| `review/` | **A 保留** | 三态审核正确；交互式界面是产品化问题，非架构问题 |
| `agents/casting.py` | **A 保留**（行为），**B 抽骨架** | 已真实验证；它的 LLM 调用骨架未来上提共享，避免第三个 Agent 复制粘贴 |
| `agents/scene_generation.py` | **A 保留**（行为），**B 抽骨架** | 同上；未来 Scene 需扩展 shot 规划时是"新增 Step"不是"改这个" |
| `prompts/`（v0.1 19 + v0.2 2） | **A 保留**（结构），**C 补契约** | prompt 与代码分离已做对；改进是补"输出契约/schema"（take 的 output-contract 写法） |
| `config.py` | **A 保留**（安全机制），**E 目录化** | key 安全读取 + 脱敏是铁律，永不丢；PRICING 表未来按 provider 分组 |

### 6.2 判定 A：完全保留项的核心理由

- **core/models + validator + assets + memory + context + workflow + review 这七块是 V0.2 的地基，本报告建议一个都不推翻。** 它们撑起了真实调用验证（Casting/SceneGeneration 各 3 次成功）、跨集一致性、版本化不覆盖、resume。参考项目里没有任何一个具备这套组合。
- **特别的，`validator.py` 的定位与 take 的 "validate 是唯一闸门" 完全同构**——take 把 schema 校验+跨字段完整性作为产出的唯一门禁，V0.2 已经这么做了，方向不需改，只需在未来给 Beat/Shot/Storyboard 加同款规则集。
- **`workflow/` 的引擎在 V0.2 之外没有更好的替代**：MovieAgent 无此层、take 的 job/resume 是它的简化（甚至 resume 哈希还有缺陷）。继续保留并作为所有未来 Step 的底盘。

## 7. 当前 V0.2 重构项（B：小幅重构；C：借鉴参考项目后重构）

### 7.1 B 小幅重构（不动行为，为"第三个业务 Agent"铺路）

| 项 | 重构内容 | 为什么现在做 |
|---|---|---|
| **Agent 公共骨架抽取** | casting 与 scene_generation 各自手写了 `_parse_json`（剥 json 围栏）与 `_normalize`/`run` 模板；抽一个共享基类/混入（如 `LLMBasedAgent`：持 validator + `run()` 调 LLMClient → 解析 → `_normalize` → validate → 交 StepOutput），两个现有 Agent 改继承，行为与测试不变 | 阶段 9+ 会加 story_break / scene_plan / shot_plan 多个 Agent，不抽就会复制第三/四/五份；**先抽骨架再加新 Agent**，避免一次大爆炸重构 |
| **Agent 与输出 schema 的绑定** | 每个 Agent 声明"我产出哪个 core 模型 + 交哪个 Artifact 名"，把目前隐式约定显式化 | 与 §10 Canonical Schema 配套，让 Validator 按 schema 自动校验，少写手写规则 |

### 7.2 C 借鉴参考项目后重构

| 项 | 借鉴谁 | 重构内容 | 时机 |
|---|---|---|---|
| **llm/router.py → ModelRouter 雏形** | take（能力感知 failover） | 保留 Router "任务类型→候选模型"的接口，但返回值从"单个模型名"改为"候选列表 + 能力要求"，为 §9 的 Provider 层留入口；**不实现智能路由** | 随 §9 一起，阶段 8 做 |
| **prompts/v0.2 → schema 驱动** | take（output-contract.md + zod 契约双份） | 每个业务 Agent 配一份"输出契约"参考（必填字段/词表/自检清单），与 core 模型 schema 对应；prompt 正文里删掉与词表矛盾的描述（参考 MovieAgent 的 prompt 矛盾教训） | 阶段 8 写 Canonical Schema 时同步固化 |
| **config.py → per-provider 配置目录化** | take（config v2：模型 ID 只出现在 config，不进代码） | PRICING/base_url/model 从"DeepSeek 一张表"改成"按 provider/能力分组"，但 `load_api_key/mask` 安全机制原样保留 | 随 §9 一起 |
| **scene_generation 未来对齐 Scene/Shot** | MovieAgent（分层粒度）+ take（shot 词表） | 不重写 scene_generation；未来新增 `shot_plan` Agent 时，scene 产物作为 shot_plan 的输入（parent artifact 喂 child），Scene 模型加 shot 引用字段 | 阶段 10 做 shot 规划时 |

### 7.3 重构的三条纪律（防止"为了漂亮而重构"）

1. 重构必须**零行为变化**：现有 19/19、38/38、25/25、24/24、29/29 测试套件全部照旧通过；抽骨架后 stage6/stage7 真实脚本不回归。
2. 一个阶段只做一类重构，且**每类都先出测试再动手**。
3. 任何模块只要被判了 A（保留），就不为"对称/美观"去动它——参考项目里 MovieAgent 的"一棵可变树 + 无版本"恰恰是被我们判 A 的模块所解决的病，别倒退。

## 8. 当前 V0.2 停止自行开发项（D）

以下四类，**结论是"别再自己从零造"，改为借鉴参考项目或等待平台能力**：

| 停止项 | 借鉴/等待谁 | 原因 |
|---|---|---|
| **镜头语言词表与 shot 状态机** | 直接采用 take 的受控枚举（shotSize 7 景别 / angle 8 角度 / movement 14 运镜 / status 5 态 / assets 指针），只补电影常识缺失项 | 这是**行业既成词汇**，自己发明一套没有价值，还会让未来图像/视频 prompt 无法对齐主流模型的语言 |
| **图像/视频厂商 SDK 逐一封装** | take 的"OpenAI 兼容通用 adapter + 配置接入"思路；等阿里云百炼的图像/视频兼容端点 | MovieAgent 把 SD/Hunyuan/CogVideoX 整棵 vendored 进仓库的教训：重、残废、锁死。V0.2 要的是"改 base_url/model/env 就换厂商" |
| **角色图像身份一致性（LoRA 训练路线）** | 停止本地训练方案；未来交给百炼/第三方平台的"角色引用/参考图/身份绑定"能力 | MovieAgent 需每电影每角色离线训 ED-LoRA + 大权重，非开箱即用。V0.2 现阶段纯文本，不应为此搭 GPU 训练管线 |
| **Router 的"智能路由"（按质量/成本自动选模型）** | 等真实 token/质量数据积累 | 无数据支撑的自动路由是玄学（v2/router.py 注释早已言明）；先把"路由的壳"立住，策略永远后置 |

> 大白话：这四类东西不是"现在做不了"，而是"自己发明 = 帮别人写轮子 + 以后还得拆"。能抄行业现成词表就抄（1），能用配置接平台就别写死代码（2/3），没数据就只立接口不填策略（4）。

## 9. 建议新增的 Provider 抽象（E 类 + 未来）

### 9.1 边界结论：Agent → Provider → Model 适合 V0.2 吗？

**适合，而且是 V0.2 未来唯一能"不回头"的多模态方案。** 依据：

- MovieAgent 反面教训：Agent 直接"巨型 if-elif 选模型 + vendored 模型树"，导致换厂商=改代码、缺能力=崩、不可测。它缺的正是这一层。
- take 正面经验：业务层只依赖能力接口，adapter 只管"协议翻译"，failover/router 只按稳定错误码走。换图像/视频厂商 = 改 config，不改任何业务代码。
- V0.2 现状：Agent 目前直接调 `LLMClient`——因为现在只有一种文本能力，还能接受；**但一旦第三个 Agent 要"看图"或"出图"，如果不先立 Provider 层，就会开始把"厂商知识"漏进 Agent。**

推荐分层：

```text
业务 Agent（只依赖"能力接口"，不认识任何厂商类）
      │ 调用
      ▼
┌─────────────────────────────────────────────────┐
│ Provider 层：按"能力"分组，不按"厂商"分组          │
│   TextProvider · VisionProvider · ImageProvider   │
│   VideoProvider · AudioProvider · TTSProvider      │
└─────────────────────────────────────────────────┘
      │ 内部：一个实现可以只实现一种能力，也可多种
      ▼
┌─────────────────────────────────────────────────┐
│ ModelRouter（升级现有 llm/router.py）              │
│   能力 → [候选 provider 有序列表] → failover        │
│   能力预检：声明了却不满足才 skip（未声明不拦）      │
└─────────────────────────────────────────────────┘
      │
      ▼
  transport（retry / 限流 / 超时 / 记费 / 脱敏）      ← 复用 llm/client 的骨架
  config（per-provider：base_url / key_env / model / pricing）
```

### 9.2 六种能力接口该不该一次做全？

**不该。** 结论：**先落地 `TextProvider`（把现有 `LLMClient` 包成第一个能力实现），其余五种只立接口签名 + mock，不实现真实厂商。**

理由：V0.2 现在真实的只有文本；把 Vision/Image/Video/Audio/TTS 的"接口"先定死（形状定了，实现才有方向），但真实接入要等对应阶段（阶段 11 图像、12 视频、13 音频/TTS）且以百炼兼容端点为前提。**一次性把 6 个真实现都写出来，就是 MovieAgent 式的大爆炸。**

推荐的**最小骨架**（全部可 mock、可单测）：

```python
# 伪代码，阶段 8 落地时细化
class Provider(Protocol):           # 所有 Provider 的公共形状
    kind: Capability                # "text" | "vision" | "image" | ...
    name: str
    model: str
    def health(self) -> bool: ...

class TextProvider(Provider):
    def chat(self, req: TextRequest) -> TextResult: ...   # ≈ 现有 LLMClient.chat
class VisionProvider(Provider):
    def understand(self, req: VisionRequest) -> VisionResult: ...  # 看图→结构化结论
class ImageProvider(Provider):
    def generate(self, req: ImageRequest) -> ImageResult: ...      # 文/图→图
class VideoProvider(Provider):
    def generate(self, req: VideoRequest) -> VideoJob: ...         # 返回 job（可能异步）
    def poll(self, job_id: str) -> VideoJobStatus: ...             # 异步完成时轮询
class AudioProvider / TTSProvider: ...
```

### 9.3 五个必须从 take 复刻、但要"裁剪版"的构件

1. **一个 OpenAI 兼容通用 adapter**（价值最高）：`POST {base_url}/images/generations`、`/videos/generations`、`/chat/completions`，支持多模态 content 数组（图/视频帧传 reference）。响应带 url=同步完成、带 id=异步 job 轮询。**百炼 / DashScope / DeepSeek / OpenAI 兼容端点全被它覆盖——换厂商 = 改 config。** Python 用 httpx + 现有 mock transport 注入。
2. **稳定错误码三分类**：在现有 `LLMError` 家族上扩展为跨模态稳定码（RATE_LIMIT / TIMEOUT / NETWORK = 可重试；400/401/404/UNSUPPORTED = 不可重试→切 fallback；NO_PROVIDER = 致命→停机报错）。V0.2 已有 `retryable` 概念，extend 而非重写。
3. **能力预检注册表**：模型元数据声明"支持多大尺寸/多长时长/哪种图生视频"，请求声明不满足就 skip 候选（未知模型不拦）。避免空耗一次 API 才发现不支持。
4. **配置化 RetryPolicy + 独立限流**：RetryPolicy（maxRetries/backoff/jitter）是 config 不是代码；每 provider 一个令牌桶防批量 429（Python 可用 tenacity，或沿用现有手写重试）。
5. **异步 job 的事件日志 + resume**：视频等长任务提交为 job，进度写 append-only JSONL；崩溃后按 `sha256(model|provider|req参数)` 回放跳过已完成。**修正 take 缺陷：把 size/duration/参考图 hash 进指纹**（take 只 hash model|prompt，改参数可能不重跑）。

### 9.4 关键设计原则（写进 DEVELOPMENT 的纪律）

- **业务代码里禁止出现具体模型 ID / 厂商域名**——只许出现在 `config.py`（per-provider 块）。V0.2 现状少量出现在 config/PRICING 是对的，未来保持。
- Agent 与 Provider 之间走**能力接口 + 稳定错误码**，Agent 不 catch 厂商特定异常。
- **TextProvider 落地时，`LLMClient` 保持原样**：stage2~7 的测试与 mock transport 全依赖它；TextProvider 是"包在它外面的一层薄壳 + Router 的候选注册"，不动内核。
- 真实 provider 一律可 mock（延续 mock transport 注入）；日常回归 0 计费。

### 9.5 ModelRouter 升级路径（从"一张表"到"能力感知"）

| 现在（llm/router.py） | 未来（ModelRouter） |
|---|---|
| `get_model(task_type) -> "deepseek-chat"` | `resolve(capability, request) -> Provider`（候选列表 + failover 已包在 resolve 内） |
| 返回单个模型名 | 返回"Provider 实例 + 使用的 model" |
| 无能力概念 | `capabilities: Text/Vision/Image/Video/Audio/TTS` 各一组 provider 注册表 |
| 策略后置（注释已言明） | **策略仍后置**：候选顺序进 config；自动"质量/成本选型"继续等数据 |

## 10. 建议新增的 Production Schema（Canonical Schema）

### 10.1 设计五条总原则

1. **内容链 = 父产子、层层收紧**：`Project → Episode → Scene → Shot`。父层产物是子层的"允许集"，子层只准引用、不准新建（V0.2 已成立的规则，向上推广）。
2. **Shot 是渲染的原子单元**：一切图像/视频/音频都挂在 shot 上；一个 shot 的结构稳定下来，图像/视频阶段的输入就天然定了。
3. **Beat 是"可选规划层"，不强制绑 shot**：给导演/编剧智能体留的"叙事节奏"产物，不要求每 beat 必须落 shot。
4. **Storyboard 是独立 Artifact，不是 Scene 的属性**：它是"某批 shot 的可视化决策快照"（图 + 风格 + 角色引用 + 布局），可人工检视、可导出、可回写。
5. **Asset 存"指针与元数据"，永不存二进制**；**LLM 输出永远先经"输出契约"，过 Validator 才转 core 模型**（防污染，见 10.5）。

### 10.2 推荐分层图（新增项标 ✚）

```text
Project（已有 Project/World，保留）
└─ Episode（已有，保留）                         ── 一集
   ├─ ✚ Beat[]      （可选规划层，导演节奏）      ── "叙事段"（一次规划 Agent 的产物）
   │     叙事推进到哪 / 情绪目标 / 涉及角色 / 关联 scene
   └─ Scene（已有 Scene，保留 + 小幅加字段）      ── 一场
       ├─ Line[] / Action[] / Move[]（已有）     ── 对白与动作
       └─ ✚ Shot[]（从 V0.1 的"镜头指令"升级）   ── 渲染原子单元
              shot_type / camera / duration / characters_layout[]
              image_prompt(必填) / video_prompt? / subtitles?
              status(draft|approved|rendering|done|failed)
              ✚ assets{image?/video?} 指针
   ✚ Storyboard（独立 Artifact）                 ── 一批 shot 的可视化快照
   ✚ Asset 家族（指针/元数据，不存二进制）         ── 见 10.3
   ✚ Timeline（派生组装视图，见 10.4）            ── 从 scenes/shots 派生
```

> 大白话：现在我们的"剧本"只到 **Scene**（一场戏里有台词、动作、走位）。未来要出图出视频，必须往下再挖一层到 **Shot**（一个镜头）——因为一张图/一段视频对应的是一个镜头，不是一个场。这一层我们目前没有，是阶段 8~10 要补的"最小缺失件"。Beat 和 Storyboard 是"导演思维"与"可视化交接"的辅助层，优先级低于 Shot。

### 10.3 逐问结论（§六 1~8）

**Q1 哪些属于 core/models.py，哪些独立模型？**
- 进 `core/models.py`：`Project / Episode / Scene / Shot / Beat / Line / Action / Move / Character / CharacterRef`。它们是被生成与校验的**内容实体**。
- **独立模型（新建模块 `core/assets.py` 扩展或 `core/media.py`）**：`Asset / ImageAsset / VideoAsset / AudioAsset`（存引用不存内容）；`Timeline / Track`（组装层，从内容派生）；`Render` 建议放**运行记录域**（见 Q6），不入 core 内容模型。

**Q2 Scene 和 Shot 如何建立关系？**
- 1:N 父子，`Scene.shots: List[Shot]`（或 scene → shot_list artifact 引用，二选一；建议**用 Artifact 引用而非内嵌**，保持 scene 产物可独立审核）。Shot 用 `scene_id + index` 定位（延续 stage7 的 `scene_1` 命名法），`index` 从 1 连续、`beat_id` 可选悬空检测（take 的完整性规则）。

**Q3 Beat 是否应该独立存在？**
- **应该独立，但作为"可选规划层"**。对应 MovieAgent 的 sub-script：一次 `story_break` Agent 把 episode 切成 beats，每 beat 是连贯叙事段；随后每个 beat 产出 1~N 个 scenes。**不是每 beat 必须落 shot**。价值：给"剧本切分/规划"一个独立可审核的产物（stage9 的 Director/Story Planner 用），避免又回到"一切塞进 scene_generation"。

**Q4 Storyboard 是 Scene 的属性还是独立 Artifact？**
- **独立 Artifact**。理由：它跨 scene（一个 storyboard 快照常含多个 shot、甚至多个 scene），有自己的人工检视/批准生命周期，且要导出成人类可读版（take 的 storyboard.md 可 import 回写）。Scene 只有 `storyboard_refs: List[str]` 之类的弱引用。

**Q5 Image / Video 是否属于 Asset？**
- **属于，Asset 是唯一入口**。`ImageAsset{asset_id, kind, shot_id, url_or_path, prompt_ref, width, height, status}`；视频同理（多一个 `duration_sec`、`first_frame_asset`）。**Asset 记录的是"这个文件在哪、由哪个 prompt 来的、对应哪个 shot、渲染状态"，不是文件本身**（V0.2 的 Artifact 指针哲学 + take 的 url 指针文件）。
- 区分两类：**产出 Asset**（生成结果：某 shot 的关键帧/短片）与**引用 Asset**（输入一致性钩子：角色形象 / 场景参考 / 风格参考 / 声音样本）。后者挂在 shot 的 `character_layout` / scene 的 `style_refs` 上。

**Q6 Render 放哪？**
- **运行记录域，不放内容模型**：`Render` 是一条"渲染任务单"（timeline 或 shot 批 → provider → job_id → 状态 → 输出 asset）。它由 Workflow/调度层产生，状态进 V0.2 已有的事件日志/Artifact 体系。**别把"一次执行"混进"剧本内容"**（这正是 MovieAgent 文件名排序即时间线的混乱根源）。

**Q7 如何保持 V0.1 兼容？**
- 纯新增，不拆不删：Beat/Shot/Storyboard/Asset/Timeline 在 V0.1 数据里**根本不存在**，不需要 from_v01。
- `Scene` 若加 `shots`/`storyboard_refs` 字段，**序列化时 V0.1 方向剥离这些字段**（V0.1 的 scene 不含镜头列表；镜头指令在 V0.1 的 shot 块里）。老 `script/0.json`（3 scene/49 镜头指令）未来做一次"V0.1 镜头指令 → 结构化 Shot"**迁移工具**（独立小模块 + 单测，进未来阶段）。
- 铁律不变：**V0.1 兼容逻辑只在边界层**（`core/models.py` 的 from_v01/to_v01、`core/io.py`），不散落业务代码。

**Q8 如何避免未来模型输出污染核心模型？**
- **核心模型是"用户侧契约"，只能由我们改；LLM 的输出永远先进"Agent 输出契约"（Raw 形态），过 Validator 白名单后由映射函数转成 core 模型落盘**。V0.2 的 scene_generation 已经是这个模式（LLM JSON → `_normalize` → validate → `Scene`）；把它上升为纪律：
  1. 每个生成 Agent 配一份 **输出契约 schema**（prompts 契约文件 + 可选的 agent 层 schema），**字段白名单制**：core 模型里没有的字段一律丢弃，而不是加进模型。
  2. **想给模型加字段 = 先改契约 + 加迁移/兼容分支 + 跑回归**，禁止"为了让某次 LLM 输出入库"顺手加字段。
  3. take 用单一 zod schema 解决；我们双份（输出契约 + core 模型）靠映射函数收敛，代价是每个 Agent 一段 `_normalize`，收益是 core 模型永远稳定——**这是 V0.2 现在已经在付的代价，值得。**

### 10.4 新增模型字段清单（阶段 8 实现时细化）

| 新增/调整 | 关键字段（粗） |
|---|---|
| ✚ `Shot` | shot_id, scene_id, beat_id?, index, duration_sec, shot_type(枚举), camera(枚举/数值), summary, characters_layout[], image_prompt, video_prompt?, subtitles?, status, assets{image?,video?}, notes? |
| ✚ `Beat` | beat_id, episode_id, index, summary, purpose, emotion?, scene_refs[] |
| ✚ `CharacterRef` | character_id, face/body_region(用于构图/一致性) |
| ✚ `Storyboard` | board_id, episode_id, title, aspect_ratio, style?, shot_ids[], character_refs{角色→url}, source, content(markdown 可回写) |
| ✚ `Asset`（基类）/ `ImageAsset`/`VideoAsset`/`AudioAsset` | asset_id, kind, shot_id?, url_or_path, prompt_ref, status, width/height 或 duration_sec, first_frame_asset?(视频) |
| ✚ `Timeline`/`Track` | timeline_id, episode_id, shots 序 + 音频轨 + 字幕轨（派生视图） |
| 调整 `Scene` | 保留全部现有字段 + from_v01/to_v01 剥离新加字段 |

## 11. Text / Vision / Image / Video / Audio 模型职责划分

核心思路一句话：**"一切需要推理的事交给文本模型，一切生成画面/声音的事交给各自的生成模型，一切'检查生成结果'的事交给视觉模型。"** 谁擅长什么就干什么，不混岗。

| 能力 | 谁当主力（候选） | 在 V0.2 承担什么 | 何时真正启用 |
|---|---|---|---|
| **Text** | DeepSeek（deepseek-chat）| 一切推理：story_break / scene_plan / shot_plan 规划、对白（Line）生成、图像/视频 prompt 起草、Casting 跨集决策、Review 理由起草 | **已在用**（阶段 6/7） |
| **Vision（VLM）** | Qwen-VL / 百炼多模态 | 看图出结论：①"这帧图像符合 shot 描述吗"（画面质检）；②"角色长相与形象档案一致吗"（一致性检查）；③"构图/景别是否命中 shot_type"（摄影合规） | 阶段 11 图像跑通之后，作为**图像验收环节**（Validator 的"人眼"版） |
| **Image** | Qwen-Image / 百炼图像端点 | 每个 shot 生成关键帧（storyboard/图像资产）；可图生图改风格 | 阶段 11 |
| **Video** | 百炼视频端点（或 Seedance 兼容）| 每个 shot 关键帧做图生视频短片（首帧=图像资产） | 阶段 12 |
| **Audio** | 音乐/BGM/音效模型 | 场景配乐/氛围音（可选、后置） | 阶段 13（可选） |
| **TTS** | 语音合成 | Line 台词 → 角色语音（需角色声音档案）| 阶段 13 |

**三条职责纪律**：
1. **Text 不越界去"画"**：图像 prompt（`image_prompt`）由 Text 模型起草，但**成像**永远是 Image provider 的事——文本模型只描述，不渲染。
2. **VLM 不越界去"生成"**：VLM 只做质检/描述/比较，不让它直接出图；它产出的是一份"结构化校验报告"，喂回 V0.2 的 Validator/Review 决策（如 VLM 判"角色不一致"→ REJECTED/回炉）。
3. **Audio/TTS 在文本管线未闭环前不启动**：对白都还没稳定，先不追语音。

## 12. 阿里云百炼接入建议

**总原则（指令 §八 原话采纳）：「百炼是 Provider，不是 FilmAgent 的核心架构」。** 不因为用了百炼就把 v2 写死成百炼专用。

| 问（§八） | 建议 |
|---|---|
| 1. DeepSeek 继续承担什么 | **文本推理主力，原样保留**。DeepSeek 在阶段 6/7 已证明对剧本类任务够用且极便宜（两次真实跑合计约 ¥0.07）；`llm/client.py` 的 base_url/key/pricing 机制不动。百炼与 DeepSeek 各自独立 key（config per-provider），DeepSeek 不"迁入"百炼 |
| 2. Qwen / Qwen-VL 适合什么 | **Qwen-VL（多模态）**：图像质检/一致性检查（§11 的 VisionProvider 主力）；Qwen 文本系可在"长中文剧本规划/大场次 story_break"做 DeepSeek 的**文本备选或补充**（同 TextProvider，Router 候选里多一个）。**不建议现阶段就 Qwen 全面替换 DeepSeek——没有数据证明更优，先留着当 fallback/对照** |
| 3. VLM 何时加入 | **图像管线跑通之后（阶段 11 后半），作为"图像验收"环节加入**。过早加 VLM = 在没有图像产物的阶段空转。它进的不是"内容生成链"，是"质检链"（Validator/Review 的补充） |
| 4. Image Gen 怎么接入 | 走百炼图像兼容端点，经 **OpenAI 兼容通用 adapter**（§9.3.1）接入；**不自己写百炼专属 SDK 分支**。ImageProvider 的请求模型（prompt/size/n/reference）已在 §9.2 立住 |
| 5. Video Gen 怎么接入 | 同样走百炼视频端点 + OpenAI 兼容 adapter；用 §9.3.5 的**异步 job 协议**（提交→轮询），handle 图生视频（首帧=ImageAsset） |
| 6. 是否让百炼成唯一 Provider | **否**。原因：单点绑定违背"可切换"目标；DeepSeek 文本已证明好，何必换；百炼若涨价/限流/改政策，V0.2 要能立即切走。**百炼 = 多模态的默认实现 + 首选实验场**，不是唯一 |
| 7. 如何保证以后可切换 | 靠四件套，全部与厂商无关：①Agent 只依赖能力接口（§9.2）；②模型 ID / base_url / key / 价格**全部进 config per-provider 块**（key 仍走安全读取 + 脱敏，绝不硬编码）；③OpenAI 兼容 adapter 让"换端点 = 改 config"；④Router 的候选 provider 是**有序列表 + failover**，某家挂了自动切下一家 |

**接入节奏建议（只做架构承诺，不当场实现）**：
- **现在（阶段 8 前）**：什么都不接。只把 config.py 改造成"per-provider 目录化"的准备姿势（key 安全机制原样保留）。
- **阶段 11 接 Image、12 接 Video 时**：先 mock provider 全通过，再最小真实调用（沿用 stage6/7 的"先 mock 后最小真实 + 每步记 request_id/token/cost"纪律），真实 key 只存环境变量，绝不入库。
- **凡是新增真实 API 的接入，单独成脚本、单独成测试目录，日常回归 0 计费**（V0.2 已固化的规则延续）。

## 13. 阶段 8 ~ V1.0 新路线（含对候选路线的增删改判断）

### 13.1 对候选路线的四类改动（不机械接受）

| 改动 | 候选路线 | 建议 | 理由 |
|---|---|---|---|
| **拆分 1** | 阶段 8 一个大阶段 | 拆成两个：**8 = Canonical Production Schema（数据层）**、**9 = Provider Architecture（接入层）** | 一个是"剧本数据结构契约"，一个是"模型接入执行架构"，**互不耦合**；每个都是可单测的地基，拆开才能各自验收。混在一起做完再验收，出错难定位 |
| **拆分 2** | 阶段 10 = Beat/Shot/Storyboard | 拆掉 Storyboard，只留 Beat+Shot 分两步 | Storyboard 是"可视化交接物"（要图像后才存在），塞进纯文本的 Beat/Shot 阶段是空转；把它并入图像阶段做输入。Beat 规划（含 scene 拆解）与 Shot 规划是**两个不同粒度的 Agent**，也应分两步 |
| **顺移并改名** | 原阶段 9 Director/Story Planner | 顺移为阶段 10，职责聚焦为 **"episode → beat（含 scene 拆解）"文本 Agent**（story_break） | 必须先有 §8 的 Beat 模型才有这个 Agent；它和 shot 规划粒度不同 |
| **合并** | 原阶段 11 "Vision+Image" | 保留 Vision 但**明确定位为图像质检环节**，不做独立阶段 | VLM 是"验收者"不是"生产者"，脱离图像产物单独开阶段无意义；嵌进图像阶段做 check |
| **新增小步** | （候选没有） | 阶段 8 内包含"Agent 公共骨架抽取 + 输出契约 schema 驱动"子包 | 加第三个业务 Agent 前必须先抽共享骨架（§7.1），否则 story_break/scene_plan/shot_plan 会复制第三份模板 |

### 13.2 推荐的新路线（阶段 8 ~ V1.0，共 18 个阶段）

> 方括号里是"该阶段完成后 V0.2 第一次具备的能力"。

- **阶段 8 · Canonical Production Schema + Agent 骨架**（纯本地，0 真实 LLM）——新增 Shot/Beat/Storyboard/Asset/Timeline 模型 + Validator 规则集 + 场景/scene 模型加 shots 引用字段的兼容；抽 LLMBasedAgent 公共骨架 + 每 Agent 输出契约文件。 **[V0.2 数据结构第一次覆盖到 Shot，够未来出图出视频]**
- **阶段 9 · Provider Architecture**（mock，0 真实多模态）——TextProvider 落地（薄壳包 LLMClient）+ 能力接口（Vision/Image/Video/Audio/TTS 立接口 + mock）+ ModelRouter 升级 + config per-provider 目录化 + OpenAI 兼容 adapter 骨架。 **[Agent 第一次"只认识能力，不认识厂商"]**
- **阶段 10 · Director / Story Planner**（第三个真实业务 Agent：`story_break`）——episode → beats 文本 Agent（needs=approved_facts/characters/world + get_output），Validator 拦未知角色/地点。 **[第一次把"一场以上的故事规划"自动化，跨场一致性有落点]**
- **阶段 11 · Shot Planner**（第四个真实业务 Agent：`shot_plan`）——scene → 结构化 Shot[]（复用 §8 Shot 模型 + §9 骨架），每 scene 一个 artifact。 **[第一次产出可渲染的镜头清单；图像/视频的输入就此定死]**
- **阶段 12 · Vision + Image Pipeline**——Storyboard 化（approved shots → storyboard artifact + 每 shot image_prompt）→ ImageProvider（mock 先行 → 最小真实百炼调用）→ ImageAsset 落盘 → **VLM 质检环节**（构图/一致性 → 回 Review 决策）。 **[V0.2 第一次产出真实图像；第一次用多模态真实 API]**
- **阶段 13 · Video Pipeline**——ImageAsset 首帧 → VideoProvider 图生视频（异步 job + 事件日志 + resume，mock→最小真实）→ VideoAsset。 **[第一次产出短视频片段]**
- **阶段 14 · TTS + Audio**——Line 台词 + 角色声音档案 → TTSProvider（mock→真实）→ AudioAsset；BGM 可选。 **[第一次有声]**
- **阶段 15 · Timeline + Film Assembly**——scenes/shots/音轨/字幕轨派生 Timeline → 按序组装（参考 take 事件日志与 MovieAgent 排序教训：**用显式轨道，不用文件名排序**）。 **[第一次整片拼接]**
- **阶段 16 · Multi-Agent Director / Judge**——质量层：VLM 逐镜质检 + 文本 Judge 复盘 + Review 决策回灌 Memory。 **[第一次有闭环自评]**
- **阶段 17 · 第一集端到端真实生成**——单集 Text→…→Video 全链路真实跑（沿用"先 mock 后最小真实 + request_id/token/cost 记账"纪律）。 **[第一个可展示的成片 demo]**
- **阶段 18 · V0.2 Release / Evaluation**——对照 V0.1 与参考项目做质量对比、文档、可能的 commit/tag。 **[V0.2.0-alpha]**

### 13.3 为什么保持"每阶段停 + 人工验收"

阶段 8~18 中有 6 个阶段要**真实花钱**（12/13/14/16/17 至少涉及真实多模态调用，图像/视频单价远高于文本，一次真实跑可能从 ¥0.01 量级跳到 ¥1~10 量级）。继续沿用"每阶段独立开工指令 + 完成后停下等人工验收 + 不做不完不叠加"的节奏，是**成本与失控的唯一刹车**。候选路线整体成立，本报告只改粒度与顺序，不改节奏。

## 14. 风险与技术债

按"越早越要处理"排序：

| 风险/债 | 说明 | 缓解 |
|---|---|---|
| **v2 至今未 commit**（git 里只有 `?? v2/`） | 阶段 0~7 的全部成果没有版本历史回退点；一旦阶段 8 重构出错无法回滚 | **进入阶段 8 前，先提交一个"阶段 0~7 完成"基线 commit**（供用户决定；这也是指令级"等验收"的意义） |
| **core/models 将两代并存** | 现有 V0.1 兼容模型 + 新 Shot/Beat/Storyboard/Asset；from_v01/to_v01 会变厚 | 铁律不动：兼容逻辑只在边界层；新模型纯新增不拆旧的；每加字段=先改契约+回归 |
| **图像/视频成本跳升** | 单次真实图像/视频调用可能从 ¥0.01 量级跳到 ¥1~10 量级，且异步长任务 | 每个真实接入阶段独立、先 mock 全过再最小真实；job 事件日志 + resume；request_id/token/cost 每步记账 |
| **多 provider 后 config/token_log 复杂化** | 各厂商计价/缓存/限流规则不同，记费算错会误导决策 | config per-provider pricing 目录化；token_log 加 provider/能力维度；真账以平台为准的注释延续 |
| **prompt 膨胀与自相矛盾** | MovieAgent 567 行 11 套模板含矛盾约束的教训 | 输出契约 + schema 驱动（§7.2）；每 Agent 独立 prompt 文件；静态检查扩展到"扫矛盾约束" |
| **角色图像一致性是行业难题** | VLM 质检只能"判不一致"，根治需 LoRA/参考图绑定，成本高 | 期望管理：先做到"角色有形象档案 + 参考图 + VLM 抽检"，不强求像素级一致；阶段 12 再定 |
| **scene_generation 与未来 shot 链并存** | 旧的单场文本 Agent 与新的 shot/图像链如何平滑过渡 | scene 产物作为 shot_plan 的输入（parent 喂 child），不重写 scene_generation |
| **新增 media/assets 模块挑战分层纪律** | 依赖方向表会新增"media"等模块，防反向依赖 | 新模块先定层再写 import（如 media 放 core 边界，Provider 放 llm 上层） |
| **take 的 resume 指纹缺陷** | 它只 hash model\|prompt，改参数可能不重跑 | 我们做视频 resume 时把 size/duration/参考图 hash 进指纹 |
| **路线长（11 阶段）易积压半成品** | 每阶段若不开独立验收就叠加，失控 | 沿用"每阶段停 + 人工验收 + 不自动进入下一阶段"节奏 |

> 一句话：**最大的风险不是"设计不够好"，而是"没有版本基线就往下走 + 真实多模态钱失控"。两个都靠纪律（先 commit 基线、真实调用 mock 先行最小化）解决，不需要新发明。**

## 15. 最终架构建议

**V0.2 目标架构（阶段 8~12 渐进成型，非一日建成）：**

```text
┌─ 内容域（core，纯数据+校验，零 I/O 零 AI）─────────────────────────┐
│  Project → Episode → (Beat 规划层) → Scene → Shot[]（渲染原子单元） │
│  Storyboard / Asset(指针) / Timeline(派生)                         │
│  Validator：Enum + Reference + Schema，覆盖到 Shot 的确定性规则     │
└─────────────────────────────────────────────────────────────────────┘
   ▲ 产出                     ▲ 引用/查 Memory
┌─ 业务域（agents，只做一件事，只依赖能力接口）─────────────────────┐
│  casting → story_break → scene_generation → shot_plan → ...      │
│  公共骨架 LLMBasedAgent：调能力→解析→_normalize→Validator→交产物   │
└─────────────────────────────────────────────────────────────────────┘
   ▲ needs 声明
┌─ 支撑域 ───────────────────────────────────────────────────────────┐
│  Memory(三态) · ContextAssembler(needs) · Workflow(Step/Artifact/  │
│  Review/retry/resume) · Provider 层(Text 落地，其余接口+mock)       │
│  ModelRouter(能力→候选 failover) · config(per-provider,key 安全)     │
└─────────────────────────────────────────────────────────────────────┘
```

**五条最终架构判断（可写进 DEVELOPMENT.md 的纪律）**：

1. **阶段 0~7 的七块地基（models/validator/assets/memory/context/workflow/review）全部保留，一个不推翻**——它们是 V0.2 相对三个参考项目真正的领先项，学参考只补缺口不倒退。
2. **未来全部内容按 `Project→Episode→Scene→Shot` 分层，Shot 是渲染原子单元**；Beat/Storyboard/Timeline 都是辅助/派生，不抢占渲染主干。
3. **模型接入走 `Agent → 能力接口 → ModelRouter → transport/config`，业务代码永不出现模型 ID/厂商域名**；百炼是默认多模态 Provider 之一，不是架构。
4. **"LLM 输出 → 输出契约 → Validator → core 模型"这条污染隔离链成为硬纪律**：core 模型只能由我们改，LLM 永远进不来定义字段。
5. **节奏不变：每阶段独立开工指令、mock 先行、最小真实、完成后停等人工验收、不自动 commit/tag/进下阶段。**

## 16. 现在应该做什么 / 不应该做什么（明确结论）

**本阶段（侦察）应做**：✔ 已完成——只读研究 take/MovieAgent、与 V0.2 逐模块比对、产出本报告、验收自检。**不再做任何事，停下等人工验收。**

**接下来（等人工批准后，进阶段 8 时才做）应做**：
1. （建议）先提交一个 **"阶段 0~7 完成"基线 commit**——给 8~18 的重构留回滚点。
2. 阶段 8：落地 Canonical Production Schema（新增 Shot/Beat/Storyboard/Asset/Timeline 模型 + Validator 规则集）+ 抽 Agent 公共骨架 + 每 Agent 输出契约。**纯本地、0 真实 LLM**。
3. 阶段 9：Provider Architecture（TextProvider + 能力接口 + ModelRouter 升级 + config per-provider + OpenAI 兼容 adapter 骨架）。**mock、0 真实多模态**。

**绝不应该做（无论现在还是阶段 8）**：
1. ✘ 不接任何真实图像/视频/VLM API、不装新依赖——那是阶段 12/13/14 的事。
2. ✘ 不新增第三个业务 Agent——先把 §7.1 的 Agent 公共骨架抽了再说。
3. ✘ 不为"学 take/MovieAgent"推翻现有 memory/context/workflow/validator——那些是我们领先的地方。
4. ✘ 不改 FilmAgent/（V0.1）任何文件、不碰参考项目、不硬编码任何 API key。
5. ✘ 不 commit、不 tag、不自动进阶段 8——等您（人工）读完本报告拍板。

## 17. 参考项目保密与合规说明

两个参考项目（take-main、movie-Agent-main）**仅被只读研究**：未复制进 v2/、未加入 import path、未加入 requirements.txt、未作为运行时依赖、未修改其任何文件。研究结论只以"架构思想借鉴"形式进入本报告，不含大段代码复制。

## 18. 验收自检记录

验收时间：2026-09-02，实测证据如下：

| 验收项 | 要求 | 实测证据 | 结论 |
|---|---|---|---|
| V0.1 是否修改 | 必须 NO | `git diff -- FilmAgent/` 为空；`git rev-parse HEAD` = `344c0c6b7c48497be934957c29fcae029cca6752`（= v0.1 tag） | ✅ NO |
| 真实 LLM 调用 | 必须 0 | 本阶段未运行任何 `run_real_*` 脚本，未调用 DeepSeek | ✅ 0 |
| 新增 API 调用 | 必须 0 | 未接入任何图像/视频/VLM API，未装新依赖 | ✅ 0 |
| v2 业务代码修改 | 必须 0 | 按文件 mtime 扫 `v2/` 9-02 全天改动，**仅新增** `v2/logs/pre_stage8_architecture_review/stage8_preflight_report.md`（本报告）；其余 v2 源文件零 touch | ✅ 0 行改动 |
| 参考项目是否被修改 | 必须 NO | 扫 `take-main` + `movie-Agent-main` 9-02 全天文件变更数 = 0、14:30 后变更数 = 0（两项目非 git 仓库，用 mtime 佐证只读） | ✅ NO |
| 产出 | 仅本报告 | 本阶段唯一新增文件即本报告 | ✅ |

**研究过程说明**：take 与 movie-Agent 的技术分析由 2 个只读子代理完成（仅 Read/Glob/Grep），未写盘、未联网调用模型生成 API；V0.2 现状盘点与设计章节由主会话基于已有代码只读核实。

**本阶段没做的事（复核指令 §十一）**：未修改 FilmAgent/、未修改 V0.1、未修改 take、未修改 MovieAgent、未安装新依赖、未新增业务 Agent、未接入新真实 API、未消耗真实 token、未改动 V0.2 业务逻辑、未进入阶段 8。

---
*报告完。等待人工验收——不自动进阶段 8、不 commit、不 tag。*
