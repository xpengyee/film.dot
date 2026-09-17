# HANDOFF.md —— FilmAgent 交接文档

> 给下一个 **WorkBuddy 会话**的交接入口。读完本文件后，按第 4/5 节顺序读取其余上下文。

## 1. 当前状态

```text
当前状态：V0.2 Stage 13（Image Generation 真实出图）**代码/测试/报告已交付，
          待人工验收 + commit/tag**（未自动 commit、未自动 tag）
最近稳定版本：v0.2-stage12（`git rev-parse v0.2-stage12` 查精确 hash）
最近 commit：见 `git log --oneline -5`（不在此写死）
下一阶段：Stage 14
Stage 14：VLM QA（生成图 vs 参考图 → 按 approved 档案字段逐项裁决）
          ★ Stage 13 已备好输入契约：manifest 记录 profile_versions +
            reference_images 的 image_id + sha256（Stage 14 不需返工）
Stage 13 交付要点：
  - `image` 模态开通（mock + 真实 OpenAI 兼容档）；video/audio 仍被工厂拒绝
  - 编排层新增 image 段（v2/orchestration/image_stage.py）
  - 三重护栏：默认不出图 / 预算硬上限（min(max_images, 3)）/ 绝不伪造出图
  - 图像独立记账 image_generation_calls.jsonl（与 token 账分开，不造第二套账）
  - ★ 真实出图**未执行**（人工四项前置未齐 → 如实报告阻塞）
开工方式：**新开一个 WorkBuddy 会话**（不要用 `claude` CLI，见 §4）
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
| V0.2 Stage 13 Image Generation | ★ 待人工批准后 commit；`git rev-parse v0.2-stage13` 查精确 hash | `v0.2-stage13`（**尚未创建**） |

约束：`FilmAgent/` 0 diff；`v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9` /
`v0.2-stage10` / `v0.2-stage11` / `v0.2-stage12` **七个 tag** 永不移动；
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

## 4. 新 WorkBuddy 会话（Stage 14）如何开始

> **不要使用 `claude` CLI 或任何命令行 TUI 工具。** 本项目一律用 **WorkBuddy 会话**
> 执行阶段（总控与执行分离见 `ORCHESTRATION.md` §1.1）。

1. **在 WorkBuddy 中新开一个会话**（每个 A/B/C/D 各自一个，不吃旧会话的长上下文）。
   工作目录指向 `D:\AI\FilmAgent-main`。

2. 依次读取以下上下文：
   1. `CLAUDE.md`
   2. `v2/docs/PROJECT_STATUS.md`
   3. `v2/docs/HANDOFF.md`（本文件）
   4. `v2/docs/ORCHESTRATION.md`（总控工作方式 / 会话分工 / 铁律 / 环境陷阱）
   5. 最近 Stage Report：`v2/logs/stage13_image/stage13_report.md`
      （含契约笔记 `stage13_contract_notes.md` —— Stage 14 的输入契约就写在那里）
   6. 当前 Git 状态：`git status --short`、`git log -5 --oneline`、
      八个 tag 指向、`git diff -- FilmAgent/`
   7. **Stage 14 开工指令**（进入该阶段前由总控生成）
   8. 与 Stage 14 直接相关的代码：`v2/core/visual_registry.py`（参考图 = QA ground truth）、
      `v2/orchestration/image_stage.py`（★manifest 的 `profile_versions` /
      `reference_images` 就是 QA 的比对锚点）、`v2/llm/providers/openai_compatible_vision.py`
      （图文协议族，VLM QA 大概率同协议复用）、`v2/orchestration/pipeline.py`（QA 段接在哪）

3. **开工自检**（指令 §3.1）：

   ```bash
   cd /d D:\AI\FilmAgent-main
   env -u PYTHONPATH python v2/logs/stage13_image/run_tests.py            # 期望 rc=0（全绿）
   env -u PYTHONPATH python v2/logs/stage12_lifecycle/run_tests.py        # 期望 125/125
   git log --oneline -5 && git status --short && git diff -- FilmAgent/   # 期望干净
   ```

## 5. 给下一个会话的关键提醒

- **Stage 13 已交付（待人工验收）**：`image` 模态开通 + 真实文生图 Provider +
  图像预算护栏与独立记账 + 编排层 image 段。详见
  `v2/logs/stage13_image/stage13_report.md` 与 `stage13_contract_notes.md`。
  ★ **真实出图未执行**（人工四项前置未齐）；★ **VLM QA 是 Stage 14，不在 Stage 13 范围**。
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
- **★ Stage 14 的输入契约已就绪**（Stage 13 备好，不返工）：manifest 每条记录
  `profile_versions`（`{asset_id: version}`，QA 逐字段裁决要知道比的是哪版档案）+
  `reference_images`（`[{image_id, sha256}]`，比对锚点）+ `sha256`（落盘可校验）。
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
