# Offline eval report (submission-sample smoke run)

Reduced scope for this session's deadline: the 3 submission samples, not the full 28-document grid. See docs/progress.md's scope-cut notes.

## 01-clean-correct.pdf (V1-C0)
- run_id: `b575c99b69e549ed97b69fe40b3ff34c`
- outcome: `auto_approved` (acceptable: ['auto_approve'])
  -> OK
- LLM calls: 3, fallback used: False
- Field verdicts (ours vs expected):
  - consignee: ours='match' expected='match' [OK]
  - hs_code: ours='match' expected='match' [OK]
  - port_of_loading: ours='match' expected='match' [OK]
  - port_of_discharge: ours='match' expected='match' [OK]
  - incoterms: ours='match' expected='match' [OK]
  - goods_description: ours='match' expected='match' [OK]
  - gross_weight: ours='match' expected='match' [OK]
  - invoice_number: ours='match' expected='match' [OK]

## 02-clean-two-errors.pdf (E1E4-C0)
- run_id: `5a3d538a6ac54320a0dc97b75561926c`
- outcome: `amendment_requested` (acceptable: ['amendment_request', 'human_review'])
  -> OK
- LLM calls: 6, fallback used: True
- Field verdicts (ours vs expected):
  - consignee: ours='match' expected='match' [OK]
  - hs_code: ours='mismatch' expected='mismatch' [OK]
  - port_of_loading: ours='match' expected='match' [OK]
  - port_of_discharge: ours='match' expected='match' [OK]
  - incoterms: ours='match' expected='match' [OK]
  - goods_description: ours='match' expected='match' [OK]
  - gross_weight: ours='mismatch' expected='mismatch' [OK]
  - invoice_number: ours='match' expected='match' [OK]

## 03-messy-scan.pdf (V2-C3)
- run_id: `608725a3850f442e8bf2c33acc85a479`
- outcome: `auto_approved` (acceptable: ['auto_approve', 'human_review'])
  -> OK
- LLM calls: 6, fallback used: True
- Field verdicts (ours vs expected):
  - consignee: ours='match' expected='match' [OK]
  - hs_code: ours='match' expected='match' [OK]
  - port_of_loading: ours='match' expected='match' [OK]
  - port_of_discharge: ours='match' expected='match' [OK]
  - incoterms: ours='match' expected='match' [OK]
  - goods_description: ours='match' expected='match' [OK]
  - gross_weight: ours='match' expected='match' [OK]
  - invoice_number: ours='match' expected='match' [OK]

## Summary
- Field verdict accuracy: 24/24
- Wrong auto-approvals: 0
- No wrong auto-approvals.
