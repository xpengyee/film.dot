# FilmAgent V0.2 阶段 8 验收报告 —— Canonical Schema / Shot-level Data Model

> 对应 `D:\AI\FilmAgent\v0.2开工指令7` 第二部分（§九～§二十二）。
> 本报告在 `v2/logs/stage8_canonical_schema/` 目录内生成，全部改动只落在 `v2/`。

---

## 1. 阶段 8 做了什么

把 V0.2 的数据结构从「Project → Scene → Script」升级为「Project → Scene → Beat → Shot → Asset」：

- 建立 **Canonical（唯一内部标准）镜头层 Schema**：`Shot`（最小渲染单元）→ `Beat`（叙事/动作连续单元）→ `Storyboard`（一个 Scene 的结构化视觉规划 artifact）→ `ShotAsset`（统一资产抽象）。
- 建立**受控镜头词表**（§十三，自建、不照搬 take 代码）：`shot_size` / `camera_angle` / `camera_movement` / `composition` 四轴全用受控枚举，并优先兼容 V0.1 已有镜头资产（`V01_SHOT_SIZE_MAP`）。
- **Validator 升级**（§十四，确定性纯代码，不依赖 LLM）：新增 `AssetValidator` / `ShotValidator` / `BeatValidator` / `StoryboardValidator`，覆盖角色/地点/动作/镜头语言/站位/Asset 引用/Shot-ID 唯一/Beat-ID 唯一/必要字段缺失等检查；原阶段 1~7 校验器零改动。
- **Adapter 边界层**（§九原则 1 + §十五）：V0.1 数据只经 `core/adapter.py` 进出 Canonical，实现「V0.1 Script → Canonical Storyboard → V0.1-compatible」**无损往返**（真实 `script/0.json` 全量验证）。
- **Agent 公共骨架**（§十六）：抽取 `LLMAgent` / `LLMStep`（`v2/agents/base.py`），`CastingAgent/Step` 与 `SceneGenerationAgent/Step` 改为继承它，行为不变；**不新增** Storyboard / Image / Video / VLM Agent。
- 扩展 `core/__init__.py` 统一导出 Canonical 模型/词表/adapter/new validators。

**本阶段未做**：任何图像/视频/VLM API、任何真实 LLM 调用、任何 UI/前端、take / movie-Agent 代码引入。真实 LLM 调用 = 0。

---

## 2. 新增 / 修改文件

新增（Stage 8 核心交付）：

| 文件 | 内容 |
|---|---|
| `v2/core/shot.py` | Canonical 镜头层 Schema：受控词表 + `CameraPlan` / `ShotAsset` / `Shot` / `Beat` / `Storyboard` + 镜头名↔Canonical 小工具 |
| `v2/core/adapter.py` | V0.1 ↔ Canonical 边界转换（行↔Shot、Scene↔Storyboard、整本脚本往返） |
| `v2/agents/base.py` | Agent / Step 公共骨架：`LLMAgent` + `LLMStep` |
| `v2/logs/stage8_canonical_schema/` | 本阶段测试套件 `run_tests.py` + `stage8_report.json` + `stage8_report.md`（本文档） |

修改（Stage 8 演进，不改业务行为）：

| 文件 | 改动 |
|---|---|
| `v2/core/validator.py` | 新增 4 个 Canonical Validator（阶段 1~7 Validator 零改动，仅扩 `__all__`） |
| `v2/core/__init__.py` | 导出 shot / adapter / new validators（顶层 `CanonicalShotAsset` 别名避免与阶段 1 `ShotAsset` 重名） |
| `v2/agents/__init__.py` | 导出 `LLMAgent` / `LLMStep` |
| `v2/agents/casting.py` | 继承 `LLMAgent`/`LLMStep`，prompt 文本与规范化逻辑逐字保持（A-keep 行为） |
| `v2/agents/scene_generation.py` | 同上 |
| `v2/logs/stage{3,4,6,7}_*/run_tests.py` | V0.1 完整性测试断言更新：基线封存后 HEAD 不再等于 v0.1，改为断言「v0.1 commit 未动 + `git diff -- FilmAgent/` 为空 + `v0.2-stage7` tag 存在」（见 §15 问题 1） |

未跟踪的 `CLAUDE.md`（仓库根）为历史遗留，非本阶段产物。

---

## 3. Canonical Schema 结构

`v2/core/shot.py`，纯数据结构 + `to_dict`/`from_dict` 对称序列化（serialize→deserialize→serialize 稳定）。

**受控词表**（§十三）：

- `SHOT_SIZES = ("WS","MS","MCU","CU","ECU")`
- `CAMERA_ANGLES = ("eye_level","high_angle","low_angle","overhead")`
- `CAMERA_MOVEMENTS = ("static","pan","tilt","dolly","tracking","zoom","handheld")`
- `COMPOSITIONS = ("single","two_shot","over_the_shoulder","group")`
- `SHOT_KINDS = ("dialogue","move","general")`；`SHOT_STATUSES = ("planned","approved","rendered")`
- `ASSET_KINDS`（§十二 统一抽象，9 类）：`character / location / prop / action / camera / image / video / audio / reference`
- `V01_SHOT_SIZE_MAP = {"Long Shot":"WS","Mid Shot":"MS","Close Shot":"CU"}`（兼容 V0.1 已有资产）

**`Shot`**（最小渲染单元）：`shot_id / scene_id / beat_id / kind / shot_type / camera(CameraPlan) / composition / subject / speaker / dialogue / actions[Action] / location / characters[] / positioning[PositionRef] / move(Move) / reason / insertion_reason / insertion_position / visual_description / continuity / duration / assets[ShotAsset] / status / metadata`。

**`Beat`**：`beat_id / purpose / shots[] / metadata`。

**`Storyboard`**（独立 artifact，不是图片）：`scene_id / location / characters(=who) / synopsis(=what) / initial_positions / beats[] / metadata`，带 `flatten_shots()` 保序展平。

**`ShotAsset`**：`kind / ref / detail` —— 引用已有资产（character/location/action 查注册表）或未来媒体产出（image/video/audio 仅可表达，不真生成）。

**命名说明**：阶段 1 已有 `core/assets.py` 的目录资产类 `Asset`/`ShotAsset`（A-keep 不能动），因此本阶段的「§十二 统一资产抽象」命名为 `ShotAsset`（在 `core/__init__.py` 导出为 `CanonicalShotAsset` 消除歧义）。Canonical Shot 的 actions/positioning/move 复用阶段 1 的 `Action` / `PositionRef` / `Move` 模型（to_dict 内以各自 to_v01 子格式出现），不重复造轮子。

---

## 4. Scene / Beat / Shot / Asset 关系

```text
Project
  ↓（World.locations / World.characters / Episode.scenes 承载）
Scene（统一 Scene：who/where/what/initial positions/lines）
  ↓  canon adapter（scene_to_storyboard）
Storyboard（结构化视觉规划 artifact，独立落盘）
  ├── Beat 01（叙事/动作目的，如"人物进房间"）
  │    ├── Shot 01（一段连续画面：台词/动作/站位/镜头语言/资产引用）
  │    └── Shot 02
  └── Beat 02（如"开始对话"）
       ├── Shot 03
       └── Shot 04
每个 Shot 内部引用 assets[ShotAsset]（character/location/prop/action/camera → 已有资产；
image/video/audio → 未来生产链产出）。
```

关键语义：
- **Shot 是渲染原子单元**：一个 Shot = 一段连续画面 + 表演内容（speaker/dialogue/actions/move）+ 空间（characters/positioning/location）+ 镜头语言（shot_type/camera/composition）+ 未来出图提示基础（visual_description）+ 引用的资产。
- **Beat 是叙事单元**：一个 Scene 按剧情切成多段 Beat，每段 Beat 挂 1..n 个 Shot；Beat 表达"这一段在完成什么"（purpose）。
- **Storyboard 可自包含**（脱离 Scene 也能还原：location/characters/synopsis/initial_positions 齐全），为将来 `Storyboard → Image Generation → Image Review → Video Generation` 提供输入计划。

---

## 5. Validator

`v2/core/validator.py` 内新增（纯代码、确定性、不依赖 LLM；阶段 1~7 既有 Validator 未被改动）：

- **`AssetValidator`**：ShotAsset 的 `kind ∈ ASSET_KINDS`、`ref` 非空、引用的 character/location/action 存在（有注册源才查，避免无意义误报）。错误码：`INVALID_ASSET_KIND / MISSING_FIELD / UNKNOWN_CHARACTER / INVALID_LOCATION / INVALID_ACTION / WRONG_TYPE`。
- **`ShotValidator`**：逐镜检查 —— ID 类型、kind/status、dialogue 必有 speaker+dialogue / move 必有 Move 对象且 destination 非空、受控词表（景别/角度/运镜/构图）、镜头名（有注册表时必须是已注册镜头或受控景别）、地点注册、角色在清单、动作合法且状态合法、站位点与走位目的地在当前地点存在、资产引用存在。
- **`BeatValidator`**：beat_id 类型、shots 是列表、Beat 内 Shot 的 scene_id/beat_id 归属一致、Beat 内 Shot ID 不重复，委托 `ShotValidator` 逐镜校验。
- **`StoryboardValidator`**：scene_id 类型、beats 是列表、地点注册（非法地点会抑制后续站位检查避免噪音）、Storyboard 级角色在清单、initial_positions 角色+站位合法、Beat ID 全局唯一、Shot ID 全局唯一、逐 Beat 委托；另提供 `validate_dict()` 直接校验落盘/LLM 产出的 dict（`Step.validate` 用）。

确定性保证：校验过程不含 LLM/网络调用，入参即出参，同输入必同输出（T21 + 全套 mock 回归佐证）。

---

## 6. Adapter

`v2/core/adapter.py`（边界层，V0.1 兼容逻辑只允许在这里，禁止向 V0.2 内部扩散）：

- `line_to_shot(line, *, scene_id, beat_id, shot_id, characters) -> Shot`：V0.1 剧本一行（台词/走位）→ Canonical Shot。原始镜头名**原样保留**在 `shot.shot_type`（如 `"Long Shot 2"`）；`camera` 用 `infer_camera_from_shot_label` 尽力推断（Long→WS / Mid→MS / Close→CU），推断不出（Pan/Track/Follow/Zoom/360/Curve 等运镜）就 `None`，**绝不编造**。台词行 → `kind="dialogue"`，走位行 → `kind="move"`（reason/insertion_* 透传保留）。
- `shot_to_line(shot) -> Line`：逆向。只支持 dialogue/move 内容镜头；纯视觉 `general` 镜头在 V0.1 行格式没有对应物，**明确抛 `ValueError`，不静默丢内容**。
- `scene_to_storyboard(scene)`：统一 Scene → Storyboard。V0.1 没有 Beat 概念，自动派生单 Beat（beat_id=1）承载全部 Shot，who/where/what/initial positions 落到 Storyboard 字段，保证可无损还原。
- `storyboard_to_scene(sb)`：逆向还原统一 Scene。
- 整本便捷函数：`scenes_to_storyboards` / `storyboards_to_scenes` / `v01_script_to_storyboards` / `storyboards_to_v01_script`。

---

## 7. Agent common foundation

`v2/agents/base.py`：

- **`LLMAgent`**：公共类属性 `name` / `needs`（声明式上下文需求清单）；`__init__(llm_client, *, prompt_name=, prompt_path=)`（允许 `llm_client=None` 供探 needs/初始化测试）；`system_prompt()` 读取 `v2/prompts/v0.2/<name>.txt`（缺失明确报 `StepError` 可重试）；`_parse_json()` 剥 ```` ```json ```` 围栏 + `json.loads`（失败抛 `StepError`）；`chat_json(step=, user_payload=)` 组装 system+user → `LLMClient.chat` → dict（非 dict 抛 `StepError`）。
- **`LLMStep(Step)`**：`step_id/name/needs/needs_review` 类属性；`__init__(agent=,...)` 注入业务 Agent（缺 agent 明确 `ValueError`）；`assembled_context(context)` 从 `WorkflowContext.inputs['assembled_context']` 取 ContextAssembler 的产物（缺失抛 `StepError`）；`make_output(data, *, artifact_name, metadata)` 构造 `StepOutput`。

改造后：`CastingAgent/Step` 与 `SceneGenerationAgent/Step` 继承公共骨架（`isinstance`/`issubclass` 断言 + needs 精确比对通过），prompt 文本、`_normalize` 规范化、Step 落盘/校验逻辑**逐字未变**；未来 StoryboardAgent/ImageAgent/VideoAgent 直接继承即可。本阶段**不实现**后三者。

---

## 8. V0.1 compatibility

- **V0.1 冻结**：`FilmAgent/` 0 diff；v0.1 tag/commit（`344c0c6`）未动（T20 + 回归内嵌断言）。
- **进入**：V0.1 剧本/角色数据只经 `core/models`（消化三种历史字段写法）→ `core/adapter` 进入 Canonical；Canonical 序列化结果**不残留任何 V0.1 容器键**（T02）。
- **§十五 硬性验收**：真实 `FilmAgent/Script/script/0.json`（3 场景、49 行、含 2 条 move 行）→ `v01_script_to_storyboards` → `storyboards_to_v01_script` **与原文深等**（T14）；逐行 →Shot→行 **模型级无损**（T15）；角色/场景/台词/动作/镜头/站位全部保留。镜头名兼容：V0.1 已注册镜头（Close/Mid/Long…）优先复用并被映射到受控景别，编号变体（`Long Shot 2` / `Track Shot 1`）经 `base_name` 剥离后判定（T16）。

---

## 9. 测试结果

新增套件 `v2/logs/stage8_canonical_schema/run_tests.py`：**27/27 通过**（T01~T21 + T13b），真实 LLM 调用 = 0。

| 分组 | 结果 |
|---|---|
| A. 词表 + Canonical 模型序列化（T01~T06） | 6/6 |
| B. 真实 fixture + 全链路（T07 §十九 fixture、T08 全链路） | 2/2 |
| C. Validator 确定性（T09~T13 + T13b） | 6/6 |
| D. Adapter + V0.1 往返（T14~T16） | 3/3 |
| E. Agent 公共骨架（T17~T19） | 3/3 |
| F. V0.1 完整性 / 无真实调用（T20、T21） | 2/2 |
| 回归 R1~R5 | 5/5 |
| **合计** | **27/27** |

覆盖要点：受控词表与 §十三一致；Shot/Beat/Storyboard/CameraPlan/ShotAsset `serialize→deserialize→serialize` 稳定；§十九 硬性 fixture `Scene→Beat01(Shot01/02)/Beat02(Shot03/04)` 含 角色/地点/动作/台词/镜头/站位/Asset reference，往返完全一致且 StoryboardValidator 0 错误；`Project→Scene→Beat→Shot→Asset` 全链路 0 错误并无损回 Scene；image/video/audio 未来媒体资产可表达；各 Validator 的拒绝路径（词表越界/镜头名未注册/地点非法/角色未知/动作与状态非法/站位非法/ID 重复/Scene 错配/缺字段/坏 dict）全部命中预期错误码。

（详细逐项结果见 `stage8_report.json` / `console.txt`。）

---

## 10. 回归结果

在 stage8 套件内以子进程复跑历史阶段，**全部通过**：

| 阶段 | 结果 |
|---|---|
| 阶段 1（core 模型/校验/资产） | 19/19 |
| 阶段 3（Workflow/重试/Artifact） | 38/38 |
| 阶段 4（Memory/Context/Review，内含阶段 1/2(mock)/3 复验） | 25/25 |
| 阶段 6（Casting，mock） | 24/24 |
| 阶段 7（Scene Generation，mock） | 29/29 |

另单独确认：`compileall`（core + agents）通过；源码 key/绝对路径扫描无真实密钥、无本地绝对路径（唯一命中为 config.py 既有 `https://api.deepseek.com` URL，正则对 `s://` 的误报，非本阶段引入，非密钥）。

---

## 11. 真实 API 调用次数

**0**（本阶段不接图像/视频/VLM；未新增真实 LLM 调用；测试全部 mock / fixture / 真实只读 V0.1 数据）。

---

## 12. Token / Cost

真实调用 = 0 → 本次新增 token 消耗 = 0，cost = **¥0.00**。（阶段 8 为纯数据结构 + 确定性校验，无计费行为。）

---

## 13. V0.1 是否修改

**NO**。`git diff -- FilmAgent/` 为空；v0.1 commit `344c0c6…` 未动；工作区对 `FilmAgent/` 无任何 M/D/R。

---

## 14. Git status

```text
HEAD            = 300aae44ad1d704219822efcf55249d1d59ad118  （= v0.2-stage7 基线）
v0.1^{commit}   = 344c0c6b7c48497be934957c29fcae029cca6752  （未动）
v0.2-stage7^{commit} = 300aae44ad1d704219822efcf55249d1d59ad118
git diff -- FilmAgent/ = 空
```

变更范围：全部在 `v2/`（见 §2 文件清单 + 回归重跑引起的 stage{3,4,6,7} console/report 证据文件更新）。
仓库根 `CLAUDE.md` 未跟踪，为历史遗留，非本阶段产物。

**本阶段未 commit / 未 tag**（按 §二十二：等人工验收后再处理）。

---

## 15. 遇到的问题

1. **基线封存使旧断言过时**：阶段 3/4/6/7 的「V0.1 完整性」测试断言 `HEAD == 344c0c6`（即 HEAD 必须等于 v0.1 commit）。基线 commit `300aae44`（v0.2-stage7）建立后 HEAD 合法地演进而离开了 v0.1，该断言必然 FAIL（回归 21/24、28/29 等）。排查确认**非业务 bug**（其余业务断言全 PASS），根因是版本策略演进。修复为真正的 V0.1 冻结不变量：`v0.1^{commit} == 344c0c6`（未移动）+ `git diff -- FilmAgent/` 为空 + `FilmAgent/` 无 M/D/R + `v0.2-stage7` tag 已建立。修复后 stage3=38/38、stage4=25/25、stage6=24/24、stage7=29/29 全过。
2. **测试脚本自身笔误**（修断言，非实现 bug）：T02 把 Canonical Shot 自己的 `speaker/dialogue` 键误列进"V0.1 容器键"黑名单；T09 的 scene_id 在 fixture 与构造处不一致（101 vs 1）；T13b 未考虑校验器对 `beats` 非列表时的早退顺序。已修正断言后全绿。
3. **静态扫描误报**：`config.py` 的 `https://api.deepseek.com`（阶段 2 既有）被"盘符"正则把 `s://` 当绝对路径命中，属误报、非密钥。
4. **设计取舍而非缺陷**：V0.1 的 Pan/Track/Follow/Zoom/360/Curve Surround 等是运镜、没有单一景别，`infer_camera_from_shot_label` 对它们返回 `None`（不强行映射、不编造），原始镜头名保留在 `shot.shot_type`，信息不丢；general 视觉镜头无 V0.1 行对应物，`shot_to_line` 明确抛错而非静默丢弃。

---

## 16. 遗留问题

- StoryboardAgent / ImageAgent / VideoAgent / VLM Agent **未实现**（§十六明确留待后续阶段）。
- image/video/audio 资产只让 Schema **可表达**，未接真实渲染/生成。
- 当前从 V0.1 自动派生 Storyboard 只有**单 Beat**（beat_id=1）；真正的多 Beat 叙事划分要等未来 StoryboardAgent（人工/智能规划）。
- 阶段 8 未 commit/tag，等待人工验收。
- 既有遗留（与阶段 8 无关）：人工审核目前为程序化 decision 模拟，交互式审核界面未做。

---

## 17. 与架构报告的偏差

- 版本策略正式生效：`v0.1 → v0.2-stage7(=300aae44) → Stage 8 → …`，此后每重大里程碑 commit/tag（阶段 8 本身按指令暂不 commit）。
- Asset 命名与架构表述的差异：§十二写"统一 Asset"，但阶段 1 已有 A-keep 的目录资产类 `Asset`/`ShotAsset`。为避免重名冲突，Canonical 统一资产实现为 `core/shot.py` 的 `ShotAsset`，并在 `core/__init__.py` 导出为 `CanonicalShotAsset`；`core/assets.py` 原类型不动。这是命名层折衷，语义一致。
- 比架构基线多出：Canonical 词表（self-defined vocabulary）、adapter 双向无损层、Agent/Step 公共骨架；未新增任何业务 Agent（与 §十六一致）。
- 其余无偏差：依赖方向（core 最底层，shot/adapter 均在 core）、Agent 边界铁律、V0.1 兼容逻辑只在边界层、API key 不落盘 等约定均保持。

---

## 18. 下一阶段建议

1. **StoryboardAgent**：真实 Beat 规划（把 Scene 语义切成有明确目的的多 Beat），作为第一个消费 Canonical Schema 的业务 Agent。
2. **Shot → Image 适配**：从 `visual_description + camera(受控景别/角度/运镜) + composition + characters/positioning/location` 组装 text-to-image prompt，验证 Canonical 是否足以驱动真实出图。
3. **Image Review / Video 链路**：给 Storyboard 的 shot.assets（image/video）接入审核与渲染状态机（status: planned → approved → rendered）。
4. 视需要接入 **VLM** 做"镜头画面 ↔ 剧本台词/走位"一致性校验（仍是未来决策）。
5. 人工验收阶段 8 后：commit + tag（建议 `v0.2-stage8`），再进入阶段 9。

---

## 验收结论

```text
V0.2 阶段 0～7 基线：PASS
V0.2 阶段 8：PASS

Commit：（阶段 8 未提交，等待人工验收后处理；当前 HEAD=300aae44=v0.2-stage7）
Tag：v0.2-stage7（未移动）
V0.1 modified：NO
Real LLM calls：0
Tests：stage8 套件 27/27；回归 阶段1 19/19、阶段3 38/38、阶段4 25/25、阶段6 24/24、阶段7 29/29
```

**阶段 8 验收结论：PASS**
