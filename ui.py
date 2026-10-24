"""Shared Streamlit pieces: settings sidebar, analysis runner, review table and report view."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import engine
import reports

# ---- Change these two lines to use any other model -------------------------
DEFAULT_BASE_URL = os.getenv("POLICYLENS_BASE_URL", "http://localhost:1234/v1")  # Ollama: http://localhost:11434/v1
DEFAULT_MODEL = os.getenv("POLICYLENS_MODEL", "google/gemma-4-12b-qat")
# ----------------------------------------------------------------------------

HERE = Path(__file__).parent
CONTROLS = engine.load_controls(HERE / "controls.json")
DOMAINS = list(dict.fromkeys(c["domain"] for c in CONTROLS))
DEFAULT_DOMAINS = ["Governance", "Access Control", "Logging & Monitoring"]
ICON = {"COVERED": "✅ COVERED", "PARTIAL": "⚠️ PARTIAL", "MISSING": "❌ MISSING"}
FROM_ICON = {v: k for k, v in ICON.items()}


def _persist(widget_key: str, store_key: str):
    st.session_state[store_key] = st.session_state[widget_key]


def sidebar():
    """Model, accuracy and speed settings, kept in sync across pages.
    Returns (client, model, selected_controls)."""
    ss = st.session_state
    for k, v in (("cfg_url", DEFAULT_BASE_URL), ("cfg_model", DEFAULT_MODEL),
                 ("cfg_domains", DEFAULT_DOMAINS), ("cfg_double", True), ("cfg_workers", 2)):
        ss.setdefault(k, v)
    with st.sidebar:
        st.header("⚙️ Model")
        st.text_input("Server URL", ss.cfg_url, key="w_url", on_change=_persist, args=("w_url", "cfg_url"))
        st.text_input("Model name", ss.cfg_model, key="w_model", on_change=_persist, args=("w_model", "cfg_model"))
        if st.button("Test connection"):
            try:
                models = engine.check_connection(engine.make_client(ss.cfg_url))
                st.success(f"Connected. Models: {', '.join(models) or 'none loaded'}")
            except Exception as e:
                st.error(f"Cannot reach the server: {e}")

        st.header("📋 SOC 2 areas")
        st.multiselect("Areas to check", DOMAINS, ss.cfg_domains, key="w_domains",
                       on_change=_persist, args=("w_domains", "cfg_domains"))
        selected = [c for c in CONTROLS if c["domain"] in ss.cfg_domains]
        st.caption(f"{len(selected)} controls selected")

        st.header("🎯 Accuracy & speed")
        st.toggle("Double-check 'Covered' verdicts", ss.cfg_double, key="w_double",
                  on_change=_persist, args=("w_double", "cfg_double"),
                  help="A second, stricter check on every Covered verdict. Disagreements are flagged for human review.")
        st.slider("Parallel checks", 1, 4, ss.cfg_workers, key="w_workers",
                  on_change=_persist, args=("w_workers", "cfg_workers"),
                  help="Controls checked at the same time. Raise it if your model server handles parallel requests.")
        st.caption("Re-running the same policy is instant (results are cached).")
        st.divider()
        st.caption("🔒 Runs fully offline. Your policy never leaves this machine.")
    return engine.make_client(ss.cfg_url), ss.cfg_model, selected


def run_analysis(client, model, policy_text, controls):
    """Check all controls in parallel with a progress bar. Returns results in checklist order."""
    ss = st.session_state
    results, errors = [None] * len(controls), []
    bar = st.progress(0.0, text="Starting checks...")
    with ThreadPoolExecutor(max_workers=ss.get("cfg_workers", 2)) as pool:
        futures = {pool.submit(engine.assess_control, client, model, policy_text, c,
                               ss.get("cfg_double", True)): i for i, c in enumerate(controls)}
        for n, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                errors.append(f"{controls[i]['control']} {controls[i]['title']}: {e}")
            bar.progress(n / len(controls), text=f"Checked {n} of {len(controls)} controls")
    for err in errors:
        st.error(f"Failed: {err}")
    return [r for r in results if r is not None]


def render_report(results, model, policy_name, key_prefix="r"):
    """Score, editable review table, fixes, evidence list, sign-off and downloads.
    Human edits are written back into `results` (a session_state list)."""
    summary_slot = st.container()

    # ---- Review table: humans confirm or override every AI verdict ----------
    st.markdown("#### 🔎 Findings: review and confirm")
    st.caption("Change **Final status** if you disagree with the AI, and add a note. "
               "Rows marked 🚩 were flagged by the accuracy checks and need a human look.")
    df = pd.DataFrame([{
        "Control": r["control"],
        "Requirement": r["title"],
        "Evidence from policy": engine._ev(r),
        "AI verdict": ICON[r["ai_status"]],
        "Flag": f"🚩 {r['review_reason']}" if r["needs_review"] else "",
        "Final status": ICON[r["status"]],
        "Reviewer note": r.get("reviewer_note", ""),
    } for r in results])
    edited = st.data_editor(
        df, key=f"{key_prefix}_editor_{id(results)}", hide_index=True, use_container_width=True,
        disabled=["Control", "Requirement", "Evidence from policy", "AI verdict", "Flag"],
        column_config={
            "Final status": st.column_config.SelectboxColumn(options=list(ICON.values()), required=True),
            "Reviewer note": st.column_config.TextColumn(width="medium"),
            "Flag": st.column_config.TextColumn(width="medium"),
            "Evidence from policy": st.column_config.TextColumn(width="large"),
        })
    for r, (_, row) in zip(results, edited.iterrows()):
        r["status"] = FROM_ICON[row["Final status"]]
        r["reviewer_note"] = str(row["Reviewer note"] or "").strip()

    s = engine.summarize(results)
    c = s["counts"]
    with summary_slot:
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Coverage score", f"{s['score']}%")
        m2.metric("✅ Covered", c["COVERED"])
        m3.metric("⚠️ Partial", c["PARTIAL"])
        m4.metric("❌ Missing", c["MISSING"])
        m5.metric("🚩 To review", len(s["open_review"]))

    # ---- What to do next ----------------------------------------------------
    st.markdown("#### 🛠️ Top priority fixes")
    if s["top_fixes"]:
        for i, r in enumerate(s["top_fixes"], 1):
            st.markdown(f"{i}. **{r['fix']}** ({r['control']}, {r['status'].lower()})")
    else:
        st.success("No gaps found for the selected controls.")
    if s["gaps"]:
        with st.expander("📝 Policy text to add (ready to paste)"):
            for r in s["gaps"]:
                st.markdown(f"**{r['control']} · {r['title']}**")
                st.code(r["fix_clause"] if r["fix_clause"] != "-" else r["fix"], language=None)
    with st.expander("📂 Evidence an auditor will ask for"):
        for r in results:
            st.markdown(f"- **{r['control']} · {r['title']}:** {r.get('evidence_needed', '-')}")

    # ---- Sign-off and downloads --------------------------------------------
    st.markdown("#### ✍️ Sign-off")
    if s["open_review"]:
        st.warning(f"{len(s['open_review'])} flagged finding(s) have no reviewer note yet. "
                   "Check them in the table above before signing off.")
    a, b = st.columns([2, 1])
    name = a.text_input("Reviewer name", key=f"{key_prefix}_reviewer")
    signed = b.checkbox("I have reviewed all findings", key=f"{key_prefix}_signed", disabled=not name.strip())
    reviewer = f"{name.strip()} on {date.today().isoformat()}" if signed and name.strip() else ""

    st.markdown("#### ⬇️ Download the report")
    if not reviewer:
        st.caption("Reports downloaded now will say 'Not yet reviewed'.")
    exports = [
        ("PDF", lambda: reports.build_pdf(policy_name, model, results, reviewer), "pdf", "application/pdf"),
        ("Word", lambda: reports.build_docx(policy_name, model, results, reviewer), "docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("HTML", lambda: reports.build_html(policy_name, model, results, reviewer), "html", "text/html"),
        ("Excel / CSV", lambda: engine.build_csv(results), "csv", "text/csv"),
        ("Markdown", lambda: engine.build_markdown(policy_name, model, results, reviewer), "md", "text/markdown"),
        ("JSON", lambda: reports.build_json(policy_name, model, results, reviewer), "json", "application/json"),
    ]
    cols = st.columns(len(exports))
    for col, (label, build, ext, mime) in zip(cols, exports):
        try:
            col.download_button(label, build(), file_name=f"policylens_report.{ext}", mime=mime,
                                key=f"{key_prefix}_{ext}", use_container_width=True)
        except ImportError:
            col.caption(f"{label}: run `pip install -r requirements.txt`")
        except Exception as e:
            col.caption(f"{label} failed: {e}")
    st.caption("AI-assisted draft. Review by a qualified GRC professional before use in an audit.")
