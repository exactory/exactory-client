# Stored unbound openness packets of 0.87.0

Source: incident `20261113110022-every-open-record-and-review-capacity`, closure r4.2 C8, ruling 30.
Study text, owner instructions, work identities, observations and workspace metadata are synthetic.
Harness prompts retain the tag's templates.

Each archive holds the common store database and its artifacts. Tests copy its regular files before opening it.
`manifest.json` gives the SHA-256 of **every file**, native packet, captures, stored turn ids, the tag's plan,
and the tag verifier's delivered units. The six finished stores preserve the original stored finding as well.
The ten stopped stores preserve only the recorded prefix: no later turn or request was fabricated.
Math stores contain the shared review store; native controller caches with machine-local roots are excluded.
The math manifest `review` value is the authored stub for the later final call, not a historical observation.

The source tag was extracted once:

```sh
mkdir -p .lane-tmp/tag-0870
git archive exactory--v0.87.0 | tar -x -C .lane-tmp/tag-0870
```

The six original stores were built with the first command, and the ten additional r4.2 stores with the next two.
The later commands keep the existing finished archives untouched.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/.lane-tmp/tag-0870/tests:$PWD/.lane-tmp/tag-0870" /opt/homebrew/bin/python3.12 tests/fixtures/openness_0870_choice/build_fixture.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/.lane-tmp/tag-0870/tests:$PWD/.lane-tmp/tag-0870" /opt/homebrew/bin/python3.12 tests/fixtures/openness_0870_choice/build_stopped.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/.lane-tmp/tag-0870/skills/math-solver/harness:$PWD/.lane-tmp/tag-0870/tests:$PWD/.lane-tmp/tag-0870" /usr/bin/python3 tests/fixtures/openness_0870_choice/build_stopped_math.py
```

The builders adapt the earlier writer probe to keep the stores. Synthetic workspace roots and runtime provenance
are supplied **before** any records or digests are built. Stored bytes are never rewritten. The transport/runtime
stubs are the neighboring fixtures' normal stubs. Archive owner names and timestamps are blank. No test reads git history
or invokes a fixture builder. The separately frozen builders used for live-built replays are completed from the same tag.

For the stopped stores, A is `arxiv:2601.00001v1` and B is `arxiv:2601.00002v1`. The chosen B extraction is
50,000 characters. A fills the first reading turn (120,000 characters in a problem chain; 119,000 plus a
1,000-character search response in the math chain). The normal inventory has two reading turns and then a final
assignment. Pending B earns no delivery credit before its own request.

The tag **can export both extractions of one original**. In the problem variant, the chosen bundle also has a
required unit inspecting the earlier extraction; the tag exporter carries both inspected bodies while the plan
names the chosen one. The math evidence closure already carries both captures from the work record. These are
actual exports by the tag, with no post-export packet alteration.

Actual tag verifier delivery at the saved state (first/chosen columns describe native packet evidence):

| Store | Delivered units | B in `remaining` | First / chosen body carried |
| --- | --- | --- | --- |
| `math-stopped-chosen-observed` | search response (`6a318146e02bbac7323f880d943bbd91a98dd1c0d08a9284b55d88e06c09ee22`) + A full text (`21e6aeddf50ee7ed94b8fc924c1c7789e2fe71476cd2b1ac35d1b0fdd76969ec`) | chosen B | true / true |
| `math-stopped-chosen-uncalled` | none; no reviewer call | chosen B | true / true |
| `v4-stopped-both-observed` | A full text (`179b27e54f3976fe7fd02aef42dd24b3187b5bd961454f10778d891e4027da7d`) | chosen B | true / true |
| `v4-stopped-choice-observed` | A full text (`179b27e54f3976fe7fd02aef42dd24b3187b5bd961454f10778d891e4027da7d`) | no B unit (case 2) | true / false |
| `v4-stopped-chosen-observed` | A full text (`179b27e54f3976fe7fd02aef42dd24b3187b5bd961454f10778d891e4027da7d`) | chosen B | false / true |
| `v4-stopped-chosen-uncalled` | none; no reviewer call | chosen B | false / true |
| `v5-stopped-both-observed` | A full text (`179b27e54f3976fe7fd02aef42dd24b3187b5bd961454f10778d891e4027da7d`) | chosen B | true / true |
| `v5-stopped-choice-observed` | A full text (`179b27e54f3976fe7fd02aef42dd24b3187b5bd961454f10778d891e4027da7d`) | no B unit (case 2) | true / false |
| `v5-stopped-chosen-observed` | A full text (`179b27e54f3976fe7fd02aef42dd24b3187b5bd961454f10778d891e4027da7d`) | chosen B | false / true |
| `v5-stopped-chosen-uncalled` | none; no reviewer call | chosen B | false / true |

Archive SHA-256 values (per-file hashes are in `manifest.json`):

- `math-stopped-chosen-observed.tar.xz`: `8e4abe108b50526b3a2d16a8062110a89d3205663769e4d3b1221a6fd52e7a07`
- `math-stopped-chosen-uncalled.tar.xz`: `70d4b774baee8de3af77746c8e9dac0d035376536a36c7788dff2d897167c876`
- `v4-choice.tar.xz`: `c18d902512ed8b046f57da26823e411e13441b821816950602d7a7291c46d942`
- `v4-control.tar.xz`: `0be423c8692742b7b2f8c87df57383a4582b91c14361621d9385d1dcd691b57e`
- `v4-stopped-both-observed.tar.xz`: `3aae4e96f5f227e0a511c61a7b842875fe954cedcb4fdbbac349b17f3264ad6a`
- `v4-stopped-choice-observed.tar.xz`: `e4bbfa9eec752ecd7af6de2d6363a46a432da8c1e506e293e2ac50b3bb965275`
- `v4-stopped-chosen-observed.tar.xz`: `45c268ad9e428450f5bb552c366c49ac571e54ab0b52ecdc5d3e3d613bf12062`
- `v4-stopped-chosen-uncalled.tar.xz`: `2500aa45f367e89580547fd29b263303ed47263ed049abcd5f3392500cc09624`
- `v4-workaround.tar.xz`: `f743b80ea84b78659eca2f3433ff4e126388e90942a7c7c401ada2b931e52b45`
- `v5-choice.tar.xz`: `7ae5588182e8440664a7baddbb7842f9a8a223521c1eeb248ebc5d82f527c878`
- `v5-control.tar.xz`: `7b0964dae9123f13afe81d964e67308c6827355ebe444208343ba5b318b187fa`
- `v5-stopped-both-observed.tar.xz`: `5cae8797b560e3c000f42788e7d895ff9563c8459122669828cc047a9b82f91c`
- `v5-stopped-choice-observed.tar.xz`: `9432869e712c22052d0b63fa299535f0f741c870bd67f923bddde3d476d91288`
- `v5-stopped-chosen-observed.tar.xz`: `b0371cf2bd51fa8ed3d9d1db0fee35a99389bd6188321d1949711babca7b5cbf`
- `v5-stopped-chosen-uncalled.tar.xz`: `4854681b32a255f426fc82b48851d741bd5bdc43b8e2214475ebcde3afae8f5d`
- `v5-workaround.tar.xz`: `c609b66589e703dd332a7450592da3fc06a35da14532229b77b702b03467d715`
