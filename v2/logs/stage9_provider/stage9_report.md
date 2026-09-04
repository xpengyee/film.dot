# Stage 9 Report —— Multi-Modal Provider Architecture（最终交付报告）

> Stage 9 分四段：A=分析 → B=实现 → C=测试/Debug → **D=文档/交付（本报告为 9-D 产物）**。
> 本文件是 Stage 9 的**权威综合报告**：把 9-A 方案、9-B 实现、9-C 测试、9-D 交付
> 汇总成一份可读的完整记录。详细测试过程见 `stage9_c_report.md`。

## 1. Stage 9 目标

让 FilmAgent 具备**与不同厂商、不同部署方式、不同模态的 AI 模型连接**的能力，
业务 Agent 的核心逻辑不跟着厂商一起改。一句话：把"用哪个模型、怎么调用它"从
业务代码里剥离出来，收进一个 **Provider 层**（9-A 方案 §1）。

本阶段不追求"接上百炼跑通"，而是**先立住架构**：

1. 定义统一 Provider 抽象接口族（Text / Vision / Multimodal / Embedding + 预留 Image/Video/Audio）。
2. 划清 Agent / Router / Provider / Model 职责边界，杜绝业务代码直接 `requests.post(...)` 或依赖某一家 SDK。
3. Mock Provider 族齐备 → unit / integration / workflow 测试 **0 API、0 成本**。
4. 规划第一个真实 Provider（百炼文本），接口与配置落位。
5. 对既有 Stage 2 层做**最小修改**，不破坏 Stage 0–8 已封存回归。

## 2. A 会话：架构分析（PASS）

- 产出：`v2/docs/stages/stage9_plan.md`（9-A 分析结论的权威记录）。
- 基于真实代码逐条核实现状问题：config 绑死单厂商单模型（§2.1）；LLMClient 是"单厂商
  文本执行器"无模态维度（§2.2）；Router 纯占位且全仓零使用（§2.3）；业务 Agent 直接持有
  LLMClient 具体类但耦合点很收敛（§2.4）；无 Provider 级结构化输出通道（§2.5）；可复用
  的好底子：分层错误体系、统一 Token/Cost、mock 注入思路（§2.6）。
- 关键架构取舍：**协议/模态变了才新实现类；厂商/模型差异收敛为同一类的不同 Config 档**，
  防止"一家一实现、一模型一类"的爆炸结构。
- 明确不做：真实图像/视频/音频生成、TTS、本地模型部署、UI、Stage 10。

## 3. B 会话：实现（PASS）

- 新增 `v2/llm/providers/`：
  - `base.py`：`Provider` 基类（kind/vendor/model 只读标签）+ `ProviderResult` /
    `EmbeddingResult`（与 `LLMResponse` 字段对齐可互转）+ 接口族
    `TextProvider` / `VisionProvider` / `MultimodalProvider` / `EmbeddingProvider` +
    预留 `ImageGenerationProvider` / `VideoGenerationProvider` / `AudioProvider`
    （调用 `NotImplementedError`）+ `GENERATION_KINDS`。
  - `mock.py`：`MockTextProvider` / `MockVisionProvider` / `MockMultimodalProvider` /
    `MockEmbeddingProvider`（确定性、可脚本化、可注入抛错测 retry）。
  - `openai_compatible.py`：`OpenAICompatibleTextProvider`（继承 `LLMClient`，复用其
    timeout/retry/token log/错误家族；厂商差异靠 Config 档）。
  - `bailian.py`：`BailianTextProvider`（OpenAI 兼容文本实现 + 百炼配置）。
  - `registry.py`：`create_provider(kind, vendor, cfg)` 工厂 + `get_default_provider(kind)`
    （按 `FILMAGENT_PROVIDER` 环境开关，缺省 mock → 0 API）+ `provider_kind_vendor`。
  - `__init__.py`：统一导出。
- 最小改动既有文件（只做加法/注解，不改行为）：
  - `v2/config.py`：+Provider 注册表 `PROVIDERS`（deepseek_text / bailian_text / local_text /
    bailian_vision / bailian_embedding 五档）+ `FILMAGENT_PROVIDER` 开关 +
    `get_provider_config` / `default_text_vendor` / `load_provider_api_key`（key 只读脱敏）。
  - `v2/llm/router.py`：`Router` 从纯占位变真入口：`get_route` → `ProviderRoute`
    （kind/vendor/provider_name/model 路由描述），`get_provider` 经工厂产出实例；
    旧 `get_model` / `model_for_step` 语义保留兜底。
  - `v2/llm/client.py`：`chat()` 新增 kw-only `structured=None` 透传位与两个内部标记位，
    旧调用语义不变。
  - `v2/agents/base.py`：业务 Agent 的 client 统一为 `TextProvider` 形状（类型注解层），
    casting / scene_generation 及其接线脚本行为不变。
  - `v2/llm/__init__.py`：导出 Provider 族。
- 新增 `v2/logs/stage9_provider/run_tests.py` 测试套件。

## 4. C 会话：测试 / Debug（PASS）

- 详况见 `stage9_c_report.md`。要点：
  - Stage 9 套件 26/26 PASS（含 mock 修复后复验）。
  - 逐套直接重跑历史回归：Stage 1=19/19、3=38/38、4=25/25、6=24/24、7=29/29、8=27/27 全绿。
  - 接口族 / Mock 深度 / Router / Factory / Config / Token-Cost / Retry-Error 边界 /
    Structured contract / Agent→Provider E2E / 旧 LLMClient 兼容 / 静态检查 全过。
  - 真实 API 调用 = 0；Cost = ¥0.00。

## 5. D 会话：文档 / 交付（PASS，本文档所属会话）

- 更新长期文档：`PROJECT_STATUS.md`、`HANDOFF.md`、`CLAUDE.md`（仅必要处）。
- 最小修正 `v2/README.md` 与当前状态直接冲突的过时表述。
- 生成本综合报告 `stage9_report.md`。
- Git commit + 创建 `v0.2-stage9` tag + 最终验证（见 §22/§23）。

## 6. Provider 架构（落地）

```text
Agent（业务）
  ↓ 只认抽象 Provider，不认识厂商 SDK / endpoint / 模型名
Router
  ↓ 决策 task_type/step → 哪个 Provider + 哪个模型（路由描述，不发请求）
Provider
  ↓ 封装厂商协议、瞬时错误重试、上报 usage/token/cost、尽力结构化
Model（具体模型名/参数 = Config 数据，不进业务代码）
```

- 类别（`v2/llm/providers/`）：`TextProvider` / `VisionProvider` / `MultimodalProvider` /
  `EmbeddingProvider` 已落地接口族；`ImageGenerationProvider` / `VideoGenerationProvider` /
  `AudioProvider` 仅预留（本阶段不实现、工厂明确拒绝）。
- 设计原则 10 条：Agent 不依赖厂商 / Router 负责路由 / Provider 负责调用 / Config 负责配置 /
  Core Validator 负责业务合法性 / Workflow 负责业务级 retry / Provider 只负责瞬时 transport
  error / Token-Cost 复用 Stage 2 单一口径 / Mock 优先 / 真实 API 按阶段控制。

## 7. Router

- `v2/llm/router.py` 从占位变为数据流真入口：`Router.get_route(task_type)` 返回
  `ProviderRoute(kind, vendor, provider_name, model)`；`Router.get_provider()` 经工厂产实例。
- 策略立场（沿用 stage2 注释 + 9-A §17）：**本阶段不做按 task_type 的智能选模型**——
  无实验数据支撑，一切任务落"当前默认档"；策略后置，等真实 token/quality 数据积累再演进。
- mock 档对未知 kind 返回通用 mock route（预期：mock 无需配置）；真实 vendor 档未知
  kind 会明确 `KeyError`。

## 8. Config

- `v2/config.py` 新增 Provider 注册表 `PROVIDERS`，登记真实端点/厂商档五档：

| 档名 | kind | vendor | default_model | 备注 |
|---|---|---|---|---|
| `deepseek_text` | text | deepseek | deepseek-chat | 与既有 API_BASE_URL/DEFAULT_MODEL 同源，历史计费保留 |
| `bailian_text` | text | bailian | （Config 填） | 百炼 OpenAI 兼容模式 |
| `local_text` | text | local | （Config 填） | 未来本地 vLLM/Ollama 接入档 |
| `bailian_vision` | vision | bailian | （Config 填） | 仅接口位 + 槽位 |
| `bailian_embedding` | embedding | bailian | （Config 填） | 仅接口位 + 槽位 |

- 环境开关 `FILMAGENT_PROVIDER=mock|deepseek|bailian|local`，缺省 mock → 0 API；
  非法取值明确 `ValueError`（不静默回退 mock，防"以为真调了"）。
- key 读取：`load_provider_api_key(vendor)` 环境变量优先；deepseek 兼容回退 V0.1
  `api_key.txt`；绝不硬编码、日志只显示脱敏。`PROVIDERS` 全走 `get_provider_config`
  返回拷贝，防误改常量。

## 9. Mock

- Mock 四件套：`MockTextProvider` / `MockVisionProvider` / `MockMultimodalProvider` /
  `MockEmbeddingProvider`。
- Text：可脚本化返回（按 step / 次数）、dict 响应可覆盖 usage、可注入抛错测 retry 路径、
  非 LLM 异常原样透传不吞错。
- Vision / Multimodal：入参即校验（image/prompt 为 None/空串抛清晰 ValueError）；合法返回固定理解结果。
- Embedding：同输入同输出（含跨实例）、维度稳定、顺序无关、usage 按词累计且 output=0。
- 全部打 `vendor="mock"`、`kind` 各自模态，走 `Provider._emit_log` 记同一 `TokenLogger`，
  cost 恒 None/0（绝不产生真实账目）。

## 10. Bailian（百炼）

- `BailianTextProvider` 已落地：OpenAI 兼容文本实现 + 百炼 Config 档（`bailian_text`）。
- **已实现接口 / 配置落位，本阶段未做任何真实调用**。真实调用前需：
  1) 在 Config 填 `default_model`（如 qwen-plus，模型名不硬编码进核心类）；
  2) 设环境变量 `BAILIAN_API_KEY`。
- 策略：**百炼是当前主要云端 Provider 候选，但 FilmAgent 核心架构不得绑定百炼**——
  换厂商/本地模型只是换 Config 档，不改业务 Agent。模型名与免费策略可能变化，
  一律放 Provider / Config 层，不写成架构事实。
- 百炼 vision / embedding 在 Stage 9 只立了接口位 + Config 槽位（工厂对真实视觉/
  向量 vendor 明确报错"未实现"）。

## 11. Local readiness（本地模型就绪度）

- 已预留：`PROVIDERS["local_text"]` Config 档 + OpenAI 兼容接入路径。
  未来 `LocalTextProvider` 大概率**不是新类**，而是 `OpenAICompatibleTextProvider`
  的一档本地配置（vLLM / Ollama / llama.cpp 普遍兼容 OpenAI `/v1/chat/completions`）。
- 本地账目：pricing=None → cost=null，无需改 token_log。
- **当前状态：未下载、未部署、未接入任何本地模型**。本阶段不下载 Ollama / vLLM /
  任何本地模型权重。

## 12. Token / Cost

- **只有一套计费口径**：`TokenUsage` + `estimate_cost` + `TokenLogger`（JSONL）。无第二套 logger。
- Mock 走 `Provider._emit_log`、真实执行器走 `LLMClient._log`，**写同一 TokenLogger**；
  Mock 的 estimated_cost 恒 None、currency 留空。
- 真实 OpenAI 兼容执行器（fake transport，0 网络）：cost 按 `config.get_pricing("deepseek-chat")`
  精确估算；每条含 request_id + 64 位 prompt_hash。未登记价格/本地模型 → cost=null。失败调用记 1 笔 `success=false`。

## 13. Retry / Error

- 复用 Stage 2 分层：`LLMError` 家族（timeout/connection/rate-limit/5xx 可重试；
  4xx/认证不可重试）+ Workflow 层 `StepError` 整步重试。
- **三层边界铁律（防三重重试叠加）**：网络/超时/限流/5xx → Provider 内瞬时退避；
  4xx/认证 → 不重试直接抛；输出不是合法 JSON / 业务质量不合格 → Workflow 整步重试
  （每次尝试留版本文件）。Provider 绝不为业务质量偷偷多请求。
- 实测：瞬时错误注入"先 429 后成功" → transport 恰被调 2 次、retry_count=1；
  认证失败只调 1 次即抛；无吞错、无无限重试、无错误类型丢失。

## 14. Structured Output

- **contract 已就位**：`TextProvider.chat(structured=...)` 透传位；Mock 感知该位
  （`last_structured`）不报错；`structured_supported=False` 时静默忽略、不越权。
- 转发路径可开启：`structured_supported=True` 且用内置默认 transport 时，`structured`
  作为 `response_format` 透传（已用替换 transport 捕获验证）。
- **真实 backend 支持未验证**（当前 0 真实 API 调用）：百炼 / DeepSeek 是否真正接受
  `response_format` 未经证实，**不得声称已支持**。确认需人工批准的最小真实冒烟
  （见 `stage9_c_report.md` §19）。
- 边界：Provider 不做业务合法性校验；业务 schema 校验仍在 Core Validator / Agent。

## 15. Agent compatibility

- `CastingAgent` / `SceneGenerationAgent` 只 import core / workflow / agents.base，
  **不 import `llm.*`**（静态核实）；只经 `LLMAgent.chat_json → self.llm_client.chat(...)`
  调用，注入任何满足 `TextProvider` 形状的对象即可。
- E2E 成立：Router（注入工厂）→ MockTextProvider → Casting Workflow → APPROVED（1 笔
  mock 账、cost=null）；SceneGenerationAgent 经 MockTextProvider 产出合法 Scene；
  **默认工厂链**（Router 不注入、走真实 registry）→ 默认 Mock → CastingAgent 解析出合法
  业务 dict → 证明业务 Agent 真正使用抽象 Provider。
- 旧 LLMClient 兼容：`OpenAICompatibleTextProvider(LLMClient, TextProvider)` 复用全部
  Stage 2 能力；`LLMClient.chat` 旧语义不变；stage6/7 真实接线脚本签名未破坏（compileall 通过）。

## 16. 测试结果（最终）

```text
Stage 9   run_tests.py   26/26   PASS   rc=0
Stage 1   run_tests.py   19/19   PASS   rc=0
Stage 3   run_tests.py   38/38   PASS   rc=0
Stage 4   run_tests.py   25/25   PASS   rc=0
Stage 6   run_tests.py   24/24   PASS   rc=0
Stage 7   run_tests.py   29/29   PASS   rc=0
Stage 8   run_tests.py   27/27   PASS   rc=0

静态检查：compileall / 全模块 import 冒烟 / 依赖方向 / 密钥扫描 全过
```

（D 会话在文档整理完成后会再跑一次最终全量回归确认，见 §16 复验备注。）

## 17. C 会话发现的真实 bug

共 **1 个真实（低危）实现缺陷**：

- **Bug-1**：Mock Provider 写 TokenLogger 时丢了 `prompt_hash`。
  现象：真实执行器每条记录写 64 位 `prompt_hash`；Stage 9 新增的 `Provider._emit_log`
  （mock 自建日志路径）`prompt_hash` 恒为空串 → Stage 2"不记 prompt 正文、只记 sha256 哈希"
  的语义在 Mock 路径被悄悄丢掉，同一 JSONL 体系下字段口径不一致。无网络/计费影响。

另 2 项调查后判为**非实现 bug**（C 探针自身 TypeError、mock 档未知 kind 不报错=预期、
default_model 空串=配置护栏）。

## 18. Bug 修复

- 最小修复，仅改 `v2/llm/providers/mock.py`：
  - 从 `core.token_log` 引入 `compute_prompt_hash` 同源算法；
  - 新增 `_messages_hash(messages)` 工具，在 **4 处** `_emit_log` 调用点回填 `prompt_hash`
    （MockText 成功/失败、MockVision、MockMultimodal、MockEmbedding）。
- 修复后：Mock 日志与真实日志字段口径一致（request_id / prompt_hash / token）。
- **此修复保留，不得撤销。** 未触碰接口族 / Config / Router / Registry / 业务 Agent /
  任何历史测试断言。

## 19. Real API calls

```text
Stage 9 全程（A/B/C/D）= 0 次真实 API 调用。
```

D 会话未运行任何真实脚本，未触碰任何真实端点，无任何环境变量 key。

## 20. Cost

```text
Stage 9 全程 = ¥0.00（0 真实调用，0 计费）。
```

## 21. V0.1 status

```text
UNCHANGED —— V0.1（FilmAgent/）完全冻结，未被修改。
git diff -- FilmAgent/  = 空
```

## 22. Git commit

- Stage 9 代码提交：`feat(v0.2): complete provider architecture`
  - commit = `v0.2-stage9` tag 指向的本次交付 commit（`git rev-parse v0.2-stage9` 查精确 hash）
- 提交内容仅含 Stage 9 实际变更（`v2/llm/providers/`、`v2/llm/{client,router,__init__}.py`、
  `v2/agents/base.py`、`v2/config.py`、`v2/docs/stages/stage9_plan.md`、
  `v2/logs/stage9_provider/` 验收证据）与本次文档交付（PROJECT_STATUS / HANDOFF /
  stage9_report / README 最小修正 / CLAUDE.md 必要更新）。
- 不含 `FilmAgent/` 任何文件；不含缓存 / 临时产物 / API key。

## 23. Git tag

```text
tag = v0.2-stage9
```

- 指向本次 Stage 9 commit。
- 与 `v0.1` / `v0.2-stage7` / `v0.2-stage8` 一样，**一经创建永不移动、永不 force**。
- 校验：四个 tag 指向、`FilmAgent/` 0 diff、working tree clean（见 §26 最终验证）。

## 24. Known limitations（已知限制）

1. `bailian_text` / `local_text` / `bailian_vision` / `bailian_embedding` 的
   `default_model` 为空串是**配置护栏**（非缺陷）：真实调用前需用户在 Config 填模型名。
2. `structured=` 的**真实后端支持未验证**——contract 与转发路径已就位，但百炼 / DeepSeek
   是否真接受 `response_format` 需人工批准的最小真实冒烟确认，当前不得声称已支持。
3. Router 是"架构入口 + 默认档"，不做按 task_type 的智能选模型（无实验数据，策略后置）。
4. Vision / Multimodal / Embedding 只有接口 + Mock，无真实厂商调用；百炼视觉/向量仅槽位。
5. Image / Video / Audio Provider 仅预留接口（调用 NotImplementedError，工厂拒绝），
   尚无任何真实生成能力。
6. 本地模型未下载 / 未部署 / 未接入；Ollama / vLLM / 权重均不随本阶段安装。
7. 业务 Agent 仍是 Casting / SceneGeneration 两个；Storyboard / Image / Video / VLM 等
   尚未实现。
8. 人工审核仍是程序化 decision 模拟；交互式审核界面、UI / 数据库 / 生产部署未做。

## 25. Stage 10 preparation

```text
Stage 10 = NOT STARTED
```

- 尚未收到下一份正式开工指令，**不虚构 Stage 10 实现细节**。
- 路线图方向（仅方向参考）：StoryboardAgent / 接入下一个业务 Agent → Project 生命周期
  → 第一条完整业务链 → 第一集真实生成 → V0.2.0-alpha。
- 新会话进入 Stage 10 前的标准读档顺序（见 HANDOFF.md §4）：
  CLAUDE.md → PROJECT_STATUS.md → HANDOFF.md → 最近 Stage Report（本文件 +
  stage9_c_report.md）→ Git 状态 → 与 Stage 10 相关的代码。

## 26. 最终结论

```text
Stage 9（A/B/C/D）= PASS

Provider 架构设计正确、职责边界清楚、Mock 一等到位、Token/Cost 单一口径、
历史功能零回归、真实 API = 0 次、成本 = ¥0.00、V0.1 完全冻结未动、
1 处低危 Mock prompt_hash 缺陷已最小修复并保留。

已 Git commit + 打 tag v0.2-stage9，工作树 clean，FilmAgent/ 0 diff。

Stage 10：NOT STARTED，等待下一份正式开工指令。
```

---

*Stage 9-D 会话到此结束。按指令：不进入 Stage 10、不在当前会话继续开发，阶段正式结束。*
