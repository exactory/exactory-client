"""Enumerate saved native queries, including request pagination provenance.

Groups retain provider, endpoint, all non-pagination parameters, query and the
declared scientific scope. Offset/page queries start at zero; cursor queries
start with '*'. Exact duplicate receipts are harmless, but conflicting pages,
changed totals, duplicate results and missing chain segments remain pending.
Count equality proves only coverage of this captured query, never field novelty.
"""

from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .errors import ResearchError
from .evidence import digest
from .providers import Arxiv, Crossref, OpenAlex


# The URL parameters whose value is a native registry's captured query. An OpenAlex
# filter such as cites:W123 is a query, so a citing-works capture binds it directly.
QUERY_PARAMETER_NAMES = {"arxiv": frozenset({"search_query"}),
                         "crossref": frozenset({"query", "query.bibliographic", "query.author"}),
                         "openalex": frozenset({"search", "filter"})}
# The URL parameters that page through the results of one native request.
PAGINATION_PARAMETER_NAMES = {"arxiv": frozenset({"start", "max_results"}),
                              "crossref": frozenset({"offset", "rows", "cursor"}),
                              "openalex": frozenset({"page", "per_page", "cursor"})}
# The URL parameters that decide which works a native request matches, as the registries' API references define
# them: arXiv search_query and id_list (https://info.arxiv.org/help/api/user-manual.html); Crossref query, every
# field query query.<field>, and filter (https://api.crossref.org/swagger-docs); OpenAlex search with its variants
# search.exact and search.semantic, filter, and corpus (https://help.openalex.org/api/ and
# https://help.openalex.org/api/searching/). A dotted name belongs to the name before its first dot.
MATCHING_PARAMETER_NAMES = {"arxiv": frozenset({"search_query", "id_list"}),
                            "crossref": frozenset({"query", "filter"}),
                            "openalex": frozenset({"search", "filter", "corpus"})}
# The matching parameters whose value is a comma-separated list that the registry reads as a set: an OpenAlex filter,
# whose clauses are combined with AND (https://help.openalex.org/api/filtering/), a Crossref filter, whose
# name:value pairs are combined with AND, or with OR when they repeat a name
# (https://github.com/CrossRef/rest-api-doc/blob/master/README.md), and an arXiv id_list
# (https://info.arxiv.org/help/api/user-manual.html).
LIST_PARAMETER_NAMES = {"arxiv": frozenset({"id_list"}), "crossref": frozenset({"filter"}),
                        "openalex": frozenset({"filter"})}


def _parse_parameters(source):
    """The query parameters of a native capture's URL, with OpenAlex's per-page spelled per_page."""
    pairs = parse_qsl(urlsplit(source["url"]).query, keep_blank_values=True)
    if source["provider"] == "openalex":
        pairs = [("per_page" if k == "per-page" else k, v) for k, v in pairs]
    return pairs


def compute_query_identity(source):
    """The query a native registry capture asks: its provider, endpoint and the parameters that decide which works
    match. Captures that differ only in another parameter (ordering, field selection, sampling, facets and
    grouping, paging, a contact address or key, or a parameter the registry does not list) ask the same query, and
    so do captures whose URLs spell one query differently: the endpoint is its host and its path without a trailing
    slash, an empty parameter or list item is no parameter, and the items of a list parameter form a set. None for
    a web or MCP capture, whose request the harness does not parse."""
    names = MATCHING_PARAMETER_NAMES.get(source["provider"])
    if names is None:
        return None
    list_names = LIST_PARAMETER_NAMES[source["provider"]]
    parameters = set()
    for key, value in _parse_parameters(source):
        if key.split(".", 1)[0] in names:
            parameters.update((key, item) for item in (value.split(",") if key in list_names else [value]) if item)
    url = urlsplit(source["url"])
    endpoint = urlunsplit((url.scheme, url.hostname, url.path.rstrip("/"), "", ""))
    return {"provider": source["provider"], "endpoint": endpoint, "parameters": sorted(parameters)}


def _number(parameters, key, default, minimum, maximum=None):
    value = parameters.get(key, str(default))
    if not value.isascii() or not value.isdigit() or len(value) > 8:
        raise ResearchError("invalid_search", "Invalid captured pagination parameter", {"parameter": key})
    value = int(value)
    if value < minimum or maximum is not None and value > maximum:
        raise ResearchError("invalid_search", "Captured pagination is outside the supported range", {"parameter": key})
    return value


def native_page(source, data, query, scope):
    provider = source["provider"]
    pairs = _parse_parameters(source)
    parameters = dict(pairs)
    if len(parameters) != len(pairs):
        raise ResearchError("invalid_search", "Captured query parameters cannot have ambiguous repeated values")
    if not any(k in QUERY_PARAMETER_NAMES[provider] and v == query for k, v in pairs):
        raise ResearchError("invalid_search", "The native page must retain its actual captured query parameter")
    size_key, default, maximum = {"arxiv": ("max_results", 10, 2000), "crossref": ("rows", 20, 1000),
                                 "openalex": ("per_page", 25, 100)}[provider]
    size = _number(parameters, size_key, default, 0 if provider == "crossref" else 1, maximum)
    cursor = parameters.get("cursor")
    if cursor is not None and (not cursor.strip() or any(k in parameters for k in ("offset", "page"))):
        raise ResearchError("invalid_search", "Cursor paging cannot be mixed with offsets or page numbers")
    mode = "cursor" if cursor is not None else "offset"
    offset = (_number(parameters, "page", 1, 1) - 1) * size if provider == "openalex" else _number(
        parameters, "start" if provider == "arxiv" else "offset", 0, 0)
    adapter = {"arxiv": Arxiv, "crossref": Crossref, "openalex": OpenAlex}[provider]()
    page = adapter.parse(data, page_size=size)
    pending = [dict(failure, source_id=source["id"]) for failure in page.failures]
    if page.total is None or page.returned_count > size or provider == "arxiv" and page.start != offset:
        pending.append({"code": "search_page_metadata_mismatch", "source_id": source["id"]})
    if any(k in parameters for k in ("sample", "group_by", "group-by")):
        pending.append({"code": "search_pagination_unsupported", "source_id": source["id"]})
    # One sort order cannot continue the pages of another, so a page group keeps every parameter except paging.
    # Cursor continuations must retain every parameter, including page size.
    url = urlsplit(source["url"])
    group = {"provider": provider, "endpoint": urlunsplit((url.scheme, url.netloc, url.path, "", "")),
             "parameters": sorted((k, v) for k, v in pairs if k not in PAGINATION_PARAMETER_NAMES[provider]),
             "query": query, "scope": scope, "mode": mode, "cursor_page_size": size if mode == "cursor" else None}
    return {"group": group, "source_id": source["id"], "sha256": source["response"]["sha256"],
            "position": cursor if mode == "cursor" else offset, "size": size, "total": page.total,
            "count": page.returned_count, "next_cursor": page.next_cursor,
            "found": [w["id"] for w in page.works], "pending": pending}


def enumerate_pages(pages):
    groups = {}
    for page in pages:
        groups.setdefault(digest(page["group"]), []).append(page)
    summaries, pending = [], []
    for key, members in sorted(groups.items()):
        group = members[0]["group"]
        failures, by_position = [], {}
        for page in members:
            failures.extend(page["pending"])
            old = by_position.get(page["position"])
            if old is not None and old["sha256"] != page["sha256"]:
                failures.append({"code": "search_page_conflict", "source_id": page["source_id"]})
            by_position[page["position"]] = page
        totals = {page["total"] for page in members}
        total = next(iter(totals)) if len(totals) == 1 else None
        if total is None:
            failures.append({"code": "search_totals_inconsistent"})
        visited, found, count = set(), set(), 0
        position = "*" if group["mode"] == "cursor" else 0
        while position in by_position and position not in visited:
            page = by_position[position]
            visited.add(position)
            if len(page["found"]) != len(set(page["found"])) or found.intersection(page["found"]):
                failures.append({"code": "search_results_duplicate", "source_id": page["source_id"]})
            found.update(page["found"])
            count += page["count"]
            if total is not None and count > total:
                failures.append({"code": "search_count_mismatch"})
            if group["mode"] == "cursor":
                position = page["next_cursor"]
            else:
                position += page["count"]
            if page["count"] == 0:
                break
        if count != total or len(visited) != len(by_position) or not visited:
            failures.append({"code": "search_response_incomplete", "next_position": position,
                             "returned_count": count, "reported_count": total})
        # Parsed failures are retained even if the raw row count reaches total.
        summary = dict(group, id=key, source_ids=[p["source_id"] for p in members],
                       returned_count=count, reported_count=total, complete=not failures)
        summaries.append(summary)
        pending.extend(dict(f, group_id=key) for f in failures)
    return summaries, pending
