import abc
import json
import logging
import os
import time
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class GeneratorMetrics:

    def __init__(self, generator_name: str):
        self.name = generator_name
        self.records_generated = 0
        self.records_written = 0
        self.records_failed_validation = 0
        self.fraud_injected = 0
        self.late_events_injected = 0
        self.duplicates_injected = 0
        self.write_errors = 0
        self._start_time = time.monotonic()

    def throughput_rps(self) -> float:
        elapsed = time.monotonic() - self._start_time
        return self.records_written / elapsed if elapsed > 0 else 0.0

    def to_dict(self) -> Dict:
        return {
            "generator": self.name,
            "records_generated": self.records_generated,
            "records_written": self.records_written,
            "records_failed_validation": self.records_failed_validation,
            "fraud_injected": self.fraud_injected,
            "late_events_injected": self.late_events_injected,
            "duplicates_injected": self.duplicates_injected,
            "write_errors": self.write_errors,
            "throughput_rps": round(self.throughput_rps(), 2),
        }

    def log(self):
        logger.info(f"[METRICS] {json.dumps(self.to_dict())}")


class BaseEventGenerator(abc.ABC):
    # Subclasses declare their event_type
    EVENT_TYPE: str = None

    def __init__(
        self,
        config,             # DotDict from ConfigLoader
        shared_state,       # SharedGeneratorState (injected)
        edge_injectors,     # List[BaseEdgeCaseInjector]
        validator=None,     # Optional SchemaValidator
    ):
        assert self.EVENT_TYPE is not None, "Subclass must define EVENT_TYPE"
        self.config = config
        self.shared_state = shared_state
        self.edge_injectors = edge_injectors
        self.validator = validator

        # Deterministic RNG: each generator type gets a unique seed derived
        # from the global seed + event type hash. This ensures reproducibility
        # while maintaining statistical independence between generators.
        base_seed = config.platform.seed
        type_hash = int(hashlib.md5(self.EVENT_TYPE.encode()).hexdigest()[:8], 16)
        self._rng = np.random.default_rng(base_seed ^ type_hash)

        self.metrics = GeneratorMetrics(self.EVENT_TYPE)
        self._output_base = config.platform.output_base_path
        self._batch_interval = config.platform.batch_interval_seconds
        self._rate_cfg = config.event_rates[self.EVENT_TYPE.replace("-", "_")]
        self._validate_enabled = config.platform.enable_validation

        # Dedup buffer: circular buffer of recent event_ids for duplicate injection
        self._recent_event_ids: List[str] = []
        self._dedup_buffer_size = 1000

        # Ensure output directory exists
        self._output_dir = self._build_output_path()
        os.makedirs(self._output_dir, exist_ok=True)

    def _build_output_path(self) -> str:
        now = datetime.now(timezone.utc)
        return os.path.join(
            self._output_base,
            self.EVENT_TYPE,
            f"dt={now.strftime('%Y-%m-%d')}",
            f"hr={now.strftime('%H')}",
        )

    def generate_event_id(self) -> str:
        return str(uuid.uuid4())

    def current_event_time(self) -> datetime:

        if self.config.platform.replay_mode:
            base = datetime.fromisoformat(self.config.platform.replay_timestamp)
            elapsed = timedelta(seconds=time.monotonic())
            return base + elapsed
        return datetime.now(timezone.utc)

    def to_iso(self, dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    @abc.abstractmethod
    def _generate_record(self) -> Dict[str, Any]:

        pass

    @abc.abstractmethod
    def _get_schema(self) -> Dict:
        pass

    def _calculate_batch_size(self) -> int:

        traffic_cfg = self.config.traffic
        base_rate = traffic_cfg.base_events_per_second[
            self.EVENT_TYPE.replace("-", "_")
        ]
        hour = datetime.now(timezone.utc).hour
        is_weekend = datetime.now(timezone.utc).weekday() >= 5

        multiplier = 1.0
        if hour in list(traffic_cfg.peak_hours):
            multiplier = traffic_cfg.peak_multiplier
        if is_weekend:
            multiplier *= traffic_cfg.weekend_dampener

        # Poisson process for realistic variability
        target = base_rate * self._batch_interval * multiplier
        actual = int(self._rng.poisson(max(target, 1)))
        return max(actual, 1)

    def _apply_edge_cases(self, records: List[Dict]) -> List[Dict]:

        for injector in self.edge_injectors:
            records = injector.inject(records, self._rng, self.metrics)
        return records

    def _add_to_dedup_buffer(self, event_id: str):
        self._recent_event_ids.append(event_id)
        if len(self._recent_event_ids) > self._dedup_buffer_size:
            self._recent_event_ids.pop(0)

    def _validate_records(self, records: List[Dict]) -> Tuple[List[Dict], List[Dict]]:

        if not self._validate_enabled or self.validator is None:
            return records, []
        valid, invalid = [], []
        for record in records:
            is_valid, errors = self.validator.validate(record, self.EVENT_TYPE)
            if is_valid:
                valid.append(record)
            else:
                record["_validation_errors"] = errors
                record["_is_valid"] = False
                self.metrics.records_failed_validation += 1
                if self.config.validation.strict_mode:
                    logger.warning(f"Dropping invalid record: {record['event_id']} errors={errors}")
                else:
                    # Tag and pass through for debugging/monitoring
                    valid.append(record)
                    invalid.append(record)
        return valid, invalid

    def _write_batch(self, records: List[Dict]) -> bool:

        if not records:
            return True

        # Rebuild output path in case hour/date changed mid-run
        output_dir = self._build_output_path()
        os.makedirs(output_dir, exist_ok=True)

        batch_id = str(uuid.uuid4())[:8]
        timestamp = int(time.time() * 1000)
        tmp_path = os.path.join(output_dir, f".{self.EVENT_TYPE}_{timestamp}_{batch_id}.tmp")
        final_path = os.path.join(output_dir, f"{self.EVENT_TYPE}_{timestamp}_{batch_id}.json")

        try:
            with open(tmp_path, "w") as f:
                for record in records:
                    f.write(json.dumps(record, default=str) + "\\n")
            os.rename(tmp_path, final_path)
            self.metrics.records_written += len(records)
            logger.debug(f"[{self.EVENT_TYPE}] Wrote {len(records)} records → {final_path}")
            return True
        except Exception as e:
            self.metrics.write_errors += 1
            logger.error(f"[{self.EVENT_TYPE}] Write failed: {e}")
            # Clean up tmp file if it exists
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            return False

    def run_batch(self) -> int:
        batch_size = self._calculate_batch_size()
        records = []

        for _ in range(batch_size):
            record = self._generate_record()
            record["ingestion_timestamp"] = self.to_iso(datetime.now(timezone.utc))
            self._add_to_dedup_buffer(record["event_id"])
            records.append(record)
            self.metrics.records_generated += 1

        records = self._apply_edge_cases(records)
        valid_records, _ = self._validate_records(records)
        self._write_batch(valid_records)

        if self.config.platform.enable_metrics:
            self.metrics.log()

        return len(valid_records)
