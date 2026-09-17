"""Sampled verification preparation (sampled-v1): a stratified random sample of the population,
placement judgments, the percentile prediction, and the verification limits."""

SAMPLED = "sampled-v1"
SAMPLE_LIMIT = 100
SEARCH_READING_LIMIT = 20
CORE_LIMIT = 10
UNPLACED_WIDEN = 20
POSITIONS = ("above", "below", "unplaced")


def core_requirements(records):
    """Fulltext requirements with purpose core under the verification profile."""
    return sorted((r for r in records.get("fulltext_requirement", {}).values()
                   if r["profile"] == "verification" and r["purpose"] == "core"), key=lambda r: r["id"])
