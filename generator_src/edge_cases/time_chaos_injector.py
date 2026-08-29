import logging
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple
import numpy as np

from edge_cases.injectors import BaseEdgeCaseInjector

logger = logging.getLogger(__name__)

# Common timezone offsets in seconds (to simulate non-UTC recording)
TIMEZONE_OFFSETS_SECONDS = {
    "US/Eastern": -5 * 3600,
    "US/Pacific": -8 * 3600,
    "Europe/London": 0,
    "Europe/Berlin": 1 * 3600,
    "Asia/Kolkata": 5 * 3600 + 1800,
    "Asia/Tokyo": 9 * 3600,
    "Australia/Sydney": 10 * 3600,
}


class TimeChaosInjector(BaseEdgeCaseInjector):

    def __init__(self, config, event_type: str):
        super().__init__(config)
        tc = config.time_chaos
        self._enabled = tc.enabled
        self._clock_skew_pct = tc.clock_skew_pct
        self._processing_divergence_pct = tc.processing_divergence_pct
        self._ntp_rollback_pct = tc.ntp_rollback_pct
        self._timezone_confusion_pct = tc.timezone_confusion_pct
        self._multi_source_pct = tc.multi_source_skew_pct

        # Persistent skew values — simulate a real drifting clock
        # The same server maintains its skew across batches (not random each time)
        self._server_skew_seconds: Dict[str, float] = {}
        self._tz_offsets = list(TIMEZONE_OFFSETS_SECONDS.values())

    def inject(self, records: List[Dict], rng: np.random.Generator, metrics) -> List[Dict]:
        if not self._enabled:
            return records

        result = []
        for record in records:
            record = self._apply_time_chaos(record, rng)
            result.append(record)

        # NTP rollback: batch-level operation (affects a contiguous sequence)
        if len(result) > 3 and rng.random() < self._ntp_rollback_pct:
            result = self._inject_ntp_rollback(result, rng)

        return result

    def _apply_time_chaos(self, record: Dict, rng: np.random.Generator) -> Dict:
        record = deepcopy(record)
        roll = rng.random()
        cumulative = 0.0

        # Ensure both event_time and processing_time are always present
        # This is foundational — downstream systems should ALWAYS have both
        if "processing_timestamp" not in record:
            record["processing_timestamp"] = record.get("ingestion_timestamp") or record.get("event_timestamp")
        if "event_time_source" not in record:
            record["event_time_source"] = "client"  # default: client-recorded

        cumulative += self._clock_skew_pct
        if roll < cumulative:
            return self._apply_clock_skew(record, rng)

        cumulative += self._processing_divergence_pct
        if roll < cumulative:
            return self._apply_processing_divergence(record, rng)

        cumulative += self._timezone_confusion_pct
        if roll < cumulative:
            return self._apply_timezone_confusion(record, rng)

        cumulative += self._multi_source_pct
        if roll < cumulative:
            return self._apply_multi_source_skew(record, rng)

        return record

    def _apply_clock_skew(self, record: Dict, rng: np.random.Generator) -> Dict:
        source_server = record.get("ad_server_id", record.get("campaign_id", "default"))[:8]

        if source_server not in self._server_skew_seconds:
            # First time we see this server: assign it a persistent skew
            # Bimodal: most servers are within ±30s, some are wildly off (minutes)
            if rng.random() < 0.8:
                skew = float(rng.uniform(-30, 30))
            else:
                skew = float(rng.choice([-300, -120, 120, 300, 600]))
            self._server_skew_seconds[source_server] = skew

        skew_seconds = self._server_skew_seconds[source_server]
        try:
            ts = datetime.fromisoformat(record["event_timestamp"].replace("Z", "+00:00"))
            skewed_ts = ts + timedelta(seconds=skew_seconds)
            record["event_timestamp"] = skewed_ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            record["_time_chaos_type"] = "clock_skew"
            record["_clock_skew_seconds"] = round(skew_seconds, 2)
            record["_source_server"] = source_server
        except (ValueError, KeyError):
            pass

        return record

    def _apply_processing_divergence(self, record: Dict, rng: np.random.Generator) -> Dict:

        try:
            event_ts = datetime.fromisoformat(record["event_timestamp"].replace("Z", "+00:00"))

            # Processing lag: lognormal (median ~2min, tail up to 2 hours)
            lag_seconds = float(rng.lognormal(mean=4.5, sigma=1.8))  # median ~90s
            lag_seconds = min(lag_seconds, 7200)  # cap at 2 hours

            processing_ts = event_ts + timedelta(seconds=lag_seconds)
            record["processing_timestamp"] = processing_ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            record["ingestion_timestamp"] = record["processing_timestamp"]
            record["_time_chaos_type"] = "processing_divergence"
            record["_processing_lag_seconds"] = round(lag_seconds, 2)
            # If pipeline uses ingestion_time for windowing, this event falls in WRONG window
            record["_event_window"] = event_ts.strftime("%Y-%m-%dT%H:00:00Z")
            record["_processing_window"] = processing_ts.strftime("%Y-%m-%dT%H:00:00Z")
            record["_window_mismatch"] = record["_event_window"] != record["_processing_window"]
        except (ValueError, KeyError):
            pass

        return record

    def _apply_timezone_confusion(self, record: Dict, rng: np.random.Generator) -> Dict:
        tz_offset_seconds = float(rng.choice(self._tz_offsets))
        try:
            ts = datetime.fromisoformat(record["event_timestamp"].replace("Z", "+00:00"))
            # The timestamp was recorded as local time but stored as if it were UTC
            # So to "un-confuse" it, we'd add the offset back; we store the confused version
            confused_ts = ts + timedelta(seconds=tz_offset_seconds)
            record["event_timestamp"] = confused_ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            record["_time_chaos_type"] = "timezone_confusion"
            record["_tz_offset_seconds"] = tz_offset_seconds
            record["event_time_source"] = "client_local_time"  # the bug marker
        except (ValueError, KeyError):
            pass

        return record

    def _apply_multi_source_skew(self, record: Dict, rng: np.random.Generator) -> Dict:
        server_side_offset_seconds = float(rng.uniform(1, 10))
        try:
            ts = datetime.fromisoformat(record["event_timestamp"].replace("Z", "+00:00"))
            server_ts = ts + timedelta(seconds=server_side_offset_seconds)
            record["server_recorded_timestamp"] = server_ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            record["_time_chaos_type"] = "multi_source_skew"
            record["_source_time_delta_seconds"] = round(server_side_offset_seconds, 3)
            record["event_time_source"] = "client"
        except (ValueError, KeyError):
            pass

        return record

    def _inject_ntp_rollback(self, records: List[Dict], rng: np.random.Generator) -> List[Dict]:

        rollback_seconds = float(rng.uniform(5, 60))
        start_idx = int(rng.integers(0, max(1, len(records) // 2)))
        affected_count = int(rng.integers(2, min(10, len(records) - start_idx)))

        for i in range(start_idx, start_idx + affected_count):
            if i >= len(records):
                break
            try:
                ts = datetime.fromisoformat(
                    records[i]["event_timestamp"].replace("Z", "+00:00")
                )
                # Earlier records in the rolled-back sequence have LATER timestamps
                # (because the clock jumped back partway through)
                offset = rollback_seconds * (1 - (i - start_idx) / affected_count)
                rolled_ts = ts - timedelta(seconds=offset)
                records[i]["event_timestamp"] = rolled_ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                records[i]["_time_chaos_type"] = "ntp_rollback"
                records[i]["_ntp_rollback_seconds"] = round(rollback_seconds, 2)
                records[i]["_monotonicity_violation"] = True
            except (ValueError, KeyError):
                pass

        logger.debug(f"NTP rollback injected: {affected_count} records, {rollback_seconds:.1f}s rollback")
        return records
