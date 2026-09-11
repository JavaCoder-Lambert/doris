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

"""Bounded NULL-safe string join probe for a disposable, loopback test cluster.

Requires Python 3.9+; PyMySQL is needed only for a database run. See EXTENDED.md.
No existing database is reused or dropped. Output files are created exclusively.
The Python oracle is independent of Doris expression and join implementations.
"""

import argparse
from collections import Counter
from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import time


SEED = 20260911
BATCH_SIZE = 16
MODES = (("off", "OFF"), ("bloom", "GLOBAL"))
DISTRIBUTIONS = ("broadcast", "shuffle")
MAX_RESULT_ROWS = 256 * 40
PREVIEW_ROWS = 12
COLUMNS = ("id", "n", "s", "prefix", "nn", "g")


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def fixtures():
    rng = random.Random(SEED)
    # Use nonnegative integers: Python and SQL remainder semantics then agree.
    # The long prefix exercises both short and longer StringRef comparisons.
    prefixes = (None, "", "p", "q", "long:" * 9)
    numbers = (None, None, 0, 1, 2, 3, 4, 5, 7, 9, 13)
    strings = (None, "", "0", "1", "2", "p0", "p1", "q3", "missing")
    payloads = [(numbers[i % len(numbers)], strings[i % len(strings)],
                 prefixes[i % len(prefixes)], strings[1 + i % (len(strings) - 1)],
                 (None, 0, 1, 2)[i % 4]) for i in range(256)]
    rng.shuffle(payloads)
    left = [(i + 1, *row) for i, row in enumerate(payloads)]
    build_keys = [None, None, "", ""] + [str(i) for i in range(10)]
    build_keys += [prefix + str(i) for prefix in ("p", "q", "long:" * 9) for i in range(5)]
    build_keys += ["right-only"]
    right = [(1001 + i, None if i % 7 == 0 else i % 10,
              build_keys[i % len(build_keys)], prefixes[i % len(prefixes)],
              build_keys[i % len(build_keys)] if build_keys[i % len(build_keys)] is not None
              else "nonnull-only", (None, 0, 1, 2)[i % 4]) for i in range(40)]
    null_build = [(2001 + i, None, None, "residual:" + str(i), "nonnull-only", i % 3)
                  for i in range(33)]
    return {"ext_left": left, "ext_right": right,
            "ext_null_build": null_build, "ext_empty_build": []}


def cast_key(row):
    return None if row[1] is None else str(row[1])


def concat_key(row):
    return None if row[1] is None else (row[3] or "") + str(row[1] % 5)


@dataclass(frozen=True)
class Case:
    name: str
    left_sql: tuple
    right_sql: tuple
    left_key: object
    right_key: object
    join: str = "INNER"
    build: str = "ext_right"
    require_hash_join: bool = True
    require_null_safe: bool = True
    require_bloom: bool = False

    def sql(self, distribution):
        predicate = " AND ".join(f"{left} <=> {right}"
                                 for left, right in zip(self.left_sql, self.right_sql))
        return ("SELECT a.id AS probe_id, b.id AS build_id FROM ext_left a "
                f"{self.join} JOIN [{distribution}] {self.build} b ON {predicate} "
                "ORDER BY probe_id, build_id")


def cases():
    cast = "CAST(a.n AS STRING)"
    concat = "CONCAT(COALESCE(a.prefix, ''), CAST(a.n % 5 AS STRING))"
    cast_args = ((cast,), ("b.s",), cast_key, lambda row: row[2])
    concat_args = ((concat,), ("b.s",), concat_key, lambda row: row[2])
    return [
        Case("cast_string_inner", *cast_args, require_bloom=True),
        Case("cast_string_left", *cast_args, join="LEFT"),
        Case("concat_string_inner", *concat_args),
        Case("concat_string_left", *concat_args, join="LEFT"),
        Case("cast_both_sides", (cast,), ("CAST(b.n AS STRING)",), cast_key, cast_key),
        Case("concat_both_sides", (concat,),
             ("CONCAT(COALESCE(b.prefix, ''), CAST(b.n % 5 AS STRING))",),
             concat_key, concat_key),
        Case("null_vs_real_empty", ("IF(a.n IS NOT NULL, '', NULL)",), ("b.s",),
             lambda row: None if row[1] is None else "", lambda row: row[2]),
        Case("nullable_probe_nonnull_build", (cast,), ("b.nn",),
             cast_key, lambda row: row[4], require_null_safe=False),
        Case("nonnull_probe_nullable_build", ("a.nn",), ("b.s",),
             lambda row: row[4], lambda row: row[2], join="LEFT", require_null_safe=False),
        # Constant equalities may legally become filters or a nested loop join.
        # These check semantics and record the plan, without claiming hash coverage.
        Case("constant_null_probe", ("CAST(NULL AS STRING)",), ("b.s",),
             lambda row: None, lambda row: row[2], join="LEFT", require_hash_join=False),
        Case("constant_null_build", (cast,), ("CAST(NULL AS STRING)",),
             cast_key, lambda row: None, join="LEFT", require_hash_join=False),
        Case("two_nullable_keys", (cast, "a.g"), ("b.s", "b.g"),
             lambda row: (cast_key(row), row[5]), lambda row: (row[2], row[5])),
        Case("all_null_build_inner", *cast_args, build="ext_null_build"),
        Case("all_null_build_left", *cast_args, join="LEFT", build="ext_null_build"),
        Case("empty_build_left", *concat_args, join="LEFT", build="ext_empty_build",
             require_hash_join=False),
    ]


def ordered(rows):
    return sorted(rows, key=lambda row: tuple(-1 if value is None else value for value in row))


def expected_rows(case, left, right):
    rows = []
    for a in left:
        matches = [b for b in right if case.left_key(a) == case.right_key(b)]
        rows.extend((a[0], b[0]) for b in matches)
        if not matches and case.join == "LEFT":
            rows.append((a[0], None))
    return ordered(rows)


def row_summary(rows):
    return {"count": len(rows), "sha256": digest(rows), "preview": rows[:PREVIEW_ROWS]}


def compare_rows(expected, actual):
    expected_counts, actual_counts = Counter(expected), Counter(actual)
    missing = ordered(list((expected_counts - actual_counts).elements()))
    unexpected = ordered(list((actual_counts - expected_counts).elements()))
    return {"passed": not missing and not unexpected,
            "expected": row_summary(expected), "actual": row_summary(ordered(actual)),
            "missing": row_summary(missing), "unexpected": row_summary(unexpected)}


def check_plan(case, plan, distribution, mode):
    hash_join_count = len(re.findall(r"\bVHASH JOIN\b", plan))
    actual_distributions = re.findall(r"join op:\s*[^\n(]+\(([^)]*)\)", plan)
    equalities = re.findall(r"equal join conjunct:\s*([^\n]+)", plan)
    rf_ids = sorted(set(re.findall(r"\bRF\d+\b", plan)))
    required_distribution = "BROADCAST" if distribution == "broadcast" else "PARTITIONED"
    issues = []
    if case.require_hash_join:
        if hash_join_count != 1:
            issues.append(f"expected one VHASH JOIN, got {hash_join_count}")
        if actual_distributions != [required_distribution]:
            issues.append(f"expected {required_distribution}, got {actual_distributions}")
        if len(equalities) != len(case.left_sql):
            issues.append(f"expected {len(case.left_sql)} physical equalities, got {len(equalities)}")
        if case.require_null_safe and any("<=>" not in equality for equality in equalities):
            issues.append("a required physical NULL-safe equality was rewritten")
    if mode == "off" and rf_ids:
        issues.append("RF appears in an OFF plan")
    if mode == "bloom" and case.require_bloom and not rf_ids:
        issues.append("CAST inner join did not generate the required RF")
    return {"passed": not issues, "issues": issues, "hash_join_count": hash_join_count,
            "actual_distributions": actual_distributions, "equalities": equalities,
            "runtime_filter_ids": rf_ids, "hash_path_required": case.require_hash_join,
            "plan_sha256": digest(plan), "explain": plan}


def check_health(before, after):
    issues = []
    verified = bool(before and after)
    before_by_id = {str(row.get("BackendId")): row for row in before}
    after_by_id = {str(row.get("BackendId")): row for row in after}
    if not before or set(before_by_id) != set(after_by_id):
        issues.append("backend membership is empty or changed")
    for backend_id, row in before_by_id.items():
        current = after_by_id.get(backend_id, {})
        for snapshot in (row, current):
            if not all(field in snapshot for field in ("BackendId", "Alive", "LastStartTime", "Version")):
                verified = False
                issues.append(f"backend {backend_id}: incomplete health metadata")
            if str(snapshot.get("Alive", "")).lower() != "true":
                issues.append(f"backend {backend_id}: not alive")
        if row.get("LastStartTime") != current.get("LastStartTime"):
            issues.append(f"backend {backend_id}: restart detected")
        if row.get("Version") != current.get("Version"):
            issues.append(f"backend {backend_id}: version changed")
        if current.get("ErrMsg"):
            issues.append(f"backend {backend_id}: nonempty ErrMsg")
    return {"passed": not issues, "status": ("unverified" if not verified else
                                             "failed" if issues else "passed"),
            "issues": issues}


def self_check():
    data = fixtures()
    assert data == fixtures()
    assert len(cases()) == 15
    for table, rows in data.items():
        assert len({row[0] for row in rows}) == len(rows), table
        assert all(len(row) == len(COLUMNS) and row[4] is not None for row in rows), table
    assert len(data["ext_left"]) > BATCH_SIZE and len(data["ext_right"]) > BATCH_SIZE
    assert len(data["ext_null_build"]) > BATCH_SIZE
    assert {row[2] for row in data["ext_right"]} >= {None, ""}
    # Hand-derived small oracle: two NULL build matches, one real empty match,
    # and one unmatched probe. This also checks duplicate multiplicity.
    sample = Case("sample", ("a.s",), ("b.s",), lambda row: row[1],
                  lambda row: row[1], join="LEFT")
    expected = [(1, 10), (1, 11), (2, 12), (3, None)]
    assert expected_rows(sample, [(1, None), (2, ""), (3, "x")],
                         [(10, None), (11, None), (12, "")]) == expected
    assert compare_rows(expected, expected[::-1])["passed"]
    assert not compare_rows(expected, expected[:-1])["passed"]
    assert not compare_rows(expected, expected + [(1, 10)])["passed"]
    cast_case = cases()[0]
    plan = ("3:VHASH JOIN(2)\n  join op: INNER JOIN(BROADCAST)[]\n"
            "  equal join conjunct: (expr <=> s)\n  runtime filters: RF000[bloom]\n")
    assert check_plan(cast_case, plan, "broadcast", "bloom")["passed"]
    assert not check_plan(cast_case, plan, "shuffle", "bloom")["passed"]
    assert not check_plan(cast_case, plan, "broadcast", "off")["passed"]
    assert not check_plan(cast_case, "", "broadcast", "bloom")["passed"]
    healthy = [{"BackendId": 1, "Alive": "true", "LastStartTime": "start1", "Version": "v"}]
    assert check_health(healthy, healthy)["passed"]
    assert not check_health(healthy, [{**healthy[0], "LastStartTime": "start2"}])["passed"]
    assert not check_health([], [])["passed"]
    counts = {}
    for case in cases():
        rows = expected_rows(case, data["ext_left"], data[case.build])
        assert 0 < len(rows) <= MAX_RESULT_ROWS, case.name
        counts[case.name] = len(rows)
    assert counts["empty_build_left"] == len(data["ext_left"])
    return {"passed": True, "seed": SEED, "fixture_sha256": digest(data),
            "input_rows": {name: len(rows) for name, rows in data.items()},
            "query_count": len(cases()) * len(MODES) * len(DISTRIBUTIONS),
            "expected_rows_per_case": counts}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="offline checks, no database or PyMySQL")
    parser.add_argument("--test-cluster", action="store_true", help="confirm the port belongs to a disposable test cluster")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, help="explicit loopback test MySQL port")
    parser.add_argument("--user", default="root")
    parser.add_argument("--database", default="nullsafe_ext_" + datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f"))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--expect-be-version", help="required substring of every BE Version, checked before DDL")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", args.database):
        parser.error("database must be a SQL identifier of 1-64 letters, numbers or underscores")
    if not args.self_check and (not args.test_cluster or args.port is None):
        parser.error("database runs require --test-cluster and an explicit --port")
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return args


def main():
    args = parse_args()
    offline = self_check()
    if args.self_check:
        print(json.dumps(offline, ensure_ascii=False, indent=2))
        return 0
    try:
        import pymysql
    except ImportError:
        raise SystemExit("PyMySQL is required for a database run; see EXTENDED.md")

    # Fail before connecting or creating a database if output cannot be preserved.
    json_path = args.output_dir / (args.database + ".json")
    sql_path = args.output_dir / (args.database + ".sql")
    for path in (json_path, sql_path):
        if path.exists():
            raise SystemExit(f"refusing to overwrite output: {path}")
    with json_path.open("x", encoding="utf-8") as json_file, sql_path.open("x", encoding="utf-8") as sql_file:
        result = {"timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "database": args.database, "database_created": False, "offline": offline,
                  "batch_size": BATCH_SIZE, "tests": [], "errors": [],
                  "scope": "SQL semantics and EXPLAIN coverage; no proof of RF arrival or actual block counts"}
        connection = None

        def run(sql, limit=MAX_RESULT_ROWS):
            sql_file.write(sql + ";\n\n")
            sql_file.flush()
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = list(cursor.fetchall())
                if len(rows) > limit:
                    raise RuntimeError(f"row count {len(rows)} exceeds the probe bound {limit}")
                return rows

        def nodes(command):
            sql_file.write(command + ";\n\n")
            sql_file.flush()
            with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(command)
                fields = ("BackendId", "Name", "Version", "Alive", "LastStartTime", "ErrMsg")
                return [{field: row[field] for field in fields if field in row}
                        for row in cursor.fetchall()]

        try:
            connection = pymysql.connect(host=args.host, port=args.port, user=args.user,
                                         password=os.environ.get("DORIS_PASSWORD", ""),
                                         connect_timeout=5, read_timeout=45, write_timeout=45,
                                         charset="utf8mb4", autocommit=True)
            result["mysql_compatibility_version"] = run("SELECT VERSION()")
            result["frontends_before"] = nodes("SHOW FRONTENDS")
            result["backends_before"] = nodes("SHOW BACKENDS")
            initial_health = check_health(result["backends_before"], result["backends_before"])
            if not initial_health["passed"]:
                raise RuntimeError("unhealthy test cluster before DDL: " + repr(initial_health["issues"]))
            if args.expect_be_version and any(args.expect_be_version not in str(row.get("Version", ""))
                                              for row in result["backends_before"]):
                raise RuntimeError("BE Version does not contain --expect-be-version")
            # No IF NOT EXISTS: a name collision must fail before touching tables.
            run(f"CREATE DATABASE `{args.database}`")
            result["database_created"] = True
            run(f"USE `{args.database}`")
            run("SET query_timeout = 30")
            run(f"SET batch_size = {BATCH_SIZE}")
            run("SET parallel_pipeline_task_num = 1")
            run("SET disable_join_reorder = true")
            run("SET runtime_filter_type = 2")
            run("SET enable_runtime_filter_prune = false")
            data = fixtures()
            result["loaded_fixtures"] = {}
            for table, rows in data.items():
                run(f"CREATE TABLE {table} (id INT NOT NULL, n INT NULL, s STRING NULL, "
                    "prefix STRING NULL, nn STRING NOT NULL, g INT NULL) DUPLICATE KEY(id) "
                    "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')")
                if rows:
                    values = ",".join("(" + ",".join(connection.escape(value) for value in row) + ")"
                                      for row in rows)
                    run(f"INSERT INTO {table} VALUES {values}")
                actual = run(f"SELECT {', '.join(COLUMNS)} FROM {table} ORDER BY id")
                # Validate the inserted values before trusting any join verdict.
                loaded = {"passed": actual == rows, "count": len(actual), "sha256": digest(actual)}
                result["loaded_fixtures"][table] = loaded
                if not loaded["passed"]:
                    raise RuntimeError(f"fixture readback mismatch in {table}")

            consecutive_errors = 0
            for label, rf_mode in MODES:
                run(f"SET runtime_filter_mode = '{rf_mode}'")
                for distribution in DISTRIBUTIONS:
                    for case in cases():
                        started = time.monotonic()
                        record = {"name": case.name, "rf_mode": label, "distribution_hint": distribution,
                                  "sql": case.sql(distribution), "passed": False}
                        try:
                            plan = "\n".join(str(row[0]) for row in run("EXPLAIN " + record["sql"]))
                            record["plan"] = check_plan(case, plan, distribution, label)
                            actual = run(record["sql"])
                            expected = expected_rows(case, data["ext_left"], data[case.build])
                            record["rows"] = compare_rows(expected, actual)
                            record["passed"] = record["rows"]["passed"] and record["plan"]["passed"]
                            consecutive_errors = 0
                        except Exception as exc:
                            record["error"] = str(exc)
                            consecutive_errors += 1
                        record["seconds"] = round(time.monotonic() - started, 3)
                        result["tests"].append(record)
                        print(f"{label:5s} {distribution:9s} {case.name:34s} "
                              f"{'PASS' if record['passed'] else 'FAIL'} "
                              f"rows={record.get('rows', {}).get('passed')} "
                              f"plan={record.get('plan', {}).get('passed')}", flush=True)
                        if consecutive_errors >= 3 or not connection.open:
                            raise RuntimeError("aborting after repeated query errors or a closed connection")
        except Exception as exc:
            result["errors"].append({"phase": "probe", "error": str(exc)})
        finally:
            if connection is not None:
                try:
                    result["backends_after"] = nodes("SHOW BACKENDS")
                    result["frontends_after"] = nodes("SHOW FRONTENDS")
                except Exception as exc:
                    result["errors"].append({"phase": "final_health", "error": str(exc)})
                try:
                    connection.close()
                except Exception as exc:
                    result["errors"].append({"phase": "connection_close", "error": str(exc)})
            result["health"] = check_health(result.get("backends_before", []),
                                             result.get("backends_after", []))
            result["all_queries_completed"] = len(result["tests"]) == offline["query_count"]
            result["semantics_passed"] = (result["all_queries_completed"] and
                                          all(test.get("rows", {}).get("passed", False)
                                              for test in result["tests"]))
            result["plan_coverage_passed"] = (result["all_queries_completed"] and
                                              all(test.get("plan", {}).get("passed", False)
                                                  for test in result["tests"]))
            result["passed"] = (not result["errors"] and result["health"]["passed"]
                                and result["all_queries_completed"]
                                and all(test["passed"] for test in result["tests"]))
            json.dump(result, json_file, ensure_ascii=False, indent=2, default=str)
            json_file.write("\n")
        print(json.dumps({"passed": result["passed"], "queries": len(result["tests"]),
                          "expected_queries": offline["query_count"], "health": result["health"],
                          "errors": result["errors"], "database": args.database,
                          "json": str(json_path.resolve()), "sql": str(sql_path.resolve())},
                         ensure_ascii=False))
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
