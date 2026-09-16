"""Prepare separated production evidence without calling a model.

Nomination excerpts are projected using only their original bounded text. Incomplete
JSON values remain labelled excerpts; no omitted source value is fetched or completed.
Construction audits must never be appended to judge-visible payloads.
"""

# Standard Library
import json

from dataclasses import dataclass, field
from typing import Any

# Package Library
from kgfeg.kgs.lp_requests import LPGenerationRequest, canonical_lp_json

_FACT_FIELDS = frozenset(
    {
        "aggregate_scope",
        "evidence_type",
        "omitted_reference_count",
        "omitted_shared_ancestor_count",
        "omitted_shared_value_count",
        "references",
        "common_segments",
        "first_code",
        "second_code",
        "shared_ancestors",
        "first_is_ancestor_of_second_distance",
        "second_is_ancestor_of_first_distance",
        "first_count",
        "jaccard",
        "second_count",
        "shared_count",
        "shared_values",
        "union_count",
        "coordinate_statement_type",
        "first_rank",
        "first_value",
        "rank_gap",
        "second_rank",
        "second_value",
        "first_page_index",
        "first_source_page_indexes",
        "page_gap",
        "second_page_index",
        "second_source_page_indexes",
    }
)
_REDACTED_FIELDS = frozenset(
    {
        "nominated_relationships",
        "strength",
        "recommendation",
        "ranking",
        "rank_score",
        "confidence",
        "recommended_direction",
        "recommended_relation",
    }
)


@dataclass
class _NominationProjection:
    """Read only complete keys and available value spans from a JSON-list prefix.

    Attributes
    ----------
    facts
        Retained factual values as exact JSON excerpts and original field paths.
    mappings
        Field actions and character spans for the separate redaction audit.
    position
        Current character offset within the original bounded string.
    text
        Original nomination evidence excerpt.
    truncated
        Whether production explicitly recorded this as an incomplete excerpt.
    """

    facts: list[dict[str, Any]] = field(default_factory=list)
    mappings: list[dict[str, Any]] = field(default_factory=list)
    position: int = 0
    text: str = ""
    truncated: bool = False

    def _field(self, *, key: str, path: str) -> None:
        """Retain or redact one available JSON value without completing its tail.

        Parameters
        ----------
        key
            Complete original field name.
        path
            Original JSON pointer within the nomination excerpt.

        Raises
        ------
        ValueError
            If a field has no supported factual/redaction interpretation or an
            untruncated value is invalid.
        """

        if key not in _FACT_FIELDS | _REDACTED_FIELDS:
            raise ValueError(f"Unsupported nomination evidence field: {path}")

        start = self.position

        try:
            _, end = json.JSONDecoder().raw_decode(self.text, start)
            complete = end < len(self.text) or not self.truncated

            if end < len(self.text) and self.text[end] not in ",}] \t\n\r":
                raise ValueError("Incomplete scalar token.")
        except ValueError:
            if not self.truncated:
                raise ValueError(f"Invalid complete nomination value: {path}") from None

            end, complete = len(self.text), False

        self.position = end
        action = "redacted" if key in _REDACTED_FIELDS else "retained"
        mapping = {
            "action": action,
            "character_start": start,
            "character_end": end,
            "source_path": path,
            "complete": complete,
        }

        if action == "retained":
            mapping["target_path"] = f"/facts/{len(self.facts)}"
            self.facts.append(
                {
                    "field": path,
                    "json_excerpt": self.text[start:end],
                    "truncated": not complete,
                }
            )

        self.mappings.append(mapping)

    def _object(self, path: str) -> None:
        """Project one signal record or its factual values object.

        Parameters
        ----------
        path
            Original object pointer.

        Raises
        ------
        ValueError
            If original JSON syntax or object keys are incompatible.
        """

        self._take("{")
        keys: set[str] = set()

        while self.position < len(self.text) and self.text[self.position] != "}":
            try:
                key, end = json.JSONDecoder().raw_decode(self.text, self.position)
            except ValueError:
                self._tail(path)
                return

            if not isinstance(key, str) or key in keys:
                raise ValueError("Nomination field names must be unique strings.")

            keys.add(key)
            self.position = end

            if self.position == len(self.text):
                self._tail(path)
                return

            self._take(":")
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")

            if self.position == len(self.text):
                self._tail(child)
                return

            if key == "triggering_values":
                self._object(child)
            else:
                self._field(key=key, path=child)

            if self.position == len(self.text) or self.text[self.position] == "}":
                break

            self._take(",")

        if self.position < len(self.text):
            self._take("}")

    def _tail(self, path: str) -> None:
        """Record an incomplete field name without inventing a value.

        Parameters
        ----------
        path
            Parent field or record pointer.

        Raises
        ------
        ValueError
            If the original excerpt was not marked truncated.
        """

        if not self.truncated:
            raise ValueError("Complete nomination evidence has incomplete syntax.")

        self.mappings.append(
            {
                "action": "incomplete_key_or_absent_value",
                "source_path": path,
                "character_start": self.position,
                "character_end": len(self.text),
            }
        )
        self.position = len(self.text)

    def _take(self, token: str) -> None:
        """Consume a required structural token from canonical nomination JSON.

        Parameters
        ----------
        token
            Expected single JSON delimiter.

        Raises
        ------
        ValueError
            If the delimiter differs from the original bounded text.
        """

        if self.text[self.position : self.position + 1] != token:
            raise ValueError("Nomination excerpt has incompatible JSON structure.")

        self.position += 1

    def project(self) -> dict[str, Any]:
        """Project factual fields from the original canonical JSON-list prefix.

        Returns
        -------
        dict[str, Any]
            Retained excerpts with no completion or semantic recommendation.

        Raises
        ------
        ValueError
            If complete material is malformed or contains unsupported fields.
        """

        self._take("[")
        index = 0

        while self.position < len(self.text) and self.text[self.position] != "]":
            self._object(f"/{index}")
            index += 1

            if self.position == len(self.text) or self.text[self.position] == "]":
                break

            self._take(",")

        if self.position < len(self.text):
            self._take("]")

        if self.position != len(self.text):
            raise ValueError("Nomination evidence has extra trailing material.")

        if not self.truncated:
            json.loads(self.text)

        return {"facts": self.facts, "original_excerpt_truncated": self.truncated}


def production_blind_payload(
    *, policy: dict[str, Any], request: LPGenerationRequest, target_pair_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project original batch facts while withholding nomination advice and ordering.

    The caller supplies only the original request and captured curriculum policy. No
    production judgment, published outcome, sampling label or evaluator response is
    accepted. Original SFI evidence and warning text remain unchanged; pair and warning
    lists use canonical order to avoid carrying candidate ranking order.

    Parameters
    ----------
    policy
        Captured curriculum semantics and instructions, without production outcomes.
    request
        Validated original bounded batch.
    target_pair_id
        Exact pair to classify within that batch.

    Returns
    -------
    tuple[dict[str, Any], list[dict[str, Any]]]
        Judge-visible facts and a separate field-level retention/redaction audit.

    Raises
    ------
    ValueError
        If the target is absent or nomination fields cannot be safely interpreted.
    """

    if target_pair_id not in {pair.pair_id for pair in request.pairs}:
        raise ValueError("Assessed pair is absent from its original request.")

    original = request.model_dump(mode="json")
    payload = {
        "framework_title": original["framework_title"],
        "pairs": [],
        "policy": policy,
        "sfis": original["sfis"],
        "target_pair_id": target_pair_id,
        "warnings": sorted(original["warnings"]),
    }
    mappings: list[dict[str, Any]] = [
        {"source_path": f"/{key}", "target_path": f"/{key}", "action": "retained"}
        for key in ("framework_title", "sfis")
    ]
    mappings.append(
        {
            "source_path": "/warnings",
            "target_path": "/warnings",
            "action": "canonical_order_original_warning_text",
        }
    )
    mappings.extend(
        {"source_path": f"/{key}", "action": "redacted_material_or_order_identifier"}
        for key in sorted(
            original.keys() - {"framework_title", "pairs", "sfis", "warnings"}
        )
    )
    ordered = sorted(
        enumerate(request.pairs),
        key=lambda entry: (entry[1].first_sfi_uuid, entry[1].second_sfi_uuid),
    )

    for target_index, (source_index, pair) in enumerate(ordered):
        source_path = f"/pairs/{source_index}"
        target_path = f"/pairs/{target_index}"
        projected = pair.model_dump(
            mode="json", exclude={"nomination", "candidate_content_hash"}
        )
        projected["warnings"] = sorted(projected["warnings"])

        for permission in projected["admissible_decisions"]:
            if permission["decision"] == "needs_review":
                permission["decision"] = "ambiguous"

        reader = _NominationProjection(
            text=pair.nomination.evidence.text,
            truncated=pair.nomination.evidence.truncated,
        )
        projected["nomination_facts"] = {
            **reader.project(),
            "evidence_types": list(pair.nomination.evidence_types),
            "original_characters": pair.nomination.evidence.original_characters,
            "references_truncated": pair.nomination.references_truncated,
        }
        payload["pairs"].append(projected)

        for key in ("first_sfi_uuid", "pair_id", "second_sfi_uuid"):
            mappings.append(
                {
                    "source_path": source_path + "/" + key,
                    "target_path": target_path + "/" + key,
                    "action": "retained",
                }
            )

        mappings.append(
            {
                "source_path": source_path + "/warnings",
                "target_path": target_path + "/warnings",
                "action": "canonical_order_original_warning_text",
            }
        )

        for source_field, target_field in (
            ("evidence_types", "evidence_types"),
            ("evidence/original_characters", "original_characters"),
            ("evidence/truncated", "original_excerpt_truncated"),
            ("references_truncated", "references_truncated"),
        ):
            mappings.append(
                {
                    "source_path": source_path + "/nomination/" + source_field,
                    "target_path": target_path + "/nomination_facts/" + target_field,
                    "action": "retained",
                }
            )

        mappings.extend(
            {
                "source_path": source_path + "/nomination/" + source_field,
                "action": "redacted_material_identifier",
            }
            for source_field in (
                "complete_evidence_content_hash",
                "evidence/content_hash",
            )
        )
        mappings.extend(
            [
                {
                    "source_path": source_path + "/admissible_decisions",
                    "target_path": target_path + "/admissible_decisions",
                    "action": "permission_label_needs_review_to_ambiguous",
                },
                {
                    "source_path": source_path + "/candidate_content_hash",
                    "action": "redacted",
                },
                {
                    "source_path": source_path + "/nomination/evidence/text",
                    "target_path": target_path + "/nomination_facts",
                    "action": "bounded_factual_projection",
                    "fields": reader.mappings,
                },
            ]
        )

    # Canonical JSON ensures the caller receives no references to mutable input models.
    return json.loads(canonical_lp_json(payload)), mappings
