#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_libs.py — 可选第三方库检测工具（内部模块）

作用：统一检测 ASE / pymatgen 是否可用，供各脚本做「可选增强 + 降级」判断。

设计原则：
- 这两个库是「可选增强」而非「必装依赖」。装了就用它们的增强功能，
  没装就退回本 Skill 自带的零依赖实现（纯标准库），保证小白无需装任何东西也能用。
- 检测结果只用于「是否启用增强分支」，不影响核心功能。

用法：
    from _libs import HAS_ASE, HAS_PYMATGEN, get_ase, get_pymatgen

    if HAS_ASE:
        from ase.build import bulk
        ...
    else:
        # 降级到零依赖实现
        ...
"""

# 尝试导入 ASE
try:
    import ase  # noqa: F401
    HAS_ASE = True
    ASE_VERSION = getattr(ase, "__version__", "unknown")
except ImportError:
    HAS_ASE = False
    ASE_VERSION = None

# 尝试导入 pymatgen
try:
    import pymatgen  # noqa: F401
    HAS_PYMATGEN = True
    PYMATGEN_VERSION = getattr(pymatgen, "__version__", "unknown")
except ImportError:
    HAS_PYMATGEN = False
    PYMATGEN_VERSION = None


def get_ase():
    """返回 ase 模块，未安装时返回 None。"""
    if HAS_ASE:
        import ase
        return ase
    return None


def get_pymatgen():
    """返回 pymatgen 模块，未安装时返回 None。"""
    if HAS_PYMATGEN:
        import pymatgen
        return pymatgen
    return None


def report():
    """打印可选库的可用性报告。"""
    lines = []
    lines.append("可选增强库检测：")
    if HAS_ASE:
        lines.append(f"  ASE      已安装 (v{ASE_VERSION})，启用结构构建/结果解析增强")
    else:
        lines.append("  ASE      未安装，使用零依赖降级实现（结构构建需手写晶格/坐标）")
    if HAS_PYMATGEN:
        lines.append(f"  pymatgen 已安装 (v{PYMATGEN_VERSION})，启用结构校验/结果分析增强")
    else:
        lines.append("  pymatgen 未安装，使用零依赖降级实现（校验/分析用内置正则解析）")
    lines.append("  安装命令：pip install ase pymatgen")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
