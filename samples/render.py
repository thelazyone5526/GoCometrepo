"""Draw a trade document as a one-page A4 PDF with a real text layer (fpdf2).

Three document types, three genuinely different layouts, because a pipeline that only ever saw
one page shape would be learning the shape rather than reading the page:

- commercial invoice: shipper header, a reference table, consignee and notify party boxes,
  routing boxes, one line item, packing details (net weight right next to gross weight),
  shipping marks, declaration and signature;
- bill of lading: the carrier's document -- shipper, consignee and notify boxes, vessel and
  voyage, ports and places, container and seal, one wide particulars table whose columns are
  marks, packages, goods, gross weight and measurement, then the carrier's terms and signature;
- packing list: a carton-by-carton table with quantities and a net and a gross weight on every
  line, totalled at the bottom, plus marks and a packer's certification.

Output is byte-for-byte repeatable: the creation date is fixed from the document's own date,
and fpdf2 derives the PDF's file ID from the content plus that date.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .shipments import BillOfLading, DocumentContent, Invoice, PackingList

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
class RenderedDocument:
    """One rendered page, whatever its document type.

    Part 2 draws bills of lading and packing lists as well as invoices, and the scan pipeline
    only needs two things from any of them, so all three renderers return this.
    """

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


def render_invoice(inv: Invoice) -> RenderedDocument:
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
    return RenderedDocument(pdf=bytes(pdf.output()), stamp_target_mm=gross_box)


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


# =============================================================================================
# Bill of lading and packing list
#
# Both reuse the geometry, colours and text helpers above, so the three layouts stay visually
# of a piece (same page margins, same box style, same grey labels) while the content and the
# arrangement differ. The three helpers below are the only new drawing primitives: a stack of
# label/value rows, a table header, and a table row -- the invoice renderer builds its tables
# inline, and it is left exactly as it was so its committed bytes cannot move.

Column = tuple[str, float, str]  # header, width in mm, alignment


def _document_date(printed: str) -> datetime:
    """A ``18 SEP 2026``-style date as the fixed PDF creation time. Same rule as the invoice."""
    return datetime.strptime(printed, "%d %b %Y").replace(hour=9, tzinfo=UTC)


def _detail_table(
    pdf: FPDF,
    x: float,
    y: float,
    w: float,
    rows: Sequence[tuple[str, str, str]],
    *,
    label_w: float = 32.0,
    row_h: float = 6.4,
) -> None:
    """A stack of (label, value, style) rows with the label cell shaded."""
    for label, value, style in rows:
        pdf.set_fill_color(*HEADER_FILL)
        pdf.rect(x, y, label_w, row_h, style="DF")
        pdf.rect(x + label_w, y, w - label_w, row_h)
        _text(pdf, x + 0.5, y + 1.3, label_w - 1, label, size=7.5, style="B", color=LABEL)
        _text(pdf, x + label_w + 0.5, y + 1.3, w - label_w - 1, value, size=9, style=style)
        y += row_h


def _table_header(pdf: FPDF, cols: Sequence[Column], y: float, h: float = 8.0) -> float:
    assert abs(sum(c[1] for c in cols) - WIDTH) < 0.01
    x = LEFT
    for header, w, _ in cols:
        pdf.set_fill_color(*HEADER_FILL)
        pdf.rect(x, y, w, h, style="DF")
        _text(pdf, x, y + (h - 7 * 0.45) / 2, w, header, size=7, style="B", align="C", color=LABEL)
        x += w
    return y + h


def _table_row(
    pdf: FPDF,
    cols: Sequence[Column],
    y: float,
    cells: Sequence[Sequence[str]],
    h: float,
    *,
    size: float = 8.0,
    style: str = "",
    line_gap: float = 4.2,
) -> float:
    """One row. Each cell is a list of lines, so a tall row can hold a whole paragraph of
    particulars in one column and a single number in the next."""
    x = LEFT
    for (_, w, align), lines in zip(cols, cells, strict=True):
        pdf.rect(x, y, w, h)
        block_h = len(lines) * line_gap if len(lines) > 1 else size * 0.45
        line_y = y + (h - block_h) / 2
        for line in lines:
            if line:
                _text(pdf, x + 0.5, line_y, w - 1, line, size=size, style=style, align=align)
            line_y += line_gap
        x += w
    return y + h


def _footer(pdf: FPDF, page_label: str) -> None:
    _text(
        pdf,
        LEFT,
        283,
        WIDTH,
        f"{page_label}  |  "
        "Sample document for software testing. All companies and data are fictional.",
        size=6.5,
        align="C",
        color=GREY,
    )


def _hand_signature(pdf: FPDF, x: float, y: float) -> None:
    """A fixed hand-drawn-looking stroke, so output stays repeatable."""
    pdf.set_line_width(0.4)
    pdf.polyline(
        [
            (x + 8, y + 20),
            (x + 13, y + 13),
            (x + 16, y + 21),
            (x + 21, y + 12),
            (x + 24, y + 19),
            (x + 30, y + 15),
            (x + 38, y + 18),
            (x + 47, y + 14),
        ]
    )
    pdf.set_line_width(0.25)


def bol_issue_date(bol: BillOfLading) -> datetime:
    return _document_date(bol.issue_date)


def packing_list_date(pl: PackingList) -> datetime:
    return _document_date(pl.issue_date)


BOL_COLUMNS: tuple[Column, ...] = (
    ("Marks and Numbers", 44.0, "L"),
    ("No. and Kind of Packages", 34.0, "L"),
    ("Description of Packages and Goods", 66.0, "L"),
    ("Gross Weight", 22.0, "R"),
    ("Measurement", 20.0, "R"),
)

BOL_TERMS = (
    "RECEIVED by the Carrier the goods specified above in apparent good order and condition "
    "unless otherwise stated, to be transported to such place as agreed, authorised or "
    "permitted herein and subject to all the terms and conditions appearing on the front and "
    "reverse of this Bill of Lading, to which the Merchant agrees by accepting this Bill of "
    "Lading. IN WITNESS whereof the number of original Bills of Lading stated above has been "
    "signed, one of which being accomplished the others to stand void."
)


def render_bol(bol: BillOfLading) -> RenderedDocument:
    """Draw one ocean bill of lading.

    Three things on this page exist to be ignored. "FREIGHT PREPAID" sits where an invoice
    prints its Incoterm; the B/L and booking numbers look like references but are not an
    invoice number; the place of receipt is a container yard, not the port of loading. The net
    weight is printed inside the goods box, two lines above the gross weight, as carriers do.
    """
    f = bol.fields
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_creation_date(bol_issue_date(bol))
    pdf.set_title(f"Bill of Lading {bol.bl_number}")
    pdf.set_author(bol.carrier.name)
    pdf.set_creator("samples.render (fictional test document)")
    pdf.add_page()
    pdf.set_draw_color(*INK)
    pdf.set_line_width(0.25)

    # Header: the carrier's name, not the shipper's -- a bill of lading is the carrier's paper
    _text(pdf, LEFT, 12, 100, bol.carrier.name, size=14, style="B")
    _text(pdf, RIGHT - 90, 12, 90, "BILL OF LADING", size=17, style="B", align="R")
    _text(
        pdf,
        RIGHT - 90,
        20,
        90,
        "FOR OCEAN TRANSPORT OR MULTIMODAL TRANSPORT",
        size=7,
        style="B",
        align="R",
        color=GREY,
    )
    _text(pdf, RIGHT - 90, 24.5, 90, "ORIGINAL", size=8, style="B", align="R", color=GREY)
    y = 19.0
    for line in bol.carrier.address:
        _text(pdf, LEFT, y, 100, line, size=8, color=GREY)
        y += 3.6
    pdf.set_line_width(0.6)
    pdf.line(LEFT, 33, RIGHT, 33)
    pdf.set_line_width(0.25)

    # Row A: shipper, and the carrier's own reference block
    shipper_lines = [(bol.shipper.name, "B")] + [(s, "") for s in bol.shipper.address]
    _box(pdf, (LEFT, 37, HALF, 26), "SHIPPER / EXPORTER", shipper_lines, size=8)
    _detail_table(
        pdf,
        LEFT + HALF,
        37,
        HALF,
        [
            ("B/L No.", bol.bl_number, "B"),
            ("Booking No.", bol.booking_number, ""),
            ("No. of Original B/L", bol.number_of_originals, ""),
            ("Place & Date of Issue", f"{bol.place_of_issue} / {bol.issue_date}", ""),
        ],
        label_w=35.0,
    )

    # Row B: consignee and notify party
    consignee_lines = [(f.consignee.printed or "", "B")] + [(s, "") for s in bol.consignee_address]
    notify_lines = [(bol.notify_party.name, "B")] + [(s, "") for s in bol.notify_party.address]
    _box(pdf, (LEFT, 63, HALF, 26), "CONSIGNEE", consignee_lines)
    _box(pdf, (LEFT + HALF, 63, HALF, 26), "NOTIFY PARTY", notify_lines)

    def quad(y: float, items: list[tuple[str, str]]) -> None:
        for i, (label, value) in enumerate(items):
            _box(pdf, (LEFT + i * QUARTER, y, QUARTER, 14), label, [(value, "")], size=8.5)

    quad(
        89.0,
        [
            ("VESSEL / VOYAGE", bol.vessel_voyage),
            ("PORT OF LOADING", f.port_of_loading.printed or ""),
            ("PLACE OF RECEIPT", bol.place_of_receipt),
            ("FREIGHT & CHARGES", bol.freight_terms),
        ],
    )
    quad(
        103.0,
        [
            ("PORT OF DISCHARGE", f.port_of_discharge.printed or ""),
            ("PLACE OF DELIVERY", bol.place_of_delivery),
            ("CONTAINER NO.", bol.container_no),
            ("SEAL NO.", bol.seal_no),
        ],
    )

    # Particulars: one wide row, because that is how a bill of lading describes a consignment
    y = _table_header(pdf, BOL_COLUMNS, 117.0)
    body_h = 46.0
    _table_row(
        pdf,
        BOL_COLUMNS,
        y,
        [
            list(bol.shipping_marks),
            [
                bol.packages,
                "SAID TO CONTAIN:",
                "1 FCL CONTAINER",
                "SHIPPER'S LOAD,",
                "STOW AND COUNT",
            ],
            [
                f.goods_description.printed or "",
                "",
                f"HS CODE: {f.hs_code.printed or ''}",
                f"NET WEIGHT: {bol.net_weight}",
                "",
                "PARTICULARS FURNISHED BY SHIPPER",
            ],
            [f.gross_weight.printed or ""],
            [bol.measurement],
        ],
        body_h,
    )
    gross_box: Rect = (LEFT + 144.0, y, 22.0, body_h)
    y += body_h

    _box(
        pdf,
        (LEFT, y, HALF, 22),
        "FREIGHT AND CHARGES",
        [(bol.freight_terms, "B"), (f"PAYABLE AT: {bol.place_of_issue}", ""), ("AS ARRANGED", "")],
    )
    _box(
        pdf,
        (LEFT + HALF, y, HALF, 22),
        "DECLARED VALUE / ON BOARD",
        [
            ("DECLARED VALUE: NIL", ""),
            (f"SHIPPED ON BOARD: {bol.issue_date}", ""),
            (f"NUMBER OF ORIGINAL B/L: {bol.number_of_originals}", ""),
        ],
    )
    y += 26

    pdf.rect(LEFT, y, WIDTH, 34)
    _text(pdf, LEFT + 0.5, y + 1.0, WIDTH - 1, "CARRIER'S TERMS", size=6.5, style="B", color=LABEL)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*INK)
    pdf.set_xy(LEFT + 0.5, y + 5)
    pdf.multi_cell(WIDTH - 1, 3.6, BOL_TERMS)
    y += 38

    delivery_lines = [(bol.notify_party.name, "B")] + [(s, "") for s in bol.notify_party.address]
    _box(
        pdf,
        (LEFT, y, HALF, 26),
        "FOR DELIVERY OF GOODS PLEASE APPLY TO",
        delivery_lines,
        size=8.5,
    )
    x = RIGHT - 85
    _text(pdf, x, y, 85, "Signed for the Carrier", size=8, color=GREY)
    _text(pdf, x, y + 4, 85, bol.carrier.name, size=9, style="B")
    _hand_signature(pdf, x, y)
    pdf.line(x, y + 22, RIGHT, y + 22)
    _text(pdf, x, y + 23, 85, "As Agent for the Carrier", size=8, color=GREY)

    _footer(pdf, "Page 1 of 1")
    return RenderedDocument(pdf=bytes(pdf.output()), stamp_target_mm=gross_box)


PACKING_COLUMNS: tuple[Column, ...] = (
    ("Carton Nos.", 22.0, "L"),
    ("Description of Goods", 72.0, "L"),
    ("Quantity", 28.0, "R"),
    ("Net Weight (KGS)", 32.0, "R"),
    ("Gross Weight (KGS)", 32.0, "R"),
)

PACKING_CERTIFICATION = (
    "We certify that the goods listed above are packed as described, that the carton count and "
    "the net and gross weights shown are true and correct, and that no solid wood packaging "
    "material has been used in this shipment."
)


def render_packing_list(pl: PackingList) -> RenderedDocument:
    """Draw one packing list.

    The table is the document: four runs of cartons, each with its own net *and* gross weight,
    totalled at the foot. The gross weight the pipeline wants is the total of the gross column,
    so every other number in that part of the page is a wrong answer that looks plausible.
    """
    f = pl.fields
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_creation_date(packing_list_date(pl))
    pdf.set_title(f"Packing List {pl.packing_list_number}")
    pdf.set_author(pl.shipper.name)
    pdf.set_creator("samples.render (fictional test document)")
    pdf.add_page()
    pdf.set_draw_color(*INK)
    pdf.set_line_width(0.25)

    _text(pdf, LEFT, 12, 100, pl.shipper.name, size=14, style="B")
    _text(pdf, RIGHT - 90, 12, 90, "PACKING LIST", size=17, style="B", align="R")
    _text(
        pdf, RIGHT - 90, 20, 90, "SHIPPER'S PACKING LIST", size=8, style="B", align="R", color=GREY
    )
    y = 19.0
    for line in pl.shipper.address:
        _text(pdf, LEFT, y, 100, line, size=8, color=GREY)
        y += 3.6
    pdf.set_line_width(0.6)
    pdf.line(LEFT, 33, RIGHT, 33)
    pdf.set_line_width(0.25)

    shipper_lines = [(pl.shipper.name, "B")] + [(s, "") for s in pl.shipper.address]
    _box(pdf, (LEFT, 37, HALF, 26), "SHIPPER / EXPORTER", shipper_lines, size=8)
    _detail_table(
        pdf,
        LEFT + HALF,
        37,
        HALF,
        [
            ("Packing List No.", pl.packing_list_number, "B"),
            ("Invoice No.", f.invoice_number.printed or "", "B"),
            ("Invoice Date", pl.invoice_date, ""),
            ("Buyer's PO No.", pl.po_number, ""),
        ],
        label_w=33.0,
    )

    consignee_lines = [(f.consignee.printed or "", "B")] + [(s, "") for s in pl.consignee_address]
    _box(pdf, (LEFT, 63, HALF, 26), "CONSIGNEE", consignee_lines)
    _detail_table(
        pdf,
        LEFT + HALF,
        63,
        HALF,
        [
            ("Vessel / Voyage", pl.vessel_voyage, ""),
            ("Container No.", pl.container_no, ""),
            ("Port of Loading", f.port_of_loading.printed or "", ""),
            ("Port of Discharge", f.port_of_discharge.printed or "", ""),
        ],
        label_w=35.0,
    )

    for i, (label, value) in enumerate(
        [
            ("HS CODE", f.hs_code.printed or ""),
            ("COUNTRY OF ORIGIN", pl.country_of_origin),
            ("TOTAL CARTONS", f"{pl.total_cartons} CARTONS"),
            ("DIMENSIONS / CARTON", pl.carton_dimensions),
        ]
    ):
        _box(pdf, (LEFT + i * QUARTER, 89.0, QUARTER, 14), label, [(value, "")], size=8.5)

    description = f.goods_description.printed or ""
    y = _table_header(pdf, PACKING_COLUMNS, 107.0)
    for line in pl.lines:
        y = _table_row(
            pdf,
            PACKING_COLUMNS,
            y,
            [
                [line.cartons],
                [description],
                [f"{line.quantity:,} {line.quantity_unit}"],
                [f"{line.net_kg:,.2f}"],
                [f"{line.gross_kg:,.2f}"],
            ],
            9.0,
        )

    # Total row: the carton and description columns merged, the three number columns totalled
    merged = PACKING_COLUMNS[0][1] + PACKING_COLUMNS[1][1]
    pdf.rect(LEFT, y, merged, 9)
    _text(pdf, LEFT, y + 2.4, merged, "TOTAL", size=9, style="B", align="R")
    x = LEFT + merged
    totals = [
        f"{pl.total_quantity:,} {pl.lines[0].quantity_unit}",
        f"{pl.total_net_kg:,.2f}",
        f"{pl.total_gross_kg:,.2f}",
    ]
    for (_, w, align), value in zip(PACKING_COLUMNS[2:], totals, strict=True):
        pdf.rect(x, y, w, 9)
        _text(pdf, x + 0.5, y + 2.4, w - 1, value, size=9, style="B", align=align)
        x += w
    y += 13

    summary = [
        ("TOTAL PACKAGES", f"{pl.total_cartons} CARTONS"),
        ("NET WEIGHT", f"{pl.total_net_kg:,.2f} KGS"),
        ("GROSS WEIGHT", f.gross_weight.printed or ""),
        ("MEASUREMENT", pl.measurement),
    ]
    for i, (label, value) in enumerate(summary):
        _box(pdf, (LEFT + i * QUARTER, y, QUARTER, 14), label, [(value, "")])
    gross_box: Rect = (LEFT + 2 * QUARTER, y, QUARTER, 14)
    y += 18

    _box(
        pdf,
        (LEFT, y, HALF, 24),
        "MARKS AND NUMBERS",
        [(s, "") for s in pl.shipping_marks],
        size=8.5,
        line_gap=3.9,
    )
    pdf.rect(LEFT + HALF, y, HALF, 24)
    _text(
        pdf, LEFT + HALF + 0.5, y + 1.0, HALF - 1, "CERTIFICATION", size=6.5, style="B", color=LABEL
    )
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*INK)
    pdf.set_xy(LEFT + HALF + 0.5, y + 5)
    pdf.multi_cell(HALF - 1, 3.4, PACKING_CERTIFICATION)
    y += 28

    _box(
        pdf,
        (LEFT, y, HALF, 24),
        "REMARKS",
        [
            ("All cartons strapped and palletised.", ""),
            ("Carton weights verified on a calibrated scale.", ""),
        ],
        size=8.5,
    )
    x = RIGHT - 85
    _text(pdf, x, y, 85, "Packed and verified by", size=8, color=GREY)
    _text(pdf, x, y + 4, 85, pl.shipper.name, size=9, style="B")
    _hand_signature(pdf, x, y)
    pdf.line(x, y + 22, RIGHT, y + 22)
    _text(pdf, x, y + 23, 85, pl.prepared_by, size=8, color=GREY)

    _footer(pdf, "Page 1 of 1")
    return RenderedDocument(pdf=bytes(pdf.output()), stamp_target_mm=gross_box)


def render_document(content: DocumentContent) -> RenderedDocument:
    """Draw whichever document type this is. One place that knows the mapping, so the generator
    and the ``.eml`` builder don't each need their own ``isinstance`` ladder."""
    if isinstance(content, Invoice):
        return render_invoice(content)
    if isinstance(content, BillOfLading):
        return render_bol(content)
    return render_packing_list(content)
