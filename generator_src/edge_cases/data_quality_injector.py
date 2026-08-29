import logging
import random
import string
import uuid
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import numpy as np

from edge_cases.injectors import BaseEdgeCaseInjector

logger = logging.getLogger(__name__)


class DataQualityInjector(BaseEdgeCaseInjector):

    DQ_FAILURE_TYPES = {
        "null_injection": 0.30,
        "corrupt_record": 0.20,
        "invalid_id_format": 0.15,
        "type_confusion": 0.15,
        "truncated_record": 0.10,
        "encoding_corruption": 0.05,
        "oversized_field": 0.03,
        "referential_integrity_violation": 0.02,
    }

    # Fields safe to null (non-critical path)
    NULLABLE_FIELDS = [
        "geo_city", "user_agent", "referrer_url", "landing_page_url",
        "ad_position", "viewability", "render_time_ms", "supply_chain_object",
        "bid_floor_currency", "user_segments", "deal_ids",
    ]

    # Fields that SHOULD NOT be null (tests downstream null-handling)
    CRITICAL_NULLABLE_FIELDS = [
        "campaign_id", "user_id", "device_type", "geo_country",
    ]

    def __init__(self, config, event_type: str):
        super().__init__(config)
        dq_cfg = config.data_quality
        self._injection_pct = dq_cfg.injection_pct
        self._critical_null_pct = dq_cfg.critical_null_pct
        self._failure_types = list(self.DQ_FAILURE_TYPES.keys())
        self._failure_weights = list(self.DQ_FAILURE_TYPES.values())

    def inject(self, records: List[Dict], rng: np.random.Generator, metrics) -> List[Dict]:
        result = []
        for record in records:
            if rng.random() < self._injection_pct:
                failure_type = str(rng.choice(self._failure_types, p=self._failure_weights))
                record = self._apply_dq_failure(record, failure_type, rng)
                record["_dq_failure_type"] = failure_type
            result.append(record)
        return result

    def _apply_dq_failure(self, record: Dict, failure_type: str,
                          rng: np.random.Generator) -> Dict:
        record = deepcopy(record)

        if failure_type == "null_injection":
            return self._inject_nulls(record, rng)

        elif failure_type == "corrupt_record":
            return self._corrupt_record(record, rng)

        elif failure_type == "invalid_id_format":
            return self._corrupt_ids(record, rng)

        elif failure_type == "type_confusion":
            return self._inject_type_confusion(record, rng)

        elif failure_type == "truncated_record":
            return self._truncate_record(record, rng)

        elif failure_type == "encoding_corruption":
            return self._corrupt_encoding(record, rng)

        elif failure_type == "oversized_field":
            return self._inject_oversized_field(record, rng)

        elif failure_type == "referential_integrity_violation":
            return self._violate_referential_integrity(record, rng)

        return record

    def _inject_nulls(self, record: Dict, rng: np.random.Generator) -> Dict:

        if rng.random() < self._critical_null_pct:
            # Null a critical field
            field = str(rng.choice(self.CRITICAL_NULLABLE_FIELDS))
            record[field] = None
            record["_null_critical_field"] = field
        else:
            # Null a non-critical field
            nullable = [f for f in self.NULLABLE_FIELDS if f in record]
            if nullable:
                field = str(rng.choice(nullable))
                record[field] = None
        return record

    def _corrupt_record(self, record: Dict, rng: np.random.Generator) -> Dict:
        corruption = str(rng.choice([
            "future_timestamp", "negative_cost", "impossible_ctr",
            "negative_bid", "zero_campaign_id", "past_epoch_timestamp",
        ]))

        if corruption == "future_timestamp":
            future = datetime.now(timezone.utc) + timedelta(days=365)
            record["event_timestamp"] = future.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        elif corruption == "negative_cost":
            for field in ["cost", "cost_micros", "bid_price", "clearing_price"]:
                if field in record and record[field] is not None:
                    record[field] = -abs(record[field])

        elif corruption == "impossible_ctr":
            # CTR > 100% — impossible but sometimes appears due to dedup bugs
            record["_simulated_ctr"] = float(rng.uniform(1.5, 10.0))

        elif corruption == "negative_bid":
            if "bid_price" in record:
                record["bid_price"] = -float(rng.uniform(0.01, 5.0))

        elif corruption == "zero_campaign_id":
            record["campaign_id"] = "00000000-0000-0000-0000-000000000000"

        elif corruption == "past_epoch_timestamp":
            # Timestamp from 1970-01-01 (epoch zero — common bug in uninitialized vars)
            record["event_timestamp"] = "1970-01-01T00:00:00.000Z"

        record["_corruption_type"] = corruption
        return record

    def _corrupt_ids(self, record: Dict, rng: np.random.Generator) -> Dict:
        corruption = str(rng.choice([
            "truncated_uuid", "sql_injection", "xss_payload",
            "non_uuid_format", "empty_string", "numeric_string",
        ]))

        id_fields = [f for f in ["event_id", "campaign_id", "user_id", "session_id"]
                     if f in record]
        if not id_fields:
            return record

        target_field = str(rng.choice(id_fields))

        if corruption == "truncated_uuid":
            original = record[target_field]
            record[target_field] = original[:12] if original else "truncated"

        elif corruption == "sql_injection":
            record[target_field] = "'; DROP TABLE events; --"

        elif corruption == "xss_payload":
            record[target_field] = "<script>alert('xss')</script>"

        elif corruption == "non_uuid_format":
            record[target_field] = "INVALID_ID_" + "".join(
                random.choices(string.ascii_uppercase, k=8)
            )

        elif corruption == "empty_string":
            record[target_field] = ""

        elif corruption == "numeric_string":
            record[target_field] = str(int(rng.integers(1, 999999)))

        record["_id_corruption"] = corruption
        record["_corrupted_field"] = target_field
        return record

    def _inject_type_confusion(self, record: Dict, rng: np.random.Generator) -> Dict:

        confusion = str(rng.choice(["numeric_as_string", "bool_as_int", "float_as_int"]))

        if confusion == "numeric_as_string":
            for field in ["cost", "bid_price", "clearing_price", "revenue"]:
                if field in record and record[field] is not None:
                    record[field] = str(record[field])  # "1.234" instead of 1.234
                    break

        elif confusion == "bool_as_int":
            for field in ["is_fraud", "_is_duplicate", "missing_impression"]:
                if field in record and isinstance(record[field], bool):
                    record[field] = 1 if record[field] else 0
                    break

        elif confusion == "float_as_int":
            for field in ["viewability", "targeting_match_score"]:
                if field in record and record[field] is not None:
                    record[field] = int(record[field] * 100)  # 0.75 → 75
                    break

        record["_type_confusion"] = confusion
        return record

    def _truncate_record(self, record: Dict, rng: np.random.Generator) -> Dict:

        all_fields = list(record.keys())
        keep_count = max(3, int(rng.integers(3, len(all_fields) // 2 + 1)))
        # Always keep event_id and event_timestamp (most parsers expect them)
        required = {"event_id", "event_timestamp", "event_type"}
        optional_fields = [f for f in all_fields if f not in required]
        keep_optional = list(rng.choice(
            optional_fields,
            size=max(0, keep_count - len(required)),
            replace=False
        ))
        keep_fields = required | set(keep_optional)
        truncated = {k: v for k, v in record.items() if k in keep_fields}
        truncated["_truncated_record"] = True
        truncated["_original_field_count"] = len(all_fields)
        truncated["_remaining_field_count"] = len(keep_fields)
        return truncated

    def _corrupt_encoding(self, record: Dict, rng: np.random.Generator) -> Dict:

        corruption_chars = [
            "\\x00",           # null byte — breaks many string operations
            "\ufffd",          # unicode replacement character
            "\xe2\x80\x8b",   # zero-width space (invisible, causes length mismatches)
            "🚀💥",            # emoji (4-byte UTF8, breaks naive string length assumptions)
            "\\r\\n",            # Windows line endings in a JSON string field
        ]

        string_fields = [
            k for k, v in record.items()
            if isinstance(v, str) and k not in ("event_id", "event_timestamp", "event_type")
        ]
        if string_fields:
            target = str(rng.choice(string_fields))
            corruption = str(rng.choice(corruption_chars))
            record[target] = record[target] + corruption if record[target] else corruption
            record["_encoding_corruption_field"] = target

        return record

    def _inject_oversized_field(self, record: Dict, rng: np.random.Generator) -> Dict:
        size_kb = int(rng.choice([1, 10, 100, 1000]))  # 1KB to 1MB
        oversized_value = "X" * (size_kb * 1024)
        record["_oversized_payload"] = oversized_value[:100] + "...[TRUNCATED]"
        record["_oversized_field_kb"] = size_kb
        # Don't actually write the full payload — just mark that it happened
        return record

    def _violate_referential_integrity(self, record: Dict,
                                       rng: np.random.Generator) -> Dict:

        violation = str(rng.choice(["nonexistent_campaign", "nonexistent_user", "nonexistent_session"]))

        if violation == "nonexistent_campaign":
            record["campaign_id"] = f"GHOST_CAMP_{str(uuid.uuid4())[:8]}"
            record["_ref_integrity_violation"] = "campaign_id_not_in_dim_campaigns"

        elif violation == "nonexistent_user":
            record["user_id"] = f"GHOST_USER_{str(uuid.uuid4())[:8]}"
            record["_ref_integrity_violation"] = "user_id_not_in_dim_users"

        elif violation == "nonexistent_session":
            record["session_id"] = f"GHOST_SESSION_{str(uuid.uuid4())[:8]}"
            record["_ref_integrity_violation"] = "session_id_orphaned"

        return record
