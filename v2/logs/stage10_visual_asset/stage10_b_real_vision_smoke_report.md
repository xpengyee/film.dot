# Stage 10-B 真实 Vision Smoke Test 报告 —— 同一张参考图完成三次验证

> 本次任务：在既有 Stage 10-B 代码 + Mock 测试（62/62）+ `APIRequestCounter` + 3 次预算硬上限
> 的基础上，执行**一次最小真实 Vision Smoke Test**。
> 特殊安排：**只用同一张已提供的二次元参考图**，跑满 3 次真实 Vision 调用（不新增图片、不下载、不生成）。
> 时间：2026-09-13；执行：Stage 10-B 真实冒烟会话。

---

## 0. 结论（TL;DR）

```text
PASS

真实 Vision 调用            = 3 次（程序化强制，恰好用满硬上限 3）
Retries                    = 0        Budget rejections = 0
真实文本调用                = 0 次     Mock 请求 = 0 次
CHAR_001 版本机制验证        = 成立（v1 → superseded，v2 → approved，current_version=v2）
LOC_001 管线验证             = 成立（v1 approved，complete=True）
图片生成（wanx-v1）          = 0 调用（按指令留给 Stage 13）
V0.1（FilmAgent/）冻结       = 未动（git diff 为空）
历史回归                     = Stage 10 套件 62/62 全绿（含 stage1/3/4/6/7/8/9 子进程回归）
```

---

## 1. 执行环境（本次真实视觉模型）

| 项 | 值 |
|---|---|
| Provider | 阿里云百炼（DashScope） |
| 接入端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1`（OpenAI 兼容） |
| Vision Model | **qwen3.7-plus** |
| 当前文本模型 | DeepSeek（本阶段真实文本调用 **0 次**） |
| Image Generation | wanx-v1 —— **本阶段 0 调用**（留给 Stage 13） |
| API Key | `sk-****fvxg`（仅经环境变量 `BAILIAN_API_KEY` 注入，**未落盘、未进 git、报告只显示脱敏**） |
| 运行时 | Anaconda Python 3.9.13 + `openai` 2.48.0（项目唯一非标准库依赖） |

**端点与密钥的预检（0 token，非模型调用）：** 执行前对候选端点做 `GET /models` 连通/鉴权探测，
`dashscope.aliyuncs.com/compatible-mode/v1` 返回 HTTP 200，`qwen3.7-plus` 在返回的 249 个模型中可用；
国际站端点返回 401（不匹配）；`coding.dashscope.aliyuncs.com` 该路径 404。故采用标准百炼兼容端点。

---

## 2. 先确认当前状态（本节为“进入前读档 + Git 核实”结果）

**读取：** `CLAUDE.md`、`v2/docs/PROJECT_STATUS.md`、`v2/docs/HANDOFF.md`、
`v2/docs/stages/stage10_plan.md`、`v2/logs/stage9_provider/stage9_report.md`。

```text
git status --short（进入时）
  M v2/core/__init__.py / v2/core/validator.py / v2/llm/__init__.py
  M v2/llm/providers/__init__.py / v2/llm/providers/registry.py      ← Stage 10-B 最小增量（未提交）
  ?? v2/agents/visual_asset.py / v2/core/visual.py / v2/core/visual_compose.py
  ?? v2/core/visual_registry.py / v2/llm/providers/openai_compatible_vision.py
  ?? v2/llm/request_counter.py / v2/prompts/v0.2/visual_asset_{plan,extract}.txt
  ?? v2/docs/stages/stage10_plan.md / v2/logs/stage10_visual_asset/ / v2/llm_calls.jsonl

Git 锚点（四个 tag 全部未移动）
  v0.1          = 344c0c6b7c48497be934957c29fcae029cca6752
  v0.2-stage7   = 300aae44ad1d704219822efcf55249d1d59ad118
  v0.2-stage8   = abf8e88a983f13c437abe2b719c920d3c5c3c144
  v0.2-stage9   = 82a89b50d337663027e8ecd638f45d7b94eaa882
  HEAD          = 82a89b5  feat(v0.2): complete provider architecture

git diff -- FilmAgent/  = 空（V0.1 冻结成立）
```

结论：环境与 Stage 9 封存一致；Stage 10-B 代码为未提交增量；V0.1 完全冻结。

---

## 3. 本次特殊安排（同一张图，三次不同语义目标）

```text
唯一输入图片：reference_shared.jpg（321,530 bytes）
  sha256 = 7d1f9014ec4f8be16d15d564e481c39b0456defd0863716c5606c928259f0e3f
  （三次调用传入的是同一个文件、同一份字节 → “同一张图”可判定）

Call #1  同一张图 → Character Visual Profile  → CHAR_001:v1
Call #2  同一张图 → Location  Visual Profile  → LOC_001:v1
         （与 #1 视觉输入相同、semantic target 不同：验证 Location 管线 /
           不同 Asset kind 共用同一 Vision Provider / Registry 对不同 kind 的处理。
           本 Call 不作为“真实场景识别准确率”的科学测试。）
Call #3  同一张图 → 声明为 CHAR_001 的第二次视觉参考 → CHAR_001:v2（版本机制）
```

执行方式：复用已封存的验收脚本 `run_real_visual_extraction.py`（本就设计为“恰好 3 次真实视觉调用”），
把三个图片槽位（character_a / character_b / location）**全部指向同一文件**，
模型经 `--model qwen3.7-plus` 指定（不改 `config.py`，保持其 0 改护栏）。
执行前先 `--check`（0 调用）确认四项前置门槛，再加 `--yes` 正式执行。

---

## 4. 三次调用结果（真实）

| # | 语义目标 | 输入图 | Asset | 版本 | complete | 本次真实请求 | 用时 |
|---|---|---|---|---|---|---|---|
| 1 | character（Mercy） | reference_shared.jpg | `CHAR_001` | `v1`（approved） | False | 1 | 52,608 ms |
| 2 | location（City Cafe） | reference_shared.jpg | `LOC_001` | `v1`（approved） | True | 1 | 36,061 ms |
| 3 | character（Mercy，第 2 次参考） | reference_shared.jpg | `CHAR_001` | `v2`（approved） | False | 1 | 30,280 ms |

**提取到的 Visual Profile（真实 VLM 输出，节选）：**

- `CHAR_001:v1`
  `appearance`: Slender figure seen from behind wearing a dark hooded coat, standing amidst a chaotic, shattered landscape of red and blue energy.
  `clothing`: Long dark grey hooded coat or cloak; pale legs visible below.　`face`/`hair`: “”（图中所见为背影裹袍，模型按“只见即录、未见留空”契约留空）
- `LOC_001:v1`
  `environment`: Exterior surreal landscape, chaotic and intense mood, sci-fi anime aesthetic.
  `lighting`: Dramatic high-contrast lighting, glowing red and cyan emissions, bright white backlight.
  `palette`: Deep reds, blacks, whites, and electric blue.
  `architecture`: Abstract jagged geometric forms, large mechanical wing structures, fractured ground with glowing cracks.
- `CHAR_001:v2`
  `appearance`: Stylized anime illustration of a lone figure in white standing amidst chaotic, abstract geometric shards and lightning.
  `clothing`: Long white coat or dress with high collar, appearing hooded or obscured by shadow.

> 说明：本图是一张**高反差、人物居于远景/剪影**的二次元场景图。character 的两个版本
> `complete=False`（`face`/`hair` 空）是模型**遵守“未见留空、不编造”契约**的诚实结果，
> 属预期，不属缺陷。

---

## 5. 程序化请求计数（`APIRequestCounter.snapshot()`，非人工统计）

```text
Real API requests         : 3
  - by kind               : {'vision': 3}
Mock requests             : 0
Logical calls             : 3
Retries                   : 0
Maximum real Vision budget: 3
Budget exceeded           : NO（被拒绝 0 次）

按样例归属：
  sample #1 (CHAR v1) : 1 次 -> CHAR_001:v1 [active=v1]
  sample #2 (LOC  v1) : 1 次 -> LOC_001:v1  [active=v1]
  sample #3 (CHAR v2) : 1 次 -> CHAR_001:v2 [active=v2]

版本机制验证：CHAR_001:v1 状态 = superseded（v2 批准后应 superseded）  ✅
```

最小审计集（每条含 timestamp / provider / model / modality / request_id / attempt / is_real，
**不含 API key、不含 prompt 正文、不含图片内容**）：

```text
2026-09-13T13:31:14+0800  bailian  qwen3.7-plus  vision  ea7ea5c6…  attempt=1  is_real=true
2026-09-13T13:32:07+0800  bailian  qwen3.7-plus  vision  1c4d7d46…  attempt=1  is_real=true
2026-09-13T13:32:43+0800  bailian  qwen3.7-plus  vision  5c0089a6…  attempt=1  is_real=true
```

---

## 6. Token / Cost 记账（与请求计数分离，走既有 `TokenLogger`）

| # | request_id | input | output | total | duration_ms | success | retry | est_cost |
|---|---|---|---|---|---|---|---|---|
| 1 | a97c5d72… | 2730 | 2579 | 5309 | 52608 | true | 0 | null |
| 2 | 9be24dca… | 2706 | 2033 | 4739 | 36061 | true | 0 | null |
| 3 | 336e8a79… | 2730 | 1655 | 4385 | 30280 | true | 0 | null |
| **合计** | | **8166** | **6267** | **14433** | | 3/3 | 0 | **null** |

- `estimated_cost = null`：`PROVIDERS["bailian_vision"]["pricing"] = None`（未登记 qwen3.7-plus 官方价）
  ——这是**配置护栏**（不以 DeepSeek 价冒充），**本报告不编造任何价格**。
  如需真实成本：在 `config.PRICING` 按百炼官方价登记 `qwen3.7-plus` 即可，无需改业务代码。

---

## 7. Registry 证据（视觉身份唯一事实源）

```text
registry.json: v2/logs/stage10_visual_asset/real_smoke_single_image/visual_assets/registry.json
summary: assets=2  by_kind={character:1, location:1}  versions=3  active=2

CHAR_001 (kind=character, name=Mercy)
  v1  status=superseded   source=vision_extracted   notes="real smoke sample #1"
  v2  status=approved     source=vision_extracted   notes="real smoke sample #3"   ← current_version
LOC_001  (kind=location,  name=City Cafe)
  v1  status=approved     source=vision_extracted   notes="real smoke sample #2"   ← current_version
```

- **身份不可变**：三次调用后 `CHAR_001` 身份不变，仅版本从 v1 递增到 v2，旧版本转 `superseded` 且内容原样保留。
- **不同 Asset kind**（character / location）经**同一 Vision Provider（同一实现类、同一计数器）**处理，
  Registry 按 kind 前缀分配 ID（`CHAR_001` / `LOC_001`）并各自独立计数。

---

## 8. 验证目标 → 结论对照

| 本次要验证的点 | 结论 | 证据 |
|---|---|---|
| 图片 → Vision → 结构化 Character Visual Profile → 版本生效 | ✅ | `CHAR_001:v1` approved（§4/§7） |
| Location Visual Profile 管线 | ✅ | `LOC_001:v1` approved，`complete=True`（§4/§7） |
| 不同 Asset kind 共用同一 Vision Provider | ✅ | 三次调用同一 `OpenAICompatibleVisionProvider`，同一次运行同一次计数（§5） |
| Registry 对不同资产类型的处理 | ✅ | `CHAR_001` / `LOC_001` 按 kind 前缀独立建档、独立版本计数（§7） |
| **版本机制**：同角色换参考图 → 资产不变、新增 v2、v1 转 superseded | ✅ | `char_v1_status_after_v2 = superseded`，`current_version=v2`（§5/§7） |
| 真实请求 ≤ 3 且程序化强制 | ✅ | `real=3`，`budget_rejections=0`（§5） |
| 真实文本调用 0 次（规范化为确定性代码） | ✅ | 无 text 模态记账；计数 by_kind 仅 vision（§5/§6） |
| 不调用图片生成（wanx-v1 留给 Stage 13） | ✅ | 全程 0 次 image 生成调用（§10） |
| 历史功能零回归 | ✅ | Stage 10 套件 62/62，含 stage1/3/4/6/7/8/9 子进程回归（§11） |

---

## 9. 观察与遗留（如实记录，供 10-C / 10-D 处理）

1. **参考图未写入版本（`reference_images` 为空）**：`run_real_visual_extraction.py` 把图片路径传给
   `agent.run_extract`，但**未调用** `reg.add_reference_image(...)`，故三个版本的 `reference_images` 均为 `[]`。
   → Stage 10-B 设计的 “参考图挂版本 + sha256 checksum（Ground-Truth 锚点）” 这条链路在本次真实冒烟中**未被行使**。
   建议 10-C 补一条 0 API 的本地断言：登记参考图 → checksum 一致 → 同图可判定。
2. **`prompt_length` 对多模态消息语义失真**：视觉消息的 `content` 是 content-parts 列表，
   Stage 2 的 `LLMClient._log` 按 `len(content)` 计，故 JSONL 里 `prompt_length=2`（实为 part 数，非字符数）、
   `message_count=1`。不影响 `prompt_hash`（仍为 64 位）与计费；`client.py` 属**禁改清单**（§24），
   此处仅登记为观察项，不修改。
3. **直接调用 vs Step 校验门槛**：真实冒烟**直接调用** `agent.run_extract`，绕过了
   `VisualAssetStep.validate`（`require_complete=True`）。因此 `complete=False` 的 `CHAR_001` 也照常 approved。
   走正式 Workflow 时，`complete=False` 会触发 Step 校验失败 → 整步重试。属**预期差异**（冒烟脚本定位为管线连通性验证），
   但应在 10-C 明确断言两种路径的差异，避免误解。
4. **成本为 null**：见 §6，未登记价格即不计成本（护栏），非缺陷。
5. **新增产物未被 `.gitignore` 覆盖**：`real_smoke_single_image/visual_assets/images/reference_shared.jpg`（321,530 B）
   等新文件目前**不在忽略列表**内。按 `stage10_plan §25`（fixture 不含图片本体、不含 key），
   10-D 提交前需决定：忽略图片本体 / 只保留报告与 JSON 证据。
6. **密钥卫生**：完整 API key 仅存在于环境变量注入的**命令行参数**中，**未写入任何文件**；
   生成的 `real_run_report.json` 只含脱敏 `sk-****fvxg`；`real_vision_calls.jsonl` 无 key。

---

## 10. 护栏核对（硬性）

```text
wanx-v1 / 任何图片生成模型调用      = 0 次        ✅（本阶段绝对不调用，留给 Stage 13）
真实 Vision 请求                    = 3 次        ✅（恰好 = 硬上限，未超）
真实文本请求                        = 0 次        ✅
API key 硬编码 / 进日志 / 进报告     = 无          ✅（仅脱敏显示）
V0.1（FilmAgent/）                  = 0 diff      ✅
四个 tag（v0.1/v0.2-stage7/8/9）    = 未移动       ✅
未下载/未部署本地模型、未搭向量库    = 是          ✅
```

---

## 11. 回归（0 成本）

```text
python v2/logs/stage10_visual_asset/run_tests.py   →  62/62 PASS  (rc=0)
  含 R-stage1 / R-stage3 / R-stage4 / R-stage6 / R-stage7 / R-stage8 / R-stage9 全绿
  含 T15a 禁改文件 0 diff、T16b ASSET_KINDS 精确不变、T16d 无硬编码 key 扫描
```

---

## 12. 产物清单

```text
v2/logs/stage10_visual_asset/
├── stage10_b_real_vision_smoke_report.md   ← 本报告
├── real_run_report.json                    ← 三次调用 + 程序化计数快照 + 审计（无 key/prompt/图片）
├── real_vision_calls.jsonl                 ← Token/Cost 记账（token 有，cost=null 护栏）
├── real_smoke_single_image/visual_assets/
│   ├── registry.json                       ← CHAR_001(v1 superseded, v2 approved) / LOC_001(v1 approved)
│   └── images/reference_shared.jpg         ← 本次唯一参考图（sha256 7d1f9014…）
└── run_real_visual_extraction.py           ← 复用既有冒烟脚本（本次 0 代码改动）
```

---

*Stage 10-B 真实 Vision Smoke Test：PASS。三次真实视觉调用合规用满、版本机制成立、V0.1 冻结未动、
未触碰图片生成。遗留项见 §9，交 10-C / 10-D。*
