"""Citation evidence in a pinned bibliography, and the manuscript's citation accounting.

A bibliography cites a work when one of the work's citation tokens occurs in it:
the arXiv family, a DOI of the work or of a provider alias, or a title of at least
two words. The lineage gate and the citation accounting read the same evidence.

The accounted works are the families of every version with a recorded full-text
reading and of every work that a selected research search of the five purposes
cites as the ground of its judgment. Every accounted work that the bibliography
does not cite needs one accounting item: {work_id, cited_as: <bibliography key>}
for a version the store does not link, or {work_id, reason}. This records a
decision; it does not judge whether the decision is scientifically right.
"""

import re

from .errors import ResearchError
from .identities import family_id
from .literature import SEARCH_PURPOSES
from .operations import fields, text


_ENTRY_START_RE = re.compile(r"(?m)^[ \t]*@[ \t]*(\w+)[ \t]*[{(][ \t\r\n]*([^,\s]+)[ \t]*,")
_INVALID_ERR_CODE = "invalid_citation_accounting"


def _fold_text(value):
    return " ".join(value.casefold().split())


def find_citation_tokens(work):
    """The strings whose presence in a bibliography counts as a citation of this work.

    An arXiv identifier contributes its family form, because a bibliography that cites one
    version cites the work; every versioned spelling contains that form. `family_id` removes
    only a validated version suffix, so an old-style subject class ending in V survives.
    A one-word title is too common a string to stand as evidence of a citation."""
    tokens = []
    for identifier in [work["id"], *work.get("aliases", [])]:
        scheme, _, value = identifier.partition(":")
        if scheme == "arxiv":
            tokens.append(family_id(identifier).partition(":")[2].casefold())
        elif scheme == "doi":
            tokens.append(value.casefold())
    title = _fold_text(work.get("title") or "")
    if len(title.split()) > 1:
        tokens.append(title)
    return tokens


def normalize_bibliography(data):
    """Casefolded bibliography text with single spaces. The bibliography is read for
    citation evidence, not validated: undecodable bytes become replacement characters."""
    return _fold_text(data.decode("utf-8", errors="replace"))


def split_bibliography(data):
    """Map each BibTeX entry key to its normalized text; other formats have no keys."""
    source = data.decode("utf-8", errors="replace")
    starts = list(_ENTRY_START_RE.finditer(source))
    entries = {}
    for index, start in enumerate(starts):
        if start.group(1).lower() in ("comment", "string", "preamble"):
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        entries[start.group(2)] = _fold_text(source[start.start():end])
    return entries


def accountable_works(records):
    """{family: {reasons, version_ids}} for every work the manuscript must account for."""
    found = {}

    def add(version_id, reason):
        item = found.setdefault(family_id(version_id), {"reasons": set(), "version_ids": set()})
        item["reasons"].add(reason)
        item["version_ids"].add(version_id)

    for reading in records.get("reading", {}).values():
        if reading.get("depth") == "fulltext":
            add(reading["version_id"], "fulltext")
    selections, searches = records.get("search_selection", {}), records.get("literature_search", {})
    for purpose in SEARCH_PURPOSES:
        selected = selections.get("research:" + purpose)
        for work_id in searches.get(selected["search_id"], {}).get("cited_work_ids", []) if selected else []:
            add(work_id, "search:" + purpose)
    return {family: {"reasons": sorted(found[family]["reasons"]), "version_ids": sorted(found[family]["version_ids"])}
            for family in sorted(found)}


def _find_tokens_of_family(records, version_ids):
    tokens = set()
    for version_id in version_ids:
        work = records.get("work", {}).get(version_id) or {"id": version_id}
        tokens.update(find_citation_tokens(work))
    return tokens


def _parse_accounted_family(item):
    try:
        return family_id(item["work_id"])
    except ResearchError as error:
        raise ResearchError(_INVALID_ERR_CODE, "work_id must be an exact work identifier",
                            {"work_id": item["work_id"]}) from error


def account_citations(records, bibliography, items):
    """Return {cited, not_cited} for the accounted works, or refuse an incomplete or
    invalid accounting. A cited work names the first BibTeX entry that carries its
    evidence, or null when the bibliography is not BibTeX."""
    whole, entries = normalize_bibliography(bibliography), split_bibliography(bibliography)
    cited, uncovered = [], {}
    for family, work in accountable_works(records).items():
        tokens = _find_tokens_of_family(records, work["version_ids"])
        if any(token in whole for token in tokens):
            key = next((k for k in sorted(entries) if any(token in entries[k] for token in tokens)), None)
            cited.append({"work_id": family, "key": key, "basis": "bibliography"})
        else:
            uncovered[family] = work["reasons"]
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ResearchError(_INVALID_ERR_CODE, "Citation accounting must be an array of items")
    not_cited, accounted = [], set()
    for item in items:
        fields(item, ("work_id",), ("cited_as", "reason"), code=_INVALID_ERR_CODE)
        family = _parse_accounted_family(item)
        if family not in uncovered or family in accounted or ("cited_as" in item) == ("reason" in item):
            raise ResearchError(_INVALID_ERR_CODE, "Each item names one accounted work that the bibliography does "
                                "not cite, once, with either cited_as or reason", {"work_id": item["work_id"]})
        accounted.add(family)
        if "cited_as" in item:
            if item["cited_as"] not in entries:
                raise ResearchError(_INVALID_ERR_CODE, "cited_as must be a key of the pinned bibliography",
                                    {"work_id": family, "cited_as": item["cited_as"]})
            cited.append({"work_id": family, "key": item["cited_as"], "basis": "declared"})
        else:
            reason = text(item["reason"], "Citation accounting reason", code=_INVALID_ERR_CODE)
            not_cited.append({"work_id": family, "reason": reason})
    missing = [{"work_id": family, "reasons": reasons} for family, reasons in uncovered.items() if family not in accounted]
    if missing:
        raise ResearchError("citation_accounting_incomplete", "Cite each fully read or search-cited work, or give "
                            "citation_accounting a reason for not citing it", {"works": missing})
    return {"cited": sorted(cited, key=lambda entry: entry["work_id"]),
            "not_cited": sorted(not_cited, key=lambda entry: entry["work_id"])}
