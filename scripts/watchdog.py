#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watchdog.py — VASP 运行看门狗（托管模式）

看门狗自己启动 VASP 子进程，周期性体检计算过程；发现致命问题时：
  1) 归档现场（OSZICAR / OUTCAR / CONTCAR / INCAR）
  2) 写一份诊断报告（markdown）
  3) 杀掉整个进程组（避免 MPI 残留进程占着内存和 CPU）
  4) 生成 INCAR.patch，并按策略自动应用后重启（默认最多 2 次）

用法
----
托管运行（推荐，看门狗替你启动 VASP，能干净杀掉整个 MPI 进程组）：
    python watchdog.py -- mpirun -np 4 vasp_std

常用参数：
    --check-every 20    每新增 20 步体检一次（离子步优先，无离子步时按电子步）
    --heartbeat 60      每 60 秒做一次心跳（查进程是否还活着、OSZICAR 有没有长）
    --max-retries 2     致命问题自动重启的最大次数（设为 0 表示不重启）
    --no-restart        只停机，不自动重启
    --no-stop           只报告不停机（第一次试跑、想先看看判据是否合理时用）

事后诊断（不启动 VASP，只对已有日志做一次体检，便于复现和测试）：
    python watchdog.py --dry-run
    python watchdog.py --dry-run --outcar OUTCAR --oszicar OSZICAR

输出文件（全部落在 --workdir，默认当前目录）
--------------------------------------------
    watchdog.log                       运行日志
    watchdog_monitor.csv               过程数据（与 monitor_to_csv.py 同一套列）
    watchdog_report_<时间>.md           诊断报告（体检发现问题时）
    watchdog_snapshots/<标签>/          现场快照
    INCAR.patch                        可选的修复建议（未自动应用时供人工审查）

退出码
------
    0  VASP 正常结束，且体检未发现致命问题
    2  VASP 正常结束，但体检发现致命问题（未触发停机，因为是终检）
    3  看门狗主动停机（致命问题）
    4  看门狗主动停机且自动重试用尽
    1  看门狗自身异常（命令未找到等）
"""

import argparse
import csv
import glob
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime

# ----------------------------------------------------------------------------
# 全局常量
# ----------------------------------------------------------------------------

LEVEL_OK = "OK"
LEVEL_WARN = "WARN"
LEVEL_FATAL = "FATAL"

# 体检阈值（集中在这里，方便按经验调整）
THRESHOLDS = {
    # SCF：电子步数达到 NELM 的这个比例就提醒（再往上就要不收敛了）
    "scf_near_nelm_ratio": 0.80,
    # SCF：连续多少个离子步都贴着上限，才算「持续」
    "scf_near_nelm_streak": 3,
    # 能量：相邻离子步能量跳变超过这个值（eV）算剧烈震荡
    "energy_jump": 5.0,
    # 能量：最近 6 步里 dE 变号次数超过这个值算震荡
    "energy_sign_flip": 4,
    # 能量：每原子能量的合理区间（eV/atom），超出就提醒
    "energy_per_atom_min": -20.0,
    "energy_per_atom_max": 5.0,
    # 力：最近 3 步最大力严格递增且最新值超过这个（eV/Å）算走偏
    "force_increase_threshold": 0.5,
    # 结构：最小原子间距下限（Å）
    "min_distance": 0.5,
    # 结构：ISIF=3 时允许的最大体积变化比例
    "max_volume_change": 0.30,
    # 磁矩：总磁矩小于初始 MAGMOM 绝对值和这个比例，算磁矩被抹掉
    "magnetization_lost_ratio": 0.10,
    # 耗时：单步耗时超过历史中位数的这个倍数算异常
    "timing_spike_ratio": 10.0,
    "timing_fatal_ratio": 50.0,
}

# OUTCAR 里出现即视为致命的关键词（大小写敏感，都是 VASP 的原文措辞）
OUTCAR_FATAL_PATTERNS = [
    "ZBrent",                              # 电子步求根失败
    "Error EDDDAV",                        # Davidson 对角化失败
    "PSMAXN",                              # 波函数太大，通常 ENCUT/内存问题
    "BAD TERMINATION",                     # 异常终止
    "Fatal error",
    "internal error",
    "LAPACK: Routine ZPOTRF failed",
    "VERY BAD NEWS",                       # 电荷密度/结构严重异常
    "NaNs",                                # 数值溢出
]

# OUTCAR 里出现只算警告的
OUTCAR_WARN_PATTERNS = [
    "Sub-Space-Matrix is not hermitian",
    "WARNING: DENTET",
    "your system is metallic but you are using ISMEAR = -5",
    "BRMIX: very serious problems",
    "PSSYEVX",
]


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------

def now_tag():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_text(path, max_bytes=None):
    """读文件；max_bytes 指定时只读尾部（OUTCAR 可能几十 MB）。"""
    try:
        with open(path, "r", errors="replace") as f:
            if max_bytes:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
            return f.read()
    except Exception:
        return ""


def _float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# INCAR / POSCAR 解析
# ----------------------------------------------------------------------------

def parse_incar(path):
    """把 INCAR 解析成 {KEY: 值字符串}。

    兼容三种写法：`NELM = 60`、`NELM=60`、`NELM 60`。
    忽略 # 和 ! 之后的注释。`=` 不能混进值里，否则 ISPIN = 2 会被读成默认值。
    """
    params = {}
    text = read_text(path)
    for line in text.splitlines():
        line = re.split(r"[#!]", line, maxsplit=1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z_][\w\-]*)\s*(?:=\s*)?(.*)$", line)
        if not m:
            continue
        params[m.group(1).upper()] = m.group(2).strip()
    return params


def incar_key(line):
    """从 INCAR 一行中取出参数名（用于改写时定位），无参数名返回 None。"""
    s = re.split(r"[#!]", line, maxsplit=1)[0].strip()
    if not s:
        return None
    m = re.match(r"^([A-Za-z_][\w\-]*)", s)
    return m.group(1).upper() if m else None


def parse_poscar(path):
    """解析 POSCAR，返回 dict（lattice、symbols、counts、coords、cartesian、volume）。"""
    text = read_text(path)
    lines = text.splitlines()
    if len(lines) < 8:
        return None
    try:
        scale = float(lines[1].split()[0])
    except (IndexError, ValueError):
        return None
    try:
        lattice = []
        for i in range(2, 5):
            v = [float(x) for x in lines[i].split()[:3]]
            if len(v) != 3:
                return None
            lattice.append(v)
    except (IndexError, ValueError):
        return None
    lattice = [[c * scale for c in row] for row in lattice]

    idx = 5
    symbols = lines[idx].split()
    if not symbols or not re.match(r"^[A-Za-z]", symbols[0]):
        # VASP4 格式：没有元素行，直接是计数
        symbols = []
        idx -= 1
    idx += 1
    try:
        counts = [int(x) for x in lines[idx].split()]
    except (IndexError, ValueError):
        return None
    idx += 1
    # 可选的 "Selective dynamics"
    cartesian = False
    if idx < len(lines) and re.match(r"^[sS]", lines[idx].strip()):
        idx += 1
    if idx < len(lines) and re.match(r"^[cCkK]", lines[idx].strip()):
        cartesian = True
    idx += 1

    coords = []
    total = sum(counts)
    for i in range(idx, min(idx + total, len(lines))):
        parts = lines[i].split()
        if len(parts) < 3:
            break
        coords.append([_float(parts[0]), _float(parts[1]), _float(parts[2])])
    if len(coords) != total:
        return None

    # 体积
    a, b, c = lattice
    volume = abs(
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )

    # 统一转成笛卡尔坐标
    cart = []
    for p in coords:
        if cartesian:
            cart.append([p[0] * scale, p[1] * scale, p[2] * scale])
        else:
            cart.append([
                p[0] * a[0] + p[1] * b[0] + p[2] * c[0],
                p[0] * a[1] + p[1] * b[1] + p[2] * c[1],
                p[0] * a[2] + p[1] * b[2] + p[2] * c[2],
            ])

    return {
        "lattice": lattice,
        "symbols": symbols,
        "counts": counts,
        "positions": cart,
        "volume": volume,
        "natoms": total,
    }


def min_interatomic_distance(struct, max_atoms=200):
    """最小原子间距（考虑周期性，遍历 27 个镜像）。原子太多时抽样，避免 O(N²) 过慢。"""
    if not struct or struct["natoms"] < 2:
        return None
    pos = struct["positions"]
    lat = struct["lattice"]
    n = len(pos)
    if n > max_atoms:
        pos = pos[:max_atoms]
        n = max_atoms

    # 快速预筛：若两原子在三个晶向上的分数坐标差异都很大，不可能最近
    imgs = []
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            for k in (-1, 0, 1):
                imgs.append([
                    i * lat[0][0] + j * lat[1][0] + k * lat[2][0],
                    i * lat[0][1] + j * lat[1][1] + k * lat[2][1],
                    i * lat[0][2] + j * lat[1][2] + k * lat[2][2],
                ])

    best = float("inf")
    for i in range(n):
        xi, yi, zi = pos[i]
        for j in range(i + 1, n):
            dx0 = pos[j][0] - xi
            dy0 = pos[j][1] - yi
            dz0 = pos[j][2] - zi
            for im in imgs:
                dx = dx0 + im[0]
                dy = dy0 + im[1]
                dz = dz0 + im[2]
                d2 = dx * dx + dy * dy + dz * dz
                if d2 < best:
                    best = d2
    return math.sqrt(best) if best < float("inf") else None


# ----------------------------------------------------------------------------
# OSZICAR / OUTCAR 解析
# ----------------------------------------------------------------------------

def parse_magmom_sum(spec):
    """算 |MAGMOM| 之和，用于判断「磁矩是否被抹平」。

    支持 VASP 的两种写法：`2*3.0`（重复计数）与 `3.0 3.0`（逐个列出）。
    """
    if not spec:
        return 0.0
    total = 0.0
    for tok in spec.replace(",", " ").split():
        m = re.match(r"^(\d+)\*\s*([-+]?[\d.]+)$", tok)
        if m:
            total += abs(_float(m.group(2))) * int(m.group(1))
            continue
        if re.match(r"^[-+]?[\d.]+$", tok):
            total += abs(_float(tok))
    return total


_ION_RE = re.compile(
    r"^\s*(\d+)\s+F=\s*(-?[\d.]+E[+-]\d+)\s+E0=\s*(-?[\d.]+E[+-]\d+)\s+d\s*E\s*=\s*(-?[\d.]+E[+-]\d+)"
)
_SCF_RE = re.compile(
    r"^\s*(RMM|DAV|EDIIS|CG):\s+(\d+)\s+(-?[\d.]+E[+-]\d+)\s+(-?[\d.]+E[+-]\d+)\s+(-?[\d.]+E[+-]\d+)\s+(\d+)\s+(-?[\d.]+E[+-]\d+)?"
)


def parse_oszicar(text):
    """解析 OSZICAR，返回 (ionic_steps, electronic_steps)。

    ionic_steps: [{"index", "E0", "F", "dE", "n_scf"}]
    electronic_steps: [{"ionic", "iter", "energy", "dE", "time"}]
    """
    ionic = []
    electronic = []
    cur = None
    n_scf = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _ION_RE.match(s)
        if m:
            if cur is not None:
                cur["n_scf"] = n_scf
            cur = {
                "index": int(m.group(1)),
                "E0": _float(m.group(3)),
                "F": _float(m.group(2)),
                "dE": _float(m.group(4)),
                "n_scf": 0,
            }
            ionic.append(cur)
            n_scf = 0
            continue
        m = _SCF_RE.match(s)
        if m:
            n_scf += 1
            electronic.append({
                "ionic": cur["index"] if cur else 0,
                "iter": int(m.group(2)),
                "energy": _float(m.group(3)),
                "dE": _float(m.group(5)),
                "time": _float(m.group(7)) if m.group(7) else None,
            })
    if cur is not None:
        cur["n_scf"] = n_scf
    return ionic, electronic


_FORCE_HEADER = "TOTAL-FORCE (eV/Angst)"


def parse_max_forces(outcar_text):
    """从 OUTCAR 提取每个离子步的最大力模（eV/Å）。"""
    forces = []
    lines = outcar_text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        if _FORCE_HEADER in lines[i]:
            j = i + 1
            while j < n and (not lines[j].strip() or lines[j].strip().startswith("-")):
                j += 1
            mx = 0.0
            cnt = 0
            while j < n:
                s = lines[j].strip()
                if not s or s.startswith("-") or s.lower().startswith("total drift"):
                    break
                parts = s.split()
                if len(parts) < 6:
                    break
                fx, fy, fz = _float(parts[3]), _float(parts[4]), _float(parts[5])
                mx = max(mx, math.sqrt(fx * fx + fy * fy + fz * fz))
                cnt += 1
                j += 1
            if cnt:
                forces.append(mx)
            i = j
        else:
            i += 1
    return forces


_MAG_RE = re.compile(r"number of electron\s+[\d.]+\s+magnetization\s+(-?[\d.]+)")


def parse_magnetization(outcar_text):
    """取 OUTCAR 中最后一次出现的总磁矩（μB）。找不到返回 None。"""
    last = None
    for m in _MAG_RE.finditer(outcar_text):
        last = _float(m.group(1))
    return last


_LOOP_RE = re.compile(r"LOOP\+:\s+cpu time\s+([\d.]+):\s+real time\s+([\d.]+)")


def parse_loop_times(outcar_text):
    """从 OUTCAR 的 LOOP+ 行提取每离子步真实耗时（秒）。"""
    return [_float(m.group(2)) for m in _LOOP_RE.finditer(outcar_text)]


# ----------------------------------------------------------------------------
# 诊断
# ----------------------------------------------------------------------------

class Finding:
    def __init__(self, level, code, title, detail, patch=None, restartable=True):
        self.level = level
        self.code = code
        self.title = title
        self.detail = detail
        self.patch = patch or {}
        self.restartable = restartable

    def __str__(self):
        return f"[{self.level}] {self.title} — {self.detail}"


# 常见问题的修复配方（可直接写进 INCAR）
PATCH_SCF_HARD = {"ALGO": "Normal", "AMIX": "0.2", "BMIX": "0.0001", "NELM": "200"}
PATCH_SCF_MIX = {"AMIX": "0.3", "BMIX": "0.0001", "NELM": "150"}
PATCH_IONIC = {"IBRION": "1", "POTIM": "0.2"}
PATCH_SOFT = {"POTIM": "0.1"}


def diagnose(state, cfg):
    """对当前状态做一次完整体检，返回 Finding 列表（只返回 WARN / FATAL）。"""
    findings = []

    ionic = state["ionic"]
    forces = state["forces"]
    th = THRESHOLDS

    nelm = int(_float(cfg["incar"].get("NELM", 60), 60) or 60)
    ispin = int(_float(cfg["incar"].get("ISPIN", 1), 1) or 1)
    isif = int(_float(cfg["incar"].get("ISIF", 2), 2) or 2)
    ibrion = int(_float(cfg["incar"].get("IBRION", -1), -1))
    nsw = int(_float(cfg["incar"].get("NSW", 0), 0) or 0)
    natoms = state["natoms"] or 0

    # 静态计算（单点能 / DOS / 能带）整场只跑一次 SCF，「连续 3 个离子步」的判据永远不成立，
    # 必须单独用「这一次 SCF 的电子步数」判断，否则 SCF 顶满 NELM 也会被判为正常。
    static_calc = (ibrion in (-1, 0)) or nsw <= 1

    # 正在进行的 SCF 循环已经走了多少电子步（OSZICAR 里 ionic=0 表示还没写 F= 行）
    cur_scf = 0
    if state["electronic"]:
        last_ionic = state["electronic"][-1]["ionic"]
        cur_scf = sum(1 for e in state["electronic"] if e["ionic"] == last_ionic)

    # --- 1. SCF 是否逼近 / 触及 NELM ---
    # 静态计算只有一次 SCF 机会，顶满即致命；优化计算给 3 步容错再说。
    need_streak = 1 if static_calc else th["scf_near_nelm_streak"]
    if ionic:
        recent = ionic[-need_streak:]
        n_scf_list = [s["n_scf"] for s in recent]
        if len(n_scf_list) >= need_streak and all(x >= nelm for x in n_scf_list):
            findings.append(Finding(
                LEVEL_FATAL, "SCF_NOT_CONVERGED", "电子步未收敛（已顶到 NELM）",
                f"最近 {len(n_scf_list)} 个离子步的电子步数都达到 NELM={nelm}，"
                f"说明 SCF 每步都没收敛。继续跑下去能量不可信。",
                PATCH_SCF_HARD,
            ))
        elif len(n_scf_list) >= 2 and all(x >= nelm * th["scf_near_nelm_ratio"] for x in n_scf_list):
            findings.append(Finding(
                LEVEL_WARN, "SCF_NEAR_NELM", "电子步数逼近 NELM",
                f"最近 {len(n_scf_list)} 个离子步分别用了 {n_scf_list} 个电子步，"
                f"已达 NELM={nelm} 的 {int(th['scf_near_nelm_ratio']*100)}% 以上，收敛余量很小。",
                PATCH_SCF_MIX,
            ))
    elif static_calc and cur_scf >= nelm:
        findings.append(Finding(
            LEVEL_FATAL, "SCF_NOT_CONVERGED_STATIC", "静态计算 SCF 未收敛（已顶到 NELM）",
            f"这是静态计算（IBRION={ibrion}, NSW={nsw}），整场只有这一次 SCF，"
            f"电子步已用满 NELM={nelm} 仍未收敛。此时能量/RES 都是不可信的中间态，"
            f"继续算下去没有意义。",
            PATCH_SCF_HARD,
        ))
    elif cur_scf >= nelm * th["scf_near_nelm_ratio"] and cur_scf < nelm:
        findings.append(Finding(
            LEVEL_WARN, "SCF_NEAR_NELM_CURRENT", "当前 SCF 循环电子步偏多",
            f"当前 SCF 已走 {cur_scf} 个电子步（NELM={nelm}），收敛余量只剩 "
            f"{nelm - cur_scf} 步。",
            PATCH_SCF_MIX,
        ))

    # --- 2. 能量剧烈震荡 ---
    if len(ionic) >= 6:
        last6 = ionic[-6:]
        d = [last6[i]["E0"] - last6[i - 1]["E0"] for i in range(1, len(last6))]
        flips = sum(1 for i in range(1, len(d)) if d[i] * d[i - 1] < 0)
        amps = [abs(x) for x in d]
        if flips >= th["energy_sign_flip"] and max(amps) > 1.0:
            findings.append(Finding(
                LEVEL_WARN, "ENERGY_OSCILLATION", "能量在震荡（不单调下降）",
                f"最近 6 个离子步中能量变化方向反复 {flips} 次，最大跳变 {max(amps):.3f} eV。"
                f"通常是混合参数或步长不合适。",
                PATCH_SCF_MIX,
            ))
        elif max(amps) > th["energy_jump"]:
            findings.append(Finding(
                LEVEL_WARN, "ENERGY_JUMP", "单步能量跳变过大",
                f"最近有一步能量变化达 {max(amps):.3f} eV（阈值 {th['energy_jump']} eV），"
                f"可能存在原子靠得太近或步长过大。",
                PATCH_SOFT,
            ))

    # --- 3. 每原子能量是否落在合理区间 ---
    if ionic and natoms:
        epa = ionic[-1]["E0"] / natoms
        if epa > th["energy_per_atom_max"] or epa < th["energy_per_atom_min"]:
            findings.append(Finding(
                LEVEL_WARN, "ENERGY_UNREALISTIC", "每原子能量超出常见范围",
                f"当前 {epa:.3f} eV/atom，超出常见区间 "
                f"[{th['energy_per_atom_min']}, {th['energy_per_atom_max']}] eV/atom。"
                f"可能是赝势选错、POSCAR 结构异常，或 POTCAR 与 POSCAR 元素不匹配。",
                {}, restartable=False,
            ))

    # --- 4. 最大力是否下降 ---
    if len(forces) >= 3:
        f3 = forces[-3:]
        if f3[0] < f3[1] < f3[2] and f3[2] > th["force_increase_threshold"]:
            findings.append(Finding(
                LEVEL_WARN, "FORCE_INCREASING", "最大力连续上升（优化走偏）",
                f"最近 3 个离子步最大力 {f3[0]:.3f} → {f3[1]:.3f} → {f3[2]:.3f} eV/Å 单调上升，"
                f"正常优化应当整体下降。",
                PATCH_SOFT,
            ))

    # --- 5. 结构是否崩坏 ---
    struct = state.get("structure")
    if struct:
        dmin = min_interatomic_distance(struct)
        if dmin is not None and dmin < th["min_distance"]:
            findings.append(Finding(
                LEVEL_FATAL, "STRUCTURE_COLLAPSE", "结构崩坏（原子重叠）",
                f"当前结构最小原子间距 {dmin:.3f} Å，低于 {th['min_distance']} Å，"
                f"原子已经撞在一起，继续算没有物理意义。",
                PATCH_SOFT,
            ))
        if isif == 3 and state.get("initial_volume"):
            ratio = struct["volume"] / state["initial_volume"] - 1.0
            if abs(ratio) > th["max_volume_change"]:
                findings.append(Finding(
                    LEVEL_FATAL, "VOLUME_DRIFT", "晶胞体积变化异常",
                    f"体积相对初始值变化 {ratio*100:+.1f}%，超过 ±{int(th['max_volume_change']*100)}%。"
                    f"ISIF=3 变胞优化跑飞了。",
                    PATCH_SOFT,
                ))

    # --- 6. 磁矩是否被抹掉 ---
    if ispin == 2:
        mag = state.get("magnetization")
        init_mag = state.get("init_magmom_sum") or 0.0
        if mag is not None and init_mag > 0.1 and abs(mag) < init_mag * th["magnetization_lost_ratio"]:
            findings.append(Finding(
                LEVEL_WARN, "MAGNETIZATION_LOST", "磁矩几乎归零",
                f"当前总磁矩 {mag:.3f} μB，初始 |MAGMOM| 合计 {init_mag:.3f} μB。"
                f"若体系本该有磁性，检查 MAGMOM 初值或结构对称性是否把它抹平了。",
                {}, restartable=False,
            ))

    # --- 7. OUTCAR 致命错误 ---
    tail = state.get("outcar_tail") or ""
    for pat in OUTCAR_FATAL_PATTERNS:
        if pat in tail:
            findings.append(Finding(
                LEVEL_FATAL, "OUTCAR_FATAL", f"OUTCAR 出现致命错误：{pat}",
                "VASP 已在输出中报出致命错误，这个作业不可能正常结束。",
                PATCH_SCF_HARD,
            ))
            break
    for pat in OUTCAR_WARN_PATTERNS:
        if pat in tail:
            findings.append(Finding(
                LEVEL_WARN, "OUTCAR_WARN", f"OUTCAR 警告：{pat}",
                "VASP 报了警告，暂时可能还能跑，但结果需要打折扣看。",
                {}, restartable=False,
            ))
            break

    # --- 8. 单步耗时是否异常飙升 ---
    times = state.get("loop_times") or []
    if len(times) >= 6:
        hist = sorted(times[:-1][-20:])
        med = hist[len(hist) // 2]
        latest = times[-1]
        if med > 0:
            ratio = latest / med
            if ratio >= THRESHOLDS["timing_fatal_ratio"]:
                findings.append(Finding(
                    LEVEL_FATAL, "TIMING_SPIKE", "单步耗时暴涨（疑似内存交换或节点故障）",
                    f"最新一步耗时 {latest:.1f}s，历史中位数 {med:.1f}s，放大 {ratio:.0f} 倍。"
                    f"通常是内存不够开始用 swap，或某个计算节点掉线。",
                    {}, restartable=False,
                ))
            elif ratio >= THRESHOLDS["timing_spike_ratio"]:
                findings.append(Finding(
                    LEVEL_WARN, "TIMING_SLOW", "单步耗时明显变慢",
                    f"最新一步耗时 {latest:.1f}s，历史中位数 {med:.1f}s，放大 {ratio:.1f} 倍。"
                    f"留意内存是否吃紧、是否有其他进程抢 CPU。",
                    {}, restartable=False,
                ))

    # --- 9. 进度停滞（心跳）---
    # 注意：这里用的是「看门狗观察到多久没有新产出」，而不是「文件 mtime 距现在多久」。
    # 后者在刚启动时会把「OSZICAR 还没生成」误判成卡死，进而杀掉正常任务。
    gap = state.get("stall_seconds")
    if gap is not None:
        fatal_after = cfg.get("stall_fatal", 3600)
        warn_after = cfg.get("stall_warn", 900)
        if fatal_after > 0 and gap > fatal_after:
            findings.append(Finding(
                LEVEL_FATAL, "STALLED", "计算疑似卡死",
                f"已连续 {gap/60:.0f} 分钟没有任何新输出（OSZICAR 与 OUTCAR 均未更新），"
                f"进程还活着但不产出。超过阈值 {fatal_after/60:.0f} 分钟。",
                {}, restartable=True,
            ))
        elif warn_after > 0 and gap > warn_after:
            findings.append(Finding(
                LEVEL_WARN, "STALL_WARN", "计算进度停滞",
                f"已连续 {gap/60:.0f} 分钟没有新输出（阈值 {warn_after/60:.0f} 分钟），暂时观察。"
                f"大体系单步 SCF 较慢时属正常，若持续变长再介入。",
                {}, restartable=False,
            ))

    return findings


# ----------------------------------------------------------------------------
# 进程控制
# ----------------------------------------------------------------------------

def start_vasp(cmd, workdir, log_path):
    """启动 VASP 子进程。Unix 下新建会话（便于整组杀掉），Windows 下新建进程组。"""
    logf = open(log_path, "a", buffering=1, errors="replace")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd, cwd=workdir, stdout=logf, stderr=subprocess.STDOUT, **kwargs
    )
    return proc, logf


def terminate_tree(proc, grace=10.0):
    """杀掉整个进程组（含所有 MPI 子进程）。返回 True 表示已终止。"""
    if proc is None or proc.poll() is not None:
        return True
    pid = proc.pid
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            )
        except Exception:
            pass
    else:
        # 先 SIGTERM 让 VASP 有机会写 CONTCAR/CHGCAR
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        t0 = time.time()
        while time.time() - t0 < grace:
            if proc.poll() is not None:
                break
            time.sleep(0.3)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    try:
        proc.wait(timeout=15)
    except Exception:
        pass
    return proc.poll() is not None


# ----------------------------------------------------------------------------
# 归档 / 报告 / 修复
# ----------------------------------------------------------------------------

def snapshot(workdir, tag, files=None):
    """把关键现场文件复制到 watchdog_snapshots/<tag>/，返回目录路径。"""
    files = files or ["OSZICAR", "OUTCAR", "CONTCAR", "INCAR", "POSCAR",
                      "KPOINTS", "vasp.log", "vasprun.xml"]
    dest = os.path.join(workdir, "watchdog_snapshots", tag)
    os.makedirs(dest, exist_ok=True)
    copied = []
    for name in files:
        src = os.path.join(workdir, name)
        if os.path.isfile(src):
            try:
                shutil.copy2(src, os.path.join(dest, name))
                copied.append(name)
            except Exception:
                pass
    return dest, copied


def write_report(workdir, findings, state, cfg, tag, action):
    """写一份 markdown 诊断报告，返回路径。"""
    path = os.path.join(workdir, f"watchdog_report_{tag}.md")
    ionic = state.get("ionic") or []
    forces = state.get("forces") or []
    lines = []
    lines.append(f"# VASP 看门狗诊断报告 — {tag}\n")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 工作目录：`{os.path.abspath(workdir)}`")
    lines.append(f"- 关键参数：NELM={cfg['incar'].get('NELM', '未设置(默认60)')}，"
                 f"ISPIN={cfg['incar'].get('ISPIN', '1')}，ISIF={cfg['incar'].get('ISIF', '2')}，"
                 f"IBRION={cfg['incar'].get('IBRION', '未设置')}")
    lines.append(f"- 已跑离子步：{len(ionic)}")
    lines.append(f"- 处理动作：**{action}**")
    lines.append("")

    fatal = [f for f in findings if f.level == LEVEL_FATAL]
    warn = [f for f in findings if f.level == LEVEL_WARN]
    lines.append("## 体检结论\n")
    lines.append(f"- 致命问题 **{len(fatal)}** 项")
    lines.append(f"- 警告 **{len(warn)}** 项\n")

    if fatal or warn:
        lines.append("| 级别 | 问题 | 说明 |")
        lines.append("|------|------|------|")
        for f in findings:
            detail = f.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {f.level} | {f.title} | {detail} |")
        lines.append("")

    if ionic:
        lines.append("## 过程数据\n")
        lines.append("| 离子步 | 能量 E0 (eV) | dE (eV) | 电子步数 | 最大力 (eV/Å) |")
        lines.append("|--------|-------------|---------|----------|---------------|")
        for i, s in enumerate(ionic[-20:]):
            gi = len(ionic) - min(len(ionic), 20) + i
            fval = f"{forces[gi]:.3f}" if gi < len(forces) else "-"
            lines.append(f"| {s['index']} | {s['E0']:.6f} | {s['dE']:.6f} | {s['n_scf']} | {fval} |")
        if len(ionic) > 20:
            lines.append(f"\n（仅显示最后 20 步，共 {len(ionic)} 步）")
        lines.append("")

    patches = {}
    for f in findings:
        patches.update(f.patch)
    if patches:
        lines.append("## 建议的 INCAR 修改\n")
        lines.append("| 参数 | 建议值 |")
        lines.append("|------|--------|")
        for k, v in patches.items():
            lines.append(f"| {k} | {v} |")
        lines.append("")
        lines.append("可直接写入 `INCAR.patch` 后手工合并，或让看门狗自动应用（`--auto-fix`）。\n")

    lines.append("## 原始文件\n")
    if "事后诊断" in action:
        lines.append("本次为事后诊断（dry-run），不启动 VASP、不生成归档快照。"
                     "若需停机前自动归档现场，请用托管模式运行看门狗。\n")
    else:
        lines.append("本次停机前的现场已归档到 `watchdog_snapshots/` 对应目录，"
                     "包含 OSZICAR、OUTCAR、CONTCAR、INCAR 等，可直接复查。\n")
    lines.append("---\n")
    lines.append("*本报告由 vasp-calculation-assistant / watchdog.py 自动生成，"
                 "阈值判据见 references/realtime-monitoring.md。*\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def write_patch(workdir, patches):
    path = os.path.join(workdir, "INCAR.patch")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 由 watchdog.py 生成的修复建议，可手工合并进 INCAR\n")
        f.write(f"# 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        for k, v in patches.items():
            f.write(f"{k} = {v}\n")
    return path


def apply_patch_to_incar(workdir, patches, tag):
    """把 patch 写进 INCAR（先备份）。返回 (成功?, 备份路径)。"""
    src = os.path.join(workdir, "INCAR")
    if not os.path.isfile(src):
        return False, None
    if not patches:
        return False, None
    backup = os.path.join(workdir, f"INCAR.watchdog_bak_{tag}")
    try:
        shutil.copy2(src, backup)
    except Exception:
        return False, None

    text = read_text(src)
    lines = text.splitlines()
    done = set()
    out = []
    for line in lines:
        key = incar_key(line)
        if key and key in patches:
            out.append(f"{key} = {patches[key]}")
            done.add(key)
            continue
        out.append(line)
    for k, v in patches.items():
        if k not in done:
            out.append(f"{k} = {v}")
    try:
        with open(src, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    except Exception:
        return False, backup
    return True, backup


# ----------------------------------------------------------------------------
# 主监控循环
# ----------------------------------------------------------------------------

class Watchdog:
    def __init__(self, cfg):
        self.cfg = cfg
        self.retries = 0
        self.stop_reason = None
        self.exit_code = 0

    # ---- 状态收集 ----
    def collect_state(self):
        wd = self.cfg["workdir"]
        osz_path = os.path.join(wd, "OSZICAR")
        outcar_path = os.path.join(wd, "OUTCAR")

        osz_text = read_text(osz_path)
        ionic, electronic = parse_oszicar(osz_text)

        outcar_text = read_text(outcar_path)
        forces = parse_max_forces(outcar_text)
        mag = parse_magnetization(outcar_text)
        loop_times = parse_loop_times(outcar_text)
        tail = outcar_text[-400000:] if outcar_text else ""

        # 结构：优先 CONTCAR（最新），退回 POSCAR
        struct = None
        for name in ("CONTCAR", "POSCAR"):
            p = os.path.join(wd, name)
            if os.path.isfile(p) and os.path.getsize(p) > 100:
                s = parse_poscar(p)
                if s:
                    struct = s
                    break

        # 产出文件的 mtime 快照：用来判断「有没有新产出」。
        # 真正的停滞时长由监控循环按「距上次观察到变化的时间」计算，
        # 不能用 time.time() - mtime（刚启动时 OSZICAR 可能还不存在或很旧，会误判）。
        mtimes = {}
        for name in ("OSZICAR", "OUTCAR"):
            p = os.path.join(wd, name)
            if os.path.isfile(p):
                try:
                    mtimes[name] = os.path.getmtime(p)
                except Exception:
                    pass

        return {
            "ionic": ionic,
            "electronic": electronic,
            "forces": forces,
            "magnetization": mag,
            "loop_times": loop_times,
            "outcar_tail": tail,
            "structure": struct,
            "natoms": struct["natoms"] if struct else self.cfg.get("natoms", 0),
            "initial_volume": self.cfg.get("initial_volume"),
            "init_magmom_sum": self.cfg.get("init_magmom_sum", 0.0),
            "mtimes": mtimes,
            "stall_seconds": None,
        }

    def dump_csv(self, state):
        """把过程数据写成 CSV（列与 monitor_to_csv.py 完全一致）。"""
        path = os.path.join(self.cfg["workdir"], "watchdog_monitor.csv")
        fields = ["step_type", "step_index", "scf_iteration", "energy",
                  "free_energy", "dE", "elapsed_time", "note"]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for s in state["ionic"]:
                    w.writerow({
                        "step_type": "ionic", "step_index": s["index"],
                        "scf_iteration": "", "energy": f"{s['E0']:.8f}",
                        "free_energy": f"{s['F']:.8f}", "dE": f"{s['dE']:.6f}",
                        "elapsed_time": "", "note": "",
                    })
                for e in state["electronic"]:
                    w.writerow({
                        "step_type": "electronic", "step_index": e["ionic"],
                        "scf_iteration": e["iter"], "energy": f"{e['energy']:.8f}",
                        "free_energy": "", "dE": f"{e['dE']:.6f}",
                        "elapsed_time": e["time"] if e["time"] else "",
                        "note": "",
                    })
        except Exception:
            pass

    # ---- 处理致命问题 ----
    def handle_fatal(self, findings, state, stage):
        tag = f"{now_tag()}_retry{self.retries}"
        snap_dir, copied = snapshot(self.cfg["workdir"], tag)

        patches = {}
        restartable = True
        for f in findings:
            if f.level == LEVEL_FATAL:
                patches.update(f.patch)
                if not f.restartable:
                    restartable = False

        action = "停机" if self.cfg["stop_on_fatal"] else "仅报告（--no-stop）"
        report = write_report(self.cfg["workdir"], findings, state, self.cfg, tag, action)
        if patches:
            write_patch(self.cfg["workdir"], patches)

        self.log("")
        self.log("=" * 68)
        self.log(f"[FATAL] 发现 {sum(1 for f in findings if f.level == LEVEL_FATAL)} 个致命问题")
        for f in findings:
            if f.level == LEVEL_FATAL:
                self.log(f"        · {f.title}：{f.detail}")
        self.log(f"[FATAL] 现场已归档：{snap_dir}（{', '.join(copied) or '无文件'}）")
        self.log(f"[FATAL] 诊断报告：{report}")
        if patches:
            self.log(f"[FATAL] 修复建议：{', '.join(f'{k}={v}' for k, v in patches.items())}")
        self.log("=" * 68)

        if not self.cfg["stop_on_fatal"]:
            self.log("[INFO] --no-stop 已启用，继续观察，不停机。")
            return "continue"

        # 停机
        if self.proc is not None:
            self.log("[STOP] 正在终止 VASP 进程组（含所有 MPI 子进程）…")
            ok = terminate_tree(self.proc)
            self.log(f"[STOP] {'已终止' if ok else '终止可能未完成，请手动检查 ps'}")

        # 是否尝试重启
        if not self.cfg["auto_restart"]:
            self.log("[STOP] --no-restart 已启用，不自动重启。")
            self.exit_code = 3
            self.stop_reason = "fatal_no_restart"
            return "stop"

        if not restartable:
            self.log("[STOP] 本次问题无法通过改 INCAR 修复（需人工判断），不自动重启。")
            self.exit_code = 3
            self.stop_reason = "fatal_not_restartable"
            return "stop"

        if self.retries >= self.cfg["max_retries"]:
            self.log(f"[STOP] 自动重试已用尽（{self.retries}/{self.cfg['max_retries']}），交给人工处理。")
            self.exit_code = 4
            self.stop_reason = "retries_exhausted"
            return "stop"

        if not patches:
            self.log("[STOP] 没有可用的修复方案，不自动重启。")
            self.exit_code = 3
            self.stop_reason = "no_patch"
            return "stop"

        # 应用修复并重启
        tag2 = f"retry{self.retries + 1}"
        ok, backup = apply_patch_to_incar(self.cfg["workdir"], patches, tag2)
        if ok:
            self.log(f"[RETRY] INCAR 已更新（备份：{os.path.basename(backup)}）："
                     f"{', '.join(f'{k}={v}' for k, v in patches.items())}")
            # 必须重新加载 INCAR：否则后续体检仍用旧参数判断，
            # 比如 NELM 已从 60 改成 200，却还按 60 判「未收敛」，会永远判定失败。
            self.cfg["incar"] = parse_incar(os.path.join(self.cfg["workdir"], "INCAR"))
            self.cfg["init_magmom_sum"] = parse_magmom_sum(self.cfg["incar"].get("MAGMOM", ""))
        else:
            self.log("[STOP] 写入 INCAR 失败，不自动重启。")
            self.exit_code = 3
            self.stop_reason = "patch_failed"
            return "stop"

        # 结构崩坏时回退到原始结构；否则从 CONTCAR 继续
        wd = self.cfg["workdir"]
        contcar = os.path.join(wd, "CONTCAR")
        poscar = os.path.join(wd, "POSCAR")
        orig = os.path.join(wd, "POSCAR.watchdog_orig")
        collapse = any(f.code in ("STRUCTURE_COLLAPSE", "VOLUME_DRIFT") for f in findings if f.level == LEVEL_FATAL)
        if collapse:
            if os.path.isfile(orig):
                shutil.copy2(orig, poscar)
                self.log("[RETRY] 结构已回退到启动前的原始 POSCAR。")
            # 崩坏的 CONTCAR 必须挪走：留着它，重启后的第一次体检又会读到坏结构，
            # 白白消耗一次重试机会。
            if os.path.isfile(contcar):
                try:
                    shutil.move(contcar, os.path.join(wd, f"CONTCAR.collapsed_{tag2}"))
                    self.log(f"[RETRY] 崩坏的 CONTCAR 已改名归档为 CONTCAR.collapsed_{tag2}。")
                except Exception:
                    pass
        elif os.path.isfile(contcar) and os.path.getsize(contcar) > 100:
            shutil.copy2(contcar, poscar)
            self.log("[RETRY] 已用 CONTCAR 覆盖 POSCAR，从上次结构继续。")

        self.retries += 1
        self.log(f"[RETRY] 第 {self.retries}/{self.cfg['max_retries']} 次重启…")
        return "restart"

    def log(self, msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}" if msg else ""
        print(line, flush=True)
        try:
            with open(os.path.join(self.cfg["workdir"], "watchdog.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    # ---- 主循环 ----
    def run(self):
        cfg = self.cfg
        wd = cfg["workdir"]
        self.proc = None

        # 启动前：备份原始 POSCAR、记录初始体积与磁矩
        poscar = os.path.join(wd, "POSCAR")
        orig = os.path.join(wd, "POSCAR.watchdog_orig")
        if os.path.isfile(poscar) and not os.path.isfile(orig):
            try:
                shutil.copy2(poscar, orig)
            except Exception:
                pass
        struct0 = parse_poscar(orig if os.path.isfile(orig) else poscar)
        if struct0:
            cfg["initial_volume"] = struct0["volume"]
            cfg["natoms"] = struct0["natoms"]
        cfg["init_magmom_sum"] = parse_magmom_sum(cfg["incar"].get("MAGMOM", ""))

        while True:
            self.log(f"[START] 启动：{' '.join(cfg['cmd'])}")
            self.log(f"[START] 工作目录：{os.path.abspath(wd)}")
            self.log(f"[START] 体检间隔：每 {cfg['check_every']} 步；心跳：{cfg['heartbeat']}s；"
                     f"停滞阈值：{cfg['stall_warn']}s 警告 / {cfg['stall_fatal']}s 致命；"
                     f"自动重启：{'是，最多 ' + str(cfg['max_retries']) + ' 次' if cfg['auto_restart'] else '否'}")

            log_path = os.path.join(wd, "watchdog_stdout.log")
            try:
                self.proc, logf = start_vasp(cfg["cmd"], wd, log_path)
            except FileNotFoundError as e:
                self.log(f"[ERROR] 找不到可执行文件：{e}")
                return 1
            except Exception as e:
                self.log(f"[ERROR] 启动失败：{e}")
                return 1

            result = self._loop()

            try:
                logf.close()
            except Exception:
                pass

            if result == "restart":
                time.sleep(2)
                continue
            return self.exit_code

    def _loop(self):
        cfg = self.cfg
        wd = cfg["workdir"]
        last_check_n = 0
        last_heartbeat = time.time()
        last_seen_ionic = 0
        last_seen_elec = 0
        started = time.time()
        last_change = started          # 最近一次「观察到产出变化」的时刻
        last_mtimes = {}

        while True:
            rc = self.proc.poll()
            if rc is not None:
                self.log(f"[END] VASP 进程已退出，退出码 {rc}")
                # 终检
                state = self.collect_state()
                self.dump_csv(state)
                findings = diagnose(state, cfg)
                fatal = [f for f in findings if f.level == LEVEL_FATAL]
                if fatal:
                    tag = f"{now_tag()}_final"
                    report = write_report(wd, findings, state, cfg, tag, "终检（进程已退出，无法停机）")
                    self.log(f"[FATAL] 终检发现 {len(fatal)} 个致命问题，报告：{report}")
                    for f in fatal:
                        self.log(f"        · {f.title}：{f.detail}")
                    self.exit_code = 2
                else:
                    warns = [f for f in findings if f.level == LEVEL_WARN]
                    self.log(f"[OK] 终检通过（警告 {len(warns)} 项）")
                    if warns:
                        for f in warns:
                            self.log(f"        · {f.title}：{f.detail}")
                    self.exit_code = 0
                return "done"

            time.sleep(cfg["poll"])

            state = self.collect_state()
            self.dump_csv(state)

            # 停滞时长 = 距「上次观察到 OSZICAR/OUTCAR 有变化」的秒数；
            # 启动宽限期内不判定（VASP 初始化、读赝势、第一次 SCF 都可能很久不出 OSZICAR）。
            mt = state.get("mtimes") or {}
            if mt and mt != last_mtimes:
                last_change = time.time()
                last_mtimes = mt
            if (time.time() - started) > cfg.get("startup_grace", 600):
                state["stall_seconds"] = time.time() - last_change
            else:
                state["stall_seconds"] = None

            n_ion = len(state["ionic"])
            n_elec = len(state["electronic"])
            n_total = n_ion if n_ion > 0 else n_elec

            # 心跳
            if cfg["heartbeat"] > 0 and (time.time() - last_heartbeat) >= cfg["heartbeat"]:
                last_heartbeat = time.time()
                gap = state["stall_seconds"]
                progress = n_ion if n_ion else n_elec
                kind = "离子步" if n_ion else "电子步"
                if gap is None:
                    gap_txt = "（启动宽限期内，暂不判停滞）"
                else:
                    gap_txt = f"距上次新产出 {gap/60:.1f} 分钟"
                self.log(f"[HEARTBEAT] 进程存活（PID {self.proc.pid}），已跑 {progress} 个{kind}，{gap_txt}")
                if gap is not None and gap > cfg.get("stall_fatal", 3600):
                    findings = [f for f in diagnose(state, cfg) if f.level == LEVEL_FATAL]
                    if findings and cfg["stop_on_fatal"]:
                        act = self.handle_fatal(findings, state, "heartbeat")
                        if act == "restart":
                            return "restart"
                        if act == "stop":
                            return "stop"

            # 步数触发体检：每新增 check_every 步一次
            if n_total == 0 or (n_total - last_check_n) < cfg["check_every"]:
                continue
            last_check_n = n_total

            # 只在有新进展时体检，避免空转
            if n_ion and n_ion == last_seen_ionic and n_elec == last_seen_elec:
                continue
            last_seen_ionic, last_seen_elec = n_ion, n_elec

            findings = diagnose(state, cfg)
            fatal = [f for f in findings if f.level == LEVEL_FATAL]
            warns = [f for f in findings if f.level == LEVEL_WARN]
            kind = "离子步" if n_ion else "电子步"
            self.log(f"[CHECK] 第 {n_total} 个{kind}：致命 {len(fatal)} 项，警告 {len(warns)} 项")
            for f in warns:
                self.log(f"        · {f.title}：{f.detail}")

            if fatal:
                act = self.handle_fatal(findings, state, f"step{n_total}")
                if act == "restart":
                    return "restart"
                if act == "stop":
                    return "stop"


# ----------------------------------------------------------------------------
# 事后诊断（dry-run）
# ----------------------------------------------------------------------------

def run_dry(cfg):
    """不启动 VASP，只对已有日志做一次体检。"""
    wd = cfg["workdir"]
    osz = cfg.get("oszicar") or os.path.join(wd, "OSZICAR")
    outcar = cfg.get("outcar") or os.path.join(wd, "OUTCAR")

    osz_text = read_text(osz)
    outcar_text = read_text(outcar)
    ionic, electronic = parse_oszicar(osz_text)
    forces = parse_max_forces(outcar_text)
    mag = parse_magnetization(outcar_text)
    loop_times = parse_loop_times(outcar_text)

    struct = None
    for p in (os.path.join(wd, "CONTCAR"), os.path.join(wd, "POSCAR")):
        if os.path.isfile(p) and os.path.getsize(p) > 100:
            struct = parse_poscar(p)
            if struct:
                break

    # 初始体积必须取自 POSCAR（原始结构），不能取 CONTCAR，否则体积变化永远算成 0
    poscar_struct = parse_poscar(os.path.join(wd, "POSCAR"))
    initial_volume = poscar_struct["volume"] if poscar_struct else None
    n_atoms = (poscar_struct or struct)["natoms"] if (poscar_struct or struct) else 0

    state = {
        "ionic": ionic, "electronic": electronic, "forces": forces,
        "magnetization": mag, "loop_times": loop_times,
        "outcar_tail": outcar_text[-400000:], "structure": struct,
        "natoms": n_atoms,
        "initial_volume": initial_volume,
        "init_magmom_sum": cfg.get("init_magmom_sum", 0.0),
        "stall_seconds": None,
    }

    findings = diagnose(state, cfg)
    fatal = [f for f in findings if f.level == LEVEL_FATAL]
    warn = [f for f in findings if f.level == LEVEL_WARN]

    print("=" * 68)
    print("VASP 看门狗 — 事后诊断（dry-run，不启动 VASP）")
    print("=" * 68)
    print(f"OSZICAR: {osz}  →  离子步 {len(ionic)} 个，电子步 {len(electronic)} 个")
    print(f"OUTCAR : {outcar}  →  力数据 {len(forces)} 组，LOOP+ 时序 {len(loop_times)} 条")
    print(f"结构   : {'已读取，' + str(struct['natoms']) + ' 原子' if struct else '未找到 CONTCAR/POSCAR'}")
    print(f"NELM   : {cfg['incar'].get('NELM', '未设置（默认 60）')}，"
          f"ISPIN: {cfg['incar'].get('ISPIN', '1')}，ISIF: {cfg['incar'].get('ISIF', '2')}")
    print()
    if not findings:
        print("[OK] 未发现问题，计算过程正常。")
    for f in findings:
        print(f"{f}")
    print()
    print(f"汇总：致命 {len(fatal)} 项，警告 {len(warn)} 项")

    if findings and cfg.get("report"):
        tag = f"{now_tag()}_dryrun"
        path = write_report(wd, findings, state, cfg, tag, "事后诊断（未停机）")
        print(f"报告已写入：{path}")
    return 0 if not fatal else 2


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        cmd = argv[i + 1:]
        argv = argv[:i]

    p = argparse.ArgumentParser(
        prog="watchdog.py",
        description="VASP 运行看门狗（托管模式）：周期体检、致命问题停机、可选自动修复重启",
        epilog="示例：python watchdog.py -- mpirun -np 4 vasp_std",
    )
    p.add_argument("--workdir", default=".", help="计算目录（默认当前目录）")
    p.add_argument("--check-every", type=int, default=20, help="每新增多少步体检一次（默认 20）")
    p.add_argument("--heartbeat", type=int, default=60, help="心跳日志间隔秒数（默认 60，0 表示关闭）")
    p.add_argument("--stall-warn", type=int, default=900,
                   help="连续多少秒无新产出给警告（默认 900 秒 = 15 分钟，0 关闭）")
    p.add_argument("--stall-fatal", type=int, default=3600,
                   help="连续多少秒无新产出判为卡死（默认 3600 秒 = 1 小时，0 关闭）")
    p.add_argument("--startup-grace", type=int, default=600,
                   help="启动宽限秒数，期间不判停滞（默认 600 秒，大体系初始化慢就调大）")
    p.add_argument("--poll", type=int, default=2, help="轮询间隔秒数（默认 2）")
    p.add_argument("--max-retries", type=int, default=2, help="自动重启最大次数（默认 2）")
    p.add_argument("--no-restart", action="store_true", help="只停机，不自动重启")
    p.add_argument("--no-stop", action="store_true", help="只报告不停机（试跑判据时用）")
    p.add_argument("--dry-run", action="store_true", help="不启动 VASP，只对已有日志做一次体检")
    p.add_argument("--oszicar", default=None, help="dry-run 时指定 OSZICAR 路径")
    p.add_argument("--outcar", default=None, help="dry-run 时指定 OUTCAR 路径")
    p.add_argument("--report", action="store_true", help="dry-run 时同时写 markdown 报告")
    args = p.parse_args(argv)

    wd = os.path.abspath(args.workdir)
    if not os.path.isdir(wd):
        print(f"[ERROR] 工作目录不存在：{wd}")
        return 1

    incar = parse_incar(os.path.join(wd, "INCAR"))
    init_magmom_sum = parse_magmom_sum(incar.get("MAGMOM", ""))

    cfg = {
        "workdir": wd,
        "cmd": cmd,
        "incar": incar,
        "check_every": max(1, args.check_every),
        "heartbeat": max(0, args.heartbeat),
        "stall_warn": max(0, args.stall_warn),
        "stall_fatal": max(0, args.stall_fatal),
        "startup_grace": max(0, args.startup_grace),
        "poll": max(1, args.poll),
        "max_retries": max(0, args.max_retries),
        "auto_restart": not args.no_restart,
        "stop_on_fatal": not args.no_stop,
        "oszicar": args.oszicar,
        "outcar": args.outcar,
        "report": args.report,
        "init_magmom_sum": init_magmom_sum,
    }

    if args.dry_run:
        return run_dry(cfg)

    if not cmd:
        p.print_help()
        print("\n[ERROR] 缺少要运行的命令。请用 `--` 把 VASP 命令隔开，例如：")
        print("        python watchdog.py -- mpirun -np 4 vasp_std")
        return 1

    wd_obj = Watchdog(cfg)
    return wd_obj.run()


if __name__ == "__main__":
    sys.exit(main())
