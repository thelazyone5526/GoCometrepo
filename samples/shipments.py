"""Ground truth for every sample invoice.

Each invoice is written down here once, in plain data, before anything is drawn. The renderer
prints exactly these strings, and the answer files are built from the same objects, so the
documents and their answers can't drift apart.

For each of the 8 fields there are two things:
- ``printed``: the exact text on the page (None when the field is missing from the document);
- ``value``: the expected normalised value, which is what a perfect Extractor returns.
  HS code: digits only. Weight: amount plus unit (KG or LB). Incoterm: the code, place split
  off. Ports: the canonical name used in the rules file. Text fields (consignee, goods
  description, invoice number): the printed text itself; the comparison normalises case,
  spacing and punctuation on both sides.

Everything else on the invoice (shipper, notify party, net weight, vessel, container...) is a
distractor: realistic content the Extractor must not confuse with a field.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import ClassVar, Literal

FIELD_NAMES: tuple[str, ...] = (
    "consignee",
    "hs_code",
    "port_of_loading",
    "port_of_discharge",
    "incoterms",
    "goods_description",
    "gross_weight",
    "invoice_number",
)

LB_PER_KG = 2.20462262


@dataclass(frozen=True)
class Weight:
    amount: float
    unit: Literal["KG", "LB"]


@dataclass(frozen=True)
class Truth:
    """One field: what is printed on the page, and the normalised value it stands for."""

    printed: str | None
    value: str | Weight | None


@dataclass(frozen=True)
class Fields:
    """The 8 fields the pipeline extracts. Attribute names match FIELD_NAMES and the rules."""

    consignee: Truth
    hs_code: Truth
    port_of_loading: Truth
    port_of_discharge: Truth
    incoterms: Truth
    goods_description: Truth
    gross_weight: Truth
    invoice_number: Truth

    def as_dict(self) -> dict[str, Truth]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class Party:
    name: str
    address: tuple[str, ...]


@dataclass(frozen=True)
class PlantedError:
    code: str  # E1..E5
    field: str  # one of FIELD_NAMES
    description: str


@dataclass(frozen=True)
class Invoice:
    """Everything printed on one commercial invoice."""

    # The customer's rules file keys expected_fields by document type, and Part 2 puts several
    # document types in one shipment, so every content class names its own type.
    doc_type: ClassVar[str] = "commercial_invoice"

    version: str  # V1, V2, E1..E5, E1E4
    base_version: str  # the correct invoice this one is derived from
    fields: Fields
    planted_errors: tuple[PlantedError, ...]
    # Distractors and ordinary invoice content
    shipper: Party
    consignee_address: tuple[str, ...]
    notify_party: Party
    invoice_date: str
    po_number: str
    payment_terms: str
    final_destination: str
    country_of_origin: str
    vessel_voyage: str
    container_no: str
    mode_of_transport: str
    quantity: int
    quantity_unit: str
    unit_price_cents: int
    packages: str
    net_weight: str  # printed next to the gross weight: the classic trap
    measurement: str
    shipping_marks: tuple[str, ...]

    @property
    def amount_cents(self) -> int:
        return self.quantity * self.unit_price_cents


# ---------------------------------------------------------------------------------------------
# Small helpers that keep printed text and normalised values consistent


def kg_weight(kg: float) -> Truth:
    return Truth(f"{kg:,.2f} KGS", Weight(round(kg, 2), "KG"))


def lb_weight(kg: float) -> Truth:
    lb = round(kg * LB_PER_KG, 2)
    return Truth(f"{lb:,.2f} LBS", Weight(lb, "LB"))


def hs_code(printed: str) -> Truth:
    return Truth(printed, "".join(ch for ch in printed if ch.isdigit()))


def text(printed: str) -> Truth:
    return Truth(printed, printed)


_ISO6346_VALUES = dict(
    zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [n for n in range(10, 39) if n % 11 != 0], strict=True)
)


def container_number(owner_and_category: str, serial: str) -> str:
    """ISO 6346 container number with a valid check digit, printed as 'ABCU 123456 7'."""
    code = owner_and_category + serial
    total = sum(
        (int(ch) if ch.isdigit() else _ISO6346_VALUES[ch]) * 2**i for i, ch in enumerate(code)
    )
    return f"{owner_and_category} {serial} {total % 11 % 10}"


# ---------------------------------------------------------------------------------------------
# The two correct invoices. Every company and address here is fictional.

ACME = "ACME Electronics Pte. Ltd."
ACME_ADDRESS = (
    "42 Pioneer Crescent, #05-11",
    "Jurong Industrial Estate",
    "Singapore 628566",
)
NOTIFY = Party(
    "Straits Harbour Customs Brokers Pte. Ltd.",
    ("Unit 7-02, Harbourfront Logistics Hub", "Singapore 098634"),
)

V1_GROSS_KG = 862.40
V1_NET_KG = 736.00

V1 = Invoice(
    version="V1",
    base_version="V1",
    fields=Fields(
        consignee=text(ACME),
        hs_code=hs_code("8471.30"),
        port_of_loading=Truth("SHANGHAI, CHINA", "Shanghai"),
        port_of_discharge=Truth("SINGAPORE", "Singapore"),
        incoterms=Truth("CIF Singapore", "CIF"),
        goods_description=text("Portable laptop computers, 14-inch, 16 GB RAM"),
        gross_weight=kg_weight(V1_GROSS_KG),
        invoice_number=text("INV-2026-00417"),
    ),
    planted_errors=(),
    shipper=Party(
        "Brightpath Computing Co., Ltd.",
        (
            "No. 1288 Zhangjiang Road, Pudong New District",
            "Shanghai 201203, China",
            "Tel: +86 21 5555 0100",
        ),
    ),
    consignee_address=ACME_ADDRESS,
    notify_party=NOTIFY,
    invoice_date="18 SEP 2026",
    po_number="ACME-PO-26-0913",
    payment_terms="T/T 30 days after B/L date",
    final_destination="SINGAPORE",
    country_of_origin="CHINA",
    vessel_voyage="PACIFIC HARMONY V.214S",
    container_no=container_number("PHCU", "204817"),
    mode_of_transport="SEA FREIGHT (FCL 20')",
    quantity=400,
    quantity_unit="UNITS",
    unit_price_cents=61_250,
    packages="80 CARTONS",
    net_weight=kg_weight(V1_NET_KG).printed or "",
    measurement="7.84 CBM",
    shipping_marks=("ACME / SINGAPORE", "PO ACME-PO-26-0913", "C/NO. 1-80", "MADE IN CHINA"),
)

V2_GROSS_KG = 3120.00
V2_NET_KG = 2700.00

V2 = Invoice(
    version="V2",
    base_version="V2",
    fields=Fields(
        consignee=text(ACME),
        hs_code=hs_code("8471.60"),
        port_of_loading=Truth("YANTIAN, CHINA", "Yantian"),
        port_of_discharge=Truth("SINGAPORE", "Singapore"),
        incoterms=Truth("CIF Singapore", "CIF"),
        goods_description=text("Wired USB keyboard and optical mouse sets"),
        gross_weight=kg_weight(V2_GROSS_KG),
        invoice_number=text("INV-2026-00533"),
    ),
    planted_errors=(),
    shipper=Party(
        "Jadeleaf Peripherals Co., Ltd.",
        (
            "Building 6, Xili Industrial Park, Nanshan District",
            "Shenzhen 518055, China",
            "Tel: +86 755 5555 0188",
        ),
    ),
    consignee_address=ACME_ADDRESS,
    notify_party=NOTIFY,
    invoice_date="22 SEP 2026",
    po_number="ACME-PO-26-0958",
    payment_terms="T/T 45 days after B/L date",
    final_destination="SINGAPORE",
    country_of_origin="CHINA",
    vessel_voyage="JADE STAR V.087S",
    container_no=container_number("JSTU", "551902"),
    mode_of_transport="SEA FREIGHT (FCL 40')",
    quantity=3000,
    quantity_unit="SETS",
    unit_price_cents=840,
    packages="150 CARTONS",
    net_weight=kg_weight(V2_NET_KG).printed or "",
    measurement="14.25 CBM",
    shipping_marks=("ACME / SINGAPORE", "PO ACME-PO-26-0958", "C/NO. 1-150", "MADE IN CHINA"),
)


# ---------------------------------------------------------------------------------------------
# Error versions: V1 with exactly one planted error each


def _plant(base: Invoice, version: str, errors: list[PlantedError], **changes: object) -> Invoice:
    """Copy `base`, change the named fields (and any distractors), and record the errors."""
    field_changes = {k: v for k, v in changes.items() if k in FIELD_NAMES}
    other_changes = {k: v for k, v in changes.items() if k not in FIELD_NAMES}
    return replace(
        base,
        version=version,
        base_version=base.version,
        fields=replace(base.fields, **field_changes),
        planted_errors=tuple(errors),
        **other_changes,
    )


ERR_HS = PlantedError(
    "E1", "hs_code", "HS code 8504.40 (static converters) is not on ACME's approved list"
)
ERR_CONSIGNEE = PlantedError(
    "E2", "consignee", "Consignee misspelled as 'ACEM Electronics Pte. Ltd.'"
)
ERR_INCOTERM = PlantedError("E3", "incoterms", "Incoterm is FOB; ACME requires CIF")
ERR_WEIGHT_LBS = PlantedError("E4", "gross_weight", "Gross weight is in LBS; ACME requires KG")
ERR_NO_INVOICE_NO = PlantedError("E5", "invoice_number", "Invoice number is missing")

E1 = _plant(V1, "E1", [ERR_HS], hs_code=hs_code("8504.40"))
E2 = _plant(V1, "E2", [ERR_CONSIGNEE], consignee=text("ACEM Electronics Pte. Ltd."))
E3 = _plant(V1, "E3", [ERR_INCOTERM], incoterms=Truth("FOB Shanghai", "FOB"))
# A supplier who writes the gross weight in pounds writes the net weight in pounds too.
# The net weight isn't one of the 8 fields, so E4 still differs from V1 in one field only.
E4 = _plant(
    V1,
    "E4",
    [ERR_WEIGHT_LBS],
    gross_weight=lb_weight(V1_GROSS_KG),
    net_weight=lb_weight(V1_NET_KG).printed,
)
# The "Invoice No." label stays, with an empty value, as when a template is left blank.
E5 = _plant(V1, "E5", [ERR_NO_INVOICE_NO], invoice_number=Truth(None, None))

# Submission sample 2 only (not in the eval grid): two errors, both settled by plain code,
# so the amendment draft in the demo doesn't depend on an LLM judgement.
E1E4 = _plant(
    V1,
    "E1E4",
    [ERR_HS, ERR_WEIGHT_LBS],
    hs_code=hs_code("8504.40"),
    gross_weight=lb_weight(V1_GROSS_KG),
    net_weight=lb_weight(V1_NET_KG).printed,
)

CORRECT_VERSIONS: tuple[Invoice, ...] = (V1, V2)
ERROR_VERSIONS: tuple[Invoice, ...] = (E1, E2, E3, E4, E5)
GRID_VERSIONS: tuple[Invoice, ...] = CORRECT_VERSIONS + ERROR_VERSIONS
ALL_VERSIONS: tuple[Invoice, ...] = GRID_VERSIONS + (E1E4,)

# =============================================================================================
# Part 2: whole shipments, not single invoices
#
# A shipment arrives as one email carrying several documents about the same consignment. The
# interesting failures live *between* those documents: the invoice says ACME, the bill of lading
# says someone else. One document on its own looks fine, so nothing below can be judged by
# reading a single page.
#
# Two document types join the commercial invoice, and both are declared the same way: printed
# text and normalised value side by side, every page a renderer draws comes from these objects,
# and every answer file is built from them too. The one new idea is that a bill of lading and a
# packing list each carry only *some* of the 8 fields. Which ones is the customer's rules file
# talking (``document_types.<type>.expected_fields`` in ``rules/acme.yaml``); the tuples below
# mirror it, because the renderers and the answer files need to know what a page is supposed to
# print before any rule is loaded. A field a document type does not carry gets the verdict
# ``not_applicable`` and cannot affect a decision.


EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "commercial_invoice": FIELD_NAMES,
    "bill_of_lading": (
        "consignee",
        "hs_code",
        "port_of_loading",
        "port_of_discharge",
        "gross_weight",
    ),
    "packing_list": ("consignee", "hs_code", "gross_weight", "invoice_number"),
}

# The four fields the cross-document checker compares. A field is only compared across the
# documents whose Doc_Type lists it above, so the invoice number is checked between the invoice
# and the packing list and the bill of lading is left out of that one.
CROSS_CHECKED_FIELDS: tuple[str, ...] = (
    "consignee",
    "hs_code",
    "gross_weight",
    "invoice_number",
)

DOC_TYPES: tuple[str, ...] = ("commercial_invoice", "bill_of_lading", "packing_list")


@dataclass(frozen=True)
class BillOfLading:
    """Everything printed on one ocean bill of lading.

    The carrier's document, so almost all of it is page furniture the Extractor must ignore: a
    B/L number that looks like a reference but is not an invoice number, "FREIGHT PREPAID" where
    an invoice would print an Incoterm, a booking number, a seal number, a place of receipt that
    is not the port of loading. ``fields.incoterms`` and ``fields.invoice_number`` are therefore
    ``Truth(None, None)``: a bill of lading really does not print either.
    """

    doc_type: ClassVar[str] = "bill_of_lading"

    version: str
    fields: Fields
    planted_errors: tuple[PlantedError, ...]
    bl_number: str
    booking_number: str
    carrier: Party
    shipper: Party
    consignee_address: tuple[str, ...]
    notify_party: Party
    issue_date: str
    place_of_receipt: str
    place_of_delivery: str
    vessel_voyage: str
    container_no: str
    seal_no: str
    packages: str
    net_weight: str  # printed inside the goods box, right beside the gross weight
    measurement: str
    freight_terms: str  # the Incoterm trap: looks like terms, is not an Incoterm
    number_of_originals: str
    place_of_issue: str
    shipping_marks: tuple[str, ...]


@dataclass(frozen=True)
class PackingLine:
    """One line of a packing list: a run of cartons with its own weights."""

    cartons: str  # "C/NO. 1-20"
    carton_count: int
    quantity: int
    quantity_unit: str
    net_kg: float
    gross_kg: float


@dataclass(frozen=True)
class PackingList:
    """Everything printed on one packing list.

    A packing list exists to say what is in which carton, so its weights are the trap: every
    line prints a net *and* a gross weight, and the gross weight the pipeline wants is the
    total of the gross column, not any single line and not the net total.
    """

    doc_type: ClassVar[str] = "packing_list"

    version: str
    fields: Fields
    planted_errors: tuple[PlantedError, ...]
    packing_list_number: str
    issue_date: str
    shipper: Party
    consignee_address: tuple[str, ...]
    invoice_date: str
    po_number: str
    vessel_voyage: str
    container_no: str
    lines: tuple[PackingLine, ...]
    carton_dimensions: str
    measurement: str
    country_of_origin: str
    shipping_marks: tuple[str, ...]
    prepared_by: str

    @property
    def total_cartons(self) -> int:
        return sum(line.carton_count for line in self.lines)

    @property
    def total_quantity(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def total_net_kg(self) -> float:
        return round(sum(line.net_kg for line in self.lines), 2)

    @property
    def total_gross_kg(self) -> float:
        return round(sum(line.gross_kg for line in self.lines), 2)


DocumentContent = Invoice | BillOfLading | PackingList


@dataclass(frozen=True)
class ShipmentDocument:
    """One attachment on a sample email: the file name it arrives under, plus its ground truth."""

    filename: str
    content: DocumentContent

    @property
    def doc_type(self) -> str:
        return self.content.doc_type

    @property
    def fields(self) -> Fields:
        return self.content.fields

    @property
    def planted_errors(self) -> tuple[PlantedError, ...]:
        return self.content.planted_errors

    @property
    def expected_fields(self) -> tuple[str, ...]:
        return EXPECTED_FIELDS[self.doc_type]

    def verdict(self, field_name: str) -> str:
        """What a perfect pipeline returns for one field of this document.

        Derived, never typed out: a field this Doc_Type does not carry is ``not_applicable``, a
        field named by one of this document's planted errors is ``mismatch``, anything else is
        ``match``. So a planted error and the expected verdicts can't drift apart.
        """
        if field_name not in self.expected_fields:
            return "not_applicable"
        if any(e.field == field_name for e in self.planted_errors):
            return "mismatch"
        return "match"


@dataclass(frozen=True)
class SampleEmail:
    """One ``.eml`` in ``samples/inbox/``: the headers, the body and the attachments.

    Two of these are real shipments with an expected outcome. The other two are the failure
    cases the inbox trigger has to survive, so they have no expected outcome: nothing should
    reach a shipment decision, and the point of the sample is that the system records the email
    and says why it stopped rather than dropping it.
    """

    name: str  # file name without the .eml suffix
    description: str
    sender: str
    subject: str  # carries the customer id and the SHP- reference, in either order
    body: str
    sent_at: str  # "%d %b %Y %H:%M" in Singapore time; fixed, never the clock
    customer_id: str | None  # None when no rules file exists for the named customer
    reference: str | None
    documents: tuple[ShipmentDocument, ...]
    expected_outcome: str | None  # None: the email never reaches a shipment decision
    expected_failure: str | None  # plain-language reason the operator should see
    cross_document_errors: tuple[PlantedError, ...] = ()

    @property
    def attachment_filenames(self) -> tuple[str, ...]:
        return tuple(d.filename for d in self.documents)

    def cross_check(self, field_name: str) -> tuple[str, tuple[ShipmentDocument, ...]]:
        """Expected cross-document result for one field, plus the documents compared.

        Fewer than two carriers is ``not_applicable`` -- there is nothing to compare, and
        calling that "consistent" would turn a missing document into a clean bill of health. A
        carrier that prints nothing where its Doc_Type says it should is ``inconsistent``, not
        ignored.
        """
        carriers = tuple(d for d in self.documents if field_name in d.expected_fields)
        if len(carriers) < 2:
            return "not_applicable", carriers
        values = [getattr(d.fields, field_name).value for d in carriers]
        if any(v is None for v in values):
            return "inconsistent", carriers
        result = "consistent" if all(v == values[0] for v in values) else "inconsistent"
        return result, carriers

    @property
    def discrepancy_fields(self) -> tuple[str, ...]:
        """Field names an operator has to look at: mismatched on a document, or inconsistent
        between documents. Counted once per field, because one wrong HS code copied onto three
        documents is one problem to fix, not three."""
        bad = {
            name
            for d in self.documents
            for name in FIELD_NAMES
            if d.verdict(name) == "mismatch"
        }
        bad |= {n for n in CROSS_CHECKED_FIELDS if self.cross_check(n)[0] == "inconsistent"}
        return tuple(n for n in FIELD_NAMES if n in bad)

    @property
    def planted_errors(self) -> tuple[PlantedError, ...]:
        """Every problem in this shipment: each document's own, plus the cross-document ones.
        De-duplicated by (code, field), since one wrong HS code is declared once per page."""
        seen: dict[tuple[str, str], PlantedError] = {}
        for err in self.cross_document_errors:
            seen[(err.code, err.field)] = err
        for doc in self.documents:
            for err in doc.planted_errors:
                seen.setdefault((err.code, err.field), err)
        return tuple(seen.values())


# ---------------------------------------------------------------------------------------------
# Deriving a bill of lading and a packing list from the invoice they belong to
#
# A real consignment is described by several documents, so the honest way to declare one is
# once. Both factories below take the invoice and copy the fields the other document type also
# carries, which means the three documents of a shipment agree by construction. A planted
# cross-document mismatch is then one argument -- a single visible line of data -- instead of
# two values somebody has to remember to keep in step.


def bol_for(
    inv: Invoice,
    *,
    bl_number: str,
    booking_number: str,
    seal_no: str,
    issue_date: str,
    place_of_receipt: str,
    place_of_delivery: str,
    consignee: Truth | None = None,
    consignee_address: tuple[str, ...] | None = None,
    extra_errors: tuple[PlantedError, ...] = (),
) -> BillOfLading:
    """The same consignment as `inv`, seen from the carrier's side.

    Consignee, HS code, both ports, the goods description and the gross weight come straight
    from the invoice. Pass `consignee` to make the bill of lading name a different party: that
    is the cross-document mismatch, and it is the only way to create one here.

    Which of the invoice's planted errors still apply is derived, not restated: an error on a
    field a bill of lading carries travels with it (a supplier who used the wrong HS code used
    it on every page), an error on a field it does not carry cannot.
    """
    fields = replace(
        inv.fields,
        incoterms=Truth(None, None),  # a B/L prints freight terms, never an Incoterm
        invoice_number=Truth(None, None),  # the B/L number is not an invoice number
    )
    if consignee is not None:
        fields = replace(fields, consignee=consignee)
    carried = tuple(e for e in inv.planted_errors if e.field in EXPECTED_FIELDS["bill_of_lading"])
    return BillOfLading(
        version=f"{inv.version}-BOL",
        fields=fields,
        planted_errors=carried + extra_errors,
        bl_number=bl_number,
        booking_number=booking_number,
        carrier=PACIFIC_BLUE_LINE,
        shipper=inv.shipper,
        consignee_address=consignee_address or inv.consignee_address,
        notify_party=inv.notify_party,
        issue_date=issue_date,
        place_of_receipt=place_of_receipt,
        place_of_delivery=place_of_delivery,
        vessel_voyage=inv.vessel_voyage,
        container_no=inv.container_no,
        seal_no=seal_no,
        packages=inv.packages,
        net_weight=inv.net_weight,
        measurement=inv.measurement,
        freight_terms="FREIGHT PREPAID",
        number_of_originals="THREE (3)",
        place_of_issue="SHANGHAI, CHINA",
        shipping_marks=inv.shipping_marks,
    )


def packing_list_for(
    inv: Invoice,
    *,
    packing_list_number: str,
    issue_date: str,
    lines: tuple[PackingLine, ...],
    carton_dimensions: str,
    prepared_by: str,
    extra_errors: tuple[PlantedError, ...] = (),
) -> PackingList:
    """The same consignment as `inv`, counted carton by carton.

    Keeps the invoice's consignee, HS code, invoice number, goods description and gross weight;
    drops the Incoterm, which a packing list does not print. The routing stays on the page
    because real packing lists repeat it, even though the rules do not check it there.
    """
    fields = replace(inv.fields, incoterms=Truth(None, None))
    carried = tuple(e for e in inv.planted_errors if e.field in EXPECTED_FIELDS["packing_list"])
    return PackingList(
        version=f"{inv.version}-PL",
        fields=fields,
        planted_errors=carried + extra_errors,
        packing_list_number=packing_list_number,
        issue_date=issue_date,
        shipper=inv.shipper,
        consignee_address=inv.consignee_address,
        invoice_date=inv.invoice_date,
        po_number=inv.po_number,
        vessel_voyage=inv.vessel_voyage,
        container_no=inv.container_no,
        lines=lines,
        carton_dimensions=carton_dimensions,
        measurement=inv.measurement,
        country_of_origin=inv.country_of_origin,
        shipping_marks=inv.shipping_marks,
        prepared_by=prepared_by,
    )


# ---------------------------------------------------------------------------------------------
# The parties and cartons the two sample shipments are built from

PACIFIC_BLUE_LINE = Party(
    "Pacific Blue Line (S) Pte. Ltd.",
    ("as Carrier, 9 Maritime Square, #11-02", "Harbourfront Centre, Singapore 099253"),
)

# A different company, not a misspelling of ACME. The cross-document check has to be settled by
# plain code, so the two names must be far enough apart that no fuzzy match and no model
# judgement is involved: "Zenith Trading Holdings" is simply not "ACME Electronics".
ZENITH = "Zenith Trading Holdings Pte. Ltd."
ZENITH_ADDRESS = (
    "18 Keppel Distripark Road, #02-04",
    "Singapore 089065",
)

# 80 cartons in four runs of 20. The quantities and weights add up to the invoice's 400 units,
# 736.00 KG net and 862.40 KG gross; 70 x 40 x 35 cm per carton is exactly the 7.84 CBM the
# invoice declares. A packing list whose totals contradicted its invoice would be a second
# planted error nobody asked for.
V1_PACKING_LINES: tuple[PackingLine, ...] = tuple(
    PackingLine(
        cartons=f"C/NO. {start}-{start + 19}",
        carton_count=20,
        quantity=100,
        quantity_unit="UNITS",
        net_kg=184.00,
        gross_kg=215.60,
    )
    for start in (1, 21, 41, 61)
)
V1_CARTON_DIMENSIONS = "70 x 40 x 35 CM"


# ---------------------------------------------------------------------------------------------
# Shipment 1: SHP-1042, clean. Three documents, all agreeing, everything inside ACME's rules.

BOL_1042 = bol_for(
    V1,
    bl_number="PBLSHA2609417",
    booking_number="BKG-SHA-260918-41",
    seal_no="SGL0471823",
    issue_date="20 SEP 2026",
    place_of_receipt="SHANGHAI CY",
    place_of_delivery="SINGAPORE CY",
)
PL_1042 = packing_list_for(
    V1,
    packing_list_number="PL-2026-00417",
    issue_date="18 SEP 2026",
    lines=V1_PACKING_LINES,
    carton_dimensions=V1_CARTON_DIMENSIONS,
    prepared_by="Lin Wei, Export Documentation",
)


# ---------------------------------------------------------------------------------------------
# Shipment 2: SHP-1043, messy. Two problems, both settled by plain code:
#
# E1  the HS code is 8504.40, which is not on ACME's approved list. The supplier copied it onto
#     every page, so the three documents agree with each other and still break the rule: this is
#     the discrepancy a single document could have caught.
# X1  the bill of lading consigns the goods to a different company. Every document is internally
#     fine; only comparing them finds it. This is the discrepancy Part 2 exists for.
#
# A different consignment from SHP-1042, not a resubmission of it, so it carries its own invoice
# number, date, PO, vessel and container.

ERR_XDOC_CONSIGNEE = PlantedError(
    "X1",
    "consignee",
    "Bill of lading consigns the goods to Zenith Trading Holdings Pte. Ltd. while the invoice "
    "and packing list name ACME Electronics Pte. Ltd.: the consignee differs between documents",
)

INV_1043 = _plant(
    V1,
    "SHP-1043-INV",
    [ERR_HS],
    hs_code=hs_code("8504.40"),
    invoice_number=text("INV-2026-00604"),
    invoice_date="29 SEP 2026",
    po_number="ACME-PO-26-1004",
    vessel_voyage="PACIFIC HARMONY V.219S",
    container_no=container_number("PHCU", "219663"),
    shipping_marks=("ACME / SINGAPORE", "PO ACME-PO-26-1004", "C/NO. 1-80", "MADE IN CHINA"),
)
BOL_1043 = bol_for(
    INV_1043,
    bl_number="PBLSHA2610604",
    booking_number="BKG-SHA-260929-77",
    seal_no="SGL0519664",
    issue_date="30 SEP 2026",
    place_of_receipt="SHANGHAI CY",
    place_of_delivery="SINGAPORE CY",
    consignee=text(ZENITH),
    consignee_address=ZENITH_ADDRESS,
    extra_errors=(ERR_XDOC_CONSIGNEE,),
)
PL_1043 = packing_list_for(
    INV_1043,
    packing_list_number="PL-2026-00604",
    issue_date="29 SEP 2026",
    lines=V1_PACKING_LINES,
    carton_dimensions=V1_CARTON_DIMENSIONS,
    prepared_by="Lin Wei, Export Documentation",
)


# ---------------------------------------------------------------------------------------------
# The four sample emails

SU_SENDER = "Brightpath Export Documentation <docs@brightpath-computing.example>"
GLOBEX_SENDER = "Globex Trading Exports <exports@globex-trading.example>"
CG_INBOX = "ACME Shipment Desk <shipments@acme-logistics.example>"


def _doc(filename: str, content: DocumentContent) -> ShipmentDocument:
    return ShipmentDocument(filename=filename, content=content)


CLEAN_SHIPMENT = SampleEmail(
    name="01-clean-shipment",
    description=(
        "Inbox sample 1: SHP-1042, three documents about one consignment -- commercial invoice, "
        "bill of lading, packing list. All three agree and every value is inside ACME's rules, "
        "so the shipment should be recommended for approval"
    ),
    sender=SU_SENDER,
    subject="SHP-1042 acme - documents for approval",
    body=(
        "Dear ACME Shipment Desk,\n\n"
        "Please find attached the commercial invoice, bill of lading and packing list for "
        "shipment SHP-1042 (PO ACME-PO-26-0913), sailing on PACIFIC HARMONY V.214S.\n\n"
        "Kindly confirm so we can release the originals.\n\n"
        "Best regards,\n"
        "Lin Wei\n"
        "Export Documentation, Brightpath Computing Co., Ltd.\n"
    ),
    sent_at="19 SEP 2026 09:12",
    customer_id="acme",
    reference="SHP-1042",
    documents=(
        _doc("SHP-1042-commercial-invoice.pdf", V1),
        _doc("SHP-1042-bill-of-lading.pdf", BOL_1042),
        _doc("SHP-1042-packing-list.pdf", PL_1042),
    ),
    expected_outcome="auto_approve",
    expected_failure=None,
)

MESSY_SHIPMENT = SampleEmail(
    name="02-messy-shipment",
    description=(
        "Inbox sample 2: SHP-1043, three documents with two problems -- HS code 8504.40 is not "
        "on ACME's approved list (on every page), and the bill of lading consigns the goods to "
        "Zenith Trading Holdings instead of ACME. Should produce an amendment request"
    ),
    sender=SU_SENDER,
    subject="acme SHP-1043 shipment documents for checking",
    body=(
        "Dear ACME Shipment Desk,\n\n"
        "Attached are the documents for shipment SHP-1043 (PO ACME-PO-26-1004) on PACIFIC "
        "HARMONY V.219S: commercial invoice, bill of lading and packing list.\n\n"
        "Please review and let us know if anything needs amending before the vessel sails.\n\n"
        "Best regards,\n"
        "Lin Wei\n"
        "Export Documentation, Brightpath Computing Co., Ltd.\n"
    ),
    sent_at="30 SEP 2026 11:48",
    customer_id="acme",
    reference="SHP-1043",
    documents=(
        _doc("SHP-1043-commercial-invoice.pdf", INV_1043),
        _doc("SHP-1043-bill-of-lading.pdf", BOL_1043),
        _doc("SHP-1043-packing-list.pdf", PL_1043),
    ),
    expected_outcome="amendment_request",
    expected_failure=None,
    cross_document_errors=(ERR_XDOC_CONSIGNEE,),
)

# The two failure cases the inbox trigger has to survive (R1.7). Both are ordinary, well-formed
# emails: the point is that an email the system cannot act on is still recorded, with a reason a
# person can read, instead of being silently skipped.

UNKNOWN_CUSTOMER_EMAIL = SampleEmail(
    name="03-unknown-customer",
    description=(
        "Inbox sample 3: a valid-looking email whose subject names 'globex', a customer with no "
        "rules file. Should be recorded and failed loudly, with no run started"
    ),
    sender=GLOBEX_SENDER,
    subject="SHP-2001 globex - documents",
    body=(
        "Hello,\n\n"
        "Attached is the commercial invoice for shipment SHP-2001. Please process.\n\n"
        "Regards,\n"
        "Globex Trading Exports\n"
    ),
    sent_at="01 OCT 2026 08:05",
    customer_id=None,
    reference="SHP-2001",
    documents=(_doc("SHP-2001-commercial-invoice.pdf", V1),),
    expected_outcome=None,
    expected_failure="No rules file for customer 'globex', so the document cannot be checked",
)

NO_ATTACHMENTS_EMAIL = SampleEmail(
    name="04-no-attachments",
    description=(
        "Inbox sample 4: a valid acme subject and a real body, but zero attachments. Should be "
        "recorded and failed loudly rather than treated as an empty shipment"
    ),
    sender=SU_SENDER,
    subject="acme SHP-1044 - documents to follow",
    body=(
        "Dear ACME Shipment Desk,\n\n"
        "Booking SHP-1044 is confirmed on PACIFIC HARMONY V.221S. The commercial invoice, bill "
        "of lading and packing list will follow in a separate mail once the vessel is loaded.\n\n"
        "Best regards,\n"
        "Lin Wei\n"
        "Export Documentation, Brightpath Computing Co., Ltd.\n"
    ),
    sent_at="02 OCT 2026 16:30",
    customer_id="acme",
    reference="SHP-1044",
    documents=(),
    expected_outcome=None,
    expected_failure="Email has no attachments, so there is nothing to verify",
)

SAMPLE_SHIPMENTS: tuple[SampleEmail, ...] = (CLEAN_SHIPMENT, MESSY_SHIPMENT)
SAMPLE_FAILURE_EMAILS: tuple[SampleEmail, ...] = (UNKNOWN_CUSTOMER_EMAIL, NO_ATTACHMENTS_EMAIL)
SAMPLE_EMAILS: tuple[SampleEmail, ...] = SAMPLE_SHIPMENTS + SAMPLE_FAILURE_EMAILS
