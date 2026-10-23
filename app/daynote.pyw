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
import logging
import logging.handlers
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
import winreg
import winsound
from ctypes import wintypes
from tkinter import filedialog, ttk
from tkinter import messagebox as native_messagebox  # 只用在主視窗建立之前（設定檔錯誤）；之後改用 ThemedMessageBox
from tkinter import font as tkfont

APP = "DayNote"
# 資料夾結構：DayNote\app\daynote.pyw（程式）、DayNote\data\（個人資料，不上傳、不打包）
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")   # Google 用戶端設定
TOKEN_PATH = os.path.join(DATA_DIR, "token.bin")      # DPAPI 加密的登入憑證
STATE_PATH = os.path.join(DATA_DIR, "state.json")     # 視窗位置、提醒記錄等，由程式自動寫入
PID_PATH = os.path.join(DATA_DIR, "daynote.pid")      # 執行中的程序編號，用來只保留一個 DayNote
PID_READ_RETRIES = 3  # PID 檔連續讀不到幾次（每秒檢查一次）才當作被取代而關閉
LOG_PATH = os.path.join(DATA_DIR, "daynote.log")      # 提醒觸發紀錄，用來追查「提醒時間不對」
log = logging.getLogger("daynote")
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

# 畫面樣式：奶油底＋米色層次，陶土色為強調色，粉彩色只用於圓點、色條等非文字點綴
# 文字用色（TEXT、GRAY、ACCENT、RED、HOLIDAY_RED）在奶油底上對比皆 ≥ 4.5:1，符合無障礙
BG = "#fbf7f0"       # 奶油底
BORDER = "#e6dccc"
TEXT = "#3d342c"     # 深可可色，取代純黑，較柔和
EVENT_DOT = "#a9bfa2"  # 粉彩鼠尾草綠：月曆上「只有行程」的日期圓點、行程左側色條
PREVIEW_DOT = "#e0bca8"  # 粉彩陶土：月曆上「重複待辦預定」的日期圓點（比待辦淡，表示還沒建立）
CARD_BG = "#f4ede2"   # 米色：輸入框、詳細頁欄位底色，比奶油底略深，看得出可輸入
FADED_TEXT = "#cdc2b3"  # 月曆上其他月份的日期
ON_ACCENT = "#ffffff"   # 強調色底上的文字
UI_FONT = "Microsoft JhengHei UI"
EVENT_TIME_FONT = (UI_FONT, 9, "bold")
UNDO_SECONDS = 5
REMINDER_CHECK_MS = 30 * 1000    # 每 30 秒檢查一次提醒
CLOSE_WAIT_SEC = 15              # 關閉時等待修改送到 Google 的上限；超過就不再等（畫面不會因斷線卡住）
REMINDER_SNOOZE_MIN = 10
REMINDER_SNOOZE_MORE_MIN = (30, 60)    # 延後下拉選單的其他選項（分鐘）
REMINDER_TOMORROW_AT = dt.time(9, 0)   # 延後下拉選單「明天早上」的時間
REMINDER_MAX_LATE_H = 24         # 錯過超過 24 小時的提醒不再補跳
NEXT_UP_TICK_MS = 60 * 1000      # 「下一件」摘要每分鐘更新
FIRST_RUN_TIP = "小技巧：在待辦上按右鍵可以改日期；Ctrl + Alt + D 隨時快速新增"  # 第一次同步成功後在狀態列提示一次
FIRST_RUN_TIP_MS = 15 * 1000
USAGE_URL = "https://github.com/MinHao1103/DayNote#怎麼用"  # README 的完整操作說明
# 使用說明視窗的內容：(按鍵, 功能)；全域快捷鍵（F8、Ctrl + Alt + D）在其他程式裡也有效
HELP_SHORTCUTS = (
    ("Ctrl + Alt + D", "在任何程式叫出快速列：新增、看今天、/ 搜尋"),
    ("F8", "顯示／隱藏 DayNote"),
    ("Ctrl + N", "游標移到新增輸入框"),
    ("← →", "前一天／後一天"),
    ("↑ ↓", "前一週／後一週"),
    ("Home", "回到今天"),
    ("F5", "立即同步"),
    ("Esc", "離開詳細頁（有修改會自動儲存）"),
    ("F1", "開啟這個說明"),
)
HELP_TIPS = (
    "輸入框可以一次寫完：明天 3點 開會 每週二 #工作",
    "點圓圈完成待辦，5 秒內可以按「復原」",
    "在待辦上按右鍵：移到今天、明天或不排日期",
    "滑鼠停在月曆日期上，看當天有幾件待辦、幾個行程",
    "按月份旁的「展開整月」看整個月",
    "右上角月亮／太陽切換深色、淺色；「⋯」→「風格」有更多配色",
)
ADD_LIST_OPTION = "＋ 新增清單…"  # 清單下拉選單的最後一項
SELECTED = "#f5e4d8"  # 月曆選中日期：粉彩蜜桃，和強調色同色系
DIVIDER = "#efe7da"   # 清單列之間的細分隔線
HOVER = "#f4ece0"
CARD = "#ffffff"
CARD_HOVER = "#f6efe5"    # 待辦卡片滑過的底色
ACCENT = "#985a43"        # 陶土色：白字對比 5.4:1、奶油底上當文字 5.1:1、米色欄位 4.7:1
ACCENT_HOVER = "#854c38"
ACCENT_SOFT = "#f3e2d6"   # 次要按鈕、選項膠囊的粉彩蜜桃底
ACCENT_SOFT_HOVER = "#ecd4c4"
RED = "#b44745"           # 柔和的玫瑰紅：逾期、錯誤
HOLIDAY_RED = "#a44c54"   # 週末與國定假日的日期數字
HOLIDAY_RED_FADED = "#e4c0c0"  # 其他月份的週末／假日
FESTIVAL = "#e2b766"      # 粉彩赭黃：一般節日（不放假）的角標
GRAY = "#72665a"     # 暖灰：在奶油底對比 5.0:1、米色欄位 4.6:1
# ---- 風格（配色＋標題字型）：「⋯」→「風格」選擇，立即套用並存在 state.json 的 theme
# 元件建立時才讀取這些常數（不要寫成函式參數預設值或類別屬性），換風格時重建畫面即可換色
# 每種風格的文字用色（TEXT、GRAY、ACCENT、RED、HOLIDAY_RED）在 BG 與 CARD_BG 上對比皆 ≥ 4.5:1（有單元測試檢查）
# fonts：title／heading＝標題字級；serif＝標題用思源宋體（沒安裝時維持正黑體）
THEMES = {
    "cream": {"name": "奶油陶土", "fonts": {"serif": True, "title": 20, "heading": 11}, "colors": {
        "BG": "#fbf7f0", "BORDER": "#e6dccc", "TEXT": "#3d342c", "EVENT_DOT": "#a9bfa2", "PREVIEW_DOT": "#e0bca8",
        "CARD_BG": "#f4ede2", "FADED_TEXT": "#cdc2b3", "ON_ACCENT": "#ffffff", "SELECTED": "#f5e4d8",
        "DIVIDER": "#efe7da", "HOVER": "#f4ece0", "CARD": "#ffffff", "CARD_HOVER": "#f6efe5", "ACCENT": "#985a43",
        "ACCENT_HOVER": "#854c38", "ACCENT_SOFT": "#f3e2d6", "ACCENT_SOFT_HOVER": "#ecd4c4", "RED": "#b44745",
        "HOLIDAY_RED": "#a44c54", "HOLIDAY_RED_FADED": "#e4c0c0", "FESTIVAL": "#e2b766", "GRAY": "#72665a"}},
    "editorial": {"name": "編輯手帳", "fonts": {"serif": True, "title": 24, "heading": 12}, "colors": {
        "BG": "#f7f3ea", "BORDER": "#ddd3c2", "TEXT": "#1f1b16", "EVENT_DOT": "#7f9c8a", "PREVIEW_DOT": "#e2b3a6",
        "CARD_BG": "#efe8db", "FADED_TEXT": "#cbc1b1", "ON_ACCENT": "#ffffff", "SELECTED": "#f3dcd2",
        "DIVIDER": "#e8e0d1", "HOVER": "#f1ebdf", "CARD": "#ffffff", "CARD_HOVER": "#f6f0e4", "ACCENT": "#b8402a",
        "ACCENT_HOVER": "#9a3421", "ACCENT_SOFT": "#f3dcd2", "ACCENT_SOFT_HOVER": "#ecccbf", "RED": "#b8402a",
        "HOLIDAY_RED": "#b8402a", "HOLIDAY_RED_FADED": "#e6c2b8", "FESTIVAL": "#d9a441", "GRAY": "#6b6257"}},
    "night": {"name": "墨夜（深色）", "fonts": {"serif": True, "title": 22, "heading": 12}, "colors": {
        "BG": "#17191e", "BORDER": "#2f323a", "TEXT": "#ece6da", "EVENT_DOT": "#7fa88f", "PREVIEW_DOT": "#7a5d3a",
        "CARD_BG": "#22252c", "FADED_TEXT": "#55585f", "ON_ACCENT": "#17191e", "SELECTED": "#4a3a20",
        "DIVIDER": "#24272e", "HOVER": "#20232a", "CARD": "#22252c", "CARD_HOVER": "#2b2f37", "ACCENT": "#e8a33d",
        "ACCENT_HOVER": "#f0b55a", "ACCENT_SOFT": "#4a3a20", "ACCENT_SOFT_HOVER": "#4f4229", "RED": "#ef7a6b",
        "HOLIDAY_RED": "#e58a7e", "HOLIDAY_RED_FADED": "#6b4a47", "FESTIVAL": "#e8a33d", "GRAY": "#9c968b"}},
    "nordic": {"name": "北歐極簡", "fonts": {"serif": False, "title": 22, "heading": 12}, "colors": {
        "BG": "#f3f4f1", "BORDER": "#d9ddd6", "TEXT": "#1c2420", "EVENT_DOT": "#8fb3a0", "PREVIEW_DOT": "#b9d0c1",
        "CARD_BG": "#e9ece6", "FADED_TEXT": "#c2c8c2", "ON_ACCENT": "#ffffff", "SELECTED": "#d8e6dc",
        "DIVIDER": "#e2e5df", "HOVER": "#eaede7", "CARD": "#ffffff", "CARD_HOVER": "#eef1ec", "ACCENT": "#2f6b4f",
        "ACCENT_HOVER": "#24563f", "ACCENT_SOFT": "#d8e6dc", "ACCENT_SOFT_HOVER": "#c7dbcd", "RED": "#ae4337",
        "HOLIDAY_RED": "#ae4337", "HOLIDAY_RED_FADED": "#e2c3bd", "FESTIVAL": "#d9a441", "GRAY": "#5f6862"}},
}
DEFAULT_THEME = "cream"
CURRENT_THEME = DEFAULT_THEME
THEME_FONTS = THEMES[DEFAULT_THEME]["fonts"]
AUTO_THEME = "auto"   # 跟隨 Windows 的淺色／深色模式（不是一組配色，套用時換成下面兩種之一）
AUTO_THEME_NAME = "跟隨 Windows（淺色／深色）"
AUTO_LIGHT, AUTO_DARK = "cream", "night"
SYSTEM_THEME_CHECK_MS = 60 * 1000  # 跟隨 Windows 時，每分鐘檢查一次系統是否切換了淺色／深色


def system_is_dark():
    """Windows「應用程式模式」是否為深色（登錄檔 AppsUseLightTheme = 0）；讀不到時視為淺色。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def resolve_theme(choice, dark):
    """使用者的選擇 → 實際套用的配色：「跟隨 Windows」依系統深淺色決定，不認得的選擇用預設。

    @param choice 使用者選的風格（THEMES 的 key 或 AUTO_THEME）
    @param dark 系統目前是否為深色
    @return THEMES 的 key
    """
    if choice == AUTO_THEME:
        return AUTO_DARK if dark else AUTO_LIGHT
    return choice if choice in THEMES else DEFAULT_THEME


def apply_theme(key):
    """把風格的配色寫進模組常數；之後建立的元件就會用新配色。不認得的風格用預設。"""
    global CURRENT_THEME, THEME_FONTS
    theme = THEMES.get(key) or THEMES[DEFAULT_THEME]
    CURRENT_THEME = key if key in THEMES else DEFAULT_THEME
    THEME_FONTS = theme["fonts"]
    globals().update(theme["colors"])


def saved_theme():
    """state.json 記錄的風格；環境變數 DAYNOTE_THEME（截圖工具用）優先。讀不到時用預設。"""
    if os.environ.get("DAYNOTE_THEME"):
        return os.environ["DAYNOTE_THEME"]
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f).get("theme", DEFAULT_THEME)
    except (OSError, ValueError, AttributeError):
        return DEFAULT_THEME


# 直接執行（或截圖工具指定風格）時才套用；測試以模組載入，維持預設風格
if __name__ == "__main__" or os.environ.get("DAYNOTE_THEME"):
    apply_theme(resolve_theme(saved_theme(), system_is_dark()))

FONT = (UI_FONT, 10)
SMALL = (UI_FONT, 9)        # 說明文字：欄位標籤、副標題、狀態列（Windows 11 caption 最小 12px＝9pt）
# 標題類字型：有思源宋體（Noto Serif TC）就用襯線字，否則維持正黑體；由 apply_fonts() 於啟動時決定
SERIF_FAMILIES = ("Noto Serif TC", "Source Han Serif TC")
TITLE_FONT = (UI_FONT, 15, "bold")         # 頁面大標題（日期、工作詳細資訊）
HERO_FONT = (UI_FONT, 36, "bold")          # 主畫面的大日期數字（標題字級的 1.8 倍）
HEADING_FONT = (UI_FONT, 11)               # 月份、清單區塊標題
SUBJECT_FONT = (UI_FONT, 14, "bold")       # 詳細頁的工作標題輸入框
ICON_FONT = ("Segoe UI Symbol", 11)
# 單色線條圖示：Windows 11 為 Segoe Fluent Icons、Windows 10 為 Segoe MDL2 Assets（字碼相同）
# 介面文字仍沿用 emoji／符號（測試與 Google Tasks 的詳細資訊都以此辨識），畫面上才換成對應圖示
ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
ICON_GLYPH_FONT = None   # 由 apply_fonts() 決定；None 表示沒有圖示字型，維持原本的符號
GLYPHS = {"📅": "\ue787", "⏰": "\ue823", "🔁": "\ue8ee", "📁": "\ue8b7", "＋": "\ue710", "✓": "\ue73e",
          "←": "\ue72b", "▾": "\ue70d", "▼": "\ue70d", "▶": "\ue76c", "◀": "\ue76b", "⟳": "\ue72c",
          "📌": "\ue718", "—": "\ue921", "✕": "\ue8bb", "⋯": "\ue712", "☀": "\ue706", "🌙": "\ue708"}


def icon_parts(text):
    """把按鈕文字拆成（圖示, 文字, 下拉箭頭）；沒有圖示字型或不是已知符號時，整段當文字。"""
    if not ICON_GLYPH_FONT:
        return "", text, ""
    icon = chevron = ""
    if text[:1] in GLYPHS and text[1:2] in ("", " "):
        icon, text = GLYPHS[text[0]], text[2:]
    if text.endswith(" ▾"):
        chevron, text = GLYPHS["▾"], text[:-2]
    return icon, text, chevron
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


def apply_fonts(root):
    """標題類字型改用已安裝的襯線中文字型；都沒有時維持正黑體（Windows 內建無合適的中文襯線字）。

    內文（10pt 以下）一律維持正黑體：小字級的中文襯線字筆畫細，在螢幕上不易閱讀。
    """
    global TITLE_FONT, HEADING_FONT, SUBJECT_FONT, ICON_GLYPH_FONT, HERO_FONT
    installed = set(tkfont.families(root))
    ICON_GLYPH_FONT = next((name for name in ICON_FAMILIES if name in installed), None)
    serif = next((name for name in SERIF_FAMILIES if name in installed), None)
    fonts = THEME_FONTS  # 依目前風格決定標題字級與是否用襯線字
    if fonts["serif"] and serif:
        TITLE_FONT = (serif, fonts["title"], "bold")
        HEADING_FONT = (serif, fonts["heading"], "bold")
        SUBJECT_FONT = (serif, 14, "bold")
    elif not fonts["serif"]:
        TITLE_FONT = (UI_FONT, fonts["title"], "bold")
        HEADING_FONT = (UI_FONT, fonts["heading"], "bold")
        SUBJECT_FONT = (UI_FONT, 14, "bold")
    HERO_FONT = (TITLE_FONT[0], round(fonts["title"] * 1.8), "bold")
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


def write_file_atomic(path, data):
    """先寫到同資料夾的暫存檔再整個換上，寫到一半斷電或當機時，原本的檔案仍完整。

    @param path 目標檔案路徑
    @param data 要寫入的 bytes
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)  # 失敗時不留下半成品暫存檔
        except OSError:
            pass
        raise


def save_refresh_token(token):
    write_file_atomic(TOKEN_PATH, _dpapi(token.encode("utf-8"), encrypt=True))


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
        write_file_atomic(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))
    except OSError as e:
        # 存不了只影響下次開啟的位置與提醒記錄，不中斷關閉流程
        log.warning("Failed to save state: path=%s error=%s", STATE_PATH, e)


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
# 全域快捷鍵：(id, 修飾鍵, 按鍵, 顯示名稱)；各自的動作見 HOTKEY_ACTIONS
HOTKEYS = (
    (1, MOD_NOREPEAT, VK_F8, "F8"),
    (2, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_D, "Ctrl + Alt + D"),
)
HOTKEY_IDS = {hk[0] for hk in HOTKEYS}
# 快捷鍵對應的動作：F8 顯示／隱藏主視窗（使用者要求），Ctrl + Alt + D 叫出快速列（在任何程式中新增／完成）
HOTKEY_ACTIONS = {1: "toggle_window", 2: "quick_bar"}


def win_hook_messages(widget, on_user_minimize=None, on_hotkey=None):
    """攔截視窗訊息，原封不動交給 tkinter 處理，只額外通知：

    - SC_MINIMIZE：使用者點工作列（或 Win + ↓）要求縮小；Win + D 不會送這個
    - WM_HOTKEY：全域快捷鍵（F8、Ctrl + Alt + D），回呼收到 HOTKEYS 的 id
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
                on_hotkey(wparam)
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
    """依註冊失敗的快捷鍵名稱，產生狀態列提示。兩組功能不同（F8 主視窗、Ctrl + Alt + D 快速列），分開說明。"""
    if "F8" in failed and "Ctrl + Alt + D" in failed:
        return "F8 和 Ctrl + Alt + D 都已被其他程式占用；隱藏後請再點 StartDayNote 叫回"
    if "F8" in failed:
        return "F8 已被其他程式占用；隱藏後請按 Ctrl + Alt + D 叫出快速列，再按 Tab 開啟主視窗"
    return "Ctrl + Alt + D 已被其他程式占用，無法叫出快速列；主視窗仍可用 F8 顯示／隱藏"


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
    """Windows 11 圓角（DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND）；成功回傳 True，不支援的系統直接略過。"""
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        preference = ctypes.c_int(2)
        return dwmapi.DwmSetWindowAttribute(_hwnd(widget), 33, ctypes.byref(preference),
                                            ctypes.sizeof(preference)) == 0  # S_OK
    except (OSError, AttributeError):
        return False


def win_border_color(widget, color):
    """Windows 11 視窗外框顏色（DWMWA_BORDER_COLOR = 34），改成和奶油底同色系；不支援的系統直接略過。"""
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        colorref = ctypes.c_uint(r | g << 8 | b << 16)  # COLORREF 是 0x00BBGGRR
        ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(_hwnd(widget), 34, ctypes.byref(colorref),
                                                      ctypes.sizeof(colorref))
    except (OSError, AttributeError):
        pass


def win_style_popup(win):
    """無邊框小視窗（快速列、提醒）比照主視窗：系統圓角＋暖色外框。

    圓角成功時拿掉 Tk 自己畫的 1px 方框，否則圓角處會出現雙層框線；Windows 10 等不支援時保留方框。
    """
    win.update_idletasks()  # 確保 Windows 視窗已建立，設定才會套到正確的 HWND
    if win_round_corners(win):
        win_border_color(win, BORDER)
        win.configure(highlightthickness=0)


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


def upcoming_days(task, start, end, today):
    """重複待辦在 [start, end] 之間「預定」的日子（只供畫面預覽，Google 上還沒有這些期）。

    與 next_due 一致：從這一期的下一天、且不早於今天開始算；不是週期父工作時回傳空清單。
    """
    rule = task.get("recur")
    if not rule or task.get("parent"):
        return []
    day = max((task.get("due") or today) + dt.timedelta(days=1), today, start)
    days = []
    while day <= end:
        if _matches(rule, day):
            days.append(day)
        day += dt.timedelta(days=1)
    return days


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
    """列表卡片的副標題：逾期、週期、清單名稱。

    提醒時間顯示在列的右側時間欄（見 App._card），這裡不重複；逾期的時間仍跟著日期寫在副標題。
    """
    due, at = task["due"], time_text(task.get("time"))
    parts = []
    if due is not None and due < today:
        parts.append(f"已逾期 · {due.month}月{due.day}日{' ' + at if at else ''}")
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


def restore_fields(before, fields):
    """把 fields 改過的欄位，依修改前的任務 before 組回 API 欄位（復原用）。"""
    restored = {}
    if "due" in fields:
        restored["due"] = due_field(before["due"])
    if "notes" in fields:
        restored["notes"] = join_meta(before.get("time"), before.get("recur"), before.get("notes", ""))
    if "title" in fields:
        restored["title"] = before["title"]
    return restored


def parse_time_text(text):
    """解析使用者輸入的時間：15:00、1500、9:05、9：05（全形冒號）、11（整點）、上午11點、下午3點半。

    一律照字面的 24 小時制：只寫數字或「11點」都不猜上午／下午，寫了「下午」才加 12 小時。
    無法解析回傳 None。
    """
    text = (text or "").strip().replace(" ", "")
    match = re.match(r"^(\d{1,2})(?:[:：]?(\d{2}))?$", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        return dt.time(hour, minute) if hour < 24 and minute < 60 else None
    quick = _QUICK_TIME_RE.match(text)
    if quick and quick.group("period") and quick.group("hour"):  # 有寫上午／下午且有幾點，才交給快速列的規則換算
        return _quick_time(text)
    match = re.match(r"^(\d{1,2})點(?:(半)|(\d{1,2})分?)?$", text)
    if match:
        hour, minute = int(match.group(1)), 30 if match.group(2) else int(match.group(3) or 0)
        return dt.time(hour, minute) if hour < 24 and minute < 60 else None
    return None


def time_text(time_value):
    return f"{time_value:%H:%M}" if time_value else ""


def is_late(task, now):
    """今天的待辦、提醒時間已過但還沒完成時回傳 True（以分鐘為單位，同一分鐘內不算過）。

    @param task 任務（due、time）
    @param now 目前時間（dt.datetime）
    @return 是否已過時間
    """
    at = task.get("time")
    return task["due"] == now.date() and at is not None and at < now.time().replace(second=0, microsecond=0)


def snooze_options(now):
    """提醒延後的選項 [(說明, 再提醒的時間)]：第一個是按鈕上的預設值，其餘放在下拉選單。

    @param now 目前時間（dt.datetime）
    @return 例如 [("10 分鐘後", …), ("30 分鐘後", …), ("1 小時後", …), ("明天 9:00", …)]
    """
    def minutes_text(m):
        return f"{m // 60} 小時後" if m % 60 == 0 else f"{m} 分鐘後"
    options = [(minutes_text(m), now + dt.timedelta(minutes=m))
               for m in (REMINDER_SNOOZE_MIN, *REMINDER_SNOOZE_MORE_MIN)]
    tomorrow = dt.datetime.combine(now.date() + dt.timedelta(days=1), REMINDER_TOMORROW_AT)
    options.append((f"明天 {REMINDER_TOMORROW_AT.hour}:{REMINDER_TOMORROW_AT:%M}", tomorrow))
    return options


def next_up_text(items, now):
    """大標題下方的摘要：今天還沒到的第一件（行程或有時間的待辦）；沒有就回傳空字串。

    @param items [(dt.time, 標題)]：今天的行程與有時間的待辦
    @param now 目前時間（dt.datetime）
    @return 例如「下一件：14:00 準備週會簡報」
    """
    upcoming = sorted((at, title) for at, title in items if at >= now.time().replace(second=0, microsecond=0))
    return f"下一件：{time_text(upcoming[0][0])} {upcoming[0][1]}" if upcoming else ""


# ---------------------------------------------------------------- 快速列：一行文字辨識成待辦

_DAY_CHARS = {c: i for i, c in enumerate(WEEKDAY_NAME)} | {"天": 6}
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_NUM = r"[零〇一二兩三四五六七八九十]{1,3}"  # 中文數字（零～九十九），例如：三、十二、二十五


def _cn_number(text):
    """阿拉伯數字或中文數字（零～九十九）轉成 int；無法辨識回傳 None。例如：「15」→15、「十五」→15、「二十」→20。"""
    if text.isdigit():
        return int(text)
    if "十" in text:
        tens, _, ones = text.partition("十")
        if (tens and tens not in _CN_DIGITS) or (ones and ones not in _CN_DIGITS):
            return None
        return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)
    return _CN_DIGITS.get(text) if len(text) == 1 else None

_WEEK_WORD = r"(?:週|周|星期|禮拜)"
_QUICK_DATE_RE = re.compile(
    r"^(?:(?P<rel>今天|今日|明天|明日|大後天|後天)"
    r"|(?P<ndays>\d{1,2}|" + _CN_NUM + r")天後"
    r"|(?P<nweeks>\d|[一二兩三四])(?:個)?" + _WEEK_WORD + r"後"
    r"|(?P<next>下)" + _WEEK_WORD + r"(?P<nextday>[一二三四五六日天])"
    r"|這?" + _WEEK_WORD + r"(?P<thisday>[一二三四五六日天])"
    r"|(?P<m1>\d{1,2})/(?P<d1>\d{1,2})"
    r"|(?P<m2>\d{1,2})月(?P<d2>\d{1,2})[日號]?"
    r"|(?P<none>無日期|不排日期))")
_QUICK_TIME_RE = re.compile(
    r"^(?P<period>凌晨|上午|早上|中午|下午|晚上|今晚)?"
    r"(?:(?P<hour>\d{1,2}|" + _CN_NUM + r")"
    r"(?:[:：](?P<colon>\d{2})|點(?:(?P<half>半)|(?P<minute>\d{1,2}|" + _CN_NUM + r")分?)?))?$")
# 只寫時段沒寫幾點（「明天晚上」「中午」）時的預設時間
_PERIOD_DEFAULT = {"凌晨": dt.time(1, 0), "上午": dt.time(9, 0), "早上": dt.time(9, 0), "中午": dt.time(12, 0),
                   "下午": dt.time(15, 0), "晚上": dt.time(20, 0), "今晚": dt.time(20, 0)}
_RELATIVE_DAYS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "後天": 2, "大後天": 3}
NO_DATE = "no-date"  # 使用者明確寫了「無日期」


def _quick_date(match, today):
    """把日期片段換算成日期；無效日期（例如 2/30）回傳 None，「無日期」回傳 NO_DATE。"""
    if match.group("rel"):
        return today + dt.timedelta(days=_RELATIVE_DAYS[match.group("rel")])
    if match.group("ndays"):
        days = _cn_number(match.group("ndays"))
        return today + dt.timedelta(days=days) if days else None
    if match.group("nweeks"):
        return today + dt.timedelta(weeks=_cn_number(match.group("nweeks")))
    if match.group("nextday"):
        next_monday = today + dt.timedelta(days=7 - today.weekday())
        return next_monday + dt.timedelta(days=_DAY_CHARS[match.group("nextday")])
    if match.group("thisday"):
        return today + dt.timedelta(days=(_DAY_CHARS[match.group("thisday")] - today.weekday()) % 7)
    if match.group("none"):
        return NO_DATE
    month, day = (match.group("m1"), match.group("d1")) if match.group("m1") else (match.group("m2"), match.group("d2"))
    for year in (today.year, today.year + 1):  # 今年已經過了就是明年
        try:
            candidate = dt.date(year, int(month), int(day))
        except ValueError:
            return None
        if candidate >= today:
            return candidate
    return None


def _quick_time(text):
    """時間片段：15:30（24 小時制照寫）、3點、3點半、下午3點15分、下午三點、凌晨2點；只寫時段（晚上）用預設時間。

    沒寫上午／下午時，1～6 點當成下午（上班族「3點開會」幾乎都是下午），7～12 點照寫。
    「晚上12點」是隔天 0 點、不屬於當天，無法用單一日期表達，不當成時間（留在標題讓使用者看到）。
    """
    match = _QUICK_TIME_RE.match(text)
    if not match:
        return None
    period = match.group("period")
    if match.group("hour") is None:
        return _PERIOD_DEFAULT.get(period)  # 沒有時段也沒有幾點（空字串）時回傳 None
    hour = _cn_number(match.group("hour"))
    minute = 30 if match.group("half") else _cn_number(match.group("minute") or match.group("colon") or "0")
    if hour is None or minute is None:
        return None
    if period in ("晚上", "今晚") and hour == 12:
        return None
    if period in ("下午", "晚上", "今晚") and hour < 12:
        hour += 12
    elif period == "中午" and hour < 6:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0
    elif period is None and match.group("colon") is None and 1 <= hour <= 6:
        hour += 12
    return dt.time(hour, minute) if hour < 24 and minute < 60 else None


def _quick_token(token, today):
    """單一片段裡的日期與時間（可黏在一起，例如「明天下午3點」）；整段都辨識得出來才算，否則回傳 None。"""
    date_match = _QUICK_DATE_RE.match(token)
    due = _quick_date(date_match, today) if date_match else None
    rest = token[date_match.end():] if date_match and due is not None else token
    time_value = _quick_time(rest) if rest else None
    if rest and time_value is None:
        return None
    if due is None and time_value is None:
        return None
    return due, time_value


def parse_quick(text, lists, today, now, default_due=None):
    """把快速列輸入的一行文字辨識成待辦，以空白分段，辨識不出來的片段留在標題。

    回傳 {"title", "due", "time", "recur", "list"}；list 為 None 時由呼叫端用上次的清單。
    - 日期：今天、明天、後天、3天後、三天後、一週後、週五、下週一、10/15、10月15日、無日期
    - 時間：15:30、3點、3點半、下午3點15分、下午三點、凌晨2點（沒寫上午下午時 1～6 點視為下午）；
      只寫時段：早上 9:00、中午 12:00、下午 15:00、晚上 20:00
    - 重複：每天、平日、每週一三五、每月15日、每月最後一天、每年3/15
    - 清單：#清單名稱（開頭相符、不分大小寫）
    沒寫日期時以 default_due 為準（主畫面＝月曆選中的那天；快速列＝今天）：
    只有時間：那天是今天且時間已經過了就改明天；只有重複規則：從那天起第一個符合的日子；什麼都沒寫：那天。
    """
    title, due, due_set, time_value, rule, target = [], None, False, None, None, None
    for token in text.split():
        if token.startswith("#") and len(token) > 1 and target is None:
            name = token[1:].lower()
            found = next((lst for lst in lists if lst.get("title", "").lower() == name), None) or next(
                (lst for lst in lists if lst.get("title", "").lower().startswith(name)), None)
            if found:
                target = found
                continue
        if rule is None and parse_recur(token):
            rule = parse_recur(token)
            continue
        parsed = _quick_token(token, today)
        if parsed and not (parsed[0] is not None and due_set) and not (parsed[1] and time_value):
            if parsed[0] is not None:
                due, due_set = (None if parsed[0] == NO_DATE else parsed[0]), True
            if parsed[1]:
                time_value = parsed[1]
            continue
        title.append(token)
    if not due_set:
        base = default_due or today
        if rule:
            due = next_due(rule, base - dt.timedelta(days=1), today)
        elif time_value and base == today and dt.datetime.combine(today, time_value) <= now:
            due = today + dt.timedelta(days=1)
        else:
            due = base
    if due is None:  # 沒有日期就不能提醒，也不能重複
        time_value, rule = None, None
    return {"title": " ".join(title), "due": due, "time": time_value, "recur": rule, "list": target}


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


def draw_rounded(canvas, w, h, radius, fill, bg, outline=None, line=1, tag="shape", x0=0, y0=0):
    """在 Canvas 畫反鋸齒圓角矩形：四角用 round_image 的圓，中間補矩形；寬度任意，不需重算整張圖。

    @param radius 圓角半徑（實際像素），會限制在高度與寬度的一半以內
    @param outline 外框顏色，None 表示沒有外框
    @param x0 左上角位置（預設畫滿整個 Canvas 的左上角）
    """
    canvas.delete(tag)
    r = max(1, min(radius, h // 2, w // 2))
    d = 2 * r
    img = round_image(canvas, d, fill, bg, outline=outline, width=line if outline else 0)
    # 每個角只貼圓的四分之一：寬度小於兩倍直徑時，整張圓圖會互相蓋住而在邊緣留下底色接縫
    cache = canvas._root().__dict__.setdefault("_corner_cache", {})
    for x, y in ((0, 0), (w - r, 0), (0, h - r), (w - r, h - r)):
        qx, qy = (0 if x == 0 else r), (0 if y == 0 else r)
        key = (str(img), qx, qy)
        if key not in cache:
            corner = tk.PhotoImage(master=canvas._root(), width=r, height=r)
            corner.tk.call(corner, "copy", img, "-from", qx, qy, qx + r, qy + r)
            cache[key] = corner
        canvas.create_image(x0 + x, y0 + y, image=cache[key], anchor="nw", tags=tag)
    boxes = [((r, 0, w - r, h), fill), ((0, r, w, h - r), fill)]  # 蓋掉圓的內側弧線
    if outline:
        boxes += [(box, outline) for box in
                  ((r, 0, w - r, line), (r, h - line, w - r, h), (0, r, line, h - r), (w - line, r, w, h - r))]
    for (left, top, right, bottom), color in boxes:
        canvas.create_rectangle(x0 + left, y0 + top, x0 + right, y0 + bottom, fill=color, outline="", tags=tag)
    canvas.tag_lower(tag)


class Pill(tk.Canvas):
    """膠囊形按鈕（反鋸齒圓角），取代方角的 Label 按鈕。text_value 保留文字，方便測試與重畫。

    style：primary（主要動作，實心強調色）、soft（次要，淡色底）、outline（細框）、ghost（無底色，滑過才顯示）、
    static（只顯示不能點：灰底一般文字色，沒有滑過效果；command 傳 None）
    文字開頭的符號（📅 ⏰ 🔁 📁 ＋ ✓ ←）與結尾的「 ▾」改畫成單色線條圖示，和文字分開垂直置中。
    """

    @staticmethod
    def palette(style):
        """樣式的 (底色, 文字色, 滑過底色, 外框色)；呼叫時才讀取，換風格後新建的按鈕就是新配色。"""
        return {"primary": (ACCENT, ON_ACCENT, ACCENT_HOVER, None),
                "soft": (ACCENT_SOFT, ACCENT, ACCENT_SOFT_HOVER, None),
                "outline": (BG, TEXT, HOVER, BORDER),
                "ghost": (BG, GRAY, HOVER, None),
                "danger": (RED, ON_ACCENT, RED, None),
                "static": (CARD_BG, TEXT, CARD_BG, None)}[style]

    def __init__(self, parent, text, command, style="primary", font=FONT, padx=14, pady=5, bg=None):
        self.fill, self.fg, self.hover_fill, self.outline = self.palette(style)
        self.parent_bg = bg or parent.cget("bg")
        if style == "ghost":
            self.fill = self.parent_bg
        self.text_value, self.font = text, font
        self.parts = icon_parts(text)
        root = parent._root()
        measure = tkfont.Font(root=root, font=font)
        size = abs(measure.actual("size"))
        self.icon_font = (ICON_GLYPH_FONT, max(size - 1, 6))  # 線條圖示佔滿整個字框，略小一級才和中文等高
        self.chevron_font = (ICON_GLYPH_FONT, max(size - 3, 6))
        # [(文字, 字型, 寬度)]：圖示、文字、下拉箭頭依序排開，整組水平置中
        self.pieces = [(t, f, tkfont.Font(root=root, font=f).measure(t))
                       for t, f in zip(self.parts, (self.icon_font, font, self.chevron_font)) if t]
        self.gap = px(6)
        self.content_w = sum(w for _, _, w in self.pieces) + self.gap * (len(self.pieces) - 1)
        self.w = self.content_w + 2 * px(padx)
        self.h = measure.metrics("linespace") + 2 * px(pady)
        super().__init__(parent, width=self.w, height=self.h, bg=self.parent_bg, highlightthickness=0,
                         cursor="hand2" if command else "")
        self._draw(self.fill)
        if command is None:  # 只顯示：沒有滑過變色，也不能點
            return
        self.bind("<Enter>", lambda e: self._draw(self.hover_fill))
        self.bind("<Leave>", lambda e: self._draw(self.fill))
        self.bind("<Button-1>", lambda e: command())

    def _draw(self, fill):
        draw_rounded(self, self.w, self.h, self.h // 2, fill, self.parent_bg, outline=self.outline)
        self.delete("label")
        x, mid = (self.w - self.content_w) / 2, self.h / 2
        for text, font, width in self.pieces:
            self.create_text(x, mid, text=text, fill=self.fg, font=font, anchor="w", tags="label")
            x += width + self.gap


class ThinScrollbar(tk.Canvas):
    """細捲軸：取代 Windows 預設的灰色捲軸，用畫面配色畫成圓角細條；內容沒超出時不顯示。

    介面比照 ttk.Scrollbar：command 接 canvas.yview，set(first, last) 由 yscrollcommand 呼叫。
    """

    WIDTH = 6

    def __init__(self, parent, command, bg=None):
        bg = bg or BG
        super().__init__(parent, width=px(self.WIDTH), bg=bg, highlightthickness=0)
        self.command, self.bg = command, bg
        self.first, self.last = 0.0, 1.0
        self.color = BORDER
        self._drag = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._paint(GRAY))
        self.bind("<Leave>", lambda e: self._paint(BORDER))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def is_needed(self):
        """內容超出可視範圍時才需要捲軸。"""
        return self.first > 0.0 or self.last < 1.0

    def _paint(self, color):
        self.color = color
        self._draw()

    def _thumb(self):
        height = self.winfo_height()
        top = round(self.first * height)
        return top, max(round(self.last * height), top + px(24))  # 至少 24px，太短不好拖

    def _draw(self):
        self.delete("all")
        if not self.is_needed():
            return
        top, bottom = self._thumb()
        w = px(self.WIDTH)
        draw_rounded(self, w, bottom - top, w // 2, self.color, self.bg, tag="thumb", y0=top)

    def _press(self, event):
        if not self.is_needed():
            return
        top, bottom = self._thumb()
        if top <= event.y <= bottom:
            self._drag = (event.y, self.first)
        else:  # 點在軌道上：往該方向翻一頁
            self.command("scroll", -1 if event.y < top else 1, "pages")

    def _move(self, event):
        if self._drag:
            start_y, start_first = self._drag
            self.command("moveto", start_first + (event.y - start_y) / max(self.winfo_height(), 1))


class PopupMenu:
    """圓角選單：取代 Windows 原生選單（原生選單的形狀改不了）。介面比照 tk.Menu 用到的部分：

    add_command／add_radiobutton／add_cascade／add_separator 建立項目；invoke／entrycget／entryconfig 以標籤
    指定項目；tk_popup(x, y) 在螢幕座標顯示。滑過的項目畫圓角底色；點項目、按 Esc、點到選單外面都會收起。
    鍵盤：↑ ↓ 移動、Enter 執行、→ 打開子選單、← 回上一層。
    """

    ROW_H = 30

    def __init__(self, master):
        self.master = master
        self.items = []            # 每項：dict(kind, label, command, state, value, variable, menu, checked)
        self.win = None
        self.parent = None         # 子選單的上一層
        self.child = None          # 目前打開的子選單
        self.rows = []             # 顯示中的 (項目, Canvas)
        self.active = None         # 鍵盤或滑鼠目前所在的列（rows 的索引）

    # ---- 建立項目（比照 tk.Menu）
    def add_command(self, label, command=None, state="normal", checked=False):
        self.items.append({"kind": "command", "label": label, "command": command, "state": state,
                           "checked": checked})

    def add_radiobutton(self, label, value, variable, command=None, state="normal"):
        self.items.append({"kind": "radio", "label": label, "value": value, "variable": variable,
                           "command": command, "state": state})

    def add_cascade(self, label, menu, state="normal"):
        menu.parent = self
        self.items.append({"kind": "cascade", "label": label, "menu": menu, "state": state})

    def add_separator(self):
        self.items.append({"kind": "separator", "label": None})

    def _item(self, label):
        item = next((i for i in self.items if i["label"] == label), None)
        if item is None:
            raise tk.TclError(f"選單沒有「{label}」")
        return item

    def entrycget(self, label, option):
        return self._item(label)[option]

    def entryconfig(self, label, **options):
        self._item(label).update(options)

    def invoke(self, label):
        """執行項目（停用的不執行）；radiobutton 先設定變數再呼叫 command。"""
        item = self._item(label)
        if item.get("state") == "disabled" or item["kind"] in ("separator", "cascade"):
            return
        if item["kind"] == "radio":
            item["variable"].set(item["value"])
        if item.get("command"):
            item["command"]()

    # ---- 顯示
    def tk_popup(self, x, y, align="left", flip_y=None):
        """在螢幕座標 (x, y) 顯示。align="right"：x 是選單右緣；flip_y：下方放不下時改成底緣對齊這個 y（往上開）。"""
        self.close()
        win = self.win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        body = tk.Frame(win, bg=CARD_BG, padx=px(4), pady=px(4))
        body.pack()
        font = tkfont.Font(root=win, font=FONT)
        marks = any(i["kind"] == "radio" or i.get("checked") for i in self.items)
        arrows = any(i["kind"] == "cascade" for i in self.items)
        self.mark_w = px(22) if marks else 0
        text_w = max([font.measure(i["label"]) for i in self.items if i["label"]] + [px(80)])
        self.width = px(12) + self.mark_w + text_w + (px(26) if arrows else 0) + px(16)
        self.rows, self.active = [], None
        for item in self.items:
            if item["kind"] == "separator":
                tk.Frame(body, bg=BORDER, height=1).pack(fill="x", padx=px(10), pady=px(4))
                continue
            row = tk.Canvas(body, width=self.width, height=px(self.ROW_H), bg=CARD_BG, highlightthickness=0,
                            cursor="" if item.get("state") == "disabled" else "hand2")
            row.pack()
            index = len(self.rows)
            self.rows.append((item, row))
            self._draw_row(index, False)
            row.bind("<Enter>", lambda e, i=index: self._hover(i))
            row.bind("<Button-1>", lambda e, i=index: self._click(i))
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        if align == "right":
            x -= w
        screen = win_virtual_screen()
        if screen and flip_y is not None and y + h > screen[3]:
            y = flip_y - h
        if screen:  # 超出螢幕就往內收（右邊放不下的子選單改到左邊）
            if x + w > screen[2]:
                x = (self.parent.win.winfo_rootx() - w + px(4)) if self.parent and self.parent.win else screen[2] - w
            x, y = max(x, screen[0]), min(max(y, screen[1]), screen[3] - h)
        win.geometry(f"+{x}+{y}")
        win_style_popup(win)
        for sequence, handler in (("<Escape>", lambda e: self._back()), ("<Up>", lambda e: self._step(-1)),
                                  ("<Down>", lambda e: self._step(1)), ("<Return>", lambda e: self._enter()),
                                  ("<Right>", lambda e: self._open_child_keyboard()),
                                  ("<Left>", lambda e: self._back() if self.parent else None)):
            win.bind(sequence, handler)
        win.bind("<FocusOut>", lambda e: win.after(80, self._close_if_unfocused))
        win.focus_force()

    def _draw_row(self, index, hover):
        item, row = self.rows[index]
        row.delete("all")
        disabled = item.get("state") == "disabled"
        h = px(self.ROW_H)
        if hover and not disabled:  # 滑過：圓角底色
            draw_rounded(row, self.width - px(4), h - px(2), px(8), ACCENT_SOFT, CARD_BG, tag="hover",
                         x0=px(2), y0=px(1))
        fg = FADED_TEXT if disabled else TEXT
        checked = (item["kind"] == "radio" and item["variable"].get() == item["value"]) or item.get("checked")
        if checked:
            mark, font = (GLYPHS["✓"], (ICON_GLYPH_FONT, 9)) if ICON_GLYPH_FONT else ("✓", FONT)
            row.create_text(px(12) + self.mark_w // 2, h // 2, text=mark, fill=ACCENT, font=font)
        row.create_text(px(12) + self.mark_w, h // 2, text=item["label"], fill=fg, font=FONT, anchor="w")
        if item["kind"] == "cascade":
            arrow, font = (GLYPHS["▶"], (ICON_GLYPH_FONT, 8)) if ICON_GLYPH_FONT else ("▶", SMALL)
            row.create_text(self.width - px(16), h // 2, text=arrow, fill=GRAY, font=font)

    def _hover(self, index):
        if self.active is not None and self.active != index and self.active < len(self.rows):
            self._draw_row(self.active, False)
        self.active = index
        self._draw_row(index, True)
        item = self.rows[index][0]
        if self.child and (item["kind"] != "cascade" or item["menu"] is not self.child):
            self.child.close()
            self.child = None
        if item["kind"] == "cascade" and item.get("state") != "disabled" and not self.child:
            self._open_child(index, focus=False)

    def _click(self, index):
        item = self.rows[index][0]
        if item.get("state") == "disabled":
            return
        if item["kind"] == "cascade":
            self._open_child(index, focus=True)
            return
        self.root_menu().close()  # 先收起整串選單再執行（執行的動作可能會重建畫面）
        self.invoke(item["label"])

    def _open_child(self, index, focus):
        item, row = self.rows[index]
        self.child = item["menu"]
        self.child.tk_popup(self.win.winfo_rootx() + self.win.winfo_width() - px(4), row.winfo_rooty() - px(5))
        if not focus:
            self.win.focus_force()  # 滑鼠帶出的子選單：焦點留在本層，鍵盤操作不被搶走

    def _open_child_keyboard(self):
        if self.active is not None and self.rows[self.active][0]["kind"] == "cascade":
            self._open_child(self.active, focus=True)
            self.child._step(1)

    def _step(self, delta):
        enabled = [i for i, (item, _) in enumerate(self.rows) if item.get("state") != "disabled"]
        if not enabled:
            return
        if self.active not in enabled:
            target = enabled[0] if delta > 0 else enabled[-1]
        else:
            target = enabled[(enabled.index(self.active) + delta) % len(enabled)]
        self._hover(target)

    def _enter(self):
        if self.active is not None:
            self._click(self.active)

    def _back(self):
        """Esc／←：子選單回到上一層，最上層則整個收起。"""
        parent = self.parent
        self.close()
        if parent and parent.win:
            parent.child = None
            parent.win.focus_force()

    def root_menu(self):
        menu = self
        while menu.parent and menu.parent.win:
            menu = menu.parent
        return menu

    def _close_if_unfocused(self):
        """焦點離開整串選單（點到外面、切到別的程式）就全部收起。"""
        root = self.root_menu()
        try:
            focus = str(self.master.focus_get() or "")
        except (KeyError, tk.TclError):
            focus = ""
        menu, inside = root, False
        while menu:
            if menu.win and focus.startswith(str(menu.win)):
                inside = True
            menu = menu.child
        if not inside:
            root.close()

    def is_open(self):
        return bool(self.win) and self.win.winfo_exists()

    def close(self):
        """收起這一層與它打開的子選單。"""
        if self.child:
            self.child.close()
            self.child = None
        if self.win is not None:
            if self.win.winfo_exists():
                self.win.destroy()
            self.win = None
        self.rows, self.active = [], None


class RoundedBox(tk.Canvas):
    """圓角外框容器：內容放在 self.inner（Frame），高度跟著內容、寬度跟著版面；可切換外框顏色（例如取得焦點時）。

    stretch=True：外框被版面撐高時（pack expand），內容也跟著撐高，例如填滿頁面剩餘空間的多行輸入框。
    """

    def __init__(self, parent, fill=None, outline=None, radius=16, padx=8, pady=4, bg=None, stretch=False):
        fill, outline = fill or BG, outline or BORDER
        self.fill, self.outline, self.radius, self.stretch = fill, outline, px(radius), stretch
        self.parent_bg = bg or parent.cget("bg")
        self.padx, self.pady = px(padx), px(pady)
        super().__init__(parent, bg=self.parent_bg, highlightthickness=0, height=px(40))
        self.inner = tk.Frame(self, bg=fill)
        self._window = self.create_window(self.padx, self.pady, window=self.inner, anchor="nw")
        self.bind("<Configure>", lambda e: self._layout())
        self.inner.bind("<Configure>", lambda e: self._fit_height())

    def _fit_height(self):
        height = self.inner.winfo_reqheight() + 2 * self.pady
        if int(self.cget("height")) != height:
            self.config(height=height)

    def _layout(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.itemconfigure(self._window, width=w - 2 * self.padx)
        if self.stretch:  # 要求高度仍由內容決定（_fit_height），這裡只把內容撐到外框實際高度
            self.itemconfigure(self._window, height=max(h - 2 * self.pady, 1))
        draw_rounded(self, w, h, self.radius, self.fill, self.parent_bg, outline=self.outline)

    def set_outline(self, color):
        self.outline = color
        self._layout()


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


def completed_task(raw, lst):
    """Google 上已完成的任務轉成「已完成」區塊用的 dict；completed 為本機完成時間（API 沒給時為 None）。"""
    time_value, rule, notes = split_meta(raw.get("notes", ""))
    stamp = raw.get("completed")
    completed = (dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                 if isinstance(stamp, str) and stamp else None)
    return {"id": raw["id"], "title": raw.get("title") or "（無標題）", "list_id": lst["id"],
            "list_title": lst.get("title", ""), "due": task_due(raw), "parent": raw.get("parent"),
            "time": time_value, "recur": rule, "notes": notes, "position": raw.get("position", ""),
            "completed": completed}


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
        tk.Label(tip, text=text, bg=TEXT, fg=ON_ACCENT, font=SMALL, padx=px(6), pady=px(2)).pack()
        tip.update_idletasks()
        x, y = self._position(tip)
        screen = win_virtual_screen()
        if screen:  # 靠近螢幕右緣（例如 ✕ 按鈕）時往內縮，避免提示被切掉
            x = min(max(x, screen[0]), screen[2] - tip.winfo_reqwidth())
        tip.geometry(f"+{x}+{y}")
        self.tip = tip

    def _position(self, tip):
        """提示的左上角（螢幕座標）：元件正下方置中。"""
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() - tip.winfo_reqwidth()) // 2
        return x, self.widget.winfo_rooty() + self.widget.winfo_height() + px(4)

    def _hide(self, _=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class CellTooltip(Tooltip):
    """Canvas 上各格子各自的提示（例如月曆日期）：滑鼠移到另一格就重新計時，顯示在該格正下方。

    cell_at(x, y) 回傳格子（None＝不在格子上）；text_of(格子) 回傳說明（空字串＝這格不顯示）；
    rect_of(格子) 回傳格子在 Canvas 內的 (左, 上, 右, 下)。
    """

    def __init__(self, widget, cell_at, text_of, rect_of):
        super().__init__(widget, lambda: text_of(self.cell))
        self.cell_at, self.text_of, self.rect_of = cell_at, text_of, rect_of
        self.cell = None
        widget.unbind("<Enter>")  # 改由 <Motion> 依格子觸發
        widget.bind("<Motion>", self._on_motion, add="+")

    def _on_motion(self, event):
        cell = self.cell_at(event.x, event.y)
        if cell == self.cell and (self.tip or self.job):
            return
        self._hide()
        self.cell = cell
        if cell is not None and self.text_of(cell):
            self._schedule()

    def _hide(self, _=None):
        super()._hide()
        if _ is not None:  # 滑鼠離開或按下：下次移動要重新計時
            self.cell = None

    def _position(self, tip):
        left, _, right, bottom = self.rect_of(self.cell)
        x = self.widget.winfo_rootx() + round((left + right) / 2) - tip.winfo_reqwidth() // 2
        return x, self.widget.winfo_rooty() + round(bottom) + px(2)


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
        win_style_popup(self.win)  # 和主視窗一樣的系統圓角與暖色外框
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

    def _chip(self, parent, text, command, style="soft"):
        """選擇器裡的小膠囊按鈕（快捷日期、確定、不設時間等）。style 同 Pill：soft／primary／ghost／outline。"""
        return Pill(parent, text, command, style=style, font=SMALL, padx=9, pady=2, bg=CARD_BG)

    def _field(self, parent, chars):
        """選擇器裡的圓角輸入框（寬 chars 個字），取得焦點時外框變成強調色。回傳 (外框, Entry)。"""
        box = RoundedBox(parent, fill=BG, outline=BORDER, radius=12, padx=8, pady=3, bg=CARD_BG)
        entry = tk.Entry(box.inner, width=chars, font=FONT, relief="flat", bg=BG, fg=TEXT,
                         insertbackground=TEXT, highlightthickness=0)
        entry.pack(fill="x")
        box.config(width=entry.winfo_reqwidth() + px(16))
        entry.bind("<FocusIn>", lambda e: box.set_outline(ACCENT), add="+")
        entry.bind("<FocusOut>", lambda e: box.set_outline(BORDER), add="+")
        return box, entry

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
        quick.pack(fill="x", pady=(0, px(6)))
        next_monday = today + dt.timedelta(days=7 - today.weekday())
        options = [("今天", today), ("明天", today + dt.timedelta(days=1)), ("下週一", next_monday)]
        for text, day in options:
            self._chip(quick, text, lambda d=day: self.pick(d)).pack(side="left", padx=(0, px(4)))
        if self.allow_none:
            self._chip(quick, "無日期", lambda: self.pick(None), style="ghost").pack(side="left")

        head = tk.Frame(self.body, bg=CARD_BG)
        head.pack(fill="x")
        year, month = self.view
        self._chip(head, "◀", lambda: self._shift(-1), style="ghost").pack(side="left")
        self._chip(head, "▶", lambda: self._shift(1), style="ghost").pack(side="right")
        tk.Label(head, text=f"{year} 年 {month} 月", bg=CARD_BG, fg=TEXT, font=FONT).pack(side="left", expand=True)

        grid = tk.Frame(self.body, bg=CARD_BG)
        grid.pack()
        for col, name in enumerate(WEEK_HEAD):
            tk.Label(grid, text=name, bg=CARD_BG, fg=GRAY, font=SMALL, width=3).grid(row=0, column=col)
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
        size = px(26)  # 每格一張反鋸齒圓形圖：選中＝實心強調色、今天＝強調色細圈、其他＝透明（與底色同色）
        for row, week in enumerate(weeks, 1):
            for col, day in enumerate(week):
                fg = TEXT if day.month == month else FADED_TEXT
                if day == self.current:
                    fg, image = ON_ACCENT, round_image(grid, size, ACCENT, CARD_BG)
                elif day == today:
                    fg, image = ACCENT, round_image(grid, size, CARD_BG, CARD_BG, outline=ACCENT, width=px(1))
                else:
                    image = round_image(grid, size, CARD_BG, CARD_BG)
                lbl = tk.Label(grid, text=str(day.day), image=image, compound="center", bg=CARD_BG, fg=fg,
                               font=SMALL, padx=px(2), pady=px(1), bd=0, cursor="hand2")
                lbl.grid(row=row, column=col)
                lbl.bind("<Button-1>", lambda e, d=day: self.pick(d))

    def _shift(self, delta):
        year, month = self.view
        index = year * 12 + (month - 1) + delta
        self.view = (index // 12, index % 12 + 1)
        self._render()



class TimePicker(_Popup):
    """提醒時間選擇器：自訂（HH:MM，Enter 確認）＋不設時間。不提供預設時間（使用者要求）。"""

    def __init__(self, app, anchor, current, on_pick):
        self.on_pick = on_pick
        self.current = current
        super().__init__(app, anchor)
        self.custom.focus_set()  # 打開就能直接輸入時間

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        tk.Label(self.body, text="提醒時間（選填）", bg=CARD_BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x")
        row = tk.Frame(self.body, bg=CARD_BG)
        row.pack(fill="x", pady=(px(6), px(4)))
        tk.Label(row, text="時間", bg=CARD_BG, fg=GRAY, font=SMALL).pack(side="left")
        box, self.custom = self._field(row, 6)
        self.custom.insert(0, time_text(self.current) or "")
        box.pack(side="left", padx=px(6))
        self.custom.bind("<Return>", lambda e: self._pick_custom())
        self._chip(row, "確定", self._pick_custom, style="primary").pack(side="left")
        # 一打開就說明輸入方式；輸入錯誤時同一行改成紅字
        self.hint = tk.Label(self.body, text=self.HINT, bg=CARD_BG, fg=GRAY, font=SMALL, anchor="w")
        self.hint.pack(fill="x", pady=(0, px(6)))
        self._chip(self.body, "不設時間", lambda: self.pick(None), style="ghost").pack(anchor="w")

    HINT = "例如 11、15:30、下午3點，按 Enter 確定"

    def _pick_custom(self):
        value = parse_time_text(self.custom.get())
        if value is None:
            self.hint.config(text=f"看不懂這個時間。{self.HINT}", fg=RED)
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
        # 常用規則排成膠囊：目前的規則實心強調色，其他為細框
        options = tk.Frame(self.body, bg=CARD_BG)
        options.pack(fill="x", pady=(px(4), 0))
        for rule in self._options():
            style = "primary" if rule == self.current else "outline"
            Pill(options, format_recur(rule), lambda r=rule: self.pick(r), style=style, font=FONT,
                 padx=12, pady=3, bg=CARD_BG).pack(anchor="w", pady=px(2))
        row = tk.Frame(self.body, bg=CARD_BG)
        row.pack(fill="x", pady=(px(6), px(2)))
        tk.Label(row, text="自訂", bg=CARD_BG, fg=GRAY, font=SMALL).pack(side="left")
        box, self.custom = self._field(row, 12)
        self.custom.insert(0, format_recur(self.current) if self.current else "")
        box.pack(side="left", padx=px(6))
        self.custom.bind("<Return>", lambda e: self._pick_custom())
        self._chip(row, "確定", self._pick_custom, style="primary").pack(side="left")
        self.hint = tk.Label(self.body, text="", bg=CARD_BG, fg=RED, font=SMALL, anchor="w", justify="left")
        self.hint.pack(fill="x")
        self._chip(self.body, "不重複", lambda: self.pick(None), style="ghost").pack(anchor="w")

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
        tk.Label(body, text=task["title"], bg=BG, fg=TEXT, font=(UI_FONT, 12, "bold"),
                 anchor="w", justify="left", wraplength=px(self.WIDTH - 40)).pack(fill="x", pady=(px(2), px(10)))

        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x")
        # 膠囊按鈕和主視窗一致；延後按鈕寫明「再提醒」，第一次看到的人不用猜
        # 最常用的 10 分鐘一鍵完成，其他時間（30 分鐘、1 小時、明天早上）放在旁邊的 ▾ 選單，比照 Windows 通知
        done = Pill(buttons, "✓ 完成", lambda: (self.close(), on_done()), style="primary", padx=12, pady=3)
        done.pack(side="right")
        self.on_snooze = on_snooze
        default = snooze_options(dt.datetime.now())[0][0]
        more = Pill(buttons, "▾", self._open_snooze_menu, style="outline", padx=8, pady=3)
        more.pack(side="right", padx=(0, px(8)))
        Tooltip(more, "其他延後時間")
        self.btn_more_snooze = more
        snooze = Pill(buttons, f"{default}再提醒", lambda: self.snooze(0), style="outline", padx=12, pady=3)
        snooze.pack(side="right", padx=(0, px(4)))
        self.snooze_menu = PopupMenu(win)
        for index, (label, _) in enumerate(snooze_options(dt.datetime.now())[1:], start=1):
            self.snooze_menu.add_command(label=f"{label}再提醒", command=lambda i=index: self.snooze(i))

        ReminderToast.active.append(self)
        ReminderToast.relayout()
        win_style_popup(win)
        try:
            winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except RuntimeError:
            pass  # 沒有音效裝置時靜音

    def snooze(self, index):
        """依 snooze_options 的第 index 個選項延後；時間在按下當下才計算。"""
        label, fire_at = snooze_options(dt.datetime.now())[index]
        self.close()
        self.on_snooze(label, fire_at)

    def _open_snooze_menu(self):
        # 提醒在螢幕右下角：選單右緣對齊按鈕，下方放不下就往上開
        btn = self.btn_more_snooze
        self.snooze_menu.tk_popup(btn.winfo_rootx() + btn.winfo_width(), btn.winfo_rooty() + btn.winfo_height() + px(4),
                                  align="right", flip_y=btn.winfo_rooty() - px(4))

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


class QuickBar:
    """快速列（Ctrl + Alt + D）：螢幕中央的一條輸入列，不用打開主視窗就能新增、完成、搜尋待辦。

    - 空白時：列出逾期與今天的待辦，↑↓ 選擇、Space 完成、Enter 開啟詳細頁
    - 打字時：即時辨識日期、時間、重複、#清單（parse_quick），Enter 新增
    - 以 / 開頭：搜尋所有未完成的待辦
    - Tab 開啟主視窗；Esc 或點到其他地方就關閉
    """

    WIDTH = 520
    MAX_ROWS = 8
    EXAMPLE = "例如：明天下午3點 跟客戶開會 每週二 #工作　·　/ 開頭可搜尋"
    KEYS = "Enter 新增或開啟 · ↑↓ 選擇 · Space 完成 · Tab 主視窗 · Esc 關閉"

    def __init__(self, app):
        self.app = app
        self.items, self.selected, self.parsed, self.busy = [], 0, None, False
        win = self.win = tk.Toplevel(app)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        body = tk.Frame(win, bg=BG, padx=px(16), pady=px(12))
        body.pack(fill="both", expand=True)

        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="＋", bg=BG, fg=ACCENT, font=(UI_FONT, 15)).pack(side="left")
        self.text = tk.StringVar(win)
        self.entry = tk.Entry(row, textvariable=self.text, font=(UI_FONT, 14), relief="flat",
                              bg=BG, fg=TEXT, insertbackground=TEXT, highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, padx=(px(8), 0), ipady=px(4))
        self.chips = tk.Label(body, bg=BG, font=FONT, anchor="w", justify="left")
        self.chips.pack(fill="x", pady=(px(4), 0))
        tk.Frame(body, bg=DIVIDER, height=1).pack(fill="x", pady=px(8))
        self.header = tk.Label(body, bg=BG, fg=GRAY, font=SMALL, anchor="w")
        self.header.pack(fill="x")
        self.rows = tk.Frame(body, bg=BG)
        self.rows.pack(fill="x")
        # 狀態文字（錯誤、新增中…）有內容才顯示，平常不佔一行空白；由 _set_status 決定顯示與否
        self.status = tk.Label(body, bg=BG, fg=GRAY, font=SMALL, anchor="w", justify="left")
        self.keys = tk.Label(body, text=self.KEYS, bg=BG, fg=GRAY, font=SMALL, anchor="w")
        self.keys.pack(fill="x", pady=(px(4), 0))

        self.text.trace_add("write", lambda *_: self._on_change())
        self.entry.bind("<Return>", lambda e: self.submit())
        self.entry.bind("<Escape>", lambda e: self.close())
        self.entry.bind("<Tab>", lambda e: (self.open_main(), "break")[1])  # 不讓 Tab 移動焦點
        self.entry.bind("<Up>", lambda e: (self.move(-1), "break")[1])
        self.entry.bind("<Down>", lambda e: (self.move(1), "break")[1])
        self.entry.bind("<space>", self._on_space)
        # 焦點離開整個快速列才關閉（只在列內元件間移動不算）
        win.bind("<FocusOut>", lambda e: win.after_idle(self._close_if_unfocused))

        self.render()
        self._place()
        win_style_popup(win)
        win.lift()
        win.focus_force()
        self.entry.focus_force()

    # ---- 視窗
    def is_open(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if self.is_open():
            self.win.destroy()

    def _close_if_unfocused(self):
        if not self.is_open():
            return
        try:
            focus = self.win.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if focus is None or not str(focus).startswith(str(self.win)):
            self.close()

    def _place(self):
        """放在主螢幕可用範圍的水平中央、上方約四分之一處（比照 Spotlight）。"""
        self.win.update_idletasks()
        area = win_work_area() or (0, 0, self.win.winfo_screenwidth(), self.win.winfo_screenheight())
        self.x = area[0] + (area[2] - area[0] - px(self.WIDTH)) // 2
        self.y = area[1] + (area[3] - area[1]) // 4
        self._resize()

    def _resize(self):
        self.win.update_idletasks()
        if hasattr(self, "x"):
            self.win.geometry(f"{px(self.WIDTH)}x{self.win.winfo_reqheight()}+{self.x}+{self.y}")

    # ---- 內容
    def mode(self):
        text = self.text.get()
        if not text.strip():
            return "agenda"
        return "search" if text.lstrip().startswith("/") else "add"

    def _set_status(self, text, fg=GRAY):
        """顯示狀態文字；空字串時收起整行，視窗高度跟著調整。"""
        self.status.config(text=text, fg=fg)
        if text:
            self.status.pack(fill="x", pady=(px(4), 0), before=self.keys)
        else:
            self.status.pack_forget()
        self._resize()

    def _on_change(self):
        self.selected = 0
        self._set_status("", GRAY)
        self.render()

    def render(self):
        app, mode = self.app, self.mode()
        today = dt.date.today()
        if mode == "add":
            self.parsed = parse_quick(self.text.get(), app.lists, today, dt.datetime.now())
            self.chips.config(text=self._chips_text(self.parsed), fg=ACCENT)
            self.items = []
            # 底部快捷鍵說明已寫「Enter 新增」，有標題時不再重複；只在缺標題時提醒
            self.header.config(text="" if self.parsed["title"] else "還沒有標題，請輸入工作內容")
        else:
            self.parsed = None
            self.chips.config(text=self.EXAMPLE, fg=GRAY)
            if not app.g.refresh_token:
                self.items = []
                self.header.config(text="尚未登入 Google：按 Tab 開啟主視窗登入")
            elif mode == "search":
                query = self.text.get().lstrip()[1:].strip().lower()
                found = [t for t in app.tasks if query and query in t["title"].lower()]
                self.items = sorted(found, key=lambda t: (t["due"] is None, t["due"] or today, app._order_key(t)))
                self.header.config(text=f"找到 {len(self.items)} 件" if query else "輸入要找的文字")
            else:
                self.items = app.today_agenda()
                # 逾期分開算，避免把以前沒做完的也當成「今天」的件數
                overdue = sum(1 for t in self.items if t["due"] and t["due"] < today)
                left = len(self.items) - overdue
                self.header.config(text=(f"今天還有 {left} 件" + (f" · 逾期 {overdue} 件" if overdue else ""))
                                   if self.items else "今天的待辦都完成了")
        if self.header.cget("text"):  # 沒有內容的標題列收起來，不留空白行
            self.header.pack(fill="x", before=self.rows)
        else:
            self.header.pack_forget()
        self.items = self.items[:self.MAX_ROWS]
        self.selected = min(self.selected, max(len(self.items) - 1, 0))
        self._render_rows(today)
        self._resize()

    def _chips_text(self, parsed):
        lst = parsed["list"] or self.app.current_list()
        parts = [f"📅 {date_text(parsed['due'])}"]
        if parsed["time"]:
            parts.append(f"⏰ {time_text(parsed['time'])}")
        if parsed["recur"]:
            parts.append(f"🔁 {format_recur(parsed['recur'])}")
        if lst:
            parts.append(f"📁 {lst.get('title', '')}")
        return "　".join(parts)

    def _render_rows(self, today):
        for w in self.rows.winfo_children():
            w.destroy()
        if not self.items:
            self.rows.config(height=1)  # Tk 的 Frame 清空後會維持原本高度，要明確縮回去
        for i, task in enumerate(self.items):
            bg = SELECTED if i == self.selected else BG
            row = tk.Frame(self.rows, bg=bg, padx=px(6), pady=px(3), cursor="hand2")
            row.pack(fill="x")
            circle = tk.Label(row, text="○", bg=bg, fg=GRAY, font=FONT, cursor="hand2")
            circle.pack(side="left")
            circle.bind("<Button-1>", lambda e, t=task: self.complete(t))
            title = tk.Label(row, text=task["title"], bg=bg, fg=TEXT, font=FONT, anchor="w")
            title.pack(side="left", fill="x", expand=True, padx=(px(6), 0))
            when, color = self._when(task, today)
            tk.Label(row, text=when, bg=bg, fg=color, font=SMALL).pack(side="right")
            for widget in (row, title):
                widget.bind("<Button-1>", lambda e, t=task: self.open_main(t))

    @staticmethod
    def _when(task, today):
        due, at = task["due"], time_text(task.get("time"))
        if due is not None and due < today:
            return f"已逾期 {due.month}/{due.day}", RED
        if due == today:
            return at, RED if is_late(task, dt.datetime.now()) else ACCENT  # 時間過了還沒做：和逾期同色
        return (f"{date_text(due, short=True)} {at}".strip(), GRAY)

    # ---- 操作
    def move(self, delta):
        if self.items:
            self.selected = (self.selected + delta) % len(self.items)
            self._render_rows(dt.date.today())

    def _on_space(self, _):
        if self.text.get():
            return None  # 輸入中：照常打空白
        if self.items:
            self.complete(self.items[self.selected])
        return "break"

    def submit(self):
        if self.mode() == "add":
            self.add()
        elif self.items:
            self.open_main(self.items[self.selected])

    def add(self):
        if self.busy:
            return
        parsed = self.parsed
        if not parsed["title"]:
            self._set_status("請輸入工作內容（目前只有日期或時間）", RED)
            return
        lst = parsed["list"] or self.app.current_list()
        if lst is None:
            self._set_status("請先登入 Google：按 Tab 開啟主視窗登入", RED)
            return
        self.busy = True
        self._set_status("新增中…", GRAY)

        def done(task, err):
            self.busy = False
            if err:
                if self.is_open():  # 快速列還開著就在這裡顯示，主視窗可能是隱藏的
                    self._set_status(str(err), RED)
                else:
                    self.app._show_error(err)
                return
            self.app.redraw()
            self.app._set_status(f"已新增「{task['title']}」到「{lst.get('title', '')}」")
            self.close()
        self.app._create_task(parsed["title"], parsed["due"], parsed["time"], parsed["recur"], lst, done)

    def complete(self, task):
        self.app.complete(task)
        self.render()
        self._set_status(f"已完成「{task['title']}」（可在主視窗按「復原」）", GRAY)

    def open_main(self, task=None):
        self.close()
        self.app.show_main(task)


def themed_dialog(parent, title, message, buttons, default, cancel, icon=None):
    """跟著風格的圓角對話框（取代 Windows 原生 messagebox：深色風格下不會跳出白色系統視窗）。

    @param parent 置中的視窗；None 表示主視窗
    @param buttons [(文字, 回傳值, Pill 樣式)]，由左到右排列；主要按鈕放最右邊
    @param default 按 Enter 的回傳值
    @param cancel 按 Esc 的回傳值
    @param icon "warning" 時標題用紅色
    @return 按下的按鈕的回傳值
    """
    parent = parent or tk._default_root
    win = tk.Toplevel(parent)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
    body = tk.Frame(win, bg=BG, padx=px(22), pady=px(18))
    body.pack(fill="both", expand=True)
    tk.Label(body, text=title, bg=BG, fg=RED if icon == "warning" else TEXT, font=HEADING_FONT,
             anchor="w").pack(fill="x")
    tk.Label(body, text=message, bg=BG, fg=TEXT, font=FONT, anchor="w", justify="left",
             wraplength=px(300)).pack(fill="x", pady=(px(8), px(16)))
    row = tk.Frame(body, bg=BG)
    row.pack(fill="x")
    result = {"value": cancel}

    def choose(value):
        result["value"] = value
        win.destroy()
    for text, value, style in reversed(buttons):  # 由右往左 pack，主要按鈕在最右邊
        Pill(row, text, lambda v=value: choose(v), style=style, padx=14, pady=4).pack(side="right", padx=(px(6), 0))
    win.bind("<Return>", lambda e: choose(default))
    win.bind("<Escape>", lambda e: choose(cancel))

    win.update_idletasks()  # 置中在主視窗上（主視窗隱藏時置中在螢幕）
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    if parent.winfo_ismapped():
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    else:
        x, y = (win.winfo_screenwidth() - w) // 2, (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{x}+{y}")
    win_style_popup(win)
    parent._active_dialog = win  # 測試用：找到目前開著的對話框
    win.focus_force()
    win.grab_set()
    parent.wait_window(win)
    return result["value"]


class ThemedMessageBox:
    """介面相容 tkinter.messagebox 用到的部分，改用 themed_dialog；按鈕文字可依情境指定（例如「刪除」「取消」）。"""

    @staticmethod
    def askyesno(title, message, parent=None, icon=None, yes="是", no="否"):
        danger = "danger" if icon == "warning" else "primary"  # 刪除、放棄、登出這類動作用紅色按鈕
        return themed_dialog(parent, title, message, [(no, False, "outline"), (yes, True, danger)],
                             default=True, cancel=False, icon=icon)

    @staticmethod
    def askyesnocancel(title, message, parent=None, icon=None, yes="是", no="否"):
        return themed_dialog(parent, title, message,
                             [("取消", None, "ghost"), (no, False, "outline"), (yes, True, "primary")],
                             default=True, cancel=None, icon=icon)

    @staticmethod
    def showerror(title, message, parent=None):
        themed_dialog(parent, title, message, [("知道了", None, "primary")], default=None, cancel=None,
                      icon="warning")


messagebox = ThemedMessageBox()  # 程式中的 messagebox.askyesno 等都改用圓角對話框


def ask_text(parent, title, prompt, ok_text="建立"):
    """中文按鈕的單行輸入框（tkinter 內建 simpledialog 的按鈕固定是英文），外觀和其他對話框一致。取消回傳 None。"""
    win = tk.Toplevel(parent)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
    body = tk.Frame(win, bg=BG, padx=px(22), pady=px(18))
    body.pack(fill="both", expand=True)
    tk.Label(body, text=title, bg=BG, fg=TEXT, font=HEADING_FONT, anchor="w").pack(fill="x")
    tk.Label(body, text=prompt, bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x", pady=(px(8), px(2)))
    box = RoundedBox(body, fill=CARD_BG, outline=ACCENT, radius=12, padx=10, pady=5)
    box.pack(fill="x", pady=(0, px(16)))
    entry = tk.Entry(box.inner, font=FONT, width=26, relief="flat", bg=CARD_BG, fg=TEXT, insertbackground=TEXT,
                     highlightthickness=0)
    entry.pack(fill="x")
    box.config(width=entry.winfo_reqwidth() + px(20))  # Canvas 預設會要求很寬，改成跟輸入框一樣寬
    result = {"value": None}

    def ok(_=None):
        result["value"] = entry.get()
        win.destroy()

    row = tk.Frame(body, bg=BG)
    row.pack(fill="x")
    Pill(row, ok_text, ok, style="primary", padx=14, pady=4).pack(side="right")
    Pill(row, "取消", win.destroy, style="outline", padx=14, pady=4).pack(side="right", padx=(0, px(6)))
    entry.bind("<Return>", ok)
    win.bind("<Escape>", lambda e: win.destroy())

    win.update_idletasks()  # 置中在主視窗上
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{x}+{y}")
    win_style_popup(win)
    parent._active_dialog = win
    entry.focus_force()
    win.grab_set()
    parent.wait_window(win)
    return result["value"]


def day_summary(day, today, task_count, preview_count, event_count, holidays):
    """月曆日期提示的文字：節日、待辦（過去的日子稱逾期）、重複預定、行程；什麼都沒有回傳空字串。

    @param day 日期
    @param today 今天
    @param task_count 當天未完成的待辦數（只算最上層，子工作跟著父任務）
    @param preview_count 重複待辦預定的件數
    @param event_count 日曆行程數
    @param holidays [(名稱, 是否放假)]
    @return 例如「國慶日（放假） · 2 件待辦 · 1 個行程」
    """
    parts = [name + ("（放假）" if off else "") for name, off in holidays if name]
    if task_count:
        parts.append(f"{task_count} 件{'逾期' if day < today else '待辦'}")
    if preview_count:
        parts.append(f"{preview_count} 件重複待辦預定")
    if event_count:
        parts.append(f"{event_count} 個行程")
    return " · ".join(parts)


def relative_day_text(day, today):
    """大日期旁的說明：今天／明天／昨天／3 天後／5 天前。"""
    diff = (day - today).days
    return {0: "今天", 1: "明天", -1: "昨天"}.get(diff) or (f"{diff} 天後" if diff > 0 else f"{-diff} 天前")


def due_field(day):
    """Tasks API 的 due 欄位值：只有日期有意義，固定送當天 00:00 UTC；None 表示不排日期。"""
    return f"{day.isoformat()}T00:00:00.000Z" if day else None


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
        apply_fonts(self)
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
        self.done_today = []  # 今天完成的任務（含 completed 完成時間），見 completed_task
        self.holidays = {}  # {日期: [(名稱, 是否放假)]}
        self.holiday_calendar = holiday_calendar
        self.weeks = []
        self.busy = False
        self.pending_writes = 0     # 還沒送完的寫入（run_write）
        self.write_seq = 0          # 每次開始寫入就加 1；同步用來判斷期間是否有寫入
        self.resync_needed = False  # 同步結果因寫入被丟掉，最後一筆寫入完成時要再同步
        self.last_refresh = 0.0
        self.show_undated = False
        self.show_overdue = True
        self.show_done = False  # 「已完成」區塊預設收合，今天的待辦才是重點
        # 預設只顯示這一週，把高度留給今天的清單（比照 Windows 11 工作列月曆、To Do「我的一天」）；
        # 已經存過設定的使用者沿用原本的選擇
        self.compact = bool(self.state_data.get("compact_calendar", True))
        self.undo_group = None
        self.undo_job = None
        self.undo_action = None     # 其他可復原的動作（例如改日期）：按「復原」時呼叫
        self.new_due = self.today   # 新增工作的預設日期，跟著月曆選中的日期
        self.list_index = -1        # 新增工作預設放進的清單（self.lists 的索引；-1＝尚未讀到清單）
        self.draft = None           # 新增頁（點「詳細」）編輯中的內容
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
        self.after(NEXT_UP_TICK_MS, self._tick_next_up)
        self.after(SYSTEM_THEME_CHECK_MS, self._follow_system_theme)
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
        win_border_color(self, BORDER)  # 外框改用暖色系，取代系統預設的灰框
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
            on_hotkey=lambda hotkey_id: self.q.put((lambda *_: self.on_hotkey(hotkey_id), None, None)))
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

    def on_hotkey(self, hotkey_id):
        """全域快捷鍵：依 HOTKEY_ACTIONS 叫出快速列，或顯示／隱藏主視窗。"""
        if HOTKEY_ACTIONS.get(hotkey_id) == "quick_bar":
            self.toggle_quick_bar()
        else:
            self.toggle_visible()

    def toggle_visible(self):
        """F8：隱藏中就叫回來並放到最前面，顯示中就隱藏。"""
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
            self.withdraw()  # 背景模式：隱藏視窗，程式與提醒照常執行；F8（或快速列按 Tab）叫回
            return
        if self.borderless:
            self._mark_user_minimize()  # 自己按「—」縮小，不是 Win + D
            win_minimize(self)
        else:
            self.iconify()

    def close(self, ask=True):
        """關閉 DayNote；詳細頁有未儲存的修改時先處理（ask=False：不詢問直接儲存，用於被新開的 DayNote 取代）。

        還在背景送出的修改（剛完成、剛改日期…）會先等它送完再結束，最多 CLOSE_WAIT_SEC 秒；
        等待期間視窗先藏起來，使用者感覺是立刻關閉。
        """
        if getattr(self, "_closing", False):  # 等待儲存期間又按了一次關閉
            return
        self._closing = True
        try:
            if not self._flush_detail_on_close(ask):
                return
            if not self.cancel_new_task(ask=ask):  # 新增頁還沒建立的內容：詢問是否放棄
                return
        finally:
            self._closing = False
        self._closing = True  # 確定要關閉了，之後不再接受第二次關閉
        self._save_window_state()
        if self.pending_writes:
            self.withdraw()
            ok = self._pump_until(lambda: not self.pending_writes, CLOSE_WAIT_SEC)
            if not ok:
                log.warning("Closed before pending writes finished: pending=%d", self.pending_writes)
        for hotkey_id in self.hotkeys_registered:
            win_unregister_hotkey(self, hotkey_id)
        self.hotkeys_registered.clear()
        if getattr(self, "_poll_job", None):
            self.after_cancel(self._poll_job)
        self.destroy()

    def _save_window_state(self):
        """視窗位置、大小、置頂與其他狀態寫進 state.json（關閉、重新啟動前呼叫）。"""
        state = dict(self.state_data, topmost=self.topmost, scale=SCALE)
        if self.winfo_x() > -30000:  # 縮小中 Windows 回報 -32000，此時沿用上次存的位置
            state.update(x=self.winfo_x(), y=self.winfo_y(),
                         w=self.winfo_width(), h=self.winfo_height())
        save_state(state)

    def set_theme(self, choice):
        """換風格：記住選擇並立即重建畫面套用。從選單呼叫時，等選單收起後才重建（不在選單自己的回呼中刪除它）。

        choice 可以是 THEMES 的 key，或 AUTO_THEME（跟隨 Windows 淺色／深色）。
        """
        if choice != AUTO_THEME and choice not in THEMES:
            return
        self.state_data["theme"] = choice
        save_state(self.state_data)
        key = resolve_theme(choice, system_is_dark())
        if key == CURRENT_THEME:  # 配色沒變（例如淺色模式下改成跟隨 Windows）：只更新提示
            name = THEMES[key]["name"]
            self._set_status(f"已改成跟隨 Windows（目前「{name}」）" if choice == AUTO_THEME else f"已是「{name}」")
            return
        self.after_idle(lambda: self._rebuild_ui(key))

    def _light_theme(self):
        """切回淺色時要用的風格：上次用過的淺色風格，沒有記錄時用預設淺色。"""
        key = self.state_data.get("light_theme")
        return key if key in THEMES and key != AUTO_DARK else AUTO_LIGHT

    def toggle_dark(self):
        """右上角太陽／月亮：在上次用的淺色風格與墨夜之間切換（會改成固定風格，不再跟隨 Windows）。"""
        if CURRENT_THEME == AUTO_DARK:
            target = self._light_theme()
        else:
            self.state_data["light_theme"] = CURRENT_THEME  # 記住淺色風格，切回來時還原
            target = AUTO_DARK
        self.theme_var.set(target)  # 「⋯」→「風格」的打勾跟著移動
        self.set_theme(target)

    def _follow_system_theme(self):
        """跟隨 Windows 時，系統切換淺色／深色後自動換配色。"""
        try:
            if self.state_data.get("theme") == AUTO_THEME and not self.draft and not self.detail:
                key = resolve_theme(AUTO_THEME, system_is_dark())
                if key != CURRENT_THEME:
                    self._rebuild_ui(key)
        finally:  # 出錯也要排下一次，否則之後再也不會跟著系統換色
            self.after(SYSTEM_THEME_CHECK_MS, self._follow_system_theme)

    def _rebuild_ui(self, key):
        """套用風格後重建所有畫面元件；任務、清單、選中的日期與編輯中的內容都保留。

        提醒視窗維持原樣（關掉後下一則就是新配色），快速列、說明等其他小視窗直接關閉。
        """
        if self.draft:
            self.draft["title_draft"] = self.draft_title.get()
            self.draft["notes_draft"] = self.draft_notes.get("1.0", "end-1c")
        elif self.detail:
            self.detail["title_draft"] = self.detail_title.get()
            self.detail["notes_draft"] = self.detail_notes.get("1.0", "end-1c")
        status, status_is_error = self.status.cget("text"), self.status.cget("fg") == RED
        self._hide_undo()
        apply_theme(key)
        apply_fonts(self)
        keep = {toast.win for toast in ReminderToast.active}
        for widget in self.winfo_children():
            if widget not in keep:
                widget.destroy()
        self.configure(bg=BG, highlightbackground=BORDER)
        self._build()
        self._bind_shortcuts()  # 月曆、清單點擊取得焦點的綁定掛在新建的元件上
        self._show_login_button(not self.g.refresh_token)
        if self.draft:
            self.main_view.pack_forget()
            self._render_new_task()
            self.detail_view.pack(fill="both", expand=True)
            self._show_page_title("新增工作")
        elif self.detail:
            self.main_view.pack_forget()
            self._render_detail()
            self.detail_view.pack(fill="both", expand=True)
            self._show_page_title("工作詳細資訊")
        else:
            self.redraw()
        if self.borderless:
            win_border_color(self, BORDER)
        if status_is_error:  # 錯誤訊息（例如同步失敗）要留著，不被換風格的提示蓋掉
            self._set_status(status, error=True)
        else:
            self._set_status(f"已換成「{THEMES[key]['name']}」")

    def _flush_detail_on_close(self, ask=True):
        """關閉前若詳細頁有未儲存的修改：詢問儲存／不儲存／取消。可以繼續關閉時回傳 True。

        儲存要等送完才繼續關閉（背景執行緒是 daemon，程式結束時會被中止，修改可能根本沒送到 Google）；
        送出改在非 daemon 執行緒，等待期間畫面照常更新，斷線時最多等 CLOSE_WAIT_SEC 秒。
        """
        if not self.detail:
            return True
        fields = self._detail_changes()
        if not fields:
            return True
        task = self.detail["task"]
        if ask:
            answer = messagebox.askyesnocancel(
                "尚未儲存", f"「{task['title']}」有尚未儲存的修改，要先儲存再關閉嗎？", parent=self, icon="warning",
                yes="儲存", no="不儲存")
            if answer is None:
                return False
            if not answer:
                return True
        try:
            self._wait_in_background(lambda: self.g.update_task(task["list_id"], task["id"], fields),
                                     CLOSE_WAIT_SEC)
        except (ApiError, NeedLogin) as e:
            if not ask:
                return True  # 被取代時無法詢問，只能放棄這次修改
            messagebox.showerror("儲存失敗", f"{e}\n\n修改尚未儲存，DayNote 先不關閉。", parent=self)
            return False
        return True

    def _wait_in_background(self, work, timeout):
        """在非 daemon 執行緒執行 work() 並等它完成，等待期間畫面照常更新（不會「沒有回應」）。

        @return work() 的回傳值
        @raise work() 拋出的例外；超過 timeout 秒時拋出 ApiError（工作仍會在背景做完）
        """
        outcome = {}

        def runner():
            try:
                outcome["result"] = work()
            except Exception as e:  # 交回主執行緒處理
                outcome["error"] = e
        thread = threading.Thread(target=runner)  # 非 daemon：程式結束前會等它送完
        thread.start()
        self._set_status("儲存中…")
        if not self._pump_until(lambda: not thread.is_alive(), timeout):
            raise ApiError(f"連線逾時（超過 {timeout} 秒）")
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    def _pump_until(self, done, timeout):
        """處理畫面事件直到 done() 成立或超過 timeout 秒。@return 是否在時限內完成"""
        deadline = time.time() + timeout
        while not done():
            if time.time() >= deadline:
                return False
            self.update()
            time.sleep(0.02)
        return True

    def _icon_button(self, parent, text, command, font=ICON_FONT, tip=None):
        """扁平小按鈕（Label 實作，比 ttk.Button 窄，頂部列才放得下）。

        單一符號（⟳ 📌 ◀ ▶ …）改用線條圖示字型顯示；text_value 保留原本的符號，供測試辨識。
        """
        shown = text
        if text in GLYPHS and ICON_GLYPH_FONT:
            shown, font = GLYPHS[text], (ICON_GLYPH_FONT, 10)
        btn = tk.Label(parent, text=shown, bg=BG, fg=GRAY, font=font,
                       padx=px(8), pady=px(4), cursor="hand2")
        btn.text_value = text
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.config(bg=HOVER))
        btn.bind("<Leave>", lambda e: btn.config(bg=BG))
        if tip:
            Tooltip(btn, tip)
        return btn

    # ---- 版面

    def _build(self):
        # 頂部：第一列只放右上角按鈕，第二列是大標題（佔滿整個寬度，字級放大也不會被按鈕切到）
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=(px(18), px(6)), pady=(px(6), 0))
        tools_row = tk.Frame(top, bg=BG)
        tools_row.pack(fill="x", pady=(px(8), 0))  # 離上緣留空白，太貼邊會有壓迫感
        tools = tk.Frame(tools_row, bg=BG)
        tools.pack(side="right", anchor="n")
        # 未登入時才出現；用和其他按鈕一致的膠囊樣式
        self.btn_login = Pill(tools, "登入", self.login, style="primary", font=SMALL, padx=10, pady=2)
        Tooltip(self.btn_login, "用瀏覽器登入 Google 帳號")
        # 同步、登出這類不常用（登出還會撤銷授權）的動作收進「⋯」選單：
        # 不再緊鄰關閉鈕而誤按，右上角也只剩圖示按鈕，和 Windows 11 標題列一致
        self.menu_more = PopupMenu(self)
        self.menu_more.add_command(label="使用說明與快捷鍵（F1）", command=self.open_help)
        # 風格：單選，目前的風格前面打勾；選了立即套用
        self.menu_theme = PopupMenu(self)
        self.theme_var = tk.StringVar(self, value=self.state_data.get("theme", CURRENT_THEME))
        self.menu_theme.add_radiobutton(label=AUTO_THEME_NAME, value=AUTO_THEME, variable=self.theme_var,
                                        command=lambda: self.set_theme(AUTO_THEME))
        self.menu_theme.add_separator()
        for key, theme in THEMES.items():
            self.menu_theme.add_radiobutton(label=theme["name"], value=key, variable=self.theme_var,
                                            command=lambda k=key: self.set_theme(k))
        self.menu_more.add_cascade(label="風格", menu=self.menu_theme)
        self.menu_more.add_separator()
        self.menu_more.add_command(label="立即同步（F5）", command=self.refresh)
        self.menu_more.add_separator()
        self.menu_more.add_command(label="登出 Google", command=self.logout)
        # 淺色／深色一鍵切換：淺色時顯示月亮（點了變深色），深色時顯示太陽；其他風格仍在「⋯」→「風格」
        self.btn_dark = self._icon_button(
            tools, "☀" if CURRENT_THEME == AUTO_DARK else "🌙", self.toggle_dark,
            tip=lambda: f"切換到淺色（{THEMES[self._light_theme()]['name']}）" if CURRENT_THEME == AUTO_DARK
            else f"切換到深色（{THEMES[AUTO_DARK]['name']}）")
        self.btn_dark.pack(side="left")
        self.btn_more = self._icon_button(tools, "⋯", self._open_more_menu, tip="更多：使用說明、風格、同步、登出")
        self.btn_more.pack(side="left")
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
        # 主畫面：大號日期數字（強調色）＋右邊疊放星期與「今天／明天」，像日曆手帳的封面，一眼看到今天幾號
        self.hero = tk.Frame(top, bg=BG)
        self.lbl_day = tk.Label(self.hero, font=HERO_FONT, bg=BG, fg=ACCENT, anchor="w")
        self.lbl_day.pack(side="left")
        meta = tk.Frame(self.hero, bg=BG)
        meta.pack(side="left", padx=(px(12), 0))
        self.lbl_weekday = tk.Label(meta, font=HEADING_FONT, bg=BG, fg=TEXT, anchor="w")
        self.lbl_weekday.pack(fill="x")
        self.lbl_relative = tk.Label(meta, font=SMALL, bg=BG, fg=GRAY, anchor="w")
        self.lbl_relative.pack(fill="x")
        # 「下一件」放在大日期右邊當第三行：瞥一眼就知道接下來要做什麼，又不多佔一整列高度
        # 沒有下一件時收起來；由 _update_next_up() 決定顯示與否
        self.lbl_next = tk.Label(meta, bg=BG, fg=ACCENT, font=SMALL, anchor="w")
        # 新增頁、詳細頁：左邊返回箭頭＋頁面標題，返回放在標題同一列（一般 App 的位置）
        self.page_row = tk.Frame(top, bg=BG)
        self.btn_detail_back = self._icon_button(
            self.page_row, "←", self._page_back,
            tip=lambda: "不建立，返回（Esc）" if self.draft else "返回；有修改會自動儲存（Esc）")
        if ICON_GLYPH_FONT:
            self.btn_detail_back.config(font=(ICON_GLYPH_FONT, 13))
        self.btn_detail_back.pack(side="left", padx=(0, px(4)))
        self.lbl_date = tk.Label(self.page_row, font=TITLE_FONT, bg=BG, fg=TEXT, anchor="w")
        self.lbl_date.pack(side="left", fill="x", expand=True)
        self.hero.pack(fill="x")
        # 頂部（含日期文字）當拖曳區
        for widget in (top, tools_row, self.page_row, self.lbl_date, self.hero, self.lbl_day, meta,
                       self.lbl_weekday, self.lbl_relative):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

        # 主畫面（月曆＋清單＋輸入列）；點任務時整塊換成詳細頁，比照手機 App
        self.main_view = tk.Frame(self, bg=BG)
        self.detail_view = tk.Frame(self, bg=BG)
        nav = self.nav = tk.Frame(self.main_view, bg=BG)
        nav.pack(fill="x", padx=px(16), pady=(px(14), px(4)))  # 和標題拉開距離
        # 左：月份標題＋「展開整月／只看這週」（點標題也能切換）；右：上一個／下一個、今天
        # 切換鈕寫明文字而不是只放箭頭，第一次用的人一看就知道月曆可以展開
        self.lbl_month = tk.Label(nav, font=HEADING_FONT, bg=BG, fg=TEXT, cursor="hand2")
        self.lbl_month.pack(side="left", padx=(px(4), 0))
        self.lbl_month.bind("<Button-1>", lambda e: self.toggle_compact())
        self.btn_compact = self._icon_button(
            nav, "", self.toggle_compact, font=SMALL,
            tip=lambda: "展開整個月" if self.compact else "收合月曆：只顯示這一週，清單空間比較大")
        self.btn_compact.config(fg=ACCENT)
        self.btn_compact.bind("<Leave>", lambda e: self.btn_compact.config(fg=ACCENT), add="+")
        self.btn_compact.pack(side="left", padx=(px(4), 0))
        btn_today = Pill(nav, "今天", lambda: self.select(dt.date.today()), style="soft",
                         font=SMALL, padx=10, pady=2)
        btn_today.pack(side="right", padx=(px(4), px(2)))
        Tooltip(btn_today, "回到今天（Home）")
        self._icon_button(nav, "▶", lambda: self.shift_month(1),
                          tip=lambda: "下一週" if self.compact else "下個月").pack(side="right")
        self._icon_button(nav, "◀", lambda: self.shift_month(-1),
                          tip=lambda: "上一週" if self.compact else "上個月").pack(side="right")

        self.cal = tk.Canvas(self.main_view, width=7 * CELL_W, height=HEAD_H + 6 * CELL_H,
                             bg=BG, highlightthickness=0, cursor="hand2")
        self.cal.pack(pady=px(4))
        self.cal.bind("<Button-1>", self._on_cal_click)
        # 滑過日期時出現圓角細外框，提示可以點
        self.cal_hover = None
        self.cal.bind("<Motion>", self._on_cal_hover, add="+")
        self.cal.bind("<Leave>", lambda e: self._paint_cal_hover(None), add="+")
        # 滑鼠停在日期上說明當天有什麼，第一次用的人不必猜圓點顏色的意思
        self.cal_tooltip = CellTooltip(self.cal, self._cal_cell_at, self._day_tip_text, self._cal_cell_rect)

        # 底部先 pack，視窗縮小時才不會被任務清單擠掉
        status_row = tk.Frame(self, bg=BG)
        status_row.pack(side="bottom", fill="x", padx=(px(18), 0))
        grip = tk.Label(status_row, text="◢", bg=BG, fg=BORDER, font=SMALL, cursor="size_nw_se")
        Tooltip(grip, "拖拉調整視窗大小")
        grip.pack(side="right", anchor="se")
        grip.bind("<Button-1>", self._start_resize)
        grip.bind("<B1-Motion>", self._on_resize)
        self.btn_undo = tk.Label(status_row, text="復原", bg=BG, fg=ACCENT, cursor="hand2",
                                 font=(SMALL[0], SMALL[1], "bold underline"))
        self.btn_undo.bind("<Button-1>", lambda e: self.undo())
        self.status = tk.Label(status_row, bg=BG, fg=GRAY, font=SMALL, anchor="w", justify="left")
        self.status.pack(side="left", fill="x", expand=True, pady=(0, px(6)))
        # 狀態文字依視窗寬度換行
        status_row.bind("<Configure>", lambda e: self.status.config(wraplength=max(e.width - px(30), px(100))))
        # 底部新增列：左邊快捷輸入（Enter 新增，可寫「明天 3點」）、「詳細」開完整新增頁、最右邊「新增」按鈕
        self.add_box = RoundedBox(self.main_view, fill=BG, outline=BORDER, radius=21, padx=6, pady=5)
        self.add_box.pack(side="bottom", fill="x", padx=px(16), pady=(px(6), px(10)))
        bottom = self.add_box.inner
        self.btn_add = Pill(bottom, "新增", self.add_task, style="primary", padx=14, pady=4)
        self.btn_add.pack(side="right")
        Tooltip(self.btn_add, "新增輸入框裡的工作（也可以按 Enter）")
        self.btn_detail = Pill(bottom, "詳細", self._on_add_click, style="soft", padx=12, pady=4)
        self.btn_detail.pack(side="right", padx=(0, px(6)))
        Tooltip(self.btn_detail, "完整新增：設定詳細資訊、日期、提醒、重複與清單")
        # 右側按鈕先放好再放輸入框：空間不足時縮的是輸入框，而不是按鈕
        self.entry = tk.Entry(bottom, font=FONT, relief="flat", bg=BG, fg=TEXT,
                              insertbackground=TEXT, highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, padx=(px(8), px(6)), ipady=px(3))
        self.entry.bind("<Return>", lambda e: self.add_task())
        self.entry.bind("<Button-1>", lambda e: self.entry.focus_force())
        self.entry.bind("<FocusIn>", lambda e: (self._hide_placeholder(), self.add_box.set_outline(ACCENT)))
        self.entry.bind("<FocusOut>", lambda e: (self._show_placeholder(), self.add_box.set_outline(BORDER)))
        self.entry.bind("<Escape>", lambda e: self.cal.focus_set())
        self.entry.bind("<KeyRelease>", lambda e: self._update_quick_chips())
        self.placeholder_on = False
        self._show_placeholder()
        # 快捷輸入時即時顯示辨識結果（例如 📅 明天 ⏰ 15:00），沒打字時不佔位置
        self.lbl_quick_chips = tk.Label(self.main_view, bg=BG, fg=ACCENT, font=SMALL, anchor="w")

        # 可捲動的任務清單
        wrap = self.list_wrap = tk.Frame(self.main_view, bg=BG)
        wrap.pack(fill="both", expand=True, padx=(px(18), px(12)))
        self.list_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        scrollbar = self.list_scrollbar = ThinScrollbar(wrap, command=self.list_canvas.yview)
        self.list_frame = tk.Frame(self.list_canvas, bg=BG)
        window = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda e: self.list_canvas.configure(
            scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda e: self.list_canvas.itemconfigure(window, width=e.width))
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        # 捲軸先 pack：Canvas 預設會要求約 378px 寬，後 pack 的捲軸在預設視窗寬度下會被擠到看不見
        scrollbar.pack(side="right", fill="y", padx=(px(4), 0))
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.bind_all("<MouseWheel>", lambda e: self.list_canvas.yview_scroll(-e.delta // 120, "units"))
        self.main_view.pack(fill="both", expand=True)  # 最後 pack，狀態列才不會被擠掉

    def redraw(self):
        self.today = dt.date.today()
        if self.detail or self.draft:  # 詳細頁／新增頁開啟中：不動編輯中的欄位與標題
            return
        d = self.selected
        self._show_date_hero(d)
        self.btn_compact.config(text="展開整月 ▾" if self.compact else "只看這週 ▴")
        if self.placeholder_on:  # 提示文字跟著選中的日期變動
            self.entry.delete(0, "end")
            self.entry.insert(0, self._placeholder_text())
        self._draw_calendar()
        self._draw_list()
        self._update_next_up()

    def _show_date_hero(self, day):
        """主畫面標題：大日期數字＋星期＋相對日期。"""
        self.page_row.pack_forget()
        self.lbl_date.config(text=f"{day.month}月{day.day}日 星期{WEEKDAY_NAME[day.weekday()]}")
        self.lbl_day.config(text=str(day.day))
        self.lbl_weekday.config(text=f"星期{WEEKDAY_NAME[day.weekday()]}")
        # 月份已顯示在下方月曆標題，這裡只寫相對日期，避免同一個資訊重複出現
        self.lbl_relative.config(text=relative_day_text(day, self.today))
        self.hero.pack(fill="x")

    def _show_page_title(self, text):
        """新增頁、詳細頁的標題（取代大日期）。"""
        self.hero.pack_forget()
        self.lbl_date.config(text=text)
        self.page_row.pack(fill="x")

    def _page_back(self):
        """頁面標題左邊的返回箭頭：新增頁放棄新增，詳細頁儲存後返回。"""
        if self.draft:
            self.cancel_new_task()
        elif self.detail:
            self.close_detail()

    def _prop_row(self, view, label, pad, top=0):
        """屬性列（清單、日期、重複）：左邊固定寬度的灰色標籤，右邊放膠囊按鈕或文字，各列對齊。"""
        row = tk.Frame(view, bg=BG)
        row.pack(fill="x", pady=(top, 0), **pad)
        tk.Label(row, text=label, bg=BG, fg=GRAY, font=SMALL, width=4, anchor="w").pack(side="left")
        return row

    def _update_next_up(self):
        """更新「下一件」：只在選中今天時顯示，其他日子留空（摘要是給「現在」用的）。"""
        text = ""
        if self.g.refresh_token and self.selected == self.today:
            items = [(parse_time_text(when), title) for when, title in self.events.get(self.today, [])]
            items += [(t["time"], t["title"]) for t in self.tasks if t["due"] == self.today and t.get("time")]
            text = next_up_text([(at, title) for at, title in items if at], self._now())
        self.lbl_next.config(text=text)
        if text:
            self.lbl_next.pack(fill="x", pady=(px(2), 0))
        else:
            self.lbl_next.pack_forget()

    def _tick_next_up(self):
        """每分鐘更新一次「下一件」，時間過了就換成下一件。"""
        try:
            if not (self.detail or self.draft):
                self._update_next_up()
        finally:  # 出錯也要排下一次，否則「下一件」從此停住
            self.after(NEXT_UP_TICK_MS, self._tick_next_up)

    def _draw_calendar(self):
        c = self.cal
        c.delete("all")
        self.cal_hover = None  # 重畫後滑過底色要重新畫
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
        preview_days = set(self._previews(self.weeks[0][0], self.weeks[-1][-1]))
        c.config(height=HEAD_H + len(self.weeks) * CELL_H)  # 依當月週數調整高度，不留空白列
        for row, week in enumerate(self.weeks):
            for col, day in enumerate(week):
                x, y = col * CELL_W, HEAD_H + row * CELL_H
                cx, cy = x + CELL_W / 2, y + px(17)
                if day == self.selected:
                    # 選取框（圓角）、今天的圓圈、日期數字共用同一個中心點 cy，圓圈才會在框內置中
                    draw_rounded(c, CELL_W - px(4), px(32), px(16), SELECTED, BG, tag="selected",
                                 x0=x + px(2), y0=cy - px(16))
                if day == self.today:
                    d = px(22)
                    img = round_image(c, d, ACCENT, SELECTED if day == self.selected else BG)
                    c.create_image(round(cx - d / 2), round(cy - d / 2), image=img, anchor="nw")
                    color = ON_ACCENT
                else:
                    off = day.weekday() >= 5 or self._is_day_off(day)
                    if day.month == month:
                        color = HOLIDAY_RED if off else TEXT
                    else:
                        color = HOLIDAY_RED_FADED if off else FADED_TEXT
                c.create_text(cx, cy, text=str(day.day), fill=color, font=FONT)
                names = self.holidays.get(day)
                if names:  # 數字右上方的小圓點：紅＝放假的節日，赭黃＝一般節日
                    mark = HOLIDAY_RED if self._is_day_off(day) else FESTIVAL
                    if day.month != month:
                        mark = HOLIDAY_RED_FADED
                    d = px(6)  # 位置落在選取框的圓角內、今天的圓圈外
                    img = round_image(c, d, mark, SELECTED if day == self.selected else BG)
                    c.create_image(round(cx + px(13) - d / 2), round(cy - px(9) - d / 2), image=img, anchor="nw")
                if day in task_days or day in preview_days or day in self.events:
                    # 紅＝有逾期未完成待辦；藍＝有待辦；淡藍＝重複待辦預定的日子；灰＝只有日曆行程
                    if day in task_days:
                        dot = RED if day < self.today else ACCENT
                    elif day in preview_days:
                        dot = PREVIEW_DOT
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

    def _raise_popdown(self, combobox):
        """視窗置頂時，下拉選單也要置頂，否則會被主視窗蓋住。"""
        popdown = self.tk.call("ttk::combobox::PopdownWindow", combobox)
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
                # 逾期的待辦多半是「今天要補做」：一鍵全部移到今天，不必逐筆打開改日期
                self.btn_overdue_today = self._section_header(
                    f"逾期（{len(overdue)}）", self.show_overdue, self._toggle_overdue, fg=RED,
                    action=("全部移到今天", lambda: self.reschedule(overdue, self.today)))
                if self.show_overdue:
                    for task in overdue:
                        self._task_with_children(task)
        # 當天標題放在逾期區之後，緊貼當天的項目
        previews = self._previews(day, day).get(day, [])
        # 當天標題和「逾期」「未排日期」同一層級（標題字型＋數量），今天的待辦才是畫面的重點
        label = "今天" if day == self.today else f"{day.month}月{day.day}日"
        self._section_header(f"{label}（{len(events) + len(tasks) + len(previews)}）", True, None, fg=TEXT)
        for name, day_off in self.holidays.get(day, []):
            tk.Label(self.list_frame, text=f"◆ {name}" + ("（放假）" if day_off else ""),
                     bg=BG, fg=HOLIDAY_RED if day_off else GRAY, font=FONT, anchor="w").pack(fill="x", pady=(0, 2))
        # 行程與待辦合成一條時間軸：全天行程 → 有時間的依時間 → 沒時間的待辦（維持 Google Tasks 順序）
        timeline = [((0, dt.time.min) if when == "全天" else (1, parse_time_text(when) or dt.time.min), (when, title), None)
                    for when, title in events]
        timeline += [((1, t["time"]) if t.get("time") else (2, dt.time.min), None, t) for t in tasks]
        for _, event, task in sorted(timeline, key=lambda item: item[0]):  # sorted 穩定：同時間時行程在前
            if task:
                self._task_with_children(task)
            else:
                self._card(event[1], "Google 日曆", None, at=event[0])
        for task in previews:
            self._preview_card(task)
        done = sorted(self.done_today, key=lambda t: t["completed"] or dt.datetime.min) if day == self.today else []
        if not events and not tasks and not previews and done:
            tk.Label(self.list_frame, text="今天的待辦都完成了", bg=BG, fg=GRAY, font=FONT).pack(pady=(px(14), px(8)))
        elif not events and not tasks and not previews:
            # 空狀態順便教新增方式：第一次用的人不必翻說明就知道從哪裡開始
            tk.Label(self.list_frame, text="這天沒有任務", bg=BG, fg=GRAY, font=FONT).pack(pady=(px(14), 0))
            tk.Label(self.list_frame, text="在下方輸入框打字新增，例如：明天 3點 開會",
                     bg=BG, fg=GRAY, font=SMALL).pack(pady=(px(2), px(8)))

        if done:
            # 今天完成的（含手機上完成的）收在這裡；點圓圈改回未完成，5 秒復原過了也找得回來
            self._section_header(f"已完成（{len(done)}）", self.show_done, self._toggle_done, fg=GRAY)
            if self.show_done:
                for task in done:
                    at = f"{task['completed']:%H:%M} 完成" if task["completed"] else "已完成"
                    self._card(task["title"], " · ".join(p for p in (at, task["list_title"]) if p), task, done=True)

        undated = sorted((t for t in top_level if t["due"] is None), key=self._order_key)
        if undated:
            self._section_header(f"未排日期（{len(undated)}）", self.show_undated, self._toggle_undated)
            if self.show_undated:
                for task in undated:
                    self._task_with_children(task)
        self.list_canvas.yview_moveto(0)

    def _previews(self, start, end):
        """重複待辦在 [start, end] 預定的日子：{日期: [來源任務]}。

        Google 上同一時間只有「這一期」，下一期在完成後才建立；這裡只做畫面預覽。
        那天已經有同標題、同規則的真實待辦（例如已提前建立的下一期）就不再顯示預覽。
        """
        today = dt.date.today()
        top = self._top_level()
        real = {(t["due"], t["title"], t.get("recur")) for t in top if t["due"]}
        previews = {}
        for task in top:
            for day in upcoming_days(task, start, end, today):
                if (day, task["title"], task["recur"]) not in real:
                    previews.setdefault(day, []).append(task)
        return previews

    def _preview_card(self, task):
        """重複待辦預定的一期：灰色、沒有完成圓圈（還沒建立，不能勾選），點了開啟這筆重複待辦。"""
        # 只有外框的空心卡片：和實心的待辦卡片區隔，表示這一期還沒建立
        box = RoundedBox(self.list_frame, fill=BG, outline=BORDER, radius=12, padx=12, pady=4)
        box.pack(fill="x", padx=(0, px(2)), pady=(0, px(6)))
        row = box.inner
        row.config(cursor="hand2")
        icon = tk.Label(row, text="🔁", bg=BG, fg=PREVIEW_DOT, font=FONT, width=2, cursor="hand2")
        icon.pack(side="left", padx=(0, px(10)), pady=px(4))
        text = tk.Frame(row, bg=BG, cursor="hand2")
        text.pack(side="left", fill="x", expand=True, pady=px(4))
        title = tk.Label(text, text=task["title"], bg=BG, fg=GRAY, font=FONT, anchor="w", cursor="hand2")
        title.pack(fill="x")
        at = time_text(task.get("time"))
        current = f"{task['due'].month}/{task['due'].day}" if task["due"] else "這一期"
        subtitle = " · ".join(p for p in ("預定", f"⏰ {at}" if at else "", f"🔁 {format_recur(task['recur'])}",
                                          task["list_title"]) if p)
        tk.Label(text, text=subtitle, bg=BG, fg=GRAY, font=SMALL, anchor="w", cursor="hand2").pack(fill="x")
        tip = f"完成 {current} 那一期後，才會建立這一期（點一下開啟這筆重複待辦）"
        for widget in (box, row, icon, text, *text.winfo_children()):
            widget.bind("<Button-1>", lambda e: self.open_detail(task))
            Tooltip(widget, tip)

    def _draw_login_prompt(self):
        """未登入時，清單區改顯示明確的登入提示（取代「這天沒有任務」）。"""
        box = tk.Frame(self.list_frame, bg=BG)
        box.pack(fill="x", pady=px(30))
        tk.Label(box, text="尚未登入 Google", bg=BG, fg=TEXT, font=HEADING_FONT).pack()
        tk.Label(box, text="登入後才能讀取與新增工作", bg=BG, fg=GRAY, font=SMALL).pack(pady=(px(4), px(12)))
        Pill(box, "登入 Google", self.login, style="primary", padx=20).pack()

    def _section_header(self, text, expanded, toggle, fg=None, action=None):
        """區塊標題列（逾期、今天、未排日期）：左邊標題＋淡色數量；可收合時右邊有箭頭，整列可點。

        text 形如「逾期（1）」；可收合時標題 Label 的 text_value 保留「▼ 逾期（1）」，供測試辨識。
        toggle 為 None：不可收合（例如當天的標題），沒有箭頭也不能點。
        action＝(文字, 函式)：收合箭頭左邊的動作連結（例如「全部移到今天」），點它不會收合區塊。
        @return 動作連結的 Label；沒有 action 時回傳 None
        """
        fg = fg or ACCENT
        full = f"{'▼' if expanded else '▶'} {text}" if toggle else text
        cursor = "hand2" if toggle else ""
        name, _, count = text.rstrip("）").partition("（")
        header = tk.Frame(self.list_frame, bg=BG, cursor=cursor)
        header.pack(fill="x", pady=(px(12), px(4)))
        title = tk.Label(header, text=name, bg=BG, fg=fg, font=HEADING_FONT, cursor=cursor, anchor="w")
        title.text_value = full
        title.pack(side="left")
        parts = [header, title]
        if count:
            badge = tk.Label(header, text=count, bg=BG, fg=GRAY, font=SMALL, cursor=cursor)
            badge.pack(side="left", padx=(px(6), 0))
            parts.append(badge)
        if toggle and ICON_GLYPH_FONT:
            arrow = tk.Label(header, text=GLYPHS["▼" if expanded else "▶"], bg=BG, fg=GRAY,
                             font=(ICON_GLYPH_FONT, 8), cursor="hand2")
            arrow.pack(side="right", padx=(0, px(6)))
            parts.append(arrow)
        elif toggle:  # 沒有圖示字型：箭頭直接寫在標題文字裡
            title.config(text=full)
        for widget in parts if toggle else ():
            widget.bind("<Button-1>", lambda e: toggle())
        if action:
            label, command = action
            link = tk.Label(header, text=label, bg=BG, fg=ACCENT, font=SMALL, cursor="hand2")
            link.pack(side="right", padx=(0, px(8)))
            link.bind("<Button-1>", lambda e: command())
            link.bind("<Enter>", lambda e: link.config(fg=ACCENT_HOVER))
            link.bind("<Leave>", lambda e: link.config(fg=ACCENT))
            return link
        return None

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
        # 逾期的時間已和日期一起寫在副標題，右側時間欄就不再重複；今天時間已過還沒做的，時間改紅色
        now = self._now()
        children = self._children(task)
        # 父工作和子工作之間只留細縫，最後一個子工作才恢復一般間距，一眼看出是同一組
        tight = px(2)
        self._card(task["title"], task_subtitle(task, self.today), task, subtitle_fg=RED if overdue else GRAY,
                   at="" if overdue else time_text(task.get("time")), at_fg=RED if is_late(task, now) else GRAY,
                   gap=tight if children else None)
        for index, child in enumerate(children):
            # 子任務不顯示清單名稱（和父任務相同），比照 Google Tasks 精簡顯示；有時間才顯示在右側
            self._card(child["title"], "", child, indent=True, at=time_text(child.get("time")),
                       at_fg=RED if is_late(child, now) else GRAY,
                       gap=tight if index < len(children) - 1 else None)

    def _card(self, title, subtitle, task, indent=False, subtitle_fg=None, at="", at_fg=None, gap=None, done=False):
        """清單中的一筆：圓角卡片浮在底色上，卡片之間留空隙（取代分隔線）；滑過時卡片變色。

        task 為 None 時是日曆行程：卡片左側色條，圓圈位置留空，只顯示、不可勾選。indent＝子工作縮排。
        at＝右側時間欄文字（14:00、全天）；行程與待辦的時間都靠右對齊，掃一眼就能讀出順序。
        gap＝和下一張卡片的空隙；父工作與子工作之間縮小空隙，看起來是同一組。
        done＝已完成區塊的卡片：實心打勾圓圈（點了改回未完成）、灰色標題、不開詳細頁。
        """
        subtitle_fg, at_fg = subtitle_fg or GRAY, at_fg or GRAY
        row = tk.Canvas(self.list_frame, bg=BG, highlightthickness=0, height=px(50))
        row.pack(fill="x", padx=(CHILD_INDENT if indent else 0, px(2)))
        row.card_fill = CARD
        inner = tk.Frame(row, bg=CARD)
        if task and done:
            circle = self._done_circle(inner, task, bg=CARD)
            circle.pack(side="left", padx=(0, px(10)))
        elif task:
            circle = self._check_circle(inner, task, bg=CARD)
            circle.pack(side="left", padx=(0, px(10)))
            count = len(self._children(task))
            Tooltip(circle, f"標記為完成（含 {count} 個子工作）" if count else "標記為完成")
        else:
            # 行程沒有完成圓圈；留同寬的空位，標題才會和待辦對齊
            spacer = tk.Frame(inner, bg=CARD, width=px(22), height=px(22))
            spacer.pack(side="left", padx=(0, px(10)))
            Tooltip(row, "Google 日曆行程（唯讀，請到日曆 App 修改）")
        lbl_at = None
        if at:  # 時間欄先 pack，標題太長時被壓縮的是標題而不是時間
            lbl_at = tk.Label(inner, text=at, bg=CARD, fg=at_fg, font=EVENT_TIME_FONT, anchor="e")
            lbl_at.pack(side="right", anchor="n", padx=(px(8), px(4)), pady=(px(2), 0))
        text = tk.Frame(inner, bg=CARD)
        text.pack(side="left", fill="x", expand=True)
        lbl_title = tk.Label(text, text=title, bg=CARD, fg=GRAY if done else TEXT, font=FONT, anchor="w",
                             justify="left")
        lbl_title.pack(fill="x")
        preview = _notes_preview(task.get("notes", "")) if task and not done else ""
        if preview:  # 比照 Google Tasks：標題下方顯示詳細資訊第一行
            tk.Label(text, text=preview, bg=CARD, fg=GRAY, font=SMALL, anchor="w").pack(fill="x")
        if subtitle:
            tk.Label(text, text=subtitle, bg=CARD, fg=subtitle_fg, font=SMALL, anchor="w").pack(fill="x")
        pad_x, pad_y = px(12), px(6)  # 卡片內距
        gap = px(5) if gap is None else gap  # 卡片之間的空隙（不畫卡片）
        window = row.create_window(pad_x, pad_y, window=inner, anchor="nw")
        if task and not done:  # 點整列（圓圈以外）開啟詳細頁；按右鍵開快速選單（改日期、完成）
            for widget in (row, inner, text, *text.winfo_children(), *([lbl_at] if lbl_at else [])):
                widget.bind("<Button-1>", lambda e: self.open_detail(task))
                widget.bind("<Button-3>", lambda e: self._popup_task_menu(task, e))
                widget.config(cursor="hand2")
        # 滑鼠移過時卡片變色，提示可以點
        row.bind("<Enter>", lambda e: self._paint_card(row, CARD_HOVER))
        # 滑鼠可能從列內的文字直接移出去，此時只有子元件收到 Leave，所以每個子元件都要檢查
        for widget in (row, *_descendants(row)):
            widget.bind("<Leave>", lambda e: self._leave_row(row, e), add="+")

        def relayout(_=None):
            width = row.winfo_width()
            if width < px(50):
                return
            lbl_title.config(wraplength=width - px(72) - (lbl_at.winfo_reqwidth() + px(12) if lbl_at else 0))
            row.itemconfigure(window, width=width - 2 * pad_x)
            inner.update_idletasks()
            height = inner.winfo_reqheight() + 2 * pad_y + gap
            row.config(height=height)  # 高度不變時不會再觸發 <Configure>，不會無限迴圈
            row.card_size = (width, height - gap)
            self._paint_card(row, row.card_fill)
            row.delete("deco")
            if task is None:  # 行程：卡片左側色條，和可勾選的待辦區隔
                row.create_rectangle(px(5), px(12), px(8), height - gap - px(12), fill=EVENT_DOT, outline="",
                                     tags="deco")
        row.bind("<Configure>", relayout)

    def _paint_card(self, row, fill):
        """重畫卡片底色（圓角）並把卡片內容的底色一起換掉。"""
        row.card_fill = fill
        size = getattr(row, "card_size", None)
        if size:
            draw_rounded(row, size[0], size[1], px(12), fill, BG, tag="card")
            row.tag_lower("card")
        for child in row.winfo_children():
            _set_bg(child, fill)

    def _leave_row(self, row, event):
        """滑鼠離開整張卡片才恢復底色（移到卡片內的子元件不算離開）。"""
        x, y = event.x_root - row.winfo_rootx(), event.y_root - row.winfo_rooty()
        if not (0 <= x < row.winfo_width() and 0 <= y < row.winfo_height()):
            self._paint_card(row, CARD)

    def _check_circle(self, parent, task, bg=None):
        """To Do 風格的圓圈：滑鼠移上去顯示 ✓，點擊後填滿並標記完成。"""
        bg = bg or CARD_BG
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
            c.create_text(mid, mid, text="✓", fill=ON_ACCENT, font=SMALL)
            self.after(250, lambda: self.complete(task))  # 讓使用者看到勾選效果再移除
        c.bind("<Button-1>", done)
        return c

    def _done_circle(self, parent, task, bg):
        """已完成的實心打勾圓圈：點了改回未完成；週期待辦已建立下一期，不提供改回（避免出現兩期）。"""
        size = px(22)
        d = size - px(2)
        c = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0)
        c.create_image((size - d) // 2, (size - d) // 2, image=round_image(c, d, GRAY, bg), anchor="nw", tags="ring")
        c.create_text(size / 2, size / 2, text="✓", fill=CARD, font=SMALL)
        c.on_bg = lambda color: c.itemconfigure("ring", image=round_image(c, d, GRAY, color))  # 卡片 hover 換底色時跟著換
        if task.get("recur"):
            Tooltip(c, "週期待辦：已建立下一期，請到下一期修改")
            return c
        c.config(cursor="hand2")
        c.bind("<Button-1>", lambda e: self.reopen(task))
        Tooltip(c, "改回未完成")
        return c

    def reopen(self, task):
        """把今天完成的待辦改回未完成：先移回清單，失敗再放回「已完成」並顯示錯誤。"""
        if task not in self.done_today:
            return
        self.done_today.remove(task)
        opened = {k: v for k, v in task.items() if k != "completed"}
        self.tasks.append(opened)
        self._set_status(f"已改回未完成「{task['title']}」")
        self.redraw()

        def done(_, err):
            if err:
                if opened in self.tasks:
                    self.tasks.remove(opened)
                self.done_today.append(task)
                self.redraw()
                self._show_error(err)
        self.run_write(lambda: self.g.uncomplete_task(task["list_id"], task["id"]), done)

    def _placeholder_text(self):
        return "新增工作，例如：明天 3點 開會"

    def _update_quick_chips(self):
        """快捷輸入時，在輸入列上方即時顯示辨識出的日期、時間、重複與清單。"""
        text = "" if self.placeholder_on else self.entry.get()
        if not text.strip():
            self.lbl_quick_chips.pack_forget()
            return
        parsed = parse_quick(text, self.lists, dt.date.today(), self._now(), default_due=self.selected)
        lst = parsed["list"] or self.current_list()
        parts = [f"📅 {date_text(parsed['due'])}"]
        if parsed["time"]:
            parts.append(f"⏰ {time_text(parsed['time'])}")
        if parsed["recur"]:
            parts.append(f"🔁 {format_recur(parsed['recur'])}")
        if lst:
            parts.append(f"📁 {lst.get('title', '')}")
        self.lbl_quick_chips.config(text="　".join(parts))
        if not self.lbl_quick_chips.winfo_ismapped():
            self.lbl_quick_chips.pack(side="bottom", fill="x", padx=px(22), before=self.list_wrap)

    def _on_add_click(self):
        """點「＋」：開啟新增頁（可一次設定日期、提醒、重複、清單與詳細資訊）；輸入框已打的字帶進標題。"""
        title = "" if self.placeholder_on else self.entry.get().strip()
        if title:
            self.entry.delete(0, "end")
        self.open_new_task(title)

    # ---- 新增頁（點「＋」）：版面比照詳細頁，按「建立」才送出

    def open_new_task(self, title=""):
        if self.detail:
            self.close_detail()
        if not self.g.refresh_token or not self.lists:
            self._set_status("請先登入 Google（右上角「登入」）" if not self.g.refresh_token
                             else "尚未讀到任何清單，請先同步", error=True)
            return
        list_index = self.list_index if 0 <= self.list_index < len(self.lists) else 0
        due, time_value, rule = self.new_due, None, None
        if title:  # 底部輸入框已經打了「明天 3點 開會」：先辨識，日期時間填進欄位，標題只留文字
            parsed = parse_quick(title, self.lists, dt.date.today(), self._now(), default_due=self.new_due)
            title, due, time_value, rule = parsed["title"], parsed["due"], parsed["time"], parsed["recur"]
            if parsed["list"]:
                list_index = self.lists.index(parsed["list"])
        self.draft = {"title_draft": title, "notes_draft": "", "due": due, "time": time_value,
                      "recur": rule, "list_index": list_index, "busy": False}
        self._hide_undo()
        self.main_view.pack_forget()
        self._render_new_task()
        self.detail_view.pack(fill="both", expand=True)
        self._show_page_title("新增工作")
        self.draft_title.focus_force()

    def _render_new_task(self):
        view, draft = self.detail_view, self.draft
        for w in view.winfo_children():
            w.destroy()
        pad = {"padx": px(18)}

        # 版面：文字欄位（標題、詳細資訊）標籤在上方；屬性（清單、日期、重複）左標籤對齊成一組
        tk.Label(view, text="標題（必填）", bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(
            fill="x", pady=(px(8), 0), **pad)
        self.draft_title = self._rounded_field(
            view, lambda parent: tk.Entry(parent, font=SUBJECT_FONT, relief="flat",
                                          bg=CARD_BG, fg=TEXT, insertbackground=TEXT, highlightthickness=0),
            pady=(px(2), px(10)), **pad)
        self.draft_title.insert(0, draft["title_draft"])

        row_list = self._prop_row(view, "清單", pad)
        current = self.lists[draft["list_index"]].get("title", "")
        self.draft_list = Pill(row_list, f"📁 {current} ▾", lambda: self._open_draft_list_menu(self.draft_list),
                               style="soft", padx=10, pady=3)
        self.draft_list.pack(side="left")
        Tooltip(self.draft_list, "要放進哪個清單；也可以新增清單")

        row = self._prop_row(view, "日期", pad, top=px(6))
        btn_due = Pill(row, f"📅 {date_text(draft['due'])} ▾",
                       lambda: DatePicker(self, btn_due, draft["due"], self._set_draft_due), style="soft", padx=10, pady=3)
        btn_due.pack(side="left", padx=(0, px(6)))
        at = time_text(draft["time"])
        btn_time = Pill(row, f"⏰ {at} ▾" if at else "⏰ 加提醒時間 ▾",
                        lambda: TimePicker(self, btn_time, draft["time"], self._set_draft_time),
                        style="soft", padx=10, pady=3)
        btn_time.pack(side="left")
        row_recur = self._prop_row(view, "重複", pad, top=px(6))
        rule = draft["recur"]
        btn_recur = Pill(row_recur, f"🔁 {format_recur(rule)} ▾" if rule else "🔁 不重複 ▾",
                         lambda: RecurPicker(self, btn_recur, draft["recur"], draft["due"] or dt.date.today(),
                                             self._set_draft_recur),
                         style="soft", padx=10, pady=3)
        btn_recur.pack(side="left")

        tk.Label(view, text="詳細資訊", bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(
            fill="x", pady=(px(12), 0), **pad)
        self.draft_notes = self._rounded_field(
            view, lambda parent: tk.Text(parent, height=5, wrap="word", font=FONT, relief="flat", bg=CARD_BG,
                                         fg=TEXT, insertbackground=TEXT, highlightthickness=0),
            expand=True, pady=(px(2), 0), **pad)
        self.draft_notes.insert("1.0", draft["notes_draft"])
        tk.Label(view, text="子工作：建立後點這筆工作就能新增", bg=BG, fg=GRAY, font=SMALL,
                 anchor="w").pack(fill="x", pady=(px(10), 0), **pad)

        foot = tk.Frame(view, bg=BG)
        foot.pack(side="bottom", fill="x", pady=px(8), **pad)
        create = Pill(foot, "＋ 建立", self.create_new_task, style="primary", padx=16)
        create.pack(side="right")
        Tooltip(create, "建立這筆工作（在標題按 Enter 也可以）")
        Pill(foot, "取消", self.cancel_new_task, style="outline").pack(side="right", padx=(0, px(6)))
        self.draft_title.bind("<Return>", lambda e: self.create_new_task())
        for widget in (self.draft_title, self.draft_notes):
            widget.bind("<Escape>", lambda e: self.cancel_new_task())

    def _rounded_field(self, parent, factory, expand=False, **pack):
        """圓角輸入框：factory(容器) 建立 Entry／Text，外框在取得焦點時變成強調色。回傳輸入元件。

        expand=True：輸入框往下長、填滿頁面剩餘空間（詳細資訊欄），頁面下方不留一大片空白。
        """
        box = RoundedBox(parent, fill=CARD_BG, outline=BORDER, radius=14, padx=10, pady=6, stretch=expand)
        box.pack(fill="both" if expand else "x", expand=expand, **pack)
        widget = factory(box.inner)
        widget.pack(fill="both" if expand else "x", expand=True)
        widget.bind("<FocusIn>", lambda e: box.set_outline(ACCENT), add="+")
        widget.bind("<FocusOut>", lambda e: box.set_outline(BORDER), add="+")
        return widget

    def _open_draft_list_menu(self, anchor):
        """新增頁的清單選單：列出所有清單（目前的打勾）與「新增清單」。"""
        menu = self.draft_list_menu = PopupMenu(self)
        for index, lst in enumerate(self.lists):
            menu.add_command(label=lst.get("title", ""), command=lambda i=index: self.choose_draft_list(i),
                             checked=index == self.draft["list_index"])
        menu.add_separator()
        menu.add_command(label=ADD_LIST_OPTION, command=lambda: self.choose_draft_list(len(self.lists)))
        menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())

    def choose_draft_list(self, index):
        """選定新增頁的清單；index 等於清單數量代表「新增清單」：建立後選取它。"""
        if not self.draft:
            return
        if index < len(self.lists):
            self.draft["list_index"] = index
            self._rerender_new_task()
            return

        def created(new_index):
            if self.draft:
                self.draft["list_index"] = new_index
                self._rerender_new_task()
        self._prompt_new_list(created)

    def _rerender_new_task(self):
        """重畫前先暫存輸入中的標題與詳細資訊。"""
        self.draft["title_draft"] = self.draft_title.get()
        self.draft["notes_draft"] = self.draft_notes.get("1.0", "end-1c")
        self._render_new_task()

    def _set_draft_due(self, day):
        self.draft["due"] = day
        if day is None:  # 沒有日期就不能提醒，也不能重複
            self.draft["time"] = None
            self.draft["recur"] = None
        self._rerender_new_task()

    def _set_draft_time(self, value):
        self.draft["time"] = value
        if value and self.draft["due"] is None:
            self.draft["due"] = dt.date.today()
        self._rerender_new_task()

    def _set_draft_recur(self, rule):
        self.draft["recur"] = rule
        if rule and self.draft["due"] is None:
            self.draft["due"] = dt.date.today()
        self._rerender_new_task()

    def _draft_has_content(self):
        return bool(self.draft_title.get().strip() or self.draft_notes.get("1.0", "end-1c").strip())

    def cancel_new_task(self, ask=True):
        """離開新增頁；已經打了內容時先確認，避免誤按 Esc 弄丟。可以離開時回傳 True。"""
        if not getattr(self, "draft", None):
            return True
        if ask and self._draft_has_content() and not messagebox.askyesno(
                "放棄新增", "這筆工作還沒建立，確定要放棄嗎？", parent=self, icon="warning",
                yes="放棄", no="繼續編輯"):
            return False
        self.draft = None
        self.detail_view.pack_forget()
        self.main_view.pack(fill="both", expand=True)
        self.redraw()
        return True

    def create_new_task(self):
        draft = self.draft
        if not draft or draft["busy"]:
            return
        title = self.draft_title.get().strip()
        if not title:
            self._set_status("請輸入工作標題", error=True)
            self.draft_title.focus_force()
            return
        lst = self.lists[draft["list_index"]]
        body = self.draft_notes.get("1.0", "end-1c").strip()
        due, time_value, rule = draft["due"], draft["time"], draft["recur"]
        draft["busy"] = True
        self._set_status("新增中…")

        def done(task, err):
            draft["busy"] = False
            if err:
                self._show_error(err)  # 留在新增頁，內容不會不見
                return
            if self.draft is draft:
                self.draft = None
                self.detail_view.pack_forget()
                self.main_view.pack(fill="both", expand=True)
            self._remember_list(draft["list_index"])  # 下次新增沿用這個清單
            if due and due != self.selected:
                self.select(due)  # 跳到那一天，馬上看得到剛建立的工作
            self.redraw()
            when = f"，{time_text(time_value)} 提醒" if time_value else ""
            self._set_status(f"已新增「{title}」到「{lst.get('title', '')}」{when}")
        self._create_task(title, due, time_value, rule, lst, done, body=body)

    # ---- 詳細頁（比照 Google Tasks App：標題、詳細資訊、日期、子工作、刪除、標示為完成）

    def _find_task(self, task_id):
        return next((t for t in self.tasks if t["id"] == task_id), None)

    def open_detail(self, task):
        if not self.cancel_new_task():  # 新增頁還有內容且使用者選擇保留：不切換
            return
        if self.detail:
            self.close_detail()  # 從子工作切換時，先儲存目前這筆
        task = self._find_task(task["id"]) or task
        self.detail = {"task": task, "due": task["due"], "time": task.get("time"), "recur": task.get("recur")}
        self._hide_undo()
        self.main_view.pack_forget()
        self._render_detail()
        self.detail_view.pack(fill="both", expand=True)
        self._show_page_title("工作詳細資訊")

    def _render_detail(self):
        view = self.detail_view
        for w in view.winfo_children():
            w.destroy()
        task = self.detail["task"]
        pad = {"padx": px(18)}

        # 版面和新增頁一致：標題 → 屬性（清單、日期、重複）→ 詳細資訊 → 子工作；返回箭頭在頁面標題左邊
        tk.Label(view, text="標題", bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(fill="x", pady=(px(8), 0), **pad)
        self.detail_title = self._rounded_field(
            view, lambda parent: tk.Entry(parent, font=SUBJECT_FONT, relief="flat",
                                          bg=CARD_BG, fg=TEXT, insertbackground=TEXT, highlightthickness=0),
            pady=(px(2), px(10)), **pad)
        self.detail_title.insert(0, self.detail.get("title_draft", task["title"]))

        # 既有工作不能換清單：和日期、重複一樣用膠囊（同高、同字級、有圖示），灰底沒有箭頭表示不能點
        row_list = self._prop_row(view, "清單", pad)
        chip_list = Pill(row_list, f"📁 {task['list_title']}", None, style="static", padx=10, pady=3)
        chip_list.pack(side="left")
        Tooltip(chip_list, "已建立的工作不能換清單")
        row = self._prop_row(view, "日期", pad, top=px(6))
        btn_due = Pill(row, f"📅 {date_text(self.detail['due'])} ▾",
                       lambda: DatePicker(self, btn_due, self.detail["due"], self._set_detail_due),
                       style="soft", padx=10, pady=3)
        btn_due.pack(side="left", padx=(0, px(6)))
        at = time_text(self.detail["time"])
        btn_time = Pill(row, f"⏰ {at} ▾" if at else "⏰ 加提醒時間 ▾",
                        lambda: TimePicker(self, btn_time, self.detail["time"], self._set_detail_time),
                        style="soft", padx=10, pady=3)
        btn_time.pack(side="left")
        if not task.get("parent"):  # 子工作不支援週期
            row_recur = self._prop_row(view, "重複", pad, top=px(6))
            rule = self.detail["recur"]
            btn_recur = Pill(row_recur, f"🔁 {format_recur(rule)} ▾" if rule else "🔁 不重複 ▾",
                             lambda: RecurPicker(self, btn_recur, self.detail["recur"],
                                                 self.detail["due"] or dt.date.today(), self._set_detail_recur),
                             style="soft", padx=10, pady=3)
            btn_recur.pack(side="left")

        tk.Label(view, text="詳細資訊", bg=BG, fg=GRAY, font=SMALL, anchor="w").pack(
            fill="x", pady=(px(12), 0), **pad)
        self.detail_notes = self._rounded_field(
            view, lambda parent: tk.Text(parent, height=5, wrap="word", font=FONT, relief="flat", bg=CARD_BG,
                                         fg=TEXT, insertbackground=TEXT, highlightthickness=0),
            expand=True, pady=(px(2), 0), **pad)
        self.detail_notes.insert("1.0", self.detail.get("notes_draft", task.get("notes", "")))
        if task.get("parent"):
            tk.Label(view, text="（這是子工作，在主畫面會顯示在父工作下方）", bg=BG, fg=GRAY, font=SMALL,
                     anchor="w").pack(fill="x", pady=(px(4), 0), **pad)
        else:
            self._render_subtasks(view, task, pad)

        foot = tk.Frame(view, bg=BG)
        foot.pack(side="bottom", fill="x", pady=px(8), **pad)
        # 刪除放在左下角、和完成／儲存分開，避免誤按；平常是灰色圖示，滑過才轉紅
        btn_delete = self._icon_button(foot, "刪除", self.delete_detail, font=FONT, tip="刪除這筆工作")
        if ICON_GLYPH_FONT:
            btn_delete.config(text="", font=(ICON_GLYPH_FONT, 11))
        btn_delete.bind("<Enter>", lambda e: btn_delete.config(fg=RED), add="+")
        btn_delete.bind("<Leave>", lambda e: btn_delete.config(fg=GRAY), add="+")
        btn_delete.pack(side="left")
        Pill(foot, "✓ 標示為完成", self.complete_detail, style="primary").pack(side="right")
        save = Pill(foot, "儲存", self.close_detail, style="outline")
        save.pack(side="right", padx=(0, px(6)))
        Tooltip(save, "儲存修改並返回（Esc）")
        if not task.get("parent") and self.detail["recur"]:
            skip = Pill(foot, "略過這一期", self.skip_detail, style="outline")
            skip.pack(side="right", padx=(0, px(6)))
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
        entry = self._rounded_field(
            view, lambda parent: tk.Entry(parent, font=FONT, relief="flat", bg=CARD_BG, fg=GRAY,
                                          insertbackground=TEXT, highlightthickness=0),
            pady=(px(4), 0), **pad)
        entry.insert(0, "＋ 新增子工作（Enter）")

        def clear_hint(_):
            if entry.cget("fg") == GRAY:
                entry.delete(0, "end")
                entry.config(fg=TEXT)
        entry.bind("<FocusIn>", clear_hint, add="+")
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
            fields["due"] = due_field(due)
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
            self.run_write(lambda: self.g.update_task(task["list_id"], task["id"], fields), done)
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
        if not messagebox.askyesno("刪除工作", delete_prompt(task, len(children)), parent=self, icon="warning",
                                   yes="刪除", no="取消"):
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
        self.run_write(work, done)

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
        self.run_write(lambda: self.g.add_task(lst["id"], title, None, parent=parent["id"]), done)

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
            if not self.g.refresh_token:
                # 登入已失效：清掉舊帳號的資料，比照登出，大日期旁的「下一件」和月曆圓點才不會留著舊內容
                self.lists, self.tasks, self.events = [], [], {}
                self.done_today = []
                self.list_index = -1
            if self.detail or self.draft:
                self._draw_list()        # 編輯中的頁面不動，只把背後的清單換成登入提示
            else:
                self.redraw()            # 清單區顯示登入提示，月曆與「下一件」一併更新
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

    def _cal_cell_at(self, x, y):
        """Canvas 座標所在的日期；不在日期格上回傳 None。"""
        row, col = (y - HEAD_H) // CELL_H, x // CELL_W
        if y >= HEAD_H and 0 <= row < len(self.weeks) and 0 <= col < 7:
            return self.weeks[row][col]
        return None

    def _on_cal_hover(self, event):
        self._paint_cal_hover(self._cal_cell_at(event.x, event.y))

    def _paint_cal_hover(self, day):
        """在滑過的日期畫圓角細外框（選中的日期已有底色就不畫）；None 表示清掉。"""
        if day == self.cal_hover:
            return
        self.cal_hover = day
        self.cal.delete("hover")
        if day is None or day == self.selected:
            return
        left, top, _, _ = self._cal_cell_rect(day)
        cy = top + px(17)  # 和 _draw_calendar 的選取框同一個中心點
        # 只畫細外框不填色：和選中日期的實心底色區分，不會被誤認成已選取
        draw_rounded(self.cal, CELL_W - px(4), px(32), px(16), BG, BG, outline=BORDER, tag="hover",
                     x0=left + px(2), y0=cy - px(16))
        self.cal.tag_lower("hover")

    def _cal_cell_rect(self, day):
        for row, week in enumerate(self.weeks):
            if day in week:
                col = week.index(day)
                return col * CELL_W, HEAD_H + row * CELL_H, (col + 1) * CELL_W, HEAD_H + (row + 1) * CELL_H
        return 0, 0, CELL_W, CELL_H

    def _day_tip_text(self, day):
        if day is None:
            return ""
        tasks = sum(1 for t in self._top_level() if t["due"] == day)
        previews = len(self._previews(day, day).get(day, []))
        return day_summary(day, self.today, tasks, previews, len(self.events.get(day, [])),
                           self.holidays.get(day, []))

    def _on_cal_click(self, event):
        row, col = (event.y - HEAD_H) // CELL_H, event.x // CELL_W
        if event.y >= HEAD_H and 0 <= row < len(self.weeks) and 0 <= col < 7:
            self.select(self.weeks[row][col])

    def _toggle_undated(self):
        self.show_undated = not self.show_undated
        self._draw_list()

    def _toggle_done(self):
        self.show_done = not self.show_done
        self._draw_list()

    def _toggle_overdue(self):
        self.show_overdue = not self.show_overdue
        self._draw_list()

    def toggle_compact(self):
        self.compact = not self.compact
        self.state_data["compact_calendar"] = self.compact
        self.redraw()

    def _select_remembered_list(self):
        """同步後選回上次用的清單；找不到（例如被刪掉）就用第一個。"""
        list_ids = [lst["id"] for lst in self.lists]
        last = self.state_data.get("last_list_id")
        if last in list_ids:
            self.list_index = list_ids.index(last)
        elif not 0 <= self.list_index < len(self.lists):
            self.list_index = 0 if self.lists else -1

    def _prompt_new_list(self, on_created=None):
        """詢問名稱並建立清單；成功後設為預設清單，並以新清單的索引呼叫 on_created。"""
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
            self._remember_list(len(self.lists) - 1)
            self._set_status(f"已建立清單「{name}」，新增的工作會放進這個清單")
            if on_created:
                on_created(len(self.lists) - 1)
        self.run_write(lambda: self.g.add_tasklist(name), done)

    def _remember_list(self, index):
        """設定之後新增工作預設放進的清單，並記住（重開 DayNote 沿用）。"""
        if 0 <= index < len(self.lists):
            self.list_index = index
            self.state_data["last_list_id"] = self.lists[index]["id"]

    # ---- 電腦端提醒（方案 B：時間存在詳細資訊第一行）

    def _check_reminders(self):
        """每隔 REMINDER_CHECK_MS 檢查一次提醒；某次檢查出錯也會繼續排下一次，不讓當天之後的提醒全部消失。"""
        try:
            self._fire_due_reminders()
        finally:
            self.after(REMINDER_CHECK_MS, self._check_reminders)

    def _fire_due_reminders(self):
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
                log.info("Reminder skipped (too late): task=%s scheduled=%s now=%s",
                         task["id"], when.isoformat(), now.isoformat(timespec="seconds"))
                continue  # 錯過太久（例如關機好幾天）就不補跳
            log.info("Reminder fired: task=%s scheduled=%s fire_at=%s now=%s",
                     task["id"], when.isoformat(), fire_at.isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds"))
            ReminderToast(self, task, when,
                          on_done=lambda t=task: self.complete(t),
                          on_snooze=lambda label, fire_at, k=key: self._snooze(k, label, fire_at))
        if changed:
            self._save_reminder_state()

    def _snooze(self, key, label, fire_at):
        """延後提醒到 fire_at；記在 state.json，重開 DayNote 也會照時間再提醒。"""
        self.reminded.discard(key)
        self.snoozed[key] = fire_at
        self._save_reminder_state()
        self._set_status(f"{label}再提醒")

    def _save_reminder_state(self):
        """已提醒記錄寫進 daynote_state.json，重開不會重複提醒；只保留最近 300 筆。"""
        self.state_data["reminded"] = sorted(self.reminded, key=lambda k: k.split("@")[-1])[-300:]
        self.state_data["snoozed"] = {k: v.isoformat() for k, v in self.snoozed.items()}
        save_state(self.state_data)

    def _watch_pid(self):
        """單一執行個體：PID 檔被移除或換成別的 PID（新開的 DayNote），就正常關閉並存好狀態。

        檔案暫時讀不到（防毒掃描、OneDrive 同步鎖住）不算被取代：連續 PID_READ_RETRIES 次都讀不到才關閉。
        """
        try:
            with open(PID_PATH, encoding="ascii") as f:
                still_mine = f.read().strip() == str(os.getpid())
            self._pid_read_failures = 0
        except FileNotFoundError:
            still_mine = False
        except OSError as e:
            self._pid_read_failures = getattr(self, "_pid_read_failures", 0) + 1
            log.warning("Failed to read pid file: attempt=%d error=%s", self._pid_read_failures, e)
            still_mine = self._pid_read_failures < PID_READ_RETRIES
        if still_mine:
            self.after(1000, self._watch_pid)
        else:
            self.close(ask=False)

    def _bind_shortcuts(self):
        """Ctrl+N 新增、←→ 前後一天、↑↓ 前後一週、Home 今天、F5 同步、Esc 離開輸入框。"""
        self.bind("<Control-n>", lambda e: self.detail or self.draft or self.entry.focus_force())
        self.bind("<Control-N>", lambda e: self.detail or self.draft or self.entry.focus_force())
        self.bind("<F5>", lambda e: self.refresh())
        self.bind("<F1>", lambda e: self.open_help())
        for key, days in (("<Left>", -1), ("<Right>", 1), ("<Up>", -7), ("<Down>", 7)):
            self.bind(key, lambda e, d=days: self._shift_day(d))
        self.bind("<Home>", lambda e: self._shift_day(None))
        # 點月曆或清單時取得鍵盤焦點，方向鍵才有作用
        for widget in (self.cal, self.list_canvas):
            widget.bind("<Button-1>", lambda e, w=widget: w.focus_set(), add="+")

    def open_help(self):
        """使用說明視窗：快捷鍵＋常用操作，最下面連到 README 完整說明。已開著就移到最上層。"""
        if getattr(self, "help_win", None) and self.help_win.winfo_exists():
            self.help_win.lift()
            return
        win = self.help_win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG, highlightthickness=1, highlightbackground=BORDER)
        body = tk.Frame(win, bg=BG, padx=px(18), pady=px(14))
        body.pack(fill="both", expand=True)

        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="使用說明", bg=BG, fg=TEXT, font=TITLE_FONT, anchor="w").pack(side="left")
        close = self._icon_button(head, "✕", win.destroy, tip="關閉（Esc）")
        close.pack(side="right")

        tk.Label(body, text="快捷鍵", bg=BG, fg=ACCENT, font=HEADING_FONT, anchor="w").pack(
            fill="x", pady=(px(10), px(4)))
        keys = tk.Frame(body, bg=BG)
        keys.pack(fill="x")
        for row, (key, desc) in enumerate(HELP_SHORTCUTS):
            # 按鍵畫成圓角小標籤（不能點，只是外觀）
            chip = Pill(keys, key, lambda: None, style="soft", font=EVENT_TIME_FONT, padx=8, pady=1)
            chip.config(cursor="")
            chip.unbind("<Button-1>")
            chip.grid(row=row, column=0, sticky="w", pady=px(2))
            tk.Label(keys, text=desc, bg=BG, fg=TEXT, font=FONT, anchor="w").grid(
                row=row, column=1, sticky="w", padx=(px(10), 0))

        tk.Label(body, text="常用操作", bg=BG, fg=ACCENT, font=HEADING_FONT, anchor="w").pack(
            fill="x", pady=(px(12), px(4)))
        for tip in HELP_TIPS:
            tk.Label(body, text=f"・{tip}", bg=BG, fg=TEXT, font=FONT, anchor="w", justify="left").pack(fill="x")

        bottom = tk.Frame(body, bg=BG)
        bottom.pack(fill="x", pady=(px(14), 0))
        self.btn_help_more = Pill(bottom, "完整說明（GitHub）", lambda: webbrowser.open(USAGE_URL),
                                  style="soft", padx=12, pady=3)
        self.btn_help_more.pack(side="left")
        Pill(bottom, "知道了", win.destroy, style="primary", padx=14, pady=3).pack(side="right")

        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()  # 置中在主視窗上；超出螢幕時往內收
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.winfo_rootx() + (self.winfo_width() - w) // 2
        y = self.winfo_rooty() + px(60)
        screen = win_virtual_screen()
        if screen:
            x = min(max(x, screen[0]), screen[2] - w)
            y = min(max(y, screen[1]), screen[3] - h)
        win.geometry(f"+{x}+{y}")
        win_style_popup(win)
        win.focus_force()

    def _shift_day(self, days):
        if self.detail or self.draft or isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
            return  # 詳細頁、新增頁或輸入框中，方向鍵交給輸入框移動游標
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
        """未登入時在「⋯」左邊顯示「登入」；登入後收起來，選單裡的「立即同步」「登出 Google」才可用。"""
        if show:
            self.btn_login.pack(side="left", padx=(0, px(4)), before=self.btn_more)
        else:
            self.btn_login.pack_forget()
        state = "disabled" if show else "normal"  # 未登入時同步只會失敗，先停用避免誤會
        self.menu_more.entryconfig("立即同步（F5）", state=state)
        self.menu_more.entryconfig("登出 Google", state=state)

    def _open_more_menu(self):
        """在「⋯」按鈕正下方展開選單，右緣對齊按鈕。"""
        x = self.btn_more.winfo_rootx() + self.btn_more.winfo_width()
        y = self.btn_more.winfo_rooty() + self.btn_more.winfo_height() + px(4)
        self.menu_more.tk_popup(x, y, align="right")

    def logout(self):
        if self.busy or not self.g.refresh_token:
            return
        if not messagebox.askyesno("登出", "確定要登出 Google？\n\n會撤銷 DayNote 的存取權，並刪除這台電腦上的登入資料，"
                                   "之後需要重新登入。", parent=self, icon="warning", yes="登出", no="取消"):
            return
        self.close_detail()  # 詳細頁有修改時先儲存
        self.cancel_new_task(ask=False)  # 已確認登出，新增頁一併關閉
        self.busy = True
        self._set_status("正在登出…")
        self.run_bg(self.g.revoke, self._after_logout)

    def _after_logout(self, revoked, err):
        self.busy = False
        self.lists, self.tasks, self.events = [], [], {}
        self.done_today = []
        self.list_index = -1
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
        # 開始時已有寫入還沒送完，或同步期間又有新的寫入，讀到的任務就可能是舊的（見 _after_load）
        stale_from = None if self.pending_writes else self.write_seq
        self.run_bg(lambda: self._load(view), lambda result, err: self._after_load(result, err, stale_from))

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
        today = now.date()
        # 今天完成的任務（含手機上完成的），給清單底部的「已完成」區塊
        midnight = dt.datetime.combine(today, dt.time())
        done_today = []
        for lst in lists:
            try:
                done_today += [completed_task(raw, lst) for raw in self.g.completed_since(lst["id"], midnight)
                               if raw.get("status") == "completed" and not raw.get("deleted")]
            except ApiError as e:  # 只是附加資訊，抓不到不影響這次同步
                log.warning("Failed to load completed tasks: list=%s error=%s", lst["id"], e)
        mark = now
        if self.backfill_since is not None:  # 第一次同步只記錄時間點，不回溯歷史
            spawned, ok = backfill_recurring(self.g, lists, self.backfill_since, now.date())
            tasks.extend(spawned)
            mark = now if ok else self.backfill_since  # 有失敗就不前進，下次重試（防重複確保不會多建）
        return view, lists, tasks, events, self._load_holidays(start, end), mark, done_today

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

    def _after_load(self, result, err, write_seq=None):
        """套用同步結果。write_seq 為同步開始時的寫入序號（None＝開始時就有寫入沒送完）；

        同步期間有寫入，就丟掉這次結果，等寫入都完成再同步一次，避免舊資料蓋掉剛做的修改。
        """
        self.busy = False
        if err:
            self._show_error(err)
            return
        if write_seq is None or write_seq != self.write_seq or self.pending_writes:
            log.info("Sync result discarded: writes happened during sync (pending=%d)", self.pending_writes)
            if self.pending_writes:
                self.resync_needed = True  # 最後一筆寫入完成時會再同步
            else:
                self.after_idle(self.refresh)
            return
        view, self.lists, self.tasks, self.events, self.holidays, mark, self.done_today = result
        if self.detail:
            self.detail["task"] = self._find_task(self.detail["task"]["id"]) or self.detail["task"]
        self.backfill_since = mark
        self.state_data["backfill_since"] = mark.isoformat()
        save_state(self.state_data)
        backfilled = [t for t in self.tasks if t.pop("backfilled", False)]
        if any([mark_past_reminder(self.reminded, t, self._now()) for t in backfilled]):
            self._save_reminder_state()
        self._show_login_button(False)
        self._select_remembered_list()
        self.synced_text = f"已同步 {dt.datetime.now():%H:%M}"
        self._set_status(self.synced_text)
        self._show_first_run_tip()
        self.redraw()
        if view != self.view:  # 讀取期間使用者切換了月份
            self.refresh()

    def _show_first_run_tip(self):
        """第一次同步成功後，在狀態列提示隱藏的快捷鍵（只提示一次），過一段時間換回同步時間。"""
        if self.state_data.get("tips_shown"):
            return
        self.state_data["tips_shown"] = True
        save_state(self.state_data)
        self._set_status(FIRST_RUN_TIP)

        def restore():
            if self.status.cget("text") == FIRST_RUN_TIP:  # 期間沒有其他訊息蓋過才換回
                self._set_status(self.synced_text)
        self.after(FIRST_RUN_TIP_MS, restore)

    def _build_task_menu(self, task):
        """待辦的右鍵選單：改日期（今天／明天／不排日期）、完成、開啟詳細資訊。子工作跟著父任務，不提供改日期。"""
        menu = PopupMenu(self)
        if not self._is_child(task):
            today = self.today
            for label, day in (("移到今天", today), ("移到明天", today + dt.timedelta(days=1)), ("不排日期", None)):
                menu.add_command(label=label, command=lambda d=day: self.reschedule([task], d),
                                 state="disabled" if task["due"] == day else "normal")
            menu.add_separator()
        menu.add_command(label="✓ 完成", command=lambda: self.complete(task))
        menu.add_command(label="開啟詳細資訊", command=lambda: self.open_detail(task))
        return menu

    def _popup_task_menu(self, task, event):
        self.task_menu = self._build_task_menu(task)
        self.task_menu.tk_popup(event.x_root, event.y_root)

    def _is_child(self, task):
        ids = {t["id"] for t in self.tasks}
        return bool(task.get("parent")) and task["parent"] in ids

    def reschedule(self, tasks, day):
        """把待辦移到 day（None＝不排日期）：先更新畫面，背景送出；失敗就還原並顯示錯誤。

        不排日期時一併清掉提醒時間與重複規則（沒有日期就不能提醒，也不能重複）。
        """
        changes = []
        for task in tasks:
            if task not in self.tasks or task["due"] == day:
                continue
            fields = {"due": due_field(day)}
            if day is None and (task.get("time") or task.get("recur")):
                fields["notes"] = join_meta(None, None, task.get("notes", ""))
            changes.append((task, fields))
        if not changes:
            return
        where = date_text(day)
        done_text = (f"已把「{changes[0][0]['title']}」移到{where}" if len(changes) == 1
                     else f"已把 {len(changes)} 件移到{where}")
        self._send_task_changes(changes, done_text, undoable=True)

    def _send_task_changes(self, changes, done_text, undoable):
        """送出一組任務修改 [(任務, API 欄位)]：先更新畫面，背景送出；失敗就還原並顯示錯誤。

        undoable 時，成功後在狀態列提供「復原」，按下會把每筆改回修改前的欄位（反向再送一次）。
        """
        befores = [dict(task) for task, _ in changes]
        for task, fields in changes:
            apply_fields(task, fields)
        self.redraw()
        self._set_status("儲存中…")

        def work():
            for task, fields in changes:
                self.g.update_task(task["list_id"], task["id"], fields)

        def done(_, err):
            if err:
                for (task, _), before in zip(changes, befores):
                    task.update(before)  # 還原畫面，下次同步以 Google 上的資料為準
                self.redraw()
                self._show_error(err)
            elif undoable:
                reverse = [(task, restore_fields(before, fields)) for (task, fields), before in zip(changes, befores)]
                self._offer_undo_action(done_text, lambda: self._send_task_changes(
                    [(t, f) for t, f in reverse if t in self.tasks], "已復原", undoable=False))
            else:
                self._set_status(done_text)
        self.run_write(work, done)

    def _offer_undo_action(self, text, action):
        """狀態列顯示 text 與「復原」，UNDO_SECONDS 秒內按「復原」會執行 action。"""
        self._set_status(text)
        self.undo_action = action
        self.btn_undo.pack(side="right", padx=px(4), before=self.status)
        self.undo_job = self.after(UNDO_SECONDS * 1000, self._hide_undo)

    def undo(self):
        """狀態列「復原」：有其他動作就復原它，否則復原剛完成的待辦。"""
        action = self.undo_action
        if action:
            self._hide_undo()
            action()
        else:
            self.undo_complete()

    def complete(self, task):
        """標記完成；比照 Google Tasks App，完成父任務時子任務一併完成。

        API 是否會自動連帶完成子任務，官方文件未說明，所以這裡逐一送出，不依賴該行為。
        """
        if task not in self.tasks:
            return
        group = [task] + self._children(task)
        for t in group:  # 先從畫面移除，失敗再放回
            self.tasks.remove(t)
        finished = [dict(t, completed=self._now()) for t in group]
        self.done_today.extend(finished)  # 立刻出現在「已完成」區塊
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
                self._drop_done(group)
                self.redraw()
                self._show_error(err)
                return
            spawned, spawn_err = result
            self._add_spawned(spawned)
            warning = f"已完成「{task['title']}」，但下一期建立失敗：{spawn_err}" if spawn_err else None
            self._offer_undo(group, [spawned] if spawned else [], warning)
        self.run_write(work, done)

    def _drop_done(self, group):
        """從「已完成」區塊拿掉這些任務（完成失敗或復原時）。"""
        ids = {t["id"] for t in group}
        self.done_today = [t for t in self.done_today if t["id"] not in ids]

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
                parent=self, icon="question", yes="略過", no="取消"):
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
        self.run_write(work, done)

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
        self.undo_action = None
        self.btn_undo.pack_forget()

    def undo_complete(self):
        group, spawned = self.undo_group, list(self.undo_spawned or [])
        if not group:
            return
        self._hide_undo()
        self.tasks.extend(group)
        self._drop_done(group)
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
        self.run_write(work, done)

    def add_task(self):
        """底部快捷新增（Enter 或「新增」）：一行文字辨識日期、時間、重複與 #清單，沒寫日期就放在月曆選中的那天。"""
        text = "" if self.placeholder_on else self.entry.get().strip()
        if not text:
            self.entry.focus_force()
            return
        if not self.g.refresh_token or not self.lists:
            msg = "請先登入 Google（右上角「登入」）" if not self.g.refresh_token else "尚未讀到任何清單，請先同步"
            self._set_status(msg, error=True)
            return
        parsed = parse_quick(text, self.lists, dt.date.today(), self._now(), default_due=self.selected)
        if not parsed["title"]:
            self._set_status("請輸入工作內容（目前只有日期或時間）", error=True)
            return
        lst = parsed["list"] or self.current_list()
        due, time_value, rule, title = parsed["due"], parsed["time"], parsed["recur"], parsed["title"]
        self.entry.delete(0, "end")
        self._update_quick_chips()
        self._set_status("新增中…")

        def done(task, err):
            if err:
                self._hide_placeholder()
                self.entry.insert(0, text)  # 放回原本整行，修正後可以直接再送出
                self._update_quick_chips()
                self._show_error(err)
                return
            when = f"，{time_text(time_value)} 提醒" if time_value else ""
            elsewhere = f"（{date_text(due)}）" if due != self.selected else ""
            self._set_status(f"已新增「{title}」到「{lst.get('title', '')}」{elsewhere}{when}")
            self.redraw()
        self._create_task(title, due, time_value, rule, lst, done)

    def _create_task(self, title, due, time_value, rule, lst, on_done, body=""):
        """（底部輸入列、新增頁與快速列共用）在 Google 建立待辦並加入畫面；完成後呼叫 on_done(畫面上的任務, 錯誤)。"""
        def done(created, err):
            if err:
                on_done(None, err)
                return
            task = {"id": created["id"], "title": created.get("title") or title,
                    "list_id": lst["id"], "list_title": lst.get("title", ""),
                    "due": task_due(created), "parent": None, "notes": body, "time": time_value,
                    "recur": rule, "position": created.get("position", "")}
            self.tasks.append(task)
            on_done(task, None)
        notes = join_meta(time_value, rule, body)
        self.run_write(lambda: self.g.add_task(lst["id"], title, due, notes=notes or None), done)

    # ---- 快速列（Ctrl + Alt + D）

    def toggle_quick_bar(self):
        """Ctrl + Alt + D：在任何程式中叫出快速列；已開著就關閉。"""
        bar = getattr(self, "quick_bar", None)
        if bar and bar.is_open():
            bar.close()
        else:
            self.quick_bar = QuickBar(self)

    def current_list(self):
        """沒有指定 #清單 時使用的清單：上次新增用的（新增頁選過的會記住）。"""
        if 0 <= self.list_index < len(self.lists):
            return self.lists[self.list_index]
        return self.lists[0] if self.lists else None

    def today_agenda(self):
        """快速列空白時列出的待辦：逾期＋今天的最上層任務，排序與主畫面相同（有時間的在前）。"""
        today = dt.date.today()
        top = self._top_level()
        overdue = sorted((t for t in top if t["due"] and t["due"] < today),
                         key=lambda t: (t["due"], self._order_key(t)))
        todays = sorted((t for t in top if t["due"] == today),
                        key=lambda t: (t.get("time") is None, t.get("time") or dt.time(), self._order_key(t)))
        return overdue + todays

    def show_main(self, task=None):
        """從快速列開啟主視窗；有指定任務就直接開它的詳細頁。"""
        if self.state() == "withdrawn":
            self.toggle_visible()
        else:
            self.lift()
            self.focus_force()
        if task is not None and task in self.tasks:
            if task["due"] and task["due"] != self.selected:
                self.select(task["due"])
            self.open_detail(task)

    # ---- 執行緒

    def run_write(self, work, on_done):
        """寫入 Google 的背景工作（新增、完成、改日期、刪除…）：同 run_bg，並記錄還有幾筆沒送完。

        同步期間有寫入時，同步讀到的可能是寫入前的舊資料；_after_load 會丟掉這次結果，等寫入都完成再同步一次，
        才不會把剛完成的待辦叫回來、或把剛改的日期改回去。
        """
        self.pending_writes += 1
        self.write_seq += 1

        def done(result, err):
            self.pending_writes -= 1
            try:
                on_done(result, err)
            finally:
                if not self.pending_writes and self.resync_needed:
                    self.resync_needed = False
                    self.refresh()
        self.run_bg(work, done)

    def run_bg(self, work, on_done):
        """在背景執行緒執行 work()，結果以 on_done(result, error) 交回主執行緒。"""
        def runner():
            try:
                self.q.put((on_done, work(), None))
            except Exception as e:  # 任何錯誤都必須回報到畫面，不能讓背景執行緒無聲結束
                if not isinstance(e, (ApiError, NeedLogin)):  # 預期內的錯誤畫面上已有說明，未預期的才留 traceback
                    log.exception("Background task failed: %s", type(e).__name__)
                self.q.put((on_done, None, e))
        threading.Thread(target=runner, daemon=True).start()

    def report_callback_exception(self, exc, val, tb):
        """Tk 回呼（按鈕、after 計時器）沒接住的例外：寫進 daynote.log。

        pythonw 沒有 stderr，Tk 預設的輸出會直接消失，出事後無從追查。
        """
        log.error("Unhandled error in UI callback", exc_info=(exc, val, tb))

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
    apply_fonts(root)
    root.title("DayNote 首次設定")
    root.configure(bg=BG, padx=px(24), pady=px(20))
    root.resizable(False, False)
    result = {"cfg": None}

    tk.Label(root, text="歡迎使用 DayNote", bg=BG, fg=TEXT, font=TITLE_FONT,
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
    pick = tk.Label(buttons, text="選擇金鑰檔…", bg=ACCENT, fg=ON_ACCENT, font=FONT, cursor="hand2",
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


def setup_logging():
    """提醒紀錄寫進 data 資料夾的 daynote.log（滿 256 KB 換檔，保留 1 份舊檔）；寫不了就不記，不影響程式。"""
    try:
        handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=256 * 1024, backupCount=1,
                                                       encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def main():
    enable_dpi_awareness()
    migrate_legacy_files()
    setup_logging()
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
        native_messagebox.showerror(APP, f"設定檔有誤：{e}\n\n請修正 data\\config.json，或刪除它後重新啟動以進行首次設定。")
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
