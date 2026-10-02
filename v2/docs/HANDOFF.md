# HANDOFF.md —— FilmAgent 交接文档

> 给下一个 **WorkBuddy 会话**的交接入口。读完本文件后，按第 4/5 节顺序读取其余上下文。

## 1. 当前状态

```text
当前状态：V0.2 Stage 14（VLM Image QA）**已完成并独立 commit/tag 封存**
最近稳定版本：v0.2-stage14（`git rev-parse v0.2-stage14^{commit}` 查精确 hash）
最近 commit：见 `git log --oneline -5`（不在此写死）
下一执行单元：Stage 15（Video Generation）按 `v2/docs/stages/Stage_15.md` 独立开工；
              **Stage 15 尚未开工，规格文件未纳入 Stage 14 commit。**
Stage 14：VLM Image QA（生成图 vs 参考图 → 按 approved 档案字段逐项裁决）
          ★ 只读质量门：不修改 Stage 13 manifest / 生成图 / Storyboard / ShotAsset / registry
          ★ 精确版本不漂移（禁 active fallback）；images[0]=生成图，refs 按 manifest 顺序
          ★ qa_decision 只由本地确定性规则计算；downstream_eligible 是可重算派生值
          ★ D14-REF-01：reference image_id 全 registry 恰好一处命中，零/多命中调用前失败
          ★ Stage 15 准入 = Review approved-like ∧ qa_decision=="pass" ∧ resume 锚点有效
          ★ 真实多图 VLM 连通性**未验证**（run_real_image_qa.py 仅为 preflight/guard scaffold）
Stage 14 交付要点：
  - v2/agents/image_qa.py（ImageQAAgent/ImageQAStep + 严格 validator + 本地 qa_decision）
  - v2/prompts/v0.2/image_qa.txt（只含 checks+summary 的稳定输出契约）
  - v2/orchestration/image_qa_stage.py（ImageQAStage：预检 → 逐图 Workflow → 二次校验 →
    quality/qa_manifest.json 只读索引 → resume）
  - pipeline 默认关闭接线（run_image_qa 缺省 False，段顺序 … → image → image_qa → completeness）
  - 正式套件 198/198、rc=0、真实 API/网络/模型切换 0/0/0
  - ★ Stage 13 历史套件在封存前实测 144/146、rc=1（两个失败是**当时**合法未提交的 Stage 14
    变更触发的 pre-commit 清洁度护栏，**非**功能回归；封存后未重跑）
开工方式：**新开一个独立 AI 编码会话**（总控 / 执行分离，见 §4）
```

## 2. Git 锚点

| 里程碑 | commit | tag |
|---|---|---|
| V0.1 冻结基准 | `344c0c6b7c48497be934957c29fcae029cca6752` | `v0.1`（永久冻结） |
| V0.2 Stage 0–7 基线 | `300aae44ad1d704219822efcf55249d1d59ad118` | `v0.2-stage7` |
| V0.2 Stage 8 Canonical Schema | `abf8e88a983f13c437abe2b719c920d3c5c3c144` | `v0.2-stage8` |
| V0.2 Stage 9 Provider Architecture | `82a89b50d337663027e8ecd638f45d7b94eaa882` | `v0.2-stage9` |
| V0.2 Stage 10 Visual Asset Layer | `c8a3ed8282a767c3119ea29e012e7b8148d3ab3b` | `v0.2-stage10` |
| V0.2 Stage 11 StoryboardAgent | `v0.2-stage11`（`git rev-parse v0.2-stage11` 查精确 hash） | `v0.2-stage11` |
| V0.2 Stage 12 Project 生命周期编排 | `v0.2-stage12`（`git rev-parse v0.2-stage12` 查精确 hash） | `v0.2-stage12` |
| V0.2 Stage 13 Image Generation | `v0.2-stage13`（`git rev-parse v0.2-stage13` 查精确 hash） | `v0.2-stage13` |
| V0.2 Stage 14 VLM Image QA | `git rev-parse v0.2-stage14^{commit}` 查精确 hash | `v0.2-stage14` |

约束：`FilmAgent/` 0 diff；`v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9` /
`v0.2-stage10` / `v0.2-stage11` / `v0.2-stage12` / `v0.2-stage13` / `v0.2-stage14` **九个 tag** 永不移动；
仓库根 `.gitignore` 0 diff。

★ Stage 13 交付时的预期工作区改动（人工验收时逐项核对，**不得多**）：

```text
M  CLAUDE.md                           （进度行 + 阶段 13 图像约定 + 架构表 + 测试命令 + 状态/下一步）
M  v2/docs/PROJECT_STATUS.md           （阶段表 + 里程碑 + 能力矩阵 + §5.7.1 图像链路）
M  v2/docs/HANDOFF.md                  （本文件）
M  v2/docs/ROADMAP.md                  （全景 + 格位 + 技术债 T-7~T-13 + 能力清单 + 成本表）
M  v2/.gitignore                       （§8.4 二进制防护规则；仓库根 .gitignore 仍 0 改）
M  v2/config.py                        （PROVIDERS["bailian_image"] + IMAGE_PRICING + estimate_image_cost）
M  v2/llm/__init__.py                  （纯追加导出）
M  v2/llm/providers/__init__.py        （纯追加导出）
M  v2/llm/providers/registry.py        （只开 image 分支；_image_kinds→_vision_vendors 消歧义）
M  v2/llm/request_counter.py           （MAX_REAL_IMAGE_REQUESTS + image 桶 + set_budget，纯追加）
M  v2/orchestration/__init__.py        （导出 ImageStage 等）
M  v2/orchestration/pipeline.py        （image 段接线 + style_spec）
M  v2/orchestration/state.py           （record_image_stage / image_entry，纯追加）
?? v2/llm/providers/mock_image.py
?? v2/llm/providers/openai_compatible_image.py
?? v2/orchestration/image_stage.py
?? v2/logs/stage13_image/              （run_tests.py / run_real_image.py / stage13_report.md /
                                        stage13_contract_notes.md / test_report.json）
★ 不得出现：*.png / *.jpg / *.jpeg / *.webp / images/ / generated_images/ /
             *.jsonl 记账 / llm_calls.jsonl / tmp_* / __pycache__
```

★ **Stage 14 独立封存的文件范围（以下 M/?? 是提交前清单，不代表当前工作区状态）**：

```text
M  CLAUDE.md                            （进度行 + 阶段 14 图像 QA 约定 + 架构表 + 测试命令 + 状态/下一步）
M  v2/docs/PROJECT_STATUS.md            （阶段表 + 里程碑 + 能力矩阵 + §5.7.2 图像 QA 质量门 + 遗留）
M  v2/docs/HANDOFF.md                   （本文件）
M  v2/docs/ROADMAP.md                   （全景 + 格位 + 技术债 + 能力清单 + Stage 15 前置）
M  v2/docs/stages/Stage_14.md           （仅追加交付/测试状态，不重写原设计契约）
M  v2/agents/__init__.py                （纯追加导出 ImageQAAgent / ImageQAStep）
M  v2/orchestration/__init__.py         （纯追加导出 ImageQAStage / QA_DIR_NAME / QA_MANIFEST_FILENAME）
M  v2/orchestration/pipeline.py         （_stage_image_qa 接线 + qa_provider + run_spec 文档）
?? v2/agents/image_qa.py
?? v2/orchestration/image_qa_stage.py
?? v2/prompts/v0.2/image_qa.txt
?? v2/docs/stages/Stage_14B.md          （总控开工指令）
?? v2/docs/stages/Stage_14C.md          （总控开工指令）
?? v2/docs/stages/Stage_14D.md          （总控开工指令）
?? v2/logs/stage14_image_qa/            （run_tests.py / run_real_image_qa.py / test_report.json /
                                         stage14_contract_notes.md / stage14_report.md）
★ 不得出现：*.png / *.jpg / *.jpeg / *.webp / generated_images/ / *.jsonl 记账 /
             llm_calls.jsonl / tmp_* / __pycache__ / .test_report.authoritative.json
★ 冻结边界：FilmAgent/ 0 diff；v2/core/ · v2/workflow/ · v2/review/ · v2/memory/ · v2/context/ ·
             v2/llm/ 0 diff；v2/orchestration/image_stage.py 0 diff；仓库根 .gitignore 0 diff；
             v0.2-stage13 未移动；历史断言一个未动
```

## 3. 协作规范（A/B/C/D 会话体系）

- **阶段是第一层边界**。默认：Stage N → 完成 → Git commit → 必要时 tag → 阶段结束
  → 新 WorkBuddy 会话 → Stage N+1。不要在一个长期会话中连续完成 Stage 9 → 10 → 11…。
- **A/B/C/D 默认 = 不同的 WorkBuddy 会话**，不是四个必须同时存在的子 Agent：
  - A：分析 / 架构 → 输出 PLAN、ARCHITECTURE、风险、测试方案。
  - B：代码实现 → 实际编码、改必要文件、跑基础测试；不重复重新分析整个项目。
  - C：测试 / Debug → 测试、边界检查、回归、定位、修复、复验；不做无意义全项目重构。
  - D：文档 / 交付 → 整理成果、更新状态文档、必要时更新 CLAUDE.md / HANDOFF.md、
    生成阶段报告、确认 Git、commit / tag、形成最终交付。
- **按任务复杂度选择会话数**：小任务一个会话（A+B+C+D）；中型任务 A→B→C→D；
  大型任务可加 A2/A3 专项分析。不要因为形式而机械开大量会话。
- **子 Agent 仅作辅助**：专项代码侦察、独立研究、局部测试、并行检查。A/B/C/D 本身
  不是四个 subagent；阶段较大时优先用不同会话，真正切断长期上下文、重建干净
  上下文、降低 Token 累积。
- **跨会话记忆靠文档**：CLAUDE.md / PROJECT_STATUS.md / HANDOFF.md / Stage Report /
  Git；聊天记录不作为项目长期记忆。

## 4. 新会话如何开始（当前：Stage 14 已封存；Stage 15 待独立开工）

> **本项目的推进方式是「总控 / 执行会话分离」**（见 `ORCHESTRATION.md` §1.1）——
> **不绑定任何特定工具**：任何能读写文件、执行命令的 AI 编码会话都能承担
> A（分析）/ B（实现）/ C（测试）/ D（交付）角色。**关键是换会话、切断长上下文，
> 而不是换工具。** 跨会话记忆一律靠本仓库的文档（`CLAUDE.md` / `v2/docs/` /
> 各阶段 `开工指令` / `stage*_report.md` / Git tag），**不依赖聊天记录**。

1. **新开一个会话**（每个 A/B/C/D 各自一个，不吃旧会话的长上下文）。
   工作目录指向 `D:\AI\FilmAgent-main`。

2. 依次读取以下上下文：
   1. `CLAUDE.md`
   2. `v2/docs/PROJECT_STATUS.md`
   3. `v2/docs/HANDOFF.md`（本文件）
   4. `v2/docs/ORCHESTRATION.md`（总控工作方式 / 会话分工 / 铁律 / 环境陷阱）
   5. 最近 Stage Report：`v2/logs/stage14_image_qa/stage14_report.md`
      （含契约笔记 `stage14_contract_notes.md` —— Stage 14 的契约与 D14-REF-01 就写在那里）
   6. 当前 Git 状态：`git status --short`、`git log -5 --oneline`、
      九个 tag 指向、`git diff -- FilmAgent/`
   7. **下一阶段开工指令**：`v2/docs/stages/Stage_15.md`（Stage 14 封存时未跟踪，
      不属于 Stage 14 commit；Stage 15 仍须独立准入）
   8. 与下一阶段直接相关的代码：`v2/orchestration/image_qa_stage.py`（Stage 15 准入的
      `downstream_eligible` 与 `qa_manifest.json` 来源）、
      `v2/agents/image_qa.py`（`qa_decision` 契约）、
      `v2/orchestration/pipeline.py`（QA 段接在哪、下一段接在哪）

3. **开工自检**（指令 §3.1）：

   ```bash
   cd /d D:\AI\FilmAgent-main
   env -u PYTHONPATH python v2/logs/stage14_image_qa/run_tests.py         # 期望 198/198，rc=0
   # Stage 13 历史实测 144/146、rc=1：当时未提交的 Stage 14 改动触发
   # T17a/T18-stage12 清洁度护栏；封存后尚未重新跑该套件，不预测其退出码。
   git log --oneline -5 && git status --short && git diff -- FilmAgent/   # 期望冻结边界干净
   ```

## 5. 给下一个会话的关键提醒

- **Stage 14 已独立封存**（`v0.2-stage14`）：VLM Image QA 只读质量门 ——
  `v2/agents/image_qa.py` + `v2/prompts/v0.2/image_qa.txt` +
  `v2/orchestration/image_qa_stage.py` + pipeline 默认关闭接线。详见
  `v2/logs/stage14_image_qa/stage14_report.md` 与 `stage14_contract_notes.md`。
  ★ **真实多图 VLM 连通性未验证**；`run_real_image_qa.py` 仅为 preflight/guard scaffold。
- **Stage 14 的 QA 约定（Stage 15+ 必须延续）**：
  - **只读质量门**：QA 绝不修改 Stage 13 `manifest.json` / 生成图 / Storyboard / `ShotAsset` /
    `VisualAsset` registry；`quality/qa_manifest.json` 只是 Artifact/Review 的**只读索引**，
    不是第二状态机，也不新增 `state.py` 字段。
  - **精确版本不漂移**：一律读 manifest `profile_versions` 指定版本（只接受
    `approved` / `superseded`），**禁止**回落 `active_version()`。
  - **★ D14-REF-01**：reference `image_id` 必须遍历 registry **全部资产的全部不可变版本**
    做全局反查，**恰好一处命中**才绑定 `asset_id+version+path+checksum`；
    零命中 / 多命中 / active fallback / 按路径或 checksum 猜归属 / 静默跳过**全部禁止**；
    仍须 manifest sha256 == Registry checksum == 磁盘 sha256 三方一致。
  - **送模顺序唯一**：`images[0]=generated`，`images[1:]=references` 严格按 manifest 顺序。
  - **本地裁决唯一**：`qa_decision` 只由本地规则算（mismatch→fail；否则 uncertain/not_visible→
    needs_review；全 match→pass）；模型自报的 qa_decision/provider/model/request_id/retry_count/
    created_at 一律忽略；严格 Schema（check 集合与 `FIELD_ORDER` 完全相等）。
  - **★ Stage 15 准入门**：`Review ∈ approved-like` ∧ `qa_decision == "pass"` ∧ resume 锚点有效；
    `downstream_eligible` 是可重算派生值，**不得仅凭该布尔值**绕过条件。
  - **默认关闭 + 惰性 Provider**：`run_image_qa` 缺省 `False`；预算
    `cap = min(max_qa_images, MAX_REAL_VISION_REQUESTS=3)` 下推 `vision` 桶，
    真实 QA Provider 必须 `budget_kind == "vision"`；第 N+1 次发送前拒绝。
  - **★ 开放问题（未修代码）**：`run_real_image_qa.py` 对 `bailian` 检查 `DASHSCOPE_API_KEY`，
    而 `config.PROVIDERS["bailian_vision"]` 用 `BAILIAN_API_KEY` —— **环境变量名不一致**，
    真实冒烟实现前必须先统一。
- **Stage 13 已封存**：`image` 模态开通 + 真实文生图 Provider +
  图像预算护栏与独立记账 + 编排层 image 段。详见
  `v2/logs/stage13_image/stage13_report.md` 与 `stage13_contract_notes.md`。
  ★ **真实出图未执行**（人工四项前置未齐）；★ **VLM QA 是 Stage 14，不在 Stage 13 范围**。
- **★ Stage 13-P 补丁（百炼原生档，2026-09-18）**：图像 Provider 现在**两档并存**，由 Config 的
  `protocol` 字段选择 —— 缺省 `"openai"` → `OpenAICompatibleImageProvider`
  （火山方舟 / OpenAI / 硅基流动 等 OpenAI Images 兼容端点）；`"dashscope_native"` →
  `BailianImageProvider`（百炼 DashScope 原生，同步/异步）。
  **换回来只改 Config，代码 0 行改动**。详见 `v2/logs/stage13_image/stage13p_native_provider.md`。
  ★ **2026-09-28 URL 修复**：`base_url` 必须表示已含 `/api/v1` 的 API base，默认 path 只是
  `/services/...` / `/tasks/...`；T20r–T20t 直接验证公共域同步、公共域异步提交+轮询、
  业务空间域的最终 URL，每条只含一段 `/api/v1`。本地定向 20/20，全量 146/146、
  rc=0、真实 API 0 次；修复待总控复核，`v0.2-stage13` 未移动。
  ★ 铁律延续：**默认不出图 / 预算硬上限 / 绝不伪造出图**；模型名一律走 Config，
  **连注释里也不许写具体型号**（套件 T16d 会扫）。
- **Stage 13 的编排约定（Stage 14+ 必须延续）**：
  - **默认不出图**：`run_spec["generate_images"]` 缺省 `False` —— 不显式打开就一张都不生成。
  - **预算硬上限**：`cap = min(run_spec["max_images"], MAX_REAL_IMAGE_REQUESTS=3)`；
    达到即拒绝（`APIBudgetExceededError`），**绝不发出第 N+1 次**；同一 cap 经
    `APIRequestCounter.set_budget("image", cap)` 下推到真实 HTTP 层（纵深防御）；retry 也计预算。
  - **出图 prompt 必须来自 `ComposedVisualInput.render_prompt()`**，**不得**绕过
    `VisualComposer` 自己拼 prompt（否则视觉一致性设计失效）。
  - **★ STYLE 是硬前置**：STYLE 不在 `core.shot.ASSET_KINDS` 里、无法从 Storyboard 自动收集，
    必须由 `run_spec["style_spec"]` 显式提供；不提供 → `compose()` 明确报错列缺口，**不硬出图**。
  - **记账分离**：token 账（`TokenLogger`）/ 请求计数（`APIRequestCounter` 分桶）/
    图像账（`image_generation_calls.jsonl`）三本账；图像账字段风格与 `TokenLogger` 对齐、
    **不含 token 概念**；**绝不**把图像成本塞进 token 账（计价单位不同）。
  - **落盘纪律**：`GEN_%03d` 单调递增、**绝不复用 ID、绝不覆盖文件**；sha256 记进 manifest；
    Storyboard 挂回走 **Artifact 新版本**（原文件字节不变）。
  - **resume 保护成本**：manifest 中已 `generated` 的 `shot_ref` 重跑时**直接跳过**
    （零 Provider 调用、零新产物），只把已有引用补挂回 Shot。
  - **诚实性**：图像审核遇 `approved_with_edit` → 明确抛 `OrchestrationError`
    （图像是二进制，无"编辑后内容"的确定性来源）；`auto_approve` 仍必标程序化模拟。
- **★ Stage 14 的输入契约（Stage 13 备好，已被 14-B/C 消费）**：manifest 每条记录
  `profile_versions`（`{asset_id: version}`，QA 逐字段裁决要知道比的是哪版档案）+
  `reference_images`（`[{image_id, sha256}]`，比对锚点）+ `sha256`（落盘可校验）。
  ★ Stage 15 起如需扩充 provenance（方案 C / Stage 13-P2），须另行设计 schema 版本与迁移，
  当前**不立项**。
- **Stage 12 已封存**：编排层 `v2/orchestration/`（`preflight.py` / `state.py` /
  `pipeline.py` / ★`image_stage.py`）是**最顶层**，只向下依赖；
  ★ **下面任何层不得 import orchestration**（Stage 12/13 套件 T01e 断言）。
- **Stage 12 的编排约定（Stage 13+ 必须延续）**：
  - **两段式**：`scene_gen_{sid}` 与 `storyboard_{sid}` **各一次 run**（`WorkflowEngine` 的
    `inputs` 是整次 run 共享的一个 dict，`StoryboardStep` 只读 `inputs["scene"]`、
    **完全忽略上游 Step 产出** → 同 run 装两 step 必然串场）；`inputs["scene"]` 必须**显式放入**。
  - **编排层写 Memory**：集级 Casting 通过后 `set_characters`（沿用同一 run 的 Casting Artifact）
    + `set_world`；★ **严禁 `approve_fact`**（事实批准是"人"的动作）。
  - **视觉资产只支持 approved / rejected**；遇 `approved_with_edit` → 明确抛 `OrchestrationError`。
  - **resume**：对已 approved 的单元**直接跳过、不调用引擎**（`state.json` 唯一事实源）；
    跳过前校验磁盘 artifact 仍存在。
  - **前置校验门**：把 Scene 喂给 Stage 11 前先 `preflight_check`（返回缺口清单、不抛异常）；
    ★ 必须先把落盘 dict `Scene.from_v01` 还原 `Line.type`，否则 move 行漏检。
  - **诚实性**：`run_report["review_mode"]` 必含 `"programmatic, NOT human review"`；
    **绝不**把 `auto_approve` 的产物描述成"已人工审核通过"。
- **Stage 11 的裁决已出（总控，2026-09-14）**：`normalize_storyboard()` 的两条防御性回锚
  （`actions` / `assets` 引用回锚）**予以批准**——属必要补全，非扩大范围。
  详见 `v2/docs/stages/stage11_plan.md` §9。
- 一切新工作只进 `v2/`；`FilmAgent/`（V0.1）永不修改；仓库根 `.gitignore` 也不动
  （运行产物忽略规则只在 `v2/.gitignore`）。
- API key 绝不硬编码、绝不进日志/异常/报告；只显示脱敏（`config.mask_api_key`）。
- Stage 9 约定：业务 Agent 只认抽象 `Provider`（`v2/llm/providers/`），
  Agent / Router / Provider / Model 职责边界见 `PROJECT_STATUS.md` §5。
- **Stage 10 约定（Stage 11+ 必须延续）**：语义与视觉分离 ——
  Shot **只存名字级引用 + 本镜头视觉增量**，**绝不**把角色外貌 / 地点环境写进 Shot；
  那些活在 `VisualAssetRegistry`，由 `VisualComposer` 在调用模型前一次性展开。
  硬证据：注册视觉资产前后 `Storyboard.to_dict()` **逐字节不变**。
- **Stage 11 约定（Stage 12+ 必须延续）**：受控词表**从 `core.shot` 常量渲染**（不手抄进 prompt）；
  warnings 统一放 `Storyboard.metadata["normalize_warnings"]`；Scene 经 `inputs` 传入，Agent 不读磁盘。
- Mock 优先，真实 API 只走独立 `run_real_*.py` 脚本（默认 `FILMAGENT_PROVIDER=mock` → 0 API）。
  图像的真实冒烟是 `v2/logs/stage13_image/run_real_image.py`（默认只体检，0 真实调用）。
- **跑测试务必加 `env -u PYTHONPATH`**（宿主注入 `sitecustomize` shim 劫持
  `shutil.rmtree` → 历史套件假失败，见 `ORCHESTRATION.md` §5.1）。
- **从仓库根跑完测试后必须删除根目录 `llm_calls.jsonl`**（`v2/llm/client.py` 默认写 cwd）。
- **复位清单是 6 个文件**（不是 4 个）：`stage3_report.json` / `stage3_console.txt` /
  `stage4_report.json` / `stage4_console.txt` / `stage6_report.json` / `stage7_report.json`
  —— 跑 stage8/9/10/11/12 的嵌套回归会连带重写 stage3 的两个产物（技术债 T-6 的新增面）。
  ★ **Stage 13 起该复位已自动化**：`v2/logs/stage13_image/run_tests.py` 的
  `_reset_history_products()` 会在套件起始与历史回归前后自动复位这 6 个文件，
  跑完工作区保持干净（技术债 T-6 已缓解）。
- **★ 二进制泄漏**（Stage 13 新增风险面，技术债 T-7）：生成的真实图片是二进制大文件，
  **绝不能进版本库**。`v2/.gitignore` 已补 §8.4 规则；交付前必做二进制扫描
  （stage13 套件 T15：`git check-ignore` 命中 / `git status` 无图片 / `v2/` 无二进制入库）。
- 每阶段完成即停下等人工验收：不自动 commit/tag、不自动进入下一阶段。
- 测试约定、依赖方向、V0.1-freeze 断言见 `CLAUDE.md`。
