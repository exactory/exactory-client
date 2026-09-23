# Citation accounting (0.43.0): test record

Source plan: the v8 harness-improvement design and tasks (T1 to T3) in the
exactory repository, `misc/harness-improvement/v8/`. The journeys below come
from that design.

## Journeys

1. As an author, I want `add --arxiv-id` to cite the published version, so that
   my bibliography cites the version of record.
2. As an author, I want `lookup` to compare a published-version entry with its
   own registry record, so that replacing a preprint entry does not fail.
3. As an author, I want registry titles with markup to render as LaTeX, so that
   the compiled bibliography shows the formula, not HTML tags.
4. As an author, I want `lookup` to block bibliography entries that the
   manuscript never cites and to list prior-art sentences without a citation.
5. As a reviewer of a study, I want the manuscript pin to show that every fully
   read or search-cited work is cited or has a stated reason.

## RED and GREEN evidence

| Task | RED commit and cause | GREEN commit | Command |
| --- | --- | --- | --- |
| T1 cache, markup, published version | `b7e43a9`: SystemExit 1 with `author_mismatch` from a title-keyed preprint record; literal `<sub>` titles; `misc` entry although the arXiv record names a DOI; no `--preprint` | `b89360f`: 41 tests OK | `python3 -m unittest tests.test_check` |
| T2 manuscript checks | `9e420b6`: `KeyError: 'manuscript'`; an uncited entry did not block; the gate passed after a LaTeX change | see the T2 and T3 commit | `python3 -m unittest tests.test_check` (48 tests OK) |
| T3 accounting at the pin | `e2bb414`: `ModuleNotFoundError: research_harness.citations` | see the T2 and T3 commit | `python3 -m unittest discover -s tests -p test_research_citation_accounting.py` (9 tests OK) |

## Guarantees

| # | Guarantee | Test |
| --- | --- | --- |
| 1 | A DOI entry is compared with its DOI record even when the cache holds a preprint record under the same title | `test_check.TestCacheIdentity` |
| 2 | Records are cached only under the primary identity | `test_check.TestCacheIdentity` |
| 3 | `<sub>`, `<sup>`, `<i>`, `<b>`, `<scp>` and inline MathML (`msub`, `msup`) render as LaTeX; other tags are dropped; MathML that does not parse keeps its text; tagged and LaTeX titles match | `test_check.TestRegistryMarkup` |
| 4 | `add --arxiv-id` cites the DOI in the arXiv record, or a unique Crossref title and first-author match; a second match, another first author or no match keeps the preprint; `--preprint` keeps it | `test_check.TestVersionOfRecord` |
| 5 | An entry that no citation command cites is blocking; commented citations do not count; `\nocite{*}` counts all; bracketed options and starred forms count | `test_check.TestManuscriptChecks` |
| 6 | Prior-art sentences without a citation are warnings with file and line; the abstract is excluded | `test_check.TestManuscriptChecks` |
| 7 | The gate treats a report as stale when a LaTeX file changes or is added, or when the report has no manuscript object | `test_check.TestGateSubcommand` |
| 8 | Accounted works are full-text readings and the citations of selected research searches, with their reasons | `test_research_citation_accounting.AccountableWorkTests` |
| 9 | Bibliography evidence, declared keys and reasons account for every work; non-BibTeX bibliographies cite without a key | `test_research_citation_accounting.AccountCitationsTests` |
| 10 | Incomplete and invalid accountings are refused with their codes | `test_research_citation_accounting.AccountCitationsTests` |
| 11 | The pin refuses unaccounted works and stores the accounting in the bundle | `test_research_citation_accounting.ManuscriptPinTests` |
| 12 | The lineage gate reads the same citation evidence as before | `test_research_publication` (token tests and lineage tests) |

## Real-data checks

- `add --arxiv-id` against the live registries: 2503.04621 and 2510.14048 (no DOI
  in the arXiv record) resolved to Science and Physical Review B records through
  Crossref; 2112.09662 resolved through the arXiv record; 2512.06307 stayed a
  preprint.
- MathML titles from Crossref: 10.1103/PhysRevLett.113.157401 and
  10.1103/PhysRevLett.115.217602 render `Bi$_{2}$Se$_{3}$` and
  `Cu$_{0.02}$Bi$_{2}$Se$_{3}$` and verify in `lookup`.
- Dry accounting on a real study store (read-only snapshot): 32 accounted
  works, all read in full; the selected five-purpose searches cite 17 of
  them. The
  bibliography of the deposited paper left 16 works neither cited nor
  explained. After the citation revision, 8 remain. One work counted as cited
  only through an entry that the LaTeX never cites; `lookup` now blocks such
  an entry.

## Full suite

Recorded in the pull request after the candidate run.
