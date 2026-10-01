# Stage 13-P 修复规格：DashScope URL 拼接与补丁验收

> 状态：总控审查发现缺陷，待执行会话修复；这属于 Stage 13 封存后的补丁，不创建 Stage 14，也不移动 `v0.2-stage13` tag。

## 背景、现状、前置阶段与为什么做

V0.1 `FilmAgent/` 冻结。V0.2 Stage 0–13 已封存，Stage 13 的图像编排可在 Mock 下端到端运行。Stage 13-P 在工作区新增 `BailianImageProvider`，让百炼原生同步/异步协议与已有 OpenAI Images 兼容档并存。T20a–T20q 定向测试 17/17，但测试替换了 HTTP 原语，未验证最终 URL。

当前 `config.PROVIDERS["bailian_native_image"]["base_url"]` 为 `https://dashscope.aliyuncs.com/api/v1`；`bailian_image.py` 的默认 path 为 `/api/v1/services/...`、`/api/v1/tasks/...`，`_post_json()` 与 `_poll_task()` 直接用 `self.base_url + path`。因此默认档实际构造 `/api/v1/api/v1/...`，首次真实请求会失败。这是修复的唯一架构阻塞。真实出图从未执行，模型、密钥、批准与单价仍为人工前置。

## 目标、架构位置与完成后的状态

让百炼原生档的同步提交、异步提交和轮询都使用正确 URL；让测试覆盖配置档和最终 HTTP URL 的组合。保留 `ImageGenerationProvider.generate_image(prompt, size, reference)` 契约、OpenAI 兼容档、预算/记账/编排行为。修复后 Stage 13-P 才能接受本地验收，之后总控再设计 Stage 14 VLM QA。

## 当前代码、文件与模块依赖

- `v2/config.py`：`bailian_native_image` 的 base URL，模型名仍为空。
- `v2/llm/providers/bailian_image.py`：默认路径、`_post_json()`、`_poll_task()`、`_request()`。
- `v2/llm/providers/registry.py`：按 `protocol` 选实现；本任务原则上不需修改。
- `v2/logs/stage13_image/run_tests.py`：T20 及历史回归入口。
- `v2/logs/stage13_image/stage13p_native_provider.md`、`v2/docs/PROJECT_STATUS.md`、`v2/docs/ROADMAP.md`、`v2/docs/HANDOFF.md`：补丁知识与状态。

依赖方向是 `core → llm → orchestration → agents`；本修复只在 Config、Provider、测试与文档层活动。

## 输入、输出、数据结构与接口

输入：Config 中的 `base_url`，Provider 默认或 Config 覆盖的 `sync_path/create_path/task_path`，以及 `generate_image()` 的 prompt/size/reference。输出：HTTP 请求指向恰好一段 `/api/v1` 的厂商地址；ProviderResult 与图像引用串结构不变。数据结构、公共接口、Schema 变更：N/A。

## 设计选择与边界

可选方案 A：把 Config 的 base URL 改为主机根，同时保留默认 path；方案 B：保留 Config 的 `/api/v1`，把默认 path 改为相对 `/services/...` 和 `/tasks/...`；方案 C：在 Provider 建立明确的 URL 组合规则。执行会话先核对厂商文档与当前两个域名示例，选择一种**唯一且可测试**的语义，避免用含糊的字符串替换掩盖双前缀。优先考虑 B：当前 Config 与业务空间域示例都以 `/api/v1` 结尾。若选择 C，须证明其对配置覆盖路径的行为清楚且没有双斜杠、双前缀。任何方案都要让测试从真实 `config.PROVIDERS` 档读取 base URL，拦截 `urllib.request.urlopen` 或 `_request` 并断言最终 URL，而非只断言传给 `_post_json` 的 path。

禁止修改：`FilmAgent/`、仓库根 `.gitignore`、`v2/core/`、`v2/workflow/`、`v2/llm/client.py`、`v2/llm/router.py`、已封存的历史测试断言、`v0.2-stage13` tag。不得配置真实模型或密钥，不得发真实 API 请求，不得创建第二套图像接口或预算机制。用户此前要求兼容档“只增不改”，继续遵守。

## 子任务、依赖、开发顺序与并行性

1. 核对当前 Config、默认路径与官方端点，写明统一的 base/path 语义。
2. 最小代码修复，仅影响百炼原生档。
3. 新增定向测试：公共域同步、公共域异步提交与轮询、业务空间域；断言最终完整 URL，且仅一个 `/api/v1`。测试使用桩，0 网络。
4. 跑 T20 与 Stage 13 全套回归；检查预算、OpenAI 兼容档和冻结边界。
5. 更新补丁记录与项目状态，交付报告。

1→2→3→4→5 必须串行；文档措辞审查可与测试准备并行。此补丁规模小，一个独立执行会话完成即可，无需多个并行会话。

## 测试、回归与验收标准

- 原 T20a–T20q 全绿；新增 URL 测试直接检查最终请求 URL，覆盖同步/异步提交/任务查询及两种域名。
- `python v2/logs/stage13_image/run_tests.py` 全绿；历史套件的真正功能失败不可豁免。项目文档记录该套件约 59 分钟。
- 若本机受沙箱写权限限制，先用独立写入检查确认环境原因，再在获准环境复跑；不能把 PermissionError 算通过。
- `git diff -- FilmAgent/` 为空，冻结文件 0 diff；`git status` 不含图片、密钥、运行账或其他泄漏。
- 无真实 API 调用，测试报告记 0；不得把 Mock 结果表述为真实连通性已验证。
- 总控亲自复核 URL 构造、测试结果、Git 范围后判 PASS/FAIL。执行会话不能自判补丁验收完成。

## 异常、架构问题与最终交付

若官方端点与上述两种方案均冲突，或需要改变 `generate_image` 契约、预算语义、封存文件，立即暂停并向总控提交冲突证据及可选方案，不擅自扩围。HTTP 错误继续使用现有 LLMError 家族；不得吞掉异常。交付：最小代码 diff、最终 URL 测试、完整测试结果及耗时、Git 状态、更新后的补丁记录、待总控验收报告。Git commit/tag：本规格不授权执行会话自行封存；保持现有 tag 不动。
