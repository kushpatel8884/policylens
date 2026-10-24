"""GRC Workspace: an AI auditor interviews you, tracks SOC 2 topics live, then builds the policy and report."""

from concurrent.futures import ThreadPoolExecutor

import streamlit as st

import engine
import ui

st.set_page_config(page_title="PolicyLens GRC Workspace", page_icon="🛡️", layout="wide")
client, model, selected = ui.sidebar()
ss = st.session_state

STATE_VIEW = {"NOT_DISCUSSED": ("⬜", "Not discussed yet"),
              "COVERED": ("✅", "In place"),
              "PARTIAL": ("⚠️", "Partly in place"),
              "NOT_DONE": ("❌", "Not in place")}

ss.setdefault("ws_chat", [{"role": "assistant", "content": engine.INTERVIEW_GREETING}])
ss.setdefault("ws_states", {})

st.title("🛡️ GRC Workspace")
st.markdown("Talk to the PolicyLens auditor. It asks about every SOC 2 topic in the areas selected "
            "in the sidebar, tracks what you have and what you're missing, then writes your policy "
            "and generates the full gap report.")

left, right = st.columns([3, 2], gap="large")

# ---- Left: the interview ---------------------------------------------------
with left:
    st.subheader("💬 Interview")
    with st.container(height=460):
        for msg in ss.ws_chat:
            with st.chat_message(msg["role"], avatar="🛡️" if msg["role"] == "assistant" else "🧑"):
                st.markdown(msg["content"])

    with st.form("ws_form", clear_on_submit=True):
        answer = st.text_area("Your answer", height=80,
                              placeholder="e.g. We use single sign-on for logins, MFA only for admins...")
        sent = st.form_submit_button("Send ➤", type="primary")
    if sent and answer.strip():
        ss.ws_chat.append({"role": "user", "content": answer.strip()})
        chat, prev_states = list(ss.ws_chat), dict(ss.ws_states)
        with st.spinner("Auditor is reviewing your answer..."):
            # Speed: update the checklist and write the reply at the same time.
            with ThreadPoolExecutor(max_workers=2) as pool:
                tracking = pool.submit(engine.track_topics, client, model, selected, chat)
                replying = pool.submit(engine.interview_reply, client, model, selected, chat, prev_states)
                try:
                    ss.ws_chat.append({"role": "assistant", "content": replying.result()})
                except Exception as e:
                    ss.ws_chat.pop()
                    st.error(f"Cannot reach the model: {e}")
                try:
                    ss.ws_states = tracking.result()
                except Exception:
                    pass  # keep the previous checklist if tracking fails
        st.rerun()

# ---- Right: live auditor checklist -----------------------------------------
with right:
    st.subheader("📋 Auditor checklist")
    states = {c["key"]: ss.ws_states.get(c["key"], "NOT_DISCUSSED") for c in selected}
    done = sum(s != "NOT_DISCUSSED" for s in states.values())
    st.progress(done / max(len(selected), 1), text=f"{done} of {len(selected)} topics discussed")
    for c in selected:
        icon, label = STATE_VIEW[states[c["key"]]]
        st.markdown(f"{icon} **{c['title']}**  \n<small>{c['control']} · {label}</small>",
                    unsafe_allow_html=True)
    counts = {k: sum(s == k for s in states.values()) for k in STATE_VIEW}
    st.caption(f"✅ {counts['COVERED']}  ⚠️ {counts['PARTIAL']}  ❌ {counts['NOT_DONE']}  ⬜ {counts['NOT_DISCUSSED']}")

# ---- Bottom: build policy and report ---------------------------------------
st.divider()
st.subheader("📑 Generate policy and audit report")
answered = any(m["role"] == "user" for m in ss.ws_chat)
b1, b2, b3 = st.columns([2, 1, 1])
if b1.button("🚀 Write policy and run full audit", type="primary", disabled=not (answered and selected)):
    with st.spinner("Writing the policy from your answers..."):
        try:
            ss.ws_policy = engine.compile_policy(client, model, ss.ws_chat)
        except Exception as e:
            st.error(f"Cannot reach the model: {e}")
            ss.ws_policy = ""
    if ss.ws_policy:
        ss.ws_results = ui.run_analysis(client, model, ss.ws_policy, selected)
if b2.button("📨 Send policy to main page", disabled=not ss.get("ws_policy")):
    ss.policy, ss.source = ss.ws_policy, "GRC Workspace interview"
    st.switch_page("PolicyLens.py")
if b3.button("↺ Start over"):
    for k in ("ws_chat", "ws_states", "ws_policy", "ws_results"):
        ss.pop(k, None)
    st.rerun()

transcript = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in ss.ws_chat)
st.download_button("⬇️ Interview transcript (audit evidence)", transcript,
                   file_name="policylens_interview.txt", disabled=not answered)

if ss.get("ws_policy"):
    with st.expander("📄 Generated policy", expanded=False):
        st.text(ss.ws_policy)
        st.download_button("⬇️ Download policy (.txt)", ss.ws_policy, file_name="generated_policy.txt")

if ss.get("ws_results"):
    st.subheader("📊 Gap report")
    name = next((l.strip() for l in ss.ws_policy.splitlines() if l.strip()), "Generated policy")
    ui.render_report(ss.ws_results, model, name, key_prefix="ws")
