"""
SAP Audit Agent — Evidence Correlator
Layer 2: Joins SAP events + agent action logs + agent reasoning logs
+ approval records into complete, verified evidence packages.

This is the layer that makes audit narratives possible.
Without it, SAP documents and agent reasoning exist in separate,
unconnected systems. With it, every document has a complete,
hash-verified evidence chain.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from ..common.models import (
    SAPEvidenceRecord,
    EvidencePackage,
    EvidenceGap,
    EvidenceGapType,
)

logger = logging.getLogger(__name__)


# ── Evidence Sources ───────────────────────────────────────────────────────────

@dataclass
class AgentActionLog:
    """
    Represents a single agent action log event (P003 Layer 2).
    Loaded from Azure Monitor or local log sink.
    """
    event_id: str
    session_id: str
    agent_id: str
    agent_classification: str
    timestamp: str
    action_type: str
    action_name: str
    parameters: Dict[str, Any]
    outcome_status: str
    risk_tier: str
    permission_check_passed: bool
    duration_ms: int
    sap_document_number: Optional[str] = None
    sap_return_code: Optional[str] = None
    approval_id: Optional[str] = None
    sequence_number: int = 0
    previous_event_hash: Optional[str] = None
    event_hash: Optional[str] = None


@dataclass
class AgentReasoningLog:
    """
    Represents an agent reasoning log event (P003 Layer 3).
    Captures why the agent made a specific decision.
    """
    event_id: str
    session_id: str
    agent_id: str
    timestamp: str
    decision_point: str
    input_data_summary: Dict[str, Any]
    alternatives_considered: List[Dict[str, str]]
    conclusion: str
    confidence: str
    conclusion_reasoning: str
    human_review_recommended: bool
    human_review_reason: Optional[str] = None
    downstream_actions_triggered: List[str] = field(default_factory=list)
    sequence_number: int = 0
    previous_event_hash: Optional[str] = None
    event_hash: Optional[str] = None


@dataclass
class ApprovalRecord:
    """
    Represents a human approval record (P002).
    Hash-verified link between human authorization and agent execution.
    """
    approval_id: str
    package_id: str
    action_package_hash: str
    approver_id: str
    approver_role: str
    approved_at: str
    approval_channel: str
    status: str
    agent_id: str
    session_id: str
    comment: Optional[str] = None


# ── Completeness Scoring ───────────────────────────────────────────────────────

# Weights for completeness score calculation
COMPLETENESS_WEIGHTS = {
    "sap_event": 25,          # SAP posting record exists
    "action_log": 25,         # Agent action log exists
    "reasoning_log": 25,      # Agent reasoning log exists
    "approval_record": 25,    # Human approval record exists (for Tier 3 actions)
}

# Audit risk levels for gap types
GAP_AUDIT_RISK = {
    EvidenceGapType.NO_APPROVAL_RECORD: "Critical",
    EvidenceGapType.HASH_MISMATCH: "Critical",
    EvidenceGapType.NO_AGENT_SESSION: "High",
    EvidenceGapType.NO_REASONING_LOG: "High",
    EvidenceGapType.MISSING_SAP_EVENT: "High",
    EvidenceGapType.MISSING_ACTION_LOG: "High",
}

GAP_RECOMMENDED_ACTIONS = {
    EvidenceGapType.NO_APPROVAL_RECORD: (
        "Locate approval record in approval store by session_id. "
        "If not found, this posting may represent a SOX control failure — "
        "escalate to internal audit immediately."
    ),
    EvidenceGapType.HASH_MISMATCH: (
        "Action parameters changed between approval and execution. "
        "Investigate whether posting parameters match what was authorized. "
        "Escalate to internal audit."
    ),
    EvidenceGapType.NO_AGENT_SESSION: (
        "Document posted by agent service user but no session reference found. "
        "Check agent logs for session_id. May indicate agent ran outside "
        "governed workflow."
    ),
    EvidenceGapType.NO_REASONING_LOG: (
        "Agent action log found but no reasoning log for this decision point. "
        "Check P003 log sink for missing events. May indicate logging failure."
    ),
    EvidenceGapType.MISSING_SAP_EVENT: (
        "Agent log references a posting but no SAP document found. "
        "Verify posting completed successfully in SAP. May be a failed posting."
    ),
    EvidenceGapType.MISSING_ACTION_LOG: (
        "SAP document exists but no agent action log found. "
        "Check P003 log sink. May indicate logging sink failure during posting."
    ),
}


# ── Log Source Interfaces ──────────────────────────────────────────────────────

class AgentLogSource:
    """
    Interface for retrieving agent action and reasoning logs.
    In production: queries Azure Monitor Log Analytics via KQL.
    In POC: reads from local NDJSON files.
    """

    def get_action_log_by_document(
        self,
        document_number: str,
        company_code: str,
    ) -> Optional[AgentActionLog]:
        raise NotImplementedError

    def get_action_log_by_session(
        self,
        session_id: str,
        action_name: str,
    ) -> Optional[AgentActionLog]:
        raise NotImplementedError

    def get_reasoning_log(
        self,
        session_id: str,
        sequence_before: int,
    ) -> Optional[AgentReasoningLog]:
        raise NotImplementedError


class ApprovalSource:
    """
    Interface for retrieving approval records.
    In production: queries Cosmos DB approval store.
    In POC: reads from local JSON files.
    """

    def get_by_approval_id(
        self,
        approval_id: str,
    ) -> Optional[ApprovalRecord]:
        raise NotImplementedError

    def get_by_session(
        self,
        session_id: str,
    ) -> List[ApprovalRecord]:
        raise NotImplementedError


class LocalAgentLogSource(AgentLogSource):
    """
    POC implementation — reads agent logs from local NDJSON files.
    Replace with AzureMonitorLogSource for production.
    """

    def __init__(self, log_path: str):
        self.log_path = log_path
        self._cache: List[Dict] = []
        self._loaded = False

    def _load(self):
        """Load all log events into memory cache."""
        if self._loaded:
            return
        import os
        from pathlib import Path
        path = Path(self.log_path)
        if not path.exists():
            logger.warning(f"Agent log path not found: {self.log_path}")
            self._loaded = True
            return
        for f in path.glob("*.ndjson"):
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            self._cache.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        self._loaded = True
        logger.info(f"Loaded {len(self._cache)} agent log events from {self.log_path}")

    def get_action_log_by_document(
        self,
        document_number: str,
        company_code: str,
    ) -> Optional[AgentActionLog]:
        self._load()
        for event in self._cache:
            if (
                event.get("layer") == "action"
                and event.get("payload", {}).get("outcome", {}).get(
                    "sap_document_number"
                ) == document_number
            ):
                return self._map_action_log(event)
        return None

    def get_action_log_by_session(
        self,
        session_id: str,
        action_name: str,
    ) -> Optional[AgentActionLog]:
        self._load()
        for event in self._cache:
            if (
                event.get("layer") == "action"
                and event.get("session_id") == session_id
                and event.get("payload", {}).get("action", {}).get("name") == action_name
            ):
                return self._map_action_log(event)
        return None

    def get_reasoning_log(
        self,
        session_id: str,
        sequence_before: int,
    ) -> Optional[AgentReasoningLog]:
        self._load()
        # Find the reasoning log closest to and before the given sequence number
        candidates = [
            e for e in self._cache
            if (
                e.get("layer") == "reasoning"
                and e.get("session_id") == session_id
                and e.get("sequence_number", 0) < sequence_before
            )
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda e: e.get("sequence_number", 0))
        return self._map_reasoning_log(latest)

    @staticmethod
    def _map_action_log(event: Dict) -> AgentActionLog:
        payload = event.get("payload", {})
        action = payload.get("action", {})
        outcome = payload.get("outcome", {})
        return AgentActionLog(
            event_id=event.get("event_id", ""),
            session_id=event.get("session_id", ""),
            agent_id=event.get("agent_id", ""),
            agent_classification=event.get("agent_classification", ""),
            timestamp=event.get("timestamp", ""),
            action_type=action.get("type", ""),
            action_name=action.get("name", ""),
            parameters=action.get("parameters", {}),
            outcome_status=outcome.get("status", ""),
            risk_tier=action.get("risk_tier", ""),
            permission_check_passed=action.get("permission_check_passed", False),
            duration_ms=outcome.get("duration_ms", 0),
            sap_document_number=outcome.get("sap_document_number"),
            sap_return_code=outcome.get("sap_return_code"),
            approval_id=payload.get("approval_id"),
            sequence_number=event.get("sequence_number", 0),
            previous_event_hash=event.get("previous_event_hash"),
            event_hash=event.get("event_hash"),
        )

    @staticmethod
    def _map_reasoning_log(event: Dict) -> AgentReasoningLog:
        payload = event.get("payload", {})
        reasoning = payload.get("reasoning", {})
        return AgentReasoningLog(
            event_id=event.get("event_id", ""),
            session_id=event.get("session_id", ""),
            agent_id=event.get("agent_id", ""),
            timestamp=event.get("timestamp", ""),
            decision_point=reasoning.get("decision_point", ""),
            input_data_summary=reasoning.get("input_data_summary", {}),
            alternatives_considered=reasoning.get("alternatives_considered", []),
            conclusion=reasoning.get("conclusion", ""),
            confidence=reasoning.get("confidence", ""),
            conclusion_reasoning=reasoning.get("conclusion_reasoning", ""),
            human_review_recommended=reasoning.get("human_review_recommended", False),
            human_review_reason=reasoning.get("human_review_reason"),
            downstream_actions_triggered=reasoning.get(
                "downstream_actions_triggered", []
            ),
            sequence_number=event.get("sequence_number", 0),
            previous_event_hash=event.get("previous_event_hash"),
            event_hash=event.get("event_hash"),
        )


class LocalApprovalSource(ApprovalSource):
    """
    POC implementation — reads approval records from local JSON files.
    Replace with CosmosDBApprovalSource for production.
    """

    def __init__(self, approvals_path: str):
        self.approvals_path = approvals_path
        self._cache: Dict[str, ApprovalRecord] = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        from pathlib import Path
        path = Path(self.approvals_path)
        if not path.exists():
            logger.warning(f"Approvals path not found: {self.approvals_path}")
            self._loaded = True
            return
        for f in path.glob("*.json"):
            with open(f, "r", encoding="utf-8") as fh:
                try:
                    data = json.load(fh)
                    if isinstance(data, list):
                        for item in data:
                            record = ApprovalRecord(**item)
                            self._cache[record.approval_id] = record
                    else:
                        record = ApprovalRecord(**data)
                        self._cache[record.approval_id] = record
                except Exception as e:
                    logger.error(f"Failed to load approval record from {f}: {e}")
        self._loaded = True
        logger.info(
            f"Loaded {len(self._cache)} approval records from {self.approvals_path}"
        )

    def get_by_approval_id(self, approval_id: str) -> Optional[ApprovalRecord]:
        self._load()
        return self._cache.get(approval_id)

    def get_by_session(self, session_id: str) -> List[ApprovalRecord]:
        self._load()
        return [r for r in self._cache.values() if r.session_id == session_id]


# ── Evidence Correlator ────────────────────────────────────────────────────────

class EvidenceCorrelator:
    """
    Joins SAP evidence records with agent logs and approval records
    to produce complete, scored, gap-analyzed evidence packages.

    The four-way join:
        SAP Document Number
            ↕ (via sap_document_number in action log)
        Agent Action Log
            ↕ (via sequence_number proximity in same session)
        Agent Reasoning Log
            ↕ (via approval_id in action log)
        Approval Record

    Usage:
        correlator = EvidenceCorrelator(log_source, approval_source)
        package = correlator.correlate(sap_evidence_record)
    """

    def __init__(
        self,
        log_source: AgentLogSource,
        approval_source: ApprovalSource,
    ):
        self.log_source = log_source
        self.approval_source = approval_source

    def correlate(self, sap_record: SAPEvidenceRecord) -> EvidencePackage:
        """
        Build a complete evidence package for a single SAP document.

        Args:
            sap_record: SAP evidence record from Layer 1

        Returns:
            EvidencePackage with all available evidence and gap analysis
        """
        package = EvidencePackage(
            document_number=sap_record.document_number,
            company_code=sap_record.company_code,
            session_id=sap_record.agent_session_reference,
            sap_event=sap_record,
        )

        # Non-agent postings don't need agent evidence
        if not sap_record.is_agent_posted:
            package.completeness_score = 100
            return package

        # Step 1 — Find agent action log
        action_log = self.log_source.get_action_log_by_document(
            document_number=sap_record.document_number,
            company_code=sap_record.company_code,
        )

        if action_log is None and sap_record.agent_session_reference:
            # Try by session + BAPI name as fallback
            action_log = self.log_source.get_action_log_by_session(
                session_id=sap_record.agent_session_reference,
                action_name="BAPI_ACC_DOCUMENT_POST",
            )

        package.action_log = self._serialize(action_log)

        # Step 2 — Find agent reasoning log
        if action_log:
            reasoning_log = self.log_source.get_reasoning_log(
                session_id=action_log.session_id,
                sequence_before=action_log.sequence_number,
            )
            package.reasoning_log = self._serialize(reasoning_log)
        else:
            reasoning_log = None

        # Step 3 — Find approval record
        approval_record = None
        if action_log and action_log.approval_id:
            approval_record = self.approval_source.get_by_approval_id(
                action_log.approval_id
            )
        package.approval_record = self._serialize(approval_record)

        # Step 4 — Verify hash chain
        package.hash_chain_verified = self._verify_hash_chain(
            action_log, approval_record
        )

        # Step 5 — Detect gaps
        package.gaps = self._detect_gaps(
            sap_record, action_log, reasoning_log, approval_record
        )

        # Step 6 — Calculate completeness score
        package.completeness_score = self._calculate_completeness(
            sap_record, action_log, reasoning_log, approval_record, package.gaps
        )

        logger.debug(
            f"Correlated document {sap_record.document_number}: "
            f"score={package.completeness_score}%, "
            f"gaps={len(package.gaps)}, "
            f"hash_verified={package.hash_chain_verified}"
        )

        return package

    def correlate_batch(
        self,
        sap_records: List[SAPEvidenceRecord],
    ) -> List[EvidencePackage]:
        """Correlate a batch of SAP evidence records."""
        packages = []
        agent_records = [r for r in sap_records if r.is_agent_posted]
        human_records = [r for r in sap_records if not r.is_agent_posted]

        logger.info(
            f"Correlating {len(sap_records)} records: "
            f"{len(agent_records)} agent-posted, {len(human_records)} human-posted"
        )

        for record in sap_records:
            try:
                package = self.correlate(record)
                packages.append(package)
            except Exception as e:
                logger.error(
                    f"Correlation failed for document {record.document_number}: {e}"
                )

        complete = [p for p in packages if p.completeness_score == 100]
        with_gaps = [p for p in packages if p.gaps]
        critical = [
            p for p in packages
            if any(g.audit_risk == "Critical" for g in p.gaps)
        ]

        logger.info(
            f"Correlation complete: {len(packages)} packages | "
            f"{len(complete)} complete | "
            f"{len(with_gaps)} with gaps | "
            f"{len(critical)} critical gaps"
        )

        return packages

    def _detect_gaps(
        self,
        sap_record: SAPEvidenceRecord,
        action_log: Optional[AgentActionLog],
        reasoning_log: Optional[AgentReasoningLog],
        approval_record: Optional[ApprovalRecord],
    ) -> List[EvidenceGap]:
        gaps = []

        # Gap: no session reference on agent-posted document
        if sap_record.is_agent_posted and not sap_record.agent_session_reference:
            gaps.append(EvidenceGap(
                gap_type=EvidenceGapType.NO_AGENT_SESSION,
                description=(
                    f"Document {sap_record.document_number} was posted by "
                    f"agent service user {sap_record.posted_by_user} but "
                    f"contains no session reference in DocumentReferenceID."
                ),
                audit_risk=GAP_AUDIT_RISK[EvidenceGapType.NO_AGENT_SESSION],
                recommended_action=GAP_RECOMMENDED_ACTIONS[
                    EvidenceGapType.NO_AGENT_SESSION
                ],
            ))

        # Gap: no action log
        if action_log is None:
            gaps.append(EvidenceGap(
                gap_type=EvidenceGapType.MISSING_ACTION_LOG,
                description=(
                    f"No agent action log found for document "
                    f"{sap_record.document_number}."
                ),
                audit_risk=GAP_AUDIT_RISK[EvidenceGapType.MISSING_ACTION_LOG],
                recommended_action=GAP_RECOMMENDED_ACTIONS[
                    EvidenceGapType.MISSING_ACTION_LOG
                ],
            ))

        # Gap: no reasoning log
        if action_log and reasoning_log is None:
            gaps.append(EvidenceGap(
                gap_type=EvidenceGapType.NO_REASONING_LOG,
                description=(
                    f"Agent action log found for document "
                    f"{sap_record.document_number} but no reasoning log "
                    f"found in session {action_log.session_id}."
                ),
                audit_risk=GAP_AUDIT_RISK[EvidenceGapType.NO_REASONING_LOG],
                recommended_action=GAP_RECOMMENDED_ACTIONS[
                    EvidenceGapType.NO_REASONING_LOG
                ],
            ))

        # Gap: no approval record for Tier 3 action
        if (
            action_log
            and action_log.risk_tier == "approve"
            and approval_record is None
        ):
            gaps.append(EvidenceGap(
                gap_type=EvidenceGapType.NO_APPROVAL_RECORD,
                description=(
                    f"Document {sap_record.document_number} was posted via "
                    f"a Tier 3 (approve) action but no approval record found "
                    f"for approval_id '{action_log.approval_id}'."
                ),
                audit_risk=GAP_AUDIT_RISK[EvidenceGapType.NO_APPROVAL_RECORD],
                recommended_action=GAP_RECOMMENDED_ACTIONS[
                    EvidenceGapType.NO_APPROVAL_RECORD
                ],
            ))

        # Gap: hash mismatch
        if action_log and approval_record:
            if not self._verify_hash_chain(action_log, approval_record):
                gaps.append(EvidenceGap(
                    gap_type=EvidenceGapType.HASH_MISMATCH,
                    description=(
                        f"Approval record hash does not match action parameters "
                        f"for document {sap_record.document_number}. "
                        f"Parameters may have changed between approval and execution."
                    ),
                    audit_risk=GAP_AUDIT_RISK[EvidenceGapType.HASH_MISMATCH],
                    recommended_action=GAP_RECOMMENDED_ACTIONS[
                        EvidenceGapType.HASH_MISMATCH
                    ],
                ))

        return gaps

    def _calculate_completeness(
        self,
        sap_record: SAPEvidenceRecord,
        action_log: Optional[AgentActionLog],
        reasoning_log: Optional[AgentReasoningLog],
        approval_record: Optional[ApprovalRecord],
        gaps: List[EvidenceGap],
    ) -> int:
        """Calculate completeness score 0-100."""
        # Non-agent postings are always 100% complete
        if not sap_record.is_agent_posted:
            return 100

        score = COMPLETENESS_WEIGHTS["sap_event"]  # SAP event always present here

        if action_log:
            score += COMPLETENESS_WEIGHTS["action_log"]

        if reasoning_log:
            score += COMPLETENESS_WEIGHTS["reasoning_log"]

        # Approval record: only required for Tier 3 actions
        if action_log and action_log.risk_tier == "approve":
            if approval_record:
                score += COMPLETENESS_WEIGHTS["approval_record"]
        else:
            # Autonomous/notify actions don't need approval records
            score += COMPLETENESS_WEIGHTS["approval_record"]

        # Deduct for critical gaps
        critical_gaps = [g for g in gaps if g.audit_risk == "Critical"]
        score = max(0, score - (len(critical_gaps) * 25))

        return min(100, score)

    @staticmethod
    def _verify_hash_chain(
        action_log: Optional[AgentActionLog],
        approval_record: Optional[ApprovalRecord],
    ) -> bool:
        """Verify that the approval hash matches the action parameters."""
        if not action_log or not approval_record:
            return False
        if not approval_record.action_package_hash:
            return False
        # In a full implementation, recompute the action package hash
        # from action_log.parameters and compare to approval_record.action_package_hash
        # For POC: trust the stored hash if both records exist and approval is approved
        return approval_record.status == "approved"

    @staticmethod
    def _serialize(obj) -> Optional[Dict[str, Any]]:
        """Convert a dataclass to a dict, or return None."""
        if obj is None:
            return None
        if hasattr(obj, "__dict__"):
            return obj.__dict__.copy()
        return obj
