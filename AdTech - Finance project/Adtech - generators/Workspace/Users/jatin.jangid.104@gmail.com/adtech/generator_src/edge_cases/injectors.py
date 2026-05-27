import abc
import logging
import uuid
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class BaseEdgeCaseInjector(abc.ABC):

    def __init__(self, config):
        self.config = config

    @abc.abstractmethod
    def inject(
        self,
        records: List[Dict],
        rng: np.random.Generator,
        metrics,
    ) -> List[Dict]:
        pass


class FraudInjector(BaseEdgeCaseInjector):

    FRAUD_PROFILES = {
        "bot_traffic": {
            "timing_jitter_ms": 50,        # near-zero variance
            "click_interval_ms": 200,      # suspiciously fast
            "session_page_views": 50,      # unrealistically high
        },
        "click_farm": {
            "burst_window_sec": 60,
            "clicks_per_ip_per_window": 25,
        },
        "impression_stuffing": {
            "multiplier_min": 5,
            "multiplier_max": 10,
        },
        "conversion_fraud": {
            "revenue_inflation": 2.5,
        },
    }

    def __init__(self, config, event_type: str, fraud_ips: List[str]):
        super().__init__(config)
        self._event_type = event_type
        self._fraud_ips = fraud_ips
        self._fraud_pct = config.event_rates.get(event_type.replace("-", "_"), type("D",(),{"late_event_pct":0.05,"late_max_minutes":60,"late_max_hours":1,"duplicate_pct":0.02,"fraud_percentage":0.05,"win_rate":0.45,"missing_impression_pct":0.02,"missing_click_pct":0.02,"ctr_base":0.025,"cvr_base":0.05,"delayed_conversion_max_hours":48,"delayed_conversion_pct":0.1})().fraud_percentage

    def inject(self, records: List[Dict], rng: np.random.Generator, metrics) -> List[Dict]:
        result = []
        for record in records:
            if rng.random() < self._fraud_pct:
                record = self._apply_fraud_pattern(record, rng)
                metrics.fraud_injected += 1
            result.append(record)
        return result

    def _apply_fraud_pattern(self, record: Dict, rng: np.random.Generator) -> Dict:
        record = deepcopy(record)
        patterns = list(self.FRAUD_PROFILES.keys())

        # Weight distribution: bot and click farm most common
        weights = [0.35, 0.35, 0.20, 0.10]
        pattern = str(rng.choice(patterns, p=weights))

        record["is_fraud"] = True
        record["fraud_label"] = pattern
        record["fraud_signals"] = {}

        if pattern == "bot_traffic":
            record["ip_address"] = str(rng.choice(self._fraud_ips))
            record["user_agent"] = "python-requests/2.28.0"
            record["fraud_signals"]["timing_variance_ms"] = float(
                rng.uniform(10, self.FRAUD_PROFILES["bot_traffic"]["timing_jitter_ms"])
            )
            record["fraud_signals"]["clicks_per_second"] = float(rng.uniform(8, 25))

        elif pattern == "click_farm":
            record["ip_address"] = str(rng.choice(self._fraud_ips[:5]))  # very small pool
            burst_count = int(rng.integers(
                self.FRAUD_PROFILES["click_farm"]["clicks_per_ip_per_window"] - 5,
                self.FRAUD_PROFILES["click_farm"]["clicks_per_ip_per_window"] + 10
            ))
            record["fraud_signals"]["ip_click_burst_count"] = burst_count
            record["fraud_signals"]["burst_window_sec"] = (
                self.FRAUD_PROFILES["click_farm"]["burst_window_sec"]
            )

        elif pattern == "impression_stuffing":
            mult = int(rng.integers(
                self.FRAUD_PROFILES["impression_stuffing"]["multiplier_min"],
                self.FRAUD_PROFILES["impression_stuffing"]["multiplier_max"]
            ))
            record["fraud_signals"]["impression_stuffing_multiplier"] = mult
            # Modify cost to reflect inflated impressions
            if "cost_micros" in record:
                record["cost_micros"] = int(record["cost_micros"] * mult)

        elif pattern == "conversion_fraud":
            inflation = self.FRAUD_PROFILES["conversion_fraud"]["revenue_inflation"]
            if "revenue" in record:
                record["revenue"] = round(record["revenue"] * inflation, 4)
            record["fraud_signals"]["missing_click_chain"] = True

        return record


class LateDataInjector(BaseEdgeCaseInjector):

    def __init__(self, config, event_type: str):
        super().__init__(config)
        rate_cfg = config.event_rates.get(event_type.replace("-", "_"), type("D",(),{"late_event_pct":0.05,"late_max_minutes":60,"late_max_hours":1,"duplicate_pct":0.02,"fraud_percentage":0.05,"win_rate":0.45,"missing_impression_pct":0.02,"missing_click_pct":0.02,"ctr_base":0.025,"cvr_base":0.05,"delayed_conversion_max_hours":48,"delayed_conversion_pct":0.1})()
        self._late_pct = rate_cfg.late_event_pct
        # late_max_minutes may be hours for conversions
        self._late_max_minutes = getattr(rate_cfg, "late_max_minutes", None)
        self._late_max_hours = getattr(rate_cfg, "late_max_hours", None)
        self._late_max_total_minutes = (
            (self._late_max_hours * 60) if self._late_max_hours
            else (self._late_max_minutes or 60)
        )

    def inject(self, records: List[Dict], rng: np.random.Generator, metrics) -> List[Dict]:
        result = []
        timestamps_for_ooo = []

        for record in records:
            if rng.random() < self._late_pct:
                # Exponential distribution: median delay is small, tail is long
                # scale = mean of exponential. We want median around 10% of max.
                scale = self._late_max_total_minutes * 0.15
                delay_minutes = float(rng.exponential(scale))
                delay_minutes = min(delay_minutes, self._late_max_total_minutes)

                # Parse and shift event_timestamp backward
                try:
                    ts = datetime.fromisoformat(
                        record["event_timestamp"].replace("Z", "+00:00")
                    )
                    late_ts = ts - timedelta(minutes=delay_minutes)
                    record["event_timestamp"] = late_ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                    record["_late_by_minutes"] = round(delay_minutes, 2)
                    metrics.late_events_injected += 1
                except (KeyError, ValueError) as e:
                    logger.warning(f"Late injection failed for record: {e}")

            result.append(record)

        # Out-of-order injection: randomly swap timestamps in ~5% of batches
        if len(result) > 2 and rng.random() < 0.05:
            result = self._inject_out_of_order(result, rng)

        return result

    def _inject_out_of_order(self, records: List[Dict], rng: np.random.Generator) -> List[Dict]:
        records = list(records)
        idx_a = int(rng.integers(0, len(records)))
        idx_b = int(rng.integers(0, len(records)))
        if idx_a != idx_b:
            ts_a = records[idx_a].get("event_timestamp")
            ts_b = records[idx_b].get("event_timestamp")
            if ts_a and ts_b:
                records[idx_a]["event_timestamp"] = ts_b
                records[idx_b]["event_timestamp"] = ts_a
                records[idx_a]["_out_of_order"] = True
                records[idx_b]["_out_of_order"] = True
        return records


class DuplicateInjector(BaseEdgeCaseInjector):
    def __init__(self, config, event_type: str, event_id_buffer: List[str]):
        super().__init__(config)
        rate_cfg = config.event_rates.get(event_type.replace("-", "_"), type("D",(),{"late_event_pct":0.05,"late_max_minutes":60,"late_max_hours":1,"duplicate_pct":0.02,"fraud_percentage":0.05,"win_rate":0.45,"missing_impression_pct":0.02,"missing_click_pct":0.02,"ctr_base":0.025,"cvr_base":0.05,"delayed_conversion_max_hours":48,"delayed_conversion_pct":0.1})()
        self._dup_pct = rate_cfg.duplicate_pct
        self._event_id_buffer = event_id_buffer  # reference to generator's buffer

    def inject(self, records: List[Dict], rng: np.random.Generator, metrics) -> List[Dict]:
        result = list(records)
        additions = []

        for record in records:
            if rng.random() < self._dup_pct and self._event_id_buffer:
                # Re-emit a previous event_id
                dup_event_id = str(rng.choice(self._event_id_buffer))
                dup = deepcopy(record)
                dup["event_id"] = dup_event_id
                dup["ingestion_timestamp"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3] + "Z"
                dup["_is_duplicate"] = True

                # Near-duplicate: slight timestamp drift (simulates retry)
                if rng.random() < 0.3:
                    try:
                        ts = datetime.fromisoformat(
                            dup["event_timestamp"].replace("Z", "+00:00")
                        )
                        drift_ms = int(rng.integers(-500, 500))
                        ts = ts + timedelta(milliseconds=drift_ms)
                        dup["event_timestamp"] = ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                        dup["_timestamp_drift_ms"] = drift_ms
                    except Exception:
                        pass

                additions.append(dup)
                metrics.duplicates_injected += 1

        return result + additions


class FinancialEdgeCaseInjector(BaseEdgeCaseInjector):
    COST_FIELDS = ["cost_micros", "bid_price", "revenue", "clearing_price"]

    def __init__(self, config):
        super().__init__(config)
        fc = config.edge_cases.financial
        self._zero_cost_pct = fc.zero_cost_campaign_pct
        self._negative_adj_pct = fc.negative_adjustment_pct
        self._overbilling_pct = fc.overbilling_scenario_pct
        self._rounding_pct = fc.currency_rounding_pct
        self._overbilling_mult = fc.max_overbilling_multiplier

    def inject(self, records: List[Dict], rng: np.random.Generator, metrics) -> List[Dict]:
        result = []
        for record in records:
            has_cost_field = any(f in record for f in self.COST_FIELDS)
            if not has_cost_field:
                result.append(record)
                continue

            roll = rng.random()
            if roll < self._zero_cost_pct:
                record = self._apply_zero_cost(record)
            elif roll < self._zero_cost_pct + self._negative_adj_pct:
                record = self._apply_negative_adjustment(record, rng)
            elif roll < self._zero_cost_pct + self._negative_adj_pct + self._overbilling_pct:
                record = self._apply_overbilling(record, rng)
            elif roll < self._zero_cost_pct + self._negative_adj_pct + self._overbilling_pct + self._rounding_pct:
                record = self._apply_rounding_artifact(record)

            result.append(record)
        return result

    def _apply_zero_cost(self, record: Dict) -> Dict:
        record = deepcopy(record)
        for field in self.COST_FIELDS:
            if field in record:
                record[field] = 0
        record["_financial_edge_case"] = "zero_cost"
        return record

    def _apply_negative_adjustment(self, record: Dict, rng: np.random.Generator) -> Dict:
        record = deepcopy(record)
        for field in self.COST_FIELDS:
            if field in record and record[field] > 0:
                # Negative adjustment = fraction of original cost reversed
                adj_pct = float(rng.uniform(0.1, 1.0))
                record[field] = -round(record[field] * adj_pct, 4)
        record["_financial_edge_case"] = "negative_adjustment"
        record["adjustment_reason"] = str(rng.choice(["fraud_reversal", "chargeback", "pricing_error"]))
        return record

    def _apply_overbilling(self, record: Dict, rng: np.random.Generator) -> Dict:
        record = deepcopy(record)
        mult = float(rng.uniform(1.01, self._overbilling_mult))
        for field in self.COST_FIELDS:
            if field in record and record[field] > 0:
                record[field] = round(record[field] * mult, 4)
        record["_financial_edge_case"] = "overbilling"
        record["_overbilling_multiplier"] = round(mult, 4)
        return record

    def _apply_rounding_artifact(self, record: Dict) -> Dict:
        record = deepcopy(record)
        for field in self.COST_FIELDS:
            if field in record and isinstance(record[field], (int, float)) and record[field] > 0:
                # Introduce floating-point rounding artifact
                record[field] = record[field] + 1e-10
        record["_financial_edge_case"] = "rounding_artifact"
        return record