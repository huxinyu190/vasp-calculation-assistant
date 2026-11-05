# 可选增强库说明（ASE / pymatgen）

> 本 Skill 的核心脚本采用**零依赖设计**（只用 Python 标准库），保证小白机器上什么都不装也能用。同时引入了两个**可选增强库**——**ASE** 和 **pymatgen**，装了它们功能更强、结果更可靠；没装则自动退回零依赖实现。这叫「可选增强 + 优雅降级」。

---

## 一、为什么引入这两个库

手写正则解析 VASP 文件（尤其 OUTCAR、vasprun.xml、复杂 POSCAR）既脆弱又容易漏字段。ASE 和 pymatgen 是计算材料科学领域**最成熟、被数千研究者日复一日使用**的库，由它们来建结构、读文件、做分析，比手写可靠得多。

| 库 | 定位 | 它帮本 Skill 优化什么 |
|----|------|---------------------|
| **ASE**（Atomic Simulation Environment） | 通用模拟环境（建结构 + 跑计算 + 解析） | 一键构建常见晶体/表面/石墨烯，标准写出 POSCAR，读 vasprun.xml 拿能量/力 |
| **pymatgen**（Python Materials Genomics） | 材料分析工具包（Materials Project 引擎） | 结构合法性校验、vasprun.xml 深度解析、DOS/能带/电子结构分析 |

**两者定位不同、互补**：ASE 擅长"驱动计算"，pymatgen 擅长"读懂结果"。业界标准做法是混用。

---

## 二、安装（可选）

```bash
pip install ase pymatgen
```

- ASE：LGPL-2.1+ 许可证（可闭源衍生，放心用）
- pymatgen：MIT 许可证

**不装也没关系**：本 Skill 会自动降级，核心功能（生成、校验、估算、监控、分析）全部仍可用，只是少了"一键建结构""读 vasprun.xml 拿全量数据""结构合法性校验"这几个增强能力。

---

## 三、各脚本的「增强 + 降级」对照

| 脚本 | 有 ASE/pymatgen 时（增强） | 没装时（降级） |
|------|--------------------------|--------------|
| `generate_inputs.py` | `--ase` 一键建 bulk/fcc111/fcc100/bcc111/graphene，标准写 POSCAR | 手写 `--lattice/--positions`（原方式） |
| `validate_inputs.py` | 额外用 pymatgen `Structure.from_file` 做结构合法性 + 原子重叠检查 | 只用内置正则校验 |
| `analyze_results.py` | `--vasprun` 用 pymatgen `Vasprun`/ASE 读全量能量、力、带隙、磁矩 | 只用正则解析 OUTCAR/OSZICAR |
| `monitor_to_csv.py` | （保持现状，正则解析 OSZICAR 已足够可靠） | 同左 |
| `estimate_time.py` | （保持现状，纯数学估算，无需库） | 同左 |

---

## 四、检测方式

所有脚本通过内部工具模块 `scripts/_libs.py` 统一检测库是否可用：

```python
from _libs import HAS_ASE, HAS_PYMATGEN
```

脚本启动时会静默判断，**绝不会因为没装库而报错或中断**。

---

## 五、常见坑（引入依赖时要注意）

1. **版本冲突**：ASE 和 pymatgen 都依赖 numpy，且各自对 numpy 版本有要求。若超算环境 numpy 版本老旧，可能装不上。解决：用 `conda create -n vasp-env python=3.10 ase pymatgen` 建独立环境，或 `pip install "numpy<2"` 后再装。
2. **Windows 上安装**：pymatgen 依赖的某些编译包在 Windows 上偶尔装不上，此时可用 conda-forge 渠道安装更稳。
3. **不强制用户装**：本 Skill 的定位是"小白友好"，所以把这两个库设为**可选**而非必装，正是为了避免把安装门槛强加给用户。

---

## 六、何时建议装

- 你要**频繁建结构**（表面、超胞、吸附、异质结）→ 强烈建议装 ASE，`bulk()`/`fcc111()` 能省掉大量手写坐标的出错机会。
- 你要**深度分析结果**（DOS、能带、磁矩、应力、电子结构）→ 建议装 pymatgen，`Vasprun` 能一次性读出几乎所有物理量。
- 你只是偶尔跑个简单结构优化 → 零依赖就够，不必装。
