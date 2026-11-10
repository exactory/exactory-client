"""Record sourced findings about a post without changing its reviewed evidence."""

from .errors import ResearchError
from .operations import fields, immutable_record, prepared_mutation, text
from .problem_claims import require_problem
from .strategy import evidence, get_record


def record_post_fact(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "post_id", "fact", "source", "date", "bears_on"))
        for key in ("id", "post_id", "fact", "date"):
            text(value[key], "The post fact field " + key)
        fields(value["bears_on"], ("value", "description"))
        for key in ("value", "description"):
            text(value["bears_on"][key], "The bears_on field " + key)
        get_record(records, "problem_post", value["post_id"], code="problem_post_missing")
        source = value["source"]
        if (not isinstance(source, dict) or not ("path" in source or
                source.get("kind") == "record" and source.get("record_kind") == "reading")):
            raise ResearchError("invalid_input",
                                "Give source as a stored artifact descriptor {path, sha256, size, media_type}, or as an "
                                "exact reading record {kind: record, record_kind: reading, id, digest}. Store a reply or "
                                "a note with the artifact command first")
        evidence(records, artifacts, [source])
        saved = records.get("problem_post_fact", {}).get(value["id"])
        record = dict(value, recorded_revision=saved["recorded_revision"] if saved else expected_revision + 1)
        return [immutable_record(records, "problem_post_fact", value["id"], record)], record

    return prepared_mutation(store, "problem.post_fact", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
