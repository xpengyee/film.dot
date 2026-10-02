# Stage 14-B —— VLM Image QA 实现指令

> 会话角色：Stage 14-B 执行会话（实现，不承担正式测试验收与最终文档封存）
> 总控裁决：D14-REF-01，采用 Stage 14-A 方案 B
> 日期：2026-10-01
> 工作目录：`D:\AI\FilmAgent-main`
> 基线 HEAD：`1a7404942edb6e493091a449256da5a75d456ccc`
> Stage 13 tag：`v0.2-stage13` → `3895c6c103f125d458fe648d80a625bf007601af`，禁止移动

---

## 1. 会话目标

实现 Stage 14 的 VLM Image QA 主体：读取 Stage 13 生成图片 manifest，解析历史精确视觉
档案版本与参考图，完成路径/SHA256 预检，用现有 MultimodalProvider 对单张生成图及其参考图
进行一次结构化 QA，产生经过本地严格校验和确定性裁决的版本化 Artifact，并接入现有
Workflow、Review、resume、QA index 与 ProjectOrchestrator。

本会话必须完成可供 14-C 独立测试与 Debug 的实现。不得执行真实 API，不得 commit/tag，
不得宣称 Stage 14 已验收完成。

---

## 2. 开工前必须阅读

按顺序阅读：

1. `CLAUDE.md`
2. `v2/docs/PROJECT_STATUS.md`
3. `v2/docs/HANDOFF.md`
4. `v2/docs/ORCHESTRATION.md`
5. `v2/docs/stages/Stage_14.md`
6. `v2/logs/stage14_image_qa/stage14_contract_notes.md`
7. `v2/core/visual.py`
8. `v2/core/visual_registry.py`
9. `v2/core/visual_compose.py`
10. `v2/orchestration/image_stage.py`
11. `v2/orchestration/pipeline.py`
12. `v2/agents/base.py`
13. `v2/llm/providers/base.py`
14. `v2/llm/providers/openai_compatible_vision.py`
15. `v2/workflow/engine.py`、`artifact.py`、`models.py`

不得仅根据本文件猜测既有接口；若本文件与源码存在实质冲突，暂停并把文件/行号、复现证据、
两个以上方案及影响范围反馈总控。

---

## 3. 当前状态与前置阶段

- Stage 13 与 Stage 13-P 已完成代码和静态/Mock 验收，Stage 13-P 提交位于当前 HEAD。
- Stage 13 已产生 `generated_images/manifest.json` 契约，但未执行项目真实出图冒烟。
- Stage 14-A 已完成契约分析，输出：
  `v2/logs/stage14_image_qa/stage14_contract_notes.md`。
- Stage 14-A 未修改业务源码、未运行真实 API、未测试、未 commit。
- 当前工作区预期包含以下已授权未提交文档：
  - `v2/docs/PROJECT_STATUS.md`
  - `v2/docs/stages/Stage_14.md`
  - `v2/docs/stages/Stage_14B.md`
  - `v2/logs/stage14_image_qa/stage14_contract_notes.md`
- 不得清理、reset、checkout 或覆盖上述文件。若还有来源不明的业务源码改动，先暂停报告。

---

## 4. 为什么需要 Stage 14-B

Stage 13 能生成并落盘图片，但系统尚不能程序化判断图片是否符合已批准的角色、地点、道具和
风格档案。若未经 QA 直接进入视频生成，角色漂移或构图错误会进入成本更高的视频链路。

Stage 14-B 在以下位置增加只读质量门：

```text
Storyboard
  → ImageStage
  → generated image + Stage 13 manifest
  → ImageQAStage preflight
  → ImageQAStep / MultimodalProvider
  → versioned Artifact + existing Review
  → quality/qa_manifest.json
  → Stage 15 只消费 review approved-like 且 qa_decision=pass 的图片
```

---

## 5. 总控裁决 D14-REF-01

Stage 14-A 发现：独立 `reference` 资产的图片会进入 Stage 13 `reference_images`，但该资产
版本不会进入 `profile_versions`。总控采用方案 B：

1. 对 manifest 中每个 reference `image_id`，遍历 registry 全部资产与全部版本。
2. 必须全局恰好一处命中，绑定该处 `asset_id + version + ReferenceImage`。
3. 命中版本状态只能为 `approved` 或 `superseded`。
4. 禁止使用 `active_version()`、当前版本、路径、checksum 或名称猜归属。
5. 零命中或多命中必须在 Provider 调用前失败。
6. manifest sha256、Registry checksum、磁盘 SHA256 必须三方一致。
7. generated 始终是传模 Image 0；reference 顺序严格保持 manifest 原始顺序。
8. 不修改 Stage 13 manifest、`image_stage.py` 或任何 core 文件。

方案 C（Stage 13-P2 扩充 provenance）当前不立项。未来若要求 manifest 自包含，再单独设计
schema 版本和迁移。

---

## 6. 本阶段实现范围

### 6.1 新增文件

```text
v2/agents/image_qa.py
v2/prompts/v0.2/image_qa.txt
v2/orchestration/image_qa_stage.py
```

### 6.2 允许最小修改

```text
v2/agents/__init__.py
v2/orchestration/__init__.py
v2/orchestration/pipeline.py
```

`v2/orchestration/state.py` 原则上不改。QA 进度由 Artifact/Review 与独立 QA index 表达；只有
源码证明确实无法完成时才暂停报告，不能先改。

### 6.3 本会话不创建/不完成

以下留给 14-C/14-D：

```text
v2/logs/stage14_image_qa/run_tests.py
v2/logs/stage14_image_qa/test_report.json
v2/logs/stage14_image_qa/run_real_image_qa.py
v2/logs/stage14_image_qa/stage14_report.md
项目知识文档的最终完成态更新
```

允许 14-B 做编译/import/最小 Mock 自检，但不得用它代替 14-C 测试矩阵。

### 6.4 禁止修改

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
历史测试与历史断言
```

不得新增第三套 Review、Artifact、状态机、请求计数器或网络客户端。

---

## 7. 输入契约

### 7.1 run_spec

```text
run_image_qa   bool，默认 False
qa_image_ids   可选 image_id 列表；缺省/空 = manifest 中全部 generated 条目
max_qa_images  int，默认 3；0 = 零调用；负数非法
qa_resume      bool，默认 True
```

选择上限为 `min(max_qa_images, MAX_REAL_VISION_REQUESTS)`。显式 ID 保持用户顺序，拒绝重复、
未知或非 generated ID；缺省时保持 Stage 13 manifest 顺序。

### 7.2 每张图的进程内输入

`context.inputs["image_qa_input"]` 至少为：

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
      "asset_id": "REF_001",
      "version": "v1"
    }
  ]
}
```

绝对路径只允许存在于进程内，不能写入 Artifact、QA index、日志或报告。

---

## 8. Ground truth 与文件预检

在构造真实 Provider 或发出调用前完成：

1. 校验 manifest 顶层结构、版本、project_id、images。
2. 仅选择 `status == "generated"` 且具备 image_id/shot_ref/path/sha256 的条目。
3. `profile_versions` 必须是非空 mapping，保持其序列化顺序。
4. 每个精确版本通过 `registry.get(asset_id)` + `asset.get_version(version)` 获取；只接受
   `approved|superseded`，禁止 active fallback。
5. Profile 字段按 `PROFILE_CLASSES[kind].FIELD_ORDER` 顺序展开。
6. 对所有 registry 版本构造 `image_id -> matches[]` 全局索引；每个 manifest reference
   必须恰好一处命中，并保留 manifest 顺序。
7. 统一安全路径解析：相对路径锚定 project root，绝对路径也必须解析后位于 project root；
   拒绝 `..`、盘符/UNC 逃逸、目录、缺失文件以及 symlink/junction 指向项目外。
8. generated：manifest sha256 == 磁盘 sha256。
9. reference：manifest sha256 == Registry checksum == 磁盘 sha256。
10. sha256 必须为 64 位十六进制，比较时规范为 lowercase。

不得因 reference 失败而降级为 `profile_only`。

---

## 9. ImageQAAgent 与确定性 Validator

### 9.1 Agent

在 `agents/image_qa.py` 实现：

- `ImageQAAgent`：使用现有 `MultimodalProvider.chat(messages, images=tuple(...))`。
- `ImageQAStep`：每次只处理一张 generated image，`needs_review=True`。
- Artifact base name：`image_qa_<image_id.lower()>`。
- 可以继承现有 `LLMAgent/LLMStep` 骨架，但不得修改骨架。
- Provider images 唯一合法顺序：generated path 第一，其后为 manifest reference 顺序。
- Prompt 只出现 Image 位置、image_id、asset_id/version 和预期字段；不得出现绝对路径、
  API key、base64 或完整运行环境信息。

### 9.2 期望检查集合

期望 key 为 `(asset_id, version, field)`：

- 依 manifest `profile_versions` 顺序遍历精确 Profile；单个 profile 依 `FIELD_ORDER`。
- 非空字段各生成且只生成一个 check。
- `notes` 仅作上下文，不生成 check。
- `negative_cues` 生成一项逆语义检查；`match` 表示禁止项未出现。
- 空字段不送审、不补值。
- 至少存在一个 expected check，否则预检失败、0 Provider 调用。

### 9.3 Provider 原始输出

只接受：

```json
{
  "checks": [
    {
      "asset_id": "CHAR_001",
      "version": "v1",
      "field": "hair",
      "expected": "black bob",
      "verdict": "match",
      "evidence": "short observation"
    }
  ],
  "summary": ""
}
```

合法 verdict：`match | mismatch | uncertain | not_visible`。

严格校验：

- 顶层/每项类型正确。
- check key 不重复。
- 实际 key 集合与 expected key 集合完全相等。
- `expected` 必须等于规范化后的 Profile 精确值，模型不得改写真值。
- 非 match verdict 的 evidence 必须非空。
- 未知、缺失、额外、重复字段或非法 verdict 全部抛 `StepError`，允许既有 Workflow
  在预算内重试；禁止静默补齐或丢弃。

### 9.4 本地总判定

```text
任一 mismatch                    → fail
否则任一 uncertain/not_visible  → needs_review
否则全部 match                  → pass
```

忽略模型自报的 `qa_decision/provider/model/request_id/retry_count/created_at`。这些字段由本地
规则和 ProviderResult 注入。

---

## 10. 最终 Artifact 与 Review

最终 Artifact 至少为：

```json
{
  "schema_version": "1",
  "image_id": "GEN_001",
  "shot_ref": "S1_B1_SH1",
  "generated_sha256": "<64 hex>",
  "ground_truth_mode": "profile_only|profile_and_references",
  "profile_versions": {"CHAR_001": "v1"},
  "reference_images": [{"image_id": "IMG_001", "sha256": "<64 hex>"}],
  "checks": [],
  "summary": "",
  "qa_decision": "pass|fail|needs_review",
  "provider": "",
  "model": "",
  "request_id": "",
  "retry_count": 0,
  "created_at": ""
}
```

- 不持久化 reference 的 asset/version/path；其归属只作为本次解析与 prompt 的进程内信息。
- 每张图一个 Workflow run 和版本化 Artifact，不覆盖旧版本。
- Review 继续使用 `approved / approved_with_edit / rejected`。
- Workflow 完成后必须重新读取最终批准的 Artifact，用同一个严格 validator 二次验证，并重新
  计算 `qa_decision`。编辑后非法则该项失败，不得写成功 QA index。
- 人工 Review 不得把 QA `fail` 改写成 `pass`。

---

## 11. QA index 与 resume

使用项目内独立索引：

```text
<project_dir>/quality/qa_manifest.json
```

索引只保存审计与 resume 锚点，不复制完整 checks，不修改 Stage 13 manifest。写入必须原子化，
复用既有 `atomic_write_json`，不能新增第二套写文件机制。

每项至少保存：image_id/shot_ref、generated 相对路径与 sha、精确 profile_versions、有序
reference image_id+sha、最终 Artifact 相对路径+sha、Workflow run/status、Review id/decision、
qa_decision、provider/model、updated_at、downstream_eligible。

`downstream_eligible` 仅在 Review approved-like 且 `qa_decision == "pass"` 时为 true。

resume 只有以下锚点全部一致才跳过：

1. generated path/sha 与磁盘重验一致；
2. profile_versions 完全一致；
3. reference 列表内容和顺序完全一致；
4. 最终 Artifact 存在、项目内、sha 一致；
5. Workflow/Review 表明最终 Artifact 已批准；
6. Artifact 通过当前严格 validator，qa_decision 重算一致；
7. `provider + model` 与当前配置完全一致。

命中 resume 时不得构造默认真实 Provider、不得消费预算。任一锚点变化时重跑并生成 Artifact
新版本。`qa_resume=false` 强制重跑，但仍不得覆盖历史 Artifact。

---

## 12. Pipeline 接线

顺序：

```text
casting → scenes → visual_assets → image → image_qa → completeness
```

要求：

1. `run_image_qa=False` 时零 QA Provider 调用、零 QA Artifact、零 index 变化，Stage 13 行为
   保持不变。
2. 支持 `generate_images=False`，直接读取已有 Stage 13 manifest/image 执行 QA。
3. QA Provider 惰性构造：完成 manifest/选择/resume 后，确有未完成项时才构造。
4. 构造器允许注入 `qa_provider`，测试使用 Mock/spy；不得自动切换模型或 fallback。
5. 真实 Provider 必须满足 MultimodalProvider 公共调用面，且 `budget_kind == "vision"`；其他
   budget kind 在请求前失败。Mock/spy 可以不具备真实预算桶。
6. 同一 run 只设置一次 vision 上限，Provider retry 与 Workflow retry 共用，不得逐图重置计数。
7. run_report 增加 image_qa 段：selected、resumed、attempted、pass、needs_review、fail、
   rejected、failed、budget_consumed、downstream_eligible、artifact paths。
8. 不改变 completeness 的既有定义，不把 QA 结果写入 state.py 第二事实源。

全局 manifest/选择/配置错误在 Provider 构造前终止。单图预检或执行失败保留失败记录，不删除
图片；若预算耗尽，停止剩余真实请求，绝不发送第 N+1 次。

---

## 13. 模型授权与真实调用

本会话真实 API 调用必须为 **0**。

任何未来真实运行只允许使用用户本次明确批准的精确 `provider + model`。切换模型，包括自动
fallback、重试换模、别名/版本变化，必须先报告原模型、目标模型、原因、免费额度是否确认、
预计调用次数与费用；用户明确同意前目标模型保持 0 次调用。API Key 可复用不代表模型获批。

不得在代码中实现未经批准的自动模型选择或静默 fallback。

---

## 14. 异常处理

- manifest/registry/path/SHA/选择错误：Provider 调用前失败。
- 非 Multimodal 调用面或真实 budget kind 错误：调用前失败。
- `APIBudgetExceededError`：非重试，停止后续真实请求。
- 其他 `LLMError`：按既有 retryable 语义转换成 `StepError`，不得吞错。
- 非法 JSON/Schema：`StepError`，由 Workflow 在相同预算内有限重试。
- rejected 或 QA fail：保留 Artifact/图片，不写 downstream eligible。
- `approved_with_edit`：最终 Artifact 必须二次严格校验。
- 日志/异常/Artifact 禁止包含 API key、base64、绝对路径和完整 prompt。

---

## 15. 开发顺序

1. 开工检查：记录 HEAD、tag、status、冻结边界；保护现有文档改动。
2. 实现纯函数：sha/path、global image_id resolver、expected checks、严格 validator、
   qa_decision。
3. 实现 Prompt、ImageQAAgent、ImageQAStep。
4. 实现 ImageQAStage：选择、预检、Workflow/Review、index、resume、报告。
5. 接入 `agents/__init__.py`、`orchestration/__init__.py`。
6. 最后最小修改 `pipeline.py`，确保默认关闭和惰性 Provider。
7. 运行编译/import/最小 Mock 自检，确认 0 网络、0 真实 API。
8. 检查 diff 和禁改边界，提交 14-B 完成报告给总控；不 commit/tag。

纯函数与 Prompt 可先分别实现；Pipeline 接线必须在 Agent/Stage 接口稳定后进行。

---

## 16. 14-B 自检要求

本会话至少完成：

- 新增模块 `py_compile`/import 成功。
- 用注入 Mock/spy 完成一张生成图 + 多参考图的最小内存/临时目录自检。
- 证明送模 tuple 顺序为 generated → manifest refs。
- 证明 D14-REF-01 的独立 REF 可解析；零命中/多命中在调用前失败。
- 证明全 match/mismatch/uncertain 三种本地裁决正确。
- 证明 `run_image_qa=False` 不构造/调用 QA Provider。
- 证明未发真实网络请求，真实 API 计数为 0。

14-C 仍须创建正式测试套件并完成 Stage_14.md §9 的完整矩阵与历史回归。

---

## 17. Acceptance Criteria

1. 新增 ImageQAAgent/ImageQAStep/ImageQAStage 并完成默认关闭的 pipeline 接线。
2. D14-REF-01 已实现：全局 image_id 恰好一次命中，独立 REF 可解析且无猜测。
3. 所有 generated/reference 路径与 SHA 在调用前验证。
4. exact profile version 不漂移，字段顺序来自 `FIELD_ORDER`。
5. 图片顺序固定且可观察：generated 第一，refs 完全按 manifest。
6. 输出 Schema 严格，qa_decision 只由本地规则计算。
7. 每图独立 Artifact/Review，approved_with_edit 后二次验证。
8. QA index 原子写，resume 锚点含顺序、Artifact hash、provider+model。
9. Stage 15 门槛可由 index 明确判断，且只有 pass + approved-like 才 eligible。
10. 默认关闭时 Stage 13 行为保持，真实 API 为 0。
11. 禁改文件 0 diff；未新增状态机、网络层、Provider 公共接口或请求计数器。
12. 无真实媒体、密钥、base64、JSONL、缓存或临时目录进入版本库。
13. 不 commit、不 tag，`v0.2-stage13` 未移动。

---

## 18. Git 与并发工作区要求

- 不执行 `git reset`、`git checkout --`、`git clean` 或任何会覆盖他人修改的操作。
- 不修改/删除 14-A 笔记和总控已更新的两份文档。
- 开工和结束都输出：
  - `git status --short`
  - `git diff --stat`
  - `git diff -- FilmAgent/`
  - `git rev-parse v0.2-stage13^{commit}`
- 若测试/自检重写历史报告，必须只精确恢复运行产生的噪声，不能整目录回退。
- 不 commit、不 tag；等待 14-C、14-D 和总控最终验收。

---

## 19. 发现架构问题时

出现以下情况立即暂停：

- 必须修改禁改文件才能完成；
- MultimodalProvider 无法保持多图顺序；
- Workflow/Review 无法取得最终 edited Artifact；
- QA index 会成为第二状态机；
- registry 全局反查无法确定唯一 reference；
- pipeline 默认关闭仍产生调用/文件；
- 需要新增/切换真实模型或扩大 API 调用范围；
- 历史行为变化或出现无法解释的并发修改。

反馈必须包含：文件/行号、最小复现、已经验证的事实、至少两个解决方案、兼容/测试/成本影响。
总控裁决前不得扩大范围。

---

## 20. 最终返回给总控的内容

1. 实际新增/修改文件列表。
2. Agent、Validator、resolver、Stage、pipeline 的实现摘要。
3. D14-REF-01 如何落地及零/多命中行为。
4. 自检命令、结果及真实 API=0 的证据。
5. 已知限制和需要 14-C 覆盖的风险。
6. `git status --short`、`git diff --stat`、冻结边界和 tag 结果。
7. 明确声明：未 commit、未 tag、未运行真实 API、未自动切换模型。
