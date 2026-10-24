"""PolicyLens - offline SOC 2 policy gap analysis powered by Gemma 4.

Run:  streamlit run PolicyLens.py
Needs a local OpenAI-compatible model server (LM Studio or Ollama).
Model settings: see the top of ui.py, or change them in the sidebar.
"""

import streamlit as st

import engine
import ui

st.set_page_config(page_title="PolicyLens", page_icon="🔎", layout="wide")
client, model, selected = ui.sidebar()

st.title("🔎 PolicyLens")
st.markdown("**Offline SOC 2 policy gap analysis powered by Gemma 4.** "
            "Upload a policy, and PolicyLens checks it control by control, "
            "cites the clause it used as evidence, and tells you what to fix.")
st.page_link("pages/1_GRC_Workspace.py", icon="🛡️",
             label="No written policy yet? Open the GRC Workspace: an AI auditor interviews you and builds it.")

# ---- Step 1: input ---------------------------------------------------------
st.subheader("1. Add a policy")
tab_file, tab_image, tab_paste = st.tabs(["📄 Text file", "🖼️ Image / screenshot", "✍️ Paste text"])

with tab_file:
    up = st.file_uploader("Upload a .txt or .md policy", type=["txt", "md"])
    if up is not None and st.session_state.get("loaded_file") != (up.name, up.size):
        st.session_state.loaded_file = (up.name, up.size)  # load once, keep later edits
        st.session_state.policy = up.getvalue().decode("utf-8", errors="ignore")
        st.session_state.source = up.name
    if st.button("Use the sample policy (Acme HealthTech)"):
        st.session_state.policy = (ui.HERE / "sample_policy.txt").read_text(encoding="utf-8")
        st.session_state.source = "sample_policy.txt"

with tab_image:
    img = st.file_uploader("Upload a photo or screenshot of a policy page", type=["png", "jpg", "jpeg"])
    if img is not None and st.button("Read text from image with Gemma 4"):
        with st.spinner("Gemma 4 is reading the image..."):
            try:
                st.session_state.policy = engine.image_to_text(client, model, img.getvalue(),
                                                               img.type or "image/png")
                st.session_state.source = img.name
                st.success("Text extracted. Check it in the box below.")
            except Exception as e:
                st.error(f"Image reading failed (does this model support vision?): {e}")

with tab_paste:
    pasted = st.text_area("Paste policy text", height=200)
    if st.button("Use pasted text") and pasted.strip():
        st.session_state.policy = pasted
        st.session_state.source = "pasted text"

policy = st.session_state.get("policy", "")
policy_name = "Policy"
if policy:
    first_line = next((l.strip() for l in policy.splitlines() if l.strip()), "Policy")
    policy_name = st.text_input("Policy name for the report", first_line)
    st.session_state.policy = st.text_area(
        f"Policy text (from {st.session_state.get('source', 'input')}), editable",
        policy, height=220)

# ---- Step 2: analyse -------------------------------------------------------
st.subheader("2. Analyse")
if not policy:
    st.info("Add a policy above to begin.")
if st.button("🔍 Analyse policy", type="primary", disabled=not (policy and selected)):
    st.session_state.results = ui.run_analysis(client, model, st.session_state.policy, selected)
    st.session_state.report_name = policy_name

# ---- Step 3: report --------------------------------------------------------
if st.session_state.get("results"):
    st.subheader("3. Gap report")
    ui.render_report(st.session_state.results, model, st.session_state.get("report_name", "Policy"))
