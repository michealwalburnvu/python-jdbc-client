# Python read-only DB client (JDBC)

A small, **read-only** SQL Server client for inspecting SQL Server databases (variant
definitions, question groups, Encompass field mappings, etc.). Uses the official
**Microsoft JDBC driver** with Kerberos auth (`authenticationScheme=JavaKerberos`)
and an encrypted connection that trusts the server cert — the JDBC equivalent of
`encrypt=true;trustServerCertificate=true`. No password is stored anywhere: the
JVM reads the ticket already in your macOS Kerberos credential cache.

Nothing is installed on the host: a project-local Temurin JRE (`.jdk/`) and the
`mssql-jdbc` jar (`lib/`) are bundled, and the driver is driven from Python via
JayDeBeApi/JPype so the CLI and read-only guard stay in one place.

## Setup

```bash
cd dqe-db-client
cp .env.example .env        # then edit with host and db
klist                       # confirm you hold a Kerberos ticket; `kinit` if not
# Already provisioned: .venv (deps), .jdk (local JRE 17), lib/mssql-jdbc.jar
```

Fill in `.env`:
- `MSSQL_SERVER` — the cname / host
- `MSSQL_DATABASE` — the database name
- `MSSQL_SPN` — optional; only if the auto-derived SPN is wrong (see below)

`encrypt` and `trustServerCertificate` default to `true`. `.env` is gitignored.

## Kerberos

- You must be on the VPN and hold a valid ticket (`klist`). Get one with `kinit`
  if it has expired; the Kerberos SSO extension normally issues one at login.
- The JVM reads the native macOS credential cache directly (`libosxkrb5`), so no
  `KRB5CCNAME`, keytab, or JAAS file is needed.
- SQL Server SPNs are registered on the **canonical** host name, not the friendly
  cname. The client follows the CNAME chain and requests
  `MSSQLSvc/<canonical-host>:<port>`. If a server's SPN differs, set `MSSQL_SPN`
  in the profile. To check which SPN the KDC knows:
  `kgetcred MSSQLSvc/<host>:1433`.
- Failure modes: `Server not found in Kerberos database` → wrong SPN;
  `No valid credentials provided` / `Integrated authentication failed` → no or
  expired ticket; `TCP/IP connection ... failed` with an unresolved host → VPN down.

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
