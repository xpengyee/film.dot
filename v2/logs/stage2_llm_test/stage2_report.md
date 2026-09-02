# FilmAgent V0.2 阶段 2 —— 验收报告

**任务**：LLMClient + Token/成本统计
**日期**：2026-08-31
**性质**：LLM 调用层（第一次真实调用 DeepSeek，仅最小集成测试）

---

## 1. Git 基准

| 项目 | 值 |
|---|---|
| HEAD | `344c0c6b7c48497be934957c29fcae029cca6752` |
| v0.1^{commit} | `344c0c6b7c48497be934957c29fcae029cca6752` |
| git status --short | 仅 `?? v2/` |
| V0.1 是否修改 | **NO**（`git diff -- FilmAgent/` 为空） |

开工前、收工后均核验。

## 2. 新增/修改文件

```text
v2/config.py                 配置（base URL / key 读取 / 默认模型 / 超时 / 重试 / 价格）
v2/llm/client.py             LLMClient（messages / 超时 / 重试 / 错误转换 / request_id / 日志）
v2/llm/router.py             基础 Router（接口先行、策略后置，全部返回 deepseek-chat）
v2/core/token_log.py         TokenUsage / 成本估算 / TokenLogger（JSONL 日志）
v2/logs/stage2_llm_test/run_tests.py      9 组测试（14 项断言）
v2/logs/stage2_llm_test/stage2_report.json 测试报告
v2/logs/stage2_llm_test/stage2_console.txt 测试控制台输出
v2/logs/stage2_llm_test/stage2_report.md   本报告
v2/logs/llm_calls.jsonl      LLM 调用日志（每次调用一笔账）
```

没有动 `v2/core/io.py` / `models.py` / `validator.py` / `assets.py`（阶段 0/1 产物）。

## 3. LLMClient（v2/llm/client.py）

| 能力 | 实现 |
|---|---|
| messages | `client.chat([{role:system,...},{role:user,...},{role:assistant,...},...])`，正式支持多轮 |
| timeout | 默认 120s，放 config，不写死；单次请求不会无限挂死 |
| retry | 默认 `max_retries=2`（初始 1 次 + 重试 2 次）；**只重试** 超时/连接/限流/5xx；参数错误、认证失败等**不盲目重试**；指数退避（测试可设为 0） |
| error handling | 把 OpenAI/DeepSeek SDK 异常统一转成 `LLMTimeoutError/LLMConnectionError/LLMRateLimitError/LLMInternalError/LLMRequestError/LLMAuthenticationError`，绝不泄漏 SDK 异常给上层 |
| request_id | 每次调用一个 UUID，把 prompt/response/token/cost/error/retry 串成一条账 |
| 返回值 | 统一 `LLMResponse`（text / model / usage / request_id / duration / retry_count），Agent 不用自己解析 API |
| 架构边界 | LLMClient 不知道任何业务（不知道剧本/角色/步骤是什么），`step`/`agent` 只是传给日志的标签 |
| 可测试性 | `transport` 可注入；真实调用用 OpenAI 兼容库，测试用 mock 替代 |

**对比 V0.1 的改进**：V0.1 `GPTCall` 只发单条 user 消息、无超时、任何异常都盲目重试、失败返回假字符串 `"api调用失败"`（上层还以为成功）。V0.2 全部修正。

## 4. Token 统计（v2/core/token_log.py）

统一成 `TokenUsage`：`input_tokens / output_tokens / total_tokens`，外加 DeepSeek 特有的
`cache_hit_tokens / cache_miss_tokens`（缓存命中与未命中价格不同）。

兼容不同 API 返回结构：标准 OpenAI 的 `prompt_tokens/completion_tokens/total_tokens`，
以及 DeepSeek 的 `prompt_cache_hit_tokens/prompt_cache_miss_tokens`，都能正确读取。

## 5. 成本统计

- **价格放 config.py 的 PRICING**，不硬编码进任何业务 Agent。
- 价格依据 2026-08-31 查证的 DeepSeek 官方公开价（2026-08-17 起高峰/低谷计价，取**高峰价保守估算**）：
  `deepseek-chat` 输入缓存命中 ¥0.10/1M、未命中 ¥3.0/1M、输出 ¥9.0/1M（CNY）。
- `estimate_cost()`：pricing 缺失或价格字段不全时**返回 None**，绝不瞎编。
- **token 与 cost 严格分开**（`tokens=使用量`，`cost=金钱`），两个字段、两个概念。

## 6. 日志（v2/logs/llm_calls.jsonl）

每行一条完整记录，字段：
`timestamp / request_id / step / agent / model / input_tokens / output_tokens / total_tokens /
estimated_cost / currency / duration_ms / success / retry_count / error / prompt_hash / prompt_length / message_count`

安全设计：
- **绝不记录完整 API Key**。
- 不记录 prompt 正文，只记 sha256 哈希 + 长度 + 消息数。

## 7. Router（v2/llm/router.py）

**当前自动路由 = NO**。`get_model(task_type)` 任何任务都返回默认模型 `deepseek-chat`。
没有 `if prompt_length > xxx: use pro` 之类的伪智能选择——等后续阶段积累真实 token/质量数据再设计。

## 8. 测试结果（14/14 通过）

| 测试 | 结果 |
|---|---|
| T1 配置读取（base_url / model / key configured + 脱敏显示 `sk-****6509`） | PASS |
| T2 最小真实 LLM 调用（单条 user，`Return exactly: OK` → 返回 OK） | PASS |
| T3 messages 多条消息（system + user） | PASS |
| T4 Token 日志（JSONL 出现真实记录，字段齐全） | PASS |
| T5 成本统计（真实 usage→cost；pricing 缺失→None；手工演算一致） | PASS |
| T6 异常处理（无效参数 / 认证失败 → LLMError，非裸 Traceback） | PASS |
| T7 重试机制（mock：timeout→timeout→成功 → retry_count=2） | PASS |
| T8 失败重试上限（mock：连续失败 → 恰好 3 次尝试后停止，不无限调用） | PASS |
| T9 API Key 不泄漏（日志 / 异常 / 控制台均无完整 Key） | PASS |

> 开发过程中发现并修复一个 bug：不可重试错误（如参数错误）在失败日志里被误记成
> `retry_count=max_retries`，实际应是 `0`（一次都没重试）。已改为 `attempts - 1`（真实重试次数）。
> 修正后日志语义准确：参数错误→0 次重试、重试成功→2 次、重试耗尽→2 次。

## 9. 真实 LLM 调用量

| 指标 | 值 |
|---|---|
| Real LLM calls | **4 次**（测试套件跑了 2 遍：首次运行验证，第 2 遍验证 retry_count 修正） |
| Input tokens | 44 |
| Output tokens | 4 |
| Total tokens | 48 |
| Estimated cost | ≈ **1.7e-4 CNY**（不到 0.02 分钱，按高峰价保守估算） |

每次真实调用仅发送 `"Return exactly: OK"` 之类的极短请求；重试/异常测试全部用 mock，不浪费真实 API。

## 10. V0.1 完整性

```text
V0.1 modified = NO
```

未修改 `LLMCaller.py` / `main.py` / Prompt / JSON / Locations；V0.2 独立读取配置
（环境变量 `DEEPSEEK_API_KEY` 优先，其次只读 V0.1 的 `api_key.txt`，绝不写入、绝不显示完整 Key）。

## 11. 下一阶段（建议，不自动执行）

```text
阶段 3 —— Workflow（流水线引擎 + 重试不覆盖轨迹落地）
```

等待用户明确批准后再进入。
