"""長時間常駐的可靠性：檔案原子寫入、計時器出錯後繼續、PID 檔暫時讀不到不關閉。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v
"""
import json
import os
import tempfile
import unittest
import unittest.mock

from tests.test_recur import daynote


class AtomicWriteTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "state.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_save_state_writes_complete_file(self):
        """存狀態 > 檔案內容完整、不留下暫存檔"""
        # Given: 狀態檔位置指到暫存資料夾
        with unittest.mock.patch.object(daynote, "STATE_PATH", self.path):
            # When: 存狀態
            daynote.save_state({"theme": "night"})
            # Then: 讀得回來，沒有 .tmp
            self.assertEqual(daynote.load_state(), {"theme": "night"})
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_failed_write_keeps_old_file(self):
        """寫入途中失敗 > 原本的狀態檔維持完整，也不拋出例外"""
        # Given: 已有一份完整的狀態檔
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"theme": "cream"}, f)
        with unittest.mock.patch.object(daynote, "STATE_PATH", self.path), \
                unittest.mock.patch.object(daynote.os, "replace", side_effect=OSError("disk full")):
            # When: 換檔時失敗
            daynote.save_state({"theme": "night"})
            # Then: 舊內容還在，暫存檔已清掉
            self.assertEqual(daynote.load_state(), {"theme": "cream"})
        self.assertFalse(os.path.exists(self.path + ".tmp"))


class FakeTimerApp:
    """借用 App 的計時器方法；after 只記錄排程，不真的執行。"""

    _check_reminders = daynote.App._check_reminders
    _tick_next_up = daynote.App._tick_next_up

    def __init__(self):
        self.scheduled = []
        self.detail = self.draft = None

    def after(self, ms, func):
        self.scheduled.append(func)

    def _fire_due_reminders(self):
        raise ValueError("bad task data")

    def _update_next_up(self):
        raise ValueError("bad task data")


class TimerResilienceTest(unittest.TestCase):

    def test_reminder_check_reschedules_after_error(self):
        """某次檢查提醒時出錯 > 例外照常拋出（交給 log），但仍排好下一次檢查"""
        # Given: 檢查提醒會出錯
        app = FakeTimerApp()
        # When: 執行一次檢查
        with self.assertRaises(ValueError):
            app._check_reminders()
        # Then: 下一次檢查已排好
        self.assertEqual(app.scheduled, [app._check_reminders])

    def test_next_up_tick_reschedules_after_error(self):
        """更新「下一件」時出錯 > 仍排好下一次更新"""
        # Given: 更新會出錯
        app = FakeTimerApp()
        # When: 執行一次更新
        with self.assertRaises(ValueError):
            app._tick_next_up()
        # Then: 下一次已排好
        self.assertEqual(app.scheduled, [app._tick_next_up])


class FakePidApp:
    """借用 App._watch_pid；記錄是否排下一次檢查或關閉。"""

    _watch_pid = daynote.App._watch_pid

    def __init__(self):
        self.scheduled, self.closed = 0, False

    def after(self, ms, func):
        self.scheduled += 1

    def close(self, ask=True):
        self.closed = True


class WatchPidTest(unittest.TestCase):

    def test_locked_pid_file_tolerated(self):
        """PID 檔被短暫鎖住（讀取失敗）> 前兩次繼續執行，連續第三次才關閉"""
        # Given: 讀 PID 檔一律失敗
        app = FakePidApp()
        with unittest.mock.patch("builtins.open", side_effect=PermissionError("locked")):
            # When: 連續檢查兩次
            app._watch_pid()
            app._watch_pid()
            # Then: 還沒關閉
            self.assertFalse(app.closed)
            self.assertEqual(app.scheduled, 2)
            # When: 第三次仍失敗
            app._watch_pid()
        # Then: 關閉
        self.assertTrue(app.closed)

    def test_removed_pid_file_closes_immediately(self):
        """PID 檔被刪除（新版 DayNote 接手）> 立即關閉"""
        # Given: PID 檔不存在
        app = FakePidApp()
        with unittest.mock.patch("builtins.open", side_effect=FileNotFoundError()):
            # When: 檢查
            app._watch_pid()
        # Then: 關閉
        self.assertTrue(app.closed)

    def test_successful_read_resets_failures(self):
        """讀取失敗後又讀到自己的 PID > 失敗次數歸零，之後再失敗也要重新累計"""
        # Given: 失敗兩次
        app = FakePidApp()
        with unittest.mock.patch("builtins.open", side_effect=PermissionError("locked")):
            app._watch_pid()
            app._watch_pid()
        # When: 讀到自己的 PID，接著又失敗一次
        with unittest.mock.patch("builtins.open", unittest.mock.mock_open(read_data=str(os.getpid()))):
            app._watch_pid()
        with unittest.mock.patch("builtins.open", side_effect=PermissionError("locked")):
            app._watch_pid()
        # Then: 沒有關閉
        self.assertFalse(app.closed)


if __name__ == "__main__":
    unittest.main()
