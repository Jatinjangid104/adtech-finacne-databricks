import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import numpy as np

from core.base_generator import BaseEventGenerator
from simulation.shared_state import SharedGeneratorState
from edge_cases.injectors import LateDataInjector


REFUND_TYPES = {
    "full_refund": 0.30,
    "partial_refund": 0.35,
    "chargeback": 0.15,
    "fraud_reversal": 0.12,
    "goodwill_credit": 0.08,
}

CHARGEBACK_REASON_CODES = [
    "4853",   # Visa: Cardholder dispute (not as described)
    "4855",   # Visa: Goods/services not provided
    "4837",   # Mastercard: No cardholder authorization
    "4841",   # Mastercard: Cancelled recurring transaction
    "10.4",   # PayPal: Other fraud
]


class RefundGenerator(BaseEventGenerator):

    EVENT_TYPE = "refunds"

    def __init__(self, config, shared_state: SharedGeneratorState, validator=None):
        edge_injectors = [
            LateDataInjector(config, "conversions"),  # refunds arrive late too
        ]
        super().__init__(config, shared_state, edge_injectors, validator)
        self._refund_types = list(REFUND_TYPES.keys())
        self._refund_weights = list(REFUND_TYPES.values())

    def _generate_record(self) -> Dict[str, Any]:
        now = self.current_event_time()

        # Source from a real conversion (refunds reference conversions)
        conversions = self.shared_state.funnel.get_recent("click", n=1)
        # We use click buffer as proxy — in full system, would use conversion buffer
        has_source = bool(conversions)

        refund_type = str(self._rng.choice(self._refund_types, p=self._refund_weights))
        campaign = self.shared_state.campaigns.get_active_campaign()
        user = self.shared_state.users.get_or_create_user()

        if has_source:
            src = conversions[0]
            original_conversion_id = src.event_id
            campaign_id = src.campaign_id
            user_id = src.user_id
            original_amount = float(
                self._rng.lognormal(mean=3.5, sigma=1.2)
            )  # approximate original revenue
        else:
            original_conversion_id = f"CONV_{str(uuid.uuid4())[:12]}"
            campaign_id = campaign.campaign_id
            user_id = user.user_id
            original_amount = float(self._rng.lognormal(mean=3.5, sigma=1.2))

        # Refund delay: chargebacks arrive weeks later, fraud reversals are immediate
        delay_profile = {
            "full_refund": (1, 48),         # hours
            "partial_refund": (2, 72),
            "chargeback": (24 * 7, 24 * 90),  # 1-90 days
            "fraud_reversal": (0, 4),
            "goodwill_credit": (24, 168),     # 1-7 days
        }
        delay_min, delay_max = delay_profile[refund_type]
        delay_hours = float(self._rng.uniform(delay_min, delay_max))
        refund_ts = now - timedelta(hours=delay_hours)  # backdate the refund

        # Refund amount
        if refund_type == "full_refund":
            refund_amount = original_amount
            refund_pct = 1.0
        elif refund_type == "partial_refund":
            refund_pct = float(self._rng.uniform(0.1, 0.9))
            refund_amount = round(original_amount * refund_pct, 4)
        elif refund_type in ("chargeback", "fraud_reversal"):
            refund_amount = original_amount  # always full
            refund_pct = 1.0
        else:  # goodwill_credit
            refund_pct = float(self._rng.uniform(0.05, 0.30))
            refund_amount = round(original_amount * refund_pct, 4)

        # Attribution impact
        attribution_impact = {
            "full_refund": "full_reversal",
            "partial_refund": "partial_reversal",
            "chargeback": "full_reversal",
            "fraud_reversal": "full_reversal_and_flag",
            "goodwill_credit": "none",
        }[refund_type]

        record = {
            "event_id": self.generate_event_id(),
            "event_type": "refund",
            "event_timestamp": self.to_iso(refund_ts),
            "ingestion_timestamp": None,
            "original_conversion_id": original_conversion_id,
            "campaign_id": campaign_id,
            "user_id": user_id,
            "refund_type": refund_type,
            "original_amount": round(original_amount, 4),
            "refund_amount": round(refund_amount, 4),
            "refund_amount_micros": int(refund_amount * 1_000_000),
            "refund_pct": round(refund_pct, 4),
            "currency": "USD",
            "delay_hours": round(delay_hours, 2),
            "attribution_impact": attribution_impact,
            "requires_invoice_adjustment": refund_type in (
                "full_refund", "partial_refund", "chargeback"
            ),
            "requires_fraud_flag": refund_type in ("chargeback", "fraud_reversal"),
            "ledger_entry_type": "REVERSAL",
            "is_fraud": refund_type == "fraud_reversal",
            "fraud_label": "fraud_reversal" if refund_type == "fraud_reversal" else "clean",
            "fraud_signals": {},
            "_financial_edge_case": "refund",
        }

        # Chargeback-specific fields
        if refund_type == "chargeback":
            record["chargeback_reason_code"] = str(
                self._rng.choice(CHARGEBACK_REASON_CODES)
            )
            record["card_network"] = str(self._rng.choice(["visa", "mastercard", "amex"], p=[0.5, 0.35, 0.15]))
            record["dispute_status"] = str(self._rng.choice(
                ["open", "under_review", "resolved_merchant_win", "resolved_customer_win"],
                p=[0.3, 0.4, 0.2, 0.1]
            ))

        return record

    def _get_schema(self) -> Dict:
        return {
            "type": "object",
            "required": ["event_id", "event_timestamp", "original_conversion_id", "refund_amount"],
        }