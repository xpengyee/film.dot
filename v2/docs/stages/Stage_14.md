# FilmAgent V0.2 —— Stage 14 执行规格：VLM Image QA

> 本文件是 Stage 14 的完整执行规格。一个全新的执行会话应能仅凭本文件、仓库常驻文档与源码独立完成分析、实现、测试和交付。
>
> 生成日期：2026-09-29。设计者：项目总控。
>
> 当前前置状态：Stage 13 已封存；Stage 13-P 已通过总控静态验收，但仍在工作区、尚未 commit。**执行 Stage 14 代码前，必须先把 Stage 13-P 独立提交并确认工作区干净。**`v0.2-stage13` tag 永不移动。

---

## 1. Stage 背景与项目当前状态

FilmAgent V0.2 已具备：Canonical `Project → Scene → Beat → Shot`、视觉资产注册表、视觉档案版本、参考图 SHA256、StoryboardAgent、ProjectOrchestrator，以及 Stage 13 图像生成段。

当前链路为：

```text
Scene → Storyboard → VisualComposer → ImageGenerationProvider
      → 生成图落盘 → manifest.json → ShotAsset(kind="image")
```

Stage 13 已为 QA 保存三类关键锚点：

```text
generated image sha256
profile_versions: {asset_id: exact_version}
reference_images: [{image_id, sha256}]
```

当前缺口是：系统能够产生图，但不能程序化回答“生成图是否符合已批准角色、地点、道具和项目风格”。若直接进入视频生成，不一致的单帧会被带入更昂贵的视频链路。

Stage 14 的任务是建立独立的 VLM QA 段：读取 Stage 13 的图像与溯源锚点，调用现有多模态 Provider，按已批准视觉档案字段逐项裁决，产出可审核、可版本化、可恢复的 QA 报告。

### 1.1 前置阶段

- Stage 9：Provider 抽象与 Mock/真实实现。
- Stage 10：视觉档案、精确版本、参考图与 SHA256。
- Stage 11：Storyboard/Shot。
- Stage 12：项目生命周期编排。
- Stage 13：生成图、manifest、预算、落盘与 resume。
- Stage 13-P：百炼原生图像 Provider 补丁。与 QA 的实现逻辑无直接耦合，但必须先单独提交，避免阶段范围混杂。

### 1.2 当前真实边界

- Stage 13 尚未通过真实图像 API 生成项目图片；Mock 图像链路已验证。
- Stage 10 的真实 Vision Provider 曾成功调用 3 次，但“一个生成图 + 多个参考图”的 QA 业务尚未真实验证。
- ChatGPT Plus 网页端的手动生图或看图不等于项目 API 调用，不能作为 Provider 连通性证据。

---

## 2. 为什么需要本 Stage

1. 图像生成成功只证明“有文件”，不证明人物、地点、道具和风格一致。
2. Stage 10 的视觉档案字段本来就同时服务 prompt 组装与 VLM QA ground truth；不消费它们，视觉版本链只完成了一半。
3. Stage 15 Video 的调用成本高于单图。应在单帧阶段先阻止明显错误进入视频。
4. 现有人工 Review 能审核结构化 QA 报告，本阶段无需另建状态机。
5. manifest 已记录精确版本与校验和，本阶段应消费该契约，而不是重新猜测“当前最新版”。

---

## 3. 本阶段目标与完成后的系统变化

### 3.1 目标

- 新增 `ImageQAAgent` / `ImageQAStep`，用多模态 Provider 对一张生成图进行结构化 QA。
- 按 manifest 中的 `profile_versions` 读取**精确版本**，不能自动改用 registry 当前版本。
- 校验生成图和参考图的路径与 SHA256，校验失败时在调用 VLM 前明确停止。
- 一次 QA 调用的图片顺序固定：生成图第一张，参考图随后按稳定顺序排列。
- 对所有可评分的非空档案字段输出逐字段结果，并由本地规则计算总结果。
- QA 报告通过 ArtifactStore 版本化，复用 Review 三态。
- 在 ProjectOrchestrator 中新增默认关闭的 QA 段，可单独对既有生成图运行，也可接在 image 段之后。
- 支持 resume，已完成且 Artifact 仍存在的图片不重复调用 VLM。
- 真实调用受现有 Vision 请求计数、预算和 TokenLogger 保护。

### 3.2 完成后的链路

```text
Storyboard
  → ImageStage
  → generated image + Stage 13 manifest
  → ImageQAStage preflight
      ├─ 校验 generated sha256
      ├─ 解析 exact profile versions
      └─ 校验 reference image sha256
  → ImageQAStep / MultimodalProvider
  → versioned QA Artifact
  → existing Review
  → QA index + run_report
  → Stage 15 只消费“审核通过且 qa_decision=pass”的图片
```

### 3.3 非目标

- 自动修图、局部重绘、重新生成图片：N/A，本阶段不实现。
- 修改 VisualAsset/Profile 内容或审批状态：N/A，禁止。
- 删除或覆盖失败图片：N/A，禁止。
- 视频、音频、TTS、Assembly：N/A，留给 Stage 15+。
- 交互式人工审核 UI：N/A，继续使用既有 review policy。
- 使用 ChatGPT Plus 网页端充当程序化 Provider：N/A。

---

## 4. 架构位置与核心决策

### 4.1 模块位置

```text
core → llm → workflow/review → agents → orchestration
```

- `agents/image_qa.py`：业务 QA、响应解析、确定性规范化、Step 验证。
- `prompts/v0.2/image_qa.txt`：VLM 的稳定输出契约。
- `orchestration/image_qa_stage.py`：磁盘/manifest/registry 解析、校验和验证、调用调度、resume、报告汇总。
- `orchestration/pipeline.py`：在 image 段后接入 QA，默认关闭。
- `orchestration/state.py`：若需要，只允许添加对 Artifact/Review 结果的进度镜像；不得成为第二事实源。
- `logs/stage14_image_qa/`：测试、报告和可选真实冒烟脚本。

### 4.2 Provider 选择

使用现有 `MultimodalProvider.chat(messages, images=...)` 契约。原因：QA 必须同时查看生成图和零到多张参考图；`VisionProvider.understand()` 只表达单图输入。

允许注入实现了 `MultimodalProvider` 的 Provider；当前 `OpenAICompatibleVisionProvider` 已实现 `chat(images=...)`，无需修改公共接口。不得新增 `QAProvider` 或复制网络传输层。

真实调用必须继续经过 Provider 的 `_invoke()` 唯一计数点。Mock 调用不计真实请求。

### 4.3 Ground truth 解析

每个 manifest 条目必须按以下顺序解析：

1. 仅接受 `status == "generated"` 且有 `path/sha256/image_id/shot_ref` 的条目。
2. `path` 必须落在当前 project 目录内，文件必须存在。
3. 磁盘生成图 SHA256 必须等于 manifest `sha256`。
4. 对 `profile_versions` 的每个 `{asset_id: version}`：
   - registry 中必须存在 asset；
   - 必须读取 `asset.get_version(version)`；
   - 该版本必须是 `approved` 或其后被新版本取代的 `superseded`；
   - 禁止自动改用 `active_version()`，否则 QA 会拿新档案审旧图。
5. 从这些精确版本收集 profile、name、kind 和参考图路径。
6. manifest `reference_images` 中每个 `image_id + sha256` 必须能在上述精确版本找到；路径存在且磁盘 SHA256 相符。
7. manifest 与 registry 的 image_id/sha256 不一致、引用缺失、越界路径或非法结构，均在 VLM 调用前 fail-loud。

没有参考图时仍可按 approved profile 进行 QA，报告 `ground_truth_mode="profile_only"`；有参考图时为 `profile_and_references`。不得因为没有参考图而编造参考图。

### 4.4 图片顺序与 Prompt 约定

传给 Provider 的 `images` 必须为 tuple：

```text
images[0]   = generated image
images[1:]  = reference images，按 manifest reference_images 顺序稳定排列
```

Prompt 必须明确写出顺序映射，但不得内嵌图片字节、API key 或绝对路径。可写 image_id、asset_id、version 和字段期望值。

### 4.5 QA 字段范围

从精确版本的 Profile `FIELD_ORDER` 导出所有非空字段：

- `notes` 只作为上下文，不参与评分。
- `negative_cues` 作为“禁止出现项”检查；`match` 表示禁止项未出现。
- 其余非空字段全部要求 VLM 返回检查结果。
- 空字段不送审、不要求模型猜测。

不得另写一份与 `core.visual.PROFILE_CLASSES/FIELD_ORDER` 漂移的字段清单。

### 4.6 VLM 原始输出与确定性总判定

VLM 只返回逐字段观察，不得由它自由决定系统最终状态。每条检查的合法 verdict：

```text
match | mismatch | uncertain | not_visible
```

本地确定性规则：

```text
存在 mismatch                 → qa_decision = fail
否则存在 uncertain/not_visible → qa_decision = needs_review
全部为 match                   → qa_decision = pass
```

不得引入浮点 confidence 阈值。模型缺字段、重复字段、未知 asset/version/field、未知 verdict、额外未声明检查，均视为非法输出并触发既有 Workflow retry；不能静默补齐为 match。

### 4.7 Artifact、Review 与状态边界

每张图一个 Step/run，Artifact 基础名：

```text
image_qa_<image_id.lower()>
```

`ImageQAStep.needs_review = True`，继续复用现有 Review 三态：

- `approved`：接受 QA 报告。
- `approved_with_edit`：允许编辑结构化报告；Workflow 写新版本，原始版本保留。编排层必须对最终 approved artifact 再跑同一确定性 validator，不能让编辑绕过结构校验。
- `rejected`：QA 报告不被接受，记录失败，不修改图片。

QA 的 `qa_decision` 与 Review 的 `decision` 是两个维度：前者是画面质量结论，后者是人对报告的审核结论。Stage 15 的门槛必须同时要求“QA Artifact 已审核通过”且 `qa_decision == "pass"`。

QA 不修改 Stage 13 `manifest.json`、图片文件、Storyboard、ShotAsset 或 VisualAsset registry。可以新增 `quality/qa_manifest.json`，但它只能索引 `image_id → QA artifact/review/status/hash`，是既有 Artifact/Review 的镜像，不是新状态机。

### 4.8 默认关闭、预算与 resume

新增 run_spec：

```text
run_image_qa       bool，缺省 False
qa_image_ids       可选 image_id 白名单；空/缺省 = manifest 中全部 generated 条目
max_qa_images      缺省 3，0 表示本次不调用
qa_resume          缺省 True
```

- `run_image_qa=False` 时必须零 QA Provider 调用、零 QA Artifact、零 QA 索引变更，Stage 13 既有行为逐字节保持。
- 选中数量取 `min(max_qa_images, MAX_REAL_VISION_REQUESTS)`。
- 把同一上限下推到真实 Provider 使用的 `vision` 预算桶；retry 消耗真实预算。
- resume 命中条件至少包括：image_id、generated sha256、profile_versions、reference image sha256 集合均一致，且 QA Artifact 仍存在并已审核通过。任一锚点变化必须重新 QA，产出 Artifact 新版本，绝不覆盖。

---

## 5. 输入、输出、数据结构与接口

### 5.1 输入

- Stage 13 `generated_images/manifest.json`。
- manifest 指向的生成图片。
- `VisualAssetRegistry` 中 manifest 指定的精确版本。
- 精确版本上的 ReferenceImage 文件。
- run_spec 的 QA 开关、过滤、预算与 resume 设置。
- 注入的 MultimodalProvider。

### 5.2 Agent 输入结构

编排层传给 Step 的 `context.inputs["image_qa_input"]` 至少包含：

```json
{
  "schema_version": "1",
  "image_id": "GEN_001",
  "shot_ref": "S1_B1_SH1",
  "generated_path": "<resolved local path>",
  "generated_sha256": "<64 hex>",
  "ground_truth_mode": "profile_only|profile_and_references",
  "profiles": [
    {
      "asset_id": "CHAR_001",
      "version": "v1",
      "kind": "character",
      "name": "Lin",
      "fields": {"appearance": "...", "hair": "..."},
      "notes": ""
    }
  ],
  "reference_images": [
    {
      "image_id": "IMG_001",
      "sha256": "<64 hex>",
      "path": "<resolved local path>",
      "asset_id": "CHAR_001",
      "version": "v1"
    }
  ]
}
```

绝对路径只存在于进程内输入，不得写进入库报告；持久化时改为 project 相对路径或省略。

### 5.3 QA Artifact 输出结构

```json
{
  "schema_version": "1",
  "image_id": "GEN_001",
  "shot_ref": "S1_B1_SH1",
  "generated_sha256": "<64 hex>",
  "ground_truth_mode": "profile_and_references",
  "profile_versions": {"CHAR_001": "v1"},
  "reference_images": [{"image_id": "IMG_001", "sha256": "<64 hex>"}],
  "checks": [
    {
      "asset_id": "CHAR_001",
      "version": "v1",
      "field": "hair",
      "expected": "black bob",
      "verdict": "match",
      "evidence": "The generated subject has a short black bob."
    }
  ],
  "qa_decision": "pass|fail|needs_review",
  "summary": "",
  "provider": "",
  "model": "",
  "request_id": "",
  "retry_count": 0,
  "created_at": ""
}
```

`qa_decision` 必须由本地规则覆盖模型值。`provider/model/request_id/retry_count` 从 ProviderResult 注入，不让模型填写。不得持久化 prompt 正文、图片 base64 或 API key。

### 5.4 公共接口变化

- `llm/providers/base.py`：N/A，禁止修改。
- `VisionProvider` / `MultimodalProvider` 签名：N/A，禁止修改。
- Canonical Shot/Storyboard Schema：N/A，禁止修改。
- 新增接口限于 `ImageQAAgent.evaluate(...)`、`ImageQAStep` 与 `ImageQAStage.run(...)`。

---

## 6. 相关文件与预计改动

### 6.1 新增

```text
v2/agents/image_qa.py
v2/prompts/v0.2/image_qa.txt
v2/orchestration/image_qa_stage.py
v2/logs/stage14_image_qa/run_tests.py
v2/logs/stage14_image_qa/stage14_report.md
v2/logs/stage14_image_qa/test_report.json
v2/logs/stage14_image_qa/run_real_image_qa.py   # 可选真实冒烟；默认体检、0 调用
```

### 6.2 允许最小修改

```text
v2/agents/__init__.py
v2/orchestration/__init__.py
v2/orchestration/pipeline.py
v2/orchestration/state.py       # 仅在确有需要时追加 QA 进度镜像
v2/docs/PROJECT_STATUS.md
v2/docs/HANDOFF.md
v2/docs/ROADMAP.md
CLAUDE.md
```

`config.py` 原则上不改：Stage 10 已有 vision/multimodal 配置。若真实证据证明缺少必要档位，执行会话必须暂停向总控报告，不得自行扩大范围。

### 6.3 禁止修改

```text
FilmAgent/ 全目录
仓库根 .gitignore
v2/core/ 全目录
v2/workflow/ 全目录
v2/review/ 全目录
v2/memory/ 全目录
v2/context/ 全目录
v2/llm/providers/base.py
v2/llm/providers/openai_compatible_vision.py
v2/llm/client.py
v2/llm/router.py
v2/llm/request_counter.py
v2/orchestration/image_stage.py
历史测试套件及其断言
```

若实现必须修改其中任何一项，先暂停并向总控提交：阻塞证据、最小修改方案、兼容风险、需要更新的历史测试。不得先改后报。

---

## 7. 子任务、依赖与开发顺序

### A. 契约分析

1. 核对 Stage 13 manifest 的真实字段与路径语义。
2. 核对 VisualAsset 精确版本、ReferenceImage 路径与 checksum 解析。
3. 核对 MultimodalProvider 的多图顺序、预算与日志行为。
4. 输出 `stage14_contract_notes.md`：接口、风险、字段裁决表、测试矩阵。

### B. Agent 与确定性 Validator

1. 实现 Prompt。
2. 实现 ImageQAAgent：组装消息、调用 provider、解析 JSON。
3. 实现字段集合生成、严格规范化、未知/缺失/重复检测。
4. 实现本地 `qa_decision` 派生。
5. 实现 ImageQAStep 与最终 approved artifact 再验证。

依赖：A → B。B 内部 1/3 可先并行设计，最终必须一起验证。

### C. 编排 QA 段

1. 实现 manifest 与路径安全解析。
2. 实现 generated/reference SHA256 预检。
3. 实现 exact profile version 解析。
4. 实现 image 选择、预算、逐图 Workflow run、QA 索引、run_report。
5. 实现 resume 锚点与 pipeline 默认关闭接线。

依赖：A → C；B 与 C 的纯结构部分可并行，Step 接线必须在 B 完成后串行整合。

### D. 测试与 Debug

按 §9 完成本阶段测试及历史回归。修复缺陷时只做最小修改，禁止删除/放宽断言。

依赖：B+C → D。

### E. 文档与交付

更新四份项目知识文档、阶段报告、Git 范围与真实成本记录。

依赖：D → E。

---

## 8. 异常处理与安全要求

- manifest JSON 非法、字段缺失、图片不存在、路径越出 project、SHA256 不符：VLM 调用前明确失败。
- 精确 profile 版本不存在或状态不合法：明确失败，禁止回落 active version。
- Provider 非 Multimodal 能力：构造/执行前明确失败，不静默用文本模型。
- VLM 非法 JSON、字段不全、额外字段、非法 verdict：`StepError`，按 Workflow 既有重试处理。
- 预算耗尽：沿用 `APIBudgetExceededError`，不可重试。
- 单张 QA 失败不删除图片；报告必须记录 image_id、错误类型和是否发出请求。
- 禁止日志记录图片 base64、API key 或完整 prompt 正文。
- 真实调用脚本缺任一前置条件时退出码非 0，真实调用数为 0，不伪造 PASS。

---

## 9. 测试方案

Stage 14 新套件必须默认 Mock、0 网络、0 真实 API，并至少覆盖：

### 9.1 契约与静态边界

- Agent/Step/Stage 接口存在，依赖方向正确。
- 禁改文件 0 diff；V0.1 0 diff。
- 公共 Provider、Canonical Schema 与 Stage 13 manifest 结构没有被改写。

### 9.2 Ground truth 与完整性

- 精确 v1 QA：即使当前 active 已是 v2，也必须读取 manifest 指定 v1。
- `approved` 与 `superseded` 版本可作为历史 ground truth；draft/rejected 不可。
- generated SHA256 正确通过，错误时零 Provider 调用。
- reference image_id/sha256/path 正确解析；缺失、伪造、磁盘变更均零调用失败。
- 路径穿越 project 根被拒绝。
- 无参考图走 `profile_only`，有参考图走 `profile_and_references`。

### 9.3 Prompt 与图片顺序

- images[0] 永远为生成图。
- 多张参考图顺序稳定且与 Prompt 标签一致。
- Prompt 只含需要的非空字段；notes 不评分；negative_cues 语义正确。
- 日志、Artifact 与 report 不含 base64、API key、绝对路径和完整 prompt。

### 9.4 输出规范化与裁决

- 全 match → pass。
- 任一 mismatch → fail。
- 无 mismatch 但 uncertain/not_visible → needs_review。
- 缺检查、重复检查、未知字段/版本/verdict、模型自报 qa_decision 与本地不一致均被拒或覆盖。
- evidence 为空的 mismatch/uncertain/not_visible 不允许通过验证。

### 9.5 Workflow / Review / Artifact

- 每图一个版本化 Artifact，重跑产生 `_v2`，原文件不覆盖。
- approved 正常落 QA 索引。
- approved_with_edit 生成新 Artifact，最终内容重新验证。
- rejected 保留原始 QA Artifact，但不标记为可下游消费。
- QA fail 与人工 reject 均不修改生成图、Storyboard、Stage 13 manifest 或 registry。

### 9.6 Pipeline、预算与 resume

- `run_image_qa=False` 保持 Stage 13 行为，零调用零新文件。
- `max_qa_images=0` 零调用；大于 3 被硬上限收紧。
- retry 按真实请求数消耗 vision 预算，第 N+1 次发送前拒绝。
- qa_image_ids 过滤正确，未知 ID 明确报错。
- 锚点完全一致且 Artifact 存在时 resume 零调用。
- generated/profile/reference 任一锚点变化时重新 QA 并产生新版本。
- 多场景/多图片不串数据。

### 9.7 端到端与回归

- Mock 完整链：Storyboard → image → QA → reviewed Artifact → run_report。
- qa_decision 非 pass 时，报告明确标注未来 Stage 15 不可消费。
- 运行 Stage 13 套件及其历史回归；不得用新增豁免掩盖功能失败。
- 延续 Stage 13 的 T-6 自动复位和 T-1 账本清理纪律。

### 9.8 可选真实冒烟

`run_real_image_qa.py` 默认只体检，0 调用。真正执行至少需要：

1. 已存在且通过 SHA 校验的 Stage 13 generated manifest/image；
2. 配置真实 vision/multimodal 模型与 API key；
3. 用户明确批准；
4. `max_qa_images=1` 且 Provider retry 关闭或预算可严格证明；
5. TokenLogger 与 APIRequestCounter 独立记账；
6. 真实成本/调用数/模型/成功或失败如实写报告。

未满足时应报告阻塞，不影响 Mock 功能验收；不得用用户在 ChatGPT 网页端生成或分析的结果冒充 API 冒烟。

---

## 10. Acceptance Criteria

1. QA 默认关闭，Stage 13 既有行为和测试保持通过。
2. MultimodalProvider 多图输入，无新 Provider 协议。
3. 生成图、档案版本和参考图三类锚点均在调用前校验。
4. 始终读取 manifest 指定的精确版本，不漂移到 active version。
5. 每个非空可评分字段恰好一条合法 check；本地确定性计算 qa_decision。
6. QA Artifact 版本化不覆盖，并走既有 Review 三态。
7. approved_with_edit 的最终报告经过重新验证。
8. QA 不修改图片、Stage 13 manifest、Storyboard、ShotAsset 或 VisualAsset registry。
9. resume 锚点完整；任何 ground truth 变化都会重新 QA。
10. 预算、retry、真实/Mock 计数符合既有纪律，测试真实 API 为 0。
11. run_report 能回答：选择多少、QA 多少、pass/fail/needs_review 各多少、跳过多少、失败多少、预算使用及 Artifact 路径。
12. V0.1、core、workflow、review、Provider 基础接口和 Stage 13 image_stage 保持 0 diff。
13. 本阶段套件与所需历史回归 rc=0；任何豁免逐条留痕且仅限合法 pre-commit 护栏。
14. 文档、报告、Git 状态与实际代码一致，无图片、密钥、base64、JSONL 或临时目录泄漏。
15. 真实冒烟若未执行，必须明确写“未验证真实多图 VLM 连通性”，不得宣称真实 QA 已跑通。

---

## 11. 文档要求

完成后更新：

- `CLAUDE.md`：阶段状态、QA 契约、命令与新文件。
- `v2/docs/PROJECT_STATUS.md`：Stage 14 实际能力、测试、真实调用与遗留。
- `v2/docs/HANDOFF.md`：下一阶段入口与 QA 下游门槛。
- `v2/docs/ROADMAP.md`：Stage 14 状态、技术债、成本与 Stage 15 前置。
- `v2/logs/stage14_image_qa/stage14_report.md`：实现、测试、AC、真实成本、Git 范围和遗留。
- `v2/logs/stage14_image_qa/stage14_contract_notes.md`：A 会话的源码核对与最终裁决。

永久文档只写最终状态，不写“待 commit”之类会在提交后立刻失真的瞬时文字。

---

## 12. Git 要求

### 12.1 开工前

- Stage 13-P 必须先独立 commit。
- `git status --short` 必须干净。
- `git diff -- FilmAgent/` 必须为空。
- `v0.2-stage13` 仍指向 Stage 13 原封存 commit，不移动。

### 12.2 本阶段交付

- 执行会话不擅自 commit/tag；完成后交总控验收。
- 总控验收通过后，commit 建议：`feat(v0.2): add VLM image QA (stage14)`。
- 是否创建 `v0.2-stage14` tag 由用户明确批准；禁止 force/move 历史 tag。
- commit 前扫描 `.png/.jpg/.jpeg/.webp/.jsonl/__pycache__/tmp_*`，不得把运行图片或账本入库。

---

## 13. 发现架构问题时的处理方式

出现以下任一情况必须暂停并向总控反馈，不得擅自扩围：

- 现有 MultimodalProvider 无法表达生成图 + 多参考图。
- 必须修改 core Schema、workflow/review、Stage 13 manifest 或 image_stage 才能实现。
- manifest 无法解析到精确 VisualAssetVersion/ReferenceImage。
- 真实厂商多图协议与当前 Provider 形状冲突。
- QA 结果需要自动修改/重生成图片。
- 历史测试的失败不是 pre-commit 路径护栏。

反馈必须包含：具体文件/行、复现证据、为何当前规格不可安全执行、至少两个方案及影响范围。没有总控裁决前停止相关实现。

---

## 14. 可选方案与选择理由

### 方案 A：只看生成图，对照文本 Profile

调用简单，但浪费 Stage 10 的参考图锚点，无法做身份视觉比对。仅作为没有参考图时的降级模式。

### 方案 B：每个资产分别调用一次 VLM

证据分离清楚，但一张图会产生多次计费调用，预算迅速放大，也容易得到互相矛盾的结论。不采用。

### 方案 C：一次调用看生成图 + 全部参考图，逐字段输出

采用。一次业务调用对应一张生成图，成本和预算清楚；Prompt 可标记图片顺序，输出可由本地规则严格验证。

### 方案 D：把 QA 结果直接写回 Stage 13 manifest

会改写已生成事实记录，混合生成与质检职责，并破坏独立版本化。不采用。QA 使用独立 Artifact 和只读索引。

### 方案 E：新增 QA 生命周期/数据库

重复现有 Workflow + Artifact + Review，不采用。

---

## 15. 最终交付物

执行会话必须返回：

1. Stage 14 契约笔记。
2. ImageQAAgent/ImageQAStep、Prompt、ImageQAStage 与 pipeline 接线。
3. 严格的 ground truth resolver、SHA 校验和确定性 validator。
4. Mock 测试、端到端测试、历史回归结果和机器可读报告。
5. 可选真实冒烟脚本及实际是否执行的诚实记录。
6. Stage 14 报告与四份项目知识文档更新。
7. `git status --short`、`git diff --stat`、冻结边界检查、泄漏扫描。
8. 明确遗留、真实调用次数与成本。
9. 不 commit、不 tag，等待总控验收。

---

## 16. 执行会话启动顺序

这是中大型阶段，采用四个独立会话，依次执行：

```text
14-A 契约/架构 → 14-B 实现 → 14-C 测试/Debug → 14-D 文档/交付 → 总控验收
```

A 完成前 B 不开工；B 完成后 C；C 全绿后 D。A/B/C/D 是独立执行会话，不是并行子 Agent。
