"""Turn a customer's YAML rules file into a validated `RuleSet` (design section 3.4, Phase 6).

The rule types are a small fixed set (`equals`, `in_list`, `pattern`, `quantity`,
`entity_name`, `llm_judgement`), each its own Pydantic model, picked by a `type` discriminator.
An unknown `type`, or a rule missing a field its type requires, fails as soon as the file is
loaded -- a clear `RuleLoadError` naming the customer and the problem -- never partway through
a run. A new customer means a new YAML file like `rules/acme.yaml`, not new code here.

`expected_fields` per document type is Nova's "schema routing" (design section 3.4): every
field named there must have a matching entry under `rules`, checked here too, since a field
the Validator is expected to check but has no rule for would otherwise only be discovered when
a real document hit it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_RULES_DIR = BACKEND_DIR / "rules"


class RuleLoadError(Exception):
    """The rules file is missing, isn't valid YAML, or fails validation once parsed -- always
    raised when the file is loaded, never in the middle of validating a document."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EqualsRule(_Strict):
    id: str
    type: Literal["equals"]
    value: str


class InListRule(_Strict):
    id: str
    type: Literal["in_list"]
    allowed: list[str]
    # For hs_code: compare only the first N normalised digits, since a supplier's HS code can
    # carry more digits of precision than ACME's rule cares about.
    compare_digits: int | None = None


class PatternRule(_Strict):
    id: str
    type: Literal["pattern"]
    regex: str
    # Placeholder values ("TBD", "N/A") that must never pass even if they happened to match
    # the regex.
    reject: list[str] = Field(default_factory=list)


class QuantityRule(_Strict):
    id: str
    type: Literal["quantity"]
    unit: str
    min_exclusive: float | None = None
    max_exclusive: float | None = None
    min: float | None = None
    max: float | None = None


class EntityNameRule(_Strict):
    id: str
    type: Literal["entity_name"]
    registered: str
    aliases: list[str] = Field(default_factory=list)


class LlmJudgementRule(_Strict):
    id: str
    type: Literal["llm_judgement"]
    criterion: str


Rule = Annotated[
    EqualsRule | InListRule | PatternRule | QuantityRule | EntityNameRule | LlmJudgementRule,
    Field(discriminator="type"),
]


class DocumentTypeRules(_Strict):
    expected_fields: list[str]


class RuleSet(_Strict):
    customer_id: str
    customer_name: str
    document_types: dict[str, DocumentTypeRules]
    rules: dict[str, Rule]

    def expected_fields_for(self, document_type: str) -> frozenset[str]:
        """Which fields this document type expects (schema routing). A document type not
        listed at all expects nothing, so every field on it becomes `not_applicable` rather
        than raising -- an unrecognised document type is itself something the Router should
        see reflected in the fields, not a crash."""
        doc_rules = self.document_types.get(document_type)
        return frozenset(doc_rules.expected_fields) if doc_rules else frozenset()


def _check_every_expected_field_has_a_rule(raw: dict, customer_id: str) -> None:
    rules = raw.get("rules") or {}
    for doc_type, doc_rules in (raw.get("document_types") or {}).items():
        for field_name in doc_rules.get("expected_fields") or []:
            if field_name not in rules:
                raise RuleLoadError(
                    f"{customer_id}: document type {doc_type!r} expects field "
                    f"{field_name!r}, but 'rules' has no entry for it"
                )


def load_rules(path: Path) -> RuleSet:
    """Load and validate one customer's rules file."""
    if not path.exists():
        raise RuleLoadError(f"No rules file at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuleLoadError(f"{path}: expected a YAML mapping at the top level")

    customer_id = raw.get("customer_id", path.stem)
    _check_every_expected_field_has_a_rule(raw, customer_id)

    try:
        return RuleSet.model_validate(raw)
    except ValidationError as exc:
        raise RuleLoadError(f"{path}: {exc}") from exc


def load_rules_for_customer(customer_id: str, *, rules_dir: Path = DEFAULT_RULES_DIR) -> RuleSet:
    """Load `<rules_dir>/<customer_id>.yaml`, e.g. `load_rules_for_customer("acme")`."""
    return load_rules(rules_dir / f"{customer_id}.yaml")
