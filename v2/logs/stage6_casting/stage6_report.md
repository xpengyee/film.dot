# V0.2 阶段 6 验收报告 —— Casting Agent 接入真实业务闭环

> 日期：2026-09-01
> 对应开工指令：`D:\AI\FilmAgent\v0.2开工指令6`
> 执行范围：首次让 V0.2"真正干活"——接入第一个真实业务 Agent（Casting），走通
> Project → Memory → ContextAssembler → CastingAgent → LLMClient → Validator → Artifact → Review 全闭环。

---

## 1. 一句话总结

**V0.2 首次用真实 LLM 跑通了 Casting 全闭环**：真实 DeepSeek 输入 → Validator 通过 →
落盘版本化 Artifact → Review 审核 → Workflow APPROVED；跨集决策（老角色复用、新角色建档）在
Mock 与真实调用下都验证成立。

## 2. V0.1 是否完全未修改

**是，完全未修改。**
- `git diff -- FilmAgent/` 为空（无任何输出）。
- `git rev-parse HEAD` == `git rev-parse v0.1^{commit}` == `344c0c6b7c48497be934957c29fcae029cca6752`。
- `git status --short` 只有 `?? v2/`（V0.2 整目录未跟踪，本就未提交），没有任何 `M` 前缀行。
- 上述断言固化为测试 T26，每次回归自动复核。

## 3. 新增 / 修改了哪些 V0.2 文件

**修改（3 个，都在 v2/core/ 内，均为纯增量）：**
| 文件 | 改动 |
|---|---|
| `v2/core/models.py` | 新增 `CastingEntry`（name/status/role/character）与 `CastingResult`（project_id/episode/topic/entries/created_at）两个数据模型，`__all__` 同步 |
| `v2/core/validator.py` | 新增 `CastingValidator`（existing 引用合法性 / 新角色不重名 / 档案字段齐全 的确定性兜底），`__all__` 同步 |
| `v2/core/__init__.py` | 导出上面 3 个新符号 |

**新增（5 个）：**
| 文件 | 内容 |
|---|---|
| `v2/prompts/v0.2/casting.txt` | V0.2 版 Casting Prompt（占位符 `{topic}`/`{existing_characters}`/`{approved_facts}`/`{world}`/`{character_limit}`，只允许输出合法 JSON）。V0.1 冻结的 `Prompt/` 只读分析、未动 |
| `v2/agents/__init__.py` | 业务 Agent 层包入口，导出 `CastingAgent`/`CastingStep` |
| `v2/agents/casting.py` | **核心**：`CastingAgent`（业务核心）+ `CastingStep`（Workflow Step 壳） |
| `v2/logs/stage6_casting/run_tests.py` | Mock 测试 T01~T20 + T26 + 回归 R1~R3（24 项断言） |
| `v2/logs/stage6_casting/run_real_casting.py` | 最小真实 DeepSeek 调用 T21~T25（3 次真实调用） |

**数据 fixture（只读复制，未改原文件）：**
`v2/logs/stage6_casting/fixtures/actors_profile.json`（4 个真实角色）与 `scenes_1.json`，来源 `FilmAgent/Script/`。

## 4. Casting Agent 做了什么

`CastingAgent.run(context)` 是"本集选角"决策者：
1. 从 `AssembledContext` 读 `project_info`（话题）、`approved_facts`（已确认剧情）、`characters`（已有角色）、`world`（世界观）；
2. 组装 system（Prompt）+ user（payload）两条消息，交 `LLMClient.chat`；
3. 解析 LLM 输出（容忍 ```` ```json ```` 围栏）；
4. **确定性规范化**：名字命中已有角色 → `existing`（复用，忽略 LLM 自创档案，防改档）；否则 → `new`（必须带完整档案）；
5. 返回 `CastingResult`。

`CastingStep` 把 Agent 接进 Workflow：声明 `needs`/`needs_review`，`run()` 产 `StepOutput(data=result.to_dict(), artifact_name="casting")`，`validate()` 用 `CastingValidator` 兜底（不合格 → 引擎重试）。

## 5. Casting 输入是什么

只读四样，全部来自 ContextAssembler 按 needs 组装，**绝不自己读磁盘**：
- `project_info`：项目话题（topic）；
- `approved_facts`：已人工确认的剧情事实（只读）；
- `characters`：项目已有角色清单（只读，用于复用判定）；
- `world`：世界观设定（只读）。

## 6. Casting 输出是什么

`CastingResult`（落盘为 `casting.json`）：
```json
{
  "project_id": "r25_full_workflow",
  "episode": 1,
  "topic": "Reconcilation in a friend reunion",
  "entries": [
    {"name": "Mercy", "status": "existing", "role": "The host of the reunion who initiates the reconciliation attempt."},
    {"name": "Caleb", "status": "existing", "role": "..."}
  ],
  "created_at": "2026-09-01T13:21:44.088+00:00"
}
```
`existing` 条目只有名字 + 本集角色定位（引用，不重复建档）；`new` 条目额外带完整 `character` 档案。

## 7. needs 声明是什么

```python
needs = ["project_info", "approved_facts", "characters", "world"]
```
测试 T02 断言这四个 key 都已在 `ContextAssembler.available_keys()` 注册，且 `needs_review=True`。

## 8. Memory 如何处理

严格遵守"LLM 输出了一个角色 ≠ 项目永久记住这个角色"：
- **Agent 只读** Memory（`approved_facts`/`characters`），不写；
- 新角色建档走 Memory 的 candidate 通道：调度层可 `add_candidate_fact(source="casting")`，状态为 `candidate`；
- **绝不自动 approved**——T13 验证：candidate 不进入 approved 清单，必须人工 `approve_fact` 后才进入；
- `CastingStep` 自身不写 Memory（符合 AGENTS.md）。

## 9. Validator 如何处理

`CastingValidator` 做确定性兜底（不依赖 LLM 自我审查），错误码：
- `INVALID_STATUS`：status 只能是 `existing`/`new`；
- `UNKNOWN_CHARACTER`：`existing` 却引用了项目里不存在的角色；
- `DUPLICATE_ROLE`：`new` 却与已有角色重名（该复用），或同一集内重名；
- `MISSING_FIELD`：新角色档案缺 age/gender/occupation/personality traits/speaking style 中任一非空字段；
- `WRONG_TYPE`：episode 不是 int 等。

`CastingStep.validate` 把错误转字符串列表返回引擎 → 决定重试。T06 验证 5 类非法输入全被拒。

## 10. Artifact 如何处理

版本化留存，**绝不覆盖**：
- 每次落盘 `casting.json` → 下一次（retry 或审核后编辑）自动探测磁盘最大版本 → 写 `casting_v2.json`、`casting_v3.json`…；
- T08：第一次不合格版本落盘 `casting.json`（age=null 的缺字段版），重试成功版本落盘 `casting_v2.json`，**两版都在**；
- T15：APPROVED_WITH_EDIT 的编辑版写 `casting_v2.json`，原 `casting.json` 保留。

## 11. Review 如何处理

`needs_review=True`，Casting 成功产出后进入人工审核节点，三态全验证：
- **APPROVED**（T14）：继续执行下游步骤（`scene_plan` 成功执行），casting 状态 APPROVED，记录 review_id；
- **APPROVED_WITH_EDIT**（T15）：编辑内容写新版本文件（`casting_v2.json`），原文件不覆盖，状态 APPROVED_WITH_EDIT；
- **REJECTED**（T16）：流程停止（下游步骤不执行），run failed，状态 REJECTED。

本阶段审核 handler 由测试用程序化 decision 构造（模拟人工），真实人工交互界面留给后续阶段。

## 12. Workflow 如何处理

`CastingStep` 是引擎里真正的 Step（依赖阶段 3 的 WorkflowEngine）：
- run 成功但 validate 有错 → 落盘 + 重试（T07）；
- run 抛异常（LLM 输出无法解析）→ 不落盘，可重试（T09 三次失败后 FAILED 停止）；
- **resume**（T17）：第一次 run APPROVED 后 `resume_from=run1` 再跑 → casting 直接 SKIPPED（复用已批准产物），**不再调 LLM**（调用次数保持 1）。

## 13. LLMClient 如何接入

CastingAgent 只用 `llm_client.chat(messages, step="casting", agent="casting")`，不自己找 key、不调 requests：
- `request_id`：每次调用一个 ID，把 prompt/响应/token/成本串起来（T20/T23 验证日志一致）；
- `retry`/`error`：只重试可重试错误，失败写 `success=False` 记录（T24 模拟断网验证：记录 `LLMConnectionError`、retry_count=1、不崩溃）；
- token/cost：`TokenUsage` 与 `estimate_cost`（高峰价保守估计）写入 token log（T19）；
- 全程不记 key、不记 prompt 正文（日志只有 `prompt_hash`/`prompt_length`，实测 0 处 `sk-`）。

## 14. Prompt 使用情况

- **V0.1 只读**：分析了 `FilmAgent/Prompt/director_1.txt`（Casting 部分）、`Script/actors_profile.json`（6 字段档案）、`scenes_N.json`（`selected-characters` 引用角色名，不重复建档）；
- **V0.2 复制**：`v2/prompts/v0.2/casting.txt`（不动冻结的 V0.1 原文），明确要求已有角色用 exact name 引用、新角色带完整档案、只输出合法 JSON、角色数上限 `{character_limit}`（沿用 V0.1 的 4）。

## 15. Mock 测试结果

**24/24 全 PASS（0 次真实调用）**，见 `stage6_report.json` / `console.txt`：
- T01~T03 初始化 / needs / ContextAssembler；
- T04~T06 Mock LLM 合法/Validator 通过/拒绝；
- T07~T09 retry / 版本留存 / 三次失败；
- T10~T12 复用 / 新角色 / 不重复建档；
- T13 candidate 不自动升 approved；
- T14~T16 Review 三态；
- T17 resume；
- T18~T20 LLMClient / token / request_id；
- T26 V0.1 完整性。

## 16. 真实 LLM 调用次数

**3 次**（run_real_casting.py，DeepSeek `deepseek-chat`，`sk-****6509`）：
1. T21 第一集（无已知角色）→ 返回 4 个全新角色，全带档案；
2. T22 第二集（已知 4 个真实角色）→ 全部复用为 `existing`；
3. T25 完整 Workflow → 落盘 + 审核 APPROVED。
> 未做完整剧本生成（按指令最小化原则）。

## 17. token 总量

**2099**（3 次成功真实调用累计）。

## 18. cost 总量

**≈ ¥0.0102**（按 config 高峰价保守估算，实际以平台为准）。

## 19. 真实 Casting 是否成功

**成功**：
- T21：真实 LLM 输出 `Maya/Liam/Sofia/Noah` 4 个新角色，Validator **0 错误**；
- T22：真实 LLM 输出 `Mercy/Caleb/Ivy/Theo` 全部 `existing`，复用判定成立；
- T25：真实产物 `real_tmp/projects/r25_full_workflow/artifacts/casting.json`（4 角色全 `existing` + 本集 role），Workflow 状态 `APPROVED`，review 文件落盘。

## 20. 跨集角色复用测试结果

**通过**：
- Mock T10：已有角色 → `existing`（不带档案）；T12：第二集 Nina 再出现 → `existing`（不重复建档）；
- 真实 T22：4 个真实角色全部复用，不创建第二身份。

## 21. 新角色创建测试结果

**通过**：
- Mock T11：Nina 建档完整（age=29、occupation=Journalist 等）；
- 真实 T21：4 个新角色档案齐全、字段非空、通过 Validator。

## 22. retry 版本留存测试结果

**通过**（T08/T09）：
- 第一次输出缺字段（age=null）→ Validator 拒 → 重试成功 → `casting.json`（旧）+ `casting_v2.json`（新）**双版本并存、内容不同、互不覆盖**；
- 连续三次无法解析的 JSON → run FAILED 停止，attempts=3，旧版本不被覆盖。

## 23. Review 三种状态测试结果

**通过**（T14/T15/T16）：APPROVED 继续下游 / APPROVED_WITH_EDIT 写新版本 / REJECTED 停止，见 §11。

## 24. Workflow resume 测试结果

**通过**（T17）：resume 后已成功 Step 直接 SKIPPED，复用已批准产物，**LLM 调用保持 1 次不浪费**。

## 25. 阶段 1–5 回归测试结果

| 阶段 | 结果 | 说明 |
|---|---|---|
| 阶段 1 | **19/19 通过** | 独立运行 |
| 阶段 2 | **通过** | 阶段 4 套件内 mock 复验（0 真实调用，指令允许） |
| 阶段 3 | **38/38 通过** | 独立运行 |
| 阶段 4 | **25/25 通过** | 独立运行（含阶段 1/2(mock)/3 复验） |
| 阶段 5 | **静态检查复核通过** | compileall 全过；全模块 import（含新增 `agents`）无 ImportError/循环依赖；敏感信息扫描 v2/ 源码 0 处真实 key；硬编码绝对路径 0 处 |

## 26. 是否存在遗留问题

如实列出（不隐藏）：
1. **openai 库此前未安装**：阶段 2 起全部测试用 mock，从未触发真实 transport；本次真实调用暴露 `ModuleNotFoundError`。已用清华镜像 `pip install openai`（2.48.0）解决。**换机器/重装环境时需 `pip install openai`**（V0.1 的 `LLMCaller.py` 同样依赖它）。
2. **测试套件做过 3 处断言修正**（均在测试代码，未碰任何实现）：
   - T08：`Character.from_v01({"name": "Nina"})` 缺 age 时得 `None`（落盘为 null）而非空串，初次断言比较方式写错 → 改为比较 `old_age is None`；
   - T11：`Character` 序列化方法名是 `to_v01()` 而非 `to_dict()`，测试调用了错误方法名；
   - T13：日志变量取值时机在 `approve_fact` 之后（该调用原地改状态），日志显示不直观 → 把取值提前到 approve 前。断言本身无变化，三处修正后 24/24 全 PASS。
3. **成本估算为保守高峰价**（config 注释说明），真账以平台为准。
4. **真实人工审核是程序化模拟**（decision 由测试构造），交互式审核界面不在本阶段范围。
5. `tmp_projects/`、`real_tmp/`、`tmp_logs/` 为测试产物，保留作验收证据；`v2/` 整体未提交 git，不会污染 V0.1。

## 27. 与架构报告是否存在偏差

**无偏差。** 实现与阶段 0-5 架构报告一致：
- 依赖方向 `core → (llm|memory|context) → workflow → review → agents` 成立，`agents` 是最顶层、只向上依赖；
- Agent 不读文件 / 不找 key / 不调 requests / 不生成文件名 / 不改 approved memory / 不决定 Workflow 下一步，全部按 AGENTS.md 边界实现；
- Casting 作为真正 Step 接 WorkflowEngine，Review 三态与 Artifact 版本化复用了阶段 4 的既有机制，无新增反向依赖。

## 28. 下一阶段建议

1. **接入下一个业务 Agent**（Scene Planning 等）时复用本阶段建立的四件套模板：`needs 声明 → CastingAgent 式业务核心 + Step 壳 → CastingValidator 式确定性校验 → 独立 run_real 脚本做最小真实调用`；
2. 把"candidate → approved"的人工审核真正做成**交互式**（当前是程序化 decision），让 Review 三态直接面对真人操作；
3. 把真实调用的 key 管理做成可插拔（环境变量已支持，可考虑 .env），避免换机后重复排查；
4. 后续阶段真实调用可复用本阶段 3 次的量级作为"每次阶段最小烧钱基线"。

---

**硬性验收指标对照**（开工指令二十二）：
- [x] V0.1 modified = NO
- [x] 阶段 1 回归通过（19/19）
- [x] 阶段 2 回归通过（mock 复验）
- [x] 阶段 3 回归通过（38/38）
- [x] 阶段 4 回归通过（25/25）
- [x] 阶段 5 静态检查通过（复核）
- [x] 阶段 6 新测试全部通过（24/24 + 真实 5/5）
- [x] Mock 测试全部通过（24/24，0 真实调用）
- [x] 至少 1 次真实 Casting LLM 调用（3 次）
- [x] 真实调用经过 LLMClient（T23 request_id/token/cost）
- [x] 真实调用产生 token log（llm_calls.jsonl，0 处 key、无 prompt 正文）
- [x] 真实输出经过 Validator（T21 0 错误）
- [x] 真实输出经过 Artifact（T25 casting.json 落盘）
- [x] 真实输出进入 Review（T25 APPROVED，review 文件落盘）
- [x] 旧 Artifact 不被覆盖（T08 双版本并存）
- [x] 跨集已有角色不会重复创建（T10/T12 mock + T22 真实）
- [x] candidate memory 不会自动进入 approved memory（T13）

**本阶段真实 LLM 调用共 3 次，累计 2099 tokens，成本约 ¥0.01。**
