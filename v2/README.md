# FilmAgent V0.2 —— 基础设施工作区

## 1. V0.2 是什么

V0.2 是 FilmAgent 的**重构版基础设施**，目标是把 V0.1 的"串行脚本流水线"改造成一个
可测试、可扩展、有人工审核的多智能体框架。已推进到 **Stage 9（Provider Architecture）**：
业务 Agent（Casting / SceneGeneration）与 Canonical Schema 已在 Stage 6/7/8 落地，
Provider 层在 Stage 9 落地。本文档是早期基础设施说明，**权威状态以
`v2/docs/PROJECT_STATUS.md` 为准**。

当前 V0.2 提供一条可运行的基础闭环：

```text
Project → Memory / Assets → ContextAssembler → Workflow Step → Artifact → Review → 继续/停止
```

## 2. V0.1 与 V0.2 的关系

- **V0.1**（`FilmAgent/`）是冻结基准，**任何情况下不得修改**。它是一套完整跑通的
  串行流水线（casting → scenes_plan → lines_generate → … → clean_script）。
- **V0.2**（`v2/`）全新重写基础设施。V0.1 的资产与 Prompt 以**只读**方式迁移进
  `v2/assets/` 与 `v2/prompts/`（`diff -r` 逐字节一致）。
- V0.1 数据通过明确的转换函数进入 V0.2 模型（见 DEVELOPMENT.md「兼容层边界」），
  兼容逻辑不允许散落到业务代码。

## 3. 目录结构

```text
v2/
├── config.py          配置中心：路径 / API / 模型 / 价格 / 密钥读取（绝不硬编码 key）
├── core/              基础层（不依赖任何上层）
│   ├── io.py          JSON 读写（保留 V0.1 容错行为）
│   ├── models.py      统一数据模型（Scene/Character/Line/World/Project/WorkflowState…）
│   ├── assets.py      资产注册表（地点/动作/镜头/站位点）
│   ├── validator.py   三个校验器（Enum/Reference/Schema）
│   └── token_log.py   Token/Cost 记录（JSONL，不记 key、不记 prompt 正文）
├── llm/               LLM 调用层（依赖 core）
│   ├── client.py      LLMClient：统一调用 / 错误家族 / 重试 / token 日志
│   ├── router.py      Router：任务 → Provider/模型 的路由（Stage 9 起返回 ProviderRoute）
│   └── providers/     Provider 层（Stage 9）：Text/Vision/Multimodal/Embedding 接口族
│                      + Mock 四件套 + OpenAICompatibleTextProvider + BailianTextProvider
│                      + Registry 工厂（create_provider / get_default_provider）
├── memory/            记忆层：facts(candidate/approved/rejected) / characters / world / history
├── context/           上下文组装层：声明式 needs + provider 注册表
├── workflow/          工作流引擎：step / artifact / engine
├── review/            人工审核层：submit / approve / approve_with_edit / reject
├── agents/            业务 Agent（Stage 6/7/8 起）：casting / scene_generation / base 骨架
├── assets/locations/  V0.1 迁移的资产（只读）
├── prompts/v0.1/      V0.1 迁移的 Prompt（只读）
├── projects/          运行时项目数据（已被 .gitignore 忽略）
└── logs/              运行日志 + 各阶段测试与验收证据
```

模块依赖方向（只允许向上依赖，禁止反向）：

```text
core → (llm | memory | context) → workflow → review → agents
```

## 4. 如何安装依赖

使用 Python 3.9+。核心依赖仅一个：`openai`（真实 LLM 调用时用，其余纯标准库）。

```bash
conda activate filmagent      # 或任意 Python 3.9+ 环境
pip install openai -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 5. 如何配置 LLM

配置集中在 `config.py`。API key **绝不硬编码**，读取顺序：

1. 环境变量 `DEEPSEEK_API_KEY`（优先）
2. `FilmAgent/api_key.txt`（V0.1 已存在的密钥文件，只读）

```bash
# 方式一：环境变量
export DEEPSEEK_API_KEY="sk-xxxx"
# 方式二：把密钥放进 FilmAgent/api_key.txt（该文件已在 .gitignore 中）
```

任何代码、日志、报告只显示脱敏后的 key（如 `sk-****abcd`，`config.mask_api_key`）。

## 6. 如何运行测试

全部测试在 `v2/logs/` 下按阶段组织。日常回归只需跑阶段 4 的套件
（它内部会复验阶段 1 / 阶段 2(mock) / 阶段 3）：

```bash
cd v2
python logs/stage4_memory_context_review_test/run_tests.py   # 25/25 + 内部回归
```

分阶段单独运行：

```bash
python logs/stage1_validation_test/run_tests.py    # 19/19，纯本地
python logs/stage3_workflow_test/run_tests.py      # 38/38，纯本地
python logs/stage4_memory_context_review_test/run_tests.py
```

> 注意：`logs/stage2_llm_test/run_tests.py` 的完整套件含**真实 LLM 调用**（计费）。
> 日常回归一律用阶段 4 内的 **mock 复验**（R2），0 次真实调用。
> 真实调用能力已在阶段 2 验收时验证，无需重复计费。

每个测试目录的产物分类：

| 内容 | 示例 | 是否进版本库 |
|---|---|---|
| 验收证据 | `stage*_report.md` / `stage*_report.json` | 保留 |
| 测试源码 | `run_tests.py` | 保留 |
| 运行日志 | `stage*_console.txt` | `.gitignore` 忽略 |
| 临时输出 | `tmp_projects/`、`project_*/`、`runs/`、`test_output_*` | `.gitignore` 忽略 |
| LLM 调用日志 | `logs/llm_calls.jsonl` | `.gitignore` 忽略 |

## 7. 如何创建 Project

目前"项目"= 一个 `project_id`（字符串）。创建项目即创建 Memory 目录：

```python
from memory.store import MemoryStore

mem = MemoryStore("my_project")          # 自动建 v2/projects/my_project/memory/
mem.set_world({"city": "Shanghai"})      # 写入世界观
```

`core.models.Project` 是数据模型（预留），业务层（阶段 6+）接入时再使用。

## 8. Memory 是什么

项目级记忆，落在 `v2/projects/<project_id>/memory/`：

- `facts.json`      事实，三态：`candidate → (人工 approve/reject) → approved/rejected`
- `characters.json` 角色信息
- `world.json`      世界观 / 设定
- `history.json`    历史产出引用（如上一集 artifact）

**核心规则**：candidate 事实绝不自动成为 approved；必须经人工
`approve_fact / reject_fact`。`get_approved_facts()` 只返回已确认事实。
每次写操作 `revision` +1，供 Context 标注"第几版"（可追踪）。

## 9. ContextAssembler 是什么

"资料管理员"，纯代码组件（不调 LLM）。按 Step 声明的 `needs` 照单取货：

```python
class LinesGenerateStep(Step):
    needs = ["approved_facts", "characters", "world"]
```

`ContextAssembler.assemble(step, context)` 只装 Step 需要的内容，每条带来源信息
（source / source_type / source_id / version）。声明了未注册的 needs 会明确报错。

当前 needs 注册表（11 个）：`project_info / approved_facts / candidate_facts /
characters / world / history / previous_artifacts / workflow_state / locations /
actions / shots`。

## 10. Workflow 是什么

线性执行引擎：按顺序执行一组 Step，负责执行 / 记录 / 错误处理 / 状态 / 重试 / 轨迹保存。

```python
from workflow import WorkflowEngine, Step, StepOutput

class MyStep(Step):
    def run(self, context):
        return StepOutput(data={...}, artifact_name="scenes_5")

engine = WorkflowEngine([MyStep(step_id="scenes")], artifacts_dir=...)
run = engine.run(project_id="p", workflow_id="wf")
```

状态机：`PENDING / RUNNING / SUCCESS / FAILED / RETRYING / SKIPPED`
+ 阶段 4 扩展：`NEEDS_REVIEW / APPROVED / APPROVED_WITH_EDIT / REJECTED`。
`is_success` 覆盖 SUCCESS / APPROVED / APPROVED_WITH_EDIT。

## 11. Review 是什么

人工审核节点。Step 声明 `needs_review=True` 成功后进入审核：

- `approved`            原始产出直接通过，run 继续
- `approved_with_edit`  人工修改后通过，**写编辑后的新版本**（原始文件不覆盖），run 继续
- `rejected`            人工拒绝，run 失败即停

审核记录持久化到 `v2/projects/<project_id>/reviews/`，能回答
"原始版本是什么 / 谁审核 / 何时 / 改成了什么 / 最终采用哪一版"。

## 12. Artifact 版本机制

同一个 Step 的产出，版本号由磁盘已有版本数 +1 决定，**绝不覆盖**：

```text
第 1 次 -> scenes_5.json
第 2 次 -> scenes_5_v2.json
第 3 次 -> scenes_5_v3.json
```

单次 run 内重试、跨 run 重跑都递增版本。历史版本全程可查。

## 13. 如何查看 Token/Cost

每次真实 LLM 调用追加一行到 `v2/logs/llm_calls.jsonl`：

```bash
tail -n 5 v2/logs/llm_calls.jsonl
```

每条含 request_id / model / input_tokens / output_tokens / estimated_cost /
duration_ms / success 等字段。日志**不记 key、不记 prompt 正文**（只记 sha256 哈希）。

成本估算按 `config.PRICING`（DeepSeek 高峰价保守估计）。`core.token_log.estimate_cost`
返回 `None` 表示无法估算（绝不瞎编）。

## 14. 当前已经完成哪些阶段

已完成 **Stage 0–9**，每阶段测试套件与验收报告保留在 `v2/logs/stageN_*/`。
**权威阶段状态以 `v2/docs/PROJECT_STATUS.md` 为准**。概要：

| 阶段 | 内容 | 测试 |
|---|---|---|
| 0 | 基础目录 + V0.1 资产/Prompt 只读迁移 | IO 验证 4 组 |
| 1 | 数据模型 + Validator + Assets | 19/19 |
| 2 | LLMClient + Token/Cost | 14/14（含真实调用验证，已封存） |
| 3 | Workflow + Artifact | 38/38 |
| 4 | Memory + Context + Review | 25/25 |
| 5 | 清理 + 固化 + 文档 | 全回归 |
| 6 | 第一个业务 Agent：CastingAgent | 24/24 |
| 7 | 第二个业务 Agent：Scene / Script Generation | 29/29 |
| 8 | Canonical Schema（Shot/Beat/Storyboard/Asset + Adapter） | 27/27 |
| 9 | Multi-Modal Provider Architecture（`v2/llm/providers/`） | 26/26 |

## 15. 当前尚未完成哪些能力

- 更多业务 Agent：dialogue / shot / storyboard / memory_writer 等未实现
  （casting / scene_generation 已实现）
- **Image / Video / Audio 真实生成**：未做（Provider 仅预留接口，尚未接真实生成）
- **本地模型**（Ollama / vLLM / 权重）：未下载 / 未部署 / 未接入（仅 Config 档预留）
- **`structured=` 真实后端支持**：未验证（contract 已就位；需人工批准的最小真实冒烟）
- **summary 摘要层**：只预留概念，LLM 生成摘要未实现
- **真实异步人工审核流程**：用同步 handler 演示闭环，生产"暂停等人工"流程待接
- **UI / 数据库 / 生产部署**：均未做

## 16. 下一阶段是什么

```text
Stage 10：NOT STARTED（等待下一份正式开工指令；进入前先读 v2/docs/HANDOFF.md）
```

路线图方向：

```text
StoryboardAgent / 接入下一个业务 Agent → Project 生命周期 → 第一条完整业务链
→ 第一集真实生成 → V0.2.0-alpha → 稳定化 → V0.2.0
```

`V0.2.0-alpha` 是重要版本留存点，代表"基础设施完整 + 至少一条真实业务链跑通 +
测试通过 + V0.1 完全冻结"。
