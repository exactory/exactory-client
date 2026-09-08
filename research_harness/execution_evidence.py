"""Current author gates require the managed producer's observed immutable chain.

Low-level imported or modeled execution history remains readable. An origin label
or an old development receipt does not create a launcher observation. Native
mathematical proof acceptance has its separate controller and is unchanged.
"""

from .development import readiness_state
from .errors import ResearchError
from .evidence import digest
from .execution_outputs import validate_observed_outputs
from .graph import obligation
from .workspace import strict_json


def _observed(records, artifacts, identifier):
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


def author_readiness_state(records, artifacts):
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
                observations[identifier] = _observed(records, artifacts, identifier)
            except ResearchError as error:
                obligations.append(obligation(error.code, error.message, execution_id=identifier))
    return dict(report, ready=not obligations, obligations=obligations, execution_observations=observations)
