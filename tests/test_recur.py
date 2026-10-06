"""週期任務（🔁）單元測試。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

規格決策（依需求討論結果）：
- 下一期 = 原定日期之後的第一個符合日；若早於今天，持續往後推到 >= 今天（不補出過期各期）
- 沒有日期的 🔁 任務以今天為基準
- 子工作不支援週期；父工作的下一期不複製子工作
- 完成成功但下一期建立失敗：維持已完成並顯示錯誤，不回滾
- 第一版不支援「每 N 週」「每月第 N 個星期 X」，視為無效規則
- 詳細資訊開頭出現重複的 🔁 行：取第一行，其餘視為內文
- 略過這一期 = 先建立下一期、再刪除這一期（失敗時寧可多一筆，不讓週期中斷）；不留完成紀錄
- 略過只在詳細頁提供；有子工作才跳確認框；不提供復原（誤按時把下一期日期改回即可）
- 刪除重複任務 = 結束週期，確認框提示「不會再產生下一期」並引導改用略過
- 復原時下一期已被刪除（Google 回 404）視為成功
- 防重複（R1）：建立下一期前查詢該日到期的任務，已有同標題、同規則的（未完成或已完成）就不建；
  互動操作不把「已刪除」算進去（避免完成→復原→再完成時漏建），同步補建則算進去（刪掉下一期 = 結束週期）
- 提醒（R2）：新的一期提醒時間已過，直接記為已提醒，不跳出來
- 同步補建（R3）：同步時查詢上次檢查後完成的 🔁 任務（手機上勾選的），沒有下一期就補建；
  第一次同步只記錄時間點、不回溯；補建失敗不影響同步，檢查時間點不前進，下次重試
- 下一期建立失敗（R4）：狀態列紅字警告與復原按鈕並存
- 同步競態（R5）：下一期的 id 已在畫面上時不重複加入
- 詳細頁略過（R6）：修改只套用在下一期，不對即將刪除的這一期送出 PATCH

以下情境依賴畫面或計時器，不寫成單元測試，改列為手動驗證：
- 從右下角提醒視窗按「完成」→ 走同一條 complete() 路徑，會產生下一期
- 詳細頁修改規則後直接按「✓ 標示為完成」→ 下一期使用修改後的規則
- 下一期帶 ⏰ 時，會在新日期的指定時間跳提醒
- 子工作的詳細頁不顯示 🔁 選單
- 月曆只在實際 due 標點，不預先畫出未來各期
- 略過後下一期帶 ⏰ 時，會在新日期跳提醒
- 主畫面列表與右下角提醒視窗不顯示「略過這一期」
- 刪除重複任務後，Google 已完成紀錄中以前的各期仍保留；在手機刪除同樣結束週期
- 同步補建出的下一期若提醒時間已過，不跳提醒（由 _after_load 處理）

實作前需實測（R7）：
- [2026-10-05 已測] Google 內建重複（以 Tasks 網頁版設定，與手機 App 同一套機制，暫時清單測完刪除）：
  API 看不到任何重複欄位；以 DayNote 方式 PATCH 詳細資訊後重複設定仍保留；以 API 勾選完成後，
  網頁顯示「下一次在 明天 🔁」，與在網頁上完成的對照組相同 → DayNote 不會打斷內建重複
- [2026-10-05 已測] 官方 App（網頁）完成的任務會被設為 hidden=true，API 完成的不會 →
  同步補建查詢必須帶 showHidden=true（已帶）；先前觀察到的 6 筆 hidden=false 應是在 DayNote 完成的
- [2026-10-05 已測] dueMin=當日 00:00Z、dueMax=隔日 00:00Z：35 個日期（含相鄰日）回傳皆與當日任務完全一致；
  dueMax 不含邊界；showDeleted=true 可查到已刪除任務（防重複判斷「刪掉下一期 = 結束週期」依賴此行為）
- [2026-10-05 已測] 真實 Google 端對端（暫時清單，測完刪除）13 項通過：完成產生下一期、防重複、復原刪除下一期、
  復原後再完成重建、略過（不留完成紀錄）、DayNote 以外完成後同步補建、重複同步不多建、刪掉下一期後不重建
- [2026-10-05 已測] 刪除已刪除的任務、對已刪除的任務標完成：Google 皆回成功（非 404），
  NotFound 處理僅為保險；另一台已略過時仍由防重複避免多建
"""
import datetime as dt
import importlib.machinery
import importlib.util
import os
import unittest
import unittest.mock

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "daynote.pyw")
_loader = importlib.machinery.SourceFileLoader("daynote", _PATH)
_spec = importlib.util.spec_from_file_location("daynote", _PATH, loader=_loader)
daynote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daynote)

D = dt.date
T = dt.time
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def need(name):
    """取得待實作的函式；尚未實作時讓該案例個別失敗，而不是整個檔案載入失敗。"""
    obj = getattr(daynote, name, None)
    if obj is None:
        raise AssertionError(f"尚未實作 daynote.{name}")
    return obj


def recur(kind, weekdays=(), day=None, month=None):
    return need("Recur")(kind, frozenset(weekdays), day, month)


def last_day():
    return need("LAST_DAY")


def parse(text):
    return need("parse_recur")(text)


# ---------------------------------------------------------------- A. 規則解析

class ParseRecurTest(unittest.TestCase):

    def test_daily(self):
        """輸入「每天」 > 解析為每日"""
        # Given: 使用者輸入每天
        text = "每天"
        # When: 解析規則
        result = parse(text)
        # Then: 為每日規則
        self.assertEqual(recur("daily"), result)

    def test_weekdays_alias(self):
        """輸入「平日」 > 解析為每週一到週五"""
        # Given: 使用者輸入平日
        text = "平日"
        # When: 解析規則
        result = parse(text)
        # Then: 為週一至週五
        self.assertEqual(recur("weekly", {MON, TUE, WED, THU, FRI}), result)

    def test_weekly_single(self):
        """輸入「每週一」 > 解析為每週一"""
        # Given: 單一星期
        text = "每週一"
        # When: 解析規則
        result = parse(text)
        # Then: 只有週一
        self.assertEqual(recur("weekly", {MON}), result)

    def test_weekly_multiple(self):
        """輸入「每週一三五」 > 解析為週一三五"""
        # Given: 多個星期
        text = "每週一三五"
        # When: 解析規則
        result = parse(text)
        # Then: 週一、三、五
        self.assertEqual(recur("weekly", {MON, WED, FRI}), result)

    def test_weekly_sunday_variants(self):
        """輸入「每週日」或「每週天」 > 皆解析為週日"""
        for text in ("每週日", "每週天"):
            with self.subTest(text=text):
                # Given: 週日的兩種寫法
                # When: 解析規則
                result = parse(text)
                # Then: 皆為週日
                self.assertEqual(recur("weekly", {SUN}), result)

    def test_weekly_character_variants(self):
        """輸入「每周一」「每星期一」 > 等同每週一"""
        for text in ("每周一", "每星期一"):
            with self.subTest(text=text):
                # Given: 週的異體寫法
                # When: 解析規則
                result = parse(text)
                # Then: 等同每週一
                self.assertEqual(recur("weekly", {MON}), result)

    def test_weekly_separators_and_digits(self):
        """輸入含空白、頓號、逗號或數字的星期 > 等同每週一三五"""
        for text in ("每週 一、三、五", "每週1,3,5", "每週1、3、5"):
            with self.subTest(text=text):
                # Given: 不同分隔符號與數字寫法
                # When: 解析規則
                result = parse(text)
                # Then: 週一、三、五
                self.assertEqual(recur("weekly", {MON, WED, FRI}), result)

    def test_weekly_digit_seven_is_sunday(self):
        """輸入「每週7」 > 7 代表週日"""
        # Given: 數字 7
        text = "每週7"
        # When: 解析規則
        result = parse(text)
        # Then: 為週日
        self.assertEqual(recur("weekly", {SUN}), result)

    def test_weekly_unordered_and_duplicated(self):
        """星期亂序或重複 > 排序並去重"""
        cases = {"每週五三一": {MON, WED, FRI}, "每週一一": {MON}}
        for text, expected in cases.items():
            with self.subTest(text=text):
                # Given: 亂序或重複的星期
                # When: 解析規則
                result = parse(text)
                # Then: 集合內容正確
                self.assertEqual(recur("weekly", expected), result)

    def test_monthly_day(self):
        """輸入「每月15日」或「每月15號」 > 解析為每月 15 日"""
        for text in ("每月15日", "每月15號"):
            with self.subTest(text=text):
                # Given: 每月固定日
                # When: 解析規則
                result = parse(text)
                # Then: 每月 15 日
                self.assertEqual(recur("monthly", day=15), result)

    def test_monthly_31_kept(self):
        """輸入「每月31日」 > 解析時保留 31，不截斷"""
        # Given: 不是每個月都有的日期
        text = "每月31日"
        # When: 解析規則
        result = parse(text)
        # Then: 仍為 31 日
        self.assertEqual(recur("monthly", day=31), result)

    def test_monthly_last_day(self):
        """輸入「每月最後一天」 > 解析為每月月底"""
        # Given: 月底規則
        text = "每月最後一天"
        # When: 解析規則
        result = parse(text)
        # Then: day 為 LAST_DAY
        self.assertEqual(recur("monthly", day=last_day()), result)

    def test_yearly(self):
        """輸入「每年3/15」或「每年3月15日」 > 解析為每年 3 月 15 日"""
        for text in ("每年3/15", "每年3月15日"):
            with self.subTest(text=text):
                # Given: 每年固定日
                # When: 解析規則
                result = parse(text)
                # Then: 3 月 15 日
                self.assertEqual(recur("yearly", month=3, day=15), result)

    def test_yearly_leap_day(self):
        """輸入「每年2/29」 > 為合法規則"""
        # Given: 閏日
        text = "每年2/29"
        # When: 解析規則
        result = parse(text)
        # Then: 2 月 29 日
        self.assertEqual(recur("yearly", month=2, day=29), result)

    def test_out_of_range_invalid(self):
        """日期超出範圍 > 視為無效規則"""
        for text in ("每月0日", "每月32日", "每年2/30", "每年13/1", "每年4/31", "每週8", "每週0"):
            with self.subTest(text=text):
                # Given: 不存在的日期或星期
                # When: 解析規則
                result = parse(text)
                # Then: 無效
                self.assertIsNone(result)

    def test_unsupported_in_v1_invalid(self):
        """第一版不支援的寫法 > 視為無效規則"""
        for text in ("每週", "每2週一", "每2天", "每月第1個星期三", "每月"):
            with self.subTest(text=text):
                # Given: 間隔或第 N 個星期的規則
                # When: 解析規則
                result = parse(text)
                # Then: 無效
                self.assertIsNone(result)

    def test_surrounding_whitespace(self):
        """前後有半形或全形空白 > 正常解析"""
        for text in ("  每天  ", "　每天　"):
            with self.subTest(text=repr(text)):
                # Given: 含空白的輸入
                # When: 解析規則
                result = parse(text)
                # Then: 為每日
                self.assertEqual(recur("daily"), result)

    def test_garbage_invalid(self):
        """空字串、None 或亂打 > 視為無效規則"""
        for text in ("", None, "abc", "每天每天", "明天"):
            with self.subTest(text=text):
                # Given: 無意義輸入
                # When: 解析規則
                result = parse(text)
                # Then: 無效
                self.assertIsNone(result)


# ---------------------------------------------------------------- B. 規則輸出

class FormatRecurTest(unittest.TestCase):

    CANONICAL = ("每天", "平日", "每週一", "每週一三五", "每週六日", "每月15日", "每月31日",
                 "每月最後一天", "每年3/15", "每年2/29")

    def test_canonical_text(self):
        """各種規則 > 輸出標準寫法"""
        for text in self.CANONICAL:
            with self.subTest(text=text):
                # Given: 由標準寫法解析出的規則
                rule = parse(text)
                # When: 轉回文字
                result = need("format_recur")(rule)
                # Then: 與標準寫法相同
                self.assertEqual(text, result)

    def test_variant_normalized(self):
        """異體寫法 > 輸出統一為標準寫法"""
        cases = {"每周 五、一、三": "每週一三五", "每月15號": "每月15日", "每年3月15日": "每年3/15",
                 "每週日": "每週日", "每週天": "每週日"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                # Given: 異體寫法
                rule = parse(text)
                # When: 轉回文字
                result = need("format_recur")(rule)
                # Then: 為標準寫法
                self.assertEqual(expected, result)

    def test_mon_to_fri_is_weekdays(self):
        """週一到週五 > 輸出「平日」"""
        # Given: 週一至週五的每週規則
        rule = recur("weekly", {MON, TUE, WED, THU, FRI})
        # When: 轉回文字
        result = need("format_recur")(rule)
        # Then: 為平日
        self.assertEqual("平日", result)

    def test_round_trip(self):
        """規則轉文字再解析 > 與原規則相同"""
        for text in self.CANONICAL:
            with self.subTest(text=text):
                # Given: 一條規則
                rule = parse(text)
                # When: 轉文字後再解析
                result = parse(need("format_recur")(rule))
                # Then: 規則不變
                self.assertEqual(rule, result)


# ---------------------------------------------------------------- C. 詳細資訊開頭的 metadata

class SplitMetaTest(unittest.TestCase):

    def split(self, notes):
        return need("split_meta")(notes)

    def test_legacy_time_only(self):
        """舊資料只有 ⏰ 行 > 行為與現行版本相同"""
        # Given: 舊版格式（時間行後有空行）
        notes = "⏰ 15:00\n\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 時間正確、內文去除開頭空行
        self.assertEqual((T(15, 0), None, "內容"), result)

    def test_recur_only(self):
        """只有 🔁 行 > 拆出規則與內文"""
        # Given: 只有週期
        notes = "🔁 每天\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 規則為每日
        self.assertEqual((None, recur("daily"), "內容"), result)

    def test_time_then_recur(self):
        """⏰ 在前、🔁 在後 > 兩者皆拆出"""
        # Given: 標準順序
        notes = "⏰ 15:00\n🔁 每天\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 時間與規則皆正確
        self.assertEqual((T(15, 0), recur("daily"), "內容"), result)

    def test_recur_then_time(self):
        """🔁 在前、⏰ 在後（手機上手動輸入的順序） > 結果與標準順序相同"""
        # Given: 反向順序
        notes = "🔁 每天\n⏰ 15:00\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 時間與規則皆正確
        self.assertEqual((T(15, 0), recur("daily"), "內容"), result)

    def test_recur_not_at_top(self):
        """🔁 不在開頭 > 不視為 metadata，內文原樣保留"""
        # Given: 🔁 出現在內文中間
        notes = "內容\n🔁 每天"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 沒有規則，內文不變
        self.assertEqual((None, None, notes), result)

    def test_invalid_recur_kept_as_body(self):
        """🔁 規則無效 > 不吃掉使用者的文字，整行留在內文"""
        # Given: 無法解析的規則
        notes = "🔁 亂打\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 沒有規則，內文完整保留
        self.assertEqual((None, None, notes), result)

    def test_valid_time_with_invalid_recur(self):
        """⏰ 有效但 🔁 無效 > 只拆出時間，🔁 行留在內文"""
        # Given: 時間有效、規則無效
        notes = "⏰ 15:00\n🔁 亂打\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 規則行成為內文第一行
        self.assertEqual((T(15, 0), None, "🔁 亂打\n內容"), result)

    def test_emoji_variants(self):
        """🔁 後沒有空白或帶 FE0F 變體選擇符 > 正常解析"""
        for notes in ("🔁每天\n內容", "🔁️ 每天\n內容"):
            with self.subTest(notes=repr(notes)):
                # Given: 不同的 emoji 輸入方式
                # When: 拆解 metadata
                result = self.split(notes)
                # Then: 規則為每日
                self.assertEqual((None, recur("daily"), "內容"), result)

    def test_time_emoji_variant(self):
        """⏰ 帶 FE0F 變體選擇符 > 正常解析時間"""
        # Given: 手機輸入的 ⏰ 可能帶 FE0F
        notes = "⏰️ 09:30\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 時間正確
        self.assertEqual((T(9, 30), None, "內容"), result)

    def test_duplicated_recur_first_wins(self):
        """開頭有兩行 🔁 > 取第一行，第二行視為內文"""
        # Given: 重複的規則行
        notes = "🔁 每天\n🔁 每週一\n內容"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 第一行生效
        self.assertEqual((None, recur("daily"), "🔁 每週一\n內容"), result)

    def test_meta_without_body(self):
        """只有 metadata 沒有內文 > 內文為空字串"""
        # Given: 只有時間與規則
        notes = "⏰ 15:00\n🔁 每天"
        # When: 拆解 metadata
        result = self.split(notes)
        # Then: 內文為空
        self.assertEqual((T(15, 0), recur("daily"), ""), result)

    def test_empty_notes(self):
        """詳細資訊為空或 None > 三者皆為空"""
        for notes in ("", None):
            with self.subTest(notes=notes):
                # Given: 沒有詳細資訊
                # When: 拆解 metadata
                result = self.split(notes)
                # Then: 皆為空
                self.assertEqual((None, None, ""), result)


class JoinMetaTest(unittest.TestCase):

    def join(self, time_value, rule, body):
        return need("join_meta")(time_value, rule, body)

    def test_join_both(self):
        """時間與規則皆有 > 固定輸出 ⏰ 在前、🔁 在後"""
        # Given: 時間、規則與內文
        rule = recur("daily")
        # When: 組回詳細資訊
        result = self.join(T(15, 0), rule, "內容")
        # Then: 標準順序
        self.assertEqual("⏰ 15:00\n🔁 每天\n內容", result)

    def test_join_none(self):
        """沒有時間也沒有規則 > 只回傳內文"""
        # Given: 只有內文
        body = "內容"
        # When: 組回詳細資訊
        result = self.join(None, None, body)
        # Then: 內文不變
        self.assertEqual("內容", result)

    def test_join_recur_only_no_body(self):
        """只有規則沒有內文 > 只有 🔁 一行"""
        # Given: 只有規則
        rule = recur("weekly", {MON})
        # When: 組回詳細資訊
        result = self.join(None, rule, "")
        # Then: 單一行
        self.assertEqual("🔁 每週一", result)

    def test_round_trip(self):
        """拆解後再組回再拆解 > 結果不變"""
        samples = [(T(15, 0), recur("daily"), "內容"), (None, recur("monthly", day=31), ""),
                   (T(9, 5), None, "多行\n內容"), (None, None, "")]
        for sample in samples:
            with self.subTest(sample=sample):
                # Given: 一組 (時間, 規則, 內文)
                notes = self.join(*sample)
                # When: 再拆解
                result = need("split_meta")(notes)
                # Then: 與原值相同
                self.assertEqual(sample, result)


# ---------------------------------------------------------------- D. 下一期日期計算

class NextDueTest(unittest.TestCase):

    def next_due(self, text, base, today):
        return need("next_due")(parse(text), base, today)

    def test_daily(self):
        """每天，今天完成今天的任務 > 下一期為明天"""
        # Given: 原定今天（2026/10/5 週一）
        base = today = D(2026, 10, 5)
        # When: 計算下一期
        result = self.next_due("每天", base, today)
        # Then: 明天
        self.assertEqual(D(2026, 10, 6), result)

    def test_daily_overdue(self):
        """每天，過期 4 天才完成 > 下一期為今天，不補出過期各期"""
        # Given: 原定 10/1，今天 10/5
        base, today = D(2026, 10, 1), D(2026, 10, 5)
        # When: 計算下一期
        result = self.next_due("每天", base, today)
        # Then: 今天
        self.assertEqual(D(2026, 10, 5), result)

    def test_weekly_same_weekday(self):
        """每週一，週一完成 > 下一期為下週一"""
        # Given: 原定週一
        base = today = D(2026, 10, 5)
        # When: 計算下一期
        result = self.next_due("每週一", base, today)
        # Then: 下週一
        self.assertEqual(D(2026, 10, 12), result)

    def test_weekly_multiple_next_in_week(self):
        """每週一三五，週一完成 > 下一期為本週三"""
        # Given: 原定週一
        base = today = D(2026, 10, 5)
        # When: 計算下一期
        result = self.next_due("每週一三五", base, today)
        # Then: 週三
        self.assertEqual(D(2026, 10, 7), result)

    def test_weekly_multiple_cross_week(self):
        """每週一三五，週五完成 > 下一期為下週一"""
        # Given: 原定週五
        base = today = D(2026, 10, 9)
        # When: 計算下一期
        result = self.next_due("每週一三五", base, today)
        # Then: 下週一
        self.assertEqual(D(2026, 10, 12), result)

    def test_weekly_base_not_in_rule(self):
        """每週一，原定日為週三（不在規則內） > 下一期為下週一"""
        # Given: 原定週三
        base = today = D(2026, 10, 7)
        # When: 計算下一期
        result = self.next_due("每週一", base, today)
        # Then: 下週一
        self.assertEqual(D(2026, 10, 12), result)

    def test_weekdays_skip_weekend(self):
        """平日，週五完成 > 下一期跳過週末為週一"""
        # Given: 原定週五
        base = today = D(2026, 10, 9)
        # When: 計算下一期
        result = self.next_due("平日", base, today)
        # Then: 下週一
        self.assertEqual(D(2026, 10, 12), result)

    def test_monthly_same_day(self):
        """每月15日，15 日完成 > 下一期為下個月 15 日"""
        # Given: 原定 10/15
        base = today = D(2026, 10, 15)
        # When: 計算下一期
        result = self.next_due("每月15日", base, today)
        # Then: 11/15
        self.assertEqual(D(2026, 11, 15), result)

    def test_monthly_base_before_rule_day(self):
        """每月15日，原定日在當月 15 日之前 > 下一期為當月 15 日"""
        # Given: 原定 10/10
        base = today = D(2026, 10, 10)
        # When: 計算下一期
        result = self.next_due("每月15日", base, today)
        # Then: 10/15
        self.assertEqual(D(2026, 10, 15), result)

    def test_monthly_31_in_february_common_year(self):
        """每月31日，平年 1/31 完成 > 下一期退到 2/28"""
        # Given: 2026 年為平年
        base = today = D(2026, 1, 31)
        # When: 計算下一期
        result = self.next_due("每月31日", base, today)
        # Then: 2/28
        self.assertEqual(D(2026, 2, 28), result)

    def test_monthly_31_in_february_leap_year(self):
        """每月31日，閏年 1/31 完成 > 下一期退到 2/29"""
        # Given: 2028 年為閏年
        base = today = D(2028, 1, 31)
        # When: 計算下一期
        result = self.next_due("每月31日", base, today)
        # Then: 2/29
        self.assertEqual(D(2028, 2, 29), result)

    def test_monthly_31_anchor_restored(self):
        """每月31日，從 2/28 完成 > 下一期回到 3/31（錨定規則日，不是 base 的日）"""
        # Given: 上一期因小月退到 2/28
        base = today = D(2026, 2, 28)
        # When: 計算下一期
        result = self.next_due("每月31日", base, today)
        # Then: 3/31
        self.assertEqual(D(2026, 3, 31), result)

    def test_monthly_31_after_30_day_month(self):
        """每月31日，從 4/30 完成 > 下一期為 5/31"""
        # Given: 四月只有 30 天
        base = today = D(2026, 4, 30)
        # When: 計算下一期
        result = self.next_due("每月31日", base, today)
        # Then: 5/31
        self.assertEqual(D(2026, 5, 31), result)

    def test_monthly_last_day(self):
        """每月最後一天，1/31 與 2/28 完成 > 依序為 2/28、3/31"""
        cases = {D(2026, 1, 31): D(2026, 2, 28), D(2026, 2, 28): D(2026, 3, 31)}
        for base, expected in cases.items():
            with self.subTest(base=base):
                # Given: 月底完成
                # When: 計算下一期
                result = self.next_due("每月最後一天", base, base)
                # Then: 下個月月底
                self.assertEqual(expected, result)

    def test_monthly_cross_year(self):
        """每月15日，12/15 完成 > 下一期為隔年 1/15"""
        # Given: 年底
        base = today = D(2026, 12, 15)
        # When: 計算下一期
        result = self.next_due("每月15日", base, today)
        # Then: 2027/1/15
        self.assertEqual(D(2027, 1, 15), result)

    def test_yearly(self):
        """每年3/15，當天完成 > 下一期為隔年 3/15"""
        # Given: 原定 2026/3/15
        base = today = D(2026, 3, 15)
        # When: 計算下一期
        result = self.next_due("每年3/15", base, today)
        # Then: 2027/3/15
        self.assertEqual(D(2027, 3, 15), result)

    def test_yearly_base_before_rule_date(self):
        """每年3/15，原定日在當年 3/15 之前 > 下一期為當年 3/15"""
        # Given: 原定 2026/3/1
        base = today = D(2026, 3, 1)
        # When: 計算下一期
        result = self.next_due("每年3/15", base, today)
        # Then: 2026/3/15
        self.assertEqual(D(2026, 3, 15), result)

    def test_yearly_leap_day(self):
        """每年2/29 > 平年退到 2/28，下一個閏年回到 2/29"""
        cases = {D(2028, 2, 29): D(2029, 2, 28), D(2031, 2, 28): D(2032, 2, 29)}
        for base, expected in cases.items():
            with self.subTest(base=base):
                # Given: 閏日規則
                # When: 計算下一期
                result = self.next_due("每年2/29", base, base)
                # Then: 平年 2/28、閏年 2/29
                self.assertEqual(expected, result)

    def test_completed_early(self):
        """每週一，提早完成下週一的任務 > 下一期為再下週一"""
        # Given: 原定 10/12，今天才 10/5
        base, today = D(2026, 10, 12), D(2026, 10, 5)
        # When: 計算下一期
        result = self.next_due("每週一", base, today)
        # Then: 10/19
        self.assertEqual(D(2026, 10, 19), result)

    def test_monthly_overdue_many_periods(self):
        """每月15日，7/15 的任務拖到 10/5 才完成 > 下一期為 10/15，不補出 8、9 月"""
        # Given: 過期近三個月
        base, today = D(2026, 7, 15), D(2026, 10, 5)
        # When: 計算下一期
        result = self.next_due("每月15日", base, today)
        # Then: 10/15
        self.assertEqual(D(2026, 10, 15), result)

    def test_no_base_uses_today(self):
        """沒有原定日期 > 以今天為基準"""
        cases = {"每天": D(2026, 10, 6), "每月15日": D(2026, 10, 15), "每週一": D(2026, 10, 12)}
        for text, expected in cases.items():
            with self.subTest(text=text):
                # Given: 今天 10/5（週一），任務沒有日期
                # When: 計算下一期
                result = self.next_due(text, None, D(2026, 10, 5))
                # Then: 今天之後的第一期
                self.assertEqual(expected, result)


# ---------------------------------------------------------------- E. 完成時產生下一期

def make_task(**overrides):
    task = {"id": "t1", "title": "繳費", "list_id": "L1", "list_title": "工作", "due": D(2026, 10, 5),
            "parent": None, "notes": "帳單", "time": None, "recur": None, "position": "001"}
    task.update(overrides)
    return task


class NextTaskForTest(unittest.TestCase):
    """純函式：決定完成一筆任務後要建立的下一期內容。"""

    def test_recurring_parent(self):
        """父工作有 🔁 每天 > 回傳同清單、同標題、完整詳細資訊、明天的下一期"""
        # Given: 有時間與週期的父工作
        task = make_task(time=T(9, 0), recur=parse("每天"))
        # When: 計算下一期
        result = need("next_task_for")(task, D(2026, 10, 5))
        # Then: 內容完整複製，日期為明天
        self.assertEqual({"list_id": "L1", "title": "繳費", "due": D(2026, 10, 6),
                          "notes": "⏰ 09:00\n🔁 每天\n帳單"}, result)

    def test_non_recurring(self):
        """沒有 🔁 > 不產生下一期"""
        # Given: 一般任務
        task = make_task()
        # When: 計算下一期
        result = need("next_task_for")(task, D(2026, 10, 5))
        # Then: 無
        self.assertIsNone(result)

    def test_subtask_ignored(self):
        """子工作有 🔁 > 不產生下一期（子工作不支援週期）"""
        # Given: 手機上在子工作打了 🔁
        task = make_task(parent="p1", recur=parse("每天"))
        # When: 計算下一期
        result = need("next_task_for")(task, D(2026, 10, 5))
        # Then: 無
        self.assertIsNone(result)

    def test_no_due_uses_today(self):
        """有 🔁 但沒有日期 > 以今天為基準"""
        # Given: 沒有日期的週期任務
        task = make_task(due=None, recur=parse("每天"))
        # When: 計算下一期
        result = need("next_task_for")(task, D(2026, 10, 5))
        # Then: 明天
        self.assertEqual(D(2026, 10, 6), result["due"])


def raw_task(id, title, due, notes="", status="needsAction", deleted=False, parent=None):
    """Google Tasks API 回傳格式的任務。"""
    raw = {"id": id, "title": title, "notes": notes, "status": status,
           "due": f"{due.isoformat()}T00:00:00.000Z" if due else None, "position": "001"}
    if deleted:
        raw["deleted"] = True
    if parent:
        raw["parent"] = parent
    return raw


class FakeGoogle:
    """API 替身：寫入記錄在 calls、讀取記錄在 reads；fail 指定的方法會丟出例外。

    fail 可為方法名稱集合（丟出 ApiError），或 {方法名稱: 例外類別名稱}，例如 {"delete_task": "NotFound"}。
    remote：Google 上既有的任務（API 格式），供防重複查詢與同步使用。
    """

    def __init__(self, fail=(), remote=()):
        self.calls, self.reads = [], []
        self.fail = fail if isinstance(fail, dict) else {name: "ApiError" for name in fail}
        self.remote = list(remote)
        self.refresh_token = "<REFRESH_TOKEN>"
        self._seq = 0

    def _check(self, name):
        if name in self.fail:
            raise need(self.fail[name])(f"{name} failed")

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        self._check(name)

    def _read(self, name, *args):
        self.reads.append((name, args))
        self._check(name)

    def names(self):
        """寫入呼叫的名稱（不含讀取）。"""
        return [c[0] for c in self.calls]

    # ---- 寫入

    def complete_task(self, list_id, task_id):
        self._record("complete_task", list_id, task_id)

    def uncomplete_task(self, list_id, task_id):
        self._record("uncomplete_task", list_id, task_id)

    def delete_task(self, list_id, task_id):
        self._record("delete_task", list_id, task_id)

    def update_task(self, list_id, task_id, fields):
        self._record("update_task", list_id, task_id, fields)

    def add_task(self, list_id, title, due, parent=None, notes=None):
        self._record("add_task", list_id, title, due, parent=parent, notes=notes)
        self._seq += 1
        return {"id": f"new{self._seq}", "title": title, "position": "002",
                "due": f"{due.isoformat()}T00:00:00.000Z" if due else None}

    # ---- 讀取

    def tasklists(self):
        self._read("tasklists")
        return [{"id": "L1", "title": "工作"}]

    def open_tasks(self, list_id):
        self._read("open_tasks", list_id)
        return [t for t in self.remote if t.get("status", "needsAction") == "needsAction" and not t.get("deleted")]

    def due_tasks(self, list_id, day):
        """防重複查詢：該日到期的所有任務（含已完成、隱藏、已刪除）。"""
        self._read("due_tasks", list_id, day)
        return [t for t in self.remote if t.get("due") and t["due"][:10] == day.isoformat()]

    def completed_since(self, list_id, since):
        """同步補建：since 之後完成的任務（含隱藏）。"""
        self._read("completed_since", list_id, since)
        return [t for t in self.remote if t.get("status") == "completed" and not t.get("deleted")]

    def events(self, start, end, calendar_id="primary"):
        return []


def app_method(name):
    """取得 App 上待實作的方法；尚未實作時讓該案例個別失敗。"""
    method = getattr(daynote.App, name, None)
    if method is None:
        raise AssertionError(f"尚未實作 App.{name}")
    return method


class _Widget:
    def pack(self, *args, **kwargs):
        pass

    def pack_forget(self):
        pass


class FakeApp:
    """借用 App 的真實方法，畫面相關的方法改成記錄用的替身，不需要建立 Tk 視窗。"""

    complete = daynote.App.complete
    undo_complete = daynote.App.undo_complete
    delete_detail = daynote.App.delete_detail
    _children = daynote.App._children
    _add_spawned = getattr(daynote.App, "_add_spawned", None)

    def __init__(self, tasks, google=None, today=D(2026, 10, 5), now=None):
        self.tasks = list(tasks)
        self.g = google or FakeGoogle()
        self.today = today
        self.now = now or dt.datetime.combine(today, T(8, 0))
        self.lists = [{"id": "L1", "title": "工作"}]
        self.undo_group = None
        self.undo_spawned = None
        self.statuses, self.errors = [], []
        self.detail = None
        self.detail_view = self.main_view = _Widget()
        self.closed_detail = []      # 每次 close_detail 的 save 參數
        self.pending_fields = {}     # 詳細頁尚未儲存的修改（API 欄位格式）
        self.reminded, self.snoozed = set(), {}
        self.reminder_saves = 0
        self.before_done = None      # 模擬背景工作完成、回呼執行前插入的事件（例如同步）

    def skip(self, task):
        app_method("skip")(self, task)

    def skip_detail(self):
        app_method("skip_detail")(self)

    def close_detail(self, save=True):
        """替身：記錄是否儲存後關閉詳細頁。"""
        self.closed_detail.append(save)
        self.detail = None

    def _detail_changes(self):
        return dict(self.pending_fields)

    def refresh(self):
        pass

    def _now(self):
        return self.now

    def _save_reminder_state(self):
        self.reminder_saves += 1

    def run_bg(self, work, on_done):
        try:
            result = work()
        except Exception as e:  # 比照 App.run_bg：錯誤交給 on_done
            if self.before_done:
                self.before_done()
            on_done(None, e)
        else:
            if self.before_done:
                self.before_done()
            on_done(result, None)

    def redraw(self):
        pass

    def _offer_undo(self, group, spawned=(), warning=None):
        """比照 App：先更新狀態列（會收起舊的復原），再掛上這次的復原。"""
        self._set_status(warning or f"已完成「{group[0]['title']}」", error=bool(warning))
        self.undo_group, self.undo_spawned = group, list(spawned)

    def _hide_undo(self):
        self.undo_group = self.undo_spawned = None

    def _set_status(self, text, error=False):
        """比照 App._set_status：每次更新狀態列都會收起復原按鈕。"""
        self._hide_undo()
        self.statuses.append((text, error))

    def _show_error(self, err):
        self.errors.append(err)


class CompleteFlowTest(unittest.TestCase):

    def test_recurring_creates_next(self):
        """完成 🔁 每天的父工作 > 原任務完成，並新增明天的下一期到畫面"""
        # Given: 一筆每日任務
        task = make_task(time=T(9, 0), recur=parse("每天"))
        app = FakeApp([task])
        # When: 勾選完成
        app.complete(task)
        # Then: 送出完成與新增，畫面只剩下一期
        self.assertEqual(["complete_task", "add_task"], app.g.names())
        _, args, kwargs = app.g.calls[1]
        self.assertEqual(("L1", "繳費", D(2026, 10, 6)), args)
        self.assertEqual({"parent": None, "notes": "⏰ 09:00\n🔁 每天\n帳單"}, kwargs)
        self.assertEqual(1, len(app.tasks))
        spawned = app.tasks[0]
        self.assertEqual(("new1", D(2026, 10, 6), T(9, 0), parse("每天"), "帳單"),
                         (spawned["id"], spawned["due"], spawned["time"], spawned["recur"], spawned["notes"]))
        self.assertEqual([spawned], app.undo_spawned)

    def test_non_recurring_regression(self):
        """完成一般任務 > 只送出完成，不新增任何任務"""
        # Given: 沒有週期的任務
        task = make_task()
        app = FakeApp([task])
        # When: 勾選完成
        app.complete(task)
        # Then: 只有完成
        self.assertEqual(["complete_task"], app.g.names())
        self.assertEqual([], app.tasks)
        self.assertEqual([task], app.undo_group)

    def test_subtask_recur_ignored(self):
        """完成有 🔁 的子工作 > 不新增下一期"""
        # Given: 父工作與帶 🔁 的子工作
        parent = make_task(id="p1")
        child = make_task(id="c1", parent="p1", recur=parse("每天"))
        app = FakeApp([parent, child])
        # When: 勾選子工作
        app.complete(child)
        # Then: 只完成子工作
        self.assertEqual(["complete_task"], app.g.names())

    def test_parent_with_children(self):
        """完成有 🔁 且有 2 個子工作的父工作 > 3 筆都完成，下一期只有父工作"""
        # Given: 週期父工作與 2 個子工作
        parent = make_task(id="p1", recur=parse("每天"))
        children = [make_task(id="c1", parent="p1", position="1"), make_task(id="c2", parent="p1", position="2")]
        app = FakeApp([parent, *children])
        # When: 勾選父工作
        app.complete(parent)
        # Then: 三次完成、一次新增（無 parent）
        self.assertEqual(["complete_task"] * 3 + ["add_task"], app.g.names())
        self.assertIsNone(app.g.calls[3][2]["parent"])
        self.assertEqual(["new1"], [t["id"] for t in app.tasks])

    def test_complete_fails_no_next(self):
        """完成 API 失敗 > 不建立下一期，任務放回畫面並顯示錯誤"""
        # Given: 完成會失敗
        task = make_task(recur=parse("每天"))
        app = FakeApp([task], FakeGoogle(fail={"complete_task"}))
        # When: 勾選完成
        app.complete(task)
        # Then: 沒有新增，任務還在
        self.assertNotIn("add_task", app.g.names())
        self.assertEqual([task], app.tasks)
        self.assertEqual(1, len(app.errors))

    def test_add_next_fails_keeps_completed(self):
        """完成成功但下一期建立失敗 > 原任務維持完成，顯示「下一期」錯誤，仍可復原"""
        # Given: 新增會失敗
        task = make_task(recur=parse("每天"))
        app = FakeApp([task], FakeGoogle(fail={"add_task"}))
        # When: 勾選完成
        app.complete(task)
        # Then: 不回滾；最後一則狀態為紅字「下一期」警告，且復原按鈕仍在（未被後續狀態蓋掉）
        self.assertEqual(["complete_task", "add_task"], app.g.names())
        self.assertEqual([], app.tasks)
        last_text, last_error = app.statuses[-1]
        self.assertIn("下一期", last_text)
        self.assertTrue(last_error)
        self.assertEqual([task], app.undo_group)
        self.assertEqual([], app.undo_spawned)

    def test_undo_deletes_next(self):
        """完成週期任務後按復原 > 原任務恢復未完成，新建的下一期被刪除"""
        # Given: 已完成並產生下一期
        task = make_task(recur=parse("每天"))
        app = FakeApp([task])
        app.complete(task)
        # When: 按復原
        app.undo_complete()
        # Then: 恢復原任務、刪除 new1
        self.assertIn(("uncomplete_task", ("L1", "t1"), {}), app.g.calls)
        self.assertIn(("delete_task", ("L1", "new1"), {}), app.g.calls)
        self.assertEqual(["t1"], [t["id"] for t in app.tasks])

    def test_undo_delete_fails(self):
        """復原時刪除下一期失敗 > 原任務仍恢復，顯示錯誤"""
        # Given: 已完成並產生下一期，刪除會失敗
        task = make_task(recur=parse("每天"))
        app = FakeApp([task], FakeGoogle(fail={"delete_task"}))
        app.complete(task)
        # When: 按復原
        app.undo_complete()
        # Then: 原任務在畫面上，有錯誤
        self.assertIn("t1", [t["id"] for t in app.tasks])
        self.assertEqual(1, len(app.errors))


class GoogleAddTaskFormatTest(unittest.TestCase):

    def test_due_format(self):
        """新增任務帶日期 > due 送出 YYYY-MM-DDT00:00:00.000Z"""
        # Given: 不經過 __init__ 的 Google，攔截 api 呼叫
        google = object.__new__(daynote.Google)
        sent = []
        google.api = lambda method, url, params=None, body=None: sent.append(body) or {}
        # When: 新增任務
        google.add_task("L1", "繳費", D(2026, 10, 6), notes="🔁 每天")
        # Then: 日期格式正確
        self.assertEqual({"title": "繳費", "notes": "🔁 每天", "due": "2026-10-06T00:00:00.000Z"}, sent[0])


# ---------------------------------------------------------------- F. 詳細頁編輯

class _Field:
    def __init__(self, value):
        self.value = value

    def get(self, *args):
        return self.value


class FakeDetail:
    _detail_changes = daynote.App._detail_changes
    _set_detail_due = daynote.App._set_detail_due
    _set_detail_time = daynote.App._set_detail_time

    def __init__(self, task, title=None, notes=None, **detail):
        self.detail = {"task": task, "due": task["due"], "time": task["time"], "recur": task["recur"], **detail}
        self.detail_title = _Field(task["title"] if title is None else title)
        self.detail_notes = _Field(task["notes"] if notes is None else notes)

    def _rerender_detail(self):
        pass

    def set_recur(self, rule):
        method = getattr(daynote.App, "_set_detail_recur", None)
        if method is None:
            raise AssertionError("尚未實作 App._set_detail_recur")
        method(self, rule)


class DetailEditTest(unittest.TestCase):

    def test_set_recur_without_due(self):
        """沒有日期的任務設定 🔁 > 日期自動設為今天"""
        # Given: 無日期任務
        view = FakeDetail(make_task(due=None))
        # When: 設定每天
        view.set_recur(parse("每天"))
        # Then: 日期為今天
        self.assertEqual(dt.date.today(), view.detail["due"])
        self.assertEqual(parse("每天"), view.detail["recur"])

    def test_clear_recur_keeps_due(self):
        """取消 🔁 > 規則清除，日期保留"""
        # Given: 週期任務
        view = FakeDetail(make_task(recur=parse("每天")))
        # When: 設為不重複
        view.set_recur(None)
        # Then: 規則為空、日期不變
        self.assertIsNone(view.detail["recur"])
        self.assertEqual(D(2026, 10, 5), view.detail["due"])

    def test_clear_due_clears_recur(self):
        """有 🔁 的任務清除日期 > 規則一併清除（比照 ⏰）"""
        # Given: 有時間與週期的任務
        view = FakeDetail(make_task(time=T(9, 0), recur=parse("每天")))
        # When: 日期設為無
        view._set_detail_due(None)
        # Then: 時間與規則皆清除
        self.assertIsNone(view.detail["time"])
        self.assertIsNone(view.detail["recur"])

    def test_change_recur_only(self):
        """只修改規則 > 只送出 notes"""
        # Given: 原本每天
        view = FakeDetail(make_task(time=T(9, 0), recur=parse("每天")))
        view.detail["recur"] = parse("每週一")
        # When: 計算差異
        result = view._detail_changes()
        # Then: 只有 notes
        self.assertEqual({"notes": "⏰ 09:00\n🔁 每週一\n帳單"}, result)

    def test_unchanged_no_patch(self):
        """原始 notes 為 🔁 在前的順序，打開後未修改 > 不送任何 PATCH"""
        # Given: 由手機順序的 notes 解析出的任務
        rule_time, rule, body = need("split_meta")("🔁 每天\n⏰ 09:00\n帳單")
        view = FakeDetail(make_task(time=rule_time, recur=rule, notes=body))
        # When: 計算差異
        result = view._detail_changes()
        # Then: 無變更
        self.assertEqual({}, result)

    def test_body_change_writes_canonical(self):
        """原始為手機順序，只改內文 > 送出標準順序的 notes"""
        # Given: 手機順序的任務
        rule_time, rule, body = need("split_meta")("🔁 每天\n⏰ 09:00\n帳單")
        view = FakeDetail(make_task(time=rule_time, recur=rule, notes=body), notes="新帳單")
        # When: 計算差異
        result = view._detail_changes()
        # Then: ⏰ 在前
        self.assertEqual({"notes": "⏰ 09:00\n🔁 每天\n新帳單"}, result)

    def test_remove_recur(self):
        """移除規則 > 🔁 行刪除，⏰ 與內文保留"""
        # Given: 有時間與週期
        view = FakeDetail(make_task(time=T(9, 0), recur=parse("每天")))
        view.detail["recur"] = None
        # When: 計算差異
        result = view._detail_changes()
        # Then: 只剩時間與內文
        self.assertEqual({"notes": "⏰ 09:00\n帳單"}, result)


# ---------------------------------------------------------------- G. 顯示與讀取

class FakeLoader:
    """借用 App._load；remote 為 Google 上的任務，backfill_since 為上次補建檢查的時間點。"""

    _load = daynote.App._load

    def __init__(self, remote, backfill_since=None, now=dt.datetime(2026, 10, 5, 15, 0), fail=()):
        self.g = FakeGoogle(fail=fail, remote=remote)
        self.backfill_since = backfill_since
        self.now = now

    def _now(self):
        return self.now

    def _load_holidays(self, start, end):
        return {}


class LoadAndDisplayTest(unittest.TestCase):

    def test_load_parses_recur(self):
        """同步讀到帶 ⏰ 與 🔁 的任務 > 拆出時間、規則，notes 只留內文"""
        # Given: Google 上的任務
        raw = {"id": "t1", "title": "繳費", "notes": "⏰ 09:00\n🔁 每月15日\n帳單",
               "due": "2026-10-15T00:00:00.000Z", "position": "001"}
        loader = FakeLoader([raw])
        # When: 讀取
        result = loader._load((2026, 10))
        # Then: 欄位正確
        task = result[2][0]
        self.assertEqual((T(9, 0), parse("每月15日"), "帳單"), (task["time"], task["recur"], task["notes"]))

    def test_load_legacy_without_recur(self):
        """同步讀到舊格式任務 > recur 為 None，其餘不變"""
        # Given: 只有 ⏰ 的任務
        raw = {"id": "t1", "title": "開會", "notes": "⏰ 15:00\n會議室", "due": "2026-10-05T00:00:00.000Z"}
        loader = FakeLoader([raw])
        # When: 讀取
        result = loader._load((2026, 10))
        # Then: 沒有規則
        task = result[2][0]
        self.assertEqual((T(15, 0), None, "會議室"), (task["time"], task.get("recur", "缺少 recur 欄位"), task["notes"]))

    def test_subtitle(self):
        """列表副標題 > 依時間、週期、逾期狀態組成"""
        today = D(2026, 10, 5)
        cases = [
            ("一般", make_task(), "工作"),
            ("有時間", make_task(time=T(9, 0)), "⏰ 09:00 · 工作"),
            ("有週期", make_task(recur=parse("每週一")), "🔁 每週一 · 工作"),
            ("時間加週期", make_task(time=T(9, 0), recur=parse("每天")), "⏰ 09:00 · 🔁 每天 · 工作"),
            ("逾期", make_task(due=D(2026, 10, 1), time=T(9, 0)), "已逾期 · 10月1日 09:00 · 工作"),
            ("逾期加週期", make_task(due=D(2026, 10, 1), recur=parse("每天")), "已逾期 · 10月1日 · 🔁 每天 · 工作"),
        ]
        for name, task, expected in cases:
            with self.subTest(name=name):
                # Given: 不同狀態的任務
                # When: 產生副標題
                result = need("task_subtitle")(task, today)
                # Then: 文字正確
                self.assertEqual(expected, result)


# ---------------------------------------------------------------- S. 略過這一期

def weekly_report(**overrides):
    """「🔁 每週一 交週報」，原定 10/12（週一）；今天預設 10/5。"""
    return make_task(**{"title": "交週報", "due": D(2026, 10, 12), "recur": parse("每週一"), **overrides})


class CanSkipTest(unittest.TestCase):

    def test_visibility(self):
        """「略過這一期」按鈕 > 只對有 🔁 的父工作顯示"""
        cases = [("週期父工作", lambda: make_task(recur=parse("每天")), True),
                 ("一般任務", lambda: make_task(), False),
                 ("週期子工作", lambda: make_task(parent="p1", recur=parse("每天")), False)]
        for name, build, expected in cases:
            with self.subTest(name=name):
                # Given: 不同類型的任務
                task = build()
                # When: 判斷是否可略過
                result = need("can_skip")(task)
                # Then: 只有週期父工作可略過
                self.assertIs(expected, result)


class SkipFlowTest(unittest.TestCase):

    def test_skip_creates_next_then_deletes_current(self):
        """略過 10/12 的每週一任務 > 先建立 10/19 再刪除 10/12，畫面只剩下一期"""
        # Given: 原定 10/12 的週報
        task = weekly_report(time=T(9, 0))
        app = FakeApp([task])
        # When: 略過這一期
        app.skip(task)
        # Then: 順序為新增後刪除，內容與完成產生的下一期相同
        self.assertEqual(["add_task", "delete_task"], app.g.names())
        _, args, kwargs = app.g.calls[0]
        self.assertEqual(("L1", "交週報", D(2026, 10, 19)), args)
        self.assertEqual({"parent": None, "notes": "⏰ 09:00\n🔁 每週一\n帳單"}, kwargs)
        self.assertEqual(("delete_task", ("L1", "t1"), {}), app.g.calls[1])
        self.assertEqual([("new1", D(2026, 10, 19))], [(t["id"], t["due"]) for t in app.tasks])

    def test_skip_leaves_no_completed_record(self):
        """略過 > 不送出 complete_task，Google 已完成清單不會多一筆"""
        # Given: 週報
        task = weekly_report()
        app = FakeApp([task])
        # When: 略過這一期
        app.skip(task)
        # Then: 沒有完成呼叫
        self.assertNotIn("complete_task", app.g.names())

    def test_skip_overdue_daily(self):
        """每天的任務過期（10/1）在 10/5 略過 > 下一期為今天 10/5"""
        # Given: 過期的每日任務
        task = make_task(due=D(2026, 10, 1), recur=parse("每天"))
        app = FakeApp([task])
        # When: 略過這一期
        app.skip(task)
        # Then: 下一期為今天
        self.assertEqual(D(2026, 10, 5), app.g.calls[0][1][2])

    def test_skip_twice_in_a_row(self):
        """略過 10/12 後再略過產生的 10/19 > 下一期為 10/26"""
        # Given: 已略過一次
        app = FakeApp([weekly_report()])
        app.skip(app.tasks[0])
        spawned = app.tasks[0]
        # When: 再略過下一期
        app.skip(spawned)
        # Then: 再往後一週
        self.assertEqual([D(2026, 10, 26)], [t["due"] for t in app.tasks])

    def test_skip_monthly_31(self):
        """每月31日，1/31 略過 > 下一期為 2/28"""
        # Given: 2026/1/31 的月底任務
        task = make_task(due=D(2026, 1, 31), recur=parse("每月31日"))
        app = FakeApp([task], today=D(2026, 1, 31))
        # When: 略過這一期
        app.skip(task)
        # Then: 2/28
        self.assertEqual(D(2026, 2, 28), app.g.calls[0][1][2])

    def test_skip_status_message(self):
        """略過成功 > 狀態列顯示「已略過 10/12，下一期 10/19」"""
        # Given: 週報
        task = weekly_report()
        app = FakeApp([task])
        # When: 略過這一期
        app.skip(task)
        # Then: 提示文字正確
        self.assertIn(("已略過 10/12，下一期 10/19", False), app.statuses)

    def test_skip_non_recurring_ignored(self):
        """一般任務或子工作呼叫略過 > 不送出任何 API"""
        cases = (("一般任務", lambda: make_task()),
                 ("子工作", lambda: make_task(parent="p1", recur=parse("每天"))))
        for name, build in cases:
            with self.subTest(name=name):
                # Given: 不可略過的任務
                task = build()
                app = FakeApp([task])
                # When: 呼叫略過
                app.skip(task)
                # Then: 沒有任何呼叫，任務還在
                self.assertEqual([], app.g.calls)
                self.assertEqual([task], app.tasks)

    def test_skip_detail_uses_edits_without_saving(self):
        """詳細頁改了標題與規則未儲存就按略過 > 修改只套用在下一期，不對這一期送出 PATCH（R6）"""
        # Given: 詳細頁有未儲存的修改
        task = weekly_report()
        app = FakeApp([task])
        app.detail = {"task": task}
        app.pending_fields = {"title": "交月報", "notes": "🔁 每月15日\n帳單"}
        # When: 在詳細頁按略過
        app.skip_detail()
        # Then: 關閉詳細頁但不儲存；下一期用新標題與新規則（10/12 之後的 15 日）；沒有 update_task
        self.assertEqual([False], app.closed_detail)
        self.assertNotIn("update_task", app.g.names())
        add = next(c for c in app.g.calls if c[0] == "add_task")
        self.assertEqual(("L1", "交月報", D(2026, 10, 15)), add[1])
        self.assertEqual("🔁 每月15日\n帳單", add[2]["notes"])

    def test_skip_with_children_confirmed(self):
        """有 2 個子工作的父工作略過並確認 > 刪除子工作與父工作，下一期不複製子工作"""
        # Given: 週報底下有 2 個子工作
        task = weekly_report(id="p1")
        children = [make_task(id="c1", parent="p1", position="1"), make_task(id="c2", parent="p1", position="2")]
        app = FakeApp([task, *children])
        with unittest.mock.patch.object(daynote.messagebox, "askyesno", return_value=True) as ask:
            # When: 略過並在確認框按是
            app.skip(task)
        # Then: 確認框提到子工作；新增 1 筆後刪除 3 筆（先子後父）
        self.assertIn("子工作", ask.call_args[0][1])
        self.assertEqual(["add_task", "delete_task", "delete_task", "delete_task"], app.g.names())
        self.assertEqual(["c1", "c2", "p1"], [c[1][1] for c in app.g.calls[1:]])
        self.assertEqual(["new1"], [t["id"] for t in app.tasks])

    def test_skip_with_children_cancelled(self):
        """有子工作的父工作略過但在確認框按否 > 不送出任何 API，畫面不變"""
        # Given: 有子工作的週報
        task = weekly_report(id="p1")
        child = make_task(id="c1", parent="p1")
        app = FakeApp([task, child])
        with unittest.mock.patch.object(daynote.messagebox, "askyesno", return_value=False):
            # When: 略過並在確認框按否
            app.skip(task)
        # Then: 什麼都沒發生
        self.assertEqual([], app.g.calls)
        self.assertEqual([task, child], app.tasks)

    def test_skip_without_children_no_confirm(self):
        """沒有子工作的任務略過 > 不跳確認框，直接執行"""
        # Given: 沒有子工作的週報
        task = weekly_report()
        app = FakeApp([task])
        with unittest.mock.patch.object(daynote.messagebox, "askyesno") as ask:
            # When: 略過這一期
            app.skip(task)
        # Then: 未詢問
        ask.assert_not_called()

    def test_skip_double_click(self):
        """連點兩次略過 > 只建立一次下一期"""
        # Given: 已略過一次
        task = weekly_report()
        app = FakeApp([task])
        app.skip(task)
        # When: 對同一筆再按一次
        app.skip(task)
        # Then: 只新增一次
        self.assertEqual(1, app.g.names().count("add_task"))

    def test_skip_add_fails(self):
        """略過時建立下一期失敗 > 不刪除這一期，畫面不變並顯示錯誤"""
        # Given: 新增會失敗
        task = weekly_report()
        app = FakeApp([task], FakeGoogle(fail={"add_task"}))
        # When: 略過這一期
        app.skip(task)
        # Then: 沒有刪除，任務還在，有錯誤
        self.assertNotIn("delete_task", app.g.names())
        self.assertEqual([task], app.tasks)
        self.assertTrue(app.errors or any(error for _, error in app.statuses))

    def test_skip_delete_fails(self):
        """略過時下一期建立成功但刪除這一期失敗 > 兩筆都留著，提示手動刪除"""
        # Given: 刪除會失敗
        task = weekly_report()
        app = FakeApp([task], FakeGoogle(fail={"delete_task"}))
        # When: 略過這一期
        app.skip(task)
        # Then: 兩筆並存，有「手動刪除」提示
        self.assertEqual({"t1", "new1"}, {t["id"] for t in app.tasks})
        self.assertTrue(any(error and "手動刪除" in text for text, error in app.statuses))

    def test_skip_not_logged_in(self):
        """未登入時略過 > 顯示登入錯誤，畫面不變"""
        # Given: API 回報需要登入
        task = weekly_report()
        app = FakeApp([task], FakeGoogle(fail={"add_task": "NeedLogin"}))
        # When: 略過這一期
        app.skip(task)
        # Then: 錯誤交給 _show_error，任務還在
        self.assertEqual(1, len(app.errors))
        self.assertIsInstance(app.errors[0], daynote.NeedLogin)
        self.assertEqual([task], app.tasks)

    def test_skip_no_undo(self):
        """略過成功 > 不提供復原"""
        # Given: 週報
        task = weekly_report()
        app = FakeApp([task])
        # When: 略過這一期
        app.skip(task)
        # Then: 沒有設定復原群組
        self.assertIsNone(app.undo_group)


# ---------------------------------------------------------------- X. 刪除

class DeletePromptTest(unittest.TestCase):

    def test_plain_task_unchanged(self):
        """刪除一般任務 > 確認文字與現行版本相同"""
        # Given: 一般任務
        task = make_task()
        # When: 產生確認文字
        result = need("delete_prompt")(task, 0)
        # Then: 與現行文字一致
        self.assertEqual("確定要刪除「繳費」？\n\n刪除後無法在 DayNote 復原。", result)

    def test_plain_task_with_children_unchanged(self):
        """刪除有子工作的一般任務 > 確認文字與現行版本相同"""
        # Given: 一般任務
        task = make_task()
        # When: 產生確認文字（含 2 個子工作）
        result = need("delete_prompt")(task, 2)
        # Then: 與現行文字一致
        self.assertEqual("確定要刪除「繳費」？\n（含 2 個子工作）\n\n刪除後無法在 DayNote 復原。", result)

    def test_recurring_task(self):
        """刪除重複任務 > 提示規則、不會再產生下一期、引導改用略過"""
        # Given: 週報
        task = weekly_report()
        # When: 產生確認文字
        result = need("delete_prompt")(task, 0)
        # Then: 三個重點都在
        for keyword in ("🔁 每週一", "不會再產生下一期", "略過這一期"):
            self.assertIn(keyword, result)

    def test_recurring_task_with_children(self):
        """刪除有子工作的重複任務 > 同時提示週期與子工作數量"""
        # Given: 週報
        task = weekly_report()
        # When: 產生確認文字（含 2 個子工作）
        result = need("delete_prompt")(task, 2)
        # Then: 兩者都有
        self.assertIn("不會再產生下一期", result)
        self.assertIn("（含 2 個子工作）", result)


class DeleteFlowTest(unittest.TestCase):

    def test_delete_recurring_ends_series(self):
        """確認刪除重複任務 > 只刪除，不建立下一期"""
        # Given: 詳細頁開著週報
        task = weekly_report()
        app = FakeApp([task])
        app.detail = {"task": task}
        with unittest.mock.patch.object(daynote.messagebox, "askyesno", return_value=True) as ask:
            # When: 按刪除並確認
            app.delete_detail()
        # Then: 只有 delete_task，確認框使用重複任務的文字
        self.assertEqual(["delete_task"], app.g.names())
        self.assertEqual([], app.tasks)
        self.assertIn("不會再產生下一期", ask.call_args[0][1])

    def test_undo_after_next_deleted(self):
        """完成後把下一期刪掉再按復原 > 原任務恢復，404 視為成功不顯示錯誤"""
        # Given: 完成產生下一期，之後下一期在 Google 上已不存在
        task = make_task(recur=parse("每天"))
        app = FakeApp([task], FakeGoogle(fail={"delete_task": "NotFound"}))
        app.complete(task)
        # When: 按復原
        app.undo_complete()
        # Then: 原任務回來、沒有錯誤
        self.assertEqual(["t1"], [t["id"] for t in app.tasks])
        self.assertEqual([], app.errors)


class NotFoundTest(unittest.TestCase):

    def test_404_raises_not_found(self):
        """Google API 回 404 > 丟出 NotFound（ApiError 的子類別）"""
        # Given: _http 固定回 404
        google = object.__new__(daynote.Google)
        google._ensure_access = lambda: None
        google.access_token = "<ACCESS_TOKEN>"
        google._http = lambda *args, **kwargs: (404, {}, {"error": {"message": "Not Found"}})
        not_found = need("NotFound")
        # When: 呼叫 API
        with self.assertRaises(not_found) as ctx:
            google.api("DELETE", "https://example.com/tasks/x")
        # Then: 仍是 ApiError，舊的錯誤處理照常運作
        self.assertIsInstance(ctx.exception, daynote.ApiError)


# ---------------------------------------------------------------- R1. 防重複

class FindSuccessorTest(unittest.TestCase):
    """純函式 find_successor(candidates, task, due, include_deleted=False)。"""

    def setUp(self):
        self.task = make_task(recur=parse("每天"))

    def find(self, candidates, include_deleted=False):
        return need("find_successor")(candidates, self.task, D(2026, 10, 6), include_deleted)

    def test_open_successor_found(self):
        """同標題、同規則、同日期的未完成任務 > 視為已存在的下一期"""
        # Given: Google 上已有下一期
        candidate = raw_task("x9", "繳費", D(2026, 10, 6), "🔁 每天\n帳單")
        # When: 尋找下一期
        result = self.find([candidate])
        # Then: 找到
        self.assertEqual("x9", result["id"])

    def test_completed_successor_found(self):
        """下一期已被完成 > 仍視為已存在"""
        # Given: 下一期已完成
        candidate = raw_task("x9", "繳費", D(2026, 10, 6), "🔁 每天", status="completed")
        # When: 尋找下一期
        result = self.find([candidate])
        # Then: 找到
        self.assertEqual("x9", result["id"])

    def test_deleted_successor_depends_on_flag(self):
        """下一期已被刪除 > 互動操作不算、同步補建才算"""
        # Given: 已刪除的下一期
        candidate = raw_task("x9", "繳費", D(2026, 10, 6), "🔁 每天", deleted=True)
        # When: 分別以兩種模式尋找
        interactive, backfill = self.find([candidate]), self.find([candidate], include_deleted=True)
        # Then: 互動找不到、補建找得到
        self.assertIsNone(interactive)
        self.assertEqual("x9", backfill["id"])

    def test_not_matching(self):
        """標題、規則或日期不同，或是子工作 > 不算下一期"""
        cases = {
            "標題不同": raw_task("x1", "繳電費", D(2026, 10, 6), "🔁 每天"),
            "沒有規則": raw_task("x2", "繳費", D(2026, 10, 6), "帳單"),
            "規則不同": raw_task("x3", "繳費", D(2026, 10, 6), "🔁 每週二"),
            "日期不同": raw_task("x4", "繳費", D(2026, 10, 7), "🔁 每天"),
            "是子工作": raw_task("x5", "繳費", D(2026, 10, 6), "🔁 每天", parent="p9"),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                # Given: 不符合的候選
                # When: 尋找下一期
                result = self.find([candidate])
                # Then: 找不到
                self.assertIsNone(result)

    def test_original_itself_excluded(self):
        """候選就是原任務本身 > 不算下一期"""
        # Given: 原任務 id 與候選相同
        candidate = raw_task("t1", "繳費", D(2026, 10, 6), "🔁 每天")
        # When: 尋找下一期
        result = self.find([candidate])
        # Then: 找不到
        self.assertIsNone(result)


class DedupeFlowTest(unittest.TestCase):

    def test_complete_successor_exists(self):
        """另一台電腦已完成並建立下一期，這台再按完成 > 不重複建立，復原也不會刪掉別人建的下一期"""
        # Given: Google 上已有 10/6 的下一期
        task = make_task(recur=parse("每天"))
        remote = [raw_task("x9", "繳費", D(2026, 10, 6), "🔁 每天\n帳單")]
        app = FakeApp([task], FakeGoogle(remote=remote))
        # When: 勾選完成
        app.complete(task)
        # Then: 只完成、不新增；復原清單不含 x9
        self.assertEqual(["complete_task"], app.g.names())
        self.assertIn(("due_tasks", ("L1", D(2026, 10, 6))), app.g.reads)
        self.assertEqual([], app.undo_spawned)

    def test_complete_after_undo_recreates(self):
        """完成→復原（下一期已刪）→再完成 > 已刪除的下一期不算，重新建立"""
        # Given: Google 上只有已刪除的 10/6
        task = make_task(recur=parse("每天"))
        remote = [raw_task("new1", "繳費", D(2026, 10, 6), "🔁 每天\n帳單", deleted=True)]
        app = FakeApp([task], FakeGoogle(remote=remote))
        # When: 勾選完成
        app.complete(task)
        # Then: 會新增
        self.assertEqual(["complete_task", "add_task"], app.g.names())

    def test_skip_already_skipped_elsewhere(self):
        """另一台電腦已略過（這一期已刪、下一期已建），這台再按略過 > 不新增，刪除回 404 視為成功"""
        # Given: 下一期已存在，這一期在 Google 上已不存在
        task = weekly_report()
        remote = [raw_task("x9", "交週報", D(2026, 10, 19), "🔁 每週一\n帳單")]
        app = FakeApp([task], FakeGoogle(fail={"delete_task": "NotFound"}, remote=remote))
        # When: 略過這一期
        app.skip(task)
        # Then: 沒有新增、沒有錯誤、這一期從畫面移除
        self.assertNotIn("add_task", app.g.names())
        self.assertEqual([], app.errors)
        self.assertFalse(any(error for _, error in app.statuses))
        self.assertNotIn("t1", [t["id"] for t in app.tasks])

    def test_dedupe_query_fails(self):
        """防重複查詢失敗 > 視同建立下一期失敗（不冒險建立可能重複的任務）"""
        # Given: due_tasks 會失敗
        task = make_task(recur=parse("每天"))
        app = FakeApp([task], FakeGoogle(fail={"due_tasks"}))
        # When: 勾選完成
        app.complete(task)
        # Then: 已完成、沒有新增、紅字警告與復原並存
        self.assertEqual(["complete_task"], app.g.names())
        self.assertTrue(app.statuses[-1][1])
        self.assertEqual([task], app.undo_group)


# ---------------------------------------------------------------- R2. 提醒時間已過

class SpawnReminderTest(unittest.TestCase):

    def test_complete_overdue_time_passed(self):
        """每天 09:00 的任務過期，15:00 補完成 > 今天這一期記為已提醒，不跳出"""
        # Given: 昨天的每日任務，現在 10/5 15:00
        task = make_task(due=D(2026, 10, 4), time=T(9, 0), recur=parse("每天"))
        app = FakeApp([task], now=dt.datetime(2026, 10, 5, 15, 0))
        # When: 勾選完成
        app.complete(task)
        # Then: new1 的今天 09:00 提醒已標記並存檔
        self.assertIn("new1@2026-10-05T09:00", app.reminded)
        self.assertGreaterEqual(app.reminder_saves, 1)

    def test_complete_future_time_not_suppressed(self):
        """下一期的提醒時間還沒到 > 不標記，到時會正常跳提醒"""
        # Given: 今天的每日任務，現在 08:00
        task = make_task(time=T(9, 0), recur=parse("每天"))
        app = FakeApp([task], now=dt.datetime(2026, 10, 5, 8, 0))
        # When: 勾選完成
        app.complete(task)
        # Then: 沒有標記
        self.assertEqual(set(), app.reminded)

    def test_skip_overdue_time_passed(self):
        """略過過期的每日任務，提醒時間已過 > 同樣不跳出"""
        # Given: 昨天的每日任務，現在 10/5 15:00
        task = make_task(due=D(2026, 10, 4), time=T(9, 0), recur=parse("每天"))
        app = FakeApp([task], now=dt.datetime(2026, 10, 5, 15, 0))
        # When: 略過這一期
        app.skip(task)
        # Then: 已標記
        self.assertIn("new1@2026-10-05T09:00", app.reminded)

    def test_no_time_nothing_marked(self):
        """下一期沒有 ⏰ > 不標記任何提醒"""
        # Given: 沒有時間的每日任務
        task = make_task(due=D(2026, 10, 4), recur=parse("每天"))
        app = FakeApp([task], now=dt.datetime(2026, 10, 5, 15, 0))
        # When: 勾選完成
        app.complete(task)
        # Then: 沒有標記
        self.assertEqual(set(), app.reminded)


# ---------------------------------------------------------------- R3. 同步補建（手機上勾選完成）

class BackfillTest(unittest.TestCase):

    SINCE = dt.datetime(2026, 10, 5, 12, 0)

    def load(self, remote, since=SINCE, fail=()):
        loader = FakeLoader(remote, backfill_since=since, fail=fail)
        return loader, loader._load((2026, 10))

    def test_phone_completed_creates_next(self):
        """手機上完成 🔁 每天的任務 > 同步時補建下一期，並出現在清單中"""
        # Given: 上次檢查後在手機完成的 10/5 任務
        remote = [raw_task("t1", "繳費", D(2026, 10, 5), "⏰ 09:00\n🔁 每天\n帳單", status="completed")]
        # When: 同步
        loader, result = self.load(remote)
        # Then: 補建 10/6，清單含新任務，檢查時間點前進到現在
        self.assertEqual([("add_task", ("L1", "繳費", D(2026, 10, 6)),
                           {"parent": None, "notes": "⏰ 09:00\n🔁 每天\n帳單"})], loader.g.calls)
        self.assertEqual([("new1", D(2026, 10, 6), parse("每天"))],
                         [(t["id"], t["due"], t["recur"]) for t in result[2]])
        self.assertIn(("completed_since", ("L1", self.SINCE)), loader.g.reads)
        self.assertEqual(loader.now, backfill_mark(result))

    def test_successor_already_open(self):
        """已完成的任務已有下一期（例如在 DayNote 完成的） > 不補建"""
        # Given: 已完成的 10/5 與未完成的 10/6
        remote = [raw_task("t1", "繳費", D(2026, 10, 5), "🔁 每天", status="completed"),
                  raw_task("t2", "繳費", D(2026, 10, 6), "🔁 每天")]
        # When: 同步
        loader, _ = self.load(remote)
        # Then: 有檢查已完成任務，但沒有新增
        self.assertIn("completed_since", [r[0] for r in loader.g.reads])
        self.assertEqual([], loader.g.calls)

    def test_successor_deleted_means_series_ended(self):
        """使用者刪掉了下一期（結束週期） > 不補建"""
        # Given: 已完成的 10/5 與已刪除的 10/6
        remote = [raw_task("t1", "繳費", D(2026, 10, 5), "🔁 每天", status="completed"),
                  raw_task("t2", "繳費", D(2026, 10, 6), "🔁 每天", deleted=True)]
        # When: 同步
        loader, _ = self.load(remote)
        # Then: 有檢查已完成任務，但沒有新增
        self.assertIn("completed_since", [r[0] for r in loader.g.reads])
        self.assertEqual([], loader.g.calls)

    def test_successor_also_completed(self):
        """下一期也已經完成了 > 不補建"""
        # Given: 10/5 與 10/6 都已完成
        remote = [raw_task("t1", "繳費", D(2026, 10, 5), "🔁 每天", status="completed"),
                  raw_task("t2", "繳費", D(2026, 10, 6), "🔁 每天", status="completed")]
        # When: 同步
        loader, _ = self.load(remote)
        # Then: 只補建 10/7（t2 的下一期），t1 不補
        self.assertEqual([D(2026, 10, 7)], [c[1][2] for c in loader.g.calls])

    def test_non_recurring_or_subtask_ignored(self):
        """已完成的一般任務或子工作 > 不補建"""
        # Given: 沒有 🔁 的任務與帶 🔁 的子工作
        remote = [raw_task("t1", "繳費", D(2026, 10, 5), "帳單", status="completed"),
                  raw_task("c1", "子項", D(2026, 10, 5), "🔁 每天", status="completed", parent="p1")]
        # When: 同步
        loader, _ = self.load(remote)
        # Then: 有檢查已完成任務，但沒有新增
        self.assertIn("completed_since", [r[0] for r in loader.g.reads])
        self.assertEqual([], loader.g.calls)

    def test_first_sync_no_backfill(self):
        """第一次同步（沒有檢查時間點） > 不回溯歷史，只記錄現在"""
        # Given: 以前完成的 🔁 任務
        remote = [raw_task("t1", "繳費", D(2026, 9, 1), "🔁 每天", status="completed")]
        # When: 第一次同步
        loader, result = self.load(remote, since=None)
        # Then: 沒有查詢已完成、沒有新增，時間點設為現在
        self.assertNotIn("completed_since", [r[0] for r in loader.g.reads])
        self.assertEqual([], loader.g.calls)
        self.assertEqual(loader.now, backfill_mark(result))

    def test_add_fails_retry_later(self):
        """補建失敗 > 同步照常完成，檢查時間點不前進（下次重試）"""
        # Given: 新增會失敗
        remote = [raw_task("t1", "繳費", D(2026, 10, 5), "🔁 每天", status="completed")]
        # When: 同步
        loader, result = self.load(remote, fail={"add_task"})
        # Then: 同步結果正常回傳，時間點維持原值
        self.assertEqual(self.SINCE, backfill_mark(result))

    def test_completed_query_fails(self):
        """查詢已完成任務失敗 > 同步照常完成，檢查時間點不前進"""
        # Given: completed_since 會失敗
        remote = [raw_task("t2", "開會", D(2026, 10, 5), "會議室")]
        # When: 同步
        loader, result = self.load(remote, fail={"completed_since"})
        # Then: 一般任務照常讀到，時間點維持原值
        self.assertEqual(["t2"], [t["id"] for t in result[2]])
        self.assertEqual(self.SINCE, backfill_mark(result))

    def test_overdue_phone_completion(self):
        """手機上補完成 7/15 的每月任務 > 補建今天以後的第一期 10/15"""
        # Given: 過期很久才在手機完成
        remote = [raw_task("t1", "繳費", D(2026, 7, 15), "🔁 每月15日", status="completed")]
        # When: 同步
        loader, _ = self.load(remote)
        # Then: 補建 10/15
        self.assertEqual([D(2026, 10, 15)], [c[1][2] for c in loader.g.calls])


def google_method(name):
    """取得 Google 上待實作的方法；尚未實作時讓該案例個別失敗。"""
    method = getattr(daynote.Google, name, None)
    if method is None:
        raise AssertionError(f"尚未實作 Google.{name}")
    return method


def backfill_mark(result):
    """_load 回傳的第 6 個值：下次同步補建的檢查時間點。"""
    if len(result) < 6:
        raise AssertionError("尚未實作 _load 回傳補建檢查時間點")
    return result[5]


class GoogleQueryTest(unittest.TestCase):
    """新增的兩個查詢必須帶對參數，否則手機完成或已刪除的任務會查不到。"""

    def google(self):
        google = object.__new__(daynote.Google)
        google.sent = []
        google.api = lambda method, url, params=None, body=None: google.sent.append((method, params)) or {}
        return google

    def test_due_tasks_params(self):
        """防重複查詢 > 帶 showCompleted、showHidden、showDeleted 與該日的 dueMin"""
        # Given: 攔截 api 的 Google
        google = self.google()
        # When: 查詢 10/6 到期的任務
        google_method("due_tasks")(google, "L1", D(2026, 10, 6))
        # Then: 參數正確
        method, params = google.sent[0]
        self.assertEqual("GET", method)
        for flag in ("showCompleted", "showHidden", "showDeleted"):
            self.assertEqual("true", params[flag])
        # 2026-10-05 實測：dueMax 不含邊界；[當日 00:00Z, 隔日 00:00Z) 剛好只回傳當日任務
        self.assertEqual(("2026-10-06T00:00:00.000Z", "2026-10-07T00:00:00.000Z"),
                         (params["dueMin"], params["dueMax"]))

    def test_completed_since_params(self):
        """同步補建查詢 > 帶 showCompleted、showHidden 與 completedMin"""
        # Given: 攔截 api 的 Google
        google = self.google()
        since = dt.datetime(2026, 10, 5, 12, 0)
        # When: 查詢之後完成的任務
        google_method("completed_since")(google, "L1", since)
        # Then: 參數正確
        _, params = google.sent[0]
        self.assertEqual(("true", "true"), (params["showCompleted"], params["showHidden"]))
        self.assertEqual(since.astimezone().isoformat(), params["completedMin"])


# ---------------------------------------------------------------- R5. 同步競態

class RefreshRaceTest(unittest.TestCase):

    def inject_spawned(self, app):
        """模擬同步在建立下一期之後才讀取，已把 new1 放進畫面。"""
        def sync():
            if "new1" not in [t["id"] for t in app.tasks]:
                app.tasks.append(make_task(id="new1", due=D(2026, 10, 6)))
        app.before_done = sync

    def test_complete_no_duplicate(self):
        """完成時同步已把下一期讀進畫面 > 畫面上只有一筆下一期"""
        # Given: 同步搶先讀到 new1
        task = make_task(recur=parse("每天"))
        app = FakeApp([task])
        self.inject_spawned(app)
        # When: 勾選完成
        app.complete(task)
        # Then: new1 只出現一次
        self.assertEqual(1, [t["id"] for t in app.tasks].count("new1"))

    def test_skip_no_duplicate(self):
        """略過時同步已把下一期讀進畫面 > 畫面上只有一筆下一期"""
        # Given: 同步搶先讀到 new1
        task = make_task(recur=parse("每天"))
        app = FakeApp([task])
        self.inject_spawned(app)
        # When: 略過這一期
        app.skip(task)
        # Then: new1 只出現一次
        self.assertEqual(1, [t["id"] for t in app.tasks].count("new1"))


# ---------------------------------------------------------------- R6. 套用詳細頁修改（純函式）

class ApplyFieldsTest(unittest.TestCase):

    def test_apply_all_fields(self):
        """API 欄位格式的修改 > 套用到任務的標題、時間、規則、內文與日期"""
        # Given: 原任務與修改
        task = make_task()
        fields = {"title": "交月報", "notes": "⏰ 10:00\n🔁 每月15日\n新內容", "due": "2026-10-15T00:00:00.000Z"}
        # When: 套用
        need("apply_fields")(task, fields)
        # Then: 欄位皆更新
        self.assertEqual(("交月報", T(10, 0), parse("每月15日"), "新內容", D(2026, 10, 15)),
                         (task["title"], task["time"], task["recur"], task["notes"], task["due"]))

    def test_clear_due(self):
        """due 為 None > 日期清除"""
        # Given: 有日期的任務
        task = make_task()
        # When: 套用清除日期
        need("apply_fields")(task, {"due": None})
        # Then: 無日期
        self.assertIsNone(task["due"])


if __name__ == "__main__":
    unittest.main()
