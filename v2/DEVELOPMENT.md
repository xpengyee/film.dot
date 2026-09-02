# FilmAgent V0.2 开发规范

> 这份文件是阶段 6+ 接入大量 Agent 时的**铁律**。
> 违反任一规范 = 重新失控。

## 1. V0.1 不允许修改

`FilmAgent/` 下所有文件（尤其 `LLMCaller.py`、`main.py`、`Prompt/`、`Locations/`、
`Script/`、`api_key.txt` 读取方式）是冻结基准。**任何情况下不得改动。**

## 2. V0.2 所有新功能进入 v2/

所有新代码、新资产、新测试一律放在 `v2/` 内。禁止在 `FilmAgent/` 里加新文件。

## 3. Agent 不直接读取底层 JSON

Agent 不得自己 `open()` 读 `Memory / Assets / Artifacts / Project` 的 JSON 文件。
数据一律通过 `ContextAssembler` 或明确的服务接口（如 `MemoryStore`）获取。

## 4. Agent 必须通过 ContextAssembler 获取 Context

```text
✅ Agent → 接收 Context（由 ContextAssembler 按 needs 组装）
❌ Agent → 自己 read memory / read locations / read characters / read artifacts
```

Agent 在 `run(context)` 里只依赖传入的 `context`，不自己去磁盘翻文件。

## 5. Agent 不直接管理 Artifact 文件名

文件名、版本号（`scenes_5.json` → `_v2` → `_v3`）由 `ArtifactStore` 统一管理。
Agent 只返回 `StepOutput(artifact_name=..., data=...)`，写盘交给 Workflow 引擎。
Agent 绝不拼路径、不写文件。

## 6. Agent 不直接管理 Workflow 状态

继续 / 停止 / 重试 / SKIPPED 由 `WorkflowEngine` 决定。Agent 只返回产出，
不调用 `sys.exit`、不设置 run 状态、不决定下一步。

## 7. LLM 调用统一走 LLMClient

禁止 Agent 直接 `import requests` 或 `import openai` 自建调用。
一切 LLM 调用走 `llm.client.LLMClient`（统一错误转换 / 重试 / token 日志 / 脱敏）。
API key 由 `config.load_api_key()` 提供，Agent **绝不自己找 key**。

## 8. Token/Cost 统一记录

每次 LLM 调用由 `LLMClient` 自动写 `core.token_log`。Agent 不得自己写 token 账。

## 9. Memory 必须区分 candidate / approved

事实先以 `candidate` 身份进入 `MemoryStore.add_candidate_fact`，**必须经人工
approve_fact 才能成为 approved**。禁止 Agent 直接写 approved 事实。
`get_approved_facts()` 是 Context 的唯一事实来源。

## 10. 审核修改不能覆盖原始 Artifact

`approved_with_edit` 必须用 `ArtifactStore.save()` 写**编辑后的新版本**文件，
原始文件原封不动。禁止"打开原文件直接覆盖"。

---

## 模块边界

依赖方向（只允许向上，禁止反向、禁止循环）：

```text
core → (llm | memory | context) → workflow → review →（未来）agents
```

- `core` 不得依赖任何上层模块。
- `workflow` 不得依赖 `review`（`ReviewDecision` 常量定义在 `workflow/models.py`，
  `review` 从 `workflow` 导入它——方向是 `review → workflow`）。
- 新增模块先确认它放在哪一层，再写 import。

## 错误体系

- 错误必须"能区分来源"，不得用 `except Exception: pass` 吞掉。
- 分层：
  - **LLM 层**：`LLMError` 家族（`LLMTimeoutError` / `LLMConnectionError` /
    `LLMRateLimitError` / `LLMInternalError` / `LLMRequestError` /
    `LLMAuthenticationError`），带 `retryable` 标记。
  - **Workflow 层**：`WorkflowError` 家族（`StepError` / `ArtifactError` /
    `RetryError`）。
  - **Memory / Context / Review**：标准异常（`KeyError` / `ValueError`）+ 明确消息。
- 未知异常必须包成 `StepError` 记录后再失败，绝不静默。

## 日志规范

- LLM 调用：`core.token_log.LLMCallRecord`（含 timestamp / request_id / model /
  tokens / cost / duration_ms / success / retry_count / error / prompt_hash）。
  不记 key、不记 prompt 正文。
- Workflow 运行：`WorkflowRun.to_dict()`（含 run_id / project_id / workflow_id /
  status / 每 step 的 StepResult）。写入 `run_log_dir`。
- 修改已验收的稳定模块（`config.py` / `core/` / `llm/`）时，先跑回归再改。

## 敏感信息

- API key 绝不硬编码进 Python、绝不进入日志 / 异常 / 报告。
- 只显示脱敏形式（`config.mask_api_key`）。
- 新增密钥文件必须加进 `.gitignore`。

## 兼容层边界

- `V0.1 compatibility` 属于**边界层**，不是 V0.2 core 的散落逻辑。
- 现有兼容逻辑已集中在明确位置：
  - `core/models.py` 的 `from_v01_script / to_v01_script / from_v01_actors /
    to_v01_actors`（数据转换）；
  - `core/io.py` 的 `read_json` 容错（V0.1 字段名三变体、非法字符替换）。
- **新增** V0.1 兼容逻辑必须走边界层（adapter / 转换函数），
  禁止在业务 Agent 里到处判断 `scene_information / sceneinformation` 之类历史字段名。
