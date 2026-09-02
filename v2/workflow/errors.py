# -*- coding: utf-8 -*-
"""
V0.2 阶段 3 —— Workflow 错误体系（v2/workflow/errors.py）

作用：给 Workflow 层一套明确的错误类型，绝不允许上层业务"抓到 Exception 就 pass"。

层级（对应开工指令 §十四）：
    WorkflowError     所有 Workflow 错误的基类
    ├── StepError     Step.run() 抛出的业务错误（默认可重试）
    ├── ArtifactError Artifact 读写 / 版本化失败（不可重试——重试也写不进去）
    └── RetryError    重试机制自身的错误（不可重试）

关键设计：每个错误带 retryable 标记（与 LLM 层 LLMError 家族同思路）。
    - retryable=True  ：这次失败"值得再试一次"（如模型产出不合格、临时故障）。
    - retryable=False ：再试也没用（如参数写死错、文件写不进去），直接 FAILED。
"""
from __future__ import annotations

from typing import Optional


class WorkflowError(Exception):
    """所有 Workflow 错误的基类。默认不可重试。"""

    retryable: bool = False

    def __init__(self, message: str, *, retryable: Optional[bool] = None):
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class StepError(WorkflowError):
    """Step.run() 抛出的业务错误（例如：模型产出格式不对、依赖数据缺失）。

    默认可重试：因为这类错误往往重新跑一次就好了（尤其 LLM 产出的随机性）。
    子类或实例化时可显式 retryable=False 表示"重试无意义"。
    """

    retryable = True


class ArtifactError(WorkflowError):
    """Artifact 读写 / 序列化 / 版本化失败。不可重试。"""

    retryable = False


class RetryError(WorkflowError):
    """重试机制自身的错误（例如：尝试重试一个声明为不可重试的 Step）。不可重试。"""

    retryable = False


__all__ = ["WorkflowError", "StepError", "ArtifactError", "RetryError"]
