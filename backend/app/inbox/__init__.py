"""The inbox trigger: turn a `.eml` file in the watched folder into a shipment (requirement 1).

Two pure pieces so far, both free of I/O beyond the bytes handed to them:
`eml.parse_eml` reads one email into sender, subject, body and attachments, and `subject`
finds the customer id and `SHP-` reference inside a subject line. Neither decides anything about
a document -- file checks stay in `app.ingest.files`, and the watcher that lists the folder and
starts the pipeline lives beside these in a later step.
"""

from .eml import Attachment, EmlError, ParsedEmail, parse_eml
from .subject import SubjectParts, find_customer_id, find_shipment_reference, parse_subject

__all__ = [
    "Attachment",
    "EmlError",
    "ParsedEmail",
    "SubjectParts",
    "find_customer_id",
    "find_shipment_reference",
    "parse_eml",
    "parse_subject",
]
