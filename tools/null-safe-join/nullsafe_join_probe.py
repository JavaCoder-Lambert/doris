#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Small Doris <=> smoke probe; writes only a newly created disposable database.

Install PyMySQL, then run against a LOCAL TEST cluster:
  python nullsafe_join_probe.py --port 19330 --output results.json --sql-output probe.sql

The database is retained for inspection. CREATE DATABASE intentionally omits
IF NOT EXISTS: passing an existing database fails without touching its tables.
This is a SQL compatibility probe, not Doris's official regression framework,
and running it against a released binary does not validate a source checkout.
"""

import argparse
import datetime
import json
import os
import re
import time
from pathlib import Path

import pymysql


LEFT = [
    (1, None, None, None),
    (2, 0, "", 0),
    (3, 1, "1", 110),
    (4, 2, "2", 220),
    (5, None, "x", 330),
    (6, 3, None, None),
    (7, 1, "1", 110),
]
RIGHT = [
    (10, None, None, None),
    (11, 0, "", 0),
    (12, 1, "1", 110),
    (13, None, "missing", 990),
    (14, 4, "4", 440),
]
BUSINESS_KEYS = ["dt_month", "Platform_Order_Number", "Ware_Shop_Code", "Shop_Code", "Seller_Sku", "MSKU"]
KEY_ROWS = [
    (1, "2026-09", "unique", "W1", "S1", "SKU1", "M1"),
    (2, "2026-09", "duplicate-null", None, "S1", "SKU1", "M1"),
    (3, "2026-09", "duplicate-null", None, "S1", "SKU1", "M1"),
    (4, None, None, None, None, None, "unique-null"),
    (5, "", "", "", "", "", ""),
    (6, "2026-09", "duplicate", "W1", "S1", "SKU1", "M1"),
    (7, "2026-09", "duplicate", "W1", "S1", "SKU1", "M1"),
    (8, "2026-09", "null-vs-empty", None, "S1", "SKU1", "M1"),
    (9, "2026-09", "null-vs-empty", "", "S1", "SKU1", "M1"),
    (10, None, None, None, None, None, None),
    (11, None, None, None, None, None, None),
    (12, "2026-09", "null-vs-empty-msku", "W1", "S1", "SKU1", None),
    (13, "2026-09", "null-vs-empty-msku", "W1", "S1", "SKU1", ""),
]


def expected_rows(left, right, predicate, join):
    rows = []
    for a in left:
        matches = [b for b in right if predicate(a, b)]
        if join == "LEFT SEMI":
            rows.extend([[a[0]]] if matches else [])
        elif join == "LEFT ANTI":
            rows.extend([] if matches else [[a[0]]])
        elif matches:
            rows.extend([[a[0], b[0]] for b in matches])
        elif join == "LEFT":
            rows.append([a[0], None])
    return sorted(rows, key=lambda row: tuple(-1 if v is None else v for v in row))


def cases():
    numeric = ("a.n", "b.n", lambda a: a[1], lambda b: b[1])
    string = ("a.s", "b.s", lambda a: a[2], lambda b: b[2])
    decimal = ("a.d", "b.d", lambda a: a[3], lambda b: b[3])
    cast = (
        "CAST(a.n AS STRING)", "b.s",
        lambda a: None if a[1] is None else str(a[1]), lambda b: b[2],
    )
    concat = (
        "CONCAT(COALESCE(a.s, ''), CAST(a.n % 5 AS STRING))", "b.s",
        lambda a: None if a[1] is None else (a[2] or "") + str(a[1] % 5),
        lambda b: b[2],
    )
    null_empty = (
        "IF(a.n IS NOT NULL, '', NULL)", "b.s",
        lambda a: None if a[1] is None else "", lambda b: b[2],
    )
    specs = []
    for name, keys, joins in [
        ("numeric", [numeric], ["INNER", "LEFT", "LEFT SEMI", "LEFT ANTI"]),
        ("string", [string], ["INNER", "LEFT", "LEFT SEMI", "LEFT ANTI"]),
        ("decimal", [decimal], ["INNER"]),
        ("composite", [numeric, string], ["INNER"]),
        ("cast_string", [cast], ["INNER", "LEFT"]),
        ("concat_string", [concat], ["INNER", "LEFT"]),
        ("null_vs_empty", [null_empty], ["INNER"]),
    ]:
        specs.extend((name, keys, join, "ns_right", RIGHT) for join in joins)
    specs.append(("empty_build", [numeric], "LEFT", "(SELECT * FROM ns_right WHERE id < 0)", []))
    specs.append(("all_null_build", [numeric], "INNER", "(SELECT * FROM ns_right WHERE n IS NULL)",
                  [r for r in RIGHT if r[1] is None]))
    output = []
    for name, keys, join, build, right in specs:
        select = "a.id" if join in ("LEFT SEMI", "LEFT ANTI") else "a.id, b.id"
        order = "1" if join in ("LEFT SEMI", "LEFT ANTI") else "1, 2"
        predicate = " AND ".join(f"{a} <=> {b}" for a, b, _, _ in keys)
        oracle = " AND ".join(f"({a} = {b} OR ({a} IS NULL AND {b} IS NULL))" for a, b, _, _ in keys)
        template = f"SELECT {select} FROM ns_left a {join} JOIN {build} b ON {{}} ORDER BY {order}"
        python_predicate = lambda a, b, keys=keys: all(af(a) == bf(b) for _, _, af, bf in keys)
        output.append({
            "name": f"{name}_{join.lower().replace(' ', '_')}",
            "sql": template.format(predicate),
            "oracle_sql": template.format(oracle),
            "expected": expected_rows(LEFT, right, python_predicate, join),
        })
    key_columns = ", ".join(BUSINESS_KEYS)
    join_columns = " AND ".join(f"r.{key} <=> p.{key}" for key in BUSINESS_KEYS)
    original = (f"WITH q_key_profile AS (SELECT {key_columns}, COUNT(*) AS key_rows FROM q_raw "
                f"GROUP BY {key_columns}) SELECT r.*, 1 AS __q_present FROM q_raw r "
                f"JOIN q_key_profile p ON {join_columns} WHERE p.key_rows = 1 ORDER BY r.id")
    window = ("SELECT * EXCEPT (__q_key_rows) FROM (SELECT r.*, 1 AS __q_present, "
              f"COUNT(*) OVER(PARTITION BY {key_columns}) AS __q_key_rows FROM q_raw r) counted "
              "WHERE __q_key_rows = 1 ORDER BY id")
    expected = [list(row) + [1] for row in KEY_ROWS
                if sum(other[1:] == row[1:] for other in KEY_ROWS) == 1]
    output.append({"name": "six_key_cte_vs_window", "sql": original, "oracle_sql": window,
                   "expected": expected,
                   "expected_columns": ["id", *BUSINESS_KEYS, "__q_present"]})
    # Model a reused q_raw CTE, including a nullable expression in its projection.
    # The screenshot does not include q_raw's definition, so these are synthetic
    # input shapes rather than a reproduction of the complete business query.
    for name, definition, cte_expected in [
        ("six_key_reused_cte", "SELECT * FROM q_raw", expected),
        ("six_key_expression_cte",
         "SELECT id, dt_month, Platform_Order_Number, Ware_Shop_Code, Shop_Code, "
         "Seller_Sku, CONCAT('prefix:', MSKU) AS MSKU FROM q_raw",
         [row[:-2] + [None if row[-2] is None else "prefix:" + row[-2], row[-1]]
          for row in expected]),
    ]:
        cte_original = original.replace("q_raw", "cte_raw").replace(
            "WITH ", f"WITH cte_raw AS ({definition}), ", 1)
        cte_window = f"WITH cte_raw AS ({definition}) " + window.replace("q_raw", "cte_raw")
        output.append({"name": name, "sql": cte_original, "oracle_sql": cte_window,
                       "expected": cte_expected,
                       "expected_columns": ["id", *BUSINESS_KEYS, "__q_present"]})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9030)
    parser.add_argument("--user", default="root")
    parser.add_argument("--database", default="nullsafe_probe_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
    parser.add_argument("--output", default="nullsafe-probe-results.json")
    parser.add_argument("--sql-output", default="nullsafe-probe.sql")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", args.database):
        parser.error("database must be a SQL identifier of 1-64 letters, numbers or underscores")
    result = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "database": args.database,
        "scope": "released-binary SQL smoke probe; EXPLAIN proves RF generation only",
        "tests": [], "errors": [],
    }
    transcript = ["-- Generated SQL transcript. Use only with a disposable test cluster."]
    connection = None
    last_columns = []

    def run(sql):
        nonlocal last_columns
        transcript.append(sql + ";")
        with connection.cursor() as cursor:
            cursor.execute(sql)
            last_columns = [column[0] for column in cursor.description] if cursor.description else []
            return [list(row) for row in cursor.fetchall()]

    def nodes(sql):
        transcript.append(sql + ";")
        with connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            return [{k: row[k] for k in ("Version", "Alive", "LastStartTime", "ErrMsg") if k in row}
                    for row in cursor.fetchall()]

    try:
        connection = pymysql.connect(
            host=args.host, port=args.port, user=args.user,
            password=os.environ.get("DORIS_PASSWORD", ""),
            connect_timeout=5, read_timeout=60, write_timeout=60, autocommit=True,
        )
        result["mysql_compatibility_version"] = run("SELECT VERSION()")
        result["frontends_before"] = nodes("SHOW FRONTENDS")
        result["backends_before"] = nodes("SHOW BACKENDS")
        run(f"CREATE DATABASE `{args.database}`")
        run(f"USE `{args.database}`")
        for table in ("ns_left", "ns_right"):
            run(f"CREATE TABLE {table} (id INT NOT NULL, n INT NULL, s STRING NULL, d DECIMAL(12, 2) NULL) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')")
        run("INSERT INTO ns_left VALUES (1,NULL,NULL,NULL),(2,0,'',0),(3,1,'1',1.10),(4,2,'2',2.20),"
            "(5,NULL,'x',3.30),(6,3,NULL,NULL),(7,1,'1',1.10)")
        run("INSERT INTO ns_right VALUES (10,NULL,NULL,NULL),(11,0,'',0),(12,1,'1',1.10),"
            "(13,NULL,'missing',9.90),(14,4,'4',4.40)")
        columns = ", ".join(f"{key} STRING NULL" for key in BUSINESS_KEYS)
        run(f"CREATE TABLE q_raw (id INT NOT NULL, {columns}) DUPLICATE KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')")
        values = ",".join("(" + ",".join(connection.escape(value) for value in row) + ")" for row in KEY_ROWS)
        run("INSERT INTO q_raw VALUES " + values)
        truth_sql = "SELECT NULL <=> NULL, NULL <=> 0, 0 <=> 0, 0 <=> 1, '' <=> '', '' <=> NULL, 'x' <=> 'x', 'x' <=> ''"
        truth = run(truth_sql)
        result["truth_table"] = {"sql": truth_sql, "actual": truth, "expected": [[1,0,1,0,1,0,1,0]],
                                 "passed": truth == [[1,0,1,0,1,0,1,0]]}
        run("SET query_timeout = 30")
        for label, mode, kind in [("off", "OFF", 1), ("in", "GLOBAL", 1),
                                  ("bloom", "GLOBAL", 2), ("min_max", "GLOBAL", 4),
                                  ("in_or_bloom", "GLOBAL", 8)]:
            try:
                run(f"SET runtime_filter_mode = '{mode}'")
                run(f"SET runtime_filter_type = {kind}")
                run("SET enable_runtime_filter_prune = false")
            except pymysql.MySQLError as exc:
                result["errors"].append({"mode": label, "phase": "session_settings", "error": str(exc)})
                continue
            for case in cases():
                record = {"mode": label, **case}
                started = time.monotonic()
                try:
                    plan = "\n".join(str(row[0]) for row in run("EXPLAIN " + case["sql"]))
                    record["explain"] = plan
                    record["runtime_filter_generated"] = bool(re.search(r"\bRF\d+\b", plan))
                    record["actual"] = run(case["sql"])
                    record["actual_columns"] = last_columns
                    record["oracle"] = run(case["oracle_sql"])
                    record["oracle_columns"] = last_columns
                    record["passed"] = record["actual"] == record["oracle"] == record["expected"]
                    if "expected_columns" in case:
                        record["passed"] = (record["passed"] and record["actual_columns"] ==
                                            record["oracle_columns"] == case["expected_columns"])
                        record["oracle_explain"] = "\n".join(str(row[0]) for row in run("EXPLAIN " + case["oracle_sql"]))
                except pymysql.MySQLError as exc:
                    record["passed"] = False
                    record["error"] = str(exc)
                record["seconds"] = round(time.monotonic() - started, 3)
                result["tests"].append(record)
                print(f"{label:11s} {case['name']:26s} {'PASS' if record['passed'] else 'FAIL'} "
                      f"RF_generated={record.get('runtime_filter_generated')}", flush=True)
        result["backends_after"] = nodes("SHOW BACKENDS")
        result["frontends_after"] = nodes("SHOW FRONTENDS")
    except Exception as exc:
        result["errors"].append({"phase": "probe", "error": str(exc)})
    finally:
        if connection is not None:
            connection.close()
        result["passed"] = (not result["errors"] and result.get("truth_table", {}).get("passed", False)
                            and len(result["tests"]) == len(cases()) * 5
                            and all(test["passed"] for test in result["tests"]))
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
        Path(args.sql_output).write_text("\n\n".join(transcript) + "\n")
    print(json.dumps({"passed": result["passed"], "cases": len(result["tests"]),
                      "errors": result["errors"], "database": args.database}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
