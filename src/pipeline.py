"""
SAP Audit Agent — End-to-End Pipeline
Wires Layer 1 (Collector) + Layer 2 (Correlator) + Layer 3 (Narrative)
into a single runnable pipeline.

For testing without a live SAP system, use --synthetic flag:
    python -m src.pipeline --synthetic

For live SAP collection:
    python -m src.pipeline
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .common.config import get_config
from .common.models import SAPEvidenceRecord, EventType, EvidencePackage
from .correlator.correlator import (
    EvidenceCorrelator,
    LocalAgentLogSource,
    LocalApprovalSource,
    AgentActionLog,
    AgentReasoningLog,
    ApprovalRecord,
)
from .narrative.main import run_narrative_generation
from .narrative.narrative_engine import AuditNarrative

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Synthetic Data for Testing ─────────────────────────────────────────────────

def make_synthetic_data() -> tuple:
    """
    Generate synthetic SAP records, agent logs, and approval records
    for end-to-end testing without a live SAP system.

    Returns three items:
    - List of SAPEvidenceRecord
    - List of agent log dicts (P003 format)
    - List of approval record dicts (P002 format)
    """

    # Synthetic SAP evidence records
    sap_records = [
        # Record 1: Agent-posted accrual — complete evidence
        SAPEvidenceRecord(
            source_service="API_JOURNALENTRYITEMBASIC_SRV",
            event_type=EventType.JOURNAL_ENTRY_POSTING,
            company_code="1000",
            fiscal_year="2026",
            period="04",
            document_number="100000001",
            posting_date="2026-04-30",
            posting_time="23:47:12",
            document_type="SA",
            posted_by_user="SVC_AGENT_EXEC_POST",
            amount=184200.00,
            currency="USD",
            gl_account="400000",
            cost_center="CC1000",
            reference="close-cycle-2026-04-Q1-001",
            document_header_text="Period close accrual — Q1 2026",
            is_agent_posted=True,
            agent_session_reference="close-cycle-2026-04-Q1-001",
        ),
        # Record 2: Agent-posted — missing approval record (critical gap)
        SAPEvidenceRecord(
            source_service="API_JOURNALENTRYITEMBASIC_SRV",
            event_type=EventType.JOURNAL_ENTRY_POSTING,
            company_code="2000",
            fiscal_year="2026",
            period="04",
            document_number="200000047",
            posting_date="2026-04-30",
            posting_time="23:51:00",
            document_type="SA",
            posted_by_user="SVC_AGENT_EXEC_POST",
            amount=52000.00,
            currency="EUR",
            gl_account="410000",
            reference="close-cycle-2026-04-Q1-001",
            document_header_text="Intercompany accrual",
            is_agent_posted=True,
            agent_session_reference="close-cycle-2026-04-Q1-001",
        ),
        # Record 3: Human-posted
        SAPEvidenceRecord(
            source_service="API_JOURNALENTRYITEMBASIC_SRV",
            event_type=EventType.JOURNAL_ENTRY_POSTING,
            company_code="1000",
            fiscal_year="2026",
            period="04",
            document_number="100000002",
            posting_date="2026-04-30",
            posting_time="16:22:00",
            document_type="SA",
            posted_by_user="MSANTOS",
            amount=15000.00,
            currency="USD",
            gl_account="500000",
            reference="",
            document_header_text="Manual adjustment — controller approved",
            is_agent_posted=False,
        ),
    ]

    # Synthetic agent action logs (P003 Layer 2 format)
    agent_logs = [
        {
            "event_id": "evt-action-001",
            "session_id": "close-cycle-2026-04-Q1-001",
            "agent_id": "gl-reconciliation-agent-001",
            "agent_classification": "executor",
            "timestamp": "2026-04-30T23:47:12Z",
            "sequence_number": 12,
            "previous_event_hash": "abc123",
            "event_hash": "def456",
            "layer": "action",
            "payload": {
                "action": {
                    "type": "sap_bapi",
                    "name": "BAPI_ACC_DOCUMENT_POST",
                    "parameters": {"company_code": "1000", "fiscal_year": "2026"},
                    "risk_tier": "approve",
                    "permission_check_passed": True,
                },
                "outcome": {
                    "status": "success",
                    "duration_ms": 234,
                    "sap_document_number": "100000001",
                    "sap_return_code": "S",
                    "error_code": None,
                    "error_message": None,
                },
                "approval_id": "approval-001",
            },
        },
        {
            "event_id": "evt-action-002",
            "session_id": "close-cycle-2026-04-Q1-001",
            "agent_id": "gl-reconciliation-agent-001",
            "agent_classification": "executor",
            "timestamp": "2026-04-30T23:51:00Z",
            "sequence_number": 18,
            "previous_event_hash": "def456",
            "event_hash": "ghi789",
            "layer": "action",
            "payload": {
                "action": {
                    "type": "sap_bapi",
                    "name": "BAPI_ACC_DOCUMENT_POST",
                    "parameters": {"company_code": "2000", "fiscal_year": "2026"},
                    "risk_tier": "approve",
                    "permission_check_passed": True,
                },
                "outcome": {
                    "status": "success",
                    "duration_ms": 198,
                    "sap_document_number": "200000047",
                    "sap_return_code": "S",
                    "error_code": None,
                    "error_message": None,
                },
                "approval_id": "approval-missing",  # No matching approval record
            },
        },
    ]

    # Synthetic reasoning logs (P003 Layer 3 format)
    reasoning_logs = [
        {
            "event_id": "evt-reasoning-001",
            "session_id": "close-cycle-2026-04-Q1-001",
            "agent_id": "gl-reconciliation-agent-001",
            "timestamp": "2026-04-30T23:47:10Z",
            "sequence_number": 11,
            "previous_event_hash": "xyz789",
            "event_hash": "abc123",
            "layer": "reasoning",
            "payload": {
                "reasoning": {
                    "decision_point": "period_close_accrual_assessment",
                    "input_data_summary": {
                        "gl_balance": 184200.00,
                        "sub_ledger_balance": 184200.00,
                        "variance": 0.00,
                        "currency": "USD",
                        "company_code": "1000",
                        "period": "2026-04",
                    },
                    "alternatives_considered": [
                        {
                            "option": "flag_for_manual_review",
                            "reason_rejected": "Variance is zero — no discrepancy",
                        },
                        {
                            "option": "escalate_to_controller",
                            "reason_rejected": "Balance within tolerance; escalation criteria not met",
                        },
                    ],
                    "conclusion": "post_accrual",
                    "confidence": "high",
                    "conclusion_reasoning": (
                        "GL balance and sub-ledger balance match exactly at USD 184,200.00. "
                        "No FX adjustment required. Period-end rate matches transaction-date "
                        "rate for all line items. All reconciliation conditions met."
                    ),
                    "human_review_recommended": False,
                    "human_review_reason": None,
                    "downstream_actions_triggered": ["sign_off_agent_notify"],
                }
            },
        },
    ]

    # Synthetic approval records (P002 format)
    # Note: only approval-001 exists — approval-missing intentionally absent
    approval_records = [
        {
            "approval_id": "approval-001",
            "package_id": "package-001",
            "action_package_hash": "sha256_hash_of_action_params",
            "approver_id": "MSANTOS",
            "approver_role": "Controller",
            "approved_at": "2026-04-30T23:31:00Z",
            "approval_channel": "teams",
            "status": "approved",
            "agent_id": "gl-reconciliation-agent-001",
            "session_id": "close-cycle-2026-04-Q1-001",
            "comment": "Reconciliation confirmed. Proceed with posting.",
        }
    ]

    return sap_records, agent_logs + reasoning_logs, approval_records


def save_synthetic_logs(
    logs: list,
    approvals: list,
    base_path: Path,
) -> tuple:
    """Save synthetic logs and approvals to local files for the correlator."""
    logs_path = base_path / "agent_logs"
    approvals_path = base_path / "approvals"
    logs_path.mkdir(parents=True, exist_ok=True)
    approvals_path.mkdir(parents=True, exist_ok=True)

    log_file = logs_path / "synthetic_logs.ndjson"
    with open(log_file, "w", encoding="utf-8") as f:
        for log in logs:
            f.write(json.dumps(log) + "\n")

    approval_file = approvals_path / "synthetic_approvals.json"
    with open(approval_file, "w", encoding="utf-8") as f:
        json.dump(approvals, f, indent=2)

    return str(logs_path), str(approvals_path)


def print_narratives(narratives: List[AuditNarrative]) -> None:
    """Print narratives to console in a readable format."""
    print("\n" + "=" * 70)
    print("AUDIT NARRATIVES")
    print("=" * 70)

    for i, narrative in enumerate(narratives, 1):
        print(f"\n{'─' * 70}")
        print(f"Document {i}: {narrative.document_number} | "
              f"Company Code: {narrative.company_code}")
        print(f"Completeness: {narrative.completeness_score}% | "
              f"Hash Verified: {narrative.hash_verified} | "
              f"Audit Ready: {narrative.audit_ready}")
        print(f"{'─' * 70}")
        print(narrative.narrative_text)

        if narrative.gaps:
            print(f"\n⚠ GAPS DETECTED ({len(narrative.gaps)}):")
            for gap in narrative.gaps:
                print(f"  [{gap.audit_risk}] {gap.gap_type.value}")
                print(f"  → {gap.recommended_action}")

    print("\n" + "=" * 70)
    audit_ready = [n for n in narratives if n.audit_ready]
    critical = [n for n in narratives if n.has_critical_gaps]
    print(f"SUMMARY: {len(narratives)} narratives | "
          f"{len(audit_ready)} audit-ready | "
          f"{len(critical)} with critical gaps")
    print("=" * 70 + "\n")


def run_pipeline(synthetic: bool = False) -> List[AuditNarrative]:
    """
    Run the full end-to-end pipeline.

    Args:
        synthetic: If True, use synthetic test data instead of live SAP

    Returns:
        List of generated AuditNarrative instances
    """
    config = get_config()
    storage_config = config["storage"]
    base_path = Path(storage_config.get("local_path", "./output/evidence"))

    logger.info("=" * 60)
    logger.info("SAP Audit Agent — Full Pipeline")
    logger.info(f"Mode: {'SYNTHETIC TEST DATA' if synthetic else 'LIVE SAP'}")
    logger.info("=" * 60)

    if synthetic:
        # Layer 1 substitute — synthetic SAP records
        logger.info("Generating synthetic test data...")
        sap_records, agent_logs, approval_records = make_synthetic_data()
        logs_path, approvals_path = save_synthetic_logs(
            agent_logs, approval_records, base_path
        )
        logger.info(
            f"Synthetic data: {len(sap_records)} SAP records, "
            f"{len(agent_logs)} log events, "
            f"{len(approval_records)} approvals"
        )

    else:
        # Layer 1 — live SAP collection
        from .collector.main import run_collection
        from .collector.evidence_store import LocalEvidenceStore

        sap_records = run_collection(config)

        if not sap_records:
            logger.warning("No SAP records collected. Exiting.")
            return []

        logs_path = str(base_path / "agent_logs")
        approvals_path = str(base_path / "approvals")

    # Layer 2 — Evidence Correlation
    logger.info("Running evidence correlation...")
    log_source = LocalAgentLogSource(log_path=logs_path)
    approval_source = LocalApprovalSource(approvals_path=approvals_path)
    correlator = EvidenceCorrelator(log_source, approval_source)
    packages = correlator.correlate_batch(sap_records)

    # Layer 3 — Narrative Generation
    logger.info("Generating audit narratives...")
    narratives = run_narrative_generation(packages, config)

    # Print to console
    print_narratives(narratives)

    return narratives


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SAP Audit Agent Pipeline")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic test data instead of live SAP connection",
    )
    args = parser.parse_args()
    run_pipeline(synthetic=args.synthetic)
