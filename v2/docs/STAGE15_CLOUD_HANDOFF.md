# FilmAgent Stage 15 云端接续指令

> 2026-10-26 的交接快照。供 Dot 或其他新会话使用；Stage 15 尚未封存。

## 当前事实

- 最近稳定锚点：`v0.2-stage14`，指向 `867872a506a71e6b679a869ca95268dd3db18d5a`；`v0.2-stage13` 指向 `3895c6c103f125d458fe648d80a625bf007601af`。
- Stage 15-B 已实现视频 Provider、Mock、Config/Registry/预算、VideoStage、pipeline 默认关闭接线和离线测试。2026-10-26 本地复跑 `python -B v2/logs/stage15_video/run_tests.py`：85/85、rc=0；机器报告在 `v2/logs/stage15_video/test_report.json`。
- 真实视频提交、轮询、下载、网络请求、模型切换均为 0。真实多图 VLM 连通性也仍未验证。
- Stage 15-C 独立测试、Stage 14 历史回归、Stage 15-D 报告与最终验收尚未完成。本快照是继续工作的起点，不是 Stage 15 完成宣告；不创建 `v0.2-stage15` tag。
- 本地 Git 原先无 remote。若云端会话拿不到包含本文件的 checkpoint commit，先取得正确仓库或 bundle；不要从公开同名 FilmAgent 仓库猜测来源。

## Dot 的下一步

1. 确认工作目录确实是包含本交接文件的 FilmAgent 仓库，读取 `git log -1 --oneline`、`git status --short`、`git remote -v`；核对 `v0.2-stage14`、`v0.2-stage13` 指向及 `git diff -- FilmAgent/` 为空。如缺少 checkpoint commit，停止并请求正确源码。
2. 依次读 `CLAUDE.md`、`v2/docs/PROJECT_STATUS.md`、`v2/docs/HANDOFF.md`、`v2/docs/ORCHESTRATION.md`、`v2/docs/stages/Stage_15.md`、`v2/logs/stage14_image_qa/stage14_report.md`、`v2/logs/stage15_video/test_report.json`，再检查 Stage 15 改动源码。
3. 以 **Stage 15-C 独立验证** 身份核对 `Stage_15.md` §13 的 T01–T14 与 §19 验收标准；运行 Stage 15 离线测试，核查每组证据。若发现缺口，只在规格允许的文件内最小修复并重测，保留真实 API、网络、模型切换为 0。
4. 在干净 checkpoint 上运行 Stage 14 历史套件，区分功能回归与清洁度护栏。记录命令、退出码、通过数、失败项；不要改历史断言。必要时按 `ORCHESTRATION.md` 的环境说明处理 `PYTHONPATH` 注入。
5. 检查冻结边界、密钥与二进制泄漏、Git intended file list。完成 C 后交给独立 D 会话整理 `stage15_report.md`，同步长期文档并请求总控验收。只有验收通过且用户明确批准才做 Stage 15 最终 tag；本 checkpoint 不代表最终 tag 授权。

## 继续工作时的硬边界

- `FilmAgent/` V0.1、`v2/core/`、`v2/workflow/`、`v2/review/`、Stage 13/14 产物与历史 tag 不得改。
- `generate_videos` 默认关闭。真实视频 Provider 需要用户对精确 provider、model、费用/额度、图片 ID 和调用次数另行批准；已有 API key 不是运行授权。
- Stage 15 只做“通过 Stage 14 的首帧图 → 每 Shot 一个视频片段”；不扩展到拼接、音频、视频 QA 或新 Canonical Schema。
- 发现规格与禁改边界冲突时，按 `Stage_15.md` §20 给总控证据、复现、影响和方案，暂停冲突实现。
