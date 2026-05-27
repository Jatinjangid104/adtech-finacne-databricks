import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import numpy as np

from core.base_generator import BaseEventGenerator
from simulation.shared_state import SharedGeneratorState, FunnelEvent
from edge_cases.injectors import BaseEdgeCaseInjector, LateDataInjector

logger = logging.getLogger(__name__)

RECONCILIATION_FAILURE_TYPES = {
    "double_billing": 0.30,
    "phantom_billing": 0.15,
    "underbilling": 0.20,
    "ledger_mismatch": 0.15,
    "missing_ledger_entry": 0.10,
    "currency_conversion_drift": 0.05,
    "retroactive_price_change": 0.05,
}

SUPPORTED_CURRENCIES = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 148.5,
    "CAD": 1.35,
    "AUD": 1.53,
    "INR": 83.2,
}


class BillingRecordGenerator(BaseEventGenerator):

    EVENT_TYPE = "billing_records"

    def __init__(self, config, shared_state: SharedGeneratorState, validator=None):
        edge_injectors = [LateDataInjector(config, "conversions")]  # reuse conversion latency profile
        super().__init__(config, shared_state, edge_injectors, validator)
        self._recon_cfg = config.reconciliation
        self._failure_types = list(RECONCILIATION_FAILURE_TYPES.keys())
        self._failure_weights = list(RECONCILIATION_FAILURE_TYPES.values())
        self._currencies = list(SUPPORTED_CURRENCIES.keys())
        self._fx_rates = list(SUPPORTED_CURRENCIES.values())

        # FX rate snapshot at billing time (will drift vs ledger time)
        self._billing_fx_snapshot: Dict[str, float] = dict(SUPPORTED_CURRENCIES)

        # Buffer of billing records for generating matching ledger entries
        self._billing_buffer: List[Dict] = []

    def _generate_record(self) -> Dict[str, Any]:
        now = self.current_event_time()
        failure_pct = self._recon_cfg.failure_injection_pct
        roll = self._rng.random()

        if roll < failure_pct:
            return self._generate_failing_billing_record(now)
        else:
            return self._generate_clean_billing_record(now)

    def _generate_clean_billing_record(self, now: datetime) -> Dict:
        # Source from a real click or conversion
        source_events = (
            self.shared_state.funnel.get_recent("click", n=1) or
            self.shared_state.funnel.get_recent("impression", n=1)
        )
        campaign = self.shared_state.campaigns.get_active_campaign()

        if source_events:
            src = source_events[0]
            event_id = src.event_id
            campaign_id = src.campaign_id
            user_id = src.user_id
            event_type = src.event_type
            amount = src.metadata.get("cost", campaign.bid_price)
        else:
            event_id = self.generate_event_id()
            campaign_id = campaign.campaign_id
            user_id = f"u_{str(uuid.uuid4())[:12]}"
            event_type = "click"
            amount = campaign.bid_price

        billing_id = self.generate_event_id()
        currency = str(self._rng.choice(self._currencies[:3]))  # mostly USD/EUR/GBP
        fx_rate = self._billing_fx_snapshot.get(currency, 1.0)
        amount_local = round(amount * fx_rate, 6)

        record = {
            "billing_id": billing_id,
            "event_id": event_id,
            "event_type": event_type,
            "event_timestamp": self.to_iso(now - timedelta(seconds=int(self._rng.integers(1, 60)))),
            "billing_timestamp": self.to_iso(now),
            "ingestion_timestamp": None,
            "campaign_id": campaign_id,
            "advertiser_id": campaign.advertiser_id,
            "user_id": user_id,
            "amount_usd": round(amount, 6),
            "amount_local": amount_local,
            "currency": currency,
            "fx_rate": fx_rate,
            "pricing_model": campaign.pricing_model,
            "billing_status": "settled",
            "reconciliation_status": "pending",
            "is_reconciliation_failure": False,
            "failure_type": None,
            "failure_details": {},
        }
        self._billing_buffer.append(record)
        return record

    def _generate_failing_billing_record(self, now: datetime) -> Dict:
        failure_type = str(self._rng.choice(self._failure_types, p=self._failure_weights))
        campaign = self.shared_state.campaigns.get_active_campaign()
        base_amount = campaign.bid_price

        record = {
            "billing_id": self.generate_event_id(),
            "event_type": "click",
            "event_timestamp": self.to_iso(now - timedelta(seconds=int(self._rng.integers(1, 300)))),
            "billing_timestamp": self.to_iso(now),
            "ingestion_timestamp": None,
            "campaign_id": campaign.campaign_id,
            "advertiser_id": campaign.advertiser_id,
            "currency": "USD",
            "fx_rate": 1.0,
            "pricing_model": campaign.pricing_model,
            "billing_status": "settled",
            "is_reconciliation_failure": True,
            "failure_type": failure_type,
            "failure_details": {},
        }

        if failure_type == "double_billing":
            # Re-use an event_id from the buffer (same event billed twice)
            if self._billing_buffer:
                original = self._billing_buffer[int(self._rng.integers(0, len(self._billing_buffer)))]
                record["event_id"] = original["event_id"]
                record["amount_usd"] = original["amount_usd"]
                record["amount_local"] = original["amount_usd"]
                record["user_id"] = original.get("user_id", "unknown")
                record["failure_details"] = {
                    "original_billing_id": original["billing_id"],
                    "description": "Same event billed twice due to at-least-once delivery",
                }
            else:
                record["event_id"] = self.generate_event_id()
                record["amount_usd"] = round(base_amount, 6)
                record["amount_local"] = round(base_amount, 6)
                record["user_id"] = f"u_{str(uuid.uuid4())[:12]}"

        elif failure_type == "phantom_billing":
            # event_id that does NOT exist in the event stream
            record["event_id"] = f"PHANTOM_{str(uuid.uuid4())[:12]}"
            record["amount_usd"] = round(base_amount, 6)
            record["amount_local"] = round(base_amount, 6)
            record["user_id"] = f"u_{str(uuid.uuid4())[:12]}"
            record["failure_details"] = {
                "description": "Billing record references non-existent event_id",
                "likely_cause": "billing engine processed from wrong Kafka offset",
            }

        elif failure_type == "underbilling":
            # event_id exists, billing amount is less than it should be
            clicks = self.shared_state.funnel.get_recent("click", n=1)
            event_id = clicks[0].event_id if clicks else self.generate_event_id()
            correct_amount = campaign.bid_price
            billed_amount = round(correct_amount * float(self._rng.uniform(0.1, 0.8)), 6)
            record["event_id"] = event_id
            record["amount_usd"] = billed_amount
            record["amount_local"] = billed_amount
            record["user_id"] = clicks[0].user_id if clicks else f"u_{str(uuid.uuid4())[:12]}"
            record["failure_details"] = {
                "correct_amount": correct_amount,
                "billed_amount": billed_amount,
                "underbilling_delta": round(correct_amount - billed_amount, 6),
                "description": "Partial billing — billing engine crashed mid-batch",
            }

        elif failure_type == "ledger_mismatch":
            # Billing is correct but ledger will have different amount
            correct_amount = base_amount
            ledger_amount = round(correct_amount * float(self._rng.uniform(0.95, 1.05)), 6)
            record["event_id"] = self.generate_event_id()
            record["amount_usd"] = correct_amount
            record["amount_local"] = correct_amount
            record["user_id"] = f"u_{str(uuid.uuid4())[:12]}"
            record["failure_details"] = {
                "billing_amount": correct_amount,
                "expected_ledger_amount": ledger_amount,
                "delta": round(ledger_amount - correct_amount, 6),
                "description": "FX rate used in ledger differs from billing rate",
            }

        elif failure_type == "missing_ledger_entry":
            record["event_id"] = self.generate_event_id()
            record["amount_usd"] = round(base_amount, 6)
            record["amount_local"] = round(base_amount, 6)
            record["user_id"] = f"u_{str(uuid.uuid4())[:12]}"
            record["billing_status"] = "settled"
            record["failure_details"] = {
                "description": "Billing settled but no ledger debit/credit created",
                "double_entry_violation": True,
            }

        elif failure_type == "currency_conversion_drift":
            currency = str(self._rng.choice(["EUR", "GBP", "JPY"]))
            billing_rate = SUPPORTED_CURRENCIES[currency]
            # Ledger uses a rate from 24 hours later — FX drift
            drift = float(self._rng.uniform(-0.02, 0.02))
            ledger_rate = billing_rate * (1 + drift)
            amount_usd = base_amount
            record["event_id"] = self.generate_event_id()
            record["currency"] = currency
            record["fx_rate"] = billing_rate
            record["amount_usd"] = round(amount_usd, 6)
            record["amount_local"] = round(amount_usd * billing_rate, 6)
            record["user_id"] = f"u_{str(uuid.uuid4())[:12]}"
            record["failure_details"] = {
                "billing_fx_rate": billing_rate,
                "ledger_fx_rate": round(ledger_rate, 6),
                "fx_drift_pct": round(drift * 100, 4),
                "amount_discrepancy_usd": round(
                    amount_usd * abs(ledger_rate - billing_rate), 6
                ),
            }

        elif failure_type == "retroactive_price_change":
            # Campaign pricing changed AFTER these events were billed
            old_price = campaign.bid_price
            new_price = round(old_price * float(self._rng.uniform(0.7, 1.4)), 6)
            record["event_id"] = self.generate_event_id()
            record["amount_usd"] = round(old_price, 6)  # billed at old price
            record["amount_local"] = round(old_price, 6)
            record["user_id"] = f"u_{str(uuid.uuid4())[:12]}"
            record["failure_details"] = {
                "billed_at_price": old_price,
                "correct_price_after_change": new_price,
                "price_delta": round(new_price - old_price, 6),
                "requires_adjustment": True,
                "description": "Campaign price changed retroactively after billing settled",
            }

        self._billing_buffer.append(record)
        return record

    def _get_schema(self) -> Dict:
        return {
            "type": "object",
            "required": ["billing_id", "event_id", "billing_timestamp", "amount_usd"],
        }


class LedgerEntryGenerator(BaseEventGenerator):

    EVENT_TYPE = "ledger_entries"

    def __init__(self, config, shared_state: SharedGeneratorState,
                 billing_generator: BillingRecordGenerator, validator=None):
        edge_injectors = []
        super().__init__(config, shared_state, edge_injectors, validator)
        self._billing_gen = billing_generator
        self._recon_cfg = config.reconciliation

    def _generate_record(self) -> Dict[str, Any]:
        now = self.current_event_time()

        # Source from billing buffer
        if not self._billing_gen._billing_buffer:
            # No billing records yet — generate a standalone ledger entry
            return self._generate_orphan_ledger_entry(now)

        billing = self._billing_gen._billing_buffer[
            int(self._rng.integers(0, len(self._billing_gen._billing_buffer)))
        ]
        return self._generate_ledger_pair(billing, now)

    def _generate_ledger_pair(self, billing: Dict, now: datetime) -> Dict:

        debit_amount = billing.get("amount_usd", 0.0)
        credit_amount = debit_amount

        is_failure = billing.get("is_reconciliation_failure", False)
        failure_type = billing.get("failure_type")

        # Introduce ledger-specific failures
        ledger_error = None
        if is_failure and failure_type in ("ledger_mismatch", "missing_ledger_entry"):
            if failure_type == "missing_ledger_entry":
                # Return a null-like record to signal missing entry
                return {
                    "ledger_id": self.generate_event_id(),
                    "billing_id": billing["billing_id"],
                    "event_id": billing.get("event_id"),
                    "entry_timestamp": self.to_iso(now),
                    "ingestion_timestamp": None,
                    "campaign_id": billing.get("campaign_id"),
                    "advertiser_id": billing.get("advertiser_id"),
                    "debit_account": None,
                    "credit_account": None,
                    "debit_amount_usd": None,
                    "credit_amount_usd": None,
                    "currency": billing.get("currency", "USD"),
                    "is_balanced": False,
                    "ledger_error": "missing_entry",
                    "reconciliation_status": "failed",
                }
            elif failure_type == "ledger_mismatch":
                delta = billing["failure_details"].get("delta", 0.0)
                credit_amount = round(debit_amount + delta, 6)
                ledger_error = "amount_mismatch"

        # Rounding: penny discrepancy (very common in prod)
        if self._rng.random() < 0.005:
            credit_amount = round(credit_amount + 0.01, 6)
            ledger_error = ledger_error or "rounding_discrepancy"

        is_balanced = abs(debit_amount - credit_amount) < 0.001

        return {
            "ledger_id": self.generate_event_id(),
            "billing_id": billing["billing_id"],
            "event_id": billing.get("event_id"),
            "entry_timestamp": self.to_iso(now),
            "ingestion_timestamp": None,
            "campaign_id": billing.get("campaign_id"),
            "advertiser_id": billing.get("advertiser_id"),
            "debit_account": f"AR_{billing.get('advertiser_id', 'unknown')}",
            "credit_account": "REVENUE_DIGITAL_ADV",
            "debit_amount_usd": round(debit_amount, 6),
            "credit_amount_usd": round(credit_amount, 6),
            "currency": billing.get("currency", "USD"),
            "fx_rate": billing.get("fx_rate", 1.0),
            "is_balanced": is_balanced,
            "balance_delta": round(debit_amount - credit_amount, 6),
            "ledger_error": ledger_error,
            "pricing_model": billing.get("pricing_model"),
            "reconciliation_status": "pending" if is_balanced else "failed",
            "is_reconciliation_failure": not is_balanced or billing.get("is_reconciliation_failure", False),
        }

    def _generate_orphan_ledger_entry(self, now: datetime) -> Dict:
        campaign = self.shared_state.campaigns.get_active_campaign()
        amount = round(float(self._rng.lognormal(mean=0.5, sigma=0.8)), 6)
        return {
            "ledger_id": self.generate_event_id(),
            "billing_id": f"ORPHAN_{str(uuid.uuid4())[:12]}",
            "event_id": None,
            "entry_timestamp": self.to_iso(now),
            "ingestion_timestamp": None,
            "campaign_id": campaign.campaign_id,
            "advertiser_id": campaign.advertiser_id,
            "debit_account": f"AR_{campaign.advertiser_id}",
            "credit_account": "REVENUE_DIGITAL_ADV",
            "debit_amount_usd": amount,
            "credit_amount_usd": amount,
            "currency": "USD",
            "fx_rate": 1.0,
            "is_balanced": True,
            "balance_delta": 0.0,
            "ledger_error": "no_billing_reference",
            "reconciliation_status": "failed",
            "is_reconciliation_failure": True,
        }

    def _get_schema(self) -> Dict:
        return {
            "type": "object",
            "required": ["ledger_id", "billing_id", "debit_amount_usd", "credit_amount_usd"],
        }