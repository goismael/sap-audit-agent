"""
SAP Audit Agent — Narrative Engine Main
Entry point for Layer 3: Audit Narrative Generation.

Run standalone:
    python -m src.narrative.main

Or import:
    from src.narrative.main import run_narrative_generation
    narratives = run_narrative_generation(packages, config)
"""

import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from ..common.config import get_config
from ..common.models import EvidencePackage
from .narrative_engine import NarrativeEngine, AuditNarrative

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run_narrative_generation(
    packages: List[EvidencePackage],
    config: Optional[dict] = None,
    skip_human_postings: bool = False,
) -> List[AuditNarrative]:
    """
    Generate audit narratives for a batch of evidence packages.

    Args:
        packages: Correlated evidence packages from Layer 2
        config: Optional config dict
        skip_human_postings: Skip human-posted documents to reduce API calls

    Returns:
        List of AuditNarrative instances
    """
    if config is None:
        config = get_config()

    logger.info("=" * 60)
    logger.info("SAP Audit Agent — Narrative Generation Starting")
    logger.info(f"Packages to process: {len(packages)}")
    logger.info(f"Model: {config['llm']['model']}")
    logger.info("=" * 60)

    # Initialize narrative engine
    engine = NarrativeEngine(config["llm"])

    # Generate narratives
    narratives = engine.generate_batch(
        packages,
        skip_human_postings=skip_human_postings,
    )

    # Save narratives to output
    storage_config = config["storage"]
    output_path = Path(storage_config.get("local_path", "./output/evidence"))
    narratives_path = output_path / "narratives"
    narratives_path.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_file = narratives_path / f"narratives_{timestamp}.jsonl"

    with open(output_file, "w", encoding="utf-8") as f:
        for narrative in narratives:
            record = {
                "document_number": narrative.document_number,
                "company_code": narrative.company_code,
                "narrative_text": narrative.narrative_text,
                "completeness_score": narrative.completeness_score,
                "hash_verified": narrative.hash_verified,
                "audit_ready": narrative.audit_ready,
                "has_critical_gaps": narrative.has_critical_gaps,
                "generated_at": narrative.generated_at,
                "model_used": narrative.model_used,
                "generation_time_ms": narrative.generation_time_ms,
                "gaps": [
                    {
                        "gap_type": g.gap_type.value,
                        "audit_risk": g.audit_risk,
                        "description": g.description,
                        "recommended_action": g.recommended_action,
                    }
                    for g in narrative.gaps
                ],
            }
            f.write(json.dumps(record) + "\n")

    logger.info(f"Narratives saved: {output_file}")

    # Print summary
    audit_ready = [n for n in narratives if n.audit_ready]
    critical = [n for n in narratives if n.has_critical_gaps]

    logger.info("=" * 60)
    logger.info(f"Narrative generation complete")
    logger.info(f"  Total generated:      {len(narratives)}")
    logger.info(f"  Audit ready:          {len(audit_ready)}")
    logger.info(f"  With critical gaps:   {len(critical)}")
    if critical:
        logger.warning(
            f"  ATTENTION: {len(critical)} documents require manual review "
            f"before audit submission."
        )
    logger.info("=" * 60)

    return narratives


if __name__ == "__main__":
    # Standalone — loads packages from correlator output
    # For full end-to-end, run pipeline.py instead
    logger.error(
        "Run src.pipeline instead for end-to-end execution. "
        "This module requires evidence packages from Layer 2."
    )
    sys.exit(1)
