# Part 2 decisions

Every real design choice in Part 2, in the format `.kiro/steering/decision-protocol.md` asks for:
Decision, Options considered, Chosen, Why, Trade-off accepted, Date.

Section 1 holds the decisions taken during the design session, with the full reasoning. Section 2
holds the owner decisions that were already settled while writing the requirements; for those,
only the alternatives actually recorded at the time are listed.

---

## 1. Design-session decisions (10 Oct 2026)

### D1. How the screen receives live updates

- **Options considered**
  - A: Polling. Refetch the shipment list every 2 s while anything is processing, stop when nothing
    is. `frontend/src/App.jsx` already does exactly this for the Part 1 run list.
  - B: Server-Sent Events. A streaming `GET /api/events` endpoint plus `EventSource` in the browser.
  - C: WebSocket.
- **Chosen**: A, polling.
- **Why**: R8.1 asks for updates "without a page reload", not sub-second push, and R8.2's
  "N of M documents done" counter reads fine at a 2 s refresh. B needs a thread-safe queue between
  the `BackgroundTasks` pipeline and a streaming generator, a response that holds a worker for the
  life of the page, client reconnect handling, and a new testing approach -- roughly 45-60 minutes
  of a 3-4 hour budget, plus a new silent-failure mode. C adds a bidirectional channel nothing
  needs. A reuses a pattern already working in the repo and costs almost nothing.
- **Trade-off accepted**: up to 2 seconds of visible lag, and one request every 2 seconds while a
  shipment is processing.
- **Production note**: polling every open browser does not scale. Real answer is SSE or WebSocket
  from a pub/sub, or a managed service, once there is more than one operator.

### D2. How the inbox watcher runs

- **Options considered**
  - A: A daemon thread inside the FastAPI `lifespan`, scanning the folder every 2 s.
  - B: A separate process, `python -m app.cli watch`, run in its own terminal.
  - C: An operator-clicked "Scan inbox" button.
  - D: The `watchdog` library for real filesystem events.
- **Chosen**: the scan logic as a plain function `scan_inbox(conn)` that knows nothing about its
  caller; B as the documented runner; A also calling the same function so the backend alone is
  enough to demonstrate.
- **Why**: C breaks R1.1 outright, which requires processing to start with no operator action.
  D is a new dependency, ruled out by the no-new-infrastructure constraint. Between A and B, B has
  the same shape as production -- ingestion is a worker, not part of the web app -- which is what
  makes the scaling answer concrete: replace the loop with a queue consumer and `scan_inbox` does
  not change. A is kept on top because it costs about five lines and means a reviewer who starts
  only the backend still sees the trigger fire.
- **Trade-off accepted**: single-process dedupe (a `SELECT` then `INSERT` on `message_id`) that a
  second watcher process would race; a directory listing that is O(total files) because nothing
  archives processed emails; a watcher thread whose death shows up only in the log.
- **Production note**: SES or Graph push notification, or an IMAP IDLE poller, feeding a real queue
  (SQS, or Celery/RQ on Redis). The `UNIQUE(message_id)` index becomes the actual dedupe guarantee,
  caught as a constraint violation rather than pre-checked. A visibility timeout redelivers a
  crashed worker's message instead of losing it.

### D3. Where Doc_Type is stored

- **Options considered**
  - A: The existing `documents.doc_type` column, which Part 1 never writes, so it is always NULL.
  - B: A new column on the Part 2 `shipment_documents` table, leaving the Part 1 column dead.
- **Chosen**: A.
- **Why**: the column exists for precisely this, and `v_documents` and `v_fields` already select
  it, so filling it makes the query layer able to answer document-type questions with no view
  change. Checked before deciding: no Part 1 test asserts the column is NULL, and
  `backend/tests/query_fixture.py` already builds its own `documents` table with
  `doc_type TEXT NOT NULL` populated -- so the tests already assume this column has a value.
  B duplicates a fact that already has a home and leaves `v_documents.doc_type` NULL forever.
- **Trade-off accepted**: Part 2 writes into a Part 1 table. No DDL change and no existing row is
  modified, but R12.6's "new tables only" gets one deliberate exception. Old Part 1 rows stay NULL
  while Part 2 rows carry a value, so the column means two things depending on when the row was
  created.
- **Production note**: make the column NOT NULL with a check constraint on the allowed values and
  backfill the old rows. A mixed NULL/populated column is what quietly breaks a report later.

### D4. How the sample bill of lading and packing list are produced

- **Options considered**
  - A: Extend `samples/` with real `render_bol()` and `render_packing_list()` layouts beside the
    existing `render_invoice()`, plus an `.eml` builder, all driven from the same ground-truth
    objects in `samples/shipments.py`.
  - B: One parameterised renderer: reuse the invoice layout, change the heading and the field set.
  - C: Hand-make three PDFs once and commit them.
- **Chosen**: A.
- **Why**: `samples/` renders commercial invoices only today, so without new document types the
  cross-document checker has nothing to run on and "multi-doc cross-validation actually fires"
  (25% of the Part 2 score) cannot be demonstrated. C has no ground-truth file, so the end-to-end
  test would assert against strings typed by hand and would drift the moment a field changed.
  B is about a third of the cost of A and gives the same test quality, but all three documents
  then look alike on screen and in the demo, which a reviewer who knows trade documents will spot.
  A keeps the Part 1 pattern intact: ground truth declared once in data, documents and answer
  files both built from it, fixed seed, one command to regenerate.
- **Trade-off accepted**: two new fpdf2 page layouts, roughly 45-60 minutes of a 3-4 hour budget,
  spent on sample data rather than on a graded feature. If the clock runs short this is the first
  thing to downgrade to option B -- not the cross-checker and not the four screen states.

### D5. Expected fields for bill of lading and packing list in `acme.yaml` (R15.5 sign-off)

- **Options considered**
  - A: Full, realistic field sets. BOL: consignee, hs_code, port_of_loading, port_of_discharge,
    gross_weight. Packing list: consignee, hs_code, gross_weight, invoice_number.
  - B: Minimal sets, only the four cross-checked fields where each applies.
  - C: Leave `acme.yaml` untouched and give `crosscheck.py` its own hard-coded carrier map,
    comparing extracted values and extraction confidence directly instead of verdicts.
- **Chosen**: A, with explicit owner sign-off under R15.5.
- **Why**: today those two document types list no expected fields, so `allowed_outcomes` permits
  only `human_review` for them. That is a gap in the data, not a safety decision -- nobody had
  written rules for them yet. R2.5 requires filling it, and the cross-document checker depends on
  it: it only compares documents whose Doc_Type lists the field, so with no entries every
  cross-check returns `not_applicable` and cross-document validation never fires at all. C avoids
  touching Part 1 but breaks R2.5, leaves both types permanently in human review, moves
  per-customer knowledge from data into code (the opposite of Part 1's "rules are data"), and
  produces a screen that contradicts itself: a shipment reading `auto_approve` while two of its
  three documents individually read human review. B is the same change two fields smaller, so it
  pays the same explanation cost without escaping it, and a BOL that doesn't check its own ports
  looks arbitrary.
- **Trade-off accepted**: after this change a clean bill of lading or packing list can be
  recommended for approval on its own, where before it always went to a person. This is a genuine
  widening at document level and is why R15.5 required asking.
- **What is *not* changed**: `trust/guardrails.py` is untouched and its 6561-case sweep still
  passes. The widening comes entirely from feeding it a fuller rule set, not from relaxing the
  rule. A second exhaustive sweep is added at shipment level on top.

### D6. Process: scope and shape of `tasks.md`, and when this file is written

- **Chosen**: (a) `tasks.md` includes the non-code deliverables -- PRD, README, teaching docs, this
  file, demo video -- with ownership marked per task, rather than code only; (b) about 14 tasks,
  each one module together with its tests, rather than ~8 phase-sized or ~25 split tasks;
  (c) this file is written now and appended after each later decision, as the protocol asks.
- **Why**: the steering requires a teaching doc for everything built, which here means five new
  docs plus an architecture-overview update -- substantial work that quietly does not happen unless
  it is a checkbox. Module-plus-tests granularity means both exhaustive guardrail sweeps sit in
  tasks that are not finished until their property test passes, instead of being sub-bullets that
  erode under time pressure. Writing this file now keeps the rejected options, which are the part
  that is worth anything later and the part that fades fastest.
- **Trade-off accepted**: the last phase of `tasks.md` cannot run unattended, because some
  artifacts need the owner; and a decision reversed later means editing this file.

---

## 2. Owner decisions carried over from `requirements.md`

Settled while writing the requirements. Recorded here because `requirements.md` says they belong in
this file. Where the alternatives were captured at the time they are listed; where they were not,
that is said rather than invented.

| Decision | Chosen | Alternative recorded | Trade-off accepted |
|---|---|---|---|
| Sending | Simulated outbox only; no real SMTP | - | Nothing actually reaches SU; delivery is never exercised |
| Cross-document fields | consignee, hs_code, gross_weight, invoice_number, compared in code, zero model calls, no unit conversion (different units = inconsistent). Results: consistent, inconsistent, uncertain, not_applicable. Document order irrelevant | - | A genuinely equivalent weight in different units reads as a discrepancy |
| Inbox | Watched folder of `.eml` files, deduplicated by Message-ID (content hash if absent); a restart reprocesses nothing | Real mailbox / SMTP, explicitly out of scope | No retry, ordering or delivery guarantee |
| Doc_Type source | The Extractor's existing `document_type` output, no extra model call; missing or unsupported = uncertain, so human_review | A dedicated classification call | Type accuracy is only as good as the existing extraction prompt |
| Part 2 storage | New tables for emails, shipments, cross-checks, drafts, outbox and review state; Part 1 tables, columns and data not altered | - | Some duplication between Part 1 and Part 2 shapes; see D3 for the one exception |
| Shipment decision and draft | Both settled in code from stored results; shipment-level model calls = 0 | Let the Router decide at shipment level, as the assignment brief's wording suggests | No model-written nuance in the reply; the template must be good enough to send with one edit |
| Query layer | One new read-only view for pending shipments, added to the allow-list and the schema doc; existing views unchanged | - | The new view is the only Part 2 surface the query layer can reach |
| Resubmission linking | By a `SHP-` reference in the email subject, first match used; no reference = new first-cycle shipment | - | A typo'd or missing reference silently starts a new shipment instead of linking |
| Single-document shipments | Allowed, and can be auto-approved (recommended for approval) if everything matches; not treated as incomplete | An "incomplete shipment" status with a per-customer required-document set, explicitly out of scope | A shipment missing a document it should have had is not flagged as missing |
| Resubmission validation | Over the Effective_Document_Set: the new email's documents plus the latest earlier document of each Doc_Type the new email lacks. Carried-over documents are not reprocessed and appear at most once | - | A carried-over document's result can be stale relative to a re-read of the same file |
| `auto_approve` meaning | "Recommended for approval" only; nothing is sent without the operator clicking Send | - | The label needs explaining on screen, since the word suggests more than it means |
| Customer and reference source | Both read from the email subject, in any order | Map the sender domain to the customer -- named as the production alternative | Subjects are inconsistent and forgeable; a typo loses the link |
| Uncertain items | No operator action in Part 2; they stay uncertain and show under "needs your check", and are excluded from the generated text for SU | Let the operator confirm or override an uncertain value, explicitly out of scope | An operator who can see the value is right still cannot clear it |
