# FilmAgent V0.2 阶段 7 —— Scene / Script Generation Agent 验收报告

## 0. 一句话总结

V0.2 接入**第二个真实业务 Agent（Scene / Script Generation）**：复用阶段 1 的 `Scene` 模型与三合一 `Validator`（`models.py` / `validator.py` **零改动**），复用 `ContextAssembler`（`needs` 声明式）与 `Workflow / Artifact / Review / Retry` 全套基础设施，首次打通
`项目 → Memory → ContextAssembler → SceneGenerationAgent → LLMClient(真实 DeepSeek) → Validator → Artifact(scene_1.json) → Review(APPROVED)` 完整真实闭环。
**Mock 29/29 全过，回归 19/19 + 38/38 + 25/25 + 24/24 全过，真实调用 3 次全成功（累计 ¥0.06），V0.1 零改动。**

---

## 1. HEAD / 环境

| 项 | 值 |
|---|---|
| git HEAD | `344c0c6b7c48497be934957c29fcae029cca6752`（= v0.1 tag） |
| 操作系统 | Windows 11 Home China（win32） |
| Python | Anaconda 环境（3.9+，实测） |
| 真实模型 | `deepseek-chat`（`config.DEFAULT_MODEL`） |
| API key | 环境变量（`config.api_key_masked()` 脱敏显示 `sk-****6509`，全文不出现明文） |
| 真实调用计费 | 3 次成功，累计 ¥0.060165 |

---

## 2. V0.1 完整性

`run_tests.py` **T26** 自动化断言通过：

```text
[PASS] T26 V0.1 完整性检查通过  ->  HEAD=344c0c6; v0.1=344c0c6; diff空=True; status='?? CLAUDE.md\n?? v2/'
```

- `git rev-parse HEAD` == `git rev-parse v0.1^{commit}` == `344c0c6b…`
- `git diff -- FilmAgent/` 为空
- `git status --short` 无任何 `M` 行（只有预期的 `?? v2/` 未跟踪）

---

## 3. 新增 / 修改文件

**新增（全部在 v2/ 内）：**

| 文件 | 说明 |
|---|---|
| `v2/agents/scene_generation.py` | `SceneGenerationAgent`（业务核心）+ `SceneGenerationStep`（Workflow Step 壳） |
| `v2/prompts/v0.2/scene_generation.txt` | Scene Generation Prompt（10 条规则 + JSON 输出 schema，含站位独占规则） |
| `v2/logs/stage7_scene_generation/run_tests.py` | Mock 测试套件（T01~T19 + T26 + 回归 R1~R4） |
| `v2/logs/stage7_scene_generation/run_real_scene_generation.py` | 最小真实 DeepSeek 调用（T21~T25） |
| `v2/logs/stage7_scene_generation/fixtures/actors_profile.json` | 4 个真实角色（从 stage6 复制，只读） |
| `v2/logs/stage7_scene_generation/fixtures/scenes_1.json` | V0.1 真实场景数据（从 stage6 复制，只读） |
| `v2/logs/stage7_scene_generation/fixtures/scene_spec.json` | 真实场景规范（含 3 角色 / 地点 / objective / constraints） |

**修改：**

| 文件 | 改动 |
|---|---|
| `v2/agents/__init__.py` | 导出 `SceneGenerationAgent` / `SceneGenerationStep` |
| `v2/prompts/v0.2/scene_generation.txt` | 修复时新增"每个角色必须独占站位点"规则（见 §15 遇到的问题） |

**未改动（复用，指令 §二十）：** `core/models.py`、`core/validator.py`、`context/assembler.py`、`workflow/*`、`review/*`、`memory/*`、`llm/*`、`config.py`。

---

## 4. Agent 做了什么

`SceneGenerationAgent` 是 V0.2 接入的**第二个真实业务 Agent**，回答"**谁 / 在哪里 / 什么时候 / 做什么 / 说什么 / 为什么做**"——根据项目已确认的事实 + 角色 + 地点 + 动作/镜头资产 + 一个明确的场景规范（scene_spec），由 LLM 生成**单个场景的结构化剧本**（台词 / 动作 / 镜头 / 走位）。

- 输入**只来自 `ContextAssembler` 组装的 `AssembledContext`**（`needs = ["project_info","approved_facts","characters","locations","actions","shots"]`）+ 调度层放入 `inputs["scene_spec"]` 的场景规范。**不读磁盘文件**。
- LLM **只走 `LLMClient`**（统一错误 / 重试 / token 日志 / 脱敏）。`agent`/`step` 标记均为 `scene_generation`。
- 产出 `Scene` 对象（复用阶段 1 模型），Step 落盘用 `scene.to_v01()`。
- **不生成文件名**（artifact 名由 Step 决定：`scene_{scene_id}.json`）；**不处理 retry**（`StepError` 上抛，引擎重试）；**不决定 Review / Memory 状态**（`needs_review=True` 由 Step 声明，审核走 review_handler）。
- `_normalize` 确定性兜底（非内容改写）：`who`/`where` 缺失时用场景规范（权威输入）补齐；`scene` 行必须是 list，否则抛 `StepError`（可重试）。内容合法性（角色/地点/动作/镜头/位置）交给 Validator。

`SceneGenerationStep`：`step_id = scene_generation_{scene_id}`（多场景是独立 step，resume 不串场）、`artifact_name = scene_{scene_id}`、`needs_review = True`、`validate()` 用 Validator 转错误字符串列表。

---

## 5. ContextAssembler

**零改动复用**。测试向 `ContextAssembler` 注入真实资产注册表 `AssetRegistry.load(v2/assets/locations/)`，按 Step 声明的 `needs` 组装 6 项上下文：

```text
project_info / approved_facts / characters / locations(15 个) / actions(20 个) / shots(9 个)
```

- 每项带来源（source/source_type/source_id/version），可追踪。
- `scene_spec` 不走 provider，由调度层放入 `WorkflowContext.inputs["scene_spec"]`（指令 §十一 允许的最小增量，**不新增 provider、不改组装逻辑**）。
- T03 验证 6 个 needs 全部已被注册且能取到真实资产。

---

## 6. Validator

**完全复用阶段 1 的三合一 `Validator`（Enum / Reference / Schema）**，`core/validator.py` **零改动**。它做**最终确定性拦截**（不替 LLM 编剧）：

| 错误码 | 含义 | 测试 |
|---|---|---|
| `UNKNOWN_CHARACTER` | 角色不在项目角色清单 | T06（Charlie） |
| `INVALID_LOCATION` | 地点未注册 | T06（"Billiard room kitchenette"） |
| `INVALID_ACTION` | 动作未注册（自动剥离数字后缀） | T06（"Flying Dance"） |
| `INVALID_SHOT` | 镜头未注册（自动剥离数字后缀） | T06（"Drone Shot 2"） |
| `INVALID_POSITION` | 站位点/走位目标不存在于该地点 | T06（"Position 99"） |
| `MISSING_LOCATION` | 场景缺地点 | T06（where 为空且 spec 不提供） |
| `POSITION_CONFLICT` | 同一时刻两人同占一个位置 | 真实环境拦截（见 §15） |

T05 合法 Scene 通过 Validator 0 错误。

---

## 7. Workflow / Artifact / Retry

**零改动复用阶段 3/4 引擎**：

- **版本化不覆盖**：第 1 次尝试落 `scene_1.json`，重试落 `scene_1_v2.json`……**绝不覆盖**（T08 验证两个版本内容不同、互不覆盖）。
- **每次尝试先落盘再校验**：质量不合格的文件也保留（轨迹不可丢）。
- **三次非法 JSON → FAILED 停止**，且不落盘（T09，`StepError` 可重试）。
- **resume**：已批准 Step 直接 `SKIPPED`，不再调 LLM（T18，calls 保持 1）。
- **多场景独立**：`scene_id=1/2` → `scene_1.json` / `scene_2.json` 互不覆盖（T17）。
- artifact 落盘内容可读回为 `Scene`（T19）。

---

## 8. Review

**三态全测（T13/T14/T15）**，复用 `ReviewService` + 引擎 review_handler：

| 决策 | 行为 | 结果 |
|---|---|---|
| `APPROVED` | 采用原产物，run 继续 | scene=APPROVED，下游步骤执行，`run=success` |
| `APPROVED_WITH_EDIT` | 写"编辑后的新版本"，原文件保留 | 文件 `scene_1.json` + `scene_1_v2.json`，都不覆盖 |
| `REJECTED` | 流程停止 | scene=REJECTED，下游不执行，`run=failed` |

---

## 9. Memory

**复用三态机制，candidate 绝不自动升 approved**（T12 验证）：

- Memory 层：两条 candidate，人工 approve 一条 → `get_approved_facts()` 只含 1 条，candidate 不进入。
- Context 层：Scene 的 `approved_facts` 只喂 approved 事实。
- **实际喂给 LLM 的 user payload**：含 approved 事实内容，**不含** candidate 内容（断言验证）。

跨集一致性（T11）：第二集继续使用已有角色清单（`who ⊆ 已有角色`），Agent 输出不携带任何"新角色档案"，不重复建档。

---

## 10. Mock 结果（真实 LLM 调用 = 0）

`run_tests.py` → **29/29 通过**（`sys.exit(0)`）：

- T01 初始化 / T02 needs 声明（6 项全部已注册 + needs_review）/ T03 ContextAssembler 组装
- T04 Mock 合法输出解析成 Scene / T05 Validator 通过 / T06 Validator 拒绝 6 类非法输出
- T07 非法输出 → retry → 成功（2 次调用）/ T08 双版本并存 / T09 三次失败 → FAILED
- T10 scene_spec 兜底 who/where / T11 跨集一致 / T12 candidate 隔离
- T13 APPROVED 继续 / T14 APPROVED_WITH_EDIT 新版本 / T15 REJECTED 停止
- T16 LLMClient 记录 request_id/token/cost / T17 多 scene_id / T18 resume SKIPPED / T19 artifact 可读回
- T26 V0.1 完整性
- 回归 R1~R4 见 §13

证据文件：`stage7_report.json`、`console.txt`。

---

## 11. 真实结果（T21~T25）

`run_real_scene_generation.py` → **5/5 通过**，3 次成功真实 DeepSeek 调用：

| 编号 | 内容 | 结果 |
|---|---|---|
| T21 | 第 1 场真实生成（3 真实角色 + Apartment living room + 已确认事实） | ✅ `who=[Caleb,Ivy,Theo]`，lines=10，Validator 0 错误，一次通过 |
| T22 | 第 2 场真实生成（Mercy/Caleb + Apartment kitchen，不同场景规范） | ✅ lines=11，Validator 0 错误，一次通过 |
| T23 | token / cost / request_id 正确记录 | ✅ 成功记录数=2+，request_id / total_tokens / estimated_cost 齐全 |
| T24 | 真实环境失败路径（模拟断网，mock transport，不烧钱） | ✅ LLMConnectionError 正确记录，retry_count=1，不崩溃 |
| T25 | **完整 Workflow 真实走一遍**：真实 LLM → 落盘 `scene_1.json` → Review approved → `APPROVED` | ✅ `run=success`，`review_id=review-01e99e710b` |

T25 真实产物（`real_tmp/projects/r25_full_workflow/artifacts/scene_1.json`）：3 角色完整对话场景（16 行，含 2 条 `move` 走位行），情节完全贴合 approved fact（Theo 三年隔阂、Mercy 重病、为 Mercy 赴约），动作/镜头全部来自真实资产表，站位无冲突。

**验收点：APPROVED 后闭环成立** —— 真实 LLM 产出经 Validator 校验 → 版本化落盘 → 人工审核 APPROVED → Workflow 成功。

---

## 12. Token / Cost

每次真实调用记录（`real_summary.json` + token log，**不含 API key、不含 prompt 正文**，只存 request_id/token/cost/success/retry）：

| 调用 | request_id（前 12） | step | tokens | 成本(¥) | success | retry | duration(ms) |
|---|---|---|---|---|---|---|---|
| T21 | `a37f814a5cc6` | scene_generation | 3691 | 0.018655 | ✅ | 0 | 15063 |
| T22 | `97a628c7212e` | scene_generation | 3421 | 0.016621 | ✅ | 0 | 9343 |
| T25 | `0ca2890df9b8` | scene_generation | 4383 | 0.024889 | ✅ | 0 | 13688 |
| **合计** | | | **11495** | **¥0.060165** | 3/3 | 0 | |

Mock 套件 0 次真实调用、0 成本。

---

## 13. 回归

`run_tests.py` 内置 R1~R4，全部通过（subprocess 运行 + 断言输出）：

| 回归 | 套件 | 结果 |
|---|---|---|
| R1 阶段 1 | `stage1_validation_test/run_tests.py` | ✅ **19/19 通过** |
| R2 阶段 3 | `stage3_workflow_test/run_tests.py` | ✅ **38/38 通过** |
| R3 阶段 4 | `stage4_memory_context_review_test/run_tests.py` | ✅ **25/25 通过**（内含阶段 2 mock 复验） |
| R4 阶段 6 | `stage6_casting/run_tests.py` | ✅ **24/24 通过**（mock） |
| 阶段 5 静态检查 | compileall + 全模块 import 冒烟 + 正则扫 key/硬编码路径 | ✅ 全部通过（无 API key、无硬编码绝对路径） |

---

## 14. V0.1 diff

**空**。`git diff -- FilmAgent/` 无输出；V0.1 的 `Prompt/`、`Locations/`、`Script/`、`LLMCaller.py`、`main.py`、`api_key.txt` 均未触碰。

---

## 15. 遇到的问题及修复

1. **真实 LLM 第一场站位冲突（POSITION_CONFLICT）**
   - 现象：首轮真实运行 T21，真实 LLM 让两个角色同占一个站位点，被 Validator 正确拦截（`POSITION_CONFLICT at scene[1].initial_positions[2]` 等 12 处）。
   - 判断：**Validator 拦截是对的**（同一时刻两人不能同站一位）；根因是 prompt 未明说"每个角色必须独占站位点"。
   - 修复：`scene_generation.txt` 新增规则 7 —— "At any single moment, each character must occupy a DISTINCT position"，并对后续规则重新编号（1–10）。
   - 结果：修复后 T21 一次通过（Validator 0 错误）。Validator 保持不变，继续兜底。

2. **真实 LLM 长输出偶发非法 JSON（控制字符）**
   - 现象：首轮 T22 真实调用返回含未转义控制字符的 JSON，`json.loads` 抛 `StepError: Invalid control character`。
   - 判断：这是真实 LLM 的常见瑕疵，正确语义是"可重试"（`StepError.retryable=True`，引擎会重试）。
   - 修复：T21/T22 统一按引擎语义做"捕获 `StepError` → 重试（最多 3 次）"兜底，并如实记录拦截/异常证据。
   - 结果：T22 一次通过。

3. **真实脚本"抛异常却显示全绿"的假象**
   - 现象：`main()` 用 try/except 包住真实调用，异常只 `log` 不记入结果 → T22 抛异常时套件仍 4/4。
   - 修复：catch 后 `check(..., False)` 如实记为 FAIL；同时 T21/T22 内部把重试耗尽也如实反馈。
   - 结果：真实套件对任何异常都能如实反映。

---

## 16. 遗留问题

1. **人工审核目前是程序化 decision 模拟**（测试注入 review_handler 直接 approve），交互式审核界面未做（与阶段 6 相同，属已知遗留）。
2. **`v2/README.md` 已过时**：仍写"尚未实现任何业务 Agent"，但阶段 6（Casting）与阶段 7（Scene Generation）均已实现。**待用户确认是否更新**（本阶段未改动，避免越界）。
3. **真实 LLM 偶发非合规输出（站位冲突 / 控制字符）**：已由 Validator 拦截 + 重试兜底，但长期看可在调度层做"生成后自动重试 N 次"的产品化策略（引擎已具备，尚未封装为默认配置）。
4. v2 尚未 commit（按阶段约定等人工验收后处理）。

---

## 17. 偏差

无实质偏差。说明两点与指令的对齐情况：

- 指令 §十六 D 要求"真实调用 3~8 次"：本阶段 **3 次成功真实调用**（T21/T22/T25 各 1），T24 为 mock 失败路径（不烧钱），满足区间且最小化。
- Mock 测试编号 T01~T19 + T26 对应指令 §十六 A–E 的覆盖项；T21~T25 为真实脚本编号（对齐阶段 6 的命名惯例），并非缺失。

---

## 18. 验收结论

对照开工指令 §二十三 验收指标：

| 指标 | 结果 |
|---|---|
| V0.1 modified = NO | ✅ T26 断言 + `git diff -- FilmAgent/` 为空 |
| Scene Generation Agent 已实现 | ✅ `v2/agents/scene_generation.py` |
| 使用统一 LLMClient / Router | ✅ LLM 全走 `LLMClient.chat(step="scene_generation", agent="scene_generation")` |
| 使用 ContextAssembler | ✅ 6 个 needs 声明式组装，零改动复用 |
| Agent 不直接读项目文件 / 管理 Artifact / Retry | ✅ 输入只来自 AssembledContext；文件名由 Step 定；`StepError` 交引擎重试 |
| Validator 拦截关键非法输出 | ✅ 7 类错误码全测 + 真实环境拦截 POSITION_CONFLICT |
| Review 三态有测试 | ✅ T13/T14/T15 |
| Retry 不覆盖历史 | ✅ T07/T08/T14 版本化验证 |
| 至少一个真实 DeepSeek Scene Generation 成功 | ✅ T21/T22/T25，3 次全成功 |
| 真实输出进 Artifact / Review | ✅ T25 落盘 `scene_1.json` + Review `APPROVED` |
| APPROVED 后闭环成立 | ✅ `项目→Memory→ContextAssembler→SceneGeneration→Validator→Artifact→Review→APPROVED` |
| 阶段 1/3/4/6 回归通过 | ✅ 19/19 + 38/38 + 25/25 + 24/24 |
| API key 零泄漏 | ✅ 只显示 `sk-****6509`；token log 不含 key / prompt 正文 |
| token/cost 可追踪 | ✅ 每次调用 request_id/tokens/cost/success/retry 全记录 |
| 如实报告 | ✅ 含首轮拦截/异常与修复过程 |

**结论：阶段 7 验收通过。** 按指令 §二十五，**立即停止**，不自动进入阶段 8，不自动 commit/tag，等待人工批准。

---

## 19. 下一阶段建议

1. **接入第三个业务 Agent**（Dialogue / Shot / Script Generation），让"场景剧本 → 台词 → 镜头 → 最终剧本"串成真实业务链，与 Casting / Scene Generation 组成第一条完整业务链。
2. **产品化重试**：把"生成后 Validator 拦截 → 自动重试 N 次"封装为调度层默认策略（引擎已支持）。
3. **交互式人工审核界面**：把 review_handler 从程序化 decision 升级为可视化 approve/edit/reject。
4. 经用户确认后更新 `v2/README.md`，并在验收通过后 commit / tag V0.2 阶段 7。
