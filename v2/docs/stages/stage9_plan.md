# Stage 9 架构方案（9-A 分析 / 架构设计）—— Multi-Modal Provider Architecture

> 本文件是 **Stage 9-A（分析 / 架构会话）** 的产出，仅做架构设计，**不含实现代码**。
> 依据：《v0.2开工指令9a》。B 会话（代码实现）应按本方案推进，不再重复全项目分析。
>
> 对应 Git：HEAD = `52d8cd2`（docs: establish project handoff and Claude session workflow）
> Tag：`v0.2-stage8`（`abf8e88`，未移动）；`v0.1` = `344c0c6`；`v0.2-stage7` = `300aae4`。
> `git diff -- FilmAgent/` = 空。Stage 9：NOT IMPLEMENTED。

---

## 1. Stage 9 的目标

让 FilmAgent 具备**与不同厂商、不同部署方式、不同模态的 AI 模型连接**的能力，且业务 Agent 的核心逻辑不需要跟着厂商一起改。一句话：把"用哪个模型、怎么调用它"从"业务代码"里剥离出来，收进一个 **Provider 层**。

Stage 9 要解决的工程问题，不是"接上百炼跑通"（那是 9-B/C 的真实联调任务），而是**先立住架构**：

1. 定义统一的 Provider 抽象接口族（Text / Vision / Multimodal / Embedding + 预留 Image/Video/Audio）。
2. 给出 Agent / Router / Provider / Model 的清晰职责边界，杜绝业务代码直接 `requests.post(...)` 或依赖某一家的 SDK。
3. Mock Provider 族齐备，保证 unit / integration / workflow 测试 **0 API、0 成本**。
4. 规划第一个真实 Provider（百炼文本），接口落位、真实联调与否交给 9-B/C 定。
5. 对现有 Stage 2 层做**最小修改**，不破坏 Stage 0–8 已封存的 27/27 + 历史回归。

**明确不在 Stage 9 范围**：真正的图像 / 视频 / 音频生成、TTS、本地模型部署、UI、Stage 10（见 §18）。

---

## 2. 当前 Stage 2 架构的问题（基于真实代码逐条核实）

以下结论来自对 `v2/config.py` / `v2/llm/client.py` / `v2/llm/router.py` / `v2/core/token_log.py` / `v2/agents/*` 的实际阅读，不是猜测。

### 2.1 config 绑死"单一厂商 + 单一模型"（`v2/config.py`）
- 顶层直接是 `API_BASE_URL = "https://api.deepseek.com"`、`DEFAULT_MODEL = "deepseek-chat"`。
- `PRICING` 字典只登记了 `deepseek-chat` 一个模型。
- 结论：整个配置是"为 DeepSeek 一家服务"的扁平面，**没有"供应商/Provider"这个维度**。接百炼或本地模型，等于在同一个命名空间里加第二套 URL / key / 价格，会互相打架。

### 2.2 LLMClient 是"单厂商文本执行器"，不是抽象接口（`v2/llm/client.py`）
- `LLMClient.__init__` 必须同时收 `api_key / base_url / model`——一个实例天然 = "某厂商 + 某模型"。
- 传输层 `transport` 是**裸函数**（`TransportFn`），默认实现 `_default_transport` 写死走 `openai.chat.completions.create`（OpenAI 兼容文本）。
- 结论：它能表达"DeepSeek 文本"和"百炼兼容文本"，但**没有模态维度**——vision / embedding 调用（不同 endpoint、不同 payload）在现有结构里无处安放；也没有"厂商选择"这层抽象。

### 2.3 Router 是纯占位，且整个仓库零使用（`v2/llm/router.py`）
- `Router.get_model(task_type)` / `model_for_step(step)` 一律返回 `default_model`。
- 全仓 grep：**没有任何业务 / 测试代码构造或调用 `Router`**。
- 结论：Router 只立了名字，没进数据流。Stage 9 必须决定它"保留 / 扩展 / 重构"后**真正接入**，否则又是一个摆设。

### 2.4 业务 Agent 直接持有 LLMClient 具体类（`v2/agents/base.py` + casting + scene_generation）
- `LLMAgent.__init__(llm_client)` 存的是**具体类实例**；`LLMClient` 由各接线脚本手动 `new`（`logs/stage6_casting/run_real_casting.py:84`、`logs/stage7_*/run_real_scene_generation.py:108` 等），再注入 Agent。
- `chat_json()` → `self.llm_client.chat(messages, step=, agent=)`。
- 结论：业务层与"某一个文本调用器"耦合。好消息是**耦合点非常收敛**——只有 `LLMAgent` 一个类持有 client，所有真实接线脚本也只有几处 `LLMClient(...)`。换 Provider 的改动面很小。

### 2.5 没有 Provider 级"结构化输出"通道
- 全仓 grep `response_format / json_schema / structured`：只在注释出现，`chat()` 不透传任何结构化选项。
- 现状：靠 prompt 约束 → Agent `_parse_json` 剥 ```json 围栏 → 解析失败抛 `StepError` → Workflow 层整步重试。
- 结论：这条路本身合理，但 Provider 层没有"尽量让模型返回结构（后端 json 模式 / schema）"的能力位，Stage 9 需要补上且**不越权**（业务合法性仍归 Core Validator，见 §10）。

### 2.6 值得保留的好底子（不是问题，是资产）
- **错误体系已分层**（见 §12）：`LLMError` 家族 6 子类带 `retryable`，`WorkflowError`/`StepError` 独立。两层重试已有雏形，Stage 9 只需划清边界、不重复造。
- **Token 统计已统一**（`v2/core/token_log.py`）：`TokenUsage` 统一各家 usage；`estimate_cost` 在 pricing 缺失时返回 `None`（天然支持"本地/免费模型 cost=0 或空"）；`TokenLogger` 写 JSONL、不记 key、不记 prompt 正文。
- **Mock 思路已存在**：测试靠注入 `transport=fake`（0 网络）。Stage 9 把"mock 化"从测试技巧升级为**一等 Provider 实现**即可。

---

## 3. Provider 层目标架构

```text
业务 Agent（Casting / SceneGeneration / 未来 Storyboard…）
        │  只认识抽象 Provider 接口，不认识任何厂商 SDK / endpoint
        ▼
Provider Layer（v2/llm/providers/）
   ├── 接口族：TextProvider / VisionProvider / MultimodalProvider / EmbeddingProvider
   │           + 预留 ImageGenerationProvider / VideoGenerationProvider / AudioProvider
   ├── 实现：OpenAICompatibleTextProvider（厂商=配置差异）
   │          MockText / Vision / Multimodal / Embedding（测试 0 成本）
   │          BailianTextProvider（第一个真实实现，OpenAI 兼容层）
   ├── Registry / Factory：config + 名字 → Provider 实例（统一入口）
   └── 每个实现内部：厂商调用 + 瞬时错误重试 + usage/token 上报（复用现有机制）
        │
        ▼
Model（具体模型名/参数：属于 Provider 的 Config，不进业务代码）
```

架构不做成"一家一实现、一模型一类"的爆炸结构，而是：

- **协议差异**（是不是 OpenAI 兼容、是 chat 还是 /embeddings、图像是 URL 还是 base64）决定"要不要新类"。
- **厂商/模型差异**（base_url、model、key、pricing）尽量收敛成**同一类的不同配置**。

理由：指令 §九/§十四明令"不为设计模式而设计模式、不过度工程"。真正需要写新实现类的是**模态/协议变了**的时候（vision 换 payload、embedding 换 endpoint、原生 DashScope 换协议），而不是"换个 base_url"的时候。

---

## 4. Agent / Router / Provider / Model 的职责边界

| 角色 | 职责 | 不做什么 |
|---|---|---|
| **Agent** | 提出业务请求（把 context 拼成 prompt、声明输出 schema 期望、解析并规范化结果、交给业务 Validator） | 不找 key、不直接调 SDK / requests、不选厂商模型、不做底层 retry |
| **Router** | 策略决策：某个 `task_type`/`step` 该走**哪个 Provider + 哪个模型**（如 simple→本地、normal→百炼、vision→VisionProvider）。决策结果是一份"路由描述"，不直接执行调用 | 不亲自发网络请求、不背业务数据 |
| **Provider** | 真正执行某一种模态的模型调用：封装厂商协议、把 SDK 异常翻译成 `LLMError` 家族、做**瞬时错误**重试与退避、上报 usage/token/cost、尽量支持结构化返回 | 不做业务合法性校验、不做 JSON 解析后的业务判定（那是 Agent/Validator）、不做整步质量重试 |
| **Model** | 具体模型名 + 推理参数，是数据不是逻辑，只存在于 Config / 路由结果里 | 不进入业务 Agent 代码 |

一句话分工：

```text
Agent 说"要什么"
Router 说"用哪家哪个模型"
Provider 说"这次调用结果怎么样"
Model / Config 说"我是什么、多贵"
```

数据流示例（对齐指令 §八）：

```text
CastingAgent
    ↓ chat(step="casting")
TextProvider（抽象）
    ├── MockTextProvider（测试 / 默认）
    └── OpenAICompatibleTextProvider("bailian" 配置)  ──或──  OpenAICompatibleTextProvider("deepseek" 配置)
            ↓
百炼 / DeepSeek / 未来本地 vLLM（模型）

VisionQAAgent（未来）
    ↓
VisionProvider（抽象）
    ├── MockVisionProvider
    └── BailianVisionProvider（9-B 只留位 / 或最小实现）
```

---

## 5. Provider 接口设计（总纲 + 关键约定）

### 5.1 放置位置与依赖方向

- 新增目录：`v2/llm/providers/`（放在 `llm` 包内最自然，与 `client.py` / `router.py` 同层）。
- 依赖方向沿用：`core → llm → … → agents`。`llm/providers` 依赖 `core.token_log`（usage）与 `config`（价格/key 读取），可复用 `llm/client.py` 的 `LLMError` 家族与重试逻辑；**不反向依赖 agents / workflow**。
- 新文件建议：
  - `v2/llm/providers/base.py`  接口族 + 公共元信息
  - `v2/llm/providers/mock.py`  Mock 四件套
  - `v2/llm/providers/openai_compatible.py`  OpenAI 兼容文本默认实现
  - `v2/llm/providers/bailian.py`  百炼实现（文本优先；vision/embedding 若做则扩展）
  - `v2/llm/providers/registry.py` 注册表 / 工厂
  - `v2/llm/providers/__init__.py` 统一导出

### 5.2 统一"调用结果"与错误

不另起炉灶，**复用** Stage 2 的既成约定：

- 调用结果：`ProviderResult`（文本类返回 `text`；多模态未来加 `content` 结构），字段对齐现有 `LLMResponse`（`text/model/usage/request_id/duration_ms/retry_count`），保证与 `LLMResponse` 可直接转换/等价，减少迁移摩擦。
- 错误：一律抛 `LLMError` 家族（`LLMTimeoutError / LLMConnectionError / LLMRateLimitError / LLMInternalError` = 可重试；`LLMRequestError / LLMAuthenticationError` = 不可重试），禁止把各家 SDK 异常漏给上层。
- usage：一律返回 `TokenUsage`；cost 由 `config.get_pricing(model)` 估算，缺失 → `None`。

### 5.3 Provider 元信息

每个 Provider 暴露只读类属性/属性：

```python
kind        # "text" | "vision" | "multimodal" | "embedding" | "image" | "video" | "audio"
vendor      # "mock" | "bailian" | "deepseek" | "local" | ...
model       # 当前生效模型名（来自 Config，不硬编码进业务）
```

`kind` 是"模态 + 协议"标签，`vendor` 是"供应商"标签。Router 按 `kind` 选队列、按 `vendor` 精确选实例。

---

## 6. Text / Vision / Multimodal / Embedding 接口

### 6.1 TextProvider（本阶段必须完整落地）

```python
class TextProvider(Provider):
    def chat(self, messages, *, step="", agent="",
             temperature=None, max_tokens=None,
             structured=None) -> ProviderResult:
        # structured: 可选 dict，形如 {"type": "json_object"} / {"json_schema": {...}}
        #   —— Provider 尽力透传给后端 json 模式；后端不支持时忽略，不报错。
        # 语义与现 LLMClient.chat 对齐，便于存量代码平滑迁移。
```

- 契约：返回文本（结构化内容以文本携带）+ usage；失败抛 `LLMError`。
- 与现有 `LLMClient.chat` 的关系（重要设计决策，见 §15）：**`LLMClient` 视为"OpenAI 兼容文本执行器"，其能力是 `OpenAICompatibleTextProvider` 的超集——可直接作为该接口的默认实现复用**，避免为抽象而写第二遍 retry/log。

### 6.2 VisionProvider / MultimodalProvider（本阶段：接口 + Mock + 留位）

```python
class VisionProvider(Provider):          # 纯视觉理解：图 → 文字描述 / 判断
    def understand(self, image, prompt, *, step="", agent="") -> ProviderResult: ...

class MultimodalProvider(Provider):      # 图文混合对话（未来 VLM / QA / Review）
    def chat(self, messages, *, images=(), step="", agent="") -> ProviderResult: ...
```

- `images` 统一以"已落盘图片引用 / content 描述"表达；**不在此设计图片二进制传输格式**（交给实现层定），避免接口被某一厂商绑架。
- 本阶段不实现真实调用；Mock 提供固定返回。

### 6.3 EmbeddingProvider（本阶段：接口 + Mock）

```python
class EmbeddingProvider(Provider):
    def embed(self, texts, *, step="", agent="") -> EmbeddingResult: ...
    # EmbeddingResult: vectors(list[list[float]]) + usage + model
```

- 明确未来消费方：Memory 语义检索（候选 fact 相关性）。Stage 9 只立接口与 Mock，不接入检索逻辑（非本阶段业务）。

---

## 7. Image / Video / Audio 未来接口预留

**只做"接口占位 + 文档级约束"，不实现、不联调。**

```python
class ImageGenerationProvider(Provider):
    def generate_image(self, prompt, *, size=None, reference=None) -> ImageResult: ...

class VideoGenerationProvider(Provider):
    def generate_video(self, prompt, *, shots=None, duration=None) -> VideoResult: ...

class AudioProvider(Provider):
    def synthesize(self, text, *, voice=None) -> AudioResult: ...
```

预留要点（写进接口 docstring，防止 B/C 顺手做多）：

- 这些接口对应未来 `Storyboard → Image → Video → Audio → Assembly` 链路，与 Stage 8 的 `ShotAsset(kind=image/video/audio)`（`v2/core/shot.py`）衔接——**资产先能表达，再接生成**。
- 结果统一返回一个"产物引用"（落盘路径 / asset ref），不让 Provider 负责决定文件命名与版本（那是 Artifact 层铁律）。
- 现在不接任何图像/视频/TTS 厂商，也不下载任何生成模型（§17/§18 硬约束）。

---

## 8. Mock Provider

### 8.1 目标
unit / integration / workflow 测试做到 **0 API 调用、0 成本、确定性**（对齐 Stage 0–8 所有历史套件的 mock 基线）。Mock 从"测试里手写 `transport=fake`"升级为 **Provider 层一等公民**。

### 8.2 必须提供的四件套
`MockTextProvider` / `MockVisionProvider` / `MockMultimodalProvider` / `MockEmbeddingProvider`。

- **MockTextProvider**：可注入一段"剧本"式响应脚本（按调用次数/step 返回预设文本或 dict）；支持：返回内容可配置、`TokenUsage` 可配置、cost 估算走 `estimate_cost`（pricing 给 0 或 None 皆可）、**可注入抛错以测试 retry 路径**。
- **MockVisionProvider / MockMultimodalProvider**：固定返回预设理解结果；入参即校验（保证测试确实喂了图/prompt）。
- **MockEmbeddingProvider**：确定性伪向量（如按文本 hash 生成同维向量），保证"同输入同输出"，供未来检索单测稳定断言。
- 全部 Mock 打 `vendor="mock"`、`kind` 对应各自模态。

### 8.3 与既有测试的关系
历史 stage6/7 测试仍可继续用 `transport=fake`（不强制迁移）；新增的 Stage 9 套件与未来 Agent 测试改用 `MockTextProvider`，并验证"Router 指到 mock 时整套 workflow 0 API 跑通"。Mock 与真实通过 Config/环境变量切换（§13/§14），切换**不触任何业务 Agent**。

---

## 9. Bailian Provider（第一个真实 Provider）

### 9.1 接入形态
- 百炼 DashScope 提供 **OpenAI 兼容模式**：chat/vision 走兼容 chat endpoint，embedding 走兼容 embedding endpoint，均可复用现有 `openai` 库 transport 思路（只需换 `base_url` 与 key）。因此**第一个真实文本实现不必自研 HTTP**。
- 命名为 `BailianTextProvider`（继承/复用 OpenAI 兼容文本实现 + 百炼配置 + 百炼 pricing）。
- 视觉/向量（qwen-vl 类 / text-embedding 类）是否在 9-B 落地实现，**由 9-B/C 决定**；9-A 仅确保其"接口位 + Mock + Config 槽位"存在。免费模型类别（大语言/视觉/语言/向量/全模态）只作为 Config 示例出现，**具体模型名不硬编码进任何核心类**。

### 9.2 结构化输出（9-B 联调要点，此处只立约定）
- 若百炼兼容层支持 json 模式 / schema，由 `structured=` 参数透传。
- Provider 只承诺"尽量结构化"；返回仍是文本 + usage。解析与业务判定不落到 Provider（§10）。

### 9.3 真实 API 调用
- **9-A 已执行真实 API 调用：0。**
- 是否在 9-B/C 对百炼做最小真实冒烟（计费），**交由实施阶段指令决定**；冒烟只走独立脚本（沿用 `run_real_*.py` 惯例，日常回归仍 0 计费）。

---

## 10. Local Provider 未来接入方式

指令 §七要求"现在不部署本地模型，但架构不允许写死 FilmAgent→Bailian"。结论：

- 本地 vLLM / Ollama / llama.cpp 服务普遍**兼容 OpenAI 的 `/v1/chat/completions`**。因此未来 `LocalTextProvider` 极可能**不是新类**，而是 `OpenAICompatibleTextProvider` 的一档本地配置：
  ```python
  PROVIDERS["local_text"] = {
      "kind": "text", "vendor": "local",
      "base_url": "http://127.0.0.1:8000/v1",   # vLLM 示例；不写死、可配置
      "api_key_env": "",                        # 本地通常无 key → 空
      "default_model": "...",                   # 由部署决定，Config 填
  }
  ```
- 本地模型账目：`usage` 可能存在；`PRICING` 无该 model → `estimate_cost` 返回 `None`（日志记 cost=null），或显式配 0。**无需改 token_log**（现状已支持）。
- 只有当某家本地服务**协议真不兼容**时才派生新实现类。此规则杜绝"每接一家就复制一个 Provider"。

---

## 11. Token / Cost 处理

### 11.1 复用，不重造
- usage：一律 `TokenUsage`；文本 Provider 记录点沿用 Stage 2（`LLMClient._log` 路径）；vision/embedding Mock 在 9-B 若实现也上报同一 JSONL（`TokenLogger`）。
- cost：`estimate_cost(usage, get_pricing(model))`。pricing 缺失 → `None`；免费/本地额度模型可在 `PRICING` 配 0 元或留空。
- **禁止**另建一套互相冲突的 token logger / cost 口径。

### 11.2 Config 的价格表演进
`PRICING` 从"按 model 一维"演进为"按 provider/model 两维"仍保持兼容（§13）：key 建议 `f"{provider}:{model}"` 或嵌套 dict；`get_pricing` 保持"查不到返回 None"语义不变。已登记 DeepSeek 价格原样保留（这是封存的 V0.1/V0.2 历史计费，不得丢失）。

---

## 12. Retry / Error 处理（三层边界，防三重重试）

### 12.1 现有多层（先讲清楚"已经有什么"）
- **Provider / LLM 层**：`LLMClient.chat` 内对 `retryable=True` 的瞬时错误（超时/连接/限流/5xx）做指数退避重试，`max_retries` 可配。
- **Workflow 层**：`WorkflowEngine._run_step` 对 `Step.run()` 抛出的 `StepError`（含 LLM 输出解析失败、质量不合格 `validate` 返回错误）整步重试，`max_attempts`（默认 3），每次尝试的 artifact 版本化保留（轨迹不丢）。

### 12.2 本方案划清的责任（直接回答"哪一层负责什么重试"）

| 失败类别 | 负责重试的层 | 依据 |
|---|---|---|
| 网络 / 超时 / 限流 / 服务端 5xx | **Provider 内部**（瞬时错误，退避） | `LLMError.retryable=True` 家族 |
| 参数 4xx / 认证失败 | **不重试**，直接抛（重试无用） | `LLMRequestError/AuthenticationError.retryable=False` |
| LLM 输出不是合法 JSON / 结构不对（可自愈型） | **Workflow 整步重试**（Agent 抛 `StepError`） | 随机性输出"重跑一次可能就好" |
| 业务质量不合格（Validator 报错） | **Workflow 整步重试**（每次尝试保留版本文件） | `Step.validate` 失败路径 |

**核心铁律：Provider 的重试只服务于"这一次调用没成功送达/没成功返回"；凡是"调用成功但结果不达标"，一律由 Workflow 层整步重试。Provider 绝不为业务质量偷偷多请求。** 这样只有"网络层"与"业务层"两层重试，不存在 Provider+Workflow+Agent 三重重试叠加。

### 12.3 明确不要做
- 不在 Provider 内重试"JSON 解析失败"（会与 Workflow 重试叠成两层同样目的的重试）。
- 不为"看起来高级"新增独立的 Agent 级重试回路——Agent 失败往上抛 `StepError`，交给引擎统一管。

---

## 13. Config 设计

### 13.1 演进方向（对 `v2/config.py` 做加法，不删既有）
新增一个 **Provider 配置注册表**（常量 + 读取函数）：

```python
# 示意结构（9-B 落地为具体 dict / 读取函数）
PROVIDERS = {
    # kind/vendor 是 Router 选路的标签；base_url/model 是"同一实现类的不同配置"
    "deepseek_text": { "kind": "text", "vendor": "deepseek",
                       "base_url": "https://api.deepseek.com",
                       "default_model": "deepseek-chat", "pricing": "deepseek-chat" },
    "bailian_text":  { "kind": "text", "vendor": "bailian",
                       "base_url": "<compatible-mode 地址>", "default_model": "<类>",
                       "pricing": "<model>" },
    "local_text":    { "kind": "text", "vendor": "local",
                       "base_url": "<本地服务地址>", "default_model": "<部署决定>",
                       "api_key_env": "", "pricing": None },   # None = cost null/0
    "bailian_vision":{ "kind": "vision", "vendor": "bailian", ... },
    "bailian_embedding": { "kind": "embedding", "vendor": "bailian", ... },
    # mock_* 由 factory 按需合成，不进这份"真实端点表"
}
```

- **API key 绝不硬编码、绝不进 git**。读取仍走环境变量优先；按 provider 可拆环境变量名（如 `BAILIAN_API_KEY`），`DEEPSEEK_API_KEY` 原语义保留兼容。任何日志/异常/报告只显示 `config.mask_api_key` 脱敏。
- 既有 `API_BASE_URL / DEFAULT_MODEL / MAX_RETRIES / RETRY_BACKOFF_SECONDS / PRICING / get_pricing / load_api_key` 全部保留并作为"deepseek_text"默认档，**保证存量测试与真实脚本零破坏**。
- 默认 Provider 选择支持环境开关（如 `FILMAGENT_PROVIDER=mock|bailian|deepseek`，9-B 定名）：测试环境默认 mock → **0 API**；跑真实脚本时显式切。

### 13.2 约束
模型名、base_url、timeout、temperature、structured 开关、pricing 都属于 Config 层，**允许且鼓励**在运行时/Config 调整；不允许散落到业务 Agent 源码。

---

## 14. Registry / Factory：是否需要（结论：需要，但保持朴素）

- **需要**，因为当前所有接线脚本都在手写 `LLMClient(api_key=..., base_url=..., model=...)`（见 §2.4），Stage 9 需要一个**统一入口**让"Config → 实例"只发生一次。
- **但要克制**：用**字典映射 + 工厂函数**即可，不上复杂的注册装饰器 / IoC 容器（指令 §十四反对过度工程）。

```python
# 示意
def create_provider(kind="text", vendor=None, *, cfg=None) -> Provider:
    # cfg/vendor 决定：mock → 对应 MockXxxProvider
    #                   真实 → OpenAI 兼容实现（bailian/deepseek/local 仅是配置档）
    #                   若实现类需要（如未来原生 DashScope）→ 按 vendor 查实现表
```

- 附带 `get_default_provider(kind)`：读默认 vendor（环境开关）→ 返回测试用 mock 或生产实现。
- Router（§4）消费工厂：`Router → create_provider(...)` 产出 Provider 实例交给 Agent；Router 本身不 new transport、不发起请求。

---

## 15. 对现有 Stage 2 的最小修改方案（B 会话行动指南）

设计约束：**行为不变、改动最小、历史测试回归 0 破坏**。逐文件给出处理建议。

| 文件 | 建议动作 | 理由 |
|---|---|---|
| `v2/config.py` | **加** PROVIDERS 注册表 + 默认选择开关；保留全部既有导出 | 加配置不破坏既有引用 |
| `v2/llm/router.py` | 小改：`get_model(task_type)` → `get_route(task_type)`（或返回"provider 标识 + model"）；仍保留 `default_model` 语义兜底；在 __init__ 里接上 factory | 从"占位"变成"真入口"，但默认行为不变（全走默认档） |
| `v2/llm/client.py` | **基本不动**；将 `LLMClient` 标记/认定为"OpenAI 兼容文本执行器"，作为 `OpenAICompatibleTextProvider` 的默认实现复用；`chat` 增可选 `structured` 透传参数（默认 None，行为不变） | 避免重写第二遍 retry/log |
| `v2/llm/providers/`（新增） | 接口族 base / mock / openai_compatible / bailian / registry / __init__ | Stage 9 核心交付 |
| `v2/agents/base.py` | **最省改动路径**：`LLMAgent` 的 `llm_client` 参数语义不变（仍是"可调用对象：`chat(messages, step=, agent=)`"）；只要 TextProvider 满足该形状，agent 无需改。B 会话在 LLMAgent 内把"client 形态"统一成 `TextProvider` 类型注解即可，不强制迁移调用方 | 业务 Agent 换 Provider 不改核心逻辑（指令核心诉求）|
| `v2/agents/casting.py` / `scene_generation.py` | **不改**（若走"形状兼容"路线） | 它们是 Stage 6/7/8 封存行为 |
| `v2/llm/__init__.py` | 增导 Provider 族 | 统一入口风格 |
| `v2/logs/stage{6,7}_*/run_tests.py` | **不改**（继续用 transport=fake） | 回归零破坏；是否迁移到 Mock Provider 属可选优化，不做强制 |
| `v2/logs/stage{6,7}_*/run_real_*.py` | **不改**（继续直连 deepseek） | 真实脚本不属于本次最小化改动面 |
| 新增 `v2/logs/stage9_provider/` | 新测试套件 `run_tests.py` + report | 见 §16 |

> 取舍说明：`LLMAgent` 究竟把 `llm_client` 重命名成 `text_provider`、还是保持字段名而换类型注解，是 9-B 的**实现细节**；本方案唯一强约束是：**对既有两个业务 Agent 与其接线脚本，不产生行为差异**（回归 24/24 + 29/29 + 其余全部套件必须保持绿）。

### 一个必须被避免的弯路
不要为了让"看起来是 Provider 架构"就把 `LLMClient` 拆掉重写、把所有测试的注入点全量迁移——那是伪重构。正确顺序是：**先有 Provider 接口与 Mock，工厂与 Router 生效，业务 Agent 换型注解；存量测试一行不改也能全绿 → 再谈可选清理。**

---

## 16. 测试方案

### 16.1 新增 Stage 9 套件（`v2/logs/stage9_provider/run_tests.py`）
建议覆盖（沿用既有 `check()` 断言风格、退出码语义、`.gitignore` 习惯）：

1. **接口族形状**：Text / Vision / Multimodal / Embedding 各 Provider 具备 `kind/vendor/model` 元信息；抽象方法签名齐全；Image/Video/Audio 预留类存在但**明确不可用**（调用抛 NotImplemented 或提示"未实现"）。
2. **MockTextProvider**：可脚本化返回（step 相关）；返回含 `usage`；`estimate_cost` 在 pricing=None → None、pricing=0 → 0；注入抛错可测 retry。
3. **Mock 三件套（vision/multimodal/embedding）**：确定性输出；同输入同输出（embedding 哈希稳定）；usage 上报。
4. **Factory / Router**：`create_provider("text")` 在 mock 档返回 Mock；Router 返回的路由描述能精确落到"一个 Provider 实例"；切换默认 vendor 只经 Config。
5. **端到端 0 API 冒烟**：用 MockTextProvider 跑一次 Casting / SceneGeneration workflow（复用 stage6/7 的真实 fixture 上下文思路），断言 **TokenLogger 文件 0 新增真实调用、cost 全 0/null、流程 APPROVED**。
6. **structured 透传**：`chat(structured={"type":"json_object"})` 在 mock 下可感知、不报错；真实档未接时静默忽略。
7. **回归**：子进程复跑 stage4（内含 1/2(mock)/3）+ stage6 + stage7 + stage8 套件全绿；`git diff -- FilmAgent/` 为空；`v0.1`/`v0.2-stage7`/`v0.2-stage8` tag 未移动。
8. **静态检查**：`compileall` + 全模块 import 冒烟 + 源码 key/绝对路径扫描无真实密钥（沿用阶段 5 方式）。

**验收口径：Stage 9 套件全绿 + 历史回归全绿 + 真实 API 调用 = 0 + ¥0.00。**

### 16.2 真实百炼联调
是否做、做多少，**由 9-B/C 决定**；若做，只走独立 `run_real_bailian.py`（最小冒烟，计费），日常回归 0 计费——与 stage6/7 真实脚本惯例一致。

---

## 17. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| Provider 抽象过度设计（接口太多、实现全空壳、样板爆炸） | 中 | §5/§14 约束：协议差异才新类；厂商差异进 Config；预留类只占位不实现 |
| 改了 Agent / Router / client 导致历史回归（24/24、29/29、8×27/27、1/3/4 各套件）被破坏 | 高 | §15 最小修改方案 + §16 套件内嵌回归子进程；任何一步先保证存量测试绿再前进 |
| 三层重试叠加（Provider retry + Workflow retry + Agent retry） | 中 | §12 边界铁律 + Stage 9 测试 5 的 retry 用例专门验证"重试只发生在该在的层" |
| Router 没有真实数据却过早"智能路由"（again：现在不该做 if/task/模型聪明选择） | 低 | 9-A 明确：Router 只做"架构入口 + 默认档"，策略后置（沿用 router.py 现注释的立场） |
| 结构化输出被 Provider 越权"代做业务校验" | 低 | §6/§10：Provider 只透传 json 模式并返回文本；业务合法性仍在 Core Validator |
| API key / 新厂商密钥泄露进 git | 高 | 环境变量优先 + `.gitignore` + `mask_api_key` 脱敏 + Stage 9 静态扫描用例 |

---

## 18. 明确"不做什么"（Stage 9 边界，9-A/B/C 都遵守）

Stage 9 **不做**（对应指令 §十七）：

- ❌ 不实现真实图像 / 视频 / 音频生成、不接 TTS。
- ❌ 不下载 / 部署任何本地模型、Ollama、vLLM（只预留配置档与接口位）。
- ❌ 不在 Stage 9 内对百炼做真实 API 联调（是否冒烟由 9-B/C 指令决定；9-A 已完成真实调用 = 0）。
- ❌ 不修改 `FilmAgent/`（V0.1 冻结）、不修改 Stage 8 已封存内容（`shot.py` / `adapter.py` / `core/__init__` / 4 个新 validator 等核心不动）。
- ❌ 不创建 UI、不开始 Stage 10、不引入第三方 Provider 源码进项目。
- ❌ 不做业务 Agent 行为重构、不迁移存量测试注入点当作"Stage 9 任务"（可选清理另立阶段）。

**Stage 9 交付物 = Provider 接口族 + Mock 族 + Factory/Router 入口 + Config 槽位 + Bailian 文本实现位 + Stage 9 测试套件。业务数据流与既有 Agent 行为不变。**

---

## 附：9-A 预留给 B 的落地清单

- B 期望新增文件：`v2/llm/providers/`（base / mock / openai_compatible / bailian / registry / __init__）、`v2/logs/stage9_provider/`（run_tests.py 等）。
- B 期望最小改动文件：`v2/config.py`（加 Provider 注册表/开关）、`v2/llm/router.py`（接 factory + 返回路由描述）、`v2/agents/base.py`（client 统一为 TextProvider 形状/注解）、`v2/llm/__init__.py`（导出）。
- 必须不改：`FilmAgent/` 全目录；`v2/core/shot.py` / `adapter.py` / `validator.py` 的既有实现；`v2/agents/casting.py` / `scene_generation.py` 行为；全部历史测试套件的断言。
