# 联网资源清单（在线查证/对比/优化）

输入文件校验、结构核对、参数优化、结果物理合理性判断时，用下面的**具体网址**联网查证。这是"输入文件可以在特定网址查找、对比、优化"这一需求的落地清单。

Agent 在校验或分析时，优先用 WebSearch / WebFetch 访问这些网址。

## 一、VASP 官方资源（最权威，参数/教程/FAQ/报错）

| 用途 | 网址 |
|------|------|
| VASP 官方 Wiki（参数手册、教程、HowTo） | https://www.vasp.at/wiki/ |
| VASP 官方论坛（报错排查、经验交流） | https://www.vasp.at/forum/ |
| VASP 官网（版本、许可、新闻） | https://www.vasp.at/ |
| 官方教程（bulk 结构优化等） | https://www.vasp.at/tutorials/latest/ |
| 结构优化专题页 | https://www.vasp.at/wiki/index.php/Structure_optimization |
| 电子自洽计算设置页 | https://www.vasp.at/wiki/index.php/Setting_up_an_electronic_minimization |
| INCAR 参数完整列表 | https://www.vasp.at/wiki/index.php/Category:INCAR_tag |
| POSCAR 格式说明 | https://www.vasp.at/wiki/index.php/POSCAR |
| POTCAR 赝势准备 | https://www.vasp.at/wiki/index.php/POTCAR |
| KPOINTS 格式说明 | https://www.vasp.at/wiki/index.php/KPOINTS |
| 并行优化（NCORE/KPAR/NPAR） | https://www.vasp.at/wiki/index.php/Optimizing_the_parallelization |
| 电子收敛问题排查 | https://www.vasp.at/wiki/index.php/Category:Electronic_minimization |

> 提示：VASP Wiki 的参数页 URL 规律通常是 `https://www.vasp.at/wiki/index.php/<TAG名>`，例如查 ENCUT 就是 `https://www.vasp.at/wiki/index.php/ENCUT`。不确定某个参数时，可先搜索 `<参数名> vasp wiki`。

## 二、晶体结构与材料数据（核对/下载结构）

| 用途 | 网址 |
|------|------|
| Materials Project（结构、参数、带隙、磁矩等材料数据） | https://materialsproject.org/ |
| Materials Project 网页版（可搜索结构） | https://next-gen.materialsproject.org/ |
| Materials Project API 文档 | https://docs.materialsproject.org/ |
| ICSD（无机晶体结构数据库，需订阅，部分开放） | https://icsd.products.fiz-karlsruhe.de/ |
| AFLOW（自动流程材料数据库） | http://aflowlib.org/ |
| NOMAD（计算材料数据归档） | https://nomad-lab.eu/ |
| Crystallography Open Database（COD，开放晶体结构） | https://www.crystallography.net/cod/ |
| Materials Cloud（含 CIF 文件） | https://www.materialscloud.org/ |

> 提示：核对晶格常数、键长、带隙时，优先查 Materials Project 对应材料的理论值（同为 PBE 泛函，可直接对比）。ICSD 给的是实验值，作参照时注意 DFT 系统误差（PBE 晶格常数通常高估 1~2%，带隙低估 ~30%）。

## 三、赝势（POTCAR）来源

| 用途 | 网址 |
|------|------|
| VASP 官方赝势（需正版许可，从 VASP Portal 下载） | https://www.vasp.at/ |
| VASP Portal（正版用户登录下载赝势） | https://www.vasp.at/portal/ |
| Materials Project 赝势说明 | https://docs.materialsproject.org/methodology/materials-methodology/pseudopotentials |

> 注意：POTCAR 受 VASP 许可协议保护，请从官方合法渠道获取，不要从第三方随意下载。Skill 只指导"如何按 POSCAR 顺序拼接"，不提供赝势文件本身。

## 四、文献值核验（结果物理合理性判断）

| 用途 | 网址 |
|------|------|
| 学术搜索引擎（查实验/理论参考值） | https://scholar.google.com/ |
| 预印本 arXiv（快速查最新计算文献） | https://arxiv.org/ |
| Web of Science / Scopus（需订阅） | https://www.webofscience.com/ |

## 五、使用原则

1. **优先官方**：参数、格式、报错 → VASP 官方 wiki 最权威。
2. **结构数据**：先 Materials Project（同泛函可直接对比），再 ICDS/COD（实验值参照）。
3. **物理量对比**：晶格常数、键长、带隙、磁矩 → Materials Project 理论值 + 文献实验值。
4. **诚实标注**：联网查到的参考值要写明来源网址，对比结论要说明是"同泛函对比"还是"跨泛函/实验对比"。
5. **无法访问时降级**：若某网站需订阅或访问受限，明确告知用户，并改用其他开放资源或标注"未核对"。

## 六、抓取不到时的处理铁律（必须停下来让用户找）

有些网站（典型如 VASP 官方 wiki）**浏览器能正常打开，但程序抓取会返回 403/反爬**。遇到这种情况，**禁止硬爬、禁止重试绕过、禁止凭空猜测内容**，必须：

1. **立即停下**，明确告诉用户"这个网页我抓取不到，需要你手动打开查看"。
2. **给出精确网址**：把要查的那个具体页面 URL 直接给用户（不要只给域名）。
3. **给出详细查找步骤**，分点写清楚用户打开后要看什么、找什么，例如：
   - 打开网址 → 在页面搜索框/浏览器查找（Ctrl+F）输入关键词 → 定位到哪一段
   - 如果是参数，告诉用户去页面哪个小标题下找
   - 如果是结构数据，告诉用户搜索框输入什么（如化学式、材料名）
4. **告诉用户把什么信息带回来**：明确列出需要用户复制回来的具体内容（如"请把 ENCUT 参数说明那一段的默认值和取值范围复制给我"）。
5. **拿到用户反馈后继续**：用户返回内容后，再据此继续校验/分析，绝不跳过这一步用猜的结论替代。

### 示例（遇到 VASP wiki 反爬时）

```
[需要你手动查一下]
我尝试访问 VASP 官方 wiki 的 ENCUT 参数页，但被网站反爬拦截（403），
请你手动打开以下网址帮我确认：

网址：https://www.vasp.at/wiki/index.php/ENCUT

查找步骤：
1. 用浏览器打开上面的网址
2. 页面加载后，按 Ctrl+F 搜索 "Default"
3. 找到 ENCUT 的默认值说明（通常写着 "Default: ENCUT = ..."）
4. 再看下面 "相关说明" 里 ENCUT 与 ENMAX 的关系描述

请把这两段文字复制回来给我：
① ENCUT 的默认值
② ENCUT 与 ENMAX 的关系说明

拿到后我会据此判断你的 ENCUT 设置是否正确。
```

### 需要用户手动查的常见情形

- VASP wiki 任何参数页（INCAR 参数、POSCAR/POTCAR/KPOINTS 格式说明）
- 需要登录/订阅的数据库（ICSD、部分 Materials Project 功能）
- 需要人工判断的页面（论坛帖子、教程截图、带公式的文档）
- 任何程序抓取失败但浏览器可访问的页面
