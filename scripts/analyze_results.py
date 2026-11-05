#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_results.py — VASP 计算结果分析脚本

用法：
    python analyze_results.py --outcar OUTCAR --oszicar OSZICAR --need gap

从 OUTCAR / OSZICAR 提取关键结果，做收敛性与物理合理性检查，
输出分析摘要。可与 references/result-analysis.md 配合，辅助生成最终报告。

本脚本只做「可程序化提取」的检查，物理合理性的人工判断和联网核对
仍由 Agent 结合参考文档完成。
"""

import argparse
import os
import re
import sys

# 可选增强库检测
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _libs import HAS_ASE, HAS_PYMATGEN  # noqa: E402


def read(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def extract_vasprun_enhanced(path):
    """用 ASE/pymatgen 从 vasprun.xml 提取全量结果。返回 dict 或 None（解析失败）。

    优先用 pymatgen Vasprun（字段最全），失败退回 ASE read_vasp_xml。
    """
    if not os.path.exists(path):
        return None
    info = {}
    # 优先 pymatgen
    if HAS_PYMATGEN:
        try:
            from pymatgen.io.vasp.outputs import Vasprun
            vr = Vasprun(path, parse_dos=False, parse_potcar_file=False)
            info["_source"] = "pymatgen Vasprun"
            info["electronic_converged"] = vr.converged_electronic
            info["ionic_converged"] = vr.converged_ionic
            if vr.final_energy is not None:
                info["total_energy"] = float(vr.final_energy)
            info["nions"] = len(vr.final_structure)
            info["n_ionic_steps"] = len(vr.ionic_steps)
            # 最终晶格
            try:
                info["lattice"] = vr.final_structure.lattice.abc
            except Exception:
                pass
            # 带隙
            try:
                if vr.get_band_structure():
                    info["bandgap"] = vr.get_band_structure().get_band_gap()["energy"]
            except Exception:
                pass
            # 磁矩
            try:
                info["magnetization"] = vr.final_structure.site_properties.get("magmom", None)
            except Exception:
                pass
            return info
        except Exception:
            info = {}  # 退回 ASE
    # 退回 ASE
    if HAS_ASE:
        try:
            from ase.io import read as ase_read
            atoms = ase_read(path, index="-1")
            info["_source"] = "ASE read_vasp_xml"
            info["nions"] = len(atoms)
            e = atoms.get_potential_energy()
            if e is not None:
                info["total_energy"] = float(e)
            return info
        except Exception:
            return None
    return None


def extract_outcar(outcar):
    """从 OUTCAR 提取关键量。"""
    info = {}
    if not outcar:
        return info
    # 收敛标志
    info["electronic_converged"] = "reached required accuracy" in outcar
    info["ionic_converged"] = "reached required accuracy - stopping structural energy minimisation" in outcar
    # 总能（最后一个 F=）
    energies = re.findall(r"free\s+energy\s+TOTEN\s*=\s*(-?[\d.]+)", outcar)
    if not energies:
        energies = re.findall(r"energy\s+without entropy\s*=\s*(-?[\d.]+)", outcar)
    if energies:
        info["total_energy"] = float(energies[-1])
    # 原子数（NIONS）
    m = re.search(r"NIONS\s*=\s*(\d+)", outcar)
    if m:
        info["nions"] = int(m.group(1))
    # 晶格常数（最终）
    lattices = re.findall(r"length of vectors\s*\n\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)", outcar)
    # 带隙（若输出）
    # 磁矩
    mag = re.findall(r"number of electron\s+([\d.]+)\s+magnetization", outcar)
    if mag:
        info["magnetization"] = float(mag[-1])
    return info


def extract_oszicar(oszicar):
    """从 OSZICAR 提取收敛历史。"""
    info = {}
    if not oszicar:
        return info
    f_lines = re.findall(r"\d+\s+F=\s*(-?[\d.Ee+-]+)\s+E0=\s*(-?[\d.Ee+-]+)", oszicar)
    if f_lines:
        info["n_ionic_steps"] = len(f_lines)
        info["first_energy"] = float(f_lines[0][0])
        info["last_energy"] = float(f_lines[-1][0])
        info["energy_change"] = info["last_energy"] - info["first_energy"]
    return info


def main():
    p = argparse.ArgumentParser(description="VASP 结果分析")
    p.add_argument("--outcar", default=None)
    p.add_argument("--oszicar", default=None)
    p.add_argument("--vasprun", default=None, help="vasprun.xml 路径（有 ASE/pymatgen 时优先用，数据更全）")
    p.add_argument("--need", default=None, help="用户需求关键词，如 gap/lattice/adsorption")
    args = p.parse_args()

    outcar = read(args.outcar)
    oszicar = read(args.oszicar)

    # ---- 增强分支：优先用 vasprun.xml ----
    enhanced = None
    if args.vasprun:
        enhanced = extract_vasprun_enhanced(args.vasprun)
        if enhanced:
            print("[增强] 使用 " + enhanced.get("_source", "库") + " 解析 vasprun.xml，结果更完整")
        else:
            print("[降级] vasprun.xml 解析失败或未安装 ASE/pymatgen，退回 OUTCAR/OSZICAR 正则解析")

    # ---- 零依赖分支：正则解析 OUTCAR/OSZICAR ----
    info_o = extract_outcar(outcar)
    info_s = extract_oszicar(oszicar)

    # 合并：增强结果优先，缺失字段用正则结果补齐
    merged_o = dict(info_o)
    merged_s = dict(info_s)
    if enhanced:
        merged_o.update({k: v for k, v in enhanced.items() if not k.startswith("_")})
        if "n_ionic_steps" in enhanced and "n_ionic_steps" not in merged_s:
            merged_s["n_ionic_steps"] = enhanced["n_ionic_steps"]

    print("=" * 60)
    print("VASP 计算结果分析")
    print("=" * 60)

    print("\n[收敛情况]")
    print(f"  电子收敛: {'是' if merged_o.get('electronic_converged') else '否/未检出'}")
    print(f"  离子收敛: {'是' if merged_o.get('ionic_converged') else '否/未检出'}")

    print("\n[能量]")
    if "total_energy" in merged_o:
        te = merged_o["total_energy"]
        nions = merged_o.get("nions")
        print(f"  总能量: {te:.6f} eV")
        if nions:
            print(f"  每原子能量: {te/nions:.6f} eV/atom")
    if "n_ionic_steps" in merged_s:
        print(f"  离子步数: {merged_s['n_ionic_steps']}")
        if "first_energy" in merged_s:
            print(f"  首步能量: {merged_s['first_energy']:.6f} eV")
            print(f"  末步能量: {merged_s['last_energy']:.6f} eV")
            print(f"  能量变化: {merged_s['energy_change']:.6f} eV")

    print("\n[其他物理量]")
    if "bandgap" in merged_o:
        print(f"  带隙: {merged_o['bandgap']:.3f} eV")
    if "magnetization" in merged_o:
        mag = merged_o["magnetization"]
        if isinstance(mag, (list, tuple)) and mag:
            print(f"  磁矩: {mag} μB")
        elif mag is not None:
            print(f"  磁矩: {mag} μB")

    if args.need:
        print(f"\n[需求核对] 用户目标: {args.need}")
        print("  (具体物理量提取与合理性判断请结合 references/result-analysis.md 完成)")

    print("\n" + "=" * 60)
    print("提示: 以上为可程序化提取的部分；物理合理性人工判断、联网核对、")
    print("      以及最终 Markdown 报告，请由 Agent 结合 result-analysis.md 完成。")
    print("=" * 60)


if __name__ == "__main__":
    main()
