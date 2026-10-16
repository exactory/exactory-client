"""The one redirect rule for a client that sends a credential.

urllib sends the headers of a request, the credential included, on to the target of a redirect,
so the client follows no redirect to another origin. urllib also turns a redirected POST into a
GET on a 301, 302, or 303, so the answer to that GET can look like the answer to the write. The
client therefore follows a redirect only of a GET or HEAD request, and refuses every redirect of
any other request, within the origin too. Every request goes through an opener of its own, so the
rule changes no state of the process, and no other caller can take it away. bin/exactory (the
exactory API key) and bin/exactory-draft (the Zenodo token) share this module, so the rule has
one definition.
"""

import urllib.parse
import urllib.request

# A read sends no body, so a followed redirect cannot turn its answer into the answer to a write.
_READ_METHODS = ("GET", "HEAD")


class RefusedRedirectError(Exception):
    """A redirect that the client did not follow. `is_cross_origin` holds whether it leads to
    another origin. A redirect within the origin is refused because its request is not a read."""

    def __init__(self, request_url: str, target_url: str, method: str, *, is_cross_origin: bool) -> None:
        super().__init__(f"{method} {request_url} redirects to {target_url}")
        self.request_url = request_url
        self.target_url = target_url
        self.method = method
        self.is_cross_origin = is_cross_origin


def get_origin(url: str) -> str:
    """The scheme, host, and port of a URL, which is what a message names. The user name of the
    URL stays out of the text."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}"


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only of a GET or HEAD request, and only to the scheme, host, and port of
    that request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        is_cross_origin = urllib.parse.urlsplit(newurl)[:2] != urllib.parse.urlsplit(req.full_url)[:2]
        if is_cross_origin or req.get_method() not in _READ_METHODS:
            fp.close()
            raise RefusedRedirectError(req.full_url, newurl, req.get_method(), is_cross_origin=is_cross_origin)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    # Python 3.9 has no handler for 308 (3.11 and later have one), so there a 308 would end as a
    # plain HTTP error before the rule above sees it. This line applies the rule on every version.
    # urllib itself still follows a 308 only where it supports one.
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302


def open_url_within_origin(request: urllib.request.Request, *, timeout: float):
    """Send the request as urllib.request.urlopen does, with the redirect rule above. A redirect
    that the rule refuses raises RefusedRedirectError before anything is sent to its target. The
    request goes through an opener of its own: the opener that is installed for the process is
    neither used nor changed, so concurrent callers do not affect one another."""
    return urllib.request.build_opener(SameOriginRedirectHandler).open(request, timeout=timeout)
