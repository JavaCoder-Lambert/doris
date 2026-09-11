# 2026-09-11 本地版本对照验证

**精确 3.0.8 的 100 项全部通过；最新正式版 4.1.4 的 100 项中 80 项通过、20 项结果错误。两版的六字段查询样本均通过，未复现 BE 崩溃。master 已含对应单字符串修复，但本次未构建运行 master。**

## 版本、环境与结果

| 测试对象 | FE / BE 实际报告的 Version | 检查结果 | 六字段三种形态 × 五种 RF 设置 |
|---|---|---|---|
| 用户精确版本 3.0.8 | `doris-3.0.8-rc01-09b0cc49a6` | **100 / 100 通过**，退出码 0 | 15 / 15 通过 |
| 最新正式版 4.1.4 | `doris-4.1.4-rc04-ad35a140c7f` | **80 通过、20 失败**，退出码 1 | 15 / 15 通过 |
| 初始对照 4.1.1 | `doris-4.1.1-rc01-b10073ad9ca` | 首轮 90 项 70 通过、20 失败；补充 10 项通过，累计 80 / 100 | 15 / 15 通过，分两次运行 |
| 最新 master `60042611fea1…` | 未构建二进制 | 源码已有 [#65975](https://github.com/apache/doris/pull/65975) 修复和回归样例 | 未运行 |

3.0.8 与 4.1.4 使用最终同一份 [nullsafe_join_probe.py](../../tools/null-safe-join/nullsafe_join_probe.py)，各自一次运行全部 20 × 5 = 100 项。两版环境/执行错误列表均为空，未跳过场景；4.1.4 的 20 项失败全是实际结果与独立预期不一致，没有将这些失败忽略。

- 本地 Colima Linux ARM64，隔离的单 FE / 单 BE；项目名 `doris-nullsafe-20260911`，仅绑定本机 SQL 端口 19330。使用合成数据和每次新建的数据库，无生产数据，没有改变生产拦截规则。
- 3.0.8、4.1.4 使用 Apache 官方完整 ARM64 FE/BE 发行包，分别核对官方 `.sha512` 成功。以现有 4.1.1 镜像提供基础系统/JDK，完整挂载各版本 FE/BE 目录，并分别保留独立数据目录。**实际引擎版本以上表 FE/BE 报告为准，不按基础镜像标签判断。**
- FE 容器限制 2 GiB，BE 3 GiB，依次启动版本；小样本结果不能作为生产资源需求或性能指标。
- `SELECT VERSION()` 返回 `5.7.99`，这是 MySQL 协议兼容版本，不能据此判断 Doris 版本。
- 工具保存 JSON 与 SQL transcript，含计划、行内容、列信息、独立预期和错误；不编译当前 checkout。

官方包来源：[3.0.8 ARM64](https://apache-doris-releases.oss-accelerate.aliyuncs.com/apache-doris-3.0.8-bin-arm64.tar.gz)、[3.0.8 校验文件](https://apache-doris-releases.oss-accelerate.aliyuncs.com/apache-doris-3.0.8-bin-arm64.tar.gz.sha512)、[4.1.4 ARM64](https://apache-doris-releases.oss-accelerate.aliyuncs.com/apache-doris-4.1.4-bin-arm64.tar.gz)、[4.1.4 校验文件](https://apache-doris-releases.oss-accelerate.aliyuncs.com/apache-doris-4.1.4-bin-arm64.tar.gz.sha512)。release / tag 身份与源码对应见 [精确版本比较](comparison-3.0.8.md)。

## 六字段唯一组合改写：两版各 15 / 15 通过

合成 `q_raw` 含 13 行，覆盖普通唯一组合、普通重复组合、带 NULL 的重复组合、全 NULL 重复组合、含多个 NULL 的唯一组合，以及 NULL 与真实空字符串的区别。

原结构按六个业务键分组计数，与原始行用六个 `<=>` 回连，筛选 `key_rows=1`。替代结构按相同六键计算窗口 `COUNT(*)`，筛选计数为 1，用 `* EXCEPT` 移除辅助列。分别测试直接读合成表、复用 CTE、CTE 内投影 Nullable 字符串表达式三种形态。

| RF 设置 | 3.0.8 三种形态 | 4.1.4 三种形态 | 原结构是否生成 RF |
|---|---|---|---|
| OFF | 全部通过 | 全部通过 | 否 |
| GLOBAL + IN（1） | 全部通过 | 全部通过 | 是 |
| GLOBAL + BLOOM（2） | 全部通过 | 全部通过 | 是 |
| GLOBAL + MIN_MAX（4） | 全部通过 | 全部通过 | 是 |
| GLOBAL + IN_OR_BLOOM（8） | 全部通过 | 全部通过 | 是 |

各次均保留 id `1,4,5,8,9,12,13` 共 7 行，结果与 Python 独立预期一致。每列内容、列名和顺序一致：`id`、六个业务键、`__q_present`；辅助计数列不泄漏。表达式 CTE 对 MSKU 加前缀并保留 NULL，其预期同样独立核对。

交付的 [six-key-unique-rows.sql](six-key-unique-rows.sql) 是查询尾部，在两版直接执行均通过 7 行 / 8 列核对；用合成表承接 `q_raw`，没有包含缺失的完整业务 CTE。

EXPLAIN 中原结构在 RF 开启时有 RF 描述；窗口方案是扫描、Exchange、排序、`VANALYTIC` 和过滤，没有原来的 Hash Join 或 Runtime Filter。这里的 RF 标记只证明计划生成，**不证明运行时发布、接收和过滤已生效**。窗口方案减少显式回连路径，但未经生产规模性能比较。

## 4.1.4 的 20 项错误：单字符串表达式 NULL 匹配丢失

以下四种形态分别在 OFF、IN、BLOOM、MIN_MAX、IN_OR_BLOOM 五种设置下失败；精确 3.0.8 对应全部通过：

1. `CAST(nullable_int AS STRING)` 作为单字符串 `<=>` 键的 INNER JOIN。
2. 同一表达式的 LEFT JOIN。
3. `CONCAT(COALESCE(nullable_string,''), CAST(nullable_int % 5 AS STRING))` 键的 INNER JOIN。
4. 同一表达式的 LEFT JOIN。

以关闭 RF 的 CAST INNER JOIN 为例：4.1.4 只返回 `(3,12),(7,12)`；Python 独立预期与 SQL 的 `a=b OR (a IS NULL AND b IS NULL)` oracle 还包含 `(1,10),(5,10)` 的 NULL 匹配。3.0.8 返回全部四行。标量 NULL-safe 比较真值表在两版都通过，因此仅测 `SELECT NULL <=> NULL` 不足以覆盖此问题。

4.1.1 初始实验也出现相同四类错误；独立重连后关闭 RF，各重跑三次仍一致。另查表达式投影与 `IS NULL`，NULL 输出本身正确，差异发生在 Join 匹配阶段。4.1.4 在 OFF 下同样失败，所以关闭 RF 并不能规避此结果错误。

此现象与上游 [#65975](https://github.com/apache/doris/pull/65975) 的单字符串 NULL 键残留 payload 修复吻合。最新 master 的 `hash_map_context.h` 已有 NULL 行规范空 `StringRef` 的处理及 `test_null_safe_eq_join_string_key.groovy` 回归样例；4.1.4 对应源码缺少该修复。3.0.8 的 nullable `<=>` 会绕开这条单字符串优化路径，不能机械回补新版本补丁。逐行源码证据见 [版本比较](comparison-3.0.8.md)。

**这是单字符串结果正确性问题，不是已复现的六字段 BE 崩溃。** 本次没有对 master 做修复前后构建实验，不能把“源码已有补丁”写成“当前编译产物测试已通过”，也不能保证其他 Join / RF 缺陷不存在。

## 原始证据、执行命令与边界

本地证据保存在仓库旁的 `../doris-investigation-20260911/`，不包含在 Git 提交中：

- `results/probe-3.0.8-complete.json` / `.sql`、`results/probe-4.1.4-complete.json` / `.sql`：最终同组 100 项记录。
- `results/probe-4.1.1-complete.json`、`results/probe-4.1.1-cte-supplement.json`、`results/recheck-4.1.1.json`：初始 90 项、补充 10 项、重连复核。
- `results/package-3.0.8.json`、`results/package-4.1.4.json`：包大小、预期/实际 SHA512 和解压位置。
- `results/documentation-sql-3.0.8.json`、`results/documentation-sql-4.1.4.json`：直接执行交付 SQL 的结果与计划。
- `results/containers-3.0.8-before-stop.json`、`results/containers-4.1.4-before-stop.json`：正常停机前容器状态。

从仓库根目录执行（每次连接当前启动的对应版本）：

```bash
../doris-investigation-20260911/.venv/bin/python \
  tools/null-safe-join/nullsafe_join_probe.py --host 127.0.0.1 --port 19330 \
  --output ../doris-investigation-20260911/results/probe-3.0.8-complete.json \
  --sql-output ../doris-investigation-20260911/results/probe-3.0.8-complete.sql
# 切换到隔离的 4.1.4 引擎后，用相应文件名运行同一命令。
```

各版本探针前后 BE 均为 Alive，`LastStartTime` 未变。容器正常停机前检查无 OOM、无自动重启。本次环境停止后保留发行包、配置和数据，便于复核；未操作生产或其他已有实验环境。

最终停止状态见 `results/containers-final-stopped.json`：本任务 FE/BE 均已停止。BE 的最终退出码 137、FE 的 143 发生于主动 `docker compose stop` 收尾阶段，不作为测试期间崩溃证据。

工具的 `passed` 只汇总 SQL 执行与结果，不自动判定节点重启。本文的进程结论另行比对节点状态和容器记录，不从 SQL 成功推断“没有崩溃”。

工具静态检查已通过 Python AST、`py_compile`、`--help`，确认 20 个唯一场景与六键预期列结构；交付文档的相对链接均可解析，`git diff --check` 与暂存差异检查通过。独立审阅重算了 CTE 预期与两版结果统计，并检查了建库碰撞、异常退出和真实失败保留。未新增或修改引擎实现。

初次尝试仓库预设命令在 JDK 17 前置检查阶段非零退出。当时只检查了系统 `java_home`，结论不完整：本机实际已安装由 jenv 管理的 Microsoft OpenJDK 17.0.19，路径为 `~/.jenv/versions/17`。后续通过命令级 `JAVA_HOME` / `JDK_17` 已解决，无需修改全局 Java 8。

后续使用与 4.1.4 匹配的框架、Thrift 0.16.0，成功执行 `./run-regression-test.sh --compile`（含 Java UDF），再执行 `./run-regression-test.sh --run -d query_p0/join -s test_null_safe_eq_join_string_key -parallel 1`。原版 4.1.4 的官方 suite 退出码为 1：`minimal_left_join` 预期 `[2,1]`、实际 `[2,NULL]`，与独立探针一致。该结果是原版错误的红测，不是修复后通过。日志保留于 `../doris-investigation-20260911/results/regression414-official-baseline.log`；4.1.4 补丁构建在独立目录 `../doris-4.1.4-nullsafe` 继续。

目前已知精确生产版本；仍未提供完整 `q_raw`、表类型、事故计划与 BE 崩溃堆栈。单 BE、小样本没有覆盖分布式 RF 合并、生命周期竞态、跨 block 大数据与生产资源压力，因此没有证明历史崩溃已修复，也没有评估升级兼容性。
