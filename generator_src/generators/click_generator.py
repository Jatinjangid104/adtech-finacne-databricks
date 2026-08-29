import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
import numpy as np

from core.base_generator import BaseEventGenerator
from simulation.shared_state import SharedGeneratorState, FunnelEvent
from edge_cases.injectors import (
    FraudInjector, LateDataInjector, DuplicateInjector, FinancialEdgeCaseInjector
)


class ClickGenerator(BaseEventGenerator):

    EVENT_TYPE = "clicks"

    def __init__(self, config, shared_state: SharedGeneratorState, validator=None):
        fraud_ips = shared_state.users._fraud_ips
        edge_injectors = [
            FraudInjector(config, self.EVENT_TYPE, fraud_ips),
            LateDataInjector(config, self.EVENT_TYPE),
            DuplicateInjector(config, self.EVENT_TYPE, self._recent_event_ids_ref()),
            FinancialEdgeCaseInjector(config),
        ]
        super().__init__(config, shared_state, edge_injectors, validator)
        rate_cfg = config.event_rates.clicks
        self._missing_impression_pct = rate_cfg.missing_impression_pct
        self._ctr_base = rate_cfg.ctr_base

    def _recent_event_ids_ref(self):
        if not hasattr(self, "_recent_event_ids"):
            self._recent_event_ids = []
        return self._recent_event_ids

    def _generate_record(self) -> Dict[str, Any]:
        now = self.current_event_time()

        # Try to source from a real impression
        impressions = self.shared_state.funnel.get_recent("impression", n=1)
        missing_impression = False

        if impressions and self._rng.random() > self._missing_impression_pct:
            source_imp = impressions[0]
            impression_id = source_imp.event_id
            campaign_id = source_imp.campaign_id
            user_id = source_imp.user_id
            session_id = source_imp.session_id
            # Click happens seconds to minutes after impression
            click_ts = source_imp.event_timestamp + timedelta(
                seconds=int(self._rng.lognormal(mean=2.5, sigma=1.2))
            )
            user = self.shared_state.users.get_user(user_id)
            device = self.shared_state.users.get_device_for_event(user) if user else "mobile"
            geo_country = user.geo_country if user else "US"
            geo_city = user.geo_city if user else "New York"
            ip_address = user.ip_address if user else "0.0.0.0"
            clearing_price = source_imp.metadata.get("clearing_price", 0.50)
            # CPC cost = bid price (first price) or clearing price
            campaign = self.shared_state.campaigns.get_campaign(campaign_id)
            cost = campaign.bid_price if campaign else clearing_price
        else:
            # Orphaned click — no impression reference
            missing_impression = True
            impression_id = None
            campaign = self.shared_state.campaigns.get_active_campaign()
            user = self.shared_state.users.get_or_create_user()
            session_id = self.shared_state.users.get_or_create_session(user)
            campaign_id = campaign.campaign_id
            user_id = user.user_id
            click_ts = now
            device = self.shared_state.users.get_device_for_event(user)
            geo_country = user.geo_country
            geo_city = user.geo_city
            ip_address = user.ip_address
            cost = campaign.bid_price

        # Debit CPC cost
        if campaign and campaign.pricing_model == "CPC":
            self.shared_state.campaigns.debit_spend(campaign_id, cost)

        event_id = self.generate_event_id()
        funnel_event = FunnelEvent(
            event_id=event_id,
            event_type="click",
            campaign_id=campaign_id,
            user_id=user_id,
            session_id=session_id,
            event_timestamp=click_ts,
            metadata={
                "cost": cost,
                "impression_id": impression_id,
                "geo_country": geo_country,
            },
        )
        self.shared_state.funnel.add("click", funnel_event)

        # Click coordinates (realistic on-ad click position)
        click_x = int(self._rng.integers(0, 300))
        click_y = int(self._rng.integers(0, 250))

        # Bot clicks have suspiciously uniform coordinates
        if self._rng.random() < self.config.event_rates.clicks.fraud_percentage * 0.5:
            click_x, click_y = 150, 125  # dead center every time

        return {
            "event_id": event_id,
            "event_type": "click",
            "event_timestamp": self.to_iso(click_ts),
            "ingestion_timestamp": None,
            "impression_id": impression_id,
            "campaign_id": campaign_id,
            "user_id": user_id,
            "session_id": session_id,
            "device_type": device,
            "geo_country": geo_country,
            "geo_city": geo_city,
            "ip_address": ip_address,
            "cost": round(cost, 6),
            "cost_micros": int(cost * 1_000_000),
            "click_x": click_x,
            "click_y": click_y,
            "time_to_click_seconds": round(
                float(self._rng.lognormal(mean=2.5, sigma=1.2)), 2
            ),
            "landing_page_url": f"https://advertiser-{campaign_id[:8]}.example.com/landing",
            "referrer_url": f"https://pub.example.com/page/{str(uuid.uuid4())[:8]}",
            "missing_impression": missing_impression,
            "is_fraud": False,
            "fraud_label": "clean",
            "fraud_signals": {},
            "_is_duplicate": False,
            "_late_by_minutes": None,
            "_financial_edge_case": None,
        }

    def _get_schema(self) -> Dict:
        return {
            "type": "object",
            "required": ["event_id", "event_timestamp", "campaign_id", "user_id", "cost"],
        }
