from datetime import date

from django.test import SimpleTestCase

from .templatetags.summary_extras import calendar_half_years


class CalendarHalfYearTests(SimpleTestCase):
    def test_six_slots_and_missing_month_counts(self):
        source = [{'month': '2026-11', 'post_count_total': 7}]
        result = calendar_half_years(source, date(2026, 11, 1))
        self.assertEqual(len(result), 1)
        self.assertEqual([row['month'] for row in result[0]['months']],
                         [f'2026-{month:02d}' for month in range(7, 13)])
        self.assertEqual(result[0]['months'][4]['post_count_total'], 7)
        self.assertEqual(result[0]['months'][0]['post_count_total'], 0)
        self.assertEqual(source, [{'month': '2026-11', 'post_count_total': 7}])

    def test_empty_input_uses_target_half(self):
        result = calendar_half_years([], date(2027, 2, 1))
        self.assertEqual(result[0]['label'], '2027年 1〜6月')
        self.assertEqual(len(result[0]['months']), 6)
        self.assertTrue(all(row['approved_count_total'] == 0 for row in result[0]['months']))

    def test_missing_half_and_year_boundary(self):
        result = calendar_half_years([{'month': '2026-06', 'faq_post_count': 3}],
                                     date(2027, 1, 1))
        self.assertEqual([half['key'] for half in result], ['2027-1', '2026-2', '2026-1'])
        self.assertTrue(all(len(half['months']) == 6 for half in result))
        self.assertEqual(result[-1]['months'][-1]['faq_post_count'], 3)
        self.assertTrue(all(row['post_count_total'] == 0 for row in result[1]['months']))