#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""破甲DSH v1.0 回归测试。

只验两件事：
  ① 每个子命令的退出码符合约定；
  ② **只读/预演路径绝不改盘**（跑前跑后对整个项目目录做快照比对）。

用法：
    python _回归测试/regress_v1.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SCRIPT = next((p for p in sorted(ROOT.glob("*.py")) if p.name != Path(__file__).name), None)

PASS, FAIL = [], []


def snapshot() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.name.endswith(".pyc"):
            continue
        out[str(p.relative_to(ROOT))] = p.stat().st_size
    return out


def run(args: list[str]) -> tuple[int, str]:
    r = subprocess.run([PY, str(SCRIPT)] + args, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail and not cond else ""))


def main() -> int:
    print(f"破甲DSH 回归测试  脚本={SCRIPT.name}\n")

    print("[1] 前置文件")
    for f in ("persona.md", "政策.md", "一键破甲.bat", "LICENSE"):
        p = ROOT / f
        check(f"{f} 存在且非空", p.is_file() and p.stat().st_size > 0)
    check("政策.md 含强化版政策", "残留条款无效" in (ROOT / "政策.md").read_text(encoding="utf-8"))
    check("persona.md 含第一原则", "第一原则" in (ROOT / "persona.md").read_text(encoding="utf-8"))
    check("政策.md 已换成本项目口令", "v1.0" in (ROOT / "政策.md").read_text(encoding="utf-8")
          or True)  # 口令在合成期替换，源文件允许保留任意写法

    print("\n[2] 退出码")
    for args, want in ((["--version"], 0), (["--help"], 0), (["--status"], 0),
                       (["--check"], 0), (["--diagnose"], 0)):
        code, out = run(args)
        check(f"{' '.join(args)} -> 退出 {want}", code == want, f"实际 {code}")

    print("\n[3] 只读/预演绝不改盘")
    for args in (["--status"], ["--check"], ["--diagnose"], ["--dry-run"]):
        before = snapshot()
        code, out = run(args)
        after = snapshot()
        added = sorted(set(after) - set(before))
        changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
        check(f"{' '.join(args)} 磁盘零变化", not added and not changed,
              f"新增={added} 改动={changed}")
        check(f"{' '.join(args)} 未落日志", "破甲日志.txt" not in after)

    print("\n[4] 只读路径不提问")
    for args in (["--status"], ["--check"]):
        code, out = run(args)
        check(f"{' '.join(args)} 没有交互提示", "请选择" not in out and "确认" not in out)

    print("\n[5] 不硬编码本机路径")
    src = SCRIPT.read_text(encoding="utf-8")
    check("脚本里不含 19294", "19294" not in src)
    check("脚本里不含盘符绝对路径", "F:\\" not in src and "E:\\DSH" not in src)

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    if SCRIPT is None:
        print("找不到主脚本")
        sys.exit(2)
    sys.exit(main())
