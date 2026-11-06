# Managed Codex hooks and retained session runtimes

This deployment requires an administrator and one initial fresh Codex session.
The plugin installer does not install system policy. Managed registrations avoid
the per-definition review of changed plugin hooks; the retained release supplies
the exact code and resources a session enrolled with. Codex 0.163.0 is the host
acceptance target. See the [official hooks documentation](https://learn.chatgpt.com/docs/hooks)
for the policy shape and trust rules.

## Administrator installation

Use a reviewed, complete release directory outside the managed prefix. Choose an
absolute Python 3.9+ interpreter protected from session writes. The interpreter,
its standard library, the prefix, `v1`, `releases`, registry, and their parent
directories must be administrator controlled. Run installation with that Python;
the printed commands pin its resolved absolute path and use isolated imports.

```sh
prefix=/usr/local/lib/exactory/managed
release=/absolute/path/to/reviewed/exactory
sudo /usr/bin/python3 -I "$release/bin/exactory-runtime" install --prefix "$prefix" > /tmp/exactory-requirements.toml
sudo "$prefix/v1/exactory-runtime" publish --prefix "$prefix" "$release"
```

Review and merge the printed block in `/tmp/exactory-requirements.toml` into
`/etc/codex/requirements.toml`. Preserve existing requirements and merge existing
`[features]` and `[hooks]` tables rather than duplicating them. The block enables
hooks, sets `managed_dir` to this prefix, and registers all eleven commands with
the plugin's matchers and timeouts. Do not set `allow_managed_hooks_only`.

Keep the entire prefix administrator owned. Releases, the approved-release
registry, the new-session default, and the publication lock live there. Only
`install` and `publish` write the prefix; hooks and `status` never write it or
open its publication lock for writing. Do not chown anything in the prefix to
the Codex account.

As the owning Codex account, without `sudo`, explicitly initialize private
session storage before starting a fresh session:

```sh
"$prefix/v1/exactory-runtime" init-state --prefix "$prefix"
"$prefix/v1/exactory-runtime" status --prefix "$prefix"
```

The default is `~/.local/state/exactory-runtime`, independently of the prefix.
New state and `sessions` directories have mode 700; the session lock and binding
files have mode 600. Each account uses its own directory and lock. Hooks run as
that account, without elevation, and serialize enrollment with this user lock.
They read one atomic snapshot of the administrator's registry; publication uses
its own lock and atomic replacement. Concurrent publication can select A or B
for a new session, while concurrent startup callbacks commit one binding.

`EXACTORY_RUNTIME_STATE_DIR` overrides the default for the dispatcher and CLI.
The `init-state` and `status` commands also accept `--state-dir /absolute/path`,
which takes precedence over the environment variable. For an override, initialize
that directory and set the same environment variable in the process launching
Codex and when running the smoke script. State inside the prefix is refused.
Missing, unwritable, or symlinked state directories are refused on every hook
event, with the usual event-specific status. Hooks never initialize or recreate
missing storage; use `init-state` deliberately and preserve existing bindings.
The bindings are operational state, not a privilege boundary against their owner.

Disable the duplicate plugin in the owner's active Codex configuration:

```toml
[plugins."exactory@exactory-ai"]
enabled = false
```

Start a fresh session. Commands already loaded from plugin cache paths cannot
be retrofitted. Retain or restore that exact old cache until those sessions finish.
SessionStart from the managed deployment emits the retained README and CLI path.
The plugin skill files remain unchanged in this implementation. Disabling the
plugin also removes their discovery; administrative stable skill entrypoints
under `/etc/codex/skills` still need the separate text-author work before this
deployment replaces the owner's complete skill workflow. Do not substitute a
symlink to the newest release for those entrypoints.

## Each release update

Run the release's normal tests and qualification checks before administrator
approval. Publishing is the explicit approval action; it does not certify a
research record or migrate its wrappers.

```sh
sudo "$prefix/v1/exactory-runtime" publish --prefix "$prefix" /absolute/path/to/reviewed/new-release
"$prefix/v1/exactory-runtime" status --prefix "$prefix"
```

Publication copies the complete plugin payload, including hidden manifests,
hooks, adapters, skills, documents, binaries and imported modules. Only `.git`,
`__pycache__`, `.pyc`, `.pyo`, and `.lane-scratch` development artifacts are
excluded. Symlinks and special files are refused; publish an unpacked release
with real files. The prefix must be outside the source directory.

The publisher compares two complete copies, hashes paths, bytes and executable
bits, writes a manifest, makes the bundle read-only and renames it to
`releases/<sha256>/`. It verifies the published copy before atomically writing
the approved registry and new-session default. `SessionStart` with source
`startup` also verifies the full payload digest before enrollment or dispatch.
Other hook events, including resume, clear and compaction, check the binding,
approval, manifest identity and entrypoints without hashing payload files or
walking the bundle. The manifest's canonical inventory must match the approved
digest; the release directory, manifest, `codex` directory and both entrypoints
must remain immutable, and entrypoints must be regular files. Subsequent events
rely on administrator protection of the published bytes. They do not perform a
fresh full-content audit. `status` explicitly audits the full approved bundles.

An interrupted publication can leave an unapproved complete bundle
or a hidden staging directory; neither is selectable by a session. The previous
registry remains usable until its atomic replacement. A corrupt published
identity must be recovered from the exact release, never overwritten with newer
bytes. There is no automatic deletion, revocation, or garbage collection in v1.
The removal API refuses referenced and approved releases.

New sessions bind to the new default. Existing bindings survive resume, clear,
compaction and process exit. Every SessionStart handler participates in the same
atomic enrollment, because Codex can launch them concurrently. Subagent events
use their parent's session id and therefore its binding. A fork with a new,
unbound id is refused; no parent is inferred from transcript text. There is no
automatic fork handoff in v1. Unknown or damaged bindings never select the
default on a tool call. Failure statuses are PreToolUse 2, PostToolUse 1,
SessionStart 1 and Stop 0; Stop emits no continuation decision.

Ordinary release updates do not reinstall or alter `v1` and do not require
policy edits. `install` is idempotent for identical code and interpreter; it
refuses replacing an existing v1 with different code. A dispatcher protocol
upgrade needs its own reviewed migration, while retained releases stay intact.

## Execution checks

Run this as the owning Codex account after the administrator has merged policy:

```sh
workspace=$(mktemp -d /private/tmp/exactory-managed-smoke.XXXXXX)
python3 tools/codex_hook_smoke.py --prefix /usr/local/lib/exactory/managed --workspace "$workspace"
```

The workspace must be empty and outside research records. The script initializes
`codex app-server --stdio`, prints its actual `hooks/list` answer, checks for
exactly the installed managed commands and no enabled duplicate Exactory plugin,
then feeds a synthetic harmless `Bash`/`true` event through every returned command
using `/bin/sh`. It runs SessionStart first and uses one new synthetic session id.
It prints command exit codes and output. `--codex /absolute/path/to/codex` selects
the host binary. Inspect `status` afterwards for that session's retained digest.

This checks discovery and direct execution. Final host acceptance belongs to the
supervisor: on real Codex 0.163.0, exercise an actual harmless tool in an existing
A thread, publish B and remove A's host cache, then exercise A again and a fresh
B thread. Inspect hook diagnostics for managed execution, check that a synthetic
protected write is denied and an ordinary write allowed, and check that activity
is recorded once and Stop produces one decision. Confirm A's context and CLI
paths stay under A's digest and B's under B's digest. A successful tool or a
`hooks/list` entry alone does not prove host-scheduled execution. Keep the existing
activation incidents open until this host acceptance passes.

For local regression tests (no administrator rights or host/model calls):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests python3.12 -m unittest test_hook_runtime_lifetime test_hook_startup test_codex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests /usr/bin/python3 -m unittest test_hook_runtime_lifetime test_hook_startup test_codex
python3 codex/generate.py --check
```
