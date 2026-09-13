# Stage 10-B 会话报告 —— Visual Asset 实现 + 程序化 API 计数（含预算硬上限）

> 对应计划：`v2/docs/stages/stage10_plan.md`（10-A 产出）+ Stage 10-B 补充指令
> （API 调用计数必须程序化实现，硬上限 MAX_REAL_VISION_REQUESTS=3）。
> 基线：HEAD = `82a89b5`（v0.2-stage9），四 tag 未移动，`FilmAgent/` 0 diff。

## 一、交付内容

### 新增文件（10）
| 文件 | 内容 |
|---|---|
| `v2/llm/request_counter.py` | **APIRequestCounter**（线程安全；real/mock 两本账；per-kind 分桶；预算硬上限；审计最小集）+ **APIBudgetExceededError**（不可重试）+ **CountingProviderWrapper**（mock 调用边界计数；mock.py 0 改）+ 进程级单例 get/set/reset |
| `v2/core/visual.py` | 视觉资产数据模型：五类词表 / 七态生命周期 / ReferenceImage(kind) / REF purpose；四个 VisualProfile；VisualAsset / VisualAssetVersion / ReferenceImage；ID·版本·checksum 格式工具；`normalize_profile_fields` 确定性规范化 |
| `v2/core/visual_registry.py` | VisualAssetRegistry：ID 分配（kind 前缀计数）/ (kind,name) 永久绑定 / 版本单调永不覆盖 / Review 三态确定性派生（approve→旧版 superseded；APPROVED_WITH_EDIT 指路新版本）/ archive / 参考图 sha256 / registry.json 持久化 |
| `v2/core/visual_compose.py` | VisualComposer + ComposedVisualInput：Shot → 组装输入（characters/location/props/style/references）；缺口明确报错（allow_partial 开关）；风格三级解析；紧凑 `key: value` 渲染 + `render_prompt()` |
| `v2/llm/providers/openai_compatible_vision.py` | OpenAICompatibleVisionProvider（继承 LLMClient：retry/错误翻译/TokenLogger 全复用；图片→base64 data URL parts；**唯一真实请求计数点在 _invoke**）+ Multimodal 同族类（独立预算桶） |
| `v2/agents/visual_asset.py` | VisualAssetAgent（plan/extract 双模式；规范化纯确定性代码，0 文本 API）+ VisualAssetStep（inputs['visual_asset_spec']，scene_spec 模式）+ field_contract_for |
| `v2/prompts/v0.2/visual_asset_plan.txt` | plan 模式 prompt（语义→结构化 profile JSON 契约） |
| `v2/prompts/v0.2/visual_asset_extract.txt` | extract 模式 prompt（参考图→结构化 profile JSON 契约） |
| `v2/logs/stage10_visual_asset/run_tests.py` | mock 测试套件（见 §三） |
| `v2/logs/stage10_visual_asset/run_real_visual_extraction.py` | 真实视觉冒烟脚本（前置四项检查 + 预算 3 程序化强制 + 程序化计数报告） |

### 修改文件（5，全部最小增量，既有行为不变）
- `v2/core/validator.py`：纯追加 `VisualAssetValidator`（含 registry 级唯一性）
- `v2/core/__init__.py` / `v2/llm/__init__.py` / `v2/llm/providers/__init__.py`：纯追加导出
- `v2/llm/providers/registry.py`：vision/multimodal 真实 vendor 分支（default_model 空 → 明确报错护栏）；mock 分支与 GENERATION_KINDS 拒绝不变
- `v2/config.py`：**0 改**（bailian_vision 槽位既有）

### 禁改清单核实（git）
`shot.py / adapter.py / models.py / assets.py / agents/base.py / casting.py / scene_generation.py / client.py / router.py / providers/base|mock|openai_compatible|bailian.py / memory/ / review/ / workflow/ / context/ / FilmAgent/` 全部 0 diff（套件 T15a 断言通过）。

## 二、API 计数机制（补充指令 §15 硬性验收逐条）

| 要求 | 落地 | 验证 |
|---|---|---|
| 程序化统计 real/mock/总量 | `APIRequestCounter.snapshot()`：real_api_requests / mock_requests / logical_calls / retries / by_kind / budget / audit | T09a-c, T13 |
| 计数贴近真实请求位置 | 唯一真实计数点 = 真实 Provider `_invoke`（每个 HTTP attempt 恰好一次）；Agent/Workflow/脚本绝不计数 | T10a,b |
| Request 与 Retry 区分 | attempt=1 计 logical_call；attempt≥2 计 retry；1 业务调用 N 次 HTTP → real=N | T09b, T10b |
| Real/Mock 分开 | 真实只在传输出口计；mock 只经 CountingProviderWrapper 计（mock.py 0 改）；两本账绝不叠加 | T09c, T11a |
| 审计最小集 | timestamp/provider/model/modality/request_id/attempt/is_real；无 key、无 prompt 正文、无图片内容 | T09e, T10g |
| 硬上限 3 程序强制 | check+count 原子（锁内）；达限抛 `APIBudgetExceededError`（retryable=False，错误信息含 max=3） | T09d, T10d |
| Retry 消耗预算 | retry 同样过计数点 | T10b |
| 并发安全 | threading.Lock；非裸全局 += | T14（8 线程 × 200 次不丢） |
| 测试接口 | fake transport 注入即可验证 0→1→2(retry)→3→拒绝仍 3 | T09/T10 全链 |
| 不假计数 | 本地校验（图缺失/prompt 空/key 无）在计数点之前，不消耗预算 | T10c |
| 不重复计数 | 单一计数点（真实 Provider._invoke；mock Wrapper 调用边界），文档化 | T10a, T11a |
| 报告用程序统计 | 套件与真实脚本均引用 snapshot()，无人工数数 | test_report.json / real 脚本 |
| 可复用（非过度设计） | per-kind 分桶 + 预算按 kind 配置；未重构 Provider 系统 | T09f, T10e |
| 计数与成本分离 | token/cost 仍走 TokenLogger（独立 JSONL，request_id 关联） | T10a |

## 三、测试结果

`python v2/logs/stage10_visual_asset/run_tests.py` → **62/62 全绿**，真实 API 调用 0 次：

- T01~T08：词表/模型往返/校验器错误码/Registry 身份·版本·生命周期·参考图/Composer 解析·缺口·风格/**Storyboard 序列化逐字节不变（Token 硬证据）**
- T09/T10/T11/T14：计数器与预算全链（见上表）
- T12/T13：Agent plan/extract（mock）+ Step 壳 + E2E（plan→registry→审批→Composer，0 真实 API）
- R-stage1/3/4/6/7/8/9：七套历史回归子进程全绿
- T15/T16：冻结检查（禁改 0 diff / config 0 改）+ 静态检查（compileall / ASSET_KINDS 精确不变 / import 冒烟 / 密钥扫描）

## 四、真实视觉冒烟：阻塞（需人工配置）

`run_real_visual_extraction.py` 前置四项检查全部程序化验证有效：

1. `BAILIAN_API_KEY` 未配置 → 阻塞（实测拦截，exit 2）
2. `config.PROVIDERS['bailian_vision']['default_model']` 为空 → 阻塞（实测拦截）
3. 参考图：未放置（脚本含回退 pics/*.png"仅管线验证"路径）
4. `--yes` 人工批准：未给出 → 阻塞

按 stage10_plan §25：**跳过真实样例并明确报告阻塞，Mock 路径完整交付，绝不伪造真实结果。** 真实 3 次样例（Character v1 / Location / Character v2 版本机制）待配置齐备后执行 `--check` 通过再 `--yes`。

## 五、遗留（10-C / 10-D）

- 10-C：真实样例执行与复核（3 次预算内）、fixtures 沉淀；§26 十一项重点全量断言
- 10-D：PROJECT_STATUS / HANDOFF / CLAUDE.md 更新；stage10_report.md 汇总；commit + tag（等人工验收）
