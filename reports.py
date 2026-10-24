"""Report exporters: HTML, PDF, Word, JSON (Markdown and CSV live in engine.py)."""

import html
import io
import json
from datetime import date

import engine

COLORS = {"COVERED": "#15803d", "PARTIAL": "#b45309", "MISSING": "#b91c1c"}
DISCLAIMER = "AI-assisted draft. Review by a qualified GRC professional before use in an audit."


def _meta(policy_name, model, reviewer):
    return [f"Policy analysed: {policy_name}", "Framework: SOC 2 Trust Services Criteria",
            f"Model: {model} (running locally)", f"Date: {date.today().isoformat()}",
            f"Reviewed by: {reviewer or 'Not yet reviewed'}"]


def _note(r):
    return r.get("reviewer_note") or (f"Flag: {r['review_reason']}" if r.get("needs_review") else "")


def build_json(policy_name, model, results, reviewer=""):
    s = engine.summarize(results)
    keep = ("control", "domain", "title", "requirement", "status", "ai_status", "needs_review",
            "review_reason", "reviewer_note", "evidence", "unverified", "gap", "fix", "fix_clause",
            "evidence_needed", "priority")
    return json.dumps({
        "policy": policy_name, "framework": "SOC 2 Trust Services Criteria", "model": model,
        "date": date.today().isoformat(), "reviewed_by": reviewer, "coverage_score": s["score"],
        "counts": s["counts"], "open_review_flags": len(s["open_review"]),
        "findings": [{k: r.get(k) for k in keep} for r in results],
    }, indent=2)


def build_html(policy_name, model, results, reviewer=""):
    s = engine.summarize(results)
    c = s["counts"]
    e = html.escape
    rows = "".join(
        f"<tr><td><b>{r['control']}</b></td><td>{e(r['title'])}</td><td>{e(engine._ev(r))}</td>"
        f"<td style='color:{COLORS[r['status']]};font-weight:bold'>{e(engine._status_text(r))}</td>"
        f"<td>{e(r['fix'])}</td><td>{e(_note(r))}</td></tr>" for r in results)
    fixes = "".join(f"<li><b>{e(r['fix'])}</b> ({r['control']})</li>" for r in s["top_fixes"]) \
        or "<li>No gaps found.</li>"
    clauses = "".join(f"<li><b>{r['control']} {e(r['title'])}:</b> {e(r['fix_clause'])}</li>" for r in s["gaps"])
    evidence = "".join(f"<li><b>{r['control']} {e(r['title'])}:</b> {e(r.get('evidence_needed', '-'))}</li>"
                       for r in results)
    meta = "<br>".join(e(m) for m in _meta(policy_name, model, reviewer))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>PolicyLens Report</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:1150px;margin:30px auto;padding:0 16px;color:#1a1a1a;background:#fff}}
h1{{color:#1f3a8a}}table{{border-collapse:collapse;width:100%;margin:12px 0}}
th,td{{border:1px solid #ccc;padding:8px;text-align:left;vertical-align:top;font-size:14px}}
th{{background:#1f3a8a;color:#fff}}tr:nth-child(even){{background:#f4f6fb}}
.score{{font-size:32px;font-weight:bold;color:#1f3a8a}}li{{margin:4px 0}}</style></head><body>
<h1>PolicyLens - SOC 2 Gap Report</h1><p>{meta}</p>
<p class="score">Coverage score: {s['score']}%</p>
<p>Covered: {c['COVERED']} &nbsp;|&nbsp; Partial: {c['PARTIAL']} &nbsp;|&nbsp; Missing: {c['MISSING']}
&nbsp;|&nbsp; Open review flags: {len(s['open_review'])}</p>
<h2>Control mapping</h2><table><tr><th>Control</th><th>Requirement</th><th>Evidence from policy</th>
<th>Status</th><th>Recommended fix</th><th>Reviewer note</th></tr>{rows}</table>
<h2>Top priority fixes</h2><ol>{fixes}</ol>
{f'<h2>Policy text to add (ready to paste)</h2><ul>{clauses}</ul>' if clauses else ''}
<h2>Evidence an auditor will ask for</h2><ul>{evidence}</ul>
<hr><p><i>{DISCLAIMER}</i></p></body></html>"""


def _latin(text):
    """PDF core fonts are Latin-1 only; swap common symbols and drop the rest."""
    for a, b in {"–": "-", "—": "-", "‘": "'", "’": "'",
                 "“": '"', "”": '"', "•": "-", "…": "...", "→": "->"}.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def build_pdf(policy_name, model, results, reviewer=""):
    from fpdf import FPDF

    s = engine.summarize(results)
    c = s["counts"]
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(True, margin=12)
    pdf.add_page()

    def heading(text, size=13):
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", size)
        pdf.set_text_color(31, 58, 138)
        pdf.cell(0, 8, _latin(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    def line(text, style="", size=10):
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(0, 5.5, _latin(text), new_x="LMARGIN", new_y="NEXT")

    heading("PolicyLens - SOC 2 Gap Report", 18)
    for m in _meta(policy_name, model, reviewer):
        line(m, size=11)
    heading(f"Coverage score: {s['score']}%   (Covered {c['COVERED']} | Partial {c['PARTIAL']} | "
            f"Missing {c['MISSING']} | Open review flags {len(s['open_review'])})")
    pdf.set_font("Helvetica", "", 8.5)
    with pdf.table(col_widths=(16, 38, 62, 32, 60, 45), line_height=4.5, text_align="LEFT") as table:
        head = table.row()
        for h in ("Control", "Requirement", "Evidence from policy", "Status", "Recommended fix", "Reviewer note"):
            head.cell(h)
        for r in results:
            row = table.row()
            for v in (r["control"], r["title"], engine._ev(r), engine._status_text(r), r["fix"], _note(r)):
                row.cell(_latin(v))
    heading("Top priority fixes")
    for i, r in enumerate(s["top_fixes"], 1):
        line(f"{i}. {r['fix']} ({r['control']})")
    if s["gaps"]:
        heading("Policy text to add (ready to paste)")
        for r in s["gaps"]:
            line(f"- {r['control']} {r['title']}: {r['fix_clause']}")
    heading("Evidence an auditor will ask for")
    for r in results:
        line(f"- {r['control']} {r['title']}: {r.get('evidence_needed', '-')}")
    pdf.ln(3)
    line(DISCLAIMER, "I", 9)
    return bytes(pdf.output())


def build_docx(policy_name, model, results, reviewer=""):
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt, RGBColor

    s = engine.summarize(results)
    c = s["counts"]
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = sec.page_height, sec.page_width
    doc.add_heading("PolicyLens - SOC 2 Gap Report", 0)
    for m in _meta(policy_name, model, reviewer):
        doc.add_paragraph(m)
    doc.add_heading(f"Coverage score: {s['score']}%", 1)
    doc.add_paragraph(f"Covered: {c['COVERED']}  |  Partial: {c['PARTIAL']}  |  Missing: {c['MISSING']}  |  "
                      f"Open review flags: {len(s['open_review'])}")
    doc.add_heading("Control mapping", 1)
    headers = ("Control", "Requirement", "Evidence from policy", "Status", "Recommended fix", "Reviewer note")
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for cell, h in zip(table.rows[0].cells, headers):
        cell.text = h
        cell.paragraphs[0].runs[0].bold = True
    for r in results:
        cells = table.add_row().cells
        for cell, v in zip(cells, (r["control"], r["title"], engine._ev(r), engine._status_text(r),
                                   r["fix"], _note(r))):
            cell.text = v
        run = cells[3].paragraphs[0].runs[0]
        run.bold = True
        run.font.color.rgb = RGBColor.from_string(COLORS[r["status"]].lstrip("#"))
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)
    doc.add_heading("Top priority fixes", 1)
    for r in s["top_fixes"]:
        doc.add_paragraph(f"{r['fix']} ({r['control']})", style="List Number")
    if s["gaps"]:
        doc.add_heading("Policy text to add (ready to paste)", 1)
        for r in s["gaps"]:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(f"{r['control']} {r['title']}: ").bold = True
            p.add_run(r["fix_clause"])
    doc.add_heading("Evidence an auditor will ask for", 1)
    for r in results:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(f"{r['control']} {r['title']}: ").bold = True
        p.add_run(r.get("evidence_needed", "-"))
    doc.add_paragraph().add_run(DISCLAIMER).italic = True
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
