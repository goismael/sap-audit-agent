"""
SAP Audit Agent — Synthetic Data Generator
Generates realistic enterprise financial close data for POC demonstration.

Simulates a mid-size multinational with:
- 4 company codes across 3 regions
- ~500 journal entries per monthly close
- Multiple document types and posting scenarios
- Realistic mix of agent-posted and human-posted documents
- Varied evidence quality: complete, partial, and critical gaps
- Multiple agents with different classifications
- Real-world failure scenarios
"""

import hashlib
import json
import random
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple

from ..common.models import SAPEvidenceRecord, EventType

# ── Company Configuration ──────────────────────────────────────────────────────

COMPANY_CODES = {
    "1000": {"name": "HQ Americas", "currency": "USD", "region": "AMER"},
    "2000": {"name": "Europe Operations", "currency": "EUR", "region": "EMEA"},
    "3000": {"name": "Asia Pacific", "currency": "SGD", "region": "APAC"},
    "4000": {"name": "Intercompany Clearing", "currency": "USD", "region": "CORP"},
}

# GL accounts by category
GL_ACCOUNTS = {
    "revenue": ["400000", "400100", "400200", "400300", "401000"],
    "cogs": ["500000", "500100", "500200", "501000"],
    "opex": ["600000", "600100", "601000", "602000", "603000", "610000"],
    "accruals": ["700000", "700100", "700200", "701000", "702000"],
    "intercompany": ["180000", "180100", "180200", "380000", "380100"],
    "fx_revaluation": ["290000", "290100", "890000"],
    "tax": ["220000", "220100", "221000"],
    "clearing": ["100001", "100002", "100003"],
}

# Document types
DOC_TYPES = {
    "SA": "G/L Account Document",
    "AA": "Asset Posting",
    "KR": "Vendor Invoice",
    "KZ": "Vendor Payment",
    "AB": "Accounting Document (Reversal)",
    "RV": "SD Billing Transfer",
    "WA": "Goods Issue",
}

# Agent service users (from P001)
AGENT_USERS = [
    "SVC_AGENT_EXEC_POST",
    "SVC_AGENT_EXEC_ACCRUAL",
    "SVC_AGENT_EXEC_IC",        # Intercompany
    "SVC_AGENT_EXEC_FX",        # FX revaluation
    "SVC_AGENT_EXEC_CLEAR",     # Auto-clearing
]

# Human users
HUMAN_USERS = [
    "MSANTOS",    # Controller
    "JSMITH",     # Senior Accountant
    "LWANG",      # APAC Finance Manager
    "ABECKER",    # EMEA Controller
    "RJOHNSON",   # CFO
    "TKIM",       # Treasury Manager
]

# Approvers (from P002)
APPROVERS = {
    "MSANTOS": {"name": "Maria Santos", "role": "Controller"},
    "RJOHNSON": {"name": "Robert Johnson", "role": "CFO"},
    "ABECKER": {"name": "Anna Becker", "role": "EMEA Controller"},
    "LWANG": {"name": "Li Wang", "role": "APAC Finance Manager"},
    "TKIM": {"name": "Thomas Kim", "role": "Treasury Manager"},
}

# Close session
SESSION_ID = "close-cycle-2026-04-Q1-001"
FISCAL_YEAR = "2026"
PERIOD = "04"
POSTING_DATE = "2026-04-30"


# ── Scenario Templates ─────────────────────────────────────────────────────────

SCENARIOS = {
    "monthly_accrual": {
        "weight": 25,
        "doc_type": "SA",
        "agent": "SVC_AGENT_EXEC_ACCRUAL",
        "gl_category": "accruals",
        "risk_tier": "notify",
        "amount_range": (5000, 250000),
        "description": "Month-end accrual posting",
        "evidence_quality": "complete",
    },
    "revenue_recognition": {
        "weight": 15,
        "doc_type": "SA",
        "agent": "SVC_AGENT_EXEC_POST",
        "gl_category": "revenue",
        "risk_tier": "approve",
        "amount_range": (50000, 2000000),
        "description": "Revenue recognition adjustment",
        "evidence_quality": "complete",
    },
    "intercompany_elimination": {
        "weight": 10,
        "doc_type": "SA",
        "agent": "SVC_AGENT_EXEC_IC",
        "gl_category": "intercompany",
        "risk_tier": "approve",
        "amount_range": (100000, 5000000),
        "description": "Intercompany elimination",
        "evidence_quality": "complete",
    },
    "fx_revaluation": {
        "weight": 5,
        "doc_type": "SA",
        "agent": "SVC_AGENT_EXEC_FX",
        "gl_category": "fx_revaluation",
        "risk_tier": "approve",
        "amount_range": (10000, 500000),
        "description": "FX period-end revaluation",
        "evidence_quality": "complete",
    },
    "auto_clearing": {
        "weight": 15,
        "doc_type": "SA",
        "agent": "SVC_AGENT_EXEC_CLEAR",
        "gl_category": "clearing",
        "risk_tier": "autonomous",
        "amount_range": (1000, 50000),
        "description": "Automatic open item clearing",
        "evidence_quality": "complete",
    },
    "human_manual": {
        "weight": 20,
        "doc_type": "SA",
        "agent": None,  # Human posted
        "gl_category": "opex",
        "risk_tier": None,
        "amount_range": (500, 75000),
        "description": "Manual journal entry",
        "evidence_quality": "complete",
    },
    "missing_approval": {
        "weight": 4,
        "doc_type": "SA",
        "agent": "SVC_AGENT_EXEC_POST",
        "gl_category": "accruals",
        "risk_tier": "approve",
        "amount_range": (25000, 200000),
        "description": "Posting with missing approval record",
        "evidence_quality": "critical_gap",
    },
    "missing_reasoning": {
        "weight": 3,
        "doc_type": "SA",
        "agent": "SVC_AGENT_EXEC_ACCRUAL",
        "gl_category": "accruals",
        "risk_tier": "notify",
        "amount_range": (5000, 100000),
        "description": "Posting with missing reasoning log",
        "evidence_quality": "high_gap",
    },
    "reversal": {
        "weight": 3,
        "doc_type": "AB",
        "agent": "SVC_AGENT_EXEC_POST",
        "gl_category": "accruals",
        "risk_tier": "approve",
        "amount_range": (10000, 300000),
        "description": "Document reversal",
        "evidence_quality": "complete",
    },
}


# ── Generator ──────────────────────────────────────────────────────────────────

class SyntheticDataGenerator:
    """
    Generates realistic synthetic financial close data for POC demonstration.
    Simulates a mid-size multinational enterprise monthly close.
    """

    def __init__(self, seed: int = 42):
        random.seed(seed)
        self._doc_counter: Dict[str, int] = {cc: 100000000 for cc in COMPANY_CODES}
        self._seq_counter = 0

    def generate(
        self,
        target_documents: int = 500,
    ) -> Tuple[List[SAPEvidenceRecord], List[Dict], List[Dict]]:
        """
        Generate a full synthetic close dataset.

        Args:
            target_documents: Approximate number of documents to generate

        Returns:
            Tuple of (sap_records, agent_logs, approval_records)
        """
        sap_records = []
        agent_logs = []
        approval_records = []

        # Distribute documents across company codes
        # HQ gets most volume, CORP (intercompany) gets less
        cc_weights = {"1000": 35, "2000": 30, "3000": 20, "4000": 15}

        for cc, weight in cc_weights.items():
            cc_count = int(target_documents * weight / 100)
            cc_records, cc_logs, cc_approvals = self._generate_company_code(
                cc, cc_count
            )
            sap_records.extend(cc_records)
            agent_logs.extend(cc_logs)
            approval_records.extend(cc_approvals)

        # Shuffle to simulate realistic posting order
        random.shuffle(sap_records)

        return sap_records, agent_logs, approval_records

    def _generate_company_code(
        self,
        company_code: str,
        count: int,
    ) -> Tuple[List[SAPEvidenceRecord], List[Dict], List[Dict]]:
        """Generate documents for a single company code."""
        records = []
        logs = []
        approvals = []

        # Select scenarios weighted by their weight
        scenario_names = list(SCENARIOS.keys())
        weights = [SCENARIOS[s]["weight"] for s in scenario_names]

        for _ in range(count):
            scenario_name = random.choices(scenario_names, weights=weights, k=1)[0]
            scenario = SCENARIOS[scenario_name]

            record, log_events, approval = self._generate_document(
                company_code, scenario, scenario_name
            )

            records.append(record)
            logs.extend(log_events)
            if approval:
                approvals.append(approval)

        return records, logs, approvals

    def _generate_document(
        self,
        company_code: str,
        scenario: Dict,
        scenario_name: str,
    ) -> Tuple[SAPEvidenceRecord, List[Dict], Dict | None]:
        """Generate a single document with its evidence chain."""
        cc_info = COMPANY_CODES[company_code]
        doc_num = self._next_doc_number(company_code)
        amount = round(random.uniform(*scenario["amount_range"]), 2)
        gl_account = random.choice(GL_ACCOUNTS[scenario["gl_category"]])
        is_agent = scenario["agent"] is not None
        agent_user = scenario["agent"] if is_agent else None
        human_user = random.choice(HUMAN_USERS) if not is_agent else None
        posting_time = self._random_time()

        # Build SAP record
        record = SAPEvidenceRecord(
            source_service="API_JOURNALENTRYITEMBASIC_SRV",
            event_type=(
                EventType.DOCUMENT_REVERSAL
                if scenario["doc_type"] == "AB"
                else EventType.JOURNAL_ENTRY_POSTING
            ),
            company_code=company_code,
            fiscal_year=FISCAL_YEAR,
            period=PERIOD,
            document_number=doc_num,
            posting_date=POSTING_DATE,
            posting_time=posting_time,
            document_type=scenario["doc_type"],
            posted_by_user=agent_user or human_user,
            amount=amount,
            currency=cc_info["currency"],
            gl_account=gl_account,
            cost_center=f"CC{company_code}0{random.randint(1,5)}",
            reference=SESSION_ID if is_agent else "",
            document_header_text=scenario["description"],
            is_agent_posted=is_agent,
            agent_session_reference=SESSION_ID if is_agent else None,
        )

        if not is_agent:
            return record, [], None

        # Build agent evidence chain
        quality = scenario["evidence_quality"]
        action_log, reasoning_log, approval = self._build_evidence_chain(
            company_code=company_code,
            doc_num=doc_num,
            agent_user=agent_user,
            amount=amount,
            currency=cc_info["currency"],
            gl_account=gl_account,
            risk_tier=scenario["risk_tier"],
            quality=quality,
            posting_time=posting_time,
        )

        log_events = [action_log]
        if reasoning_log:
            log_events.insert(0, reasoning_log)

        return record, log_events, approval

    def _build_evidence_chain(
        self,
        company_code: str,
        doc_num: str,
        agent_user: str,
        amount: float,
        currency: str,
        gl_account: str,
        risk_tier: str,
        quality: str,
        posting_time: str,
    ) -> Tuple[Dict, Dict | None, Dict | None]:
        """Build action log, reasoning log, and approval record."""
        approval_id = str(uuid.uuid4())
        action_seq = self._next_seq()
        reasoning_seq = action_seq - 1

        # ── Reasoning Log ──
        reasoning_log = None
        if quality != "high_gap":  # high_gap = missing reasoning
            reasoning_log = self._make_reasoning_log(
                seq=reasoning_seq,
                company_code=company_code,
                amount=amount,
                currency=currency,
                gl_account=gl_account,
            )

        # ── Action Log ──
        action_log = self._make_action_log(
            seq=action_seq,
            agent_user=agent_user,
            doc_num=doc_num,
            company_code=company_code,
            risk_tier=risk_tier,
            approval_id=approval_id if quality != "critical_gap" else str(uuid.uuid4()),
            posting_time=posting_time,
        )

        # ── Approval Record ──
        approval = None
        if quality == "complete" and risk_tier in ("approve", "notify"):
            approval = self._make_approval(
                approval_id=approval_id,
                company_code=company_code,
                amount=amount,
                currency=currency,
            )

        return action_log, reasoning_log, approval

    def _make_reasoning_log(
        self,
        seq: int,
        company_code: str,
        amount: float,
        currency: str,
        gl_account: str,
    ) -> Dict:
        variance = round(random.uniform(0, amount * 0.001), 2)
        prev_hash = self._random_hash()
        event = {
            "event_id": str(uuid.uuid4()),
            "session_id": SESSION_ID,
            "agent_id": f"gl-reconciliation-agent-{company_code}",
            "agent_classification": "executor",
            "timestamp": f"2026-04-30T{self._random_time(end_hour=23)}Z",
            "sequence_number": seq,
            "previous_event_hash": prev_hash,
            "event_hash": self._random_hash(),
            "layer": "reasoning",
            "payload": {
                "reasoning": {
                    "decision_point": random.choice([
                        "period_close_accrual_assessment",
                        "reconciliation_variance_assessment",
                        "intercompany_matching_assessment",
                        "fx_rate_variance_assessment",
                        "clearing_eligibility_assessment",
                    ]),
                    "input_data_summary": {
                        "gl_balance": amount,
                        "sub_ledger_balance": amount - variance,
                        "variance": variance,
                        "currency": currency,
                        "company_code": company_code,
                        "period": f"{FISCAL_YEAR}-{PERIOD}",
                        "gl_account": gl_account,
                    },
                    "alternatives_considered": [
                        {
                            "option": "flag_for_manual_review",
                            "reason_rejected": (
                                "Variance below materiality threshold"
                                if variance < amount * 0.001
                                else "Within acceptable tolerance range"
                            ),
                        },
                        {
                            "option": "escalate_to_controller",
                            "reason_rejected": "Reconciliation conditions fully met",
                        },
                    ],
                    "conclusion": random.choice([
                        "post_accrual",
                        "reconciliation_passed",
                        "elimination_approved",
                        "revaluation_complete",
                        "clearing_eligible",
                    ]),
                    "confidence": random.choice(["high", "high", "high", "medium"]),
                    "conclusion_reasoning": (
                        f"GL balance of {currency} {amount:,.2f} reconciles within "
                        f"tolerance. Variance of {currency} {variance:,.2f} is below "
                        f"materiality threshold. All preconditions for posting are met."
                    ),
                    "human_review_recommended": variance > amount * 0.0005,
                    "human_review_reason": (
                        "Variance detected — controller notification sent"
                        if variance > amount * 0.0005 else None
                    ),
                    "downstream_actions_triggered": ["sign_off_agent_notify"],
                }
            },
        }
        return event

    def _make_action_log(
        self,
        seq: int,
        agent_user: str,
        doc_num: str,
        company_code: str,
        risk_tier: str,
        approval_id: str,
        posting_time: str,
    ) -> Dict:
        prev_hash = self._random_hash()
        return {
            "event_id": str(uuid.uuid4()),
            "session_id": SESSION_ID,
            "agent_id": f"gl-reconciliation-agent-{company_code}",
            "agent_classification": "executor",
            "timestamp": f"2026-04-30T{posting_time}Z",
            "sequence_number": seq,
            "previous_event_hash": prev_hash,
            "event_hash": self._random_hash(),
            "layer": "action",
            "payload": {
                "action": {
                    "type": "sap_bapi",
                    "name": "BAPI_ACC_DOCUMENT_POST",
                    "parameters": {
                        "company_code": company_code,
                        "fiscal_year": FISCAL_YEAR,
                    },
                    "risk_tier": risk_tier,
                    "permission_check_passed": True,
                },
                "outcome": {
                    "status": "success",
                    "duration_ms": random.randint(150, 800),
                    "sap_document_number": doc_num,
                    "sap_return_code": "S",
                    "error_code": None,
                    "error_message": None,
                },
                "approval_id": approval_id,
            },
        }

    def _make_approval(
        self,
        approval_id: str,
        company_code: str,
        amount: float,
        currency: str,
    ) -> Dict:
        approver_id = random.choice(list(APPROVERS.keys()))
        approver = APPROVERS[approver_id]
        approved_minutes_before = random.randint(5, 45)
        return {
            "approval_id": approval_id,
            "package_id": str(uuid.uuid4()),
            "action_package_hash": self._random_hash(),
            "approver_id": approver_id,
            "approver_role": approver["role"],
            "approved_at": f"2026-04-30T{self._random_time(end_hour=22)}Z",
            "approval_channel": random.choice(["teams", "teams", "email"]),
            "status": "approved",
            "agent_id": f"gl-reconciliation-agent-{company_code}",
            "session_id": SESSION_ID,
            "comment": random.choice([
                "Reconciliation confirmed. Proceed.",
                "Reviewed and approved.",
                None, None, None,  # Most approvals have no comment
            ]),
        }

    def _next_doc_number(self, company_code: str) -> str:
        self._doc_counter[company_code] += 1
        return str(self._doc_counter[company_code])

    def _next_seq(self) -> int:
        self._seq_counter += 2
        return self._seq_counter

    @staticmethod
    def _random_time(end_hour: int = 23) -> str:
        hour = random.randint(6, end_hour)
        minute = random.randint(0, 59)
        second = random.randint(0, 59)
        return f"{hour:02d}:{minute:02d}:{second:02d}"

    @staticmethod
    def _random_hash() -> str:
        return hashlib.sha256(
            str(uuid.uuid4()).encode()
        ).hexdigest()[:16]


def generate_enterprise_dataset(
    target_documents: int = 500,
) -> Tuple[List[SAPEvidenceRecord], List[Dict], List[Dict]]:
    """
    Public API for generating enterprise synthetic data.

    Args:
        target_documents: Approximate number of documents to generate

    Returns:
        Tuple of (sap_records, agent_logs, approval_records)
    """
    generator = SyntheticDataGenerator(seed=42)
    return generator.generate(target_documents=target_documents)
