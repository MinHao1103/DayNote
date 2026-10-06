"""關閉 DayNote 時，詳細頁未儲存修改的處理（避免直接關閉遺失修改）。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v
"""
import unittest
import unittest.mock

from tests.test_recur import FakeGoogle, daynote, make_task


class FakeCloser:
    """借用 App._flush_detail_on_close；詳細頁的修改以 pending_fields 模擬。"""

    _flush_detail_on_close = daynote.App._flush_detail_on_close

    def __init__(self, fields, google=None):
        self.detail = {"task": make_task()}
        self.pending_fields = fields
        self.g = google or FakeGoogle()

    def _detail_changes(self):
        return dict(self.pending_fields)


class FlushDetailOnCloseTest(unittest.TestCase):

    def test_no_detail_open(self):
        """沒有開啟詳細頁 > 不詢問，直接關閉"""
        # Given: 主畫面
        closer = FakeCloser({})
        closer.detail = None
        with unittest.mock.patch.object(daynote.messagebox, "askyesnocancel") as ask:
            # When: 關閉
            result = closer._flush_detail_on_close()
        # Then: 可關閉、未詢問
        self.assertTrue(result)
        ask.assert_not_called()

    def test_clean_detail(self):
        """詳細頁沒有修改 > 不詢問，直接關閉"""
        # Given: 沒有修改
        closer = FakeCloser({})
        with unittest.mock.patch.object(daynote.messagebox, "askyesnocancel") as ask:
            # When: 關閉
            result = closer._flush_detail_on_close()
        # Then: 可關閉、未詢問、未送出
        self.assertTrue(result)
        ask.assert_not_called()
        self.assertEqual([], closer.g.calls)

    def test_dirty_choose_save(self):
        """有修改並選「是」 > 同步送出 PATCH 後關閉"""
        # Given: 改了標題
        closer = FakeCloser({"title": "新標題"})
        with unittest.mock.patch.object(daynote.messagebox, "askyesnocancel", return_value=True):
            # When: 關閉並選擇儲存
            result = closer._flush_detail_on_close()
        # Then: 已送出（呼叫返回前就完成，不經背景執行緒）
        self.assertTrue(result)
        self.assertEqual([("update_task", ("L1", "t1", {"title": "新標題"}), {})], closer.g.calls)

    def test_dirty_choose_discard(self):
        """有修改並選「否」 > 不送出，直接關閉"""
        # Given: 改了標題
        closer = FakeCloser({"title": "新標題"})
        with unittest.mock.patch.object(daynote.messagebox, "askyesnocancel", return_value=False):
            # When: 關閉並選擇不儲存
            result = closer._flush_detail_on_close()
        # Then: 可關閉、未送出
        self.assertTrue(result)
        self.assertEqual([], closer.g.calls)

    def test_dirty_choose_cancel(self):
        """有修改並選「取消」 > 不關閉"""
        # Given: 改了標題
        closer = FakeCloser({"title": "新標題"})
        with unittest.mock.patch.object(daynote.messagebox, "askyesnocancel", return_value=None):
            # When: 關閉並取消
            result = closer._flush_detail_on_close()
        # Then: 不關閉
        self.assertFalse(result)
        self.assertEqual([], closer.g.calls)

    def test_save_fails_stays_open(self):
        """選「是」但儲存失敗 > 顯示錯誤且不關閉，修改不會遺失"""
        # Given: PATCH 會失敗
        closer = FakeCloser({"title": "新標題"}, FakeGoogle(fail={"update_task"}))
        with unittest.mock.patch.object(daynote.messagebox, "askyesnocancel", return_value=True), \
                unittest.mock.patch.object(daynote.messagebox, "showerror") as error:
            # When: 關閉並選擇儲存
            result = closer._flush_detail_on_close()
        # Then: 不關閉、有錯誤提示
        self.assertFalse(result)
        error.assert_called_once()

    def test_replaced_by_new_instance_saves_silently(self):
        """被新開的 DayNote 取代（ask=False） > 不詢問，直接同步儲存"""
        # Given: 有修改
        closer = FakeCloser({"title": "新標題"})
        with unittest.mock.patch.object(daynote.messagebox, "askyesnocancel") as ask:
            # When: 不詢問地關閉
            result = closer._flush_detail_on_close(ask=False)
        # Then: 已送出、未詢問
        self.assertTrue(result)
        ask.assert_not_called()
        self.assertEqual(["update_task"], closer.g.names())

    def test_replaced_and_save_fails_still_closes(self):
        """被取代時儲存失敗 > 無法詢問，仍然關閉"""
        # Given: PATCH 會失敗
        closer = FakeCloser({"title": "新標題"}, FakeGoogle(fail={"update_task"}))
        with unittest.mock.patch.object(daynote.messagebox, "showerror") as error:
            # When: 不詢問地關閉
            result = closer._flush_detail_on_close(ask=False)
        # Then: 關閉、不跳錯誤視窗
        self.assertTrue(result)
        error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
