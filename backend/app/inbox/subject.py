"""Pull the customer id and the shipment reference out of an email subject (requirements 1.4,
1.5, 5.1).

The subject is the only place a watched `.eml` says which customer it belongs to and which
shipment it continues, so these two tokens are what link an email to a rules file and to its
earlier cycle. Both are found by position-independent search, never by splitting the subject into
fields: real subjects put the tokens in either order and wrap prose around them.

Deliberately *not* done here:

* No I/O. The caller passes the customer ids it already loaded, so nothing in this module reads
  a rules file, touches settings or imports `app.rules` / `app.config`. `parse_subject` is a pure
  function, which is why its tests are a plain table of strings.
* No guessing. An unmatched customer or reference comes back as `None`; the shipment layer then
  records the email as failed with a reason (requirement 1.5) rather than this module inventing a
  customer that does not exist.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# `SHP-` followed by digits, the reference format the samples and requirement 5.1 use. Case
# insensitive because operators type `shp-1042` as often as `SHP-1042`.
SHIPMENT_REFERENCE_PATTERN = re.compile(r"SHP-\d+", re.IGNORECASE)


@dataclass(frozen=True)
class SubjectParts:
    customer_id: str | None
    reference: str | None


def find_shipment_reference(subject: str) -> str | None:
    """The first `SHP-<digits>` in the subject, upper-cased, or None.

    First match wins rather than last or "all of them": a reply chain accumulates subjects, and
    the leading token is the one this email is about. Upper-casing is what makes `shp-1042` and
    `SHP-1042` land on the same `shipment_reviews` chain (requirement 5.1).
    """
    if not subject:
        return None
    match = SHIPMENT_REFERENCE_PATTERN.search(subject)
    return match.group(0).upper() if match else None


def _id_pattern(customer_id: str) -> re.Pattern[str]:
    """`customer_id` as a case-insensitive whole-word match.

    The lookarounds, rather than `\\b`, are so an id containing punctuation (`acme-corp`) still
    behaves: what must not happen is `acmecorp` in a subject matching the customer `acme`
    (requirement 1.4), because that would file an email under the wrong rules.
    """
    escaped = re.escape(customer_id)
    return re.compile(rf"(?<![0-9A-Za-z_]){escaped}(?![0-9A-Za-z_])", re.IGNORECASE)


def find_customer_id(subject: str, known_customer_ids: Iterable[str]) -> str | None:
    """The known customer id mentioned in the subject, in its canonical form, or None.

    Matching is case-insensitive, but the value returned is the id exactly as the caller supplied
    it, because that is the key the rules file and `shipments.customer_id` use -- a subject
    shouting `ACME` must not create a customer named `ACME`.

    Where several known ids appear, the one appearing earliest in the subject wins, and at the
    same position the longer id wins, so `acme-corp` beats `acme` instead of either depending on
    the order the caller happened to list them in.
    """
    if not subject:
        return None

    best: tuple[int, int, str] | None = None
    for customer_id in known_customer_ids:
        if not customer_id or not customer_id.strip():
            continue
        match = _id_pattern(customer_id).search(subject)
        if match is None:
            continue
        candidate = (match.start(), -len(customer_id), customer_id)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    return best[2] if best else None


def parse_subject(subject: str, known_customer_ids: Iterable[str]) -> SubjectParts:
    """Both tokens from one subject, in either order (requirements 1.4, 5.1).

    Never raises: any string, including empty, whitespace or punctuation only, yields
    `SubjectParts(None, None)`. A watcher that crashed on a strange subject would stop ingesting
    every later email in the folder, so a strange subject is data, not an error.
    """
    text = subject or ""
    return SubjectParts(
        customer_id=find_customer_id(text, known_customer_ids),
        reference=find_shipment_reference(text),
    )


__all__ = [
    "SHIPMENT_REFERENCE_PATTERN",
    "SubjectParts",
    "find_customer_id",
    "find_shipment_reference",
    "parse_subject",
]
