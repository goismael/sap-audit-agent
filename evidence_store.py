"""
SAP Audit Agent — Evidence Store
Persists collected evidence records to local JSON files (POC mode)
or Azure Monitor Log Analytics (production mode).
"""

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..common.models import SAPEvidenceRecord

logger = logging.getLogger(__name__)


class LocalEvidenceStore:
    """
    POC evidence store — persists records as NDJSON files.
    One file per company code per collection run.

    For production: replace with AzureMonitorEvidenceStore.
    Storage path is excluded from version control via .gitignore.
    """

    def __init__(self, base_path: str = "./output/evidence"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_records(
        self,
        records: List[SAPEvidenceRecord],
        session_label: str = "",
    ) -> str:
        """
        Save a batch of evidence records to a NDJSON file.

        Args:
            records: List of SAPEvidenceRecord instances
            session_label: Optional label for the collection session

        Returns:
            Path to the saved file
        """
        if not records:
            logger.info("No records to save")
            return ""

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        label = f"_{session_label}" if session_label else ""
        filename = f"evidence_{timestamp}{label}.ndjson"
        filepath = self.base_path / filename

        with open(filepath, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(self._serialize(record)) + "\n")

        logger.info(f"Saved {len(records)} evidence records to {filepath}")
        return str(filepath)

    def load_records(self, filepath: str) -> List[SAPEvidenceRecord]:
        """Load evidence records from a NDJSON file."""
        records = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    records.append(self._deserialize(data))
        return records

    def save_collection_state(self, state: Dict[str, Any]) -> None:
        """Persist collection state for delta runs."""
        state_file = self.base_path / "collection_state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def load_collection_state(self) -> Optional[Dict[str, Any]]:
        """Load persisted collection state."""
        state_file = self.base_path / "collection_state.json"
        if not state_file.exists():
            return None
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_stats(self) -> Dict[str, Any]:
        """Return basic stats about stored evidence."""
        files = list(self.base_path.glob("evidence_*.ndjson"))
        total_records = 0
        for f in files:
            with open(f, "r") as fh:
                total_records += sum(1 for line in fh if line.strip())
        return {
            "evidence_files": len(files),
            "total_records": total_records,
            "storage_path": str(self.base_path),
        }

    @staticmethod
    def _serialize(record: SAPEvidenceRecord) -> Dict[str, Any]:
        """Convert a SAPEvidenceRecord to a JSON-serializable dict."""
        d = record.__dict__.copy()
        d["event_type"] = record.event_type.value
        return d

    @staticmethod
    def _deserialize(data: Dict[str, Any]) -> SAPEvidenceRecord:
        """Reconstruct a SAPEvidenceRecord from a dict."""
        data["event_type"] = EventType(data["event_type"])
        return SAPEvidenceRecord(**data)


# Import here to avoid circular import
from ..common.models import EventType
