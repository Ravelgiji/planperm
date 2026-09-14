"""PDF overlay for the Galway City Council planning application form.

Takes a project profile (from intake) and optional personal details,
overlays text onto the blank form PDF, and returns the filled PDF bytes.

The coordinates are mapped from the actual Galway form (A4, 595.32 x 841.92 pt).
Each field position was extracted by reading text element coordinates from the
original PDF. If the form layout changes, these positions need remapping.

Nothing is stored — the filled PDF exists only in the download response.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

FORM_PATH = Path(__file__).resolve().parents[1] / "docs" / "galway_draft_base.pdf"

# Page dimensions (A4)
PAGE_W = 595.32
PAGE_H = 841.92


def _build_overlay(
    profile: dict[str, Any],
    personal: dict[str, Any] | None = None,
) -> dict[int, list[tuple[float, float, str, float]]]:
    """Map profile + personal data to (page, x, y, text, font_size) entries.

    Returns {page_index: [(x, y, text, font_size), ...]}.
    Pages are 0-indexed.
    """
    personal = personal or {}
    fields: dict[int, list[tuple[float, float, str, float]]] = {}

    def add(page: int, x: float, y: float, text: str, size: float = 9.0) -> None:
        if not text:
            return
        fields.setdefault(page, []).append((x, y, text, size))

    # -- Page 2 (index 2): Questions 1-7 --

    # Q1: Type of permission — tick "Permission" box
    # The tick boxes are at y≈759. "Permission" is at x≈55, "Outline" at x≈55 y≈742
    ptype = (personal.get("permission_type") or "permission").lower()
    if "retention" in ptype:
        add(2, 315, 759, "X", 12.0)
    elif "outline" in ptype:
        add(2, 42, 742, "X", 12.0)
    else:
        add(2, 42, 759, "X", 12.0)  # default: Permission

    # Q2: Location of proposed development
    location = personal.get("site_address") or profile.get("site_label", "")
    if location:
        # Three lines available, starting at y=646
        lines = _wrap(location, 90)
        for i, line in enumerate(lines[:3]):
            add(2, 34, 648 - (i * 17), line)

    # Q3: Name of applicant
    name = personal.get("applicant_name", "")
    if name:
        add(2, 34, 540, name)

    # Q5: Agent name
    agent = personal.get("agent_name", "")
    if agent:
        add(2, 60, 305, agent)

    # Q7: Description of proposed development
    desc = _build_description(profile)
    if desc:
        desc_lines = _wrap(desc, 95)
        y_start = 189
        for i, line in enumerate(desc_lines[:5]):
            add(2, 34, y_start - (i * 17), line)

    # -- Page 3 (index 3): Questions 8-12 --

    # Q8: Legal interest
    interest = (personal.get("legal_interest") or "owner").lower()
    if "occupier" in interest:
        add(3, 285, 755, "X", 12.0)
    elif "other" in interest:
        add(3, 462, 755, "X", 12.0)
    else:
        add(3, 42, 755, "X", 12.0)  # Owner

    # Q8: Owner name (if not the owner)
    if interest != "owner" and personal.get("owner_name"):
        add(3, 34, 654, personal["owner_name"])

    # Q9: Site area in hectares
    site_ha = profile.get("site_area_hectares", "")
    if site_ha:
        add(3, 370, 570, str(site_ha))

    # Q10: Gross floor space
    floor_area = profile.get("floor_area_sqm", "")
    if floor_area:
        # "proposed works in m²" field — right-aligned area
        add(3, 490, 475, str(floor_area))

    # Q12: Residential mix — bedroom breakdown
    bedrooms = profile.get("bedrooms", "")
    storeys = profile.get("storeys", "")
    if bedrooms:
        try:
            bed_count = int(bedrooms)
            # The row "Houses" is at y≈180. Columns: Studio(104), 1Bed(171), 2Bed(239), 3Bed(306), 4Bed(374), 4+bed(441), Total(509)
            bed_columns = {1: 171, 2: 239, 3: 306, 4: 374}
            col_x = bed_columns.get(bed_count, 441)  # 4+ for anything above 4
            add(3, col_x, 167, "1")
            add(3, 509, 167, "1")  # Total
        except ValueError:
            pass

    # -- Page 6 (index 6): Q18 Services --

    # Water supply
    water = (profile.get("water_supply") or "").lower()
    if "mains" in water or "public" in water:
        add(6, 42, 176, "X", 12.0)  # New Connection to Public Mains
    elif water:
        add(6, 42, 154, "X", 12.0)  # Other
        add(6, 250, 154, water)

    # Wastewater
    ww = (profile.get("wastewater") or "").lower()
    if "mains" in ww or "public" in ww or "sewer" in ww:
        add(6, 218, 108, "X", 12.0)  # Public Sewer
    elif "septic" in ww or "treatment" in ww:
        add(6, 325, 108, "X", 12.0)  # Other on-site treatment system
        add(6, 165, 85, ww)

    # -- Page 11 (index 11): Q22 Applicant contact details --

    if personal.get("applicant_name"):
        add(11, 120, 638, personal["applicant_name"])
    if personal.get("phone"):
        add(11, 120, 626, personal["phone"])
    if personal.get("address"):
        addr_lines = _wrap(personal["address"], 70)
        for i, line in enumerate(addr_lines[:3]):
            add(11, 120, 604 - (i * 15), line)
    if personal.get("email"):
        add(11, 120, 580, personal["email"])

    # Q25: Owner details (if different from applicant)
    if personal.get("owner_name") and personal.get("legal_interest", "owner").lower() != "owner":
        add(11, 120, 150, personal["owner_name"])
        if personal.get("owner_address"):
            oa_lines = _wrap(personal["owner_address"], 70)
            for i, line in enumerate(oa_lines[:2]):
                add(11, 120, 116 - (i * 15), line)

    return fields


def _build_description(profile: dict[str, Any]) -> str:
    """Build the Q7 description from the intake profile."""
    parts: list[str] = []

    dtype = profile.get("development_type", "")
    storeys = profile.get("storeys", "")
    bedrooms = profile.get("bedrooms", "")
    area = profile.get("floor_area_sqm", "")
    garage = profile.get("garage", "")
    existing = profile.get("existing_structures", "")

    if dtype:
        if storeys:
            word = "storey" if storeys == "1" else "storey"
            parts.append(f"Construction of a {storeys}-{word}")
        else:
            parts.append(dtype.capitalize())

        if "dwelling" in dtype.lower() or "house" in dtype.lower():
            parts[-1] += " dwelling"
        elif "extension" in dtype.lower():
            pass  # already says extension
        else:
            parts[-1] += f" ({dtype})"

    if bedrooms:
        parts.append(f"comprising {bedrooms} bedrooms")
    if area:
        parts.append(f"with a gross floor area of approximately {area} sq.m")
    if garage and garage.lower() not in ("no", "none"):
        if garage.lower() in ("detached", "attached"):
            parts.append(f"with {garage.lower()} garage")
        else:
            parts.append("with garage")
    if existing and existing.lower() not in ("greenfield", "none", "empty"):
        parts.append(f"on site with {existing}")

    if not parts:
        return ""

    return ", ".join(parts) + "."


def _wrap(text: str, max_chars: int) -> list[str]:
    """Word-wrap text into lines of roughly max_chars."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def fill_form(
    profile: dict[str, Any],
    personal: dict[str, Any] | None = None,
    form_path: str | Path | None = None,
) -> bytes:
    """Overlay profile + personal data onto the Galway planning form.

    Returns the filled PDF as bytes. Nothing is saved to disk.
    """
    from fpdf import FPDF

    src_path = Path(form_path) if form_path else FORM_PATH
    reader = PdfReader(str(src_path))
    writer = PdfWriter()

    overlay_data = _build_overlay(profile, personal)

    for page_idx, page in enumerate(reader.pages):
        if page_idx in overlay_data:
            # Build a single-page overlay with fpdf2
            box = page.mediabox
            w_pt, h_pt = float(box.width), float(box.height)
            # fpdf2 works in mm; 1pt = 0.3528mm
            w_mm = w_pt * 0.3528
            h_mm = h_pt * 0.3528

            overlay_pdf = FPDF(unit="pt", format=(w_pt, h_pt))
            overlay_pdf.add_page()
            overlay_pdf.set_auto_page_break(auto=False)

            for x, y, text, font_size in overlay_data[page_idx]:
                overlay_pdf.set_font("Helvetica", "", font_size)
                overlay_pdf.set_text_color(0, 25, 100)  # dark blue
                overlay_pdf.text(x, h_pt - y + (font_size * 0.3), text)

            overlay_buf = io.BytesIO()
            overlay_pdf.output(overlay_buf)
            overlay_buf.seek(0)
            overlay_page = PdfReader(overlay_buf).pages[0]
            page.merge_page(overlay_page)

        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
