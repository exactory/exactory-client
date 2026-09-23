# Citation accounting (0.43.0): test record

Source plan: the v8 harness-improvement design and tasks (T1 to T5) in the
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
5. As the person who supervises a study, I want the manuscript pin to show that
   every fully read or search-cited work is cited or has a stated reason.

## RED and GREEN evidence

| Task | RED commit and cause | GREEN commit | Command |
| --- | --- | --- | --- |
| T1 cache, markup, published version | `b7e43a9`: SystemExit 1 with `author_mismatch` from a title-keyed preprint record; literal `<sub>` titles; `misc` entry although the arXiv record names a DOI; no `--preprint` | `b89360f` | `python3 -m unittest tests.test_check` |
| MathML titles | test in `b557e6b`: words glued (`Films ofBiGrown`), scripts lost | `b557e6b` | `python3 -m unittest tests.test_check` |
| T2 manuscript checks | `9e420b6`: `KeyError: 'manuscript'`; an uncited entry did not block; the gate passed after a LaTeX change | `4f7ef06` | `python3 -m unittest tests.test_check` |
| T3 accounting at the pin | `e2bb414`: `ModuleNotFoundError: research_harness.citations` | `4f7ef06` | `python3 -m unittest discover -s tests -p test_research_citation_accounting.py` |
| First review (exactory-check, 2 Major, 13 Minor) | `82d1f3e`: 19 failures and 15 errors in `tests.test_check` | `a34a247` | `python3 -m unittest tests.test_check` |
| Second review (citation evidence, 1 Major, 10 Minor) | `c1a7104`: `ImportError` for `read_bibliography`, `find_citing_entry`, `collect_accountable_works` | `4aa8001` | `python3 -m unittest discover -s tests -p test_research_citation_accounting.py` |
| Fix review (2 Major, 10 Minor) | `22549e0`: 11 failures in `tests.test_check`, 11 in `test_research_citation_accounting` | `3bd27e2` | both commands above |
| Docstring escape (found in the suite output) | the strict compile helper fails on `3bd27e2` with `SyntaxError: invalid escape sequence \d` | `460c313` | `python3 -m unittest discover -s tests -p test_manifest.py` |

## Guarantees

| # | Guarantee | Test |
| --- | --- | --- |
| 1 | A DOI entry is compared with its DOI record even when the cache holds a preprint record under the same title | `test_check.TestCacheIdentity` |
| 2 | Records are cached only under the primary identity, in cache schema version 2; an older cache is ignored | `test_check.TestCacheIdentity` |
| 3 | `<sub>`, `<sup>`, `<i>`, `<b>`, `<scp>` and inline MathML (`msub`, `msup`) render as LaTeX with one math group at the outermost level; crossed tags and MathML that does not parse keep their text | `test_check.TestRegistryMarkup` |
| 4 | Only registry tag names are markup: a fabricated suffix in angle brackets is a `title_mismatch`, and inequalities stay text | `test_check.TestRegistryMarkup` |
| 5 | `add --arxiv-id` accepts a published version only as a journal, proceedings or chapter record by the same first and second authors; each DOI in the arXiv record is checked; a Crossref match also needs a year no earlier than the preprint's minus one | `test_check.TestVersionOfRecord` |
| 6 | `add` names an unreachable Crossref, prints a notice for `--preprint`, and refuses a work whose DOI or arXiv id an entry already has, whatever its key | `test_check.TestVersionOfRecord` |
| 7 | The main file is `--main`, else `paper.tex`, else the only root file; the manuscript is the main file and the files it includes; several roots without `--main` exit 2 | `test_check.TestManuscriptChecks` |
| 8 | Every citation form counts (`\cites` with notes, `\cite<...>`, `\Citet`, URLs with `%`); `\nocite`, comments after an even number of backslashes, `\iffalse` blocks and comment environments do not | `test_check.TestManuscriptChecks` |
| 9 | Prior-art sentences without a citation are warnings with file and line; the abstract is excluded | `test_check.TestManuscriptChecks` |
| 10 | `counts` stay per reference; the top-level `blocking` count adds the uncited entries | `test_check.TestManuscriptChecks` |
| 11 | The gate treats a report as stale when a manuscript file changes or an included file appears, and when the report has no manuscript object; an unrelated LaTeX file does not matter | `test_check.TestGateSubcommand` |
| 12 | Accounted works are full-text readings and the citations of selected research searches, with their reasons | `test_research_citation_accounting.AccountableWorkTests` |
| 13 | Evidence is a whole identifier or the whole entry title; a DOI prefix, a longer arXiv id, a title inside a longer title and `@comment` text are not evidence; the key is the first citing entry in file order | `test_research_citation_accounting.CitationEvidenceTests` |
| 14 | Incomplete and invalid accountings are refused; each refused item names its index and cause | `test_research_citation_accounting.AccountCitationsTests` |
| 15 | The pin refuses unaccounted works and stores the accounting in the bundle | `test_research_citation_accounting.ManuscriptPinTests` |
| 16 | The lineage gate reads the same evidence as the accounting | `test_research_publication` (lineage and token tests) |
| 17 | A published entry that `add --arxiv-id` writes keeps the arXiv id in `eprint`; `--preprint` after the published entry is refused | `test_check.TestVersionOfRecord` |
| 18 | Isotope prescripts render as `$^{208}$Pb`; `<tt>` renders and `<ovl>` keeps its text; a tag with a title or data attribute is text; a script inside text inside a script opens its own math group | `test_check.TestRegistryMarkup` |
| 19 | Included files resolve from the main file's directory; `\iffalse` branches up to `\else`, nested conditionals, `\verb`, `\nolinkurl`, bracketed notes with braces and brace-free `\input` are read as TeX reads them | `test_check.TestManuscriptChecks` |
| 20 | Titles compare after removing accents, naming Greek letters and dropping braces and math markers; a CJK title counts; text between entries and quoted titles with braces are read as BibTeX; DOI continuations are not evidence and PDF URLs are | `test_research_citation_accounting.CitationEvidenceTests` |

## Real-data checks

- `add --arxiv-id` against the live registries: 2503.04621 and 2510.14048 (no DOI
  in the arXiv record) resolved to Science and Physical Review B records through
  Crossref; 2112.09662 resolved through the arXiv record; 2512.06307 stayed a
  preprint.
- MathML titles from Crossref: 10.1103/PhysRevLett.113.157401 and
  10.1103/PhysRevLett.115.217602 render `Bi$_{2}$Se$_{3}$` and
  `Cu$_{0.02}$Bi$_{2}$Se$_{3}$` and verify in `lookup`.
- Dry accounting on a real study store (read-only snapshot): 32 accounted
  works, all read in full; the selected five-purpose searches cite 17 of them.
  The bibliography of the deposited paper left 16 works neither cited nor
  explained. After the citation revision, 8 remain. One work counted as cited
  only through an entry that the LaTeX never cites; `lookup` now blocks such
  an entry.

## Full suite

- `python3 -m unittest discover -s tests` on `460c313` with local Python 3.9.6:
  1,249 tests OK in 3,034 s, no warnings in the output.
- The GitHub Actions matrix (Python 3.9, 3.12 and 3.14) runs on the pull
  request.
