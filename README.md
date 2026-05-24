# SAP Audit Preparation Agent

**An AI agent that continuously collects, correlates, and narrates SAP S/4HANA audit evidence — built for financial close governance.**

🌐 **[Live Demo Site](https://goismael.github.io/sap-audit-agent)** · 📚 **[Governance Pattern Library](https://goismael.github.io/sap-agent-governance)**

---

## The Problem

When an auditor asks *"why did the agent post this document?"* — the answer should already be written, linked, and verifiable. In most SAP environments today, it isn't.

This agent fills that gap. It reads SAP financial events, connects them to AI agent reasoning logs and human approval records, and produces audit-ready narratives in plain language — automatically, as financial processes execute.

---

## What It Produces

```
AUDIT READINESS REPORT — Period 04/2026

Score:  87 / 100
Grade:  B — Minor Remediation Required

Documents analyzed:     500
Agent-posted:           392  (78.4%)
Human-posted:           108  (21.6%)
Audit ready:            471  (94.2%)
Complete evidence:      458
Critical gaps:            3  ← Flagged for immediate action
Hash chain verified:    389

Saved: audit_report_close-cycle-2026-04-Q1-001.md
       audit_report_close-cycle-2026-04-Q1-001.json
```

---

## Architecture

```
SAP S/4HANA (OData APIs)
        │
        ▼
SAP Data Collector          ← Layer 1: journal entries, change docs, posting periods
        │
        ▼
Evidence Correlator         ← Layer 2: SAP events + agent logs + approval records
        │
        ▼
Narrative Engine (Gemini)   ← Layer 3: plain-language audit narratives
        │
        ▼
Audit Report Generator      ← Layer 4: scored, graded period readiness report
```

---

## Layers

| Layer | Module | Status | Description |
|---|---|---|---|
| SAP Data Collector | `src/collector` | ✅ Complete | OData-only SAP integration, delta queries, agent detection |
| Evidence Correlator | `src/correlator` | ✅ Complete | Four-way join, hash verification, gap detection, completeness scoring |
| Narrative Engine | `src/narrative` | ✅ Complete | Gemini 2.5 Flash generates SOX-grade audit narratives |
| Audit Report Generator | `src/reporter` | ✅ Complete | A–D graded period report, Markdown + JSON output |

---

## Sample Narrative Output

```
AUDIT NARRATIVE — Document 100000001 / Company Code 1000

On 30 April 2026, GL Reconciliation Agent posted journal entry 100000001
to GL account 400000 in company code 1000 for USD 184,200.00 (SA document),
fiscal period 04/2026.

The agent analyzed the trial balance and confirmed GL and sub-ledger balances
matched exactly with zero variance. Two alternatives were evaluated and
rejected — manual review flagging (no discrepancy) and controller escalation
(tolerance thresholds not exceeded). The agent concluded with high confidence
that posting was the correct action.

The posting was authorized by Maria Santos (Controller) at 23:31 UTC via
Microsoft Teams. The authorization hash was verified — no parameter changes
occurred between authorization and execution.

Evidence completeness: 100%. All four evidence layers present and verified.
```

---

## Built On

This product is the practical application of the **[SAP Agent Governance Patterns](https://goismael.github.io/sap-agent-governance)** library:

| Pattern | Role in This Product |
|---|---|
| **P001** — Permission Scoping | Service user registry identifies agent-posted documents |
| **P002** — Approval Gates | Source of approval records and hash-verified authorization chain |
| **P003** — Audit Logging | Source of agent action and reasoning logs |
| **P004** — Failure Handling | Recovery events included in audit narratives |

---

## Getting Started

```bash
# Clone the repo
git clone https://github.com/goismael/sap-audit-agent.git
cd sap-audit-agent

# Install dependencies
pip install -r requirements.txt

# Configure
cp config/config.example.yaml config/config.yaml
# Add GEMINI_API_KEY to .env file

# Run with synthetic enterprise data (500 documents, no SAP required)
python -m src.pipeline --synthetic

# Run with live SAP
python -m src.pipeline
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| LLM | Google Gemini 2.5 Flash via Langchain |
| SAP Connectivity | OData REST APIs (supported pathway only) |
| Evidence Storage | Local NDJSON (POC) / Azure Monitor (production) |
| Approval Store | Local JSON (POC) / Azure Cosmos DB (production) |

---

## POC Status

This is a proof of concept. The full pipeline runs end-to-end with enterprise synthetic data (500 documents across 4 company codes). The next step is connecting Layer 1 to a live SAP S/4HANA sandbox.

What works today:
- ✅ All four layers run end-to-end in under 20 seconds (synthetic)
- ✅ Gemini generates production-quality audit narratives
- ✅ Gap detection correctly flags missing approvals as SOX control failures
- ✅ Completeness scoring and A–D grading are production-ready
- ✅ Enterprise synthetic dataset: 500 documents, 4 company codes, 9 scenario types

What's next:
- 🔜 Live SAP S/4HANA connection via BTP trial
- 🔜 Azure Monitor log sink for production P003 logs
- 🔜 Cosmos DB approval store integration

---

## Author

**Ismael** — Senior Intelligent Automation & AI Engineer
7+ years enterprise AI and automation | SAP S/4HANA | Microsoft Azure | Multi-agent systems

Companion library: [SAP Agent Governance Patterns](https://goismael.github.io/sap-agent-governance)

---

## License

MIT License — use freely, attribution appreciated.
