"""D2 admission boundaries retain the existing serialized lifecycle contract."""

from contextlib import ExitStack
import copy
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch

from research_harness import review_task_execution as execution, review_worker as worker
from research_harness.evidence import digest
from research_harness.review_budget import check_admission
from research_harness.storage import Store
from test_review_task_execution import TaskExecutionFixture


class CallAdmissionBoundaryTests(TaskExecutionFixture):
    def setUp(self):
        super().setUp()
        self.clock = [1000.0]
        self.owner = dict(pid=10, pgid=10, started='owner-start')
        self.child = dict(pid=20, pgid=20, started='child-start')
        self.processes = {10: self.owner, 20: self.child}
        clock = patch.object(execution.time, 'time', side_effect=lambda: self.clock[0])
        identities = patch.object(worker, '_identity', side_effect=lambda pid=None: self.processes[20 if pid is None else pid])
        self.addCleanup(clock.stop)
        self.addCleanup(identities.stop)
        clock.start()
        identities.start()

    def read_admission(self):
        from research_harness import review_call_admission
        return review_call_admission

    def admit(self, operation='review.invoke', identifier='next', route_id='route'):
        admission = self.read_admission()
        return execution._mutate_current(self.store, 'fixture.admit', {},
            lambda tx, value, revision: admission.admit_review_call(tx, operation, identifier, route_id),
            request_id='admit-' + identifier)

    def test_budget_precedes_capacity_and_refusal_rolls_back_every_write(self):
        self.reserve()
        self.put('review_budget', 'store', dict(id='store', max_calls=1, reason='One call'))
        before = self.store.snapshot()
        self.refuse('review_budget_exhausted', self.admit)
        self.assertEqual(self.store.snapshot(), before)
        self.put('review_budget', 'store', dict(id='store', max_calls=2, reason='Two calls'))
        before = self.store.snapshot()
        self.refuse('review_capacity_wait', self.admit)
        self.assertEqual(self.store.snapshot(), before)

    def test_uncovered_route_uses_the_budget_without_taking_local_capacity(self):
        self.reserve()
        self.put('review_route', 'remote', dict(id='remote', adapter='openai_responses_v1'))
        self.admit(route_id='remote')
        self.put('review_budget', 'store', dict(id='store', max_calls=1, reason='One call'))
        self.refuse('review_budget_exhausted', lambda: self.admit(identifier='remote-next', route_id='remote'))

    def test_claim_and_worker_start_keep_dependency_and_launch_checks(self):
        admission = self.read_admission()
        reservation = self.reserve()['result']
        records = self.store.snapshot()['records']
        admission.check_launch(records, 'review.worker_claim', reservation)
        claimed = dict(reservation, launch=dict(authorized=False))
        self.refuse('review_invocation_pending', lambda: admission.check_launch(
            records, 'review.worker_claim', claimed))
        self.refuse('review_worker_reconciliation_required', lambda: admission.check_launch(
            records, 'review.worker_running', claimed))
        authorized = dict(claimed, launch=dict(authorized=True))
        admission.check_launch(records, 'review.worker_running', authorized)
        self.put('context', 'slot', dict(version=2))
        records = self.store.snapshot()['records']
        for transition, row in (('review.worker_claim', reservation), ('review.worker_running', authorized)):
            with self.subTest(transition=transition):
                self.refuse('review_task_dependency_changed', lambda: admission.check_launch(records, transition, row))

    def test_child_registration_checks_release_token_state_and_process_identity(self):
        admission = self.read_admission()
        inline = dict(id='call', released=False, token='secret', owner=self.owner, children=[])
        staged = dict(id='task', released=False, token='secret', worker=self.owner, state='running', children=[])
        for transition, row in (('review.call_child', inline), ('review.worker_child', staged)):
            with self.subTest(transition=transition):
                def check(value, token='secret'):
                    return admission.check_launch({}, transition, value, token=token,
                                                  read_identity=lambda pid: self.processes[pid])
                check(row)
                self.refuse('review_worker_reconciliation_required', lambda: check(dict(row, released=True)))
                self.refuse('review_worker_reconciliation_required', lambda: check(row, token='other'))
                if transition == 'review.worker_child':
                    self.refuse('review_worker_reconciliation_required', lambda: check(dict(row, state='reserved')))
                self.processes[10] = dict(self.owner, started='reused-pid')
                self.refuse('review_worker_reconciliation_required', lambda: check(row))
                self.processes[10] = self.owner

    def test_new_boundaries_match_frozen_record_writers_and_replay(self):
        self.read_admission()
        self.put('review_budget', 'store', dict(id='store', max_calls=3, reason='Compare admitted calls'))
        frozen_root = self.root.parent / 'frozen'
        shutil.copytree(self.root, frozen_root)
        frozen_store = Store(frozen_root)
        namespace = dict(vars(execution), **{key: value for key, value in vars(worker).items()
                                             if key not in ('__name__', '__package__')})
        namespace['check_admission'] = check_admission
        source = Path(__file__).with_name('fixtures') / 'review_call_admission_before_d2.py.txt'
        exec(compile(source.read_text(), str(source), 'exec'), namespace)
        authority = SimpleNamespace(reservation_id=execution.identify_call('review.invoke', 'ordinary'),
                                    token='call-token', identity=self.owner)
        invocation = dict(id='ordinary', route_id='route', assignment_id='assignment', request=self.request)
        with ExitStack() as stack:
            stack.enter_context(patch.object(execution, 'runtime_provenance', return_value=dict(version='0.85.3')))
            stack.enter_context(patch.object(execution.uuid, 'uuid4', return_value=SimpleNamespace(hex='reservation-token')))
            for store, start, reserve, claim, register in (
                    (frozen_store, namespace['start_call'], namespace['reserve_task'],
                     namespace['_claim_launch'], namespace['_register_child']),
                    (self.store, execution.start_call, execution.reserve_task, worker._claim_launch, worker._register_child)):
                def apply(tx, value, revision):
                    start(tx, 'review.invoke', invocation, authority, revision)
                    tx.put('review_invocation', invocation['id'], invocation)
                    return invocation
                started = execution._mutate_current(store, 'review.invoke', invocation, apply, request_id='ordinary-start')
                self.assertEqual(execution._mutate_current(store, 'review.invoke', invocation, apply,
                                                          request_id='ordinary-start'), started)
                with worker._owner(store.root, authority.reservation_id, worker=True) as descriptor:
                    register(store.root, authority.reservation_id, authority.token, descriptor, call=True)
                membership = store.snapshot()['records']['review_pool_membership'][authority.reservation_id]
                termination = dict(reservation_id=authority.reservation_id, owner=self.owner,
                                   children=[self.child], confirmed=True)
                self.assertEqual(membership['children'], [self.child])
                execution._mutate_current(store, 'fixture.release', {},
                    lambda tx, value, revision: execution.release_call(tx, authority.reservation_id, termination, revision),
                    request_id='release')
                first = reserve(store, self.payload(), request_id='reserve-a')
                self.assertEqual(reserve(store, self.payload(), request_id='reserve-a'), first)
                identifier = first['result']['id']
                claimed = claim(store, identifier)
                self.assertEqual(claim(store, identifier), claimed)
                worker._bind_worker(store, identifier, 'reservation-token', self.owner)
                worker._authorize_launch(store, identifier, self.owner)
                authority_for_task = SimpleNamespace(reservation_id=identifier, verify=lambda store, row: None)
                start_worker = namespace['_start_worker'] if store is frozen_store else worker._start_worker
                start_worker(store, authority_for_task)
                with worker._owner(store.root, identifier, worker=True) as descriptor:
                    register(store.root, identifier, 'reservation-token', descriptor)
        before = frozen_store.snapshot()['records']
        after = self.store.snapshot()['records']
        self.assertEqual(after, before)
        self.assertEqual(digest(after), digest(before))
        self.assertNotIn('review_call_wait', after)
        self.assertNotIn('review_stop', after)
        # Stored calls and requests remain verifiable without rewriting their evidence.
        stored = copy.deepcopy(after)
        self.refuse('review_capacity_wait', lambda: self.reserve('b'))
        self.assertEqual(self.store.snapshot()['records'], stored)
