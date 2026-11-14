"""Complete synthetic runtime keys and capacity witnesses for provider stubs."""

from research_harness import review_capacity_evidence as evidence


def build_capacity_runtime(adapter_name, **members):
    runtime = dict(adapter=adapter_name, implementation_sha256='a' * 64,
                   input_contract='synthetic-explicit-input-v1')
    if adapter_name != 'openai_responses_v1':
        runtime['credential_mode'] = 'synthetic-subscription'
        if adapter_name == 'claude_cli_v1':
            runtime['flags'] = ['synthetic-print']
        else:
            runtime.update(version='fixture-cli', controls=['synthetic-isolation'])
    runtime.update(members)
    return runtime


def build_capacity_prior(route, tokens, identifier='fixture-prior'):
    return dict(id=identifier, key=evidence.describe_capacity_key(route), input_tokens=tokens)
