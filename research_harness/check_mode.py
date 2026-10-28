"""Diagnostic mutation preparation over one authenticated, non-writing view."""

from .artifacts import _CheckEffectRequired, _StagedArtifacts
from .errors import ResearchError
from .storage import (Transaction, _canonical, _check_stored_bounds, _digest, _encode_mutation_input,
                      _encode_record_change, _load, _read_original_response, _text, check_command_bounds)


class _CheckComplete(BaseException):
    def __init__(self, response):
        self.response = response


class _CheckTransaction(Transaction):
    def __init__(self):
        super().__init__(None)

    def put(self, kind, key, value):
        self._check(kind)
        _encode_record_change(key, value)


class _CheckingStore:
    """Supply the same preparation interface with no persistent effect methods."""

    def __init__(self, store, request_id):
        self.root = store.root
        self._store = store
        self._snapshot = None
        self._request_id = request_id
        self._artifacts = _StagedArtifacts(self.root)

    def read_revision(self):
        return self._read_preparation().revision

    revision = property(read_revision)

    def read_snapshot(self):
        self._read_preparation()
        return {"revision": self.revision, "records": self._records}

    snapshot = read_snapshot

    def _read_preparation(self):
        if self._snapshot is None:
            self._snapshot = self._store._read_preparation(self._request_id)
            self._snapshot._store = self
            self._records = self._snapshot.records
        return self._snapshot

    def build_artifact_store(self):
        return self._artifacts

    def read_committed_request(self, request_id):
        _text(request_id, "Request ID")
        if request_id != self._request_id:
            raise ResearchError("check_unsupported", "Check mode cannot prepare an operation requiring staged commits")
        original = self._read_preparation()._original_request
        if original is None:
            return None
        return {"operation": original["operation"], "payload": _load(original["payload"], dict),
                "response": _load(original["response"], dict)}

    committed_request = read_committed_request

    def mutate(self, operation, payload, apply, *, expected_revision, request_id, _preparation=None):
        if request_id != self._request_id or operation == "workspace.probe_artifact_intent":
            raise ResearchError("check_unsupported", "Check mode cannot prepare an operation requiring staged commits")
        encoded_payload = _encode_mutation_input(operation, payload, apply, expected_revision, request_id)
        self._read_preparation()
        original = _read_original_response(self._snapshot._original_request, operation, encoded_payload, request_id)
        if original is not None:
            raise _CheckComplete(original)
        _check_stored_bounds(payload)
        transaction = _CheckTransaction()
        try:
            result = apply(transaction)
            _check_stored_bounds(result)
            _canonical(result)
        finally:
            transaction._active = False
        snapshot_digest = _digest(_canonical({"database": self._snapshot._identity, "revision": self.revision,
                                              "history": self._snapshot._fingerprint.hex()}))
        raise _CheckComplete({"check": "accepted", "revision": self.revision, "recorded": False,
                              "operation": operation, "payload_digest": _digest(encoded_payload),
                              "snapshot_digest": snapshot_digest, "artifact_digests": self._artifacts.read_digests()})


def check_mutation(store, operation, payload, *, expected_revision, request_id, bounded_payload, replay=None):
    checking_store = _CheckingStore(store, request_id)
    try:
        check_command_bounds(bounded_payload)
    except ResearchError:
        if checking_store.committed_request(request_id) is None:
            raise
    if replay is not None:
        if checking_store._read_preparation()._original_request is None:
            raise ResearchError("check_unsupported", "Check mode cannot run commands with external or recovery effects")
        operation = replay
    try:
        operation(checking_store, payload, expected_revision=expected_revision, request_id=request_id)
    except _CheckComplete as completion:
        return completion.response
    except _CheckEffectRequired as error:
        raise ResearchError("check_unsupported", "Check mode cannot prepare external tool effects") from error
    raise ResearchError("check_unsupported", "Check mode requires one prepared mutation")
