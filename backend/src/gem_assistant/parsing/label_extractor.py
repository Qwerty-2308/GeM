"""Layer-0 extractor: label -> value using FieldRules. No LLM."""

from __future__ import annotations

from typing import Sequence

from ..domain.models import Evidence, ExtractedField, ExtractionMethod, FieldStatus, PageText
from .field_rules import DEFAULT_FIELD_RULES, FieldRule


class LabelFieldExtractor:
    def __init__(self, rules: Sequence[FieldRule] = DEFAULT_FIELD_RULES):
        self._rules = tuple(rules)

    def extract(self, pages: Sequence[PageText]) -> list[ExtractedField]:
        return [self._apply(rule, pages) for rule in self._rules]

    @staticmethod
    def _apply(rule: FieldRule, pages: Sequence[PageText]) -> ExtractedField:
        base = {"name": rule.name, "label": rule.label, "critical": rule.critical}
        for page in pages:
            for pattern, normalize in rule.layouts():
                match = pattern.search(page.text)
                if not match:
                    continue
                evidence = Evidence(page=page.number, quote=match.group(0), method=ExtractionMethod.LABEL_PARSE)
                try:
                    value = normalize(match.group("value"))
                except ValueError as error:
                    return ExtractedField(**base, status=FieldStatus.UNCLEAR, evidence=evidence,
                                          note=f"Label found but value could not be read: {error}")
                return ExtractedField(**base, status=FieldStatus.FOUND, value=value, evidence=evidence)
        return ExtractedField(**base, status=FieldStatus.NOT_FOUND)
