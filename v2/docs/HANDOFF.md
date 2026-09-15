# HANDOFF.md —— FilmAgent 交接文档

> 给下一个 **WorkBuddy 会话**的交接入口。读完本文件后，按第 4/5 节顺序读取其余上下文。

## 1. 当前状态

```text
当前状态：V0.2 Stage 12（Project 生命周期编排层）已完成并封存 ✅
最近稳定版本：v0.2-stage12（`git rev-parse v0.2-stage12` 查精确 hash）
最近 commit：见 `git log --oneline -5`（不在此写死）
下一阶段：Stage 13
Stage 13：Image Generation（真实出图）
          开工指令：**已生成** —— `D:\AI\FilmAgent\v0.2开工指令13`
Stage 14（其后）：VLM QA（生成图 vs 参考图 → 逐字段裁决）★2026-09-15 从 Stage 13 拆出
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

约束：`FilmAgent/` 0 diff；`v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9` /
`v0.2-stage10` / `v0.2-stage11` / `v0.2-stage12` **七个 tag** 永不移动；
仓库根 `.gitignore` 0 diff。

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

## 4. 新 WorkBuddy 会话（Stage 13）如何开始

> **不要使用 `claude` CLI 或任何命令行 TUI 工具。** 本项目一律用 **WorkBuddy 会话**
> 执行阶段（总控与执行分离见 `ORCHESTRATION.md` §1.1）。

1. **在 WorkBuddy 中新开一个会话**（每个 A/B/C/D 各自一个，不吃旧会话的长上下文）。
   工作目录指向 `D:\AI\FilmAgent-main`。

2. 依次读取以下上下文：
   1. `CLAUDE.md`
   2. `v2/docs/PROJECT_STATUS.md`
   3. `v2/docs/HANDOFF.md`（本文件）
   4. `v2/docs/ORCHESTRATION.md`（总控工作方式 / 会话分工 / 铁律 / 环境陷阱）
   5. 最近 Stage Report：`v2/logs/stage12_lifecycle/stage12_report.md`
   6. 当前 Git 状态：`git status --short`、`git log -5 --oneline`、
      七个 tag 指向、`git diff -- FilmAgent/`
   7. **Stage 13 开工指令**：`D:\AI\FilmAgent\v0.2开工指令13`（**已生成**，827 行完整规格）
   8. 与 Stage 13 直接相关的代码：`v2/orchestration/pipeline.py`（编排层，Stage 13 的出图
      stage 就加在这里）、`v2/core/visual_compose.py`（`ComposedVisualInput` → 图像 prompt）、
      `v2/llm/providers/base.py`（`ImageGenerationProvider` 预留接口，签名 0 改）、
      `v2/llm/providers/registry.py`（★当前对 image 的拒绝护栏 —— 本阶段要开）、
      `v2/llm/request_counter.py`（★预算分桶机制 —— 本阶段加 image 桶）、
      `v2/core/visual_registry.py`（参考图 = VLM QA 的 ground truth）

3. **开工自检**（指令 §3.1）：

   ```bash
   cd /d D:\AI\FilmAgent-main
   env -u PYTHONPATH python v2/logs/stage12_lifecycle/run_tests.py   # 期望 125/125
   git log --oneline -5 && git status --short && git diff -- FilmAgent/  # 期望干净
   ```

## 5. 给下一个会话的关键提醒

- **Stage 13 开工指令已生成**：`D:\AI\FilmAgent\v0.2开工指令13`。
  Stage 13 = **Image Generation（真实出图）** —— 打开 `image` 模态 + 真实文生图 Provider
  + 图像成本记账与**预算硬护栏** + 编排层出图段。★ **VLM QA 已拆为独立 Stage 14**
  （2026-09-15 总控裁决），**不在** Stage 13 范围。
  **开工前必须先封存 Stage 12**（指令 §3.0）；未覆盖的设计冲突**立即暂停向总控报告**，
  不要自行决定。★ 铁律：默认不出图；真实调用必须人工批准；**绝不用占位图冒充真实出图**。
- **Stage 12 已封存**：编排层 `v2/orchestration/`（`preflight.py` / `state.py` / `pipeline.py`）
  是**最顶层**，只向下依赖；★ **下面任何层不得 import orchestration**（Stage 12 套件 T01d/T01e 断言）。
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
- **跑测试务必加 `env -u PYTHONPATH`**（宿主注入 `sitecustomize` shim 劫持
  `shutil.rmtree` → 历史套件假失败，见 `ORCHESTRATION.md` §5.1）。
- **从仓库根跑完测试后必须删除根目录 `llm_calls.jsonl`**（`v2/llm/client.py` 默认写 cwd）。
- **复位清单是 6 个文件**（不是 4 个）：`stage3_report.json` / `stage3_console.txt` /
  `stage4_report.json` / `stage4_console.txt` / `stage6_report.json` / `stage7_report.json`
  —— 跑 stage8/9/10/11/12 的嵌套回归会连带重写 stage3 的两个产物（技术债 T-6 的新增面）。
- 每阶段完成即停下等人工验收：不自动 commit/tag、不自动进入下一阶段。
- 测试约定、依赖方向、V0.1-freeze 断言见 `CLAUDE.md`。
