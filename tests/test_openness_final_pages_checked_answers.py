"""The answers of a v7 final in pages are checked before anything builds on them (merge of 0.91.14 into 0.92.0).

0.91.11 checks the answer of a v7 turn again before the next turn, the verification of the chain or a finding reads
it, also when the store holds no refusal record for it. These tests drive the page and the closing turn of a final in
pages to an answer that fails its check, with the refusal record lost, and the closing composite to a refusal record
that another writer meets at each attempt. Synthetic scientific content."""

from research_harness import openness_reading_v7 as v7, problem_openness, review_continuation
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from finishing_write_fixtures import OtherWriters
from test_openness_final_pages import FinalPagesSupport
from test_openness_reading_v7_unchecked_answer import assert_unchecked, lose_refusal_record, run_recorded_request

CLOSING_FIELDS = ('statement', 'assumptions', 'scope', 'dependencies', 'policy')


class ClosingReached(Exception):
    """Ends finish_pages at the closing turn, before its answer."""


def stop_at_closing(current, packet):
    if 'closing' in packet.get('reading_final', {}):
        raise ClosingReached(current)


def build_contradicting_closing():
    """A closing answer that passes the check of its page and claims an outcome that the pages contradict."""
    return dict({key: 'Assessed.' for key in CLOSING_FIELDS}, outcome='supported_open',
                reason='Claimed open despite a page ground.', grounds=[])


def reach_closing(case, native):
    """Drive the chain of native through its readings and pages, and return the closing turn, which has no answer."""
    with case.assertRaises(ClosingReached) as reached:
        case.finish_pages(native, before_answer=stop_at_closing)
    return reached.exception.args[0]


class CheckedPageAnswerTests(FinalPagesSupport):

    def test_page_answer_that_failed_its_check_is_resumed_and_no_turn_follows_it(self):
        native = self.export_targets()
        page, packet = self.start_final(native)
        self.assertNotIn('closing', packet['reading_final'])
        refused = dict(targets=[dict(formulation=handle, coverage='c' * 801, sentence='Too long.', grounds=[])
                                for handle in packet['reading_final']['page']['targets']])
        with lose_refusal_record():
            receipt = self.invoke(page, refused)
        self.assertNotIn('output_refusal', receipt)
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('review_output_refusal', {}), {})
        plan = plan_next(records, self.artifacts, page)
        self.assertEqual((plan['state'], plan['command'], plan['request_id']),
                         ('resume', 'review-run', 'invoke-attempt-' + page), plan)
        self.assertEqual(plan['details']['message'], review_continuation.UNRECORDED_REFUSAL_MESSAGE)
        with self.assertRaises(ResearchError) as raised:
            self.assign('after-page', native, prior=page)
        assert_unchecked(self, raised.exception, 'review_source_continuation_unverified',
                         (v7, 'UNCHECKED_HEAD_MESSAGE'), page, 'attempt-' + page,
                         refusal_code=plan['details']['refusal']['code'], check=plan['details']['refusal']['check'])
        receipt = run_recorded_request(self, plan['payload'], plan['request_id'])
        self.assertEqual(receipt['output_refusal']['check'], plan['details']['refusal']['check'])

    def test_closing_answer_that_failed_its_check_gives_no_finding(self):
        native = self.export_targets()
        closing = reach_closing(self, native)
        with lose_refusal_record():
            receipt = self.invoke(closing, build_contradicting_closing())
        self.assertNotIn('output_refusal', receipt)
        records = self.store.snapshot()['records']
        self.assertEqual(records.get('review_output_refusal', {}), {})
        plan = plan_next(records, self.artifacts, closing)
        self.assertEqual((plan['state'], plan['command']), ('resume', 'review-run'), plan)
        revision = self.store.revision
        with self.assertRaises(ResearchError) as raised:
            self.mutate(self.load_problem_module().record_openness, dict(assignment_id=closing))
        assert_unchecked(self, raised.exception, 'review_context_unverified',
                         (problem_openness, 'UNCHECKED_FINDING_MESSAGE'), closing, 'attempt-' + closing,
                         refusal_code=plan['details']['refusal']['code'], check=plan['details']['refusal']['check'])
        self.assertEqual(self.store.revision, revision)


class ClosingRefusalWriteTests(OtherWriters, FinalPagesSupport):
    def test_refusal_of_a_closing_composite_is_recorded_when_a_commit_meets_every_attempt(self):
        """The check of a closing answer builds its composite; the refusal record commits against the records that
        the check read, so an unrelated commit before each attempt does not refuse it."""
        native = self.export_targets()
        closing = reach_closing(self, native)
        self.start_other_writers()
        self.meet_commits(('review.refusal',))
        receipt = self.invoke(closing, build_contradicting_closing())
        self.assertEqual(self.attempts, {'review.refusal': 1})
        refusal = self.store.snapshot()['records']['review_output_refusal']['attempt-' + closing]
        self.assertEqual(refusal['check'], 'openness.finding')
        self.assertEqual(receipt['output_refusal'], {key: refusal[key] for key in ('code', 'check', 'details')})
