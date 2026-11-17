"""Opt-in isolated full CLI fill; no production mutation or replacement SQL."""

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import resource
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import sqlalchemy as sa

CANDIDATE = "33ed69cb0b445d7daf580c6192c05e433eff2d7d"
REVISION = "20261114_29"
SOURCE_LOCK = 734190220


def validate_url(raw):
    url = sa.engine.make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in ("127.0.0.1", "localhost")
        or not re.fullmatch(r"category_fill_lab_[a-f0-9]{12}", url.database or "")
        or url.username != "sales_fill_app"
    ):
        raise ValueError("isolated_lab_url_required")
    return url


def save(path, data):
    path = Path(path)
    temporary = path.with_suffix(".pending")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        if os.geteuid() == 0 and os.environ.get("LAB_RESULT_UID"):
            os.chown(
                temporary,
                int(os.environ["LAB_RESULT_UID"]),
                int(os.environ["LAB_RESULT_GID"]),
            )
        json.dump(data, stream, indent=2, default=str)
    temporary.replace(path)


def allocated_bytes(root):
    """Allocated PGDATA blocks, incl. WAL/pgsql_tmp and persistent snapshots.
    Files can disappear during sorting. Permission errors are NOT ignored.
    Post-sample maxima are not a filesystem high-water mark.
    """
    root = Path(root)
    if (
        not root.is_dir()
        or (root / "PG_VERSION").read_text().strip() != "16"
        or not (root / "base").is_dir()
    ):
        raise OSError("pgdata_unreadable")
    if (root / "pg_wal").is_symlink() or any((root / "pg_tblspc").glob("*")):
        raise OSError("external_storage_not_measured")
    total = 0
    for directory, _, files in os.walk(
        root, onerror=lambda exc: (_ for _ in ()).throw(exc)
    ):
        for name in files:
            try:
                total += (Path(directory) / name).stat().st_blocks * 512
            except FileNotFoundError:
                pass
    return total


def memory():
    result = {
        "process_hwm_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    }
    for key, filename in (
        ("cgroup_peak_bytes", "memory.peak"),
        ("cgroup_current_bytes", "memory.current"),
        ("cgroup_limit_bytes", "memory.max"),
        ("cgroup_swap_current_bytes", "memory.swap.current"),
    ):
        try:
            result[key] = int(Path("/sys/fs/cgroup", filename).read_text())
        except (OSError, ValueError):
            result[key] = None
    try:
        result["host_available_bytes"] = next(
            int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
    except (OSError, StopIteration, ValueError):
        result["host_available_bytes"] = None
    return result


def sync_engine(url):
    return sa.create_engine(
        url.set(drivername="postgresql+psycopg"), poolclass=sa.pool.NullPool
    )


def schema_check(db, expected_version):
    version = db.scalar(sa.text("SHOW server_version_num"))
    if int(version) != expected_version:
        raise RuntimeError("unexpected_postgres_version")
    locale = dict(
        db.execute(
            sa.text(
                "SELECT datctype,datcollate FROM pg_database WHERE datname=current_database()"
            )
        )
        .mappings()
        .one()
    )
    return dict(server_version_num=int(version), **locale)


def privileges(db):
    role = dict(
        db.execute(
            sa.text(
                "SELECT current_user AS role,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
            )
        )
        .mappings()
        .one()
    )
    owner = db.scalar(
        sa.text(
            "SELECT relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) FROM pg_class WHERE oid='category_season_clients'::regclass"
        )
    )
    acl = db.scalar(
        sa.text(
            "SELECT coalesce(relacl::text,'') FROM pg_class WHERE oid='category_season_clients'::regclass"
        )
    )
    if role["rolsuper"] or role["rolcreatedb"] or role["rolcreaterole"] or not owner:
        raise RuntimeError("non_superuser_application_owner_required")
    return dict(
        **role, owns_clients=owner, acl_sha256=hashlib.sha256(acl.encode()).hexdigest()
    )


def bootstrap(url):
    """Only a NEW container/cluster: create owner profile once, never GRANT on fill."""
    import psycopg
    from psycopg import sql

    admin = sa.engine.make_url(os.environ["LAB_ADMIN_URL"])
    if admin.host not in ("127.0.0.1", "localhost") or admin.database != "postgres":
        raise ValueError("isolated_admin_required")
    with psycopg.connect(
        admin.set(drivername="postgresql").render_as_string(hide_password=False),
        autocommit=True,
    ) as db:
        if db.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='sales_fill_app')"
        ).fetchone()[0]:
            raise RuntimeError("refuse_existing_cluster_role")
        db.execute(
            sql.SQL(
                "CREATE ROLE sales_fill_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
            ).format(sql.Literal(url.password))
        )
        db.execute(
            sql.SQL("CREATE DATABASE {} OWNER sales_fill_app").format(
                sql.Identifier(url.database)
            )
        )


def seed(url, args):
    from alembic.config import Config
    from alembic import command
    from app.config import settings
    from app.services.catalog_attributes import identity
    from app.models import CatalogProduct, CatalogLookup, Base
    import start_backend

    engine = sync_engine(url)
    with engine.connect() as db:
        schema_check(db, args.expected_version)
        if db.scalar(
            sa.text(
                "SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace"
            )
        ):
            raise RuntimeError("refuse_nonempty_lab_database")
    settings.database_url = url.render_as_string(hide_password=False)
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(start_backend.__file__).parent / "alembic")
    )
    command.upgrade(cfg, "20261111_27")
    with engine.begin() as db:
        db.execute(
            sa.text(
                "CREATE INDEX ix_sales_department_date_control ON sales(department,sale_date)"
            )
        )
    command.upgrade(cfg, REVISION)
    start_backend.check_schema(engine, Base.metadata, REVISION)
    with engine.begin() as db:
        db.execute(
            sa.text(
                "CREATE TABLE category_fill_lab_marker(id int PRIMARY KEY CHECK(id=1), profile jsonb NOT NULL)"
            )
        )
        db.execute(
            sa.text("INSERT INTO category_fill_lab_marker VALUES(1,CAST(:v AS jsonb))"),
            {"v": json.dumps(vars(args), default=str)},
        )
        db.execute(
            sa.text(
                "INSERT INTO import_batches(id,filename,file_size,status) VALUES(1,'synthetic',1,'completed')"
            )
        )
    # All identifiers/values are synthetic. Bounded inserts keep generation memory small.
    for start in range(1, args.sales + 1, 10000):
        with engine.begin() as db:
            db.execute(sa.text("SET LOCAL statement_timeout='60s'"))
            db.execute(
                sa.text(
                    """INSERT INTO sales(id,sale_date,document_number,client,department,total_amount,fingerprint,import_id)
              SELECT n,date '2023-01-01'+((n*17)%1095)::int,'lab-'||n::text,'client_'||(n%:clients)::text,
              'store_'||(n%4)::text,0,md5('lab-sale-'||n::text),1 FROM generate_series(CAST(:start AS bigint),CAST(:stop AS bigint)) n"""
                ),
                dict(
                    start=start,
                    stop=min(args.sales, start + 9999),
                    clients=args.clients,
                ),
            )
    for start in range(1, args.items + 1, 25000):
        with engine.begin() as db:
            db.execute(sa.text("SET LOCAL statement_timeout='60s'"))
            db.execute(
                sa.text(
                    """INSERT INTO sale_items(id,sale_id,code,article,name,quantity,actual_price)
              SELECT n,1+(n-1)%:sales,CASE WHEN n%7=0 THEN NULL ELSE lpad((n%:products)::text,8,'0') END,
              'article_'||(n%:products)::text,'Synthetic product '||repeat('x',64),(n%5)-1,((n%997)+1)::numeric/100
              FROM generate_series(CAST(:start AS bigint),CAST(:stop AS bigint)) n"""
                ),
                dict(
                    start=start,
                    stop=min(args.items, start + 24999),
                    sales=args.sales,
                    products=args.products,
                ),
            )
    # Two local attribute records for each code/article variant, no network lookup.
    for start in range(0, args.products, 250):
        products, lookups = [], []
        for n in range(start, min(args.products, start + 250)):
            for missing in (False, True):
                pid = 1 + n + int(missing) * args.products
                code = "" if missing else str(n).zfill(8)
                article = "article_" + str(n)
                products.append(
                    dict(
                        product_id=pid,
                        code=code,
                        article=article,
                        category_id=1 + int(missing),
                        category="Synthetic",
                        subcategory="Section",
                        legacy_category="Section",
                        brand="brand",
                        manufacturer="maker",
                        horeca=False,
                        source_updated_at=datetime.now(timezone.utc),
                        synced_at=datetime.now(timezone.utc),
                    )
                )
                lookups.append(
                    dict(
                        identity=identity(code, article),
                        code=code,
                        article=article,
                        product_id=pid,
                        status="matched",
                        matched_by="code",
                        cycle=0,
                    )
                )
        with engine.begin() as db:
            db.execute(sa.insert(CatalogProduct), products)
            db.execute(sa.insert(CatalogLookup), lookups)
    with engine.begin() as db:
        for table in ("sales", "sale_items", "catalog_products", "catalog_lookups"):
            db.execute(sa.text("ANALYZE " + table))
        counts = dict(
            db.execute(
                sa.text(
                    "SELECT (SELECT count(*) FROM sales) sales,(SELECT count(*) FROM sale_items) items"
                )
            )
            .mappings()
            .one()
        )
        profile = dict(
            schema_check(db, args.expected_version),
            privileges=privileges(db),
            counts=counts,
            database_bytes=db.scalar(
                sa.text("SELECT pg_database_size(current_database())")
            ),
            source_relation_bytes={
                table: db.scalar(
                    sa.text("SELECT pg_total_relation_size(CAST(:table AS regclass))"),
                    {"table": table},
                )
                for table in (
                    "sales",
                    "sale_items",
                    "catalog_products",
                    "catalog_lookups",
                )
            },
        )
    save(args.output, dict(status="seeded", candidate=CANDIDATE, **profile))
    engine.dispose()


class Stub:
    """Real localhost HTTP; production ClientsVR adapter/filters remain untouched."""

    def __init__(self, clients):
        self.clients = clients
        self.requests = 0
        self.unknown = 0

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests += 1
                path = urlsplit(self.path).path
                if path == "/integration/managers":
                    value = ["LabManager"]
                elif path == "/integration/buyer-types":
                    value = ["LabBuyer"]
                elif path in (
                    "/integration/clients",
                    "/integration/buyer-types/LabBuyer/clients",
                ):
                    value = [
                        dict(
                            name="client_" + str(n),
                            manager="LabManager",
                            buyer_type="LabBuyer",
                        )
                        for n in range(outer.clients)
                    ]
                else:
                    outer.unknown += 1
                    self.send_error(404)
                    return
                data = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Observer:
    def __init__(self, engine, root, interval=0.25):
        self.engine, self.root, self.interval = engine, Path(root), interval
        self.stop = threading.Event()
        self.samples = []
        self.error = None

    def disk(self):
        try:
            return allocated_bytes(self.root)
        except OSError as exc:
            self.error = type(exc).__name__
            return None

    def __enter__(self):
        def run():
            try:
                with self.engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as db:
                    while not self.stop.is_set():
                        row = dict(
                            db.execute(
                                sa.text(
                                    "SELECT active_id,next_due FROM category_season_state WHERE id=1"
                                )
                            )
                            .mappings()
                            .one()
                        )
                        locks = db.scalar(
                            sa.text(
                                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=734190220 AND granted"
                            )
                        )
                        self.samples.append(
                            dict(
                                t=time.perf_counter(),
                                boottime=time.clock_gettime(time.CLOCK_BOOTTIME),
                                allocated=self.disk(),
                                memory=memory(),
                                source_lock=locks,
                                state=row,
                            )
                        )
                        self.stop.wait(self.interval)
            except BaseException as exc:
                self.error = type(exc).__name__

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=10)


def fill(url, args):
    from app.database import engine
    from app import category_seasonality as cli
    from app.config import settings
    from app.services import category_seasonality_snapshot as service
    from app.services.report_execution import REPORT_SETTINGS
    import sys

    import asyncpg, platform

    observer_engine = sync_engine(url)
    with observer_engine.connect() as db:
        schema = schema_check(db, args.expected_version)
        if db.scalar(sa.text("SELECT version_num FROM alembic_version")) != REVISION:
            raise RuntimeError("schema29_required")
        if db.scalar(sa.text("SELECT count(*) FROM category_fill_lab_marker")) != 1:
            raise RuntimeError("lab_marker_required")
        before = privileges(db)
        prior = dict(
            db.execute(
                sa.text(
                    "SELECT active_id,next_due FROM category_season_state WHERE id=1"
                )
            )
            .mappings()
            .one()
        )
        counts = dict(
            db.execute(
                sa.text("SELECT profile FROM category_fill_lab_marker WHERE id=1")
            ).scalar_one()
        )
        old_analyze = db.scalar(
            sa.text(
                "SELECT last_analyze FROM pg_stat_all_tables WHERE relid='category_season_clients'::regclass"
            )
        )
        if db.scalar(sa.text("SHOW temp_tablespaces")):
            raise RuntimeError("external_temp_tablespace_not_measured")
    metrics = dict(
        sql_count=0, max_sql_seconds=0, max_sql_stage=None, stages={}, lock={}
    )

    def before_sql(conn, cursor, statement, parameters, context, many):
        if statement.strip() == "ANALYZE category_season_clients":
            cursor.execute(
                "SELECT current_user,current_setting('statement_timeout'),current_setting('transaction_isolation'),current_setting('work_mem'),current_setting('hash_mem_multiplier'),current_setting('max_parallel_workers_per_gather'),current_setting('temp_file_limit')"
            )
            role, timeout, isolation, work_mem, hash_mem, parallel, temp_limit = (
                cursor.fetchone()
            )
            metrics["analyze_probe"] = dict(
                role=role,
                statement_timeout=timeout,
                isolation=isolation,
                work_mem=work_mem,
                hash_mem_multiplier=hash_mem,
                max_parallel_workers_per_gather=parallel,
                temp_file_limit=temp_limit,
            )
            if timeout != "1min" or isolation != "repeatable read":
                raise RuntimeError("candidate_settings_changed")
        context._lab_started = time.perf_counter()

    def after_sql(conn, cursor, statement, parameters, context, many):
        elapsed = time.perf_counter() - context._lab_started
        stage = service._BUILD_STAGE.get()[0]
        metrics["sql_count"] += 1
        item = metrics["stages"].setdefault(
            stage, dict(sql_count=0, sql_seconds=0, max_sql_seconds=0)
        )
        item["sql_count"] += 1
        item["sql_seconds"] += elapsed
        item["max_sql_seconds"] = max(item["max_sql_seconds"], elapsed)
        if elapsed > metrics["max_sql_seconds"]:
            metrics.update(max_sql_seconds=elapsed, max_sql_stage=stage)
        values = (
            parameters.values() if isinstance(parameters, dict) else parameters or ()
        )
        if SOURCE_LOCK in values:
            if "pg_try_advisory_lock(" in statement:
                metrics["lock"]["acquire_before"] = context._lab_started
                metrics["lock"]["acquire_after"] = time.perf_counter()
            if "pg_advisory_unlock(" in statement:
                metrics["lock"]["release_before"] = context._lab_started
                metrics["lock"]["release_after"] = time.perf_counter()

    class Stages(logging.Handler):
        def emit(self, record):
            if record.msg == "category_snapshot_stage stage=%s duration_ms=%.3f":
                stage, ms = record.args
                metrics.setdefault("stage_wall_seconds", {}).setdefault(stage, 0)
                metrics["stage_wall_seconds"][stage] += ms / 1000

    stage_handler = Stages()
    stage_logger = logging.getLogger(service.__name__)
    stage_logger.setLevel(logging.INFO)
    stage_logger.addHandler(stage_handler)
    sa.event.listen(engine.sync_engine, "before_cursor_execute", before_sql)
    sa.event.listen(engine.sync_engine, "after_cursor_execute", after_sql)

    def failed_sql(context):
        execution = getattr(context, "execution_context", None)
        began = getattr(execution, "_lab_started", None)
        duration = None if began is None else time.perf_counter() - began
        if duration is not None and duration > metrics["max_sql_seconds"]:
            metrics.update(
                max_sql_seconds=duration, max_sql_stage=service._BUILD_STAGE.get()[0]
            )
        metrics["failed_sql"] = {
            "seconds": duration,
            "stage": service._BUILD_STAGE.get()[0],
            "sqlstate": service.sqlstate(context.original_exception),
            "type": type(context.original_exception).__name__,
        }

    sa.event.listen(engine.sync_engine, "handle_error", failed_sql)
    report = dict(
        status="running",
        runtime=dict(
            python=platform.python_version(),
            asyncpg=asyncpg.__version__,
            sqlalchemy=sa.__version__,
        ),
        candidate=CANDIDATE,
        scope="smoke" if args.smoke else "full_fill",
        schema=schema,
        prior=prior,
        privileges_before=before,
        report_settings=list(REPORT_SETTINGS),
        disk_measurement="sampled allocated PGDATA, not exact filesystem peak",
        sampling_seconds=0.25,
    )
    save(args.output, report)
    started = time.perf_counter()
    result = None
    error = None
    cli_elapsed = None
    baseline_disk = None
    try:
        baseline_disk = allocated_bytes(args.pg_data)
    except OSError:
        if not args.smoke:
            raise
    with Stub(counts["clients"]) as stub:
        settings.clients_vr_api_url = "http://127.0.0.1:" + str(stub.server.server_port)
        settings.clients_vr_api_token = "synthetic-only"
        with Observer(observer_engine, args.pg_data) as observe:
            try:
                # Exact ordinary CLI, including locks, all preparation, publication,
                # retention and disposal. No helper/SQL replacement or fake API loaders.
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    saved_argv = sys.argv
                    sys.argv = ["category_seasonality", "fill", "--confirm"]
                    cli_begin_boottime = time.clock_gettime(time.CLOCK_BOOTTIME)
                    cli_started = time.perf_counter()
                    try:
                        cli.main()
                    finally:
                        cli_elapsed = time.perf_counter() - cli_started
                        cli_end_boottime = time.clock_gettime(time.CLOCK_BOOTTIME)
                        sys.argv = saved_argv
                result = json.loads(output.getvalue().strip())
            except BaseException as exc:
                error = dict(type=type(exc).__name__, sqlstate=service.sqlstate(exc))
    elapsed = time.perf_counter() - started
    with observer_engine.connect() as db:
        after = privileges(db)
        state = dict(
            db.execute(
                sa.text(
                    "SELECT active_id,next_due FROM category_season_state WHERE id=1"
                )
            )
            .mappings()
            .one()
        )
        snapshots = [
            dict(row)
            for row in db.execute(
                sa.text(
                    "SELECT id,status,fact_rows,started_at FROM category_season_snapshots ORDER BY started_at"
                )
            ).mappings()
        ]
        last_analyze = db.scalar(
            sa.text(
                "SELECT last_analyze FROM pg_stat_all_tables WHERE relid='category_season_clients'::regclass"
            )
        )
        if result and result.get("status") == "published":
            totals = dict(
                db.execute(
                    sa.text(
                        "SELECT count(*) groups,sum(lines) lines,sum(units) units,sum(revenue) revenue FROM category_season_facts WHERE snapshot_id=:sid"
                    ),
                    {"sid": result["id"]},
                )
                .mappings()
                .one()
            )
        else:
            totals = None
    lock = metrics["lock"]
    if len(lock) == 4:
        metrics["import_lock_seconds_lower"] = (
            lock["release_before"] - lock["acquire_after"]
        )
        metrics["import_lock_seconds_upper"] = (
            lock["release_after"] - lock["acquire_before"]
        )
    samples = observe.samples
    disk_max = max(
        (s["allocated"] for s in samples if s["allocated"] is not None), default=None
    )
    valid_states = all(
        s["state"] == prior
        or (
            result
            and s["state"]["active_id"] == result.get("id")
            and s["state"]["next_due"] is not None
        )
        for s in samples
    )
    analyze_ok = last_analyze is not None and (
        old_analyze is None or last_analyze > old_analyze
    )
    success = (
        error is None
        and result
        and result.get("status") == "published"
        and state["active_id"] == result["id"]
        and state["next_due"] is not None
        and before == after
        and analyze_ok
        and valid_states
        and len(lock) == 4
        and any(s["source_lock"] for s in samples)
    )
    if not args.smoke:
        success = success and observe.error is None and disk_max is not None
    retained = prior["active_id"] is None or any(
        s["id"] == prior["active_id"]
        and s["status"] == "published"
        and s["fact_rows"] > 0
        for s in snapshots
    )
    published = next(
        (s for s in snapshots if result and s["id"] == result.get("id")), None
    )
    next_due_valid = published is not None and state["next_due"] == service.next_slot(
        published["started_at"]
    )
    success = success and retained and next_due_valid
    if success:
        with observer_engine.connect() as db:
            db.execute(sa.text("SET LOCAL statement_timeout='60s'"))
            expected = dict(
                db.execute(
                    sa.text(
                        "SELECT count(*) lines,sum(i.quantity) units,coalesce(sum(i.quantity*i.actual_price),0) revenue FROM sale_items i JOIN sales s ON s.id=i.sale_id WHERE s.sale_date<:end"
                    ),
                    {"end": service.cutoff(published["started_at"])},
                )
                .mappings()
                .one()
            )
        success = all(totals[k] == expected[k] for k in expected)
    else:
        expected = None
    report.update(
        failure_atomic=(state == prior if error is not None else None),
        resource_measurement_complete=observe.error is None and disk_max is not None,
        expected_totals=expected,
        next_due_valid=next_due_valid,
        cli_begin_boottime=cli_begin_boottime,
        cli_end_boottime=cli_end_boottime,
        previous_snapshot_retained=retained,
        cli_wall_seconds=cli_elapsed,
        resource_samples=samples,
        status="complete" if success else "failed",
        result=result,
        error=error,
        wall_seconds=elapsed,
        metrics=metrics,
        analyze_executed=analyze_ok,
        privileges_after=after,
        state=state,
        snapshots=snapshots,
        totals=totals,
        atomic_observed=valid_states,
        observer_error=observe.error,
        samples=len(samples),
        disk_baseline_bytes=baseline_disk,
        disk_max_sampled_bytes=disk_max,
        disk_growth_sampled_bytes=(
            None
            if disk_max is None or baseline_disk is None
            else max(0, disk_max - baseline_disk)
        ),
        backend_memory=memory(),
        stub_requests=stub.requests,
        stub_unknown_paths=stub.unknown,
    )
    save(args.output, report)
    observer_engine.dispose()
    if not success:
        raise RuntimeError("full_fill_lab_incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["bootstrap", "seed", "fill"])
    parser.add_argument("--confirm-synthetic-lab", required=True, action="store_true")
    parser.add_argument("--expected-version", type=int, default=160016)
    parser.add_argument("--sales", type=int, default=1190000)
    parser.add_argument("--items", type=int, default=4600000)
    parser.add_argument("--products", type=int, default=36706)
    parser.add_argument("--clients", type=int, default=20000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pg-data", default="/pgdata")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if not args.smoke and args.expected_version != 160016:
        raise SystemExit("full_test_requires_postgresql_16_16")
    if args.output.exists() or any(
        n < 1 for n in [args.sales, args.items, args.products, args.clients]
    ):
        raise SystemExit("new_output_and_positive_counts_required")
    url = validate_url(os.environ["DATABASE_URL"])
    try:
        if args.phase == "bootstrap":
            bootstrap(url)
        elif args.phase == "seed":
            seed(url, args)
        else:
            fill(url, args)
    except BaseException as exc:
        if args.output.exists():
            partial = json.loads(args.output.read_text())
            if partial.get("status") == "running":
                partial.update(status="incomplete", harness_error=type(exc).__name__)
                save(args.output, partial)
        # Driver exceptions may contain SQL/parameters/passwords. Never stringify.
        raise SystemExit("FULL_FILL_LAB_FAILED type=" + type(exc).__name__) from None


if __name__ == "__main__":
    main()
