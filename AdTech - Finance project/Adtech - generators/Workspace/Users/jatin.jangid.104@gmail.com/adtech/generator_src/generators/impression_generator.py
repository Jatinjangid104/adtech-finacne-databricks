import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import numpy as np

from core.base_generator import BaseEventGenerator
from simulation.shared_state import SharedGeneratorState, FunnelEvent
from edge_cases.injectors import (
    FraudInjector, LateDataInjector, DuplicateInjector, FinancialEdgeCaseInjector
)


class ImpressionGenerator(BaseEventGenerator):

    EVENT_TYPE = "impressions"

    def __init__(self, config, shared_state: SharedGeneratorState, validator=None):
        fraud_ips = shared_state.users._fraud_ips
        edge_injectors = [
            FraudInjector(config, self.EVENT_TYPE, fraud_ips),
            LateDataInjector(config, self.EVENT_TYPE),
            DuplicateInjector(config, self.EVENT_TYPE, self._recent_event_ids_ref()),
            FinancialEdgeCaseInjector(config),
        ]
        super().__init__(config, shared_state, edge_injectors, validator)
        self._win_rate = config.event_rates.impressions.win_rate

    def _recent_event_ids_ref(self):
        # Pre-initialize if super().__init__() hasn't run yet
        if not hasattr(self, '_recent_event_ids'):
            self._recent_event_ids = []
        return self._recent_event_ids

    def _generate_record(self) -> Dict[str, Any]:
        now = self.current_event_time()

        # Try to source from a real bid request (causal chain)
        bid_requests = self.shared_state.funnel.get_recent("bid_request", n=1)
        missing_bid_request = False

        if bid_requests and self._rng.random() < self._win_rate:
            source_bid = bid_requests[0]
            bid_request_id = source_bid.event_id
            campaign_id = source_bid.campaign_id
            user_id = source_bid.user_id
            session_id = source_bid.session_id
            # Impression happens shortly after bid (RTB latency: 50-200ms)
            impression_ts = source_bid.event_timestamp + timedelta(
                milliseconds=int(self._rng.integers(50, 200))
            )
            user = self.shared_state.users.get_user(user_id)
            device = self.shared_state.users.get_device_for_event(user) if user else "mobile"
            geo_country = user.geo_country if user else "US"
            geo_city = user.geo_city if user else "New York"
            ip_address = user.ip_address if user else "0.0.0.0"
            bid_price = source_bid.metadata.get("bid_price", 0.50)
            floor_price = source_bid.metadata.get("floor_price_cpm", 0.10)
        else:
            # Orphaned impression (no bid request reference)
            missing_bid_request = True
            bid_request_id = None
            campaign = self.shared_state.campaigns.get_active_campaign()
            user = self.shared_state.users.get_or_create_user()
            session_id = self.shared_state.users.get_or_create_session(user)
            campaign_id = campaign.campaign_id
            impression_ts = now
            device = self.shared_state.users.get_device_for_event(user)
            geo_country = user.geo_country
            geo_city = user.geo_city
            ip_address = user.ip_address
            bid_price = 0.50
            floor_price = 0.10
            user_id = user.user_id

        # Clearing price: second-price auction (between floor and bid)
        clearing_price = round(
            float(self._rng.uniform(floor_price / 1000, bid_price * 0.95)), 6
        )
        # CPM cost = clearing_price * 1000 / 1000 = clearing_price per impression
        cost_cpm = clearing_price
        # Debiting campaign budget
        self.shared_state.campaigns.debit_spend(campaign_id, cost_cpm / 1000)

        event_id = self.generate_event_id()
        funnel_event = FunnelEvent(
            event_id=event_id,
            event_type="impression",
            campaign_id=campaign_id,
            user_id=user_id,
            session_id=session_id,
            event_timestamp=impression_ts,
            metadata={"clearing_price": clearing_price, "bid_request_id": bid_request_id},
        )
        self.shared_state.funnel.add("impression", funnel_event)

        return {
            "event_id": event_id,
            "event_type": "impression",
            "event_timestamp": self.to_iso(impression_ts),
            "ingestion_timestamp": None,
            "bid_request_id": bid_request_id,
            "campaign_id": campaign_id,
            "user_id": user_id,
            "session_id": session_id,
            "device_type": device,
            "geo_country": geo_country,
            "geo_city": geo_city,
            "ip_address": ip_address,
            "clearing_price": clearing_price,
            "clearing_price_micros": int(clearing_price * 1_000_000),
            "cost_cpm": round(cost_cpm, 6),
            "bid_price": round(bid_price, 6),
            "viewability": round(float(self._rng.beta(3, 2)), 4),
            "render_time_ms": int(self._rng.lognormal(mean=4.5, sigma=0.8)),
            "ad_server_id": f"as_{str(uuid.uuid4())[:8]}",
            "missing_bid_request": missing_bid_request,
            "auction_type": str(self._rng.choice(["first_price", "second_price"], p=[0.4, 0.6])),
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
            "required": ["event_id", "event_timestamp", "campaign_id", "user_id", "clearing_price"],
        }