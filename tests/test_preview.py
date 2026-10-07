"""重複待辦「預定」的日子（upcoming_days）單元測試。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

規格決策：
- Google 上同一時間只有「這一期」，下一期在完成後才建立；預定日子只供畫面預覽
- 與 next_due 一致：從這一期的下一天、且不早於今天開始算（逾期的這一期不補出過去的各期）
- 子工作與沒有重複規則的待辦沒有預定日子
"""
import datetime as dt
import unittest

from tests.test_recur import daynote, make_task, parse

D = dt.date
TODAY = D(2026, 10, 7)  # 星期三


def upcoming(task, start, end, today=TODAY):
    return daynote.upcoming_days(task, start, end, today)


class UpcomingDaysTest(unittest.TestCase):

    def test_weekly(self):
        """每週三、這一期是今天 > 之後每個週三，不含這一期"""
        # Given: 今天的每週三任務
        task = make_task(due=TODAY, recur=parse("每週三"))
        # When: 查十月
        days = upcoming(task, D(2026, 10, 1), D(2026, 10, 31))
        # Then: 14、21、28
        self.assertEqual(days, [D(2026, 10, 14), D(2026, 10, 21), D(2026, 10, 28)])

    def test_daily_range(self):
        """每天 > 範圍內每一天（從明天起）"""
        # Given: 每天
        task = make_task(due=TODAY, recur=parse("每天"))
        # When: 查一週
        days = upcoming(task, D(2026, 10, 5), D(2026, 10, 11))
        # Then: 8～11
        self.assertEqual(days, [D(2026, 10, d) for d in (8, 9, 10, 11)])

    def test_weekdays(self):
        """平日 > 跳過週末"""
        # Given: 平日
        task = make_task(due=TODAY, recur=parse("平日"))
        # When: 查到下週一
        days = upcoming(task, TODAY, D(2026, 10, 12))
        # Then: 四、五、下週一
        self.assertEqual(days, [D(2026, 10, 8), D(2026, 10, 9), D(2026, 10, 12)])

    def test_monthly_31_short_month(self):
        """每月31日 > 小月退到月底（與 next_due 一致）"""
        # Given: 每月31日
        task = make_task(due=D(2026, 10, 31), recur=parse("每月31日"))
        # When: 查到明年三月
        days = upcoming(task, TODAY, D(2027, 3, 31))
        # Then: 11/30、12/31、1/31、2/28、3/31
        self.assertEqual(days, [D(2026, 11, 30), D(2026, 12, 31), D(2027, 1, 31), D(2027, 2, 28), D(2027, 3, 31)])

    def test_overdue_starts_today(self):
        """這一期已逾期 > 從今天開始預定，不補出過去的各期"""
        # Given: 10/1 的每天任務還沒做
        task = make_task(due=D(2026, 10, 1), recur=parse("每天"))
        # When: 查十月上旬
        days = upcoming(task, D(2026, 10, 1), D(2026, 10, 9))
        # Then: 7～9
        self.assertEqual(days, [D(2026, 10, 7), D(2026, 10, 8), D(2026, 10, 9)])

    def test_future_period_starts_after_it(self):
        """這一期在未來 > 從這一期的下一天開始，這一期之前沒有預定"""
        # Given: 下週三才是這一期
        task = make_task(due=D(2026, 10, 14), recur=parse("每週三"))
        # When: 查十月
        days = upcoming(task, D(2026, 10, 1), D(2026, 10, 31))
        # Then: 21、28
        self.assertEqual(days, [D(2026, 10, 21), D(2026, 10, 28)])

    def test_not_recurring_or_subtask(self):
        """沒有重複規則、子工作 > 沒有預定日子"""
        cases = {"一般待辦": make_task(due=TODAY), "子工作": make_task(due=TODAY, parent="p1", recur=parse("每天"))}
        for name, task in cases.items():
            with self.subTest(name=name):
                # Given / When: 查十月
                days = upcoming(task, D(2026, 10, 1), D(2026, 10, 31))
                # Then: 無
                self.assertEqual(days, [])

    def test_range_before_today_is_empty(self):
        """查詢範圍都在今天以前 > 沒有預定日子"""
        # Given: 每天
        task = make_task(due=TODAY, recur=parse("每天"))
        # When: 查九月
        days = upcoming(task, D(2026, 9, 1), D(2026, 9, 30))
        # Then: 無
        self.assertEqual(days, [])


if __name__ == "__main__":
    unittest.main()
