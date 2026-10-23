"""DayNote 主視窗端對端測試：驅動真的 App 與 Tk 事件迴圈，斷言畫面上實際顯示的內容。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

隔離（測試不得影響使用者的真實環境）：
- Google：以記憶體版 StoreGoogle 取代，不連網
- data 資料夾：state.json、token.bin 等路徑改到暫存資料夾
- Windows：釘選桌面、圓角、背景視窗、全域快捷鍵、訊息攔截改為替身，不搶執行中 DayNote 的快捷鍵
- 對話框、提示音、剪貼簿、瀏覽器：一律替身

以下需要真實 Windows／Google 環境，不在此自動化，改列手動驗證：
OAuth 瀏覽器登入、真實同步、釘在桌面與 Win + D、全域快捷鍵實際觸發、提醒視窗位置、
開機自動啟動捷徑、雙擊 .bat／.vbs 啟動
"""
import datetime as dt
import json
import os
import re
import shutil
import tempfile
import threading
import time
import tkinter as tk
import unittest
import unittest.mock

from tests.test_recur import daynote

HOLIDAY_CAL = "holiday-test"
# Windows 送來的快捷鍵 id：依 HOTKEY_ACTIONS 找，對調按鍵時測試不用改
WINDOW_KEY = next(i for i, action in daynote.HOTKEY_ACTIONS.items() if action == "toggle_window")  # F8
QUICK_KEY = next(i for i, action in daynote.HOTKEY_ACTIONS.items() if action == "quick_bar")      # Ctrl + Alt + D


class StoreGoogle:
    """有狀態的 Google 替身：任務存在記憶體，行為比照 Tasks API（刪除只標記 deleted）。"""

    def __init__(self, logged_in=True, fail=()):
        self.refresh_token = "<REFRESH_TOKEN>" if logged_in else None
        self.lists = [{"id": "L1", "title": "工作"}, {"id": "L2", "title": "個人"}]
        self.tasks = {}
        self.events_by_cal = {"primary": [], HOLIDAY_CAL: []}
        self.fail = set(fail)
        self.fail_with = {}  # {方法名稱: 例外類別}，未指定時丟 ApiError
        self.calls = []
        self._seq = 0
        self._lock = threading.Lock()

    def _hit(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise self.fail_with.get(name, daynote.ApiError)(f"{name} failed")

    def _next_id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq}"

    def seed(self, title, due=None, notes="", list_id="L1", parent=None, status="needsAction"):
        """預先放進 Google 的任務，回傳 id。"""
        with self._lock:
            task_id = self._next_id("t")
            self.tasks[task_id] = {"id": task_id, "list_id": list_id, "title": title, "notes": notes,
                                   "status": status, "position": f"{self._seq:03d}",
                                   "due": f"{due.isoformat()}T00:00:00.000Z" if due else None}
            if parent:
                self.tasks[task_id]["parent"] = parent
            return task_id

    def by_title(self, title):
        return [t for t in self.tasks.values() if t["title"] == title]

    @staticmethod
    def _api(raw):
        return {k: v for k, v in raw.items() if k != "list_id"}

    # ---- 讀取
    def tasklists(self):
        self._hit("tasklists")
        return [dict(lst) for lst in self.lists]

    def open_tasks(self, list_id):
        self._hit("open_tasks")
        return [self._api(t) for t in self.tasks.values()
                if t["list_id"] == list_id and t["status"] == "needsAction" and not t.get("deleted")]

    def due_tasks(self, list_id, day):
        self._hit("due_tasks")
        return [self._api(t) for t in self.tasks.values()
                if t["list_id"] == list_id and t.get("due") and t["due"][:10] == day.isoformat()]

    def completed_since(self, list_id, since):
        self._hit("completed_since")
        return [self._api(t) for t in self.tasks.values() if t["list_id"] == list_id
                and t["status"] == "completed" and not t.get("deleted") and t.get("completed_at", since) > since]

    def events(self, start, end, calendar_id="primary"):
        self._hit(f"events:{calendar_id}")
        return list(self.events_by_cal.get(calendar_id, []))

    # ---- 寫入
    def add_tasklist(self, title):
        self._hit("add_tasklist")
        lst = {"id": self._next_id("L"), "title": title}
        self.lists.append(lst)
        return dict(lst)

    def add_task(self, list_id, title, due, parent=None, notes=None):
        self._hit("add_task")
        task_id = self.seed(title, due, notes or "", list_id=list_id, parent=parent)
        return self._api(self.tasks[task_id])

    def update_task(self, list_id, task_id, fields):
        self._hit("update_task")
        self.tasks[task_id].update(fields)

    def delete_task(self, list_id, task_id):
        self._hit("delete_task")
        self.tasks[task_id]["deleted"] = True

    def complete_task(self, list_id, task_id):
        self._hit("complete_task")
        self.tasks[task_id].update(status="completed", completed_at=dt.datetime.now())

    def uncomplete_task(self, list_id, task_id):
        self._hit("uncomplete_task")
        self.tasks[task_id]["status"] = "needsAction"

    # ---- 帳號
    def login(self, on_url=None):
        self._hit("login")
        if on_url:
            on_url("https://accounts.example.com/<LOGIN_URL>")
        self.refresh_token = "<REFRESH_TOKEN>"

    def revoke(self):
        self._hit("revoke")
        self.refresh_token = None
        return True


# ---------------------------------------------------------------- 畫面操作工具

def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def _shown_text(w):
    """Label 與膠囊按鈕（Pill，Canvas 繪製）顯示的文字；其他元件回傳 None。"""
    if isinstance(w, daynote.Pill):
        return w.text_value
    if isinstance(w, tk.Label):
        # 改用圖示字型顯示的按鈕，text_value 保留原本的符號文字
        return getattr(w, "text_value", None) or w.cget("text")
    return None


def texts(widget):
    """元件底下所有 Label 與按鈕顯示的文字。"""
    return [t for t in (_shown_text(w) for w in descendants(widget)) if t is not None]


def find_label(widget, text, exact=True):
    for w in descendants(widget):
        value = _shown_text(w)
        if value is not None and (value == text or (not exact and text in value)):
            return w
    raise AssertionError(f"畫面上找不到「{text}」；目前有：{texts(widget)}")


def when_dialog_open(app, action):
    """等這次的對話框真的出現才執行 action(對話框)。

    對話框會 wait_window 等使用者回應，測試只能事先排程操作；電腦忙時對話框可能比預期晚建立，
    或 _active_dialog 還指向上一個已關閉的，固定延遲會操作失敗、對話框永遠不關而卡住整個測試。
    看門狗：DIALOG_WATCHDOG_MS 內對話框還開著就強制關閉，讓測試以斷言失敗結束，而不是卡住整個測試流程。
    """
    app._active_dialog = None
    found = {}

    def check():
        dialog = app._active_dialog
        if dialog is None or not dialog.winfo_exists():
            app.after(50, check)
        else:
            found["dialog"] = dialog
            action(dialog)

    def watchdog():
        dialog = found.get("dialog")
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()
    app.after(50, check)
    app.after(DIALOG_WATCHDOG_MS, watchdog)


DIALOG_WATCHDOG_MS = 5000


def press_key(widget, key):
    """對元件按鍵：Tk 的鍵盤事件只送到有焦點的視窗，先確定焦點在元件上（最多等 2 秒）再送出。"""
    deadline = time.time() + 2
    while widget.focus_get() is not widget and time.time() < deadline:
        widget.focus_force()
        widget.update()
    widget.event_generate(key)


def click(widget, **kw):
    """模擬滑鼠點擊；帶上元件內的螢幕座標，選擇器才不會誤判為「點在外面」而關閉。"""
    root = widget._root()
    root.update()  # 確保元件已顯示，座標才正確
    x, y = kw.pop("x", 2), kw.pop("y", 2)
    widget.event_generate("<Button-1>", x=x, y=y,
                          rootx=widget.winfo_rootx() + x, rooty=widget.winfo_rooty() + y, **kw)
    root.update()


def key(widget, sequence):
    """鍵盤事件只會送到有焦點的元件，先把焦點移過去。"""
    root = widget._root()
    widget.focus_force()
    root.update()
    widget.event_generate(sequence)
    root.update()


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def settle(app, until=None, timeout=5.0):
    """跑事件迴圈直到背景工作做完（以及 until 成立）。"""
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        idle = app.q.empty() and threading.active_count() <= _BASE_THREADS and not app.busy
        if idle and (until is None or until()):
            pump(app, 0.15)  # 讓 _poll_queue 處理最後一批結果
            if app.q.empty() and (until is None or until()):
                return
        time.sleep(0.01)
    raise AssertionError("等待逾時：背景工作沒有完成")


def wait_threads(app, timeout=5.0):
    """等所有背景執行緒結束並處理完結果（不看 busy）。"""
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if threading.active_count() <= _BASE_THREADS and app.q.empty():
            pump(app, 0.15)
            return
        time.sleep(0.01)
    raise AssertionError("等待逾時：背景執行緒沒有結束")


# 載入時的執行緒數：settle 以「回到這個數量」判斷背景工作做完。
# 其他測試若留下常駐執行緒，這個基準會失準而讓 settle 逾時，屆時改為逐一追蹤 run_bg 的執行緒。
_BASE_THREADS = threading.active_count()


def circle_of(app, title):
    """清單中某筆任務的完成圓圈。"""
    label = find_label(app.list_frame, title)
    inner = label.master.master
    return next(w for w in inner.winfo_children() if isinstance(w, tk.Canvas))


def cal_click(app, day):
    """點月曆上的某一天。"""
    for row, week in enumerate(app.weeks):
        if day in week:
            col = week.index(day)
            click(app.cal, x=col * daynote.CELL_W + daynote.CELL_W // 2,
                  y=daynote.HEAD_H + row * daynote.CELL_H + daynote.CELL_H // 2)
            return
    raise AssertionError(f"{day} 不在目前的月曆上")


def re_time(text):
    """是否為 HH:MM 形式的時間按鈕文字。"""
    return re.fullmatch(r"\d{2}:\d{2}", text) is not None


def type_time(app, text):
    """在開著的時間選擇器輸入時間並按 Enter。"""
    entry = next(w for w in descendants(popup(app)) if isinstance(w, tk.Entry))
    entry.delete(0, "end")
    entry.insert(0, text)
    key(entry, "<Return>")


def popup(app):
    """目前開著的選擇器小視窗。"""
    pops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()
            and any(isinstance(c, tk.Frame) for c in w.winfo_children())]
    if not pops:
        raise AssertionError("沒有開啟的選擇器")
    return pops[-1]


# ---------------------------------------------------------------- 測試基底

class AppTestCase(unittest.TestCase):
    """每個案例一個全新的 App；所有對外的副作用都換成替身。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="daynote-test-")
        self.registered_hotkeys = []
        self.dialogs = []
        self.answer = {"askyesno": True, "askyesnocancel": True}
        patches = [
            unittest.mock.patch.multiple(
                daynote, DATA_DIR=self.tmp,
                STATE_PATH=os.path.join(self.tmp, "state.json"),
                TOKEN_PATH=os.path.join(self.tmp, "token.bin"),
                CONFIG_PATH=os.path.join(self.tmp, "config.json"),
                PID_PATH=os.path.join(self.tmp, "daynote.pid")),
            unittest.mock.patch.object(daynote, "win_pin_to_desktop", lambda w: None),
            unittest.mock.patch.object(daynote, "win_is_pinned", lambda w: True),
            unittest.mock.patch.object(daynote, "win_round_corners", lambda w: None),
            unittest.mock.patch.object(daynote, "win_border_color", lambda w, c: None),
            unittest.mock.patch.object(daynote, "win_style_popup", lambda w: None),
            unittest.mock.patch.object(daynote, "win_set_background_window", lambda w: None),
            unittest.mock.patch.object(daynote, "win_hook_messages", lambda w, **kw: object()),
            unittest.mock.patch.object(daynote, "win_register_hotkey",
                                       lambda w, i: self.registered_hotkeys.append(i) or True),
            unittest.mock.patch.object(daynote, "win_unregister_hotkey", lambda w, i: None),
            unittest.mock.patch.object(daynote.winsound, "PlaySound", lambda *a: None),
            unittest.mock.patch.object(daynote.webbrowser, "open", lambda *a, **k: True),
            unittest.mock.patch.object(daynote.messagebox, "askyesno", self._dialog("askyesno")),
            unittest.mock.patch.object(daynote.messagebox, "askyesnocancel", self._dialog("askyesnocancel")),
            unittest.mock.patch.object(daynote.messagebox, "showerror", self._dialog("showerror")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.today = dt.date.today()
        self.g = StoreGoogle()
        self.first_run = False  # True：不預寫 state.json，模擬第一次使用（會顯示快捷鍵提示）
        self.compact_calendar = False  # 既有案例以整月月曆撰寫

    def _dialog(self, name):
        def record(title, message, **kw):
            self.dialogs.append((name, title, message))
            return self.answer.get(name)
        return record

    def start(self, **kw):
        """啟動 App 並等第一次同步完成。"""
        hook = {}
        if not self.first_run:
            with open(os.path.join(self.tmp, "state.json"), "w", encoding="utf-8") as f:
                json.dump({"tips_shown": True, "compact_calendar": self.compact_calendar}, f)
        with unittest.mock.patch.object(daynote, "win_hook_messages",
                                        lambda w, **k: hook.update(k) or object()):
            self.app = daynote.App(self.g, holiday_calendar=kw.pop("holiday_calendar", HOLIDAY_CAL),
                                   desktop_reminder=False, **kw)
            pump(self.app, 0.4)  # 背景模式的視窗樣式與快捷鍵在 200ms 後才設定
            deadline = time.time() + 3.0  # 電腦忙碌時 200ms 的排程會延後，等到快捷鍵註冊完成再繼續
            while "on_hotkey" not in hook and time.time() < deadline:
                pump(self.app, 0.05)
        self.hotkey_callback = hook.get("on_hotkey")  # Windows 收到 WM_HOTKEY 時會呼叫的函式
        self.app.clipboard_clear = lambda: None   # 不動使用者的剪貼簿
        self.app.clipboard_append = lambda text: setattr(self, "clipboard", text)
        self.addCleanup(self._destroy)
        if self.g.refresh_token:
            settle(self.app)
        return self.app

    def _destroy(self):
        for toast in list(daynote.ReminderToast.active):
            toast.close()
        try:
            for job in self.app.tk.call("after", "info"):  # 取消排程中的計時器，避免銷毀後才觸發
                self.app.after_cancel(job)
            self.app.destroy()
        except tk.TclError:
            pass

    def list_texts(self):
        return texts(self.app.list_frame)

    def status(self):
        return self.app.status.cget("text")

    def type_new_task(self, title):
        app = self.app
        app.entry.focus_force()
        app.update()
        app.entry.delete(0, "end")
        app.entry.insert(0, title)
        key(app.entry, "<Return>")
        settle(app)


# ---------------------------------------------------------------- 啟動、登入、登出

class StartupTest(AppTestCase):

    def test_logged_in_startup_syncs(self):
        """已登入啟動 > 自動同步，顯示今天的任務、清單與「已同步」"""
        # Given: Google 上有今天的任務
        self.g.seed("寫週報", self.today)
        # When: 啟動
        app = self.start()
        # Then: 畫面顯示任務、清單下拉與同步狀態
        self.assertIn("寫週報", self.list_texts())
        self.assertIn("今天（1）", self.list_texts())
        self.assertEqual(app.current_list()["title"], "工作")
        self.assertEqual([lst["title"] for lst in app.lists], ["工作", "個人"])
        self.assertTrue(self.status().startswith("已同步"))
        self.assertFalse(app.btn_login.winfo_ismapped())
        self.assertEqual(app.menu_more.entrycget("登出 Google", "state"), "normal")

    def test_hotkeys_registered_on_startup(self):
        """背景模式啟動 > 註冊 F8 與 Ctrl + Alt + D 兩組快捷鍵"""
        # Given: 預設的背景模式
        # When: 啟動
        app = self.start()
        # Then: 兩組都註冊
        self.assertEqual(sorted(self.registered_hotkeys), [1, 2])
        self.assertEqual(app.hotkeys_registered, {1, 2})

    def test_logged_out_shows_prompt(self):
        """未登入啟動 > 清單區顯示登入提示、沒有可用的清單"""
        # Given: 沒有登入憑證
        self.g = StoreGoogle(logged_in=False)
        # When: 啟動
        app = self.start()
        # Then: 登入提示
        self.assertIn("尚未登入 Google", self.list_texts())
        self.assertIsNone(app.current_list())
        self.assertNotIn("tasklists", self.g.calls)

    def test_add_task_when_logged_out(self):
        """未登入就新增工作 > 紅字提示先登入，不呼叫 Google"""
        # Given: 未登入
        self.g = StoreGoogle(logged_in=False)
        self.start()
        # When: 輸入工作按 Enter
        self.type_new_task("買牛奶")
        # Then: 提示登入
        self.assertEqual(self.status(), "請先登入 Google（右上角「登入」）")
        self.assertNotIn("add_task", self.g.calls)

    def test_login_flow(self):
        """按登入 > 登入網址放進剪貼簿，完成後同步並顯示任務"""
        # Given: 未登入，Google 上已有任務
        self.g = StoreGoogle(logged_in=False)
        self.g.seed("晨會", self.today)
        app = self.start()
        # When: 按「登入 Google」
        click(find_label(app.list_frame, "登入 Google"))
        settle(app)
        # Then: 已同步、顯示任務、收起登入按鈕、選單的登出可用
        self.assertEqual(self.clipboard, "https://accounts.example.com/<LOGIN_URL>")
        self.assertIn("晨會", self.list_texts())
        self.assertFalse(app.btn_login.winfo_ismapped())
        self.assertEqual(app.menu_more.entrycget("登出 Google", "state"), "normal")

    def test_help_window(self):
        """「⋯」選單選使用說明 > 列出快捷鍵與常用操作；按完整說明 > 開啟 README；Esc 關閉"""
        # Given: 已啟動
        app = self.start()
        opened = []
        # When: 選單選使用說明
        app.menu_more.invoke("使用說明與快捷鍵（F1）")
        app.update()
        # Then: 列出全域快捷鍵與右鍵改日期的說明
        shown = texts(app.help_win)
        self.assertIn("Ctrl + Alt + D", shown)
        self.assertIn("F8", shown)
        self.assertIn("・在待辦上按右鍵：改日期、完成、刪除", shown)
        # When: 按完整說明
        with unittest.mock.patch.object(daynote.webbrowser, "open", lambda url: opened.append(url)):
            click(app.btn_help_more)
        # Then: 開啟 README 的操作說明
        self.assertEqual(opened, [daynote.USAGE_URL])
        # When: 按 Esc
        app.help_win.event_generate("<Escape>")
        app.update()
        # Then: 關閉
        self.assertFalse(app.help_win.winfo_exists())

    def use_theme_cleanup(self):
        """換風格會改到模組的顏色與字型常數；測試結束後還原成預設，避免影響其他案例。"""
        fonts = (daynote.TITLE_FONT, daynote.HEADING_FONT, daynote.SUBJECT_FONT)

        def restore():
            daynote.apply_theme(daynote.DEFAULT_THEME)
            daynote.TITLE_FONT, daynote.HEADING_FONT, daynote.SUBJECT_FONT = fonts
        self.addCleanup(restore)

    def test_theme_switches_instantly(self):
        """「⋯」→「風格」選墨夜 > 不重新啟動就換色、記住選擇，清單內容照常顯示"""
        # Given: 已啟動（預設奶油陶土），今天有一筆任務
        self.use_theme_cleanup()
        self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 選目前的風格
        app.menu_theme.invoke("奶油陶土")
        pump(app, 0.1)
        # Then: 不變
        self.assertEqual(app.cget("bg"), daynote.THEMES["cream"]["colors"]["BG"])
        # When: 選墨夜
        app.menu_theme.invoke("墨夜（深色）")
        pump(app, 0.2)
        # Then: 立即換色、已存檔、任務仍在、選單打勾在墨夜
        night = daynote.THEMES["night"]["colors"]
        self.assertEqual(app.cget("bg"), night["BG"])
        self.assertEqual(app.lbl_date.cget("fg"), night["TEXT"])
        self.assertIn("寫週報", self.list_texts())
        self.assertEqual(app.theme_var.get(), "night")
        with open(os.path.join(self.tmp, "state.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["theme"], "night")
        self.assertEqual(self.status(), "已換成「墨夜（深色）」")

    def test_dark_toggle_button(self):
        """右上角月亮 > 換成墨夜、圖示變太陽；再按太陽 > 回到原本的淺色風格（編輯手帳）"""
        # Given: 使用編輯手帳風格
        self.use_theme_cleanup()
        app = self.start()
        app.menu_theme.invoke("編輯手帳")
        pump(app, 0.2)
        self.assertEqual(app.btn_dark.text_value, "🌙")
        # When: 按月亮
        click(app.btn_dark)
        pump(app, 0.2)
        # Then: 墨夜，圖示換成太陽，選單打勾在墨夜
        self.assertEqual(app.cget("bg"), daynote.THEMES["night"]["colors"]["BG"])
        self.assertEqual(app.btn_dark.text_value, "☀")
        self.assertEqual(app.theme_var.get(), "night")
        # When: 按太陽
        click(app.btn_dark)
        pump(app, 0.2)
        # Then: 回到編輯手帳，不是預設的奶油陶土
        self.assertEqual(app.cget("bg"), daynote.THEMES["editorial"]["colors"]["BG"])
        self.assertEqual(app.state_data["theme"], "editorial")

    def test_follow_windows_theme(self):
        """選「跟隨 Windows」 > 系統深色時換成墨夜；系統切回淺色 > 自動換回奶油陶土"""
        # Given: 已啟動（奶油陶土），系統是深色
        self.use_theme_cleanup()
        dark = {"on": True}
        with unittest.mock.patch.object(daynote, "system_is_dark", lambda: dark["on"]):
            app = self.start()
            # When: 選跟隨 Windows
            app.menu_theme.invoke(daynote.AUTO_THEME_NAME)
            pump(app, 0.2)
            # Then: 換成墨夜，記住的是 auto
            self.assertEqual(app.cget("bg"), daynote.THEMES["night"]["colors"]["BG"])
            self.assertEqual(app.state_data["theme"], "auto")
            self.assertEqual(app.theme_var.get(), "auto")
            # When: 系統切回淺色，定時檢查觸發
            dark["on"] = False
            app._follow_system_theme()
            pump(app, 0.2)
            # Then: 換回奶油陶土
            self.assertEqual(app.cget("bg"), daynote.THEMES["cream"]["colors"]["BG"])

    def test_calendar_hover_highlight(self):
        """滑鼠移到月曆日期 > 該格出現淡色底；移出月曆 > 清掉"""
        # Given: 已啟動
        app = self.start()
        tomorrow = self.today + dt.timedelta(days=1)
        if tomorrow not in [d for w in app.weeks for d in w]:
            self.skipTest("明天不在目前顯示的週")
        left, top, right, _ = app._cal_cell_rect(tomorrow)
        # When: 移到明天
        app.cal.event_generate("<Motion>", x=(left + right) // 2, y=top + 5)
        app.update()
        # Then: 有滑過底色
        self.assertEqual(app.cal_hover, tomorrow)
        self.assertTrue(app.cal.find_withtag("hover"))
        # When: 移出
        app.cal.event_generate("<Leave>")
        app.update()
        # Then: 清掉
        self.assertIsNone(app.cal_hover)
        self.assertFalse(app.cal.find_withtag("hover"))

    def test_theme_switch_keeps_detail_edits(self):
        """詳細頁打到一半換風格 > 回到同一頁，打的字還在"""
        # Given: 詳細頁改了標題但還沒儲存
        self.use_theme_cleanup()
        self.g.seed("寫週報", self.today)
        app = self.start()
        app.open_detail(app.tasks[0])
        app.detail_title.insert("end", "（急）")
        # When: 換成北歐極簡
        app.menu_theme.invoke("北歐極簡")
        pump(app, 0.2)
        # Then: 仍在詳細頁，標題保留
        self.assertTrue(app.detail_view.winfo_ismapped())
        self.assertEqual(app.detail_title.get(), "寫週報（急）")
        self.assertEqual(app.cget("bg"), daynote.THEMES["nordic"]["colors"]["BG"])

    def test_rounded_menu_popup(self):
        """打開「⋯」選單 > 圓角選單顯示各項目；停用的項目點了沒反應；點「使用說明」 > 選單收起並開啟說明"""
        # Given: 已啟動
        app = self.start()
        menu = app.menu_more
        # When: 顯示選單
        menu.tk_popup(app.winfo_rootx() + 20, app.winfo_rooty() + 20)
        app.update()
        # Then: 項目都在
        labels = [item["label"] for item, _ in menu.rows]
        self.assertEqual(labels, ["使用說明與快捷鍵（F1）", "風格", "立即同步（F5）", "登出 Google"])
        # When: 點使用說明
        menu._click(0)
        app.update()
        # Then: 選單收起、說明開啟
        self.assertFalse(menu.is_open())
        self.assertTrue(app.help_win.winfo_exists())

    def test_rounded_menu_keyboard_and_cascade(self):
        """選單用 ↓ 移到「風格」、→ 打開子選單 > 子選單顯示所有風格，目前的打勾；Esc 回上一層"""
        # Given: 打開的「⋯」選單
        app = self.start()
        menu = app.menu_more
        menu.tk_popup(app.winfo_rootx() + 20, app.winfo_rooty() + 20)
        app.update()
        # When: ↓ 兩次到「風格」、→
        menu._step(1)
        menu._step(1)
        menu._open_child_keyboard()
        app.update()
        # Then: 子選單打開、列出風格
        child = app.menu_theme
        self.assertTrue(child.is_open())
        names = [item["label"] for item, _ in child.rows]
        self.assertIn("墨夜（深色）", names)
        self.assertEqual(app.theme_var.get(), "cream")
        # When: Esc
        child._back()
        app.update()
        # Then: 子選單收起，上一層還在
        self.assertFalse(child.is_open())
        self.assertTrue(menu.is_open())
        menu.close()

    def test_menu_returns_focus_to_main_window(self):
        """「⋯」→「風格」選墨夜 > 選單收起後焦點回到主視窗（否則 Windows 會把別的程式拉到前面，DayNote 被蓋住）"""
        # Given: 打開「⋯」選單並展開「風格」子選單（焦點在選單上）
        self.use_theme_cleanup()
        app = self.start()
        menu = app.menu_more
        menu.tk_popup(app.winfo_rootx() + 20, app.winfo_rooty() + 20)
        app.update()
        menu._step(1)
        menu._step(1)
        menu._open_child_keyboard()
        app.update()
        child = app.menu_theme
        self.assertTrue(str(app.focus_get()).startswith(str(child.win)))
        # When: 點「墨夜（深色）」
        row = next(canvas for item, canvas in child.rows if item.get("label") == "墨夜（深色）")
        click(row)
        pump(app, 0.3)
        # Then: 已換色，焦點在主視窗
        self.assertEqual(app.cget("bg"), daynote.THEMES["night"]["colors"]["BG"])
        focus = app.focus_get()
        self.assertIsNotNone(focus)
        self.assertEqual(focus.winfo_toplevel(), app)

    def test_picker_returns_focus_to_main_window(self):
        """詳細頁選日期 > 選擇器收起後焦點回到主視窗"""
        # Given: 詳細頁打開日期選擇器
        self.g.seed("寫週報", self.today)
        app = self.start()
        app.open_detail(app.tasks[0])
        app.update()
        picker = daynote.DatePicker(app, app.detail_title, self.today, app._set_detail_due)
        app.update()
        # When: 選明天
        picker.pick(self.today + dt.timedelta(days=1))
        app.update()
        # Then: 焦點在主視窗
        focus = app.focus_get()
        self.assertIsNotNone(focus)
        self.assertEqual(focus.winfo_toplevel(), app)

    def test_themed_dialog_buttons(self):
        """圓角對話框 > 按「刪除」回傳 True；按 Esc 回傳取消的值；按鈕文字依情境"""
        # Given: 已啟動
        app = self.start()
        dialog = daynote.ThemedMessageBox()
        # When: 打開刪除確認，出現後按「刪除」
        when_dialog_open(app, lambda d: click(find_label(d, "刪除")))
        result = dialog.askyesno("刪除工作", "確定刪除？", parent=app, icon="warning", yes="刪除", no="取消")
        # Then: True
        self.assertIs(result, True)
        # When: 打開尚未儲存，出現後按 Esc
        when_dialog_open(app, lambda d: press_key(d, "<Escape>"))
        result = dialog.askyesnocancel("尚未儲存", "要儲存嗎？", parent=app, yes="儲存", no="不儲存")
        # Then: None（取消）
        self.assertIsNone(result)

    def test_ask_text_returns_typed_name(self):
        """新增清單的輸入框 > 打字後按 Enter 回傳文字；按取消回傳 None"""
        # Given: 已啟動
        app = self.start()

        def type_and_enter(dialog):
            entry = next(w for w in descendants(dialog) if isinstance(w, tk.Entry))
            entry.insert(0, "旅行")
            press_key(entry, "<Return>")
        # When: 打「旅行」按 Enter
        when_dialog_open(app, type_and_enter)
        name = daynote.ask_text(app, "新增清單", "清單名稱：")
        # Then: 回傳文字
        self.assertEqual(name, "旅行")
        # When: 按取消
        when_dialog_open(app, lambda dialog: click(find_label(dialog, "取消")))
        name = daynote.ask_text(app, "新增清單", "清單名稱：")
        # Then: None
        self.assertIsNone(name)

    def test_f1_opens_help_once(self):
        """按 F1 兩次 > 只開一個說明視窗"""
        # Given: 已啟動
        app = self.start()
        # When: F1 兩次
        app.open_help()
        first = app.help_win
        app.open_help()
        # Then: 同一個視窗
        self.assertIs(app.help_win, first)
        self.assertTrue(first.winfo_exists())

    def test_logout(self):
        """「⋯」選單選登出並確認 > 撤銷授權、清空畫面、改顯示登入"""
        # Given: 已登入且有任務
        self.g.seed("晨會", self.today)
        app = self.start()
        # When: 選單選登出並確認
        app.menu_more.invoke("登出 Google")
        settle(app)
        # Then: 已登出
        self.assertIn("revoke", self.g.calls)
        self.assertEqual(self.status(), "已登出")
        self.assertIn("尚未登入 Google", self.list_texts())
        self.assertTrue(app.btn_login.winfo_ismapped())
        self.assertEqual(app.menu_more.entrycget("登出 Google", "state"), "disabled")
        self.assertEqual(app.menu_more.entrycget("立即同步（F5）", "state"), "disabled")

    def test_logout_cancelled(self):
        """登出時按否 > 不撤銷，畫面不變"""
        # Given: 已登入
        self.g.seed("晨會", self.today)
        app = self.start()
        self.answer["askyesno"] = False
        # When: 選單選登出但取消
        app.menu_more.invoke("登出 Google")
        pump(app, 0.2)
        # Then: 仍登入
        self.assertNotIn("revoke", self.g.calls)
        self.assertIn("晨會", self.list_texts())

    def test_sync_error_shown(self):
        """同步失敗 > 狀態列紅字顯示錯誤"""
        # Given: 讀取清單會失敗
        self.g.fail.add("tasklists")
        # When: 啟動
        app = self.start()
        # Then: 紅字錯誤
        self.assertEqual(self.status(), "tasklists failed")
        self.assertEqual(app.status.cget("fg"), daynote.RED)

    def test_login_expired_during_sync(self):
        """同步時登入已過期 > 改顯示登入按鈕與登入提示，不留舊的登出按鈕"""
        # Given: 已登入啟動後，Google 撤銷了授權
        self.g.seed("晨會", self.today)
        app = self.start()
        self.g.refresh_token = None
        self.g.fail.add("tasklists")
        self.g.fail_with["tasklists"] = daynote.NeedLogin
        # When: 選單選同步
        app.menu_more.invoke("立即同步（F5）")
        settle(app)
        # Then: 引導重新登入
        self.assertEqual(self.status(), "tasklists failed")
        self.assertIn("尚未登入 Google", self.list_texts())
        self.assertTrue(app.btn_login.winfo_ismapped())
        self.assertEqual(app.menu_more.entrycget("登出 Google", "state"), "disabled")


    def test_login_expired_clears_next_up(self):
        """同步時登入已過期 > 大日期旁的「下一件」收起，不留舊帳號的待辦"""
        # Given: 今天 23:59 有待辦，已顯示「下一件」，之後 Google 撤銷了授權
        self.g.seed("收尾", self.today, notes="⏰ 23:59")
        app = self.start()
        self.g.refresh_token = None
        self.g.fail.add("tasklists")
        self.g.fail_with["tasklists"] = daynote.NeedLogin
        # When: 同步
        app.menu_more.invoke("立即同步（F5）")
        settle(app)
        # Then: 「下一件」收起，舊待辦清空
        self.assertFalse(app.lbl_next.winfo_ismapped())
        self.assertEqual(app.tasks, [])


class DoneSectionTest(AppTestCase):
    """今天完成的待辦收在清單底部的「已完成」區塊，可以改回未完成。"""

    def done_circle(self, title):
        label = find_label(self.app.list_frame, title)
        inner = label.master.master
        return next(w for w in inner.winfo_children() if isinstance(w, tk.Canvas))

    def test_completed_task_listed_and_reopened(self):
        """完成待辦 > 出現在收合的「已完成（1）」；展開後點打勾圓圈 > 改回未完成，回到今天的清單"""
        # Given: 今天有一筆待辦
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 完成
        app.complete(app.tasks[0])
        settle(app)
        # Then: 已完成區塊（預設收合）
        self.assertIn("▶ 已完成（1）", self.list_texts())
        self.assertIn("今天的待辦都完成了", self.list_texts())
        # When: 展開並點打勾圓圈
        click(find_label(app.list_frame, "▶ 已完成（1）"))
        click(self.done_circle("寫週報"))
        settle(app)
        # Then: 回到未完成，Google 上也改回來
        self.assertEqual(self.g.tasks[task_id]["status"], "needsAction")
        self.assertEqual([t["title"] for t in app.tasks], ["寫週報"])
        self.assertEqual(app.done_today, [])
        self.assertFalse([text for text in self.list_texts() if "已完成" in text])

    def test_completed_on_phone_shows_after_sync(self):
        """手機上今天完成的待辦 > 同步後出現在「已完成」區塊"""
        # Given: Google 上今天已完成的待辦
        task_id = self.g.seed("繳電話費", self.today, status="completed")
        self.g.tasks[task_id]["completed_at"] = dt.datetime.now()
        # When: 啟動同步
        app = self.start()
        # Then: 已完成區塊有它
        self.assertEqual([t["title"] for t in app.done_today], ["繳電話費"])
        click(find_label(app.list_frame, "▶ 已完成（1）"))
        self.assertIn("繳電話費", self.list_texts())

    def test_undo_removes_from_done(self):
        """完成後按狀態列「復原」 > 從「已完成」區塊拿掉"""
        # Given: 剛完成一筆
        self.g.seed("寫週報", self.today)
        app = self.start()
        app.complete(app.tasks[0])
        settle(app)
        # When: 復原
        app.undo()
        settle(app)
        # Then: 不在已完成
        self.assertEqual(app.done_today, [])


class SyncDuringWriteTest(AppTestCase):
    """同步期間有修改：舊的同步結果不能蓋掉剛做的修改。"""

    def gate_open_tasks(self):
        """讓同步讀完任務後停住，等測試放行；回傳放行用的 Event。"""
        gate = threading.Event()
        original = self.g.open_tasks

        def slow(list_id):
            result = original(list_id)  # 先讀到「修改前」的資料
            gate.wait(5)
            return result
        self.g.open_tasks = slow
        return gate

    def test_completed_task_not_revived_by_stale_sync(self):
        """同步讀到資料後才完成待辦 > 舊結果被丟掉、待辦不會回來，寫入完成後自動再同步"""
        # Given: 今天一筆待辦，同步進行中且已讀到它
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        gate = self.gate_open_tasks()
        app.refresh()
        pump(app, 0.2)
        # When: 同步還沒結束時完成，之後同步才回來
        app.complete(app.tasks[0])
        pump(app, 0.3)
        reads_before = self.g.calls.count("tasklists")
        gate.set()
        settle(app)
        # Then: 待辦沒有回來，Google 上已完成，且又同步了一次
        self.assertEqual(self.g.tasks[task_id]["status"], "completed")
        self.assertEqual(app.tasks, [])
        self.assertGreater(self.g.calls.count("tasklists"), reads_before)

    def test_detail_edit_after_sync(self):
        """詳細頁改標題、同步完成後才返回 > 清單立刻顯示新標題（修改套用在同步後的資料上）"""
        # Given: 詳細頁改了標題，接著同步一次
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        app.open_detail(app.tasks[0])
        app.detail_title.insert("end", "（急）")
        app.refresh()
        settle(app)
        # When: 返回（自動儲存）
        app.close_detail()
        settle(app)
        # Then: Google 與清單都是新標題
        self.assertEqual(self.g.tasks[task_id]["title"], "寫週報（急）")
        self.assertIn("寫週報（急）", self.list_texts())


# ---------------------------------------------------------------- 新增工作

class AddTaskTest(AppTestCase):
    """底部快捷新增：輸入框（Enter）、最右邊「新增」按鈕、即時辨識結果。"""

    def test_add_task_today(self):
        """輸入標題按 Enter > 新增到月曆選中的那天（今天）、上次用的清單"""
        # Given: 已登入
        self.start()
        # When: 新增
        self.type_new_task("買牛奶")
        # Then: 畫面與 Google 都有
        self.assertIn("買牛奶", self.list_texts())
        self.assertEqual(self.status(), "已新增「買牛奶」到「工作」")
        (raw,) = self.g.by_title("買牛奶")
        self.assertEqual((raw["due"][:10], raw["list_id"]), (self.today.isoformat(), "L1"))

    def test_quick_add_parses_date_and_time(self):
        """輸入「明天 3點 開會」 > 辨識成明天 15:00 的「開會」，狀態列說明放在哪天"""
        # Given: 已登入
        self.start()
        # When: 新增
        self.type_new_task("明天 3點 開會")
        # Then: 欄位正確
        (raw,) = self.g.by_title("開會")
        self.assertEqual(raw["due"][:10], (self.today + dt.timedelta(days=1)).isoformat())
        self.assertEqual(raw["notes"], "⏰ 15:00")
        self.assertEqual(self.status(), "已新增「開會」到「工作」（明天），15:00 提醒")

    def test_list_tag(self):
        """輸入「看牙醫 #個人」 > 放進個人清單"""
        # Given: 已登入
        self.start()
        # When: 新增
        self.type_new_task("看牙醫 #個人")
        # Then: L2
        (raw,) = self.g.by_title("看牙醫")
        self.assertEqual(raw["list_id"], "L2")

    def test_default_due_is_selected_day(self):
        """月曆選了另一天、沒寫日期 > 放在選中的那天"""
        # Given: 選了同月份的另一天
        other = self.today + dt.timedelta(days=1 if self.today.day < 20 else -1)
        app = self.start()
        cal_click(app, other)
        # When: 新增
        self.type_new_task("交報告")
        # Then: 那一天
        (raw,) = self.g.by_title("交報告")
        self.assertEqual(raw["due"][:10], other.isoformat())

    def test_add_button_adds(self):
        """輸入後點最右邊「新增」 > 和按 Enter 一樣新增"""
        # Given: 輸入框有文字
        app = self.start()
        app.entry.focus_force()
        app.update()
        app.entry.insert(0, "買牛奶")
        # When: 點「新增」
        click(app.btn_add)
        settle(app)
        # Then: 已新增
        self.assertEqual(len(self.g.by_title("買牛奶")), 1)
        self.assertIn("買牛奶", self.list_texts())

    def test_add_button_without_text_focuses_entry(self):
        """還沒輸入就點「新增」 > 不新增，游標移到輸入框"""
        # Given: 輸入框只有提示文字
        app = self.start()
        app.cal.focus_force()
        app.update()
        # When: 點「新增」
        click(app.btn_add)
        settle(app)
        # Then: 沒有新增，焦點在輸入框
        self.assertNotIn("add_task", self.g.calls)
        self.assertIs(app.focus_get(), app.entry)

    def test_placeholder_shows_example(self):
        """輸入框沒有文字 > 提示文字示範可以寫日期時間"""
        # Given / When: 啟動
        app = self.start()
        # Then: 提示文字
        self.assertEqual(app.entry.get(), "新增工作，例如：明天 3點 開會")

    def test_chips_while_typing(self):
        """快捷輸入打字時 > 新增列上方即時顯示辨識結果；清空 > 收起"""
        # Given: 已登入
        app = self.start()
        app.entry.focus_force()
        app.update()
        # When: 打字
        app.entry.insert(0, "明天 3點 開會")
        app.entry.event_generate("<KeyRelease>")
        app.update()
        # Then: 顯示辨識結果
        self.assertTrue(app.lbl_quick_chips.winfo_ismapped())
        self.assertEqual(app.lbl_quick_chips.cget("text"), "📅 明天　⏰ 15:00　📁 工作")
        # When: 清空
        app.entry.delete(0, "end")
        app.entry.event_generate("<KeyRelease>")
        app.update()
        # Then: 收起
        self.assertFalse(app.lbl_quick_chips.winfo_ismapped())

    def test_only_date_refused(self):
        """只寫日期時間沒有標題 > 紅字提示，不建立"""
        # Given: 已登入
        self.start()
        # When: 只有日期時間
        self.type_new_task("明天 3點")
        # Then: 擋下
        self.assertEqual(self.status(), "請輸入工作內容（目前只有日期或時間）")
        self.assertNotIn("add_task", self.g.calls)

    def test_add_task_failure_restores_input(self):
        """新增失敗 > 紅字錯誤，輸入框放回原本整行"""
        # Given: 新增會失敗
        app = self.start()
        self.g.fail.add("add_task")
        # When: 新增
        self.type_new_task("明天 3點 開會")
        # Then: 整行還在
        self.assertEqual(self.status(), "add_task failed")
        self.assertEqual(app.entry.get(), "明天 3點 開會")


class NewTaskPageTest(AppTestCase):
    """完整新增頁（點「詳細」）：一次設定標題、詳細資訊、日期、提醒、重複與清單。"""

    def open_page(self):
        app = self.app
        click(app.btn_detail)
        self.assertEqual(app.lbl_date.cget("text"), "新增工作")
        return app

    def test_detail_button_opens_page_with_defaults(self):
        """點「詳細」 > 新增頁開啟，游標在標題，預設今天、不提醒、不重複、上次用的清單"""
        # Given: 已登入
        self.start()
        # When: 點「詳細」
        app = self.open_page()
        # Then: 預設值
        shown = texts(app.detail_view)
        for chip in ("📁 工作 ▾", "📅 今天 ▾", "⏰ 加提醒時間 ▾", "🔁 不重複 ▾", "＋ 建立", "取消"):
            self.assertIn(chip, shown)
        self.assertIs(app.focus_get(), app.draft_title)

    def test_typed_text_is_parsed_into_fields(self):
        """先在輸入框打「明天 3點 開會 #個人」再點「詳細」 > 標題「開會」，日期時間清單填好"""
        # Given: 輸入框有文字
        app = self.start()
        app.entry.focus_force()
        app.update()
        app.entry.insert(0, "明天 3點 開會 #個人")
        # When: 點「詳細」
        self.open_page()
        # Then: 欄位帶入
        self.assertEqual(app.draft_title.get(), "開會")
        shown = texts(app.detail_view)
        for chip in ("📁 個人 ▾", "📅 明天 ▾", "⏰ 15:00 ▾"):
            self.assertIn(chip, shown)

    def test_create_with_all_fields(self):
        """填好標題、詳細資訊、明天、20:00、每天、個人後按「建立」 > 全部寫入，回主畫面跳到明天並記住清單"""
        # Given: 新增頁
        self.start()
        app = self.open_page()
        app.draft_title.insert(0, "繳房租")
        app.draft_notes.insert("1.0", "轉帳給房東")
        # When: 設定各欄位後建立
        click(find_label(app.detail_view, "📅", exact=False))
        click(find_label(popup(app), "明天"))
        click(find_label(app.detail_view, "⏰ 加提醒時間 ▾"))
        type_time(app, "20:00")
        click(find_label(app.detail_view, "🔁 不重複 ▾"))
        click(find_label(popup(app), "每天"))
        app.choose_draft_list(1)
        self.assertEqual(app.draft_title.get(), "繳房租")  # 重畫後輸入中的內容還在
        click(find_label(app.detail_view, "＋ 建立"))
        settle(app)
        # Then: Google 上的欄位、畫面、記住的清單
        tomorrow = self.today + dt.timedelta(days=1)
        (raw,) = self.g.by_title("繳房租")
        self.assertEqual((raw["list_id"], raw["due"][:10]), ("L2", tomorrow.isoformat()))
        self.assertEqual(raw["notes"], "⏰ 20:00\n🔁 每天\n轉帳給房東")
        self.assertTrue(app.main_view.winfo_ismapped())
        self.assertEqual(app.selected, tomorrow)
        self.assertIn("繳房租", self.list_texts())
        self.assertEqual(app.current_list()["id"], "L2")
        self.assertEqual(self.status(), "已新增「繳房租」到「個人」，20:00 提醒")

    def test_enter_in_title_creates(self):
        """在標題按 Enter > 直接建立"""
        # Given: 新增頁打好標題
        self.start()
        app = self.open_page()
        app.draft_title.insert(0, "買牛奶")
        # When: Enter
        key(app.draft_title, "<Return>")
        settle(app)
        # Then: 已建立
        self.assertEqual(len(self.g.by_title("買牛奶")), 1)
        self.assertIsNone(app.draft)

    def test_no_date_goes_undated(self):
        """日期選「無日期」後建立 > 出現在「未排日期」"""
        # Given: 新增頁
        self.start()
        app = self.open_page()
        app.draft_title.insert(0, "整理書櫃")
        # When: 無日期並建立
        click(find_label(app.detail_view, "📅", exact=False))
        click(find_label(popup(app), "無日期"))
        click(find_label(app.detail_view, "＋ 建立"))
        settle(app)
        click(find_label(app.list_frame, "▶ 未排日期（1）"))
        # Then: 沒有日期
        (raw,) = self.g.by_title("整理書櫃")
        self.assertIsNone(raw["due"])
        self.assertIn("整理書櫃", self.list_texts())

    def test_empty_title_refused(self):
        """沒有標題就按「建立」 > 紅字提示，留在新增頁"""
        # Given: 新增頁
        self.start()
        app = self.open_page()
        # When: 建立
        click(find_label(app.detail_view, "＋ 建立"))
        pump(app, 0.2)
        # Then: 擋下
        self.assertEqual(self.status(), "請輸入工作標題")
        self.assertIsNotNone(app.draft)
        self.assertNotIn("add_task", self.g.calls)

    def test_cancel_without_content(self):
        """什麼都沒填就按「取消」 > 不詢問，回主畫面"""
        # Given: 空白的新增頁
        self.start()
        app = self.open_page()
        # When: 取消
        click(find_label(app.detail_view, "取消"))
        # Then: 回主畫面
        self.assertIsNone(app.draft)
        self.assertEqual(self.dialogs, [])
        self.assertTrue(app.main_view.winfo_ismapped())

    def test_cancel_with_content_asks(self):
        """已經打了內容按 Esc > 詢問；選否留下、選是放棄"""
        # Given: 打了標題
        self.start()
        app = self.open_page()
        app.draft_title.insert(0, "買牛奶")
        # When: Esc 並選否
        self.answer["askyesno"] = False
        key(app.draft_title, "<Escape>")
        # Then: 還在
        self.assertEqual(self.dialogs[-1][1], "放棄新增")
        self.assertEqual(app.draft_title.get(), "買牛奶")
        # When: 再按並選是
        self.answer["askyesno"] = True
        key(app.draft_title, "<Escape>")
        # Then: 放棄
        self.assertIsNone(app.draft)
        self.assertNotIn("add_task", self.g.calls)

    def test_create_failure_stays_on_page(self):
        """建立失敗 > 紅字錯誤，留在新增頁、內容不會不見"""
        # Given: 新增會失敗
        self.start()
        self.g.fail.add("add_task")
        app = self.open_page()
        app.draft_title.insert(0, "買牛奶")
        # When: 建立
        click(find_label(app.detail_view, "＋ 建立"))
        settle(app)
        # Then: 還在
        self.assertEqual(self.status(), "add_task failed")
        self.assertEqual(app.draft_title.get(), "買牛奶")

    def test_new_list_from_page(self):
        """清單選「新增清單」並輸入名稱 > 建立清單並選取，之後新增預設用它"""
        # Given: 新增頁
        self.start()
        app = self.open_page()
        # When: 新增清單
        with unittest.mock.patch.object(daynote, "ask_text", lambda *a, **k: "旅行"):
            app.choose_draft_list(len(app.lists))
            settle(app)
        # Then: 已選取
        self.assertIn("📁 旅行 ▾", texts(app.detail_view))
        self.assertEqual(app.current_list()["title"], "旅行")
        self.assertEqual(self.status(), "已建立清單「旅行」，新增的工作會放進這個清單")

    def test_sync_keeps_page(self):
        """新增頁開著時背景同步 > 標題與輸入中的內容不被蓋掉"""
        # Given: 新增頁打了標題
        self.start()
        app = self.open_page()
        app.draft_title.insert(0, "買牛奶")
        # When: 同步
        app.refresh()
        settle(app)
        # Then: 不變
        self.assertEqual(app.lbl_date.cget("text"), "新增工作")
        self.assertEqual(app.draft_title.get(), "買牛奶")

    def test_logged_out(self):
        """未登入點「詳細」 > 紅字提示先登入，不開新增頁"""
        # Given: 未登入
        self.g = StoreGoogle(logged_in=False)
        app = self.start()
        # When: 點「詳細」
        click(app.btn_detail)
        # Then: 提示
        self.assertEqual(self.status(), "請先登入 Google（右上角「登入」）")
        self.assertIsNone(app.draft)

    def test_time_picker_has_no_presets_and_hint(self):
        """打開時間選擇器 > 沒有預設時間、游標在時間欄位、灰字顯示輸入範例"""
        # Given: 新增頁
        self.start()
        app = self.open_page()
        # When: 打開時間選擇器
        click(find_label(app.detail_view, "⏰ 加提醒時間 ▾"))
        picker = popup(app)
        # Then: 只有輸入欄
        shown = texts(picker)
        self.assertFalse(any(re_time(t) for t in shown), shown)
        self.assertIn("不設時間", shown)
        self.assertIsInstance(app.focus_get(), tk.Entry)
        self.assertEqual(find_label(picker, "例如 11、15:30、下午3點，按 Enter 確定").cget("fg"), daynote.GRAY)

    def test_time_picker_invalid_input(self):
        """時間欄位輸入看不懂的內容 > 同一行改紅字，不套用"""
        # Given: 時間選擇器
        self.start()
        app = self.open_page()
        click(find_label(app.detail_view, "⏰ 加提醒時間 ▾"))
        # When: 無效時間
        type_time(app, "25:00")
        # Then: 紅字、未設定
        hint = find_label(popup(app), "看不懂這個時間。例如 11、15:30、下午3點，按 Enter 確定")
        self.assertEqual(hint.cget("fg"), daynote.RED)
        self.assertIsNone(app.draft["time"])


# ---------------------------------------------------------------- 完成、復原

class CompleteTest(AppTestCase):

    def test_complete_and_undo(self):
        """點圓圈完成 > 從清單移除並提供復原；按復原 > 放回清單、Google 取消完成"""
        # Given: 今天有一筆任務
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 點完成圓圈
        click(circle_of(app, "寫週報"))
        pump(app, 0.4)
        settle(app)
        # Then: 已完成、出現復原
        self.assertNotIn("寫週報", self.list_texts())
        self.assertEqual(self.g.tasks[task_id]["status"], "completed")
        self.assertEqual(self.status(), "已完成「寫週報」")
        self.assertTrue(app.btn_undo.winfo_ismapped())
        # When: 按復原
        click(app.btn_undo)
        wait_threads(app)
        # Then: 放回
        self.assertIn("寫週報", self.list_texts())
        self.assertEqual(self.g.tasks[task_id]["status"], "needsAction")

    def test_complete_parent_with_children(self):
        """完成父工作 > 子工作一併完成"""
        # Given: 父工作有子工作
        parent = self.g.seed("搬家", self.today)
        child = self.g.seed("打包", None, parent=parent)
        app = self.start()
        self.assertIn("打包", self.list_texts())
        # When: 完成父工作
        click(circle_of(app, "搬家"))
        pump(app, 0.4)
        settle(app)
        # Then: 兩筆都完成
        self.assertEqual(self.g.tasks[parent]["status"], "completed")
        self.assertEqual(self.g.tasks[child]["status"], "completed")
        self.assertNotIn("打包", self.list_texts())

    def test_complete_recurring_spawns_next(self):
        """完成 🔁 每天 的任務 > 自動建立明天的下一期"""
        # Given: 每天重複的任務
        self.g.seed("吃藥", self.today, notes="🔁 每天")
        app = self.start()
        # When: 完成
        click(circle_of(app, "吃藥"))
        pump(app, 0.4)
        settle(app)
        # Then: 明天有下一期
        open_ones = [t for t in self.g.by_title("吃藥") if t["status"] == "needsAction"]
        self.assertEqual(len(open_ones), 1)
        self.assertEqual(open_ones[0]["due"][:10], (self.today + dt.timedelta(days=1)).isoformat())
        self.assertEqual(open_ones[0]["notes"], "🔁 每天")

    def test_complete_failure_puts_back(self):
        """完成失敗 > 任務放回清單並顯示錯誤"""
        # Given: 完成會失敗
        self.g.seed("寫週報", self.today)
        app = self.start()
        self.g.fail.add("complete_task")
        # When: 點完成
        click(circle_of(app, "寫週報"))
        pump(app, 0.4)
        settle(app)
        # Then: 放回
        self.assertIn("寫週報", self.list_texts())
        self.assertEqual(self.status(), "complete_task failed")


# ---------------------------------------------------------------- 詳細頁

class DetailTest(AppTestCase):

    def open(self, title):
        click(find_label(self.app.list_frame, title))
        self.assertEqual(self.app.lbl_date.cget("text"), "工作詳細資訊")

    def test_edit_title_and_notes(self):
        """詳細頁改標題與詳細資訊後按儲存 > 送出修改，返回主畫面顯示新標題與內文預覽"""
        # Given: 一筆任務
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 開詳細頁修改後按「儲存」
        self.open("寫週報")
        app.detail_title.delete(0, "end")
        app.detail_title.insert(0, "寫月報")
        app.detail_notes.insert("1.0", "附上圖表")
        click(find_label(app.detail_view, "儲存"))
        settle(app)
        # Then: Google 與畫面都更新
        self.assertEqual(self.g.tasks[task_id]["title"], "寫月報")
        self.assertEqual(self.g.tasks[task_id]["notes"], "附上圖表")
        self.assertIn("寫月報", self.list_texts())
        self.assertIn("附上圖表", self.list_texts())
        self.assertEqual(self.status(), "已儲存「寫月報」")

    def test_escape_saves(self):
        """詳細頁按 Esc > 自動儲存並返回"""
        # Given: 一筆任務
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 修改標題後按 Esc
        self.open("寫週報")
        app.detail_title.insert("end", "（急）")
        key(app.detail_title, "<Escape>")
        settle(app)
        # Then: 已儲存
        self.assertEqual(self.g.tasks[task_id]["title"], "寫週報（急）")
        self.assertTrue(app.main_view.winfo_ismapped())

    def test_back_button_saves(self):
        """詳細頁按「← 返回」 > 自動儲存並回到主畫面"""
        # Given: 一筆任務
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 修改標題後按返回
        self.open("寫週報")
        app.detail_title.insert("end", "（急）")
        click(app.btn_detail_back)
        settle(app)
        # Then: 已儲存、回到主畫面
        self.assertEqual(self.g.tasks[task_id]["title"], "寫週報（急）")
        self.assertTrue(app.main_view.winfo_ismapped())

    def test_no_change_no_request(self):
        """詳細頁沒有修改就返回 > 不送出任何修改"""
        # Given: 一筆任務
        self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 開了直接返回
        self.open("寫週報")
        click(find_label(app.detail_view, "儲存"))
        pump(app, 0.2)
        # Then: 沒有 update
        self.assertNotIn("update_task", self.g.calls)

    def test_change_date_and_time(self):
        """詳細頁選明天、15:00 > 任務移到明天並帶提醒時間"""
        # Given: 今天的任務
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 改日期與時間
        self.open("寫週報")
        click(find_label(app.detail_view, "📅", exact=False))
        click(find_label(popup(app), "明天"))
        click(find_label(app.detail_view, "⏰ 加提醒時間 ▾"))
        type_time(app, "1500")
        click(find_label(app.detail_view, "儲存"))
        settle(app)
        # Then: 移到明天
        tomorrow = self.today + dt.timedelta(days=1)
        self.assertEqual(self.g.tasks[task_id]["due"][:10], tomorrow.isoformat())
        self.assertEqual(self.g.tasks[task_id]["notes"], "⏰ 15:00")
        self.assertNotIn("寫週報", self.list_texts())

    def test_set_recur_then_complete(self):
        """詳細頁設定「每天」後按標示為完成 > 建立明天的下一期"""
        # Given: 一般任務
        self.g.seed("吃藥", self.today)
        app = self.start()
        # When: 設定重複並完成
        self.open("吃藥")
        click(find_label(app.detail_view, "🔁 不重複 ▾"))
        click(find_label(popup(app), "每天"))
        self.assertIn("🔁 每天 ▾", texts(app.detail_view))
        click(find_label(app.detail_view, "✓ 標示為完成"))
        wait_threads(app)
        # Then: 下一期帶規則
        open_ones = [t for t in self.g.by_title("吃藥") if t["status"] == "needsAction"]
        self.assertEqual(len(open_ones), 1)
        self.assertEqual(open_ones[0]["notes"], "🔁 每天")

    def test_custom_recur_invalid_shows_hint(self):
        """自訂重複輸入看不懂的規則 > 顯示範例提示，不套用"""
        # Given: 詳細頁
        self.g.seed("運動", self.today)
        app = self.start()
        self.open("運動")
        click(find_label(app.detail_view, "🔁 不重複 ▾"))
        picker = popup(app)
        # When: 自訂輸入無效規則
        entry = next(w for w in descendants(picker) if isinstance(w, tk.Entry))
        entry.insert(0, "每兩週")
        click(find_label(picker, "確定"))
        # Then: 提示，選擇器仍開著
        self.assertIn("例如：每天", " ".join(texts(picker)))
        self.assertTrue(picker.winfo_exists())

    def test_skip_period(self):
        """重複任務按「略過這一期」 > 建立下一期並刪除這一期，不留完成紀錄"""
        # Given: 每天重複的任務
        task_id = self.g.seed("吃藥", self.today, notes="🔁 每天")
        app = self.start()
        # When: 略過
        self.open("吃藥")
        click(find_label(app.detail_view, "略過這一期"))
        settle(app)
        # Then: 這一期被刪除（不是完成），明天有下一期
        self.assertTrue(self.g.tasks[task_id].get("deleted"))
        self.assertEqual(self.g.tasks[task_id]["status"], "needsAction")
        tomorrow = self.today + dt.timedelta(days=1)
        self.assertTrue(any(t["due"][:10] == tomorrow.isoformat() and not t.get("deleted")
                            for t in self.g.by_title("吃藥")))
        self.assertTrue(self.status().startswith("已略過"))

    def test_delete_waits_for_undo_window(self):
        """刪除一般待辦 > 不詢問、立刻從畫面消失；復原時間過了才從 Google 刪除（含子工作）"""
        # Given: 有子工作的任務，復原時間縮短為 1 秒
        parent = self.g.seed("搬家", self.today)
        child = self.g.seed("打包", None, parent=parent)
        app = self.start()
        self.open("搬家")
        with unittest.mock.patch.object(daynote, "UNDO_SECONDS", 1):
            # When: 刪除
            click(find_label(app.detail_view, "刪除"))
            app.update()
            # Then: 沒有詢問、畫面已拿掉、Google 上還在、狀態列可復原
            self.assertEqual([d for d in self.dialogs if d[0] == "askyesno"], [])
            self.assertNotIn("搬家", self.list_texts())
            self.assertNotIn("delete_task", self.g.calls)
            self.assertEqual(self.status(), "已刪除「搬家」")
            self.assertTrue(app.btn_undo.winfo_ismapped())
            # When: 復原時間過了
            pump(app, 1.3)
            settle(app)
        # Then: 都刪除
        self.assertTrue(self.g.tasks[parent].get("deleted"))
        self.assertTrue(self.g.tasks[child].get("deleted"))

    def test_delete_undo(self):
        """刪除後按「復原」 > 回到清單，Google 上不會被刪"""
        # Given: 剛刪除一筆
        self.g.seed("寫週報", self.today)
        app = self.start()
        self.open("寫週報")
        click(find_label(app.detail_view, "刪除"))
        # When: 復原，並等過原本的復原時間
        click(app.btn_undo)
        pump(app, daynote.UNDO_SECONDS + 0.3)
        settle(app)
        # Then: 還在
        self.assertIn("寫週報", self.list_texts())
        self.assertNotIn("delete_task", self.g.calls)
        self.assertEqual(self.status(), "已復原「寫週報」")

    def test_ctrl_z_undoes_delete(self):
        """刪除後按 Ctrl+Z > 等同按「復原」"""
        # Given: 剛刪除一筆
        self.g.seed("寫週報", self.today)
        app = self.start()
        self.open("寫週報")
        click(find_label(app.detail_view, "刪除"))
        # When: Ctrl+Z
        press_key(app, "<Control-z>")
        app.update()
        # Then: 回到清單
        self.assertIn("寫週報", self.list_texts())

    def test_delete_recurring_asks_and_can_cancel(self):
        """刪除週期待辦 > 先詢問（刪了不會再有下一期）；按取消 > 不刪除，停在詳細頁"""
        # Given: 每週一的週報
        self.g.seed("交週報", self.today, notes="🔁 每週一")
        app = self.start()
        self.answer["askyesno"] = False
        # When: 刪除但取消
        self.open("交週報")
        click(find_label(app.detail_view, "刪除"))
        pump(app, 0.2)
        # Then: 有詢問、沒刪
        self.assertIn("不會再產生下一期", self.dialogs[-1][2])
        self.assertIsNone(app.pending_delete)
        self.assertTrue(app.detail_view.winfo_ismapped())

    def test_pending_delete_not_revived_by_sync(self):
        """刪除後、送出前剛好同步 > 那筆不會被同步帶回畫面"""
        # Given: 剛刪除一筆（還在復原時間內）
        self.g.seed("寫週報", self.today)
        app = self.start()
        self.open("寫週報")
        click(find_label(app.detail_view, "刪除"))
        # When: 同步
        app.refresh()
        settle(app)
        # Then: 仍不在清單
        self.assertNotIn("寫週報", self.list_texts())

    def test_close_commits_pending_delete(self):
        """刪除後馬上關閉 DayNote > 關閉前送出刪除，不會因為還在復原時間內就遺失"""
        # Given: 剛刪除一筆
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        self.open("寫週報")
        click(find_label(app.detail_view, "刪除"))
        # When: 關閉
        app.close(ask=False)
        # Then: Google 上已刪除
        self.assertTrue(self.g.tasks[task_id].get("deleted"))

    def test_task_menu_pick_date(self):
        """待辦按右鍵選「選日期…」 > 開日期選擇器，選了就改到那天"""
        # Given: 今天的待辦
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        target = self.today + dt.timedelta(days=5)
        # When: 右鍵「選日期…」並選 5 天後
        app._build_task_menu(app.tasks[0]).invoke("選日期…")
        app.update()
        app.task_date_picker.pick(target)
        settle(app)
        # Then: Google 上改到那天
        self.assertTrue(self.g.tasks[task_id]["due"].startswith(target.isoformat()))

    def test_task_menu_delete(self):
        """待辦按右鍵選「刪除」 > 從清單拿掉，可以復原"""
        # Given: 今天的待辦
        self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 右鍵刪除
        app._build_task_menu(app.tasks[0]).invoke("刪除")
        app.update()
        # Then: 拿掉、可復原
        self.assertNotIn("寫週報", self.list_texts())
        self.assertTrue(app.btn_undo.winfo_ismapped())

    def test_add_subtask(self):
        """詳細頁新增子工作 > 顯示在詳細頁與主畫面父工作下方"""
        # Given: 一筆任務
        parent = self.g.seed("搬家", self.today)
        app = self.start()
        self.open("搬家")
        # When: 輸入子工作按 Enter
        entry = [w for w in descendants(app.detail_view) if isinstance(w, tk.Entry)][-1]
        entry.focus_force()
        app.update()
        entry.insert(0, "叫貨車")
        key(entry, "<Return>")
        settle(app)
        # Then: 子工作建立
        (raw,) = self.g.by_title("叫貨車")
        self.assertEqual(raw["parent"], parent)
        self.assertIn("叫貨車", texts(app.detail_view))
        click(find_label(app.detail_view, "儲存"))
        self.assertIn("叫貨車", self.list_texts())

    def test_subtask_detail_has_no_recur(self):
        """開子工作的詳細頁 > 不顯示重複選單，顯示子工作說明"""
        # Given: 子工作
        parent = self.g.seed("搬家", self.today)
        self.g.seed("打包", None, parent=parent)
        app = self.start()
        # When: 開子工作
        self.open("打包")
        # Then: 沒有 🔁
        shown = texts(app.detail_view)
        self.assertFalse(any(t.startswith("🔁") for t in shown))
        self.assertIn("（這是子工作，在主畫面會顯示在父工作下方）", shown)

    def test_save_failure_restores(self):
        """儲存失敗 > 畫面還原為原標題並顯示錯誤"""
        # Given: 修改會失敗
        self.g.seed("寫週報", self.today)
        app = self.start()
        self.g.fail.add("update_task")
        # When: 改標題後儲存
        self.open("寫週報")
        app.detail_title.delete(0, "end")
        app.detail_title.insert(0, "寫月報")
        click(find_label(app.detail_view, "儲存"))
        settle(app)
        # Then: 還原
        self.assertIn("寫週報", self.list_texts())
        self.assertEqual(self.status(), "update_task failed")

    def test_close_with_unsaved_detail_saves(self):
        """詳細頁有修改時關閉 DayNote 並選「是」 > 先儲存再關閉，狀態寫入暫存 data"""
        # Given: 詳細頁有未儲存的修改
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        self.open("寫週報")
        app.detail_title.insert("end", "！")
        # When: 關閉
        app.close()
        # Then: 已儲存並關閉
        self.assertEqual(self.g.tasks[task_id]["title"], "寫週報！")
        self.assertEqual(self.dialogs[-1][0], "askyesnocancel")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "state.json")))

    def test_close_waits_for_pending_completion(self):
        """剛完成待辦、還在送出時就關閉 > 等送到 Google 才結束，完成不會遺失"""
        # Given: 完成要 0.5 秒才送到
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        original = self.g.complete_task

        def slow(*args):
            time.sleep(0.5)
            original(*args)
        self.g.complete_task = slow
        app.complete(app.tasks[0])
        # When: 立刻關閉
        app.close(ask=False)
        # Then: 關閉返回時 Google 上已完成
        self.assertEqual(self.g.tasks[task_id]["status"], "completed")


# ---------------------------------------------------------------- 月曆與清單顯示

class CalendarTest(AppTestCase):

    def test_click_calendar_day(self):
        """點月曆上的另一天 > 標題與清單切到那天，新增日期跟著變"""
        # Given: 同一週內另一天有任務（避免跨月觸發換月同步）
        other = self.today + dt.timedelta(days=1 if self.today.day < 20 else -1)
        self.g.seed("交報告", other)
        app = self.start()
        # When: 點那一天
        cal_click(app, other)
        # Then: 切換
        self.assertEqual(app.selected, other)
        self.assertIn("交報告", self.list_texts())
        self.assertIn(f"{other.month}月{other.day}日（1）", self.list_texts())
        self.assertEqual(app.new_due, other)

    def test_month_navigation_and_today(self):
        """按 ▶ 到下個月 > 重新同步該月；按「今天」 > 回到今天"""
        # Given: 已啟動
        app = self.start()
        start_view = app.view
        # When: 下個月
        click(find_label(app.main_view, "▶"))
        settle(app)
        # Then: 月份前進
        y, m = start_view
        self.assertEqual(app.view, (y + m // 12, m % 12 + 1))
        self.assertEqual(app.lbl_month.cget("text"), f"{app.view[0]} 年 {app.view[1]} 月")
        # When: 今天
        click(find_label(app.main_view, "今天"))
        settle(app)
        # Then: 回到今天
        self.assertEqual(app.selected, self.today)
        self.assertEqual(app.view, start_view)

    def test_compact_calendar(self):
        """按「只看這週」 > 月曆只剩一週，◀ ▶ 改為切換週，設定會記住"""
        # Given: 已啟動（整月）
        app = self.start()
        self.assertEqual(app.btn_compact.cget("text"), "只看這週 ▴")
        # When: 收合後按 ▶
        click(app.btn_compact)
        # Then: 只有一週
        self.assertEqual(len(app.weeks), 1)
        self.assertEqual(app.btn_compact.cget("text"), "展開整月 ▾")
        self.assertTrue(app.state_data["compact_calendar"])
        click(find_label(app.main_view, "▶"))
        settle(app)
        self.assertEqual(app.selected, self.today + dt.timedelta(days=7))

    def test_first_run_defaults_to_week(self):
        """第一次使用 > 月曆預設只顯示這一週；點月份標題 > 展開整月並記住"""
        # Given: 沒有任何設定
        self.first_run = True
        # When: 啟動
        app = self.start()
        # Then: 只有這一週，按鈕寫著可以展開
        self.assertEqual(len(app.weeks), 1)
        self.assertIn(self.today, app.weeks[0])
        self.assertEqual(app.btn_compact.cget("text"), "展開整月 ▾")
        # When: 點月份標題
        click(app.lbl_month)
        # Then: 整月，並記住
        self.assertGreaterEqual(len(app.weeks), 4)
        self.assertFalse(app.state_data["compact_calendar"])

    def test_calendar_day_tooltip(self):
        """滑鼠停在有待辦與行程的日期上 > 顯示當天摘要；移到沒有安排的日期 > 不顯示"""
        # Given: 今天兩筆待辦、一個行程
        day = self.today.isoformat()
        self.g.events_by_cal["primary"].append(
            {"summary": "週會", "start": {"dateTime": f"{day}T15:00:00"}, "end": {"dateTime": f"{day}T16:00:00"}})
        self.g.seed("寫週報", self.today)
        self.g.seed("回信", self.today)
        app = self.start()
        row = next(i for i, week in enumerate(app.weeks) if self.today in week)
        col = app.weeks[row].index(self.today)
        x, y = col * daynote.CELL_W + daynote.CELL_W // 2, daynote.HEAD_H + row * daynote.CELL_H + 5
        # When: 滑鼠移到今天並停留
        app.cal.event_generate("<Motion>", x=x, y=y)
        pump(app, 0.7)
        # Then: 顯示摘要
        tip = app.cal_tooltip.tip
        self.assertIsNotNone(tip)
        self.assertEqual(texts(tip), ["2 件待辦 · 1 個行程"])
        # When: 移到標題列（不在日期格上）
        app.cal.event_generate("<Motion>", x=x, y=2)
        pump(app, 0.7)
        # Then: 收起
        self.assertIsNone(app.cal_tooltip.tip)

    def test_date_hero_and_page_title(self):
        """主畫面顯示大日期（數字＋星期＋今天）；打開新增頁 > 改成頁面標題；返回 > 大日期回來"""
        # Given: 已啟動
        app = self.start()
        # Then: 大日期
        self.assertEqual(app.lbl_day.cget("text"), str(self.today.day))
        self.assertEqual(app.lbl_relative.cget("text"), "今天")
        self.assertTrue(app.hero.winfo_ismapped())
        # When: 打開新增頁
        app.open_new_task()
        app.update()
        # Then: 頁面標題
        self.assertFalse(app.hero.winfo_ismapped())
        self.assertTrue(app.lbl_date.winfo_ismapped())
        # When: 返回
        app.cancel_new_task(ask=False)
        app.update()
        # Then: 大日期回來
        self.assertTrue(app.hero.winfo_ismapped())

    def test_task_card_hover(self):
        """滑鼠移到待辦卡片 > 卡片與內容換成滑過色；移開 > 恢復"""
        # Given: 今天一筆任務
        self.g.seed("寫週報", self.today)
        app = self.start()
        app.update()
        label = find_label(app.list_frame, "寫週報")
        row = label.master.master.master  # Label → 文字區 → 卡片內容 → 卡片 Canvas
        # When: 滑鼠移入
        row.event_generate("<Enter>")
        app.update()
        # Then: 滑過色
        self.assertEqual(label.cget("bg"), daynote.CARD_HOVER)
        # When: 移出（座標在卡片外）
        row.event_generate("<Leave>", x=-5, y=-5)
        app._paint_card(row, daynote.CARD)  # 測試環境的 Leave 不帶螢幕座標，直接呼叫恢復
        # Then: 恢復卡片色
        self.assertEqual(label.cget("bg"), daynote.CARD)

    def test_keyboard_navigation(self):
        """方向鍵與 Home > 前後一天、前後一週、回到今天"""
        # Given: 月曆取得焦點
        app = self.start()
        # When / Then: 逐一按鍵
        for sequence, expected in (("<Right>", 1), ("<Left>", 0), ("<Down>", 7), ("<Up>", 0)):
            key(app.cal, sequence)
            settle(app)
            self.assertEqual(app.selected, self.today + dt.timedelta(days=expected), sequence)
        key(app.cal, "<Right>")
        settle(app)
        key(app.cal, "<Home>")
        settle(app)
        self.assertEqual(app.selected, self.today)

    def test_f5_refresh(self):
        """按 F5 > 重新同步"""
        # Given: 已啟動
        app = self.start()
        before = self.g.calls.count("tasklists")
        # When: F5
        key(app.cal, "<F5>")
        settle(app)
        # Then: 又讀了一次
        self.assertEqual(self.g.calls.count("tasklists"), before + 1)

    def test_overdue_section_toggle(self):
        """有逾期任務 > 今天最上方顯示「逾期」區，可收合"""
        # Given: 昨天沒做完的任務
        self.g.seed("回信", self.today - dt.timedelta(days=1))
        app = self.start()
        # Then: 預設展開
        self.assertIn("▼ 逾期（1）", self.list_texts())
        self.assertIn("回信", self.list_texts())
        # When: 收合
        click(find_label(app.list_frame, "▼ 逾期（1）"))
        # Then: 隱藏任務
        self.assertIn("▶ 逾期（1）", self.list_texts())
        self.assertNotIn("回信", self.list_texts())

    def test_events_and_holidays(self):
        """當天有日曆行程與國定假日 > 清單顯示行程時間與假日名稱"""
        # Given: 今天有全天行程與放假的節日
        day = self.today.isoformat()
        tomorrow = (self.today + dt.timedelta(days=1)).isoformat()
        self.g.events_by_cal["primary"].append(
            {"summary": "家庭聚餐", "start": {"date": day}, "end": {"date": tomorrow}})
        self.g.events_by_cal[HOLIDAY_CAL].append(
            {"summary": "測試節", "description": "國定假日", "start": {"date": day}, "end": {"date": tomorrow}})
        # When: 啟動
        self.start()
        # Then: 顯示
        shown = self.list_texts()
        self.assertIn("家庭聚餐", shown)
        self.assertIn("全天", shown)
        self.assertIn("◆ 測試節（放假）", shown)

    def test_tasks_sorted_by_time(self):
        """同一天有提醒時間的任務 > 依時間排在前面"""
        # Given: 沒時間、18:00、09:00 三筆
        self.g.seed("沒時間", self.today)
        self.g.seed("晚上", self.today, notes="⏰ 18:00")
        self.g.seed("早上", self.today, notes="⏰ 09:00")
        # When: 啟動
        self.start()
        # Then: 早上 → 晚上 → 沒時間
        shown = [t for t in self.list_texts() if t in ("沒時間", "晚上", "早上")]
        self.assertEqual(shown, ["早上", "晚上", "沒時間"])

    def test_events_and_tasks_share_one_timeline(self):
        """行程與有時間的待辦 > 合成一條時間軸依時間排列，時間顯示在右側欄，副標題不重複"""
        # Given: 15:00 的行程、14:00 與 16:00 的待辦、一筆沒時間
        day = self.today.isoformat()
        self.g.events_by_cal["primary"].append(
            {"summary": "週會", "start": {"dateTime": f"{day}T15:00:00"}, "end": {"dateTime": f"{day}T16:00:00"}})
        self.g.seed("準備簡報", self.today, notes="⏰ 14:00")
        self.g.seed("回信", self.today, notes="⏰ 16:00")
        self.g.seed("沒時間", self.today)
        # When: 啟動
        self.start()
        # Then: 14:00 準備簡報 → 15:00 週會 → 16:00 回信 → 沒時間；時間欄獨立顯示
        shown = self.list_texts()
        order = [t for t in shown if t in ("準備簡報", "週會", "回信", "沒時間")]
        self.assertEqual(order, ["準備簡報", "週會", "回信", "沒時間"])
        self.assertEqual([t for t in shown if t in ("14:00", "15:00", "16:00")], ["14:00", "15:00", "16:00"])
        self.assertNotIn("⏰ 14:00 · 工作", shown)
        self.assertIn("工作", shown)

    def test_right_click_menu_moves_to_tomorrow(self):
        """待辦按右鍵選「移到明天」 > 從今天消失、Google 上的日期改成明天"""
        # Given: 今天一筆任務
        task_id = self.g.seed("寫週報", self.today)
        app = self.start()
        task = next(t for t in app.tasks if t["id"] == task_id)
        label = find_label(app.list_frame, "寫週報")
        # When: 右鍵選單選「移到明天」
        self.assertTrue(label.bind("<Button-3>"))  # 列上有右鍵選單
        app._build_task_menu(task).invoke("移到明天")
        settle(app)
        # Then: 今天沒有了、日期改成明天
        tomorrow = self.today + dt.timedelta(days=1)
        self.assertNotIn("寫週報", self.list_texts())
        self.assertEqual(self.g.tasks[task_id]["due"], f"{tomorrow.isoformat()}T00:00:00.000Z")
        self.assertEqual(self.status(), "已把「寫週報」移到明天")

    def test_reschedule_undo(self):
        """改日期後按「復原」 > 日期、提醒、重複全部還原"""
        # Given: 今天 15:00、每天重複，選了「不排日期」
        task_id = self.g.seed("量血壓", self.today, notes="⏰ 15:00\n🔁 每天\n記在本子上")
        app = self.start()
        task = next(t for t in app.tasks if t["id"] == task_id)
        app._build_task_menu(task).invoke("不排日期")
        settle(app)
        self.assertTrue(app.btn_undo.winfo_ismapped())
        # When: 按復原
        click(app.btn_undo)
        settle(app)
        # Then: Google 與畫面都還原
        self.assertEqual(self.g.tasks[task_id]["due"], f"{self.today.isoformat()}T00:00:00.000Z")
        self.assertEqual(self.g.tasks[task_id]["notes"], "⏰ 15:00\n🔁 每天\n記在本子上")
        self.assertIn("量血壓", self.list_texts())
        self.assertEqual(self.status(), "已復原")
        self.assertFalse(app.btn_undo.winfo_ismapped())

    def test_right_click_menu_disables_current_day(self):
        """今天的待辦 > 右鍵選單的「移到今天」不能按"""
        # Given: 今天一筆任務
        self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 建立右鍵選單
        menu = app._build_task_menu(app.tasks[0])
        # Then: 今天不能按、明天可以
        self.assertEqual(menu.entrycget("移到今天", "state"), "disabled")
        self.assertEqual(menu.entrycget("移到明天", "state"), "normal")

    def test_move_to_no_date_clears_time_and_recur(self):
        """有提醒與重複的待辦選「不排日期」 > 日期、⏰、🔁 一併清掉，詳細內文保留"""
        # Given: 今天 15:00、每天重複
        task_id = self.g.seed("量血壓", self.today, notes="⏰ 15:00\n🔁 每天\n記在本子上")
        app = self.start()
        task = next(t for t in app.tasks if t["id"] == task_id)
        # When: 選「不排日期」
        app._build_task_menu(task).invoke("不排日期")
        settle(app)
        # Then: 清掉日期與 metadata
        self.assertIsNone(self.g.tasks[task_id]["due"])
        self.assertEqual(self.g.tasks[task_id]["notes"], "記在本子上")

    def test_move_all_overdue_to_today(self):
        """逾期區按「全部移到今天」 > 所有逾期待辦改成今天，逾期區消失"""
        # Given: 兩筆逾期
        a = self.g.seed("回信", self.today - dt.timedelta(days=1))
        b = self.g.seed("繳費", self.today - dt.timedelta(days=3))
        app = self.start()
        # When: 按「全部移到今天」
        click(app.btn_overdue_today)
        settle(app)
        # Then: 都改成今天、逾期區不見
        expected = f"{self.today.isoformat()}T00:00:00.000Z"
        self.assertEqual((self.g.tasks[a]["due"], self.g.tasks[b]["due"]), (expected, expected))
        self.assertFalse(any(t.startswith("▼ 逾期") for t in self.list_texts()))
        self.assertEqual(self.status(), "已把 2 件移到今天")

    def test_reschedule_failure_restores(self):
        """改日期送出失敗 > 畫面還原、狀態列顯示錯誤"""
        # Given: 今天一筆任務，Google 更新會失敗
        self.g.seed("寫週報", self.today)
        app = self.start()
        self.g.fail.add("update_task")
        # When: 移到明天
        app._build_task_menu(app.tasks[0]).invoke("移到明天")
        settle(app)
        # Then: 仍在今天、紅字錯誤
        self.assertIn("寫週報", self.list_texts())
        self.assertEqual(self.status(), "update_task failed")

    def test_late_task_time_is_red(self):
        """今天時間已過還沒做的待辦 > 右側時間改紅色；還沒到的維持灰色"""
        if dt.datetime.now().time() < dt.time(0, 1) or dt.datetime.now().time() >= dt.time(23, 59):
            self.skipTest("午夜交界無法同時建立「已過」與「未到」的時間")
        # Given: 00:00（已過）與 23:59（未到）各一筆
        self.g.seed("早就該做", self.today, notes="⏰ 00:00")
        self.g.seed("晚點再做", self.today, notes="⏰ 23:59")
        # When: 啟動
        app = self.start()
        # Then: 已過的紅色、未到的灰色
        self.assertEqual(find_label(app.list_frame, "00:00").cget("fg"), daynote.RED)
        self.assertEqual(find_label(app.list_frame, "23:59").cget("fg"), daynote.GRAY)

    def test_scrollbar_hidden_when_list_fits(self):
        """清單沒超出 > 不畫捲軸；超出 > 畫出捲軸"""
        # Given: 只有一筆任務
        self.g.seed("寫週報", self.today)
        app = self.start()
        app.update()
        # Then: 不需要捲軸
        self.assertFalse(app.list_scrollbar.is_needed())
        self.assertEqual(app.list_scrollbar.find_all(), ())
        # When: 加到 30 筆後重新同步
        for i in range(30):
            self.g.seed(f"工作 {i}", self.today)
        app.refresh()
        settle(app)
        app.update()
        # Then: 畫出捲軸，且在預設視窗寬度下看得到（沒被清單擠掉）
        self.assertTrue(app.list_scrollbar.is_needed())
        self.assertNotEqual(app.list_scrollbar.find_all(), ())
        self.assertGreater(app.list_scrollbar.winfo_width(), 1)
        self.assertTrue(app.list_scrollbar.winfo_ismapped())

    def test_next_up_shows_upcoming_item(self):
        """今天還有未到的待辦 > 大標題下方顯示「下一件」；切到別天 > 收起"""
        # Given: 今天 23:59 的待辦（測試執行時一定還沒到）
        self.g.seed("收尾", self.today, notes="⏰ 23:59")
        # When: 啟動
        app = self.start()
        # Then: 顯示下一件
        self.assertEqual(app.lbl_next.cget("text"), "下一件：23:59 收尾")
        self.assertTrue(app.lbl_next.winfo_ismapped())
        # When: 切到明天
        app.select(self.today + dt.timedelta(days=1))
        settle(app)
        # Then: 收起
        self.assertFalse(app.lbl_next.winfo_ismapped())

    def test_empty_day_teaches_quick_add(self):
        """選中沒有任務的一天 > 顯示「這天沒有任務」與新增方式的提示"""
        # Given: 沒有任何任務
        # When: 啟動
        self.start()
        # Then: 空狀態附帶提示
        shown = self.list_texts()
        self.assertIn("這天沒有任務", shown)
        self.assertIn("在下方輸入框打字新增，例如：明天 3點 開會", shown)

    def test_first_run_tip_shown_once(self):
        """第一次同步成功 > 狀態列提示快捷鍵並記錄；再同步 > 不再提示"""
        # Given: 第一次使用（沒有 state.json）
        self.first_run = True
        self.g.seed("寫週報", self.today)
        # When: 啟動
        app = self.start()
        # Then: 狀態列是提示，且已記錄
        self.assertEqual(self.status(), daynote.FIRST_RUN_TIP)
        with open(os.path.join(self.tmp, "state.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["tips_shown"])
        # When: 再同步一次
        app.refresh()
        settle(app)
        # Then: 換回同步時間
        self.assertTrue(self.status().startswith("已同步"))

    def test_next_up_hidden_without_timed_items(self):
        """今天只有沒時間的待辦 > 不顯示「下一件」，不佔高度"""
        # Given: 沒時間的待辦
        self.g.seed("寫週報", self.today)
        # When: 啟動
        app = self.start()
        # Then: 收起
        self.assertEqual(app.lbl_next.cget("text"), "")
        self.assertFalse(app.lbl_next.winfo_ismapped())


# ---------------------------------------------------------------- 重複待辦的預定日子

class PreviewTest(AppTestCase):
    """重複待辦在其他天以「預定」顯示（還沒建立，不能勾選）。"""

    def other_day(self, weeks=1):
        return self.today + dt.timedelta(days=7 * weeks)

    def test_weekly_shows_on_future_day(self):
        """今天的每週任務 > 下週同一天顯示灰色「預定」列，不能勾選"""
        # Given: 今天是這一期、每週重複
        rule = f"每週{daynote.WEEKDAY_NAME[self.today.weekday()]}"
        self.g.seed("交週報", self.today, notes=f"🔁 {rule}")
        app = self.start()
        # When: 選下週同一天
        app.select(self.other_day())
        settle(app)
        # Then: 預定列
        shown = self.list_texts()
        self.assertIn("交週報", shown)
        self.assertIn(f"預定 · 🔁 {rule} · 工作", shown)
        self.assertNotIn("這天沒有任務", shown)
        title = find_label(app.list_frame, "交週報")
        self.assertFalse(any(isinstance(w, tk.Canvas) for w in descendants(title.master.master)))

    def test_calendar_marks_preview_days(self):
        """每天重複 > 月曆上明天起的日子以淡藍點標示；今天仍是一般的藍點"""
        # Given: 每天重複
        self.g.seed("吃藥", self.today, notes="🔁 每天")
        app = self.start()
        # When: 看預定日子
        days = app._previews(app.weeks[0][0], app.weeks[-1][-1])
        # Then: 明天有、今天沒有（今天是真的待辦）
        self.assertIn(self.today + dt.timedelta(days=1), days)
        self.assertNotIn(self.today, days)

    def test_click_preview_opens_task(self):
        """點預定列 > 開啟這筆重複待辦（目前這一期）的詳細頁"""
        # Given: 下週的預定列
        rule = f"每週{daynote.WEEKDAY_NAME[self.today.weekday()]}"
        self.g.seed("交週報", self.today, notes=f"🔁 {rule}")
        app = self.start()
        app.select(self.other_day())
        settle(app)
        # When: 點它
        click(find_label(app.list_frame, "交週報"))
        # Then: 詳細頁是今天那一期
        self.assertEqual(app.detail["task"]["title"], "交週報")
        self.assertEqual(app.detail["task"]["due"], self.today)

    def test_no_duplicate_when_next_exists(self):
        """下一期已經真的建立了 > 那天只顯示真的那筆，不再多一列預定"""
        # Given: 今天和下週都有同標題同規則的待辦
        rule = f"每週{daynote.WEEKDAY_NAME[self.today.weekday()]}"
        self.g.seed("交週報", self.today, notes=f"🔁 {rule}")
        self.g.seed("交週報", self.other_day(), notes=f"🔁 {rule}")
        app = self.start()
        # When: 選下週
        app.select(self.other_day())
        settle(app)
        # Then: 只有一筆、不是預定
        shown = self.list_texts()
        self.assertEqual(shown.count("交週報"), 1)
        self.assertFalse(any(t.startswith("預定") for t in shown))

    def test_non_recurring_no_preview(self):
        """一般待辦 > 其他天不會出現"""
        # Given: 今天的一般待辦
        self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 選下週
        app.select(self.other_day())
        settle(app)
        # Then: 沒有
        self.assertIn("這天沒有任務", self.list_texts())


# ---------------------------------------------------------------- 提醒

class ReminderTest(AppTestCase):

    def seed_due_now(self):
        when = (dt.datetime.now() - dt.timedelta(minutes=1)).time().replace(second=0, microsecond=0)
        if dt.datetime.now().hour == 0 and dt.datetime.now().minute < 2:
            self.skipTest("午夜交界無法建立「今天已到時間」的提醒")
        return self.g.seed("開會", self.today, notes=f"⏰ {when:%H:%M}")

    def toast(self):
        self.assertEqual(len(daynote.ReminderToast.active), 1)
        return daynote.ReminderToast.active[0]

    def test_reminder_pops_once(self):
        """提醒時間到 > 跳出提醒視窗；再檢查一次 > 不重複跳"""
        # Given: 一分鐘前該提醒的任務
        self.seed_due_now()
        app = self.start()
        # When: 檢查提醒兩次
        app._check_reminders()
        app._check_reminders()
        # Then: 只有一個提醒，顯示標題
        self.assertIn("開會", texts(self.toast().win))

    def test_reminder_logged(self):
        """提醒跳出 > 記錄預定時間與實際觸發時間，方便追查提醒時間不對"""
        # Given: 一分鐘前該提醒的任務
        task_id = self.seed_due_now()
        app = self.start()
        # When: 檢查提醒
        with self.assertLogs("daynote", "INFO") as logs:
            app._check_reminders()
        # Then: 紀錄含任務 id 與預定時間
        self.assertEqual(len(logs.output), 1)
        self.assertIn(f"task={task_id}", logs.output[0])
        self.assertIn("scheduled=", logs.output[0])

    def test_reminder_snooze_until_tomorrow(self):
        """提醒視窗的 ▾ 選「明天 9:00再提醒」 > 延後到明天 9:00 並記錄"""
        # Given: 跳出的提醒
        self.seed_due_now()
        app = self.start()
        app._check_reminders()
        # When: 選明天早上
        self.toast().snooze_menu.invoke("明天 9:00再提醒")
        # Then: 延後到明天 9:00
        tomorrow_9 = dt.datetime.combine(self.today + dt.timedelta(days=1), dt.time(9, 0))
        self.assertEqual(list(app.snoozed.values()), [tomorrow_9])
        self.assertEqual(daynote.ReminderToast.active, [])
        self.assertEqual(self.status(), "明天 9:00再提醒")

    def test_reminder_done(self):
        """提醒視窗按「✓ 完成」 > 任務完成"""
        # Given: 跳出的提醒
        task_id = self.seed_due_now()
        app = self.start()
        app._check_reminders()
        # When: 按完成
        click(find_label(self.toast().win, "✓ 完成"))
        settle(app)
        # Then: 已完成、提醒關閉
        self.assertEqual(self.g.tasks[task_id]["status"], "completed")
        self.assertEqual(daynote.ReminderToast.active, [])

    def test_reminder_snooze(self):
        """提醒視窗按「10 分鐘後再提醒」 > 關閉並記錄延後，狀態寫入暫存 data"""
        # Given: 跳出的提醒
        self.seed_due_now()
        app = self.start()
        app._check_reminders()
        # When: 延後
        click(find_label(self.toast().win, "10 分鐘後再提醒"))
        # Then: 延後 10 分鐘
        self.assertEqual(daynote.ReminderToast.active, [])
        self.assertEqual(self.status(), "10 分鐘後再提醒")
        self.assertEqual(len(app.snoozed), 1)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "state.json")))


# ---------------------------------------------------------------- 視窗

class WindowTest(AppTestCase):

    def test_hide_and_toggle(self):
        """按「—」 > 隱藏到背景；快捷鍵切換 > 叫回來"""
        # Given: 背景模式
        app = self.start()
        # When: 按 —
        click(app.btn_min)
        app.update()
        # Then: 隱藏
        self.assertEqual(app.state(), "withdrawn")
        # When: F8
        self.hotkey_callback(WINDOW_KEY)
        pump(app, 0.3)
        # Then: 顯示
        self.assertEqual(app.state(), "normal")

    def test_topmost_toggle(self):
        """按 📌 > 切換永遠在最上層，關閉時記住設定"""
        # Given: 未置頂
        app = self.start()
        # When: 按 📌
        click(app.btn_pin)
        # Then: 置頂
        self.assertTrue(app.topmost)
        self.assertTrue(app.attributes("-topmost"))
        app.close()
        self.assertTrue(daynote.load_state()["topmost"])


# ---------------------------------------------------------------- 快速列（Ctrl + Alt + D）

class QuickBarTest(AppTestCase):

    def setUp(self):
        super().setUp()
        # 測試在真的桌面上跑：其他程式搶走焦點時 focus_get() 為 None，快速列會自己關掉而讓案例隨機失敗。
        # 這裡只讓「焦點移到 DayNote 自己的其他元件」觸發關閉（test_focus_leaving_closes 驗證的情境），
        # 焦點跑到別的程式時不關閉。
        original = daynote.QuickBar._close_if_unfocused

        def close_only_when_focus_moves_inside_app(bar):
            try:
                focus = bar.win.focus_get()
            except (KeyError, tk.TclError):
                focus = None
            if focus is not None:
                original(bar)
        patch = unittest.mock.patch.object(daynote.QuickBar, "_close_if_unfocused",
                                           close_only_when_focus_moves_inside_app)
        patch.start()
        self.addCleanup(patch.stop)

    def open_bar(self):
        """按 Ctrl + Alt + D 叫出快速列。"""
        self.hotkey_callback(QUICK_KEY)
        pump(self.app, 0.3)
        bar = self.app.quick_bar
        self.assertTrue(bar.is_open())
        return bar

    def type_in(self, bar, text):
        bar.entry.focus_force()
        self.app.update()
        bar.text.set(text)
        self.app.update()

    def test_hotkey_opens_bar_with_agenda(self):
        """按 Ctrl + Alt + D > 快速列出現、游標在輸入列，列出逾期與今天的待辦和輸入範例"""
        # Given: 逾期一筆、今天兩筆、明天一筆
        self.g.seed("回信", self.today - dt.timedelta(days=1))
        self.g.seed("晨會", self.today, notes="⏰ 09:00")
        self.g.seed("寫週報", self.today)
        self.g.seed("明天的事", self.today + dt.timedelta(days=1))
        self.start()
        # When: Ctrl + Alt + D
        bar = self.open_bar()
        # Then: 逾期在前、今天依時間，明天的不列
        shown = texts(bar.win)
        self.assertIs(self.app.focus_get(), bar.entry)
        self.assertIn("今天還有 2 件 · 逾期 1 件", shown)
        self.assertEqual([t["title"] for t in bar.items], ["回信", "晨會", "寫週報"])
        self.assertIn(daynote.QuickBar.EXAMPLE, shown)

    def test_hotkey_again_closes(self):
        """快速列開著再按 Ctrl + Alt + D > 關閉"""
        # Given: 快速列開著
        self.start()
        bar = self.open_bar()
        # When: 再按 Ctrl + Alt + D
        self.hotkey_callback(QUICK_KEY)
        pump(self.app, 0.3)
        # Then: 關閉
        self.assertFalse(bar.is_open())

    def test_typing_shows_chips(self):
        """輸入一行文字 > 即時顯示辨識出的日期、時間、重複、清單"""
        # Given: 快速列
        self.start()
        bar = self.open_bar()
        # When: 打字
        self.type_in(bar, "明天下午3點 跟客戶開會 每週二 #個人")
        # Then: 顯示辨識標籤，底部已有 Enter 說明，不再多一行「按 Enter 新增」
        self.assertEqual(bar.chips.cget("text"), "📅 明天　⏰ 15:00　🔁 每週二　📁 個人")
        self.assertFalse(bar.header.winfo_ismapped())

    def test_bar_shrinks_when_typing(self):
        """清單模式切到輸入模式 > 視窗高度跟著縮小，不留下空白"""
        # Given: 列出三筆待辦的快速列
        for title in ("晨會", "寫週報", "回信"):
            self.g.seed(title, self.today)
        self.start()
        bar = self.open_bar()
        agenda_height = bar.win.winfo_height()
        # When: 開始打字
        self.type_in(bar, "買牛奶")
        # Then: 變矮
        self.assertLess(bar.win.winfo_height(), agenda_height)

    def test_enter_adds_and_closes(self):
        """按 Enter > 依辨識結果建立待辦，快速列關閉，主視窗狀態列顯示已新增"""
        # Given: 輸入完整的一行
        app = self.start()
        bar = self.open_bar()
        self.type_in(bar, "明天下午3點 跟客戶開會 每週二 #個人")
        # When: Enter
        key(bar.entry, "<Return>")
        settle(app)
        # Then: Google 上的待辦欄位正確
        (raw,) = self.g.by_title("跟客戶開會")
        self.assertEqual(raw["due"][:10], (self.today + dt.timedelta(days=1)).isoformat())
        self.assertEqual(raw["notes"], "⏰ 15:00\n🔁 每週二")
        self.assertEqual(raw["list_id"], "L2")
        self.assertFalse(bar.is_open())
        self.assertEqual(self.status(), "已新增「跟客戶開會」到「個人」")

    def test_default_list_is_current(self):
        """沒寫 #清單 > 用主畫面目前選的清單"""
        # Given: 上次新增用的是「個人」
        app = self.start()
        app._remember_list(1)
        bar = self.open_bar()
        self.type_in(bar, "買牛奶")
        # When: Enter
        key(bar.entry, "<Return>")
        settle(app)
        # Then: 放進個人、今天
        (raw,) = self.g.by_title("買牛奶")
        self.assertEqual((raw["list_id"], raw["due"][:10]), ("L2", self.today.isoformat()))

    def test_empty_title_refused(self):
        """只寫日期時間沒有標題 > 紅字提示，不建立"""
        # Given: 只有日期時間
        app = self.start()
        bar = self.open_bar()
        self.type_in(bar, "明天 3點")
        # When: Enter
        key(bar.entry, "<Return>")
        pump(app, 0.2)
        # Then: 不建立
        self.assertNotIn("add_task", self.g.calls)
        self.assertEqual(bar.status.cget("text"), "請輸入工作內容（目前只有日期或時間）")
        self.assertEqual(bar.status.cget("fg"), daynote.RED)

    def test_add_failure_keeps_bar_open(self):
        """新增失敗 > 快速列不關，紅字顯示錯誤（主視窗可能是隱藏的）"""
        # Given: 新增會失敗
        app = self.start()
        self.g.fail.add("add_task")
        bar = self.open_bar()
        self.type_in(bar, "買牛奶")
        # When: Enter
        key(bar.entry, "<Return>")
        settle(app)
        # Then: 還開著、紅字
        self.assertTrue(bar.is_open())
        self.assertEqual(bar.status.cget("text"), "add_task failed")
        self.assertEqual(bar.text.get(), "買牛奶")

    def test_arrow_and_space_complete(self):
        """↓ 選第二筆再按 Space > 完成那一筆，清單更新"""
        # Given: 今天兩筆
        first = self.g.seed("晨會", self.today, notes="⏰ 09:00")
        second = self.g.seed("寫週報", self.today)
        app = self.start()
        bar = self.open_bar()
        # When: ↓、Space
        key(bar.entry, "<Down>")
        key(bar.entry, "<space>")
        settle(app)
        # Then: 只完成第二筆
        self.assertEqual(self.g.tasks[second]["status"], "completed")
        self.assertEqual(self.g.tasks[first]["status"], "needsAction")
        self.assertEqual([t["title"] for t in bar.items], ["晨會"])
        self.assertEqual(bar.text.get(), "")

    def test_space_while_typing_is_text(self):
        """輸入中按 Space > 照常打空白，不會完成任何待辦"""
        # Given: 今天一筆，輸入列已有文字
        self.g.seed("晨會", self.today)
        app = self.start()
        bar = self.open_bar()
        self.type_in(bar, "買")
        bar.entry.icursor("end")
        # When: Space
        key(bar.entry, "<space>")
        pump(app, 0.2)
        # Then: 是空白
        self.assertEqual(bar.text.get(), "買 ")
        self.assertNotIn("complete_task", self.g.calls)

    def test_search_and_open_detail(self):
        """輸入 /週報 > 找出所有含「週報」的待辦；Enter > 主視窗開啟它的詳細頁"""
        # Given: 不同日期的待辦，主視窗隱藏中
        self.g.seed("寫週報", self.today + dt.timedelta(days=2))
        self.g.seed("交週報給主管", None)
        self.g.seed("晨會", self.today)
        app = self.start()
        app.minimize()
        app.update()
        bar = self.open_bar()
        # When: 搜尋
        self.type_in(bar, "/週報")
        # Then: 兩筆，有日期的在前
        self.assertIn("找到 2 件", texts(bar.win))
        self.assertEqual([t["title"] for t in bar.items], ["寫週報", "交週報給主管"])
        # When: Enter
        key(bar.entry, "<Return>")
        pump(app, 0.3)
        # Then: 主視窗出現並開啟詳細頁
        self.assertFalse(bar.is_open())
        self.assertEqual(app.state(), "normal")
        self.assertEqual(app.detail["task"]["title"], "寫週報")

    def test_esc_closes(self):
        """按 Esc > 快速列關閉"""
        # Given: 快速列
        self.start()
        bar = self.open_bar()
        # When: Esc
        key(bar.entry, "<Escape>")
        # Then: 關閉
        self.assertFalse(bar.is_open())

    def test_hidden_window_hotkey_then_tab(self):
        """主視窗隱藏時按 Ctrl + Alt + D > 快速列照樣出現；按 Tab > 主視窗回來、快速列關閉"""
        # Given: 主視窗已隱藏（實際使用情境：在瀏覽器裡按 Ctrl + Alt + D）
        app = self.start()
        click(app.btn_min)
        self.assertEqual(app.state(), "withdrawn")
        # When: Ctrl + Alt + D
        bar = self.open_bar()
        # Then: 快速列可見且有焦點
        self.assertTrue(bar.win.winfo_ismapped())
        self.assertIs(app.focus_get(), bar.entry)
        # When: Tab
        key(bar.entry, "<Tab>")
        pump(app, 0.3)
        # Then: 主視窗顯示
        self.assertFalse(bar.is_open())
        self.assertEqual(app.state(), "normal")

    def test_f8_toggles_window(self):
        """按 F8 > 顯示／隱藏主視窗，不開快速列"""
        # Given: 主視窗顯示中
        app = self.start()
        # When: F8
        self.hotkey_callback(WINDOW_KEY)
        pump(app, 0.3)
        # Then: 隱藏、沒有快速列
        self.assertEqual(app.state(), "withdrawn")
        self.assertIsNone(getattr(app, "quick_bar", None))

    def test_focus_leaving_closes(self):
        """點到快速列以外（焦點離開） > 自動關閉"""
        # Given: 快速列
        app = self.start()
        bar = self.open_bar()
        # When: 焦點移到主視窗的輸入框
        app.entry.focus_force()
        pump(app, 0.3)
        # Then: 關閉
        self.assertFalse(bar.is_open())

    def test_logged_out(self):
        """未登入叫出快速列 > 提示按 Tab 登入；輸入後 Enter > 紅字提示，不呼叫 Google"""
        # Given: 未登入
        self.g = StoreGoogle(logged_in=False)
        app = self.start()
        bar = self.open_bar()
        # Then: 登入提示
        self.assertIn("尚未登入 Google：按 Tab 開啟主視窗登入", texts(bar.win))
        # When: 輸入並 Enter
        self.type_in(bar, "買牛奶")
        key(bar.entry, "<Return>")
        pump(app, 0.2)
        # Then: 擋下
        self.assertEqual(bar.status.cget("text"), "請先登入 Google：按 Tab 開啟主視窗登入")
        self.assertNotIn("add_task", self.g.calls)


if __name__ == "__main__":
    unittest.main()
