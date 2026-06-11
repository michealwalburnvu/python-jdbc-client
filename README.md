# Python read-only DB client (JDBC)

A small, **read-only** SQL Server client for inspecting SQL Server databases (variant
definitions, question groups, Encompass field mappings, etc.). Uses the official
**Microsoft JDBC driver** with Windows-domain (NTLM) auth and an encrypted
connection that trusts the server cert — the JDBC equivalent of
`encrypt=true;trustServerCertificate=true`.

Nothing is installed on the host: a project-local Temurin JRE (`.jdk/`) and the
`mssql-jdbc` jar (`lib/`) are bundled, and the driver is driven from Python via
JayDeBeApi/JPype so the CLI and read-only guard stay in one place.

## Setup

```bash
cd dqe-db-client
cp .env.example .env        # then edit with host, db, and DOMAIN\user creds
# Already provisioned: .venv (deps), .jdk (local JRE 17), lib/mssql-jdbc.jar
```

Fill in `.env`:
- `MSSQL_SERVER` — the cname / host
- `MSSQL_DATABASE` — the database name
- `MSSQL_USER` — `DOMAIN\your.user` (or set `MSSQL_DOMAIN` + bare user)
- `MSSQL_PASSWORD` — your Windows password

`encrypt` and `trustServerCertificate` default to `true`. `.env` is gitignored.

## Use

```bash
.venv/bin/python jdbc_client.py ping                       # verify connection
.venv/bin/python jdbc_client.py tables --like "%Variant%"  # find tables
.venv/bin/python jdbc_client.py columns Variant            # inspect a table
.venv/bin/python jdbc_client.py query "SELECT TOP 10 * FROM dbo.Variant"
.venv/bin/python jdbc_client.py file queries/dump.sql      # run SQL from a file
```

Output: aligned text by default; add `--json` or `--csv`. Row cap is `--limit 200`
by default; use `--all` for everything.

## Other SQL Server databases (profiles)

The client works against **any** SQL Server DB. `.env` holds your primary/default
connection (set `MSSQL_DATABASE` to whatever database you want); add a profile file
per additional server, e.g. `.env.warehouse`:

```
MSSQL_SERVER=warehouse.example.com
MSSQL_DATABASE=Warehouse
MSSQL_USER=DOMAIN\micheal.walburn
MSSQL_PASSWORD=...
```

Then select it with `--profile`:

```bash
.venv/bin/python jdbc_client.py --profile warehouse tables --like "%Order%"
```

Or override per-invocation without a file (process env wins over `.env`):

```bash
MSSQL_SERVER=host MSSQL_DATABASE=Other .venv/bin/python jdbc_client.py ping
```

(The `MSSQL_*` names are just the connection variables — they apply to whatever DB
you point at.)

## Safety

- The JDBC connection is set read-only (`setReadOnly(true)`).
- A guard rejects anything that isn't a single `SELECT`/`WITH` statement
  (blocks `INSERT/UPDATE/DELETE/MERGE/DROP/ALTER/CREATE/TRUNCATE/EXEC/INTO/...`).
- Prefer a least-privilege (read-only) DB account if one is available.

## Re-provisioning (if .jdk / lib are missing)

```bash
# local JRE 17 (Apple Silicon shown; use x64 for Intel)
mkdir -p .jdk lib
curl -sSL -o jre.tgz "https://api.adoptium.net/v3/binary/latest/17/ga/mac/aarch64/jre/hotspot/normal/eclipse"
tar -xzf jre.tgz -C .jdk --strip-components=1 && rm jre.tgz
# Microsoft JDBC driver
curl -sSL -o lib/mssql-jdbc.jar "https://repo1.maven.org/maven2/com/microsoft/sqlserver/mssql-jdbc/12.8.1.jre11/mssql-jdbc-12.8.1.jre11.jar"
uv pip install --python .venv -r requirements.txt
```
