"""Historical progress keeps the inventory known when its turn was assigned."""

import unittest

from research_harness import report_views, review_parts as parts


class PartsStatusLateUnitsTests(unittest.TestCase):
    def describe_earlier_turn(self, delivered_handles):
        assignment = dict(
            id='first', recorded_revision=1,
            review_parts=dict(protocol=parts.PROTOCOL, adapter='problem_result',
                              series_assignment_id='first', kind='read', turn=1),
            review_parts_plan=dict(
                unit_allowance=100, card_allowance=100, remaining_turns={},
                units_carded=0, units_remaining=1))
        inventory = dict(units=[
            dict(handle='u1', item_characters=10, bodies=[]),
            dict(handle='u2', item_characters=20, bodies=[])])
        packet = {parts.KEY: dict(
            protocol=parts.PROTOCOL, kind='read', turn=2,
            originals=[dict(unit=handle, item=True, start=0,
                            end=10 if handle == 'u1' else 20)
                       for handle in delivered_handles])}
        series = dict(
            packets=[packet], delivery=dict(inventory=inventory), complete=False,
            issues=dict(count=1, examples=[dict(code='final_pending')]))
        return report_views.describe_parts_turn(assignment, dict(ready=False), series)

    def test_late_units_do_not_make_an_earlier_turn_have_negative_remaining_units(self):
        progress = self.describe_earlier_turn(('u1', 'u2'))
        self.assertEqual(progress['units_carded'], 1)
        self.assertEqual(progress['units_remaining'], 0)

    def test_a_late_unit_cannot_take_credit_for_an_undelivered_earlier_unit(self):
        progress = self.describe_earlier_turn(('u2',))
        self.assertEqual(progress['units_carded'], 0)
        self.assertEqual(progress['units_remaining'], 1)


if __name__ == '__main__':
    unittest.main()
