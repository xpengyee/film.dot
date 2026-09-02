# FilmAgent V0.2 —— Agent 接入规范

> 阶段 5 不实现 Agent，但必须先定义 Agent 如何接入，避免阶段 6+ 重新失控。

## 1. 一个 Agent 的完整生命周期

```text
Agent
 ↓
接收 Context（由 ContextAssembler 按 needs 组装，Step.run(context) 传入）
 ↓
执行任务（只做"一个明确的业务任务"）
 ↓
返回结构化结果（StepOutput(data=..., artifact_name=...)）
 ↓
Workflow（引擎决定继续 / 停止 / 重试）
 ↓
Artifact（引擎写盘，版本化不覆盖）
 ↓
Review（声明 needs_review 时，人工审核）
```

## 2. 如何实现一个 Agent

Agent 实现为 `workflow.step.Step` 的子类：

```python
from workflow import Step, StepOutput

class CastingStep(Step):
    # 声明本 Agent 需要哪些信息（ContextAssembler 照单取货）
    needs = ["approved_facts", "characters", "world"]

    def run(self, context):
        # 从 context 读取已组装好的信息，不自己去磁盘翻文件
        facts = context.item("approved_facts")   # 未来装配后提供的组装结果
        # ... 完成业务任务 ...
        return StepOutput(data={...}, artifact_name="casting")

    def validate(self, data, context):
        errors = []
        # ... 检查产出是否合格 ...
        return errors
```

> 当前 `WorkflowContext` 提供 `get_output("上游step")`（步骤间传递）；
> `ContextAssembler.assemble()` 产出 `AssembledContext`（按 needs 组装）。
> 阶段 6+ 接入真实 Agent 时，由调度层把两者装配后以统一 Context 传入 `run()`。

## 3. Agent 必须做的事

- 只接收 `context`，不自己读 `Memory / Assets / Artifacts / Project` 的 JSON。
- 返回 `StepOutput`（含 `artifact_name` 基础名），不自己拼版本文件名。
- 需要 LLM 时用 `LLMClient`（统一错误 / 重试 / token 记录）。
- 产出需人工把关时，声明 `needs_review = True`。

## 4. Agent 绝不能做的事

| 禁止 | 说明 |
|---|---|
| 自己找 API key | 由 `config.load_api_key()` 提供，Agent 只走 `LLMClient` |
| 自己调用 requests / openai | 一律走 `LLMClient`（否则 token 账、错误、脱敏全失控） |
| 自己生成文件名 / 版本号 | 交给 `ArtifactStore`，Agent 只给基础名 |
| 自己修改 Memory | 事实经 `add_candidate_fact` 提出，必须人工 approve 才能进 approved |
| 自己决定 Workflow 下一步 | 继续 / 停止 / 重试由 `WorkflowEngine` 决定 |

## 5. Agent 的职责边界

Agent 的职责是：

> **完成一个明确的业务任务**（casting / 场景规划 / 台词生成 / 镜头标注 …）。

不是：

> 读文件 + 调 API + 写文件 + 记日志 + 管状态 全都自己来。
