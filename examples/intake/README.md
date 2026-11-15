# 需求确认单示例（能力 0 的落地件）

这四份文件是 `examples/` 四个案例走到「生成输入文件之前」那一步时，**真正填出来的确认单**——不是示意片段，而是能被 `scripts/intake.py` 校验通过的完整文件。

| 确认单 | 对应案例 | 任务类型 / 档位 | 内容检查 | 为什么落在这个档 |
|---|---|---|---|---|
| `intake_si_relax.md` | 案例 1：Si 晶格常数 | relax / **simple** | 14/14 | 体相、非磁、中性、无缺陷、无吸附 → 没有条件项被触发 |
| `intake_graphene_dos.md` | 案例 2：石墨烯 DOS | dos / **full** | 24/24 | 二维层 → 触发真空层 + 偶极修正，精简档没有这两栏 |
| `intake_fe_magnetism.md` | 案例 3：BCC Fe 磁矩 | energy / **full** | 24/24 | 磁性=铁磁 → 触发初始磁矩；金属还要定展宽 |
| `intake_ni_wo3_gas_sensing.md` | 案例 4：Ni 掺杂 WO₃ | combo / **full** | 29/29 | 表面 + 掺杂 + 吸附 + 磁性 + 组合任务，五项全中 |

（上面的数字是 `--check --content-only` 的输出；加上「用户二次确认」一项后，通过数各加 1。）

四份单子刻意覆盖了分档的两个方向：**一份精简档说明什么时候不该啰嗦，三份完整档说明什么时候必须啰嗦**。看完这四份，基本就知道自己的课题该走哪一档了。

## 怎么用

### 只读：对照案例正文看每一栏为什么这么填

每份单子里都写了值 + 理由（比如"ENCUT 520，因为 Si ENMAX=245.3，1.3 倍约 319，520 留余量"）。案例正文里有更完整的上下文。

### 复现门禁：跑一次内容检查

```bash
python scripts/intake.py --check examples/intake/intake_si_relax.md --content-only
```

会看到：

```
门禁结论：通过（14/14 项检查通过）
内容齐全。下一步：python intake.py --preview examples/intake/intake_si_relax.md
把确认稿交给用户核对，取得同意后再 --approve。
```

### 补完二次确认：授权凭据不入库

`.gitignore` 排除了 `*.approval.json` 与 `*.preview.json`——它们是**某一次确认**的凭据，属于运行产物，不是需求内容。所以你 clone 下来拿到的是「内容齐全、等待用户确认」的状态，直接 `--check` 会看到：

```
[未授权] 用户二次确认　（授权）
       为什么：确认单还没交给用户过目并取得同意。内容齐全不等于用户同意生成 ——
               先 --preview 出确认稿，用户明确点头后再 --approve
```

这不是坏掉了，**这正是门禁在工作**。单子里的「用户确认」行也停在占位状态——它由 `--approve` 回写，而不是手填（那一行不参与指纹计算，所以就算先回写了内容，凭据也不会因此失效）。按三步走完：

```bash
python scripts/intake.py --preview examples/intake/intake_si_relax.md                # 1 出确认稿（给人看的那版）
python scripts/intake.py --approve examples/intake/intake_si_relax.md --by-user "确认生成"  # 2 你点头后记授权
python scripts/intake.py --check   examples/intake/intake_si_relax.md                # 3 → 通过（15/15）
```

四份一次跑完：

```bash
for f in examples/intake/intake_*.md; do
  python scripts/intake.py --preview "$f" > /dev/null
  python scripts/intake.py --approve "$f" --by-user "确认生成" > /dev/null
  printf "%-34s " "$(basename "$f")"
  python scripts/intake.py --check "$f" | grep 门禁结论
done
```

（注意通配符写 `intake_*.md`，别把本文件 `README.md` 也扫进去——它不是确认单，会被判"没有可识别的任务类型"。）

> `--by-user` 要填**你自己说的原话**。示例里用的是"确认生成""可以，就这样"这类说法——脚本只认明确的同意表述，遇到"嗯""知道了""你看着办"会判为未定并要求追问。

## 三个可以自己动手的验证

在副本上改一处、跑一次 `--check`，比读十遍规则更直观（**别直接改这四个文件本身**，改成 520→480 之后指纹对不上，你就得重新走一遍确认）：

| 改什么 | 期望看到 | 说明 |
|---|---|---|
| 把 `intake_si_relax.md` 的 `ENCUT：520` 改成 `480` | `[授权过期] 用户二次确认` | 你同意的是 520 那版，改过就得重新给你看 |
| 把 `intake_graphene_dos.md` 的 `门禁档位：full` 改成 `simple` | `[档位不符] 门禁档位` | 二维体系不能用精简档跳过「收敛测试」「需要验证的项目」 |
| 把 `intake_si_relax.md` 的 `磁性=非磁` 改成 `磁性=不确定` | `[未定] 关键物理` + `[档位不符]` + `[缺栏位] 初始磁矩` | 不确定不算回答；写了磁性就必须要初始磁矩 |

## 三个提醒

1. **确认单不是"填得越多越好"**。它只该出现在会改变生成结果的地方。能从句型直接读出来的（元素、原子数）不填，能合理默认的（SIGMA、EDIFF）写成"建议起点，待验证"。
2. **示例里的数值不是"标准参数"**。ENCUT=520、k=8×8×8 这些是针对示例体系给的建议起点，换体系必须重新做收敛测试。
3. **确认单只覆盖到"生成输入文件"**。生成 ≠ 提交作业；POTCAR 受许可限制，本仓库不提供。
