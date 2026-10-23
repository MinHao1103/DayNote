"""週報產生器：區間計算（week_range）與週報文字（build_weekly_report）。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

規格決策：
- 區間是週一 00:00 到下週一 00:00（不含）；上週就是再往前 7 天
- 依清單分組，照清單順序；組內依完成時間排序
- 同一清單同標題（每天的週期待辦）合併成一行，標「（N 次）」；總數仍算每一次
- 子工作縮排在父工作下方（父工作也在週報裡時），否則當成一般項目
"""
import datetime as dt
import unittest

from tests.test_recur import daynote

D = dt.date
LISTS = [{"id": "L1", "title": "工作"}, {"id": "L2", "title": "個人"}]


def done(task_id, title, list_id="L1", day=D(2026, 10, 20), hour=10, parent=None):
    titles = {lst["id"]: lst["title"] for lst in LISTS}
    return {"id": task_id, "title": title, "list_id": list_id, "list_title": titles[list_id], "parent": parent,
            "completed": dt.datetime.combine(day, dt.time(hour, 0))}


class WeekRangeTest(unittest.TestCase):

    def test_this_and_last_week(self):
        """星期五（10/23）> 本週 10/19～10/26（不含），上週 10/12～10/19（不含）；星期一就是當天開始"""
        cases = {(D(2026, 10, 23), 0): (D(2026, 10, 19), D(2026, 10, 26)),
                 (D(2026, 10, 23), 1): (D(2026, 10, 12), D(2026, 10, 19)),
                 (D(2026, 10, 19), 0): (D(2026, 10, 19), D(2026, 10, 26)),
                 (D(2026, 10, 25), 0): (D(2026, 10, 19), D(2026, 10, 26))}
        for (today, weeks_ago), expected in cases.items():
            with self.subTest(today=today, weeks_ago=weeks_ago):
                # Given / When: 計算區間
                result = daynote.week_range(today, weeks_ago)
                # Then: 週一到下週一
                self.assertEqual(result, expected)


class BuildReportTest(unittest.TestCase):

    START, END = D(2026, 10, 19), D(2026, 10, 26)

    def build(self, items):
        return daynote.build_weekly_report(items, self.START, self.END, LISTS)

    def test_grouped_by_list_in_order(self):
        """兩個清單的已完成 > 依清單順序分組、組內依完成時間，最後寫總數"""
        # Given: 個人清單先完成一筆，工作清單兩筆（順序打亂）
        items = [done("p1", "買牛奶", "L2", hour=9), done("w2", "回信", hour=15), done("w1", "寫週報", hour=11)]
        # When: 產生週報
        text = self.build(items)
        # Then: 工作在前、時間順序
        self.assertEqual(text, "週報 10/19（一）～10/25（日）\n\n"
                               "【工作】\n- 寫週報\n- 回信\n\n"
                               "【個人】\n- 買牛奶\n\n"
                               "共完成 3 件")

    def test_repeated_titles_merged(self):
        """每天的「站會」完成 3 次 > 合併成一行標 3 次，總數仍是 3"""
        # Given: 三天各完成一次
        items = [done(f"s{i}", "站會", day=D(2026, 10, 19 + i)) for i in range(3)]
        # When: 產生週報
        text = self.build(items)
        # Then: 合併
        self.assertIn("- 站會（3 次）", text)
        self.assertIn("共完成 3 件", text)

    def test_subtask_under_parent(self):
        """子工作和父工作都完成 > 子工作縮排在父工作下方；父工作沒完成 > 子工作當一般項目"""
        # Given: 父子都完成，另一筆子工作的父工作沒完成
        items = [done("p", "準備簡報"), done("c", "整理數據", parent="p", hour=9),
                 done("o", "訂會議室", parent="not-done", hour=12)]
        # When: 產生週報
        text = self.build(items)
        # Then: 縮排與一般項目
        self.assertIn("- 準備簡報\n  - 整理數據\n- 訂會議室", text)

    def test_empty(self):
        """這週沒有完成任何待辦 > 說明沒有資料"""
        # Given / When: 空的
        text = self.build([])
        # Then: 提示
        self.assertEqual(text, "週報 10/19（一）～10/25（日）\n\n這段期間沒有完成的待辦")


if __name__ == "__main__":
    unittest.main()
