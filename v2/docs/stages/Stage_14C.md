# Stage 14-C —— VLM Image QA 正式测试与 Debug 指令

> 会话角色：Stage 14-C 独立测试与 Debug 会话
> 日期：2026-10-02
> 工作目录：`D:\AI\FilmAgent-main`
> 基线 HEAD：`1a7404942edb6e493091a449256da5a75d456ccc`
> Stage 13 tag：`v0.2-stage13` → `3895c6c103f125d458fe648d80a625bf007601af`，禁止移动
> 真实 API：本会话必须为 0
> Git：不 commit、不 tag

---

## 1. 会话目标

对 Stage 14-B 实现执行完整、可重复、机器可读的正式测试；发现缺陷时只在 Stage 14 已批准
范围内做最小修复，并在修复后完成定向复测和一次完整回归。

本会话完成后必须能够回答：

1. Ground truth 是否始终来自 manifest 指定精确版本；
2. D14-REF-01 是否在所有零/一/多命中情况下 fail-closed；
3. 任何路径/SHA/Schema/预算错误是否在真实请求前被拦截；
4. 多图顺序、Artifact/Review、resume 和 QA index 是否确定且可审计；
5. pipeline 默认关闭是否零副作用；
6. Stage 13 及其历史回归是否保持通过；
7. 测试是否保持 0 网络、0 真实 API、0 模型切换。

本会话不负责最终项目文档封存；Stage 14-D 在全部测试通过后完成报告和长期知识更新。

---

## 2. 开工前必须阅读

按顺序阅读：

1. `CLAUDE.md`
2. `v2/docs/PROJECT_STATUS.md`
3. `v2/docs/HANDOFF.md`
4. `v2/docs/ORCHESTRATION.md`
5. `v2/docs/stages/Stage_14.md`
6. `v2/docs/stages/Stage_14B.md`
7. `v2/logs/stage14_image_qa/stage14_contract_notes.md`
8. `v2/agents/image_qa.py`
9. `v2/orchestration/image_qa_stage.py`
10. `v2/orchestration/pipeline.py` 的 Stage 14 接线
11. `v2/workflow/engine.py`、`artifact.py`、`models.py`
12. `v2/llm/request_counter.py`
13. `v2/logs/stage13_image/run_tests.py` 的测试/回归/复位方式

Stage 14-A/B 的聊天记录不是知识来源；以这些文件和实际代码为准。

---

## 3. 当前交付状态

Stage 14-B 已新增：

```text
v2/agents/image_qa.py
v2/prompts/v0.2/image_qa.txt
v2/orchestration/image_qa_stage.py
```

并最小修改：

```text
v2/agents/__init__.py
v2/orchestration/__init__.py
v2/orchestration/pipeline.py
```

14-B 报告的最小自检已通过，但尚无 Stage 14 正式测试套件、机器可读正式报告和历史回归证据。
因此当前状态为：**implementation delivered / formal verification pending**。

开工时工作区本来就不是 clean。必须保留 A/B/总控的全部既有改动，不得 reset、checkout、
clean 或覆盖。先记录 `git status --short` 与 `git diff --name-status`，将其作为 C 会话前置快照。

---

## 4. 本会话范围

### 4.1 必须新增

```text
v2/logs/stage14_image_qa/run_tests.py
v2/logs/stage14_image_qa/test_report.json
v2/logs/stage14_image_qa/run_real_image_qa.py
```

`run_real_image_qa.py` 只建立未来真实冒烟入口；本会话只能运行其默认体检/拒绝路径，不能传
真实执行开关，不能发网络请求。

### 4.2 发现真实缺陷时允许最小修改

```text
v2/agents/image_qa.py
v2/prompts/v0.2/image_qa.txt
v2/orchestration/image_qa_stage.py
v2/agents/__init__.py
v2/orchestration/__init__.py
v2/orchestration/pipeline.py
```

只修改失败测试直接证明的缺陷；不得重构无关代码、替换既有架构或放宽测试。

### 4.3 本会话不负责

```text
v2/logs/stage14_image_qa/stage14_report.md
CLAUDE.md 的 Stage 14 完成态
PROJECT_STATUS/HANDOFF/ROADMAP 的最终封存态
Stage 15 设计或实现
真实 VLM 冒烟执行
commit/tag
```

上述内容留给 14-D 或总控验收。

### 4.4 禁止修改

```text
FilmAgent/ 全目录
仓库根 .gitignore
v2/core/ 全目录
v2/workflow/ 全目录
v2/review/ 全目录
v2/memory/ 全目录
v2/context/ 全目录
v2/llm/providers/base.py
v2/llm/providers/openai_compatible_vision.py
v2/llm/client.py
v2/llm/router.py
v2/llm/request_counter.py
v2/orchestration/image_stage.py
历史测试代码及其断言
```

若修复必须触碰禁改文件，停止并向总控提交证据与方案，不能先改后报。

---

## 5. 测试基础设施要求

### 5.1 单一正式入口

必须提供：

```powershell
python v2/logs/stage14_image_qa/run_tests.py
```

要求：

- 纯 Python 标准库，除非仓库已有必需依赖；禁止为测试安装新包。
- 使用临时项目目录、注入 Mock/spy/fake Provider，禁止访问网络。
- 每条测试有稳定 ID、名称、PASS/FAIL、必要诊断。
- 任一失败进程退出码非 0；全绿退出码 0。
- 无论成功失败都原子写 `test_report.json`。
- 测试结束清理临时目录、`__pycache__`、图片和临时账本。
- 不把绝对路径、API key、base64、完整 prompt 写入机器报告。
- 禁止通过 sleep、随机重试或真实服务使测试通过。

### 5.2 机器可读报告

`test_report.json` 至少包含：

```json
{
  "stage": "14-C",
  "head": "...",
  "started_at": "...",
  "ended_at": "...",
  "tests_total": 0,
  "passed": 0,
  "failed": 0,
  "groups": [],
  "real_api_requests": 0,
  "network_requests": 0,
  "model_switches": 0,
  "regressions": [],
  "freeze_checks": {},
  "failures": []
}
```

不得手工伪造计数。报告中的真实请求数应来自 `APIRequestCounter` 与 spy 证据。

### 5.3 测试数据原则

- 只在临时目录生成最小字节文件；文件扩展名可模拟图片，QA 测试只依赖文件/hash 契约。
- Registry、Stage 13 manifest、Artifact 和 QA index 均由 fixture 构造。
- 使用固定内容、固定 ID 和确定顺序，避免时间/随机值参与断言。
- 需要测试 symlink/junction 逃逸时使用临时目标；平台不允许创建时必须在报告中标为环境限制，
  同时执行可验证 `resolve(strict=True)+relative_to` 的替代边界测试。不得静默跳过。

---

## 6. 正式测试矩阵

以下每个条目都必须有独立断言或明确的参数化子案例。

### T01：静态契约与冻结边界

1. ImageQAAgent/ImageQAStep/ImageQAStage 可导入，公开接口存在。
2. Prompt 文件存在，包含 exact check、verdict、negative_cues、多图顺序要求。
3. `FilmAgent/`、core、workflow、review、Provider base、request_counter、image_stage 0 diff。
4. Stage 13 manifest schema 与 Canonical Shot/Storyboard schema 未被修改。
5. `v0.2-stage13` 仍指向固定 commit。
6. 新实现中不存在直接 requests/urllib/SDK 网络调用或第二请求计数器。

### T02：manifest 选择和调用前失败

1. 顶层非对象、版本错误、project_id 错、images 非列表。
2. generated 条目缺 image_id/shot_ref/path/sha。
3. generated image_id 重复。
4. 只选择 `status=generated`；failed/skipped/rejected 不进入 QA。
5. 缺省保持 manifest 顺序。
6. `qa_image_ids` 保持用户顺序；未知、重复、空值、错误类型失败。
7. `max_qa_images`：负数/布尔/非整数失败；0 零调用；大于 3 收紧为 3。
8. 所有结构/选择失败均发生在 Provider 构造和调用前。

### T03：精确 Profile 版本和字段顺序

1. manifest 指向 v1、active 已是 v2 时仍使用 v1。
2. `approved` 与 `superseded` 可用；draft/generated/reviewing/rejected/archived 拒绝。
3. asset/version 缺失、kind/profile 类型不匹配失败。
4. profile_versions 为空、类型错、键/值为空失败。
5. profile 顺序保持 manifest mapping 插入顺序。
6. 单 profile 字段顺序严格来自 `FIELD_ORDER`。
7. 空字段不生成 check；Style `notes` 不评分；version notes 不当真值。
8. `negative_cues` 恰好一个逆语义 check。
9. 没有任何非空可评分字段时零调用失败。

### T04：D14-REF-01 全局 reference resolver

1. 独立 `REF_*` 的 approved reference 可解析。
2. superseded reference 版本可解析。
3. reference 位于 profile_versions 外仍可解析。
4. 全局零命中失败且 Provider 调用为 0。
5. 同一 image_id 跨资产重复，多命中失败且调用为 0。
6. 同一 image_id 跨版本重复，多命中失败且调用为 0。
7. 命中 draft/rejected/archived 版本失败。
8. manifest reference image_id 重复失败。
9. 禁止按 path、checksum、asset name 或 active version 猜测。
10. registry 扫描顺序变化不改变 manifest reference 输入顺序。

### T05：路径 containment 与 SHA256

generated 和 reference 分别覆盖：

1. 项目内相对路径通过。
2. 项目内绝对路径通过但持久化只保留相对 POSIX 路径。
3. 缺失文件、目录、空路径失败。
4. `..` 逃逸、同名前缀兄弟目录、其他盘符/UNC 路径失败。
5. symlink/junction 指向项目外失败。
6. 非 64-hex sha 失败；大小写可规范化。
7. generated manifest/disk 不一致失败。
8. reference manifest/registry/disk 三方每一种不一致均失败。
9. 上述失败均为 0 Provider 调用。

### T06：Prompt、图片顺序与敏感信息

1. `images[0]` 始终是 generated。
2. `images[1:]` 完全遵循 manifest reference 顺序。
3. Prompt 中 Image 位置标签与 tuple 一致。
4. Prompt 包含 expected checks 和 negative_cues 逆语义。
5. Prompt/异常/Artifact/index/report 不含绝对路径、API key、base64。
6. Prompt 不产生额外模型调用。

### T07：Provider 输出严格校验

1. 全 match → pass。
2. 任一 mismatch 优先 → fail。
3. 无 mismatch 且存在 uncertain/not_visible → needs_review。
4. 顶层非对象、checks 非列表/空、summary 非字符串失败。
5. 未知顶层字段失败；模型自报 qa_decision/provider/model 等允许被忽略并由本地覆盖。
6. check 缺字段、额外字段、字段类型错失败。
7. 缺 check、额外 check、重复 key、未知 asset/version/field 失败。
8. expected 被改写失败。
9. 非法 verdict 失败。
10. mismatch/uncertain/not_visible evidence 为空失败；match 可为空。
11. Provider 返回顺序不同，最终按 expected/Profile 顺序规范化。
12. 模型返回 model 与已批准配置不一致时不可重试失败。
13. Provider 异常对外只暴露安全错误类型，不泄漏路径/传输细节。

### T08：Workflow、Artifact 与 Review 三态

1. 每图独立 Workflow/Step/Artifact，base name 正确。
2. 重跑生成 `_v2` 等新版本，旧 Artifact 不覆盖。
3. approved：最终 Artifact 二次验证并写 index。
4. approved_with_edit 合法编辑：写新 Artifact，重新校验、重算 qa_decision。
5. approved_with_edit 修改锚点或可信 provider/model/request_id/retry_count/created_at：失败。
6. approved_with_edit 缺/加/重复 check 或非法决定：失败。
7. rejected：保留 Artifact，index 可审计，downstream_eligible=false。
8. QA fail 但 Review approved：可记录 approved 报告，但 downstream_eligible=false。
9. QA pass 且 Review approved-like：downstream_eligible=true。
10. QA/Review 任何结果都不修改生成图、Stage 13 manifest、Storyboard、ShotAsset 或 Registry。

### T09：QA index 完整性

1. 首次写入使用原子写且顶层 version/project_id/images 合法。
2. 更新同 image_id 替换该项，不覆盖其他图片记录。
3. Artifact 路径只保存项目相对路径，sha 正确。
4. workflow/review/qa_decision/provider/model/error 字段与实际一致。
5. 损坏 JSON、错误 version/project_id/images 类型 fail-loud。
6. index 中重复 image_id 必须 fail-loud，不能用 dict comprehension 静默取最后一项。
7. index 自报 `downstream_eligible` 必须由 Review + QA decision 重算；篡改值不得被 resume 信任。
8. index 不复制完整 checks，不成为第二状态机。

### T10：resume 命中与全部 stale 条件

先证明完整命中：Provider factory/chat 为 0，预算不消费。再逐项改变：

1. generated path、manifest sha、磁盘内容任一变化。
2. shot_ref 变化。
3. profile_versions 内容或**插入顺序**变化。
4. reference image_id/sha/顺序变化。
5. Artifact 缺失、越界、hash 改变、JSON 损坏、Schema 非法。
6. Artifact qa_decision 与本地重算不一致。
7. Workflow 非 success、Step 非 approved-like、Review 非 approved-like。
8. provider 或 model 变化。
9. index downstream_eligible 与重算不一致。
10. `qa_resume=false` 强制重跑且保留旧 Artifact。

任何 stale 均须重新 QA，而不是报“resumed”。合法 stale 产生新 Artifact 版本。

### T11：预算、retry 与模型授权

使用只消费本地 `APIRequestCounter`、不联网的 fake-real Provider：

1. `budget_kind=vision` 可用；multimodal/其他桶调用前拒绝。
2. 非 mock 且无 budget_kind 拒绝；缺 chat 调用面拒绝。
3. Provider 内 retry 与 Workflow retry 都累计到同一 vision 桶。
4. 第 N+1 次在发送前拒绝，counter 审计与 budget_consumed 一致。
5. 预算耗尽后不继续处理剩余图片。
6. 不得逐图重置 counter 或扩大 cap。
7. Provider 实例身份与预检 identity 不一致时零调用失败。
8. 没有自动 fallback、别名替换或模型切换路径。
9. test_report 中 real_api_requests/network_requests/model_switches 全为 0。

### T12：pipeline 与多图片隔离

1. `run_image_qa=False`：不读 QA manifest、不构造 Provider、不写 Artifact/index，Stage 13 行为保持。
2. `max_qa_images=0`：Provider factory/chat 为 0。
3. `generate_images=False + run_image_qa=True`：可读取已有 Stage 13 manifest 执行 QA。
4. pipeline 顺序为 image → image_qa → completeness。
5. completeness 既有定义不因 QA pass/fail 改变。
6. run_report 的 selected/resumed/attempted/pass/fail/needs_review/rejected/failed/
   budget_consumed/downstream_eligible/artifact paths 与实际一致。
7. 两张图/两个场景的 profile、refs、Artifact、index 不串数据。
8. 一张图预检失败不删除图片，并按既定语义处理其他选中项。

### T13：真实冒烟脚本的零调用护栏

`run_real_image_qa.py` 必须：

1. 默认只打印脱敏计划并非 0 退出，真实调用 0。
2. 缺 manifest/image、API key、精确 provider/model、用户执行开关任一项都拒绝。
3. `max_qa_images` 强制为 1，Provider retry 关闭或能证明不会超过批准预算。
4. 明确显示 provider、model、免费额度/成本为已知或未知；未知按可能计费。
5. 不显示 API key，不自动改 Config，不自动 fallback。
6. 本会话不得运行真实执行路径。

---

## 7. 总控代码审计指定的高风险断言

以下不是可选增强，必须加入正式测试；若失败则按缺陷修复：

1. `_load_index()` 对重复 image_id 不能静默覆盖。
2. resume 不能直接信任 index 的 `downstream_eligible`，必须按 Review + qa_decision 重算。
3. profile_versions 的插入顺序改变会改变 Prompt 语义，必须使 resume stale。
4. shot_ref 变化不能误命中 resume。
5. approved_with_edit 必须同时允许合法 checks/summary 编辑，并拒绝可信 Provider 元数据篡改。
6. 预算耗尽必须拦截第 N+1 次，并停止剩余真实请求。
7. 全部 selected 项预检失败时不得构造 Provider。
8. 默认关闭时不得因 QA 接线创建质量目录、index 或调用记录。

---

## 8. Debug 规则

1. 先写能稳定复现问题的失败测试。
2. 只修改 Stage 14-B 新增模块或最小接线。
3. 不删除、跳过、放宽断言，不把 fail 改成 warning。
4. 不通过捕获 `Exception` 后继续来制造 PASS。
5. 不修改 core/workflow/review/Provider/request_counter 迁就 Stage 14。
6. 每次修复先跑相关组；局部稳定后再跑 Stage 14 全套。
7. 完整 Stage 14 全绿后只运行一次历史全回归，避免无意义重复消耗时间。
8. 若出现架构冲突，暂停并报告，不扩大 Scope。

---

## 9. 历史回归顺序

建议顺序：

```powershell
python v2/logs/stage14_image_qa/run_tests.py
python v2/logs/stage13_image/run_tests.py
```

Stage 13 套件已经嵌套其所需历史回归时，不再逐个重复运行 Stage 3–12 套件。必须记录：

- 命令、开始/结束时间、退出码；
- Stage 13 总数/通过/失败；
- 合法的 pre-commit 护栏冲突及证据；
- 功能失败不得豁免。

运行前后比较 `git status --short`。若历史套件重写报告，只精确恢复测试造成的既有噪声；不得
回退当前 Stage 14 实现或总控文档。`test_report.json` 是本阶段正式产物，必须保留。

---

## 10. 真实调用与模型切换规则

本会话：

```text
真实 API 请求 = 0
网络请求 = 0
自动模型切换 = 0
```

不得以“已有 API key”“模型已开通”或“存在免费额度”为由运行真实冒烟。未来真实运行前必须
单独向用户说明精确 provider+model、免费额度是否确认、最大调用次数和可能费用，并取得明确
同意。额度未知按可能收费处理。

---

## 11. Acceptance Criteria

1. `run_tests.py` 覆盖 T01–T13，退出码和机器报告一致。
2. Stage 14 新套件全部通过，无静默 skip；平台限制有明确替代测试和报告。
3. Stage 13 当前套件及其历史回归 rc=0；任何豁免仅限可证明的既有护栏。
4. D14-REF-01 零/一/多命中和非法状态全部得到验证。
5. 路径/SHA/Schema 错误均在 Provider 调用前失败。
6. Review 三态、Artifact 版本化、合法/非法 edit 全部验证。
7. QA index 损坏/重复记录 fail-loud，不信任派生字段。
8. resume 命中零调用，全部 stale 条件重新 QA，provider+model 是身份锚点。
9. 预算第 N+1 次被拒，所有 retry 共用同一 vision cap。
10. pipeline 默认关闭零副作用，QA 不改变 completeness。
11. 测试、报告和工作区无密钥、base64、绝对路径、图片、JSONL、缓存或临时目录泄漏。
12. 冻结边界 0 diff，`v0.2-stage13` 未移动。
13. 真实 API/网络/模型切换均为 0。
14. 未 commit、未 tag。

任一项未满足，Stage 14-C 状态为 FAIL 或 BLOCKED，不得进入 14-D 完成态文档。

---

## 12. 最终返回给总控

必须返回：

1. 测试总数、通过/失败、分组结果。
2. 发现的每个缺陷：复现测试、根因、修改文件、修复方式。
3. Stage 14 全套与 Stage 13 历史回归命令、rc、耗时。
4. APIRequestCounter 与网络 spy 的 0 调用证据。
5. `run_real_image_qa.py` 默认拒绝路径验证结果，明确未执行真实路径。
6. `test_report.json` 路径及其摘要。
7. `git status --short`、`git diff --stat`、冻结边界、泄漏扫描和 tag 结果。
8. 是否满足进入 Stage 14-D 的判断：PASS / FAIL / BLOCKED。
9. 明确声明：未 commit、未 tag、未运行真实 API、未自动切换模型。

---

## 13. 发现架构问题时

若测试证明必须修改禁改文件、Stage 13 manifest、公共 Provider 接口或 Workflow/Review 才能
正确实现，立即停止相关修复并上报：

- 失败测试 ID；
- 文件/行号和最小复现；
- 当前契约为何无法满足；
- 至少两个方案；
- 兼容、回归、成本与迁移影响。

总控裁决前不得扩围。
