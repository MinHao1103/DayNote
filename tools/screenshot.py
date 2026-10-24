"""產生 README 用的畫面截圖：用測試替身（假的 Google 資料）啟動 DayNote，截下指定畫面存成 PNG。

用法（在 DayNote 資料夾執行）：
    python tools/screenshot.py                          # 主畫面 → docs/images/00-app.png
    python tools/screenshot.py --page detail --out x.png
    python tools/screenshot.py --theme night --out night.png   # 指定風格：cream、editorial、night、nordic

可截的畫面（--page）：
    main       主畫面（預設，週檢視）        month      主畫面展開整月
    daytip     滑鼠停在今天的日期上（月曆提示）  help       使用說明與快捷鍵（F1）
    datepicker／timepicker／recurpicker  詳細頁的日期／提醒時間／重複選擇器
    moremenu   「⋯」選單（展開「風格」子選單）  taskmenu   待辦的右鍵選單  snoozemenu  提醒的延後選單
    confirm    刪除確認對話框                    asktext    新增清單的名稱輸入框
    detail     工作詳細資訊                  new        新增工作
    loggedout  尚未登入                      quick      快速列（Ctrl + Alt + D）
    quicktype  快速列輸入中（顯示辨識結果）  toast      右下角提醒
    done       今天的「已完成」區塊（展開）    search     主視窗搜尋（Ctrl+F）
    event      點日曆行程的細節卡片          links      詳細頁的網址連結
    drag       拖曳排序中（顯示放下位置的線）

- 不會讀寫 data 資料夾、不連網路：資料全部來自 tests/test_app_gui.py 的 StoreGoogle 替身
- 只使用 Python 標準函式庫（Win32 GDI 擷取畫面、zlib 編碼 PNG）
- 截圖期間視窗會出現在螢幕上約 1 秒，請勿移動滑鼠到視窗上，以免 hover 底色被截進去

作者：DayNote
日期：2026-10-22
異動歷史：
    2026-10-22  新增
"""
import argparse
import ctypes
import datetime as dt
import os
import struct
import sys
import tempfile
import unittest.mock
import zlib
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # DayNote 資料夾
sys.path.insert(0, ROOT)
# --theme 必須在載入 DayNote 之前決定（顏色在模組載入時就套用），所以先從命令列取出
if "--theme" in sys.argv[:-1]:
    os.environ["DAYNOTE_THEME"] = sys.argv[sys.argv.index("--theme") + 1]
from tests.test_app_gui import HOLIDAY_CAL, StoreGoogle, daynote, pump, settle  # noqa: E402

PAGES = ("main", "month", "daytip", "help", "datepicker", "timepicker", "recurpicker", "moremenu", "taskmenu",
         "snoozemenu", "confirm", "asktext", "detail", "new", "loggedout", "quick", "quicktype", "toast", "done", "search", "event", "links", "drag")
DEFAULT_OUT = os.path.join(ROOT, "docs", "images", "00-app.png")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def grab(x, y, w, h):
    """擷取螢幕上 (x, y, w, h) 的區域，回傳由上而下的 RGB 列（每列 bytes）。

    @param x 左上角 X（實際像素）
    @param y 左上角 Y（實際像素）
    @param w 寬度
    @param h 高度
    @return list[bytes]，每列 w * 3 bytes
    """
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    user32.GetDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    try:
        gdi32.SelectObject(mem, bmp)
        gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020)  # SRCCOPY
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # 負高度＝由上而下
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)  # DIB_RGB_COLORS
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
    raw = buf.raw
    rows = []
    for row in range(h):
        bgra = raw[row * w * 4:(row + 1) * w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        rows.append(bytes(rgb))
    return rows


def write_png(path, rows, w, h):
    """把 RGB 列寫成 PNG（8-bit truecolor，不含 alpha）。"""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    body = zlib.compress(b"".join(b"\x00" + row for row in rows), 9)  # 每列前加 filter type 0
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", body))
        f.write(chunk(b"IEND", b""))


def seed_sample(g, today):
    """README 截圖用的範例資料：逾期、有時間的待辦、子工作、日曆行程、其他清單。"""
    day = today.isoformat()
    g.events_by_cal["primary"].append(
        {"summary": "週會", "start": {"dateTime": f"{day}T15:00:00"}, "end": {"dateTime": f"{day}T16:00:00"},
         "location": "3F 會議室 A", "description": "議程：上週進度、下週上線檢核 https://teams.microsoft.com/l/meetup-join/<MEETING_ID>",
         "htmlLink": "https://www.google.com/calendar/event?eid=<EVENT_ID>"})
    g.seed("繳電話費", today - dt.timedelta(days=3))
    parent = g.seed("準備週會簡報", today, notes="⏰ 14:00")
    g.seed("整理上週數據", today, parent=parent)
    g.seed("回信給客戶", today, notes="⏰ 16:30\n報價單：https://docs.example.com/quote/2026-10")
    g.seed("買牛奶", today, list_id="L2")


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def open_page(app, g, page, today):
    """切換到要截圖的畫面，回傳要截的視窗。"""
    if page == "month":
        app.toggle_compact()
    elif page == "drag":
        for title in ("寫週報", "訂會議室"):
            g.seed(title, today)
        app.refresh()
        settle(app)
        app.list_canvas.yview_moveto(1)
        app.update()
        rows = {t["title"]: row for t, row in app._drag_slots.get("day", [])}
        rows["訂會議室"].grip.event_generate("<ButtonPress-1>", x=2, y=2)
        app._drag_move(rows["寫週報"].winfo_rooty() + 2)
        app.update()
    elif page == "event":
        label = next(w for w in _descendants(app.list_frame) if isinstance(w, daynote.tk.Label) and w.cget("text") == "週會")
        label.event_generate("<Button-1>")
        pump(app, 0.3)
        return [app, app.event_popup.win]
    elif page == "links":
        app.open_detail(next(t for t in app.tasks if t["title"] == "回信給客戶"))
        app.update()
    elif page == "search":
        app.open_search("報")
        app.update()
    elif page == "done":
        for title in ("回信給客戶", "買牛奶"):
            app.complete(next(t for t in app.tasks if t["title"] == title))
        pump(app, 0.5)
        app._hide_undo()
        app.show_done = True
        app.redraw()
        app.update()
        app.list_canvas.yview_moveto(1)
    elif page == "daytip":
        left, top, right, _ = app._cal_cell_rect(today)
        app.cal.event_generate("<Motion>", x=(left + right) // 2, y=top + 5)
        pump(app, 0.7)  # 提示在停留 0.5 秒後出現
    elif page == "moremenu":
        app._open_more_menu()
        app.update()
        app.menu_more._step(1)
        app.menu_more._step(1)
        app.menu_more._open_child_keyboard()
        return [app, app.menu_more.win, app.menu_theme.win]
    elif page == "taskmenu":
        task = next(t for t in app.tasks if t["title"] == "回信給客戶")
        menu = app._build_task_menu(task)
        menu.tk_popup(app.winfo_rootx() + daynote.px(120), app.winfo_rooty() + daynote.px(330))
        app.update()
        menu._step(1)
        return [app, menu.win]
    elif page == "snoozemenu":
        task = next(t for t in app.tasks if t["title"] == "回信給客戶")
        toast = daynote.ReminderToast(app, task, dt.datetime.combine(today, task["time"]),
                                      lambda: None, lambda *a: None)
        pump(app, 0.3)
        toast._open_snooze_menu()
        app.update()
        return [toast.win, toast.snooze_menu.win]
    elif page in ("confirm", "asktext"):
        shot = {}

        def capture():  # 對話框會等使用者回應，所以在它開著的時候截圖再關掉
            dialog = app._active_dialog
            pump(app, 0.8)
            dialog.update_idletasks()
            shot["rect"] = (dialog.winfo_rootx(), dialog.winfo_rooty(), dialog.winfo_width(), dialog.winfo_height())
            shot["rows"] = grab(*shot["rect"])
            dialog.event_generate("<Escape>")
        app.after(300, capture)
        if page == "confirm":
            daynote.ThemedMessageBox.askyesno("刪除工作", daynote.delete_prompt(app.tasks[0], 0), parent=app,
                                              icon="warning", yes="刪除", no="取消")
        else:
            daynote.ask_text(app, "新增清單", "清單名稱：")
        return shot
    elif page == "help":
        app.open_help()
        return app.help_win
    elif page in ("datepicker", "timepicker", "recurpicker"):
        task = next(t for t in app.tasks if t["title"] == "準備週會簡報")
        app.open_detail(task)
        pump(app, 0.2)
        icon = {"datepicker": "📅", "timepicker": "⏰", "recurpicker": "🔁"}[page]
        anchor = next(w for w in _descendants(app.detail_view)
                      if isinstance(w, daynote.Pill) and w.text_value.startswith(icon))
        if page == "datepicker":
            picker = daynote.DatePicker(app, anchor, task["due"], lambda d: None)
        elif page == "timepicker":
            picker = daynote.TimePicker(app, anchor, task["time"], lambda t: None)
        else:
            picker = daynote.RecurPicker(app, anchor, None, task["due"], lambda r: None)
        return picker.win
    elif page == "detail":
        app.open_detail(next(t for t in app.tasks if t["title"] == "準備週會簡報"))
    elif page == "new":
        app.open_new_task()
    elif page == "loggedout":
        g.refresh_token = None
        app._show_error(daynote.NeedLogin("尚未登入，請按右上角「登入」"))
    elif page in ("quick", "quicktype"):
        app.toggle_quick_bar()
        pump(app, 0.3)
        if page == "quicktype":
            app.quick_bar.text.set("明天下午3點 跟客戶開會 每週二 #工作")
        return app.quick_bar.win
    elif page == "toast":
        task = next(t for t in app.tasks if t["title"] == "回信給客戶")
        toast = daynote.ReminderToast(app, task, dt.datetime.combine(today, task["time"]),
                                      lambda: None, lambda: None)
        return toast.win
    return app


def main():
    parser = argparse.ArgumentParser(description="產生 DayNote 畫面截圖（使用假資料）")
    parser.add_argument("--page", choices=PAGES, default="main")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--theme", choices=list(daynote.THEMES), help="風格（預設 cream）")
    parser.add_argument("--width", type=int, help="視窗寬度（100%% 縮放的像素）；預設維持 DayNote 預設大小")
    args = parser.parse_args()

    daynote.enable_dpi_awareness()  # 和正式啟動一樣，座標才是實際像素
    tmp = tempfile.mkdtemp(prefix="daynote-shot-")
    with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as f:
        f.write('{"tips_shown": true}')  # 截圖用一般狀態，不顯示第一次的提示
    patches = [
        unittest.mock.patch.multiple(daynote, DATA_DIR=tmp, STATE_PATH=os.path.join(tmp, "state.json"),
                                     TOKEN_PATH=os.path.join(tmp, "token.bin"),
                                     CONFIG_PATH=os.path.join(tmp, "config.json"),
                                     PID_PATH=os.path.join(tmp, "daynote.pid")),
        unittest.mock.patch.object(daynote, "win_pin_to_desktop", lambda w: None),
        unittest.mock.patch.object(daynote, "win_is_pinned", lambda w: True),
        unittest.mock.patch.object(daynote, "win_set_background_window", lambda w: None),
        unittest.mock.patch.object(daynote, "win_hook_messages", lambda w, **kw: object()),
        unittest.mock.patch.object(daynote, "win_register_hotkey", lambda w, i: True),
        unittest.mock.patch.object(daynote, "win_unregister_hotkey", lambda w, i: None),
    ]
    for p in patches:
        p.start()

    today = dt.date.today()
    g = StoreGoogle()
    seed_sample(g, today)
    app = daynote.App(g, holiday_calendar=HOLIDAY_CAL, desktop_reminder=False, show_in_taskbar=True)
    app.attributes("-topmost", True)
    pump(app, 0.4)
    settle(app)
    if args.width:
        app.geometry(f"{daynote.px(args.width)}x{app.winfo_height()}")
        pump(app, 0.3)
    target = open_page(app, g, args.page, today)
    if isinstance(target, dict):  # 對話框：已在開著的時候截好
        x, y, w, h = target["rect"]
        write_png(args.out, target["rows"], w, h)
        app.destroy()
        print(f"已存檔：{args.out}（{w}×{h}）")
        return
    pump(app, 0.8)  # 等視窗與 DWM 圓角、陰影畫完
    windows = target if isinstance(target, list) else [target]  # 選單等多個視窗：截下全部的聯集
    for win in windows:
        win.update_idletasks()
    left = min(win.winfo_rootx() for win in windows)
    top = min(win.winfo_rooty() for win in windows)
    right = max(win.winfo_rootx() + win.winfo_width() for win in windows)
    bottom = max(win.winfo_rooty() + win.winfo_height() for win in windows)
    x, y, w, h = left, top, right - left, bottom - top
    write_png(args.out, grab(x, y, w, h), w, h)
    app.destroy()
    print(f"已存檔：{args.out}（{w}×{h}）")


if __name__ == "__main__":
    main()
