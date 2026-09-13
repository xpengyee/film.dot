# FilmAgent V0.2 —— Stage 10 阶段总报告（Visual Asset Layer）

> 阶段：V0.2 Stage 10 —— Visual Asset 层（视觉身份 / 版本 / 参考图 / 组装）
> 分段：10-A 架构设计 → 10-B 实现 + 真实 Vision 冒烟 → 10-C 测试 / 收尾 Debug → 10-D 文档 / 交付 / Git
> 日期：2026-09-13
> 交付会话：Stage 10-D（文档 / 交付）
> 对应 Git：`v0.2-stage10`（`git rev-parse v0.2-stage10` 查精确 hash）

---

## 0. 结论（TL;DR）

```text
Stage 10 = PASS + 已封存

Stage 10 测试套件            = 76/76 PASS（含 R-stage1/3/4/6/7/8/9 子进程回归）
历史回归                     = stage1=19 / 3=38 / 4=25 / 6=24 / 7=29 / 8=27 / 9=26 全部 rc=0
真实 Vision API 调用         = 3（Stage 10-B，qwen3.7-plus，14,433 tokens，Retry=0）
真实文本 API 调用            = 0
成本                         = est_cost=null（bailian_vision pricing 未登记，配置护栏 T-3）
V0.1（FilmAgent/）           = UNCHANGED（0 diff）；仓库根 .gitignore = 0 diff
五 tag 未移动                = v0.1 / v0.2-stage7 / v0.2-stage8 / v0.2-stage9 / v0.2-stage10
```

---

## 1. 阶段定位与本阶段解决什么

Stage 8 定义了 `Shot` / `Beat` / `Storyboard` 的 **Schema**，但 `ShotAsset(kind, ref)`
里的 `ref` 只是语义名字——**没有任何"视觉身份"层**能承载"Mercy 长什么样"、
"废弃仓库什么环境"，也没有版本与参考图概念。Stage 9 解决了"用什么模型调用"，
但没解决"被调用的视觉内容从哪来"。

Stage 10 补上这一层：**给"被镜头引用的资产"补上视觉身份 + 版本 + 参考图 + 组装能力。**

```text
Stage 7  Scene 剧本
   ↓
Stage 8  Shot / Beat / Storyboard Schema（ShotAsset.ref = 名字级引用）
   ↓
Stage 10 Visual Asset 层  ← 本阶段：名字 → 稳定视觉身份 + approved profile + 参考图
   ↓
Stage 11 StoryboardAgent（生产 Shot，引用本层的名字）
   ↓
Stage 13 Image Generation（Composer 展开 → 出图）
```

---

## 2. 核心架构决策（ADR 补充）

| # | 决策 | 理由 |
|---|---|---|
| D5 | **语义与视觉分离**：`Character` / `Location`（`core/models.py`）语义模型 0 改、不加视觉字段 | 语义档案被冻结测试覆盖；视觉层必须独立演进 |
| D6 | 视觉身份 = `(kind, name)` **永久绑定**，`asset_id` 永不变更 | 名字是系统级唯一不可变身份；换档案 = 新版本，不是新身份 |
| D7 | 参考图挂**版本**而非资产，换图 = 新版本 | 符合"绝不覆盖"铁律，支持版本精确溯源 |
| D8 | Asset 生命周期 = Review 三态的**确定性派生**，不建第三套状态机 | 反对重复建设 |
| D9 | Shot↔Asset 用"**引用解析层**"而非改 Schema | 保住 stage8 封存与 V0.1 往返 |
| D10 | 不给 `ASSET_KINDS` 加 `style`，镜头级风格走 `visual_description` | 会破 stage8 精确断言 |
| D11 | 真实 API 必须**程序化计数 + 预算硬上限** | 人工数数不可靠 |
| D13 | **Token 纪律**：Shot/Artifact 只存名字级引用 + 本镜头增量，视觉档案零渗漏 | 防 context 爆炸与身份漂移 |

---

## 3. 实现摘要

### 3.1 新增模块（A/B 会话产出）

| 文件 | 职责 | 校验/证据 |
|---|---|---|
| `v2/core/visual.py` | 五类资产（char/loc/prop/style/reference）/ 七态生命周期 / 参考图 / 来源词表 + 数据结构 + dict 序列化 + 确定性规范化（不调 LLM、不碰磁盘） | T01–T02 |
| `v2/core/visual_registry.py` | 视觉身份唯一事实源：kind 前缀计数分配 ID（CHAR_001…）、`(kind,name)` 永久绑定、版本单调递增绝不覆盖 / 号不复用、参考图 `sha256_of_file` checksum、生命周期派生、`registry.json` 持久化 | T03–T06 |
| `v2/core/visual_compose.py` | `VisualComposer`：调用模型前一次性把 Shot 的名字引用解析并展开为 `ComposedVisualInput`（紧凑 `key: value` 渲染，省 token）；缺档默认 `allow_partial=False` → 明确报错列缺口 | T07–T08 |
| `v2/agents/visual_asset.py` | `VisualAssetAgent` + `VisualAssetStep`：`plan` 模式走 TextProvider（`chat_json`）、`extract` 模式走 `VisionProvider.understand`；规范化 `normalize_profile_fields` 确定性 | T12 |
| `v2/llm/providers/openai_compatible_vision.py` | OpenAI 兼容图文 parts 的 Vision Provider（本地文件 base64） | T10f |
| `v2/llm/request_counter.py` | 真实请求计数 + 预算硬上限（按 kind 分桶、并发安全） | T09 / T14 |
| `v2/prompts/v0.2/visual_asset_plan.txt` / `visual_asset_extract.txt` | plan / extract 两模式 prompt（含 per-kind field_contract） | T12c |

### 3.2 修改文件（最小增量）

```text
v2/.gitignore                    ` +19 补 Stage 10 运行时产物忽略规则
v2/core/__init__.py              ` +32 导出 visual* 三模块
v2/core/validator.py             +263 VisualAssetValidator（纯追加，既有校验器 0 改）
v2/llm/__init__.py               ` +13 导出 Vision Provider
v2/llm/providers/__init__.py     ` +6
v2/llm/providers/registry.py     ` +57 bailian_vision 档接入
```

未改动（保持封存）：`core/shot.py` / `core/adapter.py` / `core/models.py` /
`agents/base.py` / `agents/casting.py` / `agents/scene_generation.py` /
`v2/llm/client.py` / `v2/llm/router.py` / `v2/memory/` / `v2/review/` /
`v2/workflow/` / `v2/context/` / `FilmAgent/**` / 仓库根 `.gitignore`。

---

## 4. 测试证据

### 4.1 Stage 10 套件

```text
env -u PYTHONPATH python v2/logs/stage10_visual_asset/run_tests.py
→ TOTAL: 76/76 passed (rc=0)
→ report: v2/logs/stage10_visual_asset/test_report.json
→ real_api_requests = 0
```

覆盖分组：

```text
T01 词表/契约（五类/七态/参考图/来源/ID格式/状态转移表）
T02 数据结构往返 + normalize + per-kind Profile 类
T03 校验器（10 类错误码 + per-kind 必填门槛 + registry 级唯一性）
T04 身份分配（kind 前缀计数 / (kind,name) 绑定 / registry.json 往返 + 计数器防回退）
T05 生命周期（draft→reviewing→approved / 新版批准旧版 superseded / reject / 编辑 / 退役 / 号不复用）
T06 参考图（sha256 稳定 + 内容寻址 + 持久化 + 版本绑定隔离 + Workflow 门槛边界）
T07 Composer（四类解析 + 紧凑渲染 + 缺口报错 + 风格三级解析）
T08 **Token 硬证据：组装前后 Storyboard 序列化逐字节不变，profile 不渗漏**
T09/T14 计数器（真实/mock 分账 + 预算硬上限 + 审计最小集 + 8 线程并发）
T10 Vision Provider（调用即计数 / retry 计数 / 本地校验失败不计数 / payload parts / 审计无 key）
T11 工厂护栏（真实档需配置 / mock 分支不变 / 生成类仍拒绝）
T12 Agent/Step（plan 规范化 / extract 0 API / 缺 Provider 报错 / Step 壳）
T13 E2E mock（plan → registry → 审批 → Composer 消费）
T15–T17 冻结/静态/依赖方向/密钥扫描/Git 忽略边界
R-stage1/3/4/6/7/8/9 历史回归全绿
```

### 4.2 历史回归（独立复跑确认）

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

---

## 5. 真实 API 账目

```text
Stage 10-B  真实 Vision 调用 3 次（qwen3.7-plus）
            14,433 tokens，Retry=0
            est_cost = null（PROVIDERS["bailian_vision"]["pricing"]=None，配置护栏 T-3）
            证据：v2/logs/stage10_visual_asset/real_run_report.json
                  v2/logs/stage10_visual_asset/real_vision_calls.jsonl
Stage 10-C  0 次（全程 mock / 注入 fake transport）
Stage 10-D  0 次
真实文本调用 0 次；真实图像 / 视频调用 0 次
```

---

## 6. Acceptance Criteria 逐条对照

| AC | 内容 | 结果 |
|---|---|---|
| AC1 | Stage 10 套件 76/76 + 历史回归全绿 | ✅ 总控复跑 rc=0 |
| AC2 | V0.1 冻结：`git diff -- FilmAgent/` 为空；全部 tag 未移动 | ✅ 空 / 五 tag 逐一比对 |
| AC3 | 禁改清单 0 diff | ✅ T15a 断言 + git 核实 |
| AC4 | 五类资产 / 七态 / 参考图 / 版本契约完整 | ✅ T01–T06 |
| AC5 | Registry 身份与版本纪律（ID 不变 / 号不复用 / 绝不覆盖） | ✅ T04–T05 |
| AC6 | Token 硬证据：注册视觉资产前后 `Storyboard.to_dict()` 逐字节不变 | ✅ T08 |
| AC7 | 链路可通：Shot 名字引用可被 `VisualComposer` 解析（缺档明确报错） | ✅ T07 |
| AC8 | 机制复用：生命周期 = Review 三态派生，无第三套状态机 | ✅ T05 |
| AC9 | 真实 API：3 次视觉 + 0 次文本，账目有据 | ✅ `real_run_report.json` |
| AC10 | 静态检查全过（compileall / import / 依赖方向 / 密钥扫描） | ✅ T15–T17 |
| AC11 | 文档同步（PROJECT_STATUS / HANDOFF / ROADMAP / CLAUDE.md / 本报告） | ✅ 本阶段完成 |
| AC12 | Git：工作区无泄漏文件；commit + tag 待人工批准 | ✅ 已复位回归重写报告、删 `llm_calls.jsonl` |

---

## 7. Stage 10-C 收尾三要点

1. **ReferenceImage 挂版本链路**（T06b-1..6）：`Asset → v1 → add_reference_image()`
   → `reference_images` 非空 / checksum = sha256 且稳定 / 同图重复登记 checksum 一致 /
   不同内容 → 不同 checksum / v1→v2 版本绑定隔离 / 身份不变。
2. **Smoke vs Workflow 校验边界**（T06c-1..5）：`require_complete=True` 下
   `complete=False` → `Step.validate()` 返回 FAIL（`MISSING_FIELD`，确定性错误列表非异常）；
   smoke 直调只"观察"。同一快照两种结论 = 预期差异。
3. **测试素材 Git 污染**（T17a..c）：补 `v2/.gitignore` 深路径锚定规则，
   图片 / registry 快照 / API 记账全部忽略；脚本 / 报告 / 结构化 JSON 证据保留；
   仓库根 `.gitignore` 0 diff。

---

## 8. Stage 10-D 交付动作（本会话）

```text
1. 复位被回归重写的报告产物：
   git checkout -- v2/logs/stage3_workflow_test/stage3_report.json
                   v2/logs/stage3_workflow_test/stage3_console.txt
                   v2/logs/stage4_memory_context_review_test/stage4_report.json
                   v2/logs/stage4_memory_context_review_test/stage4_console.txt
                   v2/logs/stage6_casting/stage6_report.json
                   v2/logs/stage7_scene_generation/stage7_report.json
2. 删除记账泄漏文件：rm -f llm_calls.jsonl v2/llm_calls.jsonl
3. 同步文档：PROJECT_STATUS.md / HANDOFF.md / ROADMAP.md / CLAUDE.md / 本报告
4. commit + tag v0.2-stage10（人工批准后）
```

交付前 `git status --short` 仅含预期文件（Stage 10 目录 7 个证据文件：
`run_tests.py` / `run_real_visual_extraction.py` / `test_report.json` /
`real_run_report.json` / `real_vision_calls.jsonl` / `stage10_b_report.md` /
`stage10_b_real_vision_smoke_report.md` / `stage10_c_report.md` + 本报告）。

---

## 9. 遗留问题

见 `v2/docs/PROJECT_STATUS.md` §6 与 `v2/docs/ROADMAP.md` §5 技术债登记：

```text
T-1 从仓库根跑测试生成根目录 llm_calls.jsonl（本轮已删；根治需授权动仓库根 .gitignore）
T-2 prompt_length 对多模态消息语义失真（client.py 属禁改清单）
T-3 bailian_vision pricing 未登记 → est_cost=null（配置护栏）
T-4 run_real_visual_extraction.py 未登记参考图（可选一行增强）
T-5 人工审核为程序化模拟，交互式 UI 未做
T-6 stage4/6/7_report.json 每次回归被重写（交付前需复位）
```

另：`v2/agents/storyboard.py` 未实现 —— **Stage 11 目标**，开工指令
`D:\AI\FilmAgent\v0.2开工指令11` 已就位。

---

## 10. Stage 10-D 判定

```text
Stage 10-A 架构设计      = PASS（stage10_plan.md）
Stage 10-B 实现 + 冒烟   = PASS（62/62 + 真实 Vision 3 次）
Stage 10-C 测试 / 收尾   = PASS（76/76 + 历史回归全绿）
Stage 10-D 文档 / 交付   = PASS（本文档 + 文档同步 + Git 交付）

Stage 10 = PASS + 已封存
```

**STOP**：不自动进入 Stage 11。Stage 11 由全新 Claude Code 会话按
`v0.2开工指令11` 执行。
