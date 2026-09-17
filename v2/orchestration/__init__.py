# -*- coding: utf-8 -*-
"""
V0.2 Stage 12-B —— 编排层（v2/orchestration/）

架构位置（开工指令12 §4.3，★新增最顶层）：

    core → (llm | memory | context) → workflow → review → agents → **orchestration**

依赖铁律：orchestration 可以 import 下面所有层；
          **下面任何层不得 import orchestration**（禁止反向/循环）。

模块：
    preflight.py   `preflight_check` —— 调度前置校验门（纯函数，5 条规则，不抛异常）
    state.py       `OrchestrationState` / `OrchestrationError` / 原子写
    pipeline.py    `ProjectOrchestrator` —— 两段式多场景编排 + 集级 casting +
                   视觉资产齐备 + 三种 review 策略 + resume + run_report
    image_stage.py ★Stage 13：图像段（compose → 真实出图 → 落盘 → manifest →
                   记账 → Review → ShotAsset 挂回 → Storyboard 新版本）

一句话：**把四个已封存 Agent 串成一条端到端业务链**，
让「一个项目的一集（N 个场景）」从 0 走到"分镜齐备 + 视觉资产齐备"，
并在 Stage 13 之后继续走到"每 Shot 一张真实生成的画面"。
"""
from orchestration.preflight import preflight_check, format_gaps
from orchestration.state import OrchestrationError, OrchestrationState, atomic_write_json
from orchestration.image_stage import (
    ImageStage,
    resolve_image_bytes,
    guess_extension,
    shot_ref_of,
    GENERATED_DIR_NAME,
    MANIFEST_FILENAME,
    LEDGER_FILENAME,
    DEFAULT_IMAGE_SIZE,
    DEFAULT_MAX_IMAGES,
)
from orchestration.pipeline import ProjectOrchestrator

__all__ = [
    "ProjectOrchestrator",
    "OrchestrationError",
    "OrchestrationState",
    "preflight_check",
    "format_gaps",
    "atomic_write_json",
    # ---- Stage 13：图像段 ----
    "ImageStage",
    "resolve_image_bytes",
    "guess_extension",
    "shot_ref_of",
    "GENERATED_DIR_NAME",
    "MANIFEST_FILENAME",
    "LEDGER_FILENAME",
    "DEFAULT_IMAGE_SIZE",
    "DEFAULT_MAX_IMAGES",
]
