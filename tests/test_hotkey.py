"""全域快捷鍵（F8、Ctrl + Alt + D）的註冊、重試與占用提示。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v
"""
import unittest
import unittest.mock

from tests.test_recur import daynote

F8_ID, CTRL_ALT_D_ID = 1, 2


class FakeRegistrar:
    """借用 App._register_hotkey；after 與狀態列只記錄呼叫，不真的排程或顯示。"""

    _register_hotkey = daynote.App._register_hotkey

    def __init__(self):
        self.hotkeys_registered = set()
        self.scheduled = []
        self.status = None

    def after(self, ms, func):
        self.scheduled.append((ms, func))

    def _set_status(self, text, error=False):
        self.status = (text, error)


def fake_register(occupied):
    """回傳模擬的 win_register_hotkey：occupied 內的 id 註冊失敗，並記錄每次嘗試的 id。"""
    attempts = []

    def register(widget, hotkey_id):
        attempts.append(hotkey_id)
        return hotkey_id not in occupied

    return register, attempts


class HotkeyDefinitionTest(unittest.TestCase):

    def test_f8_is_primary_without_modifiers(self):
        """快捷鍵定義 > F8 排第一且不需修飾鍵，Ctrl + Alt + D 保留為第二組"""
        # Given: 快捷鍵定義
        hotkeys = daynote.HOTKEYS
        # When: 取出各組的按鍵與修飾鍵
        keys = [(vk, mods, name) for _, mods, vk, name in hotkeys]
        # Then: F8 只帶 NOREPEAT；Ctrl + Alt + D 維持原本組合
        self.assertEqual(keys[0], (0x77, daynote.MOD_NOREPEAT, "F8"))
        self.assertEqual(keys[1], (0x44, daynote.MOD_CONTROL | daynote.MOD_ALT | daynote.MOD_NOREPEAT,
                                   "Ctrl + Alt + D"))
        self.assertEqual(daynote.HOTKEY_IDS, {F8_ID, CTRL_ALT_D_ID})


class RegisterHotkeyTest(unittest.TestCase):

    def test_all_registered(self):
        """兩組都沒被占用 > 全部註冊成功，不重試也不顯示錯誤"""
        # Given: 沒有被占用的快捷鍵
        app = FakeRegistrar()
        register, _ = fake_register(occupied=set())
        with unittest.mock.patch.object(daynote, "win_register_hotkey", register):
            # When: 註冊
            app._register_hotkey(retries=6)
        # Then: 兩組都成功
        self.assertEqual(app.hotkeys_registered, {F8_ID, CTRL_ALT_D_ID})
        self.assertEqual(app.scheduled, [])
        self.assertIsNone(app.status)

    def test_failure_schedules_retry(self):
        """F8 暫時被舊的 DayNote 占用 > 1 秒後重試，先不顯示錯誤"""
        # Given: F8 被占用、還有重試次數
        app = FakeRegistrar()
        register, _ = fake_register(occupied={F8_ID})
        with unittest.mock.patch.object(daynote, "win_register_hotkey", register):
            # When: 註冊
            app._register_hotkey(retries=6)
        # Then: Ctrl + Alt + D 已可用，排定 1 秒後重試
        self.assertEqual(app.hotkeys_registered, {CTRL_ALT_D_ID})
        self.assertEqual([ms for ms, _ in app.scheduled], [1000])
        self.assertIsNone(app.status)

    def test_retry_only_attempts_missing(self):
        """重試時 > 只重新註冊失敗的那組，不重複註冊已成功的"""
        # Given: 上一輪已註冊 Ctrl + Alt + D，F8 這次釋放了
        app = FakeRegistrar()
        app.hotkeys_registered = {CTRL_ALT_D_ID}
        register, attempts = fake_register(occupied=set())
        with unittest.mock.patch.object(daynote, "win_register_hotkey", register):
            # When: 重試
            app._register_hotkey(retries=5)
        # Then: 只嘗試 F8，兩組都可用
        self.assertEqual(attempts, [F8_ID])
        self.assertEqual(app.hotkeys_registered, {F8_ID, CTRL_ALT_D_ID})
        self.assertIsNone(app.status)

    def test_f8_occupied_after_retries(self):
        """重試用完 F8 仍被占用 > 狀態列紅字引導用 Ctrl + Alt + D 快速列再按 Tab 開主視窗"""
        # Given: F8 被其他程式長期占用、沒有重試次數
        app = FakeRegistrar()
        register, _ = fake_register(occupied={F8_ID})
        with unittest.mock.patch.object(daynote, "win_register_hotkey", register):
            # When: 最後一次註冊
            app._register_hotkey(retries=0)
        # Then: 不再重試，說明兩組功能的差別
        self.assertEqual(app.scheduled, [])
        self.assertEqual(app.status, ("F8 已被其他程式占用；隱藏後請按 Ctrl + Alt + D 叫出快速列，再按 Tab 開啟主視窗", True))

    def test_all_occupied_after_retries(self):
        """重試用完兩組都被占用 > 狀態列紅字引導重新點 StartDayNote"""
        # Given: 兩組都被占用、沒有重試次數
        app = FakeRegistrar()
        register, _ = fake_register(occupied={F8_ID, CTRL_ALT_D_ID})
        with unittest.mock.patch.object(daynote, "win_register_hotkey", register):
            # When: 最後一次註冊
            app._register_hotkey(retries=0)
        # Then: 沒有可用的快捷鍵
        self.assertEqual(app.hotkeys_registered, set())
        self.assertEqual(app.status, ("F8 和 Ctrl + Alt + D 都已被其他程式占用；隱藏後請再點 StartDayNote 叫回", True))


class HotkeyStatusMessageTest(unittest.TestCase):

    def test_only_ctrl_alt_d_occupied(self):
        """只有 Ctrl + Alt + D 被占用 > 說明快速列不能用、主視窗仍可用 F8"""
        # Given: Ctrl + Alt + D 失敗
        failed = ["Ctrl + Alt + D"]
        # When: 產生提示
        text = daynote.hotkey_status_message(failed)
        # Then: 指向 F8
        self.assertEqual(text, "Ctrl + Alt + D 已被其他程式占用，無法叫出快速列；主視窗仍可用 F8 顯示／隱藏")

    def test_actions_f8_window_ctrl_alt_d_quick_bar(self):
        """快捷鍵動作 > F8 顯示／隱藏主視窗，Ctrl + Alt + D 叫出快速列"""
        # Given / When: 動作對照
        actions = daynote.HOTKEY_ACTIONS
        # Then: 依使用者要求
        self.assertEqual(actions, {F8_ID: "toggle_window", CTRL_ALT_D_ID: "quick_bar"})


if __name__ == "__main__":
    unittest.main()
