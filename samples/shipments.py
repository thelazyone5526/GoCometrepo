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
from typing import Literal

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
