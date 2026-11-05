# vasp-calculation-assistant 使用手册

> 一个面向 **VASP / DFT 新手** 的端到端计算助手 Skill。你不需要懂 VASP 理论，也能得到正确、符合物理规律的计算结果。

## 这个 Skill 是什么

它把一次完整的 VASP 计算，拆成 9 个可以**单独用、也可以组合用**的能力，并且能**自动从你说的话里判断**该用哪个：

| 编号 | 能力 | 你什么时候用它 |
|------|------|--------------|
| 1 | 资源调度 | "我机器 64G 内存 16 核，怎么设并行参数？" |
| 2 | 输入文件生成 + 严格校验 | "帮我写 INCAR/POSCAR" / "检查输入文件有没有错" |
| 3 | 耗时估算 | "这个要跑多久？" |
| 4 | 预计算试算 | "先试算一下看结果大概什么样" |
| 5 | 运行日志解析 → CSV | "计算跑完了，把过程数据存成 CSV" |
| 6 | 结果分析 + 报告 | "结果对不对？给我出份报告" |
| 7 | 智能路由 | 自动识别你上面到底要哪个/哪几个 |
| 8 | 环境与性能测试 | "VASP 能不能用？这机器跑得快不快？" |
| 9 | 实时监控（看门狗） | "边跑边盯着，出问题自动停下来，别白烧机时" |

## 快速开始

### 1. 安装这个 Skill

把整个 `vasp-calculation-assistant` 文件夹复制到你的用户级 Skill 目录：

```
~/.workbuddy/skills/
```

（Windows 下通常是 `C:\Users\<你的用户名>\.workbuddy\skills\`）

复制完成后，重启 WorkBuddy（或刷新 Skill 列表），就能在对话中使用了。

### 2. 说一句大白话

你不需要记任何命令，直接说人话即可。例如：

- "帮我算单质硅的晶格常数，我机器 16 核 64G 内存"
- "检查一下这个 INCAR 有没有问题"
- "这个 100 原子的体系算结构优化大概要多久"
- "先试算一下，看看这个吸附结构稳不稳定"
- "算完了，帮我分析结果符不符合物理规律，出份报告"

Agent 会自动判断调用哪个能力。你可以**只问其中一件事**，也可以**一次说完整个流程**。

### 3. 提供必要信息

为了让 Agent 帮你做对，尽量提供：

- **硬件**：内存大小、CPU 型号、CPU 数量、每颗 CPU 核心数
- **体系**：是什么材料、元素种类、大概原子数
- **目的**：你想算什么（晶格常数 / 带隙 / 磁矩 / 吸附能 / DOS / 能带……）
- **结构**：如果有现成的 POSCAR / CIF，直接提供

### 4. 按 Agent 指引执行

这个 Skill 是**"生成 + 指导"型**的：它帮你写好文件、检查文件、估算时间、分析结果，但**真正运行 VASP 由你自己在机器上执行**。Agent 会告诉你每一步该跑什么命令。

## 目录结构

```
vasp-calculation-assistant/
├── SKILL.md                        # 主入口（能力说明、路由规则、铁律）
├── README.md                       # 本文件（使用手册）
├── references/                     # 参考文档（Agent 执行时查阅）
│   ├── vasp-basics.md              # VASP 理论速查（写给小白）
│   ├── input-validation.md         # 输入文件校验清单（核心）
│   ├── online-resources.md         # 联网查证网址清单（结构/参数/赝势/文献）
│   ├── resource-scheduling.md      # 资源调度规则
│   ├── time-estimation.md          # 耗时估算规则
│   ├── pre-calculation.md          # 预计算策略
│   ├── environment-test.md         # 环境与性能测试规则
│   ├── realtime-monitoring.md      # 实时监控（看门狗）规则与阈值表
│   ├── external-libs.md            # 可选增强库（ASE/pymatgen）说明
│   └── result-analysis.md          # 结果分析与报告规则
├── scripts/                        # 可执行脚本（Agent 会调用）
│   ├── _libs.py                    # 可选库检测（内部工具，供各脚本复用）
│   ├── generate_inputs.py          # 生成输入文件（支持 --ase 一键建结构）
│   ├── validate_inputs.py          # 校验输入文件（支持 pymatgen 增强校验）
│   ├── estimate_time.py            # 估算耗时
│   ├── monitor_to_csv.py           # 日志 → CSV（一次性解析）
│   ├── watchdog.py                 # 看门狗：托管运行 + 每 20 步体检 + 停机 + 重启
│   ├── test_environment.py         # 环境与性能测试
│   └── analyze_results.py          # 结果分析（支持 vasprun.xml 增强解析）
├── examples/                       # 详细使用案例
│   ├── example_si_relaxation.md    # 案例1：Si 晶格常数（完整流程）
│   ├── example_graphene_dos.md     # 案例2：石墨烯 DOS（单项能力）
│   ├── example_multi_capability.md # 案例3：Fe 磁矩（多能力+路由）
│   └── example_nidoped_wo3_gas_sensing.md  # 案例4：Ni掺杂WO3气敏（真实科研，最详细）
├── tests/                          # VASP 计算验证测试套件
│   └── input_files/                # 8 套最小体系测试输入文件（附公开参考值）
│       ├── 01_si_single_point/     # Si 单点能
│       ├── 02_si_relax/            # Si 结构优化
│       ├── 03_fe_magnetic/         # bcc Fe 磁性
│       ├── 04_ni_magnetic/         # fcc Ni 磁性
│       ├── 05_mgo_relax/           # MgO 结构优化
│       ├── 06_nacl_relax/          # NaCl 结构优化
│       ├── 07_dos/                 # Si DOS
│       ├── 08_bands/               # Si 能带
│       └── README.md               # 测试套件使用说明（含参考值与判读方法）
└── CHANGELOG.md                    # 版本记录
```

## 脚本用法速查

这些脚本你也可以手动运行（Agent 会替你调用，但了解一下没坏处）：

```bash
# 环境与性能测试（正式算之前先跑，确认 VASP 能用 + 实测机器性能）
python scripts/test_environment.py --vasp vasp_std --cores 64
python scripts/test_environment.py --parse test_env/OUTCAR   # 解析结果、提取单步耗时

# 校验输入文件（最重要，改过文件必跑）
python scripts/validate_inputs.py <计算目录>

# 估算耗时（可用上面的实测单步耗时做校准）
python scripts/estimate_time.py --natoms 100 --nkpoints 64 --encut 520 --task relax --cores 32 \
    --measured-scf-time 0.5 --measured-encut 400 --measured-nk 8 --measured-natoms 8

# 日志转 CSV（计算已结束、只要过程数据）
python scripts/monitor_to_csv.py --oszicar OSZICAR --out monitor.csv

# 看门狗：边跑边盯着，出问题自动停机（推荐第一次先加 --no-stop 看判据）
python scripts/watchdog.py -- mpirun -np 16 vasp_std
python scripts/watchdog.py --no-stop -- mpirun -np 16 vasp_std     # 只观察不停机
python scripts/watchdog.py --dry-run                              # 事后复盘已有日志

# 结果分析
python scripts/analyze_results.py --outcar OUTCAR --oszicar OSZICAR --vasprun vasprun.xml

# 生成输入文件（示例：Si，手写结构）
python scripts/generate_inputs.py --task relax --elements "Si" --counts "2" \
    --lattice "0 0.5 0.5; 0.5 0 0.5; 0.5 0.5 0" --scale 5.43 \
    --positions "0 0 0; 0.25 0.25 0.25" --kmesh "8 8 8" --encut 520 --outdir ./calc

# 生成输入文件（可选增强：装了 ASE 可一键建结构）
pip install ase   # 可选
python scripts/generate_inputs.py --ase --ase-kind bulk --elements "Si" --ase-a 5.43 --outdir ./calc
```

## 可选增强库（ASE / pymatgen）

本 Skill 的核心脚本**零依赖**（只用 Python 标准库），什么都不装也能用。但如果装了 **ASE** 和 **pymatgen** 这两个材料计算领域最成熟的库，功能会更强：

| 能力 | 没装库（默认） | 装了库（增强） |
|------|--------------|--------------|
| 建结构 | 手写晶格+坐标 | `--ase` 一键建 bulk/fcc111/graphene 等 |
| 校验 | 内置正则校验 | 额外 pymatgen 结构合法性 + 原子重叠检查 |
| 结果分析 | 正则解析 OUTCAR | pymatgen 读 vasprun.xml 拿全量能量/带隙/磁矩 |

```bash
pip install ase pymatgen   # 可选，装了更强，不装也能用
```

详见 `references/external-libs.md`。

## 实时监控（看门狗）

VASP 一个结构优化可能跑几小时到几天。**跑完之后才发现没收敛，等于白烧机时**。看门狗就是替你盯着这件事的：

```bash
cd 计算目录
python scripts/watchdog.py -- mpirun -np 16 vasp_std
```

它自己启动 VASP，每 **20 步**体检一次，重点盯三件事：

| 事故 | 看门狗怎么拦 |
|------|-------------|
| SCF 一直不收敛，每步都顶到 NELM，能量是错的 | 连续 3 个离子步顶到 NELM → 停机 |
| 结构跑崩（原子撞在一起），越跑越乱 | 最小原子间距 < 0.5 Å → 停机 |
| 内存不够开始 swap，单步从 10 秒变 400 秒 | 单步耗时暴涨 50 倍 → 停机 |

出事后按固定顺序处理：**归档现场 → 写诊断报告 → 写 INCAR.patch → SIGTERM 等 10 秒（让 VASP 落盘 CONTCAR）→ 清掉整个进程组**。默认最多自动重启 2 次，每次重启前把修复配方写进 INCAR。完整 9 项判据与阈值见 `references/realtime-monitoring.md`。

**第一次用请走这三步**（别跳）：

```bash
python scripts/watchdog.py --no-stop -- mpirun -np 16 vasp_std      # 1. 只观察，看判据在你体系上会不会误报
python scripts/watchdog.py --no-restart -- mpirun -np 16 vasp_std   # 2. 只停机不改 INCAR，看报告是否对症
python scripts/watchdog.py -- mpirun -np 16 vasp_std                # 3. 再开自动重启
```

原因是：**漏报只是白跑一次，误杀可能砍掉一个跑了几十小时的作业**。拿不准就把阈值往宽松调。

计算已经跑完、想复盘当时为什么崩：`python scripts/watchdog.py --dry-run`（不启动 VASP、不杀任何进程，只体检已有日志）。

## 核心铁律（务必记住）

1. **输入文件只要动过，就必须重新完整校验**。
2. **POTCAR 元素顺序必须与 POSCAR 一致**。
3. **ENCUT ≥ POTCAR 的 ENMAX**（建议 1.3~1.5 倍）。
4. **正式计算前做收敛性测试**。
5. 所有临时文件、CSV、报告都放在**当前工作目录**。
6. **遇到抓取不到但浏览器能打开的网页（如 VASP wiki），会停下来让你手动查**，并给你精确网址和详细查找步骤，不会硬爬或瞎猜。
7. **正式计算前先做环境测试**（能力 8）：连最小体系都跑不通，正式计算必然失败；实测的单步耗时要喂给耗时估算。
8. **看门狗停机前必须先归档现场再杀进程**，且阈值宁可宽松——误杀的代价远大于漏报。

## 详细案例

四个从入门到进阶的完整案例，见 `examples/` 目录：

1. `example_si_relaxation.md` — 硅晶格常数，走完整流程（新手首选）
2. `example_graphene_dos.md` — 石墨烯 DOS，演示单项能力和校验抓错
3. `example_multi_capability.md` — BCC 铁磁矩，演示多能力组合和智能路由
4. `example_nidoped_wo3_gas_sensing.md` — **Ni 掺杂 WO₃ 气体传感全流程（真实科研案例，7 大能力全覆盖 + 4 模型 + 3 科学问题，最详细）**

## 免责声明

- 本 Skill 生成的参数、时间估计、结果分析都是**参考和指导**，最终结果以实际 VASP 计算为准。
- 涉及赝势（POTCAR）等受版权/许可约束的材料，请从官方渠道合法获取。
- Agent 给出的结论会区分「已实测」「推断」「未验证」，请留意。
