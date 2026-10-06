"""DayNote 主視窗端對端測試：驅動真的 App 與 Tk 事件迴圈，斷言畫面上實際顯示的內容。

執行方式（在 DayNote 根目錄）：python -m unittest discover -s tests -t . -v

隔離（測試不得影響使用者的真實環境）：
- Google：以記憶體版 StoreGoogle 取代，不連網
- data 資料夾：state.json、token.bin 等路徑改到暫存資料夾
- Windows：釘選桌面、圓角、背景視窗、全域快捷鍵、訊息攔截改為替身，不搶執行中 DayNote 的 F8
- 對話框、提示音、剪貼簿、瀏覽器：一律替身

以下需要真實 Windows／Google 環境，不在此自動化，改列手動驗證：
OAuth 瀏覽器登入、真實同步、釘在桌面與 Win + D、全域快捷鍵實際觸發、提醒視窗位置、
開機自動啟動捷徑、雙擊 .bat／.vbs 啟動
"""
import datetime as dt
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


def texts(widget):
    """元件底下所有 Label 顯示的文字。"""
    return [w.cget("text") for w in descendants(widget) if isinstance(w, tk.Label)]


def find_label(widget, text, exact=True):
    for w in descendants(widget):
        if isinstance(w, tk.Label):
            value = w.cget("text")
            if value == text or (not exact and text in value):
                return w
    raise AssertionError(f"畫面上找不到「{text}」；目前有：{texts(widget)}")


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

    def _dialog(self, name):
        def record(title, message, **kw):
            self.dialogs.append((name, title, message))
            return self.answer.get(name)
        return record

    def start(self, **kw):
        """啟動 App 並等第一次同步完成。"""
        self.app = daynote.App(self.g, holiday_calendar=kw.pop("holiday_calendar", HOLIDAY_CAL),
                               desktop_reminder=False, **kw)
        self.app.clipboard_clear = lambda: None   # 不動使用者的剪貼簿
        self.app.clipboard_append = lambda text: setattr(self, "clipboard", text)
        self.addCleanup(self._destroy)
        pump(self.app, 0.4)  # 背景模式的視窗樣式與快捷鍵在 200ms 後才設定
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
        self.assertIn("今天 · 1 項", self.list_texts())
        self.assertEqual(app.cmb_list.get(), "工作")
        self.assertEqual(list(app.cmb_list["values"]), ["工作", "個人", daynote.ADD_LIST_OPTION])
        self.assertTrue(self.status().startswith("已同步"))
        self.assertTrue(app.btn_logout.winfo_ismapped())

    def test_hotkeys_registered_on_startup(self):
        """背景模式啟動 > 註冊 F8 與 Ctrl + Alt + D 兩組快捷鍵"""
        # Given: 預設的背景模式
        # When: 啟動
        app = self.start()
        # Then: 兩組都註冊
        self.assertEqual(sorted(self.registered_hotkeys), [1, 2])
        self.assertEqual(app.hotkeys_registered, {1, 2})

    def test_logged_out_shows_prompt(self):
        """未登入啟動 > 清單區顯示登入提示、下拉選單停用"""
        # Given: 沒有登入憑證
        self.g = StoreGoogle(logged_in=False)
        # When: 啟動
        app = self.start()
        # Then: 登入提示
        self.assertIn("尚未登入 Google", self.list_texts())
        self.assertEqual(app.cmb_list.get(), "請先登入")
        self.assertEqual(str(app.cmb_list.cget("state")), "disabled")
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
        # Then: 已同步、顯示任務、改顯示登出
        self.assertEqual(self.clipboard, "https://accounts.example.com/<LOGIN_URL>")
        self.assertIn("晨會", self.list_texts())
        self.assertTrue(app.btn_logout.winfo_ismapped())
        self.assertFalse(app.btn_login.winfo_ismapped())

    def test_logout(self):
        """登出並確認 > 撤銷授權、清空畫面、改顯示登入"""
        # Given: 已登入且有任務
        self.g.seed("晨會", self.today)
        app = self.start()
        # When: 按登出並確認
        click(app.btn_logout)
        settle(app)
        # Then: 已登出
        self.assertIn("revoke", self.g.calls)
        self.assertEqual(self.status(), "已登出")
        self.assertIn("尚未登入 Google", self.list_texts())
        self.assertTrue(app.btn_login.winfo_ismapped())

    def test_logout_cancelled(self):
        """登出時按否 > 不撤銷，畫面不變"""
        # Given: 已登入
        self.g.seed("晨會", self.today)
        app = self.start()
        self.answer["askyesno"] = False
        # When: 按登出但取消
        click(app.btn_logout)
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
        # When: 按 ⟳ 同步
        click(app.btn_refresh)
        settle(app)
        # Then: 引導重新登入
        self.assertEqual(self.status(), "tasklists failed")
        self.assertIn("尚未登入 Google", self.list_texts())
        self.assertTrue(app.btn_login.winfo_ismapped())
        self.assertFalse(app.btn_logout.winfo_ismapped())
        self.assertEqual(app.cmb_list.get(), "請先登入")


# ---------------------------------------------------------------- 新增工作

class AddTaskTest(AppTestCase):

    def test_add_task_today(self):
        """輸入標題按 Enter > 新增到今天、目前清單，清單立即顯示"""
        # Given: 已登入
        self.start()
        # When: 新增
        self.type_new_task("買牛奶")
        # Then: 畫面與 Google 都有
        self.assertIn("買牛奶", self.list_texts())
        self.assertEqual(self.status(), "已新增到「工作」")
        (raw,) = self.g.by_title("買牛奶")
        self.assertEqual(raw["due"][:10], self.today.isoformat())
        self.assertEqual(raw["list_id"], "L1")

    def test_add_task_with_reminder_time(self):
        """在時間選擇器輸入 15:00 再新增 > 詳細資訊寫入 ⏰ 15:00，狀態列提示提醒時間"""
        # Given: 已登入
        app = self.start()
        # When: 開時間選擇器輸入 15:00，再新增
        click(app.btn_new_time)
        type_time(app, "15:00")
        self.assertEqual(app.btn_new_time.cget("text"), "⏰ 15:00")
        self.type_new_task("繳電費")
        # Then: 帶提醒時間，且時間只套用這一筆
        (raw,) = self.g.by_title("繳電費")
        self.assertEqual(raw["notes"], "⏰ 15:00")
        self.assertEqual(self.status(), "已新增到「工作」，15:00 提醒")
        self.assertEqual(app.btn_new_time.cget("text"), "⏰")

    def test_placeholder_hints_enter(self):
        """輸入框沒有文字 > 提示文字寫明按 Enter 新增"""
        # Given / When: 啟動
        app = self.start()
        # Then: 提示文字
        self.assertEqual(app.entry.get(), "新增工作（Enter）")

    def test_plus_button_adds_typed_task(self):
        """輸入標題後點「＋」 > 和按 Enter 一樣新增"""
        # Given: 輸入框有文字
        app = self.start()
        app.entry.focus_force()
        app.update()
        app.entry.insert(0, "買牛奶")
        # When: 點 ＋
        click(app.btn_add)
        settle(app)
        # Then: 已新增
        self.assertEqual(len(self.g.by_title("買牛奶")), 1)
        self.assertIn("買牛奶", self.list_texts())

    def test_plus_button_without_text_focuses_entry(self):
        """還沒輸入就點「＋」 > 不新增，游標移到輸入框"""
        # Given: 輸入框只有提示文字
        app = self.start()
        app.cal.focus_force()
        app.update()
        # When: 點 ＋
        click(app.btn_add)
        settle(app)
        # Then: 沒有新增，焦點在輸入框、提示文字收起
        self.assertNotIn("add_task", self.g.calls)
        self.assertIs(app.focus_get(), app.entry)
        self.assertEqual(app.entry.get(), "")

    def test_time_picker_has_no_presets(self):
        """打開時間選擇器 > 沒有預設時間，游標直接在時間欄位"""
        # Given: 已登入
        app = self.start()
        # When: 打開時間選擇器
        click(app.btn_new_time)
        picker = popup(app)
        # Then: 只有輸入欄、確定、不設時間
        shown = texts(picker)
        self.assertFalse(any(re_time(t) for t in shown), shown)
        self.assertIn("不設時間", shown)
        self.assertIsInstance(app.focus_get(), tk.Entry)

    def test_time_picker_invalid_input(self):
        """時間欄位輸入看不懂的內容 > 顯示格式提示，不套用"""
        # Given: 時間選擇器
        app = self.start()
        click(app.btn_new_time)
        # When: 輸入無效時間
        type_time(app, "25:00")
        # Then: 提示，仍未設定時間
        self.assertIn("請輸入時間，例如 15:30 或 1530", texts(popup(app)))
        self.assertIsNone(app.new_time)

    def test_add_task_without_date(self):
        """日期選「無日期」再新增 > 出現在「未排日期」區塊"""
        # Given: 已登入
        app = self.start()
        # When: 選無日期並新增，再展開未排日期
        click(app.btn_new_due)
        click(find_label(popup(app), "無日期"))
        self.type_new_task("整理書櫃")
        click(find_label(app.list_frame, "▶ 未排日期（1）"))
        # Then: 沒有日期，顯示在未排日期
        (raw,) = self.g.by_title("整理書櫃")
        self.assertIsNone(raw["due"])
        self.assertIn("整理書櫃", self.list_texts())

    def test_add_task_to_other_list(self):
        """下拉選單切到「個人」再新增 > 放進個人清單，並記住選擇"""
        # Given: 已登入
        app = self.start()
        # When: 切換清單後新增
        app.cmb_list.current(1)
        app.cmb_list.event_generate("<<ComboboxSelected>>")
        self.type_new_task("看牙醫")
        # Then: 放進 L2
        (raw,) = self.g.by_title("看牙醫")
        self.assertEqual(raw["list_id"], "L2")
        self.assertEqual(app.state_data["last_list_id"], "L2")

    def test_add_new_list(self):
        """選「新增清單」並輸入名稱 > 建立清單並設為目前清單"""
        # Given: 已登入
        app = self.start()
        # When: 選最後一項並輸入名稱
        with unittest.mock.patch.object(daynote, "ask_text", lambda *a, **k: "旅行"):
            app.cmb_list.current(len(app.lists))
            app.cmb_list.event_generate("<<ComboboxSelected>>")
            settle(app)
        # Then: 新清單被選取
        self.assertEqual(app.cmb_list.get(), "旅行")
        self.assertEqual(self.status(), "已建立清單「旅行」，新增的工作會放進這個清單")

    def test_add_task_failure_restores_input(self):
        """新增失敗 > 紅字錯誤，輸入框放回原本的標題"""
        # Given: 新增會失敗
        app = self.start()
        self.g.fail.add("add_task")
        # When: 新增
        self.type_new_task("買牛奶")
        # Then: 標題還在
        self.assertEqual(self.status(), "add_task failed")
        self.assertEqual(app.entry.get(), "買牛奶")


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

    def test_no_change_no_request(self):
        """詳細頁沒有修改就返回 > 不送出任何修改"""
        # Given: 一筆任務
        self.g.seed("寫週報", self.today)
        app = self.start()
        # When: 開了直接返回
        self.open("寫週報")
        click(find_label(app.detail_view, "← 儲存並返回"))
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

    def test_delete_with_confirm(self):
        """刪除並確認 > 任務與子工作都刪除"""
        # Given: 有子工作的任務
        parent = self.g.seed("搬家", self.today)
        child = self.g.seed("打包", None, parent=parent)
        app = self.start()
        # When: 刪除並確認
        self.open("搬家")
        click(find_label(app.detail_view, "刪除"))
        settle(app)
        # Then: 都刪除
        self.assertTrue(self.g.tasks[parent].get("deleted"))
        self.assertTrue(self.g.tasks[child].get("deleted"))
        self.assertEqual(self.status(), "已刪除「搬家」")
        self.assertIn("1 個子工作", self.dialogs[-1][2])

    def test_delete_cancelled(self):
        """刪除時按否 > 不刪除，停在詳細頁"""
        # Given: 一筆任務
        self.g.seed("寫週報", self.today)
        app = self.start()
        self.answer["askyesno"] = False
        # When: 刪除但取消
        self.open("寫週報")
        click(find_label(app.detail_view, "刪除"))
        pump(app, 0.2)
        # Then: 沒刪
        self.assertNotIn("delete_task", self.g.calls)
        self.assertTrue(app.detail_view.winfo_ismapped())

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
        click(find_label(app.detail_view, "← 儲存並返回"))
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
        self.assertIn(f"{other.month}月{other.day}日 · 1 項", self.list_texts())
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
        """按「收合」 > 月曆只剩一週，◀ ▶ 改為切換週，設定會記住"""
        # Given: 已啟動
        app = self.start()
        # When: 收合後按 ▶
        click(app.btn_compact)
        # Then: 只有一週
        self.assertEqual(len(app.weeks), 1)
        self.assertEqual(app.btn_compact.cget("text"), "展開")
        self.assertTrue(app.state_data["compact_calendar"])
        click(find_label(app.main_view, "▶"))
        settle(app)
        self.assertEqual(app.selected, self.today + dt.timedelta(days=7))

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
        """提醒視窗按「10 分鐘後」 > 關閉並記錄延後，狀態寫入暫存 data"""
        # Given: 跳出的提醒
        self.seed_due_now()
        app = self.start()
        app._check_reminders()
        # When: 延後
        click(find_label(self.toast().win, "10 分鐘後"))
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
        # When: 快捷鍵（F8／Ctrl + Alt + D 觸發的同一個動作）
        app.toggle_visible()
        app.update()
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


if __name__ == "__main__":
    unittest.main()
