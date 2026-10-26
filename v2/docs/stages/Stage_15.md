# FilmAgent V0.2 —— Stage 15 执行规格：Image-to-Video Clip Generation

> 本文件是 Stage 15 的完整代码执行规格。一个全新的执行会话应能仅凭本文件、仓库常驻文档、Stage 14 报告与源码独立实现、测试和交付。
> 生成日期：2026-10-02。设计者：项目总控。
> 工作目录：`D:\AI\FilmAgent-main`。
> 本阶段代码与测试期间：真实 API、网络调用、自动模型切换必须为 0。
> 本文件属于 Stage 15，不得纳入 Stage 14 commit/tag。

---

## 0. 开工硬前置

Stage 15 执行前必须同时满足：

1. Stage 14 已由总控验收；
2. Stage 14 已独立 commit；
3. `v0.2-stage14` tag 已在用户明确批准后创建；
4. `v0.2-stage13` 及更早 tag 未移动；
5. 除本 `Stage_15.md` 外，工作区没有 Stage 14 未提交业务改动；
6. `FilmAgent/` 仍为 0 diff。

若 `v0.2-stage14` 不存在，或 Stage 14 业务文件仍未提交，**立即停止代码实现**，只向总控报告
Git 状态。不得把 Stage 14 与 Stage 15 混进同一个 commit，也不得由执行会话自行创建 tag。

开工核对：

```powershell
git status --short
git log --oneline -5
git rev-parse v0.2-stage14^{commit}
git rev-parse v0.2-stage13^{commit}
git diff -- FilmAgent/
```

---

## 1. Stage 背景与项目当前状态

FilmAgent V0.2 当前链路已经具备：

```text
Scene
  → Storyboard / Shot / CameraPlan
  → Visual Asset exact versions
  → Stage 13 Image Generation
  → generated image + manifest provenance
  → Stage 14 VLM Image QA
  → versioned QA Artifact + Review + quality/qa_manifest.json
```

Stage 14 已建立视频生成前的图片质量门，但系统还不能把合格分镜图转换为视频片段。
Canonical `Shot` 已有 `duration`、`CameraPlan`、`visual_description`、动作、构图、连续性和
`ShotAsset(kind="video")` 表达能力；Stage 9 的 `VideoGenerationProvider` 仍只是占位接口，
Provider 工厂会明确拒绝 `kind="video"`。

### 1.1 前置阶段

- Stage 9：Provider 接口族中预留 `VideoGenerationProvider`。
- Stage 11：生成带 `CameraPlan`、动作、构图、连续性和时长的 Shot。
- Stage 12：ProjectOrchestrator 与可恢复项目生命周期。
- Stage 13：每 Shot 生成图、落盘、manifest、SHA256、预算和 Review。
- Stage 14：对生成图进行精确版本 VLM QA，并给出可重算的视频准入门。

### 1.2 当前真实边界

- Stage 14 正式套件 198/198、rc=0；真实多图 VLM 连通性未验证。
- Stage 15 尚无视频 Provider 实现、视频预算、视频 manifest 或编排段。
- 用户可用平台中可能存在带免费额度的视频模型，但**额度、地域、endpoint、模型名和剩余额度
  不能由代码猜测**。
- 本阶段只实现并测试能力，禁止真实生成视频。

---

## 2. 为什么需要本 Stage

1. Stage 14 已能证明首帧图像符合角色、地点、道具和风格，下一步应把它转成动态片段。
2. 视频生成远比单图昂贵，必须建立更严格的显式选择、预算、任务恢复和下载持久化机制。
3. 万相等视频接口采用异步任务：提交与轮询分离。若只写一个阻塞调用，程序中断后容易重复提交、
   重复扣费。
4. 视频结果 URL 通常短期有效，成功后必须立即下载、校验、落盘并记录 SHA256。
5. Stage 16 配音和 Stage 17 合成需要稳定、逐 Shot 的本地视频片段与 provenance。

---

## 3. 本阶段目标与完成后的系统变化

### 3.1 目标

- 打开 `video` Provider 模态，提供 Mock 与百炼原生异步视频 Provider。
- 只允许 Stage 14 重新验证后合格的图片进入视频生成。
- 一张合格分镜图对应一个独立视频片段，不在本阶段拼接整场或整集。
- 从 Shot/CameraPlan 确定性生成运动提示词，不额外调用文本模型。
- 异步提交后立即原子保存 `task_id`，支持中断后继续轮询，禁止自动重复提交。
- 成功 URL 立即下载为本地 MP4，进行路径、大小、容器签名和 SHA256 校验。
- 复用 Review 三态；approved 才把 `ShotAsset(kind="video")` 挂回 Shot 并写新 Storyboard 版本。
- 新增视频独立 manifest、调用账、成本估算、预算和 resume。
- 在 ProjectOrchestrator 中加入默认关闭的视频段。

### 3.2 完成后的链路

```text
Stage 13 generated image
  + Stage 14 approved-like Review / qa_decision=pass
  + Canonical Shot / CameraPlan / duration
       ↓
VideoStage preflight（重新验证，不信任 downstream_eligible）
       ↓
deterministic motion prompt
       ↓
VideoGenerationProvider.submit_video()
       ↓ 立即原子保存 task_id
VideoGenerationProvider.get_video_task()（resume 只轮询原任务）
       ↓
短期 URL → 受控下载 → 本地 MP4 + SHA256
       ↓
Review
       ↓
generated_videos/manifest.json
  + ShotAsset(kind="video")
  + Storyboard 新版本
       ↓
Stage 16 Audio / Stage 17 Composition
```

---

## 4. 范围与非目标

### 4.1 本阶段范围

- 首帧图生视频（I2V）。
- 每个 Shot 独立生成一个 clip。
- 一个输入首帧；文本运动提示；显式时长与分辨率。
- Mock + 百炼 DashScope 原生异步协议。
- 提交、轮询、恢复、下载、校验、Review、manifest、Storyboard 回挂。

### 4.2 本阶段不做

- 文生视频。
- 首尾帧生视频。
- 视频续写、视频编辑、参考视频、多角色参考视频。
- 多 Shot 合并成一个模型请求。
- 镜头剪辑、转场、字幕、配音、混音、成片封装。
- 视频内容 VLM QA；若需要应另立后续 Stage。
- 本地部署视频大模型。
- 真实 API 冒烟执行。
- 自动选择、切换或 fallback 到任何具体模型。

上述均为 `N/A` 或后续 Stage，执行会话不得顺手扩展。

---

## 5. 总控架构决策

### D15-01：以“每 Shot 一个 clip”为最小生成单元

选择逐 Shot 生成，而不是整场一次生成。理由：

- 与 Canonical Shot、Stage 13 图片、Stage 14 QA 一一对应；
- 失败、重试、审核和成本可独立；
- Stage 17 可以按镜头顺序剪辑；
- 避免模型自行切镜导致结构失控。

### D15-02：Stage 14 准入必须重新计算

VideoStage 不得只读取 `downstream_eligible=true`。对每个候选图片必须重新验证：

1. Stage 13 manifest 的 generated 记录唯一且结构合法；
2. 生成图仍在项目目录内，磁盘 SHA256 与 manifest 一致；
3. Stage 14 QA index 中 image_id 唯一；
4. index 的 generated path/SHA、shot_ref 与 Stage 13 完全一致；
5. QA Artifact 路径受 containment 保护，文件存在且 hash 一致；
6. 使用 Stage 14 的严格校验函数重新验证 Artifact，并重算 `qa_decision`；
7. Workflow、Step 和 Review 均为 approved-like；
8. 本地重算 `qa_decision == "pass"`；
9. index 自报 `downstream_eligible` 若与重算不一致，fail-loud。

失败发生在视频 Provider 构造和调用之前。

### D15-03：异步任务由编排层管理，任务 ID 先落盘

Provider 提供显式 `submit_video()` 与 `get_video_task()`。VideoStage 流程：

```text
submit 成功获得 task_id
→ 立即把 submitted 条目原子写入 manifest
→ 再开始 poll
```

进程中断后，resume 只能继续查询已保存的 task_id。不得自动重新 POST。

若任务返回 `UNKNOWN`、task_id 过期、状态非法或无法确认是否提交成功，状态记为 failed/stale，
要求人工决定是否重新生成。禁止“猜测失败后自动再提交”。

### D15-04：提交、轮询和下载使用同一计数器的不同 kind

不得新造第二套请求计数器：

```text
video           真实任务提交（可能计费），硬上限 1
video_poll      查询任务状态，有限轮询上限
video_download  下载成功结果，单任务最多 1 次成功下载流程
```

全部使用既有 `APIRequestCounter`。`MAX_REAL_VIDEO_SUBMISSIONS = 1`；真实提交默认最多一个。
poll/download 不占“生成提交数”，但每个真实网络请求仍须进入总审计。

提交 POST 自动 retry 为 0，避免响应丢失时重复创建任务。轮询 GET 可对确定的瞬时错误做有界 retry，
但必须受最大 poll 次数和超时约束。

### D15-05：模型和 Provider 只来自显式配置与用户授权

代码中不得硬编码真实模型名。Config 的 `bailian_video.default_model` 初始保持空串。

真实运行前必须向用户列明：

```text
provider
精确 model
region / endpoint
输入 image_id / shot_ref
resolution
duration
最大提交次数
已确认免费额度，或“额度未知，可能计费”
估算费用，或“价格未知”
```

用户未明确同意该精确组合时，不得运行。不得从可用模型列表中自行挑选，不得在失败后切换模型。

### D15-06：运动 Prompt 确定性生成

Prompt 只能由以下已批准/Canonical 信息确定性组装：

- Shot `subject`、`visual_description`、`actions`；
- `CameraPlan.shot_size/camera_angle/camera_movement`；
- `composition`、`continuity`；
- Stage 14 精确 profiles 中的非空 `negative_cues`；
- 固定约束：保持首帧人物身份、服装、地点、构图和风格；只描述运动与镜头变化。

禁止再调用文本模型“润色 Prompt”。Provider 参数 `prompt_extend` 缺省为 `False`。
manifest/日志只存 `prompt_hash` 与长度，不存完整 Prompt。

### D15-07：Review 与二进制产物

- approved：attach video asset；
- rejected：保留本地文件和审计条目，不 attach；
- approved_with_edit：fail-loud。视频是二进制，当前没有确定性的“编辑后内容”来源；
- 任何审核结果都不能覆盖 Stage 13 图片、Stage 14 Artifact 或原 Storyboard。

### D15-08：resume 身份锚点

完整 resume 命中至少要求：

```text
source image_id/path/SHA
QA artifact path/SHA + 本地重算结果
shot_ref + motion input hash
prompt_hash
provider + model + protocol
resolution + requested duration
task_id
output path/SHA（已完成项）
Review 状态（已完成项）
```

任何锚点变化都不得复用旧 clip。已有 submitted/running task_id 只允许继续 poll；不得重新 submit。

---

## 6. 可选方案与取舍

### 方案 A：同步阻塞 `generate_video()`

实现简单，但任务提交后若进程中断，task_id 尚未持久化，可能重复提交和重复扣费。否决。

### 方案 B：Provider 暴露 submit/poll，编排层原子保存任务（采用）

代码稍多，但可恢复、可审计，能区分“生成提交”和“状态查询”，适合昂贵异步任务。

### 方案 C：一次请求生成整场多镜头

模型可能支持，但会破坏 Shot 级控制、审核和后续剪辑映射。本阶段否决。

### 方案 D：先只做 Mock，不接真实协议类

可降低代码量，但无法验证真实异步协议形状。采用 Mock + 可注入 transport 的真实类；测试全用 fake
transport，仍保持 0 网络。

---

## 7. 官方协议依据与实现边界

协议资料只允许参考阿里云官方文档，访问日期 2026-10-02：

- 万相 2.7 图生视频 API：
  `https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference`
- 视频生成模型能力表：
  `https://help.aliyun.com/zh/model-studio/video-generate-edit-model/`
- 异步任务管理：
  `https://help.aliyun.com/zh/model-studio/manage-asynchronous-tasks`

已确认的协议事实：

- HTTP 为异步流程：创建任务 → 用 task_id 轮询；
- 创建请求需要 `X-DashScope-Async: enable`；
- 新版图生视频 payload 使用 `input.media`，首帧类型为 `first_frame`；
- 时长、分辨率直接影响费用；
- 成功结果 `video_url` 有效期有限，必须及时下载；
- 模型、endpoint 和 API key 必须属于同一地域。

执行会话若发现官方协议已变化，应暂停并提交差异证据，不得凭猜测兼容多个协议。

---

## 8. 当前相关代码

必须阅读：

```text
v2/llm/providers/base.py
v2/llm/providers/registry.py
v2/llm/providers/bailian_image.py
v2/llm/providers/mock_image.py
v2/llm/request_counter.py
v2/config.py
v2/core/shot.py
v2/orchestration/image_stage.py
v2/orchestration/image_qa_stage.py
v2/orchestration/pipeline.py
v2/orchestration/state.py
v2/logs/stage14_image_qa/stage14_report.md
```

优先复用 Stage 13 的：Config 工厂、可注入 transport、错误翻译、下载/落盘、manifest、Review、
Storyboard 新版本和账本模式；优先复用 Stage 14 的：安全路径、SHA、精确 QA Artifact 校验和
approved-like 判定。禁止复制后悄悄形成不同语义。

---

## 9. 输入、输出与数据结构

### 9.1 输入

```text
Canonical Storyboard / Shot
generated_images/manifest.json
quality/qa_manifest.json
Stage 14 QA Artifact
本地生成图
run_spec 显式选择与视频参数
```

### 9.2 run_spec 新字段

```text
generate_videos: bool = False
video_image_ids: list[str]            # 真实 Provider 必须显式非空；保持用户顺序
max_videos: int = 1                   # 实际 cap=min(value, 1)
video_resolution: str                 # 缺省来自 Config；首版只允许 Config capabilities 声明值
video_duration_overrides: dict        # shot_ref/image_id → 秒数，可选
video_resume: bool = True
video_max_polls: int                  # 有界，且不得超过代码安全上限
video_poll_interval_seconds: float    # 测试注入 0；真实值走 Config
video_prompt_extend: bool = False
video_watermark: bool                 # 显式值；缺省来自 Config
video_seed: int | None
```

当 `generate_videos=False` 时，其他字段不应触发 Provider 构造、文件读取或目录创建。

### 9.3 时长规范化

时长来源顺序：显式 override → `Shot.duration`。必须是有限正数。

Provider 要求整数时，使用明确的 half-up 规则规范化为整数，并同时记录原始时长与请求时长。
规范化后若超出 Config capability 的 min/max，调用前失败；禁止静默 clamp。无有效时长时零调用失败。

### 9.4 Video task result

在 `v2/llm/providers/base.py` 增加最小结构化结果，不移除既有 `generate_video()`：

```text
VideoTaskResult
  task_id
  status             submitted | pending | running | succeeded | failed | canceled | unknown
  model
  request_id
  video_url          只在内存中短暂使用
  duration_ms
  retry_count
  error_code
  error_message      脱敏安全文本
  usage              dict（仅厂商返回的最小统计）
```

`VideoGenerationProvider` 新增：

```text
submit_video(prompt, *, media, duration, resolution,
             negative_prompt="", prompt_extend=False,
             watermark=False, seed=None) -> VideoTaskResult

get_video_task(task_id) -> VideoTaskResult
```

保留 `generate_video()` 作为兼容/便捷接口，但 VideoStage 不使用它完成真实异步流程。

### 9.5 视频 manifest

路径：

```text
v2/projects/<project_id>/generated_videos/manifest.json
```

顶层：

```json
{
  "version": "1",
  "project_id": "...",
  "videos": []
}
```

每项至少包含：

```text
video_id
shot_ref
source_image_id
source_image_path
source_image_sha256
qa_artifact_path
qa_artifact_sha256
motion_input_hash
prompt_hash / prompt_length
provider / model / protocol
resolution
source_duration / requested_duration
seed
prompt_extend / watermark
task_id / request_id
path / sha256 / mime_type / bytes
estimated_cost / currency
submit_attempts / poll_requests / download_requests
submitted_at / completed_at
review
status            submitted | running | generated | rejected | failed | stale
error
```

不得持久化完整 Prompt、API key、Authorization、base64 首帧、签名 video URL 或传输响应正文。

### 9.6 输出文件

```text
generated_videos/VID_001.mp4
generated_videos/manifest.json
video_generation_calls.jsonl       # 测试/真实运行产物，必须 gitignore
Storyboard 新版本
ShotAsset(kind="video", ref="VID_001", detail="...")
pipeline run_report.video
```

文件名序号单调递增，绝不复用；写视频必须采用同目录临时文件 + fsync + `os.replace`，失败不留半截
正式文件。

---

## 10. 预计修改文件

### 10.1 必须新增

```text
v2/llm/providers/mock_video.py
v2/llm/providers/bailian_video.py
v2/orchestration/video_stage.py
v2/logs/stage15_video/run_tests.py
v2/logs/stage15_video/run_real_video.py
```

`run_real_video.py` 本阶段只实现脱敏计划与拒绝护栏。不得留下“所有前置满足后仍假装已调用”的路径；
若不实现真实执行，则明确返回非 0 并写 `REAL_EXECUTION_NOT_IMPLEMENTED`。

### 10.2 允许最小修改

```text
v2/llm/providers/base.py
v2/llm/providers/__init__.py
v2/llm/providers/registry.py
v2/llm/__init__.py
v2/llm/request_counter.py
v2/config.py
v2/orchestration/__init__.py
v2/orchestration/pipeline.py
v2/orchestration/state.py
v2/.gitignore
```

### 10.3 后续 C/D 会话文件

```text
v2/logs/stage15_video/test_report.json
v2/logs/stage15_video/stage15_report.md
CLAUDE.md
v2/docs/PROJECT_STATUS.md
v2/docs/HANDOFF.md
v2/docs/ROADMAP.md
```

代码实现会话不提前写完成态文档。

### 10.4 禁止修改

```text
FilmAgent/ 全目录
仓库根 .gitignore
v2/core/ 全目录
v2/workflow/ 全目录
v2/review/ 全目录
v2/memory/ 全目录
v2/context/ 全目录
v2/agents/ 全目录
v2/orchestration/image_stage.py
v2/orchestration/image_qa_stage.py
v2/logs/stage14_image_qa/test_report.json
Stage 13/14 历史测试及断言
历史 tag
```

若必须触碰禁改文件才能正确实现，停止并提交架构冲突，不得先改后报。

---

## 11. 子任务

### 15-B1：视频 Provider 公共契约

- 增加 `VideoTaskResult`；
- 给 `VideoGenerationProvider` 增加 submit/poll；
- 保留既有接口兼容；
- 错误使用既有 `LLMError` 家族或视频专用子类；
- 所有错误文本脱敏。

### 15-B2：Mock Video Provider

- 完全离线、确定性；
- submit 产生稳定 task_id；
- poll 可脚本化 pending/running/succeeded/failed/unknown；
- 成功结果由 `video_bytes()` 返回确定性最小 MP4-like fixture；
- fixture 至少含合法 ISO BMFF `ftyp` 签名，明确标记 Mock，不能冒充真实生成；
- 记录 calls，便于断言顺序与数量。

### 15-B3：Bailian 原生异步 Provider

- Config 驱动 base URL、paths、model、key env、capabilities；
- 通过 `load_provider_api_key()` 取 key，不在类中硬编码环境变量名；
- submit 使用 `X-DashScope-Async: enable`；
- 本地图片转 data URL 只存在请求内存，不写日志；
- POST 不自动重试；
- poll 解析全部终态，非法/未知 Schema fail-loud；
- transport/sleep 可注入；测试不得联网；
- 不持久化或日志输出签名 URL；
- URL 组合保证 `/api/v1` 恰好一次，兼容公共域与业务空间域配置。

### 15-B4：Config、Registry、预算与成本

- 新增 `bailian_video` 配置档，`default_model=""`；
- `protocol` 明确标识原生异步视频协议；
- capabilities 声明 resolution、duration、prompt_extend、watermark；
- 新增空的/显式的 `VIDEO_PRICING` 与 `estimate_video_cost()`；未知返回 `(None, currency)`；
- Registry 只打开 video，audio 继续拒绝；
- 缺 vendor/model/base_url/key/capability 明确失败；
- Mock 是缺省 0 API 档；不静默 fallback；
- 请求计数器加入 video/video_poll/video_download，同一审计体系。

### 15-B5：Stage 14 准入解析

- 读取 Stage 13 与 14 两份 manifest；
- 拒绝重复 image_id/video_id/shot_ref 映射歧义；
- 复用 Stage 14 安全函数和严格 Artifact 校验；
- 重算 Review + qa_decision；
- 在 Provider 构造前完成全部候选预检；
- 明确的 image_id 选择保持用户顺序。

### 15-B6：Prompt、时长和请求计划

- 建立确定性 prompt renderer；
- 生成 `motion_input_hash` 与 `prompt_hash`；
- 规范化 duration，记录原始值；
- capability 校验；
- 真实 Provider 要求显式 `video_image_ids`；
- 默认关闭、最大 1 个真实提交。

### 15-B7：异步 manifest 与 resume

- 提交成功后立即原子落 submitted 条目；
- 每次状态变化原子更新；
- resume 继续 poll 既有 task；
- ambiguous submit、unknown/expired task 禁止自动重提；
- 完整完成项按全部锚点决定是否复用；
- manifest 损坏、重复 ID、非法状态 fail-loud。

### 15-B8：下载、校验与 Review

- 成功 URL 只允许 `https`；必要时可通过 Config 允许受控测试 scheme；
- 受控下载大小、超时、redirect；
- 校验非空、最大字节数、MP4 `ftyp`；
- 原子落盘与 SHA256；
- Review approved/rejected/approved_with_edit 语义按 D15-07；
- approved 才回挂 Shot 并写 Storyboard 新版本。

### 15-B9：Pipeline 与状态接线

执行顺序：

```text
... → image → image_qa → video → completeness
```

- `generate_videos=False` 零副作用；
- Video Provider 惰性构造；
- 可以 `generate_images=False + run_image_qa=False + generate_videos=True` 消费既有产物；
- completeness 既有定义不改；
- state 只记录进度镜像，不成为第二状态机；
- run_report 加独立 `video` 段。

### 15-B10：正式测试与真实脚本护栏

- 建立单一 `run_tests.py`；
- 全部网络以 transport/fetch stub 隔离；
- 建立 `run_real_video.py` 脱敏计划与拒绝路径；
- 本阶段不执行真实路径。

---

## 12. 任务依赖与开发顺序

```text
B1 公共契约
 ├─→ B2 Mock
 └─→ B3 Bailian Provider
       ↓
B4 Config / Registry / Counter
       ↓
B5 Stage 14 eligibility
       ↓
B6 Prompt / plan
       ↓
B7 async manifest / resume
       ↓
B8 download / review / attach
       ↓
B9 pipeline
       ↓
B10 tests / real guard
```

B2 与 B3 可在 B1 完成后并行分析，但共享 base/exports/registry 的修改必须串行合并。
B5/B6 可以先写纯函数；B7–B9 必须串行完成。最终测试必须在全部接线后运行。

---

## 13. 测试方案

正式入口：

```powershell
python v2/logs/stage15_video/run_tests.py
```

测试报告必须原子写：

```text
v2/logs/stage15_video/test_report.json
```

### T01：冻结边界与接口

- Stage 14 tag 存在且未移动；
- 禁改目录 0 diff；
- VideoTaskResult、Mock、Bailian、VideoStage 可导入；
- audio 仍拒绝；
- 无第二请求计数器或直接业务网络调用。

### T02：默认关闭与 Provider 惰性

- `generate_videos=False` 不读视频输入、不构造 Provider、不建目录、不写 manifest；
- `max_videos=0` 零提交；
- 无 eligible 候选零构造；
- pipeline 旧行为保持。

### T03：选择、映射与顺序

- `video_image_ids` 保持用户顺序；
- 未知、重复、空值、错误类型失败；
- image_id → shot_ref → Shot 必须恰好一次；
- 真实 Provider 未显式选择 image_id 时拒绝；
- cap 只允许一个真实提交。

### T04：Stage 14 准入门

- approved-like + local pass 通过；
- needs_review/fail/rejected/Workflow 非成功拒绝；
- 篡改 downstream_eligible 不能绕过；
- QA index 重复、Artifact 缺失/损坏/越界/hash 变化拒绝；
- image manifest path/SHA/shot_ref 变化拒绝；
- 所有失败均在 Provider 构造/submit 前。

### T05：Prompt 与 duration

- CameraPlan/动作/continuity 稳定进入 prompt；
- exact negative_cues 进入 negative prompt；
- 相同输入 hash 稳定；顺序变化使 hash 改变；
- 不包含绝对路径、key、base64；
- duration half-up、override、缺失、NaN/inf、越界；
- prompt_extend 默认 false；无额外模型调用。

### T06：Mock Provider

- submit/poll 状态序列；
- failed/canceled/unknown；
- 稳定 task_id 与 bytes；
- MP4 signature；
- Mock 0 网络、0 真实请求、0 成本。

### T07：Bailian 协议

- endpoint `/api/v1` 恰好一次；
- header、payload、first_frame media、parameters 正确；
- model 完全来自 Config；
- key 不泄漏；
- submit 0 retry；
- task_id/status/video_url Schema 严格解析；
- HTTP/JSON/厂商错误安全翻译；
- 公共域/业务空间域；
- fake transport 全程 0 网络。

### T08：异步持久化与崩溃恢复

- submit 后 poll 前 manifest 已有 task_id；
- pending/running 更新；
- 进程重启只 poll 不 submit；
- poll 超时保留 task_id；
- unknown/expired/ambiguous submit 不自动重提；
- 同一 task 不产生第二条冲突记录。

### T09：预算与请求计数

- video submit 第 2 次在发送前被拒；
- poll/download 独立计数且受上限；
- submit/poll/download 全部进入同一 APIRequestCounter 审计；
- retry 不绕过计数；
- 多图片处理在预算耗尽后不继续提交；
- report 计数与 counter 一致。

### T10：下载与文件安全

- 仅成功状态允许下载；
- https scheme、超时、大小上限、redirect；
- 空文件、非 MP4、超限、下载中断失败；
- 原子写失败不留正式文件/tmp；
- SHA256 正确；
- 签名 URL 不进 manifest/log/error；
- 路径逃逸与同名前缀兄弟目录拒绝。

### T11：manifest 与 resume

- 顶层/条目严格 Schema；
- video_id 单调递增；
- 重复 ID/非法状态 fail-loud；
- 完整锚点命中零 submit/poll/download；
- 每个 stale 条件触发明确行为；
- completed output 缺失/hash 变化不能误命中；
- 不持久化 prompt/key/base64/URL。

### T12：Review、Storyboard 与 ShotAsset

- approved 才 attach；
- rejected 保留证据不 attach；
- approved_with_edit fail-loud；
- attach 幂等；
- 只新增一个 video ShotAsset，其他 Shot 字节不变；
- 原 Storyboard 不覆盖，新版本保留；
- Stage 13/14 产物 0 修改。

### T13：pipeline、多场景与报告

- 顺序 image→image_qa→video→completeness；
- 消费既有产物模式；
- 多场景数据隔离；
- 一项失败不造成错误重复提交；
- run_report 字段、状态、成本和计数准确；
- completeness 定义不变；
- state 只是镜像。

### T14：真实脚本护栏与泄漏

- 默认打印脱敏计划并非 0 退出；0 网络/0 API；
- 缺 tag/project/image/provider/model/key/region/quota-cost声明/用户开关任一即拒绝；
- 最大提交数强制 1；
- 显示精确 provider/model/resolution/duration/image_id；
- 不改 Config、不 fallback、不显示 key；
- 代码库无 mp4、图片、JSONL、key、base64、临时项目、缓存。

---

## 14. 回归测试

开发顺序：

1. 开工前，在 Stage 14 已封存且工作区干净时运行一次 Stage 14 套件，确认基线；
2. B 会话只跑 Stage 15 定向/基础测试；
3. C 会话跑 Stage 15 完整套件；
4. C 会话再跑 Stage 14 历史套件一次；
5. 若历史套件仅因合法未提交 Stage 15 文件触发 pre-commit 清洁度护栏，必须逐项举证；
6. 任何功能失败不得豁免；
7. commit 后总控再做一次最终验收。

不得为了得到 rc=0 修改历史断言或隐藏工作区改动。

---

## 15. 真实 API、额度和模型切换规则

本代码/测试阶段必须保持：

```text
真实视频提交 = 0
真实轮询 = 0
真实下载 = 0
网络请求 = 0
自动模型切换 = 0
```

即使环境里已经存在 API key、控制台显示免费额度，也不能真实调用。

未来真实冒烟必须另行取得用户对**精确 provider + 精确 model**的明确批准。用户曾展示的候选模型
或免费额度截图只说明“可能可用”，不构成永久授权；执行前仍须说明剩余额度是否已经确认。

失败后禁止自动切换到其他模型，包括同系列快照、flash/plus、其他供应商或本地模型。

---

## 16. 异常处理

- 本地契约失败：`OrchestrationError`，Provider 0 构造/0 调用。
- Provider 配置错误：明确指出缺 vendor/model/base_url/key/capability，不回落 Mock。
- 提交 HTTP 失败：安全错误；不自动 retry；若是否创建任务不明，标 ambiguous 并停止。
- 任务 failed/canceled/unknown：记录安全 code/message，不自动重提。
- poll 超时：保留 task_id 和 running 状态，供下次 resume；不提交新任务。
- 下载失败：保留 succeeded task_id，可在 resume 中只重试下载；不得重新生成。
- 文件校验失败：不 attach，不把损坏文件标 generated。
- Review rejected：保留审计，不 attach。
- 预算耗尽：发送前拒绝。
- 发现需修改禁改文件或 Stage 14 契约：停止并报告总控。

---

## 17. 文档要求

B 会话：

- 为新模块写清职责、计数点、异步恢复和安全边界；
- 不更新项目完成状态。

C 会话：

- 保存机器报告与缺陷修复记录；
- 如实记录历史回归边界。

D 会话：

- 新增 `stage15_report.md`；
- 更新 `CLAUDE.md`、`PROJECT_STATUS.md`、`HANDOFF.md`、`ROADMAP.md`；
- 记录真实视频连通性是否验证；
- 记录用户批准的模型与调用次数；未运行则明确写未验证。

---

## 18. Git 要求

- Stage 15 单独 commit，不能混入 Stage 14 文件；
- 不提交生成的 `.mp4`、图片、JSONL、API key、base64、临时目录和缓存；
- `v2/.gitignore` 增加项目视频产物与视频账本规则，仓库根 `.gitignore` 不动；
- B/C 会话不 commit、不 tag；
- D 准备 intended file list；
- 只有总控验收通过且用户明确批准后，才允许 commit/tag；
- 建议 commit：`feat(v0.2): add image-to-video clip generation (stage15)`；
- 建议 tag：`v0.2-stage15`；
- 历史 tag 永不移动、永不 force。

---

## 19. Acceptance Criteria

1. Stage 14 已独立封存，Stage 15 diff 不混入 Stage 14 commit。
2. Video Provider 的 submit/poll 契约、Mock 和 Bailian 原生实现完成。
3. Registry 只打开 video，audio 仍拒绝；默认 Mock 0 API。
4. Stage 14 准入由本地重新验证，不能只信任 downstream_eligible。
5. 视频 Provider 在候选预检完成前不构造。
6. 一图一 Shot 一 clip；Prompt 确定性且不额外调用模型。
7. submit 后 task_id 在 poll 前原子落盘；resume 不重复提交。
8. video/video_poll/video_download 进入同一计数器，真实提交硬上限 1。
9. POST 不自动 retry；poll 有界；unknown/ambiguous 不自动重提。
10. 视频 URL 及时下载，不持久化签名 URL；MP4、大小、路径和 SHA 校验完成。
11. Review 三态按 D15-07；approved 才 attach video asset。
12. Storyboard 只新增版本，不覆盖；Stage 13/14 产物不变。
13. Pipeline 默认关闭零副作用，completeness 既有定义不变。
14. manifest/resume 全部锚点与 stale 条件通过测试。
15. Stage 15 正式测试全部通过；历史功能回归无失败。
16. 真实提交/轮询/下载、网络、自动模型切换均为 0。
17. 无密钥、base64、签名 URL、视频文件、JSONL、缓存或临时目录进入 Git。
18. 真实模型未硬编码，未获得用户精确批准前不能运行。
19. 文档和阶段报告完整。
20. 最终 commit/tag 仅在总控验收和用户批准后执行。

---

## 20. 发现架构问题时

执行会话必须暂停并向总控提交：

1. 冲突文件与行号；
2. 最小复现；
3. 当前契约为何无法满足；
4. 至少两个可选方案；
5. 对 Stage 13/14、预算、resume、兼容性和 Git 的影响；
6. 推荐方案及理由。

禁止自行扩展到首尾帧、视频拼接、视频 QA、音频或新 Schema。

---

## 21. 最终交付物

执行会话最终返回：

1. 新增/修改文件清单；
2. Provider 契约和异步状态机说明；
3. Stage 14 准入重新验证证据；
4. submit-before-poll 持久化与 crash resume 证据；
5. 请求计数、预算和无重复提交证据；
6. 视频下载/校验/落盘/Review/attach 证据；
7. Stage 15 测试总数与分组；
8. Stage 14 历史回归结果；
9. Git/冻结边界/泄漏扫描；
10. 明确声明真实 API、网络和模型切换均为 0；
11. 未 commit、未 tag；
12. 进入 C 或 D 的建议。

---

## 22. 给代码执行会话的直接指令

```text
你现在执行 FilmAgent V0.2 Stage 15 的代码实现。

工作目录：D:\AI\FilmAgent-main
唯一任务规格：v2/docs/stages/Stage_15.md

先检查 v0.2-stage14 tag 与工作区。如果 Stage 14 尚未独立 commit/tag，立即停止并报告，
不得把 Stage 14 与 Stage 15 混在一个 commit。

硬前置满足后，严格按规格实现：VideoTaskResult、Mock Video Provider、百炼原生异步
Video Provider、video Config/Registry/预算/成本、Stage 14 准入重算、VideoStage、异步
task_id 持久化与 resume、受控下载、manifest、Review、ShotAsset 回挂和 pipeline 默认关闭接线。

只实现“通过 Stage 14 的首帧图 → 每 Shot 一个视频片段”。不要实现文生视频、首尾帧、续写、
编辑、多镜头合并、音频、成片合成或视频 QA。

本会话真实 API、真实轮询、真实下载、网络请求和模型切换必须全部为 0。模型名只来自 Config，
不得硬编码，不得自动 fallback。只使用 fake transport、Mock 和本地临时文件测试。

完成基础实现和定向测试后停止，返回文件清单、测试结果、静态证据、开放问题，并明确声明
未 commit、未 tag、未运行真实 API、未切换模型。
```
