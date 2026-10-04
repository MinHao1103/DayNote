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
import datetime as dt
import hashlib
import http.server
import json
import os
import queue
import secrets
import ssl
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from ctypes import wintypes
from tkinter import messagebox, ttk

APP = "DayNote"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.bin")
STATE_PATH = os.path.join(BASE_DIR, "daynote_state.json")  # 視窗位置等狀態，由程式自動寫入
PID_PATH = os.path.join(BASE_DIR, "daynote.pid")  # 執行中的程序編號，供 start.sh 關閉舊的再開新的

# 最小權限：Tasks 讀寫、日曆事件唯讀（不含日曆清單與設定）
SCOPES = ("https://www.googleapis.com/auth/tasks "
          "https://www.googleapis.com/auth/calendar.events.readonly")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
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
CELL_W, CELL_H, HEAD_H = 50, 34, 20
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
    CELL_W, CELL_H, HEAD_H, CHILD_INDENT = px(50), px(34), px(20), px(28)
    DEFAULT_SIZE = (px(380), px(620))
    MIN_SIZE = (px(340), px(480))
WEEK_HEAD = "日一二三四五六"   # 月曆以星期日為第一欄
WEEKDAY_NAME = "一二三四五六日"  # 對應 date.weekday()


class NeedLogin(Exception):
    """需要（重新）登入 Google。"""


class ApiError(Exception):
    """可直接顯示給使用者的錯誤訊息。"""


# ---------------------------------------------------------------- 設定與憑證

def load_config():
    """讀取 config.json，client_id 未填時拋出 ValueError。"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("client_id") or cfg["client_id"].startswith("<"):
        raise ValueError("請在 config.json 填入 client_id 與 client_secret")
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


def win_other_app_windows_visible(widget):
    """除了 DayNote 之外，是否還有正常顯示（未縮小）的程式視窗。

    用來區分縮小的原因：Win + D 會把所有程式視窗縮掉（實測剩 0 個）；點工作列只會縮 DayNote。
    """
    own = _hwnd(widget)
    found = []
    try:
        user32 = _user32()
        user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetWindow.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        dwmapi = ctypes.WinDLL("dwmapi")
        skip_classes = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def check(hwnd, _):
            if hwnd == own or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            cloaked = ctypes.c_int(0)  # 隱藏中的 UWP 視窗（DWMWA_CLOAKED）
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            ex, st = user32.GetWindowLongW(hwnd, GWL_EXSTYLE), user32.GetWindowLongW(hwnd, GWL_STYLE)
            is_app = (ex & WS_EX_APPWINDOW) or (not user32.GetWindow(hwnd, 4) and st & 0x00C00000)
            if cloaked.value or ex & WS_EX_TOOLWINDOW or not is_app:
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value not in skip_classes:
                found.append(hwnd)
                return False  # 找到一個就夠了
            return True
        user32.EnumWindows(check, 0)
    except (OSError, AttributeError):
        return True  # 判斷不了時當作工作列點擊，不自動還原
    return bool(found)


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

    def open_tasks(self, list_id):
        # showCompleted=false：不回傳已完成任務（已完成任務才會被隱藏，因此不需要 showHidden）
        return self._paged(f"{TASKS_API}/lists/{_q(list_id)}/tasks",
                           {"showCompleted": "false", "maxResults": 100})

    def add_task(self, list_id, title, due, parent=None):
        """新增工作；parent 指定時建立為該工作的子工作。"""
        body = {"title": title}
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
    for child in widget.winfo_children():
        _set_bg(child, color)


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


class DatePicker:
    """小月曆日期選擇器：快捷選項（今天／明天／下週一／無日期）＋月曆。點外面或按 Esc 關閉。"""

    def __init__(self, app, anchor, current, on_pick, allow_none=True):
        self.app = app
        self.on_pick = on_pick
        base = current or dt.date.today()
        self.view = (base.year, base.month)
        self.current = current
        self.allow_none = allow_none
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

    def pick(self, day):
        self.close()
        self.on_pick(day)

    def close(self):
        if self.win.winfo_exists():
            self.win.grab_release()
            self.win.destroy()


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
                 holiday_calendar=DEFAULT_HOLIDAY_CALENDAR):
        super().__init__()
        apply_dpi_scale(self)
        self.g = google
        self.title(APP)
        self.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        self.minsize(*MIN_SIZE)
        self.state_data = load_state()
        self.borderless = borderless
        self.in_taskbar = not borderless
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
        self.detail = None          # 詳細頁編輯中的工作與欄位

        self._build()
        self.redraw()
        self.pin_to_desktop = pin_to_desktop
        if borderless:
            self.overrideredirect(True)
        self.after(50, self._apply_win_style)
        self.after(100, self._poll_queue)
        self.bind("<FocusIn>", self._on_focus)
        self._bind_shortcuts()
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
        if self.pin_to_desktop:
            win_pin_to_desktop(self)
            # 「可縮小」讓工作列點擊能縮到背景，但 Win + D 也會因此縮掉視窗；
            # 被縮小時若其他程式視窗也全被縮掉（Win + D），就立刻還原，維持釘在桌面
            self.bind("<Unmap>", lambda e: e.widget is self and self.after(150, self._undo_show_desktop))
        win_round_corners(self)
        self._reshow_with_appwindow(retries=1)

    def _undo_show_desktop(self):
        if win_is_minimized(self) and not win_other_app_windows_visible(self):
            win_show_no_activate(self)  # 所有視窗都被縮掉＝Win + D，DayNote 留在桌面上

    def _reshow_with_appwindow(self, retries):
        """套用工作列樣式並重新顯示；等視窗真的顯示後再確認，沒套上就重試。"""
        win_add_appwindow(self)
        self.withdraw()
        self.after(10, self.deiconify)
        self.after(200, lambda: self._verify_taskbar(retries))

    def _verify_taskbar(self, retries):
        self.in_taskbar = win_has_appwindow(self)
        if self.in_taskbar:
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
        if self.borderless:
            win_minimize(self)
        else:
            self.iconify()

    def close(self):
        state = dict(self.state_data, topmost=self.topmost, scale=SCALE)
        if self.winfo_x() > -30000:  # 縮小中 Windows 回報 -32000，此時沿用上次存的位置
            state.update(x=self.winfo_x(), y=self.winfo_y(),
                         w=self.winfo_width(), h=self.winfo_height())
        save_state(state)
        if getattr(self, "_poll_job", None):
            self.after_cancel(self._poll_job)
        self.destroy()

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
        self.btn_refresh = self._icon_button(tools, "⟳", self.refresh, tip="立即同步（F5）")
        self.btn_refresh.pack(side="left")
        self.btn_pin = self._icon_button(
            tools, "📌", self.toggle_topmost,
            tip=lambda: "取消置頂" if self.topmost else "置頂：永遠顯示在其他視窗上面")
        self.btn_pin.bind("<Leave>", lambda e: self._paint_pin(), add="+")
        self.btn_pin.pack(side="left")
        self._paint_pin()
        self.btn_min = self._icon_button(tools, "—", self.minimize, tip="縮小到工作列")
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
        self.cmb_list = ttk.Combobox(bottom, state="readonly", width=10,
                                     postcommand=self._raise_list_popdown)
        self.cmb_list.pack(side="right")
        self.cmb_list.bind("<<ComboboxSelected>>", lambda e: self._remember_list())
        Tooltip(self.cmb_list, "新增的工作要放進哪個清單")
        self.btn_new_due = tk.Label(bottom, bg=CARD_BG, fg=ACCENT, font=SMALL, cursor="hand2", padx=px(4))
        self.btn_new_due.pack(side="right", padx=(0, px(4)))
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
                cx = x + CELL_W / 2
                if day == self.selected:
                    c.create_rectangle(x + px(3), y + px(2), x + CELL_W - px(3), y + CELL_H - px(2),
                                       fill=SELECTED, outline="")
                if day == self.today:
                    c.create_oval(cx - px(11), y + px(3), cx + px(11), y + px(25), fill=ACCENT, outline="")
                    color = "white"
                else:
                    off = day.weekday() >= 5 or self._is_day_off(day)
                    if day.month == month:
                        color = HOLIDAY_RED if off else TEXT
                    else:
                        color = HOLIDAY_RED_FADED if off else "#c8c6c4"
                c.create_text(cx, y + px(14), text=str(day.day), fill=color, font=FONT)
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
                    c.create_oval(cx - px(2), y + px(28), cx + px(2), y + px(32), fill=dot, outline="")

    def _is_day_off(self, day):
        return any(off for _, off in self.holidays.get(day, []))

    def _raise_list_popdown(self):
        """視窗置頂時，下拉選單也要置頂，否則會被主視窗蓋住。"""
        popdown = self.tk.call("ttk::combobox::PopdownWindow", self.cmb_list)
        self.tk.call("wm", "attributes", popdown, "-topmost", 1)

    def _draw_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        day = self.selected
        events = self.events.get(day, [])
        top_level = self._top_level()
        tasks = sorted((t for t in top_level if t["due"] == day), key=self._order_key)
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
        due = task["due"]
        subtitle = (f"已逾期 · {due.month}月{due.day}日 · {task['list_title']}" if overdue
                    else task["list_title"])
        self._card(task["title"], subtitle, task, subtitle_fg=RED if overdue else GRAY)
        for child in self._children(task):
            # 子任務不顯示清單名稱（和父任務相同），比照 Google Tasks 精簡顯示
            self._card(child["title"], "", child, indent=True)

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
        c.create_oval(px(2), px(2), size - px(2), size - px(2), outline=GRAY, width=px(2), tags="ring")
        c.bind("<Enter>", lambda e: c.create_text(mid, mid, text="✓", fill=ACCENT, font=SMALL, tags="tick"))
        c.bind("<Leave>", lambda e: c.delete("tick"))

        def done(_):
            c.unbind("<Button-1>")  # 防止連點重複送出
            c.unbind("<Leave>")
            c.delete("tick")
            c.itemconfigure("ring", fill=ACCENT, outline=ACCENT)
            c.create_text(mid, mid, text="✓", fill="white", font=SMALL)
            self.after(250, lambda: self.complete(task))  # 讓使用者看到勾選效果再移除
        c.bind("<Button-1>", done)
        return c

    def _placeholder_text(self):
        return "新增工作"

    def _set_new_due(self, day):
        self.new_due = day
        self.redraw()

    # ---- 詳細頁（比照 Google Tasks App：標題、詳細資訊、日期、子工作、刪除、標示為完成）

    def _find_task(self, task_id):
        return next((t for t in self.tasks if t["id"] == task_id), None)

    def open_detail(self, task):
        if self.detail:
            self.close_detail()  # 從子工作切換時，先儲存目前這筆
        task = self._find_task(task["id"]) or task
        self.detail = {"task": task, "due": task["due"]}
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
        self._icon_button(bar, "← 返回", self.close_detail, font=FONT, tip="儲存並返回（Esc）").pack(side="left")
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
        self._rerender_detail()

    def _detail_changes(self):
        """比對詳細頁與原資料，回傳要送出的欄位。標題清空時保留原標題。"""
        task = self.detail["task"]
        fields = {}
        title = self.detail_title.get().strip()
        if title and title != task["title"]:
            fields["title"] = title
        notes = self.detail_notes.get("1.0", "end-1c")
        if notes != task.get("notes", ""):
            fields["notes"] = notes
        if self.detail["due"] != task["due"]:
            due = self.detail["due"]
            fields["due"] = f"{due.isoformat()}T00:00:00.000Z" if due else None
        return fields

    def close_detail(self):
        """返回主畫面；有修改就自動儲存（比照手機 App，不需要按儲存）。"""
        if not self.detail:
            return
        task = self.detail["task"]
        fields = self._detail_changes()
        self.detail = None
        self.detail_view.pack_forget()
        self.main_view.pack(fill="both", expand=True)
        if fields:
            before = dict(task)
            task.update({k: v for k, v in fields.items() if k != "due"})
            if "due" in fields:
                task["due"] = task_due({"due": fields["due"]})
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

    def delete_detail(self):
        task = self.detail["task"]
        children = [] if task.get("parent") else self._children(task)
        extra = f"\n（含 {len(children)} 個子工作）" if children else ""
        if not messagebox.askyesno("刪除工作", f"確定要刪除「{task['title']}」？{extra}\n\n刪除後無法在 DayNote 復原。",
                                   parent=self, icon="warning"):
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
                               "parent": parent["id"], "notes": "", "position": created.get("position", "")})
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
            self.btn_login.pack(side="left", padx=(0, 4), before=self.btn_refresh)
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

    def _remember_list(self):
        index = self.cmb_list.current()
        if 0 <= index < len(self.lists):
            self.state_data["last_list_id"] = self.lists[index]["id"]

    def _watch_pid(self):
        """單一執行個體：PID 檔被移除或換成別的 PID（start.sh 或新開的 DayNote），就正常關閉並存好狀態。"""
        try:
            with open(PID_PATH, encoding="ascii") as f:
                still_mine = f.read().strip() == str(os.getpid())
        except OSError:
            still_mine = False
        if still_mine:
            self.after(1000, self._watch_pid)
        else:
            self.close()

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
        self.btn_login.pack_forget()
        self.refresh()

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.last_refresh = time.time()
        self._set_status("同步中…")
        view = self.view
        self.run_bg(lambda: self._load(view), self._after_load)

    def _load(self, view):
        """（背景執行緒）讀取清單、所有未完成任務，以及顯示月份前後的日曆事件。"""
        lists = self.g.tasklists()
        tasks = []
        for lst in lists:
            for t in self.g.open_tasks(lst["id"]):
                if t.get("status") == "completed":
                    continue
                tasks.append({"id": t["id"], "title": t.get("title") or "（無標題）",
                              "list_id": lst["id"], "list_title": lst.get("title", ""),
                              "due": task_due(t), "parent": t.get("parent"), "notes": t.get("notes", ""),
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
        return view, lists, tasks, events, self._load_holidays(start, end)

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
        view, self.lists, self.tasks, self.events, self.holidays = result
        self.btn_login.pack_forget()
        self.cmb_list["values"] = [lst.get("title", "") for lst in self.lists]
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
        self.redraw()

        def work():
            for t in group:
                self.g.complete_task(t["list_id"], t["id"])

        def done(_, err):
            if err:
                # 可能已部分完成；先放回畫面並顯示錯誤，下次同步會以 Google 上的實際狀態為準
                self.tasks.extend(group)
                self.redraw()
                self._show_error(err)
                return
            self._offer_undo(group)
        self.run_bg(work, done)

    def _offer_undo(self, group):
        """在狀態列顯示「已完成…　復原」，UNDO_SECONDS 秒後自動收起。"""
        self._set_status(f"已完成「{group[0]['title']}」")
        self.undo_group = group
        self.btn_undo.pack(side="right", padx=px(4), before=self.status)
        self.undo_job = self.after(UNDO_SECONDS * 1000, self._hide_undo)

    def _hide_undo(self):
        if self.undo_job:
            self.after_cancel(self.undo_job)
            self.undo_job = None
        self.undo_group = None
        self.btn_undo.pack_forget()

    def undo_complete(self):
        group = self.undo_group
        if not group:
            return
        self._hide_undo()
        self.tasks.extend(group)
        self._set_status(f"已復原「{group[0]['title']}」")
        self.redraw()

        def work():
            for t in group:
                self.g.uncomplete_task(t["list_id"], t["id"])

        def done(_, err):
            if err:
                for t in group:
                    if t in self.tasks:
                        self.tasks.remove(t)
                self.redraw()
                self._show_error(err)
        self.run_bg(work, done)

    def add_task(self):
        title = "" if self.placeholder_on else self.entry.get().strip()
        index = self.cmb_list.current()
        if not title:
            return
        if index < 0:
            self._set_status("尚未讀到任何清單，請先同步", error=True)
            return
        lst = self.lists[index]
        self._remember_list()
        due = self.new_due
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
                               "due": task_due(created), "parent": None, "notes": "",
                               "position": created.get("position", "")})
            self._set_status(f"已新增到「{lst.get('title', '')}」")
            self.redraw()
        self.run_bg(lambda: self.g.add_task(lst["id"], title, due), done)

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


def main():
    enable_dpi_awareness()
    try:
        cfg = load_config()
        google = Google(cfg)
    except (OSError, ValueError, KeyError) as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP, f"設定檔有誤：{e}\n\n請參考 README.md 建立 config.json。")
        return
    watch = write_pid()
    try:
        App(google, borderless=bool(cfg.get("borderless", True)),
            pin_to_desktop=bool(cfg.get("pin_to_desktop", True)), watch_pid=watch,
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
