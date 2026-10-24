PROJECT NAME
PolicyLens

TAGLINE
Offline SOC 2 policy gap analysis powered by Gemma 4

DESCRIPTION
The problem: Companies preparing for SOC 2 audits must check their security policies against dozens of controls. GRC teams either do this by hand, which is slow and error-prone, or paste confidential policies into cloud AI tools, which is a privacy risk.

What PolicyLens does: It checks a company's policy against SOC 2 Trust Services Criteria completely offline, using Gemma 4 running locally. For every control it returns Covered, Partial or Missing, cites the exact policy clause used as evidence, recommends a fix and writes the policy text to paste in. It also lists the evidence an auditor will ask for. Reports download as PDF, Word, HTML, Excel/CSV, Markdown or JSON.

GRC Workspace: no written policy? An AI auditor interviews you, ticks off SOC 2 topics on a live checklist, asks follow-ups on anything incomplete, then writes the policy from your answers and runs the full audit.

Built for accuracy AND speed:
- A wrong "Covered" hides a real gap, so every Covered verdict gets a second strict check; disagreements are flagged for human review
- Every clause number the model cites is verified against the policy to catch hallucinations
- A reviewer confirms or overrides each verdict and signs off; the report shows AI verdict vs final decision
- Fixed SOC 2 checklist (controls.json), one control per call with structured JSON output, so results are consistent
- Parallel checks and result caching keep it fast

Four inputs: interview, text file, photo/screenshot of a policy page (Gemma 4 vision), or pasted text.
Also includes SKILL.md, an open agent skill so any AI agent can run the same SOC 2 review method.

HOW WE BUILT IT
Python, Streamlit, Gemma 4 12B via LM Studio's local OpenAI-compatible API, fpdf2 and python-docx for exports. Model-agnostic: works with Ollama or any OpenAI-compatible server by changing one URL.

PRIZE CATEGORIES
Best Use of Gemma 4: multimodal input (image to policy), a focused productivity tool for GRC teams, a working prototype.
Best Open-Source AI Project: MIT-licensed, runs an open-weight model locally, includes an agent skill.

LINKS
GitHub: <your repo link>
Demo video: <your video link>
