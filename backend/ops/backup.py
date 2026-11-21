"""Backup, restore and verify the app database and object storage (M19; docs/RUNBOOK_BACKUP_RESTORE.md).

    uv run python -m ops.backup backup  --out ../backups                 # database + objects, from settings/env
    uv run python -m ops.backup restore --from ../backups/<stamp> --database-url <EMPTY database> [--objects]
    uv run python -m ops.backup verify  --from ../backups/<stamp> --database-url <restored database>
    uv run python -m ops.backup drill                                    # = make backup-drill (throwaway containers)

A backup folder holds ``postgres.dump`` (``pg_dump`` custom format), ``objects/`` (every object of the bucket, by
key) and ``manifest.json``: when it was taken, the migration revision, the row count of every table, the RLS
policies and triggers, and the SHA-256 of every object. ``verify`` compares a restored database (and bucket) with
the manifest and checks that tenant isolation still works: as ``app_tenant`` with one workspace's id, no row of
another workspace is visible, and the activity log still refuses changes.

The PostgreSQL tools must match the server (16). A local ``pg_dump`` 16 or newer is used when there is one;
otherwise the ``postgres:16-alpine`` image runs them (Docker), with the password passed in the environment, never
on the command line.

What a backup does **not** hold: ``MASTER_KEY`` / the KMS key. Connection credentials and AI keys in the database
are encrypted with it, so keep the key's own backup separately (the runbook says where). A backup holds customer
data (uploaded files, cached snapshots, chats): store it encrypted and delete it on the retention schedule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

PG_IMAGE = "postgres:16-alpine"
SERVER_MAJOR = 16
TENANT_ROLE = "app_tenant"
MANIFEST = "manifest.json"
DUMP = "postgres.dump"
OBJECTS = "objects"


class BackupError(Exception):
    pass


# --------------------------------------------------------------------------------------- connections
@dataclass(frozen=True)
class PgTarget:
    """A database for the PostgreSQL tools: libpq parts, the password kept apart."""

    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_url(cls, url: str) -> PgTarget:
        parts = urlsplit(re.sub(r"^postgresql\+\w+://", "postgresql://", url))
        if parts.scheme not in ("postgresql", "postgres") or not parts.hostname:
            raise BackupError("The database URL must look like postgresql://user:pass@host:port/db")
        return cls(
            host=parts.hostname,
            port=parts.port or 5432,
            user=unquote(parts.username or ""),
            password=unquote(parts.password or ""),
            database=parts.path.lstrip("/") or "postgres",
        )

    def with_database(self, database: str) -> PgTarget:
        return PgTarget(self.host, self.port, self.user, self.password, database)

    def sqlalchemy_url(self) -> str:
        from urllib.parse import quote

        return (
            f"postgresql+psycopg://{quote(self.user, safe='')}:{quote(self.password, safe='')}"
            f"@{self.host}:{self.port}/{self.database}"
        )


def engine_for(target: PgTarget) -> Engine:
    return create_engine(target.sqlalchemy_url(), poolclass=NullPool)


def _local_tools_major() -> int | None:
    found = shutil.which("pg_dump")
    if not found:
        return None
    output = subprocess.run(  # noqa: S603 - a fixed, local tool
        [found, "--version"], capture_output=True, text=True, check=False
    ).stdout
    match = re.search(r"(\d+)\.", output)
    return int(match.group(1)) if match else None


def pg_command(
    tool: str, target: PgTarget, args: Sequence[str]
) -> tuple[list[str], dict[str, str]]:
    """The command line and environment for ``pg_dump`` / ``pg_restore`` / ``psql`` against ``target``."""
    local = _local_tools_major()
    env = {**os.environ, "PGPASSWORD": target.password}
    if local is not None and local >= SERVER_MAJOR:
        conn = ["-h", target.host, "-p", str(target.port), "-U", target.user, "-d", target.database]
        return [tool, *conn, *args], env
    if not shutil.which("docker"):
        raise BackupError(
            f"{tool} {SERVER_MAJOR} is needed: install the PostgreSQL {SERVER_MAJOR} client or Docker."
        )
    host = "host.docker.internal" if target.host in ("localhost", "127.0.0.1") else target.host
    conn = ["-h", host, "-p", str(target.port), "-U", target.user, "-d", target.database]
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--add-host=host.docker.internal:host-gateway",
        "-e",
        "PGPASSWORD",  # the value comes from this process's environment, not the command line
        PG_IMAGE,
        tool,
        *conn,
        *args,
    ]
    return command, env


def run_pg(
    tool: str,
    target: PgTarget,
    args: Sequence[str],
    *,
    stdin: Path | None = None,
    stdout: Path | None = None,
) -> None:
    command, env = pg_command(tool, target, args)
    with (
        open(stdin, "rb") if stdin else open(os.devnull, "rb") as source,
        open(stdout, "wb") if stdout else open(os.devnull, "wb") as sink,
    ):
        result = subprocess.run(  # noqa: S603 - fixed tools, arguments are ours
            command, stdin=source, stdout=sink, stderr=subprocess.PIPE, env=env, check=False
        )
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip().splitlines()[-5:]
        raise BackupError(f"{tool} failed: " + " / ".join(message))


# ------------------------------------------------------------------------------------- the manifest
def table_counts(conn: Connection) -> dict[str, int]:
    tables = conn.scalars(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    ).all()
    return {
        str(t): int(conn.scalar(text(f'SELECT count(*) FROM public."{t}"')) or 0)  # noqa: S608 - catalog names
        for t in tables
    }


def database_facts(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
        policies = sorted(
            f"{row[0]}.{row[1]}"
            for row in conn.execute(
                text("SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public'")
            )
        )
        rls = sorted(
            str(row[0])
            for row in conn.execute(
                text(
                    "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
                )
            )
        )
        triggers = sorted(
            f"{row[0]}.{row[1]}"
            for row in conn.execute(
                text(
                    "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
                )
            )
        )
        return {
            "revision": revision,
            "tables": table_counts(conn),
            "policies": policies,
            "rls_tables": rls,
            "triggers": triggers,
        }


# ----------------------------------------------------------------------------------------- objects
class Bucket:
    """The S3-compatible bucket, through the app's own client settings (``S3_*``)."""

    def __init__(self, client: Any, name: str) -> None:
        self.client = client
        self.name = name

    @classmethod
    def from_settings(cls, settings: Any) -> Bucket:
        from app.storage import make_s3_client

        return cls(make_s3_client(settings, timeout_seconds=60), settings.s3_bucket)

    def object_keys(self) -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.name):
            for item in page.get("Contents", []):
                yield str(item["Key"])

    def get(self, key: str) -> bytes:
        body: bytes = self.client.get_object(Bucket=self.name, Key=key)["Body"].read()
        return body

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.name, Key=key, Body=data)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.name, Key=key)


def _safe_path(root: Path, key: str) -> Path:
    path = (root / key).resolve()
    if not path.is_relative_to(root.resolve()) or key.startswith("/") or ".." in Path(key).parts:
        raise BackupError(f"Refusing an object key outside the backup folder: {key!r}")
    return path


def backup_objects(bucket: Bucket, folder: Path) -> dict[str, str]:
    root = folder / OBJECTS
    root.mkdir(parents=True, exist_ok=True)
    sums: dict[str, str] = {}
    for key in bucket.object_keys():
        data = bucket.get(key)
        path = _safe_path(root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        sums[key] = hashlib.sha256(data).hexdigest()
    return sums


def restore_objects(bucket: Bucket, folder: Path, sums: dict[str, str]) -> int:
    root = folder / OBJECTS
    for key, digest in sums.items():
        data = _safe_path(root, key).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise BackupError(f"The backup copy of {key} is damaged (checksum differs).")
        bucket.put(key, data)
    return len(sums)


def verify_objects(bucket: Bucket, sums: dict[str, str]) -> list[str]:
    problems: list[str] = []
    present = set(bucket.object_keys())
    for key, digest in sums.items():
        if key not in present:
            problems.append(f"object missing: {key}")
        elif hashlib.sha256(bucket.get(key)).hexdigest() != digest:
            problems.append(f"object differs: {key}")
    return problems


# ------------------------------------------------------------------------------------- the commands
def backup(target: PgTarget, out: Path, bucket: Bucket | None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = out / stamp
    folder.mkdir(parents=True, exist_ok=False)
    os.chmod(folder, 0o700)  # customer data: only this user may read it
    engine = engine_for(target)
    try:
        facts = database_facts(engine)
    finally:
        engine.dispose()
    run_pg("pg_dump", target, ["--format=custom", "--no-owner"], stdout=folder / DUMP)
    sums = backup_objects(bucket, folder) if bucket is not None else {}
    manifest = {
        "taken_at": datetime.now(UTC).isoformat(),
        "database": target.database,
        "dump_sha256": hashlib.sha256((folder / DUMP).read_bytes()).hexdigest(),
        "objects": sums,
        **facts,
    }
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return folder


def load_manifest(folder: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((folder / MANIFEST).read_text())
    digest = hashlib.sha256((folder / DUMP).read_bytes()).hexdigest()
    if digest != manifest["dump_sha256"]:
        raise BackupError("postgres.dump is damaged (checksum differs from the manifest).")
    return manifest


def prepare_roles(target: PgTarget) -> None:
    """The restricted role is cluster-wide, so a fresh server needs it before the grants can be restored."""
    engine = engine_for(target)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_tenant') THEN "
                    "CREATE ROLE app_tenant NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE; "
                    "END IF; END $$"
                )
            )
            conn.execute(text(f"GRANT {TENANT_ROLE} TO CURRENT_USER"))
    finally:
        engine.dispose()


def ensure_empty(target: PgTarget) -> None:
    engine = engine_for(target)
    try:
        with engine.connect() as conn:
            tables = conn.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"))
    finally:
        engine.dispose()
    if tables:
        raise BackupError(
            f"Database {target.database} is not empty ({tables} tables). Restore into a new, empty database "
            "and switch the app to it (docs/RUNBOOK_BACKUP_RESTORE.md); this tool never overwrites one."
        )


def restore(folder: Path, target: PgTarget, bucket: Bucket | None) -> dict[str, Any]:
    manifest = load_manifest(folder)
    ensure_empty(target)
    prepare_roles(target)
    run_pg(
        "pg_restore",
        target,
        ["--no-owner", "--exit-on-error", "--single-transaction"],
        stdin=folder / DUMP,
    )
    if bucket is not None:
        restore_objects(bucket, folder, manifest["objects"])
    return manifest


def _refused(conn: Connection, sql: str, params: dict[str, Any]) -> bool:
    """Runs ``sql`` in a savepoint: True when it was refused or changed no row (always rolled back)."""
    nested = conn.begin_nested()
    try:
        return conn.execute(text(sql), params).first() is None
    except Exception:
        return True
    finally:
        if nested.is_active:
            nested.rollback()


def check_isolation(engine: Engine) -> list[str]:
    """As ``app_tenant`` scoped to one workspace: no other workspace's row is visible or can be changed, and the
    activity log refuses changes (even from the owner)."""
    problems: list[str] = []
    with engine.connect() as conn:
        orgs = [row[0] for row in conn.execute(text("SELECT id FROM organizations LIMIT 2"))]
        conn.rollback()
        if len(orgs) < 2:
            return ["isolation not checked: fewer than two workspaces in the backup"]
        mine, other = orgs
        with conn.begin():
            conn.execute(text(f"SET LOCAL ROLE {TENANT_ROLE}"))
            conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": str(mine)})
            for table, column in (("organizations", "id"), ("memberships", "org_id")):
                seen = conn.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE {column} = :o"),  # noqa: S608 - fixed names
                    {"o": other},
                )
                if seen:
                    problems.append(f"RLS: {table} shows {seen} row(s) of another workspace")
            own = conn.scalar(
                text("SELECT count(*) FROM memberships WHERE org_id = :o"), {"o": mine}
            )
            if not own:
                problems.append(
                    "RLS: the workspace cannot see its own members (policy or grant missing)"
                )
            if not _refused(
                conn,
                "UPDATE memberships SET role = role WHERE org_id = :o RETURNING id",
                {"o": other},
            ):
                problems.append("RLS: another workspace's member could be changed")
            conn.rollback()
        with conn.begin():
            if not _refused(conn, "UPDATE audit_log SET action = action RETURNING id", {}):
                problems.append("the activity log accepted an UPDATE (append-only trigger missing)")
            if not _refused(conn, "DELETE FROM audit_log RETURNING id", {}):
                problems.append("the activity log accepted a DELETE (append-only trigger missing)")
            conn.rollback()
    return problems


def verify(folder: Path, target: PgTarget, bucket: Bucket | None) -> list[str]:
    manifest = load_manifest(folder)
    engine = engine_for(target)
    try:
        facts = database_facts(engine)
        problems = check_isolation(engine)
    finally:
        engine.dispose()
    if facts["revision"] != manifest["revision"]:
        problems.append(f"migration {facts['revision']} != {manifest['revision']}")
    for table, count in manifest["tables"].items():
        got = facts["tables"].get(table)
        if got != count:
            problems.append(f"rows in {table}: {got} restored, {count} backed up")
    for key in ("policies", "rls_tables", "triggers"):
        missing = sorted(set(manifest[key]) - set(facts[key]))
        if missing:
            problems.append(f"{key} missing: {', '.join(missing)}")
    if bucket is not None:
        problems += verify_objects(bucket, manifest["objects"])
    return problems


# --------------------------------------------------------------------------------------------- drill
def drill(keep: bool = False) -> int:
    """The tested path (``make backup-drill``): throwaway PostgreSQL 16 and S3 storage, two workspaces with data,
    backup, a wiped scratch database and bucket, restore, verify. Nothing outside the containers is touched."""
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.core.container import DockerContainer

    from app.config import Settings
    from loadtest.run import migrate, seed

    started = time.perf_counter()
    postgres = PostgresContainer(PG_IMAGE, driver="psycopg")
    access_key, secret_key = "drill-" + uuid.uuid4().hex[:12], uuid.uuid4().hex  # fresh per run
    storage = (
        DockerContainer("rustfs/rustfs:latest")
        .with_env("RUSTFS_ACCESS_KEY", access_key)
        .with_env("RUSTFS_SECRET_KEY", secret_key)
        .with_exposed_ports(9000)
    )
    work = Path(tempfile.mkdtemp(prefix="backup-drill-"))
    running: list[Any] = []
    try:
        print("1/6 Starting throwaway PostgreSQL 16 and S3 storage ...", flush=True)
        postgres.start()
        running.append(postgres)
        storage.start()
        running.append(storage)
        source = PgTarget.from_url(postgres.get_connection_url())
        settings = Settings(
            _env_file=None,
            app_env="test",
            database_url=source.sqlalchemy_url(),
            s3_endpoint=f"http://{storage.get_container_host_ip()}:{storage.get_exposed_port(9000)}",
            s3_access_key=access_key,
            s3_secret_key=secret_key,
            s3_bucket="drill-bucket",
            jwt_secret="drill-jwt-secret-" + uuid.uuid4().hex,
            master_key="drill-master-key-" + uuid.uuid4().hex,
        )
        bucket = Bucket.from_settings(settings)
        _wait_for_bucket(bucket)

        print(
            "2/6 Migrating and adding two workspaces with people, activity and files ...",
            flush=True,
        )
        migrate(source.sqlalchemy_url())
        engine = engine_for(source)
        try:
            workspaces = seed(engine, settings, users=6, per_org=3)
            _add_activity(engine, [w.org_id for w in workspaces])
        finally:
            engine.dispose()
        for w in workspaces:
            for n in range(3):
                bucket.put(f"orgs/{w.org_id}/files/{uuid.uuid4()}/v{n}", os.urandom(2048 + n))

        print("3/6 Backing up ...", flush=True)
        folder = backup(source, work, bucket)
        manifest = load_manifest(folder)
        print(
            f"    {sum(manifest['tables'].values())} rows in {len(manifest['tables'])} tables, "
            f"{len(manifest['objects'])} objects, {(folder / DUMP).stat().st_size // 1024} KB dump",
            flush=True,
        )

        print("4/6 Wiping a scratch database and the bucket ...", flush=True)
        scratch = source.with_database("restore_drill")
        admin = engine_for(source)
        try:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("DROP DATABASE IF EXISTS restore_drill"))
                conn.execute(text("CREATE DATABASE restore_drill"))
        finally:
            admin.dispose()
        for key in list(bucket.object_keys()):
            bucket.delete(key)
        assert not list(bucket.object_keys())

        print("5/6 Restoring into the scratch database and the bucket ...", flush=True)
        restore(folder, scratch, bucket)
        try:
            restore(folder, scratch, None)
        except BackupError:
            pass  # a second restore into a database that is no longer empty is refused, as it must be
        else:
            raise BackupError("A restore over a non-empty database was not refused.")

        print(
            "6/6 Verifying rows, policies, triggers, objects and tenant isolation ...", flush=True
        )
        problems = verify(folder, scratch, bucket)
    finally:
        for container in reversed(running):
            try:
                container.stop()
            except Exception as error:  # pragma: no cover - best effort clean-up
                print(f"Could not stop a container: {error}", file=sys.stderr)
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    elapsed = time.perf_counter() - started
    if problems:
        print("\nRESTORE DRILL FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"\nRestore drill passed in {elapsed:.0f} s: every table, policy, trigger and object came back, and"
    )
    print("one workspace still cannot see another's rows after the restore.")
    return 0


def _wait_for_bucket(bucket: Bucket, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            bucket.client.create_bucket(Bucket=bucket.name)
            return
        except Exception as error:
            code = str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                return
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _add_activity(engine: Engine, org_ids: list[uuid.UUID]) -> None:
    from app.audit import writer as audit
    from app.db.session import make_session_factory

    factory = make_session_factory(engine)
    with factory() as db:
        for org_id in org_ids:
            for action in ("auth.login.success", "connection.added", "question.asked"):
                audit.write(
                    db,
                    action=action,
                    at=datetime.now(UTC),
                    meta=audit.RequestMeta("203.0.113.9", "drill"),
                    org_id=org_id,
                )
        db.commit()


# ---------------------------------------------------------------------------------------------- CLI
def _bucket_from_env(wanted: bool) -> Bucket | None:
    if not wanted:
        return None
    from app.config import get_settings

    return Bucket.from_settings(get_settings())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ops.backup", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup", help="database (and objects) to a new folder")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--database-url", default="", help="default: DATABASE_URL")
    b.add_argument("--no-objects", action="store_true", help="database only")
    for name in ("restore", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--from", dest="folder", type=Path, required=True)
        p.add_argument("--database-url", required=True, help="the new, empty (restore) database")
        p.add_argument(
            "--objects", action="store_true", help="also restore/verify the bucket (S3_*)"
        )
    d = sub.add_parser("drill", help="the whole cycle in throwaway containers (make backup-drill)")
    d.add_argument("--keep", action="store_true", help="keep the backup folder (prints where)")
    args = parser.parse_args(argv)
    try:
        if args.command == "drill":
            return drill(keep=args.keep)
        if args.command == "backup":
            from app.config import get_settings

            url = args.database_url or get_settings().database_url
            folder = backup(PgTarget.from_url(url), args.out, _bucket_from_env(not args.no_objects))
            print(f"Backup written to {folder}")
            return 0
        target = PgTarget.from_url(args.database_url)
        bucket = _bucket_from_env(args.objects)
        if args.command == "restore":
            restore(args.folder, target, bucket)
            print(f"Restored into {target.database}. Now run: verify --from {args.folder} ...")
            return 0
        problems = verify(args.folder, target, bucket)
        for problem in problems:
            print(f"  - {problem}")
        print("Verified." if not problems else "Verification FAILED.")
        return 0 if not problems else 1
    except BackupError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
