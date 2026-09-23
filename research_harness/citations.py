"""Citation evidence in a pinned bibliography, and the manuscript's citation accounting.

A work's evidence is its arXiv family, each DOI of the work or of a provider alias,
and its title when the title has at least two words. Titles compare after
casefolding, removing accents, naming Greek letters and dropping LaTeX commands,
braces, math markers, punctuation and spaces. In a BibTeX bibliography an entry
cites a work when it carries one of the identifiers as a whole identifier or
when its title field is the work's title; text outside entries is not evidence.
Another bibliography format cites a work when its text carries an identifier or
contains the title, so there a shorter title inside a longer one also counts.
The lineage gate and the citation accounting read the same evidence.

The accounted works are the families of every version with a recorded full-text
reading and of every work that a selected research search of the five purposes
cites as the ground of its judgment. Every accounted work that the bibliography
does not cite needs one accounting item: {work_id, cited_as: <bibliography key>}
for a version the store does not link, or {work_id, reason}. This records a
decision; it does not judge whether the decision is scientifically right.
"""

import re
import unicodedata

from .errors import ResearchError
from .identities import family_id
from .literature import SEARCH_PURPOSES
from .operations import fields, text


_ENTRY_START_RE = re.compile(r"(?m)^[ \t]*@[ \t]*(\w+)[ \t]*([{(])[ \t\r\n]*([^,\s]+)[ \t]*,")
_TITLE_FIELD_RE = re.compile(r"(?<![a-z])title\s*=\s*(?=[{\"])")
_TEX_ACCENT_RE = re.compile(r"\\(?:[\"'`^~=.]|[vuHckbdrt](?![A-Za-z]))")
_TEX_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")
# Scripts without spaces between words: each character is a word of the title.
_CJK_CHARACTER_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
# An identifier ends where no letter, digit or underscore, and no punctuated
# continuation, follows; a PDF suffix may close it. 10.1063/1.365928 cites neither
# 10.1063/1.3659281, 10.1063/1.365928.5 nor 10.1063/1.365928(99).
_IDENTIFIER_END_PATTERN = r"(?:\.pdf|/pdf)?(?![0-9a-z_]|[-./:;()][0-9a-z])"
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


def _name_greek_command(match):
    """A Greek letter command (\\alpha, \\varepsilon, \\Gamma) as its name; any other command as nothing."""
    name = match.group(1).casefold()
    name = name[3:] if name.startswith("var") else name
    try:
        unicodedata.lookup("GREEK SMALL LETTER " + name.upper())
    except KeyError:
        return " "
    return " " + name + " "


def _normalize_title(value):
    """The words of a title: casefolded, with accents removed, Greek letters named,
    and other LaTeX commands, braces, math markers and punctuation dropped."""
    value = _TEX_COMMAND_RE.sub(_name_greek_command, _TEX_ACCENT_RE.sub("", value))
    characters = []
    for character in unicodedata.normalize("NFKD", value.casefold()):
        name = unicodedata.name(character, "")
        if unicodedata.combining(character):
            continue
        if name.startswith("GREEK") and " LETTER " in name:
            characters.append(" " + name.rsplit(" ", 1)[-1].casefold() + " ")
        else:
            characters.append(character if character.isalnum() else " ")
    return " ".join("".join(characters).split())


def _is_title_evidence(title):
    """Whether a normalized title is specific enough to stand as a citation: two or
    more words, counting each character of a script without spaces as a word."""
    return len(_CJK_CHARACTER_RE.findall(title)) + len(_CJK_CHARACTER_RE.sub(" ", title).split()) > 1


def _find_group_end(source, opener_index):
    """The index after the group that opens at opener_index: a brace group, a
    parenthesized BibTeX entry, or a quoted value, with braces balanced inside."""
    opener, depth = source[opener_index], 0
    for position in range(opener_index + 1, len(source)):
        character = source[position]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if opener == "{" and depth < 0:
                return position + 1
        elif depth == 0 and character == {"(": ")", '"': '"'}.get(opener):
            return position + 1
    return len(source)


def read_bibliography(data):
    """{entries: {key: {text, title}} in file order, text} of a pinned bibliography.
    Each entry's text ends at its closing delimiter and is casefolded; its title is
    normalized. The bibliography is read for citation evidence, not validated:
    undecodable bytes become replacement characters."""
    source = data.decode("utf-8", errors="replace")
    entries = {}
    for start in _ENTRY_START_RE.finditer(source):
        if start.group(1).casefold() in ("comment", "string", "preamble"):
            continue
        entry_source = source[start.start():_find_group_end(source, start.start(2))].casefold()
        title_field = _TITLE_FIELD_RE.search(entry_source)
        title = ("" if title_field is None else
                 entry_source[title_field.end() + 1:_find_group_end(entry_source, title_field.end()) - 1])
        entries[start.group(3)] = {"text": entry_source, "title": _normalize_title(title)}
    return {"entries": entries, "text": source.casefold()}


def _has_identifier(text_value, token):
    version = "" if token.startswith("10.") else r"(?:v[0-9]+)?"
    pattern = r"(?<![0-9a-z])" + re.escape(token) + version + _IDENTIFIER_END_PATTERN
    return re.search(pattern, text_value) is not None


def find_citing_entry(bibliography, works):
    """(cited, key) for the versions of one work. A BibTeX entry cites the work when it
    carries an identifier or when its title equals the work's title, compared
    without spaces. The key is the first citing entry in file order. Another format
    cites the work when its text carries an identifier or contains the title; the
    key is then None."""
    tokens = {token for work in works for token in find_citation_tokens(work)}
    titles = {title.replace(" ", "") for title in (_normalize_title(work["title"] or "") for work in works)
              if _is_title_evidence(title)}
    if not bibliography["entries"]:
        compact_text = _normalize_title(bibliography["text"]).replace(" ", "")
        cited = any(_has_identifier(bibliography["text"], token) for token in tokens) or any(
            title in compact_text for title in titles)
        return cited, None
    for key, entry in bibliography["entries"].items():
        compact_title = entry["title"].replace(" ", "")
        if compact_title in titles or any(_has_identifier(entry["text"], token) for token in tokens):
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
    missing = [{"work_id": family, "reasons": reasons}
               for family, reasons in uncovered.items() if family not in accounted]
    if missing:
        raise ResearchError("citation_accounting_incomplete", "Cite each fully read or search-cited work, or give "
                            "citation_accounting a reason for not citing it", {"works": missing})
    return {"cited": sorted(cited, key=lambda entry: entry["work_id"]),
            "not_cited": sorted(not_cited, key=lambda entry: entry["work_id"])}
