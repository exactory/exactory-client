# Source provenance repair

This local runtime starts from Exactory commit
`87570b433a0c75356dc76fdc3048118ae3547c0e` (version 0.42.2).
The study retains its original sources, unit identities, required flags,
inspection records, and managed history.

## Embedded supplements

A supplement can be a section of the main original rather than a separate
file. The completeness check will recognize that representation when required
text units collectively cover the complete extracted main original. Each
supplement must still have complete acquisition provenance and its own actual
inspection. Required units with missing links, uncovered text, missing visual
assets, and incomplete standalone supplements remain pending.

The regression suite will exercise section spans, PDF pages, whitespace gaps,
uncovered substantive text, optional coverage, separate originals, and omitted
inspections before the repair is used in the study.

## Scanned originals

An explicit OCR extraction mode will preserve the acquired PDF bytes and record
the actual extraction tools, versions, options, and page boundaries. It will
bound runtime, page count, image dimensions, and output size. Ordinary PDF text
extraction remains the default. OCR is a derivative for navigation and reading;
mathematical claims must be checked against the original page images.

## Validation and use

The unmodified source-contract suite passed all 36 tests. Test results and the
runtime patch will be retained with this repair. The managed CLI will remain
responsible for all state transitions, evidence validation, and resource
accounting. This local repair is not an official plugin release.
