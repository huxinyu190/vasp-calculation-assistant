#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_inputs.py — VASP 输入文件校验脚本

用法：
    python validate_inputs.py <目录>

对目录下的 INCAR / POSCAR / POTCAR / KPOINTS 做「格式层 + 一致性层」的
机械校验，输出 PASS / WARN / ERROR 清单。这是最高优先级的检查步骤：
任何文件被改动后，都应重新运行本脚本。

退出码：0=通过（可含 WARN），1=存在 ERROR。
"""

import os
import re
import sys

# 可选增强库检测
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _libs import HAS_PYMATGEN  # noqa: E402


def read_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", errors="replace") as f:
        return f.read()


def parse_poscar(text):
    """解析 POSCAR，返回 dict 或错误信息。"""
    lines = [l for l in text.splitlines() if l.strip() != ""]
    if len(lines) < 8:
        return None, "POSCAR 行数不足，格式不完整"
    scale = None
    try:
        scale = float(lines[1].split()[0])
    except Exception:
        return None, "第2行缩放因子无法解析为数字"
    # 晶格矢量（第3-5行）
    lattice = []
    for i in range(2, 5):
        try:
            lat = [float(x) for x in lines[i].split()]
        except Exception:
            return None, f"第{i+1}行晶格矢量无法解析"
        if len(lat) != 3:
            return None, f"第{i+1}行晶格矢量应有3个分量"
        lattice.append(lat)
    # 元素与计数（第6-7行，兼容旧格式无元素行）
    idx = 5
    # 判断第6行是否为元素符号（含字母）还是原子数（纯数字）
    if re.search(r"[A-Za-z]", lines[idx]):
        elements = lines[idx].split()
        idx += 1
        try:
            counts = [int(x) for x in lines[idx].split()]
        except Exception:
            return None, "原子数行无法解析"
        idx += 1
    else:
        elements = None
        try:
            counts = [int(x) for x in lines[idx].split()]
        except Exception:
            return None, "原子数行无法解析"
        idx += 1
    # 坐标类型
    coord_line = lines[idx].upper()
    if coord_line.startswith(("D", "C")):
        coord = "Direct" if coord_line.startswith("D") else "Cartesian"
    else:
        coord = coord_line
    # 处理 Selective dynamics 标记
    selective = False
    if "S" in coord_line or "SELECTIVE" in coord_line.upper():
        selective = True
        idx += 1  # 下一行才是坐标类型
        coord = lines[idx].upper().split()[0]
    idx += 1
    # 坐标
    positions = []
    for line in lines[idx:]:
        parts = line.split()
        if not parts:
            continue
        try:
            pos = [float(parts[0]), float(parts[1]), float(parts[2])]
        except Exception:
            continue
        positions.append(pos)
    total = sum(counts)
    return {
        "scale": scale, "lattice": lattice, "elements": elements,
        "counts": counts, "coord": coord, "selective": selective,
        "positions": positions, "total_atoms": total,
    }, None


def parse_potcar(text):
    """提取 POTCAR 中的元素与 ENMAX。"""
    if text is None:
        return None, "POTCAR 文件不存在"
    elements = re.findall(r"TITEL\s*=\s*PAW[_\s]+\S*\s+(\w+)", text)
    enmax = re.findall(r"ENMAX\s*=\s*([\d.]+)", text)
    enmax_vals = [float(x) for x in enmax]
    return {"elements": elements, "enmax": enmax_vals}, None


def parse_kpoints(text):
    """解析 KPOINTS 基本信息。"""
    if text is None:
        return None, "KPOINTS 文件不存在"
    lines = [l for l in text.splitlines() if l.strip() != ""]
    if len(lines) < 4:
        return None, "KPOINTS 行数不足"
    mesh = None
    try:
        mesh = [int(x) for x in lines[3].split()]
    except Exception:
        pass
    return {"mesh": mesh, "method": lines[2] if len(lines) > 2 else None}, None


def parse_incar(text):
    """解析 INCAR 参数为 dict。"""
    if text is None:
        return None, "INCAR 文件不存在"
    params = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or "=" not in line:
            continue
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key in params:
                # 重复定义记录警告
                params.setdefault("__dup__", []).append(key)
            params[key] = val
    return params, None


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("""用法: python validate_inputs.py <计算目录>

参数:
  <计算目录>   含 INCAR / POSCAR / POTCAR / KPOINTS 的目录，不填则视为当前目录

说明:
  校验四大输入文件的一致性与常见错误，逐条输出 PASS / WARN / ERROR。
  退出码: 0 = 无 ERROR，1 = 存在 ERROR，2 = 用法错误。
  装了 pymatgen 会自动追加结构合法性与原子重叠检查，未装则静默跳过。
""")
        sys.exit(0 if len(sys.argv) >= 2 else 2)
    d = sys.argv[1]

    results = []  # (level, msg)

    poscar_txt = read_file(os.path.join(d, "POSCAR"))
    potcar_txt = read_file(os.path.join(d, "POTCAR"))
    kpoints_txt = read_file(os.path.join(d, "KPOINTS"))
    incar_txt = read_file(os.path.join(d, "INCAR"))

    # ---- POSCAR ----
    if poscar_txt is None:
        results.append(("ERROR", "POSCAR 不存在"))
    else:
        poscar, err = parse_poscar(poscar_txt)
        if err:
            results.append(("ERROR", f"POSCAR 解析错误: {err}"))
        else:
            results.append(("PASS", f"POSCAR 解析成功，原子数={poscar['total_atoms']}"))
            # pymatgen 结构合法性增强校验（可选）
            if HAS_PYMATGEN:
                try:
                    from pymatgen.core import Structure
                    struct = Structure.from_file(os.path.join(d, "POSCAR"))
                    results.append(("PASS", f"pymatgen 结构校验通过：{struct.composition.formula}，{len(struct)} 原子，空间群可解析"))
                    # 原子间距过近检查
                    too_close = []
                    for i in range(len(struct)):
                        for j in range(i + 1, len(struct)):
                            d = struct.get_distance(i, j)
                            if d < 0.5:  # 0.5 Å 以内视为异常重叠
                                too_close.append(f"{struct[i].specie}-{struct[j].specie}@{d:.2f}Å")
                    if too_close:
                        results.append(("WARN", f"pymatgen 检测到异常过近原子对（<0.5Å）：{'; '.join(too_close[:5])}"))
                except Exception as e:
                    results.append(("WARN", f"pymatgen 结构校验失败（忽略，不影响内置校验）：{e}"))
            # 行列式（线性无关）
            a, b, c = poscar["lattice"]
            det = (a[0]*(b[1]*c[2]-b[2]*c[1]) - a[1]*(b[0]*c[2]-b[2]*c[0])
                   + a[2]*(b[0]*c[1]-b[1]*c[0]))
            if abs(det) < 1e-12:
                results.append(("ERROR", "POSCAR 晶格矢量线性相关（行列式为0）"))
            else:
                results.append(("PASS", "POSCAR 晶格矢量线性无关"))
            # 原子数一致性
            if len(poscar["positions"]) != poscar["total_atoms"]:
                results.append(("ERROR",
                    f"POSCAR 坐标行数({len(poscar['positions'])})与原子总数({poscar['total_atoms']})不一致"))
            else:
                results.append(("PASS", "POSCAR 坐标行数与原子总数一致"))
            # 精度检查：直接读原始文本中的浮点数，判断小数位数（不能用 repr(float)，
            # 因为 Python 会自动去掉末尾的 0，导致 5.430000000 被误判成 5.43）。
            all_floats = re.findall(r"-?\d+\.\d+", poscar_txt)
            min_frac = min((len(n.split(".")[1]) for n in all_floats),
                           default=0)
            if min_frac < 6:
                results.append(("WARN",
                    f"POSCAR 部分坐标/矢量小数位数仅 {min_frac} 位，建议提高到 6 位以上"))

    # ---- POTCAR ----
    potcar, err = parse_potcar(potcar_txt)
    if err:
        results.append(("ERROR", err))
    else:
        results.append(("PASS", f"POTCAR 含 {len(potcar['elements'])} 个元素: {potcar['elements']}"))
        if potcar["enmax"]:
            results.append(("PASS", f"POTCAR ENMAX 值: {potcar['enmax']}"))

    # ---- KPOINTS ----
    kpts, err = parse_kpoints(kpoints_txt)
    if err:
        results.append(("ERROR", err))
    else:
        if kpts["mesh"]:
            results.append(("PASS", f"KPOINTS 网格: {kpts['mesh']}"))
        else:
            results.append(("WARN", "KPOINTS 可能为线模式或网格未识别"))

    # ---- INCAR ----
    incar, err = parse_incar(incar_txt)
    if err:
        results.append(("ERROR", err))
    else:
        results.append(("PASS", f"INCAR 解析成功，共 {len(incar)} 个参数"))
        if "__dup__" in incar:
            results.append(("WARN", f"INCAR 重复定义参数: {incar['__dup__']}"))
            del incar["__dup__"]

    # ---- 一致性 ----
    # 元素顺序
    if poscar_txt and potcar_txt:
        poscar, _ = parse_poscar(poscar_txt)
        potcar, _ = parse_potcar(potcar_txt)
        if poscar and poscar.get("elements") and potcar:
            pe = poscar["elements"]
            poe = potcar["elements"]
            if pe == poe:
                results.append(("PASS", f"元素顺序一致: POSCAR={pe} == POTCAR={poe}"))
            else:
                results.append(("ERROR", f"元素顺序不一致: POSCAR={pe} vs POTCAR={poe}（最常见致命错误！）"))

    # ENCUT vs ENMAX
    if incar and potcar and "ENCUT" in incar and potcar["enmax"]:
        try:
            encut = float(incar["ENCUT"])
            max_enmax = max(potcar["enmax"])
            if encut < max_enmax:
                results.append(("ERROR", f"ENCUT={encut} 低于 POTCAR 最大 ENMAX={max_enmax}"))
            elif encut < 1.3 * max_enmax:
                results.append(("WARN", f"ENCUT={encut} 未达 ENMAX({max_enmax}) 的1.3倍，建议提高到 {int(1.3*max_enmax)} 以上"))
            else:
                results.append(("PASS", f"ENCUT={encut} ≥ 1.3×ENMAX({max_enmax})"))
        except Exception:
            pass

    # ISIF/IBRION 匹配
    if incar and "ISIF" in incar and "IBRION" in incar:
        try:
            isif = int(float(incar["ISIF"]))
            ibrion = int(float(incar["IBRION"]))
            if isif >= 3 and ibrion == -1:
                results.append(("ERROR", "ISIF>=3 需要离子优化，但 IBRION=-1（不做离子步），参数冲突"))
            else:
                results.append(("PASS", "ISIF/IBRION 组合合理"))
        except Exception:
            pass

    # ISMEAR 合理性（软提醒）
    if incar and "ISMEAR" in incar:
        try:
            ismear = int(float(incar["ISMEAR"]))
            if ismear == 1:
                results.append(("WARN", "ISMEAR=1（金属用）；若体系是半导体/绝缘体，请改用 ISMEAR=0"))
            if ismear == -5:
                results.append(("PASS", "ISMEAR=-5 四面体法，适合 DOS/能带/精确能量"))
        except Exception:
            pass

    # ---- 输出 ----
    print("=" * 60)
    print("VASP 输入文件校验结果")
    print("=" * 60)
    has_error = False
    for level, msg in results:
        tag = {"PASS": "[PASS ]", "WARN": "[WARN ]", "ERROR": "[ERROR]"}.get(level, "[INFO ]")
        print(f"{tag} {msg}")
        if level == "ERROR":
            has_error = True
    print("=" * 60)
    n_pass = sum(1 for l, _ in results if l == "PASS")
    n_warn = sum(1 for l, _ in results if l == "WARN")
    n_err = sum(1 for l, _ in results if l == "ERROR")
    print(f"汇总: PASS={n_pass}, WARN={n_warn}, ERROR={n_err}")
    if has_error:
        print("结论: 存在 ERROR，必须修复后重新校验！")
        sys.exit(1)
    else:
        print("结论: 无 ERROR。如有 WARN 请用户知情确认。")
        sys.exit(0)


if __name__ == "__main__":
    main()
