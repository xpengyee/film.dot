# -*- coding: utf-8 -*-
"""
V0.2 Stage 12-B —— 项目生命周期编排层（v2/orchestration/pipeline.py）

一句话目标（开工指令12 §4.1）：

    把 Casting / SceneGeneration / Storyboard / VisualAsset 四个**已封存 Agent**
    串成一条端到端流水线，使「一个项目的一集（N 个场景）」从 0 自动走到
    "分镜齐备 + 视觉资产齐备"，并留下可恢复的运行状态与完整运行报告。

架构位置（§4.3，★新增最顶层，只允许向下依赖）：

    orchestration（本文件）  ← 最顶层，可以 import 下面所有层
          ↓
    agents → review / workflow → core / llm / memory / context
    ★ 下面任何层**不得** import orchestration（禁止反向/循环）。

核心设计（三条硬约束，全部来自总控裁决，见 stage12_plan §12）：

1. **两段式**（裁决 3 / D-2）：`scene_gen_{sid}` 与 `storyboard_{sid}` **各一次 run**。
   为什么不能"同一 run 装两个 step"：`WorkflowEngine.run()` 的 `inputs` 是**整次 run
   共享的一个 dict**（A 会话实测 P3：`StoryboardStep` 只读 `inputs["scene"]`，
   **完全忽略上游 Step 产出**）→ 同 run 装两 step 必然串场或 `StepError`。
   两段式**零改引擎**、语义更干净（每段 run 恰好一个 step）、resume 天然可用。
   ★ storyboard 段必须**显式** `inputs["scene"] = Scene`（不得依赖上游产出）。

2. **集级 Casting 通过后写 Memory**（裁决 1 / A-1）：`set_characters`（沿用同一 run 的
   Casting Artifact）+ `set_world`。不写 → Context `characters` 为空 → LLM 编造角色名
   → Scene 层不回锚 lines → `UNKNOWN_CHARACTER` → 3 次重试耗尽 → 永久 FAILED。
   ★ **严禁 `approve_fact`**（事实批准是"人"的动作，编排层不得代批）。

3. **视觉资产只支持 approved / rejected**（裁决 2 / A-2）：遇 `approved_with_edit`
   → 明确抛 `OrchestrationError`（`apply_review_decision` 会直接报错，不隐式解释）。

resume 语义（裁决 6 / stage12_plan §2.6）：对已 approved 的单元**直接跳过、不调用引擎**
（`state.json` 是唯一事实源，不再存第二份 `WorkflowRun`）；跳过前校验磁盘 artifact
仍存在（缺失 → 视为 pending 重跑）。

诚实性（AC13）：`auto_approve` / `scripted` 都是**程序化模拟**，
`run_report["review_mode"]` 必须显式标注 "programmatic, NOT human review"；
**绝不**把它的产物描述成"已人工审核通过"。

Stage 13 增量（图像段，§7.4）：
    在 `visual_assets` 段之后新增 `image` 段（实现全部在 `orchestration/image_stage.py`）：
        每个 Shot → VisualComposer.compose() → 真实 generate_image →
        落盘 generated_images/<GEN_ID>.<ext> → manifest.json + 图像记账 →
        Review 三态 → approved 才把 ShotAsset(kind="image") 挂回 Shot →
        Storyboard **新版本**（原文件绝不覆盖）。
    三条护栏：**默认不出图**（`generate_images` 缺省 False）/ **预算硬上限**
    （`min(max_images, MAX_REAL_IMAGE_REQUESTS)`，绝不发出第 N+1 次）/
    **绝不伪造出图**（无 key、未配置、预算耗尽一律如实记 failed/rejected）。
    同时把 `run_spec["style_spec"]` 纳入 `visual_assets` 段（STYLE 不是 Shot 能引用的
    资产，必须显式提供；未提供时行为与 Stage 12 完全一致）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config import V2_ROOT
from core.assets import AssetRegistry
from core.io import read_json
from core.models import Episode, Project, Scene
from core.shot import Storyboard
from core.visual_compose import VisualComposer
from core.visual_registry import VisualAssetRegistry, VisualRegistryError
from memory.store import MemoryStore
from context.assembler import ContextAssembler
from workflow.artifact import ArtifactStore
from workflow.engine import WorkflowEngine
from workflow.models import (
    ReviewDecision,
    StepStatus,
    WorkflowContext,
    WorkflowRun,
    new_run_id,
    now_iso,
)

from agents.casting import CastingAgent, CastingStep
from agents.scene_generation import SceneGenerationAgent, SceneGenerationStep
from agents.storyboard import StoryboardAgent, StoryboardStep
from agents.visual_asset import VisualAssetAgent, VisualAssetStep

from orchestration.image_stage import ImageStage
from orchestration.preflight import preflight_check, format_gaps
from orchestration.state import (
    OrchestrationError,
    OrchestrationState,
    atomic_write_json,
)


# ---------------------------------------------------------------------------
# 审核策略（§7.4：必须显式声明是程序化模拟，不是真人审核）
# ---------------------------------------------------------------------------
REVIEW_POLICY_AUTO_APPROVE = "auto_approve"
REVIEW_POLICY_SCRIPTED = "scripted"
REVIEW_POLICY_HUMAN_STUB = "human_stub"

#: `human_stub` 的唯一文案（两个强制点共用，保证口径一致）
HUMAN_STUB_MESSAGE = (
    "需人工介入：等待外部注入审核决策（review_policy='human_stub'）；"
    "编排层不自动批准、不伪造人工审核"
)

_PROGRAMMATIC_NOTE = "programmatic, NOT human review"

#: 视觉资产的审核决策白名单（裁决 2）：只支持 approved / rejected
_VISUAL_ALLOWED_DECISIONS = (ReviewDecision.APPROVED, ReviewDecision.REJECTED)


def _review_mode_label(policy: str) -> str:
    """run_report 里的 review_mode 文案（★必须含 "programmatic, NOT human review"）。"""
    if policy == REVIEW_POLICY_AUTO_APPROVE:
        return f"auto_approve ({_PROGRAMMATIC_NOTE})"
    if policy.startswith(REVIEW_POLICY_SCRIPTED):
        # ★ 归一化：scripted:<file> 只标 "scripted"，绝不把本地决策文件路径
        #   泄进 run_report（报告是可交付产物，路径属于运行环境细节）。
        return f"scripted ({_PROGRAMMATIC_NOTE})"
    if policy == REVIEW_POLICY_HUMAN_STUB:
        return "human_stub (stops for external decision; NOT human review performed)"
    return f"{policy} ({_PROGRAMMATIC_NOTE})"


class _Decision:
    """最小鸭子类型审核决策。

    引擎只看 `.decision`（`ReviewDecision` 三态之一）/ `.review_id`；
    `approved_with_edit` 时另需 `.edited_content`（或 `.edited_artifact`）。
    """

    def __init__(self, decision: str, review_id: str,
                 edited_content: Any = None, comment: str = ""):
        self.decision = str(decision)
        self.review_id = str(review_id)
        self.edited_content = edited_content
        self.comment = str(comment or "")


def _slug(text: str) -> str:
    """把项目标题折成可作目录名的 project_id（保留中文 / 字母数字下划线横线）。"""
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "_", str(text or "").strip())
    s = s.strip("_")
    return s[:64] or "project"


def _names_from_character_list(items: Any) -> List[str]:
    """[{"name": …}] / ["A"] → 去重保序的名字列表。"""
    out: List[str] = []
    for c in (items or []):
        if isinstance(c, dict):
            n = str(c.get("name", "") or "").strip()
        else:
            n = str(c or "").strip()
        if n and n not in out:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# ProjectOrchestrator
# ---------------------------------------------------------------------------
class ProjectOrchestrator:
    """把一个项目的一集（N 个场景）从 0 编排到"分镜齐备 + 视觉资产齐备"。

    :param project:        `core.models.Project`（V0.1 兼容模型，**只消费不扩展**）。
    :param provider:       文本 Provider（缺省走 Router 默认档：环境开关
                           `FILMAGENT_PROVIDER`，缺省 mock → 0 API）。测试全注入 Mock。
    :param projects_root:  ★必须可注入（D-8）：默认 `V2_ROOT/"projects"`；
                           测试注入 `tmp_projects/`，绝不写真实项目目录。
    :param assets:         AssetRegistry（缺省从 `v2/assets/locations` 读）。
    :param review_policy:  "auto_approve" | "scripted:<file>" | "human_stub"。
    :param max_attempts:   每段 run 的 Step 最大尝试次数（引擎语义）。
    :param project_id:     项目标识（缺省由 project.title 折成 slug）。

    Stage 13 新增（全部可选，缺省行为与 Stage 12 **完全一致**）：
    :param image_provider: 图像 Provider（缺省走环境开关默认档，缺省 mock → 0 API）。
    :param image_log_dir:  图像账目录（缺省 `v2/logs/stage13_image/`；测试注入 tmp）。
    :param image_fetch:    URL 形态图片引用的下载器（缺省 stdlib urllib；
                           测试注入假下载器 → 0 网络）。
    """

    def __init__(
        self,
        project: Project,
        *,
        provider: Any = None,
        projects_root: Optional[Path] = None,
        assets: Optional[AssetRegistry] = None,
        review_policy: str = REVIEW_POLICY_AUTO_APPROVE,
        max_attempts: int = 3,
        project_id: Optional[str] = None,
        image_provider: Any = None,
        image_log_dir: Optional[Path] = None,
        image_fetch: Optional[Callable[[str], bytes]] = None,
    ):
        if not isinstance(project, Project):
            raise OrchestrationError(
                f"ProjectOrchestrator 需要 core.models.Project，实际 {type(project).__name__}"
            )
        self.project = project
        self.project_id = str(project_id or _slug(getattr(project, "title", "")))
        self.projects_root = Path(projects_root) if projects_root else V2_ROOT / "projects"

        # ---- 目录布局（§2.4） ----
        self.project_dir = self.projects_root / self.project_id
        self.artifacts_dir = self.project_dir / "artifacts"
        self.orchestration_dir = self.project_dir / "orchestration"
        self.run_log_dir = self.orchestration_dir / "runs"
        self.state_path = self.orchestration_dir / "state.json"

        # ---- 组件（全部 0 改复用） ----
        self.assets = assets if assets is not None else AssetRegistry.load(
            V2_ROOT / "assets" / "locations")
        self.provider = provider if provider is not None else self._default_provider()
        self.review_policy = str(review_policy or REVIEW_POLICY_AUTO_APPROVE)
        self.max_attempts = int(max_attempts)

        self.memory = MemoryStore(self.project_id, projects_root=self.projects_root)
        self.assembler = ContextAssembler(self.memory, self.assets)
        self.visual_registry = VisualAssetRegistry(self.project_dir)
        self.store = ArtifactStore(self.artifacts_dir)
        self.state = OrchestrationState.load(
            self.project_id, self.state_path, artifacts_dir=self.artifacts_dir)

        # ---- Stage 13：图像段依赖（全部可选，缺省行为与 Stage 12 一致） ----
        self.image_provider = (image_provider if image_provider is not None
                               else self._default_image_provider())
        self.image_log_dir = Path(image_log_dir) if image_log_dir else None
        self.image_fetch = image_fetch
        #: 本次 run 的项目级风格规范（run_spec["style_spec"]；STYLE 资产由它建档）
        self._style_spec: dict = {}

        # 观测位：最近一次 run 的报告（测试 / 报告引用）
        self.last_report: Optional[dict] = None
        self.last_report_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # 构造辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _default_provider():
        """缺省 Provider：走 Router 默认档（缺省 mock → 0 API）。"""
        from llm.router import Router
        return Router(kind="text").get_provider("text")

    @staticmethod
    def _default_image_provider():
        """缺省图像 Provider：走环境开关（缺省 mock → 0 API 0 成本）。

        ★ 与文本档同一纪律：**不静默退回 mock**。若环境开关指向的厂商没有可用的
        图像档（`default_model` 为空 / 无 key），工厂会明确报错，编排层原样透传
        —— 宁可明确阻塞，也绝不伪造出图（铁律 11）。
        """
        from llm.providers.registry import get_default_provider
        return get_default_provider("image")

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, run_spec: Optional[dict] = None) -> dict:
        """编排一集：Casting → [每场景] Scene → Storyboard → 视觉资产 → 齐备判定。

        run_spec（§6.1；全部可选）：
            episode       目标集号（默认 1）
            scene_ids     目标场景号列表（缺省 = 从 Episode.scenes 推 / [1]）
            scene_specs   场景规范（list[dict] 或 {sid: dict}）；缺省由 Episode/world 推导
            scenes        {sid: Scene|dict} —— 显式提供的"已批准 Scene"快捷路径（§6.1(a)）
            review_policy 覆盖构造参数
            max_attempts  覆盖构造参数
            resume        是否复用已有状态跳过已完成部分（默认 True）

            ★ Stage 13（图像段，§6.1）：
            generate_images  是否出图。**缺省 False** —— 安全默认：不出图就不花钱。
            image_shot_refs  只出这些 Shot（"S1_B1_SH2" 形态）；空/缺省 = 全部 Shot。
            image_size       图像尺寸，canonical 记法 "1024*1024"（透传 Provider；
                             厂商差异走 Config 的 size_separator）。
            max_images       本次 run 的图像硬上限（缺省 3）。实际上限取
                             min(max_images, MAX_REAL_IMAGE_REQUESTS)（§7.3-2）。
            style_spec       项目级风格规范（建 STYLE 视觉档案）。★ STYLE 不是 Shot
                             能引用的资产，不显式提供 → 出图时 compose 会明确报错
                             （不硬出图）；不提供时视觉资产计数与 Stage 12 一致。
            style_name       风格资产名（缺省 "default"）。

        :return: run_report（dict；同时落盘 `orchestration/run_<run_id>.json`）。
        :raises OrchestrationError: 入参非法 / human_stub / 视觉资产遇到编辑态决策 /
                                    编排层不变量被破坏。
        """
        spec = dict(run_spec or {})
        policy = str(spec.get("review_policy") or self.review_policy)
        max_attempts = int(spec.get("max_attempts") or self.max_attempts)
        episode = int(spec.get("episode") or 1)
        resume = bool(spec.get("resume", True))
        run_id = str(spec.get("run_id") or new_run_id())
        # ★ Stage 13：项目级风格规范（STYLE 资产建档用；确定性输入，不靠 LLM 编造）
        style_spec = spec.get("style_spec")
        self._style_spec = dict(style_spec) if isinstance(style_spec, dict) else {}

        # ★ human_stub：§7.4「停下等外部注入决策」—— 开工前即短路，
        #   保证不产生任何 artifact、也不做无谓重试（诚实：不伪造人工审核）。
        if policy == REVIEW_POLICY_HUMAN_STUB:
            raise OrchestrationError(HUMAN_STUB_MESSAGE)

        handler = self._build_review_handler(policy)

        scene_ids = self._resolve_scene_ids(spec, episode)

        report: Dict[str, Any] = {
            "project_id": self.project_id,
            "episode": episode,
            "run_id": run_id,
            "started_at": now_iso(),
            "ended_at": "",
            "status": "running",
            "review_mode": _review_mode_label(policy),
            "provider_vendor": str(getattr(self.provider, "vendor", "") or ""),
            "resume": resume,
            "scene_ids": list(scene_ids),
            "stages": {
                "casting": {},
                "scenes": {str(sid): {} for sid in scene_ids},
                "visual_assets": {"registered": [], "pending": []},
                "image": {},
            },
            "completeness": {
                "scenes_total": len(scene_ids), "scenes_done": 0,
                "storyboards_total": len(scene_ids), "storyboards_done": 0,
                "assets_referenced": 0, "assets_approved": 0,
            },
            "warnings": [],
            "errors": [],
        }

        # ---------------- ① 集级 Casting（一次 run） ----------------
        casting_ok = self._stage_casting(
            episode, handler, max_attempts, resume, report)
        if not casting_ok:
            report["status"] = "failed"
            return self._finish(report, run_id)

        # ---------------- ② 每场景：剧本段 + 分镜段（两段式） ----------------
        storyboards: List[Tuple[int, Storyboard]] = []
        scene_specs = self._resolve_scene_specs(spec, episode, scene_ids)
        provided_scenes = spec.get("scenes") or {}

        for sid in scene_ids:
            sb = self._stage_scene(
                episode, sid, scene_specs.get(sid) or {}, provided_scenes.get(sid),
                handler, max_attempts, resume, report)
            if sb is not None:
                storyboards.append((sid, sb))

        # ---------------- ③ 视觉资产段（每实体一次 run） ----------------
        entities = self._collect_entities(sb for _, sb in storyboards)
        # ★ Stage 13：项目级风格档案（可选）。
        #   STYLE 不在 Shot.assets 的可引用词表里（ASSET_KINDS 无 style），无法从
        #   Storyboard 自动收集；但 VisualComposer 出图时必须解析到唯一 approved
        #   STYLE（core/visual_compose.py 三级解析）。因此由 run_spec 显式提供
        #   style_spec 时，把 ("style", name) 并入同一段、走同一套建档/审核机制。
        #   未提供 → 不建档，assets_referenced/approved 计数与 Stage 12 完全一致。
        style_entity = self._style_entity(spec)
        if style_entity is not None:
            entities = list(entities) + [style_entity]
        report["completeness"]["assets_referenced"] = len(entities)
        self._stage_visual_assets(entities, episode, handler, max_attempts, report)

        # ---------------- ④ 图像段（★Stage 13 新增：每 Shot 一张图） ----------------
        report["stages"]["image"] = self._stage_images(
            storyboards, episode, spec, handler, report)

        # ---------------- ⑤ 齐备判定 + 落盘 ----------------
        self._compute_completeness(scene_ids, entities, report)
        report["status"] = self._decide_status(report)
        return self._finish(report, run_id)

    # ------------------------------------------------------------------
    # ① Casting
    # ------------------------------------------------------------------
    def _stage_casting(self, episode: int, handler: Callable, max_attempts: int,
                       resume: bool, report: dict) -> bool:
        wf_id = f"casting_ep{episode}"
        entry = self.state.casting_entry(episode)

        if resume and self.state.should_skip(entry, artifacts_dir=self.artifacts_dir):
            report["stages"]["casting"] = {
                "status": "approved", "artifact": entry.get("artifact", ""),
                "attempts": entry.get("attempts", 0), "run_id": entry.get("run_id", ""),
                "workflow_id": entry.get("workflow_id") or wf_id, "skipped": True,
            }
            report["warnings"].append(
                f"casting: resume 跳过（已 approved，artifact={entry.get('artifact')}）")
            # ★ 即使跳过也要保证 Memory 有角色（R2）：从既有 Casting Artifact 同步
            data = self._read_artifact(entry.get("artifact"))
            if isinstance(data, dict):
                self._apply_casting_to_memory(data)
            return True

        agent = CastingAgent(self.provider, known_characters=self._known_names())
        step = CastingStep(agent)
        ctx = self._assemble(step, wf_id)
        run = self._new_engine([step], handler).run(
            project_id=self.project_id, workflow_id=wf_id,
            inputs={"assembled_context": ctx}, max_attempts=max_attempts)
        res = run.step_result(step.step_id)

        if res is None or not res.is_success:
            status = "failed"
            self.state.record_casting(
                episode, status=status, attempts=len(res.attempts) if res else 0,
                run_id=run.run_id, workflow_id=wf_id)
            report["stages"]["casting"] = {
                "status": status, "artifact": "", "attempts": len(res.attempts) if res else 0,
                "run_id": run.run_id, "workflow_id": wf_id,
            }
            report["errors"].append({
                "stage": "casting", "workflow_id": wf_id, "run_id": run.run_id,
                "error": (res.error if res else "引擎未返回 StepResult"),
            })
            return False

        artifact = res.approved_artifact
        self.state.record_casting(
            episode, status="approved", artifact=artifact.filename,
            attempts=len(res.attempts), run_id=run.run_id, workflow_id=wf_id)
        report["stages"]["casting"] = {
            "status": "approved", "artifact": artifact.filename,
            "attempts": len(res.attempts), "run_id": run.run_id, "workflow_id": wf_id,
            "step_status": res.status,
        }
        data = artifact.read()
        if not isinstance(data, dict):
            raise OrchestrationError(
                f"casting artifact 不是 dict：{type(data).__name__}（{artifact.filename}）")
        self._apply_casting_to_memory(data)
        return True

    # ------------------------------------------------------------------
    # ② 每场景：剧本段 + 分镜段（两段式，裁决 3）
    # ------------------------------------------------------------------
    def _stage_scene(self, episode: int, sid: int, scene_spec: dict, provided_scene: Any,
                     handler: Callable, max_attempts: int, resume: bool,
                     report: dict) -> Optional[Storyboard]:
        slot = report["stages"]["scenes"].setdefault(str(sid), {})
        scene_obj: Optional[Scene] = None

        # ---- 剧本段 ----
        if provided_scene is not None:
            # §6.1(a) 快捷路径：调度层显式提供"已批准的 Scene"
            try:
                scene_obj = (Scene.from_v01(provided_scene, id=sid)
                             if isinstance(provided_scene, dict) else provided_scene)
            except Exception as exc:
                report["errors"].append({
                    "stage": "scene_source", "scene_id": sid,
                    "error": f"run_spec.scenes[{sid}] 无法解析成 Scene：{exc!r}"})
                slot["scene"] = {"status": "failed", "artifact": "", "attempts": 0,
                                 "run_id": "", "workflow_id": f"scene_gen_{sid}"}
                return None
            slot["scene"] = {"status": "approved", "artifact": "", "attempts": 0,
                             "run_id": "", "workflow_id": f"scene_gen_{sid}",
                             "provided": True}
            report["warnings"].append(
                f"scene {sid}: 使用调度层显式提供的 Scene（跳过 SceneGeneration）")
        else:
            wf_a = f"scene_gen_{sid}"
            entry_a = self.state.scene_entry(episode, sid, "scene")
            if resume and self.state.should_skip(entry_a, artifacts_dir=self.artifacts_dir):
                scene_obj = self._load_scene(entry_a.get("artifact"), sid)
                slot["scene"] = {"status": "approved", "artifact": entry_a.get("artifact", ""),
                                 "attempts": entry_a.get("attempts", 0),
                                 "run_id": entry_a.get("run_id", ""),
                                 "workflow_id": entry_a.get("workflow_id") or wf_a,
                                 "skipped": True}
                report["warnings"].append(
                    f"scene {sid}: resume 跳过（已 approved，"
                    f"artifact={entry_a.get('artifact')}）")
            else:
                agent = SceneGenerationAgent(
                    self.provider, scene_id=sid, assets=self.assets,
                    known_characters=self._known_names())
                step = SceneGenerationStep(agent, scene_id=sid)
                ctx = self._assemble(step, wf_a)
                run = self._new_engine([step], handler).run(
                    project_id=self.project_id, workflow_id=wf_a,
                    inputs={"assembled_context": ctx, "scene_spec": dict(scene_spec or {})},
                    max_attempts=max_attempts)
                res = run.step_result(step.step_id)
                if res is None or not res.is_success:
                    self.state.record_scene(
                        episode, sid, "scene", status="failed",
                        attempts=len(res.attempts) if res else 0,
                        run_id=run.run_id, workflow_id=wf_a)
                    slot["scene"] = {"status": "failed", "artifact": "",
                                     "attempts": len(res.attempts) if res else 0,
                                     "run_id": run.run_id, "workflow_id": wf_a}
                    report["errors"].append({
                        "stage": "scene_generation", "scene_id": sid,
                        "workflow_id": wf_a, "run_id": run.run_id,
                        "error": (res.error if res else "引擎未返回 StepResult")})
                    return None
                artifact = res.approved_artifact
                self.state.record_scene(
                    episode, sid, "scene", status="approved",
                    artifact=artifact.filename, attempts=len(res.attempts),
                    run_id=run.run_id, workflow_id=wf_a)
                slot["scene"] = {"status": "approved", "artifact": artifact.filename,
                                 "attempts": len(res.attempts), "run_id": run.run_id,
                                 "workflow_id": wf_a, "step_status": res.status}
                data = artifact.read()
                try:
                    scene_obj = Scene.from_v01(data, id=sid)
                except Exception as exc:
                    report["errors"].append({
                        "stage": "scene_generation", "scene_id": sid,
                        "error": f"落盘 Scene 无法解析：{exc!r}"})
                    return None

        # ---- ★ 前置校验门（§7.2）：先 preflight，再喂 Stage 11 ----
        gaps = preflight_check(scene_obj, self._known_names(), self.assets)
        if gaps:
            report["errors"].append({
                "stage": "preflight", "scene_id": sid,
                "message": "前置校验未通过：Scene 有缺口，已拦在编排层，"
                           "不进入 Storyboard 的重试风暴",
                "gaps": gaps,
            })
            self.state.record_scene(episode, sid, "storyboard", status="pending",
                                    workflow_id=f"storyboard_{sid}")
            slot["storyboard"] = {"status": "pending", "artifact": "", "attempts": 0,
                                  "run_id": "", "workflow_id": f"storyboard_{sid}",
                                  "preflight_gaps": gaps}
            return None

        # ---- 分镜段（显式 inputs["scene"]，不依赖上游 step 产出） ----
        wf_b = f"storyboard_{sid}"
        entry_b = self.state.scene_entry(episode, sid, "storyboard")
        if resume and self.state.should_skip(entry_b, artifacts_dir=self.artifacts_dir):
            sb = self._load_storyboard(entry_b.get("artifact"), sid, report)
            slot["storyboard"] = {"status": "approved", "artifact": entry_b.get("artifact", ""),
                                  "attempts": entry_b.get("attempts", 0),
                                  "run_id": entry_b.get("run_id", ""),
                                  "workflow_id": entry_b.get("workflow_id") or wf_b,
                                  "skipped": True}
            report["warnings"].append(
                f"storyboard {sid}: resume 跳过（已 approved，"
                f"artifact={entry_b.get('artifact')}）")
            return sb

        agent_b = StoryboardAgent(self.provider, scene_id=sid, registry=self.assets,
                                  known_characters=self._known_names())
        step_b = StoryboardStep(agent_b, scene_id=sid)
        ctx_b = self._assemble(step_b, wf_b)
        run_b = self._new_engine([step_b], handler).run(
            project_id=self.project_id, workflow_id=wf_b,
            inputs={
                "assembled_context": ctx_b,
                "storyboard_spec": {"scene_id": sid, "episode": episode},
                "scene": scene_obj,          # ★ 显式放入（裁决 3）
            },
            max_attempts=max_attempts)
        res_b = run_b.step_result(step_b.step_id)
        if res_b is None or not res_b.is_success:
            self.state.record_scene(
                episode, sid, "storyboard", status="failed",
                attempts=len(res_b.attempts) if res_b else 0,
                run_id=run_b.run_id, workflow_id=wf_b)
            slot["storyboard"] = {"status": "failed", "artifact": "",
                                  "attempts": len(res_b.attempts) if res_b else 0,
                                  "run_id": run_b.run_id, "workflow_id": wf_b}
            report["errors"].append({
                "stage": "storyboard_generation", "scene_id": sid,
                "workflow_id": wf_b, "run_id": run_b.run_id,
                "error": (res_b.error if res_b else "引擎未返回 StepResult")})
            return None

        artifact_b = res_b.approved_artifact
        self.state.record_scene(
            episode, sid, "storyboard", status="approved",
            artifact=artifact_b.filename, attempts=len(res_b.attempts),
            run_id=run_b.run_id, workflow_id=wf_b)
        slot["storyboard"] = {"status": "approved", "artifact": artifact_b.filename,
                              "attempts": len(res_b.attempts), "run_id": run_b.run_id,
                              "workflow_id": wf_b, "step_status": res_b.status}
        return self._load_storyboard(artifact_b.filename, sid, report)

    # ------------------------------------------------------------------
    # ③ 视觉资产段（每实体一次 run；裁决 2）
    # ------------------------------------------------------------------
    def _stage_visual_assets(self, entities: Sequence[Tuple[str, str]], episode: int,
                             handler: Callable, max_attempts: int, report: dict) -> None:
        registered: List[str] = []
        pending: List[str] = []
        for kind, name in entities:
            asset_id, status = self._run_one_visual_asset(
                kind, name, episode, handler, max_attempts, report)
            if status == "approved":
                registered.append(asset_id or f"{kind}:{name}")
            else:
                pending.append(f"{kind}:{name}")
        report["stages"]["visual_assets"] = {
            "registered": registered, "pending": pending,
            "referenced": len(entities),
        }

    def _run_one_visual_asset(self, kind: str, name: str, episode: int, handler: Callable,
                              max_attempts: int, report: dict) -> Tuple[str, str]:
        # 已齐备（registry 有 approved 版本）→ 跳过，不重复建档
        existing = self.visual_registry.active_profile(kind, name)
        if existing is not None:
            asset_id = existing[0]
            self.state.record_visual_asset(kind, name, status="approved", asset_id=asset_id,
                                           version=existing[1])
            return asset_id, "approved"

        safe = str(name).replace(" ", "_")
        step_id = f"visual_asset_{kind}_{safe}"
        agent = VisualAssetAgent(self.provider)
        step = VisualAssetStep(agent, step_id=step_id)
        spec = {
            "mode": "plan",
            "kind": kind,
            "name": name,
            "semantic_data": self._semantic_data(kind, name, episode),
        }
        run = self._new_engine([step], handler).run(
            project_id=self.project_id, workflow_id=step_id,
            inputs={"visual_asset_spec": spec}, max_attempts=max_attempts)
        res = run.step_result(step_id)

        if res is None or not res.is_success:
            status = "rejected" if (res is not None and res.status == StepStatus.REJECTED) else "failed"
            self.state.record_visual_asset(kind, name, status=status)
            report["errors"].append({
                "stage": "visual_asset", "kind": kind, "name": name,
                "workflow_id": step_id, "run_id": run.run_id,
                "status": status,
                "error": (res.error if res else "引擎未返回 StepResult")})
            return "", status

        # ★ 裁决 2：视觉资产只支持 approved / rejected
        if res.status == StepStatus.APPROVED_WITH_EDIT:
            raise OrchestrationError(
                f"视觉资产 {kind}:{name} 的审核决策为 approved_with_edit："
                f"本阶段不支持（编辑后的 profile 缺确定性来源；"
                f"VisualAssetRegistry.apply_review_decision 会直接报错）。"
                f"请改为 approved 或 rejected。")

        artifact = res.approved_artifact
        snapshot = artifact.read()
        if not isinstance(snapshot, dict):
            raise OrchestrationError(
                f"视觉资产 artifact 不是 dict：{type(snapshot).__name__}（{artifact.filename}）")
        try:
            asset = self.visual_registry.register(kind, name)
            ver = self.visual_registry.new_version(
                asset.asset_id,
                profile=snapshot.get("profile") or {},
                visual_prompt=str(snapshot.get("visual_prompt") or ""),
                source=str(snapshot.get("source") or "text_planned"),
                status="draft")
            self.visual_registry.submit_for_review(asset.asset_id, ver.version)
            self.visual_registry.approve(asset.asset_id, ver.version)
        except VisualRegistryError as exc:
            raise OrchestrationError(
                f"视觉资产 {kind}:{name} 写回 registry 失败：{exc}") from exc

        self.state.record_visual_asset(kind, name, status="approved",
                                       asset_id=asset.asset_id, version=ver.version,
                                       artifact=artifact.filename)
        return asset.asset_id, "approved"

    # ------------------------------------------------------------------
    # 组件接线
    # ------------------------------------------------------------------
    def _new_engine(self, steps: Iterable[Any], handler: Callable) -> WorkflowEngine:
        """每段 run 新建一个 engine（语义更清晰；实测复用亦无副作用）。"""
        return WorkflowEngine(list(steps), artifacts_dir=self.artifacts_dir,
                              run_log_dir=self.run_log_dir, review_handler=handler)

    def _assemble(self, step: Any, workflow_id: str):
        """按 step.needs 组装 AssembledContext（每段 run 前重新组装）。"""
        placeholder = WorkflowContext(
            project_id=self.project_id, workflow_id=workflow_id, run_id="pre",
            status="pending", inputs={}, created_at="", updated_at="")
        return self.assembler.assemble(step, placeholder, project_title=self.project.title)

    def _build_review_handler(self, policy: str) -> Callable:
        """按 review_policy 造审核回调（§7.4 三策略；引擎契约见 stage12_plan §1.6）。"""
        if policy == REVIEW_POLICY_HUMAN_STUB:
            # 防御性实现（run() 已在开工前短路，正常路径到不了这里）
            def _human_stub(artifact, context):  # pragma: no cover - 见 run() 短路
                raise OrchestrationError(HUMAN_STUB_MESSAGE)
            return _human_stub

        if policy == REVIEW_POLICY_AUTO_APPROVE:
            counter = {"n": 0}

            def _auto_approve(artifact, context):
                counter["n"] += 1
                return _Decision(ReviewDecision.APPROVED, f"auto-{counter['n']}",
                                 comment="programmatic auto approval (NOT human review)")

            return _auto_approve

        if policy.startswith(REVIEW_POLICY_SCRIPTED):
            _, _, script_path = policy.partition(":")
            if not script_path:
                raise OrchestrationError(
                    "review_policy='scripted:<file>' 必须给出决策序列文件路径")
            path = Path(script_path)
            if not path.is_file():
                raise OrchestrationError(f"scripted 审核决策文件不存在：{path}")
            try:
                seq = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise OrchestrationError(f"scripted 审核决策文件无法解析：{path}（{exc}）") from exc
            if not isinstance(seq, list):
                raise OrchestrationError(
                    f"scripted 审核决策文件必须是 JSON 列表：{path}")
            iterator = iter(seq)
            counter = {"n": 0}

            def _scripted(artifact, context):
                item = next(iterator, None)
                if item is None:
                    raise OrchestrationError(
                        "scripted 审核决策已耗尽（明确报错，不静默批准）")
                if not isinstance(item, dict):
                    raise OrchestrationError(
                        f"scripted 审核决策项必须是对象，实际 {type(item).__name__}")
                counter["n"] += 1
                return _Decision(
                    str(item.get("decision", "") or ""),
                    f"scripted-{counter['n']}",
                    item.get("edited_content"),
                    str(item.get("comment") or item.get("reason") or ""))

            return _scripted

        raise OrchestrationError(
            f"未知 review_policy：{policy!r}"
            f"（支持 auto_approve / scripted:<file> / human_stub）")

    # ------------------------------------------------------------------
    # 数据来源
    # ------------------------------------------------------------------
    def _known_names(self) -> List[str]:
        return _names_from_character_list(self.memory.get_characters())

    def _episode_obj(self, episode: int) -> Optional[Episode]:
        for ep in (self.project.episodes or []):
            if int(getattr(ep, "number", 0) or 0) == int(episode):
                return ep
        return None

    def _world_dict(self) -> dict:
        w = getattr(self.project, "world", None)
        if w is None:
            return {}
        chars = []
        for c in (getattr(w, "characters", None) or []):
            if hasattr(c, "to_v01"):
                chars.append(c.to_v01())
            elif isinstance(c, dict):
                chars.append(dict(c))
        return {
            "name": str(getattr(w, "name", "") or ""),
            "locations": [str(x) for x in (getattr(w, "locations", None) or [])],
            "characters": chars,
            "known_facts": [str(x) for x in (getattr(w, "known_facts", None) or [])],
        }

    def _resolve_scene_ids(self, spec: dict, episode: int) -> List[int]:
        """定场景号：run_spec → Episode.scenes → [1]；★重复即明确报错（R13）。"""
        raw = spec.get("scene_ids")
        ids: List[int] = []
        if raw is None:
            ep = self._episode_obj(episode)
            if ep is not None and ep.scenes:
                ids = [i + 1 for i in range(len(ep.scenes))]
            else:
                ids = [1]
        else:
            if not isinstance(raw, (list, tuple)) or not raw:
                raise OrchestrationError(
                    f"run_spec.scene_ids 必须是非空列表，实际 {raw!r}")
            for x in raw:
                try:
                    ids.append(int(x))
                except (TypeError, ValueError):
                    raise OrchestrationError(
                        f"run_spec.scene_ids 含非法值：{x!r}（必须为整数场景号）")
        seen: set = set()
        dupes: List[int] = []
        for i in ids:
            if i in seen and i not in dupes:
                dupes.append(i)
            seen.add(i)
        if dupes:
            raise OrchestrationError(
                f"run_spec.scene_ids 含重复场景号 {sorted(dupes)}："
                f"重复会让同名 artifact 被反复版本化、state 键互相覆盖；"
                f"请在入参去重（编排层不静默去重）")
        return ids

    def _resolve_scene_specs(self, spec: dict, episode: int,
                             scene_ids: Sequence[int]) -> Dict[int, dict]:
        """定每场景的 scene_spec（调度层权威输入）。

        来源顺序：run_spec.scene_specs → Episode.scenes 推导 → world 兜底。
        """
        out: Dict[int, dict] = {}
        raw = spec.get("scene_specs")

        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    sid = int(k)
                except (TypeError, ValueError):
                    raise OrchestrationError(f"run_spec.scene_specs 键必须是场景号：{k!r}")
                if not isinstance(v, dict):
                    raise OrchestrationError(
                        f"run_spec.scene_specs[{k}] 必须是 dict，实际 {type(v).__name__}")
                out[sid] = dict(v)
        elif isinstance(raw, list):
            for idx, item in enumerate(raw):
                if not isinstance(item, dict):
                    raise OrchestrationError(
                        f"run_spec.scene_specs[{idx}] 必须是 dict，实际 {type(item).__name__}")
                sid = item.get("scene_id")
                sid = int(sid) if sid is not None else (
                    scene_ids[idx] if idx < len(scene_ids) else None)
                if sid is None:
                    raise OrchestrationError(
                        f"run_spec.scene_specs[{idx}] 无法确定 scene_id")
                out[sid] = dict(item)
        elif raw is not None:
            raise OrchestrationError(
                f"run_spec.scene_specs 必须是 list 或 dict，实际 {type(raw).__name__}")

        ep = self._episode_obj(episode)
        ep_scenes = list(getattr(ep, "scenes", None) or []) if ep is not None else []
        known = self._known_names()
        locations = self.assets.location_names() if self.assets else []

        for idx, sid in enumerate(scene_ids):
            if sid in out and out[sid]:
                out[sid].setdefault("scene_id", sid)
                out[sid].setdefault("episode", episode)
                continue
            src = ep_scenes[idx] if idx < len(ep_scenes) else None
            if src is not None:
                info = getattr(src, "info", None)
                out[sid] = {
                    "scene_id": sid,
                    "episode": episode,
                    "location": str(getattr(info, "where", "") or ""),
                    "characters": [str(x) for x in (getattr(info, "who", None) or [])],
                    "objective": str(getattr(info, "what", "") or ""),
                    "time": "",
                    "constraints": "",
                }
            else:
                objective = ""
                if ep is not None:
                    objective = str(getattr(ep, "summary", "") or getattr(ep, "title", "") or "")
                out[sid] = {
                    "scene_id": sid,
                    "episode": episode,
                    "location": locations[0] if locations else "",
                    "characters": list(known),
                    "objective": objective or str(getattr(self.project, "title", "") or ""),
                    "time": "",
                    "constraints": "",
                }
        return out

    def _semantic_data(self, kind: str, name: str, episode: int) -> dict:
        """视觉档案的语义输入（★确定性数据，绝不调 LLM —— 总控 D-5 处置）。"""
        if kind == "character":
            entry = self._casting_entry_by_name(name, episode)
            if entry is not None and isinstance(entry.get("character"), dict):
                return dict(entry["character"])
            return {"name": name}
        if kind == "location":
            positions: List[str] = []
            if self.assets is not None and self.assets.has_location(name):
                positions = [p.id for p in self.assets.get_positions(name)]
            return {"name": name, "positions": positions}
        if kind == "style":
            # ★ Stage 13：项目级风格（来自 run_spec["style_spec"]，非编造）
            return dict(self._style_spec) or {"name": name}
        return {"name": name}

    def _casting_entry_by_name(self, name: str, episode: int) -> Optional[dict]:
        entry = self.state.casting_entry(episode)
        if not entry or not entry.get("artifact"):
            return None
        try:
            data = self._read_artifact(entry.get("artifact"))
        except OrchestrationError:
            return None
        for e in (data.get("entries") or []) if isinstance(data, dict) else []:
            if isinstance(e, dict) and str(e.get("name", "")) == str(name):
                return dict(e)
        return None

    # ------------------------------------------------------------------
    # Memory 写入（★裁决 1：只 set_characters / set_world，严禁 approve_fact）
    # ------------------------------------------------------------------
    def _apply_casting_to_memory(self, casting_data: dict) -> List[str]:
        """把 Casting 产物写进 Memory（沿用同一 run 的 Artifact，不凭空构造）。"""
        names = _names_from_character_list(casting_data.get("entries") or [])
        existing = self._known_names()
        merged: List[str] = []
        for n in existing + names:
            if n and n not in merged:
                merged.append(n)
        # R11：至少保证 {"name": …}（下游 Agent 只读 name）
        self.memory.set_characters([{"name": n} for n in merged])
        world = self._world_dict()
        if world:
            self.memory.set_world(world)
        return merged

    # ------------------------------------------------------------------
    # 视觉实体收集（§7.3 / stage12_plan §1.7；确定性，不靠 LLM）
    # ------------------------------------------------------------------
    @staticmethod
    def _collect_entities(storyboards: Iterable[Storyboard]) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        seen: set = set()

        def _add(kind: str, name: str) -> None:
            key = (kind, name)
            if name and key not in seen:
                seen.add(key)
                out.append(key)

        for sb in storyboards:
            if sb is None:
                continue
            for shot in sb.flatten_shots():
                for c in (getattr(shot, "characters", None) or []):
                    _add("character", str(c).strip())
                loc = str(getattr(shot, "location", "") or "").strip() \
                    or str(getattr(sb, "location", "") or "").strip()
                _add("location", loc)
                for a in (getattr(shot, "assets", None) or []):
                    if str(getattr(a, "kind", "")) == "prop":
                        _add("prop", str(getattr(a, "ref", "") or "").strip())
        return out

    @staticmethod
    def _style_entity(spec: dict) -> Optional[Tuple[str, str]]:
        """从 run_spec 解析项目级风格实体（★Stage 13 新增）。

        :returns: ("style", name) 或 None（未提供 style_spec → None）。

        为什么必须显式提供：STYLE 不是 Shot 能引用的资产（`core.shot.ASSET_KINDS`
        里没有 style），因此 `_collect_entities` 永远收不到它；但
        `VisualComposer.compose()` 出图时必须解析到唯一 approved STYLE 档案，
        否则明确报错（§7.4「绝不没档案也硬出图」）。未提供时**不建档**，
        视觉资产计数与 Stage 12 逐值一致（不破 stage12 的精确断言）。
        """
        style_spec = spec.get("style_spec")
        if not isinstance(style_spec, dict) or not style_spec:
            return None
        name = str(spec.get("style_name") or "default").strip() or "default"
        return ("style", name)

    # ------------------------------------------------------------------
    # ④ 图像段（★Stage 13 新增）
    # ------------------------------------------------------------------
    def _stage_images(self, storyboards: Sequence[Tuple[int, Storyboard]], episode: int,
                      spec: dict, handler: Callable, report: dict) -> dict:
        """Storyboard → VisualComposer → 真实出图 → 落盘 → 挂回 Shot。

        三重护栏与全部纪律见 `orchestration/image_stage.py` 模块文档：
        默认不出图（generate_images 缺省 False）/ 预算硬上限 / 绝不伪造出图。
        出图 prompt **一律**来自 `ComposedVisualInput.render_prompt()`，
        绝不绕过 VisualComposer 自己拼（§7.4 关键纪律）。
        """
        stage = ImageStage(
            project_id=self.project_id,
            project_dir=self.project_dir,
            artifacts_dir=self.artifacts_dir,
            store=self.store,
            provider=self.image_provider,
            registry=self.visual_registry,
            composer=VisualComposer(self.visual_registry),
            review_handler=handler,
            review_mode=str(report.get("review_mode", "") or ""),
            log_dir=self.image_log_dir,
            fetch=self.image_fetch,
            episode=episode,
            state=self.state,
        )
        return stage.run(storyboards, spec)

    # ------------------------------------------------------------------
    # 读盘 / 完成度 / 收尾
    # ------------------------------------------------------------------
    def _read_artifact(self, filename: Any) -> Any:
        if not filename:
            raise OrchestrationError("state 未记录 artifact 文件名")
        path = self.artifacts_dir / str(filename)
        if not path.is_file():
            raise OrchestrationError(
                f"state 记录的 artifact 不存在：{path}"
                f"（should_skip 应已拦下；若人工删除请重跑）")
        return read_json(path)

    def _load_scene(self, filename: Any, sid: int) -> Scene:
        data = self._read_artifact(filename)
        return Scene.from_v01(data, id=int(sid))

    def _load_storyboard(self, filename: Any, sid: int, report: dict) -> Optional[Storyboard]:
        data = self._read_artifact(filename)
        sb = Storyboard.from_dict(data)
        if sb is None:
            report["errors"].append({
                "stage": "storyboard_load", "scene_id": sid,
                "error": f"artifact {filename} 无法解析成 Storyboard"})
            return None
        warns = (sb.metadata or {}).get("normalize_warnings") or []
        for w in warns:
            report["warnings"].append(f"storyboard[{sid}] normalize: {w}")
        return sb

    def _compute_completeness(self, scene_ids: Sequence[int],
                              entities: Sequence[Tuple[str, str]], report: dict) -> None:
        scenes_done = 0
        storyboards_done = 0
        for sid in scene_ids:
            e_scene = self.state.scene_entry(report["episode"], sid, "scene")
            if e_scene and e_scene.get("status") == "approved" and \
                    self.state.should_skip(e_scene, artifacts_dir=self.artifacts_dir):
                scenes_done += 1
            e_sb = self.state.scene_entry(report["episode"], sid, "storyboard")
            if e_sb and e_sb.get("status") == "approved" and \
                    self.state.should_skip(e_sb, artifacts_dir=self.artifacts_dir):
                storyboards_done += 1
        approved = 0
        for kind, name in entities:
            if self.visual_registry.active_profile(kind, name) is not None:
                approved += 1
        report["completeness"].update({
            "scenes_done": scenes_done,
            "storyboards_done": storyboards_done,
            "assets_approved": approved,
        })

    @staticmethod
    def _decide_status(report: dict) -> str:
        comp = report["completeness"]
        if report["errors"] and not comp["storyboards_done"] and not comp["scenes_done"]:
            return "failed"
        all_scenes = comp["scenes_done"] == comp["scenes_total"]
        all_boards = comp["storyboards_done"] == comp["storyboards_total"]
        all_assets = comp["assets_approved"] == comp["assets_referenced"]
        if all_scenes and all_boards and all_assets:
            return "success"
        if comp["scenes_done"] == 0 and comp["storyboards_done"] == 0:
            return "failed"
        return "partial"

    def _finish(self, report: dict, run_id: str) -> dict:
        report["ended_at"] = now_iso()
        path = self.orchestration_dir / f"run_{run_id}.json"
        atomic_write_json(path, report)
        self.state.save()
        self.last_report = report
        self.last_report_path = path
        return report


__all__ = [
    "ProjectOrchestrator",
    "OrchestrationError",
    "HUMAN_STUB_MESSAGE",
    "REVIEW_POLICY_AUTO_APPROVE",
    "REVIEW_POLICY_SCRIPTED",
    "REVIEW_POLICY_HUMAN_STUB",
]
