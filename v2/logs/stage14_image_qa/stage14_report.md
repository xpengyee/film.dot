# Stage 14 阶段报告 —— VLM Image QA

> 会话：Stage 14-D（文档 / 交付 / Git 准备），独立文档会话，不承担实现与正式测试。
> 日期：2026-10-02
> 工作目录：`D:\AI\FilmAgent-main`
> 基线 HEAD：`1a7404942edb6e493091a449256da5a75d456ccc`
> Stage 13 tag：`v0.2-stage13` → `3895c6c103f125d458fe648d80a625bf007601af`（本会话未移动）
> 唯一任务规格：`v2/docs/stages/Stage_14D.md`

## 0. 本会话状态与结论边界

```text
Stage 14 状态：实现与正式测试已完成，等待总控最终验收和 Git 封存
本会话真实 API / 网络 / 模型切换：0 / 0 / 0
本会话是否重跑测试：否（Stage 14 与 Stage 13 套件均未重新执行）
本会话是否 commit / tag：否
最终状态：READY_FOR_ORCHESTRATOR_ACCEPTANCE
```

本会话只整理事实、文档与交付边界，**未修改任何业务源码、Prompt、测试或机器报告**，
**未运行任何测试、真实冒烟、网络请求或模型切换**。Stage 14 的正式测试证据来自
Stage 14-C 的既有机器报告，本报告只做静态核对与转录，不重新验证 198 条测试。

---

## 1. 背景、目标与范围

Stage 13 让系统第一次真的产生画面（`ComposedVisualInput` → 图像 → `manifest.json` → 挂回
`ShotAsset(kind="image")`），并已为 QA 备好三类锚点：生成图 `sha256`、
`profile_versions: {asset_id: exact_version}`、`reference_images: [{image_id, sha256}]`。

缺口是：**系统能出图，但不能程序化回答"这张图是否符合已批准的角色 / 地点 / 道具 / 风格"**。
若不经 QA 直接进入 Stage 15 视频生成，单帧错误会被带进更昂贵的视频链路。

Stage 14 因此新增一条**只读质量门**：

- 读取 Stage 13 `generated_images/manifest.json` 与其指向的图片；
- 在调用任何 Provider 前完成路径 containment、SHA256、精确视觉版本、reference 归属预检；
- 用现有 `MultimodalProvider.chat(messages, images=...)` 一次查看"生成图 + 全部参考图"；
- 按已批准视觉档案字段逐项裁决，由**本地确定性规则**计算 `qa_decision`；
- 结果走既有 Artifact 版本化 + Review 三态，写入只读索引 `quality/qa_manifest.json`；
- 为 Stage 15 提供准入门：只有"Review approved-like 且 `qa_decision == "pass"`"的图片可下游消费。

**范围**：本地功能与契约闭合（Mock 全绿）。**非目标**：自动修图 / 重绘 / 重生成、
修改 VisualAsset 或 Profile、删除或覆盖失败图片、视频 / 音频 / TTS / Assembly、
交互式人工审核 UI、以网页端生图结果冒充程序化 Provider。

---

## 2. A / B / C / D 分工与每段交付

Stage 14 按"总控 / 执行会话分离"模式拆为四个独立会话：

```text
Stage 14-A  契约 / 架构分析   → v2/logs/stage14_image_qa/stage14_contract_notes.md
             （源码核对、契约缺口上报、D14-REF-01 待裁决、测试矩阵）        PASS（总控已裁决）
Stage 14-B  实现              → v2/agents/image_qa.py
                                v2/prompts/v0.2/image_qa.txt
                                v2/orchestration/image_qa_stage.py
                                + v2/agents/__init__.py / v2/orchestration/__init__.py
                                  / v2/orchestration/pipeline.py 最小接线              PASS（自检）
Stage 14-C  正式测试 / Debug   → v2/logs/stage14_image_qa/run_tests.py
                                v2/logs/stage14_image_qa/test_report.json
                                v2/logs/stage14_image_qa/run_real_image_qa.py
                                + 3 处最小修复（仅 image_qa_stage.py）        PASS（198/198，rc=0）
Stage 14-D  文档 / 交付 / Git  → 本报告 + 六份长期文档同步 + 泄漏/边界审计
                                + intended commit file list                   本会话（等待总控验收）
```

各段边界（据 Stage_14B.md / Stage_14C.md / Stage_14D.md）：

- A 只做契约与架构，不写业务代码、不跑真实 API；
- B 只实现 Stage 14 自有模块 + 已批准的最小接线，不宣称验收完成；
- C 负责完整测试矩阵与最小 Debug，不负责最终文档封存；
- D（本会话）只做文档与交付准备，**不修代码、不重跑测试、不 commit、不 tag**。

---

## 3. 架构位置与端到端数据流

依赖方向沿用既有分层，未新增层：

```text
core → (llm | memory | context) → workflow → review → agents → orchestration
```

Stage 14 位于 Stage 13（Image Generation）与 Stage 15（Video Generation）之间：

```text
Stage 13 generated image + manifest provenance（sha256 / profile_versions / reference_images）
    ↓
ImageQAStage preflight
    ├─ 校验 generated 路径 containment + SHA256
    ├─ 解析 manifest 指定的精确 profile 版本（禁止漂移到 active）
    └─ D14-REF-01：reference image_id 全 registry 反查，要求恰好一处命中 + 三方 SHA256 一致
    ↓
ImageQAStep / ImageQAAgent → MultimodalProvider.chat(messages, images=(generated, refs...))
    ↓
严格 Schema 校验 + 本地确定性 qa_decision（pass / fail / needs_review）
    ↓
ArtifactStore（image_qa_<image_id> 版本化，绝不覆盖）+ Review 三态
    ↓
quality/qa_manifest.json（只读审计 / resume 索引，原子写）
    ↓
pipeline run_report["stages"]["image_qa"]
    ↓
Stage 15 准入门（见 §12）
```

| 模块 | 职责 |
|---|---|
| `v2/agents/image_qa.py` | `ImageQAAgent` / `ImageQAStep`；`expected_checks()`、`validate_provider_payload()`、`validate_artifact()`、`qa_decision()` 纯函数；响应解析与确定性规范化 |
| `v2/prompts/v0.2/image_qa.txt` | VLM 稳定输出契约（只含 checks + summary；verdict 枚举；negative_cues 逆语义；禁止内嵌路径 / 凭据 / base64） |
| `v2/orchestration/image_qa_stage.py` | `ImageQAStage`：manifest 解析、选择、路径/SHA/版本/reference 预检、Workflow 调度、二次校验、index、resume、run_report |
| `v2/orchestration/pipeline.py` | `_stage_image_qa()` 接线：段顺序 `… → image → image_qa → completeness`，默认关闭、Provider 惰性构造 |
| `v2/orchestration/__init__.py` | 导出 `ImageQAStage` / `QA_DIR_NAME` / `QA_MANIFEST_FILENAME`（纯追加） |
| `v2/agents/__init__.py` | 导出 `ImageQAAgent` / `ImageQAStep`（纯追加） |

`v2/orchestration/state.py` **未修改**：QA 进度完全由 Artifact / Review 与独立 QA index 表达，
未新增第二事实源。

---

## 4. D14-REF-01 决策及理由

### 4.1 缺口（Stage 14-A 发现，已上报总控）

Stage 13 允许把**独立 `reference` 资产**的图片放进 `ComposedVisualInput.reference_images`，
而 `ImageStage._profile_versions()` 只汇总 character / location / prop / style 的
`asset_id → version`，**不记录独立 reference 资产及其版本**。因此一个完全合法的 Stage 13
manifest 可能出现：

```text
reference_images 含独立 REF 图片  ∧  profile_versions 中没有该资产的 asset_id/version
```

按 Stage 14 原算法（"每张 reference 必须能在 profile_versions 的精确版本中找到"），
这类合法 manifest 无法完成精确版本解析。

### 4.2 总控裁决（2026-10-01，采用 14-A 方案 B）

```text
对 manifest 中每个 reference image_id：
  遍历 registry 的全部资产与全部不可变版本，以 image_id 做全局反查；
  要求**恰好一处命中**，绑定该处的 asset_id + version + ReferenceImage；
  命中版本状态只允许 approved / superseded；
  零命中、多命中、active_version() fallback、按路径/checksum/名称猜归属、静默跳过 → 全部禁止；
  仍须满足 manifest sha256 == Registry checksum == 磁盘 sha256 三方一致 + 项目目录 containment；
  Provider 图片顺序仍由 manifest 决定，registry 扫描顺序不得影响输入顺序。
```

**理由**：`ReferenceImage.image_id` 由 registry 级 `_next_image_number` **全局递增分配**，
天然可全局唯一定位。方案 B 无需修改 `core/visual*`、Stage 13 manifest schema 或
`image_stage.py`（方案 C 的 Stage 13-P2 provenance 扩充当前不立项），代价最小且可测试。

### 4.3 实现与验证

实现见 `image_qa_stage.py::build_reference_index()`（遍历全部资产的全部版本）与
`prepare_image_qa_input()`（`len(matches) != 1` → fail-loud）。
被 **T04 组 12 条**验证：独立 `REF_*` 可解析、`superseded` 不漂移、零命中/跨资产多命中/
跨版本多命中/非法状态/重复 image_id 全部在 Provider 调用前失败、不按 checksum/path 猜测、
registry 扫描顺序变化不改变输入顺序。**闭环成立**（详见 `stage14_contract_notes.md` 的追加结论）。

---

## 5. Ground truth、路径/SHA、多图顺序、严格 Schema、本地裁决

### 5.1 精确版本（不漂移）

- `profile_versions` 必须是非空 mapping，键值均为非空字符串，**保持 manifest 序列化顺序**；
- 每个版本经 `registry.get(asset_id)` + `asset.get_version(version)` 精确获取；
- 版本状态只接受 `approved` / `superseded`；**禁止** `active_version()` 回落；
- 字段顺序严格来自 `PROFILE_CLASSES[kind].FIELD_ORDER`，不另立字段清单；
- `notes` 只作上下文不评分；`negative_cues` 恰好一个逆语义 check；空字段不送审、不要求模型猜测。

### 5.2 路径 containment 与 SHA256

- 统一解析器 `safe_project_file()`：拒绝空路径、`..` 段、目录、缺失文件、其他盘符 / UNC；
  相对路径锚定 `project_root`，绝对路径也须解析后仍位于 project 内；
  用 `resolve(strict=True)` + `relative_to(root)` 判定，**不用字符串前缀**；
- generated：`manifest.sha256 == 磁盘 sha256`（64 位 hex，比较时规范为小写）；
- reference：`manifest.sha256 == ReferenceImage.checksum == 磁盘 sha256` 三方一致；
- 持久化只写**项目相对 POSIX 路径**，绝对路径只存在于进程内输入。

### 5.3 送模图片顺序

```text
images[0]   = generated image
images[1:]  = reference images，严格按 manifest reference_images 原始顺序
```

不排序、不去重、不按 asset_id/image_id 重排；manifest 出现重复 reference image_id 直接拒绝。
Prompt 中以位置标签显式标注 `Image 0 = generated`，后续列 reference 的 image_id /
asset_id / version；不含绝对路径、base64、API key。

### 5.4 严格 Schema 与本地确定性裁决

Provider 只允许返回 `{"checks": [...], "summary": "..."}`，每条 check 恰好
`asset_id / version / field / expected / verdict / evidence`；verdict 枚举
`match | mismatch | uncertain | not_visible`。本地校验：

- `(asset_id, version, field)` 不重复，实际 key 集合与期望集合**完全相等**（缺失 / 额外 / 重复均失败）；
- `expected` 必须等于 Profile 精确值，模型不得改写；
- 非 `match` verdict 的 `evidence` 必须非空；
- 模型自报的 `qa_decision / provider / model / request_id / retry_count / created_at` 一律忽略并由本地覆盖；
- 非法输出抛 `StepError`，走既有 Workflow 重试，**绝不静默补齐为 match**。

本地唯一总判定（无浮点阈值）：

```text
任一 mismatch                 → qa_decision = fail
否则任一 uncertain/not_visible → qa_decision = needs_review
否则全部 match                 → qa_decision = pass
```

`qa_decision`（画面质量结论）与 Review `decision`（人对报告的审核结论）是**两个正交维度**：
人工 `approved` 不得把 QA `fail` 改写成 `pass`。

---

## 6. Workflow / Artifact / Review、resume、预算与默认关闭

- **每图一个 Workflow run**，`artifact_name = image_qa_<image_id.lower()>`，
  `step_id = image_qa_<image_id.lower()>`，`needs_review = True`；重跑产生 `_v2` 等新版本，
  原文件字节不变。
- **Review 三态**：`approved` 接受报告；`approved_with_edit` 允许编辑 checks/summary，
  但编排层必须读取**最终批准的 Artifact** 并用同一严格 validator **二次校验** + 重算
  `qa_decision`，可信 Provider 元数据（provider/model/request_id/retry_count/created_at）
  被篡改即失败；`rejected` 保留 Artifact、index 可审计、`downstream_eligible=false`。
- **QA index**：`<project_dir>/quality/qa_manifest.json`，复用 `atomic_write_json` 原子写；
  只索引 `image_id → artifact/review/status/hash/qa_decision`，**不复制完整 checks**，
  不修改 Stage 13 manifest，不成为第二状态机。同一 image_id 更新时替换该项、不动其他记录。
- **resume**（`qa_resume` 缺省 `True`）：仅当 generated path/sha、`profile_versions`（含**插入顺序**）、
  reference 列表（含顺序）、最终 Artifact 存在性与 sha、Workflow/Review 批准态、
  Artifact 重验与 `qa_decision` 重算、`provider + model` 身份锚点**全部一致**才跳过；
  命中时零 Provider 调用、零预算消费。任一锚点变化即重跑并产生新 Artifact 版本。
  `downstream_eligible` 是**可重算的派生值**，index 自报值不被信任（按 Review + 本地重算复算，
  不一致即视为 stale）。
- **预算**：`cap = min(max_qa_images, MAX_REAL_VISION_REQUESTS=3)`，同一上限经
  `APIRequestCounter.set_budget("vision", cap)` 下推；Provider 内 retry 与 Workflow retry
  共用同一 vision 桶，**不逐图重置**；第 N+1 次在发送前拒绝，预算耗尽即停止剩余图片。
  真实 QA Provider 必须 `budget_kind == "vision"`（`multimodal` 等其他桶调用前拒绝）。
- **pipeline 默认关闭**：`run_image_qa` 缺省 `False` → 零 QA Provider 调用、零 Artifact、
  零 index 变更，Stage 13 既有行为逐字节保持；Provider **惰性构造**（只有开启且预检/resume 后
  仍有待处理项时才创建）；支持 `generate_images=False` 直接对既有 manifest 执行 QA；
  **completeness 的既有定义不因 QA 改写**。
- **多图片隔离**：两张图 / 两场景的 profile、refs、Artifact、index 不串数据；
  单图预检失败不删除图片，其他选中项按既定语义继续。

---

## 7. Stage 14-C 正式测试结果（198/198）

权威机器报告：`v2/logs/stage14_image_qa/test_report.json`

```text
Stage 14-C：198 / 198 PASS，rc = 0
T01 12/12   T02 23/23   T03 22/22   T04 12/12
T05 17/17   T06  7/7    T07 21/21   T08 17/17
T09 12/12   T10 19/19   T11 12/12   T12 11/11
T13 13/13

real_api_requests = 0
network_requests  = 0
model_switches    = 0
regressions       = []
failures          = []
```

测试分组主题：T01 静态契约与冻结边界 / T02 manifest 选择与调用前失败 /
T03 精确 Profile 版本与字段顺序 / T04 D14-REF-01 全局 reference resolver /
T05 路径 containment 与 SHA256 / T06 Prompt、图片顺序与敏感信息 /
T07 Provider 输出严格校验 / T08 Workflow、Artifact 与 Review 三态 / T09 QA index 完整性 /
T10 resume 命中与全部 stale 条件 / T11 预算、retry 与模型授权 / T12 pipeline 与多图片隔离 /
T13 真实冒烟脚本的零调用护栏。

`freeze_checks` 记录：`frozen_paths_clean = true`、`filmagent_0_diff = true`、
`stage13_tag = 3895c6c103f125d458fe648d80a625bf007601af`、`stage13_tag_ok = true`。

> **数字边界**：198 条**只代表 Stage 14 正式套件**，不包含 Stage 13 或任何历史套件。

### 7.1 本会话对机器报告的静态核对（非重跑）

本会话只做只读解析与一致性核对：JSON 可解析；`tests_total = 198 = passed`、`failed = 0`；
`groups` 13 组计数之和 = 198；`real_api_requests / network_requests / model_switches` 均为 0；
`failures = []`；`regressions = []`。**未重新执行任何测试**，上述数字全部来自 C 会话的既有报告。

---

## 8. Stage 14-C 的三个缺陷与修复

三个已证明缺陷均**只修改 `v2/orchestration/image_qa_stage.py`**（最小修复，未删除或放宽任何断言）：

| # | 缺陷 | 根因 | 修复 | 守护测试 |
|---|---|---|---|---|
| 1 | QA index 重复 `image_id` 可能被静默覆盖 | 原实现用 dict comprehension 建映射，重复项**静默取最后一项** | `_load_index()` 显式扫描并 **fail-loud**（`QA index 含重复 image_id`） | T09-6 |
| 2 | resume 可能直接信任 index 自报的 `downstream_eligible` | 派生布尔被当作可信源 | `_resume_hit()` / resumed 分支**按 Review approved-like + 本地重算 `qa_decision == pass` 复算**，与存储值不一致即视为 stale | T09-7、T10-9 |
| 3 | `profile_versions` 插入顺序改变不会使 resume stale | 用 dict 相等比较，**忽略顺序** | 改为**有序** `(asset_id, version)` 列表比较（顺序影响 checks 与 Prompt 语义） | T10-3b |

修复后 C 会话完成定向复测与一次完整回归，正式套件 198/198、rc=0。
本会话仅静态核对三处修复**仍存在于源码**（`_load_index` 重复检测 / resume 复算 /
有序 `list(stored_profiles.items()) != expected_profiles`），未修改任何代码。

---

## 9. Stage 13 历史回归：144/146、rc=1 的准确解释

必须原样记录，禁止美化：

```text
命令：python v2/logs/stage13_image/run_tests.py
结果：144 / 146 PASS，rc = 1，耗时约 3572 秒（约 59.5 分钟）
失败：T17a、T18-stage12
```

**准确措辞（本报告与长期文档统一使用）**：

```text
Stage 14 套件全绿。Stage 13 历史套件功能项通过，但整套 rc=1；
两个失败均是合法未提交 Stage 14 变更触发的 pre-commit 清洁度护栏。
```

**失败原因**：T17a 与 T18-stage12 均为 **pre-commit 工作区清洁度护栏**。Stage 14 合法修改了
`v2/agents/__init__.py`，因此相对当前 HEAD 的检查必然看到：

```text
v2/agents/__init__.py | 3 +++
```

现有证据**未显示任何 Stage 13 功能回归**。

**禁止**写成"Stage 13 146/146""全部回归 rc=0"或笼统的"历史回归全绿"。

### 9.1 本报告对机器报告覆盖边界的声明

`test_report.json` 的 `regressions` 数组当前为**空**，**未承载**上述历史回归详情。
本报告**不伪造、不回填**任何机器测试结果：历史回归结论以本报告 §9 的会话交接证据为准，
`test_report.json` 只覆盖 Stage 14 正式套件（198/198）。两者覆盖范围不同，不得互相替代。

---

## 10. 冻结边界与零调用证据

### 10.1 冻结边界（本会话静态核对，全部为 0 diff）

```text
git diff --stat -- FilmAgent/            → 空
git diff --stat -- v2/core/              → 空
git diff --stat -- v2/workflow/          → 空
git diff --stat -- v2/review/            → 空
git diff --stat -- v2/memory/            → 空
git diff --stat -- v2/context/           → 空
git diff --stat -- v2/llm/               → 空（含 providers/base.py、client.py、router.py、request_counter.py）
git diff --stat -- v2/prompts/           → 空（image_qa.txt 为 Stage 14-B 新增未跟踪文件）
git diff --stat -- v2/orchestration/image_stage.py → 空（Stage 13 文件 0 改）
git diff --stat -- .gitignore（仓库根）  → 空
git rev-parse v0.2-stage13^{commit}       → 3895c6c103f125d458fe648d80a625bf007601af（未移动）
git tag -l                                → 8 个 tag（v0.1 / v0.2-stage7…13），无新增、无移动
```

Stage 14 只新增自有模块（`agents/image_qa.py`、`prompts/v0.2/image_qa.txt`、
`orchestration/image_qa_stage.py`）并对 `agents/__init__.py`、`orchestration/__init__.py`、
`orchestration/pipeline.py` 做最小接线。

### 10.2 零调用证据（来自 Stage 14-C 报告与本会话静态核对）

```text
real_api_requests = 0        （test_report.json）
network_requests  = 0        （test_report.json）
model_switches    = 0        （test_report.json）
T01-6a：新实现无直接 requests / urllib / SDK / socket 网络调用 → detail "[]"
T01-6b：未新增第二请求计数器
T01-6c：计数只经 llm.request_counter 单例 / 注入
T13-1a / T13-1c：run_real_image_qa.py 默认 rc=2、真实调用 0
```

本会话**未运行**任何测试、真实 API、网络请求或模型切换（详见 §14 与 §0）。

---

## 11. 真实连通性与冒烟脚本的限制（未验证，不得标 PASS）

```text
★ 真实多图 VLM 连通性：未验证。
   "生成图 + 多张参考图" 的真实多模态 Provider 调用从未执行。
   Stage 10 曾真实调用 Vision Provider 3 次（单图 Profile extraction），
   但这不是 Stage 14 的多图 QA 业务。
```

`v2/logs/stage14_image_qa/run_real_image_qa.py` 的准确定位：

- 它是**授权与参数的 preflight / guard scaffold**，**不是**可直接运行的真实冒烟入口，
  **不得**描述为"已连通"或"可直接发送真实请求"；
- 默认拒绝路径已验证：缺任一前置 → `rc = 2`、**0 次真实调用**（T13-1a / T13-2 / T13-6）；
- 即使**全部**前置满足，它也只打印 `PRECONDITIONS SATISFIED` 并返回 `rc = 3`，
  **不会**真正构造 Provider 或发起 VLM 请求（`main()` 中无 Provider 构造、无网络调用）；
- 它要求 `--approve` **且**环境变量 `FILMAGENT_QA_CONFIRM_REAL=1` 才可能越过拒绝路径；
- `max_qa_images` 强制为 1；计划输出显示 provider / model 与"成本已知/未知"，
  未知一律按"可能计费"处理；**绝不显示 API key、绝不自动改 Config、绝不自动 fallback**。

**★ 开放问题（不在本会话修代码）**：脚本对 `bailian` 检查 `DASHSCOPE_API_KEY`
（`KEY_ENV_BY_VENDOR`），而项目 `PROVIDERS["bailian_vision"]` 的 `api_key_env` 是
`BAILIAN_API_KEY`（见 `v2/config.py` §PROVIDERS）。两处环境变量名**不一致**。
在未来真实冒烟实现前必须先统一，否则"前置满足"的判定与实际 Provider 取 key 的来源不一致。

---

## 12. Stage 15 准入契约

Stage 15 只能消费**同时**满足以下条件的记录：

```text
Review decision ∈ approved-like（approved / approved_with_edit）
AND
qa_decision == "pass"
AND
resume 时所有 provenance / Artifact / provider / model 锚点仍有效
```

`downstream_eligible` 是**可重算的派生值**，不是可信源；下游**不得仅凭该布尔值**绕过上述条件
（编排层在 resume 时已按 Review + 本地重算复算，与 index 自报值不一致即视为 stale）。

`qa_decision` 非 `pass`（`fail` / `needs_review`）或 Review 为 `rejected` 的记录，
在 QA index 与 run_report 中明确标注**不可下游消费**，但**不删除生成图、不修改 manifest**。

---

## 13. 交付文件清单

### 13.1 Stage 14 业务实现

```text
新增  v2/agents/image_qa.py
新增  v2/orchestration/image_qa_stage.py
新增  v2/prompts/v0.2/image_qa.txt
修改  v2/agents/__init__.py             （纯追加导出 ImageQAAgent / ImageQAStep）
修改  v2/orchestration/__init__.py      （纯追加导出 ImageQAStage / QA_DIR_NAME / QA_MANIFEST_FILENAME）
修改  v2/orchestration/pipeline.py      （_stage_image_qa 接线 + qa_provider 参数 + run_spec 文档）
```

### 13.2 Stage 14 测试与报告

```text
新增  v2/logs/stage14_image_qa/run_tests.py
新增  v2/logs/stage14_image_qa/run_real_image_qa.py
新增  v2/logs/stage14_image_qa/test_report.json
新增  v2/logs/stage14_image_qa/stage14_contract_notes.md
新增  v2/logs/stage14_image_qa/stage14_report.md      （本报告，14-D）
```

### 13.3 Stage 14 文档

```text
修改  CLAUDE.md
修改  v2/docs/PROJECT_STATUS.md
修改  v2/docs/HANDOFF.md
修改  v2/docs/ROADMAP.md
修改  v2/docs/stages/Stage_14.md
```

### 13.4 开工前已有、需要保留的既有改动（**不得覆盖、不得回退**）

```text
修改  CLAUDE.md                     ← 已含用户「工作区清理与回收站（阈值=100）」长期规则（2026-10-02）
修改  v2/docs/PROJECT_STATUS.md     ← 已含总控「模型使用授权与 API Key 授权分离」规则（2026-10-01）
修改  v2/docs/stages/Stage_14.md    ← 已含总控 D14-REF-01 裁决改写 + 真实调用精确 provider+model 授权规则
新增  v2/docs/stages/Stage_14B.md   （总控开工指令）
新增  v2/docs/stages/Stage_14C.md   （总控开工指令）
新增  v2/docs/stages/Stage_14D.md   （总控开工指令）
```

> 本会话对上述文件**逐段追加/合并**，未整文件覆盖，未清理、未 reset、未 checkout、未 clean。
> Stage 14-B/C/D 三份开工指令由总控生成，属需保留的既有文档改动。

### 13.5 明确**不纳入**交付

```text
v2/logs/stage14_image_qa/.test_report.authoritative.json  ← 14-C 期间生成的权威报告备份副本；
                                                              与 test_report.json 摘要一致、无敏感信息，
                                                              但不在 Stage_14D.md §7 D4 允许清单内，
                                                              建议不加入 commit（本会话未删除）
v2/logs/stage14_image_qa/console.txt                      ← gitignored 运行日志
v2/logs/stage14_image_qa/tmp_logs/                        ← gitignored 临时账本
v2/logs/stage14_image_qa/tmp_projects/                    ← gitignored 临时项目（含测试用最小 PNG）
```

---

## 14. Git 状态、建议 commit message / tag（均未执行）

### 14.1 交付前静态快照（本会话实读）

```text
git rev-parse HEAD                     = 1a7404942edb6e493091a449256da5a75d456ccc（= 规格基线）
git rev-parse v0.2-stage13^{commit}    = 3895c6c103f125d458fe648d80a625bf007601af
git diff --check                       = rc 0（仅 LF→CRLF 换行警告，非空白错误，见 §14.3）

git status --short
 M CLAUDE.md
 M v2/agents/__init__.py
 M v2/docs/HANDOFF.md
 M v2/docs/PROJECT_STATUS.md
 M v2/docs/stages/Stage_14.md
 M v2/orchestration/__init__.py
 M v2/orchestration/pipeline.py
?? v2/agents/image_qa.py
?? v2/docs/stages/Stage_14B.md
?? v2/docs/stages/Stage_14C.md
?? v2/docs/stages/Stage_14D.md
?? v2/logs/stage14_image_qa/
?? v2/orchestration/image_qa_stage.py
?? v2/prompts/v0.2/image_qa.txt
（+ v2/logs/stage14_image_qa/.test_report.authoritative.json，见 §13.5）

git diff --stat（本会话 D3 完成后）
 见 §14.4 收尾快照
```

### 14.2 建议（**不得执行**）

```text
commit message: feat(v0.2): add VLM image QA (stage14)
tag:            v0.2-stage14
```

**条件**：tag 只能在**总控验收通过 + 用户明确批准**后创建；任何既有 tag
**永不移动、永不 force**。执行会话不得自行宣布 Stage 14 已封存、已 commit 或已打 tag。

### 14.3 换行警告说明

`git diff --check` 返回 0（无空白错误）；`git diff` 输出中的
`LF will be replaced by CRLF the next time Git touches it` 是 Windows 工作区
`core.autocrlf` 行为产生的**提示**，不是 `diff --check` 失败，也不代表文件损坏。

### 14.4 收尾快照

见本报告文末「附：交付前最终静态快照」（D5 完成后追加）。

---

## 15. 剩余风险与开放问题

| # | 项 | 影响 | 状态 / 建议 |
|---|---|---|---|
| R-1 | **真实多图 VLM 连通性未验证** | 本地契约全绿不等于真实厂商多图协议可用 | 未验证，**不得标 PASS**；未来真实运行前须单独获批 |
| R-2 | `run_real_image_qa.py` 只是 **preflight/guard scaffold** | 不能当作可直接运行的真实冒烟入口 | 已如实登记；真实冒烟实现待立项 |
| R-3 | **百炼 key 环境变量不一致**（脚本 `DASHSCOPE_API_KEY` vs 项目 `BAILIAN_API_KEY`） | "前置满足"判定与实际取 key 来源不一致，真实冒烟会误判 | 开放问题；**本会话不改代码**，未来真实冒烟实现前必须先统一 |
| R-4 | `test_report.json` 的 `regressions` 为空，未承载 Stage 13 历史回归详情 | 只读机器报告者可能误以为"无回归" | 本报告 §9 显式声明覆盖边界；不伪造、不回填机器报告 |
| R-5 | `test_report.json` 两条 `detail` 含工作区绝对路径（T04-1、T06-1，指向 gitignored `tmp_projects/`） | 机器报告出现绝对路径（**非**用户个人临时目录；无密钥/图片/base64） | 属 14-C 机器报告，`Stage_14D.md` 明令禁改 → **仅登记**；建议总控决定是否在后续阶段做脱敏（会改变机器报告，需授权） |
| R-6 | 历史回归嵌套成本 ≈59.5 分钟（技术债 T-13） | 日常回归慢 | 已登记（Stage 13 遗留），未修 |
| R-7 | `.test_report.authoritative.json` 为 14-C 备份副本 | 不在允许交付清单内，易被误 `git add` | 建议不纳入 commit；本会话未删除 |
| R-8 | 文档措辞：本阶段状态写为"等待总控验收 / Git 封存" | 若 tag 后才更新，tag 所指 commit 内的文档会显示"未封存" | **建议**：总控验收并封存时，把六份文档的状态改为最终形式（"Stage 14 完成并封存；`git rev-parse v0.2-stage14` 查精确 hash"），遵循「永久文档不写瞬时状态」铁律 |

### 15.1 已确认未变的长期约束

- `FilmAgent/`（V0.1）0 diff；仓库根 `.gitignore` 0 diff；
- `v2/core/`、`v2/workflow/`、`v2/review/`、`v2/memory/`、`v2/context/`、`v2/llm/` 0 diff；
- `v2/orchestration/image_stage.py` 0 diff；
- 8 个 tag 未移动，`v0.2-stage13` 仍指向 `3895c6c…`；
- 历史测试套件与断言一个未动。

---

## 附：交付前最终静态快照（D5 完成后追加）

> 本快照在全部文档保存**之后**采集，是交付前最后一次只读核对。

### A. Git 状态（本会话实读，未做任何写操作）

```text
git rev-parse HEAD                    = 1a7404942edb6e493091a449256da5a75d456ccc
git rev-parse v0.2-stage13^{commit}   = 3895c6c103f125d458fe648d80a625bf007601af
git tag -l | wc -l                    = 8（v0.1 / v0.2-stage7…13；无新增、无移动）
git diff --check                      = rc 0（仅 LF→CRLF 提示，非空白错误）

git status --short
 M CLAUDE.md
 M v2/agents/__init__.py
 M v2/docs/HANDOFF.md
 M v2/docs/PROJECT_STATUS.md
 M v2/docs/ROADMAP.md
 M v2/docs/stages/Stage_14.md
 M v2/orchestration/__init__.py
 M v2/orchestration/pipeline.py
?? v2/agents/image_qa.py
?? v2/docs/stages/Stage_14B.md
?? v2/docs/stages/Stage_14C.md
?? v2/docs/stages/Stage_14D.md
?? v2/logs/stage14_image_qa/
?? v2/orchestration/image_qa_stage.py
?? v2/prompts/v0.2/image_qa.txt

git diff --stat
 CLAUDE.md                    | 115 ++++++++++++++++++++++++---
 v2/agents/__init__.py        |   3 +
 v2/docs/HANDOFF.md           | 114 +++++++++++++++++++++------
 v2/docs/PROJECT_STATUS.md    | 183 ++++++++++++++++++++++++++++++++++++-------
 v2/docs/ROADMAP.md           |  67 ++++++++++------
 v2/docs/stages/Stage_14.md   | 111 +++++++++++++++++++++++++-
 v2/orchestration/__init__.py |   9 +++
 v2/orchestration/pipeline.py |  39 ++++++++-
 8 files changed, 550 insertions(+), 91 deletions(-)

git status --porcelain -uall（未跟踪明细，含被忽略之外的全部条目）
 ?? v2/logs/stage14_image_qa/.test_report.authoritative.json   ← 见 §13.5，不纳入交付
 ?? v2/logs/stage14_image_qa/run_real_image_qa.py
 ?? v2/logs/stage14_image_qa/run_tests.py
 ?? v2/logs/stage14_image_qa/stage14_contract_notes.md
 ?? v2/logs/stage14_image_qa/stage14_report.md
 ?? v2/logs/stage14_image_qa/test_report.json
```

被 gitignore 命中（**不进交付**）：`v2/logs/stage14_image_qa/console.txt`（`v2/.gitignore:12`）、
`…/tmp_logs/`（`:15`）、`…/tmp_projects/`（`:14`，含 367 个测试用最小文件）。

### B. 冻结边界（全部 0 diff / 未移动）

```text
git diff --stat -- FilmAgent/ v2/core/ v2/workflow/ v2/review/ v2/memory/ v2/context/ v2/llm/
                    v2/orchestration/image_stage.py .gitignore   → 空
git rev-parse v0.2-stage13^{commit}                             → 3895c6c103f125d458fe648d80a625bf007601af
git tag -l | wc -l                                              → 8（无新增）
```

### C. 泄漏扫描结果

```text
git add -An v2/ CLAUDE.md | grep -iE '\.png|\.jpg|\.jpeg|\.webp|\.jsonl|images/|
        generated_images/|registry\.json|tmp_|__pycache__|\.pyc|console\.txt'
        → 无命中（不会有泄漏型文件被加入）
仓库根 llm_calls.jsonl / v2/llm_calls.jsonl → 不存在
密钥 / Bearer / 真实 base64 / 用户个人临时路径（C:\Users、AppData、/tmp 等）
        扫描全部 Stage 14 交付文件 + 修改文档 → 仅 1 处**文档措辞**命中
        （PROJECT_STATUS.md 描述 Mock 引用串形状的 `data:image/png;base64,…`，非真实内容，非泄漏）
tracked 二进制：仅 pics/*.png（V0.1 仓库基线既有，非 Stage 14 引入）
```

**登记项（不修改，仅上报总控）**：

```text
L-1  test_report.json 两条 detail（T04-1 / T06-1）含工作区绝对路径
     （指向 gitignored tmp_projects/；无密钥/图片/base64）。该文件属 14-C 机器报告，
     Stage_14D.md 明令禁改 → 仅登记，是否脱敏由总控决定。
L-2  v2/logs/stage14_image_qa/.test_report.authoritative.json 为 14-C 权威报告备份副本，
     与 test_report.json 摘要一致、无敏感信息，但**不在允许交付清单内** → 建议不纳入 commit。
```

---

## 附二：intended commit file list（D5；**仅清单，未执行任何 git 写操作**）

### ① Stage 14 业务实现

```text
新增  v2/agents/image_qa.py
新增  v2/orchestration/image_qa_stage.py
新增  v2/prompts/v0.2/image_qa.txt
修改  v2/agents/__init__.py             （+3 行：导出 ImageQAAgent / ImageQAStep）
修改  v2/orchestration/__init__.py      （+9 行：导出 ImageQAStage / QA_DIR_NAME / QA_MANIFEST_FILENAME）
修改  v2/orchestration/pipeline.py      （+39/-1：_stage_image_qa 接线 + qa_provider + run_spec 文档）
```

### ② Stage 14 测试与报告

```text
新增  v2/logs/stage14_image_qa/run_tests.py
新增  v2/logs/stage14_image_qa/run_real_image_qa.py
新增  v2/logs/stage14_image_qa/test_report.json
新增  v2/logs/stage14_image_qa/stage14_contract_notes.md
新增  v2/logs/stage14_image_qa/stage14_report.md
```

### ③ Stage 14 文档

```text
修改  CLAUDE.md
修改  v2/docs/PROJECT_STATUS.md
修改  v2/docs/HANDOFF.md
修改  v2/docs/ROADMAP.md
修改  v2/docs/stages/Stage_14.md
```

### ④ Stage 14 开工前已有、需要保留的既有改动（不得覆盖/回退）

```text
新增  v2/docs/stages/Stage_14B.md       （总控开工指令）
新增  v2/docs/stages/Stage_14C.md       （总控开工指令）
新增  v2/docs/stages/Stage_14D.md       （总控开工指令）
内含  CLAUDE.md                         ← 用户「工作区清理与回收站（阈值=100）」长期规则
内含  v2/docs/PROJECT_STATUS.md         ← 总控「模型使用授权与 API Key 授权分离」规则
内含  v2/docs/stages/Stage_14.md        ← 总控 D14-REF-01 裁决改写 + 精确 provider+model 授权规则
```

### ⑤ 明确排除

```text
排除  v2/logs/stage14_image_qa/.test_report.authoritative.json   （备份副本，不在允许清单）
排除  v2/logs/stage14_image_qa/console.txt                       （gitignored）
排除  v2/logs/stage14_image_qa/tmp_logs/                         （gitignored）
排除  v2/logs/stage14_image_qa/tmp_projects/                     （gitignored，含测试用最小文件）
```

### 建议（**未执行**）

```text
commit message: feat(v0.2): add VLM image QA (stage14)
tag:            v0.2-stage14
```

`tag` 仅在**总控验收通过 + 用户明确批准**后创建；既有 8 个 tag **永不移动、永不 force**。
本会话**未 commit、未 tag、未移动任何历史 tag**。

---

## 附三：本会话声明（D5 收尾）

```text
未重跑 Stage 14 测试          未重跑 Stage 13 测试 / 历史回归
未运行真实冒烟                未访问网络            未切换模型
未修改业务源码 / Prompt / 测试 / 机器报告
未 commit                     未 tag                未移动任何历史 tag
未执行 git reset / checkout / clean / stash / 删除任何既有改动

最终状态：READY_FOR_ORCHESTRATOR_ACCEPTANCE
```

---

## 附四：独立封存补记（2026-10-02，后续会话）

> 本节由后续封存会话追加；前述 Stage 14-D「本会话未 commit/tag」等描述是
> **D 会话当时的真实快照**，不是封存后的当前状态，不回写、不伪造历史。

- 用户明确批准独立封存 Stage 14。先静态复核了 Stage 14-C 权威报告：
  **198/198、rc=0、真实 API/网络/模型切换 0/0/0**，`failures=[]`；
  三处 C 修复仍存在；`FilmAgent/` 与冻结目录 0 diff，`v0.2-stage13` 未移动。
- 本封存会话**未重新运行 Stage 14 / Stage 13 套件**，不改原机器报告；Stage 13
  历史回归保留 **144/146、rc=1** 的封存前记录，两个失败当时是未提交 Stage 14
  变更触发的 pre-commit 清洁度护栏。封存后未复跑，不能宣称其 rc=0。
- Stage 14 业务代码、测试、报告与所需长期文档独立提交，创建 `v0.2-stage14`；
  以 `git rev-parse v0.2-stage14^{commit}` 查询精确 commit，绝不移动旧 tag。
- 排除 Stage 15 规格 `v2/docs/stages/Stage_15.md`、
  `.test_report.authoritative.json` 备份、测试图片/临时目录、日志和 JSONL。
- **真实多图 VLM 连通性仍未验证**；`run_real_image_qa.py` 仍只是 preflight/guard scaffold。

封存后状态：**Stage 14 独立封存；Stage 15 尚未开工**。
