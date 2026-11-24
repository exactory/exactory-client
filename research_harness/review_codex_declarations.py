"""The audited native declarations of the Codex CLI: the developer inputs that the CLI itself adds to a call.

A set is added only after an audit of the complete developer inputs of a retained rollout, and no set is removed.
No route record pins this module, so a release that adds an audited set makes no route record stale. The Codex
transports import these names and compare the developer inputs of each rollout with them.

MODEL_LISTING_PATTERN, MODEL_LISTING_PLACEHOLDER and remove_model_listing never change: a stored call accepted
through a listing-free set authenticates only while the installed inspection gives its recorded evidence again. A new
form of the listing gets new names and new sets.
"""

import re

# These complete normalized declarations were inspected on Codex 0.159.2 with
# configuration and rules disabled. A new declaration or platform stays
# unverified until its full input contract has been audited and tested.
NATIVE_DECLARATIONS = (
    'dfc8dfcb5cdb5a5382ea269f65f676cfb16dcde606c2c222beedb90c17505402',
    '47091490938505958b0c22ff42db6fd79b272a2af6923166f4c2cc53c4fe0df4',
    '6ded806e3cdbb35599ecaf8742574bc5274908472b1729090010c404c2151e8e',
)
# Codex 0.162.1 (audited 2026-10-20 on the rollout of probe-codex-astra, thread
# 01a15ae2-2d7d-7070-a9ac-8cc9931e4c7c): the three developer messages are the
# <skills_instructions> list of the .system skills with <collaboration_mode>, the
# <multi_agent_role> text and the <multi_agent_mode> text; no project, user or
# plugin context appears in them.
NATIVE_DECLARATIONS_0162 = (
    '696b6c75d877084ce308423c72f77f2e8cdb93a2f4ac9ede6e1c3379e7ea8bab',
    '3edd51083129388f52e881435e93e4b981e00c5ee3d62ab5d32e22c428fd504b',
    '6ded806e3cdbb35599ecaf8742574bc5274908472b1729090010c404c2151e8e',
)
# Codex 0.167.0 (audited 2026-11-10 on the rollouts of two result reviews, threads
# 01a1c54c-e346-7f90-bcc4-e7e59cc36993 and 01a1c54c-e339-7ee2-8231-dd666c828603):
# the rollout first records the model's tool list as an additional_tools item (the
# functions, clock and collaboration namespaces; no MCP, plugin, app, memory or user
# tool), then the review instructions themselves as a developer message, then the
# three 0.162.1 declarations unchanged. The tool list is the same in both rollouts
# and holds no path; it changes when user configuration is loaded, so it is pinned
# by digest. The instructions message is accepted only when its text is exactly the
# request's instructions.
NATIVE_TOOL_DECLARATIONS_0167 = 'a066e4e81a2d3e62b1ac3cd628a56afce8ff553b858fa78cb294ea87bfcfe68d'
DELIVERED_INSTRUCTIONS_ID = 'request.instructions'
NATIVE_DECLARATIONS_0167 = (NATIVE_TOOL_DECLARATIONS_0167, DELIVERED_INSTRUCTIONS_ID) + NATIVE_DECLARATIONS_0162
NATIVE_DECLARATION_SETS = {NATIVE_DECLARATIONS, NATIVE_DECLARATIONS_0162, NATIVE_DECLARATIONS_0167}
NATIVE_ENVIRONMENTS = {'722c613b0eff734b92f076c866ff2eb55f6298c54180d9d414868d070a6c3ae8'}

# The model listing in the description of collaboration.spawn_agent: one line for each model of the provider's
# catalog, after the line that introduces the overrides. The provider changes these lines when its catalog changes;
# they hold no context of the project or of the user. A line of another form is not removed.
MODEL_LISTING_PATTERN = re.compile(
    r'(?<=Available model overrides \(optional; inherited parent model is preferred\):\n)'
    r'(?:- `[a-z0-9.-]+`: [^\n]*\. Reasoning efforts: [a-z ,()]+\. Service tiers: [a-z, ]+\.\n)+')
MODEL_LISTING_PLACEHOLDER = '{MODEL_LISTING}\n'

# Developer inputs whose tool list is compared with its model listing replaced by MODEL_LISTING_PLACEHOLDER.
LISTING_FREE_DECLARATION_SETS = {
    # The audited 0.167.0 tool list of 2026-11-10 without its listing, with the 0.162.1 declarations.
    ('2ec7b8ab66029a8461afbc5b08ac79b1fb01c8c9e0fdbfb6b1454757d081ed4c', DELIVERED_INSTRUCTIONS_ID)
    + NATIVE_DECLARATIONS_0162,
    # Codex 0.167.0 with the models gpt-6.2-astra and gpt-6.2-sol (audited 2026-11-22 on the retained rollout of a
    # route probe of gpt-6.2-astra). The tool list holds the namespaces functions (exec, wait, request_user_input,
    # request_user_input_async, send_message_to_user_async), clock (sleep) and collaboration (followup_task,
    # interrupt_agent, list_agents, send_message, spawn_agent, wait_agent); it holds no MCP, plugin, app, memory or
    # user tool and no path. Against the list of 2026-11-10 it adds send_message_to_user_async and differs in the
    # model listing only. The <multi_agent_role> message is the catalog text of the gpt-6.2 models followed by the
    # text that is common to the earlier models; it holds no path and no text of a project, a user or a plugin.
    ('8788734479b727bef02c931404bff0aee9a54b5c888073e3dec166f1903d66a1', DELIVERED_INSTRUCTIONS_ID,
     '696b6c75d877084ce308423c72f77f2e8cdb93a2f4ac9ede6e1c3379e7ea8bab',
     '7069a9f039694cd8325c7e2e3dc61f6da1eead8f92d0d42b606edda70ad42515',
     '6ded806e3cdbb35599ecaf8742574bc5274908472b1729090010c404c2151e8e'),
}


def remove_model_listing(tools):
    """A new copy of a tool list with the model listing of every description string replaced by the placeholder."""
    if isinstance(tools, list):
        return [remove_model_listing(item) for item in tools]
    if isinstance(tools, dict):
        return {key: MODEL_LISTING_PATTERN.sub(MODEL_LISTING_PLACEHOLDER, value)
                if key == 'description' and isinstance(value, str) else remove_model_listing(value)
                for key, value in tools.items()}
    return tools
