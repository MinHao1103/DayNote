"""網址辨識（find_urls）與行程細節（event_info、meeting_link）單元測試。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

規格決策：
- 只認 http／https 網址；結尾的中英文標點不算網址
- 會議連結只接受 https：Google Meet 欄位優先，其次是地點與說明裡的 Teams／Meet／Zoom／Webex 網址
- 行程說明可能是 HTML：去掉標籤、還原字元，太長截斷；Google 日曆網址非 https 時不提供
"""
import datetime as dt
import unittest
import unittest.mock

from tests.test_recur import daynote


def timed_event(start, end, **extra):
    return {"summary": "專案週會", "start": {"dateTime": start.astimezone().isoformat()},
            "end": {"dateTime": end.astimezone().isoformat()}, **extra}


class FindUrlsTest(unittest.TestCase):

    def test_urls_in_text(self):
        """一段文字裡的網址 > 依序找出，去掉結尾標點，不認其他協定"""
        # Given: 中英文混合的詳細資訊
        text = ("工單：https://redmine.example.com/issues/12345。\n"
                "規格（https://docs.example.com/spec?id=7）、舊站 http://old.example.com, ftp://files.example.com")
        # When: 找網址
        urls = daynote.find_urls(text)
        # Then: 三個 http(s) 網址，不含標點與 ftp
        self.assertEqual(urls, ["https://redmine.example.com/issues/12345", "https://docs.example.com/spec?id=7",
                                "http://old.example.com"])

    def test_no_urls(self):
        """沒有網址或空白 > 空清單"""
        for text in ("買牛奶", "", None):
            with self.subTest(text=text):
                # Given / When / Then: 沒有網址
                self.assertEqual(daynote.find_urls(text), [])


class EventInfoTest(unittest.TestCase):

    def test_timed_event_with_teams(self):
        """有時間的行程、說明裡有 Teams 連結（HTML） > 時間範圍、地點、純文字說明、加入會議連結"""
        # Given: 今天 15:00–16:00 的週會
        today = dt.date.today()
        start = dt.datetime.combine(today, dt.time(15, 0))
        event = timed_event(start, start + dt.timedelta(hours=1), location="3F 會議室 A",
                            description='議程：上週進度<br>&amp; 風險<br><a href="https://teams.microsoft.com/l/meetup-join/abc">加入</a>',
                            htmlLink="https://www.google.com/calendar/event?eid=xyz")
        # When: 整理細節
        info = daynote.event_info(event)
        # Then: 各欄位
        self.assertEqual(info["when"], "今天 15:00 – 16:00")
        self.assertEqual(info["location"], "3F 會議室 A")
        self.assertEqual(info["description"], "議程：上週進度\n& 風險\n加入")
        self.assertEqual(info["link"], "https://teams.microsoft.com/l/meetup-join/abc")
        self.assertEqual(info["calendar_url"], "https://www.google.com/calendar/event?eid=xyz")

    def test_all_day_events(self):
        """單日全天 > 「全天」；跨三天 > 起訖日期加（全天）"""
        # Given: 單日與跨日（end.date 不含當天）
        one = {"summary": "員工旅遊", "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-25"}}
        three = {"summary": "出差", "start": {"date": "2026-10-24"}, "end": {"date": "2026-10-27"}}
        # When: 整理細節
        with unittest.mock.patch.object(daynote, "date_text", lambda d, short=False: f"{d.month}/{d.day}"):
            one_info, three_info = daynote.event_info(one), daynote.event_info(three)
        # Then: 時間文字
        self.assertEqual(one_info["when"], "全天")
        self.assertEqual(three_info["when"], "10/24～10/26（全天）")

    def test_unsafe_or_missing_links(self):
        """會議網址不是 https、日曆網址不是 https、沒有說明 > 都不提供"""
        # Given: 只有 http 連結的行程
        start = dt.datetime.combine(dt.date.today(), dt.time(9, 0))
        event = timed_event(start, start + dt.timedelta(minutes=30), location="http://zoom.us/j/1",
                            htmlLink="http://calendar.example.com/x")
        # When: 整理細節
        info = daynote.event_info(event)
        # Then: 沒有連結、沒有說明
        self.assertIsNone(info["link"])
        self.assertIsNone(info["calendar_url"])
        self.assertEqual(info["description"], "")

    def test_meeting_url_removed_from_text(self):
        """說明與地點直接寫 Teams 網址 > 網址做成「加入會議」，文字裡不再重複顯示"""
        # Given: 地點與說明都含同一個網址
        url = "https://teams.microsoft.com/l/meetup-join/abc"
        start = dt.datetime.combine(dt.date.today(), dt.time(10, 0))
        event = timed_event(start, start + dt.timedelta(hours=1), location=f"線上 {url}",
                            description=f"議程 {url} 請準時\n第二行")
        # When: 整理細節
        info = daynote.event_info(event)
        # Then: 文字沒有網址、換行保留
        self.assertEqual((info["location"], info["description"], info["link"]), ("線上", "議程 請準時\n第二行", url))

    def test_meet_field_preferred(self):
        """同時有 Google Meet 欄位與說明裡的 Zoom > 用 Meet"""
        # Given / When: 兩種連結都有
        link = daynote.meeting_link({"hangoutLink": "https://meet.google.com/abc-defg-hij",
                                     "description": "備用 https://zoom.us/j/999"})
        # Then: Meet
        self.assertEqual(link, "https://meet.google.com/abc-defg-hij")

    def test_long_description_truncated(self):
        """說明超過 300 字 > 截斷並加「…」"""
        # Given / When: 很長的說明
        text = daynote._plain_text("字" * 400)
        # Then: 300 字＋…
        self.assertEqual(len(text), 301)
        self.assertTrue(text.endswith("…"))


if __name__ == "__main__":
    unittest.main()
