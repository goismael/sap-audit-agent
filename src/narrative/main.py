import json, logging, sys
from pathlib import Path
from typing import List, Optional
from .narrative_engine import NarrativeEngine, AuditNarrative

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def run_narrative_generation(packages, config=None, skip_human_postings=False):
    from ..common.config import get_config
    if config is None:
        config = get_config()
    engine = NarrativeEngine(config["llm"])
    narratives = engine.generate_batch(packages, skip_human_postings=skip_human_postings)
    output_path = Path(config["storage"].get("local_path", "./output/evidence")) / "narratives"
    output_path.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    with open(output_path / f"narratives_{ts}.jsonl", "w") as f:
        for n in narratives:
            f.write(json.dumps({"document_number": n.document_number, "narrative_text": n.narrative_text, "completeness_score": n.completeness_score, "audit_ready": n.audit_ready}) + "\n")
    return narratives
