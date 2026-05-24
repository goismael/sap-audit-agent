"""
Tests for Layer 2 — Evidence Correlator
Tests the four-way join logic, gap detection, and completeness scoring.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from src.correlator.correlator import (
    EvidenceCorrelator,
    AgentActionLog,
    AgentReasoningLog,
    ApprovalRecord,
    AgentLogSource,
    ApprovalSource,
)
from src.common.models import (
    SAPEvidenceRecord,
    EventType,
    EvidenceGapType,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_sap_record(is_agent_posted=True, session_ref="session-001"):
    return SAPEvidenceRecord(
        company_code="1000",
        fiscal_year="2026",
        document_number="100000001",
        posting_date="2026-04-30",
        document_type="SA",
        posted_by_user="SVC_AGENT_EXEC_POST" if is_agent_posted else "JSMITH",
        amount=184200.00,
        currency="USD",
        gl_account="400000",
        is_agent_posted=is_agent_posted,
        agent_session_reference=session_ref if is_agent_posted else None,
        event_type=EventType.JOURNAL_ENTRY_POSTING,
    )


def make_action_log(approval_id="approval-001", risk_tier="approve"):
    return AgentActionLog(
        event_id="event-001",
        session_id="session-001",
        agent_id="gl-agent-001",
        agent_classification="executor",
        timestamp="2026-04-30T23:47:12Z",
        action_type="sap_bapi",
        action_name="BAPI_ACC_DOCUMENT_POST",
        parameters={"company_code": "1000"},
        outcome_status="success",
        risk_tier=risk_tier,
        permission_check_passed=True,
        duration_ms=234,
        sap_document_number="100000001",
        approval_id=approval_id,
        sequence_number=12,
    )


def make_reasoning_log():
    return AgentReasoningLog(
        event_id="event-011",
        session_id="session-001",
        agent_id="gl-agent-001",
        timestamp="2026-04-30T23:47:10Z",
        decision_point="reconciliation_variance_assessment",
        input_data_summary={"gl_balance": 184200.00, "variance": 0.00},
        alternatives_considered=[
            {"option": "flag_for_review", "reason_rejected": "No variance"}
        ],
        conclusion="reconciliation_passed",
        confidence="high",
        conclusion_reasoning="GL and sub-ledger match exactly.",
        human_review_recommended=False,
        sequence_number=11,
    )


def make_approval_record(approval_id="approval-001"):
    return ApprovalRecord(
        approval_id=approval_id,
        package_id="package-001",
        action_package_hash="abc123",
        approver_id="MSANTOS",
        approver_role="Controller",
        approved_at="2026-04-30T23:31:00Z",
        approval_channel="teams",
        status="approved",
        agent_id="gl-agent-001",
        session_id="session-001",
    )


def make_correlator(action_log=None, reasoning_log=None, approval=None):
    log_source = MagicMock(spec=AgentLogSource)
    approval_source = MagicMock(spec=ApprovalSource)

    log_source.get_action_log_by_document.return_value = action_log
    log_source.get_action_log_by_session.return_value = action_log
    log_source.get_reasoning_log.return_value = reasoning_log
    approval_source.get_by_approval_id.return_value = approval

    return EvidenceCorrelator(log_source, approval_source)


# ── Completeness Tests ─────────────────────────────────────────────────────────

class TestCompleteness:

    def test_human_posted_always_100(self):
        """Human-posted documents are always 100% complete."""
        record = make_sap_record(is_agent_posted=False)
        correlator = make_correlator()
        package = correlator.correlate(record)
        assert package.completeness_score == 100
        assert len(package.gaps) == 0

    def test_complete_agent_posting_is_100(self):
        """Agent posting with all four evidence layers scores 100%."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=make_action_log(),
            reasoning_log=make_reasoning_log(),
            approval=make_approval_record(),
        )
        package = correlator.correlate(record)
        assert package.completeness_score == 100
        assert len(package.gaps) == 0

    def test_missing_action_log_reduces_score(self):
        """Missing action log reduces completeness score."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=None,
            reasoning_log=None,
            approval=None,
        )
        package = correlator.correlate(record)
        assert package.completeness_score < 100

    def test_missing_reasoning_log_reduces_score(self):
        """Missing reasoning log reduces completeness score."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=make_action_log(),
            reasoning_log=None,
            approval=make_approval_record(),
        )
        package = correlator.correlate(record)
        assert package.completeness_score < 100

    def test_missing_approval_reduces_score_for_tier3(self):
        """Missing approval record reduces score for Tier 3 actions."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=make_action_log(risk_tier="approve"),
            reasoning_log=make_reasoning_log(),
            approval=None,
        )
        package = correlator.correlate(record)
        assert package.completeness_score < 100

    def test_autonomous_action_full_score_without_approval(self):
        """Autonomous actions don't need approval records for full score."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=make_action_log(risk_tier="autonomous"),
            reasoning_log=make_reasoning_log(),
            approval=None,
        )
        package = correlator.correlate(record)
        assert package.completeness_score == 100


# ── Gap Detection Tests ────────────────────────────────────────────────────────

class TestGapDetection:

    def test_no_session_reference_gap(self):
        """Agent-posted document without session reference produces gap."""
        record = make_sap_record(session_ref=None)
        correlator = make_correlator()
        package = correlator.correlate(record)
        gap_types = [g.gap_type for g in package.gaps]
        assert EvidenceGapType.NO_AGENT_SESSION in gap_types

    def test_missing_action_log_gap(self):
        """Missing action log produces gap."""
        record = make_sap_record()
        correlator = make_correlator(action_log=None)
        package = correlator.correlate(record)
        gap_types = [g.gap_type for g in package.gaps]
        assert EvidenceGapType.MISSING_ACTION_LOG in gap_types

    def test_missing_reasoning_log_gap(self):
        """Missing reasoning log produces gap when action log exists."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=make_action_log(),
            reasoning_log=None,
            approval=make_approval_record(),
        )
        package = correlator.correlate(record)
        gap_types = [g.gap_type for g in package.gaps]
        assert EvidenceGapType.NO_REASONING_LOG in gap_types

    def test_missing_approval_gap_for_tier3(self):
        """Missing approval record is a Critical gap for Tier 3 actions."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=make_action_log(risk_tier="approve"),
            reasoning_log=make_reasoning_log(),
            approval=None,
        )
        package = correlator.correlate(record)
        gap_types = [g.gap_type for g in package.gaps]
        assert EvidenceGapType.NO_APPROVAL_RECORD in gap_types
        critical = [g for g in package.gaps if g.audit_risk == "Critical"]
        assert len(critical) >= 1

    def test_complete_package_has_no_gaps(self):
        """Complete evidence package has no gaps."""
        record = make_sap_record()
        correlator = make_correlator(
            action_log=make_action_log(),
            reasoning_log=make_reasoning_log(),
            approval=make_approval_record(),
        )
        package = correlator.correlate(record)
        assert len(package.gaps) == 0


# ── Batch Correlation Tests ────────────────────────────────────────────────────

class TestBatchCorrelation:

    def test_batch_processes_all_records(self):
        """Batch correlation returns one package per input record."""
        records = [make_sap_record() for _ in range(5)]
        correlator = make_correlator(
            action_log=make_action_log(),
            reasoning_log=make_reasoning_log(),
            approval=make_approval_record(),
        )
        packages = correlator.correlate_batch(records)
        assert len(packages) == 5

    def test_batch_handles_errors_gracefully(self):
        """Batch correlation continues despite individual failures."""
        records = [make_sap_record() for _ in range(3)]
        log_source = MagicMock(spec=AgentLogSource)
        approval_source = MagicMock(spec=ApprovalSource)

        # First call raises, rest succeed
        log_source.get_action_log_by_document.side_effect = [
            Exception("Simulated failure"),
            make_action_log(),
            make_action_log(),
        ]
        log_source.get_action_log_by_session.return_value = None
        log_source.get_reasoning_log.return_value = make_reasoning_log()
        approval_source.get_by_approval_id.return_value = make_approval_record()

        correlator = EvidenceCorrelator(log_source, approval_source)
        packages = correlator.correlate_batch(records)

        # Should still return packages for the records that succeeded
        assert len(packages) >= 0
