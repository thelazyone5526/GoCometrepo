"""Draw a commercial invoice as a one-page A4 PDF with a real text layer (fpdf2).

The layout follows a common export invoice: shipper header, a reference table, consignee and
notify party boxes, routing boxes, one line item, packing details (net weight right next to
gross weight), shipping marks, declaration and signature.

Output is byte-for-byte repeatable: the creation date is fixed from the invoice date, and
fpdf2 derives the PDF's file ID from the content plus that date.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .shipments import Invoice

# Page geometry in millimetres (A4 portrait)
PAGE_W, PAGE_H = 210.0, 297.0
LEFT, RIGHT = 12.0, 198.0
WIDTH = RIGHT - LEFT
HALF = WIDTH / 2
QUARTER = WIDTH / 4

INK = (20, 20, 20)
GREY = (85, 85, 85)
LABEL = (70, 70, 70)
HEADER_FILL = (228, 228, 228)

Rect = tuple[float, float, float, float]  # x, y, w, h in mm


@dataclass(frozen=True)
class RenderedInvoice:
    pdf: bytes
    # Where a "RECEIVED" stamp lands on the C3 scan: the gross weight box.
    stamp_target_mm: Rect


def invoice_date(inv: Invoice) -> datetime:
    return datetime.strptime(inv.invoice_date, "%d %b %Y").replace(hour=9, tzinfo=UTC)


def scan_date(inv: Invoice) -> datetime:
    """The day ACME's team scanned the paper copy: one week after the invoice date."""
    return invoice_date(inv) + timedelta(days=7)


def _money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def _text(
    pdf: FPDF,
    x: float,
    y: float,
    w: float,
    s: str,
    *,
    size: float = 9,
    style: str = "",
    align: str = "L",
    color: tuple[int, int, int] = INK,
) -> None:
    """One line of text in a w-wide slot. Refuses to overflow, so every value stays on one
    line and the printed text in the answer file is exactly one line on the page."""
    pdf.set_font("Helvetica", style, size)
    if pdf.get_string_width(s) > w - 2 * pdf.c_margin:
        raise ValueError(f"Text too wide for its {w:.1f} mm slot: {s!r}")
    pdf.set_text_color(*color)
    pdf.set_xy(x, y)
    pdf.cell(w, size * 0.45, s, align=align, new_x=XPos.RIGHT, new_y=YPos.TOP)


def _box(
    pdf: FPDF,
    rect: Rect,
    label: str,
    lines: list[tuple[str, str]],
    *,
    size: float = 9,
    line_gap: float = 4.2,
) -> None:
    """A bordered box with a small grey label and value lines given as (text, style)."""
    x, y, w, h = rect
    pdf.rect(x, y, w, h)
    _text(pdf, x + 0.5, y + 1.0, w - 1, label, size=6.5, style="B", color=LABEL)
    line_y = y + 5.0
    for s, style in lines:
        _text(pdf, x + 0.5, line_y, w - 1, s, size=size, style=style)
        line_y += line_gap


def _header(pdf: FPDF, inv: Invoice) -> None:
    _text(pdf, LEFT, 12, 110, inv.shipper.name, size=14, style="B")
    _text(pdf, RIGHT - 90, 12, 90, "COMMERCIAL INVOICE", size=17, style="B", align="R")
    _text(pdf, RIGHT - 90, 20, 90, "ORIGINAL", size=8, style="B", align="R", color=GREY)
    y = 19.0
    for line in inv.shipper.address:
        _text(pdf, LEFT, y, 110, line, size=8, color=GREY)
        y += 3.6
    pdf.set_line_width(0.6)
    pdf.line(LEFT, 33, RIGHT, 33)
    pdf.set_line_width(0.25)


def _reference_table(pdf: FPDF, inv: Invoice, x: float, y: float, w: float) -> None:
    rows = [
        ("Invoice No.", inv.fields.invoice_number.printed or "", "B"),
        ("Invoice Date", inv.invoice_date, ""),
        ("Buyer's PO No.", inv.po_number, ""),
        ("Payment Terms", inv.payment_terms, ""),
        ("Currency", "USD", ""),
    ]
    label_w, row_h = 30.0, 6.4
    for label, value, style in rows:
        pdf.set_fill_color(*HEADER_FILL)
        pdf.rect(x, y, label_w, row_h, style="DF")
        pdf.rect(x + label_w, y, w - label_w, row_h)
        _text(pdf, x + 0.5, y + 1.3, label_w - 1, label, size=7.5, style="B", color=LABEL)
        _text(pdf, x + label_w + 0.5, y + 1.3, w - label_w - 1, value, size=9, style=style)
        y += row_h


def _item_table(pdf: FPDF, inv: Invoice, y: float) -> float:
    cols = [
        ("No.", 10.0, "C"),
        ("Description of Goods", 78.0, "L"),
        ("HS Code", 20.0, "C"),
        ("Quantity", 24.0, "R"),
        ("Unit Price (USD)", 27.0, "R"),
        ("Amount (USD)", 27.0, "R"),
    ]
    assert abs(sum(c[1] for c in cols) - WIDTH) < 0.01

    def row(values: list[str], h: float, *, style: str = "", fill: bool = False) -> None:
        nonlocal y
        x = LEFT
        for (_, w, align), value in zip(cols, values, strict=True):
            if fill:
                pdf.set_fill_color(*HEADER_FILL)
                pdf.rect(x, y, w, h, style="DF")
            else:
                pdf.rect(x, y, w, h)
            if value:
                size = 7.5 if fill else 9
                _text(
                    pdf, x, y + (h - size * 0.45) / 2, w, value, size=size, style=style, align=align
                )
            x += w
        y += h

    qty = f"{inv.quantity:,} {inv.quantity_unit}"
    row([c[0] for c in cols], 8, style="B", fill=True)
    row(
        [
            "1",
            inv.fields.goods_description.printed or "",
            inv.fields.hs_code.printed or "",
            qty,
            _money(inv.unit_price_cents),
            _money(inv.amount_cents),
        ],
        9,
    )
    row([""] * len(cols), 7)
    row([""] * len(cols), 7)

    # Total row: the first three columns merged
    merged = cols[0][1] + cols[1][1] + cols[2][1]
    pdf.rect(LEFT, y, merged, 8)
    _text(pdf, LEFT, y + 1.8, merged, "TOTAL", size=9, style="B", align="R")
    x = LEFT + merged
    for (_, w, align), value in zip(cols[3:], [qty, "", _money(inv.amount_cents)], strict=True):
        pdf.rect(x, y, w, 8)
        if value:
            _text(pdf, x, y + 1.8, w, value, size=9, style="B", align=align)
        x += w
    return y + 8


def _signature(pdf: FPDF, inv: Invoice, y: float) -> None:
    x = RIGHT - 78
    _text(pdf, x, y, 78, "For and on behalf of", size=8, color=GREY)
    _text(pdf, x, y + 4, 78, inv.shipper.name, size=9, style="B")
    # A plain hand-drawn-looking signature stroke (a fixed shape, so output stays repeatable)
    pdf.set_line_width(0.4)
    points = [
        (x + 8, y + 20),
        (x + 13, y + 13),
        (x + 16, y + 21),
        (x + 21, y + 12),
        (x + 24, y + 19),
        (x + 30, y + 15),
        (x + 38, y + 18),
        (x + 47, y + 14),
    ]
    pdf.polyline(points)
    pdf.set_line_width(0.25)
    pdf.line(x, y + 24, RIGHT, y + 24)
    _text(pdf, x, y + 25, 78, "Authorised Signatory", size=8, color=GREY)


def render_invoice(inv: Invoice) -> RenderedInvoice:
    f = inv.fields
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_creation_date(invoice_date(inv))
    pdf.set_title(f"Commercial Invoice {f.invoice_number.printed or ''}".strip())
    pdf.set_author(inv.shipper.name)
    pdf.set_creator("samples.render (fictional test document)")
    pdf.add_page()
    pdf.set_draw_color(*INK)
    pdf.set_line_width(0.25)

    _header(pdf, inv)

    # Row A: shipper box and reference table
    y = 37.0
    shipper_lines = [(inv.shipper.name, "B")] + [(line, "") for line in inv.shipper.address]
    _box(pdf, (LEFT, y, HALF, 32), "SHIPPER / EXPORTER", shipper_lines, size=8.5)
    _reference_table(pdf, inv, LEFT + HALF, y, HALF)

    # Row B: consignee and notify party
    y = 71.0
    consignee_lines = [(f.consignee.printed or "", "B")] + [(s, "") for s in inv.consignee_address]
    notify_lines = [(inv.notify_party.name, "B")] + [(s, "") for s in inv.notify_party.address]
    _box(pdf, (LEFT, y, HALF, 30), "CONSIGNEE", consignee_lines)
    _box(pdf, (LEFT + HALF, y, HALF, 30), "NOTIFY PARTY", notify_lines)

    # Rows C and D: routing and terms, four boxes each
    def quad(y: float, items: list[tuple[str, str]]) -> None:
        for i, (label, value) in enumerate(items):
            _box(pdf, (LEFT + i * QUARTER, y, QUARTER, 13), label, [(value, "")], size=8.5)

    quad(
        103.0,
        [
            ("PORT OF LOADING", f.port_of_loading.printed or ""),
            ("PORT OF DISCHARGE", f.port_of_discharge.printed or ""),
            ("FINAL DESTINATION", inv.final_destination),
            ("COUNTRY OF ORIGIN", inv.country_of_origin),
        ],
    )
    quad(
        116.0,
        [
            ("VESSEL / VOYAGE", inv.vessel_voyage),
            ("CONTAINER NO.", inv.container_no),
            ("TERMS OF DELIVERY", f.incoterms.printed or ""),
            ("MODE OF TRANSPORT", inv.mode_of_transport),
        ],
    )

    y = _item_table(pdf, inv, 133.0) + 4

    # Packing details: net weight sits right next to gross weight on purpose
    packing = [
        ("TOTAL PACKAGES", inv.packages),
        ("NET WEIGHT", inv.net_weight),
        ("GROSS WEIGHT", f.gross_weight.printed or ""),
        ("MEASUREMENT", inv.measurement),
    ]
    for i, (label, value) in enumerate(packing):
        _box(pdf, (LEFT + i * QUARTER, y, QUARTER, 14), label, [(value, "")])
    gross_box: Rect = (LEFT + 2 * QUARTER, y, QUARTER, 14)
    y += 18

    marks = [(s, "") for s in inv.shipping_marks]
    _box(pdf, (LEFT, y, HALF, 24), "SHIPPING MARKS", marks, size=8.5, line_gap=3.9)
    pdf.rect(LEFT + HALF, y, HALF, 24)
    _text(
        pdf, LEFT + HALF + 0.5, y + 1.0, HALF - 1, "DECLARATION", size=6.5, style="B", color=LABEL
    )
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*INK)
    pdf.set_xy(LEFT + HALF + 0.5, y + 5)
    pdf.multi_cell(
        HALF - 1,
        3.4,
        "We hereby certify that this invoice shows the actual price of the goods described, "
        "that all particulars are true and correct, and that the goods are of Chinese origin.",
    )
    y += 32

    _signature(pdf, inv, y)

    _text(
        pdf,
        LEFT,
        283,
        WIDTH,
        "Page 1 of 1  |  "
        "Sample document for software testing. All companies and data are fictional.",
        size=6.5,
        align="C",
        color=GREY,
    )
    return RenderedInvoice(pdf=bytes(pdf.output()), stamp_target_mm=gross_box)


def image_pdf(jpeg: bytes, created: datetime) -> bytes:
    """Wrap one scanned page image in an A4 PDF with no text layer, like a scanner does.

    fpdf2 embeds JPEG data as-is (DCTDecode), so the PDF holds exactly these image bytes."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_creation_date(created)
    pdf.set_creator("samples.degrade (simulated scan)")
    pdf.add_page()
    pdf.image(io.BytesIO(jpeg), x=0, y=0, w=PAGE_W, h=PAGE_H)
    return bytes(pdf.output())
