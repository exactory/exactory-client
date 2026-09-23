"""Citation evidence in a pinned bibliography, and the manuscript's citation accounting.

A work's evidence is its arXiv family, each DOI of the work or of a provider alias,
and its title when the title has at least two words. In a BibTeX bibliography an
entry cites a work when it carries one of the identifiers as a whole identifier
or when its title field is the work's title; text outside entries is not
evidence. Another bibliography format cites a work when its text carries an
identifier, or the title as whole words. The lineage gate and the citation
accounting read the same evidence.

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
_TITLE_FIELD_RE = re.compile(r"(?<![a-z])title\s*=\s*([{\"])")
_TEX_COMMAND_RE = re.compile(r"\\[a-z]+")
_TITLE_SEPARATOR_RE = re.compile(r"[^0-9a-z]+")
# An identifier ends where no letter or digit, and no dotted, slashed or hyphenated
# continuation, follows: 10.1063/1.365928 cites neither 10.1063/1.3659281 nor
# 10.1063/1.365928.5.
_IDENTIFIER_END = r"(?![0-9a-z]|[./-][0-9a-z])"
_INVALID_ERR_CODE = "invalid_citation_accounting"


def find_citation_tokens(work):
    """The identifiers that cite this work: its arXiv family and each DOI of the work
    or of a provider alias, casefolded. An arXiv identifier contributes its family
    form, because a bibliography that cites one version cites the work. `family_id`
    removes only a validated version suffix, so an old-style subject class ending in
    V survives."""
    tokens = []
    for identifier in [work["id"], *work["aliases"]]:
        scheme, _, value = identifier.partition(":")
        if scheme == "arxiv":
            tokens.append(family_id(identifier).partition(":")[2].casefold())
        elif scheme == "doi":
            tokens.append(value.casefold())
    return tokens


def _normalize_title(value):
    """The words of a title, casefolded, without LaTeX commands, braces or math markers."""
    return " ".join(_TITLE_SEPARATOR_RE.sub(" ", _TEX_COMMAND_RE.sub(" ", value.casefold())).split())


def _read_title_field(entry_source):
    """The raw title field of one casefolded BibTeX entry, or an empty string."""
    match = _TITLE_FIELD_RE.search(entry_source)
    if match is None:
        return ""
    if match.group(1) == '"':
        end = entry_source.find('"', match.end())
        return entry_source[match.end():end if end >= 0 else len(entry_source)]
    depth, position = 1, match.end()
    while position < len(entry_source) and depth:
        depth += {"{": 1, "}": -1}.get(entry_source[position], 0)
        position += 1
    return entry_source[match.end():position - 1]


def read_bibliography(data):
    """{entries: {key: {text, title}} in file order, text} of a pinned bibliography, with
    casefolded text and normalized titles. The bibliography is read for citation
    evidence, not validated: undecodable bytes become replacement characters."""
    source = data.decode("utf-8", errors="replace")
    starts = list(_ENTRY_START_RE.finditer(source))
    entries = {}
    for index, start in enumerate(starts):
        if start.group(1).casefold() in ("comment", "string", "preamble"):
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        entry_source = source[start.start():end].casefold()
        entries[start.group(2)] = {"text": entry_source, "title": _normalize_title(_read_title_field(entry_source))}
    return {"entries": entries, "text": source.casefold()}


def _carries_identifier(text_value, token):
    version = "" if token.startswith("10.") else r"(?:v[0-9]+)?"
    return re.search(r"(?<![0-9a-z])" + re.escape(token) + version + _IDENTIFIER_END, text_value) is not None


def find_citing_entry(bibliography, works):
    """(cited, key) for the versions of one work. In a BibTeX bibliography the key is
    the first citing entry in file order; in another format it is None."""
    tokens = {token for work in works for token in find_citation_tokens(work)}
    titles = {title for title in (_normalize_title(work["title"] or "") for work in works) if len(title.split()) > 1}
    if not bibliography["entries"]:
        words = " " + _normalize_title(bibliography["text"]) + " "
        cited = any(_carries_identifier(bibliography["text"], token) for token in tokens) or any(
            " " + title + " " in words for title in titles)
        return cited, None
    for key, entry in bibliography["entries"].items():
        if entry["title"] in titles or any(_carries_identifier(entry["text"], token) for token in tokens):
            return True, key
    return False, None


def collect_accountable_works(records):
    """{family: {reasons, version_ids}} for every work the manuscript must account for."""
    found = {}

    def add(version_id, reason):
        item = found.setdefault(family_id(version_id), {"reasons": set(), "version_ids": set()})
        item["reasons"].add(reason)
        item["version_ids"].add(version_id)

    for reading in records.get("reading", {}).values():
        if reading["depth"] == "fulltext":
            add(reading["version_id"], "fulltext")
    selections, searches = records.get("search_selection", {}), records.get("literature_search", {})
    for purpose in SEARCH_PURPOSES:
        selected = selections.get("research:" + purpose)
        for work_id in searches[selected["search_id"]]["cited_work_ids"] if selected else []:
            add(work_id, "search:" + purpose)
    return {family: {"reasons": sorted(found[family]["reasons"]), "version_ids": sorted(found[family]["version_ids"])}
            for family in sorted(found)}


def _build_item_refusal(index, cause, **details):
    return ResearchError(_INVALID_ERR_CODE, "Each item names one accounted work that the bibliography does not cite, "
                         "once, with either cited_as or reason", dict({"index": index, "cause": cause}, **details))


def account_citations(records, bibliography_data, items):
    """Return {cited, not_cited} for the accounted works, or refuse an incomplete or
    invalid accounting. A cited work names the first citing BibTeX entry in file
    order, or null when the bibliography is not BibTeX. A refused item names its
    index and cause: fields, already_cited, unknown_work, duplicate,
    cited_as_and_reason, neither or unknown_key."""
    bibliography = read_bibliography(bibliography_data)
    cited, uncovered, citing_keys = [], {}, {}
    for family, work in collect_accountable_works(records).items():
        found, key = find_citing_entry(bibliography, [records["work"][version] for version in work["version_ids"]])
        if found:
            cited.append({"work_id": family, "key": key, "basis": "bibliography"})
            citing_keys[family] = key
        else:
            uncovered[family] = work["reasons"]
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ResearchError(_INVALID_ERR_CODE, "Citation accounting must be an array of items")
    not_cited, accounted = [], set()
    for index, item in enumerate(items):
        try:
            fields(item, ("work_id",), ("cited_as", "reason"), code=_INVALID_ERR_CODE)
            family = family_id(item["work_id"])
            for name in ("cited_as", "reason"):
                if name in item:
                    text(item[name], "Citation accounting " + name, code=_INVALID_ERR_CODE)
        except ResearchError as error:
            raise _build_item_refusal(index, "fields") from error
        if family in citing_keys:
            raise _build_item_refusal(index, "already_cited", work_id=family, key=citing_keys[family])
        if family not in uncovered:
            raise _build_item_refusal(index, "unknown_work", work_id=family)
        if family in accounted:
            raise _build_item_refusal(index, "duplicate", work_id=family)
        if ("cited_as" in item) == ("reason" in item):
            raise _build_item_refusal(index, "cited_as_and_reason" if "cited_as" in item else "neither",
                                      work_id=family)
        accounted.add(family)
        if "cited_as" not in item:
            not_cited.append({"work_id": family, "reason": item["reason"]})
        elif item["cited_as"] in bibliography["entries"]:
            cited.append({"work_id": family, "key": item["cited_as"], "basis": "declared"})
        else:
            raise _build_item_refusal(index, "unknown_key", work_id=family, cited_as=item["cited_as"])
    missing = [{"work_id": family, "reasons": reasons} for family, reasons in uncovered.items() if family not in accounted]
    if missing:
        raise ResearchError("citation_accounting_incomplete", "Cite each fully read or search-cited work, or give "
                            "citation_accounting a reason for not citing it", {"works": missing})
    return {"cited": sorted(cited, key=lambda entry: entry["work_id"]),
            "not_cited": sorted(not_cited, key=lambda entry: entry["work_id"])}
