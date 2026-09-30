#!/usr/bin/env python3
"""PreToolUse gate: experiment code is model-written, so keep it inside the study.

Stage 3 of Exactory AI Science runs code the agent wrote. This hook is the
enforcement behind the experiment skill's safety rules: it blocks the
catastrophic class of shell commands when the run is inside a study workspace
(an ancestor of the payload's `cwd` holds `.exactory/study.json`). Outside a
workspace, and for any tool other than Bash, it stays neutral.

The hook is a denylist, not a sandbox: it cannot contain a process that writes
through an absolute path. `exactory-lab run` confines the working directory and
refuses scripts outside `experiment/`; this hook stops the shell commands that
would escape or damage the machine regardless of cwd. A block means redesign
the experiment, never route around the guard.

A heredoc body is data when nothing runs it, for example a note that quotes a
denied command, so the rules skip it. The shell expands a body whose delimiter
is not quoted, so such a body stays checked when it holds a command
substitution. Every body stays checked when the text outside the bodies,
read without its quotes and escapes, can run the text of a body as commands:
it names a program that runs text (anywhere, a shell, also when an expansion
spells its name, eval, source, trap, ssh or xargs; at the start of a command,
the dot command, make, at, batch, a script run by its path, or an expansion
run as a command, such as $0), it stores text in a variable that a later
expansion can run (read, mapfile or readarray), or it holds a command or
process substitution or backquotes, which can capture a body or make the shell
start or end a body elsewhere than the scanner reads. Every body also stays
checked when the scanner cannot read the heredocs as the shell does: a
delimiter that is not a plain word, a missing terminator, a line of a body
with an unquoted delimiter that ends in a backslash and joins the next line,
or a parameter expansion or arithmetic that it cannot read. A body that
another interpreter reads, such as python3, is data for this hook, although
that interpreter can start a shell; so is a script that a heredoc writes for
a later command line, which the hook judges by that command's own text.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_STUDY_STATE_PATH = Path(".exactory") / "study.json"

# Quoted text or an escaped character, in which shell operators are literal.
_QUOTED_TEXT_PATTERN = r"""\$'(?:\\.|[^'\\])*'|'[^']*'|"(?:\\.|[^"\\])*"|\\."""
# Quoted text, an escaped line break, arithmetic, a parameter expansion and a
# comment, in which "<<" opens no heredoc; the start of such a construct that
# this pattern cannot read; then a heredoc operator with its delimiter word,
# and an unquoted line break.
_HEREDOC_SCAN_RE = re.compile(
    _QUOTED_TEXT_PATTERN + r"""|\(\((?:[^()]|\([^()]*\))*\)\)|\$\{[^{}]*\}|\$\[[^\[\]]*\]|(?<![^\s;&|()])#[^\n]*"""
    r"""|(?P<unreadable>\(\(|\$\{|\$\[)"""
    r"""|(?<!<)<<(?!<)(?P<strip_tabs>-?)[ \t]*"""
    r"""(?P<word>(?:'[^'\n]*'|"[^"\n]*"|\\.|[^\s;&|<>()'"\\])+)"""
    r"""|(?P<line_break>\n)""",
    re.DOTALL)
# A delimiter word that the scanner reads as the shell does: a plain word, bare
# or quoted as a whole. The shell expands the body of a bare delimiter.
_PLAIN_DELIMITER_RE = re.compile(r"""(?P<bare>[\w-]+)|'(?P<single>[\w-]+)'|"(?P<double>[\w-]+)"|\\(?P<escaped>[\w-]+)""",
                                 re.ASCII)
# An expansion that runs a command in a heredoc body with an unquoted delimiter.
_COMMAND_SUBSTITUTION_RE = re.compile(r"`|\$\(")
# Quotes, escapes and escaped line breaks, which the shell removes from words.
_QUOTING_RE = re.compile(r"""\\\n|['"\\]""")
# The text of a word up to a space or an operator. It holds no character after
# which a command starts, so each scan from a command start stays linear.
_WORD_TEXT_PATTERN = r"[^\s;&|(){}`!<>]*"
# The shells of macOS and common Linux systems, which run the text that they
# read as commands.
_SHELL_NAME_PATTERN = "sh|ash|bash|rbash|dash|zsh|ksh|mksh|csh|tcsh|fish"
# An action of find that runs the words after it as a command.
_FIND_EXEC_ACTION_PATTERN = r"-(?:exec|execdir|ok|okdir)[ \t]+"
# The start of a command word: the text start, a line break, one of
# ; & | ( ) { ` ! or an -exec action of find, then any keywords, variable
# assignments, options, numbers, expansions and commands that run the word
# after them, such as env, watch, xargs and bash -c. An option is never an
# -exec action, which starts a command itself, so the scan from one command
# start ends before the next and stays linear.
_COMMAND_POSITION_PATTERN = (
    r"(?:^|(?<=[\n;&|(){`!])|(?<![^\s])" + _FIND_EXEC_ACTION_PATTERN + r")[ \t]*"
    r"(?:(?:if|then|elif|else|do|while|until|time|builtin|caffeinate|command|env|eval|exec|ionice|nice|nohup"
    r"|setsid|stdbuf|strace|taskset|timeout|unbuffer|watch|xargs|" + _SHELL_NAME_PATTERN +
    r"|[A-Za-z_][A-Za-z0-9_]*=" + _WORD_TEXT_PATTERN + r"|(?!" + _FIND_EXEC_ACTION_PATTERN + r")[-0-9$]"
    + _WORD_TEXT_PATTERN + r")[ \t]+)*")
# Text outside the heredoc bodies, read without its quotes and escapes, through
# which the shell can run the text of a body as commands. Anywhere in the
# command: a shell, ssh, eval, source, trap, xargs, $SHELL, $BASH or $0, with or
# without a directory, also as zsh (=bash) or brace expansion ({bash,}) spells
# it; or a command or process substitution or backquotes, which can capture a
# body as a value that a later expansion runs, or span lines, so that the shell
# starts or ends a body elsewhere than the scanner reads. At the start of a
# command: ".", make, at and batch, which run their input as shell command
# lines; read, mapfile and readarray, which store text in a variable that eval,
# trap or bash arithmetic can later run; a path, which runs a script such as one
# that a heredoc wrote; or a word that holds an expansion, whose value can name
# a shell. The directory before a name holds no character after which a name
# can start, so each scan from such a start stays linear.
_RUNS_BODY_TEXT_RE = re.compile(
    r"(?<![^\s;&|()`{,=])(?:[^\s;&|()`<>{,=]*/)?"
    r"(?:" + _SHELL_NAME_PATTERN + r"|ssh|eval|source|trap|xargs|\$\{?(?:SHELL|BASH|0)\}?)"
    r"(?![^\s;&|()`<>,}])"
    r"|`|[$<>=]\("
    r"|" + _COMMAND_POSITION_PATTERN + r"(?:\.[ \t]|(?:make|at|batch|read|mapfile|readarray)(?![^\s;&|()`<>])"
    r"|[^\s;&|(){}`!<>=]*[$/])",
    re.IGNORECASE)
# A shell word: quoted text, an expansion or plain text. A command
# substitution or an arithmetic expansion can hold one level of parentheses,
# as in $(dirname "$(pwd)") and $((0)).
_SHELL_WORD_PATTERN = (r"(?:" + _QUOTED_TEXT_PATTERN + r"|\$\((?:[^()]|\([^()]*\))*\)|\$\{[^{}]*\}|`[^`]*`"
                       r"""|[^\s;&|<>()'"`\\])+""")
# An output redirection operator, which writes its target: > or >>, with & for
# stderr as well (&>, >&, &>> and >>&) and with the clobber mark | or, in zsh,
# !; and <>, which opens its target for reading and writing. A descriptor
# number before the operator does not change the target.
_OUTPUT_REDIRECTION_PATTERN = r"(?:&>>?|>>?&?)[|!]?|<>"
# The target of an output redirection. Quoted text is read as well, because
# bash -c "echo x > path" writes through a redirection inside quotes. A target
# that stops at "(" holds a substitution nested more deeply than
# _SHELL_WORD_PATTERN reads, so the rest of its line counts as the target.
_REDIRECTION_TARGET_RE = re.compile(
    r"(?:" + _OUTPUT_REDIRECTION_PATTERN + r")[ \t]*(?P<target>" + _SHELL_WORD_PATTERN + r"(?:\([^\n]*)?)")
# A redirection with its word, or a process substitution, which can hold one
# level of parentheses. Neither is a file argument of tee: the state-write
# check reads an output redirection target by itself, and a process
# substitution passes tee a pipe.
_REDIRECTION_OR_PROCESS_SUBSTITUTION_PATTERN = (
    r"(?:" + _OUTPUT_REDIRECTION_PATTERN + r"|<<<|<<-?|<&?)[ \t]*" + _SHELL_WORD_PATTERN
    + r"|[<>]\((?:[^()]|\([^()]*\))*\)")
_REDIRECTION_OR_PROCESS_SUBSTITUTION_RE = re.compile(_REDIRECTION_OR_PROCESS_SUBSTITUTION_PATTERN)
# The operands of tee up to the end of its command: its words, redirections and
# process substitutions in any order. tee counts with or without its directory,
# as a command word in a command read without its quotes and escapes, so "tee"
# and bash -c "tee path" count and grep tee path does not. The same rule for
# "(" applies as for a redirection target.
_TEE_OPERANDS_RE = re.compile(
    _COMMAND_POSITION_PATTERN + r"(?:" + _WORD_TEXT_PATTERN + r"/)?tee\b(?P<operands>(?:[ \t]*(?:"
    + _REDIRECTION_OR_PROCESS_SUBSTITUTION_PATTERN + r")|[ \t]+" + _SHELL_WORD_PATTERN + r")*(?:[ \t]*[<>]?\([^\n]*)?)",
    re.IGNORECASE)

# (compiled pattern, reason). First match denies. IGNORECASE throughout.
_DENY_RULES = [
    (r"\bsudo\b|\bdoas\b|\bsu\s+-", "privilege escalation is not allowed in experiments"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r",
     "recursive force-delete: keep deletions inside the workspace"),
    (r"\bmkfs\b|\bdd\b[^\n]*\bof=/dev/|>\s*/dev/(sd|disk|nvme)|diskutil\s+.*erase",
     "raw disk or device write"),
    (r"(curl|wget|fetch)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|c|tc|k)?sh\b",
     "piping a downloaded script straight into a shell"),
    (r"(curl|wget|fetch)\b[^|;&]*\|\s*python[0-9.]*\b",
     "piping downloaded content into python"),
    (r"/etc/shadow|/etc/sudoers|~/\.ssh/|/\.ssh/id_|~/\.aws/credentials|\.aws/credentials",
     "access to credentials or secret material"),
    (r"\bsecurity\s+find-(generic|internet)-password\b|\bkeychain\b.*\bdump",
     "keychain credential extraction"),
    (r"\bcrontab\b|\blaunchctl\s+(load|unload|bootstrap)|/Library/LaunchDaemons|"
     r"/Library/LaunchAgents", "installing a persistence mechanism"),
    (r"(^|[\s;&|])(>|>>)\s*/etc/|\btee\s+/etc/", "writing into /etc"),
    (r"\bkillall\b|\bpkill\s+-9\b|\bkill\s+-9\s+-1\b", "broad process kill"),
    (r"\.claude/(settings(\.local)?\.json|hooks/)",
     "modifying Claude Code config or hooks"),
    (r"\bchmod\s+-R?\s*0?777\b", "world-writable chmod 777"),
]
_COMPILED_DENY_RULES = [(re.compile(pattern, re.IGNORECASE), reason)
                        for pattern, reason in _DENY_RULES]


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"[guard-experiment-exec] Blocked: {reason}. Redesign the"
                " experiment to stay inside the workspace and avoid this"
                " action; do not route around the guard."
            ),
        }
    }))
    sys.exit(0)


def _is_inside_study_workspace(start_dir: Path) -> bool:
    for directory in (start_dir, *start_dir.parents):
        if (directory / _STUDY_STATE_PATH).is_file():
            return True
    return False


def _remove_data_heredoc_bodies(command: str) -> str:
    """Remove the data heredoc bodies when nothing in the command can run their text as commands.

    The command stays unchanged, so every body stays checked, when its text
    outside the data bodies can run the text of a body or when the scanner
    cannot read the heredocs as the shell does. A body with an unquoted
    delimiter that holds a command substitution is not data.
    """
    kept, delimiters = [], []
    copied_to = position = 0
    while True:
        token = _HEREDOC_SCAN_RE.search(command, position)
        if token is None:
            break
        position = token.end()
        if token.group("unreadable") is not None:
            return command
        if token.group("word") is not None:
            delimiter = _PLAIN_DELIMITER_RE.fullmatch(token.group("word"))
            if delimiter is None:
                return command
            leading_tabs_pattern = "\t*" if token.group("strip_tabs") else ""
            pattern = "^" + leading_tabs_pattern + re.escape(delimiter.group(delimiter.lastgroup)) + "$"
            delimiters.append((pattern, delimiter.lastgroup != "bare"))
            continue
        if token.group("line_break") is None:
            continue
        for pattern, is_quoted in delimiters:
            terminator = re.compile(pattern, re.MULTILINE).search(command, position)
            if terminator is None:
                return command
            body = command[position:terminator.start()]
            # The shell expands this body and joins a line that ends in a
            # backslash to the next one, which can hide the real terminator.
            if not is_quoted and "\\\n" in body:
                return command
            if is_quoted or not _COMMAND_SUBSTITUTION_RE.search(body):
                kept.append(command[copied_to:position])
                copied_to = terminator.end() + 1
            position = terminator.end() + 1
        delimiters = []
    if not kept:
        return command
    outside = "".join(kept) + command[copied_to:]
    return command if _RUNS_BODY_TEXT_RE.search(_QUOTING_RE.sub("", outside)) else outside


def _has_workspace_state_write(command: str) -> bool:
    """Whether a redirection target or a tee file argument lies under .exactory/.

    The whole directory counts, not a list of the files in it: the CLI and the
    hooks own every file there, and a list of names goes stale each time the
    workspace gains a state file. Paths are compared without their quotes and
    escapes, as the shell opens them. The file arguments of tee are its
    operands other than redirections and process substitutions.
    """
    return (any(".exactory/" in _QUOTING_RE.sub("", match.group("target")).lower()
                for match in _REDIRECTION_TARGET_RE.finditer(command))
            or any(".exactory/" in _REDIRECTION_OR_PROCESS_SUBSTITUTION_RE.sub(" ", match.group("operands")).lower()
                   for match in _TEE_OPERANDS_RE.finditer(_QUOTING_RE.sub("", command))))


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command", "")
    if command == "":
        sys.exit(0)
    if not _is_inside_study_workspace(Path(payload.get("cwd") or ".").resolve()):
        sys.exit(0)
    command = _remove_data_heredoc_bodies(command)
    for pattern, reason in _COMPILED_DENY_RULES:
        if pattern.search(command):
            _deny(reason)
    if _has_workspace_state_write(command):
        _deny("writing a workspace state file through the shell")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
