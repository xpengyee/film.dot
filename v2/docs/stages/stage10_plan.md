# Stage 10 架构方案（10-A 分析 / 架构设计）—— Visual Asset + Vision/Multimodal

> 本文件是 **Stage 10-A（分析 / 架构会话）** 的产出，仅做架构设计，**不含实现代码**。
> 依据：《FilmAgent V0.2 Stage 10-A 开工指令》。B 会话（代码实现）按本方案推进，不重复全项目分析。
>
> 对应 Git：HEAD = `82a89b5`（feat(v0.2): complete provider architecture）
> Tag：`v0.2-stage9` = `82a89b50d337663027e8ecd638f45d7b94eaa882`（未移动）；
> `v0.1` = `344c0c6…`；`v0.2-stage7` = `300aae4…`；`v0.2-stage8` = `abf8e88…`。
> `git status --short` = 空；`git diff -- FilmAgent/` = 空。Stage 10：NOT IMPLEMENTED。
>
> 当前文本模型环境：GLM-5.0（接入方式 Zode）。**本方案任何新架构均不硬编码
> DeepSeek / GLM-5.0 / Zode**——模型与接入方式只存在于"当前配置"层，架构保持模型无关。

---

## 1. Stage 10 的目标

Stage 10 的核心**不是"生成图片"**，而是：

> **建立一个稳定的 Visual Asset 系统，使角色、地点、道具、风格和参考图能够被后续
> Vision、Image、Video 模型反复使用，并承担 VLM QA 的 Ground Truth Reference 角色。**

目标结构：

```text
Character / Location / Prop / Style / Reference Asset
        ↓（被引用，不复制）
Shot / Storyboard
        ↓（组装时才展开）
Vision / Multimodal（参考图 → 结构化 Visual Profile）
        ↓
Image Generation（Stage 13+）
        ↓
Video Generation（Stage 14+，Seedance 或其他，不绑定）
```

Stage 10 分四段：

```text
10-A  分析 / 架构设计        → 本文件                                    本次
10-B  代码实现 + 1~3 个真实视觉样例（图片 → Vision/Multimodal → Profile）
10-C  全面测试 / Debug
10-D  文档 / 交付 / Git
```

**明确不在 Stage 10 范围**：真实图片生成、真实视频生成、TTS、Seedance 接入、
Image/Video Agent、完整 VLM QA Agent、本地模型部署、向量数据库、Embedding 检索接入。

---

## 2. 当前 Character / Location / Asset 结构（基于真实代码逐条核实）

以下结论来自对 `v2/core/models.py` / `assets.py` / `shot.py` / `validator.py` /
`adapter.py` / `v2/memory/store.py` / `v2/llm/providers/` 的实际阅读，不是猜测。

### 2.1 Character：纯语义，零视觉字段

`Character`（`core/models.py:117`）字段：`name / age / gender / occupation /
personality_traits / speaking_style`——全部是**剧情语义**信息（V0.1
`actors_profile.json` 同构，实测样例 Mercy/Caleb/Ivy/Theo）。没有任何外貌、
发型、服装、色彩字段。`to_v01()` 往返是 Stage 1/8 封存行为。

结论：**视觉层完全缺失**，且 Character 数据类是 V0.1 兼容边界（`from_v01/to_v01`
被冻结测试覆盖），**不应往里塞视觉字段** → 视觉层必须独立建（见 §4）。

### 2.2 Location：只有"站位点"，没有视觉身份

现有地点资产两层：
- `LocationAsset`（`core/assets.py:39`）：name + positions（站位点清单），来自
  `v2/assets/locations/<地点名>/position.json`；
- Scene/Shot 里的地点 = **名字字符串**（`Scene.info.where` / `Shot.location`）。

结论：地点的"空间布局"已有表达，但**同一地点在不同 Shot 中如何保持视觉一致**
（建筑、光照、色调、标志性陈设）没有任何表达 → 需要 Location Visual Profile（见 §5）。

### 2.3 Asset：已有两层抽象，Stage 10 不推倒、只接上

- **Stage 1 目录资产**（`core/assets.py`）：`AssetRegistry` 管 V0.1 已有的
  地点/动作/镜头/站位点（合法性清单，Validator 消费）。
- **Stage 8 镜头引用资产**（`core/shot.py:122`）：`ShotAsset(kind, ref, detail)`，
  `kind ∈ ASSET_KINDS = (character, location, prop, action, camera, image, video,
  audio, reference)`，`ref` = 语义名（角色名/地点名/道具名…），挂在 `Shot.assets`。
  docstring 已声明"image/video/audio 是未来生产链产出的媒体资产；阶段 8 只让
  Schema 能表达，不真生成"。

结论：**Shot 引用资产的挂点已经存在**（`Shot.assets` + `Shot.characters` +
`Shot.location`），Stage 10 只需要给"被引用的资产"补上**视觉身份与版本**这一层，
不需要改 Shot Schema（详见 §11）。

### 2.4 可直接复用的基础设施（禁止重造清单）

| 能力 | 位置 | Stage 10 用法 |
|---|---|---|
| Provider 接口族（Text/Vision/Multimodal/Embedding + 预留 Image/Video/Audio） | `v2/llm/providers/base.py` | Vision/Multimodal 消费 Visual Asset，**不新做 Provider 抽象** |
| Mock 四件套 | `v2/llm/providers/mock.py` | 全部新测试 0 API 0 成本 |
| Registry/Factory + Router | `v2/llm/providers/registry.py` / `llm/router.py` | 视觉 Provider 经同一工厂产出 |
| Token/Cost 单一口径 | `core/token_log.py` | 真实视觉调用记同一 JSONL |
| Agent 公共骨架 | `v2/agents/base.py`（LLMAgent/LLMStep） | VisualAssetAgent 直接继承 |
| Workflow / Artifact 版本化 | `v2/workflow/` | 视觉档案草稿 = 版本化 artifact，绝不覆盖 |
| Review 三态 | `v2/review/service.py` | Asset 审批走既有 Review，**不建第三套状态机** |
| Memory 三态 | `v2/memory/store.py` | 语义事实继续归 Memory，视觉档案不进 Memory |
| 校验器模式 | `core/validator.py`（ValidationError/format_errors） | 新增 VisualAssetValidator 复用同一错误结构 |
| Canonical Schema | `core/shot.py` / `core/adapter.py` | Shot↔Asset 引用关系在其上叠加，**0 改动** |

### 2.5 关键约束（实测核实）

- `stage8_canonical_schema/run_tests.py:156` 对 `ASSET_KINDS` 做**精确集合断言**
  （`set(ASSET_KINDS) == {character, location, prop, action, camera, image, video,
  audio, reference}`）。**任何往 ASSET_KINDS 加值的改动都会破坏 Stage 8 封存测试**
  → 10-B 不改 `shot.py` 词表（理由见 §7）。
- `ShotAsset.ref` 是语义名；全系统角色身份 = 名字（CastingValidator 强制项目内
  角色名唯一；AssetRegistry 地点名唯一）→ `(kind, name)` 在项目内唯一，可作为
  稳定解析键。
- `VisionProvider.understand(image, prompt)` 的 image 入参契约 = "已落盘图片引用 /
  content 描述"（Stage 9 docstring）→ ReferenceImage 的路径引用天然满足，无需改接口。
- `create_provider(kind="vision", vendor=非mock)` 当前明确报错"未实现真实视觉
  Provider"（`registry.py:78-83`）→ 10-B 需要补**一个**真实视觉实现类（§13/§25）。

---

## 3. Visual Asset Architecture（总体架构）

### 3.1 分层

```text
core（新）                              llm（既有）
┌────────────────────────────┐        ┌──────────────────────────┐
│ core/visual.py             │        │ providers/base.py（0 改） │
│   VisualProfile ×4（数据）  │        │   VisionProvider /       │
│   VisualAsset / Version    │        │   MultimodalProvider     │
│   ReferenceImage + 词表    │        │ providers/openai_compatible_vision.py（新）│
│ core/visual_registry.py    │        │   真实视觉实现（厂商=配置）│
│   VisualAssetRegistry      │        │ providers/mock.py（0 改） │
│   身份/版本/生命周期/持久化  │        └──────────────────────────┘
│ core/visual_compose.py     │
│   VisualComposer           │        agents（新）
│   Shot → ComposedVisualInput│       ┌──────────────────────────┐
└────────────────────────────┘        │ agents/visual_asset.py   │
         ↑ 只被引用，不反向依赖          │ VisualAssetAgent + Step  │
                                      │ （继承 LLMAgent/LLMStep） │
workflow / review / memory（0 改）     └──────────────────────────┘
   草稿=版本化 Artifact → Review 三态 → Registry 派生生命周期
```

依赖方向遵守既有铁律：`core → (llm | memory | context) → workflow → review → agents`。
`core/visual*.py` 不 import 任何上层；Agent 不 import 具体厂商；review/memory 零改动。

### 3.2 五类 Visual Asset 与稳定身份

```text
CHAR_001  Character Asset   （name = 角色名，与 Character/Casting 以名字连接）
LOC_001   Location Asset    （name = 地点名，与 AssetRegistry 地点以名字连接）
PROP_001  Prop Asset        （name = 道具名，ShotAsset(kind="prop") 以名字连接）
STYLE_001 Style Asset       （name = 风格名；项目级默认风格 + 可选镜头级覆盖）
REF_001   Reference Asset   （name = 参考名；pose/storyboard 等独立参考束）
```

**语义与视觉分离**（指令 §八）：`Character`（语义：name/role/personality…）不动；
`VisualAsset(kind="character")` 承载视觉层（hair/face/clothing/参考图/visual_prompt）。
连接键 = 名字（系统内唯一且不可变）。语义对象里**不出现**任何视觉字段；
视觉档案里**不复制**语义档案（只留 name 锚点 + 可选一句话语义摘要供 prompt 组装）。

### 3.3 单个 Visual Asset 的结构

```text
VisualAsset（稳定身份）
├── asset_id      "CHAR_001"        ← 一经分配永不变更
├── kind          character | location | prop | style | reference
├── name          "Mercy"           ← 语义锚点（= 角色名/地点名/道具名）
├── current_version "v2"            ← 指向当前生效版本
├── versions      [VisualAssetVersion, ...]
└── created_at / updated_at

VisualAssetVersion（视觉版本）
├── version        "v1" | "v2" | …  ← 单调递增，绝不复用、绝不覆盖
├── status         draft | generated | reviewing | approved | rejected
│                  | superseded | archived     （见 §10 生命周期）
├── profile        CharacterVisualProfile / LocationVisualProfile /
│                  PropVisualProfile / StyleProfile（按 kind）
├── reference_images [ReferenceImage, ...]
├── visual_prompt  组装好的视觉提示词文本（见 §15）
├── source         manual | text_planned | vision_extracted | review_edited
├── notes / created_at
```

参考图（挂**版本**上，不挂资产上——换参考图 = 新版本，符合指令 §十三）：

```text
ReferenceImage
├── image_id   "IMG_001"
├── path       相对项目目录路径（v2/projects/<pid>/visual_assets/images/…）
├── kind       character | location | prop | style | pose | storyboard | other
├── note       来源/用途说明
├── checksum   文件 sha256（"同一张图"判定 + 未来 VLM QA 比对锚点）
└── created_at
```

### 3.4 持久化

```text
v2/projects/<project_id>/visual_assets/
├── registry.json     全部资产 + 版本 + 状态（原子写，core.io.write_json）
└── images/           参考图文件本体（由接线/人工放置；Agent 不做文件操作）
```

registry.json 是视觉身份的**唯一事实源**；Workflow Artifact 里保存的是"草稿产出的
版本化快照"（审核对象），审核通过后由接线层把结果写回 registry——两者职责不同
（artifact = 过程轨迹，registry = 当前状态），不算双写。

---

## 4. Character Visual Profile（最小增量）

现状（§2.1）：视觉字段 0 个。以下字段**逐个对照**指令 §七清单后取最小集——
每个字段必须服务于三件事之一：视觉一致性 / 未来 image·video prompt 组装 / VLM QA
ground truth。不满足的不进 Schema。

```python
@dataclass
class CharacterVisualProfile:
    appearance: str = ""                # 总体外貌（一句话基调，必填）
    face: str = ""                      # 脸型/五官/肤质要点（必填）
    hair: str = ""                      # 发型/发色（必填）
    clothing: str = ""                  # 本剧默认造型/服装（必填）
    body: str = ""                      # 体型/身高印象（可选）
    color_palette: str = ""             # 角色主色调（可选）
    distinguishing_features: str = ""   # 标志性视觉特征：眼镜/疤痕/饰物（可选）
    visual_style: str = ""              # 角色级风格注记（可选；通常留空继承项目风格）
    negative_cues: str = ""             # 反向提示：不要出现的元素（可选）
```

- 全部 `str`、缺省空串——与项目既有 dataclass 风格一致（空 = 未指定，不编造）。
- 必填 4 项（appearance/face/hair/clothing）：Vision 提取可从人物图直接得到；
  文本规划可从 Casting 语义档案推导。其余可选。
- **不进** Character 语义对象、**不进** V0.1 任何格式。
- Visual Prompt（版本上的 `visual_prompt` 字段）：由 profile 确定性渲染或由
  文本模型润色后经审核定稿；它是"该角色视觉身份的一句话浓缩"，供未来 image
  prompt 组装直接引用（§15）。

### 4.1 Semantic ↔ Visual 边界（指令 §八的明确答案）

| 归属 | 字段举例 | 载体 |
|---|---|---|
| 剧情语义 | name / age / gender / occupation / personality / speaking_style / role / relationship | `Character`（core/models.py，0 改）、Memory.characters、CastingResult |
| 视觉身份 | appearance / face / hair / clothing / palette / 参考图 / visual_prompt / version | `VisualAsset(kind="character")`（core/visual.py，新） |

连接：`(kind="character", name)` ↔ Character.name。角色换造型/换参考图 →
`CHAR_001` 不变，产生 `v2`（旧版 superseded）。

---

## 5. Location Visual Profile

现状（§2.2）：地点只有站位点。同一个地点跨 Shot / 跨集稳定视觉身份的最小字段：

```python
@dataclass
class LocationVisualProfile:
    environment: str = ""            # 环境总体：室内外/氛围/时代感（必填）
    lighting: str = ""               # 基础光照设定：光源/方向/冷暖（必填）
    palette: str = ""                # 色彩基调（必填）
    architecture: str = ""           # 建筑/空间结构特征（可选）
    time_of_day: str = ""            # 默认时段（可选；镜头可覆盖）
    distinguishing_features: str = ""# 标志性视觉锚点：吧台/沙发/落地窗（可选但强烈建议）
    visual_style: str = ""           # 地点级风格注记（可选）
    negative_cues: str = ""          # 反向提示（可选）
```

- 必填 3 项（environment/lighting/palette）+ 强烈建议 distinguishing_features
  （跨镜头一致性的关键锚点）。
- **不与 LocationAsset（站位点）合并**：站位点是空间逻辑（Validator 校验走位），
  视觉档案是画面逻辑（image/video prompt）——两套关注点、两种消费方，合并会把
  Stage 1 冻结行为搅进来。连接键 = 地点名。

---

## 6. Prop / Object Asset（统一 Asset，不建独立 PropAsset 类）

分析对象：手机、汽车、电脑、关键剧情道具、家具、特殊物品。

现状：`ShotAsset` 已支持 `kind="prop"`（名字引用）；无任何 prop 视觉表达。

决策：**复用统一 VisualAsset 抽象，kind="prop"**，不建独立 PropAsset 体系。理由：
1. 道具与角色/地点的消费方完全相同（image/video prompt 组装、VLM QA、参考图管理），
   只是 profile 字段不同；
2. Stage 8 `ASSET_KINDS` 已含 prop（词表不动）；
3. 独立体系会造成第二套注册表/生命周期/审核——正是指令反对的重复建设。

```python
@dataclass
class PropVisualProfile:
    form: str = ""        # 外观形态（必填）
    color: str = ""       # 颜色（必填）
    material: str = ""    # 材质（可选）
    size: str = ""        # 相对尺度（可选）
    condition: str = ""   # 新旧/状态（可选）
    distinguishing_features: str = ""  # 可选
    negative_cues: str = ""             # 可选
```

道具入册时机：由调度层在 visual_asset_spec 里声明（如剧本里的"关键道具：旧手机"），
VisualAssetAgent 建档；普通不入册的道具由 Shot.visual_description 兜底描述
（不强制所有道具都建资产——最小增量原则）。

---

## 7. Style Asset（项目级独立表达；三层视觉指令分渠道）

需要区分三层（指令 §十一）：

| 层 | 载体（现有 or 新） | 说明 |
|---|---|---|
| **项目级风格** | `VisualAsset(kind="style")`，如 STYLE_001 "cinematic realistic" | 全局默认：媒介（2D anime/3D/realistic/cel shading…）、全局色板、渲染特征、全局反向提示。Composer 默认注入每一次组装 |
| **角色/地点级风格注记** | 各 profile 的 `visual_style` 可选字段 | 个别对象的风格偏移，通常留空 |
| **镜头级视觉指令** | `Shot.visual_description`（既有字段，0 改）+ `CameraPlan` + `composition` | 本镜头增量：如"黑白回忆画面""雨夜霓虹"。Composer 拼在 shot_instructions 段 |

```python
@dataclass
class StyleProfile:
    medium: str = ""        # 媒介：2D anime / 3D / realistic / cel shading（必填）
    palette: str = ""       # 全局色彩策略（必填）
    rendering: str = ""     # 渲染/线条/上色特征（可选）
    lighting_style: str = ""# 光感倾向（可选）
    camera_style: str = ""  # 镜头语言倾向（可选）
    negative_cues: str = "" # 全局反向提示（可选）
    notes: str = ""         # 其他（可选）
```

**为什么不给 ShotAsset 加 kind="style"（重要决策记录）**：
镜头级换风格（回忆线/风格切换）是真实需求，但 (a) 当前单项目单默认风格的现实下，
镜头级差异走 `Shot.visual_description` 已足够表达；(b) `stage8` 测试对 `ASSET_KINDS`
做精确集合断言（§2.5），加值必破封存测试，不满足"真实必要"门槛；(c) Composer 的
`style_override` 参数已为未来多风格项目留位（传 asset_id 即可，无需改 Shot）。
未来 StoryboardAgent 若需要系统化多风格，再单独立项扩展词表（届时同步更新
stage8 断言并说明理由）。

项目默认风格解析规则（确定性，不靠 LLM）：registry 显式指定 > 唯一 approved 的
STYLE 资产自动生效 > 无风格资产且未指定 → Composer 明确报错（不静默裸奔）。

---

## 8. Reference Asset（统一视觉参考概念）

**参考图的第一归属是"资产的某个版本"**（`VisualAssetVersion.reference_images`）：
换参考图 = 新版本。在此之上，把"不属于任何 character/location/prop/style 的
独立参考"统一为第五类资产：

```text
REF_001 "mercy-office-pose"（purpose="pose"）      → 姿态参考束
REF_002 "ep1-storyboard-keyframes"（purpose="storyboard"）→ 分镜关键帧参考束
```

- Reference Asset = name + purpose（pose | storyboard | other）+ images + note，
  **没有 profile 结构**（它本身就是参考的集合）。
- 未来预留 purpose：`video | audio`（只登记词表注释，**当前不实现**，指令 §十二）。
- Character/Location/Prop/Style Reference 不是独立资产，而是对应资产版本上的
  `ReferenceImage(kind=…)`。
- 所有参考图统一走 `ReferenceImage`（image_id / path / kind / note / checksum），
  落在项目 `visual_assets/images/` 下；checksum（sha256）保证"同一张图"可判定，
  是未来 VLM QA 比对的锚点。

---

## 9. Identity / Version（身份与版本严格分离）

| 概念 | 例 | 语义 | 规则 |
|---|---|---|---|
| Asset Identity | `CHAR_001` | 稳定身份 | 一经分配**永不变更**；与 `(kind, name)` 永久绑定；名字是全系统不可变身份（Casting/AssetRegistry 已保证唯一） |
| Version | `CHAR_001:v1` / `:v2` | 视觉版本 | 单调递增；**绝不复用号、绝不覆盖内容**；每次新参考图/新造型/审核编辑（APPROVED_WITH_EDIT）都产生新版本 |
| Full Reference | `CHAR_001:v2` | 精确引用 | VLM QA / 审核记录 / 未来 image 产物溯源用 |

- 换视觉参考图 → `CHAR_001` 不变，新增 `v2`；`v1` 若已 approved 则转 **superseded**
  （内容与审核记录原样保留，绝不删除）。
- ID 生成：registry 内按 kind 前缀计数（`CHAR_%03d`，超过 999 自然增长
  `CHAR_1000`；格式校验 `^(CHAR|LOC|PROP|STYLE|REF)_\d{3,}$`，前缀必须匹配 kind）。
- 版本格式 `^v\d+$`；`current_version` 指向"最新 approved"，无 approved 时指向
  最新 draft（并明确标注未生效）。
- 同理适用于 LOC/PROP/STYLE/REF。

---

## 10. Asset Lifecycle（与 Memory/Review 的关系：不建第三套状态机）

### 10.1 版本状态词表

```text
draft       草稿（文本规划或 Vision 提取产出的初始结构化档案）
generated   （预留）资产本体是生成媒体时进入：未来 image 生成角色定妆图等；
            Stage 10 内通常不经过此态（Stage 10 的产出是结构化 profile，不是图）
reviewing   已提交人工审核（对应 ReviewStatus.PENDING）
approved    人工审核通过，成为生效视觉身份
rejected    人工拒绝（保留记录，不生效）
superseded  曾生效，被更新的 approved 版本取代（内容保留）
archived    人工显式归档退役（资产级操作，作用于全部版本）
```

结论：七态词表**适合**当前系统，但**状态机只有一套驱动源**：

### 10.2 与既有两套机制的关系（指令 §十四/§二十五的答案）

```text
Memory Approval（Stage 4）  管"剧情/世界观事实"（facts 三态）——视觉档案不进 Memory
Review Decision（Stage 4）  管"Step 产物的采用与否"（APPROVED / APPROVED_WITH_EDIT
                            / REJECTED）——视觉档案草稿就是一种 Step 产物
Asset Lifecycle（Stage 10） 不是新的"人审状态机"，而是对 Review 结果的**确定性派生**
                            + 版本指针算术
```

映射表（registry 内确定性执行，无 LLM 参与）：

| 事件 | 既有机制 | Registry 派生动作 |
|---|---|---|
| Agent 产出草稿 | Workflow 整步重试 + Artifact 版本化 | 新版本 status=draft |
| 提交审核 | `ReviewService.submit_for_review` | draft → reviewing |
| 人工通过 | `ReviewDecision.APPROVED` | reviewing → approved；`current_version` 指针更新；旧 approved 版本 → superseded |
| 人工改后通过 | `ReviewDecision.APPROVED_WITH_EDIT` | 编辑内容落成**新版本**（Artifact 版本化铁律：原文件不动）→ approved |
| 人工拒绝 | `ReviewDecision.REJECTED` | reviewing → rejected（保留，可再出新版本重审） |
| 人工退役 | Registry 显式 API `archive()` | 全部版本 → archived |

**不实现**：独立的 Asset 审核服务、第二套 review 记录格式、candidate/approved
双写——Review/Artifact 全部复用 Stage 3/4 既有件。

未来 VLM QA（§18）是另一条链：Generated Image（媒体 artifact）→ VLM QA 结论 →
作为 Review 输入。对象不同（生成媒体 vs 档案版本）、共用 Review 机制、
**Stage 10 不实现 VLM QA Agent**。

---

## 11. Shot ↔ Asset（稳定引用，0 改 Schema）

目标关系（指令 §十五）：

```text
SHOT_001
├── CHAR_001（= Shot.characters 里的 "Mercy" → registry 解析）
├── CHAR_002（= "Caleb"）
├── LOC_003 （= Shot.location / Storyboard.location → registry 解析）
├── PROP_005（= Shot.assets 里 kind="prop" 的 ref → registry 解析）
├── STYLE_001（= 项目默认风格 / Composer style_override）
└── REF_xxx（= Shot.assets 里 kind="reference" 的 ref）
```

实现方式：**引用解析层，而非 Schema 改造**。

- Shot 保持既有表达（`characters`/`location`/`assets[].ref` = 语义名）——
  V0.1 往返、Stage 8 序列化、adapter 全部 0 改动。
- `VisualAssetRegistry` 建立 `(kind, name) → asset_id` 的**永久绑定**（名字在全
  系统内唯一且不可变，§9），首次建档即固化。
- `VisualComposer`（§15）在**组装时**把语义名解析成 asset_id + 生效版本 +
  profile——解析失败（无此资产/无 approved 版本）**明确报错并列出缺口**，
  绝不静默用残缺视觉生成。
- Shot 只保留：资产引用（名字级，既有）+ 本镜头视觉增量（`visual_description` /
  `camera` / `composition` / `continuity`，既有字段）。**不复制**任何完整
  Character/Location 视觉描述进 Shot。

为什么不用 asset_id 直接进 ShotAsset.ref：(a) V0.1 派生的 Shot 只有名字
（adapter 不能编造 asset_id）；(b) 名字→ID 的绑定一经固化即稳定，满足"稳定引用"；
(c) 免去 Shot Schema 与 stage8 测试的任何改动。

---

## 12. Asset ↔ Memory（Registry + Memory 协同，不建向量库）

- **VisualAssetRegistry = 视觉身份唯一事实源**（身份/版本/审批/参考图/checksum）。
- **MemoryStore 继续管剧情语义事实**（facts 三态 / characters / world / history）。
  视觉档案**不写进 Memory**——避免双写与口径分裂。
- 协同方式：两者都是项目级存储（`v2/projects/<pid>/` 下并列目录），都可在未来
  成为 ContextAssembler 的 provider；Stage 10 的 Agent 输入走 Stage 7 的
  `scene_spec` 模式（调度层把 `visual_asset_spec` 放进 `WorkflowContext.inputs`，
  不新增 ContextAssembler provider，不加 needs key——够用且零改动）。
- Embedding / 语义检索（Stage 9 预留 `EmbeddingProvider`）未来可对 profile 建
  索引——**Stage 10 不做**（指令 §二十四：不搭向量数据库）。
- Casting 产物（角色语义档案）是 VisualAssetAgent 的输入之一：新角色 → 顺手可
  建视觉草稿（同一 wiring，非自动强绑）。

---

## 13. Asset ↔ Vision（Reference → Structured Profile 的管线）

Stage 9 已给 `VisionProvider.understand(image, prompt)` 与
`MultimodalProvider.chat(messages, images)`——Stage 10 **不新做 Provider 抽象**，
只定义消费方式：

```text
ReferenceImage（项目内路径 + checksum）
+ 资产语义数据（name / kind / Casting 档案一句话摘要）
        ↓  VisualAssetAgent（extract 模式）
VisionProvider.understand(image=路径引用, prompt=结构化提取契约)
        ↓  原始 JSON 文本（prompt 里明确字段契约）
Agent._parse_json + 确定性规范化（空串兜底/裁剪/枚举校验）
        ↓
VisualAssetValidator（§新增校验器）
        ↓  StepOutput → Artifact 版本化落盘
ReviewService（人工三态）
        ↓  APPROVED
VisualAssetRegistry 写回：新版本 approved（旧版本 superseded）
```

要点：
- 提取 prompt 由 GLM-5.0（当前文本通道，§19）在 10-B 设计定稿，写进
  `v2/prompts/v0.2/visual_asset_extract.txt`；JSON 契约与 profile 字段一一对应。
- 解析失败/校验失败 → `StepError` → Workflow 整步重试（既有机制，0 新代码路径）。
- Provider 只负责调用与瞬时错误重试（Stage 9 铁律）；业务合法性仍归 Validator。
- **当前 Zode 接入是文本通道，不是 VisionProvider**（§19/§20）——真实视觉调用
  只能来自真实配置的视觉 Provider（§25），不假定 GLM-5.0 能看图。

---

## 14. Asset ↔ Multimodal

- `MultimodalProvider.chat(messages, images)` 服务**多图 + 图文混合**场景：
  未来 VLM QA（生成图 vs 多张参考图对比）、跨图一致性检查、分镜关键帧评估。
- Stage 10 对 Multimodal 的全部工作 = **保持接口不动 + Mock 测试覆盖
  "多参考图 + 语义数据 → 结构化对比结论"的输入形状**（用 MockMultimodalProvider，
  0 成本），为未来 QA Agent 留好输入设计，不实现 QA Agent 本体。
- 真实 Multimodal 实现类与真实 Vision 实现类（§22）同协议族
  （OpenAI 兼容图文 chat），10-B 若实现 Vision 实现类则 Multimodal 可零成本
  派生/共用（实现细节 B 定，接口 0 改）。

---

## 15. Token / Context 控制（Composer 是核心）

错误做法（指令 §十六）与正确做法对照：

```text
❌ Shot 001 → 完整人物描述；Shot 002 → 再复制一遍；Shot 003 → 又一遍……
✅ Character Asset → approved profile（registry 存一份）
   Location Asset → approved profile（registry 存一份）
   Shot → 只存名字引用 + 本镜头增量（现状已是如此，0 改）
   调用模型之前 → VisualComposer 动态组装一次性展开
```

`VisualComposer`（`core/visual_compose.py`）：

- 输入：`Shot` + `VisualAssetRegistry`（+ 可选 `style_override`、`extra_refs`）。
- 解析：characters → 各自生效 profile；location → 生效 profile；props → 生效
  profile；style → 项目默认/覆盖。
- 输出 `ComposedVisualInput`（结构化 dict + `render_prompt()` 文本两形态）：

```text
ComposedVisualInput
├── shot_ref            "S{scene}_B{beat}_SH{shot}" 稳定镜头引用
├── subject             shot.subject
├── character_sections  [{asset_id, version, name, 紧凑 profile 文本}] ×N
├── location_section    {asset_id, version, name, 紧凑 profile 文本} | None
├── prop_sections       [...] ×N
├── style_section       {asset_id, version, 紧凑 profile 文本}
├── shot_instructions   visual_description + camera(受控词) + composition + continuity
├── negative_cues       各 profile negative 汇总去重
├── reference_images    全部相关 ReferenceImage（路径 + checksum）
├── camera              CameraPlan 序列化（video 链用）
└── duration            shot.duration（video 链用）
```

- 紧凑序列化：profile 渲染为定长顺序的 `key: value` 行（非空才输出），
  一个角色约 100~200 token——不是 JSON dump（省掉引号/括号/重复键名）。
- 缺口策略：参与画面的角色/地点无 approved 视觉档案 → **明确报错列出缺口**
  （默认 `allow_partial=False`）；这是"视觉一致性优先"的正确默认，宁停不错。
- **硬验收**（C 会话断言）：给项目注册任意视觉资产前后，同一 Storyboard 的
  `to_dict()` 序列化**逐字节不变**——证明视觉信息零渗漏进 Shot/Storyboard，
  Token 增长只发生在"真正调用模型的那一刻"。

---

## 16. Asset ↔ Image Generation（未来 Stage 13 的接口契约）

Stage 10 不实现图片生成，但保证未来：

```text
Character + Location + Prop + Style Asset（approved 版本）
+ Shot / Storyboard
        ↓ VisualComposer（Stage 10 已实现）
ComposedVisualInput（prompt 文本 + reference_images + negative_cues + size 建议）
        ↓（Stage 13）
ImageGenerationProvider.generate_image(prompt, size, reference)
```

- Stage 9 预留接口签名 `generate_image(prompt, *, size=None, reference=None)`：
  `prompt` ← `render_prompt()`；`reference` ← 参考图束引用（路径/checksum 清单
  组合成的稳定引用串）。**签名 0 改**。
- 产出落 `ShotAsset(kind="image")`（Stage 8 已能表达）；生成结果作为新版本/
  媒体 artifact 进审核——Stage 10 的 registry/artifact 机制已备好挂点。
- **Stage 13 不应回来改 Asset 架构**——这是 Stage 10 的验收标准之一（§30）。

---

## 17. Asset ↔ Video Generation（未来接口预留）

```text
Shot + Character Reference + Location Reference + Storyboard Keyframe
+ CameraPlan（shot_size/angle/movement）+ Motion（camera_movement / Move 走位）
+ Style + duration
        ↓ VisualComposer（同一出口，camera/duration 段专为 video 保留）
ComposedVisualInput
        ↓（未来）
VideoGenerationProvider.generate_video(prompt, shots=None, duration=None)
```

- 未来可接 Seedance 或其他 Video Provider——**都只是 Config 档**，Stage 10/13/14
  任何代码不绑定具体模型名（与 §20/§21 同一原则）。
- `ComposedVisualInput` 的 `camera` / `duration` / `reference_images`（含
  storyboard keyframe 参考束 REF_xxx）就是为该链预留的输入位，**本阶段只定义
  形状 + Mock 测试，不实现生成**。

---

## 18. Asset ↔ VLM QA（Ground Truth Reference 角色）

未来视觉审核要做的对比（指令 §二十三）：

```text
Generated Image vs Character Reference（这是 Mercy 吗？发型/服装一致吗？）
Generated Image vs Location Reference（这是客厅吗？吧台/光照一致吗？）
Generated Image vs Shot Specification（景别/构图/镜头指令达成了吗？）
```

Stage 10 的责任 = 让 Visual Asset 能承担 **Ground Truth Reference**：

1. **身份稳定**：CHAR_001 永不变 → QA 结论可跨集累积到同一身份上；
2. **版本精确**：QA 记录引用 `CHAR_001:v2`（full reference）→ 事后可追"当时
   拿哪版比对的"；
3. **图像可锚定**：ReferenceImage.checksum → "生成图 vs 那张参考图"可精确复现；
4. **判据结构化**：approved profile 的受控字段 → QA 输出可按字段逐项裁决
   （hair ✅ / clothing ❌），而不是一句模糊的"像/不像"。

**Stage 10 不实现 VLM QA Agent**（指令 §二十五）；上述四点全部由 §3~§15 的
Schema 与 registry 设计天然提供。

---

## 19. GLM-5.0 当前角色（文本侧）

当前文本模型 = GLM-5.0（接入方式 Zode）。它在 Stage 10 承担**全部文本侧工作**：

```text
✅ Visual Asset Prompt Planning   提取/规划两个 prompt 模板的设计定稿
✅ Asset Metadata Normalization   规范化规则设计（字段裁剪/空值兜底/枚举映射）
✅ 结构化文本转换                  profile 字段契约与 JSON 输出格式设计
✅ Asset 描述整理                  测试种子数据（mock 脚本用的 profile 样例）
✅ Storyboard / Shot 文本规划      （未来 StoryboardAgent 时代继续）
```

边界（指令 §十八）：
- 这些工作通过 **TextProvider 抽象**出现在管线里（VisualAssetAgent 的 plan 模式）；
  运行时具体是哪家哪个模型由 Config 决定。**代码与新架构里不出现 GLM-5.0 /
  Zode / DeepSeek 字样**——它们是"当前配置"，不是架构事实。
- **不假定 GLM-5.0 在当前配置下是 Vision Provider**。当前 Zode 接入是文本通道
  （本会话即证明：它以文本方式工作）；真实视觉调用只能走真实配置的
  VisionProvider（§25），必须实测确认，不得写 `VisionProvider → GLM-5.0`。
- 10-B 的真实样例里，文本规范化用**确定性代码**完成（解析/裁剪/校验），
  **0 次真实文本 API 调用**——设计上就不让文本侧消耗预算。

---

## 20. Zode 当前 Provider 位置

```text
FilmAgent Agent → Router → Provider → Model      （架构，模型无关）
TextProvider → 当前配置 → GLM-5.0                （运行时事实，Zode 是接入通道）
```

- Zode = **环境级文本接入通道**（当前驱动开发会话的连接方式），**不是**代码内
  Provider 档：`v2/config.py` 的 `PROVIDERS` 不新增 zode 档、不写 GLM 模型名。
- 管线内的文本档仍是既有四档（mock 缺省 / deepseek / bailian / local），
  换厂商 = 换 Config，业务代码 0 感知（Stage 9 原则延续）。
- 未来若 GLM 系列要走管线内 API（文本或视觉），与其他厂商同权：加一个
  OpenAI 兼容 Config 档即可，**不需要任何架构改动**——这正是本架构的验收点。

---

## 21. 百炼未来接入位置（候选，不绑定）

- 百炼 = 当前**视觉模态的第一候选** Config 槽位：`PROVIDERS["bailian_vision"]`
  已有（default_model 空 = 配置护栏，真实调用前人工填，如 qwen-vl 系列具体型号
  由用户定，不硬编码）。
- 10-B 的真实视觉实现类走 **OpenAI 兼容协议**（§22）——百炼兼容模式、其他云
  厂商、本地视觉模型（vLLM/Ollama 兼容端点）**同一实现类的不同 Config 档**
  （Stage 9"协议变才新类"原则）。
- 定位重申（PROJECT_STATUS §5.5）：**百炼是主要候选，但 FilmAgent 核心架构
  不得绑定百炼**。Visual Asset 系统对供应商零依赖。

---

## 22. 新增文件（10-B）

| 文件 | 内容 |
|---|---|
| `v2/core/visual.py` | Visual Asset 数据模型：4 个 VisualProfile + VisualAsset / VisualAssetVersion / ReferenceImage + 受控词表（VISUAL_ASSET_KINDS / 版本状态 / 参考图 kind / REF purpose）+ ID·版本格式工具；纯结构 + to_dict/from_dict，不调 LLM 不读写磁盘 |
| `v2/core/visual_registry.py` | `VisualAssetRegistry`：身份分配（kind 前缀计数）、`(kind,name)` 绑定、版本管理（单调递增/永不覆盖）、生命周期派生（消费 Review 结果的映射表 §10.2）、`archive()`、持久化 `v2/projects/<pid>/visual_assets/registry.json`、参考图登记（checksum 计算）、`active_profile()` 查询 |
| `v2/core/visual_compose.py` | `VisualComposer` + `ComposedVisualInput`：Shot → 组装输入（§15）；紧凑序列化；缺口明确报错；`render_prompt()` |
| `v2/llm/providers/openai_compatible_vision.py` | 真实视觉 Provider：OpenAI 兼容图文协议（image 以 content parts 传入）；复用 LLMClient 的重试/错误翻译/TokenLogger（组合或继承，B 定）；transport 可注入（0 网络单测）；厂商=Config 档 |
| `v2/agents/visual_asset.py` | `VisualAssetAgent`（继承 LLMAgent；持有 TextProvider + 可选 VisionProvider；plan / extract 两模式）+ `VisualAssetStep`（继承 LLMStep；needs 走 inputs["visual_asset_spec"]，Stage 7 scene_spec 模式） |
| `v2/prompts/v0.2/visual_asset_plan.txt` | plan 模式 prompt（语义数据 → 结构化 profile JSON 契约） |
| `v2/prompts/v0.2/visual_asset_extract.txt` | extract 模式 prompt（参考图 + 语义锚点 → 结构化 profile JSON 契约） |
| `v2/logs/stage10_visual_asset/run_tests.py` | mock 测试套件（0 API 0 成本；结构沿用 check() 风格 + 内嵌历史回归子进程） |
| `v2/logs/stage10_visual_asset/run_real_visual_extraction.py` | 真实视觉冒烟脚本（独立、人工批准才跑、≤3 次调用、记 TokenLogger、输出成本报告） |

## 23. 修改文件（10-B，全部最小增量）

| 文件 | 改动 | 兼容性 |
|---|---|---|
| `v2/core/validator.py` | **纯追加** `VisualAssetValidator`（ID/版本格式、kind 词表、per-kind 必填字段、状态转移合法性、参考图 checksum/path、(kind,name) 唯一） | 复用 ValidationError；既有校验器 0 改 |
| `v2/llm/providers/registry.py` | vision/multimodal 的**真实 vendor** 分支：按 `<vendor>_vision` Config 档造 OpenAICompatibleVisionProvider（default_model 空 → 明确报错护栏）；mock 分支不变；GENERATION_KINDS 仍拒绝 | 既有行为不变（现在真实 vision 本来就报错） |
| `v2/llm/providers/__init__.py` | 导出新 Provider 类 | 纯追加 |
| `v2/core/__init__.py` | 导出 visual 三模块 | 纯追加（stage8/9 导出原样） |
| `v2/llm/__init__.py` | 导出新类（如需） | 纯追加 |
| `v2/config.py` | **预期 0 改**（bailian_vision 槽位已存在）；仅当需要登记其他厂商视觉档时加一档 | 不动既有档 |

## 24. 禁止修改文件（10-B/C/D 硬清单）

```text
FilmAgent/ 全目录（V0.1 冻结；git diff -- FilmAgent/ 必须为空）
v2/core/shot.py        （ASSET_KINDS 不加 style！Shot/Beat/Storyboard/ShotAsset 0 改）
v2/core/adapter.py     （V0.1↔Canonical 无损转换 0 改）
v2/core/models.py      （Character 等语义模型 0 改）
v2/core/assets.py      （Stage 1 目录资产注册表 0 改）
v2/agents/base.py      （LLMAgent/LLMStep 骨架 0 改）
v2/agents/casting.py / scene_generation.py   （Stage 6/7 封存行为）
v2/llm/client.py / router.py                （Stage 2/9 封存；真实脚本直接用工厂，不经 Router）
v2/llm/providers/base.py / mock.py / openai_compatible.py / bailian.py  （接口族与既有实现 0 改）
v2/memory/ v2/review/ v2/workflow/ v2/context/   （全复用，0 改）
全部历史测试套件（stage1/3/4/6/7/8/9 的断言一个不动）
```

---

## 25. Stage 10-B 真实视觉 API 最小测试（A 会话定稿的样例设计）

**前置条件（人工）**：
1. 提供参考图 1~3 张：优先语义合适的（人物肖像 2 张 + 场景照 1 张），放入
   `v2/projects/<pid>/visual_assets/images/`；若无，可用仓库内 `pics/*.png`
   仅做**管线机械验证**（报告中明示"语义无效，仅验证通路"）。
2. `config.PROVIDERS["bailian_vision"]["default_model"]` 填入真实 VLM 模型名
   （用户定，不硬编码）+ 环境变量 `BAILIAN_API_KEY`。
3. 人工明确批准执行真实脚本（计费）。

**样例设计（恰好 3 次真实视觉调用，重试也计入预算）**：

| # | 输入 | 验证点 | 消耗 |
|---|---|---|---|
| 1 | 人物参考图 A + Mercy 语义档案 → extract | 图片 → Vision → 结构化 CharacterVisualProfile → Validator → Review APPROVED → CHAR_001:v1 生效 | 1 次 |
| 2 | 场景参考图 + 地点名 → extract | Location 链路同上 → LOC_001:v1 生效 | 1 次 |
| 3 | 人物参考图 B（同角色不同造型）→ extract | **版本机制**：CHAR_001 不变、产生 v2、v1 转 superseded、current_version=v2 | 1 次 |

- 样例 1 必做；2/3 视图片可用性可裁剪至 1 个（管线验证已成立）。
- 全程文本 API **0 次**（规范化为确定性代码）；Mock 测试 0 成本。
- 产出沉淀：3 份真实提取的 profile JSON 作为 fixture 存入测试目录，供 C/D 回归
  与未来对比（不含 key、不含图片本体）。
- 若当时无可用真实视觉 Provider（未配 key/模型）：**跳过真实样例并明确报告
  "阻塞：需人工配置"**，Mock 路径照常交付——绝不伪造真实结果。

## 26. Stage 10-C 测试 / Debug 重点

1. **VisualAssetValidator** 全错误码覆盖（INVALID_KIND / INVALID_ASSET_ID /
   INVALID_VERSION / DUPLICATE_NAME / MISSING_FIELD per-kind / INVALID_STATUS /
   非法状态转移 / 参考图 path·checksum 校验）。
2. **身份/版本不可变**：换参考图 → v2；asset_id 与 (kind,name) 绑定不变；
   v1 superseded 且内容可读；版本号永不复用。
3. **生命周期 = Review 派生**（§10.2 映射表逐行断言；确认没有第三套状态机、
   candidate/approved 双写不存在）。
4. **Shot↔Asset 组装**：Composer 正确解析 character/location/prop/style/reference；
   缺 approved 档案 → 明确报错列缺口；`allow_partial` 开关行为正确。
5. **Token 控制硬证据**：注册视觉资产前后 Storyboard `to_dict()` 逐字节不变；
   Shot artifact 中无 profile 文本；紧凑序列化长度合理（字符数断言）。
6. **向后兼容**：V0.1↔Canonical 往返回归（adapter 0 改的直接证明）；
   ASSET_KINDS 未变（stage8 精确断言原样通过）。
7. **Provider 层**：真实 vision 类的 transport 注入单测（0 网络）；工厂对
   default_model 空档报护栏错；mock 四件套行为不变。
8. **Agent E2E（mock）**：plan/extract 两模式 → StepOutput → Artifact 版本化 →
   Review APPROVED → registry 生效全链；APPROVED_WITH_EDIT 产生新版本。
9. **依赖方向/静态检查**：core/visual* 无上层 import；compileall + import 冒烟 +
   密钥扫描。
10. **全量历史回归**：stage1/3/4/6/7/8/9 + V0.1 冻结断言（tag 指向、FilmAgent/ 0 diff）。
11. （若有）真实样例复核：3 次 JSONL 账目、成本合计、fixture 落盘。

## 27. Stage 10-D 文档 / Git

- 更新：`PROJECT_STATUS.md`（阶段表 + §5 架构 + Provider 能力矩阵）、
  `HANDOFF.md`、`CLAUDE.md`（进度行、测试命令、架构表加 core/visual*）、
  `v2/README.md`（必要处）。
- 生成 `v2/logs/stage10_visual_asset/stage10_report.md`（综合 A/B/C/D）。
- Git 验证：`FilmAgent/` 0 diff、四 tag 未移动、working tree clean → commit →
  打 `v0.2-stage10` tag（**等人工验收后**，D 会话不自动 tag）。

---

## 28. 成本控制

```text
10-A：真实 API 0 次，¥0.00（本次会话已达成，仅读代码与写文档）
10-B：Mock 测试 0 成本；真实视觉调用 ≤3 次（人工批准 + 独立脚本 + TokenLogger
      记账 + 报告实际成本）；真实文本调用 0 次（确定性规范化）
10-C：0 真实调用（只跑 mock 回归）
10-D：0 真实调用
全程上限：3 次真实视觉调用；无本地模型下载；无图片/视频生成计费
```

## 29. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| Visual Asset 字段过度设计（字段爆炸） | 中 | §4~§8 最小字段原则：每字段必须服务一致性/prompt 组装/VLM QA 之一；全部可选字段空串兜底 |
| 生命周期与 Memory/Review 重复造状态机 | 高 | §10.2 映射表：Registry 只做确定性派生；review/memory 0 改动；C 断言无双写 |
| 真实视觉 Provider 当时不可用（无 key/模型） | 中 | §25：明确报告阻塞，Mock 路径完整交付，不伪造 |
| 真实 VLM 输出不符合 JSON 契约（解析失败） | 中 | prompt 契约明确 + `_parse_json` 围栏容错 + Workflow 整步重试；重试计入 3 次预算上限 |
| 视觉档案被 LLM 顺手改写导致身份漂移 | 中 | 版本化 + 审批生效 + superseded 留痕；approved 版本只读 |
| 名字→资产解析歧义（重名） | 低 | (kind,name) 注册唯一 + 系统级名字唯一既有保证；C 测试重名报错 |
| Composer 组装遗漏/错配资产（如镜头角色缺档案静默通过） | 中 | 默认 `allow_partial=False` 缺口明确报错；C 专项断言 |
| Token 失控（profile 渗入 context/artifact） | 中 | §15 硬验收：Storyboard 序列化前后逐字节不变断言 |
| 破坏 Stage 8 封存（词表/Schema/adapter） | 高 | §24 禁改清单 + §2.5 精确断言核实；shot/adapter/models 0 改 |
| API key / 新厂商密钥进 git | 高 | 环境变量 + .gitignore + mask_api_key + 静态扫描（既有铁律延续） |

## 30. 验收标准

```text
1. Stage 10 新套件全绿（预计 25±5 项），历史回归全绿：
   stage1=19/19, 3=38/38, 4=25/25, 6=24/24, 7=29/29, 8=27/27, 9=26/26
2. V0.1 冻结：git diff -- FilmAgent/ 为空；v0.1/v0.2-stage7/8/9 四 tag 未移动
3. 零侵入证明：shot.py / adapter.py / models.py / agents / memory / review /
   workflow / context 全部 0 diff（git 核实）
4. 五类资产（character/location/prop/style/reference）可注册、版本化、
   经 Review 批准、被 Composer 消费
5. 身份不可变：样例 3 证明 CHAR_001 不变 + v2 + v1 superseded
6. Token 硬证据：注册视觉资产前后 Storyboard 序列化逐字节不变
7. 真实 API ≤3 次视觉 + 0 次文本；成本报告实际值；无配置时明确阻塞报告
8. 静态检查全过（compileall / import / 依赖方向 / 密钥扫描）
9. 未来接口就位：ComposedVisualInput 的字段能一一映射到
   ImageGenerationProvider / VideoGenerationProvider 预留签名（§16/§17 对照表）
```

---

## 附：10-A 预留给 B/C/D 的落地清单（指令 §二十九）

### B 会话（实现 + 基础测试 + 真实样例）
- 新增：§22 九个文件；修改：§23 五处最小增量；禁止：§24 清单。
- Provider 调用：测试全部 Mock 四件套；真实仅 §25 三次视觉（人工批准）。
- 真实 API 需要：是（≤3 次视觉；前提=key/模型/图片/人工批准四项齐备）。
- 顺序建议：core/visual.py → validator → registry → composer →（单测绿）→
  Provider 真实类 → Agent/Step/prompts → 套件 → 回归 → 真实脚本 → 报告。

### C 会话（测试 / Debug）
- §26 十一项重点；修复遵循最小修改；每修一处复跑相关套件，最后全量回归一次
  （不逐字段重复跑全史）。

### D 会话（文档 / 交付）
- §27 清单；stage10_report.md 用 §三十一模板汇总；commit + tag 等人工验收。
