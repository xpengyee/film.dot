# stage11_plan.md —— Stage 11 StoryboardAgent 契约笔记 / 接线方案 / 风险 / 测试方案

> **产出会话**：Stage 11-A（分析 / 架构），工作目录 `D:\AI\FilmAgent-main`。
> **产出日期**：2026-09-13。**不写业务代码、不 commit**（A 会话边界，见 `ORCHESTRATION.md` §1.1）。
> **上游规格**：`D:\AI\FilmAgent\v0.2开工指令11`（下称"指令"）。
> **给谁读**：Stage 11-B（实现）、11-C（测试）、总控（验收）。

---

## 0. 开工自检结果（指令 §二 / §3.1）

| 检查项 | 期望 | 实测 | 结论 |
|---|---|---|---|
| `git log --oneline -5` | HEAD = `c8a3ed8` | HEAD = **`dc335a7`** `docs: migrate session workflow from Claude Code to WorkBuddy`，其父为 `c8a3ed8` | ⚠️ 见 D-1 |
| `git status --short` | 空 | 空 | ✅ |
| `git tag -l` | 五个 tag | `v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9` / `v0.2-stage10` | ✅ |
| `git diff -- FilmAgent/` | 空 | 空 | ✅ |
| stage10 套件 | 76/76 | **76/76，rc=0** | ✅ |

**D-1（指令文本过期，非阻塞）**：指令 §二 写"HEAD 应为 `c8a3ed8`"，实际 HEAD 是 `dc335a7`。
差异是 `dc335a7` 这一条**纯文档 commit**（会话体系从 `claude` CLI 迁移到 WorkBuddy），
它**不改任何 `v2/` 代码**，`v0.2-stage10` tag 仍指向 `c8a3ed8`。
判定：**不阻塞，指令未过期到影响开工的程度**；B/C/D 会话不必停下来问，按现状推进即可。
建议 D 会话顺手修正指令 §二 那两行（指令属总控资产，本次 A 会话不动）。

**附带复现的两个已知坑**（跑 stage10 套件时**一并复现**，B/C/D 必须照做，详见 §5.2）：

1. 四个历史报告产物被重写：`stage4_report.json` / `stage4_console.txt` / `stage6_report.json` / `stage7_report.json`。
2. 仓库根生成 `llm_calls.jsonl`（6990 B）。

两个坑都**已复位**（`git checkout --` 四个文件 + `rm -f llm_calls.jsonl`），工作区当前干净。

---

## 1. 契约笔记（S1）—— 数据结构的真实形状

> 以下全部**逐字段核对过源码**，不是照抄指令。凡与指令描述有出入的地方均标 **⚠️**。

### 1.1 Scene（输入侧，唯一事实来源）

`v2/core/models.py`：`Scene` / `SceneInfo` / `Line` / `Action` / `Move` / `PositionRef`。

```python
class Scene:
    id: int                                   # Scene.from_v01(data, id=...) 注入；to_v01() 不写出
    info: SceneInfo                           # who / where / when / what
    lines: List[Line]                         # 每行 type ∈ {"dialogue", "move"}
    initial_positions: List[PositionRef]      # character + position
```

`Line` 的两态（这是本阶段**最关键的一条真实契约**）：

```python
# type == "dialogue"
Line(speaker=str, content=str, actions=[Action], shot=str, positions=[PositionRef])
# type == "move"
Line(move=Move(character, destination[, reason]), shot=str, positions=[PositionRef],
     reason=str, insertion_reason=str, insertion_position=str)   # 后三个 = scenes_8 透传
```

⚠️ **要点 1**：`Line.type` 是 **V0.2 内部字段**，`to_v01()` **不会写出去**；
V0.1 侧靠"有没有 `move` 键"判断行类型。所以 B 会话从 LLM 拿到的 Scene dict
（若走 `scene.to_v01()`）**没有 `type` 字段**，必须用 `Scene.from_v01()` 还原成 `Line`
再判 `line.type`。**不要自己去猜 dict 的键。**

⚠️ **要点 2**：走位行的 `positions`（V0.1 的 `current position`）是"走位完成之后"的站位快照。
Stage 11 若要把 move 行转成 `Shot(kind="move")`，`Shot.move` 的来源是 `Line.move`，
而 `Shot.positioning` 的来源是 `Line.positions` —— **两者是独立字段，别合并**。

⚠️ **要点 3**：`Scene.to_v01()` **只在 `initial_positions` 非空时**才写 `"initial position"` 键；
空列表 = 键不存在。同理 `Line.to_v01()` 的 `current position` / `actions` / `shot` 都是"非空才写"。
B 会话做兜底时要按"键可能不存在"来写。

**Step 侧怎么拿到 Scene**：指令 §7.3 允许两种方式（`inputs["scene"]` 直接给 Scene，
或 Agent 读已批准 Artifact）。**A 会话裁决：走 `inputs["scene"]`，由调度层放**，理由见 §2.2。
若走这条，Scene 的形态建议**直接给 `Scene` 对象**（不是 dict），因为 Step 的 `validate()`
本来就需要 `Scene` 对象来对齐台词；若给 dict，B 会话需在 Step 里 `Scene.from_v01()`。

### 1.2 Storyboard / Beat / Shot（输出侧）

`v2/core/shot.py`，**0 改**。字段与 `to_dict()` 一一对应（`Shot.to_dict()` 24 键，
含 3 个常被忽略的 V0.1 透传字段：`reason` / `insertion_reason` / `insertion_position`）。

```python
Storyboard(scene_id:int, location:str, characters:[str], synopsis:str,
           initial_positions:[PositionRef], beats:[Beat], metadata:dict)
    .flatten_shots() -> [Shot]      # 按 Beat 顺序展平，保序
Beat(beat_id:int, purpose:str, shots:[Shot], metadata:dict)
Shot(24 字段，见 shot.py L171-L194)
```

### 1.3 受控词表（**stage8 精确断言锁定，一个都不能加**）

```python
SHOT_SIZES        = ("WS", "MS", "MCU", "CU", "ECU")
CAMERA_ANGLES     = ("eye_level", "high_angle", "low_angle", "overhead")
CAMERA_MOVEMENTS  = ("static", "pan", "tilt", "dolly", "tracking", "zoom", "handheld")
COMPOSITIONS      = ("single", "two_shot", "over_the_shoulder", "group")
SHOT_KINDS        = ("dialogue", "move", "general")
SHOT_STATUSES     = ("planned", "approved", "rendered")
ASSET_KINDS       = ("character","location","prop","action","camera","image","video","audio","reference")
```

⚠️ **要点 4（指令 §7.2 的安全默认值与现有字段默认值不一致）**：
指令说越界值映射到 `shot_size="MS"` / `camera_angle="eye_level"` /
`camera_movement="static"` / `composition="single"` / `kind="general"`。
核对现状：

- `CameraPlan.shot_size` 默认是 **`""`**（空串），**不是** `"MS"`。
- `Shot.composition` 默认是 **`""`**（空串），**不是** `"single"`。
- `camera_angle` / `camera_movement` / `kind` 的默认值与指令一致。

**空串本身是合法的**（`ShotValidator` 只在"非空且不在词表"时报错，见 §1.4）。
因此 B 会话有两种正当选择：

- **(a) 严格照指令**：越界 → `MS` / `single`（有值，更"确定"）。
- **(b) 越界 → 空串**：与字段自身默认值一致，语义是"没判断出来"。

**A 会话裁决：选 (a)，照指令执行。** 理由：`camera.shot_size` 为空时 `VisualComposer`
拿不到景别，未来 Stage 13 出图会退化；而 `MS` 至少是一个可渲染的中景。
但**必须在 metadata 里同时记 warning**（指令 §7.2 第 1 条已要求），
让"这是兜底值不是 LLM 判断"这件事可追溯。C 会话按 (a) 断言。

⚠️ **要点 5（`shot_type` 是被校验的，别忽略）**：
`ShotValidator` 在 **`registry is not None` 且 `shot_type` 非空**时，会要求
`base_name(shot_type) ∈ registry.shot_names()` **或** `shot_type ∈ SHOT_SIZES`，
否则报 `INVALID_SHOT_TYPE`。
→ **B 会话务必：LLM 若写了自由文本 `shot_type`（如 "establishing" / "reaction"），
规范化时要么清空、要么映射到已注册镜头名 / 受控景别**，否则 validator 直接拒。
指令 §6.2 写"`shot_type` 自由文本（非受控）"——**这条在带 registry 校验时会失败**，
是本阶段最容易踩的实现陷阱。

⚠️ **要点 6（`duration` 的 `0.0` 是合法值）**：
`ShotValidator` **不校验** `duration`。指令 §7.2 第 4 条要求 `duration <= 0` 时按 kind 兜底。
注意 `Shot.duration` 默认 `0.0`，`from_dict` 里 `float(... or 0.0)` 也把 `None` 变 `0.0`。
→ 兜底判断用 `duration <= 0` 就够（不必额外判 None）。

### 1.4 Validator 真实规则（`v2/core/validator.py`，**0 改**）

`AssetValidator(registry=None, characters=None)`：

- `kind ∉ ASSET_KINDS` → `INVALID_ASSET_KIND`
- `ref` 空 → `MISSING_FIELD`
- `kind=="character"` 且 `characters` 非空 且 `ref ∉ characters` → `UNKNOWN_CHARACTER`
- `kind=="location"` 且 `registry` 非空 且 `not has_location(ref)` → `INVALID_LOCATION`
- `kind=="action"` 且 `registry` 非空 且 `not has_action(ref)` → `INVALID_ACTION`（自动剥编号变体）
- ⚠️ **`prop` / `camera` / `image` / `video` / `audio` / `reference` 只查 kind 合法性 + ref 非空，不查存在性**

`ShotValidator(registry=None, known_characters=None, valid_states=None)`，
`validate(shot, *, scene_location="", path_prefix="shot")`：

| # | 规则 | 错误码 |
|---|---|---|
| 1 | `shot_id`/`scene_id`/`beat_id` 必须 `int` | `WRONG_TYPE` |
| 2 | `kind ∈ SHOT_KINDS`（**无空串豁免，空串即报错**） | `INVALID_STRUCTURE` |
| 3 | `status` 非空时必须 ∈ `SHOT_STATUSES` | `INVALID_STATUS` |
| 4 | `kind=="dialogue"` → `speaker` / `dialogue` 均非空 | `MISSING_FIELD` |
| 5 | `kind=="move"` → `move` 非 None，且 `move.character` / `move.destination` 非空 | `MISSING_FIELD` |
| 6 | `camera.shot_size` 非空时 ∈ `SHOT_SIZES` | `INVALID_SHOT_SIZE` |
| 7 | `camera.camera_angle` 非空时 ∈ `CAMERA_ANGLES` | `INVALID_CAMERA_ANGLE` |
| 8 | `camera.camera_movement` 非空时 ∈ `CAMERA_MOVEMENTS` | `INVALID_CAMERA_MOVEMENT` |
| 9 | `composition` 非空时 ∈ `COMPOSITIONS` | `INVALID_COMPOSITION` |
| 10 | `registry` 非空且 `shot_type` 非空 → 见 ⚠️ 要点 5 | `INVALID_SHOT_TYPE` |
| 11 | `eff_loc = shot.location or scene_location`；`registry` 非空且非空 → `has_location` | `INVALID_LOCATION` |
| 12 | `characters[i]` 非空且 ∉ `known_characters`（清单非空时） | `UNKNOWN_CHARACTER` |
| 13 | `actions[i].character ∉ known` / `.action` 未注册 / `.state ∉ valid_states` | `UNKNOWN_CHARACTER` / `INVALID_ACTION` / `INVALID_STATE` |
| 14 | `positioning[k].position` 必须存在于 `eff_loc`；`move.destination` 同理 | `INVALID_POSITION` |
| 15 | 转交 `AssetValidator` 校验 `shot.assets` | 见上 |

`BeatValidator`：`beat_id` int；`shot.scene_id == scene_id`（传了才查）；
`shot.beat_id == beat.beat_id`；**Beat 内 `shot_id` 不重复**；每个 Shot 走 `ShotValidator`。

`StoryboardValidator`：`scene_id` int；`beats` list；
**Storyboard 级 `location` 必须 `has_location`**（否则 `INVALID_LOCATION`，且**不再往下查站位**）；
Storyboard 级 `characters` / `initial_positions` 查存在性；
**`beat_id` 全局唯一**（`DUPLICATE_BEAT_ID`）；**`shot_id` 全局唯一**（`DUPLICATE_SHOT_ID`）。
**还提供 `validate_dict(data)`** —— 直接吃 dict，内部 `Storyboard.from_dict` 失败则
返回一条 `WRONG_TYPE` 错误（不抛异常）。**`StoryboardStep.validate()` 应当用这个**。

`VALID_STATES = frozenset({"standing", "sitting"})` —— `Action.state` 只允许这两个值。

### 1.5 一个"指令没写但必须处理"的契约冲突（**重要**）

指令 §6.2 要求：

```text
subject   : 本镜头主体描述
```

而 `ShotValidator` **完全不校验 `subject`** —— 它是自由文本，随便填。
但 **`VisualComposer.compose()` 会把它渲染进 `ComposedVisualInput.subject`**
（`visual_compose.py` L143：`subject=str(shot.subject or "")`），最终进图像 prompt。
→ **`subject` 是"看图的人会看到的短语"，不要塞整句剧情描述**。
建议 B 会话在 prompt 里明确约束 `subject` 为**短名词短语**（"Mercy at the window"），
并在规范化层做长度上限（例如 40 字）截断 —— 属于"确定性规范化"，正当。
（这条不是硬校验，但影响 Stage 13 出图质量，属"设计判断"而非"指令约束"。）

### 1.6 Artifact 命名与版本

```text
artifact_name = f"storyboard_{scene_id}"        →  storyboard_1.json, storyboard_1_v2.json …
step_id       = f"storyboard_generation_{scene_id}"   ← 多场景独立 step，互不覆盖
```

`ArtifactStore.next_version()` 扫磁盘既有版本 +1，**绝不覆盖**（`workflow/artifact.py` L109-L125）。
`version_filename`：v1 无后缀，v≥2 带 `_vN`。

---

## 2. 接线方案（S2）

### 2.1 Agent / Step 骨架（继承，与原有两个 Agent 完全同构）

```python
class StoryboardAgent(LLMAgent):
    name  = "storyboard_generation"
    needs = ["project_info", "approved_facts", "characters",
             "locations", "actions", "shots"]          # 与 SceneGenerationAgent 同集合

    def __init__(self, llm_client, *, scene_id=1,
                 registry=None, known_characters=None, prompt_path=None): ...

    def run(self, context, *, storyboard_spec=None, scene=None) -> Storyboard: ...

class StoryboardStep(LLMStep):
    step_id = "storyboard_generation"      # 构造时覆盖为 storyboard_generation_{scene_id}
    name    = "storyboard_generation"
    needs   = StoryboardAgent.needs
    needs_review = True
    def run(self, context): ...      # inputs: assembled_context / storyboard_spec / scene
    def validate(self, data, context) -> List[str]: ...   # StoryboardValidator.validate_dict
```

**`needs` 逐项核对**（`ContextAssembler._providers`，全部已注册，**无需新增 provider**）：

| need | provider 行为 | 本阶段用途 |
|---|---|---|
| `project_info` | `{project_id, project_title}` | prompt 主题 |
| `approved_facts` | approved 事实（**candidate 绝不进**） | 剧情真相 |
| `characters` | `memory.get_characters()` → list[dict] | **名字回锚的白名单（关键）** |
| `locations` | `[{name, positions:[id,...]}]` | **地点白名单 + 站位点清单（关键）** |
| `actions` | `assets.action_names()` | 动作引用白名单 |
| `shots` | `assets.shot_names()` | V0.1 注册镜头名（供 `shot_type` 参考） |

**与 scene_generation 的差异**：Scene 生成只需"名字 + 站位点 id"；
Storyboard 需要**逐地点的完整站位点清单**（`positioning` / `move.destination` 要指到具体点位），
所以 prompt 的 `## available locations` 段落**必须渲染 `positions` 列表**，
不能只给地点名（`scene_generation` 的 payload 里 location 已含 positions，
B 会话直接沿用它的 `_locations_from()` 即可）。

### 2.2 Scene 怎么进 Agent（**本阶段唯一的接线判断点**）

指令 §7.3 给了两条路，A 会话裁决如下 ——

| 方案 | 做法 | 判定 |
|---|---|---|
| **S-A：`inputs["scene"]` 由调度层放** | Step 从 `context.inputs.get("scene")` 取，转交 Agent | ✅ **采纳** |
| S-B：Step 提供 callback 从上游 Artifact 解析 | 调度层注册 `scene_loader` | ❌ 不采纳 |

**理由**：

1. **零新增机制**：`storyboard_spec` 本来就要走 `inputs`，Scene 顺路放同一个 dict 里最省；
   新增 callback 注册点等于"发明一套新的注入机制"，违反"不造第二套机制"（`ORCHESTRATION.md` §3.2）。
2. **不碰禁改清单**：S-B 需要在 `LLMStep` / `WorkflowEngine` 上加注册位，
   而 `v2/agents/base.py`、`v2/workflow/` 都在 §8.3 禁改清单里。
3. **Agent 边界**：S-A 下 Agent 仍然只从 `run(context, *, scene=...)` 的入参拿数据，
   **不 `open()` 任何文件**，符合指令 §7.3 的边界澄清与 §十 铁律 5。
4. **测试可控**：C 会话想构造"缺 Scene / Scene 非法 / Scene 与 spec 不符"的边界用例时，
   直接改 `inputs` 即可，不需要伪造磁盘 Artifact。

**`inputs` 的完整约定（B 会话照此实现，C 会话照此构造用例）**：

```python
context.inputs = {
    "assembled_context": AssembledContext,     # 必需（LLMStep.assembled_context 取）
    "storyboard_spec":   dict,                 # 可选，缺省 {} 全兜底
    "scene":             Scene | dict,         # ★必需：Scene 对象（推荐）或其 to_v01() dict
}
```

**缺 `scene` 时**：Step 抛 `StepError`（明确报错，可重试），**绝不静默产出空 Storyboard**。

### 2.3 LLM 输入 payload 的构成（token 纪律在这里落地）

```text
## project topic                      ← project_info
## approved_facts                     ← approved_facts（只有 confirmed）
## available characters               ← characters（名字 + 档案摘要）
## available locations (with positions) ← locations（★含 positions 列表）
## available actions                  ← actions
## available shots                    ← shots（V0.1 注册镜头名，供 shot_type）
## scene (approved script)            ← ★ Scene 摘要：who/where/when/what + lines + initial position
## storyboard specification            ← storyboard_spec
```

⚠️ **指令 §7.4 要求 prompt 里"把受控词表的合法取值原样列出"**。
B 会话应当**从 `core.shot` 导入常量并渲染**（`SHOT_SIZES` 等），**不要在 prompt 文件里手抄**：

- 手抄 → 词表变了 prompt 不变 → 漂移；
- 从常量渲染 → 单一事实来源。

但 `v0.2/prompts/v0.2/*.txt` 是**纯文本文件**（`LLMAgent.system_prompt()` 直接 `read_text`），
没有模板引擎。两个正当做法：
**(a)** 在 prompt 文件里写好词表文字，并在 `StoryboardAgent` 里加一条
"prompt 词表 vs `core.shot` 词表一致性"的运行时自检（不一致 → `StepError`）；
**(b)** 把词表段落放进 **user payload**（`_user_payload()` 用 f-string 渲染常量），
prompt 文件只放角色设定与 JSON 契约。
**A 会话推荐 (b) + 补 (a) 的自检**：(b) 保证永远同步，(a) 防止有人后来手工改 prompt 时踩坑。
实测确认：`SceneGenerationAgent._user_payload()` 已是"Python 组装 user payload"的模式，
(b) 与既有范式一致。（⚠️ 这也正是 T01"prompt 含全部受控词表取值"要断言的 —— 见 §4 T01。）

### 2.4 确定性规范化（§7.2 六条 → 落到函数）

建议 `v2/agents/storyboard.py` 内一组纯函数（**不调 LLM、不碰磁盘**，可单独单测）：

```text
_normalize_coerce(raw, scene, spec, registry, characters) -> (Storyboard, warnings: List[str])
├─ 1 词表兜底   越界 → MS/eye_level/static/single/general + warning
├─ 2 名字回锚   characters ⊆ (scene.info.who ∪ known_characters)
│               location  ∈ (scene.info.where ∪ registry.location_names())
│               找不到 → 剔除 + warning（不是报错）
├─ 3 id 重排    beat_id 1..n（按出现序）；shot_id 全局 1..N（跨 Beat 连续）
├─ 4 时长兜底   duration <= 0 → dialogue 3.0 / move 2.5 / general 2.0
├─ 5 状态强制   所有 Shot.status = "planned"
├─ 6 台词回锚   kind=="dialogue" 且 speaker 空 → 按 shot_id 序对齐 Scene 中 type=="dialogue" 的 lines
└─ 7 shot_type  非空但既不在 registry.shot_names() 也不在 SHOT_SIZES → 清空 + warning  ★见要点 5
```

**为什么规范化放在 Agent 而不是 Validator**：
`ORCHESTRATION.md` §3.2 + 阶段 9 §5.4 原则 5 明确 —— **"Core Validator 负责业务数据合法性
（Provider 不做业务判定）"**；Validator 是**门槛**（拒），不是**修复器**（改）。
把"修复"放进 Validator 会让 Stage 8 的校验器语义变味（且它在禁改清单里）。
规范化属"Agent 的确定性后处理"，与 `SceneGenerationAgent._normalize()` 完全同层同构。

**warnings 去哪**：
`Storyboard.metadata["normalize_warnings"] = [...]`（**不是** Shot.metadata）。
这样 token 硬证据（§4 T18）不受影响 —— metadata 本来就在 `to_dict()` 里，
但 warnings 是**内容相关**的，不因注册视觉资产而变，所以逐字节断言仍成立。

### 2.5 Step 的 validate() 与 review

```python
def validate(self, data, context):
    errors = self.agent.validator.validate_dict(data)   # StoryboardValidator.validate_dict
    return [str(e) for e in errors]
```

- 用 `validate_dict` 而非 `validate`：落盘/LLM 产出是 dict，直接吃，省一次手工转换；
  且它内部已把 `from_dict` 失败包成错误（**不抛异常**），正好满足 T10"返回错误列表（非异常）"。
- `needs_review = True`：走既有 Review 三态（`engine._handle_review`），**零新增审核服务**。

### 2.6 Validator 实例怎么建（**两条路，B 会话按此选**）

StoryboardValidator 需要三样：`registry`(AssetRegistry) / `known_characters` / `valid_states`。

- **路径 A（推荐给 B 会话）**：`StoryboardStep.__init__` 里由调度层注入
  `registry` + `known_characters`，Agent 内部建 `StoryboardValidator(registry, known_characters)`。
  与 `SceneGenerationAgent` 的 `assets=` / `known_characters=` 注入范式**完全一致**。
- **路径 B（可选增强，给 Step 壳的上下文校验）**：Step 的 `validate()` 建
  `StoryboardValidator(None, scene_characters, None)`（**不传 registry**）→ 纯结构校验，
  地点/动作/站位交给 Agent 内部的完整校验。**代价**：`INVALID_LOCATION` 等不会在 Step 层出现。

**A 会话选择**：**路径 A**，理由 = 范式一致、校验完整、C 会话的边界断言更好写。
路径 B 仅作为"Step 层想做与上下文相关的校验"时的说明保留（本阶段不需要）。

---

## 3. 风险清单（按严重度排序）

| # | 风险 | 影响 | 触发条件 | 处置 |
|---|---|---|---|---|
| **R1** | `shot_type` 自由文本被 `ShotValidator` 拒（`INVALID_SHOT_TYPE`） | 整步永远无法通过校验 → 3 次重试后 `FAILED` | LLM 写了指令 §6.2 允许的"自由文本 shot_type"，而 registry 非空 | 规范化第 7 条清空/映射 + warning；C 会话加"自由文本 shot_type"专测（T04 扩展） |
| **R2** | 越界词表值未兜底 → `INVALID_SHOT_SIZE` / `INVALID_COMPOSITION` / `INVALID_STRUCTURE` | 同上 | LLM 用 "Extreme Close Up" / "bird_eye" / "dolly_in" | §7.2 第 1 条确定性映射；C 会话 T04 必须覆盖**全部四轴 + kind** |
| **R3** | LLM 编造角色/地点 → `UNKNOWN_CHARACTER` / `INVALID_LOCATION` | 同上 | 常见 LLM 行为（尤其角色名大小写、昵称） | 名字回锚"剔除 + warning"（**剔除**而非报错，符合 §1.3 的 validator 是"点名清单外即拒"的现实） |
| **R4** | `beat_id` / `shot_id` 跳号或重复 → `DUPLICATE_BEAT_ID` / `DUPLICATE_SHOT_ID` | 同上 | LLM 编号不稳（指令已预判） | id 重排（从 1 连续） |
| **R5** | **规格歧义**：`kind=="dialogue"` 必须非空 `speaker`+`dialogue`；`kind=="move"` 必须非空 `move` 对象 | **若 LLM 把某镜头标成 dialogue 却没给台词 → 硬失败，兜底救不了** | LLM 误标 kind | §7.2 第 6 条只覆盖"dialogue 缺 speaker"（回锚 Scene.lines），**不覆盖"dialogue 缺 dialogue"**。B 会话需**额外规则**：`dialogue` 空且 Scene 有台词可对齐 → 补；否则把该 Shot 降级为 `kind="general"` + warning（**这是超出指令 §7.2 的补充，属必要补全，向总控报备**） |
| **R6** | 站位点越界 → `INVALID_POSITION` | 同上 | LLM 编造 "Position 99" | prompt 列出每地点 positions + 规范化剔除不存在的站位引用 + warning |
| **R7** | `subject` 被塞成整句剧情 | Stage 13 出图 prompt 质量退化（**不报错**，静默劣化） | LLM 自由发挥 | prompt 约束为短名词短语 + 长度截断；C 会话可加"subject 无换行/长度上限"的软断言 |
| **R8** | **Token 纪律破口**：规范化时把角色外貌/地点环境写进 `visual_description` | 违反 §十一 硬验收（AC6），`Storyboard.to_dict()` 会被视觉资产影响 | B 会话"顺手"把 Composer 展开结果塞进 Shot | prompt 明确禁止 + C 会话 T18 逐字节断言 + 抽查 `visual_description` 无档案关键词 |
| **R9** | 多场景 `step_id` 串场 | `storyboard_1` / `storyboard_2` 互相覆盖，`resume_from` 失效 | 忘了带 scene_id | `step_id = f"storyboard_generation_{scene_id}"`（照 `SceneGenerationStep` 范式） |
| **R10** | 越界需新增词表值（例如"确实需要一个新的景别"） | 若擅自加 → **破 stage8 精确断言** | 见指令 §九 | **暂停并向总控报告**，由总控决定是否立项。A 会话预判：**本阶段不需要新词表值**，现有四轴足够表达分镜 |
| **R11** | 从仓库根跑测试污染仓库根 `llm_calls.jsonl`（T-1） | `git add` 会把记账文件带进版本库 | 任何从仓库根跑套件的动作 | 跑完 `rm -f llm_calls.jsonl`；C/D 会话交付前必查 |
| **R12** | 历史回归重写 4 个报告产物（T-6） | `git status` 出现无关 dirty | 套件内嵌子进程跑 stage4/6/7 | `git checkout --` 复位（§5.2 给了确切的四行命令） |
| **R13** | `subject` / `visual_description` 等自由文本字段无法被 validator 拦截，LLM 输出质量不可控 | 产出"合法但没用"的 Storyboard（validator 全过，人一看很差） | 恒存在 | 本阶段**接受**（AC4 只要求合法 + `status=="planned"`）；质量门槛靠 `needs_review=True` 的人工审核节点承接。**不要**为此新增 validator 规则（会扩大范围） |
| **R14** | 单次调用产出整份 Storyboard，某一段 JSON 坏了就整份作废 | 长场景失败率上升（指令 §7.1 方案 A 的已知代价） | 超长场景 | 指令已明确"留作未来优化项"，本阶段接受；重试 3 次 + 人工审核兜底 |

### 3.1 需要向总控报备的两条（A 会话的判断边界）

1. **R5**：`kind=="dialogue"` 缺 `dialogue` 内容时，指令 §7.2 的六条规范化**不足以**保证通过
   `ShotValidator`。A 会话建议补一条"降级为 general + warning"的规则。
   这是**对指令的必要补全**（不是扩大范围），但按 §十七 应报备。
2. **§1.3 要点 4**：指令的 `shot_size="MS"` / `composition="single"` 默认值与字段自身默认
   （空串）不一致。A 会话已裁决照指令执行并配 warning，B 会话若认为空串更好**须先报备**，
   不要自行改。

---

## 4. 测试方案（C 会话执行）

目录 `v2/logs/stage11_storyboard/run_tests.py`，沿用既有 `check(name, ok, detail)` +
`_results` + `sys.exit(1 if FAIL else 0)` + 内嵌历史回归子进程（**照抄 stage10 的
`_clean_child_env()` / `run_history()` 结构，含 `HISTORY_SUITES` 八项**）。

### 4.1 T01~T19 → 具体断言建议（比指令 §12.1 更可执行）

| 编号 | 断言要点（可直接翻译成代码） |
|---|---|
| **T01** | `StoryboardAgent.needs == ["project_info","approved_facts","characters","locations","actions","shots"]`；prompt/payload 文本含 `SHOT_SIZES`/`CAMERA_ANGLES`/`CAMERA_MOVEMENTS`/`COMPOSITIONS`/`SHOT_KINDS` 的**每一个取值**（用常量遍历断言，不手写字符串）；`artifact_name == "storyboard_1"`、`step_id == "storyboard_generation_1"` |
| **T02** | `StoryboardAgent(llm_client=MockTextProvider())` 可初始化（只探 needs 不调 LLM）；`StoryboardStep.__init__` 不注入 agent → `ValueError`；缺 prompt 文件 → `StepError`（可重试） |
| **T03** | mock 合法 JSON → `Storyboard`；`len(beats) >= 1`；每 Beat `len(shots) >= 1`；`flatten_shots()` 保序；`scene_id` 与 spec 一致 |
| **T04** | mock 越界：`shot_size="Extreme Close Up"`、`camera_angle="bird_eye"`、`camera_movement="dolly_in"`、`composition="wide"`、`kind="action"`、**`shot_type="establishing"`** → 规范化后**全部命中词表/被清空**；`ShotValidator` 全过；`metadata["normalize_warnings"]` 非空；**不抛异常** |
| **T05** | mock 返回 `characters=["Mercy","Charlie"]`（Charlie 是编造）→ 结果只含 Mercy；`location="Moon base"` → 回锚到 `scene.info.where`；warning 记录被剔除的名字；合法名（`scene.info.who` / `known_characters` / `registry.location_names()`）**保留** |
| **T06** | mock 返回 `beat_id=[1,3,3]`、`shot_id=[1,1,7]` → 重排为 `beat_id=[1,2,3]`、`shot_id=[1,2,3]`（**全局连续**）；`StoryboardValidator` 无 `DUPLICATE_*` |
| **T07** | `duration` 缺失 / `0` / `-1` → `dialogue=3.0` / `move=2.5` / `general=2.0`；`duration=4.5` 保留 4.5 |
| **T08** | 所有产出 Shot `status == "planned"`（含 LLM 写了 `"approved"` 的情况被强制改回） |
| **T09** | `inputs["storyboard_spec"]` 的 `scene_id` 被派发到 artifact 名；两个 Step（scene_id=1/2）在同一 engine 里 `step_id` 独立、`list_files("storyboard_1")` 与 `storyboard_2` 不交叉；**缺 `inputs["scene"]` → `StepError`** |
| **T10** | 用 `StoryboardValidator.validate_dict()`；喂"非法产出"（如 beats 为 `None` / shot 缺 speaker）→ **返回非空错误列表，不抛异常**；engine 据此进入 retry |
| **T11** | 第一次非法 → 第二次合法：`list_files("storyboard_1") == ["storyboard_1.json","storyboard_1_v2.json"]`，v1 内容不变（照 stage7 T07/T08 的双版本断言） |
| **T12** | 三态：`approved` → `StepStatus.APPROVED` 且下游继续；`approved_with_edit` → 新版本文件 + 原文件字节不变；`rejected` → `StepStatus.REJECTED` / run failed |
| **T13** | 三次全非法 → `StepStatus.FAILED`、`len(attempts)==3`、`len(transport.calls)==3`、**不静默成功** |
| **T14** | `APIRequestCounter` 快照：`real_api_requests == 0`（程序化断言，**不人工数数**）；`mock_requests` 与预期调用次数一致（`CountingProviderWrapper` 包 `MockTextProvider`） |
| **T15** | 产出 Storyboard 的 Shot → `VisualComposer.compose(shot)`：**空 registry → `VisualComposeError` 且消息列出缺口**（`allow_partial=False` 行为一致）；建齐 approved（character+location+style）后 → `compose()` 成功，`character_sections[0]["asset_id"] == "CHAR_001"`，`"red wool coat" in ci.render_prompt()` |
| **T16** | `compileall v2/core v2/llm v2/agents`；`import core, llm, agents` ＋ `StoryboardAgent`/`StoryboardStep` 在 `agents.__all__`；`ASSET_KINDS` 精确不变（九项原样）；新文件（`v2/agents/storyboard.py`、`v2/prompts/v0.2/storyboard_generation.txt`）扫 `"sk-"` **无命中** |
| **T17** | `git diff --stat HEAD --` 遍历 §5.1 禁改清单（含 `FilmAgent`）＋ 仓库根 `.gitignore` → 全空 |
| **T18** | **Token 硬证据**：`json.dumps(sb.to_dict(), sort_keys=True)` → `VisualComposer.compose()` → 再序列化，**逐字节相等**；且 `"red wool coat"`/`"amber and wood tones"` 不在 after 里，但在 `ci.render_prompt()` 里；**再加一条**：artifact JSON 文本内不含 `visual_description` 出现的角色外貌关键词 |
| **T19** | 历史回归：`stage1/3/4/6/7/8/9/10` 八个子进程全部 rc=0（复用 stage10 的 `_clean_child_env`） |

### 4.2 额外建议（超出指令，C 会话酌情采纳 —— 都是"低成本高价值"）

- **T19b**：断言历史回归**没有**新增 dirty 文件（跑前后 `git status --short` 对比），
  把 T-6 这个坑从"靠人记得"变成"程序化发现"。**注意**：这会与"复位"动作的顺序耦合，
  若实现麻烦就不做（保持指令原样，交付前人工复位）。
- **T04b（R1 专测）**：单独断言 `shot_type` 自由文本场景 —— 这是最可能被漏的一条。
- **T05b（R6 专测）**：`positioning` 写不存在的站位点 → 规范化剔除 + warning（否则 `INVALID_POSITION`）。
- **T07b（R5 专测）**：`kind="dialogue"` 但 `dialogue` 为空 → 断言最终落到"降级为 general"或"回锚成功"，
  **且 `ShotValidator` 无 `MISSING_FIELD`**。

### 4.3 测试素材纪律

```text
tmp_projects/ 清理用 _hard_rmtree（os 原语），绝不用 shutil.rmtree（宿主 shim）
空 registry → VisualComposeError 用 expect_raises 断言
从仓库根跑完 → 立刻 rm -f llm_calls.jsonl
```

---

## 5. 禁改清单与交付前复位（照抄已核实的事实）

### 5.1 禁改（指令 §8.3，A 会话逐条核实存在且应 0 diff）

```text
FilmAgent/                                          V0.1 冻结
.gitignore（仓库根）                                 V0.1 基线段
v2/core/shot.py  v2/core/adapter.py  v2/core/models.py  v2/core/assets.py
v2/core/valid*.py 中既有校验器类（阶段 1/8/10 的）      ← 见下方说明
v2/core/visual.py  v2/core/visual_registry.py  v2/core/visual_compose.py
v2/agents/base.py  v2/agents/casting.py  v2/agents/scene_generation.py  v2/agents/visual_asset.py
v2/llm/**（client / router / providers / request_counter）
v2/memory/  v2/review/  v2/workflow/  v2/context/
全部历史测试套件 stage1/3/4/6/7/8/9/10（断言一个不动）
```

**允许修改（§8.2，最小增量）**：
`v2/agents/storyboard.py`（新）/ `v2/prompts/v0.2/storyboard_generation.txt`（新）/
`v2/logs/stage11_storyboard/*`（新）/ `v2/agents/__init__.py`（纯追加导出）/
`v2/core/validator.py`（**仅当确有缺口时纯追加；A 会话判定：本阶段不需要改 —— 现有
`StoryboardValidator` + `validate_dict` 已足够**）。
⚠️ 注意 T15（Composer 衔接）**不需要改 `visual_compose.py`**：`compose()` 已经是只读解析层。

**另一条容易踩的**：`v2/core/__init__.py` 若要导出 `StoryboardValidator` 等，
属"纯追加"；但 §1.4 已确认 `StoryboardValidator` 已在 `core.validator` 里，
**不需要动 `core/__init__.py`**。

### 5.2 交付前必做的复位（两条命令，A 会话实测有效）

```bash
# 坑 1（T-6）：历史回归重写的报告产物
git checkout -- v2/logs/stage4_memory_context_review_test/stage4_report.json \
                v2/logs/stage4_memory_context_review_test/stage4_console.txt \
                v2/logs/stage6_casting/stage6_report.json \
                v2/logs/stage7_scene_generation/stage7_report.json
# 坑 2（T-1）：仓库根记账泄漏
rm -f llm_calls.jsonl v2/llm_calls.jsonl
git status --short        # 期望：只剩 Stage 11 的预期新文件
```

---

## 6. 给 B 会话的最小执行清单

1. 备份式起手：先跑 `env -u PYTHONPATH python v2/logs/stage10_visual_asset/run_tests.py`
   确认 76/76（**跑完立刻做 §5.2 的复位**）。
2. 新建 `v2/agents/storyboard.py`：`StoryboardAgent` + `StoryboardStep` + §2.4 的七个规范化函数。
3. 新建 `v2/prompts/v0.2/storyboard_generation.txt`：角色设定 + JSON 契约；
   **受控词表走 user payload（§2.3 方案 b）**。
4. `v2/agents/__init__.py` 纯追加 `StoryboardAgent` / `StoryboardStep`。
5. 写 §4 的 T01~T05 关键单测，先让"mock 正常路径 + 词表兜底 + 名字回锚"绿。
6. **不要碰** §5.1 的任何文件；**不要**加词表值（遇到需新增 → 停下报总控，R10）。
7. 完成后按 §5.2 复位并报告；**不 commit**。

---

## 7. 结论与 AC 映射自查

| AC | A 会话的判断 |
|---|---|
| AC1 套件全绿 | §4 的 T01~T19 可执行；历史回归 rc=0 有现成范式 |
| AC2 V0.1 冻结 | 已核实当前 0 diff；§5.2 复位后仍应 0 diff |
| AC3 零侵入 | §5.1 清单已逐条核实；`validator.py` 判定**无需改** |
| AC4 产出可用 | §1.4 规则表 + §2.4 规范化 = 可达；**R5 需补一条降级规则** |
| AC5 词表纪律 | 规范化层映射；**0 改词表**；stage8 断言原样通过 |
| AC6 Token 硬证据 | T18 逐字节断言可写；§2.4 把 warnings 放 Storyboard.metadata 不破坏断言 |
| AC7 链路可通 | T15 可写；**无需改 `visual_compose.py`** |
| AC8 机制复用 | §2.5 `needs_review=True` + 既有 Review/Artifact/Retry，**零新机制** |
| AC9 真实 API | 默认 mock；真实冒烟 ≤3 次文本，独立 `run_real_storyboard.py` |
| AC10 静态检查 | T16 覆盖 |
| AC11 文档同步 | D 会话；本文件即 A 会话的文档产出 |
| AC12 Git 无泄漏 | §5.2 两条复位命令 |

**A 会话的两条待总控裁决**：
1. **R5**：`kind=="dialogue"` 缺 `dialogue` 时的降级规则，需批准（§3.1-1）。
2. **D-1**：指令 §二 的 HEAD 描述过期（无关 1 行文档 commit），建议 D 会话顺手更新。

---

## 8. 【总控裁决，2026-09-13】A 会话产出验收

| 总控独立核实结论：A 会话产出 = PASS。**逐条复验了它声称的每项事实（未采信自述），全部属实：**
>
> （下表为总控复验记录，非 A 会话自述）

| A 会话声称 | 总控实测 | 结论 |
|---|---|---|
| D-1：HEAD = `dc335a7`，是 `c8a3ed8` 的文档子 commit | `git log` 确认；`v0.2-stage10` tag 仍 = `c8a3ed8` | ✅ 属实 |
| R1：registry 非空时 `shot_type` 受约束 | `validator.py` 确认 `INVALID_SHOT_TYPE` 分支 | ✅ 属实 |
| R5：`dialogue` 需 speaker **与** dialogue 双非空 | `validator.py` L656-L666 确认两条 MISSING_FIELD | ✅ 属实 |
| 要点 4：`shot_size` / `composition` 默认空串 | 实测 `''` / `''`（`MS`/`single` 是指令的兜底值） | ✅ 属实 |
| `validate_dict` 存在 | `validator.py` L983（StoryboardValidator） | ✅ 属实 |
| `subject` 会进 Composer | `visual_compose.py` L143 确认 | ✅ 属实 |
| 两个坑复现并已复位 | 工作区仅 `?? stage11_plan.md` | ✅ 属实 |

### 裁决 1：R5 —— **批准**（降级规则）

**采纳** A 会话建议：`kind=="dialogue"` 但台词内容无法补全 →
**降级为 `kind="general"` + warning**。

理由：`ShotValidator` 是**门槛**不是修复器（`ORCHESTRATION.md` §3.2），
指令未覆盖此路径会导致"合法 LLM 输出 → 硬拒 → 重试耗尽 → 永久 FAILED"。
降级 + warning 保住了"不静默、可追溯、能通过"三者。

已写入指令 §7.2 第 8 条（同批加入 `move` 类同构情形）。

### 裁决 2：R1 —— **批准**（shot_type 安全化）

**采纳**：非空但既不在 `registry.shot_names()` 也不在 `SHOT_SIZES` → **清空 + warning**。
已写入指令 §7.2 第 7 条，并**修正**指令 §6.2 原来"自由文本（非受控）"的错误描述。

### 裁决 3：要点 4（shot_size 默认值）—— **维持指令原判**

**采纳 (a) 照指令**：越界 → `MS` / `single`（不是空串），**必须配 warning**。
理由：空串虽与字段默认一致，但 Stage 13 出图会因缺景别而退化；`MS` 至少可渲染。

### 裁决 4：D-1（指令 HEAD 描述过期）—— **已由总控直接修正**

指令 §二 已改为"HEAD 应为 `dc335a7`；关键断言：`v0.2-stage10` tag = `c8a3ed8`"。
并明确"**tag 指向才是锚点**，HEAD 可因纯文档 commit 前移"。
**D 会话无需再改指令。**

### 裁决 5：§2.3 prompt 词表同步方案 —— **批准 (b) + (a) 自检**

采纳 A 会话推荐：受控词表走 **user payload（f-string 渲染常量）**，
prompt 文件只放角色设定与 JSON 契约；**并补一条运行时自检**（prompt 内词表文字
与 `core.shot` 常量不一致 → `StepError`）。

### 裁决 6：§2.2 Scene 注入方案 —— **批准 S-A**（`inputs["scene"]`）

采纳。补充硬约束（已隐含在指令 §7.3，此处显式化）：
**缺 `inputs["scene"]` → Step 抛 `StepError`（可重试），绝不静默产出空 Storyboard。**

### 对 B 会话的追加要求（由本次裁决产生）

```text
1. 规范化实现 §7.2 全部 **8 条**（原 6 条 + 新增 shot_type 安全化 + dialogue/move 降级）
2. 受控词表从 core.shot 常量渲染进 user payload（不手抄进 prompt 文件）
3. 加"prompt 词表 vs core.shot 常量一致性"运行时自检
4. warnings 统一放 Storyboard.metadata["normalize_warnings"]
   （不放 Shot.metadata——否则 T18 逐字节断言可能被扰动）
5. subject 约束为短名词短语 + 长度截断（40 字），prompt 里明确要求
```

### 测试方案补充（C 会话 → 指令 §4.2 的 T04b/T05b/T07b 全部**采纳为必做**）

```text
T04b  shot_type 自由文本 → 被清空（R1 专测）★最易漏
T05b  positioning 指向不存在站位点 → 剔除 + warning（R6 专测）
T07b  dialogue 但台词为空 → 降级 general 且无 MISSING_FIELD（R5 专测）
```

**至此 Stage 11-A 阶段关闭。B 会话可依指令 §6 + 本文件 §6 开工。**

---

## 9. 【总控裁决，2026-09-14】Stage 11 收尾验收

**背景**：Stage 11-B/C/D 已完成，产出 `v2/logs/stage11_storyboard/stage11_report.md`
（自述 65/65 + 历史回归 8 套全绿 + 真实 API 0 次）。

### 9.1 验收状态（诚实记录）

```text
总控本次**未能完成独立复跑**（跑 stage11 套件时超时被中断）→
  "65/65" 目前仍是**报告自述**，未经总控验证。
Stage 11 因此判定为：**代码/测试完成，待验收 → 待 commit + tag**。
不得在文档中写"已封存"（本日已修正 HANDOFF / PROJECT_STATUS / ROADMAP 三处错误表述）。
```

### 9.2 裁决 7：两条防御性回锚 —— **批准**

| 补充 | 触发硬失败 | 裁决 |
|---|---|---|
| `actions` 回锚：`state` 越界 → `standing`；角色未登记 / 动作未注册 → 剔除整条 | `INVALID_STATE` / `UNKNOWN_CHARACTER` / `INVALID_ACTION` | ✅ 批准 |
| `assets` 回锚：`kind` 越界或 `ref` 空 → 剔除；character/location/action 类 `ref` 未登记 → 剔除 | `INVALID_ASSET_KIND` / `MISSING_FIELD` / `UNKNOWN_CHARACTER` / `INVALID_LOCATION` / `INVALID_ACTION` | ✅ 批准 |
| 列表字段非列表 → 记 warning 后按空处理 | 信息静默丢失（C 会话修复） | ✅ 追认（C 会话已修） |

**理由**：与 R1/R5 同性质——**必要补全，非扩大范围**。
规范化层的职责就是"把 LLM 的不可控挡在校验器之外"（指令 §7.2 的立意），
`actions` / `assets` 的引用与 `characters` / `location` 完全同构，漏掉才是缺陷。

**附带要求**：上述回锚产生的 warning 必须一并进 `Storyboard.metadata["normalize_warnings"]`
（与 §7.2 八条同口径，不新增载体）。

### 9.3 裁决 8：D-2（fail-loud）—— **维持原判，非缺陷**

`Scene.info.who` 含未登记角色 → `UNKNOWN_CHARACTER` → 3 次重试后 FAILED。
**这是正确行为**（不静默丢角色）。处置：**Stage 12 在调度层加"喂之前先校验 Scene"的前置门**
（已写入 `v0.2开工指令12` §7.2）。

### 9.4 裁决 9：多场景调度缺口 —— **不在 Stage 11 修，转 Stage 12**

Stage 11 的 S-A（`inputs["scene"]`）只支持单 scene/run。
**Stage 11 不修**（会突破已封存的 Step 契约）；由 Stage 12 的编排层解决
（每场景一次 engine run，零改 `v2/workflow/`）。已写入 `v0.2开工指令12` §7.1。

### 9.5 裁决 10：复位清单扩充 —— **采纳**

指令 §3.2 只列了 4 个文件；实测跑 stage8/9/10/11 的嵌套回归会**连带重写 stage3** 的
`stage3_report.json` / `stage3_console.txt` → **复位清单是 6 个文件**。
已同步更新 `HANDOFF.md` §5 与 `v0.2开工指令12` §3.2。

### 9.6 Stage 11 收尾动作（待人工批准，总控不自动执行）

```bash
cd /d D:\AI\FilmAgent-main
rm -f llm_calls.jsonl v2/llm_calls.jsonl
git checkout -- v2/logs/stage3_workflow_test/stage3_report.json \
                v2/logs/stage3_workflow_test/stage3_console.txt \
                v2/logs/stage4_memory_context_review_test/stage4_report.json \
                v2/logs/stage4_memory_context_review_test/stage4_console.txt \
                v2/logs/stage6_casting/stage6_report.json \
                v2/logs/stage7_scene_generation/stage7_report.json
git status --short          # 期望：仅 Stage 11 预期文件
git diff -- FilmAgent/      # 期望：空
git add v2/ && git commit -m "feat(v0.2): storyboard agent (stage11)"
git tag v0.2-stage11        # ← 需人工明确批准
```

**Stage 11 状态：PASS（代码/测试完成）→ 待 commit + tag 封存。**


