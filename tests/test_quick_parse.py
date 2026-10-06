"""快速列一行文字辨識（parse_quick）單元測試。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

規格決策：
- 以空白分段；整段都辨識得出來的片段才算日期／時間／重複／清單，其餘留在標題（「明天見」整段不是日期）
- 日期與時間可以黏在同一段：「明天下午3點」
- 沒寫上午／下午時，1～6 點視為下午，7～12 點照寫；15:30 這種冒號寫法照 24 小時制
- 只有時間：還沒過就是今天，已經過了就是明天；寫了日期就不再挪動
- 只有重複規則：從今天起第一個符合的日子；什麼都沒寫：今天
- 無日期：不能提醒也不能重複（一併清掉）
"""
import datetime as dt
import unittest

from tests.test_recur import daynote, parse

D = dt.date
T = dt.time
TODAY = D(2026, 10, 6)                      # 星期二
NOW = dt.datetime(2026, 10, 6, 14, 0)       # 下午 2 點
LISTS = [{"id": "L1", "title": "工作"}, {"id": "L2", "title": "個人"}, {"id": "L3", "title": "Shopping"}]


def quick(text, now=NOW):
    return daynote.parse_quick(text, LISTS, TODAY, now)


class QuickDateTest(unittest.TestCase):

    def test_relative_days(self):
        """今天／明天／後天／大後天／3天後 > 換算成對應日期"""
        cases = {"今天": 0, "明天": 1, "明日": 1, "後天": 2, "大後天": 3, "3天後": 3, "10天後": 10}
        for word, offset in cases.items():
            with self.subTest(word=word):
                # Given: 日期詞加標題
                # When: 辨識
                result = quick(f"{word} 繳費")
                # Then: 日期正確、標題乾淨
                self.assertEqual(result["due"], TODAY + dt.timedelta(days=offset))
                self.assertEqual(result["title"], "繳費")

    def test_this_week_weekday(self):
        """週五／星期四／禮拜二 > 從今天起最近的那一天（今天星期二就是今天）"""
        cases = {"週五": D(2026, 10, 9), "星期四": D(2026, 10, 8), "禮拜二": TODAY, "週一": D(2026, 10, 12),
                 "週日": D(2026, 10, 11), "這週六": D(2026, 10, 10)}
        for word, expected in cases.items():
            with self.subTest(word=word):
                # Given / When: 辨識
                result = quick(f"{word} 開會")
                # Then: 最近的那一天
                self.assertEqual(result["due"], expected)

    def test_next_week_weekday(self):
        """下週一／下星期五／下週日 > 下一週的那一天"""
        cases = {"下週一": D(2026, 10, 12), "下星期五": D(2026, 10, 16), "下週日": D(2026, 10, 18),
                 "下禮拜二": D(2026, 10, 13)}
        for word, expected in cases.items():
            with self.subTest(word=word):
                # Given / When: 辨識
                result = quick(f"{word} 開會")
                # Then: 下週
                self.assertEqual(result["due"], expected)

    def test_month_day(self):
        """10/15、10月15日、12月3號 > 今年那天；已經過了的 3/1 > 明年"""
        cases = {"10/15": D(2026, 10, 15), "10月15日": D(2026, 10, 15), "12月3號": D(2026, 12, 3),
                 "10/6": TODAY, "3/1": D(2027, 3, 1)}
        for word, expected in cases.items():
            with self.subTest(word=word):
                # Given / When: 辨識
                result = quick(f"報稅 {word}")
                # Then: 對應日期
                self.assertEqual(result["due"], expected)
                self.assertEqual(result["title"], "報稅")

    def test_invalid_date_stays_in_title(self):
        """2/30 這種不存在的日期 > 不當成日期，留在標題、預設今天"""
        # Given / When: 無效日期
        result = quick("2/30 測試")
        # Then: 留在標題
        self.assertEqual(result["title"], "2/30 測試")
        self.assertEqual(result["due"], TODAY)

    def test_no_date(self):
        """寫「無日期」 > 不排日期"""
        # Given / When: 無日期
        result = quick("整理書櫃 無日期")
        # Then: 沒有日期
        self.assertIsNone(result["due"])
        self.assertEqual(result["title"], "整理書櫃")

    def test_glued_word_is_not_date(self):
        """「明天見」整段不是日期 > 留在標題、預設今天"""
        # Given / When: 黏在一起的一般文字
        result = quick("跟小美說明天見")
        # Then: 不辨識
        self.assertEqual(result["title"], "跟小美說明天見")
        self.assertEqual(result["due"], TODAY)


class QuickTimeTest(unittest.TestCase):

    def test_time_forms(self):
        """各種時間寫法 > 換算成 24 小時制"""
        cases = {"15:30": T(15, 30), "9:05": T(9, 5), "9：05": T(9, 5), "3點": T(15, 0), "3點半": T(15, 30),
                 "3點15分": T(15, 15), "3點15": T(15, 15), "8點": T(8, 0), "12點": T(12, 0), "7點": T(7, 0),
                 "上午10點": T(10, 0), "早上6點": T(6, 0), "下午2點": T(14, 0), "晚上8點": T(20, 0),
                 "今晚9點": T(21, 0), "中午12點": T(12, 0), "中午1點": T(13, 0), "下午3點半": T(15, 30),
                 "18點": T(18, 0), "0:30": T(0, 30)}
        for word, expected in cases.items():
            with self.subTest(word=word):
                # Given: 明天加時間（避免「已經過了改明天」的影響）
                # When: 辨識
                result = quick(f"明天 {word} 開會")
                # Then: 時間正確
                self.assertEqual(result["time"], expected)
                self.assertEqual(result["title"], "開會")

    def test_invalid_time_stays_in_title(self):
        """25點、9:75 > 不當成時間，留在標題"""
        for word in ("25點", "9:75"):
            with self.subTest(word=word):
                # Given / When: 無效時間
                result = quick(f"明天 {word}")
                # Then: 留在標題
                self.assertIsNone(result["time"])
                self.assertEqual(result["title"], word)

    def test_plain_number_is_not_time(self):
        """「買 2 個」「繳 1500」 > 純數字不是時間"""
        # Given / When: 含數字的標題
        result = quick("繳 1500 買 2 個")
        # Then: 都留在標題
        self.assertIsNone(result["time"])
        self.assertEqual(result["title"], "繳 1500 買 2 個")

    def test_time_without_date_upcoming(self):
        """只寫時間且還沒到 > 今天"""
        # Given / When: 下午 2 點時寫 3 點
        result = quick("3點 開會")
        # Then: 今天 15:00
        self.assertEqual((result["due"], result["time"]), (TODAY, T(15, 0)))

    def test_time_without_date_passed(self):
        """只寫時間但已經過了 > 明天"""
        # Given / When: 下午 2 點時寫 9:00
        result = quick("9:00 晨會")
        # Then: 明天
        self.assertEqual((result["due"], result["time"]), (TODAY + dt.timedelta(days=1), T(9, 0)))

    def test_explicit_date_not_shifted(self):
        """寫了今天、時間已經過了 > 仍是今天（照使用者寫的）"""
        # Given / When: 明確寫今天
        result = quick("今天 9:00 補登")
        # Then: 不改日期
        self.assertEqual(result["due"], TODAY)

    def test_date_and_time_glued(self):
        """「明天下午3點」「10/15 9:30」黏在一起或分開 > 都辨識"""
        cases = {"明天下午3點 開會": (D(2026, 10, 7), T(15, 0)), "10/15 9:30 看牙醫": (D(2026, 10, 15), T(9, 30)),
                 "下週一早上9點 週會": (D(2026, 10, 12), T(9, 0)), "後天15:00 交件": (D(2026, 10, 8), T(15, 0))}
        for text, expected in cases.items():
            with self.subTest(text=text):
                # Given / When: 辨識
                result = quick(text)
                # Then: 日期與時間
                self.assertEqual((result["due"], result["time"]), expected)


class QuickRecurAndListTest(unittest.TestCase):

    def test_recur_without_date(self):
        """只寫重複規則 > 從今天起第一個符合的日子"""
        cases = {"每天": TODAY, "平日": TODAY, "每週五": D(2026, 10, 9), "每週二": TODAY,
                 "每月15日": D(2026, 10, 15), "每月1日": D(2026, 11, 1), "每月最後一天": D(2026, 10, 31),
                 "每年3/15": D(2027, 3, 15)}
        for word, expected in cases.items():
            with self.subTest(word=word):
                # Given / When: 辨識
                result = quick(f"{word} 繳費")
                # Then: 規則與第一期
                self.assertEqual(result["recur"], parse(word))
                self.assertEqual(result["due"], expected)
                self.assertEqual(result["title"], "繳費")

    def test_recur_with_date_and_time(self):
        """重複＋起始日＋時間 > 全部套用"""
        # Given / When: 完整寫法
        result = quick("下週一 早上9點 每週一 週會")
        # Then: 都有
        self.assertEqual(result, {"title": "週會", "due": D(2026, 10, 12), "time": T(9, 0),
                                  "recur": parse("每週一"), "list": None})

    def test_recur_glued_to_title_not_parsed(self):
        """「每天量血壓」黏在一起 > 不是有效規則，留在標題"""
        # Given / When: 黏在一起
        result = quick("每天量血壓")
        # Then: 沒有重複
        self.assertIsNone(result["recur"])
        self.assertEqual(result["title"], "每天量血壓")

    def test_no_date_clears_time_and_recur(self):
        """無日期又寫了時間或重複 > 一併清掉（沒有日期不能提醒、不能重複）"""
        # Given / When: 矛盾的寫法
        result = quick("無日期 3點 每天 讀書")
        # Then: 都清掉
        self.assertEqual((result["due"], result["time"], result["recur"]), (None, None, None))

    def test_list_tag(self):
        """#個人、#shop（開頭相符、不分大小寫） > 指定清單"""
        cases = {"#個人": "L2", "#工作": "L1", "#shop": "L3", "#SHOPPING": "L3"}
        for tag, expected in cases.items():
            with self.subTest(tag=tag):
                # Given / When: 辨識
                result = quick(f"買牛奶 {tag}")
                # Then: 對應清單、標籤不在標題
                self.assertEqual(result["list"]["id"], expected)
                self.assertEqual(result["title"], "買牛奶")

    def test_unknown_list_tag_stays_in_title(self):
        """#不存在的清單 > 留在標題，由呼叫端用上次的清單"""
        # Given / When: 未知清單
        result = quick("買牛奶 #旅行")
        # Then: 留在標題
        self.assertIsNone(result["list"])
        self.assertEqual(result["title"], "買牛奶 #旅行")

    def test_first_date_wins(self):
        """寫了兩個日期 > 第一個生效，第二個留在標題"""
        # Given / When: 兩個日期
        result = quick("明天 後天 開會")
        # Then: 明天
        self.assertEqual(result["due"], D(2026, 10, 7))
        self.assertEqual(result["title"], "後天 開會")

    def test_only_metadata_has_empty_title(self):
        """只寫了日期時間 > 標題為空（由快速列擋下，不建立無標題待辦）"""
        # Given / When: 沒有標題
        result = quick("明天 3點")
        # Then: 空標題
        self.assertEqual(result["title"], "")

    def test_plain_text_defaults_today(self):
        """什麼都沒寫 > 今天、無時間、無重複"""
        # Given / When: 純標題
        result = quick("  寫週報  ")
        # Then: 預設值
        self.assertEqual(result, {"title": "寫週報", "due": TODAY, "time": None, "recur": None, "list": None})


if __name__ == "__main__":
    unittest.main()
