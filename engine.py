"""PolicyLens engine: talks to the local model, scores results, builds the report.

Works with any OpenAI-compatible server (LM Studio, Ollama, llama.cpp, vLLM).
"""

import base64
import csv
import io
import json
import re
from datetime import date

from openai import BadRequestError, OpenAI

STATUSES = ("COVERED", "PARTIAL", "MISSING")

SYSTEM_PROMPT = """You are a strict SOC 2 auditor reviewing a company policy.
You judge ONE control at a time, using ONLY the policy text provided.

Rules:
- COVERED: the policy clearly meets EVERY point in "A good policy must say".
- PARTIAL: the policy meets some points but not all, or is vague (no timeframe, limited scope).
- MISSING: the policy says nothing relevant.
- evidence: cite the clause numbers you relied on with a few words each, e.g. "5.2 MFA for admins only".
  If nothing is relevant, evidence must be exactly "None". Never invent clauses.
- gap: one short sentence on what is missing ("-" if COVERED).
- fix: one short, practical recommendation ("-" if COVERED).
- fix_clause: the exact policy text to add, written as a formal policy clause the company can paste
  into its policy (1-2 sentences, uses "must"). "-" if COVERED.
Reply with JSON only."""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "evidence": {"type": "string"},
        "gap": {"type": "string"},
        "fix": {"type": "string"},
        "fix_clause": {"type": "string"},
    },
    "required": ["status", "evidence", "gap", "fix", "fix_clause"],
}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"agree": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["agree", "reason"],
}

# Results cache: re-running the same policy with the same model is instant.
_CACHE: dict = {}


def make_client(base_url: str) -> OpenAI:
    # Local servers ignore the key, but the client requires one.
    # Time limit per call so the app never waits forever on a stuck generation.
    return OpenAI(base_url=base_url, api_key="local", timeout=600, max_retries=0)


def load_controls(path: str = "controls.json") -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_connection(client: OpenAI) -> list:
    """Return the model IDs the server reports (raises if unreachable)."""
    return [m.id for m in client.models.list().data]


def image_to_text(client: OpenAI, model: str, image_bytes: bytes, mime: str) -> str:
    """Use the model's vision ability to transcribe a photo/screenshot of a policy."""
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": (
                    "Transcribe all text in this policy document image exactly, "
                    "keeping headings and clause numbers. Output only the text."
                )},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
    )
    return resp.choices[0].message.content.strip()


def _parse_json(text: str) -> dict:
    """Pull a JSON object out of a model reply, even if wrapped in ``` fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _clean(result: dict) -> dict:
    status = str(result.get("status", "")).upper().strip()
    if status not in STATUSES:
        status = "MISSING"
    out = {
        "status": status,
        "evidence": str(result.get("evidence", "None")).strip() or "None",
        "gap": str(result.get("gap", "-")).strip() or "-",
        "fix": str(result.get("fix", "-")).strip() or "-",
        "fix_clause": str(result.get("fix_clause", "-")).strip() or "-",
    }
    if status == "COVERED":
        out["gap"] = out["fix"] = out["fix_clause"] = "-"
    return out


def _ask_json(client: OpenAI, model: str, messages: list, schema: dict, name: str,
              max_tokens: int = 4000) -> dict:
    """Chat call that returns parsed JSON, using structured output when the server supports it."""
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0, messages=messages, max_tokens=max_tokens,
            response_format={"type": "json_schema", "json_schema": {
                "name": name, "strict": True, "schema": schema}})
    except BadRequestError:
        # Server does not support structured output: ask again without it.
        resp = client.chat.completions.create(model=model, temperature=0, messages=messages,
                                              max_tokens=max_tokens)
    return _parse_json(resp.choices[0].message.content)


def unverified_citations(evidence: str, policy_text: str) -> list:
    """Clause numbers the model cited that do not appear in the policy (anti-hallucination check)."""
    cited = set(re.findall(r"\b\d+\.\d+\b", evidence))
    return sorted(c for c in cited if not re.search(rf"(?<![\d.]){re.escape(c)}(?![\d])", policy_text))


def _control_block(control: dict, policy_text: str) -> str:
    return (f"CONTROL: {control['control']} - {control['title']}\n"
            f"REQUIREMENT: {control['requirement']}\n"
            f"A GOOD POLICY MUST SAY: {control['look_for']}\n\n"
            f"POLICY:\n\"\"\"\n{policy_text}\n\"\"\"\n")


def verify_covered(client: OpenAI, model: str, policy_text: str, control: dict, result: dict) -> dict:
    """Second, stricter opinion on a COVERED verdict (a wrong 'Covered' hides a real gap)."""
    prompt = (_control_block(control, policy_text) +
              f"\nA colleague marked this control COVERED, citing: {result['evidence']}\n"
              "Check strictly: does the policy meet EVERY point in 'A good policy must say'? "
              'Reply as JSON: {"agree": true/false, "reason": "one short sentence"}')
    data = _ask_json(client, model, [{"role": "user", "content": prompt}], VERIFY_SCHEMA, "verify")
    agree = data.get("agree")
    if isinstance(agree, str):
        agree = agree.strip().lower() == "true"
    return {"agree": bool(agree), "reason": str(data.get("reason", "")).strip()}


def assess_control(client: OpenAI, model: str, policy_text: str, control: dict,
                   double_check: bool = True) -> dict:
    """Judge one control. Returns the control info plus status, evidence, gap, fix, fix_clause,
    and review flags (needs_review + why)."""
    cache_key = (model, policy_text, control["key"], double_check)
    if cache_key in _CACHE:
        return dict(_CACHE[cache_key])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _control_block(control, policy_text) + (
            '\nAnswer as JSON: {"status": "COVERED|PARTIAL|MISSING", "evidence": "...", '
            '"gap": "...", "fix": "...", "fix_clause": "..."}')},
    ]
    result = _clean(_ask_json(client, model, messages, RESULT_SCHEMA, "control_result"))
    result["unverified"] = unverified_citations(result["evidence"], policy_text)

    flags = []
    if result["unverified"]:
        flags.append(f"cited clause not found in policy: {', '.join(result['unverified'])}")
    if double_check and result["status"] == "COVERED":
        check = verify_covered(client, model, policy_text, control, result)
        if not check["agree"]:
            flags.append(f"second check disagrees: {check['reason']}")
    result["needs_review"] = bool(flags)
    result["review_reason"] = "; ".join(flags)
    result["ai_status"] = result["status"]   # kept even if a human overrides the status
    result["reviewer_note"] = ""

    out = {**control, **result}
    _CACHE[cache_key] = dict(out)
    return out


INTERVIEW_GREETING = (
    "Hi! I'm the PolicyLens assistant. I'll help you write down your security policy "
    "step by step, and I'll point out anything a SOC 2 auditor would expect that you haven't mentioned yet.\n\n"
    "Let's start: **what is your company's name, and what does this policy cover** "
    "(for example, access control for your cloud and internal apps)?"
)


TOPIC_STATES = ("NOT_DISCUSSED", "COVERED", "PARTIAL", "NOT_DONE")


def track_topics(client: OpenAI, model: str, controls: list, chat: list) -> dict:
    """Read the interview so far and mark each checklist topic. Returns {key: state}."""
    transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in chat)
    topics = "\n".join(f"- {c['key']}: {c['title']}. Auditor expects: {c['look_for']}" for c in controls)
    keys = [c["key"] for c in controls]
    prompt = (
        "You track a SOC 2 interview. For EACH topic, decide from the USER's answers only:\n"
        "COVERED = user described a practice that meets everything the auditor expects;\n"
        "PARTIAL = user described a practice that meets only some of it;\n"
        "NOT_DONE = user said they do not do this;\n"
        "NOT_DISCUSSED = not talked about yet.\n\n"
        f"TOPICS:\n{topics}\n\nINTERVIEW:\n{transcript}\n\n"
        "Reply as one JSON object with exactly these keys, each set to a state: "
        + json.dumps({k: "NOT_DISCUSSED" for k in keys})
    )
    # Fixed keys (no open-ended list) so a small model cannot loop while writing it.
    schema = {"type": "object",
              "properties": {k: {"type": "string", "enum": list(TOPIC_STATES)} for k in keys},
              "required": keys, "additionalProperties": False}
    data = _ask_json(client, model, [{"role": "user", "content": prompt}], schema, "topic_tracker",
                     max_tokens=4000)
    if isinstance(data.get("topics"), list):  # tolerate the older list shape
        data = {t.get("key"): t.get("state") for t in data["topics"] if isinstance(t, dict)}
    out = {k: "NOT_DISCUSSED" for k in keys}
    for k in keys:
        state = str(data.get(k, "")).upper().strip()
        if state in TOPIC_STATES:
            out[k] = state
    return out


def interview_prompt(controls: list, states: dict | None = None) -> str:
    """System prompt for the guided-input agent, built from the selected checklist."""
    topics = "\n".join(f"- {c['title']} ({c['control']}): {c['look_for']}" for c in controls)
    progress = ""
    if states:
        todo = [c["title"] for c in controls if states.get(c["key"]) in (None, "NOT_DISCUSSED")]
        weak = [c["title"] for c in controls if states.get(c["key"]) == "PARTIAL"]
        progress = (f"\nProgress so far:\n- Still to ask about: {', '.join(todo) or 'none'}\n"
                    f"- Answered but incomplete (ask a follow-up once): {', '.join(weak) or 'none'}\n")
    return f"""You are the PolicyLens assistant. You interview a user to capture their company's
CURRENT security practices so they can be written into a policy and checked against SOC 2.

Topics an auditor will look for:
{topics}
{progress}
How to behave:
- Ask ONE short question at a time, in plain language. Work through the topics in order.
- After each answer, confirm it in one line, then move on.
- If an answer is incomplete versus the topic (e.g. no timeframe, only admins covered,
  contractors not mentioned), ask one follow-up: "Are you missing...?" or "Does this also apply to...?"
- If the user says they don't do something, accept it, note it as a gap, and move on.
  Never pretend they do it, and never invent facts about their company.
- Keep every reply under 60 words.
- When all topics are covered, list in one line anything they said they don't do yet, then ask:
  "Anything else to add, or shall I generate the policy and run the analysis?"
"""


def interview_reply(client: OpenAI, model: str, controls: list, chat: list,
                    states: dict | None = None) -> str:
    """Next assistant message in the guided interview. `chat` is a list of {role, content}."""
    messages = [{"role": "system", "content": interview_prompt(controls, states)}] + chat
    resp = client.chat.completions.create(model=model, temperature=0.3, messages=messages, max_tokens=4000)
    return resp.choices[0].message.content.strip()


def compile_policy(client: OpenAI, model: str, chat: list) -> str:
    """Turn the interview into a numbered policy document using only facts the user stated."""
    transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in chat)
    prompt = (
        "Write a company security policy document from this interview.\n"
        "Rules:\n"
        "- Use ONLY practices the USER stated. Do not add anything they did not say.\n"
        "- Leave out practices the user said they do not have (do not write them as requirements).\n"
        "- Format: company name and policy title on top, then numbered sections (1., 2.) "
        "with numbered clauses (1.1, 1.2). Plain text, no markdown.\n\n"
        f"INTERVIEW:\n{transcript}"
    )
    resp = client.chat.completions.create(
        model=model, temperature=0, messages=[{"role": "user", "content": prompt}], max_tokens=6000)
    return resp.choices[0].message.content.strip()


def summarize(results: list) -> dict:
    counts = {s: sum(r["status"] == s for r in results) for s in STATUSES}
    total = len(results) or 1
    score = round((counts["COVERED"] + 0.5 * counts["PARTIAL"]) / total * 100)
    rank = {"MISSING": 0, "PARTIAL": 1}
    prio = {"High": 0, "Medium": 1, "Low": 2}
    gaps = [r for r in results if r["status"] != "COVERED"]
    gaps.sort(key=lambda r: (prio.get(r.get("priority"), 2), rank[r["status"]]))
    review = [r for r in results if r.get("needs_review") and not r.get("reviewer_note")]
    return {"counts": counts, "score": score, "top_fixes": gaps[:3], "gaps": gaps, "open_review": review}


def _ev(r: dict) -> str:
    return r["evidence"] + (f" (unverified: {', '.join(r['unverified'])})" if r.get("unverified") else "")


def _status_text(r: dict) -> str:
    text = r["status"]
    if r.get("ai_status") and r["ai_status"] != r["status"]:
        text += f" (AI said {r['ai_status']})"
    if r.get("needs_review") and not r.get("reviewer_note"):
        text += " - NEEDS REVIEW"
    return text


def build_markdown(policy_name: str, model: str, results: list, reviewer: str = "") -> str:
    s = summarize(results)
    c = s["counts"]
    cell = lambda x: str(x).replace("|", "/").replace("\n", " ")
    lines = [
        "# PolicyLens - SOC 2 Gap Report", "",
        f"**Policy analysed:** {policy_name}  ",
        "**Framework:** SOC 2 Trust Services Criteria  ",
        f"**Model:** {model} (running locally - no data left this device)  ",
        f"**Date:** {date.today().isoformat()}  ",
        f"**Reviewed by:** {reviewer or 'Not yet reviewed'}", "",
        f"## Coverage Score: {s['score']}%", "",
        "| Covered | Partial | Missing | Open review flags |", "|---|---|---|---|",
        f"| {c['COVERED']} | {c['PARTIAL']} | {c['MISSING']} | {len(s['open_review'])} |", "",
        "## Control Mapping", "",
        "| # | Control | Requirement | Evidence from policy | Status | Recommended fix | Reviewer note |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        note = r.get("reviewer_note") or (f"Flag: {r['review_reason']}" if r.get("needs_review") else "")
        lines.append("| " + " | ".join(cell(x) for x in
                     [i, r["control"], r["title"], _ev(r), _status_text(r), r["fix"], note]) + " |")
    lines += ["", "## Top Priority Fixes"]
    lines += [f"{i}. **{r['fix']}** ({r['control']}, {r['status'].lower()})"
              for i, r in enumerate(s["top_fixes"], 1)] or ["No gaps found for the selected controls."]
    if s["gaps"]:
        lines += ["", "## Policy Text to Add (ready to paste)"]
        lines += [f"- **{r['control']} {r['title']}:** {r['fix_clause']}" for r in s["gaps"]]
    lines += ["", "## Evidence an Auditor Will Ask For"]
    lines += [f"- **{r['control']} {r['title']}:** {r.get('evidence_needed', '-')}" for r in results]
    lines += ["", "---", "*AI-assisted draft. Review by a qualified GRC professional before use in an audit.*"]
    return "\n".join(lines)


def build_csv(results: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Control", "Domain", "Title", "Final status", "AI status", "Needs review", "Review reason",
                "Evidence from policy", "Gap", "Recommended fix", "Policy text to add",
                "Evidence to collect", "Priority", "Reviewer note"])
    for r in results:
        w.writerow([r["control"], r["domain"], r["title"], r["status"], r.get("ai_status", r["status"]),
                    "Yes" if r.get("needs_review") else "No", r.get("review_reason", ""),
                    _ev(r), r["gap"], r["fix"], r.get("fix_clause", "-"),
                    r.get("evidence_needed", ""), r["priority"], r.get("reviewer_note", "")])
    return buf.getvalue()
