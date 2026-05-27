import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False
    logger.warning("jsonschema not available; structural validation disabled")


BUSINESS_RULES = {
    "bid_request": [
        lambda r: (r.get("bid_price", 0) >= 0, "bid_price must be non-negative"),
        lambda r: (r.get("floor_price_cpm", 0) >= 0, "floor_price_cpm must be non-negative"),
        lambda r: (r.get("event_id") is not None, "event_id is required"),
    ],
    "impression": [
        lambda r: (r.get("clearing_price", 0) >= 0, "clearing_price must be non-negative"),
        lambda r: (r.get("viewability", 1) <= 1.0, "viewability must be <= 1.0"),
    ],
    "click": [
        lambda r: (r.get("cost", 0) >= 0 or r.get("_financial_edge_case") == "negative_adjustment",
                   "cost must be non-negative unless negative_adjustment"),
        lambda r: (r.get("time_to_click_seconds", 1) >= 0, "time_to_click must be non-negative"),
    ],
    "conversion": [
        lambda r: (r.get("revenue") is not None, "revenue is required"),
        lambda r: (r.get("attribution_window_hours", 1) > 0, "attribution_window must be positive"),
    ],
    "campaign_cdc": [
        lambda r: (r.get("change_type") in
                   ["budget_update", "pricing_model_change", "status_change",
                    "targeting_update", "heartbeat"],
                   "invalid change_type"),
    ],
}

SCHEMAS: Dict[str, Dict] = {
    "bid_request": {
        "type": "object",
        "required": ["event_id", "event_timestamp", "campaign_id", "user_id",
                     "device_type", "bid_price", "pricing_model"],
        "properties": {
            "event_id": {"type": "string", "minLength": 1},
            "bid_price": {"type": "number", "minimum": 0},
            "device_type": {"enum": ["mobile", "desktop", "tablet"]},
            "pricing_model": {"enum": ["CPC", "CPM", "CPA"]},
        },
        "additionalProperties": True,
    },
    "impression": {
        "type": "object",
        "required": ["event_id", "event_timestamp", "campaign_id", "user_id", "clearing_price"],
        "properties": {
            "clearing_price": {"type": "number", "minimum": 0},
            "viewability": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "additionalProperties": True,
    },
    "click": {
        "type": "object",
        "required": ["event_id", "event_timestamp", "campaign_id", "user_id"],
        "additionalProperties": True,
    },
    "conversion": {
        "type": "object",
        "required": ["event_id", "event_timestamp", "campaign_id", "user_id", "revenue"],
        "properties": {
            "revenue": {"type": "number"},
        },
        "additionalProperties": True,
    },
    "campaign_cdc": {
        "type": "object",
        "required": ["event_id", "event_timestamp", "campaign_id", "change_type"],
        "additionalProperties": True,
    },
}


class SchemaValidator:

    def __init__(self, config, max_record_age_hours: int = 72):
        self._config = config
        self._max_age = timedelta(hours=max_record_age_hours)

    def validate(self, record: Dict, event_type: str) -> Tuple[bool, List[str]]:
        errors = []

        # 1. JSON Schema structural validation
        if JSONSCHEMA_AVAILABLE and event_type in SCHEMAS:
            try:
                jsonschema.validate(record, SCHEMAS[event_type])
            except jsonschema.ValidationError as e:
                errors.append(f"schema: {e.message}")
            except jsonschema.SchemaError as e:
                logger.error(f"Invalid schema definition for {event_type}: {e}")

        # 2. Business rule validation
        for rule_fn in BUSINESS_RULES.get(event_type, []):
            try:
                passed, message = rule_fn(record)
                if not passed:
                    errors.append(f"business_rule: {message}")
            except Exception as e:
                errors.append(f"rule_error: {e}")

        # 3. Temporal sanity check (very old events are suspicious unless flagged)
        if "event_timestamp" in record and record.get("_late_by_minutes") is None:
            try:
                ts = datetime.fromisoformat(
                    record["event_timestamp"].replace("Z", "+00:00")
                )
                age = datetime.now(timezone.utc) - ts
                if age > self._max_age:
                    errors.append(f"temporal: event_timestamp too old ({age.total_seconds()/3600:.1f}h)")
            except ValueError:
                errors.append("temporal: invalid event_timestamp format")

        return len(errors) == 0, errors