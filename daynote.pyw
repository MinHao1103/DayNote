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

# 最小權限：Tasks 讀寫、日曆事件唯讀（不含日曆清單與設定）
SCOPES = ("https://www.googleapis.com/auth/tasks "
          "https://www.googleapis.com/auth/calendar.events.readonly")
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
CAL_API = "https://www.googleapis.com/calendar/v3"
LOGIN_TIMEOUT_SEC = 300
FOCUS_REFRESH_SEC = 30

# 畫面樣式
BG = "#fff7d1"       # 便利貼淡黃
BORDER = "#d9c76a"
TEXT = "#323130"
EVENT_DOT = "#a19f9d"  # 月曆上「只有行程」的日期圓點
CHILD_INDENT = 28     # 子任務卡片縮排
CARD_BG = "#fffdf3"   # 卡片底色：比純白柔和，在黃底上不突兀
PLACEHOLDER = "新增工作"
SELECTED = "#f3e6a2"
HOVER = "#f5eab0"
CARD = "#ffffff"
ACCENT = "#2564cf"
RED = "#d13438"
GRAY = "#8a8886"
FONT = ("Microsoft JhengHei UI", 10)
SMALL = ("Microsoft JhengHei UI", 8)
TITLE_FONT = ("Microsoft JhengHei UI", 15, "bold")
ICON_FONT = ("Segoe UI Symbol", 11)
DEFAULT_SIZE = (380, 620)
MIN_SIZE = (340, 480)
CELL_W, CELL_H, HEAD_H = 50, 34, 20
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


GWL_EXSTYLE, GWLP_HWNDPARENT = -20, -8
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
    except (OSError, AttributeError):
        pass


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
    """無邊框視窗不能用 tkinter 的 iconify()，改用 ShowWindow(SW_MINIMIZE)。"""
    user32 = ctypes.WinDLL("user32")
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow(_hwnd(widget), 6)


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

    def add_task(self, list_id, title, due):
        body = {"title": title}
        if due:
            # due 只有日期有意義，時間部分固定填 00:00:00Z
            body["due"] = f"{due.isoformat()}T00:00:00.000Z"
        return self.api("POST", f"{TASKS_API}/lists/{_q(list_id)}/tasks", body=body)

    def complete_task(self, list_id, task_id):
        self.api("PATCH", f"{TASKS_API}/lists/{_q(list_id)}/tasks/{_q(task_id)}",
                 body={"status": "completed"})

    def events(self, start, end):
        """讀取主日曆在 [start, end) 期間的事件；singleEvents=true 會把重複事件展開成單筆。"""
        def local_iso(d):
            return dt.datetime.combine(d, dt.time()).astimezone().isoformat()
        return self._paged(f"{CAL_API}/calendars/primary/events", {
            "timeMin": local_iso(start), "timeMax": local_iso(end),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 250})


def _rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    """在 Canvas 畫圓角矩形（用 smooth 多邊形近似）。"""
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kw)


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
        tk.Label(tip, text=text, bg=TEXT, fg="white", font=SMALL, padx=6, pady=2).pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() - tip.winfo_reqwidth()) // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
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


class App(tk.Tk):
    """主視窗。網路工作一律丟到背景執行緒，結果經 queue 交回主執行緒更新畫面。"""

    def __init__(self, google, borderless=True, pin_to_desktop=True):
        super().__init__()
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
        self.weeks = []
        self.busy = False
        self.last_refresh = 0.0
        self.show_undated = False

        self._build()
        self.redraw()
        self.pin_to_desktop = pin_to_desktop
        if borderless:
            self.overrideredirect(True)
        self.after(50, self._apply_win_style)
        self.after(100, self._poll_queue)
        self.bind("<FocusIn>", self._on_focus)
        if self.g.refresh_token:
            self.refresh()
        else:
            self._show_error(NeedLogin("尚未登入，請按右上角「登入」"))

    # ---- 視窗外框：位置記憶、拖曳、縮放、置頂、縮小、關閉

    def _restore_geometry(self):
        """還原上次的位置與大小；位置跑到所有螢幕外面時改用預設位置。"""
        w = max(int(self.state_data.get("w", DEFAULT_SIZE[0])), MIN_SIZE[0])
        h = max(int(self.state_data.get("h", DEFAULT_SIZE[1])), MIN_SIZE[1])
        x, y = self.state_data.get("x"), self.state_data.get("y")
        screen = win_virtual_screen()
        if isinstance(x, int) and isinstance(y, int) and screen:
            left, top, right, bottom = screen
            # 至少要看得到頂部列的一部分，才拖得回來
            if left <= x + 60 <= right and top <= y + 10 <= bottom:
                self.geometry(f"{w}x{h}+{x}+{y}")
                return
        self.geometry(f"{w}x{h}+200+120")

    def _apply_win_style(self):
        if not self.borderless:
            # 一般視窗是備援模式，維持系統預設。
            # 實測：有邊框視窗掛到桌面後，加上工作列樣式就無法撐過 Win + D，兩者只能擇一。
            return
        if self.pin_to_desktop:
            win_pin_to_desktop(self)
        win_round_corners(self)
        self._reshow_with_appwindow(retries=1)

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
        state = dict(self.state_data, topmost=self.topmost)
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
                       padx=6, pady=2, cursor="hand2")
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.config(bg=HOVER))
        btn.bind("<Leave>", lambda e: btn.config(bg=BG))
        if tip:
            Tooltip(btn, tip)
        return btn

    # ---- 版面

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=(12, 4), pady=(6, 0))
        # 右側按鈕群先 pack，空間不足時被壓縮的是日期標題，而不是按鈕
        tools = tk.Frame(top, bg=BG)
        tools.pack(side="right")
        self.btn_login = tk.Label(tools, text="登入", bg=ACCENT, fg="white", font=FONT,
                                  padx=8, cursor="hand2")
        self.btn_login.bind("<Button-1>", lambda e: self.login())
        Tooltip(self.btn_login, "用瀏覽器登入 Google 帳號")
        self.btn_refresh = self._icon_button(tools, "⟳", self.refresh, tip="立即同步")
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
        self.lbl_date.pack(side="left", fill="x", expand=True)
        # 頂部列（含日期文字）當拖曳區
        for widget in (top, self.lbl_date):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        nav = tk.Frame(self, bg=BG)
        nav.pack(fill="x", padx=12, pady=(6, 0))
        self._icon_button(nav, "◀", lambda: self.shift_month(-1), tip="上個月").pack(side="left")
        self._icon_button(nav, "今天", lambda: self.select(dt.date.today()), font=FONT,
                          tip="回到今天").pack(side="right")
        self._icon_button(nav, "▶", lambda: self.shift_month(1), tip="下個月").pack(side="right")
        self.lbl_month = tk.Label(nav, font=FONT, bg=BG)
        self.lbl_month.pack(side="left", expand=True)

        self.cal = tk.Canvas(self, width=7 * CELL_W, height=HEAD_H + 6 * CELL_H,
                             bg=BG, highlightthickness=0)
        self.cal.pack(pady=4)
        self.cal.bind("<Button-1>", self._on_cal_click)

        # 底部先 pack，視窗縮小時才不會被任務清單擠掉
        status_row = tk.Frame(self, bg=BG)
        status_row.pack(side="bottom", fill="x", padx=(12, 0))
        grip = tk.Label(status_row, text="◢", bg=BG, fg=BORDER, font=SMALL, cursor="size_nw_se")
        Tooltip(grip, "拖拉調整視窗大小")
        grip.pack(side="right", anchor="se")
        grip.bind("<Button-1>", self._start_resize)
        grip.bind("<B1-Motion>", self._on_resize)
        self.status = tk.Label(status_row, bg=BG, fg=GRAY, font=SMALL, anchor="w", justify="left")
        self.status.pack(side="left", fill="x", expand=True, pady=(0, 6))
        # 狀態文字依視窗寬度換行
        status_row.bind("<Configure>", lambda e: self.status.config(wraplength=max(e.width - 30, 100)))
        bottom = tk.Frame(self, bg=CARD_BG, padx=8, pady=6,
                          highlightthickness=1, highlightbackground=BORDER)
        bottom.pack(side="bottom", fill="x", padx=12, pady=4)
        tk.Label(bottom, text="＋", bg=CARD_BG, fg=ACCENT, font=FONT).pack(side="left")
        self.entry = tk.Entry(bottom, font=FONT, relief="flat", bg=CARD_BG, fg=TEXT,
                              insertbackground=TEXT, highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, padx=4)
        self.entry.bind("<Return>", lambda e: self.add_task())
        self.entry.bind("<Button-1>", lambda e: self.entry.focus_force())
        self.entry.bind("<FocusIn>", lambda e: self._hide_placeholder())
        self.entry.bind("<FocusOut>", lambda e: self._show_placeholder())
        self.placeholder_on = False
        self._show_placeholder()
        self.cmb_list = ttk.Combobox(bottom, state="readonly", width=10,
                                     postcommand=self._raise_list_popdown)
        self.cmb_list.pack(side="right")
        Tooltip(self.cmb_list, "新增的工作要放進哪個清單")

        # 可捲動的任務清單
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12)
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

    def redraw(self):
        self.today = dt.date.today()
        d = self.selected
        self.lbl_date.config(text=f"{d.month}月{d.day}日 星期{WEEKDAY_NAME[d.weekday()]}")
        self._draw_calendar()
        self._draw_list()

    def _draw_calendar(self):
        c = self.cal
        c.delete("all")
        year, month = self.view
        self.lbl_month.config(text=f"{year} 年 {month} 月")
        for i, name in enumerate(WEEK_HEAD):
            c.create_text(i * CELL_W + CELL_W / 2, HEAD_H / 2, text=name, fill=GRAY, font=SMALL)

        # 子任務跟著父任務，月曆圓點只看最上層任務的日期
        task_days = {t["due"] for t in self._top_level() if t["due"]}
        self.weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
        c.config(height=HEAD_H + len(self.weeks) * CELL_H)  # 依當月週數調整高度，不留空白列
        for row, week in enumerate(self.weeks):
            for col, day in enumerate(week):
                x, y = col * CELL_W, HEAD_H + row * CELL_H
                cx = x + CELL_W / 2
                if day == self.selected:
                    c.create_rectangle(x + 3, y + 2, x + CELL_W - 3, y + CELL_H - 2, fill=SELECTED, outline="")
                if day == self.today:
                    c.create_oval(cx - 11, y + 3, cx + 11, y + 25, fill=ACCENT, outline="")
                    color = "white"
                else:
                    color = "#323130" if day.month == month else "#c8c6c4"
                c.create_text(cx, y + 14, text=str(day.day), fill=color, font=FONT)
                if day in task_days or day in self.events:
                    # 紅＝有逾期未完成待辦；藍＝有待辦；灰＝只有日曆行程
                    if day in task_days:
                        dot = RED if day < self.today else ACCENT
                    else:
                        dot = EVENT_DOT
                    c.create_oval(cx - 2, y + 28, cx + 2, y + 32, fill=dot, outline="")

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
        label = "今天" if day == self.today else f"{day.month}月{day.day}日"
        tk.Label(self.list_frame, text=f"{label} · {len(events) + len(tasks)} 項",
                 bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x", pady=(0, 2))
        for when, title in events:
            self._card(f"{when}  {title}", "Google 日曆", None)
        for task in tasks:
            self._task_with_children(task)
        if not events and not tasks:
            tk.Label(self.list_frame, text="這天沒有任務", bg=BG, fg=GRAY, font=FONT).pack(pady=8)

        undated = sorted((t for t in top_level if t["due"] is None), key=self._order_key)
        if undated:
            arrow = "▼" if self.show_undated else "▶"
            header = tk.Label(self.list_frame, text=f"{arrow} 未排日期（{len(undated)}）",
                              bg=BG, fg=ACCENT, font=FONT, cursor="hand2", anchor="w")
            header.pack(fill="x", pady=(10, 2))
            header.bind("<Button-1>", lambda e: self._toggle_undated())
            if self.show_undated:
                for task in undated:
                    self._task_with_children(task)
        self.list_canvas.yview_moveto(0)

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
        subtitle = f"已逾期 · {task['list_title']}" if overdue else task["list_title"]
        self._card(task["title"], subtitle, task, subtitle_fg=RED if overdue else GRAY)
        for child in self._children(task):
            # 子任務不顯示清單名稱（和父任務相同），比照 Google Tasks 精簡顯示
            self._card(child["title"], "", child, indent=True)

    def _card(self, title, subtitle, task, indent=False, subtitle_fg=GRAY):
        """一張圓角任務卡片；task 為 None 時是日曆事件（只顯示、不可勾選）。indent＝子任務縮排。"""
        card = tk.Canvas(self.list_frame, bg=BG, highlightthickness=0, height=48)
        card.pack(fill="x", pady=3, padx=(CHILD_INDENT if indent else 0, 0))
        inner = tk.Frame(card, bg=CARD_BG)
        if task:
            circle = self._check_circle(inner, task)
            circle.pack(side="left", padx=(0, 8))
            count = len(self._children(task))
            Tooltip(circle, f"標記為完成（含 {count} 個子任務）" if count else "標記為完成")
        else:
            icon = tk.Label(inner, text="◇", bg=CARD_BG, fg=GRAY, font=FONT, width=2)
            icon.pack(side="left", padx=(0, 6))
            Tooltip(icon, "Google 日曆行程（唯讀，請到日曆 App 修改）")
        text = tk.Frame(inner, bg=CARD_BG)
        text.pack(side="left", fill="x", expand=True)
        lbl_title = tk.Label(text, text=title, bg=CARD_BG, fg=TEXT, font=FONT, anchor="w", justify="left")
        lbl_title.pack(fill="x")
        if subtitle:
            tk.Label(text, text=subtitle, bg=CARD_BG, fg=subtitle_fg, font=SMALL, anchor="w").pack(fill="x")
        window = card.create_window(10, 7, window=inner, anchor="nw")

        def relayout(_=None):
            width = card.winfo_width()
            if width < 50:
                return
            lbl_title.config(wraplength=width - 70)
            card.itemconfigure(window, width=width - 20)
            inner.update_idletasks()
            height = inner.winfo_reqheight() + 14
            card.config(height=height)  # 高度不變時不會再觸發 <Configure>，不會無限迴圈
            card.delete("bg")
            _rounded_rect(card, 0, 0, width - 1, height - 1, 8, fill=CARD_BG, outline=BORDER, tags="bg")
            card.tag_lower("bg")
        card.bind("<Configure>", relayout)

    def _check_circle(self, parent, task):
        """To Do 風格的圓圈：滑鼠移上去顯示 ✓，點擊後填滿並標記完成。"""
        c = tk.Canvas(parent, width=22, height=22, bg=CARD_BG, highlightthickness=0, cursor="hand2")
        c.create_oval(2, 2, 20, 20, outline=GRAY, width=2, tags="ring")
        c.bind("<Enter>", lambda e: c.create_text(11, 11, text="✓", fill=ACCENT, font=SMALL, tags="tick"))
        c.bind("<Leave>", lambda e: c.delete("tick"))

        def done(_):
            c.unbind("<Button-1>")  # 防止連點重複送出
            c.unbind("<Leave>")
            c.delete("tick")
            c.itemconfigure("ring", fill=ACCENT, outline=ACCENT)
            c.create_text(11, 11, text="✓", fill="white", font=SMALL)
            self.after(250, lambda: self.complete(task))  # 讓使用者看到勾選效果再移除
        c.bind("<Button-1>", done)
        return c

    def _show_placeholder(self):
        if not self.entry.get():
            self.placeholder_on = True
            self.entry.config(fg=GRAY)
            self.entry.insert(0, PLACEHOLDER)

    def _hide_placeholder(self):
        if self.placeholder_on:
            self.placeholder_on = False
            self.entry.delete(0, "end")
            self.entry.config(fg=TEXT)

    def _set_status(self, text, error=False):
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
        if (day.year, day.month) != self.view:
            self.view = (day.year, day.month)
            self.refresh()  # 日曆事件只抓顯示中的月份，換月要重新讀取
        self.redraw()

    def shift_month(self, delta):
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
                              "due": task_due(t), "parent": t.get("parent"),
                              "position": t.get("position", "")})
        year, month = view
        first = dt.date(year, month, 1)
        next_month = dt.date(year + month // 12, month % 12 + 1, 1)
        events = {}
        # 月曆會顯示前後月的幾天，查詢範圍前後放寬
        for ev in self.g.events(first - dt.timedelta(days=7), next_month + dt.timedelta(days=14)):
            for day, when in event_days(ev):
                events.setdefault(day, []).append((when, ev.get("summary") or "（無標題）"))
        return view, lists, tasks, events

    def _after_load(self, result, err):
        self.busy = False
        if err:
            self._show_error(err)
            return
        view, self.lists, self.tasks, self.events = result
        self.btn_login.pack_forget()
        self.cmb_list["values"] = [lst.get("title", "") for lst in self.lists]
        if self.lists and not 0 <= self.cmb_list.current() < len(self.lists):
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
        due = self.selected
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
                               "due": task_due(created), "parent": None,
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
    try:
        cfg = load_config()
        google = Google(cfg)
    except (OSError, ValueError, KeyError) as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP, f"設定檔有誤：{e}\n\n請參考 README.md 建立 config.json。")
        return
    App(google, borderless=bool(cfg.get("borderless", True)),
        pin_to_desktop=bool(cfg.get("pin_to_desktop", True))).mainloop()


if __name__ == "__main__":
    main()
