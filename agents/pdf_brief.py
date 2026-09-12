"""Convert a preparation brief (markdown string) to a clean PDF.

Uses fpdf2 — pure Python, no system dependencies, works on Streamlit Cloud.
"""

from __future__ import annotations

import re
from io import BytesIO

from fpdf import FPDF


class _BriefPDF(FPDF):
    """Minimal PDF with a header, footer, and planning-brief styling."""

    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(100, 120, 110)
        self.cell(0, 6, _safe_text("PlanPerm - Preparation Brief"), align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(130, 140, 135)
        self.cell(0, 8, _safe_text(f"Page {self.page_no()}/{{nb}}  |  Informational support only - not legal, planning, architectural, or financial advice."), align="C")


def _strip_md_links(text: str) -> str:
    """Turn [label](url) into 'label (url)' for plain text."""
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)


def _strip_bold(text: str) -> str:
    return text.replace("**", "")


def _safe_text(text: str) -> str:
    """Replace characters that Helvetica can't render."""
    replacements = {
        "\u2014": "-",   # em dash
        "\u2013": "-",   # en dash
        "\u2018": "'",   # left single quote
        "\u2019": "'",   # right single quote
        "\u201c": '"',   # left double quote
        "\u201d": '"',   # right double quote
        "\u2026": "...", # ellipsis
        "\u2022": "-",   # bullet
        "\u00e9": "e",   # é
        "\u00e1": "a",   # á
        "\u00ed": "i",   # í
        "\u00f3": "o",   # ó
        "\u00fa": "u",   # ú
        "\u00c9": "E",   # É
        "\u00c1": "A",   # Á
        "\u2610": "[ ]", # checkbox ☐
        "\u2714": "[x]", # checkmark ✔
        "\u2705": "[OK]",  # ✅
        "\u274c": "[X]",   # ❌
        "\u26a0\ufe0f": "[!]",  # ⚠️
        "\u26a0": "[!]",  # ⚠
        "\u2b07\ufe0f": "v",  # ⬇️
        "\u2b07": "v",
        "\u23f3": "[~]",  # ⏳
        "\u21a9\ufe0f": "<-", # ↩️
        "\u21a9": "<-",
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    # Strip any remaining non-latin1 characters
    return text.encode("latin-1", errors="replace").decode("latin-1")


def brief_to_pdf(markdown: str) -> bytes:
    """Take the markdown brief string and return PDF bytes."""
    pdf = _BriefPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_left_margin(18)
    pdf.set_right_margin(18)

    for raw_line in markdown.split("\n"):
        line = raw_line.rstrip()

        # Sanitise for Helvetica compatibility
        line = _safe_text(line)

        # Skip markdown-only formatting
        if line.strip() == "---":
            pdf.ln(4)
            pdf.set_draw_color(200, 210, 205)
            pdf.line(18, pdf.get_y(), pdf.w - 18, pdf.get_y())
            pdf.ln(4)
            continue

        if line.strip().startswith(">"):
            # Blockquote — render as italic indented text
            text = _strip_bold(_strip_md_links(line.strip().lstrip("> ")))
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(80, 95, 85)
            pdf.set_x(24)
            pdf.multi_cell(pdf.w - 42, 5, text)
            pdf.ln(1)
            continue

        if line.startswith("# "):
            # Title
            pdf.set_font("Helvetica", "B", 18)
            pdf.set_text_color(20, 63, 49)
            pdf.ln(2)
            pdf.multi_cell(0, 9, _strip_bold(line[2:].strip()))
            pdf.ln(4)
            continue

        if line.startswith("## "):
            # Section header
            pdf.ln(4)
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(15, 118, 110)
            pdf.multi_cell(0, 7, _strip_bold(line[3:].strip()))
            pdf.ln(2)
            continue

        if line.startswith("### "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(30, 80, 60)
            pdf.multi_cell(0, 6, _strip_bold(line[4:].strip()))
            pdf.ln(1)
            continue

        if line.strip().startswith("|") and "---" in line:
            # Table separator row — skip
            continue

        if line.strip().startswith("|"):
            # Table row
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 55, 45)
            col_w = (pdf.w - 36) / max(len(cells), 1)
            for cell in cells:
                pdf.cell(col_w, 6, _strip_bold(cell), border=1, align="C")
            pdf.ln()
            continue

        if line.strip().startswith("- [ ]"):
            # Checklist item
            text = _strip_bold(_strip_md_links(line.strip()[5:].strip()))
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 55, 45)
            pdf.cell(6, 5, chr(9744))  # ☐ checkbox
            pdf.multi_cell(pdf.w - 42, 5, text)
            pdf.ln(1)
            continue

        if line.strip().startswith("- "):
            # Bullet point
            text = _strip_bold(_strip_md_links(line.strip()[2:].strip()))
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 55, 45)
            pdf.cell(5, 5, chr(8226))  # •
            pdf.multi_cell(pdf.w - 41, 5, text)
            pdf.ln(1)
            continue

        if line.strip().startswith("*") and line.strip().endswith("*"):
            # Italic line (reference, disclaimer, etc.)
            text = _strip_md_links(line.strip().strip("*").strip())
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 115, 108)
            pdf.multi_cell(0, 4.5, text)
            pdf.ln(1)
            continue

        if not line.strip():
            pdf.ln(3)
            continue

        # Regular paragraph
        text = _strip_bold(_strip_md_links(line.strip()))
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(30, 55, 45)
        pdf.multi_cell(0, 5, text)
        pdf.ln(1)

    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()
