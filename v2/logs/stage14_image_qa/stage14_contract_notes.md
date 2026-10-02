# Stage 14-A：Image QA 源码契约核对

> 会话性质：分析与架构设计，不含业务实现。
> 核对日期：2026-10-01
> 基线 HEAD：`1a7404942edb6e493091a449256da5a75d456ccc`
> Stage 13 tag：`v0.2-stage13` → `3895c6c103f125d458fe648d80a625bf007601af`（未移动）

## 1. 结论摘要

Stage 14 的主体可以在既有公共契约内落地：新增独立 Image QA agent/step/orchestration，复用 `MultimodalProvider.chat()`、Workflow/ArtifactStore/ReviewManager、Stage 13 manifest 与 VisualRegistry；不需要修改 core、workflow、review、Provider 公共接口或 `state.py`。

源码核对发现一个契约缺口：Stage 13 允许把独立 `reference` 资产加入 `ComposedVisualInput.reference_images`，而 manifest 的 `profile_versions` 不记录该 reference 资产及其版本。按 Stage 14 原“每张 reference 必须在上述精确版本中找到”的算法，这类合法 Stage 13 manifest 无法完成精确版本解析。证据、影响和三个方案见第 11 节。总控已于 2026-10-01 作出 D14-REF-01 裁决，采用方案 B：按全局 `image_id` 恰好一次命中做 Registry 反查；Stage 14-B 可据此开工。

除该冲突外，本笔记给出可直接实施的确定性契约：输入解析、路径与 SHA256 校验、多图顺序、字段检查、输出 Schema、总判定、Artifact/Review、resume、pipeline 接线、测试矩阵和真实冒烟前置。

## 2. 开工基线与边界

### 2.1 基线确认

在进入 Stage 14-A 前已完成并确认：

1. Stage 13-P 已单独提交：
   - `4d9a56e56ee59f176f0e0a8b2b43a326dded8df9` — `docs: fix stage13 closeout wording + record stage13-P`
   - `1a7404942edb6e493091a449256da5a75d456ccc` — `feat(v0.2): bailian native image provider (stage13-P)`
2. 两个 commit message 都显式记录：`v0.2-stage13` 所在 commit 仍写 pending，tag 未移动。
3. 开工时 `git status --short` 为空。
4. `git diff -- FilmAgent/` 为空。
5. `v0.2-stage13^{commit}` 仍为 `3895c6c103f125d458fe648d80a625bf007601af`。

本笔记写入前，工作区又出现 `v2/docs/PROJECT_STATUS.md` 与 `v2/docs/stages/Stage_14.md` 的并发未提交修改；内容仅新增真实调用的精确 `provider + model` 授权与换模再授权规则。本会话没有清理、覆盖或提交这些修改，并已将该规则纳入第 10 节。

### 2.2 本阶段禁改边界

Stage 14-B 必须保持以下文件/公共接口不变：

- `v2/core/visual.py`
- `v2/core/visual_registry.py`
- `v2/workflow/**`
- Review 公共接口
- `v2/llm/providers/base.py`
- `v2/llm/providers/openai_compatible_vision.py`
- `v2/llm/request_counter.py`
- Stage 13 manifest schema
- `v2/orchestration/image_stage.py`

允许新增 Image QA 自有模块、测试、mock/spy、日志目录，并对 `pipeline.py` 做 Stage 14 文档已批准的可选接线；默认关闭时必须不改变 Stage 13 行为。

## 3. 已核对的现有事实

### 3.1 VisualRegistry 与版本

- `VisualRegistry.get(asset_id)` 是精确资产查找；`VisualAsset.get_version(version)` 是精确版本查找。
- profile 类型由 `PROFILE_CLASSES` 管理，各类型检查顺序由对应类的 `FIELD_ORDER` 决定。
- 版本审核通过时，先前 approved 版本可能变为 superseded；因此 Stage 14 允许的精确版本状态是 `approved` 或 `superseded`，禁止退回 `active_version`。
- `ReferenceImage.image_id` 由 registry 级 `_next_image_number` 全局递增分配，不是资产内局部编号。
- reference 在 registry 中保存 `image_id/path/checksum`；`add_reference_image()` 创建时计算 checksum。

### 3.2 Stage 13 manifest

generated 项目包含至少：

- `image_id`
- `shot_ref`
- `path`
- `sha256`
- `profile_versions`
- `reference_images`
- 生成状态及 Provider 元数据

`ImageStage._profile_versions()` 仅从 character sections、location、props、style 汇总 `asset_id -> version`；`_reference_images()` 仅输出 composed refs 的 `image_id/sha256`。generated 路径按 project 相对路径写入。

### 3.3 MultimodalProvider 与预算

- 公共调用面已经足够：`chat(messages, *, images=tuple(...), step=..., agent=...)`。
- `OpenAICompatibleVisionProvider._attach_images()` 以 list comprehension 遍历传入 tuple，再把图像 part 附加到最后一条 user message，故调用方给出的 tuple 顺序会被保留。
- 每一次真实 HTTP attempt 都在 `_invoke()` 内消费 request counter；Provider 内部 retry 也逐 attempt 计数。
- `OpenAICompatibleVisionProvider.budget_kind == "vision"`。
- `OpenAICompatibleMultimodalProvider.budget_kind == "multimodal"`，而现有 request counter 默认预算只显式包含 vision/image；Stage 14 不得假设所有 MultimodalProvider 都使用 vision 桶。
- `MAX_REAL_VISION_REQUESTS == 3`。

### 3.4 Workflow、Artifact 与 Review

- Workflow engine 先保存 step 产物，再调用 `step.validate()`。
- `approved_with_edit` 会把编辑内容保存成新的 artifact 版本，但 engine 不会再次调用 `step.validate()`。
- 因而 Stage 14 orchestration 必须在 review 完成后，读取最终被批准的 artifact 并再次执行同一个本地 Schema/一致性校验器；不能把“review 已批准”等同于“artifact 合法”。
- ArtifactStore 支持同一 base name 的 `_vN` 版本，适合 resume 失效后重跑而不覆盖历史产物。

## 4. manifest 到精确 VisualAssetVersion

### 4.1 选择集合先固定

在构造 Provider 或发出任何调用前完成：

1. 读取 manifest 并校验顶层类型、版本、`project_id`、`images`。
2. 仅把 Stage 13 成功 generated 的图像作为候选；不得把 failed/skipped/rejected 条目送 QA。
3. 若配置 `qa_image_ids`：保持用户给定次序，拒绝重复 ID，任一未知或非 generated ID 都 fail-fast。
4. 未配置 `qa_image_ids`：保持 manifest `images` 原始顺序。
5. 最终选择数量为 `min(max_qa_images, MAX_REAL_VISION_REQUESTS)`；`max_qa_images < 0` 为配置错误，`0` 表示不调用。
6. 选择、manifest 校验、resume 判定全部发生在 Provider 调用之前。

### 4.2 精确版本解析

对每个 selected image：

1. `profile_versions` 必须是非空 mapping；asset_id 与 version 都必须是非空字符串，version 沿用 registry 的规范标识（例如 `v1`），不得自行转成整数。
2. 对每一对 `(asset_id, version)`：
   - `registry.get(asset_id)`，不存在即失败；
   - `asset.get_version(version)`，不存在即失败；
   - `version.status` 只允许 `approved`/`superseded`；
   - `version.profile` 必须存在，且资产 kind 与 profile 类型一致；
   - 禁止读取或替换为 `asset.active_version`。
3. 检查对象的稳定次序：按 manifest `profile_versions` 的序列化顺序；单个 profile 内按其类的 `FIELD_ORDER`。实现不得使用 set 或重新按 registry 顺序排序。

manifest 作为历史事实记录精确版本；即使资产后来出现新 approved 版本，也不得漂移。

### 4.3 reference 归属

当前 Stage 14 文档要求：manifest 中每个 reference `image_id` 都必须在 4.2 的精确版本集合内找到，且只能找到一次。建议解析器建立：

```text
(asset_id, version) -> VisualAssetVersion
image_id -> (asset_id, version, ReferenceImage)
```

然后依 manifest `reference_images` 原始顺序逐项查表。重复 image_id、零匹配或多匹配都失败，不得依赖 active version 或“第一张同名图片”。

注意：独立 reference 资产无法满足这条规则，见第 11 节；在裁决前不得擅自改为 registry 全局搜索。

## 5. 路径与 SHA256 验证

所有文件校验必须在 Provider 调用前完成；任一失败时该图像不得产生真实请求。

### 5.1 统一路径解析器

以可信的 `project_root.resolve()` 为锚：

- manifest/registry 路径为相对路径时：`project_root / value` 后 `resolve(strict=True)`；
- 若为绝对路径，也必须 `resolve(strict=True)`；
- resolved path 必须通过 `is_relative_to(project_root_resolved)`（或等价安全实现）；
- 拒绝 `..` 逃逸、盘符逃逸、UNC/网络路径、目录、缺失文件，以及通过 symlink/junction 解析到项目外的文件；
- 日志、artifact 和 qa manifest 只保存项目相对 POSIX 风格路径，不回写绝对路径。

禁止用简单字符串前缀判断 containment。

### 5.2 generated 图片

1. `path` 与 `sha256` 均必须存在。
2. sha 必须是规范化的 64 位十六进制值（比较时可 lowercase）。
3. 流式计算磁盘文件 SHA256。
4. 必须满足 `disk_sha == manifest.sha256`。

### 5.3 reference 图片

对 manifest 的每一项 reference：

1. 通过第 4 节得到精确 `ReferenceImage`。
2. 解析 `ReferenceImage.path` 并执行项目 containment 校验。
3. 流式计算磁盘 SHA256。
4. 三方必须一致：

```text
manifest reference sha256
== ReferenceImage.checksum
== actual disk sha256
```

缺字段、非法 sha、路径越界、文件缺失、任一 hash 不一致均为输入契约失败；不可降级为无 reference 的 QA。

### 5.4 送模图片顺序

唯一合法顺序：

```text
images = (
  generated_image_path,
  reference_0_path,
  reference_1_path,
  ...  # 完全依 manifest reference_images 顺序
)
```

不得按 asset_id/image_id/path 排序，不得 dedupe 后改变位置。若 manifest 出现重复 reference image_id，直接拒绝，而不是静默去重。

prompt 中显式标注 `Image 0 = generated`，后续按位置列出 reference 的 `image_id`、所属 `asset_id/version`；不放绝对路径。

## 6. QA 字段、Schema 与确定性总判定

### 6.1 字段集合

期望检查集合完全由解析后的精确 profile 构造：

```text
expected_key = (asset_id, version, field)
```

- 遍历 `PROFILE_CLASSES[kind].FIELD_ORDER`。
- `notes` 只作为上下文，不产生 check；这里特指 profile 的 notes 字段，不把 `VisualAssetVersion.notes` 当视觉真值。
- `negative_cues` 只产生一个逆语义检查：`match` 表示生成图中未出现任何禁止线索。
- 其余**非空**字段必须各产生且只产生一个 check。
- 空字段不送审、不产生 check，不能让模型猜测；approved/superseded 版本若违反 VisualRegistry 已有审批约束，则按 registry 数据错误处理，而不是在 QA 层另造一套 required-field 规则。
- 至少必须有一个 expected check。

### 6.2 Provider 原始响应契约

模型只负责返回内容判断，建议最小 JSON 为：

```json
{
  "checks": [
    {
      "asset_id": "CHAR_001",
      "version": "v2",
      "field": "appearance",
      "expected": "...",
      "verdict": "match",
      "evidence": "..."
    }
  ],
  "summary": "..."
}
```

允许 verdict 仅为：`match | mismatch | uncertain | not_visible`。若模型返回 `qa_decision`，本地必须忽略并重算；provider/model/request_id/retry_count/created_at 也必须由本地可信来源填充，不能采信模型声称值。

### 6.3 严格 Schema/完整性校验

本地 validator 必须校验：

- 顶层仅接受约定结构与类型；`checks` 为非空 list，`summary` 为 string。
- 每个 check 的 `asset_id/version/field/expected/verdict/evidence` 类型正确。
- `(asset_id, version, field)` 不重复。
- 实际 key 集合与 expected key 集合完全相等：缺失、额外、重复一律失败。
- `expected` 与 profile 中规范化后的精确值相等，禁止模型改写真值。
- verdict 必须在枚举内。
- `mismatch`、`uncertain`、`not_visible` 的 evidence 必须为非空可读字符串；`match` 可允许空 evidence，但建议 prompt 要求简短证据。
- `negative_cues` 的 expected 保留原值，validator/prompt 明确其逆语义，不能把“看到了禁止项”判为 match。

不得因为模型输出缺字段就本地补齐为 match，也不得丢弃 extra checks 后继续。

### 6.4 最终 Artifact Schema

最终 artifact 应由本地组装，至少包含：

```json
{
  "schema_version": "1",
  "image_id": "...",
  "shot_ref": "...",
  "generated_sha256": "...",
  "ground_truth_mode": "profile_only",
  "profile_versions": {"ASSET_ID": "v1"},
  "reference_images": [
    {
      "image_id": "IMG_001",
      "sha256": "..."
    }
  ],
  "checks": [],
  "summary": "...",
  "qa_decision": "pass",
  "provider": "...",
  "model": "...",
  "request_id": "...",
  "retry_count": 0,
  "created_at": "..."
}
```

`ground_truth_mode` 只能为 `profile_only` 或 `profile_and_references`，由 reference 列表是否为空本地决定。artifact 严格沿用 Stage_14.md §5.3，不额外持久化 generated/reference 路径、asset 归属或其他进程内字段；并禁止 API key、base64、完整 prompt、绝对路径。

### 6.5 本地唯一总判定

严格按以下优先级计算：

1. 任一 check 为 `mismatch` → `fail`
2. 否则任一 check 为 `uncertain` 或 `not_visible` → `needs_review`
3. 否则全部为 `match` → `pass`

这一定义与人工 Review 决策完全分离：

- QA decision 描述图像与视觉资产契约是否一致；
- Review decision 描述人是否批准当前 artifact；
- 人工 `approved` 不得把 QA `fail` 改成 `pass`；
- `approved_with_edit` 可以改 checks/summary，但最终 artifact 必须重验并由本地重新计算 QA decision。

## 7. Agent、Workflow、Artifact 与 Review 方案

### 7.1 每张图一个 Workflow run

- Step/Agent 一次只处理一张 generated image。
- artifact base name：`image_qa_<image_id.lower()>`。
- `needs_review = True`，Review 保留 `approved / approved_with_edit / rejected` 三态。
- 不把多张图合并进一个 Workflow run，避免单项 review、retry、resume 和预算边界混杂。

### 7.2 错误分类

- 输入/manifest/registry/path/SHA/Schema 错误：非重试错误；0 Provider 调用（若发生在调用前）。
- Provider 短暂传输错误：遵循既有 LLMError retryable 语义转换为 StepError。
- API budget exceeded：非重试 StepError，禁止 Workflow 再发业务请求。
- 模型返回非法 JSON/Schema：可以按 Stage 14 约定的有限 Workflow retry；每次重跑都受同一 request counter 硬上限保护。

不要修改 Workflow engine 的通用异常策略；Stage 14 step 自己完成 LLMError/APIBudgetExceeded 到 StepError 的准确翻译。

### 7.3 `approved_with_edit` 二次校验

Workflow 返回后 orchestration 必须：

1. 找到最终批准的 artifact 版本，而不是初始版本。
2. 重新运行与 Step 相同的严格 validator。
3. 重新计算 `qa_decision`，并校验本地来源的锚点/元数据未被编辑破坏。
4. 只有二次校验成功，才写入可消费的 qa manifest/index。

编辑后不合法时，该 run 记失败并停止该项，不得写成功镜像；无需修改 engine。

### 7.4 QA manifest/index 镜像

允许在 Stage 14 自有目录写独立 `quality/qa_manifest.json`（名称以 Stage_14.md 为准），每项保存：

- image_id/shot_ref；
- generated path + sha；
- 精确 profile_versions；
- 有序 reference image_id + sha；
- 最终 artifact 相对路径 + sha；
- workflow run id/status、review id/decision；
- qa_decision；
- provider/model；
- updated_at。

镜像只做索引和 resume 锚点，不复制完整 checks，不修改 Stage 13 manifest，不成为新的视觉资产真值源。

下游可消费条件必须同时满足：`review approved-like` 且 `qa_decision == pass`。`needs_review`、`fail` 或 rejected 均不可标为 downstream eligible。

## 8. resume 契约

`qa_resume` 默认 `true`。对每张 selected image 独立判断，只有以下条件全部满足才 skip：

1. qa index 存在该 image_id 且结构合法。
2. 当前 generated path/sha 与 index 完全相等，磁盘 sha 也已重验。
3. 当前 `profile_versions` mapping 完全相等。
4. 当前 reference 列表逐项相等，包括顺序；因为顺序改变会改变多模态输入语义。
5. 最终 artifact 文件存在、位于项目内，artifact sha 与 index 相等。
6. 关联 Workflow/Review 表明最终 artifact 已批准。
7. 读取最终 artifact 后通过当前严格 validator，且 qa_decision 重算一致。
8. provider/model 若被定义为 resume 身份的一部分，应按 Stage_14.md 固定策略比较；至少 model 配置变化不能静默复用无法追溯的结果。

命中 resume 时不得构造真实 Provider 或消费 budget。任一锚点变化则重跑该 image，ArtifactStore 自然生成 `_vN`，保留旧产物。损坏的 index/artifact 不能当作 skip；对明显结构损坏建议 fail-loud，对合法但 stale 的记录重跑。

`qa_resume=false` 时对 selected images 全部重跑，但仍不得覆盖历史 artifact。

## 9. pipeline 接线

建议接线位置：

```text
casting -> scenes -> visual_assets -> image -> image_qa -> completeness
```

契约如下：

- 新增配置 `run_image_qa`，默认 `false`。
- `max_qa_images` 默认 `3`，并受 `MAX_REAL_VISION_REQUESTS` 二次封顶。
- `qa_resume` 默认 `true`。
- QA Provider 必须惰性构造：只有 `run_image_qa=true` 且至少一个未 resume 的 selected image 时才创建。
- 支持 `generate_images=false` 但读取已有通过 SHA 验证的 Stage 13 manifest/image 执行 QA。
- 默认关闭时，Stage 13 的构造、输出、状态与报告必须保持原行为。
- QA 状态写入 Stage 14 自有 index 和 pipeline run report；无需给 `state.py` 增加公共字段。
- completeness 的既有定义不因 QA 改写；run report 可新增 image_qa 统计，但不能偷换原阶段成功语义。

建议报告至少包含 selected、resumed/skipped、attempted、pass、needs_review、fail、rejected、failed、budget_consumed、artifact paths、downstream eligible 数量。

### 9.1 Provider 实例与预算桶

传入对象必须满足 `MultimodalProvider` 公共契约。OpenAI-compatible 的 vision 类本身已实现该接口，不必改 base。

Stage_14.md 已决定把本次上限下推到真实 Provider 的 `vision` 预算桶。因此真实 Stage 14 Provider 必须使用 `budget_kind == "vision"`：

- `OpenAICompatibleVisionProvider` 同时实现 MultimodalProvider 契约并使用 `vision`，满足要求；
- `OpenAICompatibleMultimodalProvider` 使用 `multimodal`，不满足当前 Stage 14 的预算桶决定，不能被真实 QA 静默采用。

Stage 14 orchestration 在调用前验证真实 Provider 的 budget kind 为 vision，并对 `vision` 设置本次上限；其他桶 fail-fast，若未来要支持必须先由总控修改 Stage 14 决策。不得为此修改 request_counter。Provider 内部 retry 和 Workflow 外部 retry 都由同一个 vision 桶累计，不能每张图重置全局计数来规避上限。Mock/spy 可不走真实预算，但仍受 selected image 数量上限。

## 10. 测试矩阵与真实冒烟前置

### 10.1 默认 Mock/单元测试（0 网络、0 API）

至少覆盖：

1. manifest 候选筛选、顺序、显式 qa_image_ids、未知/重复 ID。
2. exact version：approved、superseded 可用；active fallback 禁止；missing/invalid status 失败。
3. 每种 profile 的 FIELD_ORDER；notes 排除；negative_cues 单项逆语义。
4. generated 路径：正常、缺失、目录、`..`、绝对越界、symlink/junction 逃逸。
5. generated sha：正确、格式错、内容错。
6. reference 三方 sha：全等及三种不等组合。
7. 多图 tuple：generated 第一、refs 严格 manifest 顺序；用自定义 spy Provider 记录实际路径值。现有 Mock 只记录图像数量，不足以证明顺序。
8. 无 refs → profile_only；有 refs → profile_and_references。
9. checks：完整、缺失、额外、重复、expected 被改写、非法 verdict、evidence 缺失。
10. 总判定优先级：mismatch 压过 uncertain/not_visible；后两者压过全 match。
11. 模型伪造 qa_decision/provider/model/request_id 时本地覆盖。
12. artifact base name、版本递增、无绝对路径/敏感数据。
13. Review 三态，尤其 approved_with_edit 后二次校验成功/失败。
14. QA decision 与 Review decision 正交矩阵。
15. resume 全命中 0 provider calls；generated/profile/ref 顺序/artifact hash 任一变化触发单项重跑。
16. qa_resume=false 强制新 artifact 版本。
17. run_image_qa=false 不构造 QA Provider，Stage 13 行为回归不变。
18. max_qa_images 为 0、负数、1、3、>3；选择上限正确。
19. budget 桶 vision/multimodal、内部 retry、Workflow retry、超限后不再调用。
20. 非法输入在 Provider 前失败且 spy call count 为 0。
21. LLMError retryable 映射与 APIBudgetExceeded 非重试映射。
22. qa index 损坏、stale、artifact 丢失、review 未批准。
23. pipeline 可在不重新生成图片时对既有 manifest 执行 QA。
24. 独立 reference 资产冲突的冻结测试：在总控选择方案前标为预期 fail/阻断，不能悄悄改变算法。

### 10.2 真实冒烟前置

真实调用必须同时满足：

1. 已存在且通过路径/SHA 校验的 Stage 13 generated manifest/image。
2. 已配置真实 vision/multimodal Provider 与 API key。
3. 用户明确批准本次精确 `provider + model`；既有 key 或其他模型的历史批准不构成授权。
4. `max_qa_images=1`。
5. Provider retry 关闭，或能严格证明所有 attempt 仍受硬预算限制。
6. 本次实际 budget kind 已设置并确认余额。
7. TokenLogger 与 APIRequestCounter 独立记账。
8. 真实报告如实记录 provider、model、调用/attempt 数、成功或失败及成本。

若需要切换模型（含自动 fallback、重试改模、版本别名变化），必须先报告原模型、目标模型、原因、目标模型免费额度是否已确认、预计调用次数与费用；额度未知按可能计费处理。用户明确同意前，对目标模型保持 0 调用。Provider 不得静默 fallback 到未授权模型。

本 A 会话不运行真实 API。

## 11. 必须上报总控的冲突

### 11.1 证据链

1. `v2/core/visual_compose.py:184-189`：shot asset 的 `kind == "reference"` 会把该独立 reference 资产的图片加入 refs。
2. `v2/core/visual_compose.py:191-197`：`extra_refs` 也会把独立 REF 资产图片加入 refs。
3. `v2/core/visual_compose.py:216`：这些 refs 经按 image_id 保序去重后写入 `ComposedVisualInput.reference_images`。
4. `v2/orchestration/image_stage.py:635-648`：`_profile_versions()` 只记录 character/location/prop/style 的 asset_id/version，不记录独立 reference 资产版本。
5. `v2/orchestration/image_stage.py:651-655`：`_reference_images()` 仍会把上述独立 reference 的 image_id/sha 写入 Stage 13 manifest。
6. `v2/core/visual_registry.py:63,196-204`：image_id 由 registry 全局序号分配，理论上可全局唯一定位，但这不是 Stage 14 当前规定的“只在 profile_versions 对应精确版本中查找”算法。

### 11.2 影响

一个完全合法的 Stage 13 image manifest 可能同时包含：

- `reference_images` 中的独立 REF 图片；
- `profile_versions` 中没有其 asset_id/version。

因此 Stage 14 无法同时满足“reference 精确归属到 immutable version”和“只从 manifest profile_versions 解析”。这不是实现细节，属于 Stage 13 manifest 与 Stage 14 解析契约之间的信息缺口。

### 11.3 可选方案

**方案 A：严格遵守当前 Stage 14（短期最小变更）**

- reference 不在 profile_versions 精确版本集合内时 fail-fast。
- 不改任何禁改文件，也不改 Stage_14.md。
- 代价：含独立 REF 的合法 Stage 13 图像无法 QA。

**方案 B：批准 Stage 14 resolver 做 registry 全局 image_id 反查**

- 利用 registry 级 image_id 唯一性，遍历所有 immutable versions，要求恰好一处命中，再绑定 asset_id/version/path/checksum。
- 不必修改 core、image_stage 或 Stage 13 manifest。
- 但它改变 Stage_14.md 第 4.3 的既定解析来源/算法，必须由总控明确批准并补文档；不能由实现会话自行决定。
- 还需新增“全局重复 image_id/零命中/版本状态”测试，且仍禁止 active fallback。

**方案 C：单独 Stage 13-P2 扩充 provenance**

- 让 manifest reference 项显式携带 `asset_id + version`，或把 reference 资产版本纳入独立映射。
- provenance 最清晰，解析复杂度最低。
- 但需要修改 `image_stage.py`/Stage 13 manifest，并处理兼容、迁移和回归；触碰本阶段禁改边界，必须作为单独获批工作，不能混入 Stage 14-B。

### 11.4 建议与停线点

建议总控在 A 与 B 中明确选择；若长期要求 manifest 自包含、无需 registry 全局扫描，则另立 C。未裁决前：

- 可以实现/测试所有不涉及独立 REF 的 Stage 14 契约；
- 不应宣布 Stage 14 全量闭合；
- 不应改 core、workflow、review、Provider 公共接口、Stage 13 manifest 或 `image_stage.py`；
- 不应以 active version、路径猜测或 silent skip 规避冲突。

### 11.5 总控裁决 D14-REF-01（2026-10-01）

**采用方案 B；Stage 14-B 可以开工。**

裁决边界：

1. Resolver 对 manifest 中每个 reference `image_id` 遍历 registry 的全部资产与全部版本，
   要求全局恰好一处命中，并绑定该处的 `asset_id + version + ReferenceImage`。
2. 命中版本状态只允许 `approved` 或 `superseded`；禁止回落当前 active version。
3. 继续执行 manifest sha256、Registry checksum、磁盘实际 sha256 三方一致校验及项目目录
   containment 校验。
4. 零命中、多命中、非法状态、路径越界或任一 hash 不一致都必须在 Provider 调用前失败；
   禁止按路径/checksum 推断归属、静默丢弃或改为无 reference QA。
5. Provider 图片顺序仍由 manifest 决定：generated 第一，references 按 manifest 顺序；
   registry 的扫描顺序不得影响输入顺序。
6. 不修改 `core/visual*`、Stage 13 manifest schema 或 `image_stage.py`。Stage 13-P2 暂不立项；
   将来若要求 manifest 完全自包含，可另行设计 schema 版本与迁移，不阻塞 Stage 14。

## 12. Stage 14-B 实施验收清单

- [ ] 只新增/接线已批准的 Stage 14 模块，没有触碰禁改边界。
- [ ] manifest、exact versions、generated/ref 路径和 SHA 全部在调用前校验。
- [ ] generated 始终是 Image 0，refs 严格保持 manifest 顺序。
- [ ] checks 集合与 `FIELD_ORDER` 推导集合完全相等。
- [ ] qa_decision 只由本地确定性规则计算。
- [ ] 每图独立 Workflow/artifact/review；edit 后重验。
- [ ] resume 比较完整输入锚点和最终 artifact，不因顺序变化误命中。
- [ ] 默认关闭且 Mock 测试 0 网络、0 API。
- [ ] 实际 budget kind 受硬上限约束，retry 逐 attempt 计数。
- [ ] pipeline report 与 qa index 可审计，且不把 Review 与 QA decision 混为一谈。
- [ ] 第 11 节冲突已有总控书面裁决并反映在测试中。
- [ ] 真实冒烟仅在精确 provider+model 获批后进行，最多 1 张图。

---

## 13. D14-REF-01 落地与闭环结论（2026-10-02 追加，Stage 14-D）

> 本节只追加**实现与测试的最终结论**，不改写上文第 11 节的上报记录与裁决文本。

### 13.1 实现位置

- `orchestration/image_qa_stage.py::build_reference_index(registry)`
  —— 遍历 `registry.assets` 的**全部资产**及其 `asset.versions` 的**全部不可变版本**，
  对每个 `version.reference_images` 按 `image_id` 建立 `image_id -> [(asset, version, ref), …]`
  多值索引（**不使用** `active_version()`）。
- `orchestration/image_qa_stage.py::prepare_image_qa_input(...)`
  —— 对 manifest 每个 reference：`matches = reference_index.get(image_id)`；
  `len(matches) != 1` → `OrchestrationError("D14-REF-01 要求 reference … 全局恰好一处命中，实际 N")`；
  命中后校验 `version.status ∈ {approved, superseded}`，再执行
  `manifest sha256 == ReferenceImage.checksum == 磁盘 sha256` 三方一致与项目目录 containment。
- 图片顺序由 `entry["reference_images"]` 原始顺序决定，registry 扫描顺序不参与排序。

### 13.2 闭环证据（Stage 14-C 正式套件 T04 组，12/12 PASS）

```text
T04-1  独立 REF_* 的 approved reference 可解析（asset_id=REF_001, version=v1）
T04-2  superseded reference 版本可解析且不漂移到 active（used=v1）
T04-3  reference 位于 profile_versions 外仍可解析
T04-4  全局零命中被拒（Provider 调用 0）—— "实际 0"
T04-5  同一 image_id 跨资产多命中被拒 —— "实际 2"
T04-6  同一 image_id 跨版本多命中被拒 —— "实际 2"
T04-7  命中 draft / rejected / archived 版本被拒
T04-8  manifest reference image_id 重复被拒
T04-9  不按 checksum/path 猜测归属（image_id 未命中即失败）
T04-10 registry 扫描顺序变化不改变 manifest reference 输入顺序
```

### 13.3 结论

D14-REF-01 已**实现并被 T04 验证闭环**：独立 `reference` 资产可解析；零命中、多命中、
非法状态、重复 image_id、按路径/checksum 猜归属**全部在 Provider 调用前 fail-loud**；
registry 扫描顺序不影响输入顺序。未修改 `core/visual*`、Stage 13 manifest schema 或
`image_stage.py`。方案 C（Stage 13-P2 provenance 扩充）**仍不立项**。

★ 仍未验证：**真实多图 VLM 连通性**（`run_real_image_qa.py` 仅为 preflight/guard scaffold；
且 bailian key 环境变量与项目配置不一致）。详见
`v2/logs/stage14_image_qa/stage14_report.md` §11。
