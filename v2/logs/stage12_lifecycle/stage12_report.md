# stage12_report.md —— Stage 12 Project 生命周期编排层 阶段报告

> **阶段**：V0.2 Stage 12 = Project 生命周期 + 第一条完整业务链
> **规格**：`D:\AI\FilmAgent\v0.2开工指令12`（及启动提示 12-A / 12-B / 12-D）
> **A 会话产出**：`v2/docs/stages/stage12_plan.md`（含 §12 总控裁决记录）
> **执行**：A（分析/架构）/ B（代码实现）/ C（测试/Debug）/ D（文档/交付）四段会话体系
> **工作目录**：`D:\AI\FilmAgent-main`
> **前置**：Stage 11 已封存（`v0.2-stage11` = `ef18f0d`）；开工 HEAD = `f36ed04`
> **真实 API**：**0 次**（全 Mock；`run_real_lifecycle.py` 未创建、未执行 —— 见 §7 遗留）
> **验收证据**：**总控已独立复跑本阶段套件 125/125 rc=0**（非执行会话自述）

---

## 1. 阶段目标与达成

```text
目标（指令 §4.1）：
  建立编排层 ProjectOrchestrator，把 Casting / SceneGeneration / Storyboard /
  VisualAsset 四个**已封存 Agent** 串成一条端到端流水线，使「一个项目的一集
  （含 N 个场景）」能从 0 自动走到"分镜齐备 + 视觉资产齐备"，并留下可恢复的
  运行状态与完整运行报告。

达成：✅ 全部达成（总控独立复跑 125/125 rc=0 为验收证据）
  Casting → [每场景] Scene → Storyboard → [视觉资产齐备] → 齐备判定
  全程零改禁改清单；★ v2/workflow/ 0 diff + 0 新增（上层编排未污染引擎）
```

打通链路：

```text
Stage 7 剧本  →  Stage 11 分镜  →  Stage 10 视觉组装  →  [Stage 13 出图]
                    ↑ 已在 Stage 11 打通
      本阶段把整条链第一次真正编排成 Project 生命周期（可 resume + 有运行报告）
```

**为什么这是第一次"整机通电"**：Stage 0–11 都在造零件（每个 Agent 单独能跑、单独能测），
但**从来没有任何代码把零件连起来跑过一集**——没有人决定先跑谁、后跑谁、跑几次、
场景怎么分发、整集是否真的齐备。Stage 12 补上这一层。

---

## 2. 交付物清单

| 类型 | 文件 | 说明 |
|---|---|---|
| 代码 | `v2/orchestration/__init__.py` | 导出 `ProjectOrchestrator` / `OrchestrationError` / `OrchestrationState` / `preflight_check` / `format_gaps` / `atomic_write_json` |
| 代码 | `v2/orchestration/preflight.py` | `preflight_check(scene, known_characters, registry) -> List[str]` —— 调度前置校验门（纯函数，5 条规则，**返回缺口清单、不抛异常**；★先 `Scene.from_v01` 还原 `Line.type`） |
| 代码 | `v2/orchestration/state.py` | `OrchestrationState`（进度模型 + `to_dict`/`from_dict` 往返 + **自实现原子写** tempfile+`os.replace`）+ `OrchestrationError` + `should_skip`（含磁盘 artifact 存在性校验） |
| 代码 | `v2/orchestration/pipeline.py` | `ProjectOrchestrator` —— 两段式多场景编排 + 集级 Casting（含写 Memory）+ 视觉资产齐备 + 三种 review 策略 + resume + run_report |
| 测试 | `v2/logs/stage12_lifecycle/run_tests.py` | mock 套件 T01~T19（含 T03b/T04b/T06b/T09b/T10b 增强）+ T18 历史回归 **9 套**子进程 |
| 测试证据 | `v2/logs/stage12_lifecycle/test_report.json` | **125/125**，`real_api_requests = 0`（★应入库：与 stage10/stage11 惯例一致） |
| 报告 | `v2/logs/stage12_lifecycle/stage12_report.md` | 本文件（D 会话产出） |
| 文档 | `v2/docs/PROJECT_STATUS.md` / `HANDOFF.md` / `ROADMAP.md` / `CLAUDE.md` | D 会话同步（见 §9） |

**未创建**（指令 §8.1 列为新增项，实际未产出 —— 如实登记，见 §7-①）：

```text
v2/logs/stage12_lifecycle/run_real_lifecycle.py    ← 真实文本冒烟脚本（未创建）
```

**未修改**（`git diff --stat HEAD` 全空，实测）：

```text
FilmAgent/ 全目录                    仓库根 .gitignore
v2/workflow/ 全部（★本阶段关键）     v2/core/ 全部（含 io.py / token_log.py / visual*.py）
v2/agents/ 全部（含 base.py 与四个业务 Agent）   v2/llm/ 全部
v2/memory/  v2/review/  v2/context/  全部历史测试套件（stage1/3/4/6/7/8/9/10/11）
```

> **零侵入证明**：`git diff --stat -- v2/workflow/` 为空，且 `git status --short -- v2/workflow/`
> 为空（既无修改、也无新增）。本阶段**没有**改引擎，而是**在引擎之上**编排 —— 与指令 §九
> 的架构立意一致。

---

## 3. 设计决策（A 会话 + 总控裁决）

### 3.1 指令 §7.1 原伪代码不可实现 → 改为【两段式】（总控裁决 3 / A-3 批准）

指令 §7.1 原方案把 `[SceneGenerationStep, StoryboardStep]` 放进**同一个 run**，
并给 `inputs["scene"]` 赋该场景的 Scene。**A 会话实测（P3）证明做不到**：

```text
WorkflowEngine.run() 的 inputs 是「整次 run 共享的一个 dict」，
而 StoryboardStep 只读 inputs["scene"]、**完全忽略上游 Step 产出**。
→ 同 run 装两 step：要么用到过期 scene（串场），要么 inputs["scene"]=None → StepError 重试耗尽 FAILED。
```

**采纳方案（零改引擎）**：

```text
Phase A —— 剧本段（每场景一次 run）
  workflow_id = scene_gen_{sid}     step = [SceneGenerationStep(agent, scene_id=sid)]
  inputs      = {assembled_context, scene_spec}            → scene_{sid}.json
Phase B —— 分镜段（每场景一次 run）
  workflow_id = storyboard_{sid}    step = [StoryboardStep(agent, scene_id=sid)]
  inputs      = {assembled_context, storyboard_spec, scene=<Scene 对象>}  → storyboard_{sid}.json
```

`inputs["scene"]` 在 Phase B **显式放入**（来源：Phase A 的 approved artifact
经 `Scene.from_v01` 还原），**不得依赖上游 step 产出**。
语义收益：每个 run 恰好一个 step → 成功/失败边界清晰、resume 天然可用、未来可并行。

### 3.2 集级 Casting 通过后写 Memory（总控裁决 1 / A-1 批准）

**不写的后果**（A 会话实测 P10/P11）：Context `characters` 为空 → 真实 LLM 编造未登记角色名
→ `SceneGenerationAgent._normalize` 不回锚 lines 的 speaker/position → `UNKNOWN_CHARACTER`
→ 3 次重试耗尽 → 永久 FAILED。

**裁决硬约束**（B 已照此实现，见 `pipeline.py::_apply_casting_to_memory`）：

```text
1. 只允许 set_characters / set_world（沿用同一 run 的 Casting Artifact，不凭空构造）
2. ★ 严禁 approve_fact（事实批准是"人"的动作，编排层不得代批）—— 测试 T04b-4 断言 facts == []
3. characters 至少保证 {"name": …}（下游 Agent 只读 name）
```

### 3.3 视觉资产只支持 approved / rejected（总控裁决 2 / A-2 批准）

`VisualAssetRegistry.apply_review_decision(APPROVED_WITH_EDIT)` **直接抛错**；且"编辑后的
profile"缺确定性来源（`Review.edited_artifact` 是 Artifact 形态）。故编排层遇
`approved_with_edit` → **明确抛 `OrchestrationError`**（fail-loud 优于静默猜）。
实现见 `pipeline.py::_run_one_visual_asset`。

### 3.4 `state.py` 自实现原子写（总控裁决 4 / A-4 批准）

实测 `core.io.write_json` **不是**原子写（`path.open("w")` 直写，无 `os.replace`），
而 `core/` 属禁改清单 → 编排层**内部**自实现 `atomic_write_json`（tempfile + `os.replace`
+ `fsync`），序列化行为对齐 `core.io.write_json`（`ensure_ascii=False` / `indent=2`）。
测试 T02b/T02k 断言"无 `*.tmp` 残留"。

### 3.5 resume：跳过不调用引擎（A 会话问题②结论）

编排层对"已 approved 的单元"**直接跳过、不调用引擎**，不使用引擎的 `resume_from`：

```text
① resume_from 的参数是 WorkflowRun 对象；编排层持久化的是 state.json →
   要用它就得再存一份完整 WorkflowRun（= 第二套状态），违反"不造第二套机制"。
② 本阶段每 run 只有 1 个 step → run 内 resume 无意义（无"部分完成"中间态）。
③ 跳过语义更强：已 approved 单元不调用引擎 → 不产生任何新 Artifact 版本（幂等最好）。
④ resume_from 的能力不冲突、不浪费，保留给"未来一个 run 内多 step 的恢复"。
★ 跳过前必须校验磁盘 artifact 仍存在（R12）—— 缺失即视为 pending 重跑，绝不"状态说齐备、磁盘没有"。
```

### 3.6 指令缺陷处置（D-1 ~ D-8，总控已全部采纳）

| # | 缺陷 | 处置 |
|---|---|---|
| D-1 | HEAD 描述过期 | 总控直接修正指令 §二（锚点看 tag，不看 HEAD） |
| D-2 | §7.1 伪代码不可实现 | 采纳两段式（§3.1） |
| D-3 | 未写"编排层写 Memory" | 采纳（§3.2） |
| D-4 | §6.3 "原子写复用 write_json" 与实现不符 | 采纳"自行实现原子写"（§3.4） |
| D-5 | `semantic_data` 来源未指定 | 采纳"确定性数据"（character←Casting entry；location←world/registry；prop←名字本身），**不得调 LLM** |
| D-6 | T06 未提示 mock 需场景感知 | 采纳（测试 T06e 断言三份内容互异） |
| D-7 | §8.3 清单缺 `io.py`/`token_log.py` | 采纳，清单已显式化 |
| D-8 | `projects_root` 未要求可注入 | 采纳，构造参数含 `projects_root`（测试全部指向 `tmp_projects/`） |

> 未触发指令 §十七 架构暂停条件：**不存在"编排层必须改 v2/workflow/"的信号**，
> 上层编排路径成立（A 会话实测 + 总控独立复核 23/23）。

---

## 4. 契约笔记（A 会话关键结论，经总控独立复核属实）

| # | 契约要点 | 影响 |
|---|---|---|
| 1 | `WorkflowEngine.__init__` 只做 `steps` / `store` 两件事，**无跨 run 可变状态**；`run()` 每次新建 `WorkflowContext` + `WorkflowRun` | 复用 engine 安全；本阶段仍"每段新建 engine"（语义更清晰） |
| 2 | 同名 Artifact 靠 `ArtifactStore` 递增版本**绝不覆盖**；名字带 `_{scene_id}` 互不串号 | N 个 run 的产物不互相覆盖 |
| 3 | **没有任何 Step 从"上游 Step 产出"取输入**；四个 Step 全部只读 `context.inputs` | §7.1 同 run 两 step 不可实现的根因 |
| 4 | `SceneGenerationStep` 用 **`self.scene_id`** 命名（不读 spec）；`StoryboardStep` 会读 `spec["scene_id"]` 并回注 | 必须**按 sid 构造 Step 实例** |
| 5 | `VisualAssetStep.needs = []`；`step_id` 默认固定 `"visual_asset"`；spec 是**单个 dict** | 视觉段**一实体一 run** + 显式传 `step_id=` |
| 6 | `Line.type` 是 V0.2 内部字段，**`to_v01()` 不写出**；V0.1 靠"有无 `move` 键"判类型 | preflight **必须先 `Scene.from_v01` 还原**，否则 move 行完全漏检（R3） |
| 7 | `VisualAssetRegistry` 每个写方法**内部自动 `save()`**；`(kind,name)` 已绑定 → 重复 `register` 报错 | "写回 registry" = 调用这些方法本身 |
| 8 | `VisualAssetStep.validate()` 用 `require_complete=True` → 必填门槛（character=appearance/face/hair/clothing 等） | mock 档案必须齐必填，否则重试耗尽 FAILED（R5） |
| 9 | `WorkflowEngine(review_handler=...)` 回调契约：`handler(artifact, context) -> 对象`，需 `.decision`（三态）/`.review_id`；`approved_with_edit` 另需 `.edited_content` | 三种 review 策略的实现形状（§5.4） |
| 10 | `resume_from` 是**按 run + 按 `step_id` 的 `is_success`** 跳过（记 SKIPPED、不写新版本） | 编排层改用"跳过不调用"（§3.5） |

---

## 5. 实现摘要

### 5.1 目录布局与组件构造（`ProjectOrchestrator.__init__`）

```text
projects_root   = <可注入，默认 V2_ROOT/"projects">      ★D-8
project_dir     = projects_root / project_id
artifacts_dir   = project_dir / "artifacts"
orchestration_dir = project_dir / "orchestration"
run_log_dir     = orchestration_dir / "runs"
state_path      = orchestration_dir / "state.json"

memory    = MemoryStore(project_id, projects_root=projects_root)   # 0 改
assets    = AssetRegistry.load(V2_ROOT / "assets" / "locations")    # 0 改
assembler = ContextAssembler(memory, assets)                        # 0 改
visual_registry = VisualAssetRegistry(project_dir)                  # 0 改
store     = ArtifactStore(artifacts_dir)                            # 0 改
state     = OrchestrationState.load(project_id, state_path, artifacts_dir=…)
provider  = <注入 或 Router(kind="text").get_provider("text")>       # 缺省 mock → 0 API
```

### 5.2 主流程 `run(run_spec)`

```text
① 集级 Casting（casting_ep{N}，一次 run）
     → approved 后 record_casting + ★_apply_casting_to_memory（set_characters / set_world）
② 每场景：剧本段 scene_gen_{sid} + 分镜段 storyboard_{sid}（两段式）
     剧本段后 → preflight_check(scene, known_names, assets)
        缺口非空 → 记 errors（stage=preflight, scene_id, gaps），storyboard 段记 pending，
                   ★不进入 Stage 11（无重试风暴），返回 None
     分镜段 → inputs["scene"] = Scene（显式放入）
③ 视觉资产段：_collect_entities（确定性）→ 每实体一次 run（step_id=visual_asset_{kind}_{safe}）
     → register → new_version(profile=快照) → submit_for_review → approve → record_visual_asset
④ 齐备判定 _compute_completeness（与磁盘交叉核对）→ _decide_status → _finish（原子写 run_report + state）
```

### 5.3 视觉实体收集（确定性，不靠 LLM）

```text
entity_set = ∪ over 本集全部 storyboard 的 flatten_shots():
    {("character", c)  for c in shot.characters if c}
  ∪ {("location", shot.location or storyboard.location)}   （非空才收）
  ∪ {("prop", a.ref) for a in shot.assets if a.kind=="prop" and a.ref}
```

`semantic_data` 的确定性来源：character ← Casting entry 档案；location ← AssetRegistry 站点；
prop ← 名字本身。**绝不调 LLM**（总控 D-5 处置）。

### 5.4 三种审核策略（§7.4；★必须标注程序化模拟）

```text
"auto_approve"     恒 APPROVED，review_id=auto-N   → review_mode = "auto_approve (programmatic, NOT human review)"
"scripted:<file>"  按预置决策序列出队（耗尽 → OrchestrationError，不静默批准）→ "scripted (programmatic, NOT human review)"
"human_stub"       ★run() 开工前即短路 → OrchestrationError（不产生任何 artifact、不做无谓重试）
```

> **诚实性（AC13）**：`review_mode` 由 `_review_mode_label()` 统一产出，**必含**
> `"programmatic, NOT human review"`；`scripted` 只标 `"scripted"`，**不把本地决策文件路径**
> 泄进可交付报告。测试 T09a/T09b 双向断言（含"不得出现'已人工审核'字样"）。

### 5.5 resume 与幂等

```text
resume=true：读 state.json → 已 approved 且磁盘 artifact 存在 → 跳过（记 warning，不调用引擎）
             failed / 缺失 / 磁盘产物被删 → 重跑（ArtifactStore 自动递增版本，绝不覆盖）
```

`run_report` 形状（§6.2 + 补充）：`project_id / episode / run_id / started_at / ended_at /
status / review_mode / provider_vendor / resume / scene_ids / stages / completeness /
warnings / errors`；`stages` 各项带 `artifact / status / attempts / run_id / workflow_id`。

### 5.6 Token / Context 纪律（§十一 硬验收）

```text
✅ 视觉身份活在 VisualAssetRegistry（一份 approved profile）
✅ Shot 只存名字级引用 + 本镜头视觉增量
✅ 编排层只传"该场景需要的那部分"上下文
❌ 编排层不把 VisualComposer 展开结果回写进 Artifact
```

**硬证据（T14a）**：注册视觉资产**前后**同一 `Storyboard.to_dict()` 序列化结果**逐字节不变**；
**新增硬验收（T14b/T14d/T14e）**：`storyboard_*.json` 文本不含档案关键词
（`"red wool coat"` / `"amber and wood tones"`）；场景间**不膨胀**（三份 artifact 大小同量级）。

---

## 6. 测试证据

### 6.1 本阶段套件（★总控已独立复跑）

```bash
env -u PYTHONPATH python v2/logs/stage12_lifecycle/run_tests.py     # rc=0
```

```text
TOTAL: 125/125 passed        real_api_requests = 0
```

> **证据性质声明**：`test_report.json`（125/125、`real_api_requests=0`）由**总控会话独立复跑**
> 产出（`env -u PYTHONPATH`，剥离宿主 shim），**不是执行会话自述**。此为本阶段验收证据。

| 组 | 覆盖 | 条数 | 结果 |
|---|---|---|---|
| T01a–f | 编排层结构：可构造 / 非 Project 入参报错 / `OrchestrationError` 家族 / **依赖方向**（orchestration 只 import 下层；下层 0 命中 orchestration）/ 导出齐备 | 6 | PASS |
| T02a–k | `state.py`：落盘 JSON 合法 / **原子写无 tmp 残留** / 往返一致 / **resume 判定**（approved 跳过、**磁盘被删不跳过**、failed 不跳过）/ 未知 kind 报错 / 无 path 报错 | 11 | PASS |
| T03a–m | `preflight.py` 5 条规则**逐条正/反例** + 边界（scene=None / 类型非法 / registry=None / known=None / 空 who）：**返回 list、不抛异常** | 13 | PASS |
| T03b-1–4 | ★**R3 专测**：落盘 dict 无 `type` 键 → 传 `to_v01()` dict 时 move 行 character/destination **仍被检出**（证明必须 `from_v01`） | 4 | PASS |
| T04a–e | preflight 集成：坏 Scene 拦在编排层 + 缺口含 `UNKNOWN_CHARACTER` + ★**Stage 11 未被调用**（`storyboard_generation` 调用 0 次，无重试风暴）+ 不静默成功 | 5 | PASS |
| T04b-1–5 | ★**R2 专测**：Memory 空 → characters 空；Casting 后编排层**已 `set_characters`**；★**未代批事实**（`facts == []`） | 5 | PASS |
| T05a–g | 单场景 E2E：casting/scene_1/storyboard_1 全落盘 + `StoryboardValidator` 0 错误 + 全 Shot `status=planned` + completeness 正确 | 7 | PASS |
| T06a–g | ★**多场景正确性（核心）**：3 场景 → 各自 `scene_id` / 内层 `shot.scene_id` 正确 + **内容互不串场** + ★**R4**：三份内容互异（证明 mock 场景感知，T06 有效） | 7 | PASS |
| T07a–b | 三次 run 的 `run_id` 互异；`ArtifactStore.list_files` 各场景不交叉 | 2 | PASS |
| T08a–c | 版本化：重跑生成 `storyboard_1_v2.json` / `casting_v2.json`；★原文件字节不变 | 3 | PASS |
| T09a–g | Review 三态（scripted）+ ★`review_mode` 含 `auto_approve (programmatic, NOT human review)` + **不得出现"已人工审核"** + 未知 policy 报错 | 7 | PASS |
| T09b-1–2 | ★`human_stub` → `OrchestrationError` 且**未产生任何 artifact**（不伪造人工审核） | 2 | PASS |
| T10a–g | 视觉齐备：实体去重（character/location/prop）+ 空 location 回落 + ★`assets_approved == assets_referenced == 5` + character 只 1 个（不重复建档）+ resume 不重复建档 | 7 | PASS |
| T10b-1–4 | ★**R5 专测**：mock 缺必填 → 重试耗尽 → 该实体记 `pending`、`assets_approved < assets_referenced`、`status != success` | 4 | PASS |
| T11a–g | resume：已完成不重跑（无新版本）+ 新场景正常产出 + 字节不变 + ★**不调用引擎**（场景 1 无 storyboard 调用）+ 只覆盖新增部分 | 7 | PASS |
| T12a–e | 失败传播：`status ∈ {partial, failed}` + ★**errors 能定位到具体 `scene_id`** + 场景 1 仍成功（不整体丢弃）+ 重复 `scene_ids` 报错（R13） | 5 | PASS |
| T13a–b | ★真实 API = 0（`APIRequestCounter` 程序化快照，不人工数数）；mock 调用已计数 | 2 | PASS |
| T14a–e | ★**Token 硬证据**：注册前后 `to_dict()` 逐字节不变 + 无档案渗漏 + ★场景间不膨胀（大小同量级 `[2979, 2979, 2979]`） | 5 | PASS |
| T15a–d | 静态检查：`compileall` / import 冒烟 + 导出齐备 / 新增文件无 `sk-` 命中 / `ASSET_KINDS` 精确不变 | 4 | PASS |
| T16 | 禁改清单 0 diff（§8.3 + `FilmAgent/` + 仓库根 `.gitignore`） | 1 | PASS |
| T17a–b | ★**`v2/workflow/` 0 diff + 0 新增**（上层编排未污染引擎）；`v2/core` + `v2/agents` 0 diff | 2 | PASS |
| T19a–g | 端到端完整性：completeness 与**磁盘实际文件交叉核对**一致 + `run_<run_id>.json` 落盘 + state.json 只序列化编排自有状态（R15） | 7 | PASS |
| T18-stage1…11 | 历史回归 **9 套**子进程全部 rc=0 | 9 | PASS |

合计 **125** 条。

### 6.2 历史回归（T18，剥离宿主 PYTHONPATH shim）

```text
stage1 19/19 · stage3 38/38 · stage4 25/25 · stage6 24/24 · stage7 29/29
stage8 27/27 · stage9 26/26 · stage10 76/76 · stage11 65/65          全部 rc=0
```

### 6.3 真实调用账目

```text
本阶段真实 API 调用 = 0 次（文本 0 / 视觉 0），成本 = ¥0.00
run_real_lifecycle.py **未创建、未执行**（详见 §7-①：属如实登记的遗留 + 阻塞）
```

### 6.4 冻结核实（实测）

```text
git log --oneline -1        → HEAD = f36ed04（未动）
git rev-parse v0.1^{commit} → 344c0c6b7c48497be934957c29fcae029cca6752
6 个 tag（v0.1 / v0.2-stage7/8/9/10/11）指向**未移动**
git diff --stat -- FilmAgent/ .gitignore v2/workflow/ v2/core/ v2/llm/ \
    v2/agents/ v2/memory/ v2/review/ v2/context/        → 全空（0 diff）
git status --short -- v2/workflow/                        → 空（0 新增）
泄漏扫描（git add -An v2/ | grep jsonl/images/registry/tmp/png/pyc）→ 0 命中
```

---

## 7. 遗留问题（如实登记，不得美化）

### ① 真实冒烟未执行：`run_real_lifecycle.py` 未创建、未运行

```text
状态：**阻塞 —— 需人工配置**（`DEEPSEEK_API_KEY` 或已配置文本 Provider + 明确批准）
事实：指令 §8.1 将 `run_real_lifecycle.py` 列为新增文件，但**该脚本实际未产出**，
      真实链路**一次都没有跑过**。本报告**绝不伪造**任何真实调用结果。
影响：Mock 路径（125/125）完整成立；真实链路连通性**未验证**。
建议：由总控决定 —— (a) 补建脚本并按 §十三 策略跑 ≤5 次真实文本调用（人工批准）；或
      (b) 作为遗留登记，待 Stage 13（真实出图）一并验证。
```

> **与指令的差异（D 会话发现，报总控）**：指令 12-D §8.1 把 `run_real_lifecycle.py` 列为
> Stage 12 交付物之一，但工作区**不存在**该文件。D 会话未擅自创建（创建真实冒烟脚本属
> B 会话代码范围，且 12-D 明确要求"如实记录为阻塞、不伪造"）。请总控裁决处置方式。

### ② `T18b` 未做

```text
内容：跑测试前后 `git status --short` 对比，把技术债 T-6 从"靠人记得"变成"程序化发现"。
定性：**可选增强**（stage12_plan §4.2 已注明"实现麻烦可不做"），非缺陷修复。
处置：如实登记为遗留；当前仍靠"跑完即复位 6 文件"的人工流程（见 §8）。
```

### ③ 其余遗留（承接 Stage 11，非本阶段引入）

```text
- 交互式人工审核 UI 未做（auto_approve 是程序化模拟，已在 review_mode 显式标注）。
- Image / Video / VLM Agent 未实现；image/video/audio 仅 Schema + Provider 预留接口。
- 真实出图（Stage 13）未做 —— 编排层已就位，Stage 13 只需在编排层加"出图 stage"。
- 技术债 T-1（记账泄漏）/ T-2（prompt_length 语义失真）/ T-3（bailian_vision pricing=None）
  / T-4（真实冒烟未落参考图 checksum）/ T-5（人工审核 UI）/ T-6（6 文件复位）均未根治。
```

---

## 8. 交付前复位（两个坑，每轮必现）

```bash
# 坑 1（T-6）：★ 6 个文件（不是 4 个）
git checkout -- v2/logs/stage3_workflow_test/stage3_report.json \
                v2/logs/stage3_workflow_test/stage3_console.txt \
                v2/logs/stage4_memory_context_review_test/stage4_report.json \
                v2/logs/stage4_memory_context_review_test/stage4_console.txt \
                v2/logs/stage6_casting/stage6_report.json \
                v2/logs/stage7_scene_generation/stage7_report.json
# 坑 2（T-1）：仓库根记账泄漏
rm -f llm_calls.jsonl v2/llm_calls.jsonl
git status --short        # 期望：仅 Stage 12 预期新文件（0 改 + N 新增）
git diff --stat -- v2/workflow/   # 期望：空
```

> 本轮 D 会话**未从仓库根跑测试**（避免引入新的 dirty / 泄漏），故无需复位；
> `git status --short` 实测仅 3 项未跟踪（`stage12_plan.md` / `v2/orchestration/` /
> `v2/logs/stage12_lifecycle/`），与预期一致。

---

## 9. AC 逐条对照（指令 §十四；总控验收用）

| AC | 结论 | 证据 |
|---|---|---|
| **AC1** 新套件全绿（T01~T19）+ 历史回归 9 套全绿 | ✅ | **总控独立复跑 125/125 rc=0**；T18 九套 rc=0（19/38/25/24/29/27/26/76/65） |
| **AC2** V0.1 冻结；全部 tag 未移动 | ✅ | `git diff -- FilmAgent/` 空；`v0.1`=`344c0c6…`；6 tag 指向未变 |
| **AC3** 零侵入：§8.3 全部 0 diff；★`v2/workflow/` 0 diff | ✅ | T16 / T17a / T17b；`git diff --stat` 禁改路径全空；`v2/workflow/` 0 diff + 0 新增 |
| **AC4** 端到端成立（1 集 × 3 场景从 0 到齐备） | ✅ | T05 / T06 / T19；completeness 与磁盘交叉核对一致 |
| **AC5** ★多场景正确性（3 份互不串场） | ✅ | T06b / T06e / T06f（场景感知 mock，证明测试有效） |
| **AC6** 前置校验门生效（坏 Scene 不触发重试风暴） | ✅ | T03 / T03b / T04c（Stage 11 调用 0 次） |
| **AC7** resume 生效（跳过已完成、不覆盖） | ✅ | T11a–g（不调用引擎、字节不变） |
| **AC8** 机制复用（无第三套状态机） | ✅ | 只用既有 `ReviewDecision` 三态 / Artifact 版本化 / Retry；`state.json` 是镜像非新判据 |
| **AC9** 真实 API：≤5 文本 + 0 视觉；无配置时明确阻塞 | ⚠️ 部分 | 真实调用 **0 次**（T13a）；★真实冒烟**未执行**（无 key）→ **已明确报"阻塞：需人工配置"，未伪造** |
| **AC10** 静态检查全过 | ✅ | T15a–d（compileall / import / 依赖方向 T01d/T01e / 密钥扫描） |
| **AC11** 文档同步 | ✅ | 本报告 + `PROJECT_STATUS` / `HANDOFF` / `ROADMAP` / `CLAUDE.md`（D 会话） |
| **AC12** Git：无泄漏文件；tag 由人工批准后创建 | ✅ | 泄漏扫描 0 命中；commit/tag 待人工批准（§10） |
| **AC13** 诚实性（auto_approve 标注程序化模拟） | ✅ | T09a / T09b；`review_mode` 含 `"programmatic, NOT human review"`；无"已人工审核"字样 |

> **AC9 说明**：AC9 的"≤5 次文本 + 0 视觉"上限**未被触达**（实际 0 次），因为真实冒烟脚本
> 未创建、未执行（§7-①）。按指令 §十三"若无可用的真实配置 → 明确报告阻塞"的约定，
> 本阶段**明确报告阻塞**，Mock 路径照常交付。**不构成 PASS 的缺口**，但需总控知悉。

---

## 10. Git 建议（**待人工批准，本会话不执行**）

```bash
cd /d D:\AI\FilmAgent-main

# ① 泄漏 / 范围核实（实测已通过，此处为封存 SOP 标准动作）
git status --short                                   # 期望：仅 Stage 12 预期文件（0 改 + N 新增）
git diff --stat -- FilmAgent/ ; git diff --stat -- .gitignore
git diff --stat -- v2/workflow/ ; git diff --stat -- v2/core/ ; git diff --stat -- v2/llm/
git status --short -- v2/workflow/                    # 期望：空（0 diff + 0 新增）

# ② 提交（★ test_report.json 入库 —— 与 stage10/stage11 惯例一致）
git add CLAUDE.md v2/
git commit -m "feat(v0.2): project lifecycle orchestration (stage12)"
git tag v0.2-stage12

# ③ 收尾自检
git status --short                                   # 期望：空
git rev-parse v0.2-stage12^{commit}
```

> tag 一经创建**永不移动、永不 force**。文档中一律用 **tag 名查 hash**
> （`git rev-parse v0.2-stage12`），**不写死 hash**（避免自引用悖论，见 `ORCHESTRATION.md` §5.2）。

---

## 11. 完成后整个项目发生什么变化

```text
之前：十一个阶段各自独立、各自可测，但从未连起来跑过一集
之后：ProjectOrchestrator 能驱动一个项目的一集（N 个场景）从 0 走到
      "分镜齐备 + 视觉资产齐备"，可 resume、有运行报告、有齐备判定

打通的关键链路：
  Casting → [每场景] Scene → Storyboard → [视觉资产] → 齐备判定
       ↑ Stage 12 把这条链第一次真正跑通

意义：
  1. 第一次端到端验证 —— 若某两个 Agent 之间的契约有裂缝，本阶段会暴露（本阶段暴露并
     解决了 §7.1 的伪代码缺陷与 R2 的 Memory 缺口）；
  2. 多场景分发机制就位 → 真实 3 场景/集 的规模问题被解决；
  3. 为 Stage 13（真实出图）备好调度底座 —— 只需在编排层加一个"出图 stage"，不必再动架构。
```

---

*Stage 12 报告 —— A/B/C/D 四段会话体系共同产出（C 会话经总控裁定跳过，见下）；*
*验收以总控亲自复跑结果为准。*

> **C 会话说明（总控裁定）**：C 会话（专项测试/Debug）**已决定跳过** ——
> B 会话交付的是**完整套件 T01~T19（125 条）**而非"只写关键单测"，
> `T18b` 属可选增强（非缺陷修复）。故 D 会话直接接手交付。
> 125/125 由**总控独立复跑**确认，作为验收证据。
