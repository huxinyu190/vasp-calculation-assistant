#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_environment.py — VASP 环境与性能测试脚本（能力 8）

功能：
1. 冒烟测试：生成最小测试体系，验证 VASP 能否正常运行
2. 性能基准：固定参数跑若干电子步，实测单电子步耗时
   测试结果可反哺 estimate_time.py（能力 3）做实测校准。

用法：
    # 冒烟测试（最小体系，几秒内完成）
    python test_environment.py --vasp vasp_std --cores 4

    # 性能基准（指定体系规模）
    python test_environment.py --vasp vasp_std --cores 64 \
        --natoms 8 --encut 400 --kmesh "1 1 1" --nsteps 20

说明：
- 本脚本只「生成输入文件 + 打印运行命令 + 解析结果」，
  不直接执行 VASP（因为是否连接虚拟机由用户决定）。
- 所有临时文件放在当前工作目录的 test_env/ 子目录。
"""

import argparse
import os
import re
import sys

# 硅的简单立方晶格常数（Å），用于生成最小测试体系
SI_LATTICE = 5.43


def write_inputs(outdir, natoms, encut, kmesh, task="single"):
    """生成最小测试体系的 INCAR / POSCAR / KPOINTS / POTCAR（占位）。"""
    os.makedirs(outdir, exist_ok=True)

    # ---- INCAR ----
    incar = f"""SYSTEM = env_test
ENCUT  = {encut}
ISMEAR = 0
SIGMA  = 0.05
ISIF   = 2
IBRION = -1
NSW    = 0
EDIFF  = 1E-5
EDIFFG = -0.02
PREC   = Normal
LREAL  = Auto
LCHARG = .FALSE.
LWAVE  = .FALSE.
"""
    with open(os.path.join(outdir, "INCAR"), "w") as f:
        f.write(incar)

    # ---- POSCAR（简单立方 Si，2 原子基胞，超胞按 natoms 扩展）----
    # 简化：这里生成 2 原子的金刚石基胞近似，实际超胞扩展由用户按需调整
    # 为保持脚本简单通用，使用「8 原子 Si 金刚石晶胞」的坐标模板
    poscar = f"""Si env test
1.0
    {SI_LATTICE} 0.0 0.0
    0.0 {SI_LATTICE} 0.0
    0.0 0.0 {SI_LATTICE}
Si
2
Direct
    0.000000000 0.000000000 0.000000000
    0.250000000 0.250000000 0.250000000
"""
    with open(os.path.join(outdir, "POSCAR"), "w") as f:
        f.write(poscar)

    # ---- KPOINTS ----
    k1, k2, k3 = (kmesh.split() + ["1", "1", "1"])[:3]
    kpoints = f"""Automatic mesh
0
Gamma
{k1} {k2} {k3}
0 0 0
"""
    with open(os.path.join(outdir, "KPOINTS"), "w") as f:
        f.write(kpoints)

    # ---- POTCAR（占位提示，实际需用户提供真实赝势）----
    with open(os.path.join(outdir, "POTCAR_PLACEHOLDER"), "w") as f:
        f.write("# 请将真实的 Si POTCAR 复制为 POTCAR\n")
        f.write("# 例如: cat <你的赝势目录>/Si/POTCAR > POTCAR\n")

    print(f"[已生成] 测试输入文件于: {outdir}/")
    print(f"  INCAR  (ENCUT={encut}, 单点能)")
    print(f"  POSCAR (2 原子 Si 基胞)")
    print(f"  KPOINTS ({k1}x{k2}x{k3} Gamma)")
    print(f"  POTCAR_PLACEHOLDER (请替换为真实 POTCAR)")
    print()


def run_command(vasp, cores, outdir):
    """打印 VASP 运行命令（不实际执行）。"""
    cmd = f"cd {outdir} && mpirun -np {cores} {vasp} > vasp.log 2>&1"
    print("=" * 60)
    print("请运行以下命令执行测试：")
    print(f"  {cmd}")
    print("=" * 60)
    print()
    return cmd


def parse_outcar(outcar_path):
    """从 OUTCAR 提取关键信息：版本、单步耗时、是否正常结束。"""
    result = {
        "ok": False,
        "version": None,
        "single_step_time": None,   # 平均单电子步耗时（秒）
        "total_time": None,
        "n_scf_steps": 0,
        "finished": False,
    }
    if not os.path.exists(outcar_path):
        return result

    with open(outcar_path, "r", errors="ignore") as f:
        text = f.read()

    # 版本
    m = re.search(r"vasp\.\d+\.\d+\.\d+", text)
    if m:
        result["version"] = m.group(0)

    # 是否正常结束
    if "General timing accounting" in text or "reached required accuracy" in text:
        result["finished"] = True

    # LOOP+ 时序：每行 "LOOP+:  cpu time ...: real time ..."
    loop_times = re.findall(r"LOOP\+:\s+cpu time\s+([\d.]+).*?real time\s+([\d.]+)", text)
    if loop_times:
        real_times = [float(t[1]) for t in loop_times]
        result["n_scf_steps"] = len(real_times)
        result["single_step_time"] = sum(real_times) / len(real_times)
        result["total_time"] = sum(real_times)

    result["ok"] = result["finished"]
    return result


def main():
    p = argparse.ArgumentParser(description="VASP 环境与性能测试")
    p.add_argument("--vasp", default="vasp_std", help="VASP 可执行文件名（默认 vasp_std）")
    p.add_argument("--cores", type=int, default=4, help="MPI 进程数（默认 4）")
    p.add_argument("--natoms", type=int, default=2, help="测试体系原子数（默认 2，最小）")
    p.add_argument("--encut", type=float, default=400, help="ENCUT (eV，默认 400)")
    p.add_argument("--kmesh", default="1 1 1", help="k 点网格，如 '1 1 1' 或 '4 4 4'")
    p.add_argument("--nsteps", type=int, default=20, help="期望电子步数（仅用于性能提示）")
    p.add_argument("--outdir", default="test_env", help="输出目录（默认当前目录 test_env/）")
    p.add_argument("--parse", default=None, help="仅解析已有 OUTCAR（传入路径）")
    args = p.parse_args()

    # 若只解析已有 OUTCAR
    if args.parse:
        r = parse_outcar(args.parse)
        print("=" * 60)
        print("OUTCAR 解析结果")
        print("=" * 60)
        print(f"版本: {r['version'] or '未识别'}")
        print(f"是否正常结束: {'是' if r['finished'] else '否（可能未完成或报错）'}")
        print(f"电子步数: {r['n_scf_steps']}")
        if r["single_step_time"] is not None:
            print(f"平均单电子步耗时: {r['single_step_time']:.3f} 秒")
            print(f"总耗时: {r['total_time']:.1f} 秒")
        print()
        if r["single_step_time"]:
            print("【反哺耗时估算】把下面这个值填入 estimate_time.py：")
            print(f"  --measured-scf-time {r['single_step_time']:.3f}")
        return

    # 生成测试输入
    outdir = os.path.abspath(args.outdir)
    write_inputs(outdir, args.natoms, args.encut, args.kmesh)

    # 打印运行命令
    run_command(args.vasp, args.cores, outdir)

    # 提示后续解析
    print("运行完成后，用以下命令解析结果、提取单步耗时：")
    print(f"  python scripts/test_environment.py --parse {outdir}/OUTCAR")
    print()
    print("性能基准建议：")
    print("  - 单电子步耗时 < 1 秒：高性能，可算大体系")
    print("  - 单电子步耗时 1~10 秒：中等，可算百原子级")
    print("  - 单电子步耗时 > 10 秒：较慢，建议优化并行参数或减小体系")


if __name__ == "__main__":
    main()
