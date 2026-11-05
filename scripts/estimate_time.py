#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
estimate_time.py — VASP 计算耗时估算脚本

用法：
    python estimate_time.py --natoms 100 --nkpoints 64 --encut 520 \
        --task relax --nionic 100 --cores 32

经验公式粗估（量级）+ 可选预计算校准（实测单步耗时后外推）。
所有输出仅为估计值，标注置信度。
"""

import argparse


def estimate(natoms, nkpoints, encut, task, nionic, cores, scf_per_step=40,
             measured_scf_time=None, measured_encut=None, measured_nk=None,
             measured_natoms=None, alpha=2.5):
    """
    返回 (预计算估计, 正式计算估计, 说明)
    若提供 measured_scf_time，则用实测校准。
    """
    # 经验常数 C：单位秒，量级可调
    # C 校准依据：中等 CPU 下，约 100 原子、64 k 点、ENCUT=520 的单电子步约 1~10 秒量级
    C = 3e-5  # 经验常数，单位秒
    freq_factor = 1.0  # 单核频率归一因子（3 GHz 基准）

    # 单电子步耗时（秒）
    t_scf = C * (natoms ** alpha) * nkpoints * ((encut / 400.0) ** 1.5) / (cores * freq_factor)

    # 电子步总数
    if task == "single":
        n_scf_total = scf_per_step
    elif task in ("relax", "dos", "bands"):
        n_scf_total = scf_per_step * nionic
    else:
        n_scf_total = scf_per_step

    total_seconds = t_scf * n_scf_total

    # 若提供实测单步耗时，校准
    note = "经验公式粗估（低置信度）"
    if measured_scf_time and measured_scf_time > 0:
        # 按参数放大回正式规模
        scale = 1.0
        if measured_encut:
            scale *= (encut / measured_encut) ** 1.5
        if measured_nk:
            scale *= (nkpoints / measured_nk)
        if measured_natoms:
            scale *= (natoms / measured_natoms) ** alpha
        cal_scf = measured_scf_time * scale
        total_seconds = cal_scf * n_scf_total
        note = f"实测校准估计（高置信度），单步实测 {measured_scf_time:.3f}s，放大系数 {scale:.1f}"

    return t_scf, total_seconds, note


def fmt(seconds):
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    elif seconds < 3600:
        return f"{seconds/60:.1f} 分钟"
    elif seconds < 86400:
        return f"{seconds/3600:.1f} 小时"
    else:
        return f"{seconds/86400:.1f} 天"


def main():
    p = argparse.ArgumentParser(description="VASP 耗时估算")
    p.add_argument("--natoms", type=int, required=True, help="原子数")
    p.add_argument("--nkpoints", type=int, default=8, help="k点数")
    p.add_argument("--encut", type=float, default=520, help="ENCUT (eV)")
    p.add_argument("--task", choices=["single", "relax", "dos", "bands"], default="relax")
    p.add_argument("--nionic", type=int, default=100, help="离子步数（结构优化）")
    p.add_argument("--cores", type=int, default=32, help="使用核心数")
    p.add_argument("--scf-per-step", type=int, default=40, help="平均每离子步电子步数")
    # 预计算校准参数
    p.add_argument("--measured-scf-time", type=float, default=None, help="预计算实测单电子步耗时(秒)")
    p.add_argument("--measured-encut", type=float, default=None, help="预计算所用ENCUT")
    p.add_argument("--measured-nk", type=int, default=None, help="预计算所用k点数")
    p.add_argument("--measured-natoms", type=int, default=None, help="预计算所用原子数")
    args = p.parse_args()

    t_scf, total, note = estimate(
        args.natoms, args.nkpoints, args.encut, args.task, args.nionic,
        args.cores, args.scf_per_step,
        args.measured_scf_time, args.measured_encut, args.measured_nk,
        args.measured_natoms,
    )

    print("=" * 60)
    print("VASP 耗时估算")
    print("=" * 60)
    print(f"体系规模: {args.natoms} 原子, {args.nkpoints} k点, ENCUT={args.encut} eV")
    print(f"计算类型: {args.task}, 离子步数: {args.nionic}, 核心数: {args.cores}")
    print("-" * 60)
    print(f"估算单电子步耗时: {fmt(t_scf)}")
    print(f"正式计算总耗时:   {fmt(total)}")
    print(f"置信度: {note}")
    print("=" * 60)
    print("提示: 本估算为估计值，实际受收敛难度、负载、I/O 影响。")


if __name__ == "__main__":
    main()
