"""Republish generator dumps as vendor-shaped source landings.

Notebook 03 is left unchanged. This adapter reads its JSON and writes
Kafka-like, object-store, SFTP, CDC, and API drops a real company would ingest.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

# Simulator-only keys. Real vendors do not ship these.
SIM_FIELDS = {
    "_financial_edge_case",
    "_time_chaos_type",
    "_clock_skew_seconds",
    "_source_server",
    "_source_time_delta_seconds",
    "_hot_partition_field",
    "_hot_partition_value",
    "_truncated_record",
    "_original_field_count",
    "_remaining_field_count",
    "_encoding_corruption_field",
    "_oversized_payload",
    "_oversized_field_kb",
    "_ref_integrity_violation",
    "_validation_errors",
    "_is_valid",
    "_burst",
    "_burst_cause",
    "_replay",
    "_replay_run_id",
}

# Topic (generator EVENT_TYPE) -> how a real company would receive it.
TOPIC_SOURCES: Dict[str, Dict[str, Any]] = {
    "bid_requests": {
        "topic": "prod.dsp.google.bid-requests",
        "source_system": "dsp_google",
        "vendor": "Google DV360",
        "source_type": "kafka",
        "mode": "streaming",
        "landing": "kafka/dsp_google/bid-requests",
        "envelope": "kafka",
        "kafka_partitions": 12,
        "key_field": "user_id",
    },
    "impressions": {
        "topic": "ssp.openx.impressions.hourly",
        "source_system": "ssp_openx",
        "vendor": "OpenX SSP",
        "source_type": "object_storage",
        "mode": "micro_batch",
        "landing": "object_store/ssp_openx/impressions",
        "envelope": "cdn_log",
    },
    "clicks": {
        "topic": "prod.clicktracker.clicks",
        "source_system": "click_tracker",
        "vendor": "First-party redirector",
        "source_type": "kafka",
        "mode": "streaming",
        "landing": "kafka/click_tracker/clicks",
        "envelope": "kafka",
        "kafka_partitions": 8,
        "key_field": "user_id",
        "late_dump_landing": "object_store/click_tracker/clicks_late",
        "late_dump_pct": 0.12,
    },
    "conversions": {
        "topic": "mmp.appsflyer.conversions.daily",
        "source_system": "mmp_appsflyer",
        "vendor": "AppsFlyer",
        "source_type": "sftp",
        "mode": "daily_batch",
        "landing": "sftp/mmp_appsflyer/conversions",
        "envelope": "mmp_csv",
        "webhook_landing": "webhook/mmp_appsflyer/conversions",
        "webhook_pct": 0.25,
    },
    "campaign_cdc": {
        "topic": "prod.campaign-db.public.campaigns",
        "source_system": "campaign_db",
        "vendor": "Debezium / PostgreSQL",
        "source_type": "kafka_cdc",
        "mode": "streaming",
        "landing": "kafka/campaign_db/cdc",
        "envelope": "debezium",
        "kafka_partitions": 3,
        "key_field": "campaign_id",
    },
    "billing_records": {
        "topic": "finance.dv360.invoices.daily",
        "source_system": "billing_dv360",
        "vendor": "Google DV360 invoice",
        "source_type": "sftp",
        "mode": "daily_batch",
        "landing": "sftp/billing/dv360_invoices",
        "envelope": "invoice_csv",
    },
    "ledger_entries": {
        "topic": "finance.erp.netsuite.gl.daily",
        "source_system": "erp_netsuite",
        "vendor": "NetSuite GL extract",
        "source_type": "sftp",
        "mode": "daily_batch",
        "landing": "sftp/erp_netsuite/gl",
        "envelope": "gl_csv",
    },
    "refunds": {
        "topic": "payments.stripe.disputes",
        "source_system": "payments_stripe",
        "vendor": "Stripe disputes API",
        "source_type": "api",
        "mode": "hourly_batch",
        "landing": "api/payments_stripe/refunds",
        "envelope": "stripe_event",
    },
}

CDC_OP_MAP = {
    "heartbeat": "r",
    "budget_update": "u",
    "pricing_model_change": "u",
    "pricing_change": "u",
    "status_change": "u",
    "pause_activate": "u",
    "targeting_update": "u",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def strip_sim_fields(record: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    payload = {k: v for k, v in record.items() if k not in SIM_FIELDS}
    labels = {k: v for k, v in record.items() if k in SIM_FIELDS}
    labels["event_id"] = record.get("event_id")
    labels["event_type"] = record.get("event_type") or record.get("EVENT_TYPE")
    return payload, labels


def _partition_for(key: Any, n_partitions: int) -> int:
    raw = str(key or "null").encode("utf-8")
    return int(hashlib.md5(raw).hexdigest(), 16) % max(n_partitions, 1)


def wrap_kafka(payload: Dict[str, Any], spec: Dict[str, Any], offset: int) -> Dict[str, Any]:
    key_field = spec.get("key_field", "event_id")
    key = payload.get(key_field) or payload.get("event_id")
    n_part = int(spec.get("kafka_partitions", 8))
    return {
        "topic": spec["topic"],
        "partition": _partition_for(key, n_part),
        "offset": offset,
        "timestamp": payload.get("event_timestamp") or now_iso(),
        "timestamp_type": "CreateTime",
        "key": str(key),
        "headers": {
            "source_system": spec["source_system"],
            "producer": spec["vendor"],
        },
        "value": payload,
    }


def wrap_cdn_log(payload: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "log_source": spec["vendor"],
        "log_ingest_time": now_iso(),
        "cdn_pop": payload.get("geo_country") or "unknown",
        **payload,
    }


def wrap_debezium(payload: Dict[str, Any], spec: Dict[str, Any], offset: int) -> Dict[str, Any]:
    change_type = payload.get("change_type") or "u"
    op = CDC_OP_MAP.get(change_type, "u")
    ts = payload.get("event_timestamp") or now_iso()
    key = payload.get("campaign_id")
    return {
        "topic": spec["topic"],
        "partition": _partition_for(key, int(spec.get("kafka_partitions", 3))),
        "offset": offset,
        "key": {"campaign_id": key},
        "value": {
            "before": payload.get("before") or None,
            "after": payload.get("after") or payload,
            "source": {
                "version": "2.5.0.Final",
                "connector": "postgresql",
                "name": "campaign-db",
                "ts_ms": ts,
                "snapshot": "false",
                "db": "campaigns",
                "schema": "public",
                "table": "campaigns",
            },
            "op": op,
            "ts_ms": ts,
        },
    }


def wrap_stripe_event(payload: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": f"evt_{payload.get('event_id', uuid.uuid4())}",
        "object": "event",
        "api_version": "2024-06-20",
        "created": payload.get("event_timestamp") or now_iso(),
        "type": "charge.dispute.created",
        "livemode": True,
        "data": {"object": payload},
        "request": {"id": None, "idempotency_key": payload.get("event_id")},
    }


def wrap_record(
    record: Dict[str, Any],
    spec: Dict[str, Any],
    offset: int,
) -> Dict[str, Any]:
    payload, _labels = strip_sim_fields(record)
    env = spec.get("envelope")
    if env == "kafka":
        return wrap_kafka(payload, spec, offset)
    if env == "cdn_log":
        return wrap_cdn_log(payload, spec)
    if env == "debezium":
        return wrap_debezium(payload, spec, offset)
    if env == "stripe_event":
        return wrap_stripe_event(payload, spec)
    # mmp_csv / invoice_csv / gl_csv: payload only; CSV writer uses this dict
    payload["_source_system"] = spec["source_system"]
    payload["_source_type"] = spec["source_type"]
    payload["_vendor"] = spec["vendor"]
    payload["_ingest_timestamp"] = now_iso()
    return payload


def iter_json_records(text: str) -> Iterator[Dict[str, Any]]:
    """Parse NDJSON, literal backslash-n separators, or concatenated objects."""
    if not text or not text.strip():
        return
    normalized = text.replace("\\n", "\n")
    decoder = json.JSONDecoder()
    idx = 0
    n = len(normalized)
    while idx < n:
        while idx < n and normalized[idx].isspace():
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(normalized, idx)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            yield obj
        idx = end


def read_generator_file(path: Path) -> List[Dict[str, Any]]:
    return list(iter_json_records(path.read_text(encoding="utf-8", errors="replace")))


def discover_topic_files(generator_root: Path) -> Dict[str, List[Path]]:
    found: Dict[str, List[Path]] = {}
    for topic in TOPIC_SOURCES:
        topic_dir = generator_root / topic
        if not topic_dir.exists():
            continue
        files = sorted(p for p in topic_dir.rglob("*") if p.is_file() and not p.name.startswith("."))
        files = [p for p in files if p.suffix in {".json", ".jsonl", ".txt"} or "json" in p.name]
        if files:
            found[topic] = files
    return found


def _dt_hr_from_record(record: Dict[str, Any]) -> Tuple[str, str]:
    ts = record.get("event_timestamp") or now_iso()
    # Accept ISO strings; fall back to today
    try:
        day = str(ts)[:10]
        hour = str(ts)[11:13] if len(str(ts)) >= 13 else datetime.now(timezone.utc).strftime("%H")
        if len(day) != 10:
            raise ValueError
        return day, hour
    except Exception:
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%H")


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
            count += 1
    return count


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    # Flatten one level; skip nested dicts/lists for CSV
    fieldnames: List[str] = []
    seen = set()
    flat_rows = []
    for row in rows:
        flat: Dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                flat[k] = json.dumps(v, default=str)
            else:
                flat[k] = v
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
        flat_rows.append(flat)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat_rows)
    return len(flat_rows)


def republish(
    generator_root: str | Path,
    sources_root: str | Path,
    labels_root: Optional[str | Path] = None,
    topics: Optional[Iterable[str]] = None,
) -> Dict[str, int]:
    """Read 03 output and write vendor-shaped files. Returns counts per topic."""
    generator_root = Path(generator_root)
    sources_root = Path(sources_root)
    labels_root = Path(labels_root) if labels_root else sources_root.parent / "sim_labels"
    sources_root.mkdir(parents=True, exist_ok=True)

    wanted = set(topics) if topics else set(TOPIC_SOURCES)
    discovered = discover_topic_files(generator_root)
    counts: Dict[str, int] = {}
    offsets: Dict[str, int] = defaultdict(int)

    for topic, files in discovered.items():
        if topic not in wanted:
            continue
        spec = TOPIC_SOURCES[topic]
        written = 0
        csv_buffer: List[Dict[str, Any]] = []
        label_rows: List[Dict[str, Any]] = []
        json_rows_by_hour: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        late_rows_by_hour: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        webhook_rows: List[Dict[str, Any]] = []

        for fpath in files:
            records = read_generator_file(fpath)
            for rec in records:
                payload, labels = strip_sim_fields(rec)
                if labels and any(k != "event_id" and k != "event_type" and v is not None for k, v in labels.items()):
                    label_rows.append(labels)

                offsets[topic] += 1
                wrapped = wrap_record(rec, spec, offsets[topic])
                day, hour = _dt_hr_from_record(rec)
                json_rows_by_hour[(day, hour)].append(wrapped)

                late_pct = float(spec.get("late_dump_pct") or 0)
                if late_pct and (hash(str(rec.get("event_id"))) % 100) < int(late_pct * 100):
                    late_rows_by_hour[(day, hour)].append(wrap_cdn_log(payload, spec))

                wh_pct = float(spec.get("webhook_pct") or 0)
                if wh_pct and (hash(str(rec.get("event_id")) + "wh") % 100) < int(wh_pct * 100):
                    webhook_rows.append(
                        wrap_kafka(
                            payload,
                            {
                                **spec,
                                "topic": "mmp.appsflyer.conversions.webhook",
                                "kafka_partitions": 4,
                                "key_field": "user_id",
                            },
                            offsets[topic],
                        )
                    )

                if spec.get("envelope") in {"mmp_csv", "invoice_csv", "gl_csv"}:
                    csv_buffer.append(wrapped)

                written += 1

        batch_id = uuid.uuid4().hex[:8]
        for (day, hour), rows in json_rows_by_hour.items():
            out_dir = sources_root / spec["landing"] / f"dt={day}" / f"hr={hour}"
            if spec["envelope"] in {"mmp_csv", "invoice_csv", "gl_csv"}:
                # JSON copy for streaming debug + CSV as the vendor file
                _write_jsonl(out_dir / f"{topic}_{batch_id}.json", rows)
            else:
                _write_jsonl(out_dir / f"{topic}_{batch_id}.json", rows)

        for (day, hour), rows in late_rows_by_hour.items():
            late_root = spec.get("late_dump_landing")
            if not late_root:
                continue
            out_dir = sources_root / late_root / f"dt={day}" / f"hr={hour}"
            _write_jsonl(out_dir / f"clicks_late_{batch_id}.json", rows)

        if webhook_rows:
            wh = spec.get("webhook_landing")
            if wh:
                day, hour = _dt_hr_from_record(webhook_rows[0].get("value") or {})
                _write_jsonl(
                    sources_root / wh / f"dt={day}" / f"hr={hour}" / f"webhook_{batch_id}.json",
                    webhook_rows,
                )

        if csv_buffer:
            # One vendor file per run/day (how finance actually drops files)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            csv_name = {
                "conversions": "appsflyer_conversions.csv",
                "billing_records": "dv360_invoice.csv",
                "ledger_entries": "netsuite_gl.csv",
            }.get(topic, f"{topic}.csv")
            _write_csv(sources_root / spec["landing"] / f"dt={day}" / csv_name, csv_buffer)

        if label_rows:
            _write_jsonl(labels_root / topic / f"labels_{batch_id}.json", label_rows)

        counts[topic] = written

    return counts


def topic_catalog() -> List[Dict[str, Any]]:
    rows = []
    for event_type, spec in TOPIC_SOURCES.items():
        rows.append(
            {
                "generator_topic": event_type,
                "kafka_or_drop_name": spec["topic"],
                "source_system": spec["source_system"],
                "vendor": spec["vendor"],
                "source_type": spec["source_type"],
                "mode": spec["mode"],
                "streaming": spec["mode"] == "streaming",
                "landing": spec["landing"],
                "envelope": spec["envelope"],
            }
        )
    return rows
