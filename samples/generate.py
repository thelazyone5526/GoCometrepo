"""Regenerate every sample document and answer file.

From the repo root:

    backend\\.venv\\Scripts\\python -m samples.generate            # writes into samples/
    backend\\.venv\\Scripts\\python -m samples.generate --out DIR  # somewhere else

Output:
- samples/grid/        the 28-document eval grid: 7 versions x 4 conditions (PDF)
- samples/submission/  the three submission samples, one per outcome
- samples/jpg/         one scan saved as JPG, to test the image-upload path
- samples/inbox/       four ``.eml`` files for the Part 2 inbox trigger

Every document gets ``<name>.answer.json`` beside it. Each document's random choices are
seeded from a fixed seed plus its own ID, so a file comes out identical on every run and
adding a document never changes the others.

The inbox is the same idea one level up. Each ``.eml`` carries its attachments as clean text
PDFs rendered from the ground truth in ``shipments.py`` -- no scan degradation, so the demo and
the end-to-end test never depend on OCR -- and the two real shipments each get a
``<name>.shipment.json`` describing the expected per-document fields, the cross-document
comparisons and the shipment outcome. Nothing in an email comes from the clock or from
randomness, so regenerating the inbox produces byte-identical files.
"""

from __future__ import annotations

import argparse
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .answers import (
    AnswerFile,
    ShipmentAnswerFile,
    answer_path,
    build_answer,
    build_shipment_answer,
    shipment_answer_path,
    write_answer,
)
from .degrade import CONDITIONS, scan
from .emails import Attachment, build_eml, message_id_for, parse_sent_at
from .render import image_pdf, render_document, render_invoice, scan_date
from .shipments import (
    CG_INBOX,
    E1E4,
    GRID_VERSIONS,
    SAMPLE_EMAILS,
    V1,
    V2,
    Invoice,
    SampleEmail,
)

SEED = 2026
SAMPLES_DIR = Path(__file__).resolve().parent
GRID, SUBMISSION, JPG, INBOX = "grid", "submission", "jpg", "inbox"
FOLDERS: tuple[str, ...] = (GRID, SUBMISSION, JPG, INBOX)

_CONDITION_SHORT = {
    "C0": "clean text PDF",
    "C1": "skewed scan",
    "C2": "blurred scan",
    "C3": "noisy low-resolution scan with stamp",
}


@dataclass(frozen=True)
class Spec:
    folder: str
    name: str  # file name without extension
    invoice: Invoice
    condition: str
    description: str
    as_jpg: bool = False

    @property
    def seed_key(self) -> str:
        """Same invoice + condition = same scan, whichever folder or format it lands in."""
        return f"{self.invoice.version}-{self.condition}"

    @property
    def doc_id(self) -> str:
        return self.seed_key + ("-JPG" if self.as_jpg else "")


@dataclass(frozen=True)
class Built:
    spec: Spec
    file: str
    data: bytes
    answer: AnswerFile


def _grid_description(inv: Invoice, condition: str) -> str:
    if inv.planted_errors:
        what = f"{inv.base_version} with planted error {inv.planted_errors[0].code}"
    else:
        what = f"{inv.version}, correct invoice"
    return f"Eval grid: {what}; {_CONDITION_SHORT[condition]}"


GRID_SPECS: tuple[Spec, ...] = tuple(
    Spec(GRID, f"{inv.version}-{c}", inv, c, _grid_description(inv, c))
    for inv in GRID_VERSIONS
    for c in CONDITIONS
)

SUBMISSION_SPECS: tuple[Spec, ...] = (
    Spec(
        SUBMISSION,
        "01-clean-correct",
        V1,
        "C0",
        "Submission sample 1: clean and correct (V1-C0). Should be auto-approved",
    ),
    Spec(
        SUBMISSION,
        "02-clean-two-errors",
        E1E4,
        "C0",
        "Submission sample 2: clean, with two planted errors (HS code 8504.40, gross weight "
        "in LBS). Should produce an amendment request draft",
    ),
    Spec(
        SUBMISSION,
        "03-messy-scan",
        V2,
        "C3",
        "Submission sample 3: messy scan of a correct invoice (V2-C3). Shows how uncertainty "
        "is handled",
    ),
)

JPG_SPECS: tuple[Spec, ...] = (
    Spec(
        JPG,
        "V1-C1",
        V1,
        "C1",
        "Image upload: the V1-C1 skewed scan saved as a JPG instead of a PDF",
        as_jpg=True,
    ),
)

ALL_SPECS: tuple[Spec, ...] = GRID_SPECS + SUBMISSION_SPECS + JPG_SPECS


def rng_for(key: str) -> np.random.Generator:
    return np.random.default_rng([SEED, zlib.crc32(key.encode("ascii"))])


def build(spec: Spec) -> Built:
    """Make one document and its answer in memory."""
    rendered = render_invoice(spec.invoice)
    if spec.condition == "C0":
        data, degradation, suffix = rendered.pdf, None, ".pdf"
    else:
        scanned_on = scan_date(spec.invoice)
        result = scan(
            rendered.pdf,
            spec.condition,
            rng_for(spec.seed_key),
            rendered.stamp_target_mm,
            scanned_on.strftime("%d %b %Y").upper(),
        )
        degradation = result.degradation
        if spec.as_jpg:
            data, suffix = result.jpeg, ".jpg"
        else:
            data, suffix = image_pdf(result.jpeg, scanned_on), ".pdf"
    file = spec.name + suffix
    answer = build_answer(
        spec.invoice,
        doc_id=spec.doc_id,
        file=file,
        condition=spec.condition,
        description=spec.description,
        degradation=degradation,
    )
    return Built(spec=spec, file=file, data=data, answer=answer)


# ---------------------------------------------------------------------------------------------
# The Part 2 inbox: whole shipments, delivered as email


@dataclass(frozen=True)
class BuiltEmail:
    sample: SampleEmail
    file: str
    eml: bytes
    answer: ShipmentAnswerFile | None  # None for the two failure cases: no outcome to expect


def build_email(sample: SampleEmail) -> BuiltEmail:
    """Render this email's attachments and wrap them in one ``.eml``, in memory.

    Attachments are clean text PDFs. The scan conditions belong to the eval grid, whose job is
    measuring how extraction copes with bad images; the inbox's job is the shipment layer, and
    mixing the two would make an end-to-end failure ambiguous.
    """
    attachments = tuple(
        Attachment(filename=doc.filename, data=render_document(doc.content).pdf)
        for doc in sample.documents
    )
    # Keyed on the shipment reference where there is one, so the id a watcher deduplicates on
    # is stable across regenerations; the sample name covers the case with no reference.
    key = sample.reference or sample.name
    message_id = message_id_for(key)
    eml = build_eml(
        sender=sample.sender,
        recipient=CG_INBOX,
        subject=sample.subject,
        body=sample.body,
        sent_at=parse_sent_at(sample.sent_at),
        message_id=message_id,
        attachments=attachments,
        boundary_key=key,
    )
    file = sample.name + ".eml"
    answer = (
        build_shipment_answer(sample, file=file, message_id=message_id, recipient=CG_INBOX)
        if sample.expected_outcome is not None
        else None
    )
    return BuiltEmail(sample=sample, file=file, eml=eml, answer=answer)


def _clear_generated(folder: Path) -> None:
    """Remove earlier output so renamed or dropped documents don't linger."""
    for pattern in ("*.pdf", "*.jpg", "*.eml", "*.answer.json", "*.shipment.json"):
        for path in folder.glob(pattern):
            path.unlink()


def generate(out_dir: Path = SAMPLES_DIR) -> list[Path]:
    for folder in FOLDERS:
        (out_dir / folder).mkdir(parents=True, exist_ok=True)
        _clear_generated(out_dir / folder)
    written: list[Path] = []
    for spec in ALL_SPECS:
        built = build(spec)
        path = out_dir / spec.folder / built.file
        path.write_bytes(built.data)
        write_answer(built.answer, answer_path(path))
        written.append(path)
    for sample in SAMPLE_EMAILS:
        built_email = build_email(sample)
        path = out_dir / INBOX / built_email.file
        path.write_bytes(built_email.eml)
        if built_email.answer is not None:
            write_answer(built_email.answer, shipment_answer_path(path))
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Regenerate the sample documents.")
    parser.add_argument("--out", type=Path, default=SAMPLES_DIR, help="output folder")
    args = parser.parse_args(argv)

    paths = generate(args.out)
    total = 0
    for path in paths:
        size = path.stat().st_size
        total += size
        print(f"{path.relative_to(args.out).as_posix():40} {size / 1024:8.1f} KB")
    documents = [p for p in paths if p.suffix != ".eml"]
    emails = [p for p in paths if p.suffix == ".eml"]
    print(
        f"{len(documents)} documents (+ {len(documents)} answer files), "
        f"{len(emails)} emails, {total / 1024 / 1024:.1f} MB"
    )


if __name__ == "__main__":
    main()
