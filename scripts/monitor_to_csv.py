#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_to_csv.py — 解析 VASP 运行日志，整理为 CSV

用法：
    # 从 OSZICAR 文件解析
    python monitor_to_csv.py --oszicar OSZICAR --out monitor.csv
    # 从 OUTCAR 文件解析
    python monitor_to_csv.py --outcar OUTCAR --out monitor.csv
    # 从粘贴的日志文本解析
    python monitor_to_csv.py --text "1 F= -.12E+02 E0= -.12E+02  d E =-.12E+02 ..."

从 OSZICAR 提取每个电子步/离子步的能量、力、耗时，整理为 CSV，
列包含: step_type, step_index, scf_iteration, energy, free_energy,
dE, elapsed_time, note。输出保存到当前目录（--out 指定）。

注意：若同时提供文件路径和 --text，优先使用 --text。
"""

import argparse
import csv
import re
import sys
import time


def parse_oszicar(text):
    """解析 OSZICAR，返回行列表。"""
    rows = []
    # OSZICAR 典型行：
    #   1 F= -.12345678E+02 E0= -.12345678E+02  d E =-.123456E+00
    #   RMM:   1  -0.123456789000E+02  -0.1234E+00 -0.1234E+00  12  0.123E+00
    ion_step = None
    scf_iter = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 离子步标记行（形如 "1 F= ... E0= ...  d E =... "）
        # 注意数值可能为 -.10835E+02 形式（负号紧跟小数点，无整数部分）
        m_ion = re.match(r"^\s*(\d+)\s+F=\s*(-?[\d.]+E[+-]\d+)\s+E0=\s*(-?[\d.]+E[+-]\d+)\s+d\s*E\s*=\s*(-?[\d.]+E[+-]\d+)", line)
        if m_ion:
            ion_step = int(m_ion.group(1))
            scf_iter = 0
            rows.append({
                "step_type": "ionic",
                "step_index": ion_step,
                "scf_iteration": "",
                "energy": m_ion.group(3),
                "free_energy": m_ion.group(2),
                "dE": m_ion.group(4),
                "elapsed_time": "",
                "note": "",
            })
            continue
        # 电子步行（RMM / DAV 等），数值同样兼容 -.xxxE+xx 形式
        m_scf = re.match(r"^\s*(RMM|DAV|EDIIS|CG):\s+(\d+)\s+(-?[\d.]+E[+-]\d+)\s+(-?[\d.]+E[+-]\d+)\s+(-?[\d.]+E[+-]\d+)\s+(\d+)\s+(-?[\d.]+E[+-]\d+)?", line)
        if m_scf:
            scf_iter += 1
            rows.append({
                "step_type": "electronic",
                "step_index": ion_step if ion_step is not None else "",
                "scf_iteration": scf_iter,
                "energy": m_scf.group(3),
                "free_energy": m_scf.group(4),
                "dE": m_scf.group(5),
                "elapsed_time": m_scf.group(7) if m_scf.group(7) else "",
                "note": m_scf.group(1),
            })
    return rows


def parse_outcar_timing(text):
    """从 OUTCAR 提取 LOOP+ 耗时信息，返回 {离子步: 耗时秒}。"""
    timing = {}
    for m in re.finditer(r"LOOP\+:\s+cpu time\s+([\d.]+)", text):
        timing[len(timing) + 1] = float(m.group(1))
    return timing


def main():
    p = argparse.ArgumentParser(description="VASP 日志解析为 CSV")
    p.add_argument("--oszicar", default=None, help="OSZICAR 文件路径")
    p.add_argument("--outcar", default=None, help="OUTCAR 文件路径")
    p.add_argument("--text", default=None, help="直接粘贴的日志文本")
    p.add_argument("--out", default="monitor.csv", help="输出 CSV 路径（默认当前目录）")
    args = p.parse_args()

    # 读取数据
    if args.text:
        osz_text = args.text
        outcar_text = None
    else:
        osz_text = None
        outcar_text = None
        if args.oszicar:
            with open(args.oszicar, "r", errors="replace") as f:
                osz_text = f.read()
        if args.outcar:
            with open(args.outcar, "r", errors="replace") as f:
                outcar_text = f.read()

    rows = []
    if osz_text:
        rows = parse_oszicar(osz_text)

    # 附加 OUTCAR 时序
    timing = {}
    if outcar_text:
        timing = parse_outcar_timing(outcar_text)

    # 写入 CSV
    fields = ["step_type", "step_index", "scf_iteration", "energy",
              "free_energy", "dE", "elapsed_time", "note"]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            # 尝试填入离子步总耗时
            if r["step_type"] == "ionic" and r["step_index"] in timing:
                r["elapsed_time"] = timing[r["step_index"]]
            w.writerow(r)

    n_ion = sum(1 for r in rows if r["step_type"] == "ionic")
    n_elec = sum(1 for r in rows if r["step_type"] == "electronic")
    print(f"[OK] 解析完成：离子步 {n_ion} 个，电子步 {n_elec} 个")
    print(f"[OK] CSV 已保存到: {args.out}")
    if n_ion == 0 and n_elec == 0:
        print("[WARN] 未识别到有效数据，请检查日志格式")


if __name__ == "__main__":
    main()
