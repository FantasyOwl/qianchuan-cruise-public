"""Shared request queue, token buckets and durable Ocean Engine cooldowns.

Limits below are conservative LOCAL policy, not a claim about platform quotas.
Stores endpoint/account identifiers, timestamps, counters and redacted errors;
never credentials, request bodies or URLs.
SQLite transactions coordinate readers, writers and other local processes.
"""
import math
import random
import sqlite3
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from datetime import datetime,timedelta,timezone

READ_PRIORITY = 0
READBACK_PRIORITY = 1
WRITE_PRIORITY = 2
_request_priority = ContextVar('official_request_priority', default=READ_PRIORITY)


@contextmanager
def prioritize_readback():
    previous = _request_priority.set(READBACK_PRIORITY)
    try:
        yield
    finally:
        _request_priority.reset(previous)


def request_priority(method):
    return WRITE_PRIORITY if method == 'POST' else _request_priority.get()


class RateDeferred(ValueError):
    def __init__(self, code, reason, retry_after, origin='local_queue',limit_kind='unknown'):
        self.code, self.reason, self.retry_after = code, reason, max(1, math.ceil(retry_after))
        self.origin=origin
        self.limit_kind=limit_kind
        super().__init__(reason)


class RateGovernor:
    def __init__(self, path=None, *, clock=None, sleeper=None, max_wait=65,jitter=None):
        self.clock, self.sleep = clock or time.time, sleeper or time.sleep
        self.max_wait = max_wait
        self.jitter=jitter or (lambda:random.uniform(0,3))
        self.lock = threading.RLock()
        self.waiting = 0
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path=str(path) if path else None
        self.memory=self._connect() if path is None else None
        with self._connection() as db:db.executescript('''
            CREATE TABLE IF NOT EXISTS rate_events(id INTEGER PRIMARY KEY, at REAL NOT NULL,
                endpoint TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'sent');
            CREATE INDEX IF NOT EXISTS rate_events_at ON rate_events(at);
            CREATE INDEX IF NOT EXISTS rate_events_endpoint ON rate_events(endpoint,at);
            CREATE TABLE IF NOT EXISTS rate_queue(id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL, priority INTEGER NOT NULL, queued_at REAL NOT NULL,
                deadline REAL NOT NULL, live_until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS rate_breakers(endpoint TEXT PRIMARY KEY, until_at REAL NOT NULL,
                last_at REAL NOT NULL, strikes INTEGER NOT NULL, code INTEGER NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rate_totals(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rate_failures(id INTEGER PRIMARY KEY,at REAL NOT NULL,
                endpoint TEXT NOT NULL,code INTEGER NOT NULL,reason TEXT,request_id TEXT,hint TEXT,scope TEXT);
        ''')
        with self.lock,self._connection() as db,db:
            db.execute('BEGIN IMMEDIATE')
            if 'server_until' not in {r[1] for r in db.execute('PRAGMA table_info(rate_breakers)')}:
                db.execute('ALTER TABLE rate_breakers ADD COLUMN server_until REAL NOT NULL DEFAULT 0')
            for table in ('rate_breakers','rate_failures'):
                if 'limit_kind' not in {r[1] for r in db.execute('PRAGMA table_info('+table+')')}:
                    db.execute('ALTER TABLE '+table+" ADD COLUMN limit_kind TEXT NOT NULL DEFAULT 'unknown'")
                db.execute('UPDATE '+table+" SET limit_kind=CASE code WHEN 40100 THEN 'platform_total' WHEN 40110 THEN 'developer_qps' ELSE limit_kind END WHERE limit_kind='unknown'")
            # Legacy 40100 delays were generated locally (no Retry-After was
            # passed for business responses). Do not preserve an invented
            # 16/32/60 minute platform penalty. Explicit server deadlines stay.
            db.execute("UPDATE rate_breakers SET until_at=min(until_at,last_at+60) WHERE code=40100 AND reason='rate_limited' AND server_until=0")
            # Both official 40100/40110 quotas concern one endpoint, not every
            # interface in this application. Retain its own endpoint deadline.
            db.execute("DELETE FROM rate_breakers WHERE endpoint='*' AND code IN (40100,40110)")

    def _connect(self):
        db=sqlite3.connect(self.path or ':memory:',timeout=2,check_same_thread=False)
        db.row_factory=sqlite3.Row
        return db

    @contextmanager
    def _connection(self):
        db=self.memory or self._connect()
        try:yield db
        finally:
            if self.memory is None:db.close()

    def close(self):
        with self.lock:
            if self.memory is not None:self.memory.close()

    def _count(self, db, key):
        db.execute('INSERT INTO rate_totals VALUES (?,1) ON CONFLICT(key) DO UPDATE SET value=value+1', (key,))

    @staticmethod
    def _wait_for_window(db, endpoint, now, window, maximum):
        if endpoint is None:
            rows = db.execute('SELECT at FROM rate_events WHERE at>? ORDER BY at DESC LIMIT ?',
                              (now-window, maximum)).fetchall()
        else:
            rows = db.execute('SELECT at FROM rate_events WHERE endpoint=? AND at>? ORDER BY at DESC LIMIT ?',
                              (endpoint, now-window, maximum)).fetchall()
        return max(0, rows[-1]['at']+window-now) if len(rows)==maximum else 0

    def _endpoint_wait(self, db, endpoint, now):
        penalty = db.execute("SELECT strikes,last_at,limit_kind FROM rate_breakers WHERE endpoint=? AND reason='rate_limited'", (endpoint,)).fetchone()
        factor = 1
        if penalty:
            platform = penalty['limit_kind']=='platform_total'
            if now-penalty['last_at'] < (300 if platform else 3600):
                factor = 2**min(3, penalty['strikes'])
        return max(self._wait_for_window(db, endpoint, now, min(30.05,3.05*factor), 1),
                   self._wait_for_window(db, endpoint, now, 60.05, 18))

    def _attempt(self, ticket, endpoint):
        now = self.clock()
        with self.lock,self._connection() as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM rate_queue WHERE deadline<? OR live_until<?', (now, now))
                refreshed = db.execute('UPDATE rate_queue SET live_until=? WHERE id=?', (now+6, ticket))
                if refreshed.rowcount != 1:
                    self._count(db, 'locally_deferred')
                    raise RateDeferred(429, 'rate_limited', 1)
                blocked = db.execute('SELECT * FROM rate_breakers WHERE endpoint IN (?,?) AND until_at>? ORDER BY until_at DESC LIMIT 1', (endpoint, '*', now)).fetchone()
                if blocked:
                    self._count(db,'locally_deferred')
                    raise RateDeferred(blocked['code'], blocked['reason'], blocked['until_at']-now,'local_cooldown',blocked['limit_kind'])
                # Capacity-one buckets refill from the last dispatch; the log
                # is the shared token state. Minute ceilings remain separate.
                wait = max(self._wait_for_window(db, None, now, 2.05, 1),
                           self._wait_for_window(db, None, now, 60.05, 25),
                           self._endpoint_wait(db, endpoint, now))
                if wait > 0:
                    return None, wait
                # Aging gives a long-waiting ordinary read its turn. Tickets
                # for cooling or paced endpoints cannot block the rest.
                contenders = db.execute('''
                    SELECT q.id,q.endpoint FROM rate_queue q
                    WHERE NOT EXISTS (SELECT 1 FROM rate_breakers b
                        WHERE b.endpoint=q.endpoint AND b.until_at>?)
                    ORDER BY CASE WHEN q.queued_at<=? THEN ? ELSE q.priority END DESC,q.id
                ''', (now, now-12, WRITE_PRIORITY)).fetchall()
                seen = set()
                for contender in contenders:
                    if contender['endpoint'] in seen:
                        continue
                    if contender['id'] == ticket:
                        break
                    seen.add(contender['endpoint'])
                    if self._endpoint_wait(db, contender['endpoint'], now)==0:
                        return None, .2
                else:
                    return None, .2
                db.execute('DELETE FROM rate_queue WHERE id=?', (ticket,))
                db.execute('DELETE FROM rate_events WHERE at<?', (now-86400,))
                row = db.execute('INSERT INTO rate_events(at,endpoint) VALUES (?,?)', (now, endpoint))
                self._count(db,'dispatched')
                return row.lastrowid, 0
            finally:
                db.commit()

    def acquire(self, endpoint, *, priority=READ_PRIORITY):
        started = self.clock()
        deadline = started + self.max_wait
        with self.lock,self._connection() as db,db:
            db.execute('DELETE FROM rate_queue WHERE deadline<? OR live_until<?', (started, started))
            ticket = db.execute('INSERT INTO rate_queue(endpoint,priority,queued_at,deadline,live_until) VALUES (?,?,?,?,?)',
                                (endpoint, priority, started, deadline, started+6)).lastrowid
        with self.lock:
            self.waiting += 1
        try:
            while True:
                receipt, wait = self._attempt(ticket, endpoint)
                if receipt is not None: return receipt
                if self.clock()-started+wait > self.max_wait:
                    with self.lock,self._connection() as db,db: self._count(db,'locally_deferred')
                    raise RateDeferred(429, 'rate_limited', wait)
                # No mutex/SQLite transaction is held while waiting. A stop or
                # readback using another endpoint can take the next shared slot.
                self.sleep(min(wait, 1.0))
        finally:
            with self.lock:
                self.waiting -= 1
                with self._connection() as db,db:
                    db.execute('DELETE FROM rate_queue WHERE id=?', (ticket,))

    def outcome(self, receipt, status):
        with self.lock,self._connection() as db,db:
            db.execute('UPDATE rate_events SET outcome=? WHERE id=?', (status, receipt))
            self._count(db,status)

    def trip(self, endpoint, code, reason, retry_after=0,limit_kind=None):
        now = self.clock()
        kind=limit_kind or ('platform_total' if code==40100 else 'developer_qps' if code==40110 else 'unknown')
        with self.lock,self._connection() as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                prior = db.execute('SELECT * FROM rate_breakers WHERE endpoint=?', (endpoint,)).fetchone()
                strikes = min(7, prior['strikes']+1) if prior and prior['limit_kind']==kind and now-prior['last_at'] < 3600 else 1
                explicit=min(172800,max(0,retry_after))
                if kind=='day_penalty':
                    day=datetime.fromtimestamp(now,timezone(timedelta(hours=8)))
                    base=((day+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)-day).total_seconds()
                elif kind=='minute_penalty':base=300
                elif kind=='platform_total':base=min(40,10*2**min(2,strikes-1))+max(0,min(3,self.jitter()))
                else:base=60 if reason=='rate_limited' else 30
                delay=max(explicit,base)
                until = max(now+delay, prior['until_at'] if prior else 0)
                server_until=max(now+explicit if explicit else 0,now+base if kind in {'minute_penalty','day_penalty'} else 0,prior['server_until'] if prior else 0)
                db.execute('INSERT OR REPLACE INTO rate_breakers VALUES (?,?,?,?,?,?,?,?)', (endpoint, until, now, strikes, code, reason,server_until,kind))
                return math.ceil(until-now)
            finally:
                db.commit()

    def failure(self,endpoint,code,reason,request_id,hint,scope,limit_kind='unknown'):
        with self.lock,self._connection() as db,db:
            db.execute('INSERT INTO rate_failures(at,endpoint,code,reason,request_id,hint,scope,limit_kind) VALUES (?,?,?,?,?,?,?,?)',
                (self.clock(),endpoint,code,reason,request_id,hint,scope,limit_kind))
            if reason=='rate_limited' and limit_kind in {'platform_total','developer_qps','minute_penalty','day_penalty'}:self._count(db,limit_kind+'_limited')
            db.execute('DELETE FROM rate_failures WHERE id NOT IN (SELECT id FROM rate_failures ORDER BY id DESC LIMIT 100)')

    def status(self):
        now = self.clock()
        with self.lock,self._connection() as db:
            counts = dict(db.execute('SELECT key,value FROM rate_totals'))
            rows = db.execute('SELECT * FROM rate_breakers WHERE until_at>? ORDER BY endpoint', (now,)).fetchall()
            minute = db.execute('SELECT count(*) FROM rate_events WHERE at>?', (now-60,)).fetchone()[0]
            second = db.execute('SELECT count(*) FROM rate_events WHERE at>?', (now-1,)).fetchone()[0]
            day = db.execute('SELECT count(*) FROM rate_events WHERE at>?', (now-86400,)).fetchone()[0]
            pending = db.execute('SELECT endpoint,count(*) AS pending FROM rate_queue WHERE deadline>=? AND live_until>=? GROUP BY endpoint ORDER BY endpoint', (now,now)).fetchall()
            persistent=self.path is not None
            errors=[dict(r) for r in db.execute('SELECT at,endpoint,code,reason,request_id,hint,scope,limit_kind FROM rate_failures ORDER BY id DESC LIMIT 5')]
        return {'policy_source':'conservative_local_policy', 'shared_read_write':True,
            'admission':'shared_priority_queue', 'limiter':'capacity_one_buckets',
            'queue_pending':sum(r['pending'] for r in pending), 'queue_by_endpoint':[dict(r) for r in pending],
            'persistent':persistent,
            'requests_last_second':second, 'requests_last_minute':minute, 'requests_last_24h':day,
            'waiting_local':self.waiting, 'counters':counts,
            'last_platform_errors':errors,
            'cooldowns':[{'endpoint':r['endpoint'],'retry_after_seconds':math.ceil(r['until_at']-now),
                'until':r['until_at'],'code':r['code'],'reason':r['reason'],'limit_kind':r['limit_kind'],'strikes':r['strikes']} for r in rows]}


_default = None
_default_lock = threading.Lock()


def default_governor():
    global _default
    with _default_lock:
        if _default is None: _default = RateGovernor()
        return _default
