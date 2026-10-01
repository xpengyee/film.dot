# Stage 13-P —— 百炼 DashScope 原生档（补丁记录）

> 2026-09-28 修复执行完成；2026-09-29 总控静态复核，**Stage 13-P：PASS**。
> `base_url` 统一表示已含 `/api/v1` 的 API base，默认 path 改为
> `/services/...` / `/tasks/...`；T20a–T20t 20/20，Stage 13 全量回归 146/146、rc=0、
> 真实 API 请求 0。修复与复验规格：`v2/docs/stages/Stage_13P_fix.md`。

> 性质：**Stage 13 的增量补丁**（不是新阶段）。`v0.2-stage13` tag **未移动**。
> 触发：T-9 核实结论 —— 百炼图像生成**不在** OpenAI 兼容模式里。
> 用户指令（2026-09-18）：**"注意要保留兼容的这些，比如火山、openai 等，现在是额外写一个，
> 如果后面换回来还可以不影响。"** ⇒ 全程遵守 **只增不改**。

---

## 1. 为什么要它（T-9 核实结论，2026-09-18）

`stage13_report.md` §12 原登记 T-9 为"**未能从公开文档核实**目标厂商是否支持 OpenAI
`POST /images/generations`"。**总控独立检索官方文档后已核实并定论**：

URL 语义只有一种：`base_url = https://<host>/api/v1`，Provider path 是该 base 下的
`/services/...` 或 `/tasks/...`。这与官方的 [DashScope API Reference](https://help.aliyun.com/en/model-studio/qwen-api-via-dashscope)
及 [异步图像任务调用说明](https://help.aliyun.com/en/model-studio/first-call-to-image-and-video-api)
一致，且同时适用公共域与业务空间域。

| 项 | 官方原文事实 |
|---|---|
| 端点 | **DashScope 原生**，**没有** OpenAI 兼容的 `/images/generations` |
| 同步（千问系） | `POST {base}/services/aigc/multimodal-generation/generation` |
| 异步（万相系） | `POST {base}/services/aigc/image-generation/generation` + header `X-DashScope-Async: enable` → `output.task_id` → 轮询 `GET {base}/tasks/{task_id}` |
| 请求体（同形） | `{"model":…, "input":{"messages":[{"role":"user","content":[{"text":<prompt>}]}]}, "parameters":{…}}` |
| 响应（同形） | 图片 URL 路径 **同步/异步一致**：`output.choices[0].message.content[0].image` |
| **size 记法** | `parameters.size` = `"宽*高"`（如 `1024*1024`）—— **与本项目 canonical 记法完全一致** |
| 水印 | `parameters.watermark` 默认 **false**（无角标） |
| 其它可传 | `parameters.seed` / `n` / `prompt_extend`；顶层 `negative_prompt`（万相 2.7-pro/image 不支持） |
| 鉴权 | `Authorization: Bearer $DASHSCOPE_API_KEY` |

> **重要**：百炼**能**出图（通义万相 / 千问系），**不是能力问题，只是协议形状不同**。

## 2. 落地内容（★全部为新增或纯追加；原 `bailian_image` 档一字未动）

| 文件 | 改动 |
|---|---|
| `v2/llm/providers/bailian_image.py` | **新增** `BailianImageProvider`：同步 + 异步（创建→轮询→终态）；纯 stdlib `urllib`（**无新依赖**）；`transport` 可注入 → 单测 0 网络；响应解析宽容（官方主路径 + `results[].url` / `url` / `image` 兜底）；★**多图明确报错、绝不静默丢图**；usage 回传；默认 path 按 `base_url=/api/v1` 语义使用 `/services/...` / `/tasks/...` |
| `v2/llm/providers/registry.py` | 纯追加：`_create_image_provider` 按 `conf["protocol"]` 分派。**缺省（不写该键）= `"openai"` → 行为与 Stage 13 完全一致** |
| `v2/llm/providers/__init__.py` | 纯追加导出 `BailianImageProvider` |
| `v2/config.py` | 纯追加：`ark_image` / `openai_image`（OpenAI Images 兼容档）+ `bailian_native_image`（原生档，`protocol="dashscope_native"`、`size_separator="*"`、`async` 开关）；`PROVIDER_ENV_API_KEYS` 补 3 个 vendor |
| `v2/logs/stage13_image/run_tests.py` | 加 **T20a~T20t** 常驻测试组；T20r–T20t 拦截最终 `_request` URL；**修 T17c**（见 §4） |

## 3. 两档并存 —— 怎么切换

```text
protocol 缺省 / "openai"      → OpenAICompatibleImageProvider
                                 （火山方舟 / OpenAI / 硅基流动 / 任何 OpenAI Images 兼容端点）
protocol = "dashscope_native" → BailianImageProvider（百炼 DashScope 原生，同步/异步）
```

编排层完全无感（它只调 `generate_image(prompt, size, reference)`）。
**换回来只改 Config，代码 0 行改动。** 已由 T20n / T20p / T20q 断言守护。

配置示例（模型名一律由人工填，**绝不硬编码**）：

```python
PROVIDERS["bailian_native_image"] = {
    "kind": "image", "vendor": "bailian_native",
    "protocol": "dashscope_native",
    "base_url": "https://dashscope.aliyuncs.com/api/v1",   # 若账号要求业务空间域请覆盖
    "default_model": "",          # 人工填（型号见厂商文档）
    "api_key_env": "BAILIAN_API_KEY",
    "size_separator": "*",        # ★ DashScope 用 "宽*高"，与 canonical 一致
    "async": False,               # 万相系改 True（走 X-DashScope-Async + task 轮询）
}
```

## 4. ★ 预算语义（异步轮询与预算的关系）

唯一计数点仍是 `_invoke()`，它**包住整次业务尝试**（含内部 1 次创建 + N 次轮询）
⇒ **一次业务调用 = 消耗 1 次 image 预算，轮询不额外消耗** ⇒ 正好是
"**一张图 = 1 次计费调用**"（否则 1 张图就会耗尽 3 次配额）。
代价：轮询请求不进 `APIRequestCounter` 审计（它只记计费口径的请求）；
单张图的完整信息记在编排层图像账 `image_generation_calls.jsonl`。
硬断言：**T20k**。

## 5. 测试

- 新增 **T20a~T20t（20 条）**：同步请求体形状 / `*` 记法透传 / `watermark=False` 显式下发 /
  usage 回传 / 官方响应路径解析 / ★多图报错 / 无载体报错 / 异步创建+轮询+终态 /
  轮询超时 → `LLMTimeoutError` / ★预算语义 / registry 分派 / ★既有档 0 变 / ★两档并存 /
  ★公共域同步最终 URL / ★公共域异步提交+轮询最终 URL / ★业务空间域最终 URL。
- 定向复跑：**20/20**，内存编译 PASS，真实 API 请求 0。
- 全量复跑：`python v2/logs/stage13_image/run_tests.py` → **146/146**，rc=0，
  真实 API 请求 0，本次工具累计墙钟约 **6 分 22 秒**。`excused_history_fails=6`
  均为 stage10–12 嵌套套件的既有 pre-commit 禁改护栏（当前合法交付改动
  `v2/config.py` / `v2/llm`），**无功能失败**；报告已逐条留痕。
- 环境留痕：首次沙箱内全量运行因既有测试产物 `PermissionError` 失败；两个独立
  `r+b` 写入探针确认同一权限原因后，在获准环境用同一测试命令复跑得到上述 rc=0；
  不将首次 `PermissionError` 计为通过。
- **修 T17c**：原实现把 `"v0.2-stage13" not in tags` 写死在**顶层**套件里，属 **pre-commit
  瞬时断言** —— 封存后**必然永久变红**（且豁免机制只作用于嵌套历史套件，管不到它）。
  改为**状态感知**：7 个既有 tag 必须存在且为 HEAD 祖先；`v0.2-stage13` 缺席或亦为 HEAD 祖先
  **都算通过**。（诚实边界：`--is-ancestor` 抓得住"删除/移到无关分支"，抓不住"向前移动到后代"。）

## 6. 已知边界（诚实登记）

- 首次真实出图仍需**人工四项前置**：Config 填模型名 + 注入 `BAILIAN_API_KEY` + 明确批准 +
  在 `IMAGE_PRICING` 登记单价（否则成本记 `null`）。
- `negative_prompt` 未接线：`ComposedVisualInput.negative_cues` 目前由
  `render_prompt()` 折进 prompt 正文（`[NEGATIVE] …`），**没有**走厂商专用通道。
  若要走专用通道，需要扩 `generate_image` 签名（**禁改契约**）或经 `reference` 逃生口 —— 属架构决策。
- `seed` 未传 → 出图不可复现（`resume` 只保证"不重复出图"）。
- 轮询不进 `APIRequestCounter` 审计（见 §4）。
- `base_url` 默认用公共域；官方 2026-09 文档示例用**业务空间域**
  （`https://{WorkspaceId}.<region>.maas.aliyuncs.com/api/v1`），账号若要求请覆盖。
- 本次只验证 URL 构造和 Mock/桩件回归，**未发真实 API 请求**；模型、密钥、
  人工批准与单价仍是真实出图前置。
