# 扩展 NULL-safe 字符串 Join 探针

`nullsafe_join_extended_probe.py` 补充现有 100 case 脚本，用于本次修复包的 SQL 语义验证。它不启动 Doris，只连接明确指定的 loopback 测试端口；执行前必须确认这是隔离测试集群。不要通过生产端口转发运行。

## 范围与判定

- 固定种子 `20260911`，左表 256 行、右表 40 行、全 NULL build 表 33 行，以及一个空 build 表。`batch_size=16`、单 pipeline task、单 bucket；输入规模超过 batch size，包含交错 NULL、真实空串、短字符串和长字符串。
- 15 个场景 × OFF / GLOBAL BLOOM × broadcast / shuffle = **60 个查询**。包括单侧及双侧 CAST / CONCAT、INNER / LEFT、NULL 与空串、两侧 Nullable 不对称、NULL 常量、两个 Nullable 键、全 NULL / 空 build。原脚本已经覆盖 IN / MIN_MAX / IN_OR_BLOOM，这里不重复完整 RF 类型矩阵。
- Python 用独立的整数转字符串、非负整数取模、字符串拼接和 `None` 相等规则生成预期；不执行 Doris SQL 改写作为 oracle。先完整读回插入数据，再比较 ID 对的多重集合，重复匹配、漏行和额外行都会失败。每个查询最多返回 `256 × 40` 行，不使用 `LIMIT` 截断结果。
- 普通场景要求 EXPLAIN 中实际存在一个 `VHASH JOIN`，broadcast 对应 `BROADCAST`，shuffle 对应 `PARTITIONED`，并检查物理等值键个数和所需 `<=>`。两键用例不能退化成一个物理键。非对称 Nullable 允许优化器将 `<=>` 化简为 `=`。
- NULL 常量和空 build 允许优化器消除 Hash Join，仍比较语义并保留真实计划；这些通过不能作为 Hash Join 路径覆盖证据。OFF 计划不得出现 RF；BLOOM 模式的单侧 CAST INNER 用例要求实际生成 RF。其他表达式及 LEFT JOIN 可能没有可用 RF，不据设置值推断过滤器生效。
- 总体通过还要求全部 60 个查询完成、计划覆盖检查通过、BE 成员不变、运行前后均 Alive、Version 和 LastStartTime 不变。错误、少跑、连接失败、建库碰撞和计划不符合要求都返回非零。

EXPLAIN 证明选择的物理计划和 RF 生成，不能证明运行时 RF 已到达或实际过滤行数。小 `batch_size` 与多行输入用于覆盖批次边界；实际每个算子的块数仍需 Profile 验证。此脚本不是压力测试、RPC 回调生命周期测试，也不能证明最初生产 BE 事故的原因。

## 运行

Python 3.9+ 可先做不连接数据库的自检，无须 PyMySQL：

```bash
python3 tools/null-safe-join/nullsafe_join_extended_probe.py --self-check
```

仅数据库执行需要 `PyMySQL>=1.1,<2`。可复用现有测试 venv，缺依赖时在独立 venv 安装，不修改 Doris 构建依赖：

```bash
python3 -m venv /tmp/doris-nullsafe-extended-venv
/tmp/doris-nullsafe-extended-venv/bin/pip install 'PyMySQL>=1.1,<2'
mkdir -p /tmp/doris-nullsafe-extended-results
/tmp/doris-nullsafe-extended-venv/bin/python \
  tools/null-safe-join/nullsafe_join_extended_probe.py \
  --test-cluster --port 19330 \
  --output-dir /tmp/doris-nullsafe-extended-results
```

端口 `19330` 只是示例，应替换为本轮修复包测试集群的映射端口。默认用户为 `root`；密码只从 `DORIS_PASSWORD` 环境变量读取。需要建库建表、写入、查询权限，以及 `SHOW FRONTENDS` / `SHOW BACKENDS` 所需管理权限。`--expect-be-version <片段>` 可以在任何 DDL 前校验全部 BE 的版本字符串；修复源码的基线相同不代表构建产物相同，应同时保存包摘要和构建提交。

数据库默认名称包含 UTC 微秒时间。可用 `--database` 固定本次名称，但它**必须不存在**：脚本只执行 `CREATE DATABASE`，没有 `IF NOT EXISTS`，也没有删除或复用数据库的逻辑。库与表全部保留供复核，需要时由操作者单独清理。它不会撤销失败前已创建的新库。

输出目录必须已存在。输出为 `<database>.json` 和 `<database>.sql`，均以独占新建方式打开，已有文件不会被覆盖。JSON 保留固定种子、输入摘要、BE/FE 版本与健康状态、全部计划，以及每次查询的预期/实际行数、SHA-256、前 12 行、缺失和额外行摘要。SQL 文件记录实际执行的设置、DDL、合成数据 INSERT、EXPLAIN 和查询；不记录密码。数据库运行尚未完成时不要依据部分输出判定通过。

如果只有 Python 自检通过、没有执行目标包，交付中应明确写“离线自检通过，目标二进制的 60 查询验证待运行”。不要把它标成修复包绿测。

## 原版 4.1.4 实测红测（2026-09-11）

本机隔离测试集群的 BE 版本为 `doris-4.1.4-rc04-ad35a140c7f`。完整执行 60 个查询，**32 通过、28 语义失败**，全部计划检查通过，`errors=[]`。四张合成表的完整读回均与 Python 固定种子数据一致，fixture SHA-256 为 `ee62ed09b394a50b57c49719b8ee84080f8643a63684507b99e2045ca63d885e`。

下面每种场景都在 OFF / BLOOM × BROADCAST / PARTITIONED 四种组合中得到同样差异。缺失与额外行的**完整列表 SHA-256** 已与独立生成的 NULL 键匹配对复核，不只检查前 12 行样例。

| 场景 | 每个查询缺失的应匹配 ID 对 | 每个查询额外结果 |
| --- | ---: | ---: |
| CAST 单侧 INNER / LEFT | 192 = 48 个 NULL probe × 4 个 NULL build | INNER 为 0；LEFT 多 48 个未匹配补 NULL 行 |
| CONCAT 单侧 INNER / LEFT | 192 = 48 × 4 | INNER 为 0；LEFT 多 48 个未匹配补 NULL 行 |
| CONCAT 双侧 INNER | 288 个 NULL/NULL 应匹配对中缺失 202 个 | 0 |
| 全 NULL build INNER / LEFT | 1584 = 48 个 NULL probe × 33 个 NULL build | INNER 为 0；LEFT 多 48 个未匹配补 NULL 行 |

双侧 CONCAT 缺失的 202 对恰好对应两侧 `COALESCE(prefix, '')` 不同的 NULL 键行；另外 86 个 NULL 对仍匹配。这与 NULL 嵌套字符串残留值影响匹配的源码机制一致，但没有依靠该机制生成语义 oracle。双侧 CAST、真实空串、Nullable 不对称、两个 Nullable 键、NULL 常量和空 build 共 8 个场景的 32 个查询通过。NULL 常量的实际计划没有 Hash Join，不能计入该内核路径覆盖；空 build 的实际计划仍保留 Hash Join。

BE `BackendId=1789091977362` 前后不变，均 `Alive=true`，报告的 `LastStartTime` 均为 `2026-09-11 03:23:10`，Version 不变且 ErrMsg 为空，健康检查通过。此轮复现了 NULL 匹配漏行，没有观察到 BE 重启，不能据此归因最初生产事故。

本地证据文件为调查目录 `results/nullsafe_ext_414_baseline_20260911.json` 和同名 `.sql`，测试库 `nullsafe_ext_414_baseline_20260911` 保留供复核。**这是原版红测，修复后 Linux x86_64 包的 60 查询绿测仍须单独执行**；复测不得修改 oracle 或删减失败场景。

## 4.1.4 源码依据

- `fe/fe-core/src/main/java/org/apache/doris/qe/SessionVariable.java` 的 `checkBatchSize` 接受 1–65535；`batch_size`、`parallel_pipeline_task_num`、`disable_join_reorder` 和 RF 变量均已有定义。
- `regression-test/suites/query_p0/join/test_hash_join_probe_side_zero_copy.groovy` 已采用小 batch、禁用 Join 重排、单 pipeline task 及 `[broadcast]`。`test_join_with_const.groovy` 已采用 `[shuffle]`。
- `fe/fe-core/src/main/java/org/apache/doris/planner/HashJoinNode.java` 的 `getNodeExplainString` 输出 `join op: ... (分布)` 和 `equal join conjunct`，探针据此验证真实计划。
- `be/src/exec/scan/scanner.cpp` 使用 `state->batch_size()` 控制合块，`be/src/exec/operator/hashjoin_probe_operator.cpp` 将其传入 probe 并检查输出行数上限。构造多于一个批次的输入，不能单靠行数声称已经测得内部块数。
