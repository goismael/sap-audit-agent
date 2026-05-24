"""
SAP Audit Agent — Report Generator
Layer 4: Produces a structured period audit readiness report
from generated narratives. Output: Markdown + JSON summary.

This is the deliverable a CFO or auditor actually receives.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..narrative.narrative_engine import AuditNarrative
from ..common.models import EvidenceGapType

logger = logging.getLogger(__name__)


# ── Report Data Model ──────────────────────────────────────────────────────────

@dataclass
class PeriodSummary:
    """High-level statistics for the audit readiness report."""
    close_cycle: str
    company_codes: List[str]
    period: str
    fiscal_year: str
    report_generated_at: str

    total_documents: int = 0
    agent_posted: int = 0
    human_posted: int = 0

    narratives_generated: int = 0
    audit_ready: int = 0
    not_audit_ready: int = 0

    complete_evidence: int = 0       # 100% completeness
    partial_evidence: int = 0        # 50-99%
    incomplete_evidence: int = 0     # <50%

    critical_gaps: int = 0
    high_gaps: int = 0
    hash_verified: int = 0

    @property
    def audit_readiness_score(self) -> int:
        """Overall audit readiness score 0-100."""
        if self.narratives_generated == 0:
            return 0
        base = (self.audit_ready / self.narratives_generated) * 80
        hash_bonus = (self.hash_verified / max(self.agent_posted, 1)) * 20
        critical_penalty = min(self.critical_gaps * 5, 20)
        return max(0, min(100, int(base + hash_bonus - critical_penalty)))

    @property
    def readiness_grade(self) -> str:
        score = self.audit_readiness_score
        if score >= 95:
            return "A — Audit Ready"
        elif score >= 80:
            return "B — Minor Remediation Required"
        elif score >= 60:
            return "C — Moderate Remediation Required"
        else:
            return "D — Significant Issues — Do Not Submit"


@dataclass
class AuditReadinessReport:
    """Complete period audit readiness report."""
    summary: PeriodSummary
    narratives: List[AuditNarrative]
    critical_items: List[AuditNarrative] = field(default_factory=list)
    report_markdown: str = ""
    report_json: Dict[str, Any] = field(default_factory=dict)


# ── Report Generator ───────────────────────────────────────────────────────────

class ReportGenerator:
    """
    Generates a structured period audit readiness report
    from a list of AuditNarrative instances.

    Produces two outputs:
    1. Markdown report — human-readable, suitable for PDF conversion
    2. JSON summary — machine-readable, suitable for dashboards

    Usage:
        generator = ReportGenerator()
        report = generator.generate(narratives, period_config)
        generator.save(report, output_path)
    """

    def generate(
        self,
        narratives: List[AuditNarrative],
        close_cycle: str,
        company_codes: List[str],
        period: str,
        fiscal_year: str,
    ) -> AuditReadinessReport:
        """
        Generate a complete audit readiness report.

        Args:
            narratives: Generated audit narratives from Layer 3
            close_cycle: Close cycle identifier
            company_codes: List of company codes covered
            period: Fiscal period (e.g. "04")
            fiscal_year: Fiscal year (e.g. "2026")

        Returns:
            AuditReadinessReport with markdown and JSON output
        """
        logger.info(f"Generating audit readiness report: {close_cycle}")

        # Build summary statistics
        summary = self._build_summary(
            narratives, close_cycle, company_codes, period, fiscal_year
        )

        # Identify critical items
        critical_items = [n for n in narratives if n.has_critical_gaps]

        # Generate markdown report
        markdown = self._generate_markdown(summary, narratives, critical_items)

        # Generate JSON summary
        json_summary = self._generate_json(summary, narratives, critical_items)

        report = AuditReadinessReport(
            summary=summary,
            narratives=narratives,
            critical_items=critical_items,
            report_markdown=markdown,
            report_json=json_summary,
        )

        logger.info(
            f"Report generated: score={summary.audit_readiness_score} "
            f"grade='{summary.readiness_grade}' "
            f"critical={summary.critical_gaps}"
        )

        return report

    def save(self, report: AuditReadinessReport, output_path: str) -> Dict[str, str]:
        """
        Save report to disk as Markdown and JSON.

        Returns:
            Dict with paths to saved files
        """
        path = Path(output_path)
        path.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cycle = report.summary.close_cycle.replace(" ", "_").replace("/", "-")

        md_path = path / f"audit_report_{cycle}_{ts}.md"
        json_path = path / f"audit_report_{cycle}_{ts}.json"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report.report_markdown)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report.report_json, f, indent=2)

        logger.info(f"Report saved: {md_path}")
        logger.info(f"JSON summary saved: {json_path}")

        return {"markdown": str(md_path), "json": str(json_path)}

    def _build_summary(
        self,
        narratives: List[AuditNarrative],
        close_cycle: str,
        company_codes: List[str],
        period: str,
        fiscal_year: str,
    ) -> PeriodSummary:
        """Calculate summary statistics from narratives."""
        summary = PeriodSummary(
            close_cycle=close_cycle,
            company_codes=company_codes,
            period=period,
            fiscal_year=fiscal_year,
            report_generated_at=datetime.now(timezone.utc).isoformat(),
            total_documents=len(narratives),
            narratives_generated=len(narratives),
        )

        for n in narratives:
            # Agent vs human
            if n.hash_verified or any(
                g.gap_type != EvidenceGapType.NO_APPROVAL_RECORD
                for g in n.gaps
            ):
                summary.agent_posted += 1
            else:
                summary.human_posted += 1

            # Audit readiness
            if n.audit_ready:
                summary.audit_ready += 1
            else:
                summary.not_audit_ready += 1

            # Completeness buckets
            if n.completeness_score == 100:
                summary.complete_evidence += 1
            elif n.completeness_score >= 50:
                summary.partial_evidence += 1
            else:
                summary.incomplete_evidence += 1

            # Gaps
            for gap in n.gaps:
                if gap.audit_risk == "Critical":
                    summary.critical_gaps += 1
                elif gap.audit_risk == "High":
                    summary.high_gaps += 1

            # Hash verification
            if n.hash_verified:
                summary.hash_verified += 1

        return summary

    def _generate_markdown(
        self,
        summary: PeriodSummary,
        narratives: List[AuditNarrative],
        critical_items: List[AuditNarrative],
    ) -> str:
        """Generate the full markdown report."""
        lines = []

        # ── Header ──
        lines.append(f"# Audit Readiness Report")
        lines.append(f"**Close Cycle:** {summary.close_cycle}  ")
        lines.append(f"**Period:** {summary.period}/{summary.fiscal_year}  ")
        lines.append(
            f"**Entities:** {', '.join(summary.company_codes)}  "
        )
        lines.append(
            f"**Generated:** {summary.report_generated_at}  "
        )
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── Readiness Score ──
        lines.append("## Audit Readiness Score")
        lines.append("")
        lines.append(
            f"### {summary.audit_readiness_score} / 100 — {summary.readiness_grade}"
        )
        lines.append("")

        # Score bar
        filled = summary.audit_readiness_score // 5
        empty = 20 - filled
        bar = "█" * filled + "░" * empty
        lines.append(f"`{bar}` {summary.audit_readiness_score}%")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── Executive Summary ──
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(
            f"This report covers the {summary.close_cycle} financial close cycle "
            f"for {len(summary.company_codes)} company code(s) "
            f"({', '.join(summary.company_codes)}), "
            f"fiscal period {summary.period}/{summary.fiscal_year}."
        )
        lines.append("")
        lines.append(
            f"The AI audit agent analyzed **{summary.total_documents} financial documents**, "
            f"generating audit narratives for each. "
            f"**{summary.audit_ready} of {summary.narratives_generated} documents "
            f"({int(summary.audit_ready/max(summary.narratives_generated,1)*100)}%) "
            f"are audit-ready.**"
        )
        lines.append("")

        if summary.critical_gaps > 0:
            lines.append(
                f"> ⚠ **ATTENTION REQUIRED:** {summary.critical_gaps} document(s) "
                f"have critical evidence gaps that must be resolved before "
                f"audit submission."
            )
            lines.append("")

        lines.append("---")
        lines.append("")

        # ── Statistics ──
        lines.append("## Document Statistics")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| Total documents analyzed | {summary.total_documents} |")
        lines.append(f"| Narratives generated | {summary.narratives_generated} |")
        lines.append(f"| Audit ready | {summary.audit_ready} |")
        lines.append(f"| Not audit ready | {summary.not_audit_ready} |")
        lines.append(f"| Complete evidence (100%) | {summary.complete_evidence} |")
        lines.append(f"| Partial evidence (50-99%) | {summary.partial_evidence} |")
        lines.append(f"| Incomplete evidence (<50%) | {summary.incomplete_evidence} |")
        lines.append(f"| Hash chain verified | {summary.hash_verified} |")
        lines.append(f"| Critical gaps | {summary.critical_gaps} |")
        lines.append(f"| High gaps | {summary.high_gaps} |")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── Critical Items ──
        if critical_items:
            lines.append("## Critical Items — Immediate Action Required")
            lines.append("")
            lines.append(
                "The following documents have critical evidence gaps that "
                "represent potential SOX control failures. Each must be "
                "reviewed and remediated before audit submission."
            )
            lines.append("")

            for i, item in enumerate(critical_items, 1):
                lines.append(
                    f"### {i}. Document {item.document_number} "
                    f"(Company Code {item.company_code})"
                )
                lines.append("")
                lines.append(
                    f"**Completeness:** {item.completeness_score}% | "
                    f"**Audit Ready:** {'Yes' if item.audit_ready else 'No'}"
                )
                lines.append("")
                for gap in item.gaps:
                    if gap.audit_risk == "Critical":
                        lines.append(f"**Gap:** {gap.gap_type.value}")
                        lines.append(f"**Risk:** {gap.audit_risk}")
                        lines.append(f"**Description:** {gap.description}")
                        lines.append(
                            f"**Recommended Action:** {gap.recommended_action}"
                        )
                        lines.append("")

            lines.append("---")
            lines.append("")

        # ── Full Narratives ──
        lines.append("## Audit Narratives")
        lines.append("")
        lines.append(
            "The following narratives were generated by the SAP Audit Agent "
            "for each financial document in this close cycle."
        )
        lines.append("")

        for i, narrative in enumerate(narratives, 1):
            status = "✅ Audit Ready" if narrative.audit_ready else "⚠ Review Required"
            lines.append(
                f"### Document {i}: {narrative.document_number} "
                f"| CC {narrative.company_code} | {status}"
            )
            lines.append("")
            lines.append(
                f"**Completeness:** {narrative.completeness_score}% | "
                f"**Hash Verified:** {'Yes' if narrative.hash_verified else 'No'} | "
                f"**Model:** {narrative.model_used}"
            )
            lines.append("")
            lines.append(narrative.narrative_text)
            lines.append("")

            if narrative.gaps:
                lines.append("**Evidence Gaps:**")
                lines.append("")
                for gap in narrative.gaps:
                    lines.append(
                        f"- [{gap.audit_risk}] `{gap.gap_type.value}`: "
                        f"{gap.description}"
                    )
                lines.append("")

            lines.append("---")
            lines.append("")

        # ── Footer ──
        lines.append("## Report Metadata")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| Generated by | SAP Audit Agent v1.0 |")
        lines.append(f"| Narrative model | Gemini 2.5 Flash |")
        lines.append(
            f"| Governance framework | "
            f"[SAP Agent Governance Patterns]"
            f"(https://goismael.github.io/sap-agent-governance) |"
        )
        lines.append(f"| Report generated | {summary.report_generated_at} |")
        lines.append(f"| Close cycle | {summary.close_cycle} |")
        lines.append("")

        return "\n".join(lines)

    def _generate_json(
        self,
        summary: PeriodSummary,
        narratives: List[AuditNarrative],
        critical_items: List[AuditNarrative],
    ) -> Dict[str, Any]:
        """Generate machine-readable JSON summary."""
        return {
            "report_metadata": {
                "generated_at": summary.report_generated_at,
                "generated_by": "SAP Audit Agent v1.0",
                "model": "gemini-2.5-flash",
            },
            "period": {
                "close_cycle": summary.close_cycle,
                "fiscal_year": summary.fiscal_year,
                "period": summary.period,
                "company_codes": summary.company_codes,
            },
            "readiness": {
                "score": summary.audit_readiness_score,
                "grade": summary.readiness_grade,
                "audit_ready_count": summary.audit_ready,
                "total_documents": summary.total_documents,
                "audit_ready_pct": round(
                    summary.audit_ready / max(summary.narratives_generated, 1) * 100,
                    1,
                ),
            },
            "evidence": {
                "complete": summary.complete_evidence,
                "partial": summary.partial_evidence,
                "incomplete": summary.incomplete_evidence,
                "hash_verified": summary.hash_verified,
            },
            "gaps": {
                "critical": summary.critical_gaps,
                "high": summary.high_gaps,
                "critical_documents": [
                    {
                        "document_number": n.document_number,
                        "company_code": n.company_code,
                        "completeness_score": n.completeness_score,
                        "gaps": [
                            {
                                "type": g.gap_type.value,
                                "risk": g.audit_risk,
                                "recommended_action": g.recommended_action,
                            }
                            for g in n.gaps
                            if g.audit_risk == "Critical"
                        ],
                    }
                    for n in critical_items
                ],
            },
            "documents": [
                {
                    "document_number": n.document_number,
                    "company_code": n.company_code,
                    "completeness_score": n.completeness_score,
                    "hash_verified": n.hash_verified,
                    "audit_ready": n.audit_ready,
                    "has_critical_gaps": n.has_critical_gaps,
                    "gap_count": len(n.gaps),
                    "generated_at": n.generated_at,
                    "generation_time_ms": n.generation_time_ms,
                }
                for n in narratives
            ],
        }
