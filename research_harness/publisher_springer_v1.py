"""Bounded springer-meta-json-v1 parser for externally captured HTTP responses.

Format reference: https://dev.springernature.com/docs/api-endpoints/meta-api/
Only a single DOI article in a complete, identity-encoded HTTP/1.x response
with Content-Length is admitted. This proves captured metadata boundaries, not
origin authenticity, article completeness, or reading. Chunked, compressed,
range and multipart responses need other parsers and remain unsupported here.
"""

import json
import re
from urllib.parse import urlsplit

from .errors import ResearchError
from .fulltext import parse_base_media_type
from .identities import normalize_identifier
from .providers import Page, _json, _work


FORMAT_ID = 'springer-meta-json-v1'


def reject_capture():
    raise ResearchError('invalid_publisher_capture',
                        'A complete single-article Springer Meta JSON HTTP capture with its exact DOI is required')


def read_http_body(data):
    if not isinstance(data, bytes):
        reject_capture()
    head, separator, body = data.partition(b'\r\n\r\n')
    if not separator or len(head) > 65536:
        reject_capture()
    lines = head.split(b'\r\n')
    if re.fullmatch(rb'HTTP/1\.[01] 200(?: [\x20-\x7e]*)?', lines[0]) is None:
        reject_capture()
    headers = {}
    for line in lines[1:]:
        name, separator, value = line.partition(b':')
        if (not separator or re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None
                or re.fullmatch(rb'[\x20-\x7e\t]*', value) is None):
            reject_capture()
        name = name.decode('ascii').lower()
        if name in headers:
            reject_capture()
        headers[name] = value.decode('ascii').strip()
    length = headers.get('content-length', '')
    if (re.fullmatch(r'[0-9]+', length) is None or len(length) > 12 or int(length) != len(body)
            or any(name in headers for name in ('content-range', 'transfer-encoding'))
            or headers.get('content-encoding', 'identity').lower() != 'identity'
            or parse_base_media_type(headers.get('content-type')) != 'application/json'):
        reject_capture()
    return body


def parse_capture(data, source_url, identifier):
    """Parse the complete recorded response, never caller-selected field spans."""
    parts = urlsplit(source_url)
    if (parts.scheme != 'https' or parts.hostname != 'api.springernature.com'
            or parts.port not in (None, 443) or parts.username is not None or parts.password is not None
            or parts.path != '/meta/v2/json' or parts.fragment):
        reject_capture()
    try:
        canonical = normalize_identifier(identifier)
        if canonical != identifier or not canonical.startswith(('doi:10.1007/', 'doi:10.1038/')):
            reject_capture()
        body = read_http_body(data)
        if json.detect_encoding(body) not in ('utf-8', 'utf-8-sig'):
            reject_capture()
        document = _json(body)
    except ResearchError as error:
        if error.code not in ('invalid_identifier', 'invalid_response'):
            raise
        raise ResearchError('invalid_publisher_capture', 'The capture must contain unambiguous UTF-8 JSON and an exact DOI') from error
    records = document.get('records')
    result = document.get('result')
    if (document.get('apiMessage') != 'This JSON was provided by Springer Nature'
            or not isinstance(records, list) or len(records) != 1
            or not isinstance(records[0], dict) or not isinstance(result, list) or len(result) != 1
            or not isinstance(result[0], dict)
            or any(result[0].get(key) != '1' for key in ('total', 'start', 'recordsDisplayed'))):
        reject_capture()
    article = records[0]
    try:
        if normalize_identifier(article.get('doi')) != identifier:
            reject_capture()
        if 'identifier' in article and normalize_identifier(article['identifier']) != identifier:
            reject_capture()
    except ResearchError as error:
        if error.code != 'invalid_identifier':
            raise
        raise ResearchError('invalid_publisher_capture', 'The captured article must identify the requested DOI') from error
    if any(not isinstance(article.get(key), str) or not article[key].strip() for key in ('title', 'abstract')):
        reject_capture()
    work = _work(identifier, article['title'], [], locator={'type': 'json_pointer', 'pointer': '/records/0'})
    work.update(abstract_text=article['abstract'], abstract_status='available', abstract_representation=FORMAT_ID)
    return Page(works=[work], returned_count=1, total=1, complete=True)
