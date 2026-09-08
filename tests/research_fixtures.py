"""Authored provider responses and deterministic external I/O for research tests."""

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


class Clock:
    def __init__(self):
        self.seconds = 1788825600.0
        self.sleeps = []

    def now(self):
        return datetime.fromtimestamp(self.seconds, timezone.utc).isoformat()

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.seconds += seconds


class Wire:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, headers, *, address, timeout):
        from research_harness.http import RawResponse
        self.requests.append((url, dict(headers), address, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, headers, body = response[:3]
        peer = response[3] if len(response) == 4 else address
        return RawResponse(status, headers, io.BytesIO(body), peer)


def client(responses, **kwargs):
    from research_harness.http import HttpClient
    wire = Wire(responses)
    clock = Clock()
    return HttpClient(transport=wire, resolver=lambda host, timeout: ["93.184.216.34"],
                      clock=clock, **kwargs), wire, clock


def entry(identifier="2601.00001v1", category="cs.LG", abstract="A short original abstract.",
          published="2026-01-03T12:00:00Z", doi=None):
    summary = "" if abstract is None else "<summary>" + escape(abstract) + "</summary>"
    primary = "" if category is None else '<arxiv:primary_category term="' + category + '"/>'
    alias = "" if doi is None else "<arxiv:doi>" + escape(doi) + "</arxiv:doi>"
    return ('<entry><id>http://arxiv.org/abs/' + identifier + '</id>'
            '<title>An authored example</title><author><name>A. Researcher</name></author>'
            '<published>' + published + '</published><updated>2026-02-01T10:00:00Z</updated>'
            + summary + primary + alias + '<category term="cs.LG"/>'
            '<link rel="related" type="application/pdf" href="http://arxiv.org/pdf/'
            + identifier + '"/></entry>')


def atom(entries=(), total=0, start=0, size=100):
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom" '
            'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            '<id>https://arxiv.org/api/authored</id><title>Authored query</title>'
            '<updated>2026-09-07T00:00:00Z</updated>'
            '<opensearch:totalResults>' + str(total) + '</opensearch:totalResults>'
            '<opensearch:startIndex>' + str(start) + '</opensearch:startIndex>'
            '<opensearch:itemsPerPage>' + str(size) + '</opensearch:itemsPerPage>'
            + ''.join(entries) + '</feed>').encode()


def xml_response(data):
    return 200, {"Content-Type": "application/atom+xml"}, data


def json_response(data):
    return 200, {"Content-Type": "application/json"}, json.dumps(data).encode()


def openalex():
    return json.loads((Path(__file__).parent / "fixtures/research/openalex.json").read_text(encoding="utf-8"))


def crossref():
    return json.loads((Path(__file__).parent / "fixtures/research/crossref.json").read_text(encoding="utf-8"))
