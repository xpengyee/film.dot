# -*- coding: utf-8 -*-
"""
V0.2 阶段 0 —— IO 验证脚本

验证 v2/core/io.py 能正确读取并写回 V0.1 的真实 JSON 数据。
测试对象全部来自 V0.1 已存在的数据（只读，绝不覆盖源文件）：

  - Script/actors_profile.json
  - Locations/Apartment living room/position.json
  - Locations/actions.json
  - Locations/shots.json

流程：read_json(源) → write_json(test_output) → read_json(test_output) → 比较。

测试输出只写入本目录（v2/logs/stage0_io_test/），不污染任何正式资产。
"""
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]       # v2/
V01_ROOT = V2_ROOT.parent / "FilmAgent"             # V0.1 核心代码目录
TEST_DIR = Path(__file__).resolve().parent          # v2/logs/stage0_io_test/

sys.path.insert(0, str(V2_ROOT))
from core.io import read_json, write_json, ensure_dir  # noqa: E402

TEST_CASES = [
    ("actors_profile", V01_ROOT / "Script" / "actors_profile.json"),
    ("position",       V01_ROOT / "Locations" / "Apartment living room" / "position.json"),
    ("actions",        V01_ROOT / "Locations" / "actions.json"),
    ("shots",          V01_ROOT / "Locations" / "shots.json"),
]


def main():
    ensure_dir(TEST_DIR)
    all_pass = True
    for name, src in TEST_CASES:
        print(f"--- {name}: {src.name} ---")
        # 1. 读取源文件
        data = read_json(src)
        print(f"  read_json    : PASS (解析为 {type(data).__name__})")
        # 2. 写入测试文件
        out_path = TEST_DIR / f"test_output_{name}.json"
        write_json(out_path, data)
        print(f"  write_json   : PASS ({out_path.name})")
        # 3. 重新读取
        re_read = read_json(out_path)
        print("  re-read      : PASS")
        # 4. 数据一致性
        equal = (data == re_read)
        print(f"  数据一致性   : {'PASS' if equal else 'FAIL'}")
        if not equal:
            all_pass = False
    print()
    print("结论:", "全部 PASS" if all_pass else "存在 FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
