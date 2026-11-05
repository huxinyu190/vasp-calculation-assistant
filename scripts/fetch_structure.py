#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构获取：把「想要的结构」变成标准 POSCAR。

数据源按推荐优先级：
  1. 本地文件              --from xxx.cif / POSCAR / CONTCAR    最可靠，推荐
  2. OPTIMADE 公开接口     --formula WO3                       免费、无需 key、JSON 稳定
  3. Materials Project API --formula WO3 --mp-key KEY          需自己注册的 key，信息更全
  4. COD 网页              --formula WO3 --cod                 实验结构，需 pymatgen，较慢
  拿不到就报错退出，由 Agent 停下让用户手动下载——绝不猜结构。

关于 OPTIMADE：这是材料数据库的统一查询协议，Materials Project 的公开只读端点
（https://optimade.materialsproject.org）不需要 API key，直接返回完整晶胞的
lattice_vectors + cartesian_site_positions + species_at_sites。
好处是不用做空间群对称展开（CIF 才需要那样处理），因此**不依赖 pymatgen**。

用法示例：
  python fetch_structure.py --formula WO3 --list                    # 看有哪些候选
  python fetch_structure.py --formula WO3 --index 2 -o POSCAR       # 取第 3 条
  python fetch_structure.py --formula WO3 --mp-id mp-553964 -o POSCAR
  python fetch_structure.py --from WO3.cif -o POSCAR                # 本地 CIF（需 pymatgen）
  python fetch_structure.py --from CONTCAR --supercell 2 2 1 -o POSCAR
  python fetch_structure.py --formula WO3 --mp-key $MP_API_KEY -o POSCAR

输出：
  POSCAR              标准 VASP5 格式（元素名 + 计数 + 分数坐标）
  structure_info.txt  结构摘要（元素、原子数、晶格、体积、空间群、来源），供核对
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

COD_BASE = "https://www.crystallography.net/cod"
UA = ("Mozilla/5.0 (compatible; vasp-calculation-assistant/1.4; "
      "+https://github.com/huxinyu190/vasp-calculation-assistant)")

# OPTIMADE 端点。mp 是主力（结构全、免费只读），其余作备选，失败会自动跳过。
OPTIMADE_PROVIDERS = {
    "mp": "https://optimade.materialsproject.org",
    "odbx": "https://optimade.odbx.science",
    "aflow": "https://aflow.org/API/optimade",
}


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------

def http_get(url, timeout=40, binary=False, retries=2):
    """取 URL，失败重试。返回 str 或 bytes；最终失败抛 RuntimeError。"""
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return data if binary else data.decode("utf-8", errors="replace")
        except Exception as e:
            last = e
    raise RuntimeError(f"请求失败 {url} → {last}") from last


def http_json(url, timeout=60):
    txt = http_get(url, timeout=timeout)
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"返回的不是合法 JSON（{e}）：{url}") from e


def strip_tags(s):
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------------------
# 化学式工具
# ----------------------------------------------------------------------------

def parse_formula(f):
    """'WO3' -> {'W':1.0,'O':3.0}；'Ni0.05W0.95O3' -> {'Ni':0.05,'W':0.95,'O':3.0}"""
    d = {}
    for el, num in re.findall(r"([A-Z][a-z]?)(\d*\.?\d*)", f):
        if not el:
            continue
        d[el] = d.get(el, 0.0) + (float(num) if num else 1.0)
    return d


def reduced_formula(f):
    """转成 OPTIMADE 的 chemical_formula_reduced 写法：元素字母序、约简、省略 1。

    'WO3' -> 'O3W'；'W2O6' -> 'O3W'；'NiO' -> 'NiO'
    非整数配比（掺杂固溶体）无法约简，返回 None，改用元素过滤。
    """
    d = parse_formula(f)
    if not d:
        return None
    vals = list(d.values())
    if any(abs(v - round(v)) > 1e-6 for v in vals):
        return None
    ints = [int(round(v)) for v in vals]
    g = 0
    for v in ints:
        g = math.gcd(g, v) or v
    if g > 1:
        ints = [v // g for v in ints]
    parts = []
    for el in sorted(d.keys()):
        n = ints[list(d.keys()).index(el)]
        parts.append(el + (str(n) if n != 1 else ""))
    return "".join(parts)


def canon_formula(s):
    """归一化成可比对的键，用于「这是不是我想要的那个化学计量比」的判定。"""
    return tuple(sorted((k, round(v, 3)) for k, v in parse_formula(s).items()))


def formula_str(elements, counts):
    return "".join(str(e) + (str(n) if n != 1 else "") for e, n in zip(elements, counts))


# ----------------------------------------------------------------------------
# 结构表示与读写
# ----------------------------------------------------------------------------

def have(mod):
    try:
        __import__(mod)
        return True
    except Exception:
        return False


HAS_PYMATGEN = have("pymatgen.core")
HAS_ASE = have("ase")


def _cart_to_frac(pts, lat):
    """直角坐标 → 分数坐标（解 3x3 线性方程，纯标准库）。"""
    m = lat
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    if abs(det) < 1e-12:
        raise RuntimeError("晶格矢量线性相关，无法换算坐标")
    inv = [
        [(m[1][1] * m[2][2] - m[1][2] * m[2][1]) / det,
         (m[0][2] * m[2][1] - m[0][1] * m[2][2]) / det,
         (m[0][1] * m[1][2] - m[0][2] * m[1][1]) / det],
        [(m[1][2] * m[2][0] - m[1][0] * m[2][2]) / det,
         (m[0][0] * m[2][2] - m[0][2] * m[2][0]) / det,
         (m[0][2] * m[1][0] - m[0][0] * m[1][2]) / det],
        [(m[1][0] * m[2][1] - m[1][1] * m[2][0]) / det,
         (m[0][1] * m[2][0] - m[0][0] * m[2][1]) / det,
         (m[0][0] * m[1][1] - m[0][1] * m[1][0]) / det],
    ]
    return [[sum(p[k] * inv[k][j] for k in range(3)) for j in range(3)] for p in pts]


def _group_by_element(syms, frac):
    """POSCAR 要求同元素连续，这里按元素首次出现顺序重排。"""
    order = []
    for x in syms:
        if x not in order:
            order.append(x)
    idx = sorted(range(len(syms)), key=lambda k: order.index(syms[k]))
    return order, [syms.count(e) for e in order], [frac[k] for k in idx]


def _from_optimade(attrs, note):
    lat = [[float(x) for x in v] for v in attrs["lattice_vectors"]]
    cart = attrs.get("cartesian_site_positions") or []
    syms = attrs.get("species_at_sites") or []
    if not cart or not syms:
        raise RuntimeError("该条目没有给出原子坐标（可能是只含对称信息的条目）")
    frac = _cart_to_frac([[float(x) for x in p] for p in cart], lat)
    elements, counts, frac = _group_by_element(list(syms), frac)
    return {
        "lattice": lat, "elements": elements, "counts": counts, "frac": frac,
        "note": note,
        "sg": attrs.get("space_group_symbol_hermann_mauguin") or "",
    }


def _from_pymatgen(s, note):
    elements, counts = [], []
    for sp, n in sorted(s.composition.items(), key=lambda kv: kv[0].Z):
        elements.append(sp.symbol)
        counts.append(int(round(n)))
    frac = [list(map(float, site.frac_coords)) for site in s]
    return {
        "lattice": [list(map(float, v)) for v in s.lattice.matrix],
        "elements": elements, "counts": counts, "frac": frac,
        "note": note, "_pymatgen": s,
    }


def _from_ase(atoms, note):
    syms = atoms.get_chemical_symbols()
    frac = atoms.get_scaled_positions().tolist()
    elements, counts, frac = _group_by_element(syms, frac)
    return {
        "lattice": atoms.cell.array.tolist(),
        "elements": elements, "counts": counts, "frac": frac,
        "note": note, "_ase": atoms,
    }


def _read_poscar_builtin(path):
    """无 ASE/pymatgen 时的最小 POSCAR 解析（VASP4/5，Direct 与 Cartesian 都支持）。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n") for ln in f]
    if len(lines) < 8:
        raise RuntimeError(f"{path} 行数太少，不像 POSCAR")
    scale = float(lines[1].split()[0])
    lat = [[float(x) * scale for x in lines[i].split()[:3]] for i in (2, 3, 4)]
    toks = lines[5].split()
    if all(re.fullmatch(r"\d+", t) for t in toks):
        elements, idx = None, 5      # VASP4：第 6 行直接是计数
    else:
        elements, idx = toks, 6      # VASP5：第 6 行元素名，第 7 行计数
    counts = [int(x) for x in lines[idx].split()]
    if elements is None:
        elements = [f"X{i+1}" for i in range(len(counts))]
    mode = lines[idx + 1].strip().lower()
    start = idx + 2
    total = sum(counts)
    frac = [[float(x) for x in ln.split()[:3]] for ln in lines[start:start + total]]
    if not mode.startswith(("d", "s")):   # Cartesian：先乘 scale 再换算
        frac = _cart_to_frac([[x * scale for x in p] for p in frac], lat)
    return {"lattice": lat, "elements": elements, "counts": counts, "frac": frac,
            "note": "POSCAR（内置解析器）"}


def _read_cif_builtin(path):
    """无 pymatgen 时的 CIF 兜底：只接受原子已全展开的 CIF。

    CIF 通常只存「非对称单元 + 空间群操作」，要得到完整晶胞必须按空间群展开。
    这里不做展开（需要完整空间群操作表，自己实现不可靠），遇到非恒等对称操作
    就明确报错。宁可报错，也不给一个原子数少一半的结构。
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    def num(tag):
        m = re.search(rf"^\s*{tag}\s+([-\d.]+)", text, re.I | re.M)
        return float(m.group(1)) if m else None

    a, b, c = num("_cell_length_a"), num("_cell_length_b"), num("_cell_length_c")
    al, be, ga = (num("_cell_angle_alpha"), num("_cell_angle_beta"),
                  num("_cell_angle_gamma"))
    if None in (a, b, c, al, be, ga):
        raise RuntimeError(f"{path} 不是标准 CIF（缺晶胞参数），或格式不完整")

    ops = []
    for tag in ("_symmetry_equiv_pos_as_xyz", "_space_group_symop_operation_xyz"):
        ops += re.findall(rf"{tag}\s+['\"]?([-+xyzXYZ0-9/\s,.]+)['\"]?", text)
    ops += re.findall(r"^\s*(-?[\dx/.]+?\+?x[^\n]*,[^\n]*,[^\n]*)\s*$", text, re.M)
    ops = [o.strip() for o in ops if o.strip()]
    is_identity = lambda s: s.replace(" ", "").lower() in ("x,y,z", "+x,+y,+z")
    if [o for o in ops if not is_identity(o)]:
        raise RuntimeError(
            f"{path} 是「非对称单元 + 空间群」形式（含非恒等对称操作），"
            f"必须按空间群展开才是完整晶胞。\n"
            f"  没有 pymatgen 时不做这一步（要完整空间群操作表，自己实现不可靠）。\n"
            f"  三个办法：\n"
            f"    ① 改用 OPTIMADE 公开接口：--formula <化学式>（免费、无需 key、无需 pymatgen）\n"
            f"    ② pip install pymatgen  之后重跑同一条命令\n"
            f"    ③ 用 VESTA 打开这个 CIF → File → Export Data → 存 POSCAR，再 --from POSCAR\n"
            f"  注：只按非对称单元算，原子数会少一大半，得到的能量毫无意义。"
        )

    lines = text.splitlines()
    fields, rows = [], []
    i = 0
    while i < len(lines):
        if lines[i].strip().lower().startswith("loop_"):
            fields, rows = [], []
            i += 1
            while i < len(lines) and lines[i].strip().startswith("_"):
                fields.append(lines[i].strip().lower())
                i += 1
            while i < len(lines):
                s = lines[i].strip()
                if (not s or s.startswith(("_", "#"))
                        or s.lower().startswith(("loop_", "data_"))):
                    break
                rows.append(_split_quoted(s))
                i += 1
            if any(f.startswith("_atom_site_fract_x") for f in fields):
                break
            continue
        i += 1

    if not rows:
        raise RuntimeError(f"{path} 里没找到原子坐标（_atom_site_fract_x/y/z）")

    def col(name):
        return fields.index(name) if name in fields else None

    ix, iy, iz = col("_atom_site_fract_x"), col("_atom_site_fract_y"), col("_atom_site_fract_z")
    isym, ilab = col("_atom_site_type_symbol"), col("_atom_site_label")
    if None in (ix, iy, iz):
        raise RuntimeError(f"{path} 的原子坐标不是分数坐标（_atom_site_fract_*）")

    syms, frac = [], []
    for r in rows:
        if len(r) <= max(ix, iy, iz):
            continue
        sym = re.sub(r"[^A-Za-z]", "", r[isym]) if isym is not None and isym < len(r) else ""
        if not sym and ilab is not None and ilab < len(r):
            m = re.match(r"([A-Z][a-z]?)", r[ilab])
            sym = m.group(1) if m else ""
        if not sym:
            continue
        syms.append(sym)
        frac.append([float(r[ix]), float(r[iy]), float(r[iz])])

    elements, counts, frac = _group_by_element(syms, frac)
    lat = cell_to_lattice(a, b, c, al, be, ga)
    return {"lattice": lat, "elements": elements, "counts": counts, "frac": frac,
            "note": "CIF（内置解析器；该 CIF 为全原子坐标，无需对称展开）"}


def _split_quoted(s):
    out, cur, q = [], "", None
    for ch in s:
        if q:
            if ch == q:
                out.append(cur)
                cur, q = "", None
            else:
                cur += ch
        elif ch in "'\"":
            q = ch
        elif ch.isspace():
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def cell_to_lattice(a, b, c, alpha, beta, gamma):
    """晶胞参数 → 3x3 晶格矩阵（行向量）。"""
    al, be, ga = (math.radians(x) for x in (alpha, beta, gamma))
    cos_al, cos_be, cos_ga = math.cos(al), math.cos(be), math.cos(ga)
    sin_ga = math.sin(ga) or 1e-8
    cx = c * cos_be
    cy = c * (cos_al - cos_be * cos_ga) / sin_ga
    cz2 = c * c - cx * cx - cy * cy
    return [[a, 0.0, 0.0],
            [b * cos_ga, b * sin_ga, 0.0],
            [cx, cy, math.sqrt(cz2) if cz2 > 0 else 0.0]]


def load_structure(path):
    low = path.lower()
    if low.endswith(".cif"):
        if HAS_PYMATGEN:
            from pymatgen.io.cif import CifParser
            structs = CifParser(path).parse_structures(primitive=False)
            if not structs:
                raise RuntimeError(f"{path} 里没有解析出任何结构")
            return _from_pymatgen(structs[0], "CIF（pymatgen 已按空间群展开，完整晶胞）")
        return _read_cif_builtin(path)
    if HAS_ASE:
        from ase.io import read
        return _from_ase(read(path), "POSCAR/CONTCAR（ASE 读取）")
    return _read_poscar_builtin(path)


def write_poscar(st, out):
    if st.get("_pymatgen") is not None:
        st["_pymatgen"].to(filename=out, fmt="poscar")
        return "pymatgen"
    if st.get("_ase") is not None:
        st["_ase"].write(out, format="vasp", direct=True, vasp5=True, sort=True)
        return "ASE"
    with open(out, "w", encoding="utf-8") as f:
        f.write("Generated by fetch_structure.py\n")
        f.write("1.0\n")
        for v in st["lattice"]:
            f.write("  " + "  ".join(f"{x:20.12f}" for x in v) + "\n")
        f.write("  " + "  ".join(st["elements"]) + "\n")
        f.write("  " + "  ".join(str(c) for c in st["counts"]) + "\n")
        f.write("Direct\n")
        for p in st["frac"]:
            f.write("  " + "  ".join(f"{x:20.12f}" for x in p) + "\n")
    return "内置写入器"


def structure_summary(st, source):
    lat = st["lattice"]
    a = math.sqrt(sum(x * x for x in lat[0]))
    b = math.sqrt(sum(x * x for x in lat[1]))
    c = math.sqrt(sum(x * x for x in lat[2]))
    vol = abs(
        lat[0][0] * (lat[1][1] * lat[2][2] - lat[1][2] * lat[2][1])
        - lat[0][1] * (lat[1][0] * lat[2][2] - lat[1][2] * lat[2][0])
        + lat[0][2] * (lat[1][0] * lat[2][1] - lat[1][1] * lat[2][0])
    )
    natoms = sum(st["counts"]) or 1
    lines = [
        "=" * 64,
        "结构摘要（写 POSCAR 前请核对）",
        "=" * 64,
        f"来源    : {source}",
        f"化学式  : {formula_str(st['elements'], st['counts'])}",
        f"原子数  : {natoms}  ({', '.join(str(e) + str(n) for e, n in zip(st['elements'], st['counts']))})",
        f"晶格    : a={a:.4f}  b={b:.4f}  c={c:.4f} Å",
        f"晶胞体积: {vol:.3f} Å³   ({vol / natoms:.2f} Å³/atom)",
    ]
    if st.get("sg"):
        lines.append(f"空间群  : {st['sg']}")
    lines += ["晶格矢量:"]
    for v in lat:
        lines.append("  " + "  ".join(f"{x:12.6f}" for x in v))
    lines.append("=" * 64)
    lines.append(f"POSCAR 元素顺序：{', '.join(st['elements'])}"
                 f"  → POTCAR 必须按同样顺序拼接，否则元素会对错")
    lines.append("注意：坐标正确不代表这是你要的那一相——同种化学式常有多个相"
                 "（WO₃ 就有单斜/正交/六方/四方），务必核对空间群与晶格常数。")
    return "\n".join(lines)


def apply_supercell(st, reps):
    na, nb, nc = reps
    if min(reps) < 1:
        raise RuntimeError("扩胞倍数必须 ≥ 1")
    if (na, nb, nc) == (1, 1, 1):
        return st
    if st.get("_ase") is not None:
        return _from_ase(st["_ase"].repeat((na, nb, nc)),
                         st["note"] + f" + 扩胞 {na}×{nb}×{nc}")
    if st.get("_pymatgen") is not None:
        s = st["_pymatgen"].copy()
        s.make_supercell([na, nb, nc])
        return _from_pymatgen(s, st["note"] + f" + 扩胞 {na}×{nb}×{nc}")
    lat = [[x * na for x in st["lattice"][0]],
           [x * nb for x in st["lattice"][1]],
           [x * nc for x in st["lattice"][2]]]
    frac, elements, counts = [], [], []
    k = 0
    for i, n in enumerate(st["counts"]):
        seg = st["frac"][k:k + n]
        k += n
        elements.append(st["elements"][i])
        counts.append(n * na * nb * nc)
        for a_ in range(na):
            for b_ in range(nb):
                for c_ in range(nc):
                    for p in seg:
                        frac.append([(p[0] + a_) / na, (p[1] + b_) / nb, (p[2] + c_) / nc])
    return {"lattice": lat, "elements": elements, "counts": counts, "frac": frac,
            "note": st["note"] + f" + 扩胞 {na}×{nb}×{nc}"}


# ----------------------------------------------------------------------------
# OPTIMADE（主力在线源，免费、无需 key）
# ----------------------------------------------------------------------------

def optimade_search(formula=None, elements=None, provider="mp", limit=50, base=None):
    """按化学式/元素查结构，返回 [{id, formula, nsites, sg, attrs}]。"""
    root = base or OPTIMADE_PROVIDERS.get(provider, OPTIMADE_PROVIDERS["mp"])
    filters = []
    if formula:
        red = reduced_formula(formula)
        if red is None:
            raise RuntimeError(
                f"{formula} 是掺杂/固溶体配比（非整数化学计量比），数据库里没有「现成的」"
                f"这种结构——MP 和 COD 收录的都是有序化合物。\n"
                f"  正确做法：取母体结构 → 扩胞 → 替换原子，例如：\n"
                f"    ① python fetch_structure.py --formula WO3 --supercell 2 2 2 -o POSCAR\n"
                f"       （32 个 W 位，替换 1 个 Ni ≈ 3.1 at%）\n"
                f"    ② 再把 POSCAR 第 6 行元素名与一个 W 的坐标改成 Ni（元素顺序、计数、"
                f"POTCAR 都要同步改，改完必须重新 validate_inputs）\n"
                f"  细节见 references/structure-sourcing.md 的「掺杂结构怎么建」\n"
                f"  想先看看有哪些相关母体：--formula WO3 或 --elements \"W,Ni,O\""
            )
        filters.append(f'chemical_formula_reduced="{red}"')
    elif elements:
        els = [e.strip() for e in re.split(r"[,\s]+", elements) if e.strip()]
        filters.append("elements HAS ALL " + ",".join(f'"{e}"' for e in els))
    else:
        raise RuntimeError("OPTIMADE 检索需要 --formula 或 --elements")

    params = {"filter": " AND ".join(filters), "page_limit": str(limit)}
    # 用 quote 而不是默认的 quote_plus：OPTIMADE 的 filter 里的空格必须编成 %20，
    # 编成 + 会被当成字面量，查询直接返回 0 条（实测踩过）。
    url = f"{root}/v1/structures?" + urllib.parse.urlencode(
        params, quote_via=urllib.parse.quote)
    data = http_json(url)

    rows = []
    for item in data.get("data", []):
        at = item.get("attributes", {})
        f_red = at.get("chemical_formula_reduced", "")
        rows.append({
            "id": item.get("id", ""),
            "formula": f_red or at.get("chemical_formula_descriptive", ""),
            "nsites": at.get("nsites"),
            "sg": at.get("space_group_symbol_hermann_mauguin", "") or "",
            "attrs": at,
        })
    # 按原子数升序：小晶胞算得快，通常是用户想要的第一个
    rows.sort(key=lambda r: (r["nsites"] or 10 ** 6, r["id"]))
    return rows, data.get("meta", {})


# ----------------------------------------------------------------------------
# Materials Project 官方 API（需 key，信息更全）
# ----------------------------------------------------------------------------

def mp_search(formula, api_key, limit=30):
    try:
        from mp_api.client import MPRester
    except ImportError:
        raise RuntimeError(
            "缺 mp-api 库，无法用 Materials Project 官方 API。\n"
            "  安装：pip install mp-api\n"
            "  key（免费）：https://materialsproject.org/api\n"
            "  不想注册：直接用默认的 OPTIMADE 接口（--formula WO3，无需 key）"
        )
    with MPRester(api_key) as mpr:
        docs = mpr.materials.summary.search(
            formula=formula,
            fields=["material_id", "formula_pretty", "structure",
                    "symmetry", "energy_above_hull", "is_stable"],
        )
    return docs[:limit]


# ----------------------------------------------------------------------------
# COD（备选，实验结构）
# ----------------------------------------------------------------------------

_TR_SPLIT = re.compile(r"<tr[^>]*>", re.I)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)


def cod_search(formula=None, elements=None, max_rows=200, count=100):
    """COD 结果页 → 候选列表。count 单页条数（COD 默认 20，实测 100 较稳）。"""
    params = {"count": str(count)}
    exact = None
    if elements:
        els = [e.strip() for e in re.split(r"[,\s]+", elements) if e.strip()]
        for i, e in enumerate(els[:8], start=1):
            params[f"el{i}"] = e
        params["nel"] = str(len(els))
    elif formula:
        els = list(parse_formula(formula).keys())
        if not (1 <= len(els) <= 8):
            raise RuntimeError(f"化学式 {formula} 有 {len(els)} 种元素，超出 COD 上限（8）")
        for i, e in enumerate(els, start=1):
            params[f"el{i}"] = e
        params["nel"] = str(len(els))
        exact = canon_formula(formula)
    else:
        raise RuntimeError("COD 检索需要 --formula 或 --elements")

    url = f"{COD_BASE}/result?" + urllib.parse.urlencode(params)
    html = http_get(url, timeout=90)

    rows = []
    for block in _TR_SPLIT.split(html)[1:]:
        m = re.search(r'href="(\d{6,8})\.cif"', block)
        if not m:
            continue
        cells = [strip_tags(c) for c in _TD.findall(block)]
        if len(cells) < 5:
            continue
        f_txt = cells[2] if len(cells) > 2 else ""
        if exact is not None and canon_formula(f_txt) != exact:
            continue
        rows.append({"id": m.group(1), "formula": f_txt,
                     "sg": cells[3] if len(cells) > 3 else "",
                     "cell": cells[4] if len(cells) > 4 else "",
                     "volume": cells[5] if len(cells) > 5 else ""})
        if len(rows) >= max_rows:
            break
    return rows


def cod_fetch_cif(cod_id, dest):
    data = http_get(f"{COD_BASE}/{cod_id}.cif", binary=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="结构获取：本地文件 / OPTIMADE / Materials Project / COD → 标准 POSCAR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python fetch_structure.py --formula WO3 --list
  python fetch_structure.py --formula WO3 --index 2 -o POSCAR
  python fetch_structure.py --formula WO3 --mp-id mp-553964 -o POSCAR
  python fetch_structure.py --from WO3.cif -o POSCAR
  python fetch_structure.py --from CONTCAR --supercell 2 2 1 -o POSCAR
""")
    ap.add_argument("--from", dest="src", metavar="FILE",
                    help="本地结构文件（.cif / POSCAR / CONTCAR）")
    ap.add_argument("--formula", help="目标化学式，如 WO3、NiO、MgO")
    ap.add_argument("--elements", help="按元素检索，如 'W,O'（化学计量比不固定时用）")
    ap.add_argument("--provider", default="mp",
                    choices=sorted(OPTIMADE_PROVIDERS), help="OPTIMADE 数据源（默认 mp）")
    ap.add_argument("--optimade-url", help="自定义 OPTIMADE 端点（覆盖 --provider）")
    ap.add_argument("--mp-key", help="Materials Project 官方 API key（不用 OPTIMADE 时）")
    ap.add_argument("--mp-id", help="直接按 Materials Project 编号取，如 mp-553964")
    ap.add_argument("--cod", action="store_true", help="改从 COD 取（实验结构，需 pymatgen）")
    ap.add_argument("--cod-id", help="直接指定 COD 编号（配合 --cod）")
    ap.add_argument("--list", action="store_true", help="只列候选，不下载")
    ap.add_argument("--index", type=int, default=0, help="选第几个候选（从 0 开始，默认 0）")
    ap.add_argument("--limit", type=int, default=50, help="候选列表上限（默认 50）")
    ap.add_argument("--supercell", nargs=3, type=int, metavar=("NA", "NB", "NC"),
                    help="扩胞，如 --supercell 2 2 1")
    ap.add_argument("-o", "--out", default="POSCAR", help="输出 POSCAR 路径（默认 POSCAR）")
    ap.add_argument("--keep-cif", action="store_true", help="保留下载的 CIF（默认用完删除）")
    args = ap.parse_args()

    workdir = os.getcwd()
    tmp_cif = None

    try:
        # ---- 1. 本地文件 ----
        if args.src:
            if not os.path.exists(args.src):
                raise RuntimeError(f"文件不存在：{args.src}")
            st = load_structure(args.src)
            source = f"本地文件 {os.path.basename(args.src)}"

        # ---- 2. Materials Project 官方 API（给了 key 才走） ----
        elif args.mp_key or (args.mp_id and os.environ.get("MP_API_KEY")):
            key = args.mp_key or os.environ.get("MP_API_KEY")
            if args.mp_id:
                rows = _mp_by_id(args.mp_id, key)
            else:
                if not args.formula:
                    raise RuntimeError("用 Materials Project API 需要 --formula 或 --mp-id")
                rows = mp_search(args.formula, key, args.limit)
            if not rows:
                raise RuntimeError(f"MP 里没查到 {args.formula or args.mp_id}")
            print(f"[MP] 命中 {len(rows)} 条：\n")
            for i, d in enumerate(rows):
                eh = getattr(d, "energy_above_hull", None)
                sym = getattr(getattr(d, "symmetry", None), "symbol", "") or ""
                print(f"  {i:>3}  {str(d.material_id):<12} {d.formula_pretty:<16} {sym:<14} "
                      f"E_hull={'—' if eh is None else f'{eh:.4f}'} eV/atom  "
                      f"{'稳定' if getattr(d, 'is_stable', False) else '亚稳'}")
            if args.list:
                print("\n[提示] 用 --index <序号> 选（默认 0）。")
                return 0
            pick = rows[args.index if args.index < len(rows) else 0]
            st = _from_pymatgen(pick.structure, f"Materials Project {pick.material_id}")
            eh = getattr(pick, "energy_above_hull", None)
            source = (f"Materials Project {pick.material_id}（{pick.formula_pretty}，"
                      f"E_hull={'—' if eh is None else f'{eh:.4f}'} eV/atom）")

        # ---- 3. OPTIMADE 公开接口（默认在线源） ----
        elif args.formula or args.elements or args.mp_id:
            if not (args.formula or args.elements):
                raise RuntimeError("按 MP 编号取结构需要 --mp-key（走官方 API）")
            rows, meta = optimade_search(args.formula, args.elements,
                                         provider=args.provider, limit=args.limit,
                                         base=args.optimade_url)
            prov = meta.get("provider", {}).get("name", args.provider)
            total = meta.get("data_returned")
            if not rows:
                raise RuntimeError(
                    f"{prov} 里没查到 {args.formula or args.elements}。\n"
                    f"  手动查：https://materialsproject.org/ 搜索 {args.formula or args.elements}\n"
                    f"  或换数据源：--provider odbx / --provider aflow\n"
                    f"  或自己下载 CIF 后用 --from"
                )
            print(f"[{prov}] 命中 {len(rows)} 条（本次返回 {total} 条，按原子数升序）：\n")
            print(f"  {'#':>3}  {'ID':<14} {'化学式':<14} {'原子数':>6}  {'空间群'}")
            print("  " + "-" * 68)
            for i, r in enumerate(rows):
                print(f"  {i:>3}  {r['id']:<14} {r['formula']:<14} "
                      f"{str(r['nsites']):>6}  {r['sg'] or '（该库未提供）'}")
            if args.list:
                print("\n[提示] 用 --index <序号> 选一条（默认 0，即原子数最少的那个）。")
                print("       同一化学式常有多个相（WO₃ 就分单斜/正交/六方/四方），"
                      "只按原子数选不一定对，")
                print("       请用 --index 配合已知空间群/晶格常数核对；拿不准就把列表贴给用户确认。")
                return 0
            pick = rows[args.index if args.index < len(rows) else 0]
            st = _from_optimade(pick["attrs"], f"OPTIMADE {prov} 的 {pick['id']}")
            source = f"{prov} {pick['id']}（{pick['formula']}，空间群 {pick['sg']}）"

        # ---- 4. COD 备选 ----
        elif args.cod or args.cod_id:
            if not (args.formula or args.elements or args.cod_id):
                raise RuntimeError("用 COD 需要 --formula 或 --elements（或 --cod-id）")
            if not HAS_PYMATGEN and not args.list:
                raise RuntimeError(
                    "从 COD 取结构需要 pymatgen：COD 的 CIF 是「非对称单元 + 空间群」，"
                    "不展开会得到原子数严重偏少的结构。\n"
                    "  安装：pip install pymatgen\n"
                    "  更省事的办法：去掉 --cod，用默认的 OPTIMADE 接口"
                    "（免费、无需 key，也无需 pymatgen）\n"
                    "  只想看候选：加 --list"
                )
            if args.cod_id:
                tmp_cif = os.path.join(workdir, f"_cod_{args.cod_id}.cif")
                print(f"[COD] 下载 {COD_BASE}/{args.cod_id}.cif")
                cod_fetch_cif(args.cod_id, tmp_cif)
                st = load_structure(tmp_cif)
                source = f"COD {args.cod_id}（实验结构）"
            else:
                rows = cod_search(args.formula, args.elements)
                if not rows:
                    raise RuntimeError(
                        f"COD 里没查到 {args.formula or args.elements}。\n"
                        f"  手动查：{COD_BASE}/result?text={urllib.parse.quote(args.formula or '')}\n"
                        f"  或改用 OPTIMADE（去掉 --cod）"
                    )
                print(f"[COD] 命中 {len(rows)} 条候选：\n")
                print(f"  {'#':>3}  {'COD ID':<9} {'化学式':<20} {'空间群':<16} 晶胞 (Å, °)")
                print("  " + "-" * 88)
                for i, r in enumerate(rows):
                    print(f"  {i:>3}  {r['id']:<9} {r['formula']:<20} {r['sg']:<16} {r['cell']}")
                if args.list:
                    print("\n[提示] 用 --cod-id <编号> 指定要下载的那一条。")
                    return 0
                pick = rows[args.index if args.index < len(rows) else 0]
                print(f"\n[COD] 选用 {pick['id']}（{pick['formula']}，{pick['sg']}）")
                tmp_cif = os.path.join(workdir, f"_cod_{pick['id']}.cif")
                cod_fetch_cif(pick["id"], tmp_cif)
                st = load_structure(tmp_cif)
                source = f"COD {pick['id']}（实验结构，空间群 {pick['sg']}）"

        else:
            raise RuntimeError(
                "没指定结构来源。三种方式：\n"
                "  --formula WO3                  OPTIMADE 公开接口（免费、无需 key，推荐）\n"
                "  --from POSCAR/CIF              本地文件（最可靠）\n"
                "  --formula WO3 --mp-key KEY     Materials Project 官方 API（信息更全）"
            )

        if args.supercell:
            st = apply_supercell(st, args.supercell)

        print()
        print(structure_summary(st, source))
        how = write_poscar(st, args.out)
        print(f"\n[OK] 已写出 {args.out}（{how}）")

        info = os.path.join(workdir, "structure_info.txt")
        with open(info, "w", encoding="utf-8") as f:
            f.write(structure_summary(st, source) + "\n")
        print(f"[OK] 结构摘要已存 {info}")
        print("\n下一步：python validate_inputs.py <目录> 确认晶格尺寸合理后再算。")
        if args.cod or args.cod_id:
            print("注意：COD 给的是实验结构（室温），PBE 优化后晶格通常变化 1~2%，"
                  "正式算前建议先做一次 ISIF=3 的结构优化。")
        return 0

    except RuntimeError as e:
        print(f"\n[STOP] {e}", file=sys.stderr)
        print("\n按铁律：结构拿不到或无法可靠转换时，不猜、不编。"
              "\n请把上面的报错和网址交给用户，让用户手动处理后放进来。", file=sys.stderr)
        return 2
    finally:
        if tmp_cif and os.path.exists(tmp_cif) and not args.keep_cif:
            try:
                os.remove(tmp_cif)
            except OSError:
                pass


def _mp_by_id(material_id, key):
    try:
        from mp_api.client import MPRester
    except ImportError:
        raise RuntimeError("缺 mp-api 库：pip install mp-api")
    with MPRester(key) as mpr:
        docs = mpr.materials.summary.search(
            material_ids=[material_id],
            fields=["material_id", "formula_pretty", "structure", "symmetry",
                    "energy_above_hull", "is_stable"],
        )
    return docs


if __name__ == "__main__":
    sys.exit(main())
