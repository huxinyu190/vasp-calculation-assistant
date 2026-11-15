#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""需求确认单：生成骨架 + 门禁校验 + 生成前二次确认（能力 0）。

生成输入文件之前，先把「要算什么、按什么物理模型算、按什么精度算」问清楚并落成
一份可核对的确认单。这个脚本负责四件事：

  --new <任务类型>   生成确认单骨架：必填项标记好，阶段链按任务类型预填
  --check <文件>     检查已填的确认单：必填项是否齐全、关键物理有没有被静默默认、
                     用户是否在最后一次改动之后确认过
  --preview <文件>   把确认单渲染成给人看的确认稿，交给用户过目
  --approve <文件>   记录用户对确认稿的同意，绑定当前内容指纹

为什么确认要分两步：确认单是「我理解的需求」，不是「用户同意的需求」。让 Agent 自己
在单子上写一句“确认生成”，既不构成用户授权，也拦不住改完单子还拿着旧授权去生成。
所以「用户确认」不是一个可手填的栏位，而是一份带时间戳和内容指纹的凭据：
确认稿展示之后用户点头，才 `--approve` 落一份凭据；确认单之后再被改动任何一个字，
指纹对不上，凭据立即失效，必须重新过目。

退出码：
  0  通过，可以进入生成阶段
  2  用法错误或文件读不到
  3  门禁阻断，禁止生成输入文件

只用标准库；不联网；只改动 -o 指定的确认单、以及 --preview / --approve 生成的
`<sheet>.preview.json`（确认稿展示记录）与 `<sheet>.approval.json`（用户授权凭据）。
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

# Windows 控制台默认可能是 GBK，中文输出会炸
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PLACEHOLDER = "待填"

SIMPLE = "simple"
FULL = "full"
BOTH = (SIMPLE, FULL)
FULL_ONLY = (FULL,)
NONE = ()

# ---------------------------------------------------------------- 任务类型

TASKS = {
    "relax": {
        "name": "几何优化（relax）",
        "chain": "relax",
        "simple_ok": True,
        "inherent": [],
    },
    "energy": {
        "name": "能量差 / 形成能 / 相稳定性",
        "chain": "relax → static",
        "simple_ok": True,
        "inherent": [("能量参考态", "能量差与形成能必须有明确参考态和化学势，否则数值没有意义")],
    },
    "dos": {
        "name": "DOS / PDOS",
        "chain": "relax → static → dos",
        "simple_ok": True,
        "inherent": [],
    },
    "band": {
        "name": "能带 / 带隙",
        "chain": "relax → static → band",
        "simple_ok": True,
        "inherent": [],
    },
    "workfunction": {
        "name": "功函数",
        "chain": "relax → static（workfunction）",
        "simple_ok": False,
        "inherent": [
            ("真空层", "功函数要取真空能级平台，真空不够厚平台就不平"),
            ("偶极修正", "不对称 slab 不修偶极，两侧真空能级差会污染功函数"),
            ("晶面、层数与固定层", "功函数依赖具体晶面与层数"),
        ],
    },
    "charge": {
        "name": "电荷密度 / Bader / 差分电荷",
        "chain": "relax → static（charge_density）",
        "simple_ok": False,
        "inherent": [],
    },
    "frequency": {
        "name": "有限差分频率 / ZPE",
        "chain": "relax → frequency",
        "simple_ok": False,
        "inherent": [("频率设置", "全体系频率与部分 Hessian 的输入完全不同，且 POTIM 是位移幅度不是时间步")],
    },
    "aimd": {
        "name": "AIMD（固定晶胞 NVT）",
        "chain": "relax（或直接初态）→ aimd",
        "simple_ok": False,
        "inherent": [("AIMD 温度与时长", "温度、时间步和总步数决定成本与能否采样，缺一项都算不出来")],
    },
    "combo": {
        "name": "组合任务（多个目标合并）",
        "chain": "合并共享 SCF，再按目标分叉",
        "simple_ok": False,
        "inherent": [],
    },
}

ALIASES = {
    "relax": "relax", "优化": "relax", "弛豫": "relax", "结构优化": "relax",
    "geometry": "relax", "几何优化": "relax",
    "energy": "energy", "能量": "energy", "能量差": "energy", "形成能": "energy",
    "总能": "energy", "相稳定": "energy", "稳定性": "energy",
    "dos": "dos", "态密度": "dos", "pdos": "dos",
    "band": "band", "能带": "band", "带隙": "band", "gap": "band",
    "workfunction": "workfunction", "功函数": "workfunction", "wf": "workfunction",
    "charge": "charge", "电荷": "charge", "bader": "charge",
    "差分电荷": "charge", "电荷密度": "charge",
    "frequency": "frequency", "频率": "frequency", "zpe": "frequency",
    "振动": "frequency",
    "aimd": "aimd", "分子动力学": "aimd", "md": "aimd", "动力学": "aimd",
    "combo": "combo", "组合": "combo", "多目标": "combo",
}

# ---------------------------------------------------------------- 字段定义
# (分区, 键, 出现在哪些档位, 在哪些档位算必填, 提示语)

FIELDS = [
    ("目标", "科学问题", BOTH, BOTH,
     "一句话说清要回答什么科学问题"),
    ("目标", "目标结果", BOTH, BOTH,
     "多选：平衡结构 / 总能与能量差 / 带隙与能带 / DOS与PDOS / 功函数 / Bader与差分电荷 / 频率与ZPE / AIMD轨迹"),
    ("目标", "精度要求", BOTH, BOTH,
     "定性趋势，或定量数值（如带隙 ±0.05 eV），或与实验/文献可比"),
    ("目标", "对比基准", FULL_ONLY, NONE,
     "实验值 / 文献计算值 / 已有计算结果 / 无"),
    ("目标", "交付物", FULL_ONLY, FULL_ONLY,
     "仅输入文件 / 输入加运行 / 输入加后处理脚本 / 完整报告"),

    ("体系与结构", "体系类型", BOTH, BOTH,
     "体相 3D / 表面 slab / 二维层 / 孤立分子 / 缺陷或掺杂超胞 / 界面"),
    ("体系与结构", "结构来源", BOTH, BOTH,
     "本地 CIF 或 POSCAR / 数据库检索 / 需要建模"),
    ("体系与结构", "元素与原子数", BOTH, NONE,
     "从结构文件读出即可，不要拿这个去问用户"),
    ("体系与结构", "晶胞或超胞", FULL_ONLY, NONE,
     "倍数或目标原子数"),
    ("体系与结构", "晶面、层数与固定层", FULL_ONLY, NONE,
     "如 (001)、4 层、固定底部 2 层"),
    ("体系与结构", "真空层", FULL_ONLY, NONE,
     "厚度（Å）；表面与二维起步 15 Å，二维可到 20 Å"),
    ("体系与结构", "偶极修正", FULL_ONLY, NONE,
     "开 / 关；不对称 slab 建议开"),
    ("体系与结构", "缺陷或掺杂设置", FULL_ONLY, NONE,
     "空位/间隙/取代、具体位点、浓度或超胞大小"),
    ("体系与结构", "吸附物与位点", FULL_ONLY, NONE,
     "分子种类、初始取向、top/bridge/hollow、覆盖度"),
    ("体系与结构", "是否变胞", FULL_ONLY, NONE,
     "固定晶胞 / 只放松原子 / 同时变胞"),

    ("物理模型", "关键物理", BOTH, BOTH,
     "写成 磁性=…；电荷态=…；+U=…；色散=…；SOC=… 五项都要给值"),
    ("物理模型", "初始磁矩", FULL_ONLY, NONE,
     "每个元素的初始磁矩（μB）"),
    ("物理模型", "反铁磁构型", FULL_ONLY, NONE,
     "哪些原子自旋向上、哪些向下"),
    ("物理模型", "U 值与元素", FULL_ONLY, NONE,
     "元素、U 值、作用在 d 还是 f 轨道"),
    ("物理模型", "需要保持的约束", FULL_ONLY, NONE,
     "是否要在优化中保持磁序"),

    ("方法与精度", "泛函", BOTH, BOTH,
     "PBE / PBEsol / 其他 GGA"),
    ("方法与精度", "赝势", FULL_ONLY, FULL_ONLY,
     "PBE 标准赝势 / 用户指定版本"),
    ("方法与精度", "ENCUT", BOTH, BOTH,
     "数值，或 1.3×ENMAX 的具体值"),
    ("方法与精度", "k 网格", BOTH, BOTH,
     "如 6 6 6；表面用 12 12 1（真空方向 1）"),
    ("方法与精度", "展宽", BOTH, BOTH,
     "ISMEAR 与 SIGMA；金属与绝缘体不同"),
    ("方法与精度", "EDIFF", FULL_ONLY, FULL_ONLY,
     "如 1E-6"),
    ("方法与精度", "EDIFFG", FULL_ONLY, FULL_ONLY,
     "如 -0.02（负值是力阈值 eV/Å，正值才是能量阈值）"),
    ("方法与精度", "收敛测试", FULL_ONLY, FULL_ONLY,
     "做不做；做哪几项（ENCUT / k 点 / 真空 / slab 厚度）"),
    ("方法与精度", "固定密度复用", FULL_ONLY, FULL_ONLY,
     "是否需要 ICHARG=11 复用 SCF 电荷密度"),
    ("方法与精度", "能量参考态", FULL_ONLY, NONE,
     "体相 / 干净表面 / 气相分子，含化学势；形成能与吸附能必须有"),

    ("计算链", "阶段链", BOTH, BOTH,
     "如 relax → static → band；只跑目标需要的阶段"),
    ("计算链", "共享 SCF 与分支", FULL_ONLY, NONE,
     "哪些后处理共用同一次 SCF"),
    ("计算链", "AIMD 温度与时长", FULL_ONLY, NONE,
     "目标温度、时间步（fs）、总步数、平衡段与采样段"),
    ("计算链", "频率设置", FULL_ONLY, NONE,
     "全体系 IBRION=6 还是部分 Hessian；是否需要 ZPE"),

    ("资源", "核数", BOTH, BOTH,
     "单作业最多可用核数"),
    ("资源", "单作业时限", BOTH, BOTH,
     "小时数或截止时间"),
    ("资源", "机时预算", FULL_ONLY, NONE,
     "核时或金额；大体系与 AIMD 必填"),
    ("资源", "节点数", FULL_ONLY, FULL_ONLY,
     "本流程按单节点设计；多节点需另行准备"),
    ("资源", "截止日期", FULL_ONLY, NONE,
     "日期；与精度冲突时要先定优先级"),

    ("假设与风险", "采用的默认值", BOTH, BOTH,
     "哪些是建议起点、为什么；不得写成“已收敛”“标准参数”"),
    ("假设与风险", "已知风险", FULL_ONLY, NONE,
     "如结构未验证、磁态可能漂移、参考态选取有争议"),
    ("假设与风险", "需要验证的项目", FULL_ONLY, FULL_ONLY,
     "收敛性、磁态、真空厚度、参考态"),
]

SECTIONS = ["目标", "体系与结构", "物理模型", "方法与精度", "计算链", "资源", "假设与风险"]

KEY_ALIASES = {
    "磁性": "磁性", "自旋": "磁性", "磁态": "磁性",
    "电荷": "电荷态", "电荷态": "电荷态", "带电": "电荷态",
    "u": "+U", "+u": "+U", "加u": "+U", "dft+u": "+U",
    "色散": "色散", "色散修正": "色散", "vdw": "色散",
    "soc": "SOC", "自旋轨道": "SOC", "自旋轨道耦合": "SOC",
}
KEY_PHYSICS = ["磁性", "电荷态", "+U", "色散", "SOC"]

# 关键物理里出现这些词，说明既没给决定、也没明确授权默认
UNDECIDED = {"不确定", "不知道", "你决定", "待定", "随便"}


# ---------------------------------------------------------------- 工具函数

def norm_task(raw):
    t = (raw or "").strip().strip("*`").lower()
    if not t:
        return None
    if t in TASKS:
        return t
    return ALIASES.get(t)


def norm_level(raw):
    v = (raw or "").strip().strip("*`").lower()
    if v in ("simple", "精简", "精简档", "轻", "简单"):
        return SIMPLE
    if v in ("full", "完整", "完整档", "严格"):
        return FULL
    return None


def clean_key(raw):
    k = raw.strip().strip("*`【】[] ")
    k = re.sub(r"[（(]\s*必填\s*[）)]\s*$", "", k).strip()
    return k.strip("*` ")


FIELD_LINE = re.compile(r"^\s*[-*+]\s+(.+?)\s*[：:]\s*(.*)$")


def parse_sheet(path):
    fields = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = FIELD_LINE.match(line.rstrip("\r\n"))
            if not m:
                continue
            key = clean_key(m.group(1))
            if not key or len(key) > 24:
                continue
            val = m.group(2).strip()
            if key not in fields or (not fields[key].strip() and val):
                fields[key] = val
    return fields


def filled(val):
    v = (val or "").strip()
    if not v or v.startswith(PLACEHOLDER):
        return False
    return v not in {"-", "—", "?", "？", "...", "…"}


def parse_key_physics(val):
    got = {}
    for part in re.split(r"[；;]", val or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(.+?)\s*[=＝]\s*(.*)$", part)
        if not m:
            continue
        k = m.group(1).strip().strip("*` ")
        v = m.group(2).strip()
        got[KEY_ALIASES.get(k.lower(), k)] = v
    return got


def has(text, words):
    return any(w in (text or "") for w in words)


# 「体相 3D，非磁、中性、无缺陷」这类写法里，关键词前面挂着否定词。
# 条件项要判的是「有没有这一项」，所以先剥掉「无缺陷」「非掺杂」「不含吸附」再判断，
# 否则一句"无缺陷"就会把体相单质逼去填掺杂位点。
NEGATIONS = ("不存在", "不涉及", "没有", "不含", "无", "非", "未")


def has_pos(text, words):
    """只看肯定出现：把「否定词+关键词」的组合剔掉后再匹配。"""
    t = text or ""
    for w in words:
        for neg in NEGATIONS:
            t = t.replace(neg + w, "")
    return has(t, words)


def filled_of(fields, key):
    """取字段值，但把未填的占位符当成空——否则提示语里的关键词会误触发条件项。"""
    v = fields.get(key, "")
    return v if filled(v) else ""


# ---------------------------------------------------------------- 门禁规则

def required_items(task, level, fields):
    """返回 [(键, 理由, 来源)]，来源用于报告里区分必填/条件/任务固有。"""
    out = []
    seen = set()

    def add(key, why, src):
        if key in seen:
            return
        seen.add(key)
        out.append((key, why, src))

    for _sec, key, _levels, need, hint in FIELDS:
        if level in need:
            add(key, hint, "必填")

    for key, why in TASKS[task].get("inherent", []):
        add(key, why, "本任务固有")

    # 条件项：从已填内容里推出来的
    kp = parse_key_physics(filled_of(fields, "关键物理"))
    mag = kp.get("磁性", "")
    if filled(mag) and not has(mag, ("非磁", "无磁", "nm")):
        add("初始磁矩", "有磁性就必须给初始磁矩，否则收敛到哪个磁态不可控", "条件")
        if has(mag, ("反铁磁", "afm", "AFM")):
            add("反铁磁构型", "AFM 结果对自旋排布敏感，不给构型等于随机取一个", "条件")

    u = kp.get("+U", "")
    if filled(u) and has(u, ("是", "开", "yes", "y")) and not has(u, ("否", "不", "no")):
        add("U 值与元素", "+U 的 U 值必须来自用户指定或明确文献，不能自行给", "条件")

    kind = filled_of(fields, "体系类型")
    goal = filled_of(fields, "目标结果")

    if has_pos(kind, ("表面", "二维", "界面", "slab", "Slab", "薄膜", "层状")):
        add("真空层", "表面与二维必须留足真空，厚度不够结果全是镜像相互作用", "条件")
        add("偶极修正", "不对称 slab 不修偶极，功函数和吸附能都会偏", "条件")
    if has_pos(kind, ("表面", "界面", "slab", "Slab")):
        add("晶面、层数与固定层", "晶面、层数和固定层直接决定模型与成本", "条件")
    if has_pos(kind, ("缺陷", "掺杂", "空位", "取代", "间隙")):
        add("缺陷或掺杂设置", "缺陷/掺杂的位点与浓度决定形成能和电子结构", "条件")
    if has_pos(kind, ("吸附",)) or has_pos(goal, ("吸附",)):
        add("吸附物与位点", "吸附能依赖初始构型与位点，覆盖度决定参考态", "条件")
    if has_pos(goal, ("能量差", "形成能", "吸附能", "结合能", "相稳定", "稳定性")):
        add("能量参考态", "做能量差必须明确参考态与化学势，否则数值不可比", "条件")
    if has_pos(goal, ("AIMD", "aimd", "分子动力学")):
        add("AIMD 温度与时长", "没有温度和总时长就无法设计 MD 任务", "条件")
    if has_pos(goal, ("频率", "ZPE", "振动")):
        add("频率设置", "全体系频率与部分 Hessian 是两套输入，POTIM 含义也不同", "条件")
    if has_pos(goal, ("差分电荷", "差分")):
        add("能量参考态", "差分电荷要各参考态同盒子同网格，参考态必须先定", "条件")

    return out


USER_CONFIRM_OK = ("确认", "可以", "同意", "按这个生成", "没问题", "ok", "OK", "Ok",
                   "就这样", "没错", "行")
USER_CONFIRM_NO = ("不确认", "先不", "暂不", "等等", "再改")

APPROVAL_SUFFIX = ".approval.json"

# 「用户确认」这一行是授权的记录位，不是需求内容 —— 计算指纹时必须排除，
# 否则 --approve 回写确认时间会让指纹自己失效。
APPROVE_LINE = re.compile(
    r"^\s*[-*+]\s*用户确认(?:\s*[（(]\s*必填\s*[）)])?\s*[：:]")


def approval_path(sheet):
    return os.path.splitext(sheet)[0] + APPROVAL_SUFFIX


def sheet_fingerprint(sheet):
    """确认单实质内容的 sha256。改一个字就变，用来让旧授权自动失效。"""
    with open(sheet, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = [ln.rstrip() for ln in lines if not APPROVE_LINE.match(ln)]
    return hashlib.sha256("\n".join(kept).strip().encode("utf-8")).hexdigest()


def load_approval(sheet):
    p = approval_path(sheet)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"_broken": True}
    if not isinstance(data, dict):
        return {"_broken": True}
    return data


def approval_state(sheet):
    """-> (状态, 凭据)。状态：ok / missing / broken / stale。"""
    ap = load_approval(sheet)
    if ap is None:
        return "missing", None
    if ap.get("_broken"):
        return "broken", None
    if ap.get("fingerprint") != sheet_fingerprint(sheet):
        return "stale", ap
    return "ok", ap


def write_approval(sheet, by_user):
    now = datetime.datetime.now().astimezone()
    data = {
        "sheet": os.path.basename(sheet),
        "fingerprint": sheet_fingerprint(sheet),
        "confirmed_at": now.isoformat(timespec="seconds"),
        "user_words": by_user.strip(),
        "tool": "intake.py",
    }
    p = approval_path(sheet)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return p, data


def writeback_confirm(sheet, by_user):
    """把授权回写进确认单的「用户确认」一行，让单子自己就带着痕迹。"""
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    words = by_user.strip().replace('"', "”").replace("\n", " ")
    new_line = "- 用户确认（必填）：确认生成（%s，用户原话：“%s”）" % (stamp, words)
    with open(sheet, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().replace("\r\n", "\n").replace("\r", "\n").split("\n")
    hit = False
    for i, ln in enumerate(lines):
        if APPROVE_LINE.match(ln):
            lines[i] = new_line
            hit = True
            break
    if not hit:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append("")
        lines.append(new_line)
    with open(sheet, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    return stamp


# 这些条件项一旦触发，就说明物理模型不平凡：磁性、带电、缺陷掺杂、吸附、表面/二维。
# 即使有人手工把对应栏位补进精简档的单子里，档位也得改成完整档 —— 否则会顺带绕过
# 完整档才有的「收敛测试」「需要验证的项目」这两道验收要求。
# 注意「能量参考态」不在其中：体相能量差本来就是精简档的合法任务。
ESCALATE_KEYS = ("初始磁矩", "反铁磁构型", "U 值与元素", "真空层", "偶极修正",
                 "晶面、层数与固定层", "缺陷或掺杂设置", "吸附物与位点")


def collect_results(fields, task, level):
    """内容层面的门禁项（不含用户授权）。[(状态, 键, 原因, 来源)]"""
    results = []

    # 关键物理单独查：五项都要有值，且不能停在“不确定”
    kp_raw = fields.get("关键物理", "")
    kp = parse_key_physics(kp_raw)
    if not filled(kp_raw):
        results.append(("未填", "关键物理",
                        "磁性/电荷态/+U/色散/SOC 这五项会直接改变计算设置，不能静默默认", "必填"))
    else:
        missing = [k for k in KEY_PHYSICS if not filled(kp.get(k, ""))]
        undecided = [k for k in KEY_PHYSICS
                     if filled(kp.get(k, "")) and kp[k].strip() in UNDECIDED]
        if missing:
            results.append(("未填", "关键物理",
                            "缺 %s；这五项每一项都会改变计算设置" % "、".join(missing), "必填"))
        elif undecided:
            results.append(("未定", "关键物理",
                            "%s 写成了“不确定”。不确定时要给推荐方案并让用户点头，"
                            "不能把不确定带进生成阶段" % "、".join(undecided), "必填"))
        else:
            results.append(("PASS", "关键物理", "", "必填"))

    for key, why, src in required_items(task, level, fields):
        if key == "关键物理":
            continue
        if key not in fields:
            results.append(("缺栏位", key, why, src))
            continue
        if not filled(fields[key]):
            results.append(("未填", key, why, src))
        else:
            results.append(("PASS", key, "", src))

    # 档位自降：条件项触发了，却还挂在精简档上
    if level == SIMPLE:
        hit = [k for k in ESCALATE_KEYS
               if any(r[1] == k and r[3] == "条件" for r in results)]
        if hit:
            results.insert(0, ("档位不符", "门禁档位",
                               "体系或目标触发了 %s —— 物理模型不平凡，按分级规则应使用完整档。"
                               "精简档缺了「收敛测试」「需要验证的项目」这些验收栏位，"
                               "补上几个字段也补不回这些要求" % "、".join(hit), "档位"))

    return results


def collect_warns(fields, task, level, chain):
    """不阻断、但必须说出来的提醒。"""
    warns = []
    defaults = filled_of(fields, "采用的默认值")
    for bad in ("已收敛", "已验算", "标准答案", "标准参数", "无需验证"):
        if bad in defaults:
            warns.append("「采用的默认值」里出现了“%s”。默认值只能写成“建议起点，"
                         "需要按体系验证”，不能宣称已收敛。" % bad)
    if task in ("band",) and not has(chain, ("band", "能带")):
        warns.append("任务是能带，但阶段链里没看到 band 阶段。")
    if task in ("dos",) and not has(chain, ("dos", "DOS", "态密度")):
        warns.append("任务是 DOS，但阶段链里没看到 dos 阶段。")
    if task == "aimd" and not has(chain, ("aimd", "AIMD", "md")):
        warns.append("任务是 AIMD，但阶段链里没看到 aimd 阶段。")
    if level == SIMPLE and not TASKS[task]["simple_ok"]:
        warns.append("该任务类型不属于精简档适用范围，建议改用 --level full 重新生成。")
    return warns


def approval_result(sheet):
    """用户二次确认的凭据检查 —— 与内容检查分开，因为它管的是「授权」不是「填没填」。"""
    state, ap = approval_state(sheet)
    if state == "missing":
        return ("未授权", "用户二次确认",
                "确认单还没交给用户过目并取得同意。内容齐全不等于用户同意生成 —— "
                "先 --preview 出确认稿，用户明确点头后再 --approve", "授权")
    if state == "broken":
        return ("授权无效", "用户二次确认",
                "凭据文件读不出来（可能被截断或手改坏）。删掉它重新 --approve", "授权")
    if state == "stale":
        return ("授权过期", "用户二次确认",
                "确认单在用户确认之后又被改过，之前那次同意已经不作数。"
                "重新 --preview 给用户看改动后的版本，再 --approve", "授权")
    return ("PASS", "用户二次确认", "", "授权")


def check(path, content_only=False):
    fields = parse_sheet(path)

    task = norm_task(fields.get("任务类型", ""))
    if not task:
        print("=" * 66)
        print("需求确认单检查：%s" % path)
        print("=" * 66)
        print()
        print("[缺栏位] 任务类型")
        print("   确认单没有可识别的「任务类型」，无法判断该查哪些必填项。")
        print("   怎么补：在「## 0. 元信息」下写一行「- 任务类型：energy」")
        print("   可选值：%s" % " / ".join(TASKS.keys()))
        print()
        print("门禁结论：阻断")
        return 3

    level = norm_level(fields.get("门禁档位", "")) or FULL
    task_name = TASKS[task]["name"]
    chain = filled_of(fields, "阶段链") or TASKS[task]["chain"]

    results = collect_results(fields, task, level)
    warns = collect_warns(fields, task, level, chain)

    # 用户二次确认：查的是凭据，不看确认单里那一行文字（那行由 --approve 回写）
    if not content_only:
        results.append(approval_result(path))

    # 报告
    n_pass = sum(1 for r in results if r[0] == "PASS")
    bad = [r for r in results if r[0] != "PASS"]

    print("=" * 66)
    print("需求确认单检查%s" % ("（只查内容，未查用户确认）" if content_only else ""))
    print("文件：%s" % path)
    print("任务类型：%s（%s）" % (task, task_name))
    print("门禁档位：%s　　阶段链：%s" % (level, chain))
    print("=" * 66)
    print()

    for status, key, why, src in results:
        if status == "PASS":
            continue
        tag = {"未填": "未填", "缺栏位": "缺栏位", "未定": "未定", "档位不符": "档位不符",
               "未授权": "未授权", "授权过期": "授权过期",
               "授权无效": "授权无效"}[status]
        print("[%s] %s　（%s）" % (tag, key, src))
        if why:
            print("       为什么：%s" % why)
        if status == "档位不符":
            print("       怎么改：把「- 门禁档位：」改成 full，再补齐完整档的栏位 ——")
            print("               python intake.py --new %s --level full -o <新文件>" % task)
        if status == "缺栏位":
            print("       怎么补：在确认单里加一行「- %s：」再填值" % key)
            if src == "条件" and level == SIMPLE:
                print("       或者：这一项属于完整档，直接重新生成更省事 ——")
                print("             python intake.py --new %s --level full" % task)
        if status in ("未授权", "授权过期", "授权无效"):
            print("       怎么做：python intake.py --preview %s" % path)
            print("               用户看过后：python intake.py --approve %s --by-user \"用户原话\"" % path)
        print()

    print("-" * 66)
    if not bad:
        print("门禁结论：通过（%d/%d 项检查通过）" % (n_pass, len(results)))
        if content_only:
            print("内容齐全。下一步：python intake.py --preview %s" % path)
            print("把确认稿交给用户核对，取得同意后再 --approve。")
        else:
            _st, _ap = approval_state(path)
            if _st == "ok":
                print("用户确认：%s　原话：“%s”"
                      % (_ap.get("confirmed_at", ""), _ap.get("user_words", "")))
            print("内容齐全、用户已确认，可以进入生成阶段（能力 2）。")
            print("注意：生成输入文件 ≠ 提交作业，提交要另行授权。")
    else:
        n_empty = sum(1 for r in bad if r[0] == "未填")
        n_absent = sum(1 for r in bad if r[0] == "缺栏位")
        n_undecided = sum(1 for r in bad if r[0] == "未定")
        n_unauth = sum(1 for r in bad if r[0] in ("未授权", "授权过期", "授权无效"))
        n_level = sum(1 for r in bad if r[0] == "档位不符")
        parts = []
        if n_level:
            parts.append("档位与体系不匹配")
        if n_empty:
            parts.append("%d 项未填" % n_empty)
        if n_absent:
            parts.append("%d 项缺栏位" % n_absent)
        if n_undecided:
            parts.append("%d 项未定" % n_undecided)
        if n_unauth:
            parts.append("用户未确认或确认已失效")
        print("门禁结论：阻断（%s）" % "，".join(parts))
        if n_level:
            print("禁止生成输入文件。把档位改成完整档并重新采集，别用精简档硬算。")
        if n_empty or n_absent or n_undecided:
            print("禁止生成输入文件。先补齐上面的项，再重跑 --check。")
        if n_unauth:
            print("另外：内容齐全也必须先把确认稿给用户过目并取得同意 —— 见上面的命令。")

    if warns:
        print()
        print("提醒（不阻断）：")
        for w in warns:
            print("  · %s" % w)

    return 0 if not bad else 3


# ---------------------------------------------------------------- 确认稿与授权

PREVIEW_SUFFIX = ".preview.json"
SEP = "=" * 66
THIN = "-" * 66


def preview_path(sheet):
    return os.path.splitext(sheet)[0] + PREVIEW_SUFFIX


def write_preview_record(sheet):
    now = datetime.datetime.now().astimezone()
    with open(preview_path(sheet), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({
            "sheet": os.path.basename(sheet),
            "fingerprint": sheet_fingerprint(sheet),
            "shown_at": now.isoformat(timespec="seconds"),
        }, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def preview(sheet):
    """渲染成给人看的确认稿。只有内容齐全才留展示记录，残缺单子不给用户看。"""
    fields = parse_sheet(sheet)
    task = norm_task(fields.get("任务类型", ""))
    if not task:
        print("确认单没有可识别的「任务类型」，无法预览。")
        print("  在「## 0. 元信息」下写一行「- 任务类型：energy」，可选值：%s"
              % " / ".join(TASKS.keys()))
        return 2

    level = norm_level(fields.get("门禁档位", "")) or FULL
    chain = filled_of(fields, "阶段链") or TASKS[task]["chain"]
    results = collect_results(fields, task, level)
    bad = [r for r in results if r[0] != "PASS"]
    fp = sheet_fingerprint(sheet)

    print(SEP)
    print("　VASP 输入需求确认单（生成前请核对）")
    print(SEP)
    print()

    if bad:
        print("[注意] 这份确认单还有 %d 项没定，现在不能拿去让用户确认生成。" % len(bad))
        for st, key, _why, _src in bad:
            print("   · %s（%s）" % (key, st))
        print("  先问清这几项，再重新 --preview。下面只展示已经定了的部分。")
        print()

    print("任务：%s（%s）" % (task, TASKS[task]["name"]))
    print("档位：%s　　阶段链：%s" % (level, chain))
    print()

    # 「关键物理」拆成五个键值来显示，比挤在一行好核对
    kp = parse_key_physics(filled_of(fields, "关键物理"))
    kp_shown = bool(kp) and all(filled(kp.get(k, "")) for k in KEY_PHYSICS)

    for sec in SECTIONS:
        if sec == "物理模型" and kp_shown:
            print("【%s】" % sec)
            for k in KEY_PHYSICS:
                print("  %s：%s" % (k, kp[k]))
            for _s, key, _lv, _need, _hint in FIELDS:
                if _s != sec or key == "关键物理":
                    continue
                v = filled_of(fields, key)
                if v:
                    print("  %s：%s" % (key, v))
            print()
            continue

        rows = []
        for _s, key, _lv, _need, _hint in FIELDS:
            if _s != sec:
                continue
            v = filled_of(fields, key)
            if v:
                rows.append((key, v))
        if not rows:
            continue
        print("【%s】" % sec)
        for key, v in rows:
            print("  %s：%s" % (key, v))
        print()

    print(THIN)
    print("生成范围：POSCAR / INCAR / KPOINTS / POTCAR / 提交脚本")
    print("不做的事：提交作业（需另行授权）、POTCAR 内容（受许可限制，不在仓库分发）")
    print("确认单指纹：%s" % fp[:12])
    print(THIN)

    if bad:
        print()
        print("补齐缺失项后重新 --preview，再把这一页交给用户。")
        return 3

    write_preview_record(sheet)
    print()
    print("把以上内容原样交给用户核对。用户明确表示同意后，执行：")
    print("  python intake.py --approve %s --by-user \"用户的原话\"" % sheet)
    print("用户要改任何一项，就先改确认单、再重新 --preview（旧授权会自动失效）。")
    return 0


def approve(sheet, by_user):
    """记录用户同意。要求内容齐全 + 确认稿已生成且与当前内容一致。"""
    fields = parse_sheet(sheet)
    task = norm_task(fields.get("任务类型", ""))
    if not task:
        print("确认单没有可识别的「任务类型」，不能记录授权。")
        return 2

    level = norm_level(fields.get("门禁档位", "")) or FULL
    results = collect_results(fields, task, level)
    bad = [r for r in results if r[0] != "PASS"]
    if bad:
        print("内容检查还没过，不能记录用户授权。还差 %d 项：" % len(bad))
        for st, key, _why, _src in bad:
            print("  [%s] %s" % (st, key))
        print()
        print("先补齐并 --check 通过，再 --preview 给用户看，最后才 --approve。")
        return 3

    words = (by_user or "").strip()
    if not words:
        print("必须用 --by-user 记录用户的原话。空值不能当授权凭据 ——")
        print("没有原话，事后无法判断用户到底同意了哪个版本。")
        return 2
    if any(w in words for w in USER_CONFIRM_NO):
        print("用户原话「%s」是否定或暂缓，不能记为授权。" % words)
        return 3
    if not any(w in words for w in USER_CONFIRM_OK):
        print("用户原话「%s」里没有明确的同意表述。" % words)
        print("一般性讨论、“嗯”“知道了”“你看着办”都不构成授权。")
        print("请让用户明确回复“确认生成”“可以，就这样”这类话，再带上原话重新 --approve。")
        return 3

    # 必须先在当前内容上生成过确认稿 —— 否则等于没给用户看就替他签了字
    pk = preview_path(sheet)
    if not os.path.isfile(pk):
        print("还没有给用户看过确认稿，不能记录授权。先执行：")
        print("  python intake.py --preview %s" % sheet)
        return 3
    try:
        with open(pk, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        rec = {}
    if rec.get("fingerprint") != sheet_fingerprint(sheet):
        print("确认稿生成之后，确认单又被改过 —— 用户看到的是旧版本。")
        print("重新 --preview 出新确认稿，请用户看过后再 --approve。")
        return 3

    p, data = write_approval(sheet, words)
    stamp = writeback_confirm(sheet, words)

    print(SEP)
    print("已记录用户确认")
    print(SEP)
    print("确认单：%s" % sheet)
    print("时间　：%s" % stamp)
    print("原话　：“%s”" % words)
    print("指纹　：%s（绑定当前内容）" % data["fingerprint"][:12])
    print("凭据　：%s" % p)
    print()
    print("现在可以跑最后一次门禁：")
    print("  python intake.py --check %s" % sheet)
    print("通过后即可生成输入文件。确认单之后再改动任何内容，这份授权都会失效。")
    return 0


# ---------------------------------------------------------------- 生成骨架

def build_sheet(task, level):
    t = TASKS[task]
    lines = []
    lines.append("# VASP 输入需求确认单")
    lines.append("")
    lines.append("> 填写规则：每条写成「- 键：值」，值写在冒号后面。")
    lines.append("> 想不出来的项写「不确定」并说明原因，不要留 `待填` —— 留着会被门禁拦下。")
    lines.append("> 本单只覆盖「需求采集 → 生成输入文件」，不含作业提交；提交需另行授权。")
    lines.append("")
    lines.append("## 0. 元信息")
    lines.append("")
    lines.append("- 任务类型：%s" % task)
    lines.append("- 门禁档位：%s" % level)
    lines.append("- 阶段链建议：%s" % t["chain"])
    lines.append("")

    n = 0
    for sec in SECTIONS:
        rows = [f for f in FIELDS if f[0] == sec and level in f[2]]
        if not rows:
            continue
        n += 1
        lines.append("## %d. %s" % (n, sec))
        lines.append("")
        for _s, key, _lv, need, hint in rows:
            mark = "（必填）" if level in need else ""
            lines.append("- %s%s：%s —— %s" % (key, mark, PLACEHOLDER, hint))
        lines.append("")

    # 本任务固有、但档位模板里没排的项（如精简档做能量差，仍然必须给参考态）
    present = {f[1] for f in FIELDS if level in f[2]}
    extra = [(k, w) for k, w, _src in required_items(task, level, {})
             if k not in present and k != "关键物理"]
    if extra:
        n += 1
        lines.append("## %d. 按本任务补充" % n)
        lines.append("")
        for key, why in extra:
            lines.append("- %s（必填）：%s —— %s" % (key, PLACEHOLDER, why))
        lines.append("")

    n += 1
    lines.append("## %d. 确认" % n)
    lines.append("")
    lines.append("- 生成范围：POSCAR、INCAR、KPOINTS、POTCAR、提交脚本")
    lines.append("- 不生成或不执行：作业提交（需另行授权）、POTCAR 内容（受许可限制，不在仓库分发）")
    lines.append("- 用户确认：%s —— 不要手填，由 `intake.py --approve` 记录" % PLACEHOLDER)
    lines.append("")

    req = [k for k, _w, _s in required_items(task, level, {})]
    lines.append("---")
    lines.append("")
    lines.append("本档位必填项：%s。" % "、".join(req))
    lines.append("条件项（体系类型或目标结果触发时才填）：初始磁矩、反铁磁构型、U 值与元素、"
                 "晶面层数固定层、真空层、偶极修正、缺陷或掺杂设置、吸附物与位点、"
                 "能量参考态、AIMD 温度与时长、频率设置。")
    lines.append("")
    return "\n".join(lines)


def new_sheet(task, level, out):
    t = TASKS[task]
    if level == SIMPLE and not t["simple_ok"]:
        print("任务类型 %s（%s）不属于精简档适用范围，已自动改用完整档。" % (task, t["name"]))
        level = FULL
    text = build_sheet(task, level)
    if out == "-":
        sys.stdout.write(text)
        return 0
    if os.path.exists(out):
        print("目标文件已存在，未覆盖：%s" % out)
        print("  另存为别的名字，或先确认可以覆盖再手动删除。")
        return 2
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("已生成需求确认单：%s" % out)
    print("  任务类型：%s（%s）" % (task, t["name"]))
    print("  门禁档位：%s" % level)
    print("  阶段链建议：%s" % t["chain"])
    print()
    print("下一步：按对话内容逐项填值，然后跑")
    print("  python intake.py --check %s" % out)
    print("门禁通过后才生成输入文件。")
    return 0


# ---------------------------------------------------------------- CLI

def list_tasks():
    print("支持的任务类型（--new 后面用左边这个短名）：")
    print()
    print("  %-14s %-28s %-24s %s" % ("短名", "任务", "建议阶段链", "精简档"))
    print("  " + "-" * 78)
    for key, t in TASKS.items():
        print("  %-14s %-28s %-24s %s" % (
            key, t["name"], t["chain"], "可用" if t["simple_ok"] else "只能完整档"))
    print()
    print("档位怎么选：")
    print("  精简档 —— 单点、能量、DOS、能带这类参数高度标准化、又不涉及磁性/电荷/缺陷的")
    print("  完整档 —— 磁性体系、带电、缺陷掺杂、吸附、功函数、频率、AIMD、投稿级定量对比")
    print("  拿不准就用完整档：多问三句的代价，远小于生成一份错的输入。")
    return 0


def main():
    ap = argparse.ArgumentParser(
        prog="intake.py",
        description="VASP 输入需求确认单：生成骨架、门禁校验、生成前二次确认",
        epilog="退出码：0 通过 / 2 用法或文件错误 / 3 门禁阻断",
    )
    ap.add_argument("--list", action="store_true", help="列出支持的任务类型与档位说明")
    ap.add_argument("--new", metavar="任务类型", help="生成确认单骨架")
    ap.add_argument("--level", metavar="simple|full",
                    help="门禁档位；不给则按任务类型自动选")
    ap.add_argument("-o", "--out", metavar="文件", default="intake.md",
                    help="确认单输出路径（默认 intake.md；用 - 输出到屏幕）")
    ap.add_argument("--check", metavar="文件", help="检查已填的确认单（内容 + 用户确认凭据）")
    ap.add_argument("--content-only", action="store_true",
                    help="只查内容完整性，不查用户是否确认（采集中反复自检用）")
    ap.add_argument("--preview", metavar="文件", help="渲染给人看的确认稿，交给用户过目")
    ap.add_argument("--approve", metavar="文件", help="记录用户对确认稿的同意")
    ap.add_argument("--by-user", metavar="原话",
                    help="用户同意时的原话，与 --approve 配合，必须含明确同意表述")
    args = ap.parse_args()

    if args.list:
        return list_tasks()

    modes = [m for m in (args.new, args.check, args.preview, args.approve) if m]
    if len(modes) > 1:
        print("--new / --check / --preview / --approve 不能同时用，一次只做一件事。")
        return 2

    if args.by_user and not args.approve:
        print("--by-user 只能和 --approve 一起用。")
        return 2

    if args.new:
        task = norm_task(args.new)
        if not task:
            print("不认识的任务类型：%s" % args.new)
            print("可选：%s" % " / ".join(TASKS.keys()))
            return 2
        level = norm_level(args.level) if args.level else None
        if args.level and not level:
            print("--level 只能是 simple 或 full，收到：%s" % args.level)
            return 2
        if not level:
            level = SIMPLE if TASKS[task]["simple_ok"] else FULL
        return new_sheet(task, level, args.out)

    target = args.check or args.preview or args.approve
    if target:
        if not os.path.isfile(target):
            print("读不到确认单：%s" % target)
            return 2
        try:
            if args.check:
                return check(target, content_only=args.content_only)
            if args.preview:
                return preview(target)
            return approve(target, args.by_user)
        except OSError as e:
            print("读写失败：%s" % e)
            return 2

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
