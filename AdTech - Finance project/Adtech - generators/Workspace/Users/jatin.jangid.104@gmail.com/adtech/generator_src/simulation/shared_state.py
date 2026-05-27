import hashlib
import logging
import random
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Deque, Dict, List, Optional, Set
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    user_id: str
    is_bot: bool
    device_affinity: str           # primary device (but can switch)
    geo_country: str
    geo_city: str
    ip_address: str
    created_at: datetime
    session_count: int = 0
    last_seen: Optional[datetime] = None
    current_session_id: Optional[str] = None
    session_start: Optional[datetime] = None
    campaign_affinity: List[str] = field(default_factory=list)  # preferred campaigns


@dataclass
class CampaignState:
    campaign_id: str
    advertiser_id: str
    name: str
    category: str
    pricing_model: str             # CPC | CPM | CPA
    bid_price: float
    daily_budget: float
    total_budget: float
    spent_budget: float = 0.0
    status: str = "active"         # active | paused | ended
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_modified: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    targeting_geos: List[str] = field(default_factory=list)
    targeting_devices: List[str] = field(default_factory=list)
    cdc_version: int = 1


@dataclass
class FunnelEvent:
    event_id: str
    event_type: str                # bid_request | impression | click | conversion
    campaign_id: str
    user_id: str
    session_id: str
    event_timestamp: datetime
    metadata: Dict = field(default_factory=dict)


class UserSimulator:

    DEVICE_TYPES = ["mobile", "desktop", "tablet"]
    GEO_CITY_MAP = {
        "US": ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"],
        "GB": ["London", "Manchester", "Birmingham", "Glasgow", "Leeds"],
        "DE": ["Berlin", "Munich", "Hamburg", "Frankfurt", "Cologne"],
        "FR": ["Paris", "Lyon", "Marseille", "Toulouse", "Nice"],
        "CA": ["Toronto", "Vancouver", "Montreal", "Calgary", "Ottawa"],
        "AU": ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide"],
        "IN": ["Mumbai", "Delhi", "Bangalore", "Chennai", "Hyderabad"],
        "OTHER": ["Singapore", "Dubai", "Tokyo", "Seoul", "Amsterdam"],
    }

    def __init__(self, config, rng: np.random.Generator):
        self.config = config
        self._rng = rng
        self._users: Dict[str, UserProfile] = {}
        self._bot_ips: List[str] = []
        self._clean_ips: List[str] = []
        self._fraud_ips: List[str] = []

        self._initialize_ip_pools()
        self._initialize_user_pool()

    def _random_ip(self, reserved: bool = False) -> str:
        while True:
            octets = [
                int(self._rng.integers(1, 255)),
                int(self._rng.integers(0, 255)),
                int(self._rng.integers(0, 255)),
                int(self._rng.integers(1, 254)),
            ]
            # Skip private/loopback ranges
            if octets[0] in (10, 127) or (octets[0] == 172 and 16 <= octets[1] <= 31):
                continue
            if octets[0] == 192 and octets[1] == 168:
                continue
            return ".".join(map(str, octets))

    def _initialize_ip_pools(self):
        cfg = self.config.fraud
        # Clean user IPs
        pool_size = cfg.ip_pool_size
        fraud_pool_size = cfg.fraud_ip_pool_size
        self._clean_ips = [self._random_ip() for _ in range(pool_size - fraud_pool_size)]
        # Fraud IPs — small pool, high reuse (click farm signature)
        self._fraud_ips = [self._random_ip() for _ in range(fraud_pool_size)]
        logger.info(f"IP pools: {len(self._clean_ips)} clean, {len(self._fraud_ips)} fraud")

    def _initialize_user_pool(self):
        cfg = self.config.users
        pool_size = cfg.total_pool_size
        bot_count = int(pool_size * cfg.bot_user_ratio)
        geo_dist = self.config.users.geo_distribution

        geo_countries = list(geo_dist.to_dict().keys())
        geo_weights = list(geo_dist.to_dict().values())

        device_dist = cfg.device_distribution
        device_types = list(device_dist.to_dict().keys())
        device_weights = list(device_dist.to_dict().values())

        for i in range(pool_size):
            user_id = f"u_{str(uuid.UUID(int=int(hashlib.md5(str(i).encode()).hexdigest(), 16)))}"
            is_bot = i < bot_count

            country = self._rng.choice(geo_countries, p=geo_weights)
            city = self._rng.choice(self.GEO_CITY_MAP.get(country, ["Unknown"]))
            device = self._rng.choice(device_types, p=device_weights)
            ip = (
                self._rng.choice(self._fraud_ips)
                if is_bot
                else self._rng.choice(self._clean_ips)
            )

            self._users[user_id] = UserProfile(
                user_id=user_id,
                is_bot=is_bot,
                device_affinity=device,
                geo_country=country,
                geo_city=city,
                ip_address=ip,
                created_at=datetime.now(timezone.utc),
            )

        logger.info(f"User pool initialized: {pool_size} users ({bot_count} bots)")

    def get_or_create_user(self) -> UserProfile:

        cfg = self.config.users
        all_ids = list(self._users.keys())

        roll = self._rng.random()
        if roll < cfg.new_user_injection_rate:
            # Inject a truly new user mid-stream
            new_user = self._create_new_user()
            self._users[new_user.user_id] = new_user
            return new_user
        elif roll < cfg.new_user_injection_rate + (1 - cfg.returning_user_ratio):
            # Pick from recently created users (not returning)
            recent_ids = all_ids[-1000:]
            return self._users[str(self._rng.choice(recent_ids))]
        else:
            # Returning user — skewed toward more active users (Zipf)
            # Heavy users appear more often
            weights = np.ones(len(all_ids))
            # Assign higher weight to users with more sessions
            for idx, uid in enumerate(all_ids[:100]):  # top 100 are "heavy"
                weights[idx] = 5.0
            weights /= weights.sum()
            chosen_id = str(self._rng.choice(all_ids, p=weights))
            return self._users[chosen_id]

    def _create_new_user(self) -> UserProfile:
        cfg = self.config.users
        geo_dist = cfg.geo_distribution
        geo_countries = list(geo_dist.to_dict().keys())
        geo_weights = list(geo_dist.to_dict().values())
        device_dist = cfg.device_distribution
        device_types = list(device_dist.to_dict().keys())
        device_weights = list(device_dist.to_dict().values())

        user_id = f"u_{str(uuid.uuid4())[:12]}"
        country = str(self._rng.choice(geo_countries, p=geo_weights))
        city = str(self._rng.choice(self.GEO_CITY_MAP.get(country, ["Unknown"])))
        device = str(self._rng.choice(device_types, p=device_weights))
        ip = str(self._rng.choice(self._clean_ips))

        return UserProfile(
            user_id=user_id,
            is_bot=False,
            device_affinity=device,
            geo_country=country,
            geo_city=city,
            ip_address=ip,
            created_at=datetime.now(timezone.utc),
        )

    def get_or_create_session(self, user: UserProfile) -> str:
        timeout_minutes = self.config.users.session_timeout_minutes
        now = datetime.now(timezone.utc)

        if user.is_bot:
            timeout_minutes = 2  # bots cycle sessions aggressively

        session_expired = (
            user.session_start is None
            or (now - user.session_start) > timedelta(minutes=timeout_minutes)
        )

        if session_expired:
            user.current_session_id = f"s_{str(uuid.uuid4())[:16]}"
            user.session_start = now
            user.session_count += 1

        user.last_seen = now
        return user.current_session_id

    def get_device_for_event(self, user: UserProfile) -> str:
        if self._rng.random() < 0.10 and not user.is_bot:
            others = [d for d in self.DEVICE_TYPES if d != user.device_affinity]
            return str(self._rng.choice(others))
        return user.device_affinity

    def all_user_ids(self) -> List[str]:
        return list(self._users.keys())

    def get_user(self, user_id: str) -> Optional[UserProfile]:
        return self._users.get(user_id)


class CampaignStateManager:

    def __init__(self, config, rng: np.random.Generator):
        self.config = config
        self._rng = rng
        self._campaigns: Dict[str, CampaignState] = {}
        self._cdc_log: Deque[Dict] = deque(maxlen=10000)

        self._initialize_campaigns()

    def _generate_bid_price(self, pricing_model: str) -> float:
        pm_cfg = self.config.campaigns.pricing_models[pricing_model]
        dist = pm_cfg.bid_distribution
        if dist == "lognormal":
            price = float(self._rng.lognormal(
                mean=pm_cfg.bid_mu,
                sigma=pm_cfg.bid_sigma
            ))
            return round(max(pm_cfg.bid_floor, min(price, pm_cfg.bid_ceiling)), 4)
        return float(pm_cfg.bid_floor)

    def _initialize_campaigns(self):
        cfg = self.config.campaigns
        pm_list = list(cfg.pricing_models.to_dict().keys())
        pm_weights = [cfg.pricing_models[m].weight for m in pm_list]
        categories = list(cfg.categories)

        # Pareto budget distribution: top campaigns get most budget
        count = cfg.count
        pareto_alpha = cfg.pareto_alpha
        budgets = self._rng.pareto(pareto_alpha, count) + 1
        budgets = (budgets / budgets.sum()) * (count * 5000)  # total pool = N*5000

        for i in range(count):
            campaign_id = f"camp_{str(uuid.UUID(int=int(hashlib.md5(str(i*999).encode()).hexdigest(), 16)))[:8]}"
            advertiser_id = f"adv_{str(i // 5).zfill(4)}"  # ~10 campaigns per advertiser
            pricing_model = str(self._rng.choice(pm_list, p=pm_weights))
            status_roll = self._rng.random()
            lc = cfg.lifecycle
            if status_roll < lc.active_probability:
                status = "active"
            elif status_roll < lc.active_probability + lc.paused_probability:
                status = "paused"
            else:
                status = "ended"

            daily_budget = float(budgets[i])
            self._campaigns[campaign_id] = CampaignState(
                campaign_id=campaign_id,
                advertiser_id=advertiser_id,
                name=f"Campaign_{i}_{str(self._rng.choice(categories))}",
                category=str(self._rng.choice(categories)),
                pricing_model=pricing_model,
                bid_price=self._generate_bid_price(pricing_model),
                daily_budget=daily_budget,
                total_budget=daily_budget * float(self._rng.integers(7, 90)),
                status=status,
                targeting_geos=list(self._rng.choice(
                    list(self.config.users.geo_distribution.to_dict().keys()),
                    size=int(self._rng.integers(1, 5)),
                    replace=False
                )),
                targeting_devices=list(self._rng.choice(
                    ["mobile", "desktop", "tablet"],
                    size=int(self._rng.integers(1, 4)),
                    replace=False
                )),
            )

        logger.info(f"Campaign pool initialized: {count} campaigns")

    def get_active_campaign(self) -> CampaignState:
        active = [c for c in self._campaigns.values() if c.status == "active"]
        if not active:
            # Edge case: all campaigns paused — return any (for testing downstream)
            active = list(self._campaigns.values())

        # Weight by remaining budget (richer campaigns get more impressions)
        budgets = np.array([max(c.daily_budget - c.spent_budget, 0.01) for c in active])
        weights = budgets / budgets.sum()
        idx = int(self._rng.choice(len(active), p=weights))
        return active[idx]

    def get_campaign(self, campaign_id: str) -> Optional[CampaignState]:
        return self._campaigns.get(campaign_id)

    def debit_spend(self, campaign_id: str, amount: float):
        if campaign_id in self._campaigns:
            self._campaigns[campaign_id].spent_budget += amount

    def generate_cdc_event(self) -> Optional[Dict]:

        cfg = self.config.event_rates.campaign_cdc
        campaigns = list(self._campaigns.values())
        campaign = self._campaigns[str(self._rng.choice(list(self._campaigns.keys())))]

        change_type_roll = self._rng.random()
        before_state = self._campaign_snapshot(campaign)

        if change_type_roll < cfg.budget_update_pct:
            change_type = "budget_update"
            # Increase or decrease budget
            factor = float(self._rng.uniform(0.5, 2.0))
            campaign.daily_budget = round(campaign.daily_budget * factor, 2)

        elif change_type_roll < cfg.budget_update_pct + cfg.pricing_change_pct:
            change_type = "pricing_model_change"
            pm_list = list(self.config.campaigns.pricing_models.to_dict().keys())
            new_model = str(self._rng.choice([m for m in pm_list if m != campaign.pricing_model]))
            campaign.pricing_model = new_model
            campaign.bid_price = self._generate_bid_price(new_model)

        elif change_type_roll < cfg.budget_update_pct + cfg.pricing_change_pct + cfg.pause_activate_pct:
            change_type = "status_change"
            campaign.status = "paused" if campaign.status == "active" else "active"

        else:
            change_type = "targeting_update"
            campaign.targeting_geos = list(self._rng.choice(
                list(self.config.users.geo_distribution.to_dict().keys()),
                size=int(self._rng.integers(1, 5)),
                replace=False
            ))

        campaign.last_modified = datetime.now(timezone.utc)
        campaign.cdc_version += 1

        after_state = self._campaign_snapshot(campaign)
        cdc_event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "campaign_cdc",
            "event_timestamp": campaign.last_modified.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "ingestion_timestamp": None,
            "campaign_id": campaign.campaign_id,
            "advertiser_id": campaign.advertiser_id,
            "change_type": change_type,
            "cdc_version": campaign.cdc_version,
            "before": before_state,
            "after": after_state,
            "is_fraud": False,
            "fraud_label": "clean",
        }
        self._cdc_log.append(cdc_event)
        return cdc_event

    def _campaign_snapshot(self, c: CampaignState) -> Dict:
        return {
            "pricing_model": c.pricing_model,
            "bid_price": c.bid_price,
            "daily_budget": c.daily_budget,
            "total_budget": c.total_budget,
            "status": c.status,
            "targeting_geos": c.targeting_geos,
            "targeting_devices": c.targeting_devices,
        }

    def all_campaign_ids(self) -> List[str]:
        return list(self._campaigns.keys())


class FunnelStateStore:

    MAX_BUFFER_PER_STAGE = 5000
    MAX_PENDING_CONVERSIONS = 10000

    def __init__(self):
        self._bid_requests: Deque[FunnelEvent] = deque(maxlen=self.MAX_BUFFER_PER_STAGE)
        self._impressions: Deque[FunnelEvent] = deque(maxlen=self.MAX_BUFFER_PER_STAGE)
        self._clicks: Deque[FunnelEvent] = deque(maxlen=self.MAX_BUFFER_PER_STAGE)
        # Pending conversions: click_id → list of pending (delayed) conversions
        self._pending_conversions: Dict[str, List[Dict]] = {}

    def add(self, stage: str, event: FunnelEvent):
        store = self._get_store(stage)
        if store is not None:
            store.append(event)

    def _get_store(self, stage: str) -> Optional[Deque]:
        return {
            "bid_request": self._bid_requests,
            "impression": self._impressions,
            "click": self._clicks,
        }.get(stage)

    def get_recent(self, stage: str, n: int = 1) -> List[FunnelEvent]:
        store = self._get_store(stage)
        if not store:
            return []
        items = list(store)
        if not items:
            return []
        # Return random recent items (not always the most recent — avoids artificial correlation)
        count = min(n, len(items))
        indices = np.random.choice(len(items), size=count, replace=False)
        return [items[i] for i in indices]

    def get_by_user(self, stage: str, user_id: str, n: int = 3) -> List[FunnelEvent]:
        store = self._get_store(stage)
        if not store:
            return []
        user_events = [e for e in store if e.user_id == user_id]
        return user_events[-n:] if user_events else []

    def register_pending_conversion(self, click_id: str, conversion_data: Dict):
        if len(self._pending_conversions) < self.MAX_PENDING_CONVERSIONS:
            if click_id not in self._pending_conversions:
                self._pending_conversions[click_id] = []
            self._pending_conversions[click_id].append(conversion_data)

    def pop_due_conversions(self, now: datetime) -> List[Dict]:
        due = []
        to_remove = []
        for click_id, conversions in self._pending_conversions.items():
            still_pending = []
            for conv in conversions:
                if datetime.fromisoformat(conv["_emit_after"]) <= now:
                    due.append(conv)
                else:
                    still_pending.append(conv)
            if still_pending:
                self._pending_conversions[click_id] = still_pending
            else:
                to_remove.append(click_id)
        for k in to_remove:
            del self._pending_conversions[k]
        return due

    def count(self, stage: str) -> int:
        store = self._get_store(stage)
        return len(store) if store else 0


class SharedGeneratorState:

    def __init__(self, config, rng: np.random.Generator):
        self.config = config
        self.users = UserSimulator(config, rng)
        self.campaigns = CampaignStateManager(config, rng)
        self.funnel = FunnelStateStore()