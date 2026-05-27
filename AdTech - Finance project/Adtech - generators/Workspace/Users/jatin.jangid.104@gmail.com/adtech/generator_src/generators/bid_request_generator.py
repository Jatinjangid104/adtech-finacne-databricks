import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
import numpy as np

from core.base_generator import BaseEventGenerator
from simulation.shared_state import SharedGeneratorState, FunnelEvent
from edge_cases.injectors import (
    FraudInjector, LateDataInjector, DuplicateInjector, FinancialEdgeCaseInjector
)


PUBLISHER_DOMAINS = [
    "news.example.com", "sports.example.net", "tech.example.org",
    "entertainment.example.com", "finance.example.net", "health.example.org",
    "travel.example.com", "shopping.example.net", "gaming.example.io",
]

AD_SIZES = ["300x250", "728x90", "320x50", "160x600", "970x250", "300x600"]

AD_POSITIONS = ["above_fold", "below_fold", "sidebar", "footer", "interstitial"]


class BidRequestGenerator(BaseEventGenerator):

    EVENT_TYPE = "bid_requests"

    def __init__(self, config, shared_state: SharedGeneratorState, validator=None):
        # Build edge case injectors in pipeline order
        fraud_ips = shared_state.users._fraud_ips
        edge_injectors = [
            FraudInjector(config, self.EVENT_TYPE, fraud_ips),
            LateDataInjector(config, self.EVENT_TYPE),
            DuplicateInjector(config, self.EVENT_TYPE, self._recent_event_ids_ref()),
            FinancialEdgeCaseInjector(config),
        ]
        super().__init__(config, shared_state, edge_injectors, validator)

    def _recent_event_ids_ref(self):
        # Pre-initialize if super().__init__() hasn't run yet
        if not hasattr(self, '_recent_event_ids'):
            self._recent_event_ids = []
        return self._recent_event_ids

    def _generate_record(self) -> Dict[str, Any]:
        campaign = self.shared_state.campaigns.get_active_campaign()
        user = self.shared_state.users.get_or_create_user()
        session_id = self.shared_state.users.get_or_create_session(user)
        device = self.shared_state.users.get_device_for_event(user)
        now = self.current_event_time()

        event_id = self.generate_event_id()
        publisher_domain = str(self._rng.choice(PUBLISHER_DOMAINS))
        ad_size = str(self._rng.choice(AD_SIZES))
        ad_position = str(self._rng.choice(AD_POSITIONS))

        # Floor price: lognormal, lower bound at $0.01
        floor_price_cpm = max(
            0.01,
            float(self._rng.lognormal(mean=0.5, sigma=0.7))
        )

        # Bid price from campaign config
        pm = campaign.pricing_model
        pm_cfg = self.config.campaigns.pricing_models[pm]
        bid_price = max(
            pm_cfg.bid_floor,
            min(float(self._rng.lognormal(pm_cfg.bid_mu, pm_cfg.bid_sigma)), pm_cfg.bid_ceiling)
        )

        # Store in funnel state for downstream generators
        funnel_event = FunnelEvent(
            event_id=event_id,
            event_type="bid_request",
            campaign_id=campaign.campaign_id,
            user_id=user.user_id,
            session_id=session_id,
            event_timestamp=now,
            metadata={"bid_price": bid_price, "floor_price_cpm": floor_price_cpm},
        )
        self.shared_state.funnel.add("bid_request", funnel_event)

        return {
            "event_id": event_id,
            "event_type": "bid_request",
            "event_timestamp": self.to_iso(now),
            "ingestion_timestamp": None,  # filled by base class
            "campaign_id": campaign.campaign_id,
            "advertiser_id": campaign.advertiser_id,
            "user_id": user.user_id,
            "session_id": session_id,
            "device_type": device,
            "geo_country": user.geo_country,
            "geo_city": user.geo_city,
            "ip_address": user.ip_address,
            "publisher_domain": publisher_domain,
            "ad_size": ad_size,
            "ad_position": ad_position,
            "floor_price_cpm": round(floor_price_cpm, 6),
            "bid_price": round(bid_price, 6),
            "bid_price_micros": int(bid_price * 1_000_000),
            "pricing_model": campaign.pricing_model,
            "targeting_match_score": round(float(self._rng.beta(2, 5)), 4),
            "user_agent": self._generate_user_agent(device, user.is_bot),
            "page_url": f"https://{publisher_domain}/article/{str(uuid.uuid4())[:8]}",
            "inventory_type": str(self._rng.choice(["display", "video", "native", "rich_media"],
                                                   p=[0.55, 0.25, 0.15, 0.05])),
            "viewability_score": round(float(self._rng.beta(3, 2)), 4),
            "is_fraud": False,
            "fraud_label": "clean",
            "fraud_signals": {},
            "_is_duplicate": False,
            "_late_by_minutes": None,
            "_out_of_order": False,
            "_financial_edge_case": None,
        }

    def _generate_user_agent(self, device: str, is_bot: bool) -> str:
        if is_bot:
            return "python-requests/2.28.0"
        agents = {
            "mobile": [
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36",
                "Mozilla/5.0 (Linux; Android 12; Samsung SM-G991B) AppleWebKit/537.36",
            ],
            "desktop": [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/108.0",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Firefox/108.0",
            ],
            "tablet": [
                "Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
                "Mozilla/5.0 (Linux; Android 12; SM-T870) AppleWebKit/537.36",
            ],
        }
        pool = agents.get(device, agents["desktop"])
        return str(self._rng.choice(pool))

    def _get_schema(self) -> Dict:
        return {
            "type": "object",
            "required": ["event_id", "event_timestamp", "campaign_id", "user_id",
                         "device_type", "bid_price"],
            "properties": {
                "event_id": {"type": "string"},
                "event_timestamp": {"type": "string", "format": "date-time"},
                "bid_price": {"type": "number", "minimum": 0},
            }
        }