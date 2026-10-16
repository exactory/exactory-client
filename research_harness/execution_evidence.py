"""Current author gates require the managed producer's observed immutable chain.

Low-level imported or modeled execution history remains readable. An origin label
or an old development receipt does not create a launcher observation. Native
mathematical proof acceptance has its separate controller and is unchanged.
"""

from .development import readiness_state
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .execution_outputs import log_bytes, output_metric, output_path, read_sealed_outputs, validate_observed_outputs
from .graph import obligation
from .workspace import read_file, strict_json


def validate_observed_execution(records, artifacts, identifier):
    execution = records["execution"][identifier]["payload"]
    admission_id = execution["origin"]["admission_id"]
    admission = records.get("execution_admission", {}).get(admission_id)
    claim = records.get("execution_claim", {}).get(admission_id)
    binding = records.get("execution_binding", {}).get(admission_id)
    observation = records.get("execution_observation", {}).get(admission_id)
    if any(value is None for value in (admission, claim, binding, observation)):
        raise ResearchError("execution_observation_required", "Managed candidate evidence needs the admitted launcher's durable claim and actual outcome observation")
    if (observation.get("claim_digest") != digest(claim) or observation.get("binding_digest") != binding["digest"]
            or observation.get("execution") != execution or claim["binding_digest"] != binding["digest"]
            or binding["admission_digest"] != admission["digest"] or execution["command"] != admission["command"]
            or records.get("execution_outcome", {}).get(admission_id) != {"execution_id": identifier}):
        raise ResearchError("execution_observation_mismatch", "The current managed result differs from its actual launch and outcome observation")
    config = strict_json(artifacts.read(claim["config_artifact"]))
    terminal = strict_json(artifacts.read(observation["terminal"]))
    artifacts.read(observation["log"])
    files = [{"path": binding["script"], "sha256": execution["command"]["program"]["sha256"]}]
    files.extend({"path": item["path"], "sha256": item["artifact"]["sha256"]} for item in binding["inputs"])
    if (config != claim["config"] or config["binding_digest"] != binding["digest"]
            or config["files"] != files or config["outputs"] != binding["outputs"]
            or digest({key: value for key, value in binding.items() if key != "digest"}) != binding["digest"]
            or config["seed"] != execution["command"]["seed"] or config["backend"] != binding["backend"]
            or config["timeout_seconds"] != binding["timeout_seconds"] or config["runtime"] != binding["runtime"]
            or terminal.get("config_sha256") != claim["config_artifact"]["sha256"]
            or terminal.get("binding_digest") != binding["digest"] or terminal["status"] != execution["status"]
            or terminal["exit_code"] != execution["exit_code"]):
        raise ResearchError("execution_observation_mismatch", "The producer configuration or terminal bytes differ from the recorded managed outcome")
    released = records.get("execution_remote_release", {}).get(admission_id)
    if binding["backend"] == "colab":
        from .remote_execution import _job
        if (released is None or released["job_sha256"] != digest(_job(claim))
                or released["admission_id"] != admission_id or terminal["runtime"] != released["worker"]["runtime"]):
            raise ResearchError("execution_observation_mismatch", "The remote result lacks its exact admitted runtime and one-time worker release")
    elif terminal.get("runtime") != binding["runtime"] or terminal.get("actual_argv") != config["argv"]:
        raise ResearchError("execution_observation_mismatch", "The local result differs from the admitted runtime or executed argv")
    validate_observed_outputs(artifacts, config, terminal, observation, execution)
    return {"admission": admission, "binding": binding, "claim": claim, "observation": observation,
            "remote_release": released}


def validate_probe_execution_observation(records, artifacts, execution, observation=None):
    """Identical bytes earn new prospective credit only from an actual producer chain."""
    admission_id = execution["origin"]["admission_id"]
    observation = observation or records.get("execution_observation", {}).get(admission_id)
    if observation is None:
        raise ResearchError("probe_exploratory_origin", "Probe artifacts remain exploratory when imported; an origin label and admission cannot create a prospective execution")
    directory = "research/runs/" + digest({"admission_id": admission_id})
    terminal = strict_json(artifacts.read(observation["terminal"]))
    if terminal.get("output_seal", {}).get("origin") == "recovery":
        return validate_probe_recovery_observation(records, artifacts, execution, observation, terminal, directory)
    # Reconciliation has verified the worker's seal before recording the outcome.
    # Its private observation is checked against durable terminal bytes too, so a
    # declarative observation cannot substitute for the producer's actual run.
    if terminal != strict_json(read_file(artifacts.root, directory + "/terminal.json")):
        raise ResearchError("execution_observation_mismatch", "The observation differs from the durable producer terminal")
    projected = dict(records,
        execution=dict(records.get("execution", {}), **{execution["id"]: {"payload": execution}}),
        execution_outcome=dict(records.get("execution_outcome", {}), **{admission_id: {"execution_id": execution["id"]}}),
        execution_observation=dict(records.get("execution_observation", {}), **{admission_id: observation}))
    validate_observed_execution(projected, artifacts, execution["id"])
    return True


def validate_probe_recovery_observation(records, artifacts, execution, observation, terminal, directory):
    """Preserve sealed incomplete history and its charge without prospective credit."""
    admission_id = execution["origin"]["admission_id"]
    admission = records.get("execution_admission", {}).get(admission_id)
    claim = records.get("execution_claim", {}).get(admission_id)
    binding = records.get("execution_binding", {}).get(admission_id)
    if any(value is None for value in (admission, claim, binding)):
        raise ResearchError("execution_observation_required", "Recovery needs the original admission, claim and binding")
    config = strict_json(artifacts.read(claim["config_artifact"]))
    if (execution["status"] != "interrupted" or execution["exit_code"] is not None
            or terminal.get("status") != "interrupted" or terminal.get("exit_code") is not None
            or terminal.get("config_sha256") != claim["config_artifact"]["sha256"]
            or terminal.get("binding_digest") != binding["digest"] or config != claim["config"]
            or observation.get("claim_digest") != digest(claim) or observation.get("execution") != execution
            or observation.get("binding_digest") != binding["digest"] or claim["binding_digest"] != binding["digest"]
            or binding["admission_digest"] != admission["digest"] or execution["command"] != admission["command"]
            or config["binding_digest"] != binding["digest"] or config["outputs"] != binding["outputs"]
            or digest({key: value for key, value in binding.items() if key != "digest"}) != binding["digest"]):
        raise ResearchError("execution_observation_mismatch", "The incomplete observation differs from its admitted recovery chain")
    try:
        read_file(artifacts.root, directory + "/terminal.json")
    except ResearchError as error:
        if error.code != "artifact_missing":
            raise
    else:
        raise ResearchError("execution_output_seal_required", "A producer terminal must keep its original worker seal")
    files = read_sealed_outputs(artifacts.root, directory, config, terminal)
    captured = observation.get("files")
    if (not isinstance(captured, list)
            or any(not isinstance(item, dict) or set(item) != {"path", "artifact"} for item in captured)
            or [item["path"] for item in captured] != list(files)
            or any(artifacts.read(item["artifact"]) != files[item["path"]] for item in captured)):
        raise ResearchError("execution_output_mismatch", "The recovered artifact inventory differs from the retained partial bytes")
    declared = [item for item in config["outputs"] if output_path(item["path"]) in files]
    if (len(execution["outputs"]) != len(declared)
            or any(output["id"] != item["id"] or output["requirement_id"] != item["requirement_id"]
                   or output["artifact"]["media_type"] != item["media_type"]
                   or artifacts.read(output["artifact"]) != files[output_path(item["path"])]
                   for output, item in zip(execution["outputs"], declared))
            or artifacts.read(observation["log"]) != log_bytes(files)
            or observation["summary"]["metric"] != output_metric(config, files)):
        raise ResearchError("execution_output_mismatch", "The incomplete execution differs from its recovered outputs and streams")
    return False


def author_readiness_state(records, artifacts):
    artifacts = Evaluation.of(records, artifacts)
    report = readiness_state(records, artifacts)
    candidate = report["candidate"]
    observations = {}
    obligations = list(report["obligations"])
    if candidate is not None:
        identifiers = {item["execution_id"] for item in candidate["evidence"] if item.get("kind") == "result"}
        for identifier in sorted(identifiers):
            execution = records["execution"][identifier]["payload"]
            if execution["origin"]["kind"] != "managed":
                continue
            try:
                observations[identifier] = validate_observed_execution(records, artifacts, identifier)
            except ResearchError as error:
                obligations.append(obligation(error.code, error.message, execution_id=identifier))
    return dict(report, ready=not obligations, obligations=obligations, execution_observations=observations)
