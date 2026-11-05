# 版本记录

本项目遵循语义化版本规范（Semantic Versioning）。

## [1.0.0] - 2026-11-05

### 初始版本
- 新增 7 大能力：资源调度、输入文件生成与校验、耗时估算、预计算、实时监控转 CSV、结果分析与报告、智能路由。
- 新增参考文档 7 篇：VASP 基础、输入校验、联网资源清单、资源调度、耗时估算、预计算、结果分析。
- 新增脚本 5 个：generate_inputs.py、validate_inputs.py、estimate_time.py、monitor_to_csv.py、analyze_results.py。
- 新增使用案例 3 个：Si 晶格常数、石墨烯 DOS、BCC Fe 磁矩。
- 新增 README 使用手册。
- 新增铁律：遇到抓取不到但浏览器可打开的网页（如 VASP wiki），停下让用户手动查并给出详细步骤，不硬爬不猜测。

## [1.0.0] 追加 - 2026-11-05

- 新增使用案例 4：`example_nidoped_wo3_gas_sensing.md` — Ni 掺杂 WO₃ 气体传感机理的 DFT 计算全流程。基于真实科研需求（4 个计算模型 + 3 个核心科学问题），覆盖 7 大能力全流程，含资源调度、输入生成、校验、耗时估算、预计算、监控、结果分析，并结合网上文献给出预估结果与判读方法。因编写机器未装 VASP，所有数值为文献参考值，用于演示判读逻辑。

## [1.1.0] - 2026-11-05

### 新增能力 8：环境与性能测试
- 新增参考文档 `references/environment-test.md`：说明冒烟测试 + 性能基准两步走、判读标准、常见失败排查、反哺耗时估算的用法。
- 新增脚本 `scripts/test_environment.py`：生成最小测试体系、打印运行命令、解析 OUTCAR 提取单步耗时，结果可喂给 `estimate_time.py` 做实测校准。
- SKILL.md：能力从 7 个扩展到 8 个，新增路由信号（"VASP 能不能用""性能测试""benchmark"等），总工作流首步改为环境测试，新增核心铁律第 7 条（正式计算前先做环境测试）。
- README.md：能力表、目录结构、脚本用法、核心铁律同步更新。

## [1.2.0] - 2026-11-05

### 引入可选增强库（ASE / pymatgen）
- 新增内部工具模块 `scripts/_libs.py`：统一检测 ASE/pymatgen 是否可用，供各脚本复用「可选增强 + 降级」判断。
- 新增参考文档 `references/external-libs.md`：说明 ASE/pymatgen 的定位、安装（可选）、各脚本增强/降级对照、常见坑。
- `generate_inputs.py`：新增 `--ase` 模式，装 ASE 后可一键构建 bulk/fcc111/fcc100/bcc111/graphene 结构并标准写 POSCAR；未装则降级到手写结构。
- `validate_inputs.py`：装 pymatgen 后额外做结构合法性 + 原子重叠检查；未装则静默跳过，内置校验不受影响。
- `analyze_results.py`：新增 `--vasprun` 参数，装 pymatgen/ASE 后读 vasprun.xml 拿全量能量/力/带隙/磁矩/晶格；失败降级到正则解析 OUTCAR/OSZICAR。
- SKILL.md、README.md 同步说明可选库与降级策略。

### 新增 VASP 计算验证测试套件
- 新增 `tests/input_files/`：8 套测试输入文件，覆盖单点能、结构优化、自旋极化（磁性）、多元素体系、态密度 DOS、能带结构六大计算模块，每个测试都附有公开资料参考值，跑完对比即可验证 VASP 是否正确。
- 8 个测试：Si 单点能、Si 结构优化、bcc Fe 磁性、fcc Ni 磁性、MgO 优化、NaCl 优化、Si DOS、Si 能带。全部为最小体系，单测几秒到 1 分钟，合计 5 分钟内跑完。
- 所有参考值来自公开资料（VASP 官方教程/workshop、Kitchin 教程、AM05 文献、diva-portal 博士论文等），并明确区分「PBE 参考值」与「实验值」，避免拿实验值直接对比导致的误判。
- 每个目录含 INCAR/POSCAR/KPOINTS + POTCAR.README（赝势受 VASP 许可保护，需用户自备，README 说明拼接方法）。
- 主说明文档 `tests/input_files/README.md` 含运行方式、各测试参考值与合格范围、判读方法、来源链接。

### 修复校验脚本精度判定 bug
- `validate_inputs.py` 原精度检查用 `repr(float)` 判断小数位数，Python 会自动去掉末尾的 0（如 5.430000000 → 5.43），导致标准 9 位小数坐标被误报「精度不足」。
- 改为直接读 POSCAR 原始文本中的浮点数字符串判断小数位数，消除误报。
