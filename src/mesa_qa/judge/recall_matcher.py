from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_NEGATION_TRIGGERS = [
    r"\bdoes\s+not\s+(?:use|have|run|support|employ)\b",
    r"\bdoesn't\s+(?:use|have|run|support|employ)\b",
    r"\bdid\s+not\s+(?:use|have|run|support|employ)\b",
    r"\bdidn't\s+(?:use|have|run|support|employ)\b",
    r"\bdo\s+not\s+(?:use|have|run|support|employ)\b",
    r"\bdon't\s+(?:use|have|run|support|employ)\b",
    r"\bis\s+not\b",
    r"\bisn't\b",
    r"\bwas\s+not\b",
    r"\bwasn't\b",
    r"\bare\s+not\b",
    r"\baren't\b",
    r"\bno\s+longer\b",
    r"\bnever\s+(?:used|had|ran|supported)\b",
    r"\bnever\b",
    r"\bwithout\b",
    r"\bswitched\s+away\s+from\b",
    r"\bswitched\s+from\b",
    r"\bmigrated\s+away\s+from\b",
    r"\bmigrated\s+from\b",
    r"\breplaced\s+by\b",
    r"\bdeprecated\b",
    r"\bremoved\b",
    r"\bdiscontinued\b",
    r"\bnot\b",
]

_AMBIGUITY_TRIGGERS = [
    r"\bunclear\s+whether\b",
    r"\bunclear\s+if\b",
    r"\bunclear\b",
    r"\bunknown\b",
    r"\bunsure\s+whether\b",
    r"\bunsure\s+if\b",
    r"\bunsure\b",
    r"\buncertain\b",
    r"\bnot\s+sure\b",
    r"\bcannot\s+determine\b",
    r"\bcan't\s+determine\b",
    r"\bno\s+information\s+(?:about|on|regarding)?\b",
    r"\bno\s+record\s+(?:of)?\b",
    r"\bno\s+memory\s+(?:of)?\b",
    r"\bdo\s+not\s+know\b",
    r"\bdon't\s+know\b",
    r"\bmight\s+be\b",
    r"\bmaybe\b",
    r"\bpossibly\b",
    r"\bperhaps\b",
]


def match_recall(
    expected: Any,
    actual_text: str,
    structured_actual: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, str]:
    """Evaluate recall correctness using structured results first, normalized deterministic matching second,

    and strict negation / ambiguity handling.

    Returns:
        (is_pass: bool, category: str, reason: str)
    """
    if expected is None:
        return True, "NORMAL", "No expected truth specified"

    expected_values = expected if isinstance(expected, list) else [expected]

    # 1. Structured MESA result first
    if structured_actual and isinstance(structured_actual, dict):
        structured_val = (
            structured_actual.get("value")
            or structured_actual.get("structured_value")
            or structured_actual.get("fact")
        )
        if structured_val is not None:
            if isinstance(structured_val, (str, int, float, bool)):
                str_sval = str(structured_val).strip()
                if all(
                    str(ev).strip().casefold() == str_sval.casefold()
                    for ev in expected_values
                ):
                    return True, "NORMAL", "Structured value matches expected fact"
                return (
                    False,
                    "MEMORY_RECALL_MISMATCH",
                    f"Structured value '{str_sval}' does not match expected '{expected}'",
                )

    # 2. Deterministic normalized comparison for each expected value
    normalized_actual = _normalize_text(actual_text)

    for exp in expected_values:
        exp_str = str(exp).strip()
        if not exp_str:
            continue

        match_status, category, reason = _evaluate_single_target(exp_str, normalized_actual)
        if not match_status:
            return False, category, reason

    return True, "NORMAL", "All expected values positively confirmed"


def _normalize_text(text: str) -> str:
    # Collapse excess whitespace
    return re.sub(r"\s+", " ", text).strip()


def _evaluate_single_target(target: str, text: str) -> Tuple[bool, str, str]:
    target_norm = target.casefold()
    text_norm = text.casefold()

    # Check if target is present in text at all
    pattern = r"\b" + re.escape(target_norm) + r"\b"
    if not re.search(pattern, text_norm):
        # Fallback to substring if target contains special non-word characters
        if target_norm not in text_norm:
            return (
                False,
                "MEMORY_RECALL_MISMATCH",
                f"Expected fact '{target}' was not found in response",
            )

    # Split text into sentences (avoid breaking dots inside numbers or IPs)
    sentences = re.split(r"\n+|(?<=[!?])\s+|(?<=[^\s\d])\.\s+|(?<=\d)\.\s+(?=[A-Z])", text)
    positive_found = False
    positive_clause = ""
    negated_found = False
    negated_clause = ""
    ambiguous_found = False
    ambiguous_clause = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_lower = sentence.casefold()
        if target_norm not in sentence_lower:
            continue

        # Split sentence into contrastive clauses: e.g. "Atlas does not use Django, but it uses FastAPI"
        clauses = re.split(r"[,;]|\b(?:but|however|although|whereas|instead\s+of|while)\b", sentence)
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            clause_lower = clause.casefold()
            if target_norm not in clause_lower:
                continue

            # Check for ambiguity in clause
            is_ambiguous = False
            for amb_pat in _AMBIGUITY_TRIGGERS:
                if re.search(amb_pat, clause_lower):
                    is_ambiguous = True
                    break
            if is_ambiguous:
                ambiguous_found = True
                ambiguous_clause = clause
                continue

            # Check for negation in clause
            is_negated = False
            for neg_pat in _NEGATION_TRIGGERS:
                neg_match = re.search(neg_pat, clause_lower)
                if neg_match:
                    # Check if negation precedes the target in this clause
                    target_pos = clause_lower.find(target_norm)
                    if neg_match.start() < target_pos:
                        is_negated = True
                        break
                    # Or if negation immediately follows target (e.g. "FastAPI is not used")
                    if target_pos < neg_match.start():
                        between = clause_lower[target_pos + len(target_norm) : neg_match.start()]
                        if between.strip() in ("", "is", "was", "are", "were"):
                            is_negated = True
                            break

            if is_negated:
                negated_found = True
                negated_clause = clause
            else:
                positive_found = True
                positive_clause = clause

    if positive_found and not negated_found:
        return True, "NORMAL", f"Expected fact '{target}' positively confirmed: '{positive_clause}'"

    if negated_found:
        return (
            False,
            "NEEDS_REVIEW",
            f"Expected fact '{target}' appears in a negated context: '{negated_clause}'",
        )

    if ambiguous_found:
        return (
            False,
            "NEEDS_REVIEW",
            f"Expected fact '{target}' appears in an ambiguous context: '{ambiguous_clause}'",
        )

    if positive_found:
        return True, "NORMAL", f"Expected fact '{target}' positively confirmed"

    return False, "NEEDS_REVIEW", f"Recall response regarding '{target}' is inconclusive"
