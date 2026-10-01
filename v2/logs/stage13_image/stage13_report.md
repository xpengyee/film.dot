# Stage 13 阶段报告 —— Image Generation（真实出图）

> 阶段：V0.2 Stage 13
> 开工指令：`D:\AI\FilmAgent\v0.2开工指令13`
> 执行会话：单个 WorkBuddy 会话，按 **A→D 全流程**（A 分析 / B 实现 / C 测试 / D 交付）
> 执行时间：2026-09-15
> 前置状态：Stage 12 已封存（`v0.2-stage12`）；开工自检通过（见 §6.1）
> 对应 Git：★ **尚未 commit、尚未 tag**（§16：每阶段完成即停下等人工验收）

---

## 1. 一句话结论

> **`image` 模态已开通，真实文生图 Provider 已实现，编排层已新增"出图段"——
> 每个 Shot 能经 `VisualComposer` 组装 → 生成图像 → 版本化落盘 → 以
> `ShotAsset(kind="image")` 挂回 Shot，全程受程序化预算护栏与独立成本记账约束。**
>
> **Mock 路径已端到端跑通并全绿；★真实出图未执行** —— 人工四项前置未齐
> （Config 未填模型名 / 未注入 key / 未批准 / 单价未登记），
> 脚本如实报告"阻塞"，**未用任何占位图冒充真实生成**（铁律 11）。

---

## 2. 交付物清单

### 2.1 新增文件

| 文件 | 内容 |
|---|---|
| `v2/llm/providers/mock_image.py` | `MockImageGenerationProvider`：确定性最小合法 PNG（纯 stdlib `zlib`+`struct`，无第三方依赖、无 base64 大常量）；同入参必同字节、不同 prompt 必不同字节；返回 `data:image/png;base64,…` 引用；**0 网络 0 成本**；`calls` 观测位供"实际调用数"断言 |
| `v2/llm/providers/openai_compatible_image.py` | `OpenAICompatibleImageProvider`（`POST {base_url}/images/generations`）+ `ImageGenerationError`；错误翻译/瞬时重试/`transport` 注入沿用 `LLMClient` 既有模式（不继承 LLMClient：chat 的传输入参形状不适用于图像端点）；`_invoke()` 是**唯一真实计数点**（retry 也计预算）；b64_json / url 两路都处理；`size` 归一化走 Config 的 `size_separator` |
| `v2/orchestration/image_stage.py` | `ImageStage`（图像段全部实现）+ `resolve_image_bytes()`（data URL / http(s) / 本地路径 三路解析）+ `guess_extension()` + `sha256_of_bytes()` + `shot_ref_of()` |
| `v2/logs/stage13_image/run_tests.py` | mock 套件（T01~T19；0 真实 API；含内嵌历史回归 10 套子进程） |
| `v2/logs/stage13_image/run_real_image.py` | ★真实出图冒烟脚本（并入 Stage 12 遗留的 `run_real_lifecycle` 链路；默认只体检，0 真实调用） |
| `v2/logs/stage13_image/stage13_contract_notes.md` | A / A2 契约笔记（接入点契约 + 厂商协议调研 + 三处冲突裁决） |
| `v2/logs/stage13_image/stage13_report.md` | 本文件 |

### 2.2 修改文件（最小增量，全部在 §8.2 允许清单内）

| 文件 | 改动 | 兼容性 |
|---|---|---|
| `v2/llm/providers/registry.py` | 只开 `kind == "image"` 分支（mock + 真实 Config 档）；video/audio 保持拒绝；`_image_kinds()` → `_vision_vendors()` 消歧义（行为 0 变）+ 新增 `_image_vendors()`；`get_default_provider` 环境开关未设时显式回落 `mock` | 既有 kind 行为不变；stage9/10 断言保持绿（§6.4） |
| `v2/llm/providers/__init__.py` / `v2/llm/__init__.py` | 纯追加导出（`MockImageGenerationProvider` / `OpenAICompatibleImageProvider` / `ImageGenerationError` / `MAX_REAL_IMAGE_REQUESTS`） | 纯追加 |
| `v2/llm/request_counter.py` | `MAX_REAL_IMAGE_REQUESTS = 3` + `DEFAULT_REAL_BUDGETS["image"]` + `set_budget()` | 纯追加；既有 kind 行为不变 |
| `v2/config.py` | `PROVIDERS["bailian_image"]`（`default_model=""` 配置护栏、`size_separator="x"`、`default_size`）+ `IMAGE_PRICING`（空表 = 护栏）+ `get_image_pricing()` + `estimate_image_cost()` | 纯追加；既有档位 0 改 |
| `v2/orchestration/image_stage.py`（新增）/ `pipeline.py` | image 段接线（`_stage_images`）+ `run_spec["style_spec"]` 并入 visual_assets 段 + 构造参数 `image_provider` / `image_log_dir` / `image_fetch` | 缺省行为与 Stage 12 **完全一致**（`generate_images` 缺省 False、未传 style_spec 时不建档） |
| `v2/orchestration/state.py` | `record_image_stage()` / `image_entry()`（纯追加，仍只是对 Artifact/Review 结果的**镜像**） | 纯追加 |
| `v2/orchestration/__init__.py` | 导出 `ImageStage` 等 | 纯追加 |
| `v2/.gitignore` | §8.4 六条二进制防护规则 | 仓库根 `.gitignore` **仍 0 diff** |

### 2.3 明确未做（§4.4 范围外，未越界）

```text
VLM QA Agent（生成图 vs 参考图逐字段裁决）        → ★ Stage 14（已为它备好输入契约，见 §5.5）
Video Generation / Audio / TTS / Assembly        → Stage 15+
图生图 / 局部重绘 / 多图合成 / 图像编辑            → 未立项
交互式人工审核 UI / 数据库 / 生产部署              → 未立项
修改任何已封存层（core / workflow / agents / 既有 Provider / memory / review / context）
                                                  → 硬禁止，实测 0 diff（§6.6）
```

---

## 3. 设计决策（A / B）

### 3.1 引用串契约：`ProviderResult.text` 携带"产物引用"

`ImageGenerationProvider.generate_image()` 的签名是 Stage 9 就定好的契约（`base.py:244`），
`ProviderResult` **没有二进制字段**，且基类 docstring 明确要求"返回一个**产物引用**，
不要在这里决定文件命名与版本（那是 Artifact 层铁律）"。因此本阶段确立三形态引用串：

```text
data:image/<fmt>;base64,<...>   内嵌图片（Mock 与"后端返回 b64_json"走这条）
http(s)://...                   远端 URL（由编排层下载为二进制）
<本地路径>                       已是本地文件
```

→ 接口签名 0 改；落盘/命名/版本/去重（sha256）全部留在编排层。

### 3.2 Mock 用"确定性 PNG 编码器"而不是硬编码 base64 常量

开工指令举例"1×1 PNG，base64 内嵌"。实现改为**纯 stdlib 的确定性 PNG 编码器**
（`zlib` + `struct`，像素由 `sha256(seed)` 派生），理由：

1. 不往源码里塞大 base64 常量（可读性 / 可审计性更好）；
2. **不同 prompt → 不同字节**，使"多镜头出图"与"manifest sha256 逐一相符"的断言
   **真有区分度**（否则 N 张一模一样的图也能蒙混过关）；
3. 仍是"最小合法图片"，仍是 0 网络 0 成本，仍完全确定性。

### 3.3 两道预算防线（而不是一道）

`APIRequestCounter.count_mock_request()` **不检查预算**（只计数，`request_counter.py:133-156`），
所以 Mock 路径的预算护栏**必须由编排层自己执行**。设计为：

```text
第一道（业务层）：ImageStage 自持 dispatched 计数，cap = min(max_images, MAX_REAL_IMAGE_REQUESTS)；
                 dispatched >= cap → 记 rejected + 抛/记 APIBudgetExceededError，**不发第 N+1 次**。
第二道（传输层）：同一 cap 经新增的 counter.set_budget("image", cap) 下推到 APIRequestCounter，
                 真实 HTTP 层独立拒绝（即使业务层漏判也拦住）。
```

`cap = min(max_images, MAX_REAL_IMAGE_REQUESTS)` 是**保守读法**（"必须程序化设限" +
"`MAX_REAL_IMAGE_REQUESTS` 硬上限"两条同时满足）→ 副作用是 `max_images` 只能调小、
不能调大（技术债 T-8，已在 run_report 同时暴露 `max_images_requested` 与 `budget_cap`，不误导）。

### 3.4 图像账与 token 账**物理分开**

```text
Token 账（既有 TokenLogger）      : text / vision / multimodal 的 token 与成本 → llm_calls.jsonl
请求计数（既有 APIRequestCounter）: 真实 HTTP attempt，按 kind 分桶 → 内存 + 审计
图像账（本阶段新增）              : image_generation_calls.jsonl —— 每张图一行
                                   字段风格与 TokenLogger 对齐（timestamp/request_id/step/agent/
                                   model/estimated_cost/currency/duration_ms/success/retry_count/
                                   error/prompt_hash/prompt_length）
                                   ★ 但**不含任何 token 概念**（input_tokens/output_tokens/total_tokens）
                                   另加图像专属溯源：image_id/shot_ref/size/provider/path/sha256/
                                   reference_images/profile_versions
```

→ 既"不造第二套与 TokenLogger 风格完全不同的账"，也**不把图像成本塞进 token 账**
（两者计价单位不同）。硬断言：套件 T12d 逐键检查。

### 3.5 挂回 Shot 采用"最小增量"，不动任何其它字段

`_attach_shot_asset()` 只 `shot.assets.append(ShotAsset(kind="image", ref=…, detail=…))`，
**不改** `shot.status`（虽然 `SHOT_STATUSES` 有 `rendered`）。理由：T14 硬证据要求
"出图的 Shot 只多一个 image asset、未出图的 Shot 序列化逐字节不变"；
`status` 的语义跃迁属后续阶段（并有 `ShotValidator` 约束），本阶段不越界。

### 3.6 图像段支持 resume，专门保护成本

manifest 中已 `generated` 的 `shot_ref` 在重跑时**直接跳过**（零 Provider 调用、零新产物），
只把已有引用补挂回 Shot → 不会重复花钱。硬证据：套件 T19g（重跑 `calls=0`、
`skipped=2`、`budget_used=0`）。

### 3.7 图像审核遇 `approved_with_edit` → fail-loud

图像是二进制产物，没有"编辑后内容"的确定性来源（与 Stage 12 视觉资产裁决 2 同源），
故明确抛 `OrchestrationError` 指路，**不隐式解释**。硬证据：套件 T09f。

---

## 4. 契约笔记（A / A2 产出）

完整版见 **`v2/logs/stage13_image/stage13_contract_notes.md`**。要点：

- **接入点契约**：逐条以"实读代码 + 文件:行号"为据（Provider 接口族 / registry 护栏 /
  计数器语义 / VisualComposer / ArtifactStore / OrchestrationState / run_report 结构断言）。
- **厂商协议调研（A2）**：
  - 实现形状 = OpenAI 兼容 `POST /images/generations`（`data[0].b64_json` 优先 → `data[0].url` 兜底
    → 两者皆无则 `ImageGenerationError`）；
  - 尺寸记法差异（canonical `1024*1024` vs 线上 `1024x1024`）**全部落进 Config** 的
    `size_separator`，代码里**没有任何尺寸常量硬编码**；
  - ★**未能核实项**：公开文档（阿里云百炼《DashScope API 参考》）只列出 text-generation /
    multimodal-generation 两个**原生**端点，**兼容模式章节未覆盖图像生成**，因此无法确认
    目标厂商是否完整支持该端点、返回 b64 还是 URL、是否异步轮询 → 已如实登记为
    **首次真实调用前必须由人工确认的前置项**（技术债 T-9）；
  - **差异过大的兜底**：`transport` 可注入 —— 若厂商需要异步轮询 / 签名 / 不同路径，
    注入厂商 transport 即可，**不动接口签名、不动架构**（§17 要求的"先报告差异，不自行发明协议层"）。

---

## 5. 实现摘要（B）

### 5.1 编排层段顺序（image 段插入位置）

```text
ProjectOrchestrator.run(run_spec)
  ① 集级 Casting
  ② 每场景【两段式】：scene_gen_{sid} → preflight → storyboard_{sid}
  ③ 视觉资产段（★Stage 13：run_spec["style_spec"] 提供时把 ("style", name) 并入同一段）
  ④ ★图像段（Stage 13 新增）      ← 每 Shot 一张图
  ⑤ 齐备判定 + run_report 原子落盘 + state.json 原子落盘
```

### 5.2 图像段内部流程

```text
for 每个已 approved 的 Storyboard:
    for 每个 Shot（受 run_spec["image_shot_refs"] 过滤）:
        0. resume 命中（manifest 已有 generated 记录）→ 零调用、只补挂引用
        1. composer.compose(shot) → ComposedVisualInput        ← ★不得绕过
             失败（缺 approved 档案）→ 记 failed + 缺口清单，不硬出图
        2. prompt = ci.render_prompt()；size = run_spec["image_size"]；
           reference = ci.reference_images 的 "image_id:sha256" 组合；
           profile_versions = {asset_id: version}（★QA 溯源锚点）
        3. 预算检查（dispatched >= cap → 拒绝，不发第 N+1 次）
        4. generate_image(...) → 引用串 → resolve_image_bytes() → 落盘
             GEN_%03d 单调递增；目标文件已存在 → 明确报错（绝不覆盖）
        5. manifest 条目（sha256 / path / provider / model / prompt_hash / prompt_length /
           reference_images / profile_versions / estimated_cost / duration_ms / status）
           + 图像账 append
        6. Review 三态（复用既有回调）：APPROVED → 挂回；REJECTED → 不挂回但留 manifest 记录；
           APPROVED_WITH_EDIT → 明确抛 OrchestrationError
    → 有改动才落 Storyboard 新版本（ArtifactStore 自动递增，原文件字节不变）
→ manifest 原子落盘（images[] 追加）+ state.json 记图像段进度镜像
```

### 5.3 `run_report["stages"]["image"]` 字段（§6.2⑤ + 少量审计增量）

```text
enabled / images_requested / images_generated / images_failed / images_rejected /
images_skipped / budget_used / budget_cap / budget_rejections / max_images_requested /
review_mode / cost_total / cost_currency / unpriced_images / size / provider /
manifest / shots_total / shots_selected / storyboard_versions / skipped_reason
```

★ **`report["completeness"]` 一个键都没加** —— stage12 对它是**精确相等**断言
（`run_tests.py:539-543`），加键会破历史套件。

### 5.4 `cost_total` 的诚实口径

任一条目缺价 → `cost_total = null`（**不给残缺合计**），并暴露 `unpriced_images` 张数。
初始 `IMAGE_PRICING` 为空表 → 当前真实出图的成本必然记 `null`（**绝不用 token 价冒充**）。

### 5.5 ★为 Stage 14（VLM QA）备好的输入契约（§7.6，否则会返工）

manifest 每条记录：

```json
{
  "image_id": "GEN_001", "shot_ref": "S1_B1_SH1",
  "path": "generated_images/GEN_001.png", "sha256": "<64hex>",
  "size": "1024*1024", "provider": "mock", "model": "mock-image",
  "prompt_hash": "<64hex>", "prompt_length": 812,
  "reference_images": [{"image_id": "IMG_001", "sha256": "..."}],   ← QA 比对锚点
  "profile_versions": {"CHAR_001": "v1", "LOC_001": "v1"},          ← 逐字段裁决要知道比的是哪版
  "estimated_cost": null, "currency": "", "duration_ms": 1,
  "status": "generated|failed|rejected", "created_at": "..."
}
```

硬证据：套件 T10c（字段齐备）+ T10a（sha256 与磁盘逐一相符）。

---

## 6. 测试证据（C）

### 6.1 开工自检（指令 §3.1）

```text
git log --oneline -5   → HEAD = 30e163b feat(v0.2): project lifecycle orchestration (stage12)  ✅
git status --short     → 空（Stage 12 已封存）                                                  ✅
git tag -l             → v0.1 / v0.2-stage7 / 8 / 9 / 10 / 11 / 12 齐全，v0.2-stage13 尚未创建   ✅
git diff -- FilmAgent/ → 空                                                                      ✅
```

### 6.2 Stage 13 套件（`v2/logs/stage13_image/run_tests.py`，0 真实 API）

```bash
env -u PYTHONPATH python v2/logs/stage13_image/run_tests.py
```

```text
T01  结构 / 依赖方向：generate_image 签名与 base 一致 / kind·vendor / ImageGenerationError /
     ★下层不得 import orchestration（反向扫 0 命中）/ orchestration 的 import 仍只向下
T02  registry：image 可造（mock）；★video/audio 仍被拒绝；缺省 vendor 仍拒绝；
     真实档 default_model 空 → 明确报错；未登记 vendor → 报错；
     get_default_provider("image") → Mock；image 桶与 vision 同量级；
     config bailian_image 档 + IMAGE_PRICING 空表护栏
T03  Mock 出图：确定性（两次字节一致）/ 不同 prompt 不同字节 / 合法 PNG /
     sha256 / data URL 引用 / ★0 网络（socket 打桩）/ 空 prompt 报错 / 编码器可用
T04  ★默认不出图：零调用 / 零图像文件（连目录不建）/ 零记账 / 零 manifest /
     run_report 如实记录 enabled=False / 不影响原编排结果
T05  单 Shot 出图：请求1生成1失败0 / 真实落盘 GEN_001.png / manifest 字段完整 /
     ShotAsset 挂回 / ★Storyboard 新版本 / ★原文件字节不变 / 新版本只含一个 image asset
T06  多 Shot：3 Shot → 3 图 / manifest 生成条数 == 磁盘文件数 / sha256 互不相同 /
     两份 Storyboard 各自新版本 / ID 单调无重复 / ★max_images 高于硬上限时被收紧
T07  ★预算护栏：请求3上限2 → 生成2拒绝1 / ★实际调用数 == 2（第3次未发出）/
     cap 与 rejections 如实记录 / 被拒条目带 APIBudgetExceededError 且无 path·sha256 /
     磁盘 2 张 / 记账 2 条 / 上限下推到 counter / ★真实 HTTP 层同样只发 2 次
T08  重试计预算：瞬时失败重试 2 次 → 3 次真实请求；审计 attempt 1/2/3 且 is_real；
     审计无 prompt 正文与 key；★预算耗尽后不再重试；b64/url 两形态；
     无载体 → ImageGenerationError；本地校验不耗预算；size 归一化；空模型名构造报错
T09  审核三态：APPROVED 挂回 / ★auto_approve 标注程序化模拟 / REJECTED 不挂回但留 manifest
     且 sha256 完整 / REJECTED 不产生新版本 / APPROVED_WITH_EDIT → 明确 OrchestrationError
T10  ★sha256 一致性：manifest 每条 == 磁盘实际（逐一相符）/ 路径形态 / QA 锚点字段齐备 /
     prompt 正文不入库（只记 hash+length）
T11  缺档案：无 STYLE → compose 明确报错列缺口 / 零 Provider 调用 / 零图像文件（无占位图）/
     角色无 approved 档案 → 同样报错列缺口
T12  记账：★条数 == 实际调用数 / ★未登记价格 → null（非 0、非编造）/ cost_total null /
     字段风格与 TokenLogger 对齐且不含 token 概念 / 图像专属溯源字段齐备 /
     与 token 账分开 / 记账文件被 git 忽略
T13  ★真实 API = 0（程序化快照断言）/ socket 打桩下跑通 / 缺省档为 mock / 无 is_real 审计
T14  ★Token 硬证据：未参与出图的 Shot 序列化逐字段不变 / 出图 Shot 只多 assets 一个字段 /
     只多一条 image asset / ★档案文本不渗漏 / 原文件字节不变
T15  ★二进制泄漏：图片全部被 git 忽略 / git status 无图片 / v2/ 无二进制入库 /
     仓库根 .gitignore 0 diff / v2/.gitignore 已补 §8.4 全部规则
T16  静态检查：compileall / import 冒烟 + 新导出齐备 / ★无新增硬编码密钥字样
     （与 HEAD 逐行对比）/ ★无硬编码图像模型名 / ASSET_KINDS 精确不变
T17  禁改清单 0 diff（§8.3 全部 + FilmAgent/ + 仓库根 .gitignore）/ v2/workflow + v2/core 0 diff /
     ★既有 7 个 tag 未移动且 v0.2-stage13 尚未创建 / V0.1 冻结
T18  历史回归：stage1/3/4/6/7/8/9/10/11/12 十套子进程
T19  端到端（mock）：status=success / run_report.image 统计正确 / budget_used == 磁盘 == 调用数 /
     manifest 可追溯 / 每个成功出图的 Shot 都挂上引用 / state.json 记图像段进度 /
     ★resume 零新调用 / 重跑仍补挂引用 / 不新增图像文件 / URL 形态经注入下载器落盘 /
     下载失败明确报错 / 引用串三路解析
```

**结果：见 §6.3 的实测数字**（`test_report.json` 为机器可读证据）。

### 6.3 实测数字

```text
命令：env -u PYTHONPATH python v2/logs/stage13_image/run_tests.py
结果：TOTAL 126/126 passed    RC = 0
     real_api_requests = 0（程序化快照断言）
     顶层 [FAIL] 行 = 0
     豁免登记（excused_history_fails）= 6 条，全部是 §6.4 的 pre-commit 禁改护栏
机器可读证据：v2/logs/stage13_image/test_report.json
耗时：约 59 分钟（★原因见 §6.4.2：历史回归嵌套成本呈指数级）
```

豁免的 6 条 FAIL（逐条留痕，供总控审计）：

```text
stage10: [FAIL] T15b config.py 预期 0 改（bailian_vision 槽位既有）
stage11: [FAIL] T17 禁改文件 0 diff（§8.3 硬清单 + FilmAgent/ + 仓库根 .gitignore） -> 有改动: ['v2/llm']
stage11: [FAIL] T19-stage10 历史回归全绿  ->  TOTAL: 75/76 passed; FAIL: T15b config.py 预期 0 改
stage12: [FAIL] T16 禁改清单 0 diff（§8.3 + FilmAgent/ + 仓库根 .gitignore） -> 有改动: ['v2/llm']
stage12: [FAIL] T18-stage10 历史回归全绿 -> TOTAL: 75/76 passed; FAIL: T15b config.py 预期 0 改
stage12: [FAIL] T18-stage11 历史回归全绿 -> FAIL: T17 禁改文件 0 diff ... 有改动: ['v2/llm']
```

★ 关键观察：**stage12 的 T16 只列 `['v2/llm']`** —— 修复复位清单后，stage10/11 的
`test_report.json` 污染已消失，护栏报出的路径**恰好只剩本阶段声明式登记的交付改动**。

### 6.4 历史回归与"pre-commit 禁改护栏"的豁免（★总控裁决 2026-09-15）

历史套件里的「禁改文件 0 diff」类断言用的是 `git diff --stat HEAD -- <path>`，
属 **pre-commit 护栏**；而 Stage 13 依 §8.2 **合法修改**了 `v2/llm/**` 与 `v2/config.py`。
三处会因此变红（详见 `stage13_contract_notes.md` §3.2）：

| 套件 | 断言 | 涉及路径 | 本阶段为何合法 |
|---|---|---|---|
| stage10 | T15b `config.py 预期 0 改` | `v2/config.py` | §8.2 允许（bailian_image 档 + 图像价格表） |
| stage11 | T17 `禁改文件 0 diff` | `v2/llm`（整目录） | §8.2 允许（registry / request_counter / `__init__` / 新增 provider） |
| stage12 | T16 `禁改清单 0 diff` | `v2/llm`（整目录） | 同上 |

**处置（不掩盖真回归）**：`_judge_history()` **只豁免**"仅因 Stage 13 实际改动路径而未过的
禁改护栏"（路径来自**声明式登记**的 `STAGE13_DELIVERABLE_PATHS`，**不从 `git status`
推导** —— 那样会把"回归留下的脏文件"误判成本阶段合法改动），其余任何 FAIL 一律判失败；
被豁免的 FAIL 逐条写入 `test_report.json.excused_history_fails` 供审计。

**commit + tag 之后** HEAD 已含改动 → `diff vs HEAD` 为空 → 三套恢复 76/76、65/65、125/125
（见 §11 的"临时 commit 复验"证据）。

### 6.4.1 ★实测修正：回归重写的产物**不止 6 个文件**

开工指令 §3.2 坑 1 登记的是 6 个文件。实测发现**污染面更大**：各套件 `main()` 都会重写
自己的 `TEST_DIR/test_report.json`，其中 stage10/11/12 的报告含随机 id → 必然 dirty：

```text
v2/logs/stage10_visual_asset/test_report.json
v2/logs/stage11_storyboard/test_report.json
v2/logs/stage12_lifecycle/test_report.json
```

→ 后果：若只复位那 6 个文件，历史套件里的「禁改 0 diff」类断言仍会**假失败**
（首次全量复跑实测：stage13 的 T17a 报 `有改动: ['v2/logs/stage10_visual_asset',
'v2/logs/stage11_storyboard']`）。

**处置**：`_reset_history_products()` 改为**按目录扫 dirty 后精确复位**
（`git status --porcelain --untracked-files=no -- <10 个历史套件目录>` → `git checkout --`），
不再维护容易过时的手写清单；且这些目录全部属禁改清单，其中的 dirty **只可能**来自回归重写，
复位永远正确。→ 该修正已同时登记为技术债 T-6 的"污染面修正"。

修复效果（实测）：修复前 stage12 的 T16 报 `有改动: ['v2/llm', 'v2/logs/stage10_visual_asset', …]`；
修复后**只报 `['v2/llm']`**（见 §6.3）。

### 6.4.2 ★实测：全量复跑耗时 ≈59 分钟（新增技术债 T-13）

T18 内嵌 10 套历史回归，而这些历史套件**自身也在内嵌**：

```text
stage12 → stage1..11；stage11 → stage1..10；stage10 → stage1..9；stage3 → stage1
⇒ 执行次数约 f(13) = 1 + Σf(1..12) ≈ 70 次套件进程
```

实测 **≈59 分钟**（另一次 ≈58 分钟）。这不是缺陷，但意味着：
① 日常回归不能全量跑；② CI 化需先解决嵌套。已登记为技术债 **T-13**（可选方案见 ROADMAP §5）。
本阶段**如实记录实测耗时**，不粉饰。


### 6.5 静态检查与门禁

```text
compileall（v2/orchestration + v2/llm/providers）     全过
import 冒烟 + 导出齐备                                 全过
无新增硬编码密钥字样（与 HEAD 逐行对比）                0 命中
★无硬编码图像模型名（扫具体型号字样）                   0 命中
ASSET_KINDS 精确不变                                   全过
既有 7 个 tag 未移动 / v0.2-stage13 尚未创建            全过
FilmAgent/ 0 diff / 仓库根 .gitignore 0 diff            全过
```

### 6.6 禁改清单实测 0 diff

```text
FilmAgent/                                    0 diff
v2/core/                                      0 diff（含 shot.py / visual*.py / token_log.py）
v2/workflow/                                  0 diff + 0 新增
v2/agents/                                    0 diff
v2/memory/ v2/review/ v2/context/              0 diff
v2/llm/client.py  router.py                    0 diff
v2/llm/providers/base.py mock.py
    openai_compatible.py bailian.py
    openai_compatible_vision.py                0 diff
全部历史测试套件（stage1/3/4/6/7/8/9/10/11/12）  0 diff（断言一个未动）
仓库根 .gitignore                              0 diff
```

---

## 7. 真实出图账目（★本阶段成本最高项）

### 7.1 结论：**未执行**（如实报告阻塞，不伪造）

```text
真实图像调用次数：0
真实图像成本    ：¥0.00（未产生任何计费调用）
真实文本调用次数：0（全程 FILMAGENT_PROVIDER 未设 → mock）
耗时            ：—
```

### 7.2 阻塞原因（`run_real_image.py` 体检输出，四项人工前置均未满足）

```text
① 图像模型名未配置：config.PROVIDERS['bailian_image']['default_model'] 为空，且未传 --image-model
② BAILIAN_API_KEY 未注入（环境变量）
③ 未明确批准（缺 --approve）
④ 图像模型单价未登记在 config.IMAGE_PRICING（未加 --accept-unknown-cost）
+ 文本调用计划 6 次 > --max-text-calls=5 → 脚本**拒绝执行**（不静默超支）
```

脚本退出码 `2`，**未发出任何真实调用**。

### 7.3 成本预估（脚本自动给出，供人工决策）

```text
文本侧（deepseek-chat，粗估 2500 in / 1000 out tokens per call）：
    单次 ≈ ¥0.0165 × 6 次 ≈ ¥0.099（CNY）
图像侧：未登记单价 → **无法预估**（estimated_cost 将记 null）
→ 人工补齐 ①②④ 后重跑体检可得到图像侧预估；确认后再加 --approve 执行。
```

### 7.4 执行方式（人工批准后）

```bash
env -u PYTHONPATH python v2/logs/stage13_image/run_real_image.py \
    --approve --text-vendor deepseek --image-model <具体模型名> \
    --max-images 3 --max-text-calls 8 [--accept-unknown-cost]
```

脚本会：真实跑 **一条完整链**（Casting → Scene → Storyboard → 视觉档案（含 STYLE）→ 出图 ≤3 张），
文本侧**关闭重试**（保证"真实文本调用数 == 计划数"，不被重试放大成本），
落 `real_run_report.json`（实际张数 / 成本 / 耗时 / 成功率）+ `manifest_copy.json`
（结构化证据，无二进制）+ 两本记账，并在事后再次核验上限。

---

## 8. Acceptance Criteria 逐条对照

| AC | 内容 | 结果 | 证据 |
|---|---|---|---|
| AC1 | Stage 13 新套件全绿（T01~T19），历史回归 10 套全绿（stage1=19, 3=38, 4=25, 6=24, 7=29, 8=27, 9=26, 10=76, 11=65, 12=125 全部 rc=0） | ✅ 新套件全绿；历史 10 套**语义全绿**（stage10/11/12 各仅剩 §6.4 的 pre-commit 禁改护栏 1 条，commit 后归零） | `test_report.json`；§6.4；§11 |
| AC2 | V0.1 冻结：`git diff -- FilmAgent/` 为空；全部 tag 未移动 | ✅ | T17d / T17c |
| AC3 | 零侵入：§8.3 禁改清单全部 0 diff；★`v2/workflow/` 0 diff；`v2/core/` 0 diff | ✅ | T17a / T17b；§6.6 |
| AC4 | image 模态开通：mock 可造；★video/audio 仍被拒绝（护栏未被误开） | ✅ | T02a / T02b / T02c |
| AC5 | 端到端成立：编排层能从 Storyboard 跑到"每 Shot 一张图 + 挂回 Shot" | ✅（mock 全链；★真实出图未执行，见 AC12） | T19a~T19e / T05 / T06 |
| AC6 | ★预算护栏生效：超上限的请求被拒且**未发出**（计数与调用数一致） | ✅ | T07b（calls=2）/ T07h（真实 HTTP 层 sent=2）/ T07f |
| AC7 | ★默认安全：`generate_images` 缺省 False 时零调用零文件 | ✅ | T04a~T04d |
| AC8 | ★成本记账：`image_generation_calls.jsonl` 条数与实际调用一致；未登记价格记 null，未用文本价格冒充 | ✅ | T12a / T12b / T12d |
| AC9 | ★二进制防护：图像文件全部被 git 忽略；交付物无二进制泄漏 | ✅ | T15a~T15e |
| AC10 | 溯源完整：manifest 记录 sha256 / profile_versions / reference_images（QA 输入契约就绪） | ✅ | T10a / T10c |
| AC11 | 静态检查全过（compileall / import / 依赖方向 / 密钥扫描 / 无硬编码模型名） | ✅ | T16a~T16e / T01e / T01f |
| AC12 | 真实 API：≤3 张图像 + ≤5 次文本；成本报告实际值；无配置时明确阻塞报告 | ⚠️ **如实报告阻塞**：0 张 / 0 次 / 无成本；四项人工前置未齐，脚本以退出码 2 明确报告，**未伪造** | §7；`run_real_image.py` 体检输出 |
| AC13 | 诚实性：`auto_approve` 标注程序化模拟；**未用占位图冒充真实出图** | ✅ | T09b；§7.1；T11d（缺档案时零图像文件） |
| AC14 | 文档同步：PROJECT_STATUS / HANDOFF / ROADMAP / CLAUDE.md / stage13_report.md | ✅ | 见 §9 |
| AC15 | Git：工作区无泄漏（含二进制）；commit 后 tag 由人工批准创建 | ✅ 工作区无泄漏（T15）；★**未 commit、未 tag**（等人工批准） | §10 |

**AC12 说明（诚实边界）**：开工指令 §13 明确"若无可用的真实配置：明确报告
'阻塞：需人工配置'，Mock 路径照常交付"。本次正是该情形，且**没有**用任何占位图 /
纯色块 / mock 结果冒充真实生成（铁律 11 / §7.3-7）。

---

## 9. 文档同步（AC14）

| 文档 | 更新内容 |
|---|---|
| `CLAUDE.md` | 进度行（Stage 13 已交付待验收）；新增"阶段 13 图像特有约定"（三重护栏 / prompt 唯一来源 / STYLE 硬前置 / 引用串契约 / 三本账 / 落盘纪律 / resume / fail-loud / 二进制防护）；架构表 llm 与 orchestration 两行；测试命令与日常回归策略；"当前状态与下一步"改写（含 Stage 13 遗留与 Stage 14 入口） |
| `v2/docs/PROJECT_STATUS.md` | §2 阶段表加 Stage 13；§3 标题改「历史 Stage 0–13」+ 加 Stage 13 里程碑行 + 加 Stage 13 分段记录；§5.3 Provider 能力矩阵（Image 从"仅预留"→"已开通，真实调用未执行"）；★新增 §5.7.1「图像链路」（段顺序 / 模块表 / 三重护栏 / 记账边界 / resume / STYLE 前置）；§5.8 百炼档位补 `bailian_image`；§6 已知问题（Image 行改写 + 新增「Stage 13 遗留」块：T-8~T-13、实测修正、run_real_lifecycle 并入）；§7 下一阶段改写为 Stage 14（含 QA 输入契约） |
| `v2/docs/HANDOFF.md` | §1 当前状态（Stage 13 交付要点 + 真实出图未执行）；§2 Git 锚点加 Stage 13 行 + ★预期工作区改动清单（人工验收逐项核对）；§4 改为"Stage 14 如何开始"（含新开工自检命令）；§5 新增 Stage 13 编排约定 9 条 + Stage 14 输入契约 + T-7 二进制防护提醒 |
| `v2/docs/ROADMAP.md` | §2 全景（Stage 13 移入"已完成"）；§4 当前格位（Stage 13 交付 + 4 条遗留）；§5 技术债新增 T-7~T-12、T-6 状态更新为"已缓解"；§6 未实现能力清单（真实图片生成 → 代码已实现/真实调用未执行）；§7 成本表加 Stage 13 行 + 图像计费入口说明 |
| `v2/logs/stage13_image/stage13_report.md` | 本文件 |
| `v2/logs/stage13_image/stage13_contract_notes.md` | A / A2 契约笔记 |

---

## 10. Git 状态（§16）

```text
★ 未自动 commit、未自动 tag（铁律 4 / §16）。

预期工作区改动（人工验收逐项核对，不得多）：
  M  v2/.gitignore
  M  v2/config.py
  M  v2/llm/__init__.py
  M  v2/llm/providers/__init__.py
  M  v2/llm/providers/registry.py
  M  v2/llm/request_counter.py
  M  v2/orchestration/__init__.py
  M  v2/orchestration/pipeline.py
  M  v2/orchestration/state.py
  ?? v2/llm/providers/mock_image.py
  ?? v2/llm/providers/openai_compatible_image.py
  ?? v2/orchestration/image_stage.py
  ?? v2/logs/stage13_image/

★ 不得出现：*.png / *.jpg / *.jpeg / *.webp / images/ / generated_images/ /
             *.jsonl 记账 / llm_calls.jsonl / tmp_*
```

人工批准后的命令（**不自动执行**）：

```bash
git add CLAUDE.md v2/ && git commit -m "feat(v0.2): image generation (stage13)"
git tag v0.2-stage13
```

★ 文档措辞铁律（ORCHESTRATION §5.2）：永久文档**不写"待 commit"**，
写阶段结果 + 用 `git rev-parse v0.2-stage13` 查 hash，不写死 hash。

---

## 11. 最终实测

> 本节由**最后一次全量复跑**填入（执行时间 2026-09-15 23:12 → 2026-09-16 00:11，耗时 ≈59 分钟）。

```text
$ env -u PYTHONPATH python v2/logs/stage13_image/run_tests.py
...
------------------------------------------------------------------------
TOTAL: 126/126 passed
report -> D:\AI\FilmAgent-main\v2\logs\stage13_image\test_report.json
RC=0

统计：
  顶层 [PASS] 126 条
  顶层 [FAIL] 0 条
  real_api_requests = 0
  豁免（excused_history_fails）= 6 条（全部为 §6.4 的 pre-commit 禁改护栏）
  T18 历史回归 10 套：stage1 / 3 / 4 / 6 / 7 / 8 / 9 全绿（rc=0）；
                     stage10 = 75/76、stage11 = 64/65、stage12 = 124/125
                     （各仅剩 1 条 pre-commit 禁改护栏，见 §6.4）
```

### 11.1 交付前工作区自检（人工验收前）

```text
$ git status --short
 M CLAUDE.md
 M v2/.gitignore
 M v2/config.py
 M v2/docs/HANDOFF.md
 M v2/docs/PROJECT_STATUS.md
 M v2/docs/ROADMAP.md
 M v2/llm/__init__.py
 M v2/llm/providers/__init__.py
 M v2/llm/providers/registry.py
 M v2/llm/request_counter.py
 M v2/orchestration/__init__.py
 M v2/orchestration/pipeline.py
 M v2/orchestration/state.py
?? v2/llm/providers/mock_image.py
?? v2/llm/providers/openai_compatible_image.py
?? v2/logs/stage13_image/
?? v2/orchestration/image_stage.py
→ 与 §10 的"预期改动"逐项一致，无多余文件。

$ git diff -- FilmAgent/            → 空（V0.1 冻结）
$ git diff -- .gitignore            → 空（仓库根 .gitignore 0 改）
$ git ls-files v2 | grep -cE '\.(png|jpg|jpeg|webp|gif)$'   → 0（无二进制入库）
$ ls llm_calls.jsonl v2/llm_calls.jsonl                     → 不存在（无记账泄漏）
$ ls v2/projects/                                            → 空（未写真实项目目录）
$ git tag -l                                                 → 7 个 tag，v0.2-stage13 尚未创建
$ find v2/logs/stage13_image -name '*.png' -o -name '*.jpg'  → 0（测试图片已随 tmp_projects 清理）
```

### 11.2 关于"commit 后历史套件恢复满分"的证据说明（★诚实边界）

本阶段**未做任何 commit**（铁律 4 / §16：不自动 commit、不自动 tag），因此无法在本会话内
直接观测"commit 后 stage10/11/12 恢复 76/76、65/65、125/125"。此处给出的证据是：

1. **机制层面**：那 3 条护栏的判据是 `git diff --stat HEAD -- <path>` 为空；
   Stage 13 的改动一旦进入 HEAD，该 diff **按定义为空** → 护栏必然通过；
2. **范围层面**：已逐条核对该 3 套的禁改清单，确认**除上述 3 条护栏外没有任何其它断言**
   会因本阶段改动而失败（详见 `stage13_contract_notes.md` §3.4 的"附带确认"表）；
3. **实测层面**：`test_report.json.excused_history_fails` 逐条登记了被豁免的 FAIL，
   其报出的路径**恰好只有** `v2/llm` 与 `v2/config.py`（本阶段声明式登记的交付改动）。

→ 建议总控在人工批准 `commit + tag v0.2-stage13` 后复跑
`env -u PYTHONPATH python v2/logs/stage12_lifecycle/run_tests.py` 确认 125/125（此为最终裁决依据）。

---

## 12. 遗留与技术债（详见 ROADMAP §5）

| # | 项 | 影响 | 处置 |
|---|---|---|---|
| T-7 | ★图像是二进制大文件 | 单次误 `git add` 即污染版本库 | **已落地防护**：§8.4 六条规则 + 套件 T15 扫描 |
| T-8 | 单 run 出图上限固定 `min(max_images, 3)`（`max_images` 只能调小） | 需要一次出图 >3 张时须改常量 | 已登记（安全默认，非缺陷）；run_report 同时暴露 `max_images_requested` 与 `budget_cap` |
| T-9 | ★目标厂商是否支持 OpenAI `/images/generations` **未能从公开文档核实** | 首次真实出图可能因协议差异失败 | 首次真实调用前人工确认；差异过大 → 注入厂商 transport（不动架构） |
| T-10 | `config.IMAGE_PRICING` 空表 → 图像成本记 `null` | 真实出图无成本数字 | 人工登记所选模型单价即可（护栏，非缺陷） |
| T-11 | `manifest.json` 因 `v2/.gitignore` 的 `projects/` 整目录规则**不入库**（与 §8.4「manifest 保留为结构化证据」表述有张力） | 真实运行 manifest 不作为仓库证据留存 | 真实冒烟脚本已把**副本（无二进制）** 落到 `v2/logs/stage13_image/real_run/manifest_copy.json` |
| T-12 | 图像审核遇 `approved_with_edit` 直接抛错 | 无法对生成图做"编辑后通过" | 图像编辑未立项（§4.4）；fail-loud 优于静默猜 |
| T-6 | 6 个历史报告产物被回归重写 | 交付前需复位 | ★**Stage 13 已缓解**：套件自动复位（按 `git status` 扫历史套件目录后精确 `git checkout --`） |
| T-13 | ★**历史回归嵌套成本呈指数级**（stage13 的 T18 内嵌 10 套，而各套件自身也在内嵌） | 一次全量复跑实测 **≈58 分钟**（约 70 次套件执行）；CI 化不可行 | 已登记，未修（可选方案见 ROADMAP §5） |

### 12.1 裁决点登记（本阶段新增，供总控复核）

| # | 裁决点 | 总控裁决 | 落地 |
|---|---|---|---|
| A-1 | stage10 T11b 的「生成类」断言 vs 开通 image | **保留措辞**（真实档未配置时报错自然含「生成类」） | `_create_image_provider` 报错措辞；stage10 保持 76/76 |
| A-2 | 历史套件「禁改 0 diff」护栏 vs 本阶段合法改 `v2/llm` + `v2/config.py` | **按 pre-commit 护栏理解** | `_judge_history()` 只豁免"仅因本阶段改动路径"的护栏，逐条留痕 |
| A-3 | STYLE 视觉档案缺口（编排层从不建 STYLE，但 Composer 强制要求） | **扩展 visual_assets 段支持 style**（`run_spec["style_spec"]`） | `_style_entity()` + `_semantic_data(kind="style")`；未提供时计数与 Stage 12 逐值一致 |
| A-4 | `cap = min(max_images, MAX_REAL_IMAGE_REQUESTS)` 使 `max_images` 只能调小 | 采保守读法（安全默认） | 已登记为 T-8，并在 run_report 暴露两个数值 |
| A-5 | manifest 因 `projects/` 整目录忽略而不入库 | 用"副本落 `logs/`"作为入库证据 | 已登记为 T-11 |

---

## 13. 完成后整个项目发生什么变化（§二十 对照）

```text
之前：整条链能跑到"分镜齐备 + 视觉资产齐备"，但一张图都没有
之后：每个 Shot 能经 VisualComposer 组装 → 生成图像 → 版本化落盘 →
      挂回 Shot；全程受预算护栏与成本记账约束；manifest 为 QA 备好溯源锚点

打通的关键链路：
  Storyboard → ComposedVisualInput → ImageGenerationProvider → 图像资产 → Shot
       ↑ Stage 13 让这条链第一次真正具备"产出画面"的能力（Mock 已端到端验证）

意义：
  1. V0.2 第一次具备**真实媒体资产**的产出能力（不再只是结构化数据）；
  2. 图像模态开通后，Stage 14（VLM QA）与 Stage 15（Video）都只需在既有挂点上扩展；
  3. 成本护栏就位 —— 后续任何真实生成类阶段都复用同一套预算/记账机制。
```

---

---

## 附：Stage 13-P 补丁（2026-09-18，总控追加）

> 本节由**总控**在封存后追加，记录 `v0.2-stage13` 之后的增量补丁。
> **tag 未移动**（`ORCHESTRATION.md` §5.2 铁律）。

- **T-9 已核实并解决**：百炼**没有** OpenAI 兼容图像端点；其原生协议为 DashScope 形状
  （千问系同步 / 万相系异步 `X-DashScope-Async` + `task_id` 轮询，返回 URL；
  `size` 用 `宽*高`，与 canonical 记法**一致**）。
  → 新增 `v2/llm/providers/bailian_image.py`（`protocol="dashscope_native"`），
  与既有 OpenAI 兼容档**并存、可切换**（用户明确要求"只增不改、换回来不影响"）。
- **预算语义定案**：`_invoke()` 包住整次业务尝试（含内部 N 次轮询）
  ⇒ **一张图 = 1 次计费调用，轮询不额外消耗预算**（T20k 断言）。
- **套件变更**：新增 **T20a~T20q（17 条）**；**修 T17c** —— 原实现把
  `"v0.2-stage13" not in tags` 写死在**顶层**套件里，属 **pre-commit 瞬时断言**，
  封存后**必然永久变红**（豁免机制只作用于嵌套历史套件，管不到它）→ 改为**状态感知**。
  故本套件总数由 126 变为 **143**（§6.3 的 `126/126` 是 Stage 13 封存时的实测值，**保留不改**）。
- **文档更正**：§6.4 原写豁免路径"从 `git status` 推导"与代码不符 ——
  实际是**声明式登记** `STAGE13_DELIVERABLE_PATHS`（**不从 `git status` 推导**，避免循环论证），已更正。
- 完整记录见 `v2/logs/stage13_image/stage13p_native_provider.md`。

---

*Stage 13 阶段报告 —— 由执行会话（A→D 全流程）产出；commit / tag 等人工批准。*
*附节（Stage 13-P）由总控追加。*
