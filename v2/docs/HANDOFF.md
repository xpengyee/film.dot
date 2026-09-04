# HANDOFF.md —— FilmAgent 交接文档

> 给下一个 Claude Code 会话的交接入口。读完本文件后，按第 5 节顺序读取其余上下文。

## 1. 当前状态

```text
当前状态：V0.2 Stage 9（Provider Architecture）已完成并封存
最近稳定版本：v0.2-stage9
最近 commit：v0.2-stage9（本次交付 commit；`git rev-parse v0.2-stage9` 查精确 hash）
下一阶段：Stage 10
Stage 10：尚未开始（等待下一份正式开工指令，不要虚构其实现细节）
```

## 2. Git 锚点

| 里程碑 | commit | tag |
|---|---|---|
| V0.1 冻结基准 | `344c0c6b7c48497be934957c29fcae029cca6752` | `v0.1`（永久冻结） |
| V0.2 Stage 0–7 基线 | `300aae44ad1d704219822efcf55249d1d59ad118` | `v0.2-stage7` |
| V0.2 Stage 8 Canonical Schema | `abf8e88a983f13c437abe2b719c920d3c5c3c144` | `v0.2-stage8` |
| V0.2 Stage 9 Provider Architecture | `v0.2-stage9`（本次交付 commit；`git rev-parse v0.2-stage9` 查精确 hash） | `v0.2-stage9` |

约束：`FilmAgent/` 0 diff；`v0.1` / `v0.2-stage7` / `v0.2-stage8` / `v0.2-stage9`
四个 tag 永不移动。

## 3. 协作规范（A/B/C/D 会话体系）

- **阶段是第一层边界**。默认：Stage N → 完成 → Git commit → 必要时 tag → 阶段结束
  → 新 Claude Code 会话 → Stage N+1。不要在一个长期 Claude 会话中连续完成
  Stage 9 → 10 → 11…。
- **A/B/C/D 默认 = 不同的 Claude Code 会话 / 终端**，不是四个必须同时存在的子 Agent：
  - A：分析 / 架构 → 输出 PLAN、ARCHITECTURE、风险、测试方案。
  - B：代码实现 → 实际编码、改必要文件、跑基础测试；不重复重新分析整个项目。
  - C：测试 / Debug → 测试、边界检查、回归、定位、修复、复验；不做无意义全项目重构。
  - D：文档 / 交付 → 整理成果、更新状态文档、必要时更新 CLAUDE.md / HANDOFF.md、
    生成阶段报告、确认 Git、commit / tag、形成最终交付。
- **按任务复杂度选择会话数**：小任务一个会话（A+B+C+D）；中型任务 A→B→C→D；
  大型任务可加 A2/A3 专项分析。不要因为形式而机械开大量会话。
- **子 Agent 仅作辅助**：专项代码侦察、独立研究、局部测试、并行检查。A/B/C/D 本身
  不是四个 subagent；阶段较大时优先用不同会话/终端，真正切断长期上下文、重建干净
  上下文、降低 Token 累积。
- **跨会话记忆靠文档**：CLAUDE.md / PROJECT_STATUS.md / HANDOFF.md / Stage Report /
  Git；聊天记录不作为项目长期记忆。

## 4. 新 Claude Code 会话（Stage 10）如何开始

1. 全新终端（新会话，不吃旧会话的长上下文）：

   ```bat
   cd /d D:\AI\FilmAgent-main
   claude
   ```

2. 依次读取以下上下文：
   1. `CLAUDE.md`
   2. `v2/docs/PROJECT_STATUS.md`
   3. `v2/docs/HANDOFF.md`（本文件）
   4. 最近 Stage Report：`v2/logs/stage9_provider/stage9_report.md`
      （另见 `stage9_c_report.md`）
   5. 当前 Git 状态：`git status --short`、`git log -5 --oneline`、
      四个 tag 指向、`git diff -- FilmAgent/`
   6. 与 Stage 10 直接相关的代码（在 `v2/` 内；Stage 10 开工指令在 `D:\AI\FilmAgent\`）

## 5. 给下一个会话的关键提醒

- **Stage 10 尚未开始**。开工指令文件位于 `D:\AI\FilmAgent\`（`v0.2开工指令*` 系列；
  Stage 10 的正式开工指令目前**尚未生成**）。以指令文件为准推进，在收到正式指令前
  **不要自己开工 Stage 10**，也不要虚构 Stage 10 的实现细节。
- 一切新工作只进 `v2/`；`FilmAgent/`（V0.1）永不修改。
- API key 绝不硬编码、绝不进日志/异常/报告；只显示脱敏（`config.mask_api_key`）。
- Stage 9 之后的新约定（务必遵守）：业务 Agent 只认抽象 `Provider`（`v2/llm/providers/`），
  Agent / Router / Provider / Model 职责边界见 `PROJECT_STATUS.md` §5；Mock 优先，真实
  API 只走独立 `run_real_*.py` 脚本（默认 `FILMAGENT_PROVIDER=mock` → 0 API）。
- 每阶段完成即停下等人工验收：不自动 commit/tag、不自动进入下一阶段。
- 测试约定、依赖方向、V0.1-freeze 断言见 `CLAUDE.md`。
