"""The one redirect rule for a client that sends a credential.

urllib sends the headers of a request, the credential included, on to the target of a redirect,
so the client follows no redirect to another origin. urllib also turns a redirected POST into a
GET on a 301, 302, or 303, so the answer to that GET can look like the answer to the write. The
client therefore follows a redirect only of a GET or HEAD request, and refuses every redirect of
any other request, within the origin too. Every request goes through an opener of its own, so the
rule changes no state of the process, and no other caller can take it away. bin/exactory (the
exactory API key) and bin/exactory-draft (the Zenodo token) share this module, so the rule has
one definition. The facts that both commands judge the end of a request by live here too: which
requests are reads, which failures prove that no request byte was sent, which leave the outcome
unknown, and what a message quotes of an error answer.
"""

import http.client
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

# A read sends no body, so a followed redirect cannot turn its answer into the answer to a write.
_READ_METHODS = ("GET", "HEAD")
# urllib wraps every OSError from HTTPConnection.request(), including send failures, in URLError.
# Only these causes prove that no request byte was written: DNS resolution, connection refusal,
# certificate verification in the TLS handshake, and http.client's address check before sending.
# A timeout, reset, broken pipe, or other TLS/OSError leaves the outcome unknown.
_UNSENT_REQUEST_CAUSE_TYPES = (socket.gaierror, ConnectionRefusedError,
                              ssl.SSLCertVerificationError, http.client.InvalidURL)
# What urllib raises once a request went out and its answer did not arrive in full: a timeout or a
# dropped connection while the client waits for the answer or reads it (socket.timeout, which is
# TimeoutError from Python 3.10 on, and ConnectionResetError, both OSError), and an answer that
# breaks off (http.client.HTTPException, which includes RemoteDisconnected and IncompleteRead).
# URLError is an OSError and InvalidURL is an HTTPException. Both may also arise before sending;
# request_was_not_sent distinguishes those cases. Catch HTTPError first: it carries an answer.
INCOMPLETE_ANSWER_ERROR_TYPES = (OSError, http.client.HTTPException)
# How much of the body of an error answer a message quotes.
_MAX_ERROR_DETAIL_CHARACTERS = 2000


def is_read_method(method: str) -> bool:
    """Whether a request of this method is a read (GET or HEAD). A read changes nothing on the
    server, so it can be sent again. Any other request may have changed something before its
    answer, whatever that answer was."""
    return method in _READ_METHODS


def request_was_not_sent(error: Exception) -> bool:
    """Whether the failure proves that no request byte was sent."""
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    return isinstance(cause, _UNSENT_REQUEST_CAUSE_TYPES)


def describe_request_error_cause(error: Exception) -> str:
    """The cause of a request failure, unwrapping urllib's connection or send error."""
    return str(error.reason) if isinstance(error, urllib.error.URLError) else str(error)


def read_error_detail(error: urllib.error.HTTPError) -> str:
    """What a message quotes of an error answer: the start of its body, or a sentence that says
    that the body broke off. The status arrived before the body, so the message of the status
    holds either way."""
    try:
        return error.read().decode("utf-8", "replace")[:_MAX_ERROR_DETAIL_CHARACTERS]
    except INCOMPLETE_ANSWER_ERROR_TYPES as read_error:
        return f"The detail of the answer did not arrive in full. Cause: {read_error}."


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
        if is_cross_origin or not is_read_method(req.get_method()):
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
