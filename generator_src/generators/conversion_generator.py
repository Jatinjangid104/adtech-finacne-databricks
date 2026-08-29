import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import numpy as np

from core.base_generator import BaseEventGenerator
from simulation.shared_state import SharedGeneratorState, FunnelEvent
from edge_cases.injectors import (
    FraudInjector, LateDataInjector, DuplicateInjector, FinancialEdgeCaseInjector
)


CONVERSION_TYPES = [
    ("purchase", 0.35),
    ("signup", 0.25),
    ("lead_form", 0.20),
    ("app_install", 0.10),
    ("add_to_cart", 0.07),
    ("checkout_start", 0.03),
]


class ConversionGenerator(BaseEventGenerator):

    EVENT_TYPE = "conversions"

    def __init__(self, config, shared_state: SharedGeneratorState, validator=None):
        fraud_ips = shared_state.users._fraud_ips
        edge_injectors = [
            FraudInjector(config, self.EVENT_TYPE, fraud_ips),
            LateDataInjector(config, self.EVENT_TYPE),
            DuplicateInjector(config, self.EVENT_TYPE, self._recent_event_ids_ref()),
            FinancialEdgeCaseInjector(config),
        ]
        super().__init__(config, shared_state, edge_injectors, validator)
        rate_cfg = config.event_rates.conversions
        self._missing_click_pct = rate_cfg.missing_click_pct
        self._delayed_conv_pct = rate_cfg.delayed_conversion_pct
        self._delayed_max_hours = rate_cfg.delayed_conversion_max_hours
        self._cvr_base = rate_cfg.cvr_base

        self._conv_types = [t[0] for t in CONVERSION_TYPES]
        self._conv_weights = [t[1] for t in CONVERSION_TYPES]

    def _recent_event_ids_ref(self):
        if not hasattr(self, "_recent_event_ids"):
            self._recent_event_ids = []
        return self._recent_event_ids

    def run_batch(self) -> int:
        # First, emit any pending delayed conversions whose window has passed
        now = datetime.now(timezone.utc)
        pending = self.shared_state.funnel.pop_due_conversions(now)
        if pending:
            for conv in pending:
                conv.pop("_emit_after", None)
                conv["ingestion_timestamp"] = self.to_iso(now)
            self._write_batch(pending)
            self.metrics.records_written += len(pending)

        # Then run normal batch
        return super().run_batch()

    def _generate_record(self) -> Dict[str, Any]:
        now = self.current_event_time()
        rate_cfg = self.config.event_rates.conversions

        # Source from a real click
        clicks = self.shared_state.funnel.get_recent("click", n=1)
        missing_click = False

        if clicks and self._rng.random() > self._missing_click_pct:
            source_click = clicks[0]
            click_id = source_click.event_id
            campaign_id = source_click.campaign_id
            user_id = source_click.user_id
            session_id = source_click.session_id
            user = self.shared_state.users.get_user(user_id)
            geo_country = user.geo_country if user else "US"
            geo_city = user.geo_city if user else "New York"
            ip_address = user.ip_address if user else "0.0.0.0"
            device = self.shared_state.users.get_device_for_event(user) if user else "mobile"

            # Cross-device check: 15% chance user converts on different device
            if self._rng.random() < 0.15 and user:
                devices = ["mobile", "desktop", "tablet"]
                other_devices = [d for d in devices if d != device]
                device = str(self._rng.choice(other_devices))
                session_id = f"s_{str(uuid.uuid4())[:16]}"  # new session for cross-device

            # Multi-touch: find all clicks for this user (attribution touch points)
            all_user_clicks = self.shared_state.funnel.get_by_user("click", user_id, n=5)
            touch_points = [
                {"click_id": c.event_id, "campaign_id": c.campaign_id,
                 "timestamp": self.to_iso(c.event_timestamp)}
                for c in all_user_clicks
            ]
        else:
            # Orphaned conversion — no click reference
            missing_click = True
            click_id = None
            campaign = self.shared_state.campaigns.get_active_campaign()
            user = self.shared_state.users.get_or_create_user()
            session_id = self.shared_state.users.get_or_create_session(user)
            campaign_id = campaign.campaign_id
            user_id = user.user_id
            device = self.shared_state.users.get_device_for_event(user)
            geo_country = user.geo_country
            geo_city = user.geo_city
            ip_address = user.ip_address
            touch_points = []

        campaign = self.shared_state.campaigns.get_campaign(campaign_id)
        conv_type = str(self._rng.choice(self._conv_types, p=self._conv_weights))

        # Revenue: lognormal (most conversions are small, occasional large purchase)
        revenue_base = {
            "purchase": (3.5, 1.2),
            "signup": (0.5, 0.3),
            "lead_form": (1.5, 0.8),
            "app_install": (2.0, 0.6),
            "add_to_cart": (3.0, 1.0),
            "checkout_start": (3.2, 1.0),
        }
        mu, sigma = revenue_base.get(conv_type, (2.0, 0.8))
        revenue = round(float(self._rng.lognormal(mean=mu, sigma=sigma)), 4)

        # CPA cost = bid price if CPA campaign
        cpa_cost = 0.0
        if campaign and campaign.pricing_model == "CPA":
            cpa_cost = campaign.bid_price
            self.shared_state.campaigns.debit_spend(campaign_id, cpa_cost)

        event_id = self.generate_event_id()
        conv_ts = now  # immediate (non-delayed)

        # Delayed conversion: register for future emission
        is_delayed = self._rng.random() < self._delayed_conv_pct
        if is_delayed:
            delay_hours = float(self._rng.exponential(scale=self._delayed_max_hours * 0.2))
            delay_hours = min(delay_hours, self._delayed_max_hours)
            emit_after = now + timedelta(hours=delay_hours)

            delayed_record = {
                "event_id": event_id,
                "event_type": "conversion",
                "event_timestamp": self.to_iso(now),
                "ingestion_timestamp": None,
                "click_id": click_id,
                "campaign_id": campaign_id,
                "user_id": user_id,
                "session_id": session_id,
                "device_type": device,
                "geo_country": geo_country,
                "geo_city": geo_city,
                "ip_address": ip_address,
                "conversion_type": conv_type,
                "revenue": revenue,
                "revenue_micros": int(revenue * 1_000_000),
                "cpa_cost": round(cpa_cost, 6),
                "attribution_model": str(self._rng.choice(
                    ["last_click", "first_click", "linear", "time_decay"],
                    p=[0.50, 0.20, 0.20, 0.10]
                )),
                "attribution_window_hours": 48,
                "touch_points": touch_points,
                "is_delayed_conversion": True,
                "delay_hours": round(delay_hours, 2),
                "missing_click": missing_click,
                "is_fraud": False,
                "fraud_label": "clean",
                "fraud_signals": {},
                "_is_duplicate": False,
                "_emit_after": emit_after.isoformat(),
                "_financial_edge_case": None,
            }
            if click_id:
                self.shared_state.funnel.register_pending_conversion(click_id, delayed_record)
            # Return a placeholder-free record to avoid double emission
            # We still need to return something — emit a different record
            return self._generate_record()  # recursive, bounded by low delayed_conv_pct

        return {
            "event_id": event_id,
            "event_type": "conversion",
            "event_timestamp": self.to_iso(conv_ts),
            "ingestion_timestamp": None,
            "click_id": click_id,
            "campaign_id": campaign_id,
            "user_id": user_id,
            "session_id": session_id,
            "device_type": device,
            "geo_country": geo_country,
            "geo_city": geo_city,
            "ip_address": ip_address,
            "conversion_type": conv_type,
            "revenue": revenue,
            "revenue_micros": int(revenue * 1_000_000),
            "cpa_cost": round(cpa_cost, 6),
            "attribution_model": str(self._rng.choice(
                ["last_click", "first_click", "linear", "time_decay"],
                p=[0.50, 0.20, 0.20, 0.10]
            )),
            "attribution_window_hours": 48,
            "touch_points": touch_points,
            "is_delayed_conversion": False,
            "delay_hours": 0.0,
            "missing_click": missing_click,
            "is_fraud": False,
            "fraud_label": "clean",
            "fraud_signals": {},
            "_is_duplicate": False,
            "_financial_edge_case": None,
        }

    def _get_schema(self) -> Dict:
        return {
            "type": "object",
            "required": ["event_id", "event_timestamp", "campaign_id", "user_id", "revenue"],
        }
