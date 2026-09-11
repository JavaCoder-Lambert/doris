# NULL-safe Join SQL 验证工具

用于**隔离测试集群**的跨版本 SQL 语义检查。这个工具执行 DDL/DML，会创建一个全新数据库并保留样本供检查；它不连接预设生产地址，不修改 SQL 拦截规则，也不会复用或清理已有数据库。

它是独立 SQL 诊断工具，不是 Doris 官方回归框架，不编译当前 checkout。连接哪个发行二进制，结果就只代表哪个二进制。测试失败时保留真实结果并以非零退出码退出，不会把错误输出改成预期。

## 运行

先启动可用的测试 FE/BE（单副本即可），准备 Python 3 和 PyMySQL。可以使用独立虚拟环境：

```bash
python3 -m venv /tmp/doris-nullsafe-venv
/tmp/doris-nullsafe-venv/bin/python -m pip install 'PyMySQL>=1.1,<2'

# 在仓库根目录运行；9030 是示例测试 FE 的 MySQL 端口。
# 如需密码，通过环境变量 DORIS_PASSWORD 提供，不要写入脚本。
/tmp/doris-nullsafe-venv/bin/python tools/null-safe-join/nullsafe_join_probe.py \
  --host 127.0.0.1 --port 9030 \
  --output /tmp/doris-nullsafe-results.json \
  --sql-output /tmp/doris-nullsafe-transcript.sql
```

默认数据库名称含 UTC 时间及微秒，也可用 `--database` 指定**尚不存在**的名称。`CREATE DATABASE` 不带 `IF NOT EXISTS`，遇到已存在数据库直接失败，不向其中写表。脚本不会自动删除测试数据。

需要创建库、表、写入样本和读取 FE/BE 节点信息的测试账号。连接、DDL 或变量设置失败也会记入 JSON 并返回非零退出码。

## 覆盖与解释

- 标量 NULL/非 NULL/空字符串真值检查。
- 20 组 Join/表达式场景，分别在 OFF、IN、BLOOM、MIN_MAX、IN_OR_BLOOM 下运行，共 100 组检查。
- Nullable 数字、字符串、Decimal、复合键、空 build、全 NULL build、INNER/LEFT/SEMI/ANTI，以及 CAST/CONCAT 产生 NULL 的键。
- 六字段 `GROUP BY COUNT(*)` 后用 `<=>` 回连的原结构，与窗口 `COUNT(*) OVER(PARTITION BY ...)` 改写对照。该项同时核对行内容、列名和列顺序。
- 两个补充场景覆盖真正复用的 CTE，以及 CTE 投影中的 Nullable 字符串表达式；缺失的完整业务 CTE 没有被假定为这些样本。
- Python 独立预期与 SQL 改写 oracle 双重检查，保留每条 EXPLAIN、实际值、预期值和执行时间。
- `runtime_filter_generated` 仅表示 EXPLAIN 中出现 RF 编号；不表示运行时完成了发布、接收和过滤。要证明 RF 实际生效需另看 Query Profile。

结果总字段 `passed=false` 时，先看 `errors` 区分环境失败，再看各 case 的 `error`、`passed`、`actual`、`oracle`、`expected`。本次精确 3.0.8 全部 100 项通过；4.1.4 和初始 4.1.1 对照均保留 20 项真实结果不一致，不应忽略后称为“全部通过”。完整范围见 [验证记录](../../docs/development/null-safe-join-validation.md)。

`passed` 只汇总 SQL 执行与结果，不自动判定节点健康或重启。检查崩溃时，应另行比对前后 `Alive`、`LastStartTime` 及进程/容器日志；多节点应按稳定节点 ID 采集和对应，不能仅按返回顺序配对。SQL 成功不等于 BE 从未重启。

这组样本没有大规模数据、多 BE 通信竞态和业务完整 `q_raw`，不能替代事故复现或性能压测。工具使用测试数据的确定性排序，查询耗时只作为排查记录。

## 查询改写和源码阅读

- [六字段唯一组合窗口查询](../../docs/development/six-key-unique-rows.sql)：接在既有 `q_raw` CTE 后使用，去掉不再引用的 `q_key_profile` CTE。辅助列名需保持不冲突。
- [调查结论与排查流程](../../docs/development/null-safe-join-investigation.md)
- [精确 3.0.8 与最新代码、发行版对照](../../docs/development/comparison-3.0.8.md)
- [候选修复与版本证据](../../docs/development/null-safe-join-candidates.md)
- [架构和二开导航](../../docs/development/architecture-guide.md)

后续若修改内核，请继续运行仓库预设的 `run-fe-ut.sh`、`run-be-ut.sh`、`run-regression-test.sh`；不要用这个 SQL 工具替代源码构建与官方测试。
