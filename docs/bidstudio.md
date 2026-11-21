# BidStudio: tender analysis, technical and commercial proposals

*Analyze. Design. Price. Propose. Verify.*

BidStudio is part of moa7amed-queue (since 0.7.0). Give it a tender package (RFP, RFQ,
scope of work, addenda, clarifications, vendor quotations) and ask for the analysis, the
technical proposal, the commercial proposal, the complete package, or a review of an
existing proposal. It reads every document, traces every requirement to its source,
builds the compliance matrix, the clarification register and a bid/no-bid
recommendation, designs the solution through the planning engine, estimates the work,
prices it with a deterministic engine, writes the proposals (DOCX, PDF) and the
workbooks (XLSX), reviews the package, and says what is verified, assumed, pending or
blocked.

**It never submits, sends or publishes a bid.** It never invents a requirement, a
company credential, a reference project, a certification or a vendor price.

## Contents

- [Asking for it](#asking-for-it)
- [Modes](#modes)
- [Documents and provenance](#documents-and-provenance)
- [Requirements](#requirements)
- [Compliance matrix](#compliance-matrix)
- [Clarifications and addenda](#clarifications-and-addenda)
- [Bid / no-bid](#bid--no-bid)
- [Solution and estimate](#solution-and-estimate)
- [Pricing](#pricing)
- [Documents BidStudio writes](#documents-bidstudio-writes)
- [Review and submission checklist](#review-and-submission-checklist)
- [Approvals](#approvals)
- [Company profiles](#company-profiles)
- [Queue integration](#queue-integration)
- [Confidentiality](#confidentiality)
- [Commands](#commands)
- [Demonstration](#demonstration)
- [Limitations](#limitations)

## Asking for it

In a Claude Code session, with the tender files at hand:

```text
Draft our technical and commercial offer for this tender.   (attach or name the files)
Analyze this tender.
Check our price quotation against the RFP before we submit.
Revise the proposal to reflect this clarification.
جهز العرض الفني والمالي لهذه المناقصة
```

Claude starts the `bidstudio` skill, which first checks the request is tender work
(`moa7amed-queue bid intent`): "what is an RFP?" stays a conversation. A request queued
while Claude is busy (`queue: Draft the complete bid package...`) is saved as a bid
project rather than a task (`bid_hook_intake`). The skill, the capture hook and the
`bid` command group are the only entry points; there is no separate router.

## Modes

| Mode | Produces |
| :-- | :-- |
| analyze | what the tender asks for and its scope, the must-have requirements, the compliance matrix and the eligibility checks, the risks, what information is missing, and the questions to send the client; no proposal |
| technical | the above, the solution and the technical proposal |
| commercial | the estimate, the pricing model, the commercial proposal, BOQ |
| complete | the whole package: analysis, compliance matrix, technical and commercial proposals, BOQ/BOM, delivery plan, risks, clarifications, submission checklist |
| review | existing proposals checked against the RFP |

## Documents and provenance

```bash
moa7amed-queue bid doc b1 RFP.pdf --type rfp --version 1.0 --date 2026-10-01
moa7amed-queue bid doc b1 Addendum-1.pdf --type addendum --amends D1 --date 2026-11-05
moa7amed-queue bid doc b1 Quotation-Q7781.xlsx --type quotation
```

Each document gets an id (D1...), its SHA-256, type, version, date, source and the
relation to others (`--amends` for an addendum changing clauses, `--supersedes` for a
full replacement). Files are copied into the queue's data folder and their text is
extracted there:

| Format | Read with |
| :-- | :-- |
| PDF | pypdf, PyMuPDF or pdftotext, whichever is installed; page by page with page markers |
| DOCX | built-in reader (headings and tables kept) |
| XLSX | built-in reader (every sheet, row by row, formulas with their values) |
| TXT, Markdown, CSV | as text (UTF-8, Windows Arabic or Latin-1) |
| other | recorded as a limitation |

Pages without a text layer (scans, images) are never skipped silently: the document is
marked "partly read" with the page numbers, `bid pages` renders them to images (PyMuPDF),
Claude reads them, and `bid read` records their text with the page. There is no OCR
engine built in. A file that is not there is recorded as missing.

## Requirements

Every requirement keeps the tender's own wording and its source:

```json
{"id": "REQ-002", "class": "EXPLICIT_REQUIREMENT", "category": "certification", "mandatory": true,
 "text": "The bidder must hold a valid ISO/IEC 27001 certificate.",
 "source": {"doc": "D1", "section": "2.2", "page": 2}}
```

- The engine checks that an explicit requirement's text is in its document (spacing,
  quotes, case and Arabic spelling variants ignored; `...` skips words). A paraphrase or
  an invented requirement is a BLOCKER.
- Classes: EXPLICIT_REQUIREMENT, VERIFIED_SUPPORTING_EVIDENCE, PROPOSED_SOLUTION,
  ASSUMPTION, UNKNOWN, CONFLICTING. Categories cover submission, eligibility,
  certifications, registrations, scope, functional, non-functional, security,
  infrastructure, integration, delivery, training, warranty, SLA, support, payment,
  performance guarantees, penalties, contractual constraints, evaluation, local content,
  forms.
- Sections of the bid are merged: list entries with an id, title or name (proposal
  sections, pricing items, components) are updated or added one by one, so a stage that
  sends one section keeps the others; `"remove": true` on an entry drops it.
- Requirements are never deleted or edited in place: a changed clause becomes a new
  requirement that supersedes the old one, with the addendum as its source. Correcting
  an extraction mistake must say so (`"correction": "<why>"`).

## Compliance matrix

| Column | |
| :-- | :-- |
| Requirement ID, RFP section/page, requirement, mandatory, category | from the requirement |
| Status | COMPLIANT, PARTIALLY_COMPLIANT, NON_COMPLIANT, PENDING_VERIFICATION, NOT_APPLICABLE |
| Proposed response, evidence, proposal section, owner, risk, clarification | the answer |

Checked by the engine:

- COMPLIANT needs a response, evidence and the proposal section; a response that merely
  repeats the requirement is rejected.
- A claim about the company (certificate, reference, staff, product, partnership) must
  cite an approved item of the company profile with its evidence (`C-...`); a missing,
  unapproved or expired item is a BLOCKER or HIGH finding. Presenting something as an
  existing capability without such an item is a BLOCKER.
- Mandatory requirements not met, still open, or partly met where eligibility is at
  stake are listed separately as disqualifying conditions.

## Clarifications and addenda

The clarification register holds the question id, related requirements, question,
reason, technical and pricing impact, priority, status, the client's response and the
updated assumption. When an addendum arrives it is added with `--amends`; every
requirement from the amended document is flagged until it has been checked against the
addendum (`"checked_against"`) or superseded. Every change of the bid is a revision
(`bid revisions b1`); revisions are immutable (a database trigger refuses changes).

## Bid / no-bid

Eleven factors (technical capability, delivery feasibility, partner capabilities,
certifications, licensing, timeline, financial exposure, contractual risk, resources,
support obligations, competitiveness) rated 1 to 5 with their evidence, or left unknown.
The engine recommends BID, BID_WITH_CONDITIONS, NO_BID or INSUFFICIENT_INFORMATION: a
mandatory eligibility, certification or registration that is not met means NO_BID unless
conditions (a certified partner, say) are recorded; more than half the factors unknown
means INSUFFICIENT_INFORMATION. It is a recommendation; the decision is yours.

## Solution and estimate

The bid has a linked project plan of the same id: users, workload, data, availability,
security, hosting, CPU, RAM, storage, network and licensing go through the planning
engine with their evidence classes, options are checked against the target, and the
decision is approved there ([planning.md](planning.md)). Unknown hardware is described
as labelled scenarios; capacity figures are estimates.

The work breakdown gives each package's roles three-point effort (person-days) with its
basis (historical, benchmark or heuristic) and confidence. The engine computes the PERT
expectation per role, optimistic/baseline/conservative totals and a schedule from the
dependencies; a schedule beyond the delivery period is a review finding.

## Pricing

All arithmetic is done by `bid_pricing.py` with decimal numbers, and the workbook carries
formulas that reproduce it; a model never does the sums.

| Figure | Definition |
| :-- | :-- |
| COST | quantity x unit cost x exchange rate, per line |
| MARKUP | (selling - cost) / cost; selling = cost x (1 + markup) |
| GROSS_MARGIN | (selling - cost) / selling; selling = cost / (1 - margin) |
| CONTINGENCY | a share of the cost, priced like the lines it protects and built into their prices |
| SELLING_PRICE | sum of the lines, less any discount (before tax) |
| TAX | rate x taxable selling price after discount |
| TOTAL_BID_PRICE | selling price + tax |

- Every line's cost has a source: a **quotation** (a document of the bid), the company's
  **rate card**, a labelled **planning estimate**, or **pending**. Pending lines keep the
  model incomplete and prices cannot be approved; estimates and unconfirmed exchange
  rates are flagged everywhere.
- Unit prices are rounded first and line totals derived from them, so quantity x unit
  price = line total in the BOQ, to the cent.
- Labour lines come from the estimate at the rate card (`bid labour`).
- `bid price b1 --scenarios`: sensitivity to exchange rates (+/-5%), effort (+20%,
  -10%) and licence costs (+10%).
- A minimum margin can be set; a lower result is a warning, never a reason to drop scope.

## Documents BidStudio writes

| Document | Formats | Envelope |
| :-- | :-- | :-- |
| Technical proposal | DOCX, PDF | technical (no prices) |
| Commercial proposal | DOCX, PDF | commercial (selling prices only) |
| Executive summary | DOCX, PDF | internal |
| Pricing model | XLSX (Assumptions, Lines, Summary, Scenarios; formulas) | internal, RESTRICTED |
| BOQ | XLSX (formulas) | commercial |
| Compliance matrix | XLSX: current requirements, status, response, proposal section | technical |
| Compliance matrix, internal | XLSX: plus evidence references, owner, risk, clarifications, superseded requirements, disqualifying conditions | internal |
| Clarification register | XLSX | internal |

- DOCX and XLSX are written by the plugin itself (no Office or extra package needed):
  headings, tables with repeated header rows, bullets, title page, header with the tender
  reference, page numbers, right-to-left paragraphs and sheets for Arabic.
- PDF is printed by a browser through Playwright when it is installed (the same engine
  Replica uses); without it the PDF is reported as unavailable and the DOCX can be saved
  as PDF from Word.
- After writing, every file is read back and checked: required sections present (from
  the tender's mandated structure), no price or currency amount in the technical
  envelope, no cost, markup, margin, contingency or supplier figure in a customer
  document. A violation is a BLOCKER on the output.
- Drafts carry "DRAFT - not approved for release" in the header; final documents need
  approved prices and the release approval.

Verified on this machine (2026-11-20): the generated DOCX open in Microsoft Word and in
python-docx, the XLSX in openpyxl; Word renders the Arabic commercial proposal right to
left with its tables, header and page numbers; the PDFs printed by Chrome contain the
Arabic text in shaped form; the workbook formulas give the engine's figures (checked by
an independent evaluator in the tests).

## Review and submission checklist

`bid outputs b1` lists the generated files and their check results; `bid outputs b1 --text
technical` (commercial, boq, compliance, technical/pdf...) prints a document's text, so a
reviewing session can read what was actually written.

`bid review b1` lists verified defects (BLOCKER, HIGH, MEDIUM, LOW): untraced or
superseded requirements, unanswered mandatory items, restated answers, unsupported or
expired claims, disqualifying conditions, schedule beyond the delivery period, pending
and estimated prices, assumed exchange rates, BOQ not reconciling with the model, output
problems, missing forms and signatures, missing approvals. Findings recorded by a reader
(Claude reviewing as an evaluator) are listed as advisory, never as verified.
`bid checklist b1` is the submission checklist; its last line, "submitted", stays open:
submitting is yours. BidStudio never claims legal or contractual compliance.

## Approvals

Required, and only from you (`bid approve b1 <gate> --yes`): architecture, compliance
commitments, licensing, pricing, contract exceptions, release. Each approval records a
hash of what was approved; a later change shows the gate as "changed since approval".
Ordinary analysis, drafting, recalculation and formatting need no approval.

## Company profiles

Profiles live in the queue's data directory (`companies/<id>/profile.json`), outside
every project and outside the plugin. Items (`C-...`) carry the claim, its evidence and
whether it is approved and until when. Rate cards (`ratecard.json`) are kept beside them
and never written into a customer document. Several companies can be kept apart; nothing
company-specific ships with the plugin.

```bash
moa7amed-queue bid company new acme --name "Acme Systems"
moa7amed-queue bid company apply acme --file profile-items.json
moa7amed-queue bid company ratecard acme --file ratecard.json
```

## Queue integration

`bid queue b1` turns the stages into ordinary queue tasks with dependencies (source
`bid:b1`), once: RFP intake -> compliance -> clarifications and bid/no-bid -> solution
architecture -> estimate -> pricing -> technical draft -> commercial draft -> independent
review. Each task reads its stage's skill and records its results through `moa7amed-queue
bid` commands; its session may run only those commands and the `project` commands
(`--allowedTools`), and use the bid's folder. Routing, workflows, verification, retries
and crash recovery are the queue's.

## Confidentiality

- Tender documents, extracted text, outputs and pricing stay in the queue's data folder
  on your machine. Nothing is sent anywhere except what Claude reads in your sessions and
  tasks, under your Claude Code login and its data policies.
- No telemetry; no upload; no automatic submission.
- Internal pricing and rate cards are kept out of customer documents by construction and
  by the post-generation check.
- Bids are separate projects; a bid's outputs contain only its own documents and data.

## Commands

```text
moa7amed-queue bid intent TEXT | tools
moa7amed-queue bid new [ID] --request T [--mode M] [--company C] | list | show ID [--section S] | config ID ...
moa7amed-queue bid doc ID [FILE] [--type T] [--version V] [--date D] [--amends D] [--supersedes D]
moa7amed-queue bid text ID DOC | pages ID DOC [--pages 3,4] | read ID DOC PAGE --data TEXT
moa7amed-queue bid apply ID --data JSON | --file F
moa7amed-queue bid check | decision | estimate | labour | price [--scenarios] | review | checklist | revisions | status ID
moa7amed-queue bid approve ID GATE [--yes]
moa7amed-queue bid generate ID [--kind K ...] [--final] [--no-pdf]
moa7amed-queue bid outputs ID [--text KIND[/FORMAT]]
moa7amed-queue bid queue ID
moa7amed-queue bid company new|show|apply|ratecard|list ...
```

Skills: `bidstudio` (Claude may start it), `bid-analyze`, `bid-compliance`,
`bid-clarifications`, `bid-technical`, `bid-estimation`, `bid-pricing`, `bid-commercial`,
`bid-review` (you start them), `bid-status` (read-only).

## Demonstration

The synthetic tender package in `tests/fixtures/bid` (an English software RFP, an
addendum, a vendor quotation, an Arabic RFP, a hardware tender with a scanned page, a
licensing and support RFQ; all invented) is used by the tests and by
`tests/bid_live.py`, which runs stages through the queue with the real CLI.

Last full run (2026-11-20, Claude Code 2.1.329): a fictitious company profile with two
approved items (registration, one reference) and a rate card; the RFP, its addendum and a
USD vendor quotation; all nine stages queued and run with the real CLI, each on its first
attempt (Sonnet, the architecture stage on Opus).

| Stage | Result |
| :-- | :-- |
| Intake | 31 requirements recorded with sources; every quote verified against its document; the addendum's 90-day delivery and 500/80 users supersede the original clauses; the quotation's USD prices against the SAR requirement recorded as CONFLICTING |
| Compliance | 16 COMPLIANT, 4 partly, 8 pending verification, 1 not applicable; ISO 27001 left PENDING_VERIFICATION because the profile holds no certificate; one reference out of two required: PARTIALLY_COMPLIANT |
| Clarifications, bid/no-bid | 12 questions; BID_WITH_CONDITIONS, certifications critical |
| Architecture (Opus) | three labelled hosting scenarios (assumed), capacity estimates, five decisions in the linked plan, waiting for approval and for the hosting answer |
| Estimate | 15 work packages, 147 / 212.5 / 320 person-days, all marked heuristic; the conservative schedule exceeds the delivery period and is recorded as a risk |
| Pricing | SAR 369,451.00 selling, 19.6% gross margin; status incomplete: licences and hosting pending (no quotations), so prices cannot be approved |
| Drafts | 21 technical sections (16-page PDF), commercial proposal (4 pages), BOQ, workbooks; no check problems |
| Review | verified blockers (eligibility evidence, Form A unsigned, pending prices, approvals) and 15 advisory findings (penalty base, RTO against the 99.5% availability, schedule slack, VAT presentation...) |

Cost: $5.11 reported by Claude Code for the run, including one re-run of the technical
drafting stage. The run found three defects that were fixed before release: a later
stage's partial update replaced the whole list of proposal sections (sections are now
merged), the customer compliance matrix showed internal evidence references (there is now
a customer and an internal version), and the reviewing task could not read the generated
documents (`bid outputs --text`). An earlier attempt failed on permissions because task
prompts pointed to skill files outside the project; the stage instructions are now part
of the task prompt.

## Limitations

- No OCR engine: scanned pages are read visually by Claude and recorded by hand.
- Requirement extraction, the solution, the estimate figures and the proposal text are
  Claude's work; the engine checks traceability, evidence, arithmetic and separation,
  not the quality of the writing.
- The quote check confirms the wording is in the cited document, not that the page or
  section number is right.
- PDF needs Playwright with a browser; diagrams are not generated (tables are).
- Legal and contractual review needs your specialists; BidStudio flags, it does not
  certify.
