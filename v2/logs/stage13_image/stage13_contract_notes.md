# Stage 13 契约笔记（A / A2 会话产出）

> 角色：A（分析 / 架构）+ A2（厂商协议专项）
> 产出时间：2026-09-15
> 说明：本文件是**分析产物**，不含实现代码；实现见 `v2/llm/providers/mock_image.py`、
> `v2/llm/providers/openai_compatible_image.py`、`v2/orchestration/image_stage.py`。
> 所有结论均以**实读代码**为依据（附文件:行号），不以文档转述为准。

---

## 一、接入点契约（S1）

### 1.1 Provider 接口族（`v2/llm/providers/base.py`，★禁改件，签名 0 改）

```python
class ImageGenerationProvider(Provider):        # base.py:233
    kind = "image"
    def generate_image(self, prompt: str, *, size: Optional[str] = None,
                       reference: Optional[str] = None) -> ProviderResult:
        raise NotImplementedError(...)          # base.py:244-248
```

实读结论：

| 事实 | 位置 | 对 Stage 13 的约束 |
|---|---|---|
| 签名是 `(prompt, *, size=None, reference=None)`，返回 `ProviderResult` | base.py:244 | 实现子类即可；**不得改接口** |
| `ProviderResult` 只有 `text/model/usage/request_id/duration_ms/retry_count` | base.py:93-107 | 图像**没有二进制字段** → 图片必须以 `text` 携带"引用串" |
| 基类 docstring 明确"结果返回一个**产物引用**（落盘路径 / asset ref），不要在这里决定文件命名与版本" | base.py:236-240 | 落盘/命名/版本归 **Artifact 层**（本阶段落在编排层） |
| `GENERATION_KINDS = ("image", "video", "audio")` | base.py:275 | 本阶段只开 `image`，video/audio 保持拒绝 |
| `Provider.kind/vendor/model` 是只读标签；`logger` 可选 | base.py:43-48 | `kind="image"`、`vendor` 由 Config 档决定 |

**由此确定的引用串三形态契约**（本阶段新增，写进两个 Provider 的模块文档）：

```text
data:image/<fmt>;base64,<...>   内嵌图片（Mock 与"后端返回 b64_json"走这条）
http(s)://...                   远端 URL（需下载为二进制）
<本地路径>                       已是本地文件
```

理由：接口签名 0 改的前提下，`text` 是唯一可用通道；且"谁落盘"必须留给上层。

### 1.2 registry 护栏（`v2/llm/providers/registry.py`，§8.2 允许修改）

```python
if kind in GENERATION_KINDS:                     # registry.py:69-73（改前）
    raise ValueError("...生成类 Provider（image/video/audio）是 Stage 9 预留接口...")
```

实读结论与接入点：

- `create_provider(kind, vendor)` 对 image/video/audio 一律 raise → 本阶段把 `kind == "image"`
  提前分流到新函数 `_create_image_provider`，**video/audio 走原分支保持拒绝**。
- **缺省 vendor 不回落 mock**：`create_provider("vision")`（vendor=None）会落到
  `_create_real_image_provider(None)` 并 raise（registry.py:139-141）。为保持同一范式，
  `create_provider("image")`（vendor=None）也必须 raise —— 这条恰好让 stage9 的既有断言
  `lambda: create_provider("image")`（stage9 run_tests.py:343，要求抛 ValueError/KeyError）**继续成立**。
- 缺省档入口是 `get_default_provider(kind)`：原实现 `vendor = env or None`，
  对 text 有效（内部再取一次默认），对 image 会 raise。**改法**：环境开关未设时显式回落
  `vendor="mock"`（registry.py:get_default_provider）。对 text/vision/multimodal 结果不变。
- ⚠️ **命名陷阱（实读发现）**：原 `_image_kinds()` 返回的是 **vision** 档 vendor
  （`name.endswith("_vision")`，registry.py:121-123），与新增的"文生图 image 模态"不是一回事。
  本阶段改名为 `_vision_vendors()`，新增 `_image_vendors()`（行为 0 变，仅消歧义）。

### 1.3 预算机制（`v2/llm/request_counter.py`，§8.2 允许修改）

实读结论：

| 事实 | 位置 | 结论 |
|---|---|---|
| 唯一"真实请求计数点"= 真实 Provider 的传输出口 `_invoke` | 模块 docstring §1 | 图像 Provider 必须把计数点放在每个 HTTP attempt 之前 |
| `consume_real_request(kind, ...)` 原子完成"检查预算→计数→审计" | request_counter.py:97-130 | 复用；新增 `image` 桶 |
| `count_mock_request` **不检查预算**（只计数） | request_counter.py:133-156 | ★Mock 路径的预算护栏**必须由编排层自己执行** |
| `DEFAULT_REAL_BUDGETS` 按 kind 分桶；None = 不限 | request_counter.py:47-49 | 追加 `"image": MAX_REAL_IMAGE_REQUESTS` |
| `snapshot()` 给出 `real/mock/logical/retries/budget_rejections/by_kind` | request_counter.py:195-210 | 直接作为测试与报告的**程序化账目** |

**因此设计为两道防线**：

1. 编排层 run 级上限 `cap = min(run_spec["max_images"], MAX_REAL_IMAGE_REQUESTS)`，
   达到即拒绝（`APIBudgetExceededError`），**绝不发出第 N+1 次**（§7.3-2 / AC6）；
2. 同一 `cap` 通过新增的 `APIRequestCounter.set_budget("image", cap)` 下推到真实 HTTP 层
   （纵深防御：业务层漏判时传输层仍会拒）。

### 1.4 VisualComposer / 视觉注册表（`v2/core/`，★禁改件）

```python
VisualComposer.compose(shot, *, style_override="", extra_refs=None, allow_partial=False)
    -> ComposedVisualInput          # visual_compose.py:130
ComposedVisualInput.render_prompt()  -> str       # visual_compose.py:70   ← 出图 prompt 的唯一来源
ComposedVisualInput.reference_images: List[{"image_id","path","checksum","kind"}]
ComposedVisualInput.negative_cues / camera / duration
```

实读结论：

- `compose()` 默认 `allow_partial=False`：角色/地点/道具缺 approved 档案 → `VisualComposeError`
  且 message 自带缺口清单（visual_compose.py:218-221）→ 满足 §7.4"绝不没档案也硬出图"。
- ★**风格是硬前置**：`_resolve_style()`（visual_compose.py:252-283）三级解析
  （`style_override` > `default_style_asset_id` > **唯一 approved STYLE**），全缺 → raise。
- `registry.active_profile(kind, name) -> (asset_id, version, ver)`（visual_registry.py:286-295）
  即 manifest 里 `profile_versions` 的天然来源（§7.6 QA 溯源锚点）。
- `ReferenceImage` 的 sha256 字段名是 `checksum`（visual.py / visual_registry.py:44-50）
  → manifest 的 `reference_images[].sha256` 由 `checksum` 映射而来。

### 1.5 编排层（`v2/orchestration/`）

| 事实 | 位置 | 结论 |
|---|---|---|
| `run()` 段顺序：casting → 每场景(scene, storyboard) → visual_assets → 齐备判定 | pipeline.py:279-306 | image 段插在 visual_assets 之后、齐备判定之前 |
| `storyboards: List[(sid, Storyboard)]` 已聚合，`_stage_scene` 返回 Storyboard 对象 | pipeline.py:287-296 | image 段直接消费，不需重新读盘 |
| `_collect_entities()` 只收 character/location/prop | pipeline.py:858-881 | ★**收不到 style**（`ASSET_KINDS` 无 style）→ 见 §三.3 |
| `_review_mode_label()` 已含 `"programmatic, NOT human review"` | pipeline.py:101-111 | image 段的 review_mode 直接复用该标签（由 pipeline 传入，避免循环 import） |
| `ArtifactStore.save()` 自动探测版本，v1 无后缀、v2+ 带 `_vN`，绝不覆盖 | workflow/artifact.py:102-165 | Storyboard 新版本天然成立 |
| `OrchestrationState` 是**镜像**而非第二套状态机 | state.py:27-32 | 图像进度只能纯追加，不得新建判据 |
| `report["completeness"]` 在 stage12 是**精确相等**断言 | stage12 run_tests.py:539-543 | ★**绝不能给 completeness 加键** |
| `stages` / `run_report` 无键集断言 | stage12 run_tests.py 全文 | 新增 `stages["image"]` 安全 |

---

## 二、厂商协议调研（S2 / A2）

### 2.1 目标协议形状（按 OpenAI 兼容实现）

```text
POST {base_url}/images/generations
  body: {"model": <Config 模型名>, "prompt": <str>, "size": "<W>x<H>"}
  resp: {"data": [{"b64_json": "<base64>"} 或 {"url": "https://..."}]}
```

处理顺序（实现于 `openai_compatible_image._reference_from_images_response`）：

1. `data[0].b64_json` 优先 → 直接拼 `data:image/png;base64,<...>`（**无二次网络**）；
2. 否则 `data[0].url` → 原样返回 URL，由编排层下载为二进制；
3. 两者皆无 → `ImageGenerationError`（不可重试，明确报错，**绝不静默留空文件**）。

### 2.2 尺寸记法差异 → 全部落进 Config

| 层 | 记法 | 依据 |
|---|---|---|
| run_spec / manifest（canonical） | `"1024*1024"` | 开工指令13 §6.1 / §6.2 明确用 `*` |
| 线上协议 | `"1024x1024"` | OpenAI 兼容约定 |

实现：`PROVIDERS["bailian_image"]["size_separator"] = "x"`，
`OpenAICompatibleImageProvider.normalize_size()` 在**发请求前**做替换；
manifest 与 run_report 始终记 canonical 记法（不因厂商而变）。**代码里没有任何尺寸常量硬编码。**

### 2.3 ★未能核实项（诚实登记，不推测）

公开文档核查结果：阿里云百炼官方《DashScope API 参考》只明确列出两个**原生**端点
（`/api/v1/services/aigc/text-generation/generation`、
`/api/v1/services/aigc/multimodal-generation/generation`），**兼容模式章节未覆盖图像生成**，
因此**无法从公开文档确认**兼容模式是否完整支持 `/images/generations`、返回 b64 还是 URL、
以及是否为异步任务（需 task_id 轮询）。

**处置（按 §17 的要求"先报告差异与选项，不要自行发明协议层"）：**

- 代码只实现**标准 OpenAI 兼容形状**，不发明任何厂商专属字段；
- `transport` 是**可注入**的（`OpenAICompatibleImageProvider(transport=...)`）——
  这是设计好的逃生口：若目标厂商需要异步轮询 / 签名 / 不同路径，
  **注入一个厂商 transport 即可，不动接口签名、不动架构**；
- 首次真实调用前必须由人工确认该端点在所选厂商上可用（已列入
  `run_real_image.py` 的体检项与 `stage13_report.md` 的遗留清单）。

### 2.4 计费单位 → 图像价格表与 token 价格表**物理分开**

```text
config.PRICING        : 每 1M tokens（text/vision，既有）
config.IMAGE_PRICING  : 按"每张 / 按尺寸"（本阶段新增，结构见 config.py）
    "<model>": {"per_image": <float>, "by_size": {"1024*1024": <float>}, "currency": "CNY"}
```

`estimate_image_cost(model, size) -> (cost, currency)`；**未登记 → (None, "")**。
纪律：`estimated_cost` 记 `null`，**绝不用 token 价格冒充**（§7.3-5）。
初始 `IMAGE_PRICING` 为空表（护栏状态），需人工在真实出图前登记。

---

## 三、开工指令与既有实现的冲突及裁决（★总控已逐条裁决）

排查方法：对全部历史套件 `grep` 了 `create_provider` / `GENERATION_KINDS` / `禁改` /
`0 diff` / `预期 0 改` / `sk-` / `ASSET_KINDS` / `completeness` 等断言点。

### 3.1 冲突 1：stage10 T11b 的「生成类」断言 → 裁决：**保留措辞**

`stage10_visual_asset/run_tests.py:872`：
```python
ok = ok and "生成类" in str(expect_raises(ValueError, create_provider, "image", "bailian"))
```

Stage 13 §7.1 要开通 image，§8.3 又禁止改历史断言，AC1 要求 stage10 = 76/76。

**裁决（总控）**：真实 image 档未配置时的报错措辞**自然包含**「生成类」字样，历史断言不动。
实现：`_create_image_provider` 在 `default_model` 为空时报
`"create_provider(kind='image', vendor='bailian')：生成类 Provider（image）已开通，但
config.PROVIDERS['bailian_image']['default_model'] 为空：真实出图前请在 Config 填入具体图像模型名（不硬编码）..."`。
—— 诚实（该调用确实仍被拒绝）且 stage10 保持绿。

附带确认（不冲突）：stage9 的 `create_provider("image")`（vendor 缺省）仍抛错（§1.2）；
stage9 对预留基类 `ImageGenerationProvider().generate_image()` 抛 `NotImplementedError` 的断言
不受影响（`base.py` 是禁改件）。

### 3.2 冲突 2：历史套件的「禁改 0 diff」护栏 → 裁决：**按 pre-commit 护栏理解**

`git diff --stat HEAD -- <path>` 类断言共 3 处会因本阶段**合法**改动而变红：

| 套件 | 断言 | 涉及路径 | 本阶段为何合法改动 |
|---|---|---|---|
| stage10 | T15b `config.py 预期 0 改` | `v2/config.py` | §8.2 明确允许（bailian_image 档 + 图像价格表） |
| stage11 | T17 `禁改文件 0 diff` | `v2/llm`（整目录） | §8.2 明确允许（registry / request_counter / \_\_init\_\_ / 新增 provider） |
| stage12 | T16 `禁改清单 0 diff` | `v2/llm`（整目录） | 同上 |

**裁决（总控）**：这些是 **pre-commit 护栏**（`diff vs HEAD`），人工批准 commit+tag 之后
HEAD 已含改动 → 自然归零，三套恢复 76/76、65/65、125/125。

执行侧的落地方式（不掩盖真回归）：`run_tests.py` 的 `_judge_history()` **只豁免**
"仅因 Stage 13 实际改动路径而未过的禁改护栏"（路径从 `git status` 推导，非手写死清单），
其余任何 FAIL 一律判失败；被豁免的 FAIL 逐条写入 `test_report.json.excused_history_fails` 供审计。

### 3.3 冲突 3：STYLE 视觉档案缺口 → 裁决：**扩展 visual_assets 段支持 style**

**现象（已实证）**：`_collect_entities()`（pipeline.py:858-881）只收 character/location/prop，
而 STYLE 不是 Shot 能引用的资产（`core.shot.ASSET_KINDS` 里没有 style）→ 编排层**永远不建 STYLE 档案**；
但 `VisualComposer._resolve_style()` 出图时必须解析到唯一 approved STYLE，否则 raise。实测：

```text
approved STYLE assets: []
compose FAILS -> 镜头 S1_B1_SH2 无法解析项目默认风格（style_override / default_style_asset_id / 唯一 approved STYLE 均缺失）。
```

→ 按 §7.4 原样接线，出图会 100% 卡在 `compose()`，**AC5「端到端成立」无法达成**。

**裁决（总控）**：扩展 visual_assets 段支持 style。实现：

- `run_spec` 新增可选 `style_spec`（确定性输入，与 `scene_specs` 同一范式）+ `style_name`（缺省 `"default"`）；
- 提供时把 `("style", name)` 并入既有 `_collect_entities` 结果，**走同一套** VisualAssetAgent +
  Review + registry 机制（不新建段、不新建状态机）；
- `_semantic_data(kind="style")` 返回 `style_spec`（确定性数据，绝不调 LLM —— 沿用总控 D-5 处置）；
- **未提供 → 不建档**，`assets_referenced/approved` 计数与 Stage 12 逐值一致
  （不破 stage12 的精确相等断言）。

### 3.4 附带确认（不冲突，但已核实）

| 项 | 结论 |
|---|---|
| `report["completeness"]` | stage12 用**精确相等**断言 → 本阶段**绝不加键**；图像统计只进 `stages["image"]` |
| `stages` / `run_report` 键集 | 无断言 → 新增 `stages["image"]` 安全 |
| `state.json` | stage12 只断言"含 episodes/project_id、不含 title/world" → 纯追加 `scenes[].images` 安全 |
| `ASSET_KINDS` | 未改（stage8 封存前提） |
| `v2/llm/providers/__init__.py` / `v2/llm/__init__.py` | 不在任何历史禁改清单里 → 纯追加导出安全 |
| `v2/llm/providers/mock_image.py` / `openai_compatible_image.py` | 新文件，无历史清单涉及 |
| `v2/.gitignore` | §8.2 允许补规则；已核实新增规则**不会误伤** `pics/*.png`（V0.1 已跟踪资产） |

### 3.5 ★实测修正：回归重写产物不止 6 个文件（对指令 §3.2 坑 1 的补充）

指令 §3.2 登记"6 个历史报告产物"被回归重写。实测**污染面更大**——各套件 `main()` 都会重写
自己的 `TEST_DIR/test_report.json`，其中 stage10/11/12 的报告含随机 id → 必然 dirty：

```text
v2/logs/stage10_visual_asset/test_report.json
v2/logs/stage11_storyboard/test_report.json
v2/logs/stage12_lifecycle/test_report.json
```

若只复位 6 个文件，历史套件的「禁改 0 diff」断言仍会**假失败**（首次全量复跑实测：
stage13 T17a 报 `有改动: ['v2/logs/stage10_visual_asset', 'v2/logs/stage11_storyboard']`）。

**处置**：复位改为**按目录扫 dirty 后精确复位**（10 个历史套件目录全部属禁改清单，
其中的 dirty 只可能来自回归重写，复位永远正确），不再维护容易过时的手写清单。

---

## 四、子任务依赖复核（对照 §8.5）

```text
S1 分析接入点        ✅ 本文件 §一
S2 厂商协议调研      ✅ 本文件 §二（含"未能核实项"的诚实登记）
S3 Mock + registry   ✅ 已实现并单测
S4 真实 Provider + Config + 预算 ✅ 已实现并单测
S5 编排层 image 段   ✅ 已实现并端到端验证
S6 run_tests.py      ✅ T01~T19
S7 历史回归 10 套    ✅ 见 stage13_report.md 的测试证据
S8 run_real_image.py ✅ 已实现；★真实调用待人工四项前置（当前如实报告阻塞）
S9 报告 + 文档 + Git ✅ 报告与文档已同步；commit/tag 等人工批准
```
