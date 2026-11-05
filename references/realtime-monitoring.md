# 实时监控与自动止损（看门狗）

> 这份文档讲的是 `scripts/watchdog.py`：在 VASP 跑的过程中**持续盯着**，每 20 步体检一次，发现问题就停机、留现场、给方案。
>
> 它和能力 5（`monitor_to_csv.py` 日志解析）不是一回事，分工见本文第 9 节。

## 一、为什么需要看门狗

VASP 一个结构优化可能跑几小时到几天。**跑完之后才发现没收敛，等于白烧机时**。最常见的三类事故：

| 事故 | 典型损失 | 看门狗怎么拦 |
|------|---------|-------------|
| SCF 一直不收敛，每步都顶到 NELM，能量是错的 | 跑满 NSW 才发现，白跑 | 连续 3 个离子步顶到 NELM → 停机 |
| 结构跑崩（原子撞在一起），力巨大，越跑越乱 | 几十步后 CONTCAR 已成废结构 | 最小间距 < 0.5 Å → 停机 |
| 内存不够开始用 swap，单步从 10 秒变 400 秒 | 跑一夜只前进几步 | 单步耗时暴涨 50 倍 → 停机 |

这三类如果不拦，**你以为在算，其实在烧电**。

## 二、托管模式：一条命令启动

看门狗**自己启动 VASP**，所以它能干净地杀掉整个 MPI 进程组（VASP 是 MPI 并行，只杀主进程会留下几个子进程继续占内存和 CPU）。

```bash
cd 计算目录
python <skill>/scripts/watchdog.py -- mpirun -np 16 vasp_std
```

`--` 后面的内容原样当作 VASP 命令执行。就这么简单，不需要改 INCAR、不需要改 POSCAR、不需要装任何东西（纯 Python 标准库）。

启动后你会看到：

```
[22:01:38] [START] 启动：mpirun -np 16 vasp_std
[22:01:38] [START] 体检间隔：每 20 步；心跳：60s；停滞阈值：900s 警告 / 3600s 致命；自动重启：是，最多 2 次
[22:01:39] [CHECK] 第 20 个离子步：致命 0 项，警告 0 项
[22:03:41] [HEARTBEAT] 进程存活（PID 42528），已跑 20 个离子步，距上次新产出 1.2 分钟
...
[22:41:03] [END] VASP 进程已退出，退出码 0
[22:41:03] [OK] 终检通过（警告 0 项）
```

### 为什么不做「附着模式」

附着模式（`--pid` 监控已经在跑的作业）在这里**故意不支持**，原因很实际：

- 附着模式只能按主进程号杀，**MPI 派生的子进程杀不干净**，会留下孤儿进程继续占满 CPU 和内存，下一次计算直接变慢
- 托管模式是父子关系，能用 `killpg` 一次性清掉整个进程组（Unix）或 `taskkill /T /F` 清掉整棵树（Windows）

如果你的场景必须用作业调度系统（Slurm/PBS）提交、改不了 mpirun 命令，那就在提交脚本里写：

```bash
# Slurm 的 sbatch 脚本里
srun python /path/to/watchdog.py -- mpirun -np $SLURM_NTASKS vasp_std
```

这样看门狗仍然是父进程，仍然能整组清理。

## 三、体检调度：什么时候查

两个独立的时间轴：

| 触发方式 | 默认 | 说明 |
|---------|------|------|
| **步数体检** | 每新增 **20 步** | 结构优化按离子步计；单点/DOS（没有离子步）按电子步计 |
| **心跳检查** | 每 **60 秒** | 只查「进程还活着吗」「有没有新产出」，不做完整体检，开销极小 |
| **终检** | 进程退出时 | 不论正常结束还是崩溃，都做一次完整体检并出结论 |

20 步这个默认值的取舍：**太密会频繁读 OUTCAR（几十 MB）拖慢计算，太疏则崩了 50 步才发现**。如果你的体系一步要跑 10 分钟以上，建议改成 `--check-every 5`，早发现早止损。

## 四、九项判据（阈值与依据）

所有阈值集中在 `watchdog.py` 顶部的 `THRESHOLDS` 字典里，可以直接改。

### 致命级（触发停机）

| 判据 | 阈值 | 为什么是致命 |
|------|------|-------------|
| `SCF_NOT_CONVERGED` 电子步未收敛 | 结构优化：连续 **3** 个离子步的电子步数都 **≥ NELM**<br>静态计算（IBRION=-1/0 或 NSW≤1）：**1** 次 SCF 用满 NELM 即判 | 每步都没收敛，能量完全不可信，继续跑毫无意义 |
| `SCF_NOT_CONVERGED_STATIC` 静态 SCF 未收敛 | SCF 还在跑、OSZICAR 尚未写 `F=` 行，电子步已 **≥ NELM** | 单点/DOS/能带整场只有一次 SCF，顶格即意味着这次计算白跑，早停早省机时 |
| `STRUCTURE_COLLAPSE` 结构崩坏 | 最小原子间距 **< 0.5 Å** | 原子已经重叠，物理上不可能，后面全是垃圾数据 |
| `VOLUME_DRIFT` 体积异常 | ISIF=3 时体积变化 **> ±30%** | 变胞优化跑飞，通常是 POTIM 太大或结构初值离谱 |
| `OUTCAR_FATAL` 致命报错 | 出现 `ZBrent`、`Error EDDDAV`、`PSMAXN`、`BAD TERMINATION`、`Fatal error`、`internal error`、`LAPACK: Routine ZPOTRF failed`、`VERY BAD NEWS`、`NaNs` | VASP 自己都说完了，不可能正常结束 |
| `TIMING_SPIKE` 耗时暴涨 | 单步耗时 > 历史中位数 **50 倍** | 几乎一定是内存不够开始 swap，或节点出问题 |
| `STALLED` 疑似卡死 | **3600 秒**（1 小时）无任何新产出 | 进程活着但不干活，通常已死锁 |

> **为什么静态计算要单独判**：单点能、DOS、能带这类计算（`IBRION=-1`）整场只跑一次 SCF，不存在「第 2、第 3 个离子步」，按「连续 3 步」判永远凑不满，SCF 顶满 NELM 也会被报成正常——而这恰恰是最该拦住的一类失败。所以看门狗按 `IBRION`/`NSW` 分流：静态计算 1 步顶格即致命，结构优化仍给 3 步容错（第 1 步偶发不收敛但后面能收敛的情况确实存在）。

### 警告级（记录，不停机）

| 判据 | 阈值 | 说明 |
|------|------|------|
| `SCF_NEAR_NELM` 逼近 NELM | 最近 2 步电子步数 ≥ NELM × **0.80** | 收敛余量很小，下一步可能就顶到上限 |
| `SCF_NEAR_NELM_CURRENT` 当前循环偏多 | 正在进行的那次 SCF 已用 ≥ NELM × **0.80** 步 | 第一个 SCF 就快顶格，趁早改混合参数，别等它跑满 |
| `ENERGY_OSCILLATION` 能量震荡 | 最近 6 步中能量变化**变号 ≥ 4 次**且最大跳变 > 1 eV | 混合参数不合适，通常会自己缓过来 |
| `ENERGY_JUMP` 能量跳变 | 单步变化 > **5 eV** | 有可能是原子靠太近，也可能是正常调整 |
| `ENERGY_UNREALISTIC` 能量不合理 | 每原子能量超出 **[-20, 5] eV/atom** | 大概率赝势选错或 POTCAR 与 POSCAR 元素不匹配 |
| `FORCE_INCREASING` 力不降反升 | 最近 3 步最大力**严格递增**且最新 > **0.5 eV/Å** | 优化走偏，需要减小 POTIM |
| `MAGNETIZATION_LOST` 磁矩归零 | \|总磁矩\| < 初始 \|MAGMOM\| 之和的 **10%** | 若体系本该有磁性，说明是假非磁解 |
| `OUTCAR_WARN` 其他警告 | `Sub-Space-Matrix is not hermitian`、`WARNING: DENTET`、金属却用 `ISMEAR=-5`、`BRMIX` 问题、`PSSYEVX` | 能算完，但结果要打折扣看 |
| `TIMING_SLOW` 耗时变慢 | > 历史中位数 **10 倍** | 留意内存与其他进程 |
| `STALL_WARN` 进度停滞 | **900 秒**（15 分钟）无新产出 | 大体系单步慢时属正常，只看不减 |

### 关于停滞判据的一个坑（已修正）

早期实现用「`OSZICAR` 的 mtime 距现在多久」判卡死。这在**刚启动时必然误判**——VASP 初始化要读赝势、建对称性、第一次 SCF，前几分钟可能根本没有 OSZICAR 文件，或者文件是上一次计算留下的旧文件。

现在的实现是：

1. 以「看门狗**观察到** OSZICAR/OUTCAR 发生变化」为基准，记录 `last_change` 时刻
2. 停滞时长 = 当前时刻 − `last_change`
3. **启动宽限期**（`--startup-grace`，默认 600 秒）内完全不判停滞

大体系（几百原子、第一次 SCF 要十几分钟）请把 `--startup-grace` 和 `--stall-fatal` 一起调大。

## 五、停机流程（顺序固定，不能乱）

一旦某次体检出现致命项，按这个顺序处理：

```
1. 归档现场  → watchdog_snapshots/<时间>_retry<N>/
                复制 OSZICAR、OUTCAR、CONTCAR、INCAR、POSCAR、KPOINTS、vasp.log、vasprun.xml
2. 写诊断报告 → watchdog_report_<时间>_retry<N>.md
                含：致命/警告清单、最后 20 步过程数据、建议的 INCAR 修改
3. 写修复建议 → INCAR.patch
4. 终止进程组 → Unix: SIGTERM → 等 10 秒 → SIGKILL（整个进程组）
                Windows: taskkill /T /F
5. 决定是否重启（见下节）
```

**为什么先归档再杀**：SIGTERM 之后 VASP 会尝试写 CONTCAR/CHGCAR，但有可能写坏。先复制一份干净的现场，后面排查才有原始证据。

**为什么 SIGTERM 之后还要等 10 秒**：给 VASP 机会把 CONTCAR 和 CHGCAR 落盘，这样如果只是参数不合适、结构没崩，还能从 CONTCAR 接着算，省掉重头开始的几小时。

## 六、自动重启策略

默认**最多重试 2 次**（`--max-retries 2`），每次重启前做三件事：

1. **把 INCAR.patch 应用到 INCAR**（原文件备份为 `INCAR.watchdog_bak_retry<N>`）
2. **重新加载 INCAR**——这一步不能省。比如 NELM 已从 60 改成 200，如果还用旧的 60 判断，会永远报「未收敛」，两次重试全白费
3. **处理结构**：
   - 结构崩坏 → 回退到启动前的原始 `POSCAR.watchdog_orig`，并把崩坏的 CONTCAR **改名归档**（`CONTCAR.collapsed_retry<N>`）。留着坏 CONTCAR，重启后第一次体检又会读到它，白白消耗一次重试机会
   - 只是参数问题 → 用 CONTCAR 覆盖 POSCAR，从上次结构继续

### 常见问题 → 自动配方

| 问题 | 自动写入的 INCAR 修改 | 为什么 |
|------|---------------------|--------|
| SCF 不收敛 / 逼近 NELM | `ALGO=Normal`、`AMIX=0.2`、`BMIX=0.0001`、`NELM=200` | 降低混合系数、加电子步上限，是最通用的救火组合 |
| 能量震荡 | `AMIX=0.3`、`BMIX=0.0001`、`NELM=150` | 减弱混合，抑制来回摆 |
| 结构崩坏 / 力上升 / 能量跳变 | `POTIM=0.1` | 把离子步长减半甚至更多，避免一步迈过头 |
| OUTCAR 报 ZBrent / EDDDAV | `ALGO=Normal`、`AMIX=0.2`、`BMIX=0.0001`、`NELM=200` | 换成更稳的算法重试 |

### 哪些问题**不**自动重启

以下情况重试没意义，看门狗会直接停手交给人工（退出码 3）：

- `ENERGY_UNREALISTIC` — 能量量级离谱，多半是赝势或 POSCAR 本身错了，改 INCAR 救不回来
- `MAGNETIZATION_LOST` — 需要人工决定要不要重新设置 MAGMOM 初值
- `TIMING_SPIKE` — 是硬件/内存问题，改参数没用
- `OUTCAR_WARN` 类 — 只是警告，不影响继续

### 为什么限制 2 次

如果根因是结构或赝势错误，**改参数重启多少次都会失败**。不限次数会陷入「崩了重启、重启又崩」的死循环，白白烧机时。2 次足够覆盖「参数不合适」这类临时性问题，超过就说明是别的原因。

## 七、输出文件

全部落在 `--workdir`（默认当前目录），符合「临时文件放当前目录」的约定：

| 文件 | 内容 |
|------|------|
| `watchdog.log` | 完整运行日志（带时间戳），排查时先看这个 |
| `watchdog_stdout.log` | VASP 自己的标准输出（等价于 `vasp.log`） |
| `watchdog_monitor.csv` | 过程数据，**列与 `monitor_to_csv.py` 完全一致**，可直接用 Excel 画图 |
| `watchdog_report_<时间>.md` | 诊断报告，只在发现问题时生成 |
| `watchdog_snapshots/<标签>/` | 停机现场快照 |
| `INCAR.patch` | 修复建议（未自动应用时供人工审查） |
| `INCAR.watchdog_bak_retry<N>` | 每次自动修改前的 INCAR 备份 |

## 八、退出码

| 码 | 含义 | 你该做什么 |
|----|------|-----------|
| 0 | 正常结束，体检无致命问题 | 进入能力 6 结果分析 |
| 2 | VASP 正常结束，但**终检**发现致命问题 | 看报告，结果不可用 |
| 3 | 看门狗主动停机（致命问题，未重启） | 看报告 + INCAR.patch，人工调整后重跑 |
| 4 | 主动停机且**自动重试用尽** | 说明不是参数问题，检查结构/赝势 |
| 1 | 看门狗自身异常（命令找不到等） | 检查 mpirun/vasp_std 路径 |

## 九、和能力 5 的分工（重要，别混了）

| | 能力 5 `monitor_to_csv.py` | 能力 9 `watchdog.py` |
|---|---|---|
| 做什么 | 读一次日志，转成 CSV | 持续盯着，判活，必要时停机 |
| 有循环吗 | 没有，跑完就退出 | 有，一直跑到 VASP 结束 |
| 能停止 VASP 吗 | **不能** | 能（托管模式下杀整个进程组） |
| 什么时候用 | 计算已结束，想要过程数据 | 计算正在跑，想有人看着 |
| 事后复现 | — | `--dry-run` 可对已有日志重放一次体检 |

**一句话**：`monitor_to_csv.py` 是「事后记账」，`watchdog.py` 是「全程盯梢 + 出事拉闸」。

## 十、`--dry-run`：事后诊断

VASP 已经跑完了、或者作业被别人杀掉了，想复盘当时到底出了什么问题：

```bash
# 对当前目录的 OSZICAR/OUTCAR 做一次完整体检
python watchdog.py --dry-run

# 指定文件 + 顺便出报告
python watchdog.py --dry-run --oszicar path/OSZICAR --outcar path/OUTCAR --report
```

这个模式**不启动 VASP、不杀任何进程**，纯粹读文件做判断。适合：

- 复现历史故障，确认判据是否合理
- 拿到别人的 OUTCAR，快速判断这次计算靠不靠谱
- 调阈值时验证效果（把 `THRESHOLDS` 改了之后先 dry-run 试试）

## 十一、参数速查

```bash
python watchdog.py \
  --check-every 20 \      # 每 20 步体检一次
  --heartbeat 60 \        # 每 60 秒打一条心跳日志
  --stall-warn 900 \      # 15 分钟无产出 → 警告
  --stall-fatal 3600 \    # 1 小时无产出 → 判定卡死
  --startup-grace 600 \   # 启动后 10 分钟内不判停滞
  --max-retries 2 \       # 自动重启最多 2 次
  --poll 2 \              # 每 2 秒轮询一次
  --workdir . \           # 计算目录
  -- mpirun -np 16 vasp_std
```

关闭某些行为：

```bash
--no-restart    # 只停机，不自动重启（推荐第一次用这个，先看看判据是否符合你的体系）
--no-stop       # 只报告不停机（纯观察模式，试阈值时用）
```

## 十二、第一次用建议这样做

1. **先 `--no-stop` 跑一次**，看它在你的体系上会报什么。如果发现某个判据在你的体系上频繁误报（比如大体系单步本来就慢，老是报 `TIMING_SLOW`），就调对应阈值
2. 确认判据合理后，改成 `--no-restart` 正式跑——**先只停机，不自动改 INCAR**
3. 观察 2~3 次停机的诊断报告，确认 `INCAR.patch` 里的建议确实对症，再开自动重启

跳过这三步直接开自动重启也能工作，但你会失去对「看门狗到底在做什么」的掌控感。第一次就出事的话，不容易分清是计算的问题还是阈值的问题。

## 十三、阈值调不准怎么办

所有阈值都在 `watchdog.py` 顶部的 `THRESHOLDS` 里，改完用 `--dry-run` 立刻验证，不用等计算跑起来：

```python
THRESHOLDS = {
    "scf_near_nelm_ratio": 0.80,   # 想更早预警就改 0.70
    "scf_near_nelm_streak": 3,     # 想更严格就改 2
    "energy_jump": 5.0,            # 吸附体系能量跳变大，可放宽到 8
    "min_distance": 0.5,           # 含氢体系可降到 0.4（H 键很短）
    "max_volume_change": 0.30,     # 高压/相变研究要放宽
    "force_increase_threshold": 0.5,
    "magnetization_lost_ratio": 0.10,
    "timing_spike_ratio": 10.0,
    "timing_fatal_ratio": 50.0,
}
```

**改阈值的原则**：宁可漏报一点，也不要误杀。漏报的代价是白跑一次，误杀的代价是一个跑了几十小时的作业被砍。所以拿不准时，往宽松调。
