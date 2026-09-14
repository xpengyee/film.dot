# stage11_report.md —— Stage 11 StoryboardAgent 阶段报告

> **阶段**：V0.2 Stage 11 = StoryboardAgent（Scene → Storyboard → Beat → Shot）
> **规格**：`D:\AI\FilmAgent\v0.2开工指令11`；**A 会话产出**：`v2/docs/stages/stage11_plan.md`
> **执行**：B（代码实现）/ C（测试/Debug）/ D（文档/交付）三段，各自独立会话
> **工作目录**：`D:\AI\FilmAgent-main`
> **前置**：Stage 10 已封存（`v0.2-stage10` = `c8a3ed8`）；开工 HEAD = `dc335a7`
> **真实 API**：**0 次**（全 Mock；真实冒烟脚本已建但未执行，待人工批准）

---

## 1. 阶段目标与达成

```text
目标：实现 StoryboardAgent，把已批准 Scene 确定性规划为 Storyboard → Beat → Shot，
      Shot 落在 Stage 8 受控词表内、引用 Stage 10 视觉资产（只存名字级引用 + 本镜头
      视觉增量）、可被 VisualComposer 直接消费，并经 Workflow/Review 走完既有审核链路。

达成：✅ 全部达成
  Scene 剧本 → StoryboardAgent → Storyboard（Beat/Shot 齐备，受控词表内，status=planned）
  → 走既有 Artifact 版本化 + Review 三态 + Retry → 可被 VisualComposer 直接消费
```

打通链路：

```text
Stage 7 剧本  →  Stage 11 分镜  →  Stage 10 视觉组装  →  [Stage 13 出图]
                    ↑ 本阶段补上这条链缺失的中间环
```

---

## 2. 交付物清单

| 类型 | 文件 | 说明 |
|---|---|---|
| 代码 | `v2/agents/storyboard.py` | `StoryboardAgent(LLMAgent)` + `StoryboardStep(LLMStep)` + `normalize_storyboard()`（§7.2 八条）+ 词表渲染/自检 |
| Prompt | `v2/prompts/v0.2/storyboard_generation.txt` | 只放角色设定 + JSON 契约（受控词表走 user payload） |
| 导出 | `v2/agents/__init__.py` | 纯追加 `StoryboardAgent` / `StoryboardStep`（diff 4 行） |
| 测试 | `v2/logs/stage11_storyboard/run_tests.py` | mock 套件 T01~T20 + T19 历史回归 8 套子进程 |
| 测试证据 | `v2/logs/stage11_storyboard/test_report.json` | 65/65，`real_api_requests=0` |
| 真实脚本 | `v2/logs/stage11_storyboard/run_real_storyboard.py` | T31~T35，≤2 次真实文本调用（**未运行**） |
| Fixtures | `v2/logs/stage11_storyboard/fixtures/scene_1.json` / `actors_profile.json` | 真实冒烟输入（scene_1 已通过 Stage 7 `Validator` 0 错误） |

**未修改**（`git diff --stat HEAD` 全空）：`FilmAgent/`、仓库根 `.gitignore`、`v2/core/shot.py`、
`adapter.py`、`models.py`、`assets.py`、`validator.py`、`visual*.py`、`agents/base.py`、
`casting.py`、`scene_generation.py`、`visual_asset.py`、`v2/llm`、`v2/memory`、`v2/review`、
`v2/workflow`、`v2/context`、全部历史测试套件。

---

## 3. 契约笔记（S1 关键结论，来自 A 会话并复核）

| # | 契约要点 | 影响 |
|---|---|---|
| 1 | `Line.type` 是 V0.2 内部字段，`to_v01()` 不写出；V0.1 靠"有无 `move` 键"判行类型 | 对齐台词必须用 `Scene.from_v01()` 还原，不能猜 dict 键 |
| 2 | `Shot.positioning` 来源是 `Line.positions`，`Shot.move` 来源是 `Line.move` | 两者独立字段，不合并 |
| 3 | `Shot.to_dict()` 24 键（含 V0.1 透传 `reason` / `insertion_reason` / `insertion_position`） | 构造时保留透传字段 |
| 4 | `CameraPlan.shot_size` / `Shot.composition` 自身默认是**空串**（非 `MS`/`single`） | 总控裁决 3：越界取 `MS`/`single`（有值更可渲染）+ **必须记 warning** |
| 5 | `registry` 非空且 `shot_type` 非空时，必须命中 `registry.shot_names()` 或 `SHOT_SIZES`，否则 `INVALID_SHOT_TYPE`（`validator.py` L715-L727） | 规范化第 7 条：非空但都不命中 → 清空 + warning |
| 6 | `kind=="dialogue"` 要求 `speaker` **与** `dialogue` 双非空（L656-L667）；`kind=="move"` 要求 `move` 对象完整 | 规范化第 8 条：补不齐 → 降级 `general` + warning |
| 7 | `ShotValidator` **不校验** `duration` / `subject` / `visual_description` | 时长与 subject 属"规范化/质量"而非校验 |
| 8 | `subject` 会进 `VisualComposer.compose()` → 图像 prompt（`visual_compose.py` L143） | 约束为短名词短语 + 40 字截断 |
| 9 | `StoryboardValidator.validate_dict()` 直接吃 dict，`from_dict` 失败包成错误（不抛异常） | `Step.validate()` 用它，天然满足"返回错误列表" |
| 10 | `ArtifactStore.next_version()` 扫磁盘 +1，**绝不覆盖** | 重试/编辑自动产生 `storyboard_1_v2.json` |

---

## 4. 实现摘要

### 4.1 接线（与 `SceneGenerationAgent` 同构，零新机制）

```text
StoryboardAgent.needs = ["project_info", "approved_facts", "characters",
                         "locations", "actions", "shots"]      # 与 Stage 7 同集合
StoryboardStep.step_id = f"storyboard_generation_{scene_id}"    # 多场景独立 step
StoryboardStep.needs_review = True                              # 走既有 Review 三态
artifact_name = f"storyboard_{scene_id}"                        # storyboard_1.json / _v2 …
context.inputs = {"assembled_context", "storyboard_spec", "scene"}
```

- **Scene 注入（A 会话裁决 S-A）**：由调度层放进 `inputs["scene"]`（`Scene` 对象或 `to_v01()` dict）。
  **缺 → 抛 `StepError`**，绝不静默产出空 Storyboard。Agent 不 `open()` 任何文件。
- Step 把解析后的 `scene_id` 回注 `storyboard_spec`，保证 artifact 命名与 `Storyboard.scene_id` 一致。

### 4.2 确定性规范化（§7.2 全部 8 条，纯函数 `normalize_storyboard()`）

```text
1 受控词表兜底   shot_size/camera_angle/camera_movement/composition/kind 越界 → MS/eye_level/static/single/general + warning
2 名字回锚       characters ⊆ (Scene.who ∪ known_characters)；location ∈ (Scene.where ∪ registry.location_names())
                 找不到 → 剔除 / 回锚 + warning（含 positioning / actions / assets 的引用）
3 id 重排        beat_id 按出现序 1..n；shot_id 全局 1..N（消除跳号/重复）
4 时长兜底       duration <= 0 → dialogue 3.0 / move 2.5 / general 2.0（合理值保留）
5 状态强制       所有 Shot.status = "planned"
6 speaker/dialogue 回锚   dialogue 镜头按序对齐 Scene.lines（保序游标）
7 shot_type 安全化        非空但既不在 registry.shot_names() 也不在 SHOT_SIZES → 清空 + warning
8 dialogue/move 降级      台词补不齐 / move 不完整 → kind="general" + warning
```

warnings 统一放 `Storyboard.metadata["normalize_warnings"]`（**不放 Shot.metadata**，
以免扰动 T18 的逐字节断言）。

### 4.3 超出 §7.2 的防御性补充（已向总控报备，待裁决）

| 补充 | 理由 | 对应硬失败 |
|---|---|---|
| `actions` 回锚：state 越界 → `standing`；角色未登记 / 动作未注册 → 剔除整条 | 与 §7.2 第 1/2 条同精神 | `INVALID_STATE` / `UNKNOWN_CHARACTER` / `INVALID_ACTION` |
| `assets` 回锚：kind 越界或 ref 空 → 剔除；character/location/action 类 ref 未登记 → 剔除 | 同上 | `INVALID_ASSET_KIND` / `MISSING_FIELD` / `UNKNOWN_CHARACTER` / `INVALID_LOCATION` / `INVALID_ACTION` |
| 列表字段（characters/positioning/actions/assets/shots）非列表 → 记 warning 后按空处理 | 原先静默当空，违反"不静默丢弃信息"（C 会话修复） | 信息静默丢失（不报错但退化） |

### 4.4 Prompt 词表纪律

- 受控词表**从 `core.shot` 常量渲染进 user payload**（`render_vocab_block()`），prompt 文件只放
  角色设定 + JSON 契约 → 单一事实来源，永不漂移。
- 另加**运行时自检** `check_prompt_vocab_consistency()`：prompt 文件若出现词表文字且与
  `core.shot` 常量不一致 → 抛 `StepError`（防后人手工抄错）。

### 4.5 Token / Context 纪律（§十一）

```text
Shot 只存「名字级引用（characters / location / assets[].ref）+ 本镜头视觉增量（visual_description）」
角色外貌 / 地点环境 活在 VisualAssetRegistry，由 VisualComposer 在调用模型前一次性展开
硬证据（T18b）：注册视觉资产前后同一 Storyboard.to_dict() 逐字节不变
```

---

## 5. 测试证据

### 5.1 本阶段套件

```bash
env -u PYTHONPATH python v2/logs/stage11_storyboard/run_tests.py   # rc=0
```

```text
TOTAL: 65/65 passed        real_api_requests = 0
```

| 组 | 覆盖 | 结果 |
|---|---|---|
| T01 / T01b / T01e / T01f | needs 契约、payload 含全部受控词表取值、命名规范、prompt 只放契约、词表护栏 | PASS |
| T02a–c | Agent 初始化 / Step 必须注入 agent / 缺 prompt → `StepError` | PASS |
| T03a–c | mock 正常路径 → 合法 Storyboard + 保序 + Validator 0 错误 | PASS |
| T04 / T04b | 越界值兜底（MS/eye_level/static/single/general）+ shot_type 自由文本清空（R1 专测） | PASS |
| T05 / T05b | 名字回锚（编造角色剔除 / 地点回锚）+ positioning 非法站位剔除（R6 专测） | PASS |
| T06 / T07 / T07b / T08 | id 重排 / 时长兜底 / dialogue 降级（R5 专测）/ status 强制 | PASS |
| T09a–d | Step 壳：spec 派发、artifact 命名、缺 scene → `StepError`、多场景 step_id 独立 | PASS |
| T10a–c | `validate()` 返回错误列表（非异常） | PASS |
| T11 / T12a–c / T13 | Artifact 版本化 / Review 三态 / Retry 耗尽 FAILED | PASS |
| T14 | 真实 API = 0（`APIRequestCounter` 程序化快照） | PASS |
| T15a–b | Composer 衔接：空 registry 列缺口 / 全 registry 解析角色+地点 | PASS |
| T16a–d | compileall / `ASSET_KINDS` 精确不变 / import 冒烟 / 密钥扫描 | PASS |
| T17 | 禁改清单 0 diff（含 `FilmAgent/` + 仓库根 `.gitignore`） | PASS |
| T18a–d | Token 硬证据（逐字节不变）+ 无档案渗漏 + 名字引用可解析 | PASS |
| T20a–j | 边界/防御（C 会话补充）：scene 形态、异常输入、地点继承、上游不一致 fail-loud、空 Beat 跳过、护栏不误报、缺 spec 兜底、依赖方向、列表字段非列表 | PASS |
| T19 | 历史回归 stage1/3/4/6/7/8/9/10 八套子进程 | PASS（8/8 rc=0） |

### 5.2 历史回归（T19，剥离宿主 PYTHONPATH shim）

```text
stage1 19/19 · stage3 38/38 · stage4 25/25 · stage6 24/24
stage7 29/29 · stage8 27/27 · stage9 26/26 · stage10 76/76     全部 rc=0
```

### 5.3 真实调用账目

```text
本阶段真实 API 调用 = 0 次，成本 = ¥0.00
run_real_storyboard.py 已就绪但**未执行**（需人工批准 + 配置 key；预期 ≤2 次文本调用，<¥0.1）
```

---

## 6. AC 逐条对照（总控验收用；总控将亲自复跑）

| AC | 结论 | 证据 |
|---|---|---|
| AC1 套件全绿（T01~T19）+ 历史回归全绿 | ✅ | 65/65 rc=0；T19 八套 rc=0 |
| AC2 V0.1 冻结；tag 未移动 | ✅ | `git diff -- FilmAgent/` = 0；`v0.1` = `344c0c6…`；5 tag 未动 |
| AC3 禁改清单全部 0 diff | ✅ | 18 个路径 `git diff --stat HEAD` 全空 |
| AC4 产出可用（Validator 全过 + status=planned） | ✅ | T03c、T08 |
| AC5 词表纪律（越界被映射，未改任何词表） | ✅ | T04/T04b；T16b；T19-stage8 原样通过 |
| AC6 Token 硬证据（逐字节不变 + 无渗漏） | ✅ | T18b、T18c |
| AC7 链路可通（Composer 可解析；缺档明确报错） | ✅ | T15a、T15b |
| AC8 机制复用（版本化/三态/重试，无第三套状态机） | ✅ | T11、T12、T13 |
| AC9 真实 API ≤3 文本 + 0 视觉；无配置时阻塞报告 | ✅ | 0 次；脚本未运行 |
| AC10 静态检查全过 | ✅ | T16a–d、T20i |
| AC11 文档同步 | ✅ | 本文件 + `PROJECT_STATUS` / `HANDOFF` / `ROADMAP` / `CLAUDE.md` |
| AC12 Git 无泄漏文件 | ✅ | 无 `llm_calls.jsonl` / 图片 / registry 快照 |

---

## 7. 遗留问题与待总控裁决

1. **待裁决：两条防御性回锚**（`actions` 的 state/角色/动作；`assets` 的 kind/ref）超出 §7.2 的 8 条。
   性质与 A 会话 R5 相同（必要补全，非扩大范围）。
2. **D-2（已判非缺陷，fail-loud）**：若上游 `Scene.info.who` 含未登记角色，
   `StoryboardValidator` 报 `UNKNOWN_CHARACTER` → 3 次重试后 FAILED（不静默丢角色）。
   建议 Stage 12 在调度层先校验 Scene 再喂 Stage 11。已固化为 T20e。
3. **多场景调度缺口**：S-A 只支持 `inputs["scene"]`（单 scene/run）。同一 workflow 内放
   N 个 storyboard step 会都读同一个 scene。`step_id` 命名已独立（T09d 验证），
   但"跨场景取 scene"的调度机制缺失 → 留 Stage 12 设计。本会话未擅自引入旁路。
4. **复位清单需扩充**：跑 stage8/stage9/stage10 的嵌套回归会**连带重写 stage3** 的
   `stage3_report.json` / `stage3_console.txt`（指令 §3.2 坑 1 只列了 4 个文件）。
   建议补进指令。
5. **T-6（技术债）**：本轮又复现一次（每次必现），处理方式仍是"跑完即复位"。
6. `run_real_storyboard.py` 的 `real_summary.json` / `real_console.txt` 属运行产物，
   首次真实执行后需确认 Git 忽略边界（`real_console.txt` 已忽略；`real_summary.json` 保留为证据）。

---

## 8. Git 建议（**待人工批准，本会话不执行**）

```bash
cd /d D:\AI\FilmAgent-main
rm -f llm_calls.jsonl v2/llm_calls.jsonl
git checkout -- v2/logs/stage3_workflow_test/stage3_report.json \
                v2/logs/stage3_workflow_test/stage3_console.txt \
                v2/logs/stage4_memory_context_review_test/stage4_report.json \
                v2/logs/stage4_memory_context_review_test/stage4_console.txt \
                v2/logs/stage6_casting/stage6_report.json \
                v2/logs/stage7_scene_generation/stage7_report.json
git status --short          # 期望：仅 Stage 11 预期文件（1 改 + 8 新增）
git diff -- FilmAgent/      # 期望：空

git add v2/ && git commit -m "feat(v0.2): storyboard agent (stage11)"
git tag v0.2-stage11
```

> tag 一经创建永不移动、永不 force。

---

*Stage 11 报告 —— B/C/D 三段会话共同产出；验收以总控亲自复跑结果为准。*
