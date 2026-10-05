# DayNote

Windows 桌面便利貼：月曆＋待辦，與 Google Tasks／Google 日曆同步。只用 Python 標準函式庫。

<p align="center"><img src="docs/images/00-app.png" width="320" alt="DayNote"></p>

## 功能

- 月曆圓點：🔵 待辦　🔴 逾期　⚪ 只有行程
- 週末與國定假日紅字；右上角三角：🔺紅＝放假節日、🔸橘＝一般節日（點日期看名稱）
- 點任務開啟詳細頁：改標題、詳細資訊、日期，新增子工作，刪除
- 點圓圈完成（含子工作），5 秒內可「復原」
- 提醒時間：新增時按「⏰」或在詳細頁設定，時間到在右下角跳出提醒（可「10 分鐘後」再提醒）
- 新增時用「📅 今天 ▾」選日期，或「無日期」；清單選單最後一項「＋ 新增清單…」可建立新清單
- 背景執行：不佔工作列，`—` 隱藏、**Ctrl + Alt + D** 叫回；Win + D 後仍留在桌面；可置頂、收合月曆

## 資料夾結構

```
DayNote\
├─ StartDayNote.bat      點兩下啟動 DayNote
├─ SetupAutostart.bat    點兩下設定開機自動啟動
├─ README.md
├─ app\                  程式（daynote.pyw）
├─ data\                 個人資料：config.json、token.bin、state.json（不上傳、不打包）
├─ tools\                開發用：build_portable.py（打包可攜版）
├─ docs\                 新手設定教學（SETUP_GUIDE.md）與截圖
├─ runtime\              可攜版才有：內附的 Python
└─ dist\                 打包輸出（不上傳）
```

## 安裝設定

需要 Windows 10/11 與 Google 帳號。API 個人使用免費，不需綁卡。

| 版本 | 適合 | 需要安裝 |
|---|---|---|
| **可攜版**（`DayNote-portable.zip`） | 一般使用、換電腦 | 不需要，內附 Python，解壓縮即可 |
| 原始碼版（本 repo） | 開發 | [Python 3.10+](https://www.python.org/downloads/)（保留預設勾選的 tcl/tk） |

可攜版由原始碼版產生：`python tools\build_portable.py` → `dist/DayNote-portable.zip`（約 16 MB，不含任何個人檔案）。

換電腦時：解壓縮可攜版（或複製整個資料夾，含 `data\config.json`），重新登入一次即可；`data\token.bin` 綁定原本的 Windows 帳號，無法沿用。

### 第一次設定

1. 點兩下 `StartDayNote.bat`
2. 跳出「首次設定」視窗後，照 **[新手設定教學（含圖）](docs/SETUP_GUIDE.md)** 做，約 10 分鐘

<img src="docs/images/15-setup-wizard.png" alt="首次設定視窗">

## 使用

點兩下 `StartDayNote.bat` 啟動，背景執行、無命令列視窗。重複執行時舊的會自動關閉，只留一個。

DayNote 不會出現在工作列與 Alt + Tab。按 `—` 隱藏到背景（提醒照常跳出），按 **Ctrl + Alt + D** 叫回；按 Win + D 時 DayNote 仍留在桌面。想要工作列圖示，在 `data\config.json` 設 `"show_in_taskbar": true`。

| 快捷鍵 | 功能 |
|---|---|
| `Ctrl+Alt+D` | 顯示／隱藏 DayNote（任何時候都能用） |
| `Ctrl+N` | 新增工作 |
| `←` `→` / `↑` `↓` | 前後一天 / 一週 |
| `Home` | 今天 |
| `F5` | 同步 |
| `Esc` | 離開輸入框／詳細頁 |

**開機自動啟動**：點兩下 `SetupAutostart.bat`，選「1 開啟」或「2 關閉」（也可在命令列執行 `SetupAutostart.bat on`／`off`／`status`）。開機後在背景執行，不會出現命令列視窗。

**避免每 7 天重新登入**：「目標對象」→「發布應用程式」。個人使用免審核。

## 設定

| 欄位 | 預設 | 說明 |
|---|---|---|
| `client_id` / `client_secret` | 必填 | OAuth 用戶端 |
| `ca_file` | `""` | 公司網路出現「TLS 憑證驗證失敗」時，填根憑證（PEM）路徑 |
| `borderless` | `true` | `false` 改回一般視窗 |
| `pin_to_desktop` | `true` | Win + D 後仍顯示 |
| `show_in_taskbar` | `false` | `false`＝背景執行，不顯示工作列圖示；`true`＝顯示工作列圖示，點圖示可縮小 |
| `holiday_calendar` | 台灣假日 | Google 公開假日日曆 ID；`""` 關閉 |
| `desktop_reminder` | `true` | 電腦端右下角提醒；`false` 關閉 |

## 常見問題

<img src="docs/images/14-access-denied.png" alt="access_denied">

| 狀況 | 處理 |
|---|---|
| 403 `access_denied` | 帳號沒加進測試使用者，或剛加入需等幾分鐘 |
| 「設定檔有誤」 | `data\config.json` 格式錯誤（例如少逗號）；刪除後重新執行會跳出首次設定 |
| `invalid_client` | 用戶端剛建立未生效，或 ID／密鑰有誤 |
| DayNote 顯示 HTTP 403 | 登入時權限沒全勾：刪除 `data\token.bin` 重新登入 |
| 「登入已過期」 | 測試中的專案 7 天到期，重新登入 |
| 視窗異常 | `data\config.json` 設 `"borderless": false` |

## 限制

- Google Tasks API 不支援：星號、時間、粗體等格式、圖片
- 提醒時間存在詳細資訊第一行（`⏰ 15:00`），手機 App 看得到這行字但不會推播；只有 DayNote 開著時才會提醒
- Windows 系統通知出現時，可能暫時蓋住 DayNote 的提醒視窗
- 日曆只讀主日曆，不能編輯
- 節日資料來自 Google 台灣假日日曆，不含補班日
- 子工作一律顯示在父工作下方
- 跨清單搬移工作請用 Google Tasks App
- 刪除無法在 DayNote 復原
- 釘在桌面時，檔案總管重新啟動會連帶關閉 DayNote
