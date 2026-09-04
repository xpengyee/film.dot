# -*- coding: utf-8 -*-
"""
V0.2 阶段 8 —— Agent / Step 公共骨架（v2/agents/base.py）

作用（开工指令 7 §十六）：让所有业务 Agent 共享统一的基础接口：

        Agent
          ↓ input Context（ContextAssembler 按 needs 组装）
          ↓ LLM / Provider（LLMClient，统一错误/重试/token 日志）
          ↓ structured output（json 解析 + 确定性规范化）
          ↓ Validator（纯规则，不调 LLM）
          ↓ Artifact（Workflow 版本化落盘）
          ↓ Review（人工审核三态）

现在接入骨架的：CastingAgent / CastingStep（阶段 6）、
SceneGenerationAgent / SceneGenerationStep（阶段 7）。
未来 StoryboardAgent / ImageAgent / VideoAgent 直接继承即可，不需要各自再造
prompt 读取 / JSON 解析 / chat 调用 / Step 接线这一套。

LLMAgent 只负责"LLM 业务核心"的公共部分：
    - name / needs 类属性（身份 + 声明式上下文需求清单）
    - __init__ 收 llm_client、按 v2/prompts/v0.2/<name>.txt 定位 prompt
    - system_prompt() 读 prompt 文件（缺失明确报 StepError，可重试）
    - _parse_json() 剥 ```json 围栏 + json.loads（失败抛 StepError，可重试）
    - chat_json()   组装 system+user 消息 -> LLMClient.chat -> 解析成 dict

LLMStep 只负责"Workflow Step 壳"的公共部分：
    - step_id / name / needs / needs_review 类属性
    - __init__ 注入 agent（业务核心），其余交给 workflow.Step 基类
    - assembled_context() 从 WorkflowContext.inputs 取调度层放好的 AssembledContext
    - make_output()       构造 StepOutput（data + artifact_name + metadata）

边界（AGENTS.md / DEVELOPMENT.md，与原有 Agent 完全一致）：
    不自己找 key / 不自己调 requests / 不读磁盘 / 不生成文件名 /
    不改 Memory / 不决定 Workflow 下一步。输入只来自 AssembledContext，
    LLM 只走 LLMClient，产出只交 StepOutput。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import V2_ROOT
from workflow.errors import StepError
from workflow.models import StepOutput
from workflow.step import Step
# 注意：这里不 import TextProvider 符号——Stage8 冻结测试要求 agents/base.py
# 不出现 llm.* 导入；且本文件顶部有 `from __future__ import annotations`，
# 字符串注解在运行时不会求值，因此 `"TextProvider"` 直接作为类型标注即可。


class LLMAgent:
    """LLM 业务 Agent 公共骨架：身份、prompt 读取、JSON 解析、chat 调用。

    子类只需：
        1. 覆盖类属性 name / needs；
        2. 在 __init__ 里传 prompt_name（= prompt 文件名，如 "casting"），
           然后声明自己的校验器 / 业务参数；
        3. 实现 run(context) -> 结构化结果，内部用 chat_json() 拿 LLM dict，
           再做自己的确定性规范化。
    """

    name: str = ""
    needs: List[str] = []          # 声明式上下文需求清单（交给 ContextAssembler 照单取货）

    def __init__(
        self,
        llm_client: Optional["TextProvider"] = None,
        *,
        prompt_name: str = "",
        prompt_path: Optional[Path] = None,
    ):
        """
        :param llm_client:   TextProvider（抽象文本 Provider，Stage 9 起统一形状）。
                             传入 LLMClient 也完全兼容——LLMClient 本身就是
                             "OpenAI 兼容文本执行器"，天然满足 TextProvider 的
                             chat(messages, step=, agent=) -> {text, usage} 形状。
                             允许为 None（仅初始化 / 探 needs 用，不实际调 LLM）。
        :param prompt_name:  V0.2 Prompt 文件名（v2/prompts/v0.2/<prompt_name>.txt）
        :param prompt_path:  显式 prompt 路径；给了就优先用（覆盖 prompt_name）
        """
        self.llm_client = llm_client
        if prompt_path is None:
            if not prompt_name:
                raise ValueError(
                    f"{type(self).__name__} 必须提供 prompt_name 或 prompt_path"
                )
            prompt_path = V2_ROOT / "prompts" / "v0.2" / f"{prompt_name}.txt"
        self.prompt_path = Path(prompt_path)

    # ---------------- Prompt ----------------
    def system_prompt(self) -> str:
        """读取系统提示词文件（缺失时报 StepError，Workflow 可重试）。"""
        try:
            return self.prompt_path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 防御：prompt 缺失应明确报错
            raise StepError(
                f"读取 {type(self).__name__} Prompt 失败 {self.prompt_path}: {exc}"
            ) from exc

    # ---------------- JSON 解析 ----------------
    def _parse_json(self, text: str) -> Any:
        """把 LLM 输出解析成 JSON。

        最小容错：LLM 偶尔用 ```json ... ``` 包裹输出，剥掉代码围栏再 json.loads。
        解析失败抛 StepError（Workflow 按可重试处理）。
        """
        text = (text or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise StepError(f"{self.name} LLM 输出不是合法 JSON: {exc}") from exc

    # ---------------- chat ----------------
    def chat_json(self, *, step: str, user_payload: str) -> dict:
        """标准 LLM 调用：system(prompt 文件) + user(payload) -> LLMClient -> JSON dict。

        产出必须是 dict；不是 dict（数组/字符串/null）抛 StepError（可重试）。
        """
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": user_payload},
        ]
        response = self.llm_client.chat(messages, step=step, agent=self.name)
        raw = self._parse_json(response.text)
        if not isinstance(raw, dict):
            raise StepError(f"{self.name} LLM 输出不是 JSON 对象: {type(raw).__name__}")
        return raw


class LLMStep(Step):
    """把 LLM 业务 Agent 接入 Workflow 引擎的 Step 公共骨架。

    子类只需：
        1. 覆盖类属性 step_id / name / needs / needs_review；
        2. 在 __init__ 里注入业务 Agent（交给本类接线到 workflow.Step）；
        3. 实现 run()：用 self.assembled_context(context) 取上下文 ->
           agent.run() -> self.make_output(...) 返回 StepOutput；
        4. 实现 validate()：调 self.agent 的校验器，错误转字符串列表。
    """

    step_id: str = ""
    name: str = ""
    needs_review: bool = True

    def __init__(
        self,
        agent=None,
        *,
        step_id: Optional[str] = None,
        name: Optional[str] = None,
        needs: Optional[List[str]] = None,
        needs_review: Optional[bool] = None,
    ):
        super().__init__(
            step_id=step_id or self.step_id,
            name=name or self.name,
            needs=list(needs) if needs is not None else list(self.needs),
            needs_review=self.needs_review if needs_review is None else bool(needs_review),
        )
        if agent is None:
            raise ValueError(f"{type(self).__name__} 必须注入业务 Agent")
        self.agent = agent

    # ---------------- 公共接线 ----------------
    def assembled_context(self, context):
        """从 WorkflowContext.inputs 取调度层放好的 AssembledContext。

        调度层必须先调用 ContextAssembler.assemble() 并把结果放进
        WorkflowContext.inputs['assembled_context']（阶段 4 约定）。
        """
        assembled = context.inputs.get("assembled_context")
        if assembled is None:
            raise StepError(
                "调度层必须先调用 ContextAssembler.assemble()，并把结果放进 "
                "WorkflowContext.inputs['assembled_context']"
            )
        return assembled

    def make_output(self, data: Any, *, artifact_name: str,
                    metadata: Optional[Dict[str, Any]] = None) -> StepOutput:
        """构造 StepOutput（引擎负责把 data 写成版本化 artifact）。"""
        return StepOutput(
            data=data,
            artifact_name=artifact_name,
            metadata=dict(metadata or {}),
        )


__all__ = ["LLMAgent", "LLMStep"]
