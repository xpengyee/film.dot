# stage12_plan.md —— Stage 12 编排层契约笔记 / 接线方案 / 风险 / 测试方案

> **产出会话**：Stage 12-A（分析 / 架构），工作目录 `D:\AI\FilmAgent-main`。
> **产出日期**：2026-09-14。**不写业务代码、不 commit**（A 会话边界，见 `ORCHESTRATION.md` §1.1）。
> **上游规格**：`D:\AI\FilmAgent\v0.2开工指令12`（下称"指令"）。
> **给谁读**：Stage 12-B（实现）、12-C（测试）、总控（验收）。
>
> **本文件的所有结论都经过"读源码 + 实测探针"双重核实**。探针是仓库外临时脚本
> （`%TEMP%/stage12a_probe/`），跑完即删，**未在仓库留下任何文件**；
> 实测输出已摘录进本文件（标 `【实测】`）。

---

## 0. 开工自检结果（指令 §二 / §3.1）

| 检查项 | 期望 | 实测 | 结论 |
|---|---|---|---|
| `git log --oneline -5` | HEAD = Stage 11 交付 commit | HEAD = **`f36ed04`** `docs: add stage-closeout SOP to ORCHESTRATION.md`；父 `a76e70e` `docs: mark stage11 sealed`；`ef18f0d` = stage11 代码 commit | ✅ 见 D-1 |
| `git status --short` | 空 | 空 | ✅ |
| `git tag -l` | 6 个 tag | `v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9` / `v0.2-stage10` / `v0.2-stage11` | ✅ |
| `git diff -- FilmAgent/` | 空 | 空 | ✅ |
| `v0.2-stage11` tag 指向 | `ef18f0d` | `ef18f0d`（`docs: add stage-closeout SOP` 未移动 tag） | ✅ |
| stage11 套件 | 65/65 | **65/65，rc=0**（耗时 13m17s，含 8 套嵌套历史回归全绿） | ✅ |
| `v2/orchestration/` | 尚不存在（本阶段新建） | 不存在 | ✅ |
| `v2/projects/` | 存在 | 存在且为空（`projects/` 已被 `v2/.gitignore` 忽略） | ✅ |
| 禁改清单 0 diff | 全空 | `v2/workflow/ v2/core/ v2/llm/ v2/agents/ v2/memory/ v2/review/ v2/context/` 全空 | ✅ |

**D-1（指令文本过期，非阻塞）**：指令 §二 只写"HEAD 应为 Stage 11 的交付 commit"，
实际 HEAD 比 Stage 11 代码 commit 多了 **2 条纯文档 commit**（`a76e70e` / `f36ed04`，
都属 Stage 11 收尾文档，**不改任何 `v2/` 代码**）。
判定：**不阻塞**。锚点看 `v0.2-stage11` tag（= `ef18f0d`），不看 HEAD —— 与
`ORCHESTRATION.md` §5.2 的"tag 指向才是锚点"一致。
另：指令抬头写的"HEAD = `dc335a7`；最近 tag = `v0.2-stage10`"是**指令生成时**的状态，
现已过期（属历史描述，不影响执行）。

**指令 §3.0 前置条件**：Stage 11 **已封存**（`ef18f0d` + tag `v0.2-stage11`），
满足"Stage 12 不得在未封存的 Stage 11 之上开工"。**无需执行 §3.0 的 commit/tag 动作。**

**两个坑本轮实测复现（确定性事实，B/C/D 必须照做）**：

1. **T-6（★是 6 个文件，不是 4 个）**：跑 stage11 套件（含嵌套回归）后，
   以下 6 个产物被重写：
   `stage3_report.json` / `stage3_console.txt` / `stage4_report.json` /
   `stage4_console.txt` / `stage6_report.json` / `stage7_report.json`。
2. **T-1**：从仓库根跑套件生成根目录 `llm_calls.jsonl`（本次同时存在 `v2/llm_calls.jsonl`）。

两个坑**均已复位**（`git checkout --` 6 文件 + `rm -f llm_calls.jsonl v2/llm_calls.jsonl`），
工作区当前**完全干净**（`git status --short` 空）。

---

## 1. 契约笔记（S1）—— 逐字段核对源码

> 全部核对过源码。与指令描述有出入处均标 **⚠️**，并在 §8 汇总为"指令缺陷"。

### 1.1 WorkflowEngine 的真实语义（`v2/workflow/engine.py`，禁改）

```python
WorkflowEngine(
    steps: Iterable[Step],
    *,
    artifacts_dir: Path,
    run_log_dir: Optional[Path] = None,      # 给了就写 <run_id>.json
    retry_backoff_seconds: float = 0.0,
    review_handler: Optional[Callable[[Artifact, Any], Any]] = None,
)
.run(*, project_id, workflow_id, inputs=None, max_attempts=3,
     resume_from: Optional[WorkflowRun] = None) -> WorkflowRun
```

**多次调用 / 多个 engine 的副作用（问题①，★实测）**：

- `__init__` 只做两件事：`self.steps = list(steps)`、`self.store = ArtifactStore(artifacts_dir)`。
  **引擎没有除 steps / store 之外的跨 run 可变状态。**
- `run()` 每次都新建 `WorkflowContext` + `WorkflowRun`；`inputs=dict(inputs or {})` 是
  **每次 run 一份新的浅拷贝**。
- 【实测 P1】同一 engine 实例连续 `run()` 两次 → `run1=success run2=success`、
  `run_id 不同=True`、产物 `['storyboard_1.json','storyboard_1_v2.json']`（递增不覆盖）。
  → **复用同一 engine 是安全的**（但本阶段推荐每段 run 新建 engine，语义更清晰）。
- 【实测 P2】同一 `artifacts_dir` 下为 scene 1/2/3 各跑一次（新建 engine）→
  产物 `['storyboard_1.json','storyboard_2.json','storyboard_3.json']`，三份
  `scene_id` / `characters` / 内层 `shot.scene_id` 各自正确，`run_id` 互异。
  → **N 个 run 的 Artifact 不互相覆盖**（名字带 `_{scene_id}`，且 ArtifactStore 只递增）。
- `resume_from` 的真实语义【实测 P4】：按 **`step_id` 在上一次 `WorkflowRun` 里
  `is_success`** 判定 → 该 step 记 `StepStatus.SKIPPED`、`approved_artifact` 沿用上一轮、
  **不写新版本文件**。它是**按 run** 的，不是按项目。

**单 Step 执行流程（`_run_step`）**：
`run()` → 先落盘（无论质量如何，版本文件都保留）→ `validate()` →
错误列表为空 → 成功（若 `needs_review=True` 则转 `_handle_review`）；
非空或抛异常 → 按 `retryable` 与 `max_attempts` 重试；耗尽 → `FAILED` → **整个 run 立即失败并停止后续 step**。

**⚠️ 要点 A（本阶段最关键的一条）**：**没有任何 Step 从"上游 Step 的产出"里取输入。**
四个 Step 全部只读 `context.inputs`（详见 §1.2）。这是 §7.1 多场景问题的根因，
也是"同 run 串场"无法用 inputs 之外的办法绕过的地方。

### 1.2 四个 Agent / Step 的真实接线点（`v2/agents/*.py`，禁改）

| Step | 读 `inputs` 的键 | `artifact_name` | `step_id` | `needs_review` |
|---|---|---|---|---|
| `CastingStep` | `assembled_context` | `casting` | `casting` | True |
| `SceneGenerationStep` | `assembled_context`、`scene_spec` | **`scene_{self.scene_id}`**（构造时注入） | `scene_generation_{self.scene_id}` | True |
| `StoryboardStep` | `assembled_context`、`storyboard_spec`、**`scene`**（缺则 `StepError`） | `storyboard_{spec.scene_id or self.scene_id}` | `storyboard_generation_{self.scene_id}` | True |
| `VisualAssetStep` | **`visual_asset_spec`**（★**不读 `assembled_context`**） | `visual_asset_{kind}_{name}`（空格→`_`） | 默认 `visual_asset`（可传 `step_id=`） | True |

**⚠️ 要点 B（易错）**：`SceneGenerationStep` 命名用的是 **`self.scene_id`（构造参数）**，
**不读 `spec["scene_id"]`**；而 `StoryboardStep` 会读 `spec["scene_id"]` 并**回注**进 spec。
→ B 会话必须**按 scene_id 构造 Step 实例**（不能只靠 spec），C 会话的用例也要照此构造。

**⚠️ 要点 C**：`VisualAssetStep.needs = []`，`run()` 不调用 `assembled_context()` →
视觉资产段的 `inputs` **不需要 `assembled_context`**（但 `ContextAssembler.assemble()`
对 needs=[] 的 step 会返回 items=[] 的 AssembledContext，传了也无害）。

**⚠️ 要点 D**：`VisualAssetStep.step_id` 类属性是固定值 `"visual_asset"`【实测 P9】，
且它读的 `visual_asset_spec` 是**单个 dict** →
**一个 run 只能喂一个实体**，多实体必须多 run（并各自传 `step_id=`）。

**`needs` 逐项核对**（`ContextAssembler._providers` 全部已注册，**无需新增 provider**）：

| need | provider 行为 | 本阶段用途 |
|---|---|---|
| `project_info` | `{project_id, project_title}` | prompt 主题 |
| `approved_facts` | `memory.get_approved_facts()`（**candidate 绝不进**） | 剧情真相 |
| `characters` | `memory.get_characters()` → `list[dict]` | **★名字白名单（关键，见 §3 R2）** |
| `world` | `memory.get_world()` | Casting 用 |
| `locations` | `[{name, positions:[id,…]}]` | 地点白名单 + 站位点清单 |
| `actions` | `assets.action_names()` | 动作白名单 |
| `shots` | `assets.shot_names()` | V0.1 注册镜头名 |

`ContextAssembler.assemble(step, context, *, project_title="")`：**只有
`previous_artifacts` / `workflow_state` 两个 need 会用到 `context`**；本阶段 needs 不含它们，
所以可以在 `engine.run()` 之前用一个"占位 `WorkflowContext`"调用 `assemble()`。

### 1.3 Scene 的真实形状（preflight 用，`v2/core/models.py`，禁改）

```python
Scene(id: int, info: SceneInfo(who: List[str], where: str, what: str),
      lines: List[Line], initial_positions: List[PositionRef])

Line.type ∈ {"dialogue", "move"}          # ★V0.2 内部字段，to_v01() 不写出
  dialogue: speaker / content / actions / shot / positions
  move:     move(Move(character, destination, reason)) / positions / shot
PositionRef(character, position)
```

**⚠️ 要点 E（preflight 必须遵守）**：`Line.type` **不写进 `to_v01()`**，
V0.1 侧靠"有没有 `move` 键"判断。因此 preflight **必须先
`Scene.from_v01(dict, id=sid)` 还原**，绝不能直接在落盘 dict 上判 `line["type"]`。
【实测 P12】`from_v01` 后 `line types = ['dialogue','move']`；`to_v01()` 再序列化
仍不含 `type`；往返后类型正确、`move.destination` 保留。

### 1.4 判据来源表（问题③，逐条对照真实代码）

| 指令 §7.2 规则 | 判据来源（真实代码） | 同源校验器（既有） |
|---|---|---|
| 1. `scene.info.who ⊆ known_characters` | `MemoryStore.get_characters()` → `[{"name":…}]`；**另需并入 Casting 产物**（§3 R2） | `ReferenceValidator.check_character` on `info.who`（`validator.py` L203-204） |
| 2. `scene.info.where ∈ registry.location_names()` | `AssetRegistry.has_location(where)` / `location_names()` | `EnumValidator` L96-110（`INVALID_LOCATION`） |
| 3. dialogue 行 `speaker ∈ known_characters` | 遍历 `scene.lines`，`line.type=="dialogue"` → `line.speaker` | `ReferenceValidator` L207-208 |
| 4. `initial_positions[].position` / `line.positions[].position ∈ 该地点站位点` | `registry.has_position(where, pos)` / `get_positions(where)` → `[p.id]` | `EnumValidator` L161-171（initial）/ L143-149（line.positions） |
| 5. move 行 `move.destination` 同上 | `line.type=="move"` → `line.move.destination` | `EnumValidator` L151-159 |

**⚠️ 要点 F（preflight 与既有 Validator 的关系）**：
上面 5 条**已经被 `core/validator.py` 的 `Validator`（Enum+Reference+Schema）覆盖**，
但那是**校验器（门槛/拒）**，且跑在 **Scene 生成 step 内部** → 触发 3 次重试 → `FAILED`
（就是 D-2 的 fail-loud 重试风暴）。
preflight 是**调度前置条件（编排层职责）**：**返回缺口清单、不抛异常**，
让编排层在"喂给 Stage 11 之前"就明确停下。
**两条纪律**：① 不得把 preflight 加进 `core/validator.py`（指令 §7.2 已明确）；
② preflight **不改判据来源**，只把同一套判据前移（避免两套规则漂移）。
【实测 P13】坏 Scene 返回 6 条缺口（who / speaker / line.positions / move.character /
move.destination / initial_positions），好 Scene 返回 `[]`，**全程不抛异常**。

### 1.5 视觉资产链路真实签名（`v2/core/visual_registry.py`，禁改）

```python
VisualAssetRegistry(project_dir: Path)          # 落盘 <project_dir>/visual_assets/registry.json

register(kind, name, *, purpose="", asset_id="") -> VisualAsset
    # (kind,name) 已绑定 → VisualRegistryError（绝不重复建档）
new_version(asset_id, *, profile=None, reference_images=None, visual_prompt="",
            source="text_planned", notes="", status="draft") -> VisualAssetVersion
    # profile 可传 dict（走 new_profile 规范化）/ Profile 实例 / None
    # 返回的 .version 是字符串 "v1" / "v2" …
submit_for_review(asset_id, version)   # draft|generated → reviewing
approve(asset_id, version)             # reviewing → approved；旧 approved 自动 superseded
reject(asset_id, version)              # reviewing → rejected
apply_review_decision(asset_id, version, decision)   # ★见下
active_profile(kind, name) -> Optional[(asset_id, version, ver)]
active_version(asset_id) -> Optional[ver]            # 无 approved → None（绝不拿 draft 充数）
find(kind, name) -> Optional[VisualAsset]
```

- **每个写方法内部都会 `save()`** → 不需要额外"写回 registry"动作；
  "写回"= 调用这些方法本身。
- **⚠️ 要点 G**：`apply_review_decision(APPROVED_WITH_EDIT)` **直接抛
  `VisualRegistryError`**（要求"先把编辑内容落成 `new_version`，再对新版本 approve"，
  绝不原地改）。→ 见 §3 R9。
- `VisualAssetStep.validate()` 用
  `VisualAssetValidator.validate_profile_dict(kind, profile, require_complete=True)`
  → **必填字段门槛**：character = `appearance/face/hair/clothing`；
  location = `environment/lighting/palette`；prop = `form/color`；style = `medium/palette`。
  **mock 档案必须齐这些字段**，否则重试耗尽 → FAILED（见 §3 R5）。

**"实体 → 建档 → 审批 → 写回"调用序列（★问题④结论，已实测 P6 全绿）**：

```text
① 实体收集（确定性，不靠 LLM，见 §1.7）
② 若 registry.find(kind, name) 已有 approved 版本 → 跳过（已齐备）
③ VisualAssetStep(spec={"mode":"plan","kind":k,"name":n,"semantic_data":…}).run(ctx)
   → 产出快照 dict {kind, source, profile, visual_prompt, created_at, complete, name}
   → 经 Review（needs_review=True）
④ asset = registry.register(kind, name)                      # 返回 CHAR_001 / LOC_001 …
⑤ ver   = registry.new_version(asset.asset_id, profile=快照["profile"],
                               visual_prompt=快照["visual_prompt"],
                               source=快照["source"], status="draft")
⑥ registry.submit_for_review(asset.asset_id, ver.version)     # draft → reviewing
⑦ registry.approve(asset.asset_id, ver.version)              # reviewing → approved
   （若 Review 决策是 rejected → registry.reject(...)）
⑧ 齐备判定：registry.active_profile(kind, name) is not None
```

【实测 P6】`register → CHAR_001` / `new_version → v1 draft` /
`submit_for_review → reviewing` / `approve → approved current_version=v1` /
`active_profile(...) = True` / `registry.json 落盘 = True`；重复 `register` 正确报
`VisualRegistryError`；补齐 location/style 后 `VisualComposer.compose(shot)` 成功
（`char_section.asset_id == "CHAR_001"`，`"red wool coat" in render_prompt() == True`）。

### 1.6 审核回调契约（问题⑤，★实测）

`review_handler(artifact, context) -> 对象`，对象需有：
`.decision`（`ReviewDecision` 三态之一）、`.review_id`；
`approved_with_edit` 时还需 `.edited_content`（或 `.edited_artifact`）。

【实测 P7】三种决策的真实结果：

| decision | Step 状态 | run 状态 | 产物文件 |
|---|---|---|---|
| `approved` | `APPROVED` | `success` | `fixed.json` |
| `approved_with_edit` | `APPROVED_WITH_EDIT` | `success` | `fixed.json` + **`fixed_v2.json`**（引擎写编辑后新版本） |
| `rejected` | `REJECTED` | **`failed`** | `fixed.json` |

**三种策略的实现形状**：

```python
# ① auto_approve（★必须显式标注为程序化模拟）
def _auto_approve(artifact, context):
    n = state["auto_n"] = state.get("auto_n", 0) + 1
    return _Decision(ReviewDecision.APPROVED, f"auto-{n}")

# ② scripted:<file>（测试用：按预置决策序列出队）
def _scripted(seq):                       # seq: [{"decision":…, "edited_content":…}, …]
    it = iter(seq)
    def h(artifact, context):
        item = next(it, None)
        if item is None:
            raise OrchestrationError("scripted 审核决策已耗尽（明确报错，不静默）")
        return _Decision(item["decision"], f"scripted-{…}", item.get("edited_content"))
    return h

# ③ human_stub（停下等外部注入）
def _human_stub(artifact, context):
    raise OrchestrationError("需人工介入：等待外部注入审核决策（human_stub）")
```

`_Decision` = 最小鸭子类型对象（`.decision` / `.review_id` / `.edited_content`）。
**可选增强**：用 `ReviewService(pid, ArtifactStore(artifacts_dir), projects_root=…)` 做基座
（`submit_for_review` → `approve/reject` → `to_review_result`），可同时落审核记录；
【实测 P8】该基座可用（`step.status=APPROVED`，`reviews/review-*.json` 落盘）。
**纪律**：编排层**不实现第二套审核状态机**，只用既有 `ReviewDecision` 三态。

### 1.7 §7.3 实体收集的字段依据（`v2/core/shot.py`，禁改）

```python
Storyboard.flatten_shots() -> [Shot]     # 按 Beat 顺序展平，保序
Shot.characters : List[str]              # → ("character", name)
Shot.location   : str                    # → ("location", loc)   （空则继承 Storyboard.location）
Storyboard.location : str
Shot.assets     : List[ShotAsset(kind, ref, detail)]  # kind=="prop" → ("prop", ref)
```

收集规则（**确定性，不靠 LLM**）：

```text
entity_set = ∪ over 本集全部 storyboard artifact 的 flatten_shots():
    {("character", c)  for c in shot.characters if c}
  ∪ {("location", shot.location or storyboard.location)}   （非空才收）
  ∪ {("prop", a.ref) for a in shot.assets if a.kind=="prop" and a.ref}
```

### 1.8 ArtifactStore 版本探测（`v2/workflow/artifact.py`，禁改）

`next_version(name)` 用 `glob(f"{name}*{ext}")` 再按 stem 校验 → **不会串号**
（`storyboard_1*.json` 会 glob 到 `storyboard_10.json`，但 stem 校验把它排除）。
v1 无后缀、v≥2 带 `_vN`。**绝不覆盖**。

### 1.9 `core.io.write_json` **不是原子写**（⚠️ 指令措辞有误）

【实测 P14】`inspect.getsource(write_json)` 中**不含** `os.replace` / `tempfile` /
`.tmp` / `rename` 任一关键字 → 它是 `path.open("w")` + `json.dump`，**直接覆盖写**。
→ 指令 §6.3 "原子写（复用 `core.io.write_json`）"**与实现不符**（见 §8 D-4）。

---

## 2. 接线方案（S2）

### 2.1 ★ 核心结论：指令 §7.1 的伪代码不可实现，必须改为"两段式"

指令 §7.1 把 `[SceneGenerationStep(...), StoryboardStep(...)]` 放进**同一个 run**，
并同时给 `inputs["scene"]` 赋"该场景的 Scene 或上游产出"。

【实测 P3】**这个形状做不到**：
- 上游 step 产出 `sceneA(who=["Mercy"])`；`inputs["scene"]` 放 `sceneB(who=["Caleb"])`；
- 跑完 `storyboard_2.json.characters == ["Caleb"]`（即用了 `inputs["scene"]`），
  内层 `shots[0].characters == ["Caleb"]`，且 `scene_1.json` 确实也落了盘；
- → **`StoryboardStep` 完全忽略上游 Step 的产出，只读 run 开始时固定的 `inputs["scene"]`。**

原因见 §1.2 要点 A / §1.1 要点 A：`inputs` 是**整次 run 共享且不可中途修改**的，
而 `StoryboardStep` 是禁改件，不可能改成读 `context.get_output(...)`。

**⚠️ 若照抄伪代码的后果**：要么 Storyboard 用了**过期的 scene**（与刚生成的 scene 不一致 →
`Shot.scene_id` / `characters` 与 artifact 名错位，正是"串场"），
要么干脆 `inputs["scene"]=None` → `StepError` 重试耗尽 → FAILED。

**正确形状（零改引擎、零改 Agent）：两段式，每段每场景一次 run。**

```text
Phase A —— 剧本段（每场景一次 run）
  workflow_id = f"scene_gen_{sid}"
  steps       = [SceneGenerationStep(agent, scene_id=sid)]
  inputs      = {"assembled_context": ctx, "scene_spec": {...}}
  → scene_{sid}.json（approved）

Phase B —— 分镜段（每场景一次 run）
  workflow_id = f"storyboard_{sid}"
  steps       = [StoryboardStep(agent, scene_id=sid)]
  inputs      = {"assembled_context": ctx, "storyboard_spec": {...},
                 "scene": <Phase A 的 approved Scene 对象>}
  → storyboard_{sid}.json（approved）
```

Phase B 的 `scene` 来源：`run_a.steps[f"scene_generation_{sid}"].approved_artifact.read()`
→ `Scene.from_v01(data, id=sid)`（**编排层读盘是允许的；Agent 才禁止读盘**）。
resume 场景下改为读 `state.json` 记录的 `scene_{sid}.json`。

**为什么这仍是"指令 §7.1 的立意"（而非绕路）**：
① 零改引擎；② 每个场景仍是独立的编排单元（`step_id` 早已带 `scene_id`）；
③ resume 天然可用；④ 未来可并行（N 个 run 互不依赖）；
⑤ 只是把"一次 run 装两个 step"纠正为"一个 run 一个 step"，**语义更干净**
（每段 run 恰好一个 step，成功/失败边界清晰）。

### 2.2 集级 Casting（一次 run）

```text
workflow_id = f"casting_ep{episode}"
steps       = [CastingStep(agent)]
inputs      = {"assembled_context": ctx}
→ casting.json（approved）
```

**Casting 通过后必须把角色写进 Memory**（否则下游必然失败，见 §3 R2）：

```python
names = [e["name"] for e in casting_entries if e.get("name")]
existing = [c["name"] for c in memory.get_characters() if c.get("name")]
memory.set_characters([{"name": n} for n in dedup(existing + names)])
memory.set_world(project.world.to_v01() if hasattr(project.world, "to_v01") else {...})
```

依据：`v2/logs/pre_stage8_architecture_review/stage8_preflight_report.md` L142 已确立
**"角色清单 → `set_characters`（沿用 stage6 casting 产物），scene/shot 步
`needs=["characters"]` 读取"** 的约定；`stage11` 的真实冒烟脚本
`run_real_storyboard.py` L131-132 也是这么做的。
**只写 characters / world；绝不 `approve_fact`（facts 三态不动，D2 铁律）。**
→ 该动作**指令未明写**，属必要补全，**请总控裁决**（§9 待裁决 A-1）。

### 2.3 视觉资产段（每实体一次 run）

```text
for (kind, name) in entity_set:                      # §1.7 确定性收集
    if registry.active_profile(kind, name):          # 已齐备 → 跳过
        continue
    workflow_id = f"visual_asset_{kind}_{safe(name)}"
    steps       = [VisualAssetStep(agent, step_id=f"visual_asset_{kind}_{safe(name)}")]
    inputs      = {"visual_asset_spec": {"mode":"plan","kind":kind,"name":name,
                                         "semantic_data": {...}}}
    → visual_asset_{kind}_{name}.json（approved）
    → §1.5 的 ④~⑦ 建档/版本/审批
```

`semantic_data` 的确定性来源（不调 LLM）：character → Casting entry 的 `character` 档案；
location → `Project.world` / AssetRegistry 的地点名；prop → 收集到的名字本身。

### 2.4 组件构造序列（B 会话照此实现）

```python
projects_root = <可注入，默认 V2_ROOT/"projects">          # ★必须可注入（测试指向 tmp_projects）
project_dir   = projects_root / project_id
artifacts_dir = project_dir / "artifacts"
run_log_dir   = project_dir / "orchestration" / "runs"
state_path    = project_dir / "orchestration" / "state.json"
report_path   = project_dir / "orchestration" / f"run_{run_id}.json"

memory    = MemoryStore(project_id, projects_root=projects_root)   # 0 改
assets    = AssetRegistry.load(V2_ROOT / "assets" / "locations")
assembler = ContextAssembler(memory, assets)
vreg      = VisualAssetRegistry(project_dir)
handler   = <按 review_policy 造，见 §1.6>

# 每一段 run 之前都要重新 assemble（Memory 在 casting 后变了）
placeholder = WorkflowContext(project_id=pid, workflow_id=wf_id, run_id="pre",
                              inputs={}, created_at="", updated_at="")
ctx = assembler.assemble(step, placeholder, project_title=project.title)

engine = WorkflowEngine([step], artifacts_dir=artifacts_dir,
                        run_log_dir=run_log_dir, review_handler=handler)
run = engine.run(project_id=pid, workflow_id=wf_id, inputs=inp, max_attempts=…)
```

**⚠️ 必须可注入 `projects_root`**：`v2/projects/` 与 `logs/**/tmp_projects/` 都被
`v2/.gitignore` 忽略，但 C 会话必须在 `tmp_projects/` 下跑（**绝不碰真实 `v2/projects/`**）。
`MemoryStore` / `ReviewService` 已支持 `projects_root=`；`VisualAssetRegistry` 吃
`project_dir`；编排层自己也要接受 `projects_root`。

### 2.5 `inputs` 完整约定表（B 照此实现，C 照此造用例）

| 段 | workflow_id | step_id | inputs 键 | 值形状 |
|---|---|---|---|---|
| Casting | `casting_ep{N}` | `casting` | `assembled_context` | `AssembledContext` |
| 剧本 | `scene_gen_{sid}` | `scene_generation_{sid}` | `assembled_context`、`scene_spec` | `{scene_id, episode, location, characters, objective, time, constraints}` |
| 分镜 | `storyboard_{sid}` | `storyboard_generation_{sid}` | `assembled_context`、`storyboard_spec`、`scene` | `{scene_id, episode, target_beat_count?, …}` + **`Scene` 对象**（推荐）或 `to_v01()` dict |
| 视觉 | `visual_asset_{kind}_{name}` | `visual_asset_{kind}_{name}` | `visual_asset_spec` | `{mode:"plan", kind, name, semantic_data}` |

### 2.6 编排状态与 resume 语义（★问题② 结论）

**结论：编排层对"已 approved 的单元"直接跳过、不调用引擎；不使用引擎的 `resume_from` 做跨 run 恢复。**

理由：

1. `resume_from` 的参数是 **`WorkflowRun` 对象**；编排层持久化的是 `state.json`。
   要用它就得再存一份完整 `WorkflowRun`（= 第二套状态），与指令 §6.3
   "编排层唯一事实源"冲突（违反"不造第二套机制"）。
2. 本阶段**每个 run 只有 1 个 step** → 该 step 要么成功要么整个 run 失败，
   **run 内 resume 无意义**（没有"部分完成"的中间态）。
3. **跳过语义更强**：已 approved 的单元**不调用引擎 → 不产生任何新 Artifact 版本**
   （幂等性最好）。而 `resume_from` 会新起一个 run（虽然也不写盘，但多一次 run 记录）。
4. `resume_from` 的能力**不冲突、不浪费**：保留给"未来一个 run 内多 step 的恢复"场景。

**state.json 的 `status` 是对既有 Review/Artifact 结果的镜像**，不是新判据：

```text
approved  ⇐ 该步的 StepResult.is_success 且 approved_artifact 非空
             （含 SUCCESS / APPROVED / APPROVED_WITH_EDIT 三种引擎状态）
failed    ⇐ StepResult.status ∈ {FAILED, REJECTED}
pending   ⇐ 未执行 / SKIPPED
```

**⚠️ 要点 H**：resume 跳过前**必须校验磁盘 artifact 仍存在**（人工删文件 / 换机器场景），
否则"状态说齐备、磁盘没有"→ 静默不齐备。缺失 → 视为 pending 重跑（或明确报错）。

### 2.7 编排状态与运行报告的形状（指令 §6.3 照抄，补 3 点）

- `state.json`：`{version, project_id, updated_at, episodes:{N:{casting, scenes:{sid:{scene, storyboard}}}}, visual_assets:{registered, pending}}`
- `run_<run_id>.json`：`{project_id, episode, run_id, started_at, ended_at, status, stages, completeness, warnings, errors}`
- **补充 1**：`stages.casting` / `stages.scenes.{sid}` 各项须带 `artifact` 文件名、
  `status`、`attempts`、`run_id`、`workflow_id`（便于定位到具体 run）。
- **补充 2**：`run_report` **必须**带 `review_mode` 字段（指令 §7.4 硬要求）：
  `"auto_approve (programmatic, NOT human review)"`。
- **补充 3**：`completeness` 四对数：
  `scenes_total/scenes_done`、`storyboards_total/storyboards_done`、
  `assets_referenced/assets_approved`。**必须与磁盘实际文件交叉核对一致**（T19）。

---

## 3. 风险清单（按严重度排序）

| # | 风险 | 影响 | 触发条件 | 处置 |
|---|---|---|---|---|
| **R1** | **指令 §7.1 伪代码不可实现**（同 run 内 Storyboard 读不到上游 scene）【实测 P3】 | 串场 / `StepError` 重试耗尽 → FAILED | 照抄伪代码 | **两段式**（§2.1）；C 会话加"同 run 串场"反例断言 |
| **R2** | **Memory 未写角色 → Context `characters` 为空**【实测 P10/P11】 | 真实 LLM 拿不到角色白名单 → 编造名字 → `SceneGenerationAgent._normalize` **不回锚 lines 的 speaker/position** → `UNKNOWN_CHARACTER` → 重试耗尽 FAILED | casting 后未 `set_characters`；或首次运行 Memory 为空 | casting approved 后 `set_characters`（§2.2）；**报备总控（A-1）** |
| **R3** | preflight 在 `to_v01()` dict 上判 → `Line.type` 不存在 | move 行 destination **完全漏检** → 漏到 Stage 11 → 重试风暴 | 直接对落盘 dict 做 preflight | 先 `Scene.from_v01(data, id=sid)`（§1.3 要点 E）【实测 P12】 |
| **R4** | **mock 只用 `script={step:…}` → 多场景返回同一份内容** | T06 "互不串场"**无法真正证明**（三份 artifact 内容相同也算"通过"） | C 会话用默认 mock 写法 | **场景感知 mock**：按 user payload 里的 `scene_id` 出不同内容（输出带 scene 标记），或加 spy 记录每步看到的 `scene_id` |
| **R5** | `VisualAssetStep.validate` 要求 `require_complete=True` | mock 档案缺必填 → 3 次重试耗尽 → FAILED（视觉齐备永远达不到） | mock 档案不完整 | mock 给全必填（§1.5）；C 会话加"缺必填 → 重试耗尽"负例 |
| **R6** | `VisualAssetStep.step_id` 默认 `"visual_asset"` + spec 是单 dict【实测 P9】 | 同 run 多实体：`step_id` 撞车、`run.steps` 字典覆盖、spec 串场 | 一个 run 塞多个 visual_asset step | **一实体一 run** + 传 `step_id=`（§2.3） |
| **R7** | `apply_review_decision(APPROVED_WITH_EDIT)` 直接报错 | 视觉资产走编辑后通过 → 报错 | 用 `apply_review_decision` 处理编辑决策 | 编辑路径必须"先 `new_version` 再 `approve`"；或本阶段视觉资产**只支持 approved/rejected**，编辑决策明确报错（**建议后者**，见 R9） |
| **R8** | `SceneGenerationStep` 用 `self.scene_id` 命名（**不读 spec**） | 多场景 artifact 名与实际 scene_id 错位 | 只传 spec 不按 sid 构造 Step | 按 sid 构造 Step 实例（§1.2 要点 B） |
| **R9** | 视觉资产的"编辑后通过"缺语义来源：`Review` 的 `edited_artifact` 是 **Artifact 形态**，不是 profile | 编排层无法确定性把 edited 内容转成 profile → 要么静默错、要么崩溃 | `review_policy` 对视觉资产产出 `approved_with_edit` | **建议本阶段视觉资产只支持 approved/rejected；`approved_with_edit` → 明确报 `OrchestrationError`**（不隐式解释）。请总控确认（A-2） |
| **R10** | `core.io.write_json` **不是原子写**【实测 P14】 | 指令 §6.3 "原子写"措辞与实现不符；崩溃可能留半截 state.json | 照指令直接调 `write_json` | `state.py` 自己实现**临时文件 + `os.replace`**（属编排层内部实现，不碰 `core/`）；或修正指令措辞。见 §8 D-4 |
| **R11** | Casting `new` 角色档案形状 vs `set_characters` 期望形状 | 写入形状不对 → 下游 `_characters_from` 拿不到 name | 直接把 `Character.to_v01()` 塞进去 | 至少保证 `{"name": …}`；可附带其他键（agent 只读 `name`） |
| **R12** | resume 时 state 说齐备、磁盘 artifact 被删 | 静默不齐备（run_report 撒谎） | 人工删文件 / 换机器 | 跳过前校验文件存在；缺失 → pending 重跑（§2.6 要点 H） |
| **R13** | `run_spec.scene_ids` 重复 | 同名 artifact 被版本化（不覆盖但语义混乱）、`state` 键覆盖 | 调用方传重复 id | 入参去重 + 明确报错 |
| **R14** | 多场景共用 `artifacts_dir` | 文件名靠 `_{sid}` 区分；sid 冲突才出问题 | 见 R13 | 同 R13；`step_id` 已带 sid，天然独立 |
| **R15** | `Project` / `Episode` / `World` 无 `to_dict`（V0.1 兼容模型） | 试图把整个 Project 序列化进 state.json → 崩 | B 会话"顺手"序列化 Project | **只序列化编排自有状态**；Project 只被消费（指令 §6.1 已写明） |
| **R16** | 历史回归重写 6 个报告产物（T-6）+ 根目录 `llm_calls.jsonl`（T-1） | `git status` 出现无关 dirty、记账泄漏 | 每次跑套件 | 交付前复位（§5.2） |
| **R17** | 真实冒烟（§十三）≤5 次文本调用 | 无配置时无法执行 | 无 `DEEPSEEK_API_KEY` | 明确报"阻塞：需人工配置"，Mock 路径照常交付；**不伪造** |
| **R18** | `subject` / `visual_description` 等自由文本质量不可控 | 合法但没用的 Storyboard | 恒存在 | 本阶段接受；质量门槛靠 `needs_review=True` 的人工审核节点承接 |

---

## 4. 测试方案（C 会话执行）

目录 `v2/logs/stage12_lifecycle/run_tests.py`，沿用既有
`check(name, ok, detail)` + `_results` + `sys.exit(1 if FAIL else 0)` + 内嵌历史回归子进程
（**照抄 stage11 的 `_clean_child_env()` / `run_history()` 结构**，
`HISTORY_SUITES` 由 8 项扩为 **9 项**（加 `("stage11","stage11_storyboard")`）。

### 4.1 T01~T19 → 可执行断言建议

| 编号 | 断言要点（可直接翻译成代码） |
|---|---|
| **T01** | `ProjectOrchestrator` 可构造；`OrchestrationError` 存在且继承明确基类；**依赖方向**：① `v2/orchestration/*.py` 的 import 只落在 `core/llm/memory/context/workflow/review/agents/config`（正则/AST 扫）；② 反向扫 `v2/core`、`v2/llm`、`v2/memory`、`v2/context`、`v2/workflow`、`v2/review`、`v2/agents` 全部源码，**不得出现 `orchestration`** |
| **T02** | `OrchestrationState` 读写往返（`to_dict`/`from_dict` 一致）；写入后文件存在且 JSON 合法；**原子写**：写入过程不残留 `*.tmp`（若自实现）；resume 判定：`approved` 条目 → `should_skip=True`；`failed`/缺失 → `False`；**磁盘 artifact 被删 → 不跳过**（R12） |
| **T03** | `preflight_check(scene, known_characters, registry) -> List[str]`：5 条规则**逐条正/反例**（用 §1.4 的 6 缺口形状 + 好 Scene `[]`）；**返回 list、不抛异常**（含 `scene=None` / 空 who / 空 where / 非法 registry 的边界）；**必须用 `Scene` 对象**（传 `to_v01()` dict 会漏 move 行 → 加一条专测） |
| **T04** | 集成：坏 Scene（`who` 含未登记角色）→ 编排层抛/记 `OrchestrationError` 并**列出缺口**；**断言 `MockTextProvider.calls` 里 `storyboard_generation` 调用次数 == 0**（证明 Stage 11 未被调用 / 无 3 次重试） |
| **T05** | 单场景 E2E（mock）：1 集 × 1 场景 → `casting.json` + `scene_1.json` + `storyboard_1.json` 全部落盘；`Validator` 全过；所有 Shot `status=="planned"`；`run_report.completeness` 数值正确 |
| **T06** | ★**多场景正确性（核心）**：1 集 × 3 场景 → ① `storyboard_N.json["scene_id"] == N`；② 内层所有 `shot.scene_id == N`；③ `characters` 与该场景 `who` 一致；④ **内容互不串场**：用**场景感知 mock**（输出带 `scene-N` 标记），断言 `storyboard_1.json` 文本**不含** `scene-2`/`scene-3` 标记（反之亦然）；⑤ 三份 `scene_N.json` 也各自正确 |
| **T07** | 三次 run 的 `workflow_id` / `run_id` 互异；`step_id` 为 `storyboard_generation_1/2/3`；`ArtifactStore.list_files("storyboard_1")` 与 `storyboard_2`/`_3` **不交叉** |
| **T08** | 同一场景重跑 → `storyboard_1_v2.json` 出现，**`storyboard_1.json` 字节不变**（读两次 bytes 比对）；`casting` 同理 |
| **T09** | Review 三态（scripted 决策序列）：`approved` → 继续；`approved_with_edit` → 新版本文件 + 原文件字节不变；`rejected` → run failed；**`run_report["review_mode"]` 含 `auto_approve (programmatic, NOT human review)`**；**不得**出现"已人工审核"字样 |
| **T10** | 视觉齐备：`entity_set` 汇总去重正确（character/location/prop 三类，构造含重复引用与空 location 的场景）→ 每个实体经 `VisualAssetStep(plan)` 建草稿 → approved → registry 写回 → `completeness.assets_approved == assets_referenced`；`registry.json` 存在；重复实体**不重复建档** |
| **T11** | resume：跑完一集 → 改 `run_spec`（如加一场景）重跑 → 已完成部分 `SKIPPED`、**不重跑、不产生新版本**（比对文件列表与字节）；`state.json` 更新正确 |
| **T12** | 失败传播：某场景 preflight 失败 / 校验失败 → `run_report["status"] ∈ {"partial","failed"}`；`errors` 能定位到具体 `scene_id`；**不静默成功**（`status != "success"`） |
| **T13** | `get_api_counter()` 快照：`real_api_requests == 0`（程序化断言，**不人工数数**）；`mock_requests` 与预期调用次数一致 |
| **T14** | **Token 硬证据**：① 注册视觉资产前后 `json.dumps(sb.to_dict(), sort_keys=True)` **逐字节相等**；② `storyboard_*.json` 文本内**不含**档案关键词（如 mock 里放的 `"red wool coat"` / `"amber and wood tones"`）；③ **场景间不膨胀**：`storyboard_3.json` 文本**不含** `scene-1`/`scene-2` 的内容标记，且三份 artifact 大小与各自场景复杂度同量级 |
| **T15** | `compileall v2/orchestration`；import 冒烟；`v2/orchestration/` 与 `v2/logs/stage12_lifecycle/` 新文件扫 `"sk-"` **无命中** |
| **T16** | 禁改清单 0 diff（§8.3 全部 + `FilmAgent/` + 仓库根 `.gitignore`）—— 用 `git diff --stat HEAD -- <paths>` |
| **T17** | ★**`v2/workflow/` 0 diff**（本阶段关键：证明"上层编排"未污染引擎） |
| **T18** | 历史回归：`stage1/3/4/6/7/8/9/10/11` **九套**子进程全部 rc=0（复用 `_clean_child_env`） |
| **T19** | 端到端完整性：`run_report.completeness` 与**磁盘实际文件**交叉核对一致（`scenes_done` == 磁盘 `scene_*.json` 数、`storyboards_done` == `storyboard_*.json` 数、`assets_approved` == registry 中 approved 数） |

### 4.2 额外建议（超出指令，低成本高价值 —— C 会话酌情采纳）

- **T03b（R3 专测）**：preflight 传 `to_v01()` dict（无 `type` 键）→ 断言 move 行**必须**被检出（
  用 `Scene.from_v01` 才正确）。这是最易漏的一条。
- **T04b（R2 专测）**：Memory 空 + `known_characters` 非空 → 断言 `characters` ContextItem 为空，
  且编排层**在 casting 后已 `set_characters`**（用 spy 或直接读 `memory.get_characters()`）。
- **T06b（R4 专测）**：断言 mock 是"场景感知"的（若 mock 对 3 个场景返回同一内容 → 该测试**应失败**，
  以证明测试本身有效）。
- **T09b**：`review_policy="human_stub"` → 抛 `OrchestrationError` 且**未产生 artifact**。
- **T10b（R5 专测）**：mock 档案缺必填 → 重试耗尽 → 该实体记 `pending`，
  `assets_approved < assets_referenced`，`status != "success"`。
- **T18b**：跑前后 `git status --short` 对比，把 T-6 从"靠人记得"变成"程序化发现"（实现麻烦可不做）。

### 4.3 测试素材纪律

```text
tmp_projects/ 清理用 _hard_rmtree（os 原语），绝不用 shutil.rmtree（宿主 shim）
编排层必须接受 projects_root 注入 → 测试全部指向 v2/logs/stage12_lifecycle/tmp_projects/
从仓库根跑完 → 立刻 rm -f llm_calls.jsonl（§3.2 坑 2）
交付前复位 6 个历史报告产物（§3.2 坑 1）
```

---

## 5. 禁改清单核实 + 交付前复位

### 5.1 禁改清单（指令 §8.3，逐条核实"存在且应 0 diff"）

```text
FilmAgent/ 全目录                                    V0.1 冻结
仓库根 .gitignore                                    V0.1 基线段
v2/workflow/ 全部（engine/step/artifact/errors/models）★本阶段关键
v2/core/shot.py  adapter.py  models.py  assets.py  validator.py  io.py  token_log.py
v2/core/visual.py  visual_registry.py  visual_compose.py
v2/agents/base.py  casting.py  scene_generation.py  storyboard.py  visual_asset.py
v2/llm/ 全部（client / router / providers / request_counter）
v2/memory/  v2/review/  v2/context/
全部历史测试套件（stage1/3/4/6/7/8/9/10/11 断言一个不动）
```

**允许新增（§8.1）**：

```text
v2/orchestration/__init__.py  state.py  preflight.py  pipeline.py     （全部新增）
v2/logs/stage12_lifecycle/run_tests.py  run_real_lifecycle.py  stage12_report.md
v2/docs/*、CLAUDE.md（D 会话文档同步）
```

**A 会话判定**：本阶段**不需要**修改 `validator.py` / `shot.py` / 任何 Agent /
任何 `v2/llm/` 文件。**唯一可能触及的"允许修改"文件是 `v2/agents/__init__.py`
（纯追加导出）——但 A 会话判定：也不需要**。理由：`VisualAssetAgent` /
`VisualAssetStep` **当前未在 `agents/__init__.py` 导出**（实测 `__all__` 只有 8 项），
而编排层可以像 stage11 的测试那样**直接从子模块 import**
（`from agents.visual_asset import VisualAssetAgent, VisualAssetStep`）→ **零 diff**。
若 B 会话出于风格一致性想追加导出，属"纯追加"，但**非必要**（最小增量优先）。

### 5.2 交付前必做的复位（两条命令，A 会话实测有效）

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
git status --short        # 期望：只剩 Stage 12 的预期新文件
git diff --stat -- v2/workflow/   # 期望：空
```

---

## 6. 给 B 会话的最小执行清单

1. **起手自检**：`env -u PYTHONPATH python v2/logs/stage11_storyboard/run_tests.py`
   确认 65/65（**跑完立刻做 §5.2 的复位**）。
2. 新建 `v2/orchestration/preflight.py`：`preflight_check(scene, known_characters, registry) -> List[str]`
   （纯函数，5 条规则，**先 `Scene.from_v01`**，返回 list 不抛异常）。
3. 新建 `v2/orchestration/state.py`：`OrchestrationState` + **原子写**（临时文件 + `os.replace`）
   + `should_skip`（含 R12 的"文件存在性校验"）。
4. 新建 `v2/orchestration/pipeline.py`：`ProjectOrchestrator`，按 §2.1 的**两段式** +
   §2.2 集级 casting（**含 `set_characters`**）+ §2.3 视觉资产 + §1.6 三种 review 策略 +
   §2.6 resume（跳过不调用）+ §2.7 run_report。**构造参数必须含 `projects_root`**。
5. 新建 `v2/orchestration/__init__.py`：导出 `ProjectOrchestrator` / `OrchestrationError` /
   `OrchestrationState` / `preflight_check`。
6. 写 T03/T05/T06 关键单测，先让"preflight 正反例 + 单场景 E2E + 多场景不串场"绿。
7. **不要碰** §5.1 的任何文件；**不要**改 `v2/workflow/`（遇到"必须改引擎"→ 立即停下报总控）。
8. 完成后按 §5.2 复位并报告；**不 commit**。

---

## 7. AC 逐条映射自查（指令 §十四）

| AC | A 会话的判断 |
|---|---|
| AC1 新套件全绿 + 历史回归 9 套 | §4 的 T01~T19 可执行；历史回归有现成范式（加 stage11） |
| AC2 V0.1 冻结 / tag 未移动 | 已核实当前 0 diff；6 个 tag 指向未变 |
| AC3 零侵入（★`v2/workflow/` 0 diff） | §5.1 已逐条核实；**判定无需改任何禁改文件** |
| AC4 端到端成立（1 集 × 3 场景） | §2.1 两段式 + §2.2 + §2.3 可达；**R2 必须补 `set_characters`** |
| AC5 ★多场景正确性 | T06 可写；**但必须用场景感知 mock（R4），否则测试无效** |
| AC6 前置校验门生效 | T03/T04 可写；**必须用 `Scene` 对象（R3）** |
| AC7 resume 生效 | §2.6 结论（跳过不调用）+ T11 可写 |
| AC8 机制复用（无第三套状态机） | §1.6 + §2.6；`needs_review=True` + 既有 Review/Artifact/Retry |
| AC9 真实 API ≤5 次文本 + 0 视觉 | 默认 mock；`run_real_lifecycle.py` 独立脚本；无配置 → 明确阻塞 |
| AC10 静态检查全过 | T15 + T01（依赖方向） |
| AC11 文档同步 | D 会话；本文件即 A 会话的文档产出 |
| AC12 Git 无泄漏 | §5.2 两条复位命令 |
| AC13 诚实性（auto_approve 标注） | T09 断言 `review_mode` 字段；**不得**写"已人工审核" |

---

## 8. 发现的指令缺陷（⚠️ + 建议修正）

| # | 缺陷 | 位置 | 影响 | 建议修正 |
|---|---|---|---|---|
| **D-1** | HEAD 描述过期 | 指令 §二 / 抬头 | 无（锚点看 tag） | 改为"锚点 = `v0.2-stage11` tag = `ef18f0d`；HEAD 可因纯文档 commit 前移" |
| **D-2** | ★**§7.1 伪代码把 SceneGenerationStep 与 StoryboardStep 放进同一 run** | 指令 §7.1 | **该形状不可实现**（实测 P3：Storyboard 只读 `inputs["scene"]`，忽略上游产出） | 改为**两段式**：`scene_gen_{sid}` 与 `storyboard_{sid}` 各一次 run（§2.1）；"每场景一次 run"的表述改为"**每段每场景一次 run**" |
| **D-3** | §6.1 未写"编排层负责把 Casting 产物写入 Memory" | 指令 §6.1 / §五 | 不写 → Context `characters` 为空 → 场景生成必然重试耗尽（实测 P10/P11） | 在 §6.1 增加"集级 Casting 通过后，编排层 `set_characters`（沿用 casting 产物）+ `set_world`；**不 approve facts**" |
| **D-4** | §6.3 写"原子写（复用 `core.io.write_json`）" | 指令 §6.3 | 措辞与实现不符（实测 P14：`write_json` **不是**原子写） | 改为"**编排层自行实现原子写**（临时文件 + `os.replace`），复用 `core.io.write_json` 的序列化行为（`ensure_ascii=False` / `indent=2`）" |
| **D-5** | §7.3 未指定 `semantic_data` 的来源 | 指令 §7.3 | B 会话需自行决定 → 可能去调 LLM（违反"不靠 LLM"） | 明确"`semantic_data` 由**确定性数据**构成：character ← Casting entry 档案；location ← world/registry；prop ← 名字本身" |
| **D-6** | §十二 T06 未提示"mock 必须场景感知" | 指令 §12.1 | 用默认 `script={step:…}` mock 时 T06 形同虚设（R4） | 在 §12.1 T06 增加"测试须使用按 `scene_id` 出不同内容的 mock（或 spy），否则无法证明不串场" |
| **D-7** | §十二 T16 未列 `v2/core/io.py` / `token_log.py` | 指令 §8.3 | 清单不完整（本阶段确实 0 改，但应显式） | 在 §8.3 禁改清单补 `v2/core/io.py`、`v2/core/token_log.py` |
| **D-8** | §8.2 未提"`projects_root` 必须可注入" | 指令 §8.2 / §6.2 | C 会话若不注入，测试会写进真实 `v2/projects/`（虽被 gitignore，但污染本机数据） | 在 §八 增加"编排层构造参数须含 `projects_root`（默认 `V2_ROOT/'projects'`），测试注入 `tmp_projects/`" |

**未发现"编排层必须改 `v2/workflow/`"的架构信号。** 实测证明"上层编排"路径成立
（零改引擎，多次 run 安全，Artifact 不覆盖）。**§十七 的暂停条件未触发。**

---

## 9. 需要总控裁决的事项

| # | 事项 | A 会话建议 | 依据 |
|---|---|---|---|
| **A-1** | **编排层写 Memory（`set_characters` / `set_world`）是否批准？** | **批准**（必要补全） | 实测 P10/P11：不写则 Context `characters` 为空 → 真实 LLM 编造名字 → `SceneGenerationAgent._normalize` **不回锚** lines → `UNKNOWN_CHARACTER` → 重试耗尽 FAILED。且 `stage8_preflight_report.md` L142 已确立"角色清单沿用 casting 产物"的约定。**只写 characters/world，绝不 `approve_fact`。** |
| **A-2** | **视觉资产的 `approved_with_edit` 如何处理？** | **本阶段不支持**：产出该决策 → 明确报 `OrchestrationError`（只支持 approved / rejected） | 实测：`apply_review_decision(APPROVED_WITH_EDIT)` 直接抛错；且"编辑后的 profile"缺确定性来源（`Review.edited_artifact` 是 Artifact 形态）。隐式解释会引入不确定语义（违反"不静默"）。 |
| **A-3** | **"每场景一次 run" 是否接受改为"每段每场景一次 run"（两段式）？** | **接受**（这是指令 §7.1 立意的正确落地） | 实测 P3：同 run 装两 step 不可实现；两段式零改引擎、语义更干净、resume 天然可用 |
| **A-4** | **`state.py` 自行实现原子写是否越界？** | **不越界**（编排层内部实现，不碰 `core/`） | 实测 P14：`write_json` 非原子；指令 §6.3 措辞有误（D-4）。替代方案是修正指令措辞、接受非原子写。 |

---

## 10. 五个问题的结论（每条一句话）

1. **多场景分发**：`WorkflowEngine` 多次调用 / 新建 engine **均无副作用**（无跨 run 可变状态），
   同名 Artifact 靠 `ArtifactStore` 递增版本**绝不覆盖**、名字带 `_{scene_id}` 互不串号；
   **但**"一个 run 装 SceneGenerationStep + StoryboardStep"**不可实现**（实测：StoryboardStep
   只读 `inputs["scene"]`，忽略上游产出）→ 必须改为**两段式、每段每场景一次 run**。
2. **resume 语义**：引擎 `resume_from` 是**按 run + 按 step_id 的 is_success** 跳过（实测 SKIPPED
   且不写新版本）；编排层应**对已 approved 的单元直接跳过、不调用引擎**（而非传 `resume_from`），
   因为编排层的持久化事实源是 `state.json`，用它就不必再存一份 `WorkflowRun`（避免第二套状态）。
3. **前置校验门**：5 条规则的判据全部来自既有代码（`MemoryStore.get_characters` /
   `AssetRegistry.has_location/get_positions/has_position` / `Scene.lines[].type`），
   与 `Validator`（Enum+Reference）**同源**，但 preflight 是"调度前置条件、返回缺口清单、不抛异常"；
   **必须先把落盘 dict `Scene.from_v01()` 还原 `Line.type`**，否则 move 行漏检。
4. **视觉资产实体收集**：字段依据是 `Shot.characters` / `Shot.location`+`Storyboard.location` /
   `Shot.assets[kind=="prop"].ref`；真实调用序列是
   `register → new_version(profile=…) → submit_for_review → approve`（每步内部自动 `save()`），
   `VisualAssetStep.validate` 用 `require_complete=True` 卡必填字段；**一实体一 run**。
5. **审核策略注入**：`WorkflowEngine(review_handler=…)` 的回调契约是
   `handler(artifact, context) -> 对象`，需 `.decision`（三态）/`.review_id`，
   `approved_with_edit` 另需 `.edited_content`；三种策略分别实现为
   恒 APPROVED（**必须在 run_report 标注程序化模拟**）/ 预置序列出队 / 抛 `OrchestrationError` 停下。

---

## 11. 遗留项（交 B/C/D 会话）

```text
1. D-2/D-3/D-4 三条指令缺陷：建议总控修正指令（本文件 §8 已给出改法）。
2. A-1/A-2/A-3/A-4 四项待裁决：B 会话在收到裁决前，建议按"§9 建议"实现（有实测依据）。
3. 真实冒烟 run_real_lifecycle.py（≤5 次文本）需人工批准 + 配置 key；无配置 → 明确阻塞报告。
4. §4.2 的 T03b/T04b/T06b/T09b/T10b/T18b 六条增强测试：C 会话酌情采纳（T03b/T06b 建议必做）。
5. Stage 12 完成后应更新 PROJECT_STATUS / HANDOFF / ROADMAP / CLAUDE.md（D 会话），
   架构表新增 orchestration 层，依赖方向改为
   core → (llm|memory|context) → workflow → review → agents → orchestration。
```

---

*Stage 12-A 产出完毕。本文件不写业务代码、不含 commit；B 会话可依指令 §六/§八 + 本文件 §2/§6 开工。*

---

## 12. 【总控裁决，2026-09-14】Stage 12-A 产出验收

> **给谁读**：Stage 12-B（实现）、12-C（测试）、总控（验收）。
> 本节由**总控会话**撰写，独立于 A 会话；所有"总控实测"列均为总控亲自复跑/读码所得，**未采信 A 会话自述**。

### 12.1 验收方法（诚实记录）

总控采用**两条互不依赖的路径**复核 A 会话的每一条实测结论，**不要求 A 复用脚本**：

```text
路径 1（源码抽查）：总控直接读 v2/workflow/engine.py、v2/agents/storyboard.py、
  v2/agents/scene_generation.py、v2/agents/visual_asset.py、v2/core/io.py、
  v2/llm/providers/mock.py、v2/core/visual_registry.py —— 逐条比对 A 的断言。
路径 2（独立探针）：总控**自建** verify.py（位于仓库外 %TEMP%/stage12a_verify/，
  未复用 A 的 probe 脚本），覆盖 V1~V8 共 23 项断言，含真实 WorkflowEngine +
  MockTextProvider + VisualAssetRegistry 端到端跑。
```

**独立探针结果**：`PASS=23 FAIL=0`（含 V1 上游产出被忽略、V2 多场景不串场、
V3 engine 复用+版本递增+resume SKIPPED、V4 空 Memory 不回锚、V5 Line.type 往返、
V6 三态契约、V7 视觉链路+Token 硬证据、V8 ReviewService 基座）。
**A 会话产出 = PASS。**

### 12.2 独立复核记录表（非 A 自述）

| A 会话声称 | 总控实测 | 结论 |
|---|---|---|
| **D-2**：§7.1 同 run 装两 step 不可实现（Storyboard 只读 `inputs["scene"]`） | V1：上游 `scene_1` 产出被忽略，实际用 `inputs["scene"]`；V1b 两 step 均执行 | ✅ 属实 |
| 多场景：每场景一次 run → 不覆盖 / 不串场 / run_id 互异 | V2：3 场景产物齐备无多余版本；V2b run_id 互异；V2c 内外层 scene_id 各自正确；V2d step_id 独立 | ✅ 属实 |
| engine 复用无副作用；版本递增且 v1 字节不变；resume→SKIPPED 不写新版本 | V3：复用两次均成功 run_id 不同；V3b 递增且 v1 字节不变；V3c SKIPPED 且不写新版本 | ✅ 属实 |
| 空 Memory → `characters` 空 → Scene 层不回锚 lines → 硬拒重试风暴 | V4：空 Memory → characters 项为空；V4b lines speaker/position 未回锚 → `UNKNOWN_CHARACTER`/`INVALID_LOCATION` | ✅ 属实 |
| `Line.type` 不由 `to_v01()` 写出，`from_v01` 可还原 | V5：`to_v01()` 不含 `type`；`from_v01` 还原 dialogue/move | ✅ 属实 |
| review 三态：approved 1 文件 / approved_with_edit 2 文件 / rejected failed 1 文件 | V6：success 1 文件 / success 2 文件 / failed 1 文件 | ✅ 属实 |
| 视觉资产链路 `register→new_version→submit→approve`；Token 硬证据逐字节不变 | V7：CHAR/LOC/STYLE 三链路 approved；V7b 重复 register 被拒；V7c registry.json 落盘；V7d 前后 `to_dict()` 逐字节不变；V7e `"red wool coat"` 只在 `render_prompt()` | ✅ 属实 |
| `ReviewService` 可作 handler 基座 | V8：基座可用 + 审核记录落盘 | ✅ 属实 |
| 源码级断言（`write_json` 非原子 / `VisualAssetStep.step_id` 固定 / `MockTextProvider._pick` 按 `script[step]`） | 总控读 `io.py`、`visual_asset.py`、`mock.py` 确认 | ✅ 属实 |
| 交付前复位；禁改路径 0 diff；6 个 tag 未移动 | `git diff --stat` 禁改路径为空；6 tag 指向未变 | ✅ 属实 |

> **一处口径更正（非 A 的错误，是记录精度问题）**：`v0.1` 是**附注 tag（annotated tag）**，
> 其 **tag 对象** = `1dd5303`、**peel 后 commit** = `344c0c6`（`git log -1 v0.1` 显示后者）。
> 后续文档记录锚点时应写 `git rev-parse v0.1^{commit}` = `344c0c6`，避免歧义。

### 12.3 裁决 1：A-1 —— **批准**（编排层可写 Memory）

**采纳** A 会话建议：集级 Casting 通过后，编排层可调用
`MemoryStore.set_characters(...)`（沿用 casting 产物）与 `set_world(...)`。

**硬约束（写入裁决，B 会话必须遵守）**：

```text
1. 只允许 set_characters / set_world —— 它们是"编排层已确定的事实"的直接落库。
2. ★ 严禁调用 approve_fact(...)：事实候选的批准是"人"的动作，编排层不得代批。
   （若未来需要，须另立阶段 + 总控批准，见 ORCHESTRATION.md 的审核职责边界。）
3. 写入的 characters 必须与 Casting Artifact 一致（同一 run 的产物），不得凭空构造。
```

理由：与 D-2 同性质——**必要补全**。不写则 Context `characters` 为空 → 真实 LLM 编造
未登记角色名 → `SceneGenerationAgent._normalize` **不回锚** lines → `UNKNOWN_CHARACTER`
→ 3 次重试耗尽 → 永久 FAILED（V4/V4b 已实证）。且 `stage8_preflight_report.md` L142
已确立"角色清单沿用 casting 产物"的约定，本裁决只是把该约定落到编排层。

### 12.4 裁决 2：A-2 —— **批准**（视觉资产不支持编辑态）

**采纳**：视觉资产的 review 决策若为 `approved_with_edit` → 编排层**明确抛
`OrchestrationError`**，只支持 `approved` / `rejected` 两态。

理由：`VisualAssetRegistry.apply_review_decision(APPROVED_WITH_EDIT)` **直接抛
`VisualRegistryError`**（实测）；且"编辑后的 profile"缺确定性来源
（`Review.edited_artifact` 是 Artifact 形态，非 profile）。隐式解释会引入不确定语义，
违反"不静默、可追溯"。**fail-loud 优于静默猜。**

### 12.5 裁决 3：A-3 —— **批准**（两段式：每段每场景一次 run）

**采纳**：废弃指令 §7.1 原伪代码的"同一 run 装 SceneGenerationStep + StoryboardStep"，
改为**两段式**：`scene_gen_{sid}` 与 `storyboard_{sid}` **各一次 run**。

**并同步修正指令措辞**：指令 §7.1 中"每场景一次 run"改为"**每段每场景一次 run**"，
`inputs["scene"]` 在 storyboard 段必须显式放入（不得依赖上游 step 产出）。

理由：V1 已实证同 run 两 step 不可实现（Storyboard 只读 `inputs["scene"]`）；
两段式**零改引擎**、语义更干净、resume 天然可用（V3c）。

### 12.6 裁决 4：A-4 —— **批准**（`state.py` 自行实现原子写）

**采纳**：`v2/orchestration/state.py` 自行实现原子写（临时文件 + `os.replace`），
**不越界**——它在编排层内部，不碰 `core/`。

**并同步修正指令 §6.3 措辞（D-4）**：原"原子写（复用 `core.io.write_json`）"
改为"编排层**自行实现原子写**（临时文件 + `os.replace`），序列化行为对齐
`core.io.write_json`（`ensure_ascii=False` / `indent=2`）"。理由：实测 `write_json`
**非原子**（`path.open("w")` 直写，无 `os.replace`）。

### 12.7 对其余指令缺陷的处置（D-1 / D-5 / D-6 / D-7 / D-8）

| # | 处置 |
|---|---|
| **D-1**（HEAD 描述过期） | **已由总控直接修正**指令 §二：锚点 = `v0.2-stage11` tag = `ef18f0d`；HEAD 可因纯文档 commit 前移。D 会话无需再改。 |
| **D-5**（`semantic_data` 来源未指定） | **采纳 A 建议**：`semantic_data` 由**确定性数据**构成（character←Casting entry 档案；location←world/registry；prop←名字本身），**不得调 LLM**。已写入指令 §7.3。 |
| **D-6**（T06 未提示 mock 需场景感知） | **采纳**：写入指令 §12.1 T06 —— 测试须使用按 `scene_id` 出不同内容的 mock/spy，否则无法证明不串场。 |
| **D-7**（§8.3 清单缺 `io.py`/`token_log.py`） | **采纳**：指令 §8.3 禁改清单补 `v2/core/io.py`、`v2/core/token_log.py`（本阶段确实 0 改，显式化）。 |
| **D-8**（`projects_root` 未要求可注入） | **采纳**：指令 §八 增加"编排层构造参数须含 `projects_root`（默认 `V2_ROOT/'projects'`），测试注入 `tmp_projects/`"。 |

### 12.8 对 B 会话的追加要求（由本次裁决产生）

```text
1. pipeline.py 必须实现【两段式】：scene_gen_{sid} 与 storyboard_{sid} 各一次 run；
   storyboard 段显式 inputs["scene"] = Scene（不得依赖上游 step 产出）。
2. 集级 Casting 通过后：MemoryStore.set_characters(casting 产物) + set_world(...)；
   ★ 严禁 approve_fact。
3. 视觉资产 review：只接 approved / rejected；遇 approved_with_edit → 抛 OrchestrationError。
4. state.py：自实现原子写（tempfile + os.replace），序列化对齐 core.io.write_json。
5. ProjectOrchestrator 构造参数必须含 projects_root（默认 V2_ROOT/'projects'）。
6. resume：对已 approved 单元【直接跳过、不调用引擎】（state.json 为唯一事实源，
   不存第二份 WorkflowRun）。
7. auto_approve 策略：run_report 必须含 review_mode 字段并标注"程序化模拟"，
   ★ 不得写"已人工审核"（AC13 诚实性）。
8. 遇"必须改 v2/workflow/"→ 立即停下报总控（§十七暂停条件）。
```

### 12.9 对 C 会话的测试补充（采纳 A 的 §4.2 六条为**必做**）

```text
T03b  preflight 正反例（含 move 行漏检反例——必须用 Scene 对象，非落盘 dict）★
T06b  多场景不串场：按 scene_id 出不同内容的 mock（否则 T06 形同虚设）★
T09b  auto_approve 的 review_mode 字段断言（AC13）
T04b  / T10b / T18b  酌情采纳
```

### 12.10 结论

```text
Stage 12-A 产出 = PASS（总控独立复核 23/23 + 源码抽查全属实）。
A-1 ~ A-4 四项裁决全部【批准】。
指令缺陷 D-1 ~ D-8 全部采纳并已/将由总控同步进指令正文。
未触发 §十七 架构暂停条件（编排层零改 v2/workflow/）。
→ Stage 12-A 阶段关闭；B 会话可依《v0.2开工指令12-B》开工。
```

**至此 Stage 12-A 阶段关闭。B 会话可依《v0.2开工指令12-B》+ 本文件 §2/§6 开工。**
