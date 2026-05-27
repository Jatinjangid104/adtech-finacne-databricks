import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from edge_cases.injectors import BaseEdgeCaseInjector 

logger = logging.getLogger(__name__)


# Schema evolution timeline: list of (version, change_type, change_spec)
# Applied cumulatively — V3 record has all changes from V1, V2, V3.
SCHEMA_EVOLUTION_TIMELINE = {
    "bid_requests": [
        (1, "baseline", {}),
        (2, "add_optional_field", {
            "field": "supply_chain_object",
            "type": "string",
            "nullable": True,
            "default": None,
            "description": "OpenRTB Supply Chain Object (ads.txt)"
        }),
        (3, "add_optional_field", {
            "field": "bid_floor_currency",
            "type": "string",
            "nullable": True,
            "default": "USD",
        }),
        (4, "type_widening", {
            "field": "bid_price_micros",
            "old_type": "int32",
            "new_type": "int64",
            "description": "Exceeding int32 max for high-value bids"
        }),
        (5, "add_nested_object", {
            "field": "user_segments",
            "schema": {
                "segment_ids": ["list", "string"],
                "model_scores": {"type": "object"},
            },
            "description": "DSP audience segment data"
        }),
        (6, "rename_field", {
            "old_name": "targeting_match_score",
            "new_name": "relevance_score",
            "keep_old": True,  # transition period: keep both
        }),
        (7, "deprecate_field", {
            "field": "viewability_score",
            "replacement": "measured_viewability",
            "emit_null": True,
        }),
        (8, "add_array_field", {
            "field": "deal_ids",
            "element_type": "string",
            "nullable": True,
        }),
    ],
    "clicks": [
        (1, "baseline", {}),
        (2, "add_optional_field", {
            "field": "click_type",
            "type": "string",
            "nullable": True,
            "default": "standard",
        }),
        (3, "add_optional_field", {
            "field": "interaction_type",
            "type": "string",
            "nullable": True,
        }),
        (4, "type_narrowing", {  # DANGEROUS
            "field": "cost",
            "old_type": "float64",
            "new_type": "float32",
            "description": "DANGEROUS: reduces precision, causes rounding in aggregations",
        }),
        (5, "add_nested_object", {
            "field": "viewability_data",
            "schema": {
                "in_view_pct": "float",
                "time_in_view_ms": "int",
            },
        }),
    ],
    "conversions": [
        (1, "baseline", {}),
        (2, "add_optional_field", {
            "field": "order_id",
            "type": "string",
            "nullable": True,
        }),
        (3, "add_optional_field", {
            "field": "product_ids",
            "type": "array",
            "element_type": "string",
            "nullable": True,
        }),
        (4, "add_optional_field", {
            "field": "consent_string",
            "type": "string",
            "nullable": True,
            "description": "GDPR TCF 2.0 consent string",
        }),
        (5, "rename_field", {
            "old_name": "revenue",
            "new_name": "gross_revenue",
            "keep_old": True,
        }),
    ],
}


class SchemaEvolutionController:
    def __init__(self, config, rng: np.random.Generator):
        self.config = config
        self._rng = rng
        se_cfg = config.schema_evolution
        self._evolution_rate = se_cfg.version_advance_probability
        self._current_versions: Dict[str, int] = {}
        self._version_history: Dict[str, List[Tuple]] = {}

        # Initialize all event types at V1
        for event_type in SCHEMA_EVOLUTION_TIMELINE:
            self._current_versions[event_type] = 1

        logger.info("Schema evolution controller initialized at V1 for all event types")

    def maybe_advance_version(self, event_type: str):
        if self._rng.random() < self._evolution_rate:
            timeline = SCHEMA_EVOLUTION_TIMELINE.get(event_type, [])
            max_version = len(timeline)
            current = self._current_versions.get(event_type, 1)
            if current < max_version:
                new_version = current + 1
                self._current_versions[event_type] = new_version
                change = timeline[new_version - 1]
                logger.warning(
                    f"[SCHEMA EVOLUTION] {event_type} advanced to V{new_version}: "
                    f"{change[1]} — {change[2]}"
                )

    def get_current_version(self, event_type: str) -> int:
        return self._current_versions.get(event_type, 1)


class SchemaEvolutionInjector(BaseEdgeCaseInjector):

    def __init__(self, config, event_type: str, evolution_controller: SchemaEvolutionController):
        super().__init__(config)
        self._event_type = event_type
        self._controller = evolution_controller
        self._timeline = SCHEMA_EVOLUTION_TIMELINE.get(event_type, [])

    def inject(self, records: List[Dict], rng: np.random.Generator, metrics) -> List[Dict]:
        # Advance version once per batch
        self._controller.maybe_advance_version(self._event_type)
        current_version = self._controller.get_current_version(self._event_type)

        result = []
        for record in records:
            # Some records in the batch are still at old schema version
            # (simulate rolling deployment: not all producers upgraded yet)
            record_version = self._sample_record_version(current_version, rng)
            record = self._apply_schema_version(record, record_version, rng)
            result.append(record)
        return result

    def _sample_record_version(self, current_version: int, rng: np.random.Generator) -> int:

        if current_version == 1:
            return 1
        # 70% at current version, 25% at current-1, 5% at older versions
        weights = [0.05] * (current_version - 2) + [0.25, 0.70] if current_version > 2 else [0.30, 0.70]
        versions = list(range(1, current_version + 1))
        # Ensure weights sum to 1 and match versions length
        if len(weights) < len(versions):
            weights = [1.0 / len(versions)] * len(versions)
        weights = weights[-len(versions):]
        total = sum(weights)
        weights = [w / total for w in weights]
        return int(rng.choice(versions, p=weights))

    def _apply_schema_version(self, record: Dict, version: int, rng: np.random.Generator) -> Dict:
        record = deepcopy(record)
        record["schema_version"] = version

        for ver, change_type, spec in self._timeline:
            if ver > version:
                break
            record = self._apply_change(record, change_type, spec, rng)

        return record

    def _apply_change(self, record: Dict, change_type: str,
                      spec: Dict, rng: np.random.Generator) -> Dict:

        if change_type == "baseline":
            return record

        elif change_type == "add_optional_field":
            field = spec["field"]
            if field not in record:
                # 90% chance the new field is populated, 10% null (optional)
                if rng.random() < 0.9:
                    default = spec.get("default")
                    if spec.get("type") == "array":
                        record[field] = []
                    else:
                        record[field] = default
                else:
                    record[field] = None

        elif change_type == "type_widening":
            field = spec["field"]
            if field in record and record[field] is not None:
                # Safe: widen the type representation
                record[field] = int(record[field]) if "int" in spec["new_type"] else float(record[field])
                record[f"_{field}_type_widened"] = True

        elif change_type == "type_narrowing":
            field = spec["field"]
            if field in record and record[field] is not None:
                # DANGEROUS: loses precision
                try:
                    import struct
                    # Simulate float32 precision loss
                    val = float(record[field])
                    packed = struct.pack('f', val)
                    narrowed = struct.unpack('f', packed)[0]
                    record[field] = narrowed
                    record[f"_{field}_precision_loss"] = abs(val - narrowed)
                except Exception:
                    pass

        elif change_type == "add_nested_object":
            field = spec["field"]
            if field not in record:
                nested = {}
                for sub_field, sub_type in spec.get("schema", {}).items():
                    if sub_type == "float":
                        nested[sub_field] = round(float(rng.random()), 4)
                    elif sub_type == "int":
                        nested[sub_field] = int(rng.integers(0, 10000))
                    elif isinstance(sub_type, list) and sub_type[0] == "list":
                        nested[sub_field] = [str(rng.integers(1000, 9999)) for _ in range(int(rng.integers(0, 5)))]
                    elif sub_type == "object" or isinstance(sub_type, dict):
                        nested[sub_field] = {"score": round(float(rng.random()), 4)}
                    else:
                        nested[sub_field] = None
                record[field] = nested

        elif change_type == "rename_field":
            old_name = spec["old_name"]
            new_name = spec["new_name"]
            if old_name in record:
                record[new_name] = record[old_name]
                if not spec.get("keep_old", True):
                    del record[old_name]
                record[f"_{old_name}_deprecated"] = True

        elif change_type == "deprecate_field":
            field = spec["field"]
            if field in record and spec.get("emit_null", True):
                replacement = spec.get("replacement")
                if replacement:
                    record[replacement] = record[field]  # migrate value to new name
                record[field] = None  # null out deprecated field

        elif change_type == "add_array_field":
            field = spec["field"]
            if field not in record:
                # Arrays sometimes empty, sometimes populated
                count = int(rng.integers(0, 4))
                record[field] = [str(rng.integers(10000, 99999)) for _ in range(count)] if count > 0 else []

        return record