#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布助手：生成 SHA256SUMS.txt + 打包 Release 附件（zip）。

用法：
    python release.py            # 生成清单 + 打包到 dist/
    python release.py --print    # 只打印当前文件哈希（用于更新 README）
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
ZIP_NAME = "pojia-dsh.zip"

# 进 Release 附件（zip）的文件 —— 用户解压后直接能跑
ASSETS = [
    "破甲DSH.py",
    "一键破甲.bat",
    "persona.md",
    "政策.md",
    "使用说明.md",
    "更新日志.md",
    "README.md",
    "LICENSE",
    "preview.png",
    "赞赏码.png",
]

# 只进清单、不塞进 zip 的仓库文件
EXTRA = [
    "release.py",
    "_回归测试/regress_v1.py",
    ".bandit.yml",
    ".semgrep.yml",
    ".github/workflows/security.yml",
    ".gitattributes",
    ".gitignore",
]

# 运行期产物，永远不进清单
SKIP_NAMES = {"破甲日志.txt", "SHA256SUMS.txt"}
SKIP_DIRS = {"__pycache__", "dist", "状态", ".git"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def tracked_files() -> list[str]:
    out: list[str] = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.name in SKIP_NAMES or p.suffix in (".pyc", ".pojiabak"):
            continue
        out.append(str(rel).replace("\\", "/"))
    return out


def cmd_print() -> int:
    for rel in tracked_files():
        print(f"{sha256(ROOT / rel)}  {rel}")
    return 0


def cmd_build() -> int:
    missing = [a for a in ASSETS if not (ROOT / a).is_file()]
    if missing:
        print("缺少要打包的文件：")
        for m in missing:
            print(f"  - {m}")
        return 2

    DIST.mkdir(exist_ok=True)
    zip_path = DIST / ZIP_NAME
    # 外层套一个文件夹，解压不会散落一地
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in ASSETS:
            zf.write(ROOT / rel, arcname=f"破甲DSH/{rel}")

    # 清单：本地文件 + zip 本体
    lines = []
    for rel in tracked_files():
        lines.append(f"{sha256(ROOT / rel)}  {rel}")
    lines.append(f"{sha256(zip_path)}  dist/{ZIP_NAME}")
    (ROOT / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[OK] 打包完成：{zip_path}  ({zip_path.stat().st_size:,} 字节)")
    print(f"[OK] 清单完成：SHA256SUMS.txt（{len(lines)} 条）")
    print("\n--- README 用的哈希表 ---")
    for rel in ASSETS:
        print(f"| {rel} | `{sha256(ROOT / rel)}` |")
    print(f"| {ZIP_NAME} | `{sha256(zip_path)}` |")
    return 0


def main() -> int:
    # 中文 Windows 控制台默认 GBK，直接 print emoji / 生僻字会 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if "--print" in sys.argv:
        return cmd_print()
    return cmd_build()


if __name__ == "__main__":
    sys.exit(main())
