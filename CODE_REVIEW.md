# Independent runtime repair review

Review date: 2026-09-20.

Base: `87570b433a0c75356dc76fdc3048118ae3547c0e`.

Scope: the uncommitted acquisition, full-text, reading, OCR, documentation, and test changes, including `REPAIR_NOTES.md`. This review used disposable test stores only. No live study command, database edit, source edit, commit, or push was performed.

## Strengths

The repair retains original-byte identity checks, required-unit flags, separate inspection obligations, and the stricter standalone-supplement boundary. Required text coverage rejects substantive gaps and optional-only coverage. The OCR implementation uses explicit arguments, a shared finite deadline, bounded subprocess output, complete per-page iteration, and measured tool version output. OCR failures return pending results without partial extracted text.

## Findings

Final review state: no unresolved findings. The one P2 below was reproduced, repaired by the implementer, and independently re-reviewed. Its initial reproduction is retained as evidence.

### Resolved: Important P2, identical original receipts could not share embedded-supplement coverage after re-extraction

Initial locations: `research_harness/reading.py:90-101`, `research_harness/reading.py:107-115`. The repaired grouping is at `research_harness/reading.py:95-120`.

`covers_embedded_supplement` chooses the supplement link's capture and only accepts required text units whose artifact exactly equals that capture's extraction. A supplement PDF-page link may legitimately retain an earlier receipt while the required main text uses another extraction of the same original PDF. When the extraction text differs, even only in whitespace, the function finds no covering spans and leaves the required supplement incomplete.

This prevents a complete newly extracted body from satisfying the embedded-supplement obligation while preserving the old supplement source link and inspection. The documentation explicitly states that different extraction options produce distinct captures of the same original and that a reading on any capture of that original satisfies its full-text coverage. The failure matters for the OCR repair because the purpose of re-extraction is to produce different text while retaining the original PDF and recorded evidence.

Concrete reproduction, from this runtime directory:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. python3 - <<'PY'
from literature_fixtures import LiteratureCase
from research_harness.literature import import_bundle
from research_harness.reading import record_reading

case = LiteratureCase()
case.setUp()
try:
    work = case.metadata()
    old = case.capture(work, pdf_text=
        'Main result.\fSupplement derivation. References: none.\f')
    new = case.capture(work, pdf_text=
        'Main result.\n\fSupplement derivation.\nReferences: none.\f')
    bundle = case.bundle(work, new)
    bundle['units'].append({
        'id': 'supplement', 'kind': 'supplement', 'required': True,
        'link': {
            'version_id': work, 'source_id': old['source_id'],
            'artifact': old['original'],
            'locator': {'kind': 'pdf', 'page_index': 1,
                        'printed_page': 'S1', 'region': [0, 0, 1, 1]},
        },
    })
    case.mutate(import_bundle, bundle)
    result = case.mutate(record_reading, case.full_note(bundle))['result']
    print(old['original'] == new['original'])
    print(old['text'] != new['text'])
    print(result['status'])
    print([p['code'] for p in result['pending']])
finally:
    case.doCleanups()
PY
```

Observed output before the repair:

```text
True
True
partial
['required_unit_incomplete']
```

Expected: the complete required text on the new capture and the separately inspected supplement page on the identical original can establish completeness.

Suggested repair: group eligible required text units by verified extraction artifact and matching main-original identity. Accept a group only when it covers its own complete extraction. Do not combine offsets from different text artifacts. Keep the supplement's original identity, provenance, and separate inspection requirements. Add regression tests for a supplement that retains a prior same-original receipt and for incompatible extraction groups whose individual coverage remains incomplete.

Resolution verified: the implementation now groups eligible spans by verified extraction SHA, checks the matching main-original SHA, and requires complete coverage within at least one group. It does not mix offsets between extractions. The added `test_embedded_supplement_can_reuse_prior_capture_of_identical_original` passes, and the source-contract suite passes all 41 tests.

An additional independent probe used two same-original extractions, `Alpha. Omega. References: none.\f` and `Alpha! Omega. References: none.\f`, with only `Alpha.` required from the first and only the remaining tail required from the second. Their offsets would cover a whole document if incorrectly combined. The repaired helper correctly returned `partial` with `required_unit_incomplete`, since neither extraction was completely covered on its own.

No critical findings. No unresolved important or minor finding.

## Validation

The following focused suites passed:

- `python3 -m unittest discover -s tests -p 'test_research_ocr.py' -v`: 3 tests.
- `python3 -m unittest discover -s tests -p 'test_research_source_contracts.py' -v`: 40 tests initially; 41 tests passed after the reviewed repair.
- `python3 -m unittest discover -s tests -p 'test_research_fulltext.py' -v`: 9 tests.
- `git diff --check`: passed.

Additional disposable probes confirmed:

- Different original PDF bytes with identical extracted text cannot borrow the main original's complete coverage. The result remained partial with `required_unit_incomplete`.
- Explicit `ocr: true` options are accepted; a simultaneous layout option, a nonboolean OCR option, and an unknown option are rejected.
- A real two-page authored PDF, with text on page one and a blank page two, successfully passed through `extract(..., ocr=True)`. It returned `Authored OCR reference page.\n\f\f`, and extraction measures reported two pages. Reported tools were pdfinfo 26.02.0, pdftoppm 26.02.0, and Tesseract 5.5.0.

The supplied OCR tests exercise output limits, a hanging OCR subprocess, nonzero tool exit, missing tools, blank output, and invalid resource settings. They do not establish a native-tool memory limit. The implementation bounds deadline, page count, raster output dimensions, and captured output, as described in its documentation.

## Declined to judge

- Scientific correctness, OCR recognition accuracy for mathematical claims, and whether recorded inspections reflect actual researcher comprehension: outside this software review, which was expressly not a scientific readiness or manuscript review.
- Publication readiness, repository integration, and production release packaging: this patch is explicitly a local runtime repair and no publication or merge was requested in the review task.

## Assessment

Ready for the intended local runtime contract: yes.

The same-original re-extraction regression is resolved and independently verified. The focused tests and real-tool smoke test support the repaired runtime contract, with original identity, complete per-extraction coverage, separate inspection obligations, and OCR failure boundaries retained.
