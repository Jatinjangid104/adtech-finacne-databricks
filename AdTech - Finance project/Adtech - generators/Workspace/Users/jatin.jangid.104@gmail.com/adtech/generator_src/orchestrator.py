import logging
import os
import signal
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class GeneratorOrchestrator:

    def __init__(self, config, validator=None):
        self.config = config
        self._running = False
        self._iteration = 0
        self._total_written = 0
        self._errors = 0
        self._start_time = None

        base_seed = config.platform.seed
        rng = np.random.default_rng(base_seed ^ 0xDEADBEEF)

        # ── Shared State ──────────────────────────────────────────────
        from simulation.shared_state import SharedGeneratorState
        self.shared_state = SharedGeneratorState(config, rng)

        # ── Schema Evolution (cross-cutting) ──────────────────────────
        from edge_cases.schema_evolution_injector import SchemaEvolutionController
        self.schema_controller = SchemaEvolutionController(config, rng)

        # ── Traffic Dynamics (cross-cutting) ──────────────────────────
        from simulation.traffic_dynamics import BurstTrafficController, HotPartitionSimulator
        self.burst_controller = BurstTrafficController(config, rng)
        self.hot_partition_sim = HotPartitionSimulator(config, rng, self.shared_state)

        # ── Replay Manager ────────────────────────────────────────────
        from simulation.replay_manager import ReplayManager
        self.replay_manager = ReplayManager(config)

        if config.replay.enabled:
            self.replay_manager.start_replay(
                replay_type=config.replay.replay_type,
                seed=config.replay.replay_seed,
                reason=config.replay.replay_reason,
            )

        # ── Build Generator Pipeline ──────────────────────────────────
        self._generators = self._build_generators(validator, rng)

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _build_generators(self, validator, rng) -> List:
        from generators.bid_request_generator import BidRequestGenerator
        from generators.impression_generator import ImpressionGenerator
        from generators.click_generator import ClickGenerator
        from generators.conversion_generator import ConversionGenerator
        from generators.campaign_cdc_generator import CampaignCDCGenerator
        from generators.refund_generator import RefundGenerator
        from generators.reconciliation_generator import (
            BillingRecordGenerator, LedgerEntryGenerator
        )

        billing_gen = BillingRecordGenerator(self.config, self.shared_state, validator)
        ledger_gen = LedgerEntryGenerator(
            self.config, self.shared_state, billing_gen, validator
        )

        return [
            # Funnel order matters: each layer references the one above
            BidRequestGenerator(self.config, self.shared_state, validator),
            ImpressionGenerator(self.config, self.shared_state, validator),
            ClickGenerator(self.config, self.shared_state, validator),
            ConversionGenerator(self.config, self.shared_state, validator),
            # Financial layer (reference conversions/clicks)
            RefundGenerator(self.config, self.shared_state, validator),
            billing_gen,
            ledger_gen,
            # Metadata stream
            CampaignCDCGenerator(self.config, self.shared_state, validator),
        ]

    def _build_cross_cutting_injectors(self, rng) -> Dict:
        from edge_cases.time_chaos_injector import TimeChaosInjector
        from edge_cases.data_quality_injector import DataQualityInjector
        from edge_cases.schema_evolution_injector import SchemaEvolutionInjector

        return {
            event_type: [
                TimeChaosInjector(self.config, event_type),
                DataQualityInjector(self.config, event_type),
                SchemaEvolutionInjector(
                    self.config, event_type, self.schema_controller
                ),
            ]
            for event_type in [
                "bid_requests", "impressions", "clicks", "conversions",
                "refunds", "billing_records", "ledger_entries", "campaign_cdc",
            ]
        }

    def _handle_shutdown(self, signum, frame):
        logger.info("Shutdown signal received. Finishing current batch...")
        self._running = False

    def run(self, max_iterations: Optional[int] = None):
        self._running = True
        self._start_time = time.monotonic()

        # Build cross-cutting injectors
        cross_injectors = self._build_cross_cutting_injectors(
            np.random.default_rng(self.config.platform.seed ^ 0xCAFEBABE)
        )
        cross_rng = np.random.default_rng(self.config.platform.seed ^ 0xF00D)

        logger.info("=" * 70)
        logger.info("AdTech Data Generator V2 — All 14 FAANG Challenges Active")
        logger.info(f"  Active generators: {[g.EVENT_TYPE for g in self._generators]}")
        logger.info(f"  Schema evolution: {self.config.schema_evolution.enabled}")
        logger.info(f"  Time chaos: {self.config.time_chaos.enabled}")
        logger.info(f"  Burst traffic: {self.config.burst_traffic.enabled}")
        logger.info("=" * 70)

        while self._running:
            if max_iterations is not None and self._iteration >= max_iterations:
                break

            batch_start = time.monotonic()

            # ── Tick cross-cutting controllers ────────────────────────
            burst_mult, burst_cause = self.burst_controller.get_current_multiplier()
            self.burst_controller.maybe_trigger_burst()
            self.hot_partition_sim.tick()

            if burst_cause:
                logger.warning(f"[BURST ACTIVE] {burst_cause} × {burst_mult:.1f}")

            # ── Run each generator ────────────────────────────────────
            batch_written = 0
            for generator in self._generators:
                try:
                    # Run the generator's normal batch
                    records = self._run_generator_batch(generator, burst_mult)

                    # Apply cross-cutting injectors (time chaos, DQ, schema evolution)
                    injectors = cross_injectors.get(generator.EVENT_TYPE, [])
                    for injector in injectors:
                        records = injector.inject(records, cross_rng, generator.metrics)

                    # Apply hot partition forcing
                    records = [
                        self.hot_partition_sim.apply_hot_key(r, cross_rng)
                        for r in records
                    ]

                    # Apply burst metadata tagging
                    if burst_cause:
                        records = [
                            self.burst_controller.inject_burst_metadata(r, burst_cause)
                            for r in records
                        ]

                    # Apply replay annotation
                    if self.config.replay.enabled:
                        records = [self.replay_manager.annotate_record(r) for r in records]

                    # Write final records
                    written = generator._write_batch(records)
                    batch_written += len(records)

                except Exception as e:
                    self._errors += 1
                    logger.error(
                        f"Generator {generator.EVENT_TYPE} failed: {e}",
                        exc_info=True
                    )

            self._total_written += batch_written
            self._iteration += 1

            if self._iteration % 10 == 0:
                self._log_aggregate_stats()

            elapsed = time.monotonic() - batch_start
            sleep_time = max(0, self.config.platform.batch_interval_seconds - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        if self.config.replay.enabled:
            self.replay_manager.end_replay()

        self._log_final_stats()

    def _run_generator_batch(self, generator, burst_multiplier: float) -> List[Dict]:
        base_batch_size = generator._calculate_batch_size()
        burst_batch_size = max(1, int(base_batch_size * burst_multiplier))

        records = []
        for _ in range(burst_batch_size):
            try:
                record = generator._generate_record()
                record["ingestion_timestamp"] = generator.to_iso(
                    __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    )
                )
                generator._add_to_dedup_buffer(record["event_id"])
                records.append(record)
                generator.metrics.records_generated += 1
            except Exception as e:
                logger.warning(f"Record generation failed in {generator.EVENT_TYPE}: {e}")

        # Apply per-generator edge case injectors
        records = generator._apply_edge_cases(records)
        return records

    def _log_aggregate_stats(self):
        elapsed = time.monotonic() - self._start_time
        rps = self._total_written / elapsed if elapsed > 0 else 0
        logger.info(
            f"[ORCHESTRATOR] iter={self._iteration} written={self._total_written:,} "
            f"errors={self._errors} rps={rps:.1f} "
            f"schema_versions={self.schema_controller._current_versions}"
        )

    def _log_final_stats(self):
        elapsed = time.monotonic() - self._start_time
        logger.info("=" * 70)
        logger.info("Run Complete")
        logger.info(f"  Iterations:    {self._iteration:,}")
        logger.info(f"  Records:       {self._total_written:,}")
        logger.info(f"  Errors:        {self._errors}")
        logger.info(f"  Throughput:    {self._total_written/max(elapsed,1):.1f} rps")
        logger.info(f"  Schema state:  {self.schema_controller._current_versions}")
        logger.info("=" * 70)