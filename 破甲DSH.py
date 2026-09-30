#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""破甲DSH v1.0 —— DeepSeek Harness 专用人格装载台。

把「操作者人格 + 宽松政策」注入到 DSH 里，走官方支持的通道：

    L1  用户全局指令文件  $DSH_HOME/AGENTS.md      ← 主路径，改完下一句就生效
    L2  系统提示词层      profile/cordis.patch.yml ← 可选加档（覆盖官方 preset 的 persona）
    L3  旧 preset 目录    $DSH_HOME/.agent-presets ← 新版已废弃，只做检测提示

设计铁律
    1. 只写用户自己的文件（$DSH_HOME 下），**绝不碰安装目录**，升级 DSH 不丢。
    2. 每条注入路径都必须真机验证过；没验过的功能默认关闭并标注「实验性」。
    3. 改前必备份、原子替换、UTF-8 无 BOM、还原只动「护照」里记录过的路径。
    4. 只读动作（--status/--diagnose/--check）静默、不提问、不落盘。

作者：z91772524-ai    许可：MIT
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

APP_NAME = "破甲DSH"
VERSION = "1.0"
SELF_CHECK_PHRASE = "破甲自检"
SELF_CHECK_REPLY = "破甲已生效 | 目标 DSH | v1.0"

# 官方线程/工具约定：哪些目录名算安装根（必须用结构特征判定，不能只看名字）
NEW_ARCH_MARK = Path("resources") / "app.asar"                     # 0.2.x 起：整个 dsh 打包进 asar
OLD_ARCH_MARK = Path("resources") / "app" / "node_modules" / "@deepseek-ai"
ASAR_DSH_PREFIX = "dsh/node_modules/@deepseek-ai/"                 # asar 内部 dsh 实现所在前缀
PRESET_PACKAGE = ASAR_DSH_PREFIX + "dsh-agent-preset-registry"     # 新版 preset 注册表
INSTRUCTIONS_PACKAGE = ASAR_DSH_PREFIX + "dsh-agent-instructions"  # AGENTS.md 注入器（L1 的依据）
WEB_APP_PRESETS = ASAR_DSH_PREFIX + "dsh-web-app/presets/"         # 官方四个模式的 patch 定义

CORPORATE_HINT = re.compile(r"harness|deepseek|\bdsh\b", re.I)
HERE = Path(__file__).resolve().parent

# ── ANSI ────────────────────────────────────────────────────────────────────
def _enable_vt() -> None:
    """打开控制台 VT 处理，让 ANSI 转义序列在 cmd 里生效。

    用 SetConsoleMode 而不是"空跑的 os.system 调用"：后者靠 cmd 的副作用碰巧生效，
    而且会让安全扫描多一条命中（本项目门禁要求它为 0）。
    """
    if sys.platform != "win32":
        return
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.GetStdHandle(-11)                       # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


_enable_vt()


class C:
    R = "\033[0m"
    B = "\033[1m"
    DIM = "\033[2m"
    CY = "\033[36m"
    GR = "\033[32m"
    YL = "\033[33m"
    RD = "\033[31m"
    WH = "\033[97m"


def enable_utf8() -> None:
    """控制台切 UTF-8，避免中文提示乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 日志 ────────────────────────────────────────────────────────────────────
class Log:
    """控制台 + 落盘（破甲日志.txt）双写。"""

    def __init__(self, path: Path, quiet: bool = False, file_write: bool = True):
        self.path = path
        self.quiet = quiet
        self.file_write = file_write

    def _write(self, text: str, echo: bool = True, color: str = "") -> None:
        if echo and not self.quiet:
            print(f"{color}{text}{C.R}" if color else text)
        if not self.file_write:
            return
        try:
            with open(self.path, "a", encoding="utf-8", newline="") as fh:
                fh.write(text + "\n")
        except Exception:
            pass

    def line(self, text: str = "", color: str = "") -> None:
        self._write(text, True, color)

    def ok(self, text: str) -> None:
        self._write(f"  [OK]   {text}", True, C.GR)

    def warn(self, text: str) -> None:
        self._write(f"  [WARN] {text}", True, C.YL)

    def err(self, text: str) -> None:
        self._write(f"  [FAIL] {text}", True, C.RD)

    def info(self, text: str) -> None:
        self._write(f"  [info] {text}")

    def section(self, title: str) -> None:
        self._write("")
        self._write(f"—— {title} ——", True, C.CY)

    def only_file(self, text: str) -> None:
        self._write(text, False)


# ── 原子写 / 备份 ────────────────────────────────────────────────────────────
def atomic_write(path: Path, text: str) -> None:
    """UTF-8 无 BOM + 同目录临时文件 + 原子替换（断电不会留半个文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pojia-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def backup_once(path: Path, suffix: str = ".pojiabak") -> Path | None:
    """备份目标文件；**已存在的备份绝不覆盖**（否则第二次跑会把我们写的内容当原件存下）。"""
    if not path.exists():
        return None
    bak = path.with_name(path.name + suffix)
    if bak.exists():
        return None
    shutil.copy2(path, bak)
    return bak


# ── 护照（记录「我改过什么」，还原时只动这些） ───────────────────────────────
class Passport:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"version": VERSION, "entries": []}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def record(self, kind: str, target: str, detail: dict | None = None) -> None:
        for e in self.data["entries"]:
            if e.get("kind") == kind and e.get("target") == target:
                e.update({"detail": detail or {}, "at": now_stamp()})
                break
        else:
            self.data["entries"].append(
                {"kind": kind, "target": target, "detail": detail or {}, "at": now_stamp()}
            )
        self.data["version"] = VERSION
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")

    def entries(self, kind: str = "") -> list[dict]:
        return [e for e in self.data["entries"] if not kind or e.get("kind") == kind]

    def drop(self, kind: str, target: str) -> None:
        self.data["entries"] = [
            e for e in self.data["entries"]
            if not (e.get("kind") == kind and e.get("target") == target)
        ]
        atomic_write(self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")


# ── 进程枚举（ctypes，不依赖 wmic / PowerShell） ────────────────────────────
def iter_process_paths() -> list[str]:
    """返回所有进程的可执行文件全路径（拿不到主模块的跳过）。"""
    TH32CS_SNAPPROCESS = 0x00000002
    TH32CS_SNAPMODULE = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010
    INVALID = ctypes.c_void_p(-1).value
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    paths: list[str] = []

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    class MODULEENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
            ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
            ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)), ("modBaseSize", wt.DWORD),
            ("hModule", wt.HMODULE), ("szModule", ctypes.c_char * 256),
            ("szExePath", ctypes.c_char * 260),
        ]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID or snap is None:
        return paths
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        more = k32.Process32First(snap, ctypes.byref(pe))
        while more:
            msnap = k32.CreateToolhelp32Snapshot(
                TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pe.th32ProcessID
            )
            if msnap != INVALID and msnap is not None:
                try:
                    me = MODULEENTRY32()
                    me.dwSize = ctypes.sizeof(MODULEENTRY32)
                    if k32.Module32First(msnap, ctypes.byref(me)):
                        try:
                            paths.append(me.szExePath.decode("mbcs"))
                        except Exception:
                            pass
                finally:
                    k32.CloseHandle(msnap)
            more = k32.Process32Next(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return paths


# ── 注册表：卸载项 ──────────────────────────────────────────────────────────
def iter_uninstall_entries() -> list[dict]:
    import winreg  # noqa: PLC0415  （仅 Windows 需要）

    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    out: list[dict] = []
    for hive, sub in roots:
        try:
            key = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        with key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, name) as sk:
                        item = {"_key": name}
                        for field in ("DisplayName", "DisplayVersion", "InstallLocation",
                                      "UninstallString", "DisplayIcon", "Publisher"):
                            try:
                                item[field] = winreg.QueryValueEx(sk, field)[0]
                            except OSError:
                                item[field] = ""
                        out.append(item)
                except OSError:
                    continue
    return out


# ── 快捷方式（用 PowerShell 调 WScript.Shell；失败就静默跳过） ───────────────
def iter_shortcut_targets() -> list[tuple[str, str]]:
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$sh=New-Object -ComObject WScript.Shell;"
        "$dirs=@("
        "\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\","
        "\"$env:PROGRAMDATA\\Microsoft\\Windows\\Start Menu\\Programs\","
        "\"$env:USERPROFILE\\Desktop\");"
        "Get-ChildItem $dirs -Recurse -Filter *.lnk | ForEach-Object {"
        "  $t=$sh.CreateShortcut($_.FullName).TargetPath;"
        "  if($t){ \"$($_.Name)|$t\" } }"
    )
    out: list[tuple[str, str]] = []
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=45,
        )
        text = r.stdout.decode("utf-8", "replace")
        for ln in text.splitlines():
            if "|" in ln:
                name, target = ln.split("|", 1)
                out.append((name.strip(), target.strip().strip('"')))
    except Exception:
        pass
    return out


# ── 判定「这个目录是不是 DSH 安装根」 ───────────────────────────────────────
def arch_of(root: Path) -> str:
    """返回 'new' / 'old' / ''（不是安装根）。"""
    if (root / NEW_ARCH_MARK).is_file():
        return "new"
    if (root / OLD_ARCH_MARK).is_dir():
        return "old"
    return ""


def dsh_fingerprint(root: Path) -> bool:
    """确认这个 Electron 应用**就是 DSH**，不是别的 Electron 程序。

    教训：`resources\\app.asar` 是所有 Electron 应用都有的，拿它当判据会把
    coloros-assist / twinkle-tray 之类的无关程序一起捞进来（实测踩过）。
    """
    yml = root / "resources" / "app-update.yml"
    if yml.is_file():
        try:
            txt = yml.read_text(encoding="utf-8", errors="replace").lower()
            if any(k in txt for k in ("@deepseek-ai", "dsh-desktop", "dsh-desk",
                                      "deepseek", "harness")):
                return True
        except OSError:
            pass
    pj = root / "resources" / "app" / "package.json"
    if pj.is_file():
        try:
            txt = pj.read_text(encoding="utf-8", errors="replace").lower()
            if any(k in txt for k in ("dsh-plugin-desktop", "dsh-desktop", "deepseek")):
                return True
        except OSError:
            pass
    if CORPORATE_HINT.search(root.name):
        return True
    try:
        for child in root.iterdir():
            if child.suffix.lower() == ".exe" and CORPORATE_HINT.search(child.stem):
                return True
    except OSError:
        pass
    return False


def looks_like_dsh(root: Path) -> bool:
    """**结构判据**：先看结构特征，再验产品指纹——绝不只看目录名。"""
    if not root.is_dir():
        return False
    arch = arch_of(root)
    if arch == "old":
        # resources/app/node_modules/@deepseek-ai 已经足够特异，别的 Electron 不会有
        return True
    if arch == "new":
        return dsh_fingerprint(root)
    return False


# ── asar 读取（纯标准库，用于读官方 preset 定义 / 版本） ─────────────────────
def asar_header(fh) -> dict:
    fh.seek(0)
    head = fh.read(16)
    if len(head) < 16:
        raise ValueError("文件太短，不是 asar")
    hsz = int.from_bytes(head[4:8], "little")
    if hsz <= 0 or hsz > 256 << 20:
        raise ValueError(f"asar 头长度异常：{hsz}")
    fh.seek(8)
    raw = fh.read(hsz).decode("utf-8", "replace")
    i, j = raw.find("{"), raw.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("asar 头里找不到 JSON")
    return json.loads(raw[i:j + 1])


def asar_base(header_size: int) -> int:
    return 8 + header_size


def asar_walk(node: dict, prefix: str = "") -> list[dict]:
    out: list[dict] = []
    for name, val in (node.get("files") or {}).items():
        p = f"{prefix}/{name}"
        if "files" in val:
            out.extend(asar_walk(val, p))
        else:
            out.append({"path": p, "size": int(val.get("size") or 0),
                        "offset": int(val.get("offset") or 0)})
    return out


class Asar:
    """只读打开一个 .asar 归档并按需取文件。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.fh = open(self.path, "rb")
        self.header = asar_header(self.fh)
        self.hsz = int.from_bytes(self.fh.read(0) or b"\0\0\0\0", "little") or 0
        # 重新取一次真实 headerSize（asar_header 已消费，这里直接读文件头）
        self.fh.seek(4)
        self.hsz = int.from_bytes(self.fh.read(4), "little")
        self.base = asar_base(self.hsz)
        self.entries = asar_walk(self.header)

    def find(self, needle: str) -> list[dict]:
        return [e for e in self.entries if needle in e["path"]]

    def read(self, entry: dict) -> bytes:
        if not entry["size"]:
            return b""
        self.fh.seek(self.base + entry["offset"])
        return self.fh.read(entry["size"])

    def read_path(self, needle: str) -> bytes | None:
        hits = self.find(needle)
        for h in hits:
            if h["path"].endswith(needle.split("/")[-1]) and h["size"]:
                return self.read(h)
        return self.read(hits[0]) if hits and hits[0]["size"] else None

    def close(self) -> None:
        try:
            self.fh.close()
        except Exception:
            pass


# ── 安装探测 ────────────────────────────────────────────────────────────────
class Install:
    __slots__ = ("root", "arch", "version", "channel", "source", "running")

    def __init__(self, root: Path, arch: str, source: str):
        self.root = Path(root)
        self.arch = arch
        self.version = ""
        self.channel = ""
        self.source = source
        self.running = False

    @property
    def asar_path(self) -> Path:
        return self.root / "resources" / "app.asar"

    @property
    def label(self) -> str:
        v = self.version or "版本未知"
        a = {"new": "新版(asar)", "old": "旧版(明文)"}.get(self.arch, "?")
        return f"{v}  [{a}]"

    def read_version(self) -> str:
        # ① asar 内 dsh 的 package.json
        if self.arch == "new" and self.asar_path.is_file():
            try:
                a = Asar(self.asar_path)
                try:
                    for e in a.find(f"{ASAR_DSH_PREFIX}dsh/package.json"):
                        data = json.loads(a.read(e).decode("utf-8", "replace"))
                        if data.get("version"):
                            self.version = data["version"]
                            break
                finally:
                    a.close()
            except Exception:
                pass
        # ② 明文版 resources/app/package.json
        if not self.version:
            pj = self.root / "resources" / "app" / "package.json"
            if pj.is_file():
                try:
                    data = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
                    self.version = data.get("version", "")
                except Exception:
                    pass
        return self.version

    def read_channel(self) -> str:
        yml = self.root / "resources" / "app-update.yml"
        if yml.is_file():
            try:
                txt = yml.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^channel:\s*(\S+)", txt, re.M)
                self.channel = m.group(1) if m else "release"
                return self.channel
            except Exception:
                pass
        self.channel = "?"
        return self.channel


def _clean_reg_path(raw: str) -> Path | None:
    if not raw:
        return None
    p = raw.strip().strip('"')
    m = re.match(r'^"?([A-Za-z]:\\[^"]+?\.exe)"?', p)
    if m:
        p = m.group(1)
    p = p.strip().strip('"')
    cand = Path(p)
    if cand.suffix.lower() == ".exe":
        cand = cand.parent
    return cand if cand.exists() else None


def discover_installs(log: Log | None = None) -> list[Install]:
    """按可靠性从高到低找 DSH 安装：进程 → 注册表 → 快捷方式 → 常见位置 → 整个盘根。"""
    found: dict[str, Install] = {}

    def add(root: Path, source: str, running: bool = False) -> None:
        try:
            root = root.resolve()
        except OSError:
            return
        key = str(root).lower()
        arch = arch_of(root)
        if not arch and not looks_like_dsh(root):
            return
        if key not in found:
            found[key] = Install(root, arch or "new", source)
        if running:
            found[key].running = True

    # ① 正在运行的进程 —— 最权威
    running_roots: set[str] = set()
    for exe in iter_process_paths():
        low = exe.lower()
        if "harness" in low or "dsh" in low or "deepseek" in low:
            root = Path(exe).parent
            if looks_like_dsh(root):
                add(root, "进程", running=True)
                running_roots.add(str(root.resolve()).lower())
            else:
                # 有些部署 exe 比 resources 深一层
                if looks_like_dsh(root.parent):
                    add(root.parent, "进程", running=True)
                    running_roots.add(str(root.parent.resolve()).lower())

    # ② 注册表卸载项
    for item in iter_uninstall_entries():
        name = item.get("DisplayName", "")
        if not name or not CORPORATE_HINT.search(name):
            continue
        for raw in (item.get("InstallLocation", ""),
                    item.get("UninstallString", ""),
                    item.get("DisplayIcon", "")):
            cand = _clean_reg_path(raw)
            if cand and looks_like_dsh(cand):
                add(cand, f"注册表:{name}")
                break

    # ③ 开始菜单 / 桌面快捷方式
    for name, target in iter_shortcut_targets():
        if not target or not CORPORATE_HINT.search(name + target):
            continue
        cand = Path(target)
        cand = cand.parent if cand.suffix.lower() == ".exe" else cand
        if looks_like_dsh(cand):
            add(cand, f"快捷方式:{name}")
        elif looks_like_dsh(cand.parent):
            add(cand.parent, f"快捷方式:{name}")

    # ④ 常见安装位置（只扫一层，快）
    bases = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
             Path(os.environ.get("ProgramFiles", "")),
             Path(os.environ.get("ProgramFiles(x86)", "")),
             Path(os.environ.get("APPDATA", ""))]
    for base in bases:
        try:
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if child.is_dir() and looks_like_dsh(child):
                    add(child, "常见位置")
        except OSError:
            continue

    # ⑤ 各盘根 2 层内（名字粗筛 + 结构复核）
    for drive in "CDEFGH":
        root = Path(f"{drive}:\\")
        if not root.exists():
            continue
        try:
            for lvl1 in root.iterdir():
                if not lvl1.is_dir() or str(lvl1).startswith(("C:\\Windows",)):
                    continue
                if looks_like_dsh(lvl1):
                    add(lvl1, "盘根扫描")
                    continue
                if not CORPORATE_HINT.search(lvl1.name):
                    continue
                try:
                    for lvl2 in lvl1.iterdir():
                        if lvl2.is_dir() and looks_like_dsh(lvl2):
                            add(lvl2, "盘根扫描")
                except OSError:
                    continue
        except OSError:
            continue

    # 补版本号；按「在跑 > 进程 > 注册表 > …」排序
    order = {"进程": 0, "注册表": 1, "快捷方式": 2, "常见位置": 3, "盘根扫描": 4}
    items = list(found.values())
    for ins in items:
        ins.read_version()
        ins.read_channel()
    items.sort(key=lambda i: (not i.running, order.get(i.source.split(":")[0], 9), str(i.root)))
    if log:
        log.info(f"共发现 {len(items)} 个 DSH 安装候选")
    return items


# ── 用户数据根（DSH_HOME）与 profile ────────────────────────────────────────
def looks_like_dsh_home(path: Path) -> bool:
    """判据：里面有 profiles\\ 且至少含一个像 profile 的子目录。"""
    prof = path / "profiles"
    if not prof.is_dir():
        return False
    for child in prof.iterdir():
        if child.is_dir() and child.name.lower() != "node_modules":
            return True
    return False


def discover_dsh_homes(explicit: str = "") -> list[Path]:
    cands: list[Path] = []
    if explicit:
        cands.append(Path(explicit))
    for env in ("DSH_HOME", "DEEPSEEK_HOME"):
        val = os.environ.get(env)
        if val:
            cands.append(Path(val))
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    cands.append(home / ".dsh")
    # Electron user-data 目录旁证（只用来提示，不作为写入目标）
    cands += [Path("C:/home/.dsh"), Path("F:/home/.dsh")]
    out: list[Path] = []
    seen: set[str] = set()
    for c in cands:
        try:
            key = str(c.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        if looks_like_dsh_home(c):
            out.append(c)
    return out


def list_profiles(dsh_home: Path) -> list[str]:
    prof = dsh_home / "profiles"
    if not prof.is_dir():
        return []
    return sorted(
        c.name for c in prof.iterdir()
        if c.is_dir() and c.name.lower() != "node_modules" and not c.name.startswith(".")
    )


def pick_dsh_home(explicit: str, log: Log) -> Path | None:
    homes = discover_dsh_homes(explicit)
    if not homes:
        log.err("没找到 DSH 用户数据目录（要有 profiles 子目录）")
        log.info("可以手动指定：--dsh-home \"D:\\你的\\.dsh\"")
        return None
    if len(homes) == 1:
        return homes[0]
    log.warn(f"发现 {len(homes)} 个候选数据目录，按顺序取第一个（可用 --dsh-home 指定）")
    for h in homes:
        log.info(f"  候选：{h}")
    return homes[0]


# ── 人格合成 ────────────────────────────────────────────────────────────────
PERSONA_NAME = "persona.md"
POLICY_NAME = "政策.md"
AGENTS_NAME = "AGENTS.md"
LEDGER_DIR = "状态"
LEDGER_NAME = "破甲护照.json"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_policy(text: str) -> str:
    """把政策正文里的旧口令统一换成本项目的口令行。"""
    text = re.sub(
        r"^\s*破甲已生效[｜|].*$", "    " + SELF_CHECK_REPLY, text, flags=re.M
    )
    return text.rstrip() + "\n"


def compose(persona_path: Path, policy_path: Path) -> str:
    """人格在前（权重最高），内置政策在后。"""
    persona = read_text(persona_path).strip()
    policy = normalize_policy(read_text(policy_path))
    if not persona:
        raise RuntimeError(f"{persona_path.name} 是空的")
    if not policy:
        raise RuntimeError(f"{policy_path.name} 是空的")
    return persona + "\n\n" + policy


def resolve_sources(persona_arg: str = "") -> tuple[Path, Path]:
    persona = Path(persona_arg) if persona_arg else HERE / PERSONA_NAME
    policy = HERE / POLICY_NAME
    missing = [p for p in (persona, policy) if not p.is_file()]
    if missing:
        names = "、".join(str(p) for p in missing)
        raise RuntimeError(
            f"缺文件：{names}\n"
            f"       {PERSONA_NAME} 与 {POLICY_NAME} 必须和 {APP_NAME}.py 放在同一目录。"
        )
    return persona, policy


# ── L1：用户全局指令文件 $DSH_HOME/AGENTS.md ────────────────────────────────
def l1_target(dsh_home: Path) -> Path:
    return dsh_home / AGENTS_NAME


def l1_probe(dsh_home: Path, expect: str) -> dict:
    """只读地看 L1 当前是什么状态。"""
    target = l1_target(dsh_home)
    info = {"path": target, "exists": target.is_file(), "same": False,
            "bytes": 0, "sha256": "", "backup": None}
    bak = target.with_name(target.name + ".pojiabak")
    if bak.is_file():
        info["backup"] = bak
    if info["exists"]:
        cur = read_text(target)
        info["bytes"] = len(target.read_bytes())
        info["sha256"] = sha256_file(target)
        info["same"] = cur.strip() == expect.strip()
    return info


def apply_l1(dsh_home: Path, text: str, log: Log, passport: Passport,
             dry_run: bool = False) -> bool:
    target = l1_target(dsh_home)
    want = sha256_of_text(text)
    probe = l1_probe(dsh_home, text)
    log.section("L1 用户全局指令文件")
    log.info(f"目标：{target}")
    if probe["exists"] and probe["same"]:
        log.ok(f"已经是最新内容（{probe['bytes']} 字节，sha256 {probe['sha256'][:16]}…），跳过")
        return False
    if probe["exists"]:
        log.info(f"当前：{probe['bytes']} 字节，sha256 {probe['sha256'][:16]}…  →  将替换")
    else:
        log.info("当前：文件不存在，将新建")
    log.info(f"写入：{len(text.encode('utf-8'))} 字节，sha256 {want[:16]}…")
    if dry_run:
        log.line("  （预演模式，磁盘未改动）", C.DIM)
        return False
    bak = backup_once(target, ".pojiabak")
    if bak:
        log.ok(f"已备份原文件 → {bak.name}")
    atomic_write(target, text)
    if not l1_probe(dsh_home, text)["same"]:
        log.err("写入后校验失败，请检查磁盘权限")
        return False
    passport.record("l1", str(target), {"sha256": want, "bytes": len(text.encode('utf-8'))})
    log.ok("已写入并校验通过（UTF-8 无 BOM，原子替换）")
    log.line(f"  {C.GR}下一句对话就生效，不需要重启 DSH{C.R}")
    return True


def revert_l1(dsh_home: Path, log: Log, passport: Passport, dry_run: bool = False) -> bool:
    target = l1_target(dsh_home)
    bak = target.with_name(target.name + ".pojiabak")
    log.section("L1 还原")
    if bak.is_file():
        log.info(f"用备份还原：{bak.name}（{bak.stat().st_size} 字节）")
        if dry_run:
            log.line("  （预演模式，磁盘未改动）", C.DIM)
            return False
        shutil.copy2(bak, target)
        bak.unlink()
        passport.drop("l1", str(target))
        log.ok("已还原成官方原样（备份文件已消费）")
        return True
    if not target.exists():
        log.ok("文件本来就不存在，无需还原")
        return False
    log.warn("没有备份可用。")
    log.line(f"  如果你确认这份 {AGENTS_NAME} 是装破甲时创建的，可以手动删除：")
    log.line(f"      {target}", C.DIM)
    log.line("  为安全起见，脚本不自动删除「没有备份记录」的文件。", C.DIM)
    return False


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── L2（实验性，默认不启用） ────────────────────────────────────────────────
def l2_probe(dsh_home: Path) -> dict:
    """只读地看官方 preset 定义在哪、有没有被 profile patch 覆盖过。"""
    out = {"asar": None, "preset_yml": None, "patched": [], "note": ""}
    prof_root = dsh_home / "profiles"
    for prof in list_profiles(dsh_home):
        patch = prof_root / prof / "cordis.patch.yml"
        if patch.is_file() and "preset-standard" in read_text(patch):
            out["patched"].append(patch)
    return out


# ── L3：旧 preset 目录（新版已废弃） ────────────────────────────────────────
def l3_probe(dsh_home: Path) -> dict:
    legacy = dsh_home / ".agent-presets"
    items = []
    if legacy.is_dir():
        for child in sorted(legacy.iterdir()):
            if child.is_dir() and not child.name.startswith("_"):
                comp = child / "agent.cordis.yml"
                items.append({"id": child.name, "composition": comp.is_file(),
                              "bytes": comp.stat().st_size if comp.is_file() else 0})
    return {"path": legacy, "exists": legacy.is_dir(), "items": items}


# ── 报告 ────────────────────────────────────────────────────────────────────
def banner(log: Log) -> None:
    log.line("")
    log.line(f"{C.CY}╔{'═' * 62}╗{C.R}")
    log.line(f"{C.CY}║{C.R}  {C.WH}{APP_NAME}  v{VERSION}{C.R}    DeepSeek Harness 专用人格装载台" + " " * 12 + f"{C.CY}║{C.R}")
    log.line(f"{C.CY}║{C.R}  {C.DIM}一套人格，一个脚本；只写你自己的文件，官方目录一个字节不碰{C.R}" + " " * 3 + f"{C.CY}║{C.R}")
    log.line(f"{C.CY}╚{'═' * 62}╝{C.R}")


def render_status(installs: list[Install], dsh_home: Path | None, profiles: list[str],
                  l1: dict | None, log: Log, expect_bytes: int = 0) -> None:
    log.section("DSH 安装")
    if not installs:
        log.err("没找到任何 DSH 安装（--pick 可手动指定目录）")
    for ins in installs:
        mark = f"{C.GR}●运行中{C.R}" if ins.running else f"{C.DIM}○未运行{C.R}"
        log.line(f"  [{mark}] {ins.root}")
        log.line(f"           {ins.label}   来源：{ins.source}   通道：{ins.channel or '?'}")

    log.section("用户数据目录（DSH_HOME）")
    if dsh_home:
        log.line(f"  {dsh_home}")
        log.line(f"  profile：{', '.join(profiles) if profiles else '（无）'}")
    else:
        log.err("没找到（判据：目录下有 profiles 且含 profile 子目录）")

    log.section("注入层")
    if l1:
        if l1["exists"] and l1["same"]:
            log.ok(f"L1 全局指令  {l1['path']}")
            log.line(f"              已就位：{l1['bytes']} 字节，sha256 {l1['sha256'][:16]}…")
            log.line(f"              {C.GR}下一句对话即生效，无需重启{C.R}")
        elif l1["exists"]:
            log.warn(f"L1 全局指令  {l1['path']}")
            log.line(f"              存在但不是本项目的内容：{l1['bytes']} 字节"
                     + (f"（期望 {expect_bytes} 字节）" if expect_bytes else ""))
            log.line(f"              跑 [1] 一键破甲 即可覆盖（改前自动备份）")
        else:
            log.line(f"L1 全局指令  {l1['path']}")
            log.line(f"              {C.DIM}未写入 —— 跑 [1] 一键破甲{C.R}")
        if l1["backup"]:
            log.line(f"              备份：{l1['backup'].name}")
    if dsh_home:
        l3 = l3_probe(dsh_home)
        if l3["exists"]:
            log.warn(f"L3 旧 preset 目录  {l3['path']}（{len(l3['items'])} 个）")
            log.line("              " + ", ".join(i["id"] for i in l3["items"]))
            log.line(f"              {C.DIM}新版 DSH 已不扫描该目录，装了也不会出现在选择器里{C.R}")
        else:
            log.line(f"L3 旧 preset 目录  {C.DIM}不存在（正常）{C.R}")


def render_diagnose(installs: list[Install], dsh_home: Path | None, log: Log,
                    persona_path: Path | None, policy_path: Path | None,
                    text: str = "") -> None:
    log.section("安装取证")
    for ins in installs:
        log.line(f"  根目录      {ins.root}")
        log.line(f"  架构        {ins.arch}")
        log.line(f"  版本        {ins.version or '未知'}")
        log.line(f"  更新通道    {ins.channel or '未知'}")
        log.line(f"  app.asar    {'有' if ins.asar_path.is_file() else '无'}"
                 + (f"（{ins.asar_path.stat().st_size:,} 字节）" if ins.asar_path.is_file() else ""))
        if ins.arch == "new" and ins.asar_path.is_file():
            try:
                a = Asar(ins.asar_path)
                try:
                    log.line(f"  asar 条目   {len(a.entries):,} 个")
                    for label, needle in (("注入器", INSTRUCTIONS_PACKAGE),
                                          ("预设注册表", PRESET_PACKAGE),
                                          ("官方模式", WEB_APP_PRESETS)):
                        hits = a.find(needle)
                        log.line(f"  {label:<10} {'✓ ' + str(len(hits)) + ' 项' if hits else '✗ 未找到'}")
                finally:
                    a.close()
            except Exception as exc:
                log.line(f"  asar 读取失败：{exc}")
        log.line("")

    if persona_path and policy_path:
        log.section("人格与政策")
        for p in (persona_path, policy_path):
            log.line(f"  {p.name:<12} {p.stat().st_size:>7,} 字节   {p}")
        if text:
            log.line(f"  合成结果     {len(text.encode('utf-8')):>7,} 字节   "
                     f"sha256 {sha256_of_text(text)[:32]}…")
        log.line(f"  自证口令     发送「{SELF_CHECK_PHRASE}」→ 预期只回一行：")
        log.line(f"               {C.GR}{SELF_CHECK_REPLY}{C.R}")


def run_check(installs: list[Install], dsh_home: Path | None, text: str, log: Log) -> int:
    """体检：只看事实，不改任何东西，也不提问。"""
    problems = 0
    log.section("体检")
    if not installs:
        log.err("未发现 DSH 安装")
        problems += 1
    else:
        ins = installs[0]
        log.ok(f"安装：{ins.root.name}  {ins.label}")
        if ins.arch == "new" and ins.asar_path.is_file():
            try:
                a = Asar(ins.asar_path)
                try:
                    has_instr = bool(a.find(INSTRUCTIONS_PACKAGE))
                    if has_instr:
                        log.ok("宿主内含 agent-instructions（L1 的依据）")
                    else:
                        log.warn("宿主内未见 agent-instructions —— AGENTS.md 可能不会被注入")
                        problems += 1
                finally:
                    a.close()
            except Exception as exc:
                log.warn(f"asar 读取失败，跳过宿主检查：{exc}")
    if not dsh_home:
        log.err("未找到用户数据目录")
        problems += 1
    else:
        log.ok(f"数据目录：{dsh_home}")
        profiles = list_profiles(dsh_home)
        log.ok(f"profile：{', '.join(profiles) if profiles else '（无）'}") if profiles else log.warn("没有可用 profile")
        l1 = l1_probe(dsh_home, text)
        if l1["exists"] and l1["same"]:
            log.ok(f"L1 已就位（{l1['bytes']} 字节）")
        elif l1["exists"]:
            log.warn(f"L1 内容与本项目不一致（{l1['bytes']} 字节）—— 跑一次 [1] 一键破甲")
        else:
            log.warn("L1 未写入 —— 跑一次 [1] 一键破甲")
        l3 = l3_probe(dsh_home)
        if l3["exists"]:
            log.info(f"L3 旧目录存在（{len(l3['items'])} 个），新版不扫描，可留可删")
    log.line("")
    if problems == 0:
        log.line(f"  {C.GR}体检结论：未发现问题{C.R}")
    else:
        log.line(f"  {C.YL}体检结论：{problems} 处需要注意{C.R}")
    return problems


def print_selfcheck(log: Log) -> None:
    log.section("自证口令")
    log.line("  在 DSH 里【新建一个会话】，单独发送这四个字：")
    log.line(f"      {C.CY}{SELF_CHECK_PHRASE}{C.R}")
    log.line("  预期只回一行（不多一个字、不带解释）：")
    log.line(f"      {C.GR}{SELF_CHECK_REPLY}{C.R}")
    log.line("")
    log.line(f"  {C.DIM}如果回复带解释、加戏或拒答，说明人格没真正载入；{C.R}")
    log.line(f"  {C.DIM}先跑 [2] 检测状态确认 L1 已就位，再确认发的是【新会话】。{C.R}")


# ── 交互 ────────────────────────────────────────────────────────────────────
def can_ask(args) -> bool:
    """只读路径绝不提问；非交互（--yes / --quiet / 管道）也不提问。"""
    if getattr(args, "yes", False) or getattr(args, "quiet", False):
        return False
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


MENU = """
  {cy}[1]{r} 一键破甲        {d}写入全局人格（L1）{r}
  {cy}[2]{r} 检测状态        {d}只读，不改任何文件   ← 建议先看这个{r}
  {cy}[3]{r} 诊断详情        {d}逐项取证：安装 / 版本 / 宿主能力{r}
  {cy}[4]{r} 预演            {d}只显示会改什么，磁盘一个字节不动{r}
  {cy}[5]{r} 还原            {d}用备份还原成官方原样{r}
  {cy}[6]{r} 自证口令        {d}打印验收步骤 + 预期回执{r}
  {cy}[0]{r} 退出
"""


def menu_loop(args, log: Log) -> int:
    while True:
        log.line("")
        log.line(MENU.format(cy=C.CY, r=C.R, d=C.DIM))
        try:
            choice = input("  请选择 (0-6) > ").strip()
        except (EOFError, KeyboardInterrupt):
            log.line("")
            return 0
        if choice in ("0", "q", "Q", ""):
            return 0
        if choice == "1":
            args.apply = True
            do_apply(args, log)
        elif choice == "2":
            args.status = True
            do_status(args, log)
        elif choice == "3":
            args.diagnose = True
            do_diagnose(args, log)
        elif choice == "4":
            args.dry_run = True
            do_apply(args, log)
        elif choice == "5":
            args.revert = True
            do_revert(args, log)
        elif choice == "6":
            print_selfcheck(log)
        else:
            log.warn("请输入 0-6")


# ── 主流程 ──────────────────────────────────────────────────────────────────
def setup(args, log: Log | None = None) -> tuple[Log, Passport, list[Install], Path | None, str]:
    if log is None:
        log = Log(HERE / "破甲日志.txt", quiet=getattr(args, "quiet", False))
    log.only_file(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S}  {APP_NAME} v{VERSION}  argv={sys.argv[1:]} =====")
    passport = Passport(HERE / LEDGER_DIR / LEDGER_NAME)
    installs = discover_installs(log)
    dsh_home = pick_dsh_home(getattr(args, "dsh_home", "") or "", log)
    text = ""
    try:
        persona, policy = resolve_sources(getattr(args, "persona", "") or "")
        text = compose(persona, policy)
    except RuntimeError as exc:
        log.err(str(exc))
    return log, passport, installs, dsh_home, text


def do_status(args, log: Log) -> int:
    _, _, installs, dsh_home, text = _cache_or_setup(args)
    render_status(installs, dsh_home, list_profiles(dsh_home) if dsh_home else [],
                  l1_probe(dsh_home, text) if (dsh_home and text) else None,
                  log, len(text.encode("utf-8")))
    return 0


def do_diagnose(args, log: Log) -> int:
    _, _, installs, dsh_home, text = _cache_or_setup(args)
    persona = policy = None
    try:
        persona, policy = resolve_sources(getattr(args, "persona", "") or "")
    except RuntimeError:
        pass
    render_diagnose(installs, dsh_home, log, persona, policy, text)
    if dsh_home:
        print_selfcheck(log)
    return 0


def do_apply(args, log: Log) -> int:
    _, passport, installs, dsh_home, text = _cache_or_setup(args)
    if not text:
        log.err("人格文本合成失败，已中止")
        return 2
    if not dsh_home:
        log.err("没找到 DSH 用户数据目录，已中止（--dsh-home 可手动指定）")
        return 2
    dry = bool(getattr(args, "dry_run", False))
    if any(i.running for i in installs):
        log.warn("DSH 正在运行 —— 不影响 L1（下一句对话即生效），不需要重启，也不结束进程")
    if dry:
        log.section("预演（磁盘一个字节都不会改）")
    apply_l1(dsh_home, text, log, passport, dry_run=dry)
    log.line("")
    print_selfcheck(log)
    return 0


def do_revert(args, log: Log) -> int:
    _, passport, _, dsh_home, _ = _cache_or_setup(args)
    if not dsh_home:
        log.err("没找到 DSH 用户数据目录，已中止")
        return 2
    if can_ask(args) and not getattr(args, "yes", False):
        ans = input(f"  确认把 {dsh_home / AGENTS_NAME} 还原成官方原样？(y/N) > ").strip().lower()
        if ans not in ("y", "yes"):
            log.line("  已取消")
            return 0
    revert_l1(dsh_home, log, passport, dry_run=bool(getattr(args, "dry_run", False)))
    return 0


_CTX: dict = {}


def _cache_or_setup(args):
    if "log" not in _CTX:
        log, passport, installs, dsh_home, text = setup(args)
        _CTX.update({"log": log, "passport": passport, "installs": installs,
                     "dsh_home": dsh_home, "text": text})
    c = _CTX
    return c["log"], c["passport"], c["installs"], c["dsh_home"], c["text"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=f"{APP_NAME}.py",
        description=f"{APP_NAME} v{VERSION} —— 给 DeepSeek Harness 注入操作者人格（只写你自己的文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            f"  python {APP_NAME}.py --status          # 只读：看状态\n"
            f"  python {APP_NAME}.py --dry-run         # 预演：不改盘\n"
            f"  python {APP_NAME}.py --apply --yes     # 一键破甲\n"
            f"  python {APP_NAME}.py --revert --yes    # 还原\n"
            f"  python {APP_NAME}.py --persona 我.md   # 换人格文件\n"
        ),
    )
    p.add_argument("--status", action="store_true", help="只读：检测状态")
    p.add_argument("--diagnose", action="store_true", help="只读：详细取证")
    p.add_argument("--check", action="store_true", help="只读：体检")
    p.add_argument("--dry-run", action="store_true", help="预演，不改任何文件")
    p.add_argument("--apply", action="store_true", help="写入人格（L1）")
    p.add_argument("--revert", action="store_true", help="还原成官方原样")
    p.add_argument("--selfcheck", action="store_true", help="打印自证口令步骤")
    p.add_argument("--persona", default="", metavar="文件", help="自定义人格文件")
    p.add_argument("--dsh-home", default="", metavar="目录", help="手动指定 DSH 用户数据目录")
    p.add_argument("--yes", "-y", action="store_true", help="非交互，不二次确认")
    p.add_argument("--quiet", action="store_true", help="静默：不打印、不提问（仍写日志）")
    p.add_argument("--version", action="version", version=f"{APP_NAME} v{VERSION}")
    return p


def main(argv: list[str] | None = None) -> int:
    enable_utf8()
    args = build_parser().parse_args(argv)

    # 纯只读的几条：不提问、不落盘、不建目录
    if args.selfcheck and not any((args.status, args.diagnose, args.check, args.apply, args.revert)):
        log = Log(HERE / "破甲日志.txt", quiet=args.quiet, file_write=False)
        banner(log)
        print_selfcheck(log)
        return 0

    # 只读路径（状态/取证/体检/预演）一律不落盘、不建目录、不提问
    readonly = bool(args.status or args.diagnose or args.check or args.selfcheck
                    or (args.dry_run and not args.apply and not args.revert))
    log = Log(HERE / "破甲日志.txt", quiet=args.quiet, file_write=not readonly)
    _, passport, installs, dsh_home, text = setup(args, log)
    _CTX.update({"log": log, "passport": passport, "installs": installs,
                 "dsh_home": dsh_home, "text": text})

    if not args.quiet:
        banner(log)

    if args.status:
        return do_status(args, log)
    if args.diagnose:
        return do_diagnose(args, log)
    if args.check:
        return run_check(installs, dsh_home, text, log)
    if args.revert:
        return do_revert(args, log)
    if args.apply:
        return do_apply(args, log)
    if args.dry_run:
        args.dry_run = True
        return do_apply(args, log)

    # 无参 = 交互菜单
    if can_ask(args):
        return menu_loop(args, log)
    # 管道 / 非交互：默认只读状态
    render_status(installs, dsh_home, list_profiles(dsh_home) if dsh_home else [],
                  l1_probe(dsh_home, text) if (dsh_home and text) else None,
                  log, len(text.encode("utf-8")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  已中断")
        sys.exit(130)

