"""DayNote：Windows 桌面月曆＋待辦小工具。

- Google Tasks：讀取未完成任務、新增任務、勾選完成
- Google 日曆：唯讀顯示主日曆（primary）的事件
- 只使用 Python 標準函式庫；執行方式：pythonw daynote.pyw
"""
import sys

if sys.version_info < (3, 10):
    sys.exit("DayNote 需要 Python 3.10 以上，目前版本：" + sys.version.split()[0])

import base64
import calendar
import ctypes
import dataclasses
import datetime as dt
import hashlib
import http.server
import json
import os
import queue
import re
import secrets
import ssl
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import winsound
from ctypes import wintypes
from tkinter import filedialog, messagebox, ttk

APP = "DayNote"
# 資料夾結構：DayNote\app\daynote.pyw（程式）、DayNote\data\（個人資料，不上傳、不打包）
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")   # Google 用戶端設定
TOKEN_PATH = os.path.join(DATA_DIR, "token.bin")      # DPAPI 加密的登入憑證
STATE_PATH = os.path.join(DATA_DIR, "state.json")     # 視窗位置、提醒記錄等，由程式自動寫入
PID_PATH = os.path.join(DATA_DIR, "daynote.pid")      # 執行中的程序編號，用來只保留一個 DayNote
# 舊版（所有檔案放在同一層）的位置 → 新位置；啟動時自動搬移
LEGACY_FILES = {"config.json": CONFIG_PATH, "token.bin": TOKEN_PATH, "daynote_state.json": STATE_PATH}


def migrate_legacy_files():
    """把舊版放在 DayNote 根目錄的個人檔案搬進 data 資料夾；新位置已有檔案時不覆蓋。

    舊版的 daynote.pid 直接刪除：仍在執行的舊版 DayNote 看到 PID 檔消失會自行關閉，避免同時開兩個。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    for old_name, new_path in LEGACY_FILES.items():
        old_path = os.path.join(ROOT_DIR, old_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            try:
                os.replace(old_path, new_path)
            except OSError:
                pass  # 搬不動（例如被占用）就留在原處，下次啟動再試
    try:
        os.remove(os.path.join(ROOT_DIR, "daynote.pid"))
    except OSError:
        pass

# 最小權限：Tasks 讀寫、日曆事件唯讀（不含日曆清單與設定）
SCOPES = ("https://www.googleapis.com/auth/tasks "
          "https://www.googleapis.com/auth/calendar.events.readonly")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
CAL_API = "https://www.googleapis.com/calendar/v3"
# Google 提供的台灣節慶假日公開日曆；可在 config.json 的 holiday_calendar 改成其他地區或設空字串關閉
DEFAULT_HOLIDAY_CALENDAR = "zh-tw.taiwan#holiday@group.v.calendar.google.com"
LOGIN_TIMEOUT_SEC = 300
FOCUS_REFRESH_SEC = 30

# 畫面樣式
BG = "#ffffff"       # 白底
BORDER = "#d6d6d6"
TEXT = "#323130"
EVENT_DOT = "#a19f9d"  # 月曆上「只有行程」的日期圓點
CARD_BG = "#f7f7f7"   # 輸入框、詳細頁欄位底色：比白底略深，看得出可輸入
EVENT_TIME_FONT = ("Microsoft JhengHei UI", 9, "bold")
UNDO_SECONDS = 5
REMINDER_CHECK_MS = 30 * 1000    # 每 30 秒檢查一次提醒
REMINDER_SNOOZE_MIN = 10
REMINDER_MAX_LATE_H = 24         # 錯過超過 24 小時的提醒不再補跳
ADD_LIST_OPTION = "＋ 新增清單…"  # 清單下拉選單的最後一項
SELECTED = "#e8f0fc"  # 月曆選中日期：淡藍，和強調色一致
DIVIDER = "#ececec"   # 清單列之間的細分隔線
HOVER = "#f3f3f3"
CARD = "#ffffff"
ACCENT = "#2564cf"
RED = "#d13438"
HOLIDAY_RED = "#c4314b"   # 週末與國定假日的日期數字
HOLIDAY_RED_FADED = "#e8a9b4"  # 其他月份的週末／假日
FESTIVAL = "#e3a21a"      # 一般節日（不放假）的角標
GRAY = "#605e5c"     # 在白底上對比約 6.5:1，符合無障礙 4.5:1
FONT = ("Microsoft JhengHei UI", 10)
SMALL = ("Microsoft JhengHei UI", 8)
TITLE_FONT = ("Microsoft JhengHei UI", 15, "bold")
ICON_FONT = ("Segoe UI Symbol", 11)
DEFAULT_SIZE = (380, 620)
MIN_SIZE = (340, 480)
CELL_W, CELL_H, HEAD_H = 50, 38, 20
CHILD_INDENT = 28     # 子任務卡片縮排
# 以上像素尺寸以 100% 縮放（96 DPI）為準，啟動時由 apply_dpi_scale() 依螢幕縮放比例換算
SCALE = 1.0


def px(value):
    """把 100% 縮放下的像素值換算成目前螢幕的實際像素。"""
    return round(value * SCALE)


def enable_dpi_awareness():
    """宣告程式支援高 DPI，避免 Windows 把整個視窗點陣放大造成文字模糊。失敗則維持系統預設。"""
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except (OSError, AttributeError):
        try:
            ctypes.WinDLL("user32").SetProcessDPIAware()
        except (OSError, AttributeError):
            pass


def apply_dpi_scale(root):
    """依螢幕 DPI 換算全域像素常數（字型以 pt 指定，tkinter 會自動縮放，不需處理）。"""
    global SCALE, CELL_W, CELL_H, HEAD_H, CHILD_INDENT, DEFAULT_SIZE, MIN_SIZE
    SCALE = max(root.winfo_fpixels("1i") / 96, 1.0)
    CELL_W, CELL_H, HEAD_H, CHILD_INDENT = px(50), px(38), px(20), px(28)
    DEFAULT_SIZE = (px(380), px(620))
    MIN_SIZE = (px(340), px(480))
WEEK_HEAD = "日一二三四五六"   # 月曆以星期日為第一欄
WEEKDAY_NAME = "一二三四五六日"  # 對應 date.weekday()


class NeedLogin(Exception):
    """需要（重新）登入 Google。"""


class ConfigMissing(Exception):
    """還沒設定 Google 用戶端（沒有 config.json 或仍是範例值），需要首次設定。"""


class ApiError(Exception):
    """可直接顯示給使用者的錯誤訊息。"""


class NotFound(ApiError):
    """Google 上找不到該資源（HTTP 404），例如任務已在其他裝置刪除。"""


# ---------------------------------------------------------------- 設定與憑證

def load_config():
    """讀取 config.json；還沒設定用戶端時拋出 ConfigMissing，格式錯誤時拋出 ValueError。"""
    if not os.path.exists(CONFIG_PATH):
        raise ConfigMissing("找不到 config.json")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        # JSON 不支援註解；允許整行以 // 開頭的註解（方便保留備份設定）
        lines = [ln for ln in f.read().splitlines() if not ln.lstrip().startswith("//")]
    cfg = json.loads("\n".join(lines))
    if not cfg.get("client_id") or cfg["client_id"].startswith("<"):
        raise ConfigMissing("config.json 尚未填入 client_id 與 client_secret")
    return cfg


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, encrypt):
    """以 Windows DPAPI 加密／解密，只有同一個 Windows 使用者帳號能解開。"""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    func = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    func.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    func.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    # 0x01 = CRYPTPROTECT_UI_FORBIDDEN：不跳出任何 Windows 對話框
    if not func(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)):
        raise OSError(f"DPAPI 失敗（Windows 錯誤碼 {ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def load_refresh_token():
    """讀取並解密 refresh token；檔案不存在或無法解密時回傳 None（改為重新登入）。"""
    try:
        with open(TOKEN_PATH, "rb") as f:
            return _dpapi(f.read(), encrypt=False).decode("utf-8")
    except OSError:
        return None


def save_refresh_token(token):
    with open(TOKEN_PATH, "wb") as f:
        f.write(_dpapi(token.encode("utf-8"), encrypt=True))


def delete_refresh_token():
    try:
        os.remove(TOKEN_PATH)
    except FileNotFoundError:
        pass


def load_state():
    """讀取視窗狀態（位置、大小、置頂）；檔案不存在或損壞時回傳空 dict。"""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 存不了只影響下次開啟的位置，不中斷關閉流程


# ---------------------------------------------------------------- Windows 視窗輔助

def _hwnd(widget):
    """取得 tkinter 視窗外框的 Windows HWND。"""
    user32 = ctypes.WinDLL("user32")
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    return user32.GetParent(widget.winfo_id())


def _user32():
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    return user32


GWL_STYLE, GWL_EXSTYLE, GWLP_HWNDPARENT = -16, -20, -8
WS_MINIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00080000
WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = 0x00040000, 0x00000080


def win_pin_to_desktop(widget):
    """擁有者設為 Windows 桌面（Progman），Win + D 之後仍顯示（已在 Win11 實測）。

    取捨：若 Explorer 當掉或重新啟動，擁有者消失會連帶關閉 DayNote，重開即可。
    """
    try:
        user32 = _user32()
        progman = user32.FindWindowW("Progman", None)
        if progman:
            user32.SetWindowLongPtrW(_hwnd(widget), GWLP_HWNDPARENT, progman)
    except (OSError, AttributeError):
        pass  # 失敗只是 Win + D 時會跟著隱藏


def win_is_pinned(widget):
    """視窗的擁有者是否為 Windows 桌面（Progman）。"""
    try:
        user32 = _user32()
        user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetWindow.restype = wintypes.HWND
        progman = user32.FindWindowW("Progman", None)
        return bool(progman) and user32.GetWindow(_hwnd(widget), 4) == progman  # GW_OWNER
    except (OSError, AttributeError):
        return False


def win_add_appwindow(widget):
    """無邊框視窗、或擁有者是桌面的視窗，預設不會出現在工作列；加上 WS_EX_APPWINDOW 強制顯示。

    需要重新顯示視窗（withdraw → deiconify）才會套用到工作列。
    """
    try:
        user32 = _user32()
        hwnd = _hwnd(widget)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        # 無邊框視窗沒有「可縮小」屬性，點工作列圖示不會縮到背景；補上後行為和一般程式一樣
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_MINIMIZEBOX | WS_SYSMENU)
    except (OSError, AttributeError):
        pass


WM_SYSCOMMAND, SC_MINIMIZE, GWLP_WNDPROC = 0x0112, 0xF020, -4
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)


WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_D, VK_F8 = 0x44, 0x77
# 全域快捷鍵：(id, 修飾鍵, 按鍵, 顯示名稱)。F8 為主；保留 Ctrl + Alt + D 給舊使用者，也當 F8 被占用時的備援
HOTKEYS = (
    (1, MOD_NOREPEAT, VK_F8, "F8"),
    (2, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_D, "Ctrl + Alt + D"),
)
HOTKEY_IDS = {hk[0] for hk in HOTKEYS}


def win_hook_messages(widget, on_user_minimize=None, on_hotkey=None):
    """攔截視窗訊息，原封不動交給 tkinter 處理，只額外通知：

    - SC_MINIMIZE：使用者點工作列（或 Win + ↓）要求縮小；Win + D 不會送這個
    - WM_HOTKEY：全域快捷鍵（F8、Ctrl + Alt + D）
    回呼裡不可直接操作 tkinter，請只排入佇列。回傳要保留的 callback 參考；失敗回傳 None。
    """
    try:
        user32 = _user32()
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                           wintypes.WPARAM, wintypes.LPARAM]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        hwnd = _hwnd(widget)
        state = {}

        def proc(h, msg, wparam, lparam):
            if msg == WM_SYSCOMMAND and (wparam & 0xFFF0) == SC_MINIMIZE and on_user_minimize:
                on_user_minimize()
            elif msg == WM_HOTKEY and wparam in HOTKEY_IDS and on_hotkey:
                on_hotkey()
            return user32.CallWindowProcW(state["old"], h, msg, wparam, lparam)

        callback = _WNDPROC(proc)
        state["old"] = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
        return callback if state["old"] else None
    except (OSError, AttributeError):
        return None


def win_register_hotkey(widget, hotkey_id):
    """註冊 HOTKEYS 中指定 id 的全域快捷鍵；被其他程式占用時回傳 False。"""
    try:
        user32 = ctypes.WinDLL("user32")
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        _, mods, vk, _ = next(hk for hk in HOTKEYS if hk[0] == hotkey_id)
        return bool(user32.RegisterHotKey(_hwnd(widget), hotkey_id, mods, vk))
    except (OSError, AttributeError):
        return False


def win_unregister_hotkey(widget, hotkey_id):
    try:
        user32 = ctypes.WinDLL("user32")
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey(_hwnd(widget), hotkey_id)
    except (OSError, AttributeError):
        pass


def hotkey_status_message(failed):
    """依註冊失敗的快捷鍵名稱，產生狀態列提示；還有能用的就引導改用它。"""
    usable = [name for _, _, _, name in HOTKEYS if name not in failed]
    if usable:
        return f"{'、'.join(failed)} 已被其他程式占用，請改用 {'、'.join(usable)} 叫出 DayNote"
    return f"{' 和 '.join(failed)} 都已被其他程式占用；隱藏後請再點 StartDayNote 叫回"


def win_set_background_window(widget):
    """背景模式：設為工具視窗，不出現在工作列與 Alt + Tab（需重新顯示視窗才會套用）。"""
    try:
        user32 = _user32()
        hwnd = _hwnd(widget)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    except (OSError, AttributeError):
        pass


def win_is_background_window(widget):
    try:
        style = _user32().GetWindowLongW(_hwnd(widget), GWL_EXSTYLE)
        return bool(style & WS_EX_TOOLWINDOW) and not style & WS_EX_APPWINDOW
    except (OSError, AttributeError):
        return False


def win_is_minimized(widget):
    try:
        return bool(ctypes.WinDLL("user32").IsIconic(_hwnd(widget)))
    except (OSError, AttributeError):
        return False


def _show_window_async(widget, cmd):
    """以 ShowWindowAsync 改變視窗狀態。

    不可用同步的 ShowWindow：它會在 ctypes 呼叫中途觸發 tkinter 的視窗事件（例如 <Unmap>），
    此時 GIL 已釋放，Python 會直接當掉（實測 Fatal Python error）。
    """
    user32 = ctypes.WinDLL("user32")
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync(_hwnd(widget), cmd)


def win_show_no_activate(widget):
    _show_window_async(widget, 4)  # SW_SHOWNOACTIVATE：顯示但不搶焦點


def win_has_appwindow(widget):
    try:
        return bool(_user32().GetWindowLongW(_hwnd(widget), GWL_EXSTYLE) & WS_EX_APPWINDOW)
    except (OSError, AttributeError):
        return False


def win_round_corners(widget):
    """Windows 11 圓角（DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND）；不支援的系統直接略過。"""
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        preference = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(_hwnd(widget), 33, ctypes.byref(preference),
                                     ctypes.sizeof(preference))
    except (OSError, AttributeError):
        pass


def win_minimize(widget):
    """無邊框視窗不能用 tkinter 的 iconify()，改用 ShowWindowAsync(SW_MINIMIZE)。"""
    _show_window_async(widget, 6)


def win_virtual_screen():
    """回傳所有螢幕合起來的範圍 (left, top, right, bottom)；取不到時回傳 None。"""
    try:
        metric = ctypes.WinDLL("user32").GetSystemMetrics
        left, top = metric(76), metric(77)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        return left, top, left + metric(78), top + metric(79)  # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
    except (OSError, AttributeError):
        return None


# ---------------------------------------------------------------- Google 連線

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """接收 OAuth loopback 回呼，把 query 參數交給 server.result。"""

    def do_GET(self):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
        if "code" in query or "error" in query:
            self.server.result = query
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h3>DayNote 已收到登入結果，可以關閉此分頁。</h3>".encode("utf-8"))

    def log_message(self, *args):
        pass  # 不輸出任何記錄，避免授權碼外洩；pythonw 下也沒有 stderr 可寫


class Google:
    """Google OAuth（loopback + PKCE）與 Tasks / Calendar API 的精簡封裝。

    網路相關方法都會阻塞，必須在背景執行緒呼叫。
    """

    def __init__(self, cfg):
        self.client_id = cfg["client_id"]
        self.client_secret = cfg.get("client_secret", "")
        # 若 ca_file 有值就額外信任該根憑證（公司 TLS 檢查用）；絕不關閉憑證驗證
        ctx = ssl.create_default_context(cafile=cfg.get("ca_file") or None)
        # ProxyHandler() 不帶參數時會沿用系統代理設定（Windows 登錄檔／環境變數）
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(), urllib.request.HTTPSHandler(context=ctx))
        self.refresh_token = load_refresh_token()
        self.access_token = None
        self.expires_at = 0.0
        self.lock = threading.Lock()

    # ---- 底層 HTTP

    def _http(self, method, url, body=None, headers=None, form=False):
        """送出請求，回傳 (狀態碼, 回應標頭, JSON)。4xx/5xx 不拋例外，由呼叫端判斷。"""
        headers = dict(headers or {})
        data = None
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=30) as resp:
                status, resp_headers, raw = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            status, resp_headers, raw = e.code, e.headers, e.read()
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                raise ApiError("TLS 憑證驗證失敗。若公司網路有 TLS 檢查，請向 IT 取得根憑證（PEM 格式），"
                               "並在 config.json 的 ca_file 指定檔案路徑。") from None
            raise ApiError(f"網路連線失敗：{e.reason}") from None
        except OSError as e:
            raise ApiError(f"網路連線失敗：{e}") from None
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        return status, resp_headers, payload

    # ---- OAuth

    def _request_token(self, form):
        """呼叫 token 端點；成功時更新 access token（及新的 refresh token）。"""
        form = {"client_id": self.client_id, "client_secret": self.client_secret, **form}
        status, _, data = self._http("POST", TOKEN_URL, form, form=True)
        if status == 200:
            self.access_token = data["access_token"]
            self.expires_at = time.time() + int(data.get("expires_in", 3600)) - 60
            if data.get("refresh_token"):
                self.refresh_token = data["refresh_token"]
                save_refresh_token(self.refresh_token)
        return status, data

    def login(self, on_url=None):
        """開啟瀏覽器登入，等待 loopback 回呼並換取 token。

        on_url：取得登入網址時的回呼（在背景執行緒呼叫），供畫面複製網址作為備援。
        """
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(16)

        server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)  # 0 = 隨機埠
        server.timeout = 1
        server.result = None
        redirect_uri = f"http://127.0.0.1:{server.server_port}"
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",  # 要求 refresh token
            "prompt": "consent",       # 確保每次重新登入都會拿到新的 refresh token
        }
        auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)
        if on_url:
            on_url(auth_url)
        webbrowser.open(auth_url)
        deadline = time.time() + LOGIN_TIMEOUT_SEC
        try:
            while server.result is None and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        result = server.result
        if result is None:
            raise ApiError("登入逾時，請再按一次「登入」")
        if result.get("state") != state:
            raise ApiError("登入回應的 state 不符，已中止")
        if "error" in result:
            raise ApiError(f"登入未完成：{result['error']}")
        status, data = self._request_token({
            "grant_type": "authorization_code",
            "code": result["code"],
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        })
        if status != 200:
            raise ApiError(f"換取 token 失敗（HTTP {status}：{data.get('error', '')}）")
        if not self.refresh_token:
            raise ApiError("Google 沒有回傳 refresh token，請再登入一次")

    def logout(self):
        self.access_token = None
        self.refresh_token = None
        delete_refresh_token()

    def revoke(self):
        """撤銷 Google 授權並登出；不論撤銷成敗，本機登入資料一律刪除。

        @return True：Google 已撤銷（或授權早已失效）；False：連不上 Google，需到帳號頁面手動移除
        """
        token = self.refresh_token
        try:
            if not token:
                return True
            status, _, _ = self._http("POST", REVOKE_URL, {"token": token}, form=True)
            return status in (200, 400)  # 400 invalid_token：授權早已失效，視同已撤銷
        except ApiError:
            return False
        finally:
            self.logout()

    def _ensure_access(self):
        """確保有可用的 access token；過期時用 refresh token 更新，更新失敗則要求重新登入。"""
        with self.lock:
            if self.access_token and time.time() < self.expires_at:
                return
            if not self.refresh_token:
                raise NeedLogin("尚未登入 Google")
            status, data = self._request_token(
                {"grant_type": "refresh_token", "refresh_token": self.refresh_token})
            if status == 400 and data.get("error") == "invalid_grant":
                self.logout()
                raise NeedLogin("登入已過期或權限已撤銷，請重新登入")
            if data.get("error") == "unauthorized_client":
                # 登入憑證是由另一個 OAuth 用戶端發出的（config.json 換過 client_id），重新登入即可
                self.logout()
                raise NeedLogin("config.json 的用戶端已更換，舊的登入憑證無法使用，請重新登入")
            if status != 200:
                raise ApiError(f"更新登入狀態失敗（HTTP {status}：{data.get('error', '')}）")

    # ---- API

    def api(self, method, url, params=None, body=None):
        """呼叫 Google API。401 會更新 token 後重試一次；429/5xx 依 Retry-After 等待後重試一次。"""
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(2):
            self._ensure_access()
            status, headers, data = self._http(
                method, url, body, {"Authorization": f"Bearer {self.access_token}"})
            if status < 300:
                return data
            if attempt == 0 and status == 401:
                self.access_token = None
                continue
            if attempt == 0 and status in (429, 500, 502, 503, 504):
                retry_after = headers.get("Retry-After") or ""
                time.sleep(min(int(retry_after), 10) if retry_after.isdigit() else 2)
                continue
            break
        error = data.get("error")
        message = error.get("message", "") if isinstance(error, dict) else ""
        if status == 404:
            raise NotFound(f"Google 上找不到這筆資料（HTTP 404）：{message}")
        raise ApiError(f"Google API 錯誤（HTTP {status}）：{message}")

    def _paged(self, url, params):
        """依 nextPageToken 取回所有分頁的 items。"""
        items, page_token = [], None
        while True:
            data = self.api("GET", url, dict(params, pageToken=page_token) if page_token else params)
            items += data.get("items", [])
            page_token = data.get("nextPageToken")
            if not page_token:
                return items

    def tasklists(self):
        return self._paged(f"{TASKS_API}/users/@me/lists", {"maxResults": 100})

    def add_tasklist(self, title):
        return self.api("POST", f"{TASKS_API}/users/@me/lists", body={"title": title})

    def open_tasks(self, list_id):
        # showCompleted=false：不回傳已完成任務（已完成任務才會被隱藏，因此不需要 showHidden）
        return self._paged(f"{TASKS_API}/lists/{_q(list_id)}/tasks",
                           {"showCompleted": "false", "maxResults": 100})

    def due_tasks(self, list_id, day):
        """某日到期的所有任務（含已完成、隱藏、已刪除），供建立下一期前的防重複檢查。

        實測：dueMax 不含邊界，[當日 00:00Z, 隔日 00:00Z) 剛好只回傳當日任務。
        """
        return self._paged(f"{TASKS_API}/lists/{_q(list_id)}/tasks", {
            "showCompleted": "true", "showHidden": "true", "showDeleted": "true", "maxResults": 100,
            "dueMin": f"{day.isoformat()}T00:00:00.000Z",
            "dueMax": f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00.000Z"})

    def completed_since(self, list_id, since):
        """since（本機時間）之後完成的任務，含隱藏；供同步時補建手機上完成的週期任務。"""
        return self._paged(f"{TASKS_API}/lists/{_q(list_id)}/tasks", {
            "showCompleted": "true", "showHidden": "true", "maxResults": 100,
            "completedMin": since.astimezone().isoformat()})

    def add_task(self, list_id, title, due, parent=None, notes=None):
        """新增工作；parent 指定時建立為該工作的子工作；notes 用來帶入「⏰ 時間」行。"""
        body = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            # due 只有日期有意義，時間部分固定填 00:00:00Z
            body["due"] = f"{due.isoformat()}T00:00:00.000Z"
        params = {"parent": parent} if parent else None
        return self.api("POST", f"{TASKS_API}/lists/{_q(list_id)}/tasks", params=params, body=body)

    def update_task(self, list_id, task_id, fields):
        """部分更新（標題、詳細資訊、日期）；欄位值為 None 代表清除。"""
        return self.api("PATCH", f"{TASKS_API}/lists/{_q(list_id)}/tasks/{_q(task_id)}", body=fields)

    def delete_task(self, list_id, task_id):
        self.api("DELETE", f"{TASKS_API}/lists/{_q(list_id)}/tasks/{_q(task_id)}")

    def complete_task(self, list_id, task_id):
        self.api("PATCH", f"{TASKS_API}/lists/{_q(list_id)}/tasks/{_q(task_id)}",
                 body={"status": "completed"})

    def uncomplete_task(self, list_id, task_id):
        """復原：把已完成的任務改回未完成（completed 時間一併清除）。"""
        self.api("PATCH", f"{TASKS_API}/lists/{_q(list_id)}/tasks/{_q(task_id)}",
                 body={"status": "needsAction", "completed": None})

    def events(self, start, end, calendar_id="primary"):
        """讀取日曆在 [start, end) 期間的事件；singleEvents=true 會把重複事件展開成單筆。"""
        def local_iso(d):
            return dt.datetime.combine(d, dt.time()).astimezone().isoformat()
        return self._paged(f"{CAL_API}/calendars/{_q(calendar_id)}/events", {
            "timeMin": local_iso(start), "timeMax": local_iso(end),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 250})


# ---- 任務時間與週期：Google Tasks API 不能存時間與重複規則，改存在詳細資訊開頭
#      「⏰ 15:00」「🔁 每週一」（方案 B）；手機上看得到，也可以直接在手機輸入

_TIME_LINE = re.compile(r"^\s*⏰️?\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")
_RECUR_LINE = re.compile(r"^\s*🔁️?\s*(.+?)\s*$")
LAST_DAY = -1  # 每月最後一天


@dataclasses.dataclass(frozen=True)
class Recur:
    """重複規則。kind：daily／weekly／monthly／yearly。

    weekdays 為 date.weekday() 的集合（0＝週一）；monthly 的 day 可為 LAST_DAY；yearly 使用 month＋day。
    """
    kind: str
    weekdays: frozenset = frozenset()
    day: int | None = None
    month: int | None = None


_WEEKDAY_CHARS = {c: i for i, c in enumerate(WEEKDAY_NAME)} | {"天": 6, "7": 6} | {str(i + 1): i for i in range(6)}
_WEEKLY_RE = re.compile(r"^每(?:週|周|星期)(.+)$")
_MONTHLY_RE = re.compile(r"^每月(\d{1,2})[日號]$")
_YEARLY_RE = re.compile(r"^每年(\d{1,2})(?:/|月)(\d{1,2})日?$")
_WEEKDAYS_MON_FRI = frozenset(range(5))


def parse_recur(text):
    """解析重複規則文字（每天、平日、每週一三五、每月15日、每月最後一天、每年3/15）。無法解析回傳 None。

    第一版不支援「每 N 週」「每月第 N 個星期 X」。
    """
    text = (text or "").strip()
    if text == "每天":
        return Recur("daily")
    if text == "平日":
        return Recur("weekly", _WEEKDAYS_MON_FRI)
    if text == "每月最後一天":
        return Recur("monthly", day=LAST_DAY)
    match = _WEEKLY_RE.match(text)
    if match:
        chars = re.sub(r"[\s、,，]", "", match.group(1))
        if not chars or any(c not in _WEEKDAY_CHARS for c in chars):
            return None
        return Recur("weekly", frozenset(_WEEKDAY_CHARS[c] for c in chars))
    match = _MONTHLY_RE.match(text)
    if match:
        day = int(match.group(1))
        return Recur("monthly", day=day) if 1 <= day <= 31 else None
    match = _YEARLY_RE.match(text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        try:
            dt.date(2028, month, day)  # 以閏年驗證，2/29 合法
        except ValueError:
            return None
        return Recur("yearly", day=day, month=month)
    return None


def format_recur(rule):
    """重複規則的標準寫法（與 parse_recur 互為反函數）。"""
    if rule.kind == "daily":
        return "每天"
    if rule.kind == "weekly":
        if rule.weekdays == _WEEKDAYS_MON_FRI:
            return "平日"
        return "每週" + "".join(WEEKDAY_NAME[i] for i in sorted(rule.weekdays))
    if rule.kind == "monthly":
        return "每月最後一天" if rule.day == LAST_DAY else f"每月{rule.day}日"
    return f"每年{rule.month}/{rule.day}"


def split_meta(notes):
    """從詳細資訊開頭拆出時間與重複規則：回傳 (time 或 None, Recur 或 None, 內文)。

    ⏰、🔁 兩行順序不拘，各取第一行；遇到第一行非 metadata（含無效規則）即停止，該行起皆為內文。
    """
    lines = (notes or "").split("\n")
    time_value, rule, index = None, None, 0
    for line in lines:
        time_match = _TIME_LINE.match(line)
        recur_match = _RECUR_LINE.match(line)
        if time_value is None and time_match:
            time_value = dt.time(int(time_match.group(1)), int(time_match.group(2)))
        elif rule is None and recur_match and parse_recur(recur_match.group(1)):
            rule = parse_recur(recur_match.group(1))
        else:
            break
        index += 1
    if index == 0:
        return None, None, notes or ""
    return time_value, rule, "\n".join(lines[index:]).lstrip("\n")


def join_meta(time_value, rule, body):
    """把時間與重複規則放回詳細資訊開頭（固定 ⏰ 在前、🔁 在後）；都沒有時只回傳內文。"""
    head = []
    if time_value:
        head.append(f"⏰ {time_value:%H:%M}")
    if rule:
        head.append(f"🔁 {format_recur(rule)}")
    return "\n".join(head + ([body] if body else [])) if head else body


def _matches(rule, day):
    if rule.kind == "daily":
        return True
    if rule.kind == "weekly":
        return day.weekday() in rule.weekdays
    month_len = calendar.monthrange(day.year, day.month)[1]
    target = month_len if rule.day == LAST_DAY else min(rule.day, month_len)  # 小月退到月底
    if rule.kind == "monthly":
        return day.day == target
    return day.month == rule.month and day.day == target


def next_due(rule, base, today):
    """下一期日期：原定日期（無日期時用今天）之後第一個符合規則的日子；早於今天就繼續往後推，不補出過期各期。"""
    start = max((base or today) + dt.timedelta(days=1), today)
    for offset in range(366 * 2):
        day = start + dt.timedelta(days=offset)
        if _matches(rule, day):
            return day
    raise ValueError(f"找不到符合規則的日期：{rule}")


def next_task_for(task, today):
    """完成或略過一筆任務後要建立的下一期內容；不是週期父工作時回傳 None。"""
    if not task.get("recur") or task.get("parent"):
        return None
    return {"list_id": task["list_id"], "title": task["title"],
            "due": next_due(task["recur"], task["due"], today),
            "notes": join_meta(task.get("time"), task["recur"], task.get("notes", ""))}


def can_skip(task):
    """「略過這一期」只提供給週期父工作（子工作不支援週期）。"""
    return bool(task.get("recur")) and not task.get("parent")


def find_successor(candidates, task, due, include_deleted=False):
    """在 Google 上的任務（API 格式）中找已存在的下一期：同標題、同規則、同日期的最上層任務。

    已完成的也算；已刪除的只有 include_deleted 時才算（同步補建用：刪掉下一期代表結束週期）。
    """
    for raw in candidates:
        if raw.get("id") == task["id"] or raw.get("parent"):
            continue
        if raw.get("deleted") and not include_deleted:
            continue
        if (raw.get("title") or "") != task["title"] or task_due(raw) != due:
            continue
        if split_meta(raw.get("notes"))[1] == task["recur"]:
            return raw
    return None


def spawn_next(google, task, plan, include_deleted=False):
    """（背景執行緒）依 plan 建立下一期；Google 上已有下一期時不建立並回傳 None，否則回傳新任務。"""
    if find_successor(google.due_tasks(task["list_id"], plan["due"]), task, plan["due"], include_deleted):
        return None
    created = google.add_task(plan["list_id"], plan["title"], plan["due"], notes=plan["notes"])
    return {"id": created["id"], "title": created.get("title") or plan["title"],
            "list_id": task["list_id"], "list_title": task.get("list_title", ""),
            "due": task_due(created) or plan["due"], "parent": None, "time": task.get("time"),
            "recur": task["recur"], "notes": task.get("notes", ""), "position": created.get("position", "")}


def reminder_key(task_id, when):
    """已提醒記錄的鍵：任務 id＋提醒時間（重開 DayNote 不重複提醒）。"""
    return f"{task_id}@{when:%Y-%m-%dT%H:%M}"


def mark_past_reminder(reminded, task, now):
    """新建的一期若提醒時間已過，直接記為已提醒，避免剛完成就跳出同一件事的提醒。有標記時回傳 True。"""
    if not (task.get("time") and task.get("due")):
        return False
    when = dt.datetime.combine(task["due"], task["time"])
    if when > now:
        return False
    reminded.add(reminder_key(task["id"], when))
    return True


def delete_prompt(task, child_count):
    """刪除確認文字；週期任務會提醒「不會再產生下一期」並引導改用略過。"""
    extra = f"\n（含 {child_count} 個子工作）" if child_count else ""
    text = f"確定要刪除「{task['title']}」？{extra}\n\n刪除後無法在 DayNote 復原。"
    if can_skip(task):
        text = (f"這是重複任務（🔁 {format_recur(task['recur'])}），刪除後不會再產生下一期。\n"
                f"只想跳過這次，請改用「略過這一期」。\n\n{text}")
    return text


def task_subtitle(task, today):
    """列表卡片的副標題：逾期／時間、週期、清單名稱。"""
    due, at = task["due"], time_text(task.get("time"))
    parts = []
    if due is not None and due < today:
        parts.append(f"已逾期 · {due.month}月{due.day}日{' ' + at if at else ''}")
    elif at:
        parts.append(f"⏰ {at}")
    if task.get("recur"):
        parts.append(f"🔁 {format_recur(task['recur'])}")
    parts.append(task["list_title"])
    return " · ".join(parts)


def apply_fields(task, fields):
    """把 API 欄位格式的修改（title／notes／due）套用到畫面上的任務。"""
    if "title" in fields:
        task["title"] = fields["title"]
    if "notes" in fields:
        task["time"], task["recur"], task["notes"] = split_meta(fields["notes"])
    if "due" in fields:
        task["due"] = task_due({"due": fields["due"]})


def parse_time_text(text):
    """解析使用者輸入的時間：15:00、1500、9:05、9：05（全形冒號）。無法解析回傳 None。"""
    match = re.match(r"^\s*(\d{1,2})\s*[:：]?\s*(\d{2})\s*$", text or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return dt.time(hour, minute) if hour < 24 and minute < 60 else None


def time_text(time_value):
    return f"{time_value:%H:%M}" if time_value else ""


def _notes_preview(notes, limit=24):
    """詳細資訊的單行預覽：取第一個非空白行，過長時截斷。"""
    line = next((ln.strip() for ln in notes.splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[:limit] + "…"


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def _set_bg(widget, color):
    """把元件及其所有子元件的底色一起換掉（清單列 hover 用）。"""
    try:
        widget.config(bg=color)
    except tk.TclError:
        pass
    on_bg = getattr(widget, "on_bg", None)
    if on_bg:  # 反鋸齒圓圈的邊緣混了底色，底色變了要換圖
        on_bg(color)
    for child in widget.winfo_children():
        _set_bg(child, color)


def _hex_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def round_image(widget, size, fill, bg, outline=None, width=0, bg_below=None):
    """Tk 的 Canvas 畫圓沒有反鋸齒，改用 4×4 超取樣算出邊緣混色後的圓形圖片。

    @param size 直徑（實際像素）
    @param fill 圓內顏色；只要外框時傳入底色
    @param bg 圓外的底色
    @param outline 外框顏色，None 表示實心圓
    @param width 外框粗細（實際像素）
    @param bg_below (列, 顏色)：從該列起底色不同，例如圓點跨過月曆選取框的下緣
    @return tk.PhotoImage（依參數快取，同一張圖重複使用）
    """
    root = widget._root()
    cache = root.__dict__.setdefault("_round_cache", {})
    key = (size, fill, bg, outline, width, bg_below)
    if key in cache:
        return cache[key]
    n = 4
    r_out = size / 2
    r_in = r_out - width if outline else r_out
    fill_rgb, line_rgb = _hex_rgb(fill), _hex_rgb(outline or fill)
    rows = []
    for y in range(size):
        back = _hex_rgb(bg_below[1] if bg_below and y >= bg_below[0] else bg)
        line = []
        for x in range(size):
            outer = inner = 0
            for sy in range(n):
                dy = y + (sy + 0.5) / n - r_out
                for sx in range(n):
                    dx = x + (sx + 0.5) / n - r_out
                    d2 = dx * dx + dy * dy
                    if d2 <= r_out * r_out:
                        outer += 1
                        if d2 <= r_in * r_in:
                            inner += 1
            a_fill, a_line, a_bg = inner / n ** 2, (outer - inner) / n ** 2, 1 - outer / n ** 2
            line.append("#%02x%02x%02x" % tuple(
                round(f * a_fill + o * a_line + b * a_bg) for f, o, b in zip(fill_rgb, line_rgb, back)))
        rows.append("{" + " ".join(line) + "}")
    img = tk.PhotoImage(master=root, width=size, height=size)
    img.put(" ".join(rows))
    cache[key] = img
    return img


def _q(value):
    return urllib.parse.quote(value, safe="")


def task_due(task):
    """Tasks 的 due 只有日期有意義：直接取前 10 碼當作「那一天」，不做時區轉換。"""
    due = task.get("due")
    return dt.date.fromisoformat(due[:10]) if due else None


def event_days(event):
    """回傳事件要顯示的 [(日期, 時間文字)]。

    全天事件展開到每一天（end.date 為不含當天）；有時間的事件只放在開始那天（轉成本機時區）。
    """
    start, end = event.get("start", {}), event.get("end", {})
    if "date" in start:
        first = dt.date.fromisoformat(start["date"])
        last = dt.date.fromisoformat(end.get("date", start["date"]))
        return [(first + dt.timedelta(days=i), "全天") for i in range(max((last - first).days, 1))]
    begin = dt.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone()
    return [(begin.date(), begin.strftime("%H:%M"))]


def backfill_recurring(google, lists, since, today):
    """（背景執行緒）補建 since 之後在其他裝置（手機）完成、但還沒有下一期的週期任務。

    已刪除的下一期也算存在（使用者刪掉下一期 = 結束週期）。回傳 (新建任務, 是否全部成功)。
    """
    spawned, ok = [], True
    for lst in lists:
        try:
            completed = google.completed_since(lst["id"], since)
        except ApiError:
            ok = False
            continue
        for raw in completed:
            if raw.get("parent") or raw.get("deleted"):
                continue
            time_value, rule, body = split_meta(raw.get("notes"))
            if not rule:
                continue
            task = {"id": raw["id"], "title": raw.get("title") or "", "list_id": lst["id"],
                    "list_title": lst.get("title", ""), "due": task_due(raw), "parent": None,
                    "time": time_value, "recur": rule, "notes": body}
            try:
                created = spawn_next(google, task, next_task_for(task, today), include_deleted=True)
            except ApiError:
                ok = False
                continue
            if created:
                created["backfilled"] = True
                spawned.append(created)
    return spawned, ok


# ---------------------------------------------------------------- 畫面

class Tooltip:
    """滑鼠停留在元件上 0.5 秒後，在下方顯示一行功能說明。

    text 可以是字串，或回傳字串的函式（用於會變動的說明，例如置頂開關）。
    """

    DELAY_MS = 500

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self.job = None
        # add="+"：不覆蓋元件原本的滑鼠事件（例如按鈕的 hover 變色）
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        self._cancel()
        self.job = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None

    def _show(self):
        self.job = None
        text = self.text() if callable(self.text) else self.text
        tip = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)  # 主視窗置頂時，提示也要在最上層
        tk.Label(tip, text=text, bg=TEXT, fg="white", font=SMALL, padx=px(6), pady=px(2)).pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() - tip.winfo_reqwidth()) // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(4)
        screen = win_virtual_screen()
        if screen:  # 靠近螢幕右緣（例如 ✕ 按鈕）時往內縮，避免提示被切掉
            x = min(max(x, screen[0]), screen[2] - tip.winfo_reqwidth())
        tip.geometry(f"+{x}+{y}")
        self.tip = tip

    def _hide(self, _=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class _Popup:
    """跟著某個按鈕跳出的小視窗（日期、時間選擇器共用）：點外面或按 Esc 關閉。"""

    def __init__(self, app, anchor):
        self.app = app
        self.win = tk.Toplevel(app)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        self.body = tk.Frame(self.win, bg=CARD_BG, padx=px(8), pady=px(6))
        self.body.pack()
        self._render()
        self._place(anchor)
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<Button-1>", self._click_outside, add="+")
        self.win.focus_force()
        self.win.grab_set()  # 點視窗其他地方時，事件會先送到這裡，用來判斷是否點在外面

    def _render(self):
        raise NotImplementedError

    def _place(self, anchor):
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        x = anchor.winfo_rootx() + anchor.winfo_width() - w
        y = anchor.winfo_rooty() + anchor.winfo_height() + px(4)
        screen = win_virtual_screen()
        if screen:
            if y + h > screen[3]:  # 下方放不下（例如底部輸入列）就改放上方
                y = anchor.winfo_rooty() - h - px(4)
            x = min(max(x, screen[0]), screen[2] - w)
        self.win.geometry(f"+{x}+{y}")

    def _click_outside(self, event):
        x, y = event.x_root, event.y_root
        wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
        if not (wx <= x < wx + self.win.winfo_width() and wy <= y < wy + self.win.winfo_height()):
            self.close()

    def _link(self, parent, text, command, fg=ACCENT, font=SMALL):
        lbl = tk.Label(parent, text=text, bg=CARD_BG, fg=fg, font=font, cursor="hand2", padx=px(4))
        lbl.bind("<Button-1>", lambda e: command())
        return lbl

    def pick(self, value):
        self.close()
        self.on_pick(value)

    def close(self):
        if self.win.winfo_exists():
            self.win.grab_release()
            self.win.destroy()


class DatePicker(_Popup):
    """小月曆日期選擇器：快捷選項（今天／明天／下週一／無日期）＋月曆。"""

    def __init__(self, app, anchor, current, on_pick, allow_none=True):
        self.on_pick = on_pick
        base = current or dt.date.today()
        self.view = (base.year, base.month)
        self.current = current
        self.allow_none = allow_none
        super().__init__(app, anchor)

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        today = dt.date.today()
        quick = tk.Frame(self.body, bg=CARD_BG)
        quick.pack(fill="x", pady=(0, px(4)))
        next_monday = today + dt.timedelta(days=7 - today.weekday())
        options = [("今天", today), ("明天", today + dt.timedelta(days=1)), ("下週一", next_monday)]
        for text, day in options:
            self._link(quick, text, lambda d=day: self.pick(d)).pack(side="left")
        if self.allow_none:
            self._link(quick, "無日期", lambda: self.pick(None), fg=GRAY).pack(side="left")

        head = tk.Frame(self.body, bg=CARD_BG)
        head.pack(fill="x")
        year, month = self.view
        self._link(head, "◀", lambda: self._shift(-1), fg=GRAY, font=ICON_FONT).pack(side="left")
        self._link(head, "▶", lambda: self._shift(1), fg=GRAY, font=ICON_FONT).pack(side="right")
        tk.Label(head, text=f"{year} 年 {month} 月", bg=CARD_BG, fg=TEXT, font=FONT).pack(side="left", expand=True)

        grid = tk.Frame(self.body, bg=CARD_BG)
        grid.pack()
        for col, name in enumerate(WEEK_HEAD):
            tk.Label(grid, text=name, bg=CARD_BG, fg=GRAY, font=SMALL, width=3).grid(row=0, column=col)
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
        for row, week in enumerate(weeks, 1):
            for col, day in enumerate(week):
                fg = TEXT if day.month == month else "#c8c6c4"
                bg = CARD_BG
                if day == self.current:
                    fg, bg = "white", ACCENT
                elif day == today:
                    fg = ACCENT
                lbl = tk.Label(grid, text=str(day.day), bg=bg, fg=fg, font=SMALL, width=3,
                               pady=px(2), cursor="hand2")
                lbl.grid(row=row, column=col)
                lbl.bind("<Button-1>", lambda e, d=day: self.pick(d))

    def _shift(self, delta):
        year, month = self.view
        index = year * 12 + (month - 1) + delta
        self.view = (index // 12, index % 12 + 1)
        self._render()



class TimePicker(_Popup):
    """提醒時間選擇器：常用時間＋自訂（HH:MM，Enter 確認）＋不設時間。"""

    PRESETS = ("09:00", "12:00", "15:00", "18:00", "20:00")

    def __init__(self, app, anchor, current, on_pick):
        self.on_pick = on_pick
        self.current = current
        super().__init__(app, anchor)

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        tk.Label(self.body, text="提醒時間（選填）", bg=CARD_BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x")
        grid = tk.Frame(self.body, bg=CARD_BG)
        grid.pack(fill="x", pady=px(4))
        for i, text in enumerate(self.PRESETS):
            value = parse_time_text(text)
            selected = value == self.current
            lbl = tk.Label(grid, text=text, bg=ACCENT if selected else CARD_BG, fg="white" if selected else TEXT,
                           font=FONT, cursor="hand2", padx=px(6), pady=px(2))
            lbl.grid(row=i // 3, column=i % 3, padx=px(2), pady=px(2), sticky="ew")
            lbl.bind("<Button-1>", lambda e, v=value: self.pick(v))
        row = tk.Frame(self.body, bg=CARD_BG)
        row.pack(fill="x", pady=(px(2), px(4)))
        tk.Label(row, text="自訂", bg=CARD_BG, fg=GRAY, font=SMALL).pack(side="left")
        self.custom = tk.Entry(row, width=6, font=FONT, relief="flat", bg=BG, fg=TEXT, insertbackground=TEXT,
                               highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        self.custom.insert(0, time_text(self.current) or "")
        self.custom.pack(side="left", padx=px(6))
        self.custom.bind("<Return>", lambda e: self._pick_custom())
        self._link(row, "確定", self._pick_custom).pack(side="left")
        self.hint = tk.Label(self.body, text="", bg=CARD_BG, fg=RED, font=SMALL, anchor="w")
        self.hint.pack(fill="x")
        self._link(self.body, "不設時間", lambda: self.pick(None), fg=GRAY).pack(anchor="w")

    def _pick_custom(self):
        value = parse_time_text(self.custom.get())
        if value is None:
            self.hint.config(text="請輸入 HH:MM，例如 15:30")
            return
        self.pick(value)


class RecurPicker(_Popup):
    """重複規則選擇器：依起始日期提供常用規則＋自訂（例如「每週一三五」，Enter 確認）＋不重複。"""

    def __init__(self, app, anchor, current, base, on_pick):
        self.on_pick = on_pick
        self.current = current
        self.base = base
        super().__init__(app, anchor)

    def _options(self):
        base = self.base
        options = ["每天", "平日", f"每週{WEEKDAY_NAME[base.weekday()]}", f"每月{base.day}日"]
        if base.day == calendar.monthrange(base.year, base.month)[1]:
            options.append("每月最後一天")
        options.append(f"每年{base.month}/{base.day}")
        return [parse_recur(text) for text in options]

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        tk.Label(self.body, text="重複（依起始日期）", bg=CARD_BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x")
        for rule in self._options():
            selected = rule == self.current
            lbl = tk.Label(self.body, text=format_recur(rule), bg=ACCENT if selected else CARD_BG,
                           fg="white" if selected else TEXT, font=FONT, cursor="hand2", anchor="w",
                           padx=px(6), pady=px(2))
            lbl.pack(fill="x", pady=px(1))
            lbl.bind("<Button-1>", lambda e, r=rule: self.pick(r))
        row = tk.Frame(self.body, bg=CARD_BG)
        row.pack(fill="x", pady=(px(4), px(2)))
        tk.Label(row, text="自訂", bg=CARD_BG, fg=GRAY, font=SMALL).pack(side="left")
        self.custom = tk.Entry(row, width=12, font=FONT, relief="flat", bg=BG, fg=TEXT, insertbackground=TEXT,
                               highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        self.custom.insert(0, format_recur(self.current) if self.current else "")
        self.custom.pack(side="left", padx=px(6))
        self.custom.bind("<Return>", lambda e: self._pick_custom())
        self._link(row, "確定", self._pick_custom).pack(side="left")
        self.hint = tk.Label(self.body, text="", bg=CARD_BG, fg=RED, font=SMALL, anchor="w", justify="left")
        self.hint.pack(fill="x")
        self._link(self.body, "不重複", lambda: self.pick(None), fg=GRAY).pack(anchor="w")

    def _pick_custom(self):
        rule = parse_recur(self.custom.get())
        if rule is None:
            self.hint.config(text="例如：每天、平日、每週一三五、\n每月15日、每月最後一天、每年3/15")
            return
        self.pick(rule)


def win_work_area():
    """主螢幕扣掉工作列的可用範圍 (left, top, right, bottom)；取不到時回傳 None。"""
    try:
        rect = wintypes.RECT()
        if ctypes.WinDLL("user32").SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except (OSError, AttributeError):
        pass
    return None


class ReminderToast:
    """右下角提醒小視窗（仿 Windows 通知）：時間到時跳出並播放提示音，多則時往上堆疊。"""

    active = []
    WIDTH = 300

    def __init__(self, app, task, when, on_done, on_snooze):
        self.app = app
        win = self.win = tk.Toplevel(app)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        body = tk.Frame(win, bg=BG, padx=px(14), pady=px(10))
        body.pack(fill="both")

        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="DayNote 提醒", bg=BG, fg=GRAY, font=SMALL).pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=GRAY, font=ICON_FONT, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())

        late = (dt.datetime.now() - when).total_seconds() // 60
        when_text = f"⏰ {when:%H:%M}" + (f"（已過 {int(late)} 分鐘）" if late >= 2 else "")
        if when.date() != dt.date.today():
            when_text = f"⏰ {when.month}/{when.day} {when:%H:%M}"
        tk.Label(body, text=when_text, bg=BG, fg=ACCENT, font=FONT, anchor="w").pack(fill="x", pady=(px(6), 0))
        tk.Label(body, text=task["title"], bg=BG, fg=TEXT, font=("Microsoft JhengHei UI", 12, "bold"),
                 anchor="w", justify="left", wraplength=px(self.WIDTH - 40)).pack(fill="x", pady=(px(2), px(10)))

        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x")
        done = tk.Label(buttons, text="✓ 完成", bg=ACCENT, fg="white", font=FONT, cursor="hand2",
                        padx=px(12), pady=px(3))
        done.pack(side="right")
        done.bind("<Button-1>", lambda e: (self.close(), on_done()))
        snooze = tk.Label(buttons, text="10 分鐘後", bg=CARD_BG, fg=TEXT, font=FONT, cursor="hand2",
                          padx=px(12), pady=px(3), highlightthickness=1, highlightbackground=BORDER)
        snooze.pack(side="right", padx=(0, px(8)))
        snooze.bind("<Button-1>", lambda e: (self.close(), on_snooze()))

        ReminderToast.active.append(self)
        ReminderToast.relayout()
        try:
            winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except RuntimeError:
            pass  # 沒有音效裝置時靜音

    @classmethod
    def relayout(cls):
        """從工作列上方開始往上堆疊所有提醒。"""
        area = win_work_area()
        for toast in cls.active:
            toast.win.update_idletasks()
        y = (area[3] if area else toast.win.winfo_screenheight() - px(48)) - px(12)
        for toast in cls.active:
            w, h = px(cls.WIDTH), toast.win.winfo_reqheight()
            right = area[2] if area else toast.win.winfo_screenwidth()
            y -= h
            toast.win.geometry(f"{w}x{h}+{right - w - px(12)}+{y}")
            y -= px(10)

    def close(self):
        if self in ReminderToast.active:
            ReminderToast.active.remove(self)
        if self.win.winfo_exists():
            self.win.destroy()
        ReminderToast.relayout()


def ask_text(parent, title, prompt, ok_text="建立"):
    """中文按鈕的單行輸入框（tkinter 內建 simpledialog 的按鈕固定是英文）。取消回傳 None。"""
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg=BG, padx=px(16), pady=px(12))
    win.resizable(False, False)
    win.transient(parent)
    win.attributes("-topmost", True)
    tk.Label(win, text=prompt, bg=BG, fg=TEXT, font=FONT, anchor="w").pack(fill="x")
    entry = tk.Entry(win, font=FONT, width=24, relief="flat", bg=CARD_BG, fg=TEXT, insertbackground=TEXT,
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
    entry.pack(fill="x", pady=(px(6), px(12)), ipady=px(3))
    result = {"value": None}

    def ok(_=None):
        result["value"] = entry.get()
        win.destroy()

    buttons = tk.Frame(win, bg=BG)
    buttons.pack(fill="x")
    btn_ok = tk.Label(buttons, text=ok_text, bg=ACCENT, fg="white", font=FONT, padx=px(14), pady=px(3), cursor="hand2")
    btn_ok.pack(side="right")
    btn_ok.bind("<Button-1>", ok)
    btn_cancel = tk.Label(buttons, text="取消", bg=BG, fg=GRAY, font=FONT, padx=px(10), pady=px(3), cursor="hand2")
    btn_cancel.pack(side="right", padx=(0, px(6)))
    btn_cancel.bind("<Button-1>", lambda e: win.destroy())
    entry.bind("<Return>", ok)
    win.bind("<Escape>", lambda e: win.destroy())

    win.update_idletasks()  # 置中在主視窗上
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{x}+{y}")
    entry.focus_force()
    win.grab_set()
    parent.wait_window(win)
    return result["value"]


def date_text(day, short=False):
    """日期的簡短顯示：今天／明天／無日期／10/8（四）；short=True 時省略星期（空間有限的輸入列用）。"""
    if day is None:
        return "無日期"
    today = dt.date.today()
    if day == today:
        return "今天"
    if day == today + dt.timedelta(days=1):
        return "明天"
    return f"{day.month}/{day.day}" if short else f"{day.month}/{day.day}（{WEEKDAY_NAME[day.weekday()]}）"


class App(tk.Tk):
    """主視窗。網路工作一律丟到背景執行緒，結果經 queue 交回主執行緒更新畫面。"""

    def __init__(self, google, borderless=True, pin_to_desktop=True, watch_pid=False,
                 holiday_calendar=DEFAULT_HOLIDAY_CALENDAR, desktop_reminder=True, show_in_taskbar=False):
        super().__init__()
        apply_dpi_scale(self)
        self.g = google
        self.title(APP)
        self.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        self.minsize(*MIN_SIZE)
        self.state_data = load_state()
        self.borderless = borderless
        # 背景模式（預設）：不顯示工作列圖示；只有無邊框時可用，一般視窗維持系統預設
        self.show_in_taskbar = show_in_taskbar or not borderless
        self.in_taskbar = not borderless
        self.hotkeys_registered = set()  # 已註冊成功的 HOTKEYS id
        self.topmost = bool(self.state_data.get("topmost", False))
        self._drag_offset = None
        self._resize_start = None
        self._restore_geometry()
        self.attributes("-topmost", self.topmost)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.q = queue.Queue()
        self.today = dt.date.today()
        self.selected = self.today
        self.view = (self.today.year, self.today.month)
        self.lists, self.tasks, self.events = [], [], {}
        self.holidays = {}  # {日期: [(名稱, 是否放假)]}
        self.holiday_calendar = holiday_calendar
        self.weeks = []
        self.busy = False
        self.last_refresh = 0.0
        self.show_undated = False
        self.show_overdue = True
        self.compact = bool(self.state_data.get("compact_calendar", False))
        self.undo_group = None
        self.undo_job = None
        self.new_due = self.today   # 新增工作的日期，預設跟著月曆選中的日期
        self.new_time = None        # 新增工作的提醒時間（選填）
        self.desktop_reminder = desktop_reminder
        self.reminded = set(self.state_data.get("reminded", []))  # 已提醒過的「任務@時間」
        self.snoozed = {k: dt.datetime.fromisoformat(v) for k, v in self.state_data.get("snoozed", {}).items()}
        self.detail = None          # 詳細頁編輯中的工作與欄位
        self.undo_spawned = None    # 完成時建立的下一期（復原時一併刪除）
        since = self.state_data.get("backfill_since")
        self.backfill_since = dt.datetime.fromisoformat(since) if since else None  # 同步補建的檢查時間點

        self._build()
        self.redraw()
        self.pin_to_desktop = pin_to_desktop
        if borderless:
            self.overrideredirect(True)
        self.after(50, self._apply_win_style)
        self.after(100, self._poll_queue)
        self.bind("<FocusIn>", self._on_focus)
        self._bind_shortcuts()
        if desktop_reminder:
            self.after(5000, self._check_reminders)
        if watch_pid:
            self.after(1000, self._watch_pid)
        if self.g.refresh_token:
            self.refresh()
        else:
            self._show_error(NeedLogin("尚未登入，請按右上角「登入」"))

    # ---- 視窗外框：位置記憶、拖曳、縮放、置頂、縮小、關閉

    def _restore_geometry(self):
        """還原上次的位置與大小；位置跑到所有螢幕外面時改用預設位置。"""
        ratio = SCALE / float(self.state_data.get("scale", 1.0))
        w = max(round(self.state_data.get("w", DEFAULT_SIZE[0] / ratio) * ratio), MIN_SIZE[0])
        h = max(round(self.state_data.get("h", DEFAULT_SIZE[1] / ratio) * ratio), MIN_SIZE[1])
        x, y = self.state_data.get("x"), self.state_data.get("y")
        if isinstance(x, int) and isinstance(y, int):
            x, y = round(x * ratio), round(y * ratio)
        screen = win_virtual_screen()
        if isinstance(x, int) and isinstance(y, int) and screen:
            left, top, right, bottom = screen
            # 至少要看得到頂部列的一部分，才拖得回來
            if left <= x + 60 <= right and top <= y + 10 <= bottom:
                self.geometry(f"{w}x{h}+{x}+{y}")
                return
        self.geometry(f"{w}x{h}+{px(200)}+{px(120)}")

    def _apply_win_style(self):
        if not self.borderless:
            # 一般視窗是備援模式，維持系統預設。
            # 實測：有邊框視窗掛到桌面後，加上工作列樣式就無法撐過 Win + D，兩者只能擇一。
            return
        self.update_idletasks()  # 確保 Windows 視窗已建立，否則設定會套到錯誤的 HWND
        if self.pin_to_desktop:
            win_pin_to_desktop(self)
        win_round_corners(self)
        if not self.show_in_taskbar:
            # 背景模式：工具視窗不在工作列，也沒有「可縮小」樣式，Win + D 不會影響它
            win_set_background_window(self)
            self.withdraw()
            self.after(10, self.deiconify)
            self.after(200, self._finish_background_setup)
            return
        if self.pin_to_desktop:
            # 「可縮小」讓工作列點擊能縮到背景，但 Win + D 也會因此縮掉視窗；
            # 被縮小時若不是使用者要求的（攔截 SC_MINIMIZE 判斷），就是 Win + D，立刻還原
            self.bind("<Unmap>", lambda e: e.widget is self and self.after(150, self._undo_show_desktop))
        self._reshow_with_appwindow(retries=1)

    def _finish_background_setup(self):
        if self.pin_to_desktop and not win_is_pinned(self):
            win_pin_to_desktop(self)
        self._install_hooks()

    def _install_hooks(self):
        """攔截視窗訊息並註冊 F8 與 Ctrl + Alt + D。回呼只排入佇列，由主執行緒切換顯示。"""
        if getattr(self, "_message_hook", None):
            return
        self._message_hook = win_hook_messages(
            self, on_user_minimize=self._mark_user_minimize,
            on_hotkey=lambda: self.q.put((lambda *_: self.toggle_visible(), None, None)))
        if self._message_hook:
            self._register_hotkey(retries=6)

    def _register_hotkey(self, retries):
        """註冊 HOTKEYS 中尚未成功的快捷鍵。重新啟動時舊的 DayNote 最多 1 秒後才關閉並釋放快捷鍵，所以失敗會每秒重試。"""
        for hotkey_id, _, _, _ in HOTKEYS:
            if hotkey_id not in self.hotkeys_registered and win_register_hotkey(self, hotkey_id):
                self.hotkeys_registered.add(hotkey_id)
        failed = [name for hotkey_id, _, _, name in HOTKEYS if hotkey_id not in self.hotkeys_registered]
        if not failed:
            return
        if retries:
            self.after(1000, lambda: self._register_hotkey(retries - 1))
        else:
            self._set_status(hotkey_status_message(failed), error=True)

    def toggle_visible(self):
        """F8 或 Ctrl + Alt + D：隱藏中就叫回來並放到最前面，顯示中就隱藏。"""
        if self.state() == "withdrawn":
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)  # 暫時置頂確保出現在最前面
            self.after(300, lambda: self.attributes("-topmost", self.topmost))
            self.focus_force()
        else:
            self.withdraw()

    def _mark_user_minimize(self):
        self._self_minimize_until = time.time() + 1.5

    def _undo_show_desktop(self):
        """被縮小時：若不是使用者要求的（工作列、Win + ↓、「—」按鈕），就是 Win + D，叫回桌面上。"""
        if time.time() < getattr(self, "_self_minimize_until", 0):
            return
        if win_is_minimized(self):
            win_show_no_activate(self)

    def _reshow_with_appwindow(self, retries):
        """套用工作列樣式並重新顯示；等視窗真的顯示後再確認，沒套上就重試。"""
        win_add_appwindow(self)
        self.withdraw()
        self.after(10, self.deiconify)
        self.after(200, lambda: self._verify_taskbar(retries))

    def _verify_taskbar(self, retries):
        # 視窗重新顯示後再確認「釘在桌面」；沒套上（例如啟動太早）就重設
        if self.pin_to_desktop and not win_is_pinned(self):
            win_pin_to_desktop(self)
        self.in_taskbar = win_has_appwindow(self)
        if self.in_taskbar:
            self._install_hooks()
            return
        if retries:
            self._reshow_with_appwindow(retries - 1)
        else:
            self.btn_min.pack_forget()  # 沒有工作列圖示時，縮小後會找不回視窗

    def _start_drag(self, event):
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _on_drag(self, event):
        if self.borderless and self._drag_offset:
            dx, dy = self._drag_offset
            self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _start_resize(self, event):
        self._resize_start = (event.x_root, event.y_root, self.winfo_width(), self.winfo_height())

    def _on_resize(self, event):
        x0, y0, w0, h0 = self._resize_start
        w = max(w0 + event.x_root - x0, MIN_SIZE[0])
        h = max(h0 + event.y_root - y0, MIN_SIZE[1])
        self.geometry(f"{w}x{h}")

    def toggle_topmost(self):
        self.topmost = not self.topmost
        self.attributes("-topmost", self.topmost)
        self._paint_pin()

    def _paint_pin(self):
        self.btn_pin.config(fg=ACCENT if self.topmost else GRAY,
                            bg=HOVER if self.topmost else BG)

    def minimize(self):
        if not self.show_in_taskbar:
            self.withdraw()  # 背景模式：隱藏視窗，程式與提醒照常執行；F8 或 Ctrl + Alt + D 叫回
            return
        if self.borderless:
            self._mark_user_minimize()  # 自己按「—」縮小，不是 Win + D
            win_minimize(self)
        else:
            self.iconify()

    def close(self, ask=True):
        """關閉 DayNote；詳細頁有未儲存的修改時先處理（ask=False：不詢問直接儲存，用於被新開的 DayNote 取代）。"""
        if not self._flush_detail_on_close(ask):
            return
        state = dict(self.state_data, topmost=self.topmost, scale=SCALE)
        if self.winfo_x() > -30000:  # 縮小中 Windows 回報 -32000，此時沿用上次存的位置
            state.update(x=self.winfo_x(), y=self.winfo_y(),
                         w=self.winfo_width(), h=self.winfo_height())
        save_state(state)
        for hotkey_id in self.hotkeys_registered:
            win_unregister_hotkey(self, hotkey_id)
        self.hotkeys_registered.clear()
        if getattr(self, "_poll_job", None):
            self.after_cancel(self._poll_job)
        self.destroy()

    def _flush_detail_on_close(self, ask=True):
        """關閉前若詳細頁有未儲存的修改：詢問儲存／不儲存／取消。可以繼續關閉時回傳 True。

        儲存必須同步送出：背景執行緒是 daemon，程式結束時會被中止，修改可能根本沒送到 Google。
        """
        if not self.detail:
            return True
        fields = self._detail_changes()
        if not fields:
            return True
        task = self.detail["task"]
        if ask:
            answer = messagebox.askyesnocancel(
                "尚未儲存", f"「{task['title']}」有尚未儲存的修改，要先儲存再關閉嗎？", parent=self, icon="warning")
            if answer is None:
                return False
            if not answer:
                return True
        try:
            self.g.update_task(task["list_id"], task["id"], fields)
        except (ApiError, NeedLogin) as e:
            if not ask:
                return True  # 被取代時無法詢問，只能放棄這次修改
            messagebox.showerror("儲存失敗", f"{e}\n\n修改尚未儲存，DayNote 先不關閉。", parent=self)
            return False
        return True

    def _icon_button(self, parent, text, command, font=ICON_FONT, tip=None):
        """扁平小按鈕（Label 實作，比 ttk.Button 窄，頂部列才放得下）。"""
        btn = tk.Label(parent, text=text, bg=BG, fg=GRAY, font=font,
                       padx=px(6), pady=px(2), cursor="hand2")
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.config(bg=HOVER))
        btn.bind("<Leave>", lambda e: btn.config(bg=BG))
        if tip:
            Tooltip(btn, tip)
        return btn

    # ---- 版面

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=(px(12), px(4)), pady=(px(6), 0))
        # 右側按鈕群先 pack，空間不足時被壓縮的是日期標題，而不是按鈕
        tools = tk.Frame(top, bg=BG)
        tools.pack(side="right", anchor="n")  # 按鈕固定在右上角
        self.btn_login = tk.Label(tools, text="登入", bg=ACCENT, fg="white", font=FONT,
                                  padx=px(8), cursor="hand2")
        self.btn_login.bind("<Button-1>", lambda e: self.login())
        Tooltip(self.btn_login, "用瀏覽器登入 Google 帳號")
        self.btn_logout = self._icon_button(tools, "登出", self.logout, font=FONT,
                                            tip="登出 Google 並撤銷 DayNote 的存取權")
        self.btn_refresh = self._icon_button(tools, "⟳", self.refresh, tip="立即同步（F5）")
        self.btn_refresh.pack(side="left")
        self.btn_pin = self._icon_button(
            tools, "📌", self.toggle_topmost,
            tip=lambda: "取消置頂" if self.topmost else "置頂：永遠顯示在其他視窗上面")
        self.btn_pin.bind("<Leave>", lambda e: self._paint_pin(), add="+")
        self.btn_pin.pack(side="left")
        self._paint_pin()
        self.btn_min = self._icon_button(
            tools, "—", self.minimize,
            tip=lambda: "縮小到工作列" if self.show_in_taskbar else "隱藏到背景（F8 叫回）")
        btn_close = self._icon_button(tools, "✕", self.close, tip="關閉")
        if self.borderless:
            self.btn_min.pack(side="left")
            btn_close.pack(side="left")
        self.lbl_date = tk.Label(top, font=TITLE_FONT, bg=BG, anchor="w")
        # 標題往下留白，帶動下方月曆與清單整體下移；右上角按鈕位置不變
        self.lbl_date.pack(side="left", fill="x", expand=True, pady=(px(26), 0))
        # 頂部列（含日期文字）當拖曳區
        for widget in (top, self.lbl_date):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        # 主畫面（月曆＋清單＋輸入列）；點任務時整塊換成詳細頁，比照手機 App
        self.main_view = tk.Frame(self, bg=BG)
        self.detail_view = tk.Frame(self, bg=BG)
        nav = tk.Frame(self.main_view, bg=BG)
        nav.pack(fill="x", padx=px(12), pady=(px(18), 0))  # 和標題拉開距離
        self._icon_button(nav, "◀", lambda: self.shift_month(-1),
                          tip=lambda: "上一週" if self.compact else "上個月").pack(side="left")
        self.btn_compact = self._icon_button(
            nav, "", self.toggle_compact, font=FONT,  # 與「今天」同字級
            tip=lambda: "展開整個月" if self.compact else "收合月曆：只顯示這一週")
        self.btn_compact.pack(side="right")
        self._icon_button(nav, "今天", lambda: self.select(dt.date.today()), font=FONT,
                          tip="回到今天（Home）").pack(side="right")
        self._icon_button(nav, "▶", lambda: self.shift_month(1),
                          tip=lambda: "下一週" if self.compact else "下個月").pack(side="right")
        self.lbl_month = tk.Label(nav, font=FONT, bg=BG)
        self.lbl_month.pack(side="left", expand=True)

        self.cal = tk.Canvas(self.main_view, width=7 * CELL_W, height=HEAD_H + 6 * CELL_H,
                             bg=BG, highlightthickness=0)
        self.cal.pack(pady=px(4))
        self.cal.bind("<Button-1>", self._on_cal_click)

        # 底部先 pack，視窗縮小時才不會被任務清單擠掉
        status_row = tk.Frame(self, bg=BG)
        status_row.pack(side="bottom", fill="x", padx=(px(12), 0))
        grip = tk.Label(status_row, text="◢", bg=BG, fg=BORDER, font=SMALL, cursor="size_nw_se")
        Tooltip(grip, "拖拉調整視窗大小")
        grip.pack(side="right", anchor="se")
        grip.bind("<Button-1>", self._start_resize)
        grip.bind("<B1-Motion>", self._on_resize)
        self.btn_undo = tk.Label(status_row, text="復原", bg=BG, fg=ACCENT, cursor="hand2",
                                 font=(SMALL[0], SMALL[1], "bold underline"))
        self.btn_undo.bind("<Button-1>", lambda e: self.undo_complete())
        self.status = tk.Label(status_row, bg=BG, fg=GRAY, font=SMALL, anchor="w", justify="left")
        self.status.pack(side="left", fill="x", expand=True, pady=(0, px(6)))
        # 狀態文字依視窗寬度換行
        status_row.bind("<Configure>", lambda e: self.status.config(wraplength=max(e.width - px(30), px(100))))
        bottom = tk.Frame(self.main_view, bg=CARD_BG, padx=px(8), pady=px(6),
                          highlightthickness=1, highlightbackground=BORDER)
        bottom.pack(side="bottom", fill="x", padx=px(12), pady=px(4))
        tk.Label(bottom, text="＋", bg=CARD_BG, fg=ACCENT, font=FONT).pack(side="left")
        self.entry = tk.Entry(bottom, font=FONT, relief="flat", bg=CARD_BG, fg=TEXT,
                              insertbackground=TEXT, highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, padx=px(4))
        self.entry.bind("<Return>", lambda e: self.add_task())
        self.entry.bind("<Button-1>", lambda e: self.entry.focus_force())
        self.entry.bind("<FocusIn>", lambda e: self._hide_placeholder())
        self.entry.bind("<FocusOut>", lambda e: self._show_placeholder())
        self.entry.bind("<Escape>", lambda e: self.cal.focus_set())
        self.placeholder_on = False
        self._show_placeholder()
        self.cmb_list = ttk.Combobox(bottom, state="disabled", width=10,  # 讀到清單後才啟用
                                     postcommand=self._raise_list_popdown)
        self.cmb_list.pack(side="right")
        self.cmb_list.bind("<<ComboboxSelected>>", lambda e: self._on_list_selected())
        Tooltip(self.cmb_list, "新增的工作要放進哪個清單")
        self.btn_new_due = tk.Label(bottom, bg=CARD_BG, fg=ACCENT, font=SMALL, cursor="hand2", padx=px(4))
        self.btn_new_due.pack(side="right", padx=(0, px(4)))
        self.btn_new_time = tk.Label(bottom, bg=CARD_BG, fg=ACCENT, font=SMALL, cursor="hand2", padx=px(2))
        self.btn_new_time.pack(side="right")
        self.btn_new_time.bind("<Button-1>", lambda e: TimePicker(
            self, self.btn_new_time, self.new_time, self._set_new_time))
        Tooltip(self.btn_new_time, "提醒時間（選填）：時間到會在右下角跳出提醒")
        # 右側按鈕都放好後再放輸入框：空間不足時縮的是輸入框，而不是按鈕
        self.entry.pack_forget()
        self.entry.pack(side="left", fill="x", expand=True, padx=px(4))
        self.btn_new_due.bind("<Button-1>", lambda e: DatePicker(
            self, self.btn_new_due, self.new_due, self._set_new_due))
        Tooltip(self.btn_new_due, "新增的工作要排在哪一天（可選「無日期」）")

        # 可捲動的任務清單
        wrap = tk.Frame(self.main_view, bg=BG)
        wrap.pack(fill="both", expand=True, padx=px(12))
        self.list_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(wrap, orient="vertical", command=self.list_canvas.yview)
        self.list_frame = tk.Frame(self.list_canvas, bg=BG)
        window = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda e: self.list_canvas.configure(
            scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda e: self.list_canvas.itemconfigure(window, width=e.width))
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        self.list_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>", lambda e: self.list_canvas.yview_scroll(-e.delta // 120, "units"))
        self.main_view.pack(fill="both", expand=True)  # 最後 pack，狀態列才不會被擠掉

    def redraw(self):
        self.today = dt.date.today()
        if self.detail:  # 詳細頁開啟中：只更新子工作區，不動編輯中的欄位
            return
        d = self.selected
        self.lbl_date.config(text=f"{d.month}月{d.day}日 星期{WEEKDAY_NAME[d.weekday()]}")
        self.btn_compact.config(text="展開" if self.compact else "收合")
        self.btn_new_due.config(text=f"📅 {date_text(self.new_due, short=True)} ▾")
        self.btn_new_time.config(text=f"⏰ {time_text(self.new_time)}" if self.new_time else "⏰")
        if self.placeholder_on:  # 提示文字跟著選中的日期變動
            self.entry.delete(0, "end")
            self.entry.insert(0, self._placeholder_text())
        self._draw_calendar()
        self._draw_list()

    def _draw_calendar(self):
        c = self.cal
        c.delete("all")
        year, month = self.view
        self.lbl_month.config(text=f"{year} 年 {month} 月")
        for i, name in enumerate(WEEK_HEAD):
            weekend = name in "日六"
            c.create_text(i * CELL_W + CELL_W / 2, HEAD_H / 2, text=name,
                          fill=HOLIDAY_RED if weekend else GRAY, font=SMALL)

        # 子任務跟著父任務，月曆圓點只看最上層任務的日期
        task_days = {t["due"] for t in self._top_level() if t["due"]}
        self.weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
        if self.compact:
            self.weeks = [w for w in self.weeks if self.selected in w] or self.weeks[:1]
        c.config(height=HEAD_H + len(self.weeks) * CELL_H)  # 依當月週數調整高度，不留空白列
        for row, week in enumerate(self.weeks):
            for col, day in enumerate(week):
                x, y = col * CELL_W, HEAD_H + row * CELL_H
                cx, cy = x + CELL_W / 2, y + px(17)
                if day == self.selected:
                    # 選取框、今天的圓圈、日期數字共用同一個中心點 cy，圓圈才會在框內置中
                    c.create_rectangle(x + px(2), cy - px(16), x + CELL_W - px(2), cy + px(16),
                                       fill=SELECTED, outline="")
                if day == self.today:
                    d = px(22)
                    img = round_image(c, d, ACCENT, SELECTED if day == self.selected else BG)
                    c.create_image(round(cx - d / 2), round(cy - d / 2), image=img, anchor="nw")
                    color = "white"
                else:
                    off = day.weekday() >= 5 or self._is_day_off(day)
                    if day.month == month:
                        color = HOLIDAY_RED if off else TEXT
                    else:
                        color = HOLIDAY_RED_FADED if off else "#c8c6c4"
                c.create_text(cx, cy, text=str(day.day), fill=color, font=FONT)
                names = self.holidays.get(day)
                if names:  # 右上角三角形：紅＝放假的節日，橘＝一般節日
                    mark = HOLIDAY_RED if self._is_day_off(day) else FESTIVAL
                    if day.month != month:
                        mark = HOLIDAY_RED_FADED
                    rx, ty = x + CELL_W - px(4), y + px(2)
                    c.create_polygon(rx - px(8), ty, rx, ty, rx, ty + px(8), fill=mark, outline="")
                if day in task_days or day in self.events:
                    # 紅＝有逾期未完成待辦；藍＝有待辦；灰＝只有日曆行程
                    if day in task_days:
                        dot = RED if day < self.today else ACCENT
                    else:
                        dot = EVENT_DOT
                    d, left, top = px(4), round(cx - px(2)), round(cy + px(13))
                    if day == self.selected:  # 圓點下緣超出選取框，超出的部分底色是白的
                        img = round_image(c, d, dot, SELECTED, bg_below=(round(cy + px(16)) - top, BG))
                    else:
                        img = round_image(c, d, dot, BG)
                    c.create_image(left, top, image=img, anchor="nw")

    def _is_day_off(self, day):
        return any(off for _, off in self.holidays.get(day, []))

    def _raise_list_popdown(self):
        """視窗置頂時，下拉選單也要置頂，否則會被主視窗蓋住。"""
        popdown = self.tk.call("ttk::combobox::PopdownWindow", self.cmb_list)
        self.tk.call("wm", "attributes", popdown, "-topmost", 1)

    def _draw_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        if not self.g.refresh_token:
            self._draw_login_prompt()
            return
        day = self.selected
        events = self.events.get(day, [])
        top_level = self._top_level()
        # 有時間的排前面並依時間排序，其餘依 Google Tasks 的順序
        tasks = sorted((t for t in top_level if t["due"] == day),
                       key=lambda t: (t.get("time") is None, t.get("time") or dt.time(), self._order_key(t)))
        if day == self.today:
            # 比照 To Do「我的一天」：今天的清單最上方集中顯示所有逾期待辦
            overdue = sorted((t for t in top_level if t["due"] and t["due"] < day),
                             key=lambda t: (t["due"], self._order_key(t)))
            if overdue:
                self._section_header(f"逾期（{len(overdue)}）", self.show_overdue,
                                     self._toggle_overdue, fg=RED)
                if self.show_overdue:
                    for task in overdue:
                        self._task_with_children(task)
        # 當天標題放在逾期區之後，緊貼當天的項目
        label = "今天" if day == self.today else f"{day.month}月{day.day}日"
        tk.Label(self.list_frame, text=f"{label} · {len(events) + len(tasks)} 項",
                 bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x", pady=(8, 2))
        for name, day_off in self.holidays.get(day, []):
            tk.Label(self.list_frame, text=f"◆ {name}" + ("（放假）" if day_off else ""),
                     bg=BG, fg=HOLIDAY_RED if day_off else GRAY, font=FONT, anchor="w").pack(fill="x", pady=(0, 2))
        for when, title in events:
            self._card(title, "Google 日曆", None, event_time=when)
        for task in tasks:
            self._task_with_children(task)
        if not events and not tasks:
            tk.Label(self.list_frame, text="這天沒有任務", bg=BG, fg=GRAY, font=FONT).pack(pady=8)

        undated = sorted((t for t in top_level if t["due"] is None), key=self._order_key)
        if undated:
            self._section_header(f"未排日期（{len(undated)}）", self.show_undated, self._toggle_undated)
            if self.show_undated:
                for task in undated:
                    self._task_with_children(task)
        self.list_canvas.yview_moveto(0)

    def _draw_login_prompt(self):
        """未登入時，清單區改顯示明確的登入提示（取代「這天沒有任務」）。"""
        box = tk.Frame(self.list_frame, bg=BG)
        box.pack(fill="x", pady=px(30))
        tk.Label(box, text="尚未登入 Google", bg=BG, fg=TEXT, font=("Microsoft JhengHei UI", 12, "bold")).pack()
        tk.Label(box, text="登入後才能讀取與新增工作", bg=BG, fg=GRAY, font=SMALL).pack(pady=(px(4), px(12)))
        btn = tk.Label(box, text="登入 Google", bg=ACCENT, fg="white", font=FONT, padx=px(18), pady=px(5),
                       cursor="hand2")
        btn.pack()
        btn.bind("<Button-1>", lambda e: self.login())

    def _section_header(self, text, expanded, toggle, fg=ACCENT):
        """可收合區塊的標題列（逾期、未排日期）。"""
        header = tk.Label(self.list_frame, text=f"{'▼' if expanded else '▶'} {text}",
                          bg=BG, fg=fg, font=FONT, cursor="hand2", anchor="w")
        header.pack(fill="x", pady=(10, 2))
        header.bind("<Button-1>", lambda e: toggle())

    # ---- 任務階層（比照 Google Tasks：子任務縮排在父任務下方）

    def _top_level(self):
        """最上層任務：沒有 parent，或 parent 不在未完成清單中（父任務已完成時，子任務照常顯示）。"""
        ids = {t["id"] for t in self.tasks}
        return [t for t in self.tasks if not t.get("parent") or t["parent"] not in ids]

    def _children(self, task):
        return sorted((t for t in self.tasks if t.get("parent") == task["id"]),
                      key=lambda t: t.get("position", ""))

    def _order_key(self, task):
        """先依清單順序、再依 Google Tasks 的 position 排序，和手機 App 一致。"""
        list_ids = [lst["id"] for lst in self.lists]
        index = list_ids.index(task["list_id"]) if task["list_id"] in list_ids else len(list_ids)
        return index, task.get("position", "")

    def _task_with_children(self, task):
        overdue = task["due"] is not None and task["due"] < self.today
        self._card(task["title"], task_subtitle(task, self.today), task, subtitle_fg=RED if overdue else GRAY)
        for child in self._children(task):
            # 子任務不顯示清單名稱（和父任務相同），比照 Google Tasks 精簡顯示；有時間才顯示
            child_at = time_text(child.get("time"))
            self._card(child["title"], f"⏰ {child_at}" if child_at else "", child, indent=True)

    def _card(self, title, subtitle, task, indent=False, subtitle_fg=GRAY, event_time=None):
        """清單中的一列（扁平樣式，比照 Google Tasks：無外框，列與列之間以細線分隔）。

        task 為 None 時是日曆行程：左側灰色色條＋粗體時間，只顯示、不可勾選。indent＝子工作縮排。
        """
        row = tk.Canvas(self.list_frame, bg=BG, highlightthickness=0, height=px(44))
        row.pack(fill="x", padx=(CHILD_INDENT if indent else 0, 0))
        inner = tk.Frame(row, bg=BG)
        if task:
            circle = self._check_circle(inner, task, bg=BG)
            circle.pack(side="left", padx=(0, px(10)))
            count = len(self._children(task))
            Tooltip(circle, f"標記為完成（含 {count} 個子工作）" if count else "標記為完成")
        else:
            icon = tk.Label(inner, text=event_time or "", bg=BG, fg=GRAY, font=EVENT_TIME_FONT,
                            width=5, anchor="w")
            icon.pack(side="left", padx=(px(4), px(6)))
            Tooltip(icon, "Google 日曆行程（唯讀，請到日曆 App 修改）")
        text = tk.Frame(inner, bg=BG)
        text.pack(side="left", fill="x", expand=True)
        lbl_title = tk.Label(text, text=title, bg=BG, fg=TEXT, font=FONT, anchor="w", justify="left")
        lbl_title.pack(fill="x")
        preview = _notes_preview(task.get("notes", "")) if task else ""
        if preview:  # 比照 Google Tasks：標題下方顯示詳細資訊第一行
            tk.Label(text, text=preview, bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x")
        if subtitle:
            tk.Label(text, text=subtitle, bg=BG, fg=subtitle_fg, font=SMALL, anchor="w").pack(fill="x")
        window = row.create_window(px(4), px(6), window=inner, anchor="nw")
        if task:  # 點整列（圓圈以外）開啟詳細頁
            for widget in (row, inner, text, *text.winfo_children()):
                widget.bind("<Button-1>", lambda e: self.open_detail(task))
                widget.config(cursor="hand2")
        # 滑鼠移過整列時淡淡變色，提示可以點
        row.bind("<Enter>", lambda e: _set_bg(row, HOVER))
        # 滑鼠可能從列內的文字直接移出去，此時只有子元件收到 Leave，所以每個子元件都要檢查
        for widget in (row, *_descendants(row)):
            widget.bind("<Leave>", lambda e: self._leave_row(row, e), add="+")

        def relayout(_=None):
            width = row.winfo_width()
            if width < px(50):
                return
            lbl_title.config(wraplength=width - px(60))
            row.itemconfigure(window, width=width - px(8))
            inner.update_idletasks()
            height = inner.winfo_reqheight() + px(12)
            row.config(height=height)  # 高度不變時不會再觸發 <Configure>，不會無限迴圈
            row.delete("deco")
            row.create_line(0, height - 1, width, height - 1, fill=DIVIDER, tags="deco")
            if task is None:  # 行程左側色條，和可勾選的待辦區隔
                row.create_rectangle(0, px(8), px(3), height - px(8), fill=EVENT_DOT, outline="", tags="deco")
        row.bind("<Configure>", relayout)

    def _leave_row(self, row, event):
        """滑鼠離開整列才恢復底色（移到列內的子元件不算離開）。"""
        x, y = event.x_root - row.winfo_rootx(), event.y_root - row.winfo_rooty()
        if not (0 <= x < row.winfo_width() and 0 <= y < row.winfo_height()):
            _set_bg(row, BG)

    def _check_circle(self, parent, task, bg=CARD_BG):
        """To Do 風格的圓圈：滑鼠移上去顯示 ✓，點擊後填滿並標記完成。"""
        size, mid = px(22), px(22) / 2
        c = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, cursor="hand2")
        d = size - px(2)  # 外框外緣直徑，和原本 create_oval 畫出來的大小相同
        state = {"done": False}

        def ring_image(color):
            if state["done"]:
                return round_image(c, d, ACCENT, color)
            return round_image(c, d, color, color, outline=GRAY, width=px(2))
        c.create_image((size - d) // 2, (size - d) // 2, image=ring_image(bg), anchor="nw", tags="ring")
        c.on_bg = lambda color: c.itemconfigure("ring", image=ring_image(color))  # 清單列 hover 換底色時跟著換
        c.bind("<Enter>", lambda e: c.create_text(mid, mid, text="✓", fill=ACCENT, font=SMALL, tags="tick"))
        c.bind("<Leave>", lambda e: c.delete("tick"))

        def done(_):
            c.unbind("<Button-1>")  # 防止連點重複送出
            c.unbind("<Leave>")
            c.delete("tick")
            state["done"] = True
            c.itemconfigure("ring", image=ring_image(c.cget("bg")))
            c.create_text(mid, mid, text="✓", fill="white", font=SMALL)
            self.after(250, lambda: self.complete(task))  # 讓使用者看到勾選效果再移除
        c.bind("<Button-1>", done)
        return c

    def _placeholder_text(self):
        return "新增工作"

    def _set_new_due(self, day):
        self.new_due = day
        self.redraw()

    def _set_new_time(self, value):
        self.new_time = value
        if value and self.new_due is None:  # 提醒需要日期；沒有日期時預設今天
            self.new_due = dt.date.today()
        self.redraw()

    # ---- 詳細頁（比照 Google Tasks App：標題、詳細資訊、日期、子工作、刪除、標示為完成）

    def _find_task(self, task_id):
        return next((t for t in self.tasks if t["id"] == task_id), None)

    def open_detail(self, task):
        if self.detail:
            self.close_detail()  # 從子工作切換時，先儲存目前這筆
        task = self._find_task(task["id"]) or task
        self.detail = {"task": task, "due": task["due"], "time": task.get("time"), "recur": task.get("recur")}
        self._hide_undo()
        self.main_view.pack_forget()
        self._render_detail()
        self.detail_view.pack(fill="both", expand=True)
        self.lbl_date.config(text="工作詳細資訊")

    def _render_detail(self):
        view = self.detail_view
        for w in view.winfo_children():
            w.destroy()
        task = self.detail["task"]
        pad = {"padx": px(12)}

        bar = tk.Frame(view, bg=BG)
        bar.pack(fill="x", pady=(px(6), px(4)), **pad)
        self._icon_button(bar, "← 儲存並返回", self.close_detail, font=FONT, tip="儲存並返回（Esc）").pack(side="left")
        btn_delete = self._icon_button(bar, "刪除", self.delete_detail, font=FONT, tip="刪除這筆工作")
        btn_delete.config(fg=RED)
        btn_delete.bind("<Leave>", lambda e: btn_delete.config(fg=RED), add="+")
        btn_delete.pack(side="right")

        tk.Label(view, text=f"清單：{task['list_title']}", bg=BG, fg=GRAY, font=SMALL,
                 anchor="w").pack(fill="x", **pad)
        self.detail_title = tk.Entry(view, font=("Microsoft JhengHei UI", 14, "bold"), relief="flat",
                                     bg=CARD_BG, fg=TEXT, insertbackground=TEXT,
                                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        self.detail_title.insert(0, self.detail.get("title_draft", task["title"]))
        self.detail_title.pack(fill="x", pady=(px(4), px(8)), ipady=px(4), **pad)

        tk.Label(view, text="詳細資訊", bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x", **pad)
        self.detail_notes = tk.Text(view, height=5, wrap="word", font=FONT, relief="flat", bg=CARD_BG, fg=TEXT,
                                    insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
                                    highlightcolor=ACCENT, padx=px(6), pady=px(4))
        self.detail_notes.insert("1.0", self.detail.get("notes_draft", task.get("notes", "")))
        self.detail_notes.pack(fill="x", pady=(px(2), px(8)), **pad)

        row = tk.Frame(view, bg=BG)
        row.pack(fill="x", **pad)
        tk.Label(row, text="日期", bg=BG, fg=GRAY, font=SMALL).pack(side="left")
        btn_due = tk.Label(row, text=f"📅 {date_text(self.detail['due'])} ▾", bg=CARD_BG, fg=ACCENT, font=FONT,
                           cursor="hand2", padx=px(8), pady=px(2), highlightthickness=1, highlightbackground=BORDER)
        btn_due.pack(side="left", padx=px(8))
        btn_due.bind("<Button-1>", lambda e: DatePicker(self, btn_due, self.detail["due"], self._set_detail_due))
        at = time_text(self.detail["time"])
        btn_time = tk.Label(row, text=f"⏰ {at} ▾" if at else "⏰ 加提醒時間 ▾", bg=CARD_BG, fg=ACCENT, font=FONT,
                            cursor="hand2", padx=px(8), pady=px(2), highlightthickness=1, highlightbackground=BORDER)
        btn_time.pack(side="left")
        btn_time.bind("<Button-1>", lambda e: TimePicker(self, btn_time, self.detail["time"], self._set_detail_time))
        if not task.get("parent"):  # 子工作不支援週期
            row_recur = tk.Frame(view, bg=BG)
            row_recur.pack(fill="x", pady=(px(6), 0), **pad)
            tk.Label(row_recur, text="重複", bg=BG, fg=GRAY, font=SMALL).pack(side="left")
            rule = self.detail["recur"]
            btn_recur = tk.Label(row_recur, text=f"🔁 {format_recur(rule)} ▾" if rule else "🔁 不重複 ▾",
                                 bg=CARD_BG, fg=ACCENT, font=FONT, cursor="hand2", padx=px(8), pady=px(2),
                                 highlightthickness=1, highlightbackground=BORDER)
            btn_recur.pack(side="left", padx=px(8))
            btn_recur.bind("<Button-1>", lambda e: RecurPicker(
                self, btn_recur, self.detail["recur"], self.detail["due"] or dt.date.today(), self._set_detail_recur))
        if task.get("parent"):
            tk.Label(view, text="（這是子工作，在主畫面會顯示在父工作下方）", bg=BG, fg=GRAY, font=SMALL,
                     anchor="w").pack(fill="x", pady=(px(4), 0), **pad)
        else:
            self._render_subtasks(view, task, pad)

        foot = tk.Frame(view, bg=BG)
        foot.pack(side="bottom", fill="x", pady=px(8), **pad)
        done = tk.Label(foot, text="✓ 標示為完成", bg=ACCENT, fg="white", font=FONT, cursor="hand2",
                        padx=px(12), pady=px(4))
        done.pack(side="right")
        done.bind("<Button-1>", lambda e: self.complete_detail())
        save = tk.Label(foot, text="儲存", bg=CARD_BG, fg=ACCENT, font=FONT, cursor="hand2",
                        padx=px(10), pady=px(4), highlightthickness=1, highlightbackground=BORDER)
        save.pack(side="right", padx=(0, px(6)))
        save.bind("<Button-1>", lambda e: self.close_detail())
        Tooltip(save, "儲存修改並返回（Esc）")
        if not task.get("parent") and self.detail["recur"]:
            skip = tk.Label(foot, text="略過這一期", bg=CARD_BG, fg=ACCENT, font=FONT, cursor="hand2",
                            padx=px(10), pady=px(4), highlightthickness=1, highlightbackground=BORDER)
            skip.pack(side="right", padx=(0, px(6)))
            skip.bind("<Button-1>", lambda e: self.skip_detail())
            Tooltip(skip, "這次不做：建立下一期並刪除這一期，不留完成紀錄")
        for widget in (self.detail_title, self.detail_notes):
            widget.bind("<Escape>", lambda e: self.close_detail())

    def _render_subtasks(self, view, task, pad):
        tk.Label(view, text="子工作", bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x", pady=(px(10), 0), **pad)
        for child in self._children(task):
            row = tk.Frame(view, bg=BG)
            row.pack(fill="x", pady=px(1), **pad)
            circle = self._check_circle(row, child, bg=BG)
            circle.pack(side="left", padx=(0, px(8)))
            Tooltip(circle, "標記為完成")
            title = tk.Label(row, text=child["title"], bg=BG, fg=TEXT, font=FONT, anchor="w", cursor="hand2")
            title.pack(side="left", fill="x", expand=True)
            title.bind("<Button-1>", lambda e, c=child: self.open_detail(c))
        entry = tk.Entry(view, font=FONT, relief="flat", bg=CARD_BG, fg=GRAY, insertbackground=TEXT,
                         highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        entry.insert(0, "＋ 新增子工作（Enter）")
        entry.pack(fill="x", pady=(px(4), 0), ipady=px(3), **pad)

        def clear_hint(_):
            if entry.cget("fg") == GRAY:
                entry.delete(0, "end")
                entry.config(fg=TEXT)
        entry.bind("<FocusIn>", clear_hint)
        entry.bind("<Button-1>", lambda e: entry.focus_force())
        entry.bind("<Return>", lambda e: self.add_subtask(task, entry.get().strip()))
        entry.bind("<Escape>", lambda e: self.close_detail())

    def _rerender_detail(self):
        """重畫詳細頁前先暫存輸入中的標題與詳細資訊，避免未儲存的修改被清掉。"""
        self.detail["title_draft"] = self.detail_title.get()
        self.detail["notes_draft"] = self.detail_notes.get("1.0", "end-1c")
        self._render_detail()

    def _set_detail_due(self, day):
        self.detail["due"] = day
        if day is None:
            self.detail["time"] = None   # 沒有日期就不能提醒
            self.detail["recur"] = None  # 也不能重複
        self._rerender_detail()

    def _set_detail_recur(self, rule):
        self.detail["recur"] = rule
        if rule and self.detail["due"] is None:  # 重複需要起始日期；沒有日期時預設今天
            self.detail["due"] = dt.date.today()
        self._rerender_detail()

    def _set_detail_time(self, value):
        self.detail["time"] = value
        if value and self.detail["due"] is None:  # 提醒需要日期；沒有日期時預設今天
            self.detail["due"] = dt.date.today()
        self._rerender_detail()

    def _detail_changes(self):
        """比對詳細頁與原資料，回傳要送出的欄位。標題清空時保留原標題。"""
        task = self.detail["task"]
        fields = {}
        title = self.detail_title.get().strip()
        if title and title != task["title"]:
            fields["title"] = title
        notes = self.detail_notes.get("1.0", "end-1c")
        # 比對拆解後的值（而非原始字串），手機上 🔁／⏰ 順序不同但內容未改時不會送出 PATCH
        if (notes != task.get("notes", "") or self.detail["time"] != task.get("time")
                or self.detail.get("recur") != task.get("recur")):
            fields["notes"] = join_meta(self.detail["time"], self.detail.get("recur"), notes)
        if self.detail["due"] != task["due"]:
            due = self.detail["due"]
            fields["due"] = f"{due.isoformat()}T00:00:00.000Z" if due else None
        return fields

    def close_detail(self, save=True):
        """返回主畫面；有修改就自動儲存（比照手機 App，不需要按儲存）。save=False 時只關閉、不送出修改。"""
        if not self.detail:
            return
        task = self.detail["task"]
        fields = self._detail_changes() if save else {}
        self.detail = None
        self.detail_view.pack_forget()
        self.main_view.pack(fill="both", expand=True)
        if fields:
            before = dict(task)
            apply_fields(task, fields)
            self._set_status("儲存中…")

            def done(_, err):
                if err:
                    task.update(before)  # 還原畫面，下次同步以 Google 上的資料為準
                    self.redraw()
                    self._show_error(err)
                else:
                    self._set_status(f"已儲存「{task['title']}」")
            self.run_bg(lambda: self.g.update_task(task["list_id"], task["id"], fields), done)
        self.redraw()

    def complete_detail(self):
        task = self.detail["task"]
        self.close_detail()
        self.complete(task)

    def skip_detail(self):
        """詳細頁的「略過這一期」：修改只套用在下一期，不對即將刪除的這一期送出 PATCH（避免與刪除競態）。"""
        task = self.detail["task"]
        apply_fields(task, self._detail_changes())
        self.close_detail(save=False)
        self.skip(task)

    def delete_detail(self):
        task = self.detail["task"]
        children = [] if task.get("parent") else self._children(task)
        if not messagebox.askyesno("刪除工作", delete_prompt(task, len(children)), parent=self, icon="warning"):
            return
        self.detail = None
        self.detail_view.pack_forget()
        self.main_view.pack(fill="both", expand=True)
        group = children + [task]  # 先刪子工作再刪父工作，不依賴 API 是否連帶刪除
        for t in group:
            if t in self.tasks:
                self.tasks.remove(t)
        self.redraw()

        def work():
            for t in group:
                self.g.delete_task(t["list_id"], t["id"])

        def done(_, err):
            if err:
                self._show_error(err)
                self.refresh()  # 可能只刪了一部分，重新同步以 Google 上的實際狀態為準
            else:
                self._set_status(f"已刪除「{task['title']}」")
        self.run_bg(work, done)

    def add_subtask(self, parent, title):
        if not title or title.startswith("＋"):
            return
        lst = {"id": parent["list_id"], "title": parent["list_title"]}

        def done(created, err):
            if err:
                self._show_error(err)
                return
            self.tasks.append({"id": created["id"], "title": created.get("title") or title,
                               "list_id": lst["id"], "list_title": lst["title"], "due": task_due(created),
                               "parent": parent["id"], "notes": "", "time": None, "recur": None,
                               "position": created.get("position", "")})
            self._set_status(f"已新增子工作「{title}」")
            if self.detail and self.detail["task"]["id"] == parent["id"]:
                self._rerender_detail()
            self.redraw()
        self.run_bg(lambda: self.g.add_task(lst["id"], title, None, parent=parent["id"]), done)

    def _show_placeholder(self):
        if not self.entry.get():
            self.placeholder_on = True
            self.entry.config(fg=GRAY)
            self.entry.insert(0, self._placeholder_text())

    def _hide_placeholder(self):
        if self.placeholder_on:
            self.placeholder_on = False
            self.entry.delete(0, "end")
            self.entry.config(fg=TEXT)

    def _set_status(self, text, error=False):
        self._hide_undo()
        self.status.config(text=text, fg=RED if error else GRAY)

    def _show_error(self, err):
        if isinstance(err, NeedLogin):
            self._refresh_list_values()  # 下拉選單顯示「請先登入」
            self._draw_list()            # 清單區顯示登入提示
            self._show_login_button(True)
            self._set_status(str(err), error=True)
        elif isinstance(err, ApiError):
            self._set_status(str(err), error=True)
        else:
            self._set_status(f"未預期的錯誤：{type(err).__name__}: {err}", error=True)

    # ---- 操作

    def select(self, day):
        self.selected = day
        self.new_due = day
        if (day.year, day.month) != self.view:
            self.view = (day.year, day.month)
            self.refresh()  # 日曆事件只抓顯示中的月份，換月要重新讀取
        self.redraw()

    def shift_month(self, delta):
        if self.compact:  # 收合時只顯示一週，◀ ▶ 改為切換週
            self.select(self.selected + dt.timedelta(days=7 * delta))
            return
        year, month = self.view
        index = year * 12 + (month - 1) + delta
        self.view = (index // 12, index % 12 + 1)
        self.refresh()
        self.redraw()

    def _on_cal_click(self, event):
        row, col = (event.y - HEAD_H) // CELL_H, event.x // CELL_W
        if event.y >= HEAD_H and 0 <= row < len(self.weeks) and 0 <= col < 7:
            self.select(self.weeks[row][col])

    def _toggle_undated(self):
        self.show_undated = not self.show_undated
        self._draw_list()

    def _toggle_overdue(self):
        self.show_overdue = not self.show_overdue
        self._draw_list()

    def toggle_compact(self):
        self.compact = not self.compact
        self.state_data["compact_calendar"] = self.compact
        self.redraw()

    def _refresh_list_values(self):
        """下拉選單：所有清單＋最後一項「新增清單」；尚未讀到清單（未登入）時停用。"""
        logged_in = bool(self.g.refresh_token)
        self.cmb_list["values"] = [lst.get("title", "") for lst in self.lists] + [ADD_LIST_OPTION]
        self.cmb_list.config(state="readonly" if logged_in and self.lists else "disabled")
        if not logged_in:
            self.cmb_list.set("請先登入")

    def _on_list_selected(self):
        self.cmb_list.selection_clear()  # 選完不要留著藍色反白
        if self.cmb_list.current() == len(self.lists):
            self._prompt_new_list()
        else:
            self._remember_list()

    def _select_remembered_list(self):
        list_ids = [lst["id"] for lst in self.lists]
        last = self.state_data.get("last_list_id")
        self.cmb_list.current(list_ids.index(last) if last in list_ids else 0)

    def _prompt_new_list(self):
        """選了「新增清單」：先回到原本的清單，再詢問名稱並建立。"""
        self._select_remembered_list()
        name = ask_text(self, "新增清單", "清單名稱：")
        name = (name or "").strip()
        if not name:
            return
        self._set_status(f"建立清單「{name}」中…")

        def done(created, err):
            if err:
                self._show_error(err)
                return
            self.lists.append({"id": created["id"], "title": created.get("title") or name})
            self._refresh_list_values()
            self.cmb_list.current(len(self.lists) - 1)
            self._remember_list()
            self._set_status(f"已建立清單「{name}」，新增的工作會放進這個清單")
        self.run_bg(lambda: self.g.add_tasklist(name), done)

    def _remember_list(self):
        index = self.cmb_list.current()
        if 0 <= index < len(self.lists):
            self.state_data["last_list_id"] = self.lists[index]["id"]

    # ---- 電腦端提醒（方案 B：時間存在詳細資訊第一行）

    def _check_reminders(self):
        """時間到的工作在右下角跳出提醒；同一筆只提醒一次（含 DayNote 重開後）。"""
        now = dt.datetime.now()
        changed = False
        for task in list(self.tasks):
            if not (task.get("time") and task["due"]):
                continue
            when = dt.datetime.combine(task["due"], task["time"])
            key = reminder_key(task["id"], when)
            if key in self.reminded:
                continue
            fire_at = self.snoozed.get(key, when)
            if now < fire_at:
                continue
            self.reminded.add(key)
            self.snoozed.pop(key, None)
            changed = True
            if now - when > dt.timedelta(hours=REMINDER_MAX_LATE_H) and fire_at == when:
                continue  # 錯過太久（例如關機好幾天）就不補跳
            ReminderToast(self, task, when,
                          on_done=lambda t=task: self.complete(t),
                          on_snooze=lambda k=key: self._snooze(k))
        if changed:
            self._save_reminder_state()
        self.after(REMINDER_CHECK_MS, self._check_reminders)

    def _snooze(self, key):
        self.reminded.discard(key)
        self.snoozed[key] = dt.datetime.now() + dt.timedelta(minutes=REMINDER_SNOOZE_MIN)
        self._save_reminder_state()
        self._set_status(f"{REMINDER_SNOOZE_MIN} 分鐘後再提醒")

    def _save_reminder_state(self):
        """已提醒記錄寫進 daynote_state.json，重開不會重複提醒；只保留最近 300 筆。"""
        self.state_data["reminded"] = sorted(self.reminded, key=lambda k: k.split("@")[-1])[-300:]
        self.state_data["snoozed"] = {k: v.isoformat() for k, v in self.snoozed.items()}
        save_state(self.state_data)

    def _watch_pid(self):
        """單一執行個體：PID 檔被移除或換成別的 PID（新開的 DayNote），就正常關閉並存好狀態。"""
        try:
            with open(PID_PATH, encoding="ascii") as f:
                still_mine = f.read().strip() == str(os.getpid())
        except OSError:
            still_mine = False
        if still_mine:
            self.after(1000, self._watch_pid)
        else:
            self.close(ask=False)

    def _bind_shortcuts(self):
        """Ctrl+N 新增、←→ 前後一天、↑↓ 前後一週、Home 今天、F5 同步、Esc 離開輸入框。"""
        self.bind("<Control-n>", lambda e: self.detail or self.entry.focus_force())
        self.bind("<Control-N>", lambda e: self.detail or self.entry.focus_force())
        self.bind("<F5>", lambda e: self.refresh())
        for key, days in (("<Left>", -1), ("<Right>", 1), ("<Up>", -7), ("<Down>", 7)):
            self.bind(key, lambda e, d=days: self._shift_day(d))
        self.bind("<Home>", lambda e: self._shift_day(None))
        # 點月曆或清單時取得鍵盤焦點，方向鍵才有作用
        for widget in (self.cal, self.list_canvas):
            widget.bind("<Button-1>", lambda e, w=widget: w.focus_set(), add="+")

    def _shift_day(self, days):
        if self.detail or isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return  # 詳細頁或輸入框中，方向鍵交給輸入框移動游標
        self.select(dt.date.today() if days is None else self.selected + dt.timedelta(days=days))

    def _on_focus(self, event):
        if (event.widget is self and self.g.refresh_token
                and time.time() - self.last_refresh > FOCUS_REFRESH_SEC):
            self.refresh()

    def login(self):
        if self.busy:
            return
        self.busy = True
        self._set_status("正在開啟瀏覽器…")
        # 網址經 queue 交回主執行緒處理（tkinter 只能在主執行緒操作）
        on_url = lambda url: self.q.put((self._on_login_url, url, None))
        self.run_bg(lambda: self.g.login(on_url), self._after_login)

    def _on_login_url(self, url, _):
        self.clipboard_clear()
        self.clipboard_append(url)
        self._set_status("請在瀏覽器完成 Google 登入（5 分鐘內）。\n"
                         "若瀏覽器沒有開啟：登入網址已複製到剪貼簿，請貼到瀏覽器網址列。")

    def _after_login(self, _, err):
        self.busy = False
        if err:
            self._show_error(err)
            return
        self._show_login_button(False)
        self.refresh()

    def _show_login_button(self, show):
        """未登入顯示「登入」，已登入顯示「登出」，兩者在同一個位置切換。"""
        if show:
            self.btn_logout.pack_forget()
            self.btn_login.pack(side="left", padx=(0, 4), before=self.btn_refresh)
        else:
            self.btn_login.pack_forget()
            self.btn_logout.pack(side="left", before=self.btn_refresh)

    def logout(self):
        if self.busy or not self.g.refresh_token:
            return
        if not messagebox.askyesno("登出", "確定要登出 Google？\n\n會撤銷 DayNote 的存取權，並刪除這台電腦上的登入資料，"
                                   "之後需要重新登入。", parent=self):
            return
        self.close_detail()  # 詳細頁有修改時先儲存
        self.busy = True
        self._set_status("正在登出…")
        self.run_bg(self.g.revoke, self._after_logout)

    def _after_logout(self, revoked, err):
        self.busy = False
        self.lists, self.tasks, self.events = [], [], {}
        self._refresh_list_values()
        self.redraw()
        self._show_login_button(True)
        if revoked:
            self._set_status("已登出")
        else:
            self._set_status("已刪除本機登入資料，但無法連線 Google 撤銷授權；\n"
                             "請到 myaccount.google.com/connections 移除 DayNote", error=True)

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.last_refresh = time.time()
        self._set_status("同步中…")
        view = self.view
        self.run_bg(lambda: self._load(view), self._after_load)

    def _load(self, view):
        """（背景執行緒）讀取清單、所有未完成任務，以及顯示月份前後的日曆事件。

        同時補建手機上完成的週期任務；回傳的最後一項為下次補建檢查的時間點。
        """
        now = self._now()
        lists = self.g.tasklists()
        tasks = []
        for lst in lists:
            for t in self.g.open_tasks(lst["id"]):
                if t.get("status") == "completed":
                    continue
                tasks.append({"id": t["id"], "title": t.get("title") or "（無標題）",
                              "list_id": lst["id"], "list_title": lst.get("title", ""),
                              "due": task_due(t), "parent": t.get("parent"),
                              **dict(zip(("time", "recur", "notes"), split_meta(t.get("notes", "")))),
                              "position": t.get("position", "")})
        year, month = view
        first = dt.date(year, month, 1)
        next_month = dt.date(year + month // 12, month % 12 + 1, 1)
        events = {}
        # 月曆會顯示前後月的幾天，查詢範圍前後放寬
        start, end = first - dt.timedelta(days=7), next_month + dt.timedelta(days=14)
        for ev in self.g.events(start, end):
            for day, when in event_days(ev):
                events.setdefault(day, []).append((when, ev.get("summary") or "（無標題）"))
        mark = now
        if self.backfill_since is not None:  # 第一次同步只記錄時間點，不回溯歷史
            spawned, ok = backfill_recurring(self.g, lists, self.backfill_since, now.date())
            tasks.extend(spawned)
            mark = now if ok else self.backfill_since  # 有失敗就不前進，下次重試（防重複確保不會多建）
        return view, lists, tasks, events, self._load_holidays(start, end), mark

    def _load_holidays(self, start, end):
        """（背景執行緒）讀取節慶假日。失敗只是不顯示節日，不影響任務同步。

        Google 假日日曆的 description 以「國定假日」開頭代表放假，「假日節慶」為一般節日（實測）。
        """
        holidays = {}
        if not self.holiday_calendar:
            return holidays
        try:
            items = self.g.events(start, end, self.holiday_calendar)
        except ApiError:
            return holidays
        for ev in items:
            day_off = (ev.get("description") or "").startswith("國定假日")
            for day, _ in event_days(ev):
                holidays.setdefault(day, []).append((ev.get("summary") or "", day_off))
        return holidays

    def _after_load(self, result, err):
        self.busy = False
        if err:
            self._show_error(err)
            return
        view, self.lists, self.tasks, self.events, self.holidays, mark = result
        self.backfill_since = mark
        self.state_data["backfill_since"] = mark.isoformat()
        save_state(self.state_data)
        backfilled = [t for t in self.tasks if t.pop("backfilled", False)]
        if any([mark_past_reminder(self.reminded, t, self._now()) for t in backfilled]):
            self._save_reminder_state()
        self._show_login_button(False)
        self._refresh_list_values()
        list_ids = [lst["id"] for lst in self.lists]
        last = self.state_data.get("last_list_id")
        if last in list_ids:
            self.cmb_list.current(list_ids.index(last))
        elif self.lists and not 0 <= self.cmb_list.current() < len(self.lists):
            self.cmb_list.current(0)
        self._set_status(f"已同步 {dt.datetime.now():%H:%M}")
        self.redraw()
        if view != self.view:  # 讀取期間使用者切換了月份
            self.refresh()

    def complete(self, task):
        """標記完成；比照 Google Tasks App，完成父任務時子任務一併完成。

        API 是否會自動連帶完成子任務，官方文件未說明，所以這裡逐一送出，不依賴該行為。
        """
        if task not in self.tasks:
            return
        group = [task] + self._children(task)
        for t in group:  # 先從畫面移除，失敗再放回
            self.tasks.remove(t)
        self.redraw()  # 也會更新 self.today，之後才計算下一期
        plan = next_task_for(task, self.today)

        def work():
            for t in group:
                self.g.complete_task(t["list_id"], t["id"])
            if not plan:
                return None, None
            try:
                return spawn_next(self.g, task, plan), None
            except (ApiError, NeedLogin) as e:  # 完成已成功，下一期失敗不回滾
                return None, e

        def done(result, err):
            if err:
                # 可能已部分完成；先放回畫面並顯示錯誤，下次同步會以 Google 上的實際狀態為準
                self.tasks.extend(group)
                self.redraw()
                self._show_error(err)
                return
            spawned, spawn_err = result
            self._add_spawned(spawned)
            warning = f"已完成「{task['title']}」，但下一期建立失敗：{spawn_err}" if spawn_err else None
            self._offer_undo(group, [spawned] if spawned else [], warning)
        self.run_bg(work, done)

    def _add_spawned(self, spawned):
        """把新建的下一期放進畫面；同步可能已先讀到同一筆，依 id 避免重複。提醒時間已過就不再跳。"""
        if not spawned:
            return
        if spawned["id"] not in {t["id"] for t in self.tasks}:
            self.tasks.append(spawned)
        if mark_past_reminder(self.reminded, spawned, self._now()):
            self._save_reminder_state()
        self.redraw()

    def _now(self):
        return dt.datetime.now()

    def skip(self, task):
        """略過這一期：先建立下一期、再刪除這一期，不留完成紀錄。失敗時寧可多一筆，不讓週期中斷。"""
        if not can_skip(task) or task not in self.tasks:
            return
        children = self._children(task)
        if children and not messagebox.askyesno(
                "略過這一期", f"略過「{task['title']}」這一期？\n（含 {len(children)} 個子工作，會一併刪除）",
                parent=self, icon="question"):
            return
        group = children + [task]  # 先刪子工作再刪父工作
        for t in group:
            self.tasks.remove(t)
        self.redraw()
        plan = next_task_for(task, self.today)

        def work():
            spawned = spawn_next(self.g, task, plan)  # 失敗就直接丟出，這一期不刪
            try:
                for t in group:
                    try:
                        self.g.delete_task(t["list_id"], t["id"])
                    except NotFound:
                        pass  # 已在其他裝置刪除（例如另一台已略過）
            except (ApiError, NeedLogin) as e:
                return spawned, e
            return spawned, None

        def done(result, err):
            if err:
                self.tasks.extend(group)
                self.redraw()
                self._show_error(err)
                return
            spawned, delete_err = result
            self._add_spawned(spawned)
            if delete_err:
                self.tasks.extend(t for t in group if t not in self.tasks)
                self.redraw()
                self._set_status(f"已建立下一期，但這一期刪除失敗，請手動刪除「{task['title']}」", error=True)
                return
            skipped = f" {task['due'].month}/{task['due'].day}" if task["due"] else ""
            self._set_status(f"已略過{skipped}，下一期 {plan['due'].month}/{plan['due'].day}")
        self.run_bg(work, done)

    def _offer_undo(self, group, spawned=(), warning=None):
        """在狀態列顯示「已完成…　復原」，UNDO_SECONDS 秒後自動收起。

        spawned 為這次完成所建立的下一期（復原時一併刪除）；warning 有值時以紅字取代完成訊息，復原按鈕照常提供。
        """
        self._set_status(warning or f"已完成「{group[0]['title']}」", error=bool(warning))
        self.undo_group = group
        self.undo_spawned = list(spawned)
        self.btn_undo.pack(side="right", padx=px(4), before=self.status)
        self.undo_job = self.after(UNDO_SECONDS * 1000, self._hide_undo)

    def _hide_undo(self):
        if self.undo_job:
            self.after_cancel(self.undo_job)
            self.undo_job = None
        self.undo_group = None
        self.undo_spawned = None
        self.btn_undo.pack_forget()

    def undo_complete(self):
        group, spawned = self.undo_group, list(self.undo_spawned or [])
        if not group:
            return
        self._hide_undo()
        self.tasks.extend(group)
        for s in spawned:
            if s in self.tasks:
                self.tasks.remove(s)
        self._set_status(f"已復原「{group[0]['title']}」")
        self.redraw()

        def work():
            for t in group:
                self.g.uncomplete_task(t["list_id"], t["id"])
            try:
                for s in spawned:  # 刪除這次完成所建立的下一期
                    try:
                        self.g.delete_task(s["list_id"], s["id"])
                    except NotFound:
                        pass  # 下一期已被刪除，視為成功
            except (ApiError, NeedLogin) as e:
                return e
            return None

        def done(delete_err, err):
            if err:
                for t in group:
                    if t in self.tasks:
                        self.tasks.remove(t)
                self.redraw()
                self._show_error(err)
            elif delete_err:
                self._show_error(delete_err)  # 原任務已復原；下一期可能還在，下次同步以 Google 為準
        self.run_bg(work, done)

    def add_task(self):
        title = "" if self.placeholder_on else self.entry.get().strip()
        index = self.cmb_list.current()
        if not title:
            return
        if not 0 <= index < len(self.lists):
            msg = "請先登入 Google（右上角「登入」）" if not self.g.refresh_token else "尚未讀到任何清單，請先同步"
            self._set_status(msg, error=True)
            return
        lst = self.lists[index]
        self._remember_list()
        due, time_value = self.new_due, self.new_time
        if time_value and due is None:
            due = dt.date.today()
        self.entry.delete(0, "end")
        self._set_status("新增中…")

        def done(created, err):
            if err:
                self._hide_placeholder()
                self.entry.insert(0, title)
                self._show_error(err)
                return
            self.tasks.append({"id": created["id"], "title": created.get("title") or title,
                               "list_id": lst["id"], "list_title": lst.get("title", ""),
                               "due": task_due(created), "parent": None, "notes": "", "time": time_value,
                               "recur": None,
                               "position": created.get("position", "")})
            self.new_time = None  # 時間只套用在這一筆
            when = f"，{time_text(time_value)} 提醒" if time_value else ""
            self._set_status(f"已新增到「{lst.get('title', '')}」{when}")
            self.redraw()
        notes = join_meta(time_value, None, "")
        self.run_bg(lambda: self.g.add_task(lst["id"], title, due, notes=notes or None), done)

    # ---- 執行緒

    def run_bg(self, work, on_done):
        """在背景執行緒執行 work()，結果以 on_done(result, error) 交回主執行緒。"""
        def runner():
            try:
                self.q.put((on_done, work(), None))
            except Exception as e:  # 任何錯誤都必須回報到畫面，不能讓背景執行緒無聲結束
                self.q.put((on_done, None, e))
        threading.Thread(target=runner, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                on_done, result, err = self.q.get_nowait()
                on_done(result, err)
        except queue.Empty:
            pass
        finally:
            self._poll_job = self.after(100, self._poll_queue)


# ---------------------------------------------------------------- 首次設定引導

HELP_URL = "https://github.com/MinHao1103/DayNote#第一次設定"  # README 的圖文設定教學


def parse_client_json(path):
    """讀取 Google Cloud Console 下載的用戶端 JSON，回傳 (client_id, client_secret)。

    電腦版應用程式的格式為 {"installed": {"client_id": ..., "client_secret": ...}}。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("檔案內容不是用戶端設定")
    if "web" in data and "installed" not in data:
        raise ValueError("這是「網頁應用程式」用戶端，請改建立「電腦版應用程式」類型")
    info = data.get("installed", data)
    client_id, secret = info.get("client_id", ""), info.get("client_secret", "")
    if not client_id.endswith(".apps.googleusercontent.com") or not secret:
        raise ValueError("找不到 client_id 或 client_secret，請確認是從 Google Cloud Console 下載的用戶端 JSON")
    return client_id, secret


def write_config(client_id, client_secret):
    """寫入 config.json；原本的檔案能讀就保留其他設定，只更新用戶端。"""
    cfg = {"client_id": "", "client_secret": "", "ca_file": ""}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if not ln.lstrip().startswith("//")]
        old = json.loads("\n".join(lines))
        if isinstance(old, dict):
            cfg.update(old)
    except (OSError, ValueError):
        pass
    cfg.update(client_id=client_id, client_secret=client_secret)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def run_setup_wizard():
    """沒有 config.json（或仍是範例值）時的首次設定視窗。完成回傳設定，取消回傳 None。"""
    root = tk.Tk()
    apply_dpi_scale(root)
    root.title("DayNote 首次設定")
    root.configure(bg=BG, padx=px(24), pady=px(20))
    root.resizable(False, False)
    result = {"cfg": None}

    tk.Label(root, text="歡迎使用 DayNote", bg=BG, fg=TEXT, font=("Microsoft JhengHei UI", 15, "bold"),
             anchor="w").pack(fill="x")
    steps = ("第一次使用需要一個 Google 金鑰檔（client_secret_….json）。\n\n"
             "還沒有：按「開啟設定教學」，照步驟做完就會下載這個檔案。\n"
             "已經有：按「選擇金鑰檔…」，到「下載」資料夾選它。")
    tk.Label(root, text=steps, bg=BG, fg=TEXT, font=FONT, justify="left", anchor="w",
             wraplength=px(380)).pack(fill="x", pady=(px(10), px(14)))
    message = tk.Label(root, text="", bg=BG, fg=RED, font=SMALL, justify="left", anchor="w", wraplength=px(380))

    def choose():
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        path = filedialog.askopenfilename(
            parent=root, title="選擇 Google 金鑰檔（檔名 client_secret_ 開頭）",
            initialdir=downloads if os.path.isdir(downloads) else None,
            filetypes=[("Google 金鑰檔", "client_secret*.json"), ("JSON", "*.json"), ("所有檔案", "*.*")])
        if not path:
            return
        try:
            client_id, secret = parse_client_json(path)
            write_config(client_id, secret)
            result["cfg"] = load_config()
        except (OSError, ValueError, ConfigMissing) as e:
            message.config(text=f"無法使用這個檔案：{e}", fg=RED)
            return
        root.destroy()

    buttons = tk.Frame(root, bg=BG)
    buttons.pack(fill="x")
    pick = tk.Label(buttons, text="選擇金鑰檔…", bg=ACCENT, fg="white", font=FONT, cursor="hand2",
                    padx=px(14), pady=px(5))
    pick.pack(side="left")
    pick.bind("<Button-1>", lambda e: choose())
    guide = tk.Label(buttons, text="開啟設定教學", bg=BG, fg=ACCENT, font=FONT, cursor="hand2", padx=px(12))
    guide.pack(side="left")
    guide.bind("<Button-1>", lambda e: webbrowser.open(HELP_URL))
    cancel = tk.Label(buttons, text="取消", bg=BG, fg=GRAY, font=FONT, cursor="hand2")
    cancel.pack(side="right")
    cancel.bind("<Button-1>", lambda e: root.destroy())
    message.pack(fill="x", pady=(px(10), 0))

    root.update_idletasks()  # 置中
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return result["cfg"]


def main():
    enable_dpi_awareness()
    migrate_legacy_files()
    try:
        try:
            cfg = load_config()
        except ConfigMissing:
            cfg = run_setup_wizard()  # 首次執行：引導選擇 Google 用戶端 JSON
            if not cfg:
                return
        google = Google(cfg)
    except (OSError, ValueError, KeyError) as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP, f"設定檔有誤：{e}\n\n請修正 data\\config.json，或刪除它後重新啟動以進行首次設定。")
        return
    watch = write_pid()
    try:
        App(google, borderless=bool(cfg.get("borderless", True)),
            pin_to_desktop=bool(cfg.get("pin_to_desktop", True)), watch_pid=watch,
            desktop_reminder=bool(cfg.get("desktop_reminder", True)),
            show_in_taskbar=bool(cfg.get("show_in_taskbar", False)),
            holiday_calendar=cfg.get("holiday_calendar", DEFAULT_HOLIDAY_CALENDAR)).mainloop()
    finally:
        remove_pid()


def write_pid():
    """寫入自己的 PID；若有舊的 DayNote 在執行，它看到 PID 被換掉就會自行關閉。成功回傳 True。"""
    try:
        with open(PID_PATH, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        return True
    except OSError:
        return False  # 寫不了只是少了「單一執行個體」功能，不影響程式本身


def remove_pid():
    """只刪除自己寫的 PID 檔，避免誤刪新開的那一份。"""
    try:
        with open(PID_PATH, encoding="ascii") as f:
            if f.read().strip() != str(os.getpid()):
                return
        os.remove(PID_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    main()
