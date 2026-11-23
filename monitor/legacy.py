"""Read only the original DBs. No original HTTP API or automation client calls."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .schema import normalize_snapshot, now_cn


def redact(text):
    text = re.sub(r"(?i)(?:bearer\s+)[\w.\-]+", "Bearer [已隐藏]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+", r"\1[已隐藏]", text)
    return re.sub(r"[?&](?:token|key|signature|X-Bogus|msToken)=[^&\s]+", "", text, flags=re.I)


def read_rows(path, table, limit=5000):
    if not path.is_file():
        return []
    # mode=ro refuses creation; query_only prevents accidental write statements.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as c:
        c.execute("PRAGMA query_only=ON")
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(f'SELECT * FROM "{table}" ORDER BY id DESC LIMIT ?', (limit,))]


def inspect_legacy(settings):
    root = Path(settings["legacy_root"])
    allowed = {a["id"] for a in settings["accounts"]}
    findings = {"state": "observing", "mode": "read_only", "original_modified": False,
                "excluded_accounts": [], "observed_rows": {}, "last_metrics_at": None,
                "issues": [], "events": [], "log_tail": [], "config_observations": {}}
    snapshots, excluded = [], set()
    for db, table in [("web_console.db", "account_metrics"), ("qianchuan_inspector.db", "plan_snapshots"),
                      ("qianchuan_inspector.db", "control_task_snapshots"), ("qianchuan_inspector.db", "material_snapshots")]:
        try:
            rows = read_rows(root/"data"/db, table)
        except (sqlite3.Error, OSError) as exc:
            findings["issues"].append(f"{table}: {type(exc).__name__}")
            continue
        findings["observed_rows"][table] = len(rows)
        if table == "account_metrics" and rows:
            findings["last_metrics_at"] = rows[0].get("captured_at")
        for r in rows:
            if r.get("account_id") not in allowed:
                excluded.add(str(r.get("account_id", "")))
                continue
            metrics = {}
            basis = "unknown"
            scope = {"account_metrics": "account", "plan_snapshots": "plan", "control_task_snapshots": "control", "material_snapshots": "creative"}[table]
            for dst, src in {"spend": "spend", "gmv": "deal", "roi": "roi", "budget": "budget", "orders": "orders",
                             "clicks": "clicks", "ctr": "ctr", "cvr": "cvr", "balance": "balance"}.items():
                if src in r:
                    metrics[dst] = r[src]
            if table == "material_snapshots":
                metrics.update(spend=r.get("overall_spend"), roi=r.get("overall_roi"))
            if table == "plan_snapshots":
                metrics.update(spend=r.get("today_spend"), orders=r.get("today_orders"), budget=r.get("control_budget"), target_roi=None)
            issues = ["旧程序字段口径未经校验；仅展示，不参与调控"]
            # Historical date follows the record, never today's date.
            captured = r.get("captured_at", "")
            raw = {"account_id": r["account_id"], "entity_id": str(r.get("plan_id") or r.get("task_id") or r.get("material_id") or r["account_id"]),
                   "scope": scope, "metric_basis": basis, "promotion_type": "unknown", "metrics": metrics,
                   "source": {"kind": "legacy_db", "dataset_id": "legacy-"+table, "captured_at": captured, "data_at": None,
                              "metric_date": r.get("metric_date") or captured[:10], "coverage": "visible_only"},
                   "quality": {"issues": issues}}
            try:
                snapshots.append(normalize_snapshot(raw, settings))
            except (ValueError, TypeError):
                findings["issues"].append(f"{table} 中有不符合数据规范的记录，已跳过")
    findings["excluded_accounts"] = sorted(excluded)
    if excluded:
        findings["issues"].append("旧数据库含有白名单以外账户，已排除其投放指标")
    for table in ("operation_events", "metric_refresh_events"):
        try:
            rows = read_rows(root/"data"/"web_console.db", table, 25)
            for r in rows:
                findings["events"].append({k: redact(str(r.get(k, "")))[:1200] for k in
                    ("captured_at", "level", "action", "result", "detail", "status", "message")})
        except (sqlite3.Error, OSError):
            pass
    log = root/"logs"/"web_console.log"
    if log.is_file():
        with log.open("rb") as f:
            f.seek(max(0, log.stat().st_size - 24000))
            tail = f.read()
        # Decode UTF-8 first; use fallback only when the file actually uses legacy encoding.
        try:
            decoded = tail.decode("utf-8")
        except UnicodeDecodeError:
            decoded = tail.decode("utf-8", errors="replace")
        lines = decoded.splitlines()[-100:]
        findings["log_tail"] = [redact(x)[:1000] for x in lines if '"GET /api/' not in x][-35:]
        if "TargetClosedError" in decoded:
            findings["issues"].append("旧巡航近期出现 TargetClosedError，浏览器连接已断开或页面关闭")
    config = root/"config.yaml"
    if config.is_file():
        content = config.read_text("utf-8", errors="replace")
        dry = re.search(r"^\s*dry_run:\s*(true|false)\b", content, re.M)
        links = re.findall(r"^\s*(?:live_manage_url|control_page_url|material_page_url):\s*(\S+)", content, re.M)
        ids = sorted({parse_qs(urlsplit(u).query).get("aavid", [""])[0] for u in links})
        findings["config_observations"] = {"dry_run": dry.group(1) == "true" if dry else None,
                                            "configured_account_ids": ids,
                                            "accounts_match": bool(ids) and set(ids) <= allowed}
        if dry and dry.group(1) == "false":
            findings["issues"].append("旧程序 dry_run=false；本助手仅旁观，未改动旧程序的运行开关")
        if ids and not set(ids) <= allowed:
            findings["issues"].append("旧程序配置中的账户 ID 与示例分组A白名单不一致")
    if not root.is_dir():
        findings["state"] = "missing"
        findings["issues"].append("找不到旧程序目录，请检查 config.json 中的 legacy_root")
    findings["issues"] = list(dict.fromkeys(findings["issues"]))
    findings["scoped_snapshots"] = len(snapshots)
    findings["observed_at"] = now_cn().isoformat()
    return snapshots, findings
