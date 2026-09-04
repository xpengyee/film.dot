# Stage 9-C Test / Debug Report —— Provider Architecture

> Stage 9 分四段：A=分析（`v2/docs/stages/stage9_plan.md`，COMPLETE）
> → B=实现（COMPLETE）→ **C=测试 / Debug（本文）** → D=文档 / 交付（未开始，等人工指令）。
> C 会话职责：全面测试、发现 bug、定位、修复、复验。**不 commit、不 tag、不进入 Stage 10 / 9-D。**

---

## 1. Test environment

```text
时间：2026-09-04
平台：Windows 11 / Git Bash / Python（Anaconda，3.9+）
项目根：D:\AI\FilmAgent-main
Git HEAD：52d8cd2（docs: establish project handoff and Claude session workflow）
工作区：B 会话实现未提交（5 个修改文件 + v2/llm/providers/、v2/docs/stages/、v2/logs/stage9_provider/ 3 个新目录）
真实 API key：无环境变量 / 未触碰任何真实端点
```

C 会话开始时工作区状态与 B 报告一致；`FilmAgent/`（V0.1）0 diff、三个 tag 未移动（见 §17）。

## 2. Initial test results（第一轮：完整本地测试 + 历史回归）

- Stage 9 套件 `python v2/logs/stage9_provider/run_tests.py` → **26/26 PASS，退出码 0**。
- 另按项目现有脚本**逐套**直接重跑历史回归（不只依赖套件内嵌子进程）：

```text
Stage 1  run_tests.py   19/19   rc=0
Stage 3  run_tests.py   38/38   rc=0
Stage 4  run_tests.py   25/25   rc=0
Stage 6  run_tests.py   24/24   rc=0
Stage 7  run_tests.py   29/29   rc=0
Stage 8  run_tests.py   27/27   rc=0
```

**结论：B 的"实现 + 基础测试"全绿。** 后续问题来自 C 自写的扩展边界探针。

## 3. Bugs found

共确认 **1 个真实（低危）实现缺陷**；另有 2 项调查后判为"非实现 bug"（归类见 §4）。

### Bug-1（真实，低危）：Mock Provider 写 TokenLogger 时丢了 prompt_hash

- **现象**：同一套 `TokenLogger`（`v2/logs/llm_calls.jsonl` JSONL）下，真实执行器（`LLMClient._log`）每条记录都写 64 位 `prompt_hash`；而 Stage 9 的 `MockTextProvider` 等走的是**新路径** `Provider._emit_log`（`v2/llm/providers/base.py`），`prompt_hash` 恒为空串。
- **为什么算缺陷**：Stage 9 把 Mock 从"测试里注入 fake transport"升级为"一等 Provider 并自己写日志"，等于在既有记账体系里**新增了一条写日志路径**。对照验证：Stage 2 fake-transport 注入路径（stage6/7 一直用，经 `LLMClient._log`）确实写 64 位 hash；而新 Mock 路径不写 → **Stage 2 的"不记 prompt 正文、只记 sha256 哈希"语义在 Mock 路径被悄悄丢掉**。这正是本阶段第十轮要求的排查项（request_id / prompt hash 不能因 Provider 接入而丢失）。
- 影响：Mock 驱动的 workflow 日志与真实执行器日志**字段口径不一致**，削弱"同一套 JSONL 统一记账"的完整性；无网络 / 计费影响。

## 4. Root causes

- **Bug-1 根因**：`base.Provider._emit_log` 的 `prompt_hash` 参数默认 `""`，`mock.py` 四处 `_emit_log` 调用点都没有回填（只传了 `prompt_length` / `message_count`）。9-B 作者没意识到 Mock 自建日志路径需要补足与 `LLMClient._log` 相同的字段。

**调查后排除（非实现 bug，正确分类）：**

| 现象 | 分类 | 说明 |
|---|---|---|
| C 探针一度 `MockEmbeddingProvider.embed()[...]` 报 `TypeError` | **test bug（我自己的探针）** | `embed()` 返回 `EmbeddingResult`，应取 `.vectors`。修正探针后通过。 |
| `Router(kind="nonsense")` 在默认 mock 档不报错 | **expected behavior** | mock 档不需要查 Config，`get_route` 直接短路返回通用 mock route；真实 vendor 档未知 kind 会 `KeyError` 报错（已实测）。mock 无需配置所以不报错，合理。 |
| `bailian_text` / `local_text` 的 `default_model` 为空串 | **expected behavior（配置护栏）** | 构造 Provider、`Router.get_route` 均正常，不产生 runtime 错误；只有真正调用（无 Config 填 model）才会以请求失败收场。属"真实调用前由用户在 Config 填"的护栏，**不需要为了测试通过而给核心配置硬编码模型名**（符合指令 §九判断）。 |

## 5. Fixes

仅修改 1 个文件，最小修复：

```text
v2/llm/providers/mock.py
```

- `from core.token_log import ... compute_prompt_hash`
- 新增模块内小工具 `_messages_hash(messages)`：与真实执行器同源，对 messages 列表算 sha256。
- 在 **4 处** `_emit_log` 调用点回填 `prompt_hash`：
  - `MockTextProvider.chat` 成功与失败两条路径 → `_messages_hash(messages)`
  - `MockVisionProvider.understand` → hash `[{role:user, content:prompt}]`
  - `MockMultimodalProvider.chat` → `_messages_hash(messages)`
  - `MockEmbeddingProvider.embed` → hash 各文本构成的 user 消息

未改：接口族 / Config / Router / Registry / LLMClient / 业务 Agent / 任何历史测试断言。架构（9-A 方案）未动。

## 6. Provider tests（第三轮：接口族）

- Text / Vision / Multimodal / Embedding 抽象类：`kind` 正确；未实现方法明确 `NotImplementedError`。
- 真实 / Mock 文本实现（`OpenAICompatibleTextProvider`、`BailianTextProvider`、`MockTextProvider`）签名与抽象 `TextProvider.chat` **参数集合一致**（messages / step / agent / temperature / max_tokens / structured）。
- `ProviderResult` ↔ `LLMResponse` 往返无损（text / model / usage / request_id / duration_ms / retry_count）。
- 预留 Image / Video / Audio：类存在、kind=image/video/audio、调用明确 `NotImplementedError`，**可表示"未来可扩展"，不被误认为已实现生产能力**；`create_provider` 对三类明确拒绝（不提供 mock）。

## 7. Mock Provider 深度测试（第四轮）

扩展探针（40 项断言全过，含 B 未覆盖边界）：

- MockText：默认纯兜底 / dict 响应可覆盖 usage / 出队中途注入 `LLMTimeoutError` **原样透传且后续调用不受污染** / 注入非 LLM `ValueError` 也原样透传**不吞错**。
- MockVision：image / prompt 为 None 或空串均抛清晰 `ValueError`；合法调用正常。
- MockMultimodal：空 messages 或空 images 均报错；text + 多图合法。
- MockEmbedding：**同输入同输出（含跨实例）**、维度稳定、空列表返回空向量、顺序无关性、usage 按词累计且 output=0。

## 8. Router / Factory tests（第五轮）

- 缺省（无 `FILMAGENT_PROVIDER`）→ text 默认 mock（0 API）。
- `FILMAGENT_PROVIDER=deepseek` → Router / 默认 Provider 落 `deepseek_text` 档（model=deepseek-chat，与 Config 一致）；`=bailian` → `BailianTextProvider`。
- env 非法取值 → **明确 ValueError，不静默回退 mock**（防"以为真调了"）。
- `cfg` 显式覆盖 provider 配置档生效；不存在 vendor / 真实档未知 kind → 明确报错。
- 路由描述 `ProviderRoute` 精确对应当前实例模型（deepseek 档 route.model == provider.model）。

## 9. Config tests（第六轮）

- `get_provider_config` 未知档 → `KeyError`（带可用档列表）。
- PROVIDERS 注册表含 5 档：deepseek_text / bailian_text / local_text / bailian_vision / bailian_embedding。
- bailian 未配 env → key 为空串（不硬编码 / 不伪造）；`mask_api_key` 脱敏（空/短/长）正确。
- 环境变量优先级：`FILMAGENT_PROVIDER` 驱动默认档、厂商级 env key 读取、deepseek 回退 `api_key.txt` 语义保留。
- **`default_model` 空串（bailian/local）判定：配置护栏，非运行时错误**（见 §4 表）。

## 10. Token / Cost tests（第十轮：不重复计费）

- **只存在一套计费口径**：`TokenUsage` + `estimate_cost` + `TokenLogger`（JSONL）。无第二套 logger / 计费机制。
- Mock 走 `Provider._emit_log`、真实执行器走 `LLMClient._log`，**都写同一 `TokenLogger`**。
- 真实 OpenAI 兼容执行器（注入 fake transport，0 网络）：cost 按 `config.get_pricing("deepseek-chat")` 精确估算（0.00012 CNY @ 10/10 tokens），currency=CNY，一条记录含 request_id + 64 位 prompt_hash。
- 未登记价格 / 本地模型：`tokens>0 且 cost=null`（local 未来语义成立）。
- 失败调用也记 1 笔 `success=false + error`（不吞）。
- 修复后：**Mock 日志与真实日志字段口径一致**（均含 request_id / prompt_hash / token）。

## 11. Retry / Error boundary tests（第七轮：防三重重试叠加）

- **Provider 内**：瞬时错误（429/超时/5xx，`retryable=True`）退避重试——注入"先 429 后成功"，transport 恰被调 2 次、`retry_count=1`；耗尽有界（不无限），总调用 = 1+max_retries。
- **Provider 内不重试**：认证失败（`retryable=False`）transport 只被调 1 次即抛。
- **业务层**：LLM 输出不是合法 JSON → Agent `_parse_json` 抛 `StepError`（Workflow 整步重试对象），**Provider 不重复请求**（calls=1）。
- 无吞错、无重复重试、无无限重试、无错误类型丢失；未知异常由 Workflow 引擎包成 `StepError` 记录（不静默）。

## 12. Structured output tests（第八轮：contract vs real 区分）

- **contract 已就位**：Mock 感知 `structured` 位（`last_structured`）且不报错；`structured_supported=False` 时静默忽略、不越权。
- **转发路径可开启**：`structured_supported=True` 且用内置默认 transport 时，`structured` 确会作为 `response_format` 透传（已用替换 transport 捕获验证）。
- **real backend support：未经真实调用验证**。当前 0 真实 API 调用，百炼/DeepSeek 是否真正接受 `response_format` 尚未证实 —— 不得声称已支持。若要确认需最小化、人工批准的真实冒烟（见 §19）。
- Provider 不做业务合法性校验；业务 schema 校验仍在 Core Validator / Agent（`response_format` 只是"尽力结构化"）。

## 13. Agent → Provider E2E tests（第九轮）

- `CastingAgent` / `SceneGenerationAgent` **只 import core / workflow / agents.base，不 import `llm.*`**（静态核实）；它们只经 `LLMAgent.chat_json → self.llm_client.chat(...)` 调用，注入任何满足 `TextProvider` 形状的对象即可。
- 端到端（B 套件 T10/T11 + C 补充探针）：
  - Router（注入工厂）→ MockTextProvider → Casting Workflow → **APPROVED**（1 笔 mock 账、cost=null）。
  - SceneGenerationAgent 经 MockTextProvider 产出合法 Scene（validator 0 错误）。
  - **默认工厂链**（Router 不注入、走真实 registry）→ 默认 Mock → CastingAgent 解析出合法业务 dict（entries=2）→ 证明"业务 Agent 真正使用抽象 Provider"，不是偷偷依赖旧 LLMClient。
- 业务行为不变性：Stage 6 / 7 套件全绿（含各自的 fake-transport 注入路径与业务断言）。

## 14. 旧 LLMClient 兼容（第十轮）

- `OpenAICompatibleTextProvider(LLMClient, TextProvider)`：复用 LLMClient 即继承 **timeout / retry / token usage / error 家族 / request_id / prompt_hash 日志**（R7 / R10 探针全部实证）。
- `LLMClient.chat` 仅新增 kw-only `structured=None` 与两个内部标记位，旧调用语义不变；stage2 套件（mock 模式）回归绿。
- 既有真实接线脚本（stage6/7 `run_real_*.py`）仍直接构造 `LLMClient(api_key, base_url, model)`，签名未破坏（compileall 通过；未运行真实脚本，0 计费）。

## 15. Historical regression

逐套直接跑 + Stage 9 套件内嵌 R1~R6，全部绿（见 §2、§5 后复验）：

```text
Stage 1: 19/19 | Stage 3: 38/38 | Stage 4: 25/25
Stage 6: 24/24 | Stage 7: 29/29 | Stage 8: 27/27
```

修复 mock.py 后 Stage 9 套件复验：**26/26 PASS，退出码 0**（含上述内嵌回归）。

## 16. Static checks（第二轮）

- `python -m compileall v2`：通过。
- 全 v2 模块 import 冒烟（config / llm 全族 / core / memory / context / workflow / review / agents）：通过。
- 依赖方向：`llm/` 与 `providers/` **无**对 agents / workflow / review 的 import（无反向 / 无循环）。
- 密钥扫描：v2 源码无真实 key；绝对路径扫描仅命中测试脚本自身的扫描器字符串；无裸 `except: pass`。

## 17. V0.1 modified

```text
git diff -- FilmAgent/   = 空
git status -- FilmAgent/ = 无 dirty 行
v0.1        = 344c0c6b7c48497be934957c29fcae029cca6752   （未移动）
v0.2-stage7 = 300aae44ad1d704219822efcf55249d1d59ad118   （未移动）
v0.2-stage8 = abf8e88a983f13c437abe2b719c920d3c5c3c144   （未移动）
```

## 18. Remaining issues

- `bailian_text` / `local_text` `default_model` 空串：**预期护栏**，非缺陷；真实联调前需用户在 Config 填模型名。
- structured `real backend support`：未验证（需人工批准的真实冒烟，见 §19）；contract 已就位。
- Router 在 mock 档对未知 kind 返回通用 route（不报错）：预期（mock 无需配置）；真实档会报错。若未来按 kind 精确路由可再补 mock 档校验（**当前不做，属过度**）。
- 若自定义 transport 抛**非 LLMError** 异常，`LLMClient.chat` 不拦截（只处理 `LLMError` 家族）；该异常会一路到 Workflow 引擎被包成 `StepError` 记录 —— 不静默，符合现有约定。真实默认 transport 已把全部 openai SDK 异常翻译成 `LLMError`。

## 19. Optional real Bailian smoke test

**未执行（默认不调用真实百炼）。** 如后续需要，建议最小化单次冒烟以确认：`structured` 是否真被百炼兼容层接受（§12 唯一待真实验证项）。届时申请内容如下（需人工批准）：

```text
原因：验证 response_format/json 模式在百炼 OpenAI 兼容层是否真实生效
预计调用次数：1
预计 Token / Cost：极小（单条短消息；预估 ¥0.001 级）
使用 provider/model：bailian_text（如 qwen-plus；需先 Config 填 default_model + BAILIAN_API_KEY）
```

日常回归仍 0 计费；该项即使批准也只走独立脚本。

## 20. Stage 9-C status: **PASS**

验收口径对照：

```text
Stage 9 tests     26/26 PASS（含 mock fix 后复验）
历史回归          Stage 1/3/4/6/7/8 全绿
静态检查          compileall / import / 依赖方向 / 密钥扫描 全过
Provider E2E      Agent → Router → Factory → Mock → 业务解析 成立
Real API calls    0
Cost              ¥0.00
V0.1              未动
未解决 bug        无（1 处低危缺陷已最小修复并复验）
```

核心链路（Agent → Router → Provider → Model abstraction）设计正确、错误边界清楚、
历史功能不回归、Token/Cost 单一口径、V0.1 不受影响。

---

## Modified files（C 会话）

```text
v2/llm/providers/mock.py
```

**为什么改**：补齐 Mock 自建日志路径丢失的 `prompt_hash`，使 Mock 与真实执行器在
同一 TokenLogger 体系下字段口径一致（保留 Stage 2"不记 prompt 正文、只记 hash"语义）。
最小局部修复，未触碰 9-A 架构与任何其他文件。

其余所有改动（agents/base.py / config.py / llm/{__init__,client,router}.py 与 3 个新目录）均为 **B 会话产物**，C 未改动。

---

*Stage 9-C 会话到此结束。按指令：不 commit、不 tag、不进入 Stage 9-D / Stage 10，等待人工验收与下一条指令。*
