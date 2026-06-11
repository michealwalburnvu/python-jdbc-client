#!/usr/bin/env python3
"""Read-only SQL Server client for the DQE database (via the Microsoft JDBC driver).

Connects with Windows-domain (NTLM) credentials supplied via a local .env file,
with an encrypted connection that trusts the server certificate
(encrypt=true; trustServerCertificate=true). Runs SELECT-only queries: a guard
rejects anything that could mutate the database, and the JDBC connection is set
read-only.

Driven through JayDeBeApi/JPype against a project-local JRE and the bundled
mssql-jdbc jar — nothing is installed on the host.

Usage:
    python dqe_client.py ping
    python dqe_client.py tables [--like PATTERN]
    python dqe_client.py columns TABLE_NAME
    python dqe_client.py query "SELECT TOP 10 * FROM dbo.Variant"
    python dqe_client.py file path/to/query.sql

Output options: --json, --csv, --limit N (default 200), --all.
"""

import argparse
import csv
import io
import json
import os
import re
import sys

import jpype
import jaydebeapi

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env(profile=None):
    """Load connection vars from .env (default) or .env.<profile> for another DB.

    Pre-existing process env vars win (override=False), so per-invocation
    `MSSQL_SERVER=... MSSQL_DATABASE=... python dqe_client.py ...` also works.
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    fname = f".env.{profile}" if profile else ".env"
    load_dotenv(os.path.join(_HERE, fname), override=False)
_DRIVER_JAR = os.path.join(_HERE, "lib", "mssql-jdbc.jar")
_DRIVER_CLASS = "com.microsoft.sqlserver.jdbc.SQLServerDriver"


def _find_libjvm():
    for root, _dirs, files in os.walk(os.path.join(_HERE, ".jdk")):
        if "libjvm.dylib" in files:
            return os.path.join(root, "libjvm.dylib")
    return None


# --- read-only guard --------------------------------------------------------

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_STARTS_READONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|"
    r"GRANT|REVOKE|DENY|BACKUP|RESTORE|SHUTDOWN|RECONFIGURE|INTO|"
    r"sp_executesql|xp_cmdshell|DBCC|WAITFOR)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(Exception):
    pass


def assert_read_only(sql: str) -> str:
    """Raise UnsafeQueryError unless `sql` is a single read-only statement."""
    stripped = _COMMENT_BLOCK.sub(" ", sql)
    stripped = _COMMENT_LINE.sub(" ", stripped)

    statements = [s for s in (part.strip() for part in stripped.split(";")) if s]
    if len(statements) != 1:
        raise UnsafeQueryError(
            f"Only a single statement is allowed (found {len(statements)})."
        )

    statement = statements[0]
    if not _STARTS_READONLY.match(statement):
        raise UnsafeQueryError("Query must begin with SELECT or WITH.")

    hit = _FORBIDDEN.search(statement)
    if hit:
        raise UnsafeQueryError(
            f"Forbidden keyword '{hit.group(0)}' is not allowed in a read-only query."
        )
    return statement


# --- connection -------------------------------------------------------------

def _env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        sys.exit(f"Missing required env var: {name} (set it in dqe-db-client/.env)")
    return value


def _bool_str(name, default="true"):
    return "true" if _env(name, default).lower() in ("1", "true", "yes") else "false"


def _split_domain_user():
    """Return (domain, user) from MSSQL_USER (DOMAIN\\user) and/or MSSQL_DOMAIN."""
    raw = _env("MSSQL_USER", required=True)
    domain = _env("MSSQL_DOMAIN")
    user = raw
    if "\\" in raw:
        parsed_domain, user = raw.split("\\", 1)
        domain = domain or parsed_domain
    if not domain:
        sys.exit("Missing domain: set MSSQL_DOMAIN or use MSSQL_USER=DOMAIN\\user")
    return domain, user


def _ensure_jvm():
    if jpype.isJVMStarted():
        return
    libjvm = _find_libjvm()
    if not libjvm:
        sys.exit("Local JRE not found under dqe-db-client/.jdk")
    jpype.startJVM(libjvm, classpath=[_DRIVER_JAR])


def connect():
    server = _env("MSSQL_SERVER", required=True)
    database = _env("MSSQL_DATABASE", required=True)
    port = _env("MSSQL_PORT", "1433")
    timeout = _env("MSSQL_TIMEOUT", "30")
    password = _env("MSSQL_PASSWORD", required=True)
    domain, user = _split_domain_user()
    encrypt = _bool_str("MSSQL_ENCRYPT", "true")
    trust = _bool_str("MSSQL_TRUST_SERVER_CERT", "true")

    url = (
        f"jdbc:sqlserver://{server}:{port};"
        f"databaseName={database};"
        f"encrypt={encrypt};"
        f"trustServerCertificate={trust};"
        f"integratedSecurity=true;"
        f"authenticationScheme=NTLM;"
        f"domain={domain};"
        f"loginTimeout={timeout};"
        f"applicationName=dqe-readonly-client"
    )

    _ensure_jvm()
    conn = jaydebeapi.connect(_DRIVER_CLASS, url, {"user": user, "password": password})
    try:
        conn.jconn.setReadOnly(True)
    except Exception:
        pass
    return conn


# --- output -----------------------------------------------------------------

def render(columns, rows, fmt):
    if fmt == "json":
        print(json.dumps([dict(zip(columns, r)) for r in rows], default=str, indent=2))
        return
    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(columns)
        w.writerows(rows)
        print(out.getvalue(), end="")
        return

    cols = [str(c) for c in columns]
    widths = [len(c) for c in cols]
    str_rows = []
    for r in rows:
        cells = ["" if v is None else str(v) for v in r]
        str_rows.append(cells)
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    print("  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
    print("  ".join("-" * widths[i] for i in range(len(cols))))
    for cells in str_rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)))


def _fetch(sql):
    statement = assert_read_only(sql)
    conn = connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(statement)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            return columns, rows
        finally:
            cur.close()
    finally:
        conn.close()


def run_select(sql, fmt, limit, show_all):
    columns, rows = _fetch(sql)
    truncated = False
    if not show_all and limit and len(rows) > limit:
        truncated = True
        rows = rows[:limit]
    render(columns, rows, fmt)
    if truncated:
        print(f"\n-- showing first {limit} rows; pass --all or --limit to see more",
              file=sys.stderr)


# --- subcommands ------------------------------------------------------------

def cmd_ping(args):
    columns, rows = _fetch("SELECT @@VERSION AS version, DB_NAME() AS db, SUSER_SNAME() AS login")
    version, db, login = rows[0]
    print("Connected OK")
    print(f"  database: {db}")
    print(f"  login:    {login}")
    print(f"  server:   {str(version).splitlines()[0]}")


def cmd_tables(args):
    where = ""
    if args.like:
        where = f"WHERE t.TABLE_NAME LIKE '{args.like.replace(chr(39), chr(39) * 2)}'"
    sql = f"""
        SELECT t.TABLE_SCHEMA AS [schema], t.TABLE_NAME AS [table], t.TABLE_TYPE AS [type]
        FROM INFORMATION_SCHEMA.TABLES t
        {where}
        ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME
    """
    run_select(sql, args.format, args.limit, args.all)


def cmd_columns(args):
    table = args.table.split(".")[-1].replace("'", "''")
    sql = f"""
        SELECT c.ORDINAL_POSITION AS pos, c.COLUMN_NAME AS [column],
               c.DATA_TYPE AS type, c.CHARACTER_MAXIMUM_LENGTH AS max_len,
               c.IS_NULLABLE AS nullable
        FROM INFORMATION_SCHEMA.COLUMNS c
        WHERE c.TABLE_NAME = '{table}'
        ORDER BY c.ORDINAL_POSITION
    """
    run_select(sql, args.format, args.limit, args.all)


def cmd_query(args):
    run_select(args.sql, args.format, args.limit, args.all)


def cmd_file(args):
    with open(args.path, "r") as f:
        run_select(f.read(), args.format, args.limit, args.all)


def build_parser():
    p = argparse.ArgumentParser(description="Read-only DQE SQL Server client (JDBC).")
    p.add_argument("--json", dest="format", action="store_const", const="json",
                   default="table", help="output JSON")
    p.add_argument("--csv", dest="format", action="store_const", const="csv",
                   help="output CSV")
    p.add_argument("--limit", type=int, default=200, help="max rows to show (default 200)")
    p.add_argument("--all", action="store_true", help="show all rows")
    p.add_argument("--profile", help="connection profile: load .env.<profile> instead of .env "
                                     "(point at any SQL Server DB)")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="verify the connection").set_defaults(func=cmd_ping)

    pt = sub.add_parser("tables", help="list tables")
    pt.add_argument("--like", help="filter table names (SQL LIKE pattern)")
    pt.set_defaults(func=cmd_tables)

    pc = sub.add_parser("columns", help="list columns for a table")
    pc.add_argument("table")
    pc.set_defaults(func=cmd_columns)

    pq = sub.add_parser("query", help="run a read-only SELECT")
    pq.add_argument("sql")
    pq.set_defaults(func=cmd_query)

    pf = sub.add_parser("file", help="run a read-only SELECT from a .sql file")
    pf.add_argument("path")
    pf.set_defaults(func=cmd_file)

    return p


def main():
    args = build_parser().parse_args()
    _load_env(getattr(args, "profile", None))
    try:
        args.func(args)
    except UnsafeQueryError as e:
        sys.exit(f"Blocked (read-only guard): {e}")


if __name__ == "__main__":
    main()
