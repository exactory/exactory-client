# Full-text capture and assigned sessions (0.61.0): test record

The source records are `misc/harness-improvement/v13/` (tasks T0, T1 and T14)
and `misc/harness-improvement/v16/` in the exactory repository. The first
records full-text failures in study 2601.01283 and an assigned session that
cannot stop inside an autopilot study. The second records the request budget
of a publisher link with three redirects.

The implementation record is
`misc/harness-improvement/20261024163824-open-records-v13-v15-v16-v17/`.
Sections T2 and T3 of its `TASKS.md` fix the release scope and record the
implementation and review decisions. Every log path below is relative to that
record. The release branch is based on 0.60.1 (`8f98d2a`). The recorded RED
runs used the earlier lane baselines identified below.

## Journeys

1. As an author, I want a PDF served as `binary/octet-stream` to extract with
   the same signature check as `application/octet-stream`. Its reported media
   type and original bytes stay recorded.
2. As a reviewer, I want the projection to count what request construction
   sent for each media-type spelling. Earlier reviews still authenticate.
3. As an author, I want a PubMed Central (PMC) page to extract its article body,
   section headings and references, with references excluded from body content.
4. As a reviewer with one assignment, I want my session to stop inside an
   autopilot study without reading or changing its counter. An unmarked session
   still continues the study under the existing conditions and cap.
5. As an author, I want the full-text reference to explain the request budget
   for redirects and retries, the retained pending capture and the retry request.

## RED and GREEN evidence

RED identifies a failure before a repair. GREEN identifies a pass after it.
Failure counts include failing subtests. Test modules run from the worktree
root with `EXACTORY_API_KEY=` and `PYTHONPATH=tests:.`. The branch checks use
`/opt/homebrew/bin/python3.12` and `/usr/bin/python3`. The branch column records
OK on both interpreters at `675ef4e` with the release-file updates.

### PDFs served as generic binary (v13 task T0)

Every method below failed against `4127fc5` in
`logs/lane-f/red-final-fixture-baseline.log` and passed in
`logs/lane-f/green.log`. The RED run included the initial PMC method as well:
10 methods, 23 failures and no errors. The GREEN run passed all 10 methods.

The methods cover extraction, acquisition, stored page maps, native PDF
attachments, text delivery, frozen source evidence and source components.
Their modules are `test_research_fulltext`, `test_research_binary_pdf`,
`test_review_generic_binary_pdf`, `test_review_input_projection`,
`test_review_frozen_pdf_capture` and `test_research_public_source_delivery`.

| Test | RED commit | Branch |
| --- | --- | --- |
| `test_binary_octet_stream_requires_a_pdf_signature_for_extraction` | `4127fc5` | OK |
| `test_binary_octet_stream_acquisition_preserves_reported_type_and_original` | `4127fc5` | OK |
| `test_stored_binary_octet_stream_pdf_uses_only_a_successful_captured_page_map` | `4127fc5` | OK |
| `test_binary_octet_stream_pdf_is_withheld_on_a_text_only_route` | `4127fc5` | OK |
| `test_binary_octet_stream_pdf_is_attached_as_pdf_without_relabeling_its_descriptor` | `4127fc5` | OK |
| `test_binary_octet_stream_pdf_requires_exact_native_attachment_bytes` | `4127fc5` | OK |
| `test_binary_octet_stream_text_requires_matching_descriptor_and_non_pdf_bytes` | `4127fc5` | OK |
| `test_stored_generic_binary_pdf_without_frozen_inference_is_not_a_locator_refusal` | `4127fc5` | OK |
| `test_stored_binary_octet_stream_pdf_delivers_its_exact_source_jpeg_component` | `4127fc5` | OK |

`logs/lane-f/t0-local-check.log` records the extraction of the stored
5,833,899-byte S3 PDF from study 2601.01283. It reports `extracted`, 9 pages
and `pdf_signature_from_generic_binary`. The reported media type is
`binary/octet-stream`.

### Review delivery and media-type spellings

The 11 methods below failed against `f514cb3` in
`logs/lane-f/review-2-red.log`, with 63 failures and no errors. All 11 passed
in `logs/lane-f/review-2-green.log`. The baseline is recorded in
`logs/lane-f/review-2-state.json`.

These methods use the same six modules as the generic binary tests. They
exercise type case and parameters, exact artifact descriptors, construction
and projection, stored page maps and visual inputs.

| Test | RED commit | Branch |
| --- | --- | --- |
| `test_generic_binary_media_spellings_detect_only_pdf_signatures` | `f514cb3` | OK |
| `test_generic_binary_media_spellings_extract_pdfs_and_keep_non_pdf_reports` | `f514cb3` | OK |
| `test_declared_pdf_media_spellings_extract_without_rewriting_the_reported_type` | `f514cb3` | OK |
| `test_generic_binary_media_spellings_agree_from_construction_through_projection` | `f514cb3` | OK |
| `test_declared_pdf_media_spellings_agree_from_construction_through_projection` | `f514cb3` | OK |
| `test_image_media_spellings_agree_from_construction_through_projection` | `f514cb3` | OK |
| `test_native_attachment_media_spellings_match_without_relabeling_the_descriptor` | `f514cb3` | OK |
| `test_visual_png_media_spellings_preserve_exact_images_and_descriptors` | `f514cb3` | OK |
| `test_stored_pdf_media_spellings_use_the_same_captured_page_map` | `f514cb3` | OK |
| `test_generic_binary_media_spellings_do_not_invent_a_frozen_pdf_page_map` | `f514cb3` | OK |
| `test_pdf_media_spellings_deliver_source_components_without_relabeling_captures` | `f514cb3` | OK |

### The article body of a PubMed Central page (v13 task T1)

The initial method was named
`test_pmc_main_article_body_extracts_sections_without_the_nested_reference_list`.
It failed against `4127fc5` in `logs/lane-f/red-final-fixture-baseline.log`.
The revised method retains references in the text and excludes them from body
content. It also checks a page with only references under both
`main-article-body` and `article-body`.

The revised assertions failed against the initial, uncommitted implementation
on `4127fc5` in `logs/lane-f/review-1-red.log`: one method and three failures.
The method passed in `logs/lane-f/review-1-green.log`. Both names identify the
same test in `test_research_fulltext`, with revised assertions in the release.

| Test | Initial RED commit | Branch |
| --- | --- | --- |
| `test_pmc_main_article_body_retains_references_without_counting_them_as_body` | `4127fc5` | OK |

`logs/lane-f/review-1-t1-local-check.log` records extraction of the stored PMC
page from study 2601.01283. It retains "2. Results and Discussion" and
"3. Conclusion", with all 40 references in the text and none in body content.

### Sessions started for one assignment (v13 task T14)

`logs/lane-r/red.log` records these three methods against `4127fc5`.
The two marked-session methods failed in eight subtests. The unmarked-session
method already passed and serves as the regression control. All three passed
in `logs/lane-r/green.log`. They are in `test_stop_cap` and run both the
Claude Code hook and the Codex adapter from a study subdirectory.

| Test | Baseline `4127fc5` | Branch |
| --- | --- | --- |
| `test_assigned_session_preserves_counter` | RED | OK |
| `test_assigned_session_leaves_counter_absent` | RED | OK |
| `test_unassigned_session_advances_counter` | OK | OK |

The existing `test_hooks.TestContinueAutopilot` checks also run independently
of the runner's marker and cap. With the marker exported, three existing
checks failed before the environment repair in `logs/lane-r/review-1-red.log`.
All 53 hook and cap tests passed in `logs/lane-r/review-1-green.log`.
`logs/lane-r/review-1-payload-red.log` and
`logs/lane-r/review-1-payload-green.log` record the separate payload-consumption
check on both hosts.

With `EXACTORY_AUTOPILOT_MAX=100` and `EXACTORY_ASSIGNED_SESSION=review`, the
existing `test_the_safety_cap_requests_a_summary` failed on `ec98003` in
`logs/lane-r/review-2-red.log`. After the helper removed both inherited
variables, all 53 tests passed in `logs/lane-r/review-2-green.log`.
`test_stop_cap` sets its own cap and marker values explicitly.

### The request budget of a full-text capture (v16)

This change adds documentation and no test method. The source record describes
a request with `max_requests: 2` that exhausted its budget during a chain that
needed four attempts. The documentation states that each redirect and retry
uses an attempt. A pending capture stays recorded, and a new request id with a
larger budget tries again.

`logs/lane-r/texts-3.12.log` and `logs/lane-r/texts-3.9.log` record 66 passing
tests on each interpreter after the documentation was placed. The modules are
`test_research_guidance`, `test_user_authorized_publication`,
`test_research_problem_tools` and `test_codex`.

| Test module | RED | Branch |
| --- | --- | --- |
| `test_research_guidance` | No new method. | OK |
| `test_user_authorized_publication` | No new method. | OK |
| `test_research_problem_tools` | No new method. | OK |
| `test_codex` | No new method. | OK |

The release-file updates change the current-version pins in `test_codex`,
`test_manifest` and `test_research_guidance` to 0.61.0. The guidance check also
requires `docs/releases/0.61.0.md`.
