# Stage 14-D —— VLM Image QA 文档封存与交付准备指令

> 会话角色：Stage 14-D 独立文档、交付与 Git 准备会话
> 日期：2026-10-02
> 工作目录：`D:\AI\FilmAgent-main`
> 基线 HEAD：`1a7404942edb6e493091a449256da5a75d456ccc`
> Stage 13 tag：`v0.2-stage13` → `3895c6c103f125d458fe648d80a625bf007601af`，禁止移动
> 真实 API / 网络 / 模型切换：必须保持 0
> 测试：本会话不重新执行 Stage 14 或 Stage 13 套件
> Git：只准备交付清单；不 commit、不创建或移动 tag

---

## 1. 会话目标

Stage 14-A 已完成契约分析，Stage 14-B 已完成实现，Stage 14-C 已完成正式测试与最小修复。
本会话负责把已验证事实写入仓库长期知识，生成完整 Stage 14 报告，核对交付边界，并将项目
整理为可由总控进行最终静态验收的状态。

本会话不再开发功能。完成后必须使一个不了解此前聊天记录的新会话，仅阅读项目文档、源码、
测试报告和 Git 状态，就能准确回答：

1. Stage 14 为什么存在、实现了什么、采用了哪些关键契约；
2. 哪些测试通过，哪些历史回归未以 rc=0 结束，以及原因；
3. 哪些事实已经验证，哪些真实服务能力仍未验证；
4. 哪些图片允许进入 Stage 15；
5. Stage 14 应包含哪些文件，冻结边界是否保持；
6. commit/tag 应在什么条件下由谁批准。

最终状态只能写为：

```text
READY_FOR_ORCHESTRATOR_ACCEPTANCE
```

不得由执行会话自行宣布 Stage 14 已封存、已 commit 或已打 tag。

---

## 2. 为什么需要 Stage 14-D

Stage 14-C 的测试结果目前主要存在于机器报告和会话交接中。若不进行 D：

- `PROJECT_STATUS.md` 与 `HANDOFF.md` 仍会把 Stage 14-C 写成下一步；
- `ROADMAP.md` 与 `CLAUDE.md` 仍会把 VLM QA 写成未实现；
- Stage 14 的已验证能力、限制与 Stage 15 准入门无法长期恢复；
- 历史回归的两项 pre-commit 护栏失败可能被误写成“全绿”或误判为功能回归；
- 真实多图 VLM 连通性可能被错误描述为已经验证。

因此本阶段只整理事实、文档和交付边界，不改变已测试的代码状态。

---

## 3. 开工前必须阅读

按顺序阅读：

1. `CLAUDE.md`
2. `v2/docs/PROJECT_STATUS.md`
3. `v2/docs/HANDOFF.md`
4. `v2/docs/ROADMAP.md`
5. `v2/docs/ORCHESTRATION.md`
6. `v2/docs/stages/Stage_14.md`
7. `v2/docs/stages/Stage_14B.md`
8. `v2/docs/stages/Stage_14C.md`
9. `v2/logs/stage14_image_qa/stage14_contract_notes.md`
10. `v2/logs/stage14_image_qa/test_report.json`
11. `v2/agents/image_qa.py`
12. `v2/orchestration/image_qa_stage.py`
13. `v2/orchestration/pipeline.py` 的 Stage 14 接线
14. `v2/prompts/v0.2/image_qa.txt`
15. `v2/logs/stage14_image_qa/run_real_image_qa.py`

Stage 14-A/B/C 的聊天记录不是项目知识来源。本文件第 4 节是总控根据 Stage 14-C 交接与静态
检查固化的验收事实，D 会话必须将其准确写入阶段报告。

---

## 4. Stage 14-C 已确认事实

### 4.1 正式测试

权威机器报告：

```text
v2/logs/stage14_image_qa/test_report.json
```

结果：

```text
Stage 14-C：198 / 198 PASS，rc=0
T01 12/12   T02 23/23   T03 22/22   T04 12/12
T05 17/17   T06  7/7    T07 21/21   T08 17/17
T09 12/12   T10 19/19   T11 12/12   T12 11/11
T13 13/13

real_api_requests = 0
network_requests  = 0
model_switches    = 0
failures          = []
```

不得把 198 条写成包含历史 Stage 13 套件的总数；它只代表 Stage 14 正式套件。

### 4.2 Stage 14-C 修复

三个已证明缺陷均只修改了 `v2/orchestration/image_qa_stage.py`：

1. QA index 重复 `image_id` 原先可能静默覆盖；现改为 fail-loud。
2. resume 原先可能直接信任 index 的 `downstream_eligible`；现按 Review approved-like 与
   本地重算 `qa_decision == pass` 复算，不一致即 stale。
3. `profile_versions` 插入顺序原先不会使 resume stale；现按有序 `(asset_id, version)`
   列表比较，因为顺序会影响 checks 与 Prompt 语义。

### 4.3 历史回归

Stage 13 套件的实际结果必须原样记录：

```text
命令：python v2/logs/stage13_image/run_tests.py
结果：144 / 146 PASS，rc=1，耗时约 3572 秒（约 59.5 分钟）
失败：T17a、T18-stage12
```

这两项均为 pre-commit 工作区清洁度护栏：Stage 14 合法修改了
`v2/agents/__init__.py`，所以相对当前 HEAD 的检查必然看到
`v2/agents/__init__.py | 3 +++`。现有证据未显示 Stage 13 功能回归。

必须使用以下准确措辞：

```text
Stage 14 套件全绿。Stage 13 历史套件功能项通过，但整套 rc=1；
两个失败均是合法未提交 Stage 14 变更触发的 pre-commit 清洁度护栏。
```

禁止写成“Stage 13 146/146”“全部回归 rc=0”或笼统的“历史回归全绿”。

`test_report.json` 当前 `regressions` 数组为空，未承载上述历史回归详情。不得伪造或回填机器
测试结果；应在 `stage14_report.md` 明确记录此报告覆盖边界与本节的交付证据。

### 4.4 真实服务边界

- Stage 14-C 未运行任何真实 API、网络请求或模型切换。
- `run_real_image_qa.py` 已验证默认拒绝路径，默认 `rc=2`、0 调用。
- 该脚本当前是授权与参数的 preflight/guard scaffold：即使所有前置满足，也只打印
  `PRECONDITIONS SATISFIED` 并返回 `rc=3`，不会真正构造 Provider 或发起 VLM 请求。
- 脚本针对 `bailian` 检查 `DASHSCOPE_API_KEY`，而项目
  `PROVIDERS["bailian_vision"]` 使用 `BAILIAN_API_KEY`。在未来真实冒烟实现前必须统一，
  不能把当前脚本描述为可直接运行的真实冒烟入口。
- 因此“生成图 + 多张参考图”的真实多模态 Provider 连通性仍未验证。

这不阻塞 Stage 14 本地功能交付，因为主 Stage 验收允许在未获用户真实调用授权时如实登记
未验证；但它必须进入限制/开放问题，不能标成 PASS 证据。

---

## 5. 本阶段架构位置与完成后的项目状态

Stage 14 位于 Stage 13 Image Generation 与 Stage 15 Video Generation 之间：

```text
Stage 13 generated image + manifest provenance
    ↓
Stage 14 preflight（路径、SHA256、精确版本、reference 全局唯一解析）
    ↓
ImageQAStep / MultimodalProvider
    ↓
严格 Schema 校验 + 本地确定性 qa_decision
    ↓
ArtifactStore + Review + quality/qa_manifest.json
    ↓
Stage 15 准入门
```

Stage 15 只能消费同时满足以下条件的记录：

```text
Review decision ∈ approved-like
AND
qa_decision == "pass"
AND
resume 时所有 provenance / Artifact / provider / model 锚点仍有效
```

`downstream_eligible` 是可重算的派生值，不是可信源；下游不得仅凭该布尔值绕过上述条件。

---

## 6. 输入、输出、数据结构与接口

### 6.1 输入

- Stage 13 `generated_images/manifest.json`；
- 生成图文件及 SHA256；
- manifest 精确 `profile_versions`；
- manifest 顺序的 `reference_images`；
- Visual Registry 中对应 profile/reference 版本；
- 明确配置的 VLM provider/model；
- 既有 Workflow、ArtifactStore、Review 和 vision 请求预算。

### 6.2 输出

- 每张图独立、版本化的 QA Artifact；
- `quality/qa_manifest.json` 审计/resume index；
- pipeline `image_qa` run report；
- Stage 14 正式测试报告；
- 本 D 会话新增的 `stage14_report.md` 与更新后的长期项目文档。

### 6.3 核心接口

- `ImageQAAgent` / `ImageQAStep`；
- `ImageQAStage`；
- pipeline 默认关闭的 `run_image_qa` 接线；
- `validate_artifact()` 严格校验与本地裁决；
- D14-REF-01：全 Registry 扫描 `image_id`，要求恰好一次命中。

本阶段不新增公共数据结构或公共 Provider 接口。

---

## 7. 必须完成的子任务

### D1：建立交付前静态快照

只读执行并记录摘要：

```powershell
git status --short
git diff --name-status
git diff --stat
git diff --check
git rev-parse HEAD
git rev-parse v0.2-stage13^{commit}
```

还要静态核对：

- `test_report.json` 可解析，198/198、0/0/0、failures 为空；
- Stage 14-C 三处修复仍存在；
- `FilmAgent/`、冻结目录和禁改接口 0 diff；
- `v0.2-stage13` 仍指向固定 commit。

不得运行 `run_tests.py`、历史回归、真实冒烟或任何联网命令。

### D2：新增 Stage 14 阶段报告

创建：

```text
v2/logs/stage14_image_qa/stage14_report.md
```

至少包含：

1. 背景、目标和范围；
2. A/B/C/D 分工与每段交付；
3. 架构与端到端数据流；
4. D14-REF-01 决策及理由；
5. 精确版本、路径/SHA、多图顺序、严格 Schema、本地裁决；
6. Workflow/Artifact/Review、resume、预算、pipeline 默认关闭；
7. 14-C 的 198/198 分组结果；
8. 三个缺陷与修复；
9. Stage 13 历史回归 144/146、rc=1 的准确解释；
10. 冻结边界和零调用证据；
11. 真实连通性与冒烟脚本的限制；
12. Stage 15 准入契约；
13. 交付文件清单；
14. Git 状态、建议 commit message/tag，但明确未执行；
15. 剩余风险和开放问题。

### D3：更新长期项目知识

更新以下文件：

```text
CLAUDE.md
v2/docs/PROJECT_STATUS.md
v2/docs/HANDOFF.md
v2/docs/ROADMAP.md
v2/docs/stages/Stage_14.md
v2/logs/stage14_image_qa/stage14_contract_notes.md
```

更新要求：

- 把 Stage 14 从“未实现/进行中”更新为“实现与正式测试已完成，等待总控最终验收和 Git 封存”；
- 把下一执行动作更新为“总控验收 Stage 14-D”，不得提前写 Stage 15 已开工；
- 在架构图、能力矩阵和数据流中加入 Image QA；
- 固化 D14-REF-01 与 Stage 15 准入条件；
- 记录真实 VLM 连通性未验证；
- 记录 198/198 与历史回归的真实边界；
- 保留已有 Stage 13-P、模型授权、回收站及其他用户规则，不覆盖并发文档改动；
- `Stage_14.md` 只追加实际交付/测试状态，不重写原设计契约；
- `stage14_contract_notes.md` 追加 D14-REF-01 已实现且被 T04 验证的闭环结论。

### D4：交付物与泄漏审计

检查全部 Stage 14 改动及未跟踪文件，确保不包含：

```text
真实图片（png/jpg/jpeg/webp 等）
JSONL 调用账本
API key / token / Authorization header
base64 图片正文
绝对用户临时路径
__pycache__ / *.pyc
tmp_* / 临时项目目录
console.txt 等 gitignored 运行日志
```

允许保留并纳入交付：

```text
v2/logs/stage14_image_qa/run_tests.py
v2/logs/stage14_image_qa/run_real_image_qa.py
v2/logs/stage14_image_qa/test_report.json
v2/logs/stage14_image_qa/stage14_contract_notes.md
v2/logs/stage14_image_qa/stage14_report.md
```

`run_real_image_qa.py` 必须在报告里标为 preflight/guard scaffold，不得称为已连通或可直接发送
真实请求的脚本。

### D5：准备 Git 交付清单

输出精确的 intended file list，区分：

1. Stage 14 业务实现；
2. Stage 14 测试与报告；
3. Stage 14 文档；
4. Stage 14 开工前已有、需要保留的用户/总控文档改动。

建议但不得执行：

```text
commit message: feat(v0.2): add VLM image QA (stage14)
tag: v0.2-stage14
```

tag 只能在总控验收通过、用户明确批准后创建；任何既有 tag 永不移动、永不 force。

---

## 8. 任务依赖、顺序与并行性

执行顺序：

```text
D1 静态快照
  ↓
D2 阶段报告
  ↓
D3 长期文档同步
  ↓
D4 泄漏/边界审计
  ↓
D5 Git 交付清单
  ↓
返回总控验收
```

可以并行阅读文档和源码，但文件写入必须按上面顺序串行，避免阶段报告与长期文档出现不同
结论。最终审计必须在全部文档保存后进行。

---

## 9. 允许修改与禁止修改

### 9.1 允许新增

```text
v2/logs/stage14_image_qa/stage14_report.md
```

### 9.2 允许修改

```text
CLAUDE.md
v2/docs/PROJECT_STATUS.md
v2/docs/HANDOFF.md
v2/docs/ROADMAP.md
v2/docs/stages/Stage_14.md
v2/logs/stage14_image_qa/stage14_contract_notes.md
```

仅允许文档同步。必须保留这些文件中开工前已有的全部非 Stage 14 改动。

### 9.3 禁止修改

```text
FilmAgent/ 全目录
v2/core/ 全目录
v2/workflow/ 全目录
v2/review/ 全目录
v2/memory/ 全目录
v2/context/ 全目录
v2/agents/ 全目录
v2/orchestration/ 全目录
v2/llm/ 全目录
v2/prompts/ 全目录
v2/config.py
v2/logs/stage14_image_qa/run_tests.py
v2/logs/stage14_image_qa/test_report.json
v2/logs/stage14_image_qa/run_real_image_qa.py
历史测试、报告和断言
仓库根 .gitignore
```

不得为了让工作区看起来干净而 reset、checkout、clean、stash 或删除既有改动。

---

## 10. 测试与验收方式

### 10.1 本会话测试

N/A：用户与总控已明确要求不重复运行 Stage 14/Stage 13 测试。

### 10.2 本会话允许的验证

仅做静态验证：

- JSON 可解析与字段/计数核对；
- Markdown 路径、阶段状态和数字一致性检查；
- Git diff、diff-check、tag、冻结边界检查；
- 敏感信息与二进制/缓存/临时文件扫描；
- 对三处 C 修复做源码存在性核对。

不得把静态检查写成“重新验证 198 条测试”。正式测试证据来自 Stage 14-C 的既有报告。

### 10.3 回归测试

N/A：本会话不修改运行时代码，且用户明确要求不重跑。历史回归结论按 §4.3 如实记录。

---

## 11. Acceptance Criteria

1. `stage14_report.md` 存在并覆盖 §7 D2 的全部内容。
2. 六份长期文档状态一致，没有继续把 Stage 14-C 写成下一执行单元。
3. Stage 14 状态写为“等待总控验收/Git 封存”，没有提前宣布最终封存。
4. 198/198、各组计数、0 API/网络/模型切换写法与机器报告一致。
5. Stage 13 历史回归明确写为 144/146、rc=1，并准确解释两项清洁度护栏。
6. 三个 C 修复、D14-REF-01、本地确定性裁决、resume 和 Stage 15 准入条件已固化。
7. 真实多图 VLM 连通性写为未验证；冒烟脚本写为 preflight/guard scaffold。
8. 报告记录百炼密钥环境变量不一致这一开放问题，但不在 D 会话改代码。
9. `FilmAgent/`、冻结目录、业务源码、测试报告与既有 tag 没有被 D 会话修改。
10. `git diff --check` 通过；仅换行警告不算失败，但应在交付中说明。
11. 无密钥、base64、绝对临时路径、图片、JSONL、缓存、临时目录或 console 日志泄漏。
12. 未运行测试、真实 API、网络请求或模型切换。
13. 未 commit、未 tag、未移动任何历史 tag。
14. 最终状态为 `READY_FOR_ORCHESTRATOR_ACCEPTANCE`。

任一项未满足，不得声称 Stage 14-D 完成。

---

## 12. 异常处理与架构问题

- 若发现文档与代码事实冲突，先引用文件/行号和证据，再按已验证实现修正文档。
- 若发现 Stage 14 业务缺陷，不得在 D 会话修代码；记录最小复现与影响，状态改为
  `BLOCKED_PENDING_FIX` 并上报总控。
- 若发现机器报告损坏或计数不一致，不得手改成 PASS；记录差异并停止完成态交付。
- 若冻结边界出现无法解释的改动，不得清理或回退；列出路径、来源判断和建议处理方式。
- 若文档存在并发改动，逐段合并，禁止整文件覆盖。
- 若发现模型/provider 需要真实验证，不得自行调用或切换。必须向用户说明精确 provider、
  精确 model、免费额度/成本是否确认、最大调用次数，获得明确授权后另开任务。

---

## 13. 最终交付物

必须返回给总控：

1. `stage14_report.md` 路径与摘要；
2. 已更新的长期文档清单；
3. Stage 14 的端到端能力与 Stage 15 准入条件；
4. 198/198 正式测试摘要；
5. Stage 13 144/146、rc=1 的准确说明；
6. 三个已修复缺陷；
7. 真实 VLM 未验证、冒烟 scaffold 和密钥变量不一致等限制；
8. 当前 `git status --short`、`git diff --stat`、`git diff --check`；
9. 冻结边界、泄漏扫描、HEAD 与 `v0.2-stage13` 结果；
10. intended commit file list、建议 commit message 与 tag；
11. 明确声明：未重跑测试、未运行真实 API、未联网、未切换模型、未 commit、未 tag；
12. 最终状态：`READY_FOR_ORCHESTRATOR_ACCEPTANCE` 或带证据的阻塞状态。

---

## 14. 给执行会话的直接开工指令

```text
你现在执行 FilmAgent V0.2 的 Stage 14-D。

工作目录：D:\AI\FilmAgent-main
唯一任务规格：v2/docs/stages/Stage_14D.md

严格按该文件完成静态快照、stage14_report.md、长期文档同步、泄漏/冻结边界审计和
Git 交付清单。不要重新运行 Stage 14 或 Stage 13 测试，不要运行真实冒烟，不要访问网络，
不要修改任何业务源码、Prompt、测试或机器报告，不要 commit，不要 tag。

Stage 14-C 的权威结果是 198/198、rc=0、真实 API/网络/模型切换均为 0。Stage 13 历史
套件实际为 144/146、rc=1；两个失败是合法 Stage 14 未提交改动触发的 pre-commit 清洁度
护栏，必须如实写入，禁止描述成 146/146 或全部回归 rc=0。

真实多图 VLM 连通性仍未验证。run_real_image_qa.py 当前只是 preflight/guard scaffold，
且 bailian key 环境变量与项目配置不一致；只记录限制，不在本会话修代码。

完成后以 READY_FOR_ORCHESTRATOR_ACCEPTANCE 返回总控，并附全部静态证据和 intended
commit file list。
```
