"""
SAP Audit Agent — Correlator Main
Entry point for Layer 2: Evidence Correlation.

Run standalone:
    python -m src.correlator.main

Or import:
    from src.correlator.main import run_correlation
    packages = run_correlation(sap_records, config)
"""

import logging
import sys
from typing import List, Optional

from ..common.config import get_config
from ..common.models import SAPEvidenceRecord, EvidencePackage
from .correlator import EvidenceCorrelator, LocalAgentLogSource, LocalApprovalSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run_correlation(
    sap_records: List[SAPEvidenceRecord],
    config: Optional[dict] = None,
) -> List[EvidencePackage]:
    """
    Run correlation for a batch of SAP evidence records.

    Args:
        sap_records: Records from Layer 1 collector
        config: Optional config dict

    Returns:
        List of EvidencePackage instances ready for narrative generation
    """
    if config is None:
        config = get_config()

    storage_config = config["storage"]
    local_path = storage_config.get("local_path", "./output/evidence")

    logger.info("=" * 60)
    logger.info("SAP Audit Agent — Correlation Starting")
    logger.info(f"Records to correlate: {len(sap_records)}")
    logger.info("=" * 60)

    # Initialize sources
    log_source = LocalAgentLogSource(log_path=f"{local_path}/agent_logs")
    approval_source = LocalApprovalSource(approvals_path=f"{local_path}/approvals")

    # Initialize correlator
    correlator = EvidenceCorrelator(
        log_source=log_source,
        approval_source=approval_source,
    )

    # Run correlation
    packages = correlator.correlate_batch(sap_records)

    # Summary
    complete = [p for p in packages if p.completeness_score == 100]
    critical = [
        p for p in packages
        if any(g.audit_risk == "Critical" for g in p.gaps)
    ]

    logger.info("=" * 60)
    logger.info(f"Correlation complete: {len(packages)} packages")
    logger.info(f"  Complete (100%): {len(complete)}")
    logger.info(f"  With critical gaps: {len(critical)}")
    if critical:
        logger.warning(
            f"  ATTENTION: {len(critical)} documents have critical audit gaps. "
            f"Review before audit submission."
        )
    logger.info("=" * 60)

    return packages


if __name__ == "__main__":
    # Standalone run — loads records from evidence store
    from ..collector.evidence_store import LocalEvidenceStore
    from pathlib import Path

    cfg = get_config()
    store = LocalEvidenceStore(
        cfg["storage"].get("local_path", "./output/evidence")
    )

    # Load most recent evidence file
    evidence_path = Path(cfg["storage"].get("local_path", "./output/evidence"))
    evidence_files = sorted(evidence_path.glob("evidence_*.ndjson"), reverse=True)

    if not evidence_files:
        logger.error("No evidence files found. Run the collector first.")
        sys.exit(1)

    latest_file = evidence_files[0]
    logger.info(f"Loading evidence from {latest_file}")
    records = store.load_records(str(latest_file))

    packages = run_correlation(records, cfg)
    logger.info(f"Done. {len(packages)} evidence packages ready for narrative generation.")
