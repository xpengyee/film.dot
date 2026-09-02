# -*- coding: utf-8 -*-
"""
V0.2 阶段 2 —— LLM 调用层测试（含最小真实 DeepSeek 调用）

覆盖（对应开工指令 §29~§37 的 9 项测试）：
    T1  配置读取（key configured / base URL / 默认模型；不显示完整 Key）
    T2  最小真实 LLM 调用（单条 user 消息）—— 真实 API call
    T3  messages（system + user 多条消息）—— 真实 API call
    T4  Token 日志（v2/logs/llm_calls.jsonl 出现条目，字段齐全）
    T5  成本统计（tokens × pricing → estimated_cost；无法计算时返回 None）
    T6  异常处理（无效参数/认证失败 → LLMError，而不是 Traceback）
    T7  重试机制（mock：timeout→timeout→成功 → retry_count=2）
    T8  失败重试上限（mock：连续失败 → max_retries 生效，不会无限调用）
    T9  API Key 不泄漏（日志 / 异常 / 控制台输出都不含完整 Key）

真实 API 调用只有 T2 和 T3 两次（每次一个极短请求，成本可忽略）。
其余全部用 mock transport，不浪费真实 API。
"""
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]       # v2/
TEST_DIR = Path(__file__).resolve().parent          # 本目录（stage2_llm_test）
LOG_FILE = V2_ROOT / "logs" / "llm_calls.jsonl"     # 共享日志（真实 + mock 都在这里记账）

sys.path.insert(0, str(V2_ROOT))
import config                                        # noqa: E402
from core.token_log import TokenUsage, TokenLogger, estimate_cost  # noqa: E402
from llm.client import (                              # noqa: E402
    LLMClient, LLMError, LLMTimeoutError, LLMRequestError,
    LLMAuthenticationError,
)
from llm.router import Router                         # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_results = []
_log_lines = []
_captured_error_strs = []   # 存异常字符串，供 T9 检查不泄漏 key


def log(msg=""):
    _log_lines.append(str(msg))


def check(name, ok, detail=""):
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    log(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ->  {detail}" if detail else ""))


def make_client(transport, *, api_key="sk-test-not-real", max_retries=2, **kw):
    """构造一个 LLMClient（默认用假 key，只跑 mock；真实调用单独传 key）。"""
    return LLMClient(
        api_key=api_key,
        base_url="http://localhost:0/mock",
        model="deepseek-chat",
        timeout=1.0,
        max_retries=max_retries,
        retry_backoff_seconds=kw.pop("retry_backoff_seconds", 0.0),
        logger=shared_logger,
        transport=transport,
    )


shared_logger = TokenLogger(V2_ROOT / "logs")


# ---------------------------------------------------------------------------
# T1 配置读取
# ---------------------------------------------------------------------------
def test_config():
    configured = config.api_key_configured()
    masked = config.api_key_masked()
    base_url_ok = config.API_BASE_URL == "https://api.deepseek.com"
    model_ok = config.DEFAULT_MODEL == "deepseek-chat"
    # 脱敏 key 不等于真实 key；且显示形式是掩码
    raw = config.load_api_key()
    masked_ok = (not raw) or (masked != raw and "****" in masked)
    check("T1 配置读取(base_url/model)", base_url_ok and model_ok,
          f"base_url={config.API_BASE_URL}; default_model={config.DEFAULT_MODEL}")
    check("T1 配置读取(key configured + 脱敏)", configured and masked_ok,
          f"key_configured={configured}; masked={masked!r}")
    check("T1 超时/重试可配置", config.TIMEOUT_SECONDS == 120.0 and config.MAX_RETRIES == 2,
          f"timeout={config.TIMEOUT_SECONDS}s; max_retries={config.MAX_RETRIES}")
    return raw


# ---------------------------------------------------------------------------
# T2 最小真实 LLM 调用
# ---------------------------------------------------------------------------
def test_real_single_message(raw_key):
    if not raw_key:
        check("T2 最小真实 LLM 调用", False, "未配置 API key，无法真实调用")
        return None
    client = LLMClient(api_key=raw_key, base_url=config.API_BASE_URL,
                       model="deepseek-chat", timeout=config.TIMEOUT_SECONDS,
                       max_retries=config.MAX_RETRIES, retry_backoff_seconds=0.3,
                       logger=shared_logger)
    try:
        resp = client.chat([{"role": "user", "content": "Return exactly: OK"}],
                           step="stage2_test", agent="t2_single_message")
    except Exception as exc:
        _captured_error_strs.append(f"{type(exc).__name__}: {exc}")
        check("T2 最小真实 LLM 调用", False, f"真实调用失败: {type(exc).__name__}: {exc}")
        return None
    ok = ("OK" in (resp.text or "").upper()
          and resp.usage.total_tokens > 0
          and bool(resp.request_id)
          and resp.duration_ms >= 0)
    check("T2 最小真实 LLM 调用", ok,
          f"text={resp.text!r}; input={resp.usage.input_tokens} output={resp.usage.output_tokens} "
          f"total={resp.usage.total_tokens}; request_id={resp.request_id[:8]}...; "
          f"duration={resp.duration_ms}ms; retry_count={resp.retry_count}")
    return resp


# ---------------------------------------------------------------------------
# T3 messages（system + user）
# ---------------------------------------------------------------------------
def test_real_messages(raw_key):
    if not raw_key:
        check("T3 messages 多条消息", False, "未配置 API key，无法真实调用")
        return None
    client = LLMClient(api_key=raw_key, base_url=config.API_BASE_URL,
                       model="deepseek-chat", timeout=config.TIMEOUT_SECONDS,
                       max_retries=config.MAX_RETRIES, retry_backoff_seconds=0.3,
                       logger=shared_logger)
    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "Return exactly: OK"},
    ]
    try:
        resp = client.chat(messages, step="stage2_test", agent="t3_messages")
    except Exception as exc:
        _captured_error_strs.append(f"{type(exc).__name__}: {exc}")
        check("T3 messages 多条消息", False, f"真实调用失败: {type(exc).__name__}: {exc}")
        return None
    ok = "OK" in (resp.text or "").upper() and resp.usage.total_tokens > 0
    check("T3 messages 多条消息", ok,
          f"system+user 两条消息; text={resp.text!r}; total_tokens={resp.usage.total_tokens}")
    return resp


# ---------------------------------------------------------------------------
# T4 Token 日志
# ---------------------------------------------------------------------------
def test_token_log(resp2, resp3):
    records = shared_logger.read_all()
    log(f"  (llm_calls.jsonl 当前累计 {len(records)} 条)")
    hits = []
    for rid in (resp2.request_id if resp2 else None, resp3.request_id if resp3 else None):
        if rid is None:
            continue
        found = [r for r in records if r["request_id"] == rid]
        if found:
            hits.append(found[0])
    ok = bool(hits) and all(
        h["model"] == "deepseek-chat"
        and h["total_tokens"] > 0
        and h["duration_ms"] >= 0
        and h["success"] is True
        and h["request_id"]
        for h in hits
    )
    check("T4 Token 日志(JSONL)", ok,
          f"命中 {len(hits)} 条真实调用日志; 文件={LOG_FILE.name}")
    if hits:
        sample = hits[0]
        log(f"  样例: request_id={sample['request_id'][:8]}... model={sample['model']} "
            f"in={sample['input_tokens']} out={sample['output_tokens']} "
            f"total={sample['total_tokens']} dur={sample['duration_ms']}ms "
            f"success={sample['success']}")


# ---------------------------------------------------------------------------
# T5 成本统计
# ---------------------------------------------------------------------------
def test_cost(resp2):
    pricing = config.get_pricing("deepseek-chat")
    if resp2 is not None:
        cost = estimate_cost(resp2.usage, pricing)
        ok = cost is not None and cost >= 0
        # tokens 与 cost 是分开的两个字段（不同单位、不同含义）
        check("T5 成本统计(真实 usage)", ok,
              f"tokens(total={resp2.usage.total_tokens}) ≠ cost({cost} {pricing['currency']})")
    else:
        check("T5 成本统计(真实 usage)", False, "无真实 usage，无法计算")
    # 无法计算时必须返回 None，而不是瞎编
    none_case = estimate_cost(TokenUsage(1000, 500, 1500), None) is None \
        and estimate_cost(TokenUsage(1000, 500, 1500), {}) is None
    check("T5 成本统计(无价格→None)", none_case, "pricing 缺失时 estimate_cost 返回 None")
    # 手工演算：1000 输入 + 500 输出，无缓存拆分，全按未命中价
    manual = estimate_cost(TokenUsage(1000, 500, 1500), pricing)
    expect = (1000 / 1e6) * pricing["input_cache_miss"] + (500 / 1e6) * pricing["output"]
    check("T5 成本统计(手工演算一致)", manual is not None and abs(manual - round(expect, 8)) < 1e-12,
          f"1000入+500出 → {manual} {pricing['currency']}")


# ---------------------------------------------------------------------------
# T6 异常处理（无效参数 / 认证失败 → LLMError，不是裸 Traceback）
# ---------------------------------------------------------------------------
def test_error_handling():
    def bad_param_transport(messages, model, temperature, max_tokens, timeout):
        raise LLMRequestError("invalid parameter: max_tokens must be int")

    client = make_client(bad_param_transport)
    try:
        client.chat([{"role": "user", "content": "x"}], step="t6", agent="t6_badparam")
        check("T6 无效参数→LLMError", False, "没有抛异常")
    except LLMError as exc:
        _captured_error_strs.append(f"{type(exc).__name__}: {exc}")
        check("T6 无效参数→LLMError", isinstance(exc, LLMRequestError) and not exc.retryable,
              f"抛 {type(exc).__name__}（不可重试），而非裸 Traceback")

    def auth_fail_transport(messages, model, temperature, max_tokens, timeout):
        raise LLMAuthenticationError("authentication failed")

    client2 = make_client(auth_fail_transport)
    try:
        client2.chat([{"role": "user", "content": "x"}], step="t6", agent="t6_auth")
        check("T6 认证失败→LLMError", False, "没有抛异常")
    except LLMError as exc:
        _captured_error_strs.append(f"{type(exc).__name__}: {exc}")
        check("T6 认证失败→LLMError", isinstance(exc, LLMAuthenticationError),
              f"抛 {type(exc).__name__}（不可重试）")


# ---------------------------------------------------------------------------
# T7 重试机制（mock：timeout→timeout→成功）
# ---------------------------------------------------------------------------
def test_retry():
    calls = [0]

    def flaky_transport(messages, model, temperature, max_tokens, timeout):
        calls[0] += 1
        if calls[0] <= 2:
            raise LLMTimeoutError("simulated timeout")
        return "OK", TokenUsage(input_tokens=10, output_tokens=2, total_tokens=12)

    client = make_client(flaky_transport, max_retries=2)
    try:
        resp = client.chat([{"role": "user", "content": "hi"}], step="t7", agent="t7_retry")
    except Exception as exc:
        _captured_error_strs.append(f"{type(exc).__name__}: {exc}")
        check("T7 重试机制", False, f"意外失败: {exc}")
        return
    ok = resp.retry_count == 2 and calls[0] == 3 and resp.text == "OK"
    check("T7 重试机制", ok,
          f"第3次才成功; 总尝试={calls[0]}; retry_count={resp.retry_count}; "
          f"日志 success=true")


# ---------------------------------------------------------------------------
# T8 失败重试上限（mock：连续失败，不能无限调用）
# ---------------------------------------------------------------------------
def test_retry_limit():
    calls = [0]

    def always_fail_transport(messages, model, temperature, max_tokens, timeout):
        calls[0] += 1
        raise LLMTimeoutError("simulated timeout")

    client = make_client(always_fail_transport, max_retries=2)
    try:
        client.chat([{"role": "user", "content": "hi"}], step="t8", agent="t8_limit")
        check("T8 失败重试上限", False, "连续失败竟然成功了（不应如此）")
    except LLMError as exc:
        _captured_error_strs.append(f"{type(exc).__name__}: {exc}")
        ok = isinstance(exc, LLMTimeoutError) and calls[0] == 3  # 初始1次 + 重试2次，不再多
        records = shared_logger.read_all()
        last = records[-1]
        log_ok = last["success"] is False and last["retry_count"] == 2 and last["error"]
        check("T8 失败重试上限", ok and log_ok,
              f"总尝试={calls[0]}(=max_retries+1，没无限重试); "
              f"最终抛 {type(exc).__name__}; 日志 success={last['success']} retry_count={last['retry_count']}")


# ---------------------------------------------------------------------------
# T9 API Key 不泄漏
# ---------------------------------------------------------------------------
def test_no_key_leak(raw_key):
    if not raw_key:
        check("T9 API Key 不泄漏", True, "未配置 key（无从泄漏）")
        return
    log_text = LOG_FILE.read_text(encoding="utf-8") if LOG_FILE.exists() else ""
    console_text = "\n".join(_log_lines)
    error_text = "\n".join(_captured_error_strs)
    leaked_in_log = raw_key in log_text
    leaked_in_console = raw_key in console_text
    leaked_in_errors = raw_key in error_text
    ok = not (leaked_in_log or leaked_in_console or leaked_in_errors)
    check("T9 API Key 不泄漏", ok,
          f"日志={not leaked_in_log} 控制台={not leaked_in_console} 异常={not leaked_in_errors}; "
          f"只显示脱敏 {config.api_key_masked()!r}")


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main():
    log("=" * 64)
    log("V0.2 阶段 2 —— LLM 调用层测试")
    log("=" * 64)
    log(f"API base_url = {config.API_BASE_URL}")
    log(f"默认模型     = {config.DEFAULT_MODEL}")
    log(f"timeout      = {config.TIMEOUT_SECONDS}s, max_retries = {config.MAX_RETRIES}")
    log(f"key 配置     = {config.api_key_configured()}（显示 {config.api_key_masked()!r}）")
    log(f"日志文件     = {LOG_FILE}")
    log("")

    raw_key = test_config()

    resp2 = test_real_single_message(raw_key)
    resp3 = test_real_messages(raw_key)
    log("")
    log("--- Token 日志 ---")
    test_token_log(resp2, resp3)
    log("")
    log("--- 成本统计 ---")
    test_cost(resp2)
    log("")
    log("--- 异常处理 & 重试（mock，不浪费真实 API）---")
    test_error_handling()
    test_retry()
    test_retry_limit()
    log("")

    # 真实调用统计
    real_calls = sum(1 for r in (resp2, resp3) if r is not None)
    real_in = sum(r.usage.input_tokens for r in (resp2, resp3) if r is not None)
    real_out = sum(r.usage.output_tokens for r in (resp2, resp3) if r is not None)
    real_total = sum(r.usage.total_tokens for r in (resp2, resp3) if r is not None)
    real_cost = None
    pricing = config.get_pricing("deepseek-chat")
    if resp2:
        c2 = estimate_cost(resp2.usage, pricing)
        c3 = estimate_cost(resp3.usage, pricing) if resp3 else None
        if c2 is not None and c3 is not None:
            real_cost = round(c2 + c3, 8)

    log("--- 真实 LLM 调用统计 ---")
    log(f"Real LLM calls   = {real_calls}")
    log(f"Input tokens     = {real_in}")
    log(f"Output tokens    = {real_out}")
    log(f"Total tokens     = {real_total}")
    log(f"Estimated cost   = {real_cost} {pricing['currency'] if pricing else ''} "
        f"(按高峰价保守估算)")

    log("")
    log("--- API Key 安全检查 ---")
    test_no_key_leak(raw_key)

    log("")
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 64)
    log(f"测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")
    log("本阶段真实 API 调用次数见上方统计；其余测试均为 mock，不额外计费。")

    report = {
        "stage": "stage2_llm_test",
        "real_llm_calls": real_calls,
        "input_tokens": real_in,
        "output_tokens": real_out,
        "total_tokens": real_total,
        "estimated_cost": real_cost,
        "currency": (pricing or {}).get("currency"),
        "total": total,
        "passed": passed,
        "results": _results,
    }
    from core.io import write_json
    write_json(TEST_DIR / "stage2_report.json", report)
    (TEST_DIR / "stage2_console.txt").write_text("\n".join(_log_lines), encoding="utf-8")
    print("\n".join(_log_lines))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
