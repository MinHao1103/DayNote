# DayNote

Windows 桌面便利貼：月曆＋待辦，與 Google Tasks／Google 日曆同步。只用 Python 標準函式庫。

<p align="center"><img src="images/00-app.png" width="320" alt="DayNote"></p>

## 功能

- 月曆圓點：🔵 待辦　🔴 逾期　⚪ 只有行程
- 點任務開啟詳細頁：改標題、詳細資訊、日期，新增子工作，刪除
- 點圓圈完成（含子工作），5 秒內可「復原」
- 新增時用「📅 今天 ▾」選日期，或「無日期」
- Win + D 後仍留在桌面；可置頂、收合月曆

## 安裝設定

需要 Windows 10/11、Python 3.10+、Google 帳號。API 個人使用免費，不需綁卡。

### 1. 建立專案

[Google Cloud Console](https://console.cloud.google.com/) → ① 專案選擇器 →「新增專案」→ 名稱 `DayNote`。建立後切換過去，確認 ② 顯示 DayNote。

<img src="images/01-project.png" alt="建立專案">

### 2. 啟用 API

搜尋並 ①「啟用」以下兩個 API（② 確認服務名稱）：

- `Google Tasks API`（不是 Cloud Tasks）
- `Google Calendar API`

<img src="images/02-tasks-api.png" alt="啟用 Google Tasks API">

### 3. OAuth 同意畫面

☰ →「API 和服務」→「OAuth 同意畫面」→ ①「開始」：

<img src="images/05-auth-start.png" alt="開始設定">

| 步驟 | 填寫 |
|---|---|
| 應用程式資訊 | 名稱 `DayNote`、支援信箱選自己 |
| 目標對象 | **外部** |
| 聯絡資訊 | 自己的 Gmail |
| 完成 | 勾選同意 → 建立 |

### 4. 測試使用者

「目標對象」→ ①「Add users」→ 輸入自己的 Gmail → ③「儲存」，確認 ② 出現在清單。

<img src="images/07-test-users.png" alt="加入測試使用者">

### 5. 建立用戶端

「用戶端」→「建立用戶端」：① **電腦版應用程式**、② 名稱 `DayNote`、③ **不要勾**、④ 建立。

<img src="images/08-create-client.png" alt="建立 OAuth 用戶端">

> ⚠️ 用戶端密鑰只顯示一次，請立刻下載 JSON 或複製。

### 6. config.json

複製 `config.example.json` 為 `config.json`，填入 ID 與密鑰（此檔已被 `.gitignore` 排除，勿外流）：

```json
{
  "client_id": "<CLIENT_ID>.apps.googleusercontent.com",
  "client_secret": "<CLIENT_SECRET>"
}
```

### 7. 執行並登入

```bash
./start.sh
```

按 ①「登入」：

<p align="center"><img src="images/10-login-button.png" width="320" alt="登入按鈕"></p>

出現「未經驗證」→ ①「繼續」：

<img src="images/11-unverified.png" alt="未經驗證的應用程式">

① 全選權限 → ③「繼續」：

<img src="images/12-consent.png" alt="授權">

看到「已收到登入結果」即完成。

## 使用

`start.sh` 可重複執行：已開啟時會先關閉舊的再開新的（也可直接 `pythonw daynote.pyw`）。

| 快捷鍵 | 功能 |
|---|---|
| `Ctrl+N` | 新增工作 |
| `←` `→` / `↑` `↓` | 前後一天 / 一週 |
| `Home` | 今天 |
| `F5` | 同步 |
| `Esc` | 離開輸入框／詳細頁 |

**開機啟動**：`Win+R` → `shell:startup` → 新增捷徑，目標填 `"<pythonw.exe 路徑>" "D:\DayNote\daynote.pyw"`。

**避免每 7 天重新登入**：「目標對象」→「發布應用程式」。個人使用免審核。

## 設定

| 欄位 | 預設 | 說明 |
|---|---|---|
| `client_id` / `client_secret` | 必填 | OAuth 用戶端 |
| `ca_file` | `""` | 公司網路出現「TLS 憑證驗證失敗」時，填根憑證（PEM）路徑 |
| `borderless` | `true` | `false` 改回一般視窗 |
| `pin_to_desktop` | `true` | Win + D 後仍顯示 |

## 常見問題

<img src="images/14-access-denied.png" alt="access_denied">

| 狀況 | 處理 |
|---|---|
| 403 `access_denied` | 帳號沒加進測試使用者，或剛加入需等幾分鐘 |
| 「設定檔有誤」 | 沒有 `config.json`，或格式錯誤 |
| `invalid_client` | 用戶端剛建立未生效，或 ID／密鑰有誤 |
| DayNote 顯示 HTTP 403 | 登入時權限沒全勾：刪除 `token.bin` 重新登入 |
| 「登入已過期」 | 測試中的專案 7 天到期，重新登入 |
| 視窗異常 | `config.json` 設 `"borderless": false` |

## 限制

- Google Tasks API 不支援：星號、幾點提醒、粗體等格式、圖片
- 日曆只讀主日曆，不能編輯
- 子工作一律顯示在父工作下方
- 跨清單搬移工作請用 Google Tasks App
- 刪除無法在 DayNote 復原
- 釘在桌面時，檔案總管重新啟動會連帶關閉 DayNote
