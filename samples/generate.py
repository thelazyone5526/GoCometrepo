"""Regenerate every sample document and answer file.

From the repo root:

    backend\\.venv\\Scripts\\python -m samples.generate            # writes into samples/
    backend\\.venv\\Scripts\\python -m samples.generate --out DIR  # somewhere else

Output:
- samples/grid/        the 28-document eval grid: 7 versions x 4 conditions (PDF)
- samples/submission/  the three submission samples, one per outcome
- samples/jpg/         one scan saved as JPG, to test the image-upload path

Every document gets ``<name>.answer.json`` beside it. Each document's random choices are
seeded from a fixed seed plus its own ID, so a file comes out identical on every run and
adding a document never changes the others.
"""

from __future__ import annotations

import argparse
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .answers import AnswerFile, answer_path, build_answer, write_answer
from .degrade import CONDITIONS, scan
from .render import image_pdf, render_invoice, scan_date
from .shipments import E1E4, GRID_VERSIONS, V1, V2, Invoice

SEED = 2026
SAMPLES_DIR = Path(__file__).resolve().parent
GRID, SUBMISSION, JPG = "grid", "submission", "jpg"

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


def _clear_generated(folder: Path) -> None:
    """Remove earlier output so renamed or dropped documents don't linger."""
    for pattern in ("*.pdf", "*.jpg", "*.answer.json"):
        for path in folder.glob(pattern):
            path.unlink()


def generate(out_dir: Path = SAMPLES_DIR) -> list[Path]:
    for folder in (GRID, SUBMISSION, JPG):
        (out_dir / folder).mkdir(parents=True, exist_ok=True)
        _clear_generated(out_dir / folder)
    written: list[Path] = []
    for spec in ALL_SPECS:
        built = build(spec)
        path = out_dir / spec.folder / built.file
        path.write_bytes(built.data)
        write_answer(built.answer, answer_path(path))
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
    print(f"{len(paths)} documents (+ {len(paths)} answer files), {total / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
