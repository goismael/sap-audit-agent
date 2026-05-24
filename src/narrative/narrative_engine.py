"""
SAP Audit Agent — Narrative Engine
Layer 3: Uses Google Gemini via Langchain to generate plain-language
audit narratives from correlated evidence packages.

This is the layer that answers the auditor's core question:
"Why did the agent do what it did — and who authorized it?"
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from ..common.models import EvidencePackage, EvidenceGap

logger = logging.getLogger(__name__)


# ── Narrative Output ───────────────────────────────────────────────────────────

@dataclass
class AuditNarrative:
    """
    A generated audit narrative for a single SAP document.
    Produced by the Narrative Engine from an EvidencePackage.
    """
    document_number: str
    company_code: str
    narrative_text: str
    completeness_score: int
    gaps: List[EvidenceGap] = field(default_factory=list)
    hash_verified: bool = False
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_used: str = "gemini-2.5-flash"
    generation_time_ms: int = 0

    @property
    def has_critical_gaps(self) -> bool:
        return any(g.audit_risk == "Critical" for g in self.gaps)

    @property
    def audit_ready(self) -> bool:
        return self.completeness_score >= 80 and not self.has_critical_gaps


# ── Prompt Templates ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an AI audit documentation specialist with deep expertise \
in SAP S/4HANA financial systems and SOX compliance requirements.

Your role is to translate structured financial system events and AI agent logs \
into clear, precise audit narratives that answer the questions external auditors ask.

STRICT RULES:
- Write in past tense, third person
- Be specific — include document numbers, amounts, dates, approver names
- Never speculate — only state what the evidence confirms
- Flag gaps explicitly — do not fill missing evidence with assumptions
- Use audit terminology: "posted", "authorized", "reversed", "reconciled", "escalated"
- Keep the narrative under 250 words
- End with a completeness statement that includes the score
- If there are critical gaps, state them clearly at the end under "AUDIT ATTENTION REQUIRED"
"""

AGENT_POSTING_PROMPT = """Generate an audit narrative for the following \
SAP financial document posted by an AI agent.

=== SAP DOCUMENT ===
Document Number: {document_number}
Company Code: {company_code}
Posting Date: {posting_date}
Document Type: {document_type}
GL Account: {gl_account}
Amount: {amount} {currency}
Posted By: {posted_by} (AI Agent Service User)
Session Reference: {session_reference}

=== AGENT ACTION ===
Agent ID: {agent_id}
Agent Classification: {agent_classification}
Action: {action_name}
Risk Tier: {risk_tier}
Permission Check: {permission_check}
Outcome: {outcome_status}
Duration: {duration_ms}ms

=== AGENT REASONING ===
Decision Point: {decision_point}
Data Analyzed: {input_data_summary}
Alternatives Considered: {alternatives_considered}
Conclusion: {conclusion}
Confidence: {confidence}
Reasoning: {conclusion_reasoning}

=== HUMAN AUTHORIZATION ===
Approver: {approver_id} ({approver_role})
Approved At: {approved_at}
Channel: {approval_channel}
Hash Verified: {hash_verified}
Status: {approval_status}

=== EVIDENCE QUALITY ===
Completeness Score: {completeness_score}%
Gaps Detected: {gap_count}
Gap Details: {gap_details}

Generate the audit narrative now.
"""

HUMAN_POSTING_PROMPT = """Generate a brief audit narrative for the following \
SAP financial document posted by a human user.

=== SAP DOCUMENT ===
Document Number: {document_number}
Company Code: {company_code}
Posting Date: {posting_date}
Document Type: {document_type}
GL Account: {gl_account}
Amount: {amount} {currency}
Posted By: {posted_by} (Human User)

This document was posted directly by a human user and does not involve \
AI agent governance controls. Standard SAP authorization controls apply.

Generate a brief audit narrative confirming the human posting.
"""


# ── Narrative Engine ───────────────────────────────────────────────────────────

class NarrativeEngine:
    """
    Generates plain-language audit narratives from evidence packages
    using Google Gemini 2.5 Flash via Langchain.

    The engine handles two document types differently:
    - Agent-posted documents: full narrative with reasoning + approval chain
    - Human-posted documents: brief confirmation narrative

    Usage:
        engine = NarrativeEngine(config["llm"])
        narrative = engine.generate(evidence_package)
    """

    def __init__(self, llm_config: Dict[str, Any]):
        self.model_name = llm_config.get("model", "gemini-2.5-flash")
        self.temperature = llm_config.get("temperature", 0.1)
        self.max_tokens = llm_config.get("max_output_tokens", 1024)

        # Initialize Gemini via Langchain
        self.llm = ChatGoogleGenerativeAI(
            model=self.model_name,
            google_api_key=llm_config["api_key"],
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )

        # Build chains
        self._agent_chain = self._build_chain(AGENT_POSTING_PROMPT)
        self._human_chain = self._build_chain(HUMAN_POSTING_PROMPT)

        logger.info(f"Narrative Engine initialized: model={self.model_name}")

    def _build_chain(self, user_prompt_template: str):
        """Build a Langchain chain for a given prompt template."""
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", user_prompt_template),
        ])
        return prompt | self.llm | StrOutputParser()

    def generate(self, package: EvidencePackage) -> AuditNarrative:
        """
        Generate an audit narrative for a single evidence package.

        Args:
            package: Correlated evidence package from Layer 2

        Returns:
            AuditNarrative with generated text and metadata
        """
        import time
        start = time.time()

        try:
            if package.sap_event and package.sap_event.is_agent_posted:
                narrative_text = self._generate_agent_narrative(package)
            else:
                narrative_text = self._generate_human_narrative(package)

            duration_ms = int((time.time() - start) * 1000)

            narrative = AuditNarrative(
                document_number=package.document_number,
                company_code=package.company_code,
                narrative_text=narrative_text,
                completeness_score=package.completeness_score,
                gaps=package.gaps,
                hash_verified=package.hash_chain_verified,
                model_used=self.model_name,
                generation_time_ms=duration_ms,
            )

            logger.info(
                f"Narrative generated: doc={package.document_number} "
                f"score={package.completeness_score}% "
                f"duration={duration_ms}ms "
                f"audit_ready={narrative.audit_ready}"
            )

            return narrative

        except Exception as e:
            logger.error(
                f"Narrative generation failed for document "
                f"{package.document_number}: {e}"
            )
            # Return a placeholder narrative on failure
            return AuditNarrative(
                document_number=package.document_number,
                company_code=package.company_code,
                narrative_text=(
                    f"[NARRATIVE GENERATION FAILED] "
                    f"Document {package.document_number} — "
                    f"Error: {str(e)}. "
                    f"Manual narrative required."
                ),
                completeness_score=package.completeness_score,
                gaps=package.gaps,
                hash_verified=False,
                generation_time_ms=int((time.time() - start) * 1000),
            )

    def generate_batch(
        self,
        packages: List[EvidencePackage],
        skip_human_postings: bool = False,
    ) -> List[AuditNarrative]:
        """
        Generate narratives for a batch of evidence packages.

        Args:
            packages: List of correlated evidence packages
            skip_human_postings: If True, skip human-posted documents
                                  to reduce API calls

        Returns:
            List of AuditNarrative instances
        """
        narratives = []
        to_process = packages

        if skip_human_postings:
            to_process = [
                p for p in packages
                if p.sap_event and p.sap_event.is_agent_posted
            ]
            skipped = len(packages) - len(to_process)
            if skipped:
                logger.info(f"Skipping {skipped} human-posted documents")

        logger.info(f"Generating narratives for {len(to_process)} documents")

        for i, package in enumerate(to_process, 1):
            logger.info(
                f"Generating narrative {i}/{len(to_process)}: "
                f"doc={package.document_number}"
            )
            narrative = self.generate(package)
            narratives.append(narrative)

        # Summary stats
        audit_ready = [n for n in narratives if n.audit_ready]
        critical = [n for n in narratives if n.has_critical_gaps]

        logger.info(
            f"Narrative generation complete: {len(narratives)} generated | "
            f"{len(audit_ready)} audit-ready | "
            f"{len(critical)} with critical gaps"
        )

        return narratives

    def _generate_agent_narrative(self, package: EvidencePackage) -> str:
        """Generate full narrative for an agent-posted document."""
        sap = package.sap_event
        action = package.action_log or {}
        reasoning = package.reasoning_log or {}
        approval = package.approval_record or {}

        # Extract reasoning fields
        reasoning_data = reasoning.get("reasoning", reasoning)
        action_data = action.get("action", action)
        outcome_data = action.get("outcome", {})

        # Format alternatives considered
        alternatives = reasoning_data.get("alternatives_considered", [])
        alt_text = "; ".join([
            f"{a.get('option', 'N/A')} (rejected: {a.get('reason_rejected', 'N/A')})"
            for a in alternatives
        ]) if alternatives else "None documented"

        # Format gap details
        gap_details = self._format_gaps(package.gaps)

        return self._agent_chain.invoke({
            "document_number": sap.document_number if sap else "N/A",
            "company_code": sap.company_code if sap else "N/A",
            "posting_date": sap.posting_date if sap else "N/A",
            "document_type": sap.document_type if sap else "N/A",
            "gl_account": sap.gl_account if sap else "N/A",
            "amount": f"{sap.amount:,.2f}" if sap else "N/A",
            "currency": sap.currency if sap else "N/A",
            "posted_by": sap.posted_by_user if sap else "N/A",
            "session_reference": sap.agent_session_reference or "Not found",
            "agent_id": action.get("agent_id", "N/A"),
            "agent_classification": action.get("agent_classification", "N/A"),
            "action_name": action_data.get("name", "N/A"),
            "risk_tier": action_data.get("risk_tier", "N/A"),
            "permission_check": (
                "Passed" if action_data.get("permission_check_passed") else "Failed"
            ),
            "outcome_status": outcome_data.get("status", "N/A"),
            "duration_ms": outcome_data.get("duration_ms", "N/A"),
            "decision_point": reasoning_data.get("decision_point", "N/A"),
            "input_data_summary": str(
                reasoning_data.get("input_data_summary", "Not available")
            ),
            "alternatives_considered": alt_text,
            "conclusion": reasoning_data.get("conclusion", "N/A"),
            "confidence": reasoning_data.get("confidence", "N/A"),
            "conclusion_reasoning": reasoning_data.get(
                "conclusion_reasoning", "Not documented"
            ),
            "approver_id": approval.get("approver_id", "Not found"),
            "approver_role": approval.get("approver_role", "N/A"),
            "approved_at": approval.get("approved_at", "Not found"),
            "approval_channel": approval.get("approval_channel", "N/A"),
            "hash_verified": "Yes" if package.hash_chain_verified else "No",
            "approval_status": approval.get("status", "Not found"),
            "completeness_score": package.completeness_score,
            "gap_count": len(package.gaps),
            "gap_details": gap_details,
        })

    def _generate_human_narrative(self, package: EvidencePackage) -> str:
        """Generate brief narrative for a human-posted document."""
        sap = package.sap_event

        return self._human_chain.invoke({
            "document_number": sap.document_number if sap else "N/A",
            "company_code": sap.company_code if sap else "N/A",
            "posting_date": sap.posting_date if sap else "N/A",
            "document_type": sap.document_type if sap else "N/A",
            "gl_account": sap.gl_account if sap else "N/A",
            "amount": f"{sap.amount:,.2f}" if sap else "N/A",
            "currency": sap.currency if sap else "N/A",
            "posted_by": sap.posted_by_user if sap else "N/A",
        })

    @staticmethod
    def _format_gaps(gaps: List[EvidenceGap]) -> str:
        """Format gaps list for prompt injection."""
        if not gaps:
            return "None"
        return "; ".join([
            f"{g.gap_type.value} ({g.audit_risk} risk): {g.description}"
            for g in gaps
        ])
