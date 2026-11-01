"""Citation evidence in a pinned bibliography, and the manuscript's citation accounting.

A work's evidence is its arXiv family, each DOI of the work or of a provider alias,
and its title when the title has at least two words. Titles compare after
casefolding, removing accents, naming Greek letters and dropping LaTeX commands,
braces, math markers, punctuation and spaces. In a BibTeX bibliography an entry
cites a work when it carries one of the identifiers as a whole identifier or
when its first top-level title field is the work's title; text outside entries
is not evidence.
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
from urllib.parse import unquote, urlsplit

from .errors import ResearchError
from .identities import family_id, resolve_arxiv_family
from .literature import SEARCH_PURPOSES
from .operations import fields, text


_ENTRY_START_RE = re.compile(r"(?m)^[ \t]*@[ \t]*(\w+)[ \t]*([{(])[ \t\r\n]*([^,\s]+)[ \t]*,")
_TEX_ACCENT_RE = re.compile(r"\\(?:[\"'`^~=.]|[vuHckbdrt](?![A-Za-z]))")
_TEX_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")
# Scripts without spaces between words: each character is a word of the title.
_CJK_CHARACTER_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
# An arXiv candidate ends before any identifier continuation, allowing a PDF suffix.
_IDENTIFIER_END_PATTERN = r"(?:\.pdf|/pdf)?(?![0-9a-z_]|[-./:;()][0-9a-z])"
# Try schemes only at maximal run starts, so even unlimited failed attempts are linear.
_IDENTIFIER_START_RE = re.compile(
    r'(?P<url>(?<![0-9a-z+.-])[a-z][a-z0-9+.-]*://'
    r'|(?<![0-9a-z./-])(?:www\.|export\.)?arxiv\.org/)'
    r'|(?P<doi>(?<![0-9a-z])10\.[0-9]{4,9}/)')
_SPAN_CONTEXT_CLOSERS = {"<": ">", "[": "](", '"': '"'}
_SPAN_TRAILING_PUNCTUATION = ".,;:"
_ARXIV_HOSTS = ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
_DOI_HOSTS = ("doi.org", "dx.doi.org")
_ARXIV_PATH_RE = re.compile(r"/(?:abs|pdf|html|format)/(.+?)(?:\.pdf)?/?")
_ARXIV_FTP_PATH_RE = re.compile(r"/ftp/([^/]+)/papers/[0-9]{4}/([0-9]{4}\.[0-9]{4,5}|[0-9]{7})\.pdf")
_ARXIV_LABEL_PATTERN = r"(?<![0-9a-z./_-])arxiv(?:\s*:\s*|\s+)"
_EPRINT_FIELD_PATTERN = r'(?<![0-9a-z_])eprint\s*=\s*[{"]\s*(?:arxiv\s*:\s*)?'
_OLD_ARXIV_ID_PATTERN = r"[a-z]+(?:-[a-z]+)?(?:\.[a-z]{2})?/[0-9]{7}"
_ARXIV_ID_PATTERN = r"(?:[0-9]{4}\.[0-9]{4,5}|" + _OLD_ARXIV_ID_PATTERN + ")"
# Labels and eprint fields accept either id style; unlabelled ids require an archive.
_ARXIV_REFERENCE_RE = re.compile(
    "(?:(?:" + _ARXIV_LABEL_PATTERN + "|" + _EPRINT_FIELD_PATTERN + ")(?P<explicit>" + _ARXIV_ID_PATTERN + ")"
    "|(?<![0-9a-z./_-])(?P<old>" + _OLD_ARXIV_ID_PATTERN + "))"
    r"(?P<version>v[0-9]+)?" + _IDENTIFIER_END_PATTERN)
_INVALID_ERR_CODE = "invalid_citation_accounting"


def find_citation_tokens(work):
    """The identifiers that cite this work: its arXiv family and each DOI of the work
    or of a provider alias, casefolded. An arXiv identifier contributes its family
    form, because a bibliography that cites one version cites the work. `family_id`
    removes only a validated version suffix, so an old-style subject class ending in
    V survives. A registered DataCite arXiv DOI also contributes its arXiv family."""
    tokens = []
    for identifier in [work["id"], *work["aliases"]]:
        scheme, _, value = identifier.partition(":")
        if scheme == "arxiv":
            tokens.append(family_id(identifier).partition(":")[2].casefold())
        elif scheme == "doi":
            tokens.append(value.casefold())
            arxiv_family = resolve_arxiv_family(identifier)
            if arxiv_family is not None:
                tokens.append(arxiv_family.partition(":")[2].casefold())
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


def _find_group_end(source, opener_index, limit=None):
    """The index after the group that opens at opener_index: a brace group, a
    parenthesized BibTeX entry, or a quoted value, with unescaped braces balanced inside.
    Return None when the group has no closing delimiter before limit."""
    if limit is None:
        limit = len(source)
    opener, depth, escaped, quoted = source[opener_index], 0, False, False
    for position in range(opener_index + 1, limit):
        character = source[position]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if opener == "{" and depth < 0:
                return position + 1
        elif opener == "(" and depth == 0 and character == '"':
            # A parenthesized entry cannot end inside a quoted field value.
            quoted = not quoted
        elif depth == 0 and not quoted and character == {"(": ")", '"': '"'}.get(opener):
            return position + 1
    return None


def _read_fields(source):
    """Yield top-level BibTeX fields in order, joining parts without their wrappers."""
    position, length = 0, len(source)
    while position < length:
        # Whitespace and commas separate fields; malformed field heads end the scan.
        while position < length and (source[position].isspace() or source[position] == ","):
            position += 1
        start = position
        while position < length and (source[position].isalnum() or source[position] in "_-:"):
            position += 1
        name = source[start:position]
        while position < length and source[position].isspace():
            position += 1
        if not name or position == length or source[position] != "=":
            return
        position += 1
        parts = []
        while position < length:
            while position < length and source[position].isspace():
                position += 1
            if position == length or source[position] in ",})":
                break
            start = position
            if source[position] in '{"':
                # Quotes close only outside braces; an unclosed part takes the rest.
                end = _find_group_end(source, position)
                parts.append(source[position + 1:end - 1] if end is not None else source[position + 1:])
                position = end if end is not None else length
            else:
                # Bare numbers and macro names are literal parts; macros are not expanded.
                while position < length and not source[position].isspace() and source[position] not in "#,})":
                    position += 1
                parts.append(source[start:position])
            while position < length and source[position].isspace():
                position += 1
            if position == length or source[position] != "#":
                break
            position += 1
        yield name, "".join(parts)


def read_bibliography(data):
    """Read entry text, normalized titles and canonical identifier sets in file order.
    Each entry's text excludes its head, ends at its closing delimiter or the
    next entry head and is casefolded. Titles and identifiers come from top-level
    field values. Without entries, identifiers come from the whole bibliography.
    Undecodable bytes become replacement characters."""
    source = data.decode("utf-8", errors="replace")
    entries = {}
    starts = list(_ENTRY_START_RE.finditer(source))
    for index, start in enumerate(starts):
        if start.group(1).casefold() in ("comment", "string", "preamble"):
            continue
        # Even an ignored head bounds the scan of an unclosed preceding entry.
        limit = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        # A BibTeX key is a label; only the entry body supplies identifiers.
        end = _find_group_end(source, start.start(2), limit)
        entry_source = source[start.end():end if end is not None else limit].casefold()
        title, identifiers = None, set()
        for name, value in _read_fields(entry_source):
            # Only the first top-level title supplies title evidence, even when empty.
            if name == "title" and title is None:
                title = value
            # A top-level eprint value carries an implicit arXiv label.
            identifiers.update(_read_identifiers("arxiv:" + value if name == "eprint" else value))
        entries[start.group(3)] = {"text": entry_source, "title": _normalize_title(title or ""),
                                   "identifiers": identifiers}
    bibliography = {"entries": entries, "text": source.casefold()}
    if not entries:
        bibliography["identifiers"] = _read_identifiers(bibliography["text"])
    return bibliography


def _unescape_identifier(value):
    """Remove raw TeX escapes once, retaining letter commands and escaped digits."""
    return re.sub(r"\\(.)", lambda match: match[0] if match[1].isalnum() else match[1], value)


def _read_url_identifier(value):
    """Read one registry identifier from a whole URL, ignoring other hosts and paths."""
    try:
        parts = urlsplit(value)
        if not parts.scheme:
            parts = urlsplit("https://" + value)
    except ValueError:
        return None
    # Only DOI registry hosts supply a DOI, with percent-encoded path data decoded.
    if parts.hostname in _DOI_HOSTS:
        return "doi:" + unquote(parts.path[1:]).casefold()
    if parts.hostname not in _ARXIV_HOSTS:
        return None
    # arXiv hosts supply ids only through recognized modern or legacy FTP paths.
    path = _ARXIV_PATH_RE.fullmatch(parts.path)
    if path:
        return "arxiv:" + path[1]
    ftp_path = _ARXIV_FTP_PATH_RE.fullmatch(parts.path)
    if ftp_path:
        archive, filename = ftp_path.groups()
        return "arxiv:" + (filename if "." in filename else archive + "/" + filename)
    return None


def _read_arxiv_family(identifier):
    """Return a validated arXiv family, skipping invalid untrusted candidates."""
    # Restore old-style subject-class capitals after the bibliography's casefold.
    if identifier.startswith("arxiv:"):
        identifier = re.sub(r"\.[a-z]{2}/", lambda match: match[0].upper(), identifier)
    try:
        family = (family_id(identifier) if identifier.startswith("arxiv:")
                  else resolve_arxiv_family(identifier))
    except ResearchError:
        return None
    return family.casefold() if family is not None and family.startswith("arxiv:") else None


def _read_arxiv_references(text_value, spans):
    """Yield validated families from references outside every original identifier span."""
    span_index = 0
    for match in _ARXIV_REFERENCE_RE.finditer(text_value):
        # Advance through ordered spans so overlap checks take linear total time.
        while span_index < len(spans) and spans[span_index][1] <= match.start():
            span_index += 1
        if span_index < len(spans) and spans[span_index][0] < match.end():
            continue
        value = (match["explicit"] or match["old"]) + (match["version"] or "")
        family = _read_arxiv_family("arxiv:" + value)
        if family is not None:
            yield family


def _trim_span(value):
    """Trim punctuation and excess closing brackets without removing balanced pairs."""
    # Count once; recounting after each removed character would be quadratic.
    excess = {closer: value.count(closer) - value.count(opener)
              for opener, closer in (("(", ")"), ("[", "]"), ("<", ">"))}
    end = len(value)
    while end:
        character = value[end - 1]
        if character in _SPAN_TRAILING_PUNCTUATION:
            end -= 1
        elif excess.get(character, 0) > 0:
            excess[character] -= 1
            end -= 1
        else:
            break
    return value[:end]


def _find_identifier_end(source, start):
    """Scan to whitespace, the start's context closer, or an unmatched closing brace."""
    closer = _SPAN_CONTEXT_CLOSERS.get(source[start - 1]) if start else None
    position, depth = start, 0
    while position < len(source):
        character = source[position]
        if character.isspace():
            break
        if character == "\\":
            # An escaped character cannot close the context or change brace depth.
            position += 1
            if position < len(source) and not source[position].isspace():
                position += 1
            continue
        if closer and source.startswith(closer, position):
            break
        if character == "{":
            depth += 1
        elif character == "}":
            if depth == 0:
                break
            depth -= 1
        position += 1
    return position


def _read_identifiers(text_value):
    """Collect whole DOI identifiers and arXiv families once from bibliography text."""
    identifiers = set()
    # One left-to-right pass lets the first span consume all nested identifiers.
    spans, position = [], 0
    while (start := _IDENTIFIER_START_RE.search(text_value, position)) is not None:
        position = _find_identifier_end(text_value, start.start())
        spans.append((start.start(), position))
        # Unescape the raw span before URL percent-decoding can introduce backslashes.
        value = _unescape_identifier(_trim_span(text_value[start.start():position]))
        identifier = "doi:" + value if start.lastgroup == "doi" else _read_url_identifier(value)
        if identifier is None:
            continue
        if identifier.startswith("doi:"):
            identifiers.add(identifier)
        # Only an unversioned DataCite arXiv DOI contributes an arXiv family.
        family = _read_arxiv_family(identifier)
        if family is not None:
            identifiers.add(family)
    identifiers.update(_read_arxiv_references(text_value, spans))
    return identifiers


def find_citing_entry(bibliography, works):
    """(cited, key) for the versions of one work. A BibTeX entry cites the work when it
    carries an identifier or when its title equals the work's title, compared
    without spaces. The key is the first citing entry in file order. Another format
    cites the work when its text carries an identifier or contains the title; the
    key is then None."""
    identifiers = {("doi:" if token.startswith("10.") else "arxiv:") + token
                   for work in works for token in find_citation_tokens(work)}
    titles = {title.replace(" ", "") for title in (_normalize_title(work["title"] or "") for work in works)
              if _is_title_evidence(title)}
    if not bibliography["entries"]:
        compact_text = _normalize_title(bibliography["text"]).replace(" ", "")
        cited = bool(bibliography["identifiers"] & identifiers) or any(
            title in compact_text for title in titles)
        return cited, None
    for key, entry in bibliography["entries"].items():
        compact_title = entry["title"].replace(" ", "")
        if compact_title in titles or entry["identifiers"] & identifiers:
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
