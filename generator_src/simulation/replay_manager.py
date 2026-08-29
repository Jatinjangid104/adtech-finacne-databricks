import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ReplaySpec:
    replay_run_id: str
    replay_type: str                    # full | range | partial | incremental
    seed: int
    event_types: List[str]              # which event types to replay
    start_time: Optional[datetime]      # for range replay
    end_time: Optional[datetime]        # for range replay
    checkpoint_offset: Optional[int]    # for incremental replay
    num_batches: int
    reason: str                         # human-readable reason for replay
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ReplayManager:

    REPLAY_LOG_PATH = "/dbfs/data/adtech/replay_log.jsonl"

    def __init__(self, config):
        self.config = config
        self._active_replay: Optional[ReplaySpec] = None
        self._replay_record_count = 0

    def start_replay(
        self,
        replay_type: str = "full",
        seed: Optional[int] = None,
        event_types: Optional[List[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        checkpoint_offset: Optional[int] = None,
        num_batches: int = 100,
        reason: str = "manual_replay",
    ) -> ReplaySpec:

        spec = ReplaySpec(
            replay_run_id=str(uuid.uuid4()),
            replay_type=replay_type,
            seed=seed or self.config.platform.seed,
            event_types=event_types or [
                "bid_requests", "impressions", "clicks", "conversions", "campaign_cdc"
            ],
            start_time=start_time,
            end_time=end_time,
            checkpoint_offset=checkpoint_offset,
            num_batches=num_batches,
            reason=reason,
        )
        self._active_replay = spec
        self._replay_record_count = 0
        self._log_replay_start(spec)
        logger.info(f"[REPLAY] Started: run_id={spec.replay_run_id} type={replay_type} reason={reason}")
        return spec

    def end_replay(self):
        if self._active_replay:
            self._log_replay_end(self._active_replay, self._replay_record_count)
            logger.info(
                f"[REPLAY] Completed: run_id={self._active_replay.replay_run_id} "
                f"records={self._replay_record_count}"
            )
            self._active_replay = None

    def annotate_record(self, record: Dict) -> Dict:
        if not self._active_replay:
            record["is_replay"] = False
            record["replay_run_id"] = None
            record["replay_idempotency_key"] = None
            return record

        spec = self._active_replay
        self._replay_record_count += 1

        # Deterministic idempotency key: hash of (event_id + replay_run_id)
        # Downstream MERGE ON event_id AND NOT is_replay can skip already-processed records
        idempotency_key = hashlib.sha256(
            f"{record['event_id']}:{spec.replay_run_id}".encode()
        ).hexdigest()[:16]

        record["is_replay"] = True
        record["replay_run_id"] = spec.replay_run_id
        record["replay_type"] = spec.replay_type
        record["replay_reason"] = spec.reason
        record["replay_idempotency_key"] = idempotency_key
        record["replay_sequence_num"] = self._replay_record_count

        # For range replay: filter out records outside the time window
        if spec.start_time or spec.end_time:
            try:
                ts = datetime.fromisoformat(record["event_timestamp"].replace("Z", "+00:00"))
                if spec.start_time and ts < spec.start_time:
                    record["_replay_out_of_range"] = True
                if spec.end_time and ts > spec.end_time:
                    record["_replay_out_of_range"] = True
            except (ValueError, KeyError):
                pass

        return record

    def _log_replay_start(self, spec: ReplaySpec):
        os.makedirs(os.path.dirname(self.REPLAY_LOG_PATH), exist_ok=True)
        entry = {
            "log_type": "replay_start",
            "replay_run_id": spec.replay_run_id,
            "replay_type": spec.replay_type,
            "seed": spec.seed,
            "event_types": spec.event_types,
            "num_batches": spec.num_batches,
            "reason": spec.reason,
            "created_at": spec.created_at.isoformat(),
        }
        with open(self.REPLAY_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\\n")

    def _log_replay_end(self, spec: ReplaySpec, record_count: int):
        entry = {
            "log_type": "replay_end",
            "replay_run_id": spec.replay_run_id,
            "total_records": record_count,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self.REPLAY_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\\n")
        except Exception as e:
            logger.error(f"Failed to write replay end log: {e}")
