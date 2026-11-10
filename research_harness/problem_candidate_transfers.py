"""Record in the source that a slate candidate was taken up elsewhere."""

from pathlib import Path

from .errors import ResearchError
from .operations import fields, immutable_record, prepared_mutation, text
from .problem_candidates import find_slate
from .problem_claims import require_problem


def record_candidate_transfer(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "candidate_id", "destination_workspace", "reason"))
        for key in ("id", "candidate_id", "destination_workspace", "reason"):
            text(value[key], "Candidate transfer " + key)
        destination = Path(value["destination_workspace"])
        if not destination.is_absolute():
            raise ResearchError("invalid_input", "Give destination_workspace as the absolute path of the workspace of "
                                "the record that takes the candidate up.")
        slate = find_slate(records)
        if slate is None or value["candidate_id"] not in slate["candidate_ids"]:
            raise ResearchError("problem_candidate_transfer_not_on_slate", "The latest slate does not list this "
                                "candidate. Name a candidate that the latest slate selects, defers or rejects. When the "
                                "record holds no slate, or the candidate was recorded after the latest slate, record a "
                                "slate with problem-slate first.",
                                dict(candidate_id=value["candidate_id"], slate_id=None if slate is None else slate["id"]))
        try:
            destination = destination.resolve(strict=True)
        except FileNotFoundError as error:
            raise ResearchError("invalid_input", "destination_workspace does not exist. Give the absolute path of the "
                                "workspace of the record that takes the candidate up.") from error
        except (OSError, RuntimeError) as error:
            raise ResearchError("invalid_input", "destination_workspace cannot be resolved. Give the absolute path of "
                                "the workspace of the record that takes the candidate up.") from error
        if destination == store.root.resolve():
            raise ResearchError("problem_candidate_transfer_self", "destination_workspace is this record. Name the "
                                "workspace of the other record that takes the candidate up.")
        record = dict(value, destination_workspace=str(destination), recorded_revision=expected_revision + 1)
        return [immutable_record(records, "forming_transfer", value["id"], record)], record

    return prepared_mutation(store, "problem.candidate_transfer", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
