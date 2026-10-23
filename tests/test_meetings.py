"""會議前提醒：從日曆行程挑出要提醒的會議、找出「加入會議」連結（meeting_link、upcoming_meetings）。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v
"""
import datetime as dt
import unittest

from tests.test_recur import daynote

TODAY = dt.date(2026, 10, 23)


def event(start, event_id="ev1", **extra):
    """有開始時間的行程；start 為本機時間。"""
    begin = start.astimezone().isoformat()
    finish = (start + dt.timedelta(hours=1)).astimezone().isoformat()
    return {"id": event_id, "summary": "週會", "start": {"dateTime": begin}, "end": {"dateTime": finish}, **extra}


class MeetingLinkTest(unittest.TestCase):

    def test_link_sources(self):
        """Meet 欄位、會議資料、地點、說明裡的視訊網址 > 都找得到"""
        cases = {
            "hangout": ({"hangoutLink": "https://meet.google.com/abc-defg-hij"}, "https://meet.google.com/abc-defg-hij"),
            "entry": ({"conferenceData": {"entryPoints": [
                {"entryPointType": "phone", "uri": "tel:+886-2-0000-0000"},
                {"entryPointType": "video", "uri": "https://zoom.us/j/123"}]}}, "https://zoom.us/j/123"),
            "location": ({"location": "線上 https://us02web.zoom.us/j/999?pwd=x"}, "https://us02web.zoom.us/j/999?pwd=x"),
            "html": ({"description": '<a href="https://teams.microsoft.com/l/meetup-join/19%3a">加入</a>'},
                     "https://teams.microsoft.com/l/meetup-join/19%3a"),
            "sentence": ({"description": "連結：https://meet.google.com/xyz."}, "https://meet.google.com/xyz"),
        }
        for name, (fields, expected) in cases.items():
            with self.subTest(source=name):
                # Given / When: 找連結
                link = daynote.meeting_link(fields)
                # Then: 正確網址
                self.assertEqual(link, expected)

    def test_no_link(self):
        """實體會議、非視訊網址、非 https > 沒有連結"""
        cases = ({"location": "3F 會議室"}, {"description": "議程 https://example.com/agenda"},
                 {"hangoutLink": "http://meet.google.com/abc"}, {"description": "http://zoom.us/j/1"}, {})
        for fields in cases:
            with self.subTest(fields=fields):
                # Given / When: 找連結
                link = daynote.meeting_link(fields)
                # Then: 沒有
                self.assertIsNone(link)


class UpcomingMeetingsTest(unittest.TestCase):

    def test_today_and_tomorrow_only(self):
        """昨天、今天、明天、後天的行程 > 只留今天與明天，依開始時間排序"""
        # Given: 四天各一場
        events = [event(dt.datetime.combine(TODAY + dt.timedelta(days=offset), dt.time(10, 0)), f"ev{offset}")
                  for offset in (1, -1, 0, 2)]
        # When: 挑選
        meetings = daynote.upcoming_meetings(events, TODAY)
        # Then: 今天、明天
        self.assertEqual([m["id"] for m in meetings], ["ev0", "ev1"])
        self.assertEqual(meetings[0]["start"], dt.datetime.combine(TODAY, dt.time(10, 0)))

    def test_skip_all_day_declined_and_duplicates(self):
        """全天行程、已婉拒的、重複抓到的同一場 > 都不重複提醒"""
        # Given: 一場正常、一場全天、一場已婉拒、正常那場重複一次
        at = dt.datetime.combine(TODAY, dt.time(14, 0))
        events = [event(at), {"id": "all", "start": {"date": TODAY.isoformat()}},
                  event(at, "no", attendees=[{"self": True, "responseStatus": "declined"}]), event(at)]
        # When: 挑選
        meetings = daynote.upcoming_meetings(events, TODAY)
        # Then: 只剩一場
        self.assertEqual([m["id"] for m in meetings], ["ev1"])

    def test_start_text(self):
        """還沒開始／剛好開始／已開始 > 說明剩幾分鐘或已開始幾分鐘"""
        start = dt.datetime(2026, 10, 23, 14, 0)
        cases = {dt.datetime(2026, 10, 23, 13, 55): "14:00 開始（還有 5 分鐘）",
                 start: "14:00 開始", dt.datetime(2026, 10, 23, 14, 3): "14:00 開始（已開始 3 分鐘）"}
        for now, expected in cases.items():
            with self.subTest(now=now):
                # Given / When: 組說明文字
                text = daynote.meeting_start_text(start, now)
                # Then: 正確
                self.assertEqual(text, expected)


if __name__ == "__main__":
    unittest.main()
