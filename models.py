"""
SAP Audit Agent — Common Data Models
Shared across all layers: collector, correlator, narrative, reporter.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from enum import Enum
import uuid


class EventType(Enum):
    JOURNAL_ENTRY_POSTING = "journal_entry_posting"
    DOCUMENT_REVERSAL = "document_reversal"
    FX_REVALUATION = "fx_revaluation"
    PERIOD_CLOSE = "period_close"
    MASTER_DATA_CHANGE = "master_data_change"
    AUTHORIZATION_CHANGE = "authorization_change"
    PAYMENT_RUN = "payment_run"
    BALANCE_CARRYFORWARD = "balance_carryforward"


class EvidenceGapType(Enum):
    NO_AGENT_SESSION = "no_agent_session_reference"
    NO_REASONING_LOG = "no_reasoning_log"
    NO_APPROVAL_RECORD = "no_approval_record"
    HASH_MISMATCH = "hash_mismatch"
    MISSING_SAP_EVENT = "missing_sap_event"
    MISSING_ACTION_LOG = "missing_action_log"


@dataclass
class SAPEvidenceRecord:
    """
    A single financial event collected from SAP S/4HANA.
    Produced by Layer 1 — SAP Data Collector.
    """
    evidence_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # SAP event fields
    source_service: str = ""               # OData service that provided this record
    event_type: EventType = EventType.JOURNAL_ENTRY_POSTING
    company_code: str = ""
    fiscal_year: str = ""
    period: str = ""
    document_number: str = ""
    posting_date: str = ""
    posting_time: str = ""
    document_type: str = ""
    posted_by_user: str = ""
    amount: float = 0.0
    currency: str = ""
    gl_account: str = ""
    cost_center: Optional[str] = None
    profit_center: Optional[str] = None
    reference: str = ""
    document_header_text: str = ""

    # Agent governance fields
    is_agent_posted: bool = False          # True if posted_by_user is a known agent service user
    agent_session_reference: Optional[str] = None  # session_id from P003 logs

    # Raw SAP response for full traceability
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceGap:
    """Represents a missing or failed evidence link."""
    gap_type: EvidenceGapType
    description: str
    audit_risk: str                        # "Critical", "High", "Medium"
    recommended_action: str


@dataclass
class EvidencePackage:
    """
    Complete evidence package for a single SAP document.
    Produced by Layer 2 — Evidence Correlator.
    """
    package_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    assembled_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Core identifiers
    document_number: str = ""
    company_code: str = ""
    session_id: Optional[str] = None

    # Evidence layers
    sap_event: Optional[SAPEvidenceRecord] = None
    action_log: Optional[Dict[str, Any]] = None     # From P003 Layer 2
    reasoning_log: Optional[Dict[str, Any]] = None  # From P003 Layer 3
    approval_record: Optional[Dict[str, Any]] = None # From P002

    # Verification
    hash_chain_verified: bool = False
    completeness_score: int = 0            # 0-100
    gaps: List[EvidenceGap] = field(default_factory=list)

    # Narrative (populated by Layer 3)
    audit_narrative: Optional[str] = None


@dataclass
class CollectionState:
    """
    Tracks the state of a collection run for delta queries.
    Persisted between runs so we only collect what changed.
    """
    last_run_at: str = ""
    company_code: str = ""
    service: str = ""
    last_document_date: Optional[str] = None
    documents_collected: int = 0
    errors: List[str] = field(default_factory=list)
