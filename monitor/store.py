from __future__ import annotations

import json
import sqlite3
import threading
from datetime import timedelta
from contextlib import contextmanager
from pathlib import Path

from .schema import now_cn, parse_time
from .observation_time import review_observation_time


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL, data_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS snapshot_account_time ON snapshots(account_id,captured_at);
                CREATE INDEX IF NOT EXISTS snapshot_time ON snapshots(captured_at);
                CREATE TABLE IF NOT EXISTS series_latest (
                    series_key TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, captured_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                    event TEXT NOT NULL, details_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS diagnostics (
                    key TEXT PRIMARY KEY, updated_at TEXT NOT NULL, data_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operation_reports (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, after_at TEXT NOT NULL,
                    received_at TEXT NOT NULL, data_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS report_time ON operation_reports(after_at);
                CREATE TABLE IF NOT EXISTS operation_report_heads (
                    object_key TEXT PRIMARY KEY, report_id TEXT NOT NULL, after_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS budget_migrations (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
                    data_json TEXT NOT NULL, final_review_json TEXT);
                CREATE TABLE IF NOT EXISTS monitor_cycles (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, state TEXT NOT NULL, added INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT);
            ''')
            if 'final_review_json' not in {row[1] for row in c.execute('PRAGMA table_info(budget_migrations)')}:
                c.execute('ALTER TABLE budget_migrations ADD COLUMN final_review_json TEXT')
            # Rebuild only missing series heads for older local databases. The
            # original cruise database is never opened by this Store class.
            for (value,) in c.execute("SELECT data_json FROM snapshots ORDER BY captured_at"):
                s=json.loads(value)
                self._head(c,s)

    @staticmethod
    def _head(c,s):
        src=s["source"]
        identity=json.dumps([s["account_id"],s["entity_id"],s["scope"],s["metric_basis"],src.get("series_key","")],ensure_ascii=False)
        c.execute("""INSERT INTO series_latest VALUES (?,?,?)
                     ON CONFLICT(series_key) DO UPDATE SET snapshot_id=excluded.snapshot_id,captured_at=excluded.captured_at
                     WHERE excluded.captured_at>=series_latest.captured_at""",(identity,s["id"],src["captured_at"]))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, snapshots):
        added = 0
        with self.lock, self.connect() as c:
            for s in snapshots:
                added += c.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)", (
                    s["id"], s["account_id"], s["source"]["captured_at"], json.dumps(s, ensure_ascii=False, allow_nan=False))).rowcount
                self._head(c,s)
        return added

    def snapshots(self, limit=20000):
        with self.connect() as c:
            rows = c.execute("SELECT data_json FROM snapshots ORDER BY captured_at DESC LIMIT ?", (limit,)).fetchall()
        return [review_observation_time(json.loads(row[0])) for row in reversed(rows)]

    def latest_observations(self, account_id):
        """Current per-source heads for cruise rounds; does not scan old windows."""
        with self.connect() as c:
            rows = c.execute('SELECT DISTINCT s.data_json FROM snapshots s JOIN series_latest h ON h.snapshot_id=s.id WHERE s.account_id=?', (account_id,)).fetchall()
        return [review_observation_time(json.loads(r[0])) for r in rows]

    def evaluation_snapshots(self, now=None, hours=28, account_id=None, series_key=None):
        """No global row cap may truncate a 24h per-object observation window.

        Include each source's last known row even if it is older, so stalled
        sources remain visible. Historical snapshots remain stored for audit.
        """
        since=((now or now_cn())-timedelta(hours=hours)).isoformat()
        query="""WITH wanted AS (
                   SELECT id FROM snapshots WHERE captured_at>=?
                   UNION SELECT snapshot_id FROM series_latest)
                 SELECT data_json FROM snapshots WHERE id IN wanted ORDER BY captured_at"""
        with self.connect() as c:rows=c.execute(query,(since,)).fetchall()
        values=[review_observation_time(json.loads(r[0])) for r in rows]
        return [s for s in values if (account_id is None or s["account_id"]==account_id)
                and (series_key is None or s["source"].get("series_key")==series_key)]

    def add_events(self,events):
        added=0
        with self.lock,self.connect() as c:
            for event in events:
                raw=json.dumps(event,ensure_ascii=False,sort_keys=True,allow_nan=False)
                old=c.execute("SELECT data_json FROM operation_reports WHERE id=?",(event["id"],)).fetchone()
                if old and json.loads(old[0])!=event:raise ValueError("操作报告ID已有不同内容，已拒绝整批覆盖")
                added+=c.execute("INSERT OR IGNORE INTO operation_reports VALUES (?,?,?,?,?)",
                    (event["id"],event["account_id"],event["after_at"],now_cn().isoformat(),raw)).rowcount
                key=json.dumps([event[k] for k in ("account_id","entity_id","scope","action","source","status")])
                c.execute("""INSERT INTO operation_report_heads VALUES (?,?,?)
                             ON CONFLICT(object_key) DO UPDATE SET report_id=excluded.report_id,after_at=excluded.after_at
                             WHERE excluded.after_at>=operation_report_heads.after_at""",(key,event["id"],event["after_at"]))
        return added

    def events(self,account_id=None,now=None,hours=28):
        since=((now or now_cn())-timedelta(hours=hours)).isoformat()
        with self.connect() as c:
            rows=c.execute("""WITH wanted AS (
                         SELECT id FROM operation_reports WHERE after_at>=?
                         UNION SELECT report_id FROM operation_report_heads)
                         SELECT data_json FROM operation_reports WHERE id IN wanted ORDER BY after_at""",(since,)).fetchall()
        values=[json.loads(r[0]) for r in rows]
        return [e for e in values if account_id is None or e["account_id"]==account_id]

    def recovery_history(self, snapshots, events):
        """Fetch explicit linked episode history without widening all strategy windows."""
        snapshots=snapshots if isinstance(snapshots,list) else []
        events=events if isinstance(events,list) else []
        heads = {}
        for row in snapshots:
            if row.get('scope') != 'plan':
                continue
            src = row.get('source', {})
            key = (row['account_id'], row['entity_id'], row.get('scene'), row.get('promotion_type'),
                   row.get('metric_basis'), src.get('kind'), src.get('series_key'))
            clock = (src.get('metric_date', ''), src.get('captured_at', ''))
            if key not in heads or clock > heads[key][0]:
                heads[key] = clock, row
        wanted = {(s['account_id'], s['entity_id'], s.get('context', {}).get('roi_recovery_event_id'))
                  for _, s in heads.values() if isinstance(s.get('context'), dict)
                  and isinstance(s['context'].get('roi_recovery_event_id'), str)}
        if not wanted:
            return [review_observation_time(row) for row in snapshots], events
        extra_snapshots, extra_events = {}, {}
        with self.connect() as c:
            for account_id, entity_id, root_id in wanted:
                found = c.execute('SELECT data_json FROM operation_reports WHERE id=? AND account_id=?',
                                  (root_id, account_id)).fetchone()
                if not found:
                    continue
                root = json.loads(found[0])
                if root.get('entity_id') != entity_id or root.get('scope') != 'plan' or root.get('action') != 'roi_target_down':
                    continue
                since = (parse_time(root['before_at'])-timedelta(hours=2, minutes=5)).isoformat()
                for (value,) in c.execute('SELECT data_json FROM snapshots WHERE account_id=? AND captured_at>=? ORDER BY captured_at', (account_id, since)):
                    row = json.loads(value)
                    if row.get('scope') == 'plan' and row.get('entity_id') == entity_id and row.get('source',{}).get('series_key') == root.get('source_key'):
                        extra_snapshots[row['id']] = row
                # Include unrelated same-plan reports too, so retention cannot hide interference.
                for (value,) in c.execute('SELECT data_json FROM operation_reports WHERE account_id=? AND after_at>=? ORDER BY after_at', (account_id, root['before_at'])):
                    row = json.loads(value)
                    if row.get('entity_id') == entity_id:
                        extra_events[row['id']] = row
        for row in snapshots:
            extra_snapshots.setdefault(row['id'], row)
        for row in events:
            extra_events.setdefault(row['id'], row)
        return [review_observation_time(row) for row in extra_snapshots.values()], list(extra_events.values())

    def audit(self, event, details):
        with self.lock, self.connect() as c:
            c.execute("INSERT INTO audit(created_at,event,details_json) VALUES (?,?,?)", (
                now_cn().isoformat(), event, json.dumps(details, ensure_ascii=False, allow_nan=False)))

    def add_migration(self, plan):
        with self.lock, self.connect() as c:
            c.execute("INSERT INTO budget_migrations(id,created_at,data_json) VALUES (?,?,?)",
                      (plan["id"], plan["created_at"], json.dumps(plan, ensure_ascii=False, allow_nan=False)))

    def migrations(self):
        with self.connect() as c:
            return [{**json.loads(raw), "cancelled": bool(cancelled), "final_review": json.loads(final) if final else None}
                    for raw, cancelled, final in c.execute("SELECT data_json,cancelled,final_review_json FROM budget_migrations ORDER BY created_at")]

    def finalize_migration(self, review):
        if review.get('state') != 'completed_reported' or review.get('executed') is not False:
            raise ValueError('只归档已交叉对照且未由本程序执行的交接复核')
        with self.lock, self.connect() as c:
            c.execute("UPDATE budget_migrations SET final_review_json=? WHERE id=? AND final_review_json IS NULL",
                      (json.dumps(review, ensure_ascii=False, allow_nan=False), review['id']))

    def cancel_migration(self, plan_id):
        with self.lock, self.connect() as c:
            if not c.execute("UPDATE budget_migrations SET cancelled=1 WHERE id=?", (plan_id,)).rowcount:
                raise ValueError("本地预算交接计划不存在")

    def audit_rows(self, limit=100):
        with self.connect() as c:
            return [{"id": x[0], "created_at": x[1], "event": x[2], "details": json.loads(x[3])} for x in
                    c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]

    def diagnostic(self, key, details):
        with self.lock, self.connect() as c:
            c.execute("INSERT OR REPLACE INTO diagnostics VALUES (?,?,?)", (
                key, now_cn().isoformat(), json.dumps(details, ensure_ascii=False, allow_nan=False)))

    def patch_diagnostic(self, key, details):
        with self.lock, self.connect() as c:
            old = c.execute('SELECT data_json FROM diagnostics WHERE key=?', (key,)).fetchone()
            value = {**(json.loads(old[0]) if old else {}), **details}
            c.execute('INSERT OR REPLACE INTO diagnostics VALUES (?,?,?)',
                      (key, now_cn().isoformat(), json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def begin_cycle(self, run_id, started_at):
        with self.lock, self.connect() as c:
            cursor = c.execute('INSERT INTO monitor_cycles(run_id,started_at,state) VALUES (?,?,?)',
                               (run_id, started_at, 'running'))
            # Keep at most 10,000 actual rounds; do not turn the retained span into uptime.
            c.execute('DELETE FROM monitor_cycles WHERE id NOT IN (SELECT id FROM monitor_cycles ORDER BY id DESC LIMIT 10000)')
            return cursor.lastrowid

    def finish_cycle(self, cycle_id, finished_at, *, added=0, error_type=None):
        state = 'error' if error_type else 'ok'
        with self.lock, self.connect() as c:
            c.execute('UPDATE monitor_cycles SET finished_at=?,state=?,added=?,error_type=? WHERE id=? AND state=?',
                      (finished_at, state, added, error_type, cycle_id, 'running'))

    def cycles(self):
        with self.connect() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute('SELECT * FROM monitor_cycles ORDER BY id')]

    def diagnostics(self):
        with self.connect() as c:
            return {row[0]: {"updated_at": row[1], **json.loads(row[2])} for row in c.execute("SELECT * FROM diagnostics")}
