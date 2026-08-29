import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BurstEvent:
    start_time: float          # monotonic clock
    duration_seconds: float
    multiplier: float
    cause: str
    active: bool = True


class BurstTrafficController:

    BURST_PROFILES = {
        "flash_sale": {
            "multiplier_range": (10, 50),
            "duration_range_sec": (60, 300),
            "shape": "flat",
            "probability_per_batch": 0.001,
        },
        "bot_farm": {
            "multiplier_range": (50, 200),
            "duration_range_sec": (10, 45),
            "shape": "cliff",  # sudden on, sudden off
            "probability_per_batch": 0.0005,
        },
        "viral_news": {
            "multiplier_range": (5, 15),
            "duration_range_sec": (300, 3600),
            "shape": "bell",  # gradual rise, peak, gradual fall
            "probability_per_batch": 0.002,
        },
        "ddos": {
            "multiplier_range": (200, 1000),
            "duration_range_sec": (15, 90),
            "shape": "cliff",
            "probability_per_batch": 0.0001,
        },
        "promoted_post": {
            "multiplier_range": (3, 8),
            "duration_range_sec": (1800, 7200),
            "shape": "bell",
            "probability_per_batch": 0.003,
        },
    }

    def __init__(self, config, rng: np.random.Generator):
        self.config = config
        self._rng = rng
        self._active_bursts: List[BurstEvent] = []
        self._burst_history: List[BurstEvent] = []

    def maybe_trigger_burst(self) -> Optional[BurstEvent]:
        for profile_name, profile in self.BURST_PROFILES.items():
            if self._rng.random() < profile["probability_per_batch"]:
                mult = float(self._rng.uniform(*profile["multiplier_range"]))
                duration = float(self._rng.uniform(*profile["duration_range_sec"]))
                burst = BurstEvent(
                    start_time=time.monotonic(),
                    duration_seconds=duration,
                    multiplier=mult,
                    cause=profile_name,
                )
                self._active_bursts.append(burst)
                self._burst_history.append(burst)
                logger.warning(
                    f"[BURST] {profile_name} triggered: {mult:.1f}x for {duration:.0f}s"
                )
                return burst
        return None

    def get_current_multiplier(self) -> Tuple[float, Optional[str]]:

        now = time.monotonic()
        effective_multiplier = 1.0
        active_cause = None

        expired = []
        for burst in self._active_bursts:
            elapsed = now - burst.start_time
            if elapsed >= burst.duration_seconds:
                burst.active = False
                expired.append(burst)
                continue

            progress = elapsed / burst.duration_seconds
            profile = self.BURST_PROFILES.get(burst.cause, {})
            shape = profile.get("shape", "flat")

            if shape == "flat":
                shape_multiplier = burst.multiplier
            elif shape == "cliff":
                # Sudden on, sudden off — no ramp
                shape_multiplier = burst.multiplier
            elif shape == "bell":
                # Gaussian bell: peaks at midpoint
                # 4*sigma covers the duration (2 sigma each side of center)
                center = 0.5
                sigma = 0.2
                bell = np.exp(-0.5 * ((progress - center) / sigma) ** 2)
                shape_multiplier = 1.0 + (burst.multiplier - 1.0) * bell
            else:
                shape_multiplier = burst.multiplier

            effective_multiplier = max(effective_multiplier, shape_multiplier)
            active_cause = burst.cause

        for b in expired:
            self._active_bursts.remove(b)

        return effective_multiplier, active_cause

    def inject_burst_metadata(self, record: Dict, burst_cause: Optional[str]) -> Dict:
        if burst_cause:
            record["_burst_event"] = burst_cause
            record["_during_burst"] = True
        return record


class HotPartitionSimulator:

    HOT_KEY_PROFILES = {
        "campaign_concentration": {
            "field": "campaign_id",
            "hot_pct": 0.80,           # 80% of events use hot_value
            "probability": 0.001,       # low probability per batch
            "duration_batches": 50,
        },
        "publisher_concentration": {
            "field": "publisher_domain",
            "hot_pct": 0.70,
            "probability": 0.002,
            "duration_batches": 100,
        },
        "geo_flood": {
            "field": "geo_country",
            "hot_pct": 0.85,
            "probability": 0.001,
            "duration_batches": 30,
        },
        "device_skew": {
            "field": "device_type",
            "hot_pct": 0.92,
            "probability": 0.003,
            "duration_batches": 20,
        },
    }

    def __init__(self, config, rng: np.random.Generator, shared_state):
        self.config = config
        self._rng = rng
        self._shared_state = shared_state
        self._active_hot_keys: Dict[str, Dict] = {}  # field → {value, remaining_batches}
        self._batch_count = 0

    def tick(self):
        self._batch_count += 1
        expired = []
        for field, state in self._active_hot_keys.items():
            state["remaining_batches"] -= 1
            if state["remaining_batches"] <= 0:
                expired.append(field)
                logger.info(f"[HOT_PARTITION] {field}={state['value']} hot key expired")

        for f in expired:
            del self._active_hot_keys[f]

        # Maybe activate new hot keys
        for profile_name, profile in self.HOT_KEY_PROFILES.items():
            field = profile["field"]
            if field not in self._active_hot_keys and self._rng.random() < profile["probability"]:
                hot_value = self._pick_hot_value(field)
                if hot_value:
                    self._active_hot_keys[field] = {
                        "value": hot_value,
                        "hot_pct": profile["hot_pct"],
                        "remaining_batches": profile["duration_batches"],
                        "profile": profile_name,
                    }
                    logger.warning(
                        f"[HOT_PARTITION] {field}={hot_value} is now HOT "
                        f"({profile['hot_pct']*100:.0f}% of traffic)"
                    )

    def _pick_hot_value(self, field: str) -> Optional[str]:
        if field == "campaign_id":
            ids = self._shared_state.campaigns.all_campaign_ids()
            return str(self._rng.choice(ids)) if ids else None
        elif field == "publisher_domain":
            from generators.bid_request_generator import PUBLISHER_DOMAINS
            return str(self._rng.choice(PUBLISHER_DOMAINS))
        elif field == "geo_country":
            countries = list(self.config.users.geo_distribution.to_dict().keys())
            return str(self._rng.choice(countries))
        elif field == "device_type":
            return "mobile"
        return None

    def apply_hot_key(self, record: Dict, rng: np.random.Generator) -> Dict:
        for field, state in self._active_hot_keys.items():
            if field in record and rng.random() < state["hot_pct"]:
                record[field] = state["value"]
                record["_hot_partition_field"] = field
                record["_hot_partition_value"] = state["value"]
        return record
