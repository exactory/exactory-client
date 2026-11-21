"""Change one node of a valid command payload at a time (design 0.91.8 D5).

A command ends every changed payload with an acceptance or with a ResearchError. find_unrefused_changes lists each
change that ends with another exception, so a test can run it on the payload of any command.
"""

import copy
import json

from research_harness.errors import ResearchError

# The values that replace a node, one at a time: null, a text, a number, a boolean, an empty list and an empty object.
REPLACEMENT_VALUES = (None, "text", 1, True, [], {})
UNKNOWN_MEMBER_NAME = "unknown_member"


def list_node_paths(node, path=()):
    """The path of the node and of every node below it, as tuples of member names and list indexes."""
    paths = [path]
    children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, child in children:
        paths.extend(list_node_paths(child, path + (key,)))
    return paths


def build_payload_changes(payload):
    """Every change of one node, as (path, change, changed payload): the removal of the node from its object or its
    list, each replacement value in its place, and an unknown member added to an object."""
    changes = []
    for path in list_node_paths(payload):
        changed = copy.deepcopy(payload)
        node = changed
        for key in path:
            node = node[key]
        if isinstance(node, dict):
            node[UNKNOWN_MEMBER_NAME] = "text"
            changes.append((path, "add the member " + UNKNOWN_MEMBER_NAME, changed))
        if not path:
            changes.extend(((), "replace with " + json.dumps(value), value) for value in REPLACEMENT_VALUES)
            continue
        for change, value in [("remove", None)] + [("replace with " + json.dumps(value), value)
                                                   for value in REPLACEMENT_VALUES]:
            changed = copy.deepcopy(payload)
            parent = changed
            for key in path[:-1]:
                parent = parent[key]
            if change == "remove":
                del parent[path[-1]]
            else:
                parent[path[-1]] = copy.deepcopy(value)
            changes.append((path, change, changed))
    return changes


def build_member_additions(payload, members):
    """For every object of the payload and every member of members that it lacks, the payload with that member added,
    as (path, change, changed payload)."""
    changes = []
    for path in list_node_paths(payload):
        node = payload
        for key in path:
            node = node[key]
        if not isinstance(node, dict):
            continue
        for name in sorted(members.keys() - node.keys()):
            changed = copy.deepcopy(payload)
            target = changed
            for key in path:
                target = target[key]
            target[name] = copy.deepcopy(members[name])
            changes.append((path, "add the member " + name, changed))
    return changes


def find_unrefused_changes(payload, call, changes=None):
    """Run call on every changed payload: by default the changes of build_payload_changes. Return (path, change,
    exception) for each change that ends with an exception other than ResearchError; an acceptance and a ResearchError
    are the two admitted ends."""
    found = []
    for path, change, changed in build_payload_changes(payload) if changes is None else changes:
        try:
            call(changed)
        except ResearchError:
            continue
        except Exception as error:  # Every other exception is the finding that this helper reports.
            found.append(("/".join(map(str, path)), change, type(error).__name__ + ": " + str(error)))
    return found
