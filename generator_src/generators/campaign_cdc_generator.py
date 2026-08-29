from datetime import datetime, timezone
from typing import Any, Dict
import numpy as np

from core.base_generator import BaseEventGenerator
from simulation.shared_state import SharedGeneratorState
from edge_cases.injectors import LateDataInjector


class CampaignCDCGenerator(BaseEventGenerator):

    EVENT_TYPE = "campaign_cdc"

    def __init__(self, config, shared_state: SharedGeneratorState, validator=None):
        edge_injectors = [
            LateDataInjector(config, self.EVENT_TYPE),
        ]
        super().__init__(config, shared_state, edge_injectors, validator)

    def _generate_record(self) -> Dict[str, Any]:
        cdc_event = self.shared_state.campaigns.generate_cdc_event()
        if cdc_event is None:
            # Fallback: generate a no-op heartbeat CDC event
            campaign = self.shared_state.campaigns.get_active_campaign()
            return {
                "event_id": self.generate_event_id(),
                "event_type": "campaign_cdc",
                "event_timestamp": self.to_iso(self.current_event_time()),
                "ingestion_timestamp": None,
                "campaign_id": campaign.campaign_id,
                "change_type": "heartbeat",
                "cdc_version": campaign.cdc_version,
                "before": {},
                "after": {},
                "is_fraud": False,
                "fraud_label": "clean",
            }
        return cdc_event

    def _get_schema(self) -> Dict:
        return {
            "type": "object",
            "required": ["event_id", "event_timestamp", "campaign_id", "change_type"],
        }
