# Doris `<=>`、Nullable、Hash Join / Runtime Filter 崩溃候选

核查日期：2026-09-11。本文件记录 Apache Doris 官方 GitHub PR、提交、差异与发布说明的候选研究；未访问生产。用户后续已确认精确版本 `doris-3.0.8-rc01-09b0cc49a6`，下列六组旧修复均已在该版本源码中找到对应处理，详见 [精确版本比较](comparison-3.0.8.md)。隔离发行包实验另见 [验证记录](null-safe-join-validation.md)。

**最新证据优先：用户补充截图显示 `1105 - sql match regex sql block rule: block_null_safe_equal`。这条错误是 FE 的 SQL 正则阻止规则命中，不能作为该 SQL 导致 BE 崩溃的证据。** 以下历史 crash 候选只在另有 BE 崩溃日志时用于比对，不是对截图错误的根因解释。

已有多项相关修复，且若干修复在 3.0.0～3.0.4 已进入版本线；无法凭“用了 `<=>` 后 BE 崩溃”确定命中哪一项。SQL 中存在 NULL、表达式的 nullable 属性、运行时 ColumnNullable 包装、C++ 空指针是四件不同的事。必须用实际 BE 版本/commit、崩溃堆栈及计划中的类型区分。

## 截图错误的源码定位与只读核查

3.0 分支核查快照：`4090f6c40d2238b7aff73185bcada48f96fbd6be`。

- [SqlBlockRuleMgr.java:256-265](https://github.com/apache/doris/blob/4090f6c40d2238b7aff73185bcada48f96fbd6be/fe/fe-core/src/main/java/org/apache/doris/blockrule/SqlBlockRuleMgr.java#L256-L265)：规则 `Enable=true` 且 `rule.getSqlPattern().matcher(originSql).find()` 命中时，原样抛出截图中的 `AnalysisException`。
- [StmtExecutor.java:843-853](https://github.com/apache/doris/blob/4090f6c40d2238b7aff73185bcada48f96fbd6be/fe/fe-core/src/main/java/org/apache/doris/qe/StmtExecutor.java#L843-L853)：Nereids 路径先 `planner.plan`，再 `checkBlockRules()`，之后才进入 `handleQueryWithRetry`。因此该错误表示正式查询执行分发前被拒绝；不能据此推断 BE 空指针。
- [ShowSqlBlockRuleStmt.java](https://github.com/apache/doris/blob/4090f6c40d2238b7aff73185bcada48f96fbd6be/fe/fe-core/src/main/java/org/apache/doris/analysis/ShowSqlBlockRuleStmt.java)：`SHOW SQL_BLOCK_RULE [FOR rule_name]` 检查全局 `ADMIN` 权限。[官方 3.x 中文文档](https://doris.apache.org/zh-CN/docs/3.x/sql-manual/sql-statements/data-governance/SHOW-SQL_BLOCK_RULE/)同样给出语法与 ADMIN 要求。

以下仅为待执行的只读命令，本次没有连接线上执行：

```sql
SHOW SQL_BLOCK_RULE FOR block_null_safe_equal;
-- 有需要再查看全部规则
SHOW SQL_BLOCK_RULE;
```

关注 `Sql`（实际正则）、`Enable`、`Global`、`SqlHash`。`Global=false` 时还需核对相关用户绑定规则。规则名称只是人为命名；在读取 `Sql` 前，不能仅凭 `block_null_safe_equal` 断言实际正则精确为 `<=>`。本次不关闭、删除或绕过规则。

截图 SQL 的已知形状是：`q_key_profile` 按六个键分组计数，再与 `q_raw` 用六个 `<=>` 内连接，保留 `key_rows=1`。此处 `<=>` 使两边 NULL 键能够匹配；简单改成 `=` 会改变这类记录的结果。是否可用保留语义的其他 SQL 实现，应按完整查询另行验证，不能把错误消失当作 BE 故障修好。

## 证据怎么理解

- `merged=true` 与 merge commit：证明某个 PR 的改动已合并到其 `base` 分支。
- `base=branch-3.0` 的已合并 backport：证明修改进入 3.0 分支；单凭这一项不能指定首个正式发布包。
- `dev/3.0.4-merged` 等标签：维护者的发布归属记录；不是现场二进制验证。
- 正式版本发布说明明确列出 PR：可以说“该版本发布记录包含修复”。仍不能证明用户正在运行该构建，或所有同类问题均已解决。
- 本次没有逐个拉取所有历史 release tag 做祖先遍历，所以不将标签单独写成“全系列绝对最早修复版本”。本地分支代码核查由主报告补充。

## 最有鉴别力的候选

### 1. `<=>` 两侧都被判定为非 nullable：#36263

- 上游：[PR #36263](https://github.com/apache/doris/pull/36263)，2024-06-14 合入 `master`。
- 提交：[56ac97616d3f33c70b3e375b009c6316de667ada](https://github.com/apache/doris/commit/56ac97616d3f33c70b3e375b009c6316de667ada)。
- 核心文件：`be/src/pipeline/exec/hashjoin_build_sink.cpp`、`hashjoin_probe_operator.cpp`。
- 根因与修改：合法的 FE 计划可能保留 `EQ_FOR_NULL`，但两侧表达式均为 non-nullable。BE 仍选择 null-safe 路径，导致哈希表/实际列表示不一致。修改后，只有 opcode 为 `EQ_FOR_NULL` **且至少一侧 nullable** 时启用 null-safe 处理；两侧都非 nullable 时按普通等值处理。
- 鉴别堆栈：`IColumn::get_raw_data → MethodKeysFixed<..., false>::pack_fixeds → init_serialized_keys → ProcessHashTableBuild`；PR 附带构建侧 coredump 栈。它是列表示/哈希方法不一致，不是“SQL NULL 就等于 C++ nullptr”。
- 版本证据：PR 标签 `dev/3.0.0-merged`、`dev/2.1.5-merged`；[2.1.5 发布记录](https://github.com/apache/doris/issues/38111)明确列出 #36263。[backport #37073](https://github.com/apache/doris/pull/37073)于 2024-07-02 合入 `branch-2.1`，commit `6789f5bc80db8742efbf54ac2a5562325914ff91`。
- 排除/降权条件：故障构建已含该条件，或实际崩溃位于 RF 发布/扫描路径而非 fixed-key build；此时不能继续把 #36263 当已定位根因。

### 2. 构建列被转换为 nullable，但哈希表类型未同步：#32623

- 上游：[PR #32623](https://github.com/apache/doris/pull/32623)，2024-03-21 合入 `master`。
- 提交：[4127f452c49ce3df8d949450b106bd0987333adb](https://github.com/apache/doris/commit/4127f452c49ce3df8d949450b106bd0987333adb)。
- 核心文件：`hashjoin_build_sink.cpp`、`vec/exec/join/vhash_join_node.cpp`、`vec/common/hash_table/hash_map_context_creator.h`。
- 根因与修改：某些 `<=>` 计划需要将 build key 从 non-nullable 转成 nullable；原来哈希表选型仍使用未转换的表达式类型。修复在 `try_get_hash_map_context_fixed` 之前按 `_should_convert_to_nullable` / `_should_convert_build_side_to_nullable` 生成实际类型列表。
- 鉴别证据：PR 的明确错误为 `Column Nullable(Int64) is not a contiguous block of memory`，随后 `IColumn::get_raw_data → MethodKeysFixed<..., false>::pack_fixeds`。需继续核对转换标记，才能与 #36263 区分。
- 版本证据：[2.1.1 发布记录](https://github.com/apache/doris/issues/32736)明确列出 #32623；另有 `dev/2.0.8-merged` 标签及 [#32821](https://github.com/apache/doris/pull/32821)。这属于 3.x 之前已经出现过的修复，不能只根据 3.x 故障症状假定它仍缺失。

### 3. RF 将非 nullable argument 强转为 ColumnNullable：#34602

- 上游：[PR #34602](https://github.com/apache/doris/pull/34602)，2024-05-10 合入 `master`。
- 提交：[640f88471a8df0d9a676880c59ce906079b7776c](https://github.com/apache/doris/commit/640f88471a8df0d9a676880c59ce906079b7776c)。
- 文件/函数：`be/src/vec/utils/util.hpp`，`change_null_to_true`。
- 根因与修改：存在 argument 不代表它有可读取的 null map。原代码在 `argument != nullptr` 时直接按 ColumnNullable 读取；修复改为 `argument != nullptr && argument->has_null()`，同时将结果列分支由 `is_nullable()` 改为 `has_null()`。
- 版本证据：PR 标签 `dev/3.0.0-merged`。本次未取得单独的 3.0.0 发布说明条目。
- 鉴别条件：堆栈/内联位置应涉及 `change_null_to_true` 与 RF Boolean 结果规范化。若只是 NULL 数据很多，或纯哈希表构建函数崩溃，不足以匹配。

补充同类旧问题：[PR #33869](https://github.com/apache/doris/pull/33869)，commit [d1a10ae6a6c11beadf6fa88a0671093c3275eb27](https://github.com/apache/doris/commit/d1a10ae6a6c11beadf6fa88a0671093c3275eb27)，2024-04-19 合入 master。`hybrid_set.h` 的 StringSet / StringValueSet 将 `nullmap != nullptr || !nullmap[i]` 改成 `nullmap == nullptr || !nullmap[i]`，避免对空 nullmap 解引用。精确栈是 `_insert_fixed_len_string → RuntimePredicateWrapper::insert_fixed_len/insert_batch → VRuntimeFilterSlots::insert`；其回归 SQL 使用普通 `=`，因此不能把它称为 `<=>` 专属 bug。未单独确认首个发布版本。

### 4. RF 异步回调使用已析构对象：#38058

- 上游：[PR #38058](https://github.com/apache/doris/pull/38058)，2024-07-18 合入 `master`。
- 提交：[829de217d6b1d2f7e13359e3031109e9ded82382](https://github.com/apache/doris/commit/829de217d6b1d2f7e13359e3031109e9ded82382)。
- 核心文件：`be/src/exprs/runtime_filter.cpp`，`SyncSizeClosure` / `IRuntimeFilter::send_filter_size`。
- 根因与修改：同步 filter size 的 RPC 返回之前，`IRuntimeFilter` 可能已析构；closure 原来保存裸 `_filter` 指针。修复持有 `RuntimeFilterContextSPtr` 和提前生成的 debug string，避免回调再解引用已释放的 filter。
- 版本证据：PR 标签 `dev/3.0.1-merged`、`dev/2.1.5-merged`；另有 [backport #38093](https://github.com/apache/doris/pull/38093)。本次未以发布说明单独确认 3.0.1 条目。
- 鉴别条件：RF size RPC 异常/结束回调、取消或生命周期竞争，与 hash key 中 SQL NULL 无必然关系。只有数据相关的稳定表达式崩溃应降低该候选优先级。

### 5. VARCHAR / CHAR Runtime Filter 类型不一致：#43758

- 上游：[PR #43758](https://github.com/apache/doris/pull/43758)，2024-11-13 合入 `master`。
- 提交：[0aba1ed7784decace38efd9f97d677da75dc6bcf](https://github.com/apache/doris/commit/0aba1ed7784decace38efd9f97d677da75dc6bcf)。
- 文件/修改：`be/src/exprs/create_predicate_function.h` 的 `create_olap_column_predicate` 已创建与目标 PrimitiveType 对齐的 `filter_olap`，却错误把原始 `filter` 传给 `BloomFilterColumnPredicate<PT>`；修复传入 `filter_olap`。
- 鉴别证据：`Bad cast from BloomFilterFunc<15>* to BloomFilterFunc<10>*`，调用链包括 `BloomFilterColumnPredicate`、`create_olap_column_predicate`。需核查两侧字符串具体类型及计划隐式转换。
- 3.x 直接 backport：[PR #43919](https://github.com/apache/doris/pull/43919)，2024-11-14 合入 `branch-3.0`，commit [62801e3303c3dbe1229f6687edc8e63e72b2a6f7](https://github.com/apache/doris/commit/62801e3303c3dbe1229f6687edc8e63e72b2a6f7)。
- 发布证据：[3.0.3 发布记录](https://github.com/apache/doris/issues/44522)明确列出 #43758 和 #43919；PR 也有 `dev/3.0.3-merged`。这是可以明确写“3.0.3 发布记录已包含”的候选。
- 排除/降权条件：RF 不涉及 VARCHAR/CHAR 边界，或崩溃不是 BloomFilterFunc 类型断言。

### 6. RF 禁用释放 bloom_filter_func 后仍读取：#47034

- 上游：[PR #47034](https://github.com/apache/doris/pull/47034)，2025-01-16 合入 `master`。
- 提交：[3749b7b8e0a43978910a078aaf59b10447cc2672](https://github.com/apache/doris/commit/3749b7b8e0a43978910a078aaf59b10447cc2672)。
- 文件/函数：`be/src/exprs/runtime_filter.cpp`，`RuntimePredicateWrapper::get_build_bf_cardinality`。
- 根因与修改：#46789 允许在 RF disabled 后释放内存，之后函数仍仅按 filter enum 决定是否解引用 `bloom_filter_func`。修复直接判断真实指针：`_context->bloom_filter_func && _context->bloom_filter_func->get_build_bf_cardinality()`。PR 描述的触发实例使用 `fuzzy_disable_runtime_filter_in_be`；不能假定生产也启用了该测试开关。
- 精确崩溃栈：`RuntimePredicateWrapper::get_build_bf_cardinality → IRuntimeFilter::need_sync_filter_size → VRuntimeFilterSlots::send_filter_size → HashJoinBuildSinkLocalState::close`；地址 `0x0`，null pointer of `BloomFilterFuncBase`。
- 3.x 直接 backport：[PR #47052](https://github.com/apache/doris/pull/47052)，2025-01-16 合入 `branch-3.0`，commit [44e7be32a02dd38898c95d552d427faa94c20732](https://github.com/apache/doris/commit/44e7be32a02dd38898c95d552d427faa94c20732)。原 PR 标签 `dev/3.0.4-merged`、`dev/2.1.8-merged`；本次未取得正式 3.0.4 发布说明单列此 PR 的证据。
- 本次先核对 3.0 分支 HEAD `4090f6c40d2238b7aff73185bcada48f96fbd6be`，后核对精确 3.0.8 提交 `09b0cc49a60ffdd444df3e40e5f3dc180299b561` 的 `runtime_filter.cpp`，均含该空指针检查。不能据此把其他 RF 崩溃也判为已修复。

## 容易误判为本次问题的较新修复

| 上游记录 | 实际修复 | 与当前判断的边界 |
|---|---|---|
| [#62627](https://github.com/apache/doris/pull/62627)，[99877f5ce9636f9c92e8cfbed465f0034d76de13](https://github.com/apache/doris/commit/99877f5ce9636f9c92e8cfbed465f0034d76de13)，2026-05-13 | RF 给 target 包 CAST 时，把 child nullable / child type / destination type 传反；修正 `RuntimeFilterTranslator` | 只有含该三参 `CastExpr` / `Cast.castNullable` 错误调用的代码才匹配。本次核对的 3.0 HEAD `4090f6c40d2238b7aff73185bcada48f96fbd6be`、3.1 HEAD `a3e9f66a1106a4208e6855ffef9e0fb8b3c981ff` 是两参构造，没有本 PR 修复的三参错误代码。不能直接归因于 3.x。 |
| [#62056](https://github.com/apache/doris/pull/62056)，[3160231a3e12ad9ba27be5fe63d8aa36841c893e](https://github.com/apache/doris/commit/3160231a3e12ad9ba27be5fe63d8aa36841c893e)；[4.1 backport #63256](https://github.com/apache/doris/pull/63256)、[4.0 backport #63257](https://github.com/apache/doris/pull/63257) | broadcast/shared hash table builder 提前终止却发 signaled，non-builder 从 map 取得空 RF wrapper，`RuntimeFilterWrapper::set_state` 崩溃；改为 `_terminated` 时不发完成信号，并对 wrapper/map 查找返回明确错误 | 两个 backport 的 base 分别明确是 4.1 / 4.0；PR 将共享 wrapper 路径追溯到 #49556。3.x 需先证实存在相同重构与时序，不能按名字套用。 |
| [#66442](https://github.com/apache/doris/pull/66442)，[38fb05eaaab72739b34c942503bb0000f0967dd5](https://github.com/apache/doris/commit/38fb05eaaab72739b34c942503bb0000f0967dd5)，2026-08-06 | TopN comparison 的 `ColumnConst(ColumnNullable)` 结果需递归规范化，按 NULLS FIRST/LAST 决定过滤 | 属于 TopN predicate、constant nullable result；不是 `<=>` Hash Join 崩溃的直接证据。 |
| [#65975](https://github.com/apache/doris/pull/65975)，[e5b67e095b4fd40acd656ad25aed6bf5d8b52718](https://github.com/apache/doris/commit/e5b67e095b4fd40acd656ad25aed6bf5d8b52718)，2026-07-30 | 单字符串 null-safe hash key 在 null row 中残留表达式字节，改为规范空 StringRef，让 NULL 与 NULL 正确匹配 | 修复的是结果错误/随块分布改变；不是 crash。master 当前已含，不能用它解释 BE 退出。 |

## 最新通用 RPC / Exchange 候选

补充最新通用执行链候选：[#67755 / 9125fd692271](https://github.com/apache/doris/commit/9125fd692271ef6292a73000f4f95d53ecb24ee3)在调用业务回调前清空 RPC request attachment，并为每次 Exchange RPC 创建独立 callback/Controller/response，避免重入时复用仍在调用栈上的状态；没有修改 NULL-safe key 算法。精确 3.0.8 仍有 [Channel 复用 callback 并 Reset Controller](https://github.com/apache/doris/blob/09b0cc49a60ffdd444df3e40e5f3dc180299b561/be/src/vec/sink/vdata_stream_sender.h#L179-L187)、[成功回调再次发送 RPC](https://github.com/apache/doris/blob/09b0cc49a60ffdd444df3e40e5f3dc180299b561/be/src/pipeline/exec/exchange_sink_buffer.cpp#L255-L277)，及 [closure 在回调后读取 Controller/response，未提前清空 attachment](https://github.com/apache/doris/blob/09b0cc49a60ffdd444df3e40e5f3dc180299b561/be/src/util/ref_count_closure.h#L94-L105)的模式。它可作为“master 已处理、3.0.8 有相关代码模式”的独立候选；没有生产堆栈或竞态复现，不能认定历史六键崩溃同源，单 BE 语义测试通过也不能排除此竞态。

后续 4.1.4 验证补充：相同 ASAN ELF 在真正 x86_64 Linux 内核上已完整执行定向测试，四个新增 RPC 用例分别复现 response 污染、Controller 污染、回调前 attachment 未清理及 callback 销毁后 attachment 未清理。它们是确定性的回调重入/资源契约测试，不是网络并发或用户事故复现。修复后的运行状态、精确 ELF/XML 摘要见 [4.1.4 交付记录](4.1.4-null-safe-fix.md)。

## 下一步如何快速区分

1. 保留现场 FE 与每台 BE 的完整版本和 git commit；当前已知用户报告的 `3.0.8-rc01-09b0cc49a6`，仍需按节点核对是否混部。不要把 MySQL 协议兼容版本 `5.7.99` 当作 Doris 版本。
2. 取得同一 query ID 的第一份 fatal 日志/信号栈，保留 `SIGSEGV` / `SIGABRT`、地址、第一处 Doris 函数以及附近函数；常见的 signal handler / glog 帧不足以定位。
3. 对原 SQL 做只生成计划的 EXPLAIN，确认哪一组 `<=>` 实际成为 Hash Join key，是否有 CAST、nullable 转换、BROADCAST、RF producer/consumer。表 DDL 允许 NULL 并不等于经过投影和外连接后的表达式 nullable。
4. 根据堆栈先挑一项回归，再在隔离环境做 RF 开/关或 join 方式对比。RF 关闭后不复现只能提高该路径的怀疑度，不能证明 NULL 语义没有问题；不要把 `<=>` 全量替换成 `=`，两边均 NULL 的匹配结果会改变。

当前已知精确版本、截图中的查询片段和规则拦截错误，尚未取得完整现场 SQL、EXPLAIN 与 BE 栈。上述 crash 项均为可核对候选，不是生产根因结论，也不是建议直接升级 master。
