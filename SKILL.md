---
name: vasp-calculation-assistant
description: VASP 第一性原理计算的端到端助手，面向不懂 DFT/VASP 理论的小白用户。覆盖环境与性能测试、硬件资源调度、四大输入文件（INCAR/POSCAR/POTCAR/KPOINTS）生成与严格校验、计算耗时估算、预计算试算、实时运行监控（整理为 CSV）、结果物理合理性分析与 Markdown 计算报告生成。支持单项或多项能力组合调用，并能从用户提示词中自动识别该调用哪些能力。当用户提到 VASP 计算、DFT、第一性原理、结构优化、自洽计算、能带、态密度、赝势、KPOINTS、INCAR、POSCAR、POTCAR、收敛性测试、计算耗时、预计算、资源调度、性能测试、环境测试等时使用。
version: 1.2.0
---

# VASP 计算助手（vasp-calculation-assistant）

这个 Skill 帮你在**不完全懂 VASP 理论**的情况下，也能做出**正确、符合物理规律、符合你预期**的第一性原理计算结果。

它把一次完整的 VASP 计算拆成 8 个可以**单独用、也可以组合用**的能力。你不需要按顺序走完所有步骤——只要把你的需求用大白话告诉我，我会自动判断该调用哪些能力。

## 什么时候用这个 Skill

- 你想先确认 VASP 能不能正常用、这台机器跑得快不快（正式算之前必做）
- 你想跑一次 VASP 计算，但不确定参数怎么设、文件怎么写
- 你已经有了输入文件，想让我检查有没有错
- 你想知道这次计算大概要跑多久
- 你想在正式跑之前先"试算"一下，看看结果大致对不对
- 你想实时盯着计算进度，并把过程数据存成 CSV
- 计算跑完了，你想知道结果对不对、符不符合物理规律，并要一份正式报告

## 8 大能力总览

| 编号 | 能力 | 一句话说明 | 参考文档 |
|------|------|-----------|----------|
| 1 | 资源调度 | 根据虚拟机硬件（内存、CPU 型号/数量/核心数）给出并行参数建议 | `references/resource-scheduling.md` |
| 2 | 输入文件生成 + 校验 | 生成四大输入文件，并严格校验一致性 | `references/input-validation.md` |
| 3 | 耗时估算 | 用体系规模 + 预计算校准，估计计算时长 | `references/time-estimation.md` |
| 4 | 预计算 | 正式算前跑一次快试算，反推结果趋势并优化输入 | `references/pre-calculation.md` |
| 5 | 实时监控 → CSV | 解析运行日志，整理成 CSV | 见下方脚本 |
| 6 | 结果分析 + 报告 | 判断结果合理性，输出 Markdown 报告 | `references/result-analysis.md` |
| 7 | 智能路由 | 从提示词自动识别该调用哪个能力 | 本文件"路由规则" |
| 8 | 环境与性能测试 | 快速验证 VASP 可用性 + 实测机器性能，反哺耗时估算 | `references/environment-test.md` |

VASP 基础理论速查见 `references/vasp-basics.md`，联网查证网址清单见 `references/online-resources.md`，可选增强库（ASE/pymatgen）说明见 `references/external-libs.md`。

## 路由规则（能力 7）

收到用户请求后，**先判断意图**，再调用对应能力。判断时看用户提示词里的关键词和语境，不要机械匹配，要理解他到底想干什么。

| 用户意图信号 | 调用能力 |
|-------------|----------|
| "我的机器是 XX 内存、XX 核 CPU，该怎么设并行参数""怎么分配资源""NPAR/NCORE/KPAR 设多少" | 1 资源调度 |
| "帮我写 INCAR/POSCAR/POTCAR/KPOINTS""生成输入文件""检查输入文件有没有错""参数对不对" | 2 输入文件生成 + 校验 |
| "要跑多久""多长时间能算完""估计耗时" | 3 耗时估算 |
| "先试算一下""预计算""跑个快的看看趋势""大概会是什么结果" | 4 预计算 |
| "监控进度""盯一下跑得怎么样""把过程数据存成 CSV" | 5 实时监控 → CSV |
| "结果对不对""符不符合物理规律""给我一份计算报告""分析结果" | 6 结果分析 + 报告 |
| "VASP 能不能用""测试一下环境""机器跑得快不快""性能测试""基准测试""benchmark" | 8 环境与性能测试 |
| "帮我从头到尾跑一遍 XX 的计算" | 组合：8 → 2 → 3 → 4 → 5 → 6（必要时含 1） |

**组合调用原则**：用户如果描述的是一个完整流程（比如"帮我算 Si 的晶格常数"），按自然顺序串联多个能力；如果只是问其中一个点，只调用那一个。不确定时，先做能力 2 的输入文件生成，因为一切计算的起点都是输入文件。

## 总工作流（完整跑一遍时）

```
[8 环境与性能测试] → [1 资源调度] → [2 生成输入文件] → [2 校验输入文件]
      → [3 估算耗时] → [4 预计算试算] → (根据预计算反馈优化 2)
      → [正式计算] → [5 实时监控→CSV] → [6 结果分析→报告]
```

其中预计算（能力 4）是一个**反馈环**：它的结果会反过来优化输入文件（能力 2）和耗时估算（能力 3）。而环境与性能测试（能力 8）是**正式计算前的第一道关**——它实测的"单电子步耗时"会直接喂给能力 3，把耗时估算从"经验粗估"升级为"实测校准"。

## 核心铁律（贯穿所有能力）

1. **输入文件只要动过，就必须重新完整校验**（能力 2 的校验环节）。这是最高优先级的安全规则。
2. **POTCAR 元素顺序必须与 POSCAR 完全一致**——这是新手最容易犯、后果最严重的错误。
3. **ENCUT 必须 ≥ POTCAR 中所有元素的 ENMAX**（建议 1.3~1.5 倍）。
4. **正式计算前必须做收敛性测试**：逐步加密 ENCUT 和 k 点，确认目标物理量变化 < 阈值（能量收敛标准建议 1 meV/原子 ≈ 0.001 eV/原子；ENCUT 步长 50~100 eV，k 点每次加密 2）。详见 `references/vasp-basics.md`。
5. **所有脚本生成的中间文件、CSV、报告，一律放在当前工作目录**（不写系统临时目录）。
6. **结论要区分"已实测""基于经验推断""未验证"**，不把推断当事实。
7. 涉及删除、覆盖文件等破坏性操作，必须先向用户确认。
8. **遇到抓取不到、但浏览器能打开的网页（如 VASP wiki 的反爬限制），必须停下来让用户手动查找**，给出"去哪里找、怎么找"的详细步骤，绝不硬爬、绝不凭空猜测结果。详见 `references/online-resources.md` 的"使用原则"。

## 各能力详细说明

### 能力 1：资源调度
读入用户提供的虚拟机配置（内存、CPU 型号、CPU 数量、每颗 CPU 核心数），按 `references/resource-scheduling.md` 的规则给出：
- 建议的 MPI 进程数（NP）、OpenMP 线程数（OMP_NUM_THREADS）
- INCAR 里的并行参数（NCORE / KPAR / NPAR）
- 内存是否够用、单核内存需求是否满足
- 该用哪个 VASP 可执行文件（std / gam / ncl）

### 能力 2：输入文件生成 + 校验
按 `references/vasp-basics.md` 和 `references/input-validation.md` 生成四大输入文件，并用 `scripts/generate_inputs.py` 生成、`scripts/validate_inputs.py` 校验。

**可选增强**：若已安装 ASE/pymatgen（见 `references/external-libs.md`），生成可用 `--ase` 一键构建常见结构（bulk/fcc111/graphene 等），校验会额外用 pymatgen 做结构合法性检查；未安装则自动降级到手写/内置正则，核心功能不受影响。

校验是**重中之重**，必须逐项核对：
- 文件格式是否正确（每行、每段）
- 四文件之间的一致性（元素顺序、k 点密度、ENCUT）
- 参数是否合理（ISMEAR、ISIF、IBRION、EDIFF 等）
- 需要时可联网核对结构、赝势、参数（具体网址见 `references/online-resources.md`，含 Materials Project https://materialsproject.org/、ICSD https://icsd.products.fiz-karlsruhe.de/、VASP 官方 wiki https://www.vasp.at/wiki/）

**任何文件被动过 → 重新校验。**

### 能力 3：耗时估算
用 `scripts/estimate_time.py`，结合体系规模（原子数 × k 点 × ENCUT × 计算类型）给经验公式粗估，再用能力 4 的预计算实测单步耗时校准，输出预计算时长 + 正式计算时长两个估计值。详见 `references/time-estimation.md`。

### 能力 4：预计算
正式计算前，把体系"缩小"跑一次特别快的试算（降 ENCUT、减 k 点、降 EDIFF、减少 NSW），从试算的过程和结果中：
- 判断正式计算结果的**趋势**和**量级**
- 判断是否符合用户预期
- 拿实测耗时反哺能力 3 的估算
- 暴露输入文件的潜在问题，反哺能力 2 优化

详见 `references/pre-calculation.md`。

### 能力 5：实时监控 → CSV
用 `scripts/monitor_to_csv.py` 解析 VASP 运行日志（OSZICAR、OUTCAR，或用户粘贴的日志片段），提取每个电子步/离子步的能量、力、耗时，整理成结构化 CSV 文件，保存在当前目录。

CSV 固定输出 8 列（列名与脚本完全一致）：

| 列名 | 含义 |
|------|------|
| step_type | 步骤类型（ionic=离子步 / electronic=电子步） |
| step_index | 所属离子步序号（电子步归属到当前离子步） |
| scf_iteration | 该离子步内的电子步序号（离子步行此列为空） |
| energy | 能量（E0，电子步当前能量） |
| free_energy | 自由能（F，含展宽项） |
| dE | 能量变化量 |
| elapsed_time | 该步耗时（秒，来自 OUTCAR 的 LOOP+ 时序，无则空） |
| note | 备注（电子步算法，如 RMM/DAV） |

### 能力 6：结果分析 + 报告
用 `scripts/analyze_results.py` 和 `references/result-analysis.md`，判断：
- 能量是否收敛、力是否收敛到目标阈值
- 结构是否合理（键长、原子间距、对称性）
- 结果是否符合真实物理规律（带隙符号、能量量级、磁性等）
- 是否满足用户最初的需求

**可选增强**：若已安装 ASE/pymatgen，`analyze_results.py --vasprun` 会用 pymatgen `Vasprun`/ASE 读 vasprun.xml，一次性拿到能量、力、带隙、磁矩、晶格等全量数据，比正则解析 OUTCAR 更完整可靠；未安装或 vasprun.xml 缺失则自动降级到正则解析。

最终输出一份完整的 Markdown 计算报告（含计算设置、收敛情况、结果数据、物理合理性判断、结论）。

### 能力 8：环境与性能测试
用 `scripts/test_environment.py` 和 `references/environment-test.md`，在正式计算前：
- **冒烟测试**：用最小体系（如 2 原子 Si）跑单点能，验证 VASP 可执行文件、库、MPI、赝势路径是否正常（几秒内完成）
- **性能基准**：固定参数（原子数、ENCUT、k 点）跑若干电子步，实测**单电子步平均耗时**，作为这台机器的性能基准
- 测试结果**反哺能力 3**：把实测单步耗时喂给 `estimate_time.py` 的 `--measured-scf-time`，把耗时估算升级为"实测校准（高置信度）"
- 测试不通过时，按 `environment-test.md` 第四节排查，先修环境再谈计算

## 使用说明

详细的使用方法、脚本用法、以及 4 个从入门到进阶的使用案例，见 `README.md` 和 `examples/` 目录。
