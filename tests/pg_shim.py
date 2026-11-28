"""Test-only stand-in for the psycopg driver: sends SQL to a REAL local Postgres through the psql command line.
Used because the build machine has a Postgres server but cannot install Python packages. Parameters are turned
into SQL literals here (test code only; the app itself uses psycopg's proper parameter binding)."""
import os
import shutil
import subprocess

PGHOST = os.environ.get("RIA_TEST_PGHOST", "/var/tmp/pgtest")
PGPORT = os.environ.get("RIA_TEST_PGPORT", "55432")
FS, RS = "\x1f", "\x1e"


def available() -> bool:
    if not shutil.which("psql"):
        return False
    r = subprocess.run(["psql", "-h", PGHOST, "-p", PGPORT, "-U", "postgres", "-At", "-c", "select 1"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "1"


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def _bind(sql: str, params) -> str:
    parts = sql.split("%s")
    assert len(parts) - 1 == len(params or ()), (sql, params)
    return parts[0] + "".join(_lit(p) + rest for p, rest in zip(params or (), parts[1:]))


class Cursor:
    def __init__(self, conn):
        self.conn, self._rows = conn, []

    def _psql(self, sql: str):
        r = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", PGHOST, "-p", PGPORT, "-U", "postgres",
                            "-d", self.conn.dbname, "-A", "-t", "-F", FS, "-R", RS, "-c", sql],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr.strip())
        return r.stdout

    def execute(self, sql, params=None):
        if self.conn.fail:
            raise ConnectionError("simulated outage")
        out = self._psql(_bind(sql, params))
        self._rows = [tuple(rec.split(FS)) for rec in out.rstrip("\n").split(RS) if rec.strip("\n")]

    def executemany(self, sql, seq):
        stmts = [_bind(sql, p) for p in seq]
        for i in range(0, len(stmts), 200):
            self._psql(";\n".join(stmts[i:i + 200]))

    def fetchall(self):
        return self._rows


class Connection:
    def __init__(self, dbname, fail=False):
        self.dbname, self.fail = dbname, fail

    def cursor(self):
        return Cursor(self)

    def commit(self):
        pass

    def close(self):
        pass


def make_db(name: str) -> str:
    subprocess.run(["psql", "-h", PGHOST, "-p", PGPORT, "-U", "postgres", "-c", f"drop database if exists {name}"],
                   capture_output=True)
    subprocess.run(["psql", "-h", PGHOST, "-p", PGPORT, "-U", "postgres", "-c", f"create database {name}"],
                   capture_output=True, check=True)
    return name
