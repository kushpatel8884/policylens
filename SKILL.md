---
name: soc2-policy-review
description: Review a company security policy against SOC 2 Trust Services Criteria and produce a gap report with evidence. Use when asked to check, audit or gap-assess a policy for SOC 2.
---

# SOC 2 Policy Review

Use this method to assess a security policy against SOC 2, the same way the PolicyLens app does.

## Inputs
- The policy text (ask for it if not provided).
- The checklist in `controls.json`. Each control has `control`, `title`, `requirement`, `look_for`, `priority`.

## Method
1. Ask which areas to check if unclear (default: Governance, Access Control, Logging & Monitoring).
2. Assess **one control at a time**, using only the policy text:
   - **COVERED**: the policy meets every point in `look_for`.
   - **PARTIAL**: some points met, or vague (no timeframe, limited scope such as admins only).
   - **MISSING**: nothing relevant.
3. For every control, cite the clause numbers used as evidence, or write `None`. Never invent clauses.
4. Give one practical fix for each PARTIAL or MISSING control.
5. Score = (Covered + 0.5 × Partial) ÷ total × 100.

## Output
- Coverage score and counts.
- Table: Control | Requirement | Evidence | Status | Recommended fix.
- Top 3 fixes, High priority first, Missing before Partial.
- End with: "AI-assisted draft. Review by a qualified GRC professional before use in an audit."

## If there is no written policy
Interview the user one question at a time through the selected controls. Ask a follow-up whenever an answer is incomplete ("Does this also apply to contractors?"). Record "not done" honestly, never assume. Then write the policy from their answers only, and run the review above.
