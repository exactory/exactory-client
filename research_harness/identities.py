"""Explicit work families, version identities and evidence-backed alias lookup.

normalize_identifier accepts DOI, arXiv (including old archive IDs), OpenAlex,
and explicit url:HTTPS identifiers. family_id removes ONLY an arXiv version.
work records are keyed by exact id; work_family/{work_id} lists version_ids.
alias/{identifier} contains assertions [{work_id, source_id, locator, relation}].
Aliases are provider assertions of `same_work`, never interchangeable versions.
resolve_family(records, identifier) requires exactly one unambiguous family.
Conflicting assertions remain stored and raise ambiguous_alias on lookup. No
title matching or automatic coalescing of different provider families occurs.
"""

import re
from urllib.parse import unquote, urlsplit

from .errors import ResearchError
from .http import safe_url


_ARXIV = re.compile(r"(?P<base>(?:[0-9]{2}(?:0[1-9]|1[0-2])\.[0-9]{4,5}|[a-z-]+(?:\.[A-Z]{2})?/[0-9]{2}(?:0[1-9]|1[0-2])[0-9]{3}))(?P<version>v[1-9][0-9]*)?\Z")
_DOI = re.compile(r"10\.[0-9]{4,9}/[^\s<>\x00-\x1f]+\Z", re.I)


def normalize_identifier(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ResearchError("invalid_identifier", "A supported exact identifier is required")
    if value.startswith("url:"):
        return "url:" + safe_url(value[4:])
    if value.lower().startswith(("http://", "https://")):
        parts = urlsplit(value)
        if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ResearchError("invalid_identifier", "Identifier URLs cannot contain credentials, queries or fragments")
        host, path = parts.netloc.lower(), unquote(parts.path)
        if host in ("arxiv.org", "export.arxiv.org") and path.startswith(("/abs/", "/pdf/", "/html/")):
            value = path.split("/", 2)[2]
            if value.endswith(".pdf"):
                value = value[:-4]
        elif host in ("doi.org", "dx.doi.org"):
            value = "doi:" + path[1:]
        elif host == "openalex.org":
            value = "openalex:" + path[1:]
        else:
            raise ResearchError("invalid_identifier", "Identifier URL is not a supported registry")
    if value.lower().startswith("arxiv:"):
        value = value[6:]
    if _ARXIV.fullmatch(value):
        return "arxiv:" + value
    if value.lower().startswith("doi:"):
        value = value[4:]
    if _DOI.fullmatch(value):
        return "doi:" + value.lower()
    if value.lower().startswith("openalex:"):
        value = value[9:]
    if re.fullmatch(r"W[1-9][0-9]*", value):
        return "openalex:" + value
    raise ResearchError("invalid_identifier", "Expected an exact arXiv, DOI, OpenAlex or explicit url: identifier")


def family_id(identifier):
    canonical = normalize_identifier(identifier)
    return re.sub(r"v[1-9][0-9]*$", "", canonical) if canonical.startswith("arxiv:") else canonical


def version_of(identifier):
    canonical = normalize_identifier(identifier)
    if canonical.startswith("arxiv:"):
        return _ARXIV.fullmatch(canonical[6:]).group("version")
    return None


def resolve_family(records, identifier):
    canonical = normalize_identifier(identifier)
    own = family_id(canonical)
    candidates = {a["work_id"] for a in records.get("alias", {}).get(canonical, {}).get("assertions", [])}
    if own in records.get("work_family", {}):
        candidates.add(own)
    if not candidates:
        raise ResearchError("unknown_work", "No acquired work resolves this identifier", {"identifier": canonical})
    if len(candidates) != 1:
        raise ResearchError("ambiguous_alias", "Identifier has conflicting family assertions; explicit resolution is required",
                            {"identifier": canonical, "candidates": sorted(candidates)})
    return next(iter(candidates))


def family_versions(records, identifier):
    """Return acquired exact IDs without choosing a latest or equivalent version."""
    family = resolve_family(records, identifier)
    return list(records.get("work_family", {}).get(family, {}).get("version_ids", []))
