"""Parse Forest2 nightly result exports (CSV) into one summary row per test artifact.

The big exports carry a multi-line JSON PAYLOAD and a huge NETWORK_LOGS column per row. We stream
the CSV, keep only the small summary fields, and cache the result on disk so a 700MB+ file is only
parsed once. Slim exports that only have WORKFLOW_NAME and SOURCE_FILE are supported too (date and
test name come from the file name, status is "unknown").
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pandas as pd

csv.field_size_limit(sys.maxsize)

HERE = Path(__file__).parent
IMPORT_DIR = HERE / "imports"
CACHE_DIR = HERE / ".cache"
FILE_GLOB = "Forest2_Results*.csv"

COLUMNS = [
    "source_file", "export_file", "artifact_name", "workflow", "brand", "test_name", "platform", "browser",
    "environment", "status", "passed", "run_date", "started_at", "ended_at", "duration_s",
    "steps_total", "steps_failed", "validations_total", "validations_failed", "error",
    "first_failure", "head_branch", "build_name", "run_id",
]

_FILE_TS = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z")


def _file_timestamp(source_file: str):
    m = _FILE_TS.search(source_file or "")
    if not m:
        return pd.NaT
    d, hh, mm, ss, ms = m.groups()
    return pd.Timestamp(f"{d}T{hh}:{mm}:{ss}.{ms}", tz="UTC")


def _test_name_from_file(source_file: str) -> str:
    name = re.sub(r"^results-", "", source_file or "")
    name = _FILE_TS.sub("", name).rstrip("-.json").removesuffix(".json").strip("-")
    return name


def _first_failure(payload: dict) -> str | None:
    items = (payload.get("validations") or {}).get("items") or []
    for item in items:
        if item.get("outcome") == "failed":
            fails = item.get("failures") or (item.get("validationSummary") or {}).get("failures") or []
            detail = ""
            if fails:
                f = fails[0]
                detail = f" ({f.get('path')}: expected {f.get('expected')}, got {f.get('actual')})"
            return f"{item.get('testName') or item.get('tag')}{detail}"
    return None


def _row_from_payload(row: dict, payload: dict) -> dict:
    steps = payload.get("steps") or {}
    vals = payload.get("validations") or {}
    summary = payload.get("summary") or {}
    if summary and not steps:  # nightly scan rows report per-site totals instead of steps
        steps = {"total": summary.get("sites"), "failed": summary.get("failed")}
    bs = payload.get("browserstack") or {}
    gh = payload.get("github") or {}
    passed = payload.get("passed")
    started = pd.to_datetime(payload.get("startedAt"), utc=True, errors="coerce")
    ended = pd.to_datetime(payload.get("endedAt"), utc=True, errors="coerce")
    browser = payload.get("browserString")
    if not browser and isinstance(payload.get("browser"), dict):
        b = payload["browser"]
        browser = f"{b.get('os', '')} {b.get('browser', '')}".strip() or None
    err = payload.get("error")
    return {
        "source_file": row.get("SOURCE_FILE"),
        "artifact_name": row.get("ARTIFACT_NAME") or None,
        "workflow": row.get("WORKFLOW_NAME") or payload.get("workflow"),
        "brand": payload.get("brand"),
        "test_name": payload.get("testName") or payload.get("workflow") or row.get("WORKFLOW_NAME"),
        "platform": payload.get("platform"),
        "browser": browser,
        "environment": payload.get("environment"),
        "passed": passed,
        "started_at": started,
        "ended_at": ended,
        "duration_s": (ended - started).total_seconds() if pd.notna(started) and pd.notna(ended) else None,
        "steps_total": steps.get("total"),
        "steps_failed": steps.get("failed"),
        "validations_total": vals.get("total"),
        "validations_failed": vals.get("failed"),
        "error": err if isinstance(err, str) else (json.dumps(err) if err else None),
        "first_failure": _first_failure(payload),
        "head_branch": row.get("HEAD_BRANCH") or gh.get("ref"),
        "build_name": bs.get("buildName"),
        "run_id": row.get("WORKFLOW_RUN_ID") or payload.get("runId"),
        "_created": row.get("ARTIFACT_CREATED_AT"),
    }


def parse_export(path: Path) -> pd.DataFrame:
    records = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw = row.get("PAYLOAD")
            payload = {}
            if raw:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}
            rec = _row_from_payload(row, payload)
            if not raw:
                rec["test_name"] = _test_name_from_file(row.get("SOURCE_FILE", ""))
            records.append(rec)

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)

    # Date: when the test ran; fall back to artifact creation time, then the file name timestamp.
    for col in ("started_at", "ended_at"):  # all-empty columns (slim exports) arrive as object dtype
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    created = pd.to_datetime(df.pop("_created"), utc=True, errors="coerce")
    from_name = df["source_file"].map(_file_timestamp)
    when = df["started_at"].fillna(created).fillna(from_name)
    df["run_date"] = when.dt.tz_convert("America/New_York").dt.date
    df["status"] = df["passed"].map({True: "Passed", False: "Failed"}).fillna("Unknown")
    df["passed"] = df["passed"].astype("boolean")
    df["export_file"] = path.name
    return df[COLUMNS]


def list_exports(*folders: Path) -> list[Path]:
    seen: dict[str, Path] = {}
    for folder in folders:
        if folder.is_dir():
            for p in folder.glob(FILE_GLOB):
                if p.suffix.lower() == ".csv":
                    seen.setdefault(str(p.resolve()), p)
    return sorted(seen.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def load_export(path: Path) -> pd.DataFrame:
    """Parse once, then reuse an on-disk cache keyed by file name, size, and mtime."""
    st = path.stat()
    CACHE_DIR.mkdir(exist_ok=True)
    cache = CACHE_DIR / f"{path.stem}__{st.st_size}_{int(st.st_mtime)}__v2.pkl"  # bump v when COLUMNS change
    if cache.exists():
        return pd.read_pickle(cache)
    df = parse_export(path)
    df.to_pickle(cache)
    return df
