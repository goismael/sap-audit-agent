# SAP Audit Preparation Agent

**An AI agent that continuously collects, correlates, and narrates SAP S/4HANA audit evidence — built for financial close governance.**

---

## The Problem

When an auditor asks "why did the agent post this document?" — the answer should already be written, linked, and verifiable. In most SAP environments today, it isn't.

This agent fills that gap. It reads SAP financial events, connects them to AI agent reasoning logs and human approval records, and produces audit-ready narratives in plain language — automatically, as financial processes execute.

## Architecture

```
SAP S/4HANA (OData APIs)
        │
        ▼
SAP Data Collector          ← Layer 1: pulls journal entries, change docs, posting periods
        │
        ▼
Evidence Correlator         ← Layer 2: joins SAP events + agent logs + approval records
        │
        ▼
Narrative Engine (Gemini)   ← Layer 3: generates plain-language audit narratives
        │
        ▼
Audit Report Generator      ← Layer 4: produces period readiness reports + evidence packages
```

## Layers

| Layer | Module | Status |
|---|---|---|
| SAP Data Collector | `src/collector` | ✅ Available |
| Evidence Correlator | `src/correlator` | 🔜 Coming |
| Narrative Engine | `src/narrative` | 🔜 Coming |
| Audit Report Generator | `src/reporter` | 🔜 Coming |

## Built On

This product is the practical application of the [SAP Agent Governance Patterns](https://github.com/goismael/sap-agent-governance) library:

- **P001** — Permission scoping identifies agent-posted documents
- **P002** — Approval records form the authorization evidence chain
- **P003** — Agent action and reasoning logs are the primary evidence source
- **P004** — Failure and recovery events are included in audit narratives

## Tech Stack

- **Language:** Python 3.11+
- **LLM:** Google Gemini 2.5 Flash via Langchain
- **SAP Connectivity:** OData REST APIs (supported pathway)
- **Storage:** Azure Monitor Log Analytics / local JSON for POC
- **Orchestration:** Microsoft Agent Framework

## Getting Started

```bash
# Clone the repo
git clone https://github.com/goismael/sap-audit-agent.git
cd sap-audit-agent

# Install dependencies
pip install -r requirements.txt

# Configure your SAP connection
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your SAP credentials

# Run the collector
python -m src.collector.main
```

## Author

**Ismael** — Senior Intelligent Automation & AI Engineer
7+ years enterprise AI and automation | SAP S/4HANA | Microsoft Azure | Multi-agent systems

Companion library: [SAP Agent Governance Patterns](https://goismael.github.io/sap-agent-governance)

## License

MIT License
