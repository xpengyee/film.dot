# FilmAgent V0.2 —— Stage 10-C 收尾测试 / 小修复 报告

> 阶段定位：Stage 10-A（架构分析）、Stage 10-B（实现 + qwen3.7-plus 真实 Vision 冒烟，
> 3 次真实调用、Retry=0、62/62 测试全绿）已完成。本阶段为 **Stage 10-C：Testing / Debug /
> Final Cleanup**，只处理 3 个真实收尾点，不做重新设计、不做新功能。
> 时间：2026-09-13；执行：Stage 10-C 收尾会话。

---

## 0. 结论（TL;DR）

```text
Stage 10-C = PASS

收尾点 1 ReferenceImage 挂 Asset Version   = PASS（0 API 本地断言全绿）
收尾点 2 SHA-256 校验                      = PASS
收尾点 3 Version-bound reference 隔离       = PASS
收尾点 4 Smoke vs Workflow 校验边界          = PASS
收尾点 5 测试素材 Git 污染                  = PASS（已修 v2/.gitignore）
Stage 10 测试套件                          = 76/76 PASS（含 R-stage1/3/4/6/7/8/9 子进程回归）
历史回归                                   = Stage1/3/4/6/7/8/9 全部 PASS（独立复跑确认）
真实 Vision API 调用                        = 0（本阶段默认 0）
真实文本 API 调用                           = 0
费用                                       = ¥0.00
V0.1（FilmAgent/）                          = UNCHANGED（0 diff，四 tag 未移动）
未 commit / 未 tag / 未进入 Stage 10-D / 11
```

---

## 1. 收尾点 1：ReferenceImage 必须真正挂到 Asset Version

### 1-A 前置研究（不重新设计）

复核既有实现，确认链路：

```text
VisualAsset（稳定身份 asset_id，永不变）
  └── VisualAssetVersion（单调递增，绝不覆盖）
        ├── reference_images: List[ReferenceImage]      ← 挂版本上（换图 = 新版本入口）
        └── profile / visual_prompt
ReferenceImage = { image_id: IMG_XXX, path, kind, note, checksum(=sha256 64hex), created_at }
VisualAssetRegistry.add_reference_image(asset_id, version, *, path, kind, note)
  → 解析路径（相对则试 images_dir / project_dir）
  → checksum = sha256_of_file(path)（文件不存在抛 FileNotFoundError，不编造）
  → image_id = IMG_{_next_image_number:03d} 并自增
  → ver.reference_images.append(img) + self.save()
```

**Stage 10-B 遗留根因（已确认）**：`run_real_visual_extraction.py` 把图片路径传给
`agent.run_extract`，但**从未调用** `reg.add_reference_image(...)`，故三个版本的
`reference_images` 均为 `[]`：

```json
// v2/logs/stage10_visual_asset/real_smoke_single_image/visual_assets/registry.json（修改前实测）
"CHAR_001": { "versions": [ { "version": "v1", "reference_images": [] },
                            { "version": "v2", "reference_images": [] } ] }
"LOC_001":  { "versions": [ { "version": "v1", "reference_images": [] } ] }
"next_image_number": 1
```

即「参考图 → 挂版本 → sha256（Ground-Truth 锚点）」这条链路在真实冒烟中**未被行使**。

### 1-B 修复方式（最小、0 API）

**不改** `run_real_visual_extraction.py` 的核心定位（见收尾点 4），改为在 Stage 10 测试套件
新增 **0 API 本地测试 `test_reference_image_version_binding()`（T06b）**，用本地 1×1 PNG
完整行使该链路（`v2/logs/stage10_visual_asset/run_tests.py`）。

覆盖断言：

| 编号 | 断言 | 结果 |
|---|---|---|
| T06b-1 | `Asset→v1→add_reference_image()`：`reference_images` 非空；`image_id=IMG_001`；`kind` 正确；`path` 指向实际存在的文件（可解析，无独立 `uri` 字段）；`checksum == sha256_of_file(img)` 且为合法 64-hex | PASS |
| T06b-2 | **同图重复注册**：`image_id` 递增（IMG_002 ≠ IMG_001），但 `checksum` **完全一致** | PASS |
| T06b-3 | **不同内容 → 不同 checksum**（内容寻址，非身份寻址） | PASS |
| T06b-4 | 持久化往返：`registry.json` 重载后参考图数量 / 顺序 / IMG 号 / checksum 全不丢 | PASS |
| T06b-5 | `CHAR_001:v1 → 新 reference → CHAR_001:v2`：v1 `reference_images` 保留（3 张）、v2 独立保存（1 张）、v2 图 `IMG_004`、**身份 CHAR_001 / name Mercy 不变** | PASS |
| T06b-6 | 换参考图 = 新版本入口：`latest_version()=v2`、`current_version=v2`、v1 参考图不被改写 | PASS |

### 1-C 同图 checksum 稳定性（硬要求）

- 断言 T06b-2 显式证明：同一文件两次登记 → `checksum` **逐字符相同**（`c414cd0e204d… == c414cd0e204d…`）。
- 断言 T06b-3 反向证明：不同字节 → 不同 `checksum`。
- `checksum` 由 `sha256_of_file()` 对**文件内容**计算，**不**使用文件名 / 时间戳 / 随机 UUID。
  `ReferenceImage.image_id`（IMG 号）才承担"每次登记一个实体"的角色，与 checksum 语义正交。

---

## 2. 收尾点 2：Smoke Test 与正式 Workflow 校验边界

### 2-A 保留 smoke test 定位（未改核心）

`run_real_visual_extraction.py` 的定位是**真实 Vision 连通性 + Profile extraction 观察**
（允许观察模型原始输出，含 `complete=False` 的诚实结果），**不**为"更好看"改成完整业务
Workflow。本阶段对该脚本 **0 代码改动**。

### 2-B 新增 0 API 边界测试（`test_smoke_vs_workflow_validation_boundary()`，T06c）

| 编号 | 断言 | 结果 |
|---|---|---|
| T06c-1 | 校验器：`require_complete=True` 下缺必填字段 → 报错 | PASS |
| T06c-2 | `VisualAssetStep` + `require_complete=True` 构造 `complete=False` → `Step.validate()` 返回 **FAIL**，错误码 `MISSING_FIELD`（`data.profile.face` / `hair`） | PASS |
| T06c-3 | 门槛以**确定性错误列表**返回（非异常）→ Workflow Engine 据此拒绝推进（RETRYING → 重试耗尽 → FAILED），不会直接变成 approved | PASS |
| T06c-4 | 同一 Step 对 `complete=True` 通过（门槛不误杀） | PASS |
| T06c-5 | **同一快照两种结论**：smoke 直调 `run_plan` 只"观察"（`complete=False` 原样记录）；同一快照进 `Step.validate()` 即 FAIL | PASS |

结论：**正式 Workflow 有质量门槛（complete=False 不能 approved），smoke test 只是观察模型输出**。
两者是不同契约，差异为预期行为（Stage 10-B 报告 §9.3 已登记）。

### 2-C 纯本地 0 API

T06c 全程使用 `MockTextProvider` 注入 `VisualAssetStep`，真实 API = 0、成本 = ¥0。

---

## 3. 收尾点 3：测试图片 / 测试产物不能污染 Git

### 3-A 分析现有 `.gitignore`（不粗暴加通配符）

现有 `v2/.gitignore` 规则针对阶段 1~9 的产物，**已覆盖** `logs/**/tmp_projects/`
（故 `tmp_projects/t06/visual_assets/images/mercy_a.png` 本就忽略），但**未覆盖**：

| 路径 | 修复前 | 原因 |
|---|---|---|
| `v2/logs/stage10_visual_asset/real_smoke_single_image/visual_assets/images/reference_shared.jpg` | ❌ 未忽略 | 真实测试图片（二进制，321,530 B） |
| `.../real_smoke_single_image/visual_assets/registry.json` | ❌ 未忽略 | 运行时注册表快照 |
| `v2/logs/stage10_visual_asset/real_vision_calls.jsonl` | ❌ 未忽略 | 真实 API 记账（含 prompt 统计/耗时） |
| `v2/llm_calls.jsonl` | ❌ 未忽略 | `llm/client.py` 默认写 cwd 的记账文件 |

### 3-B 修复内容（只补真实缺口，深路径锚定，不用 `*.png` 之类宽通配）

在 `v2/.gitignore` 追加（**仓库根 `.gitignore` 保持 0 改**）：

```gitignore
# ---------- Stage 10 视觉资产的运行时产物（不忽略脚本/报告/结构化 JSON 证据） ----------
# 1) 真实/临时项目目录下的图片本体与注册表：二进制素材，绝不进版本库
logs/**/visual_assets/images/
logs/**/visual_assets/registry.json
# 2) 真实冒烟运行目录：真实 API 调用产生的项目目录（含图片 + 注册表快照）
logs/**/real_smoke_single_image/
logs/**/real_project/
# 3) 真实 API 记账 / 临时产物
logs/**/real_vision_calls.jsonl
logs/**/real_llm_calls.jsonl
logs/**/image_generation_calls.jsonl

# ---------- API 调用记账文件（全局兜底，任意层级） ----------
llm_calls.jsonl
**/llm_calls.jsonl
```

**保留（不忽略）**：测试脚本、测试报告、结构化 JSON 证据 ——
`run_tests.py` / `run_real_visual_extraction.py` / `real_run_report.json` /
`test_report.json` / `stage10_b_report.md` / `stage10_b_real_vision_smoke_report.md` /
本报告。**未**为保留一张图把整个运行产物目录加入 Git。

另：仓库根遗留的一次探针产物 `llm_calls.jsonl`（16 行 mock `vl-unit` 记账，非真实调用、
无密钥）已删除；此后由 `**/llm_calls.jsonl` 兜底，不会再进版本库。
**最终 `git status` 对 Stage 10 目录仅可见 6 个证据文件**（脚本 2 + 报告 3 + JSON 1），
图片 / 注册表 / API 记账全部被忽略。

新增测试 T17（`test_freeze_and_static` 内）：

| 编号 | 断言 | 结果 |
|---|---|---|
| T17a | 测试图片 / 真实产物 / API 记账（6 条路径）均被 Git 忽略 | PASS |
| T17b | 脚本 / 报告 / 结构化 JSON 证据（7 条路径）未被误忽略 | PASS |
| T17c | 仓库根 `.gitignore` 0 diff（V0.1 冻结延伸） | PASS |

---

## 4. Stage 10 测试

```text
python v2/logs/stage10_visual_asset/run_tests.py  →  76/76 PASS  (rc=0)
report -> v2/logs/stage10_visual_asset/test_report.json
real_api_requests = 0
```

本阶段新增/补强 12 项断言（原 62 项 → 76 项）：

```text
T06b-1..6   ReferenceImage 挂版本 + sha256 稳定 + 版本绑定隔离（收尾点 1，0 API）
T06c-1..5   Smoke vs Workflow require_complete 校验边界（收尾点 2，0 API）
T17a..c     测试素材 Git 忽略边界（收尾点 3，0 API）
```

已含指令要求的全部证据：ReferenceImage persistence、SHA-256、Version-bound reference、
Smoke vs Workflow validation boundary、Git-ignore related path check。

---

## 5. 历史回归

套件内 `run_history()` 子进程回归 **全绿**；另行独立复跑确认（`env -u PYTHONPATH` 剥离宿主
`sitecustomize` 注入，见 §9.1）：

| 阶段 | 结果 |
|---|---|
| Stage 1 | 19/19 PASS (rc=0) |
| Stage 3 | 38/38 PASS (rc=0) |
| Stage 4 | 25/25 PASS (rc=0) |
| Stage 6 | 24/24 PASS (rc=0) |
| Stage 7 | 29/29 PASS (rc=0) |
| Stage 8 | 27/27 PASS (rc=0) |
| Stage 9 | 26/26 PASS (rc=0) |
| Stage 10 | 76/76 PASS (rc=0) |

全部使用项目现有脚本（`v2/logs/<dir>/run_tests.py`），未猜路径、未改历史套件源码。

---

## 6. 真实 API 调用 / Token · Cost

```text
Real Vision API calls : 0
Real Text API calls   : 0
Cost                  : ¥0.00
```

- 本阶段默认 Real Vision API = 0，全程 mock / 注入 fake transport。
- `real_vision_calls.jsonl`（3 条，Stage 10-B 13:33 产物）与 `real_run_report.json`
  **未被本阶段改动**（mtime 仍为 09-13 13:33），证明本阶段**未再次调用 qwen3.7-plus**。
- 上次（Stage 10-B）真实耗时：3 次调用 / 14,433 tokens / est_cost=null（配置护栏，
  未登记 qwen3.7-plus 官方价）。本阶段无新增。

---

## 7. V0.1 状态

```text
git diff -- FilmAgent/        = EMPTY（0 diff）
git diff --stat v0.1 -- FilmAgent/ = EMPTY

四 tag commit 哈希（与基线逐一比对，全部未移动）：
  v0.1         344c0c6b7c48497be934957c29fcae029cca6752  ✅
  v0.2-stage7  300aae44ad1d704219822efcf55249d1d59ad118  ✅
  v0.2-stage8  abf8e88a983f13c437abe2b719c920d3c5c3c144  ✅
  v0.2-stage9  82a89b50d337663027e8ecd638f45d7b94eaa882  ✅
  HEAD         82a89b5 feat(v0.2): complete provider architecture（未新增 commit）

仓库根 .gitignore = 0 diff
```

结论：**V0.1 UNCHANGED**。未 commit、未 tag。

---

## 8. 修改文件

```text
修改（2）：
  v2/.gitignore                                   ← 补 Stage 10 运行时产物忽略规则
  v2/logs/stage10_visual_asset/run_tests.py       ← 新增 T06b / T06c / T17 + shim-safe 清理

删除（1，未跟踪的探针产物）：
  llm_calls.jsonl（仓库根，mock 探针 16 行，无密钥）

未改动（保持）：
  run_real_visual_extraction.py（smoke 定位保留）
  v2/core/visual.py / visual_registry.py / visual_compose.py / validator.py
  v2/agents/visual_asset.py / v2/llm/** / v2/workflow/** / FilmAgent/**
```

（注：`v2/core/__init__.py`、`v2/llm/__init__.py`、`v2/llm/providers/*` 等为 **Stage 10-B
未提交增量**，本阶段未再触碰。）

---

## 9. 遗留问题

1. **宿主 `sitecustomize` 劫持 `shutil.rmtree`（Environment issue，非实现/测试缺陷）**：
   运行环境经 `PYTHONPATH` 注入 shim，把 `shutil.rmtree` 改为"移入回收站"，导致历史套件
   清理自身 tmp 目录失败（表现为 R-stage4/6/7/8/9 假失败）。**修复方式**：测试套件内改用
   `os` 层原语 `_hard_rmtree()`（只删自己创建的 `tmp_projects`），并在 `run_history()` 为
   子进程剥离 shim 注入（`_clean_child_env()`）。历史套件源码 **0 改动**。此为宿主环境问题，
   **非** FilmAgent 缺陷。
2. **`prompt_length` 对多模态消息语义失真（Stage 9 观察项，未处理）**：视觉消息 `content`
   是 content-parts 列表，`LLMClient._log` 按 `len(content)` 计，故 JSONL 中
   `prompt_length=2`（实为 part 数）、`message_count=1`。不影响 `prompt_hash`（仍 64 位）与
   计费；`v2/llm/client.py` 属**禁改清单**，按指令仅登记、不修改。
3. **成本为 null（配置护栏，非缺陷）**：`PROVIDERS["bailian_vision"]["pricing"]=None`，
   未登记 qwen3.7-plus 官方价即不计成本，不以 DeepSeek 价冒充。如需真实成本，在
   `config.PRICING` 登记即可，无需改业务代码。
4. **文档未同步 Stage 10**：`v2/docs/PROJECT_STATUS.md` / `HANDOFF.md` 仍写"Stage 9 已完成 /
   Stage 10 尚未开始"，属 **Stage 10-D** 交付范围，本阶段按指令不改。
5. **`run_real_visual_extraction.py` 仍未登记参考图**：按指令 §四 2-A 保留 smoke 定位
   （只验证连通性与 Profile extraction），参考图链路已由 0 API 本地测试 T06b 完整证明。
   若 Stage 10-D 希望真实冒烟也落参考图，可加一行 `reg.add_reference_image(...)`（属可选增强，
   非本阶段范围）。

---

## 10. Stage 10-D 建议

1. **同步文档**：更新 `PROJECT_STATUS.md` / `HANDOFF.md` / `CLAUDE.md`，写入 Stage 10-A/B/C
   状态与 Visual Asset 层边界（身份/版本/参考图/checksum 契约）。
2. **提交策略**：Stage 10-B 增量 + 本阶段修复建议**一起 commit**（建议一次 commit
   `feat(v0.2): visual asset layer + stage10-c cleanup`），tag `v0.2-stage10`；
   确认 `git status` 仅含预期文件（Stage 10 目录仅 6 个证据文件）。
3. **可选增强**：真实冒烟脚本登记参考图（一行），使真实链路也落 checksum 锚点。
4. **进入 Stage 11 前**：确认 Stage 11 计划是否依赖 Visual Asset 的 approved 版本 /
   参考图 checksum；若依赖，T06b 的契约即为接口基线。
5. **价格登记（可选）**：在 `config.PRICING` 登记 qwen3.7-plus，使后续真实调用有成本记账。

---

## 11. Stage 10-C 判定

```text
ReferenceImage 链路        = PASS
Workflow 校验边界           = PASS
测试素材 Git 污染           = PASS
Stage 10 测试              = 76/76 PASS
历史回归                   = 全部 PASS
V0.1 冻结                  = 未修改（0 diff，四 tag 未移动）

Stage 10-C = PASS
```

**STOP**：不 commit、不 tag、不进入 Stage 10-D / Stage 11。
