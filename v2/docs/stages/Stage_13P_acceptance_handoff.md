# Stage 13-P 总控验收交接指令

> 用途：交给总控做 Stage 13-P 最终验收与后续阶段决策。

请对 Stage 13-P 补丁做最终验收与后续阶段决策。

## 一、本次验收方式

本次**不要重跑任何测试、不要发真实 API 请求、不要重复实现或修改代码**。

执行会话已经完成修复和完整回归。请仅基于现有代码 diff、测试报告、补丁记录和 Git 状态进行审查。

## 二、已完成内容

### 1. DashScope URL 修复

- `base_url` 统一表示已包含 `/api/v1` 的 API base。
- Provider 默认 path 改为 `/services/...` 和 `/tasks/...`。
- 不再构造 `/api/v1/api/v1/...` 错误 URL。

### 2. 最终 HTTP URL 测试

已新增 T20r–T20t，直接拦截并断言最终 `_request` URL，覆盖：

- 公共域同步请求。
- 公共域异步提交与任务轮询。
- 业务空间域。
- 每条最终 URL 只包含一段 `/api/v1`。

### 3. 测试结果

- T20 定向测试：**20/20**。
- Stage 13 完整回归：**146/146**。
- 退出码：**0**。
- 真实 API 请求：**0**。
- `git diff --check`：通过。
- `excused_history_fails=6` 均为既有 pre-commit 禁改护栏，没有功能失败。

### 4. 边界检查

- `FilmAgent/`：0 diff。
- `v2/core/`、`v2/workflow/`：0 diff。
- Git 状态无图片、密钥或 JSONL 账本泄漏。
- 未执行 commit。
- 未移动 `v0.2-stage13` tag。

## 三、主要审查材料

- `v2/docs/stages/Stage_13P_fix.md`
- `v2/llm/providers/bailian_image.py`
- `v2/logs/stage13_image/run_tests.py`
- `v2/logs/stage13_image/test_report.json`
- `v2/logs/stage13_image/stage13p_native_provider.md`
- `v2/docs/PROJECT_STATUS.md`
- `v2/docs/ROADMAP.md`
- `v2/docs/HANDOFF.md`

## 四、请总控完成的工作

1. 静态复核 URL 语义和 T20r–T20t 是否准确覆盖最终 URL，而不是只检查 path。
2. 核对补丁是否保持 OpenAI Images 兼容档不变，并且没有扩大公共接口、预算和编排边界。
3. 核对现有测试报告、Git 改动范围和文档表述。
4. 给出 Stage 13-P 最终 PASS/FAIL。

### 若验收 PASS

- 批准将当前 Stage 13-P 工作区改动提交。
- 该改动是 Stage 13 的补丁，**不创建 Stage 14，不移动 `v0.2-stage13` tag**。
- 提交后由总控开始设计 Stage 14 VLM QA 的正式规格。

### 若验收 FAIL

- 只列出具体阻塞文件、代码位置和必须修正的验收项。
- 不要求无目的地重跑全套测试。

## 五、诚实边界

本次只验证了 URL 构造及 Mock/桩件回归，**没有验证真实百炼连通性**。

真实出图仍需以下人工前置：

1. 配置具体模型名。
2. 注入 API Key。
3. 获得真实调用批准。
4. 登记图像模型单价。

上述真实出图前置不属于 Stage 13-P 补丁的验收阻塞。
