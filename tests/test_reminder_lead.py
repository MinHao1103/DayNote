"""提前提醒（⏰ 15:00 提前10分鐘）單元測試：格式讀寫、快速輸入辨識、跳提醒的時間。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

規格決策：
- 提前多久寫在 ⏰ 那一行（⏰ 15:00 提前10分鐘），手機上看得懂；舊格式（⏰ 15:00）＝準時
- 已提醒記錄仍以原本的時間為鍵：提前跳過了，準時就不再跳一次
- 快速輸入「提前10分」「提前十分鐘」「提前半小時」「提前1小時」；沒有時間時留在標題
"""
import datetime as dt
import unittest

from tests.test_recur import daynote

D = dt.date
T = dt.time
TODAY = D(2026, 10, 6)
NOW = dt.datetime(2026, 10, 6, 14, 0)


def quick(text):
    return daynote.parse_quick(text, [], TODAY, NOW)


class LeadMetaTest(unittest.TestCase):
    """提前提醒寫進／讀出詳細資訊。"""

    def test_round_trip(self):
        """15:00 提前 10 分鐘 > 寫成「⏰ 15:00 提前10分鐘」，讀回時間、提前分鐘數與內文"""
        # Given / When: 寫入
        notes = daynote.join_meta(T(15, 0), None, "帶簡報", 10)
        # Then: 格式與讀回
        self.assertEqual(notes, "⏰ 15:00 提前10分鐘\n帶簡報")
        self.assertEqual(daynote.split_meta(notes), (T(15, 0), None, "帶簡報"))
        self.assertEqual(daynote.reminder_lead(notes), 10)

    def test_old_format_is_on_time(self):
        """舊格式「⏰ 15:00」、沒有時間、時間在重複規則後面 > 分別是準時、None、照讀"""
        # Given / When / Then: 三種寫法
        self.assertIsNone(daynote.reminder_lead("⏰ 15:00\n內文"))
        self.assertIsNone(daynote.reminder_lead("只有內文"))
        self.assertEqual(daynote.reminder_lead("🔁 每天\n⏰ 9:00 提前5分"), 5)

    def test_lead_text(self):
        """說明文字 > 準時、提前10分鐘、提前1小時"""
        # Given / When / Then: 各種分鐘數
        self.assertEqual(daynote.lead_text(None), "準時")
        self.assertEqual(daynote.lead_text(10), "提前10分鐘")
        self.assertEqual(daynote.lead_text(60), "提前1小時")
        self.assertEqual(daynote.reminder_text(T(15, 0), 30), "15:00 · 提前30分鐘")

    def test_reminder_at(self):
        """15:00 提前 10 分鐘 > 14:50 跳提醒；沒有時間 > None"""
        # Given: 兩筆待辦
        task = {"due": TODAY, "time": T(15, 0), "lead": 10}
        # When / Then: 跳提醒的時間
        self.assertEqual(daynote.reminder_at(task), dt.datetime(2026, 10, 6, 14, 50))
        self.assertIsNone(daynote.reminder_at({"due": TODAY, "time": None}))

    def test_mark_past_uses_lead(self):
        """新建的一期 14:55 時：15:00 提前 10 分鐘 > 已過提醒時間，記為已提醒（鍵仍是 15:00）"""
        # Given: 已過提前提醒時間
        reminded = set()
        task = {"id": "t1", "due": TODAY, "time": T(15, 0), "lead": 10}
        # When: 標記
        marked = daynote.mark_past_reminder(reminded, task, dt.datetime(2026, 10, 6, 14, 55))
        # Then: 以原時間為鍵
        self.assertTrue(marked)
        self.assertEqual(reminded, {"t1@2026-10-06T15:00"})


class QuickLeadTest(unittest.TestCase):
    """快速輸入辨識提前提醒。"""

    def test_minutes(self):
        """「3點 開會 提前10分」 > 15:00、提前 10 分鐘、標題只剩開會"""
        # Given / When: 辨識
        result = quick("3點 開會 提前10分")
        # Then: 都有
        self.assertEqual((result["title"], result["time"], result["lead"]), ("開會", T(15, 0), 10))

    def test_other_wordings(self):
        """提前十分鐘、提前半小時、提前1小時、提前兩小時 > 10、30、60、120 分鐘"""
        # Given / When / Then: 各種寫法
        for text, lead in (("提前十分鐘", 10), ("提前半小時", 30), ("提前1小時", 60), ("提前兩小時", 120)):
            with self.subTest(text=text):
                self.assertEqual(quick(f"4點 開會 {text}")["lead"], lead)

    def test_without_time_stays_in_title(self):
        """沒寫時間只寫「提前10分」 > 不算提前提醒，留在標題讓使用者看到"""
        # Given / When: 沒有時間
        result = quick("開會 提前10分")
        # Then: 留在標題
        self.assertEqual((result["title"], result["time"], result["lead"]), ("開會 提前10分", None, None))


class RemindLaterOptionsTest(unittest.TestCase):
    """右鍵「稍後提醒」的選項。"""

    def test_afternoon(self):
        """下午 2:03 > 1 小時後（進位到 3:05）、今天 17:00、明天 9:00"""
        # Given / When: 下午兩點多
        options = daynote.remind_later_options(dt.datetime(2026, 10, 6, 14, 3, 20))
        # Then: 三個選項
        self.assertEqual(options, [("1 小時後提醒", dt.datetime(2026, 10, 6, 15, 5)),
                                   ("今天 17:00 提醒", dt.datetime(2026, 10, 6, 17, 0)),
                                   ("明天 9:00 提醒", dt.datetime(2026, 10, 7, 9, 0))])

    def test_late_afternoon_skips_evening(self):
        """下午 4:30（離 17:00 不到一小時）> 沒有「今天 17:00」"""
        # Given / When: 快下班
        labels = [label for label, _ in daynote.remind_later_options(dt.datetime(2026, 10, 6, 16, 30))]
        # Then: 只有兩個
        self.assertEqual(labels, ["1 小時後提醒", "明天 9:00 提醒"])

    def test_late_night_crosses_midnight(self):
        """晚上 11:30 > 1 小時後是隔天 0:30"""
        # Given / When: 半夜
        options = daynote.remind_later_options(dt.datetime(2026, 10, 6, 23, 30))
        # Then: 隔天
        self.assertEqual(options[0][1], dt.datetime(2026, 10, 7, 0, 30))


if __name__ == "__main__":
    unittest.main()
