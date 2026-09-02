# -*- coding: utf-8 -*-
"""
V0.2 阶段 0 —— 最小 IO 模块。

设计原则：迁移 V0.1 已有 IO 行为 + 最小封装，不重新发明。

迁移自 V0.1 `util.py` 的两个函数，保留其实际行为：
- read_json：打开文件用 errors='ignore' 容忍非法字节；读入后再把
  替换字符 U+FFFD（显示为 �）统一替换成 '.'，再重新解析。
  （这是 V0.1 真实运行依赖的容错行为，不能丢。）
- write_json：json.dump 使用 ensure_ascii=False，保证中文可读。
  （相比 V0.1 额外加了 indent=2 与自动建目录，便于后续人工审核，
  不影响 JSON 语义。）
- ensure_dir：显式确保目录存在（V0.1 是借 cretae_new_path 隐式处理）。

本模块只提供基础能力，不实现 Project / Asset / Memory / Workflow 等业务逻辑。
"""
from pathlib import Path
import json


def ensure_dir(path):
    """确保目录存在（含父目录），返回 Path 对象。"""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_compact_string(data):
    """与 V0.1 util.toString 等价：ensure_ascii=False + 紧凑分隔符。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def read_json(path):
    """读取 JSON 文件，保留 V0.1 util.read_json 的容错行为。

    步骤：
    1. 以 encoding='utf-8', errors='ignore' 打开文件（容忍坏字节）；
    2. 解析为 Python 对象；
    3. 序列化成紧凑字符串后把 U+FFFD（�）替换为 '.'；
    4. 再次解析返回。
    """
    path = Path(path)
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    text = _to_compact_string(data)
    text = text.replace("�", ".")
    return json.loads(text)


def write_json(path, data):
    """写入 JSON 文件，行为对齐 V0.1 util.write_json（ensure_ascii=False）。

    相比 V0.1 的增强（不影响语义）：
    - 自动创建父目录；
    - indent=2，便于人工阅读与审核。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


__all__ = [
    "read_json",
    "write_json",
    "ensure_dir",
]
