# Shop-floor Execution System (MES) with Genealogy

A small, industry-shaped MES for one discrete line. Demonstrates ISA-95 hierarchy, strict order state machine, OEE with loss Pareto, immutable genealogy, and recall.

## Why this exists
ERP does not run the shift. The PLC does not know the customer order. MES is the layer in between — where ISA-95, OEE, traceability, and (from 2028–29 for EU electronics) digital product passport data actually live.

## What “done” looks like
- Planner releases Order 10482 for 5,000 units.
- Operator HMI shows the order, target rate, and recipe. Large touch targets, no hover.
- Micro-stops coded (JAM, NOMAT, QUAL, CHANGE) instead of generic “down.”
- At ship-out, one query answers: “This serial used resin lot R-19, ran on press 3, second shift, and failed a cavity check that was later waived by QE.”
- Scrap is a reason code, not a deleted row.
- ERP boundary: completions and scrap exported as boring CSV.

## Knowledge proved
- ISA-95 shape: site → area → line → cell → order → operation → material lot
- OEE the way a plant manager argues it: Availability × Performance × Quality + Pareto of loss codes
- Traceability and audit. Immutable history. No silent edits.
- Two users who do not share a UI: planner on a desk, operator on a glove-friendly screen

## Industry-ready bar
- Time is an industrial problem (seeded week of shifts, order can span midnight)
- Downtime reasons are a managed list, not free text
- Show an ERP boundary (CSV export). Do not rebuild SAP.
- Recall drill is the demo climax, not the OEE gauge

## Run it
```bash
pip install -r requirements.txt
python3 app.py
```
Then open http://localhost:5000

- Operator HMI: /operator
- Planner: /planner

Seeded with historical orders so OEE is not zeros, plus current order 10482.

**Demo recall:** Search `SN-10482-001` on the planner page. It used resin lot R-19, ran on Press 3, failed a cavity check later waived by QE.

## Interview line
“I can reconstruct a unit’s parents, the machine state at that minute, and who waived the defect. That is the job.”

## Stack
Python / Flask / SQLite (Postgres-shaped relational model). Strict state machine. Role-split UI. Audit log on every mutation.
