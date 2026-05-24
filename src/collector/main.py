"""
SAP Audit Agent — Collector Main
Entry point for Layer 1: SAP Data Collection.

Run:
    python -m src.collector.main

Or import and use programmatically:
    from src.collector.main import run_collection
    records = run_collection(config)
"""

import logging
import sys
from typing import List, Optional

from ..common.config import get_config
from ..common.models import SAPEvidenceRecord, CollectionState
from .odata_client import SAPODataClient
from .journal_entry_collector import JournalEntryCollector
from .evidence_store import LocalEvidenceStore

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run_collection(config: Optional[dict] = None) -> List[SAPEvidenceRecord]:
    """
    Run a full collection cycle.

    1. Load configuration
    2. Connect to SAP
    3. Collect journal entries for all company codes
    4. Save evidence records to store
    5. Update collection state for next delta run

    Args:
        config: Optional config dict. Loads from config.yaml if not provided.

    Returns:
        List of collected SAPEvidenceRecord instances
    """
    if config is None:
        config = get_config()

    sap_config = config["sap"]
    collection_config = config["collection"]
    storage_config = config["storage"]

    logger.info("=" * 60)
    logger.info("SAP Audit Agent — Collection Starting")
    logger.info(f"Mode: {collection_config['mode']}")
    logger.info(f"Company codes: {collection_config['company_codes']}")
    logger.info(f"Fiscal year: {collection_config['fiscal_year']}")
    logger.info("=" * 60)

    # Initialize SAP client
    client = SAPODataClient(sap_config)

    # Test connectivity
    logger.info("Testing SAP connectivity...")
    if not client.ping():
        logger.error(
            "Cannot connect to SAP. Check base_url, credentials, "
            "and network connectivity."
        )
        sys.exit(1)
    logger.info("SAP connectivity: OK")

    # Initialize store
    store = LocalEvidenceStore(storage_config.get("local_path", "./output/evidence"))

    # Load previous collection state (for delta mode)
    previous_state = None
    if collection_config["mode"] == "delta":
        raw_state = store.load_collection_state()
        if raw_state:
            previous_state = CollectionState(**raw_state)
            logger.info(f"Resuming from previous state: last run {previous_state.last_run_at}")
        else:
            logger.info("No previous state found — running initial full collection")

    # Initialize collectors
    journal_collector = JournalEntryCollector(
        client=client,
        agent_service_users=set(collection_config["agent_service_users"]),
        company_codes=collection_config["company_codes"],
        fiscal_year=collection_config["fiscal_year"],
    )

    # Collect journal entries
    logger.info("Collecting journal entries...")
    records = journal_collector.collect(
        state=previous_state,
        lookback_days=collection_config.get("initial_lookback_days", 30),
    )

    # Log agent vs human breakdown
    agent_posted = [r for r in records if r.is_agent_posted]
    human_posted = [r for r in records if not r.is_agent_posted]
    logger.info(f"Records collected: {len(records)} total")
    logger.info(f"  Agent-posted: {len(agent_posted)}")
    logger.info(f"  Human-posted: {len(human_posted)}")

    # Save to evidence store
    if records:
        filepath = store.save_records(
            records,
            session_label=f"fy{collection_config['fiscal_year']}"
        )
        logger.info(f"Evidence saved: {filepath}")

        # Update collection state
        new_state = {}
        for cc in collection_config["company_codes"]:
            cc_state = journal_collector.get_collection_state(records, cc)
            new_state[cc] = cc_state.__dict__
        store.save_collection_state(new_state)
        logger.info("Collection state updated")

    logger.info("=" * 60)
    logger.info(f"Collection complete: {len(records)} records")
    logger.info("=" * 60)

    return records


if __name__ == "__main__":
    run_collection()
