# 结构获取：算之前，先拿到「对的那个结构」

> 配合 `scripts/fetch_structure.py` 使用。
>
> 一句话原则：**结构错了，后面所有计算全废**——而且 VASP 不会报错，它只会安安静静地算出一个毫无意义的能量。所以这一步宁可慢，不能猜。

## 一、为什么单独立这一步

以前的做法是「让 Agent 把坐标写进 POSCAR」或「用 ASE 内置模板建结构」。两个都有硬伤：

| 做法 | 问题 |
|------|------|
| 手写坐标 | 复杂结构（掺杂、表面、异质结）几十个原子，写错一个小数点根本看不出来 |
| ASE 内置 `bulk()` | 只有几十种常见结构，`WO3`、`MoS2` 这类多相材料不在其中 |
| 凭空生成 | 最危险：看起来像模像样，其实是编的 |

真实科研里的结构几乎都来自数据库或文献。所以能力 2 前面补一步"结构获取"：**先从可靠来源拿，拿不到就停下**。

## 二、四层优先级（拿到即停）

```
① 本地文件（.cif / POSCAR / CONTCAR）     最可靠，你有就用这个
        ↓ 没有
② OPTIMADE 公开接口（默认可用于 MP）      免费、无需 key、无需装任何库
        ↓ 查不到
③ Materials Project 官方 API              需要你自己注册的免费 key，信息更全
        ↓ 还没有
④ COD 网页库                              实验结构，需要 pymatgen，服务器较慢
        ↓ 都没有
⑤ 停下 → 给用户精确网址 + 检索步骤 → 用户手动下载后放进来
```

### 为什么②放这么前：OPTIMADE 是什么

OPTIMADE 是材料数据库的统一查询协议。Materials Project 的公开只读端点是：

```
https://optimade.materialsproject.org/v1/structures
```

它 `不 需 要 API key`，直接返回**完整晶胞**：

- `lattice_vectors` — 3×3 晶格矩阵（Å）
- `cartesian_site_positions` — 所有原子的直角坐标
- `species_at_sites` — 对应元素
- `nsites` — 原子数

关键好处：**返回的就是完整结构，不需要做空间群对称展开**。所以它不依赖 pymatgen、不依赖 ASE，纯标准库就能用。这也是它比 COD 更省事的原因（COD 的 CIF 只存非对称单元，必须展开）。

代价：这个端点不提供空间群、形成能、稳定性。要这些信息就得用 ③。

## 三、怎么用

### 1. 查有什么（不下载）

```bash
python scripts/fetch_structure.py --formula WO3 --list
```

```
[Materials Project - OPTIMADE] 命中 40 条（本次返回 40 条，按原子数升序）：

    #  ID             化学式               原子数  空间群
  --------------------------------------------------------------------
    0  mp-19390       O3W                 4  （该库未提供）
    1  mp-1386422     O3W                 8  （该库未提供）
    2  mp-19443       O3W                 8  （该库未提供）
    ...
```

按原子数升序，是因为小晶胞算得快、通常也是你想要的那个。

### 2. 取第 N 个，直接出 POSCAR

```bash
python scripts/fetch_structure.py --formula WO3 --index 0 -o POSCAR
```

会额外写一份 `structure_info.txt`，内容是结构摘要，方便核对：

```
来源    : Materials Project - OPTIMADE mp-19390（O3W）
化学式  : WO3
原子数  : 4  (W1, O3)
晶格    : a=3.8062  b=3.8062  c=3.8062 Å
晶胞体积: 55.141 Å³   (13.79 Å³/atom)
POSCAR 元素顺序：W, O  → POTCAR 必须按同样顺序拼接，否则元素会对错
```

### 3. 扩胞

```bash
python scripts/fetch_structure.py --formula WO3 --supercell 2 2 2 -o POSCAR
# → W8O24，32 个原子，a=7.61 Å
```

### 4. 用本地文件（最可靠）

```bash
python scripts/fetch_structure.py --from MoS2.cif -o POSCAR
python scripts/fetch_structure.py --from CONTCAR --supercell 2 2 1 -o POSCAR
```

读 CIF 时：装了 pymatgen 会自动按空间群展开（推荐）；没装则走内置解析器，遇到"非对称单元 + 空间群"的 CIF 会**明确报错**而不是给你一个残缺结构。

### 5. 用 Materials Project 官方 API（有 key 时）

```bash
python scripts/fetch_structure.py --formula WO3 --mp-key $MP_API_KEY --list
```

比 OPTIMADE 多两个关键信息：

- `E_hull`（energy above hull）：**离凸包越近越稳定**，0 表示热力学稳定相
- 空间群符号

同一个化学式常有好几个相（WO₃ 就有单斜 γ、正交 β、四方、六方），有 E_hull 就能判断哪个是该温度下的稳定相。

key 免费申请：<https://materialsproject.org/api>，装库 `pip install mp-api`。

### 6. COD（实验结构）

```bash
python scripts/fetch_structure.py --formula WO3 --cod --list
python scripts/fetch_structure.py --formula WO3 --cod --cod-id 1528915 -o POSCAR
```

COD 给的是**实验测定的室温结构**，做 DFT 时要注意：

- 实验晶格常数与 PBE 优化结果通常差 1~2%（PBE 偏高）
- 实验结构里的原子位置有热振动平均效应，不是理论平衡位置
- 建议拿到后先做一次 `ISIF=3` 结构优化再算性质

COD 的 CIF 是"非对称单元 + 空间群"，**必须装 pymatgen**（脚本会提前拦住并提示）。

## 四、掺杂结构怎么建（最容易做错的一类）

**数据库里没有"Ni 掺杂 WO₃"这种结构**——MP 和 COD 收录的都是有序化合物，掺杂固溶体不是。脚本遇到 `Ni0.05W0.95O3` 这类非整数配比会直接拒绝并给指引，不会拿个近似结构糊弄你。

正确流程：

```bash
# 第 1 步：取母体，扩胞到能表达目标浓度的尺寸
python scripts/fetch_structure.py --formula WO3 --supercell 2 2 2 -o POSCAR
# 32 个原子（8 个 W 位），替换 1 个 W → 12.5 at%；再扩到 3×3×2 可做 ~5%
```

第 2 步：把其中一个 W 换成 Ni。要同步改四处，缺一处就错：

| 位置 | 改什么 |
|------|--------|
| POSCAR 第 6 行 | 元素名加 `Ni`（顺序决定 POTCAR 拼接顺序） |
| POSCAR 第 7 行 | 计数：`W O` → `W Ni O`，数字同步调整 |
| POSCAR 坐标区 | 该原子的坐标要挪到 Ni 对应的连续区块里（POSCAR 要求同元素连续） |
| POTCAR | 按新顺序重新拼接，`Ni` 的 POTCAR 不能少 |

改完**必须重新跑** `validate_inputs.py`——它会检查元素顺序、计数与坐标行数是否自洽。

**选哪个 W 位替换，是有物理意义的**，不能随便挑：

- 同一位点（Wyckoff 位置）的原子在晶体学上等价，替换任意一个结果相同
- 不同位点不等价，形成能会差几十 meV，直接影响结论
- 不确定体系里有哪些不等价位 → 让用户查文献，或用 `--mp-key` 拿对称性信息，**不要自己猜**

## 五、拿到结构后的核对清单

写 POSCAR 之前，逐条确认：

1. **化学式对不对**：掺杂的话，元素种类和比例是否与目标一致
2. **原子数对不对**：比如 WO₃ 的 4 原子立方胞是 1W+3O；如果拿到 16 原子，那是 2×2×2 超胞或别的相
3. **晶格常数**：与文献/数据库值差多少？超过 5% 就要警惕
4. **是不是你要的那个相**：同分异构相晶格常数可能只差 0.1 Å，但物理性质完全不同
5. **元素顺序**：POSCAR 的顺序 = POTCAR 的顺序，改一个必须改另一个
6. **有没有真空层**：做表面/二维材料时 c 方向要留 ≥15 Å，看 `c` 值是否明显大于另外两个

## 六、常见坑

| 坑 | 现象 | 怎么避免 |
|----|------|---------|
| 拿错相 | 能量算出来和文献差 0.1 eV/atom | 用 E_hull 挑稳定相，或按空间群核对 |
| 实验结构直接用 | 优化后晶格缩了 2% | 正常现象；报告里说明是实验值起步 |
| 超胞与原胞混用 | 每原子能量对得上，但总能差整数倍 | 比较时统一换算成 eV/atom |
| 掺杂替换了不等价位 | 形成能偏低/偏高几十 meV | 查文献确认替换位点 |
| 忘了改 POTCAR 顺序 | VASP 直接报错，或更糟——算出一个看似正常的错结果 | 改完必跑 `validate_inputs.py` |
| 用 1×1×1 做掺杂 | 掺杂浓度 25%，物理上不现实 | 至少 2×2×2，浓度控制在几个 at% |

## 七、拿不到的时候（重要）

如果 OPTIMADE / MP / COD 都查不到，**不要构造一个"看起来合理"的结构**。按铁律停下来，交给用户，并给出可操作的信息：

1. 说清楚查了哪些库、用的什么检索条件、返回了什么
2. 给出精确网址，例如 `https://materialsproject.org/materials?formula=WO3`
3. 给出下一步怎么做：在网站搜索框输入化学式 → 按稳定性排序 → 点进材料页 → Export → CIF
4. 告诉用户拿到 CIF 后放哪、怎么继续（`--from xxx.cif`）

用户的实验体系、未发表的新相、特定掺杂构型，数据库里本来就不会有——这时候**只有用户知道该用什么结构**。
