"""The one rule for a client that sends a credential: it never follows a redirect to another origin.

urllib sends the headers of a request, the credential included, on to the target of a redirect.
It also turns a redirected POST into a GET at the target, so the answer of another server can
look like the answer to a write. A client that sends a credential refuses such a redirect before
it follows it. bin/exactory (the exactory API key) and bin/exactory-draft (the Zenodo token)
share this module, so the rule has one definition.
"""

import urllib.parse
import urllib.request


class RefusedRedirectError(Exception):
    """A redirect to another origin, which the client did not follow."""

    def __init__(self, request_url: str, target_url: str) -> None:
        super().__init__(f"{request_url} redirects to {target_url}")
        self.request_url = request_url
        self.target_url = target_url


def get_origin(url: str) -> str:
    """The scheme, host, and port of a URL, which is what a message names. The user name of the
    URL stays out of the text."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to the scheme, host, and port of the request that got it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl)[:2] != urllib.parse.urlsplit(req.full_url)[:2]:
            fp.close()
            raise RefusedRedirectError(req.full_url, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url_within_origin(request: urllib.request.Request, *, timeout: float):
    """Send the request as urllib.request.urlopen does, with the redirect rule above. A redirect to
    another origin raises RefusedRedirectError before anything is sent there.

    urlopen uses the opener that is installed for the process, so this installs the opener of the
    rule for the length of the call and then restores the default one. The callers and their tests
    keep the urlopen call that they already have."""
    urllib.request.install_opener(urllib.request.build_opener(SameOriginRedirectHandler))
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    finally:
        urllib.request.install_opener(None)
