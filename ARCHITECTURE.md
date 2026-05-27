# Architecture

This document describes the technical architecture of the SAP Audit Preparation Agent — how the four pipeline layers work, how data flows between them, and the key design decisions behind each component.

---

## Overview

The agent is a four-layer pipeline that runs alongside SAP S/4HANA financial close workflows. Each layer has a single responsibility and a clean interface to the next.

```
SAP S/4HANA
  OData APIs (supported pathway only)
        │
        ▼
┌─────────────────────────────────┐
│  Layer 1 — SAP Data Collector   │
│  src/collector/                 │
└─────────────────┬───────────────┘
                  │
        ┌─────────┴──────────┐
        │                    │
        ▼                    ▼
  SAP Evidence       P003 Agent Logs
  Records            P002 Approval Records
        │                    │
        └─────────┬──────────┘
                  ▼
┌─────────────────────────────────┐
│  Layer 2 — Evidence Correlator  │
│  src/correlator/                │
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│  Layer 3 — Narrative Engine     │
│  src/narrative/                 │
│  Gemini 2.5 Flash via Langchain │
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│  Layer 4 — Report Generator     │
│  src/reporter/                  │
│  Scored A–D · MD + JSON output  │
└─────────────────────────────────┘
```

---

## Layer 1 — SAP Data Collector

**Module:** `src/collector/`

### Responsibility

Pulls financial events from SAP S/4HANA and identifies which postings were made by AI agents versus human users.

### Key Components

| File | Purpose |
|---|---|
| `odata_client.py` | Authenticated OData REST client with pagination, retry logic, and error handling |
| `journal_entry_collector.py` | Collects journal entry line items; detects agent-posted documents |
| `evidence_store.py` | Persists collected records as NDJSON (POC) or Azure Monitor (production) |
| `main.py` | Orchestrates collection across all configured company codes |

### SAP OData Services

| Service | Data Collected |
|---|---|
| `API_JOURNALENTRYITEMBASIC_SRV` | Journal entry line items, amounts, GL accounts, posting dates |
| `API_GLACCOUNT_DOCUMENT_SRV` | GL-level document detail |
| `API_EXCHANGERATE_SRV` | Period-end FX exchange rates |
| `API_FISCALYEAR_SRV` | Posting period open/closed status |

Only supported SAP API pathways are used. No undocumented BAPIs or bulk RFC extractions.

### Agent Detection

The collector identifies agent-posted documents by matching the `CreatedByUser` field against a configurable registry of known agent service users (defined in `config.yaml` under `collection.agent_service_users`). This registry implements the P001 permission scoping pattern — service users follow the naming convention `SVC_AGENT_[CLASSIFICATION]_[DOMAIN]`.

### Collection Modes

- **Delta** — retrieves only records changed since the last run using `LastChangeDateTime` as the OData filter. Persists state between runs.
- **Full** — retrieves all records for the configured fiscal year and company codes.

### Output

Each collected record is a `SAPEvidenceRecord` (`src/common/models.py`) containing SAP event fields plus governance fields: `is_agent_posted` and `agent_session_reference`. The session reference is extracted from `DocumentReferenceID` — agent frameworks should write their `session_id` to this field when calling `BAPI_ACC_DOCUMENT_POST`.

---

## Layer 2 — Evidence Correlator

**Module:** `src/correlator/`

### Responsibility

Joins SAP evidence records with agent action logs, agent reasoning logs, and approval records to produce complete, scored, gap-analyzed evidence packages.

### The Four-Way Join

```
SAP Document Number
      ↕  (via sap_document_number in action log)
Agent Action Log (P003 Layer 2)
      ↕  (via sequence_number proximity in same session)
Agent Reasoning Log (P003 Layer 3)
      ↕  (via approval_id in action log)
Approval Record (P002)
```

The join key is `session_id` — present in every P003 log event and stored in the SAP document's `DocumentReferenceID` field. This creates a queryable link between SAP documents and agent reasoning without requiring custom SAP development.

### Completeness Scoring

Each evidence package receives a completeness score (0–100%) based on which layers are present:

| Evidence Layer | Weight |
|---|---|
| SAP posting record | 25% |
| Agent action log | 25% |
| Agent reasoning log | 25% |
| Approval record (Tier 3 actions only) | 25% |

Autonomous and Notify-tier actions do not require an approval record for a 100% score. Only Approve-tier actions (Tier 3) require one.

### Gap Classification

| Gap Type | Audit Risk | Description |
|---|---|---|
| `no_approval_record` | Critical | Tier 3 action with no approval — potential SOX control failure |
| `hash_mismatch` | Critical | Approval parameters differ from execution parameters |
| `no_agent_session` | High | Agent-posted document with no session reference |
| `no_reasoning_log` | High | Action log found but no reasoning log in session |
| `missing_action_log` | High | SAP document exists but no agent action log found |

### Hash Chain Verification

Approval records include a SHA-256 hash of the exact action parameters that were approved (from P002). The correlator verifies this hash against the action log parameters — any parameter change between authorization and execution produces a `hash_mismatch` gap.

### Log Source Interfaces

The correlator reads from two abstract interfaces:

- `AgentLogSource` — retrieves P003 action and reasoning logs
- `ApprovalSource` — retrieves P002 approval records

POC implementations (`LocalAgentLogSource`, `LocalApprovalSource`) read from local NDJSON/JSON files. Production implementations should query Azure Monitor Log Analytics and Azure Cosmos DB respectively.

### Output

A list of `EvidencePackage` objects (`src/common/models.py`), each containing all available evidence layers, the completeness score, and a list of `EvidenceGap` instances.

---

## Layer 3 — Narrative Engine

**Module:** `src/narrative/`

### Responsibility

Generates plain-language audit narratives from evidence packages using Google Gemini 2.5 Flash via Langchain. Answers the auditor's core question: *why did the agent do what it did, and who authorized it?*

### Model Choice

Gemini 2.5 Flash is used for three reasons:

- **Cost** — approximately 30x cheaper than GPT-4 Turbo for equivalent quality on structured financial text
- **Context window** — 1M tokens enables feeding an entire close cycle's evidence in a single call if needed
- **Quality** — instruction-following on structured output is production-grade for this use case

The model is configurable via `config.yaml`. Swapping to Claude or GPT-4 requires changing one line in `narrative_engine.py`.

### Prompt Strategy

Two prompt templates handle different document types:

**Agent-posted documents** — full narrative including:
- What was posted (document number, amount, GL account, company code)
- Which agent acted (agent ID, classification, BAPI called)
- Why it acted (decision point, data analyzed, alternatives rejected)
- Who authorized it (approver name, role, channel, hash verification)
- Evidence quality (completeness score, gaps if any)

**Human-posted documents** — brief confirmation narrative noting the human user, standard SAP authorization controls, and completeness.

Both templates enforce SOX audit terminology: *posted*, *authorized*, *reversed*, *reconciled*, *escalated*. The system prompt prohibits speculation — only confirmed evidence is included.

### Failure Handling

Narrative generation failures are caught per-document. A failed narrative produces a placeholder record noting the failure — the batch continues. One document failure does not stop the pipeline.

### Output

A list of `AuditNarrative` objects with `narrative_text`, `completeness_score`, `audit_ready` (bool), `has_critical_gaps` (bool), and generation metadata.

---

## Layer 4 — Report Generator

**Module:** `src/reporter/`

### Responsibility

Produces a structured period Audit Readiness Report from the generated narratives.

### Scoring Formula

```
Score = (Audit Ready % × 80) + (Hash Verified % × 20) − (Critical Gaps × 5)
```

Capped at 0–100.

### Grade Scale

| Grade | Score | Meaning |
|---|---|---|
| A | 95–100 | Audit Ready |
| B | 80–94 | Minor Remediation Required |
| C | 60–79 | Moderate Remediation Required |
| D | 0–59 | Significant Issues — Do Not Submit |

### Output Formats

- **Markdown** — human-readable report with executive summary, statistics, critical items section, and all narratives. Suitable for PDF conversion.
- **JSON** — machine-readable summary with score, grade, document-level metadata, and gap details. Suitable for dashboards and downstream processing.

---

## Common Models

**Module:** `src/common/`

Shared data models used across all layers:

| Model | Description |
|---|---|
| `SAPEvidenceRecord` | A single financial event from SAP — produced by Layer 1 |
| `EvidencePackage` | Complete evidence package for one document — produced by Layer 2 |
| `EvidenceGap` | A missing or failed evidence link with audit risk classification |
| `CollectionState` | Delta collection state persisted between runs |
| `EventType` | Enum: `JOURNAL_ENTRY_POSTING`, `DOCUMENT_REVERSAL`, `FX_REVALUATION`, etc. |
| `EvidenceGapType` | Enum of all gap types with audit risk levels |

---

## End-to-End Pipeline

**Module:** `src/pipeline.py`

Wires all four layers together. Two modes:

```bash
# Synthetic enterprise data (no SAP connection required)
python -m src.pipeline --synthetic

# Live SAP collection
python -m src.pipeline
```

Synthetic mode generates a realistic enterprise dataset (configurable document count, 4 company codes, 9 scenario types including reversals, FX revaluation, intercompany eliminations, and deliberate control gaps).

---

## Governance Foundation

This agent is built on the [SAP Agent Governance Patterns](https://github.com/goismael/sap-agent-governance) library. The patterns are prerequisites — the audit agent reads their outputs as evidence sources.

| Pattern | Role in This Agent |
|---|---|
| P001 — Permission Scoping | Defines the service user registry used for agent detection |
| P002 — Approval Gates | Source of approval records and hash-verified authorization chain |
| P003 — Audit Logging | Source of agent action logs and reasoning logs |
| P004 — Failure Handling | Recovery events included in audit narratives |

Without P001–P004 implemented in the underlying agent system, the Evidence Correlator will produce incomplete packages for all agent-posted documents.

---

## Storage

### POC (Default)

| Data | Storage | Location |
|---|---|---|
| SAP evidence records | Local NDJSON | `./output/evidence/` |
| Agent logs | Local NDJSON | `./output/evidence/agent_logs/` |
| Approval records | Local JSON | `./output/evidence/approvals/` |
| Narratives | Local JSONL | `./output/evidence/narratives/` |
| Reports | Local MD + JSON | `./output/evidence/reports/` |
| Collection state | Local JSON | `./output/evidence/collection_state.json` |

All output paths are excluded from version control via `.gitignore`.

### Production

| Data | Storage |
|---|---|
| Agent action and reasoning logs | Azure Monitor Log Analytics (immutable, 7-year retention) |
| Approval records | Azure Cosmos DB (strong consistency, hash-verified) |
| SAP evidence records | Azure Monitor or Azure Blob (append-only) |

---

## Configuration

All configuration lives in `config/config.yaml` (excluded from version control). Copy `config/config.example.yaml` and fill in your values. Environment variables are resolved automatically using `${VAR_NAME}` syntax.

Key configuration sections:

```yaml
sap:
  base_url:             # SAP S/4HANA system URL
  client:               # SAP client number
  username:             # Service user (type S, read-only)
  password:             # From environment variable

collection:
  company_codes:        # List of company codes to monitor
  fiscal_year:          # Target fiscal year
  agent_service_users:  # Known agent service users (P001 registry)

llm:
  model:                # Gemini model name
  api_key:              # From environment variable
  temperature:          # 0.1 recommended for consistent audit narratives

storage:
  mode:                 # "local" or "azure"
  local_path:           # Output directory for POC mode
```

---

## Design Decisions

**Why OData only?**
SAP's API policy mandates supported pathways. Undocumented BAPIs and bulk RFC extractions create upgrade risk and are not supported in SAP BTP environments. OData v2/v4 services are the correct long-term approach.

**Why session_id in DocumentReferenceID?**
This field is queryable via OData without custom development. It creates the join key between SAP documents and agent logs without requiring ABAP extensions or SAP modifications.

**Why Gemini over other models?**
Cost at scale. A monthly close with 10,000 documents at 5 seconds per narrative would cost approximately $0.50 with Gemini 2.5 Flash versus $15+ with GPT-4 Turbo. The model is abstracted behind Langchain — it can be swapped without changing business logic.

**Why NDJSON for POC storage?**
Zero infrastructure, human-readable, appendable. Each record is a complete JSON object on its own line — trivial to load, filter, and inspect without a database. Production swap to Azure Monitor requires only replacing the sink class.

**Why separate agent detection from collection?**
Agent service users change over time as new agents are deployed. The registry in `config.yaml` decouples detection logic from collection logic — adding a new agent service user requires a config change, not a code change.
