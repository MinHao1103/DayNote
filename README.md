# DayNote

Windows 桌面月曆＋待辦小工具（精簡版）。Google Tasks 存待辦，Google 日曆唯讀顯示行程。只用 Python 標準函式庫。

## 檔案

| 檔案 | 用途 |
|---|---|
| `daynote.pyw` | 主程式（單一檔案） |
| `config.example.json` | 設定範本，複製成 `config.json` 後填值 |
| `config.json` | 你的設定（自己建立，勿外流） |
| `token.bin` | 登入後自動產生，DPAPI 加密的 refresh token（勿外流；刪掉即登出） |
| `daynote_state.json` | 程式自動產生，記住視窗位置、大小、置頂狀態（刪掉即回到預設） |
| `docs/DayNote-setup-guide.html` | 圖文設定教學（含截圖，勿公開分享） |

## 功能

- 月曆：切換月份、回到今天；有任務或行程的日期顯示圓點，有逾期未完成任務為紅點
- 點日期 → 下方列出當天的日曆事件（唯讀）與待辦
- 勾選待辦 → 標記完成並從畫面消失
- 底部輸入列：Enter 新增到「目前選中的日期」，下拉選清單
- 「未排日期」區：沒有到期日的待辦，預設收起
- 同步：右上角 ⟳ 手動同步；視窗取得焦點時自動同步（30 秒內不重複）
- 便利貼外觀：淡黃底色、無系統標題列（Win11 嘗試圓角）
  - 拖曳：按住頂部列（日期文字或空白處）移動
  - 縮放：拖拉右下角 `◢`
  - 右上角按鈕：`⟳` 同步、`📌` 置頂開關（藍色＝置頂中）、`—` 縮小到工作列、`✕` 關閉
  - 位置、大小、置頂狀態會記住，下次開啟還原；位置跑到螢幕外時自動回到預設位置
  - 釘在桌面：按 Win + D 顯示桌面時，DayNote 仍留在桌面上
  - 任務卡片為圓角，左側圓圈點擊即完成（滑鼠移上去顯示 ✓）

## 首次設定

> 選單名稱依 Google 官方文件（英文介面）。中文介面的翻譯可能不同，以實際畫面為準。

1. **建立專案**：到 <https://console.cloud.google.com/>，左上角專案選單 → **New Project**，名稱填 `DayNote` → **Create**，建立後切換到這個專案。
2. **啟用 API**：Menu → **APIs & Services** → **Library**，搜尋並點 **Enable**：
   - `Google Tasks API`
   - `Google Calendar API`
3. **設定同意畫面**：Menu → **Google Auth platform** → **Branding** → **Get Started**
   - **App name** 填 `DayNote`，**User support email** 選自己的 Gmail → **Next**
   - **Audience** 選 **External** → **Next**
   - 聯絡 **Email address** 填自己的 Gmail → **Next**
   - 勾選同意 *Google API Services: User Data Policy* → **Continue** → **Create**
4. **加入測試使用者**：**Audience** → **Test users** → **Add users** → 輸入自己的 Gmail → **Save**
5. **（選做）宣告權限範圍**：**Data Access** → **Add or Remove Scopes**，勾選 `.../auth/tasks` 與 `.../auth/calendar.events.readonly` → **Save**
6. **建立 OAuth 用戶端**：**Clients** → **Create Client** → **Application type** 選 **Desktop app**，**Name** 填 `DayNote` → **Create**
   - ⚠️ **client secret 只在建立當下顯示一次**，請立刻按下載 JSON 或複製下來
7. **填設定**：複製 `config.example.json` 為 `config.json`，填入 `client_id`、`client_secret`。

> 桌面應用程式的 client secret **不是真正的機密**（任何拿到程式的人都看得到），Google 官方文件也這樣說明；真正要保護的是 `token.bin`。即便如此也不要公開分享 `config.json`。

### 公司網路 TLS 檢查

若出現「TLS 憑證驗證失敗」，請向 IT 取得公司根憑證（PEM 格式），在 `config.json` 填入路徑：

```json
"ca_file": "D:\\DayNote\\corp-root-ca.pem"
```

### 無邊框模式（borderless）

`config.json` 的 `"borderless"`：`true`（預設，未填也視為 true）＝便利貼樣式；`false`＝一般 Windows 視窗（有系統標題列）。

無邊框的取捨：Windows 預設不讓無邊框視窗出現在工作列，程式用 Windows API 把工作列圖示與 Alt+Tab 加回來。若這步失敗，視窗仍可使用，但會隱藏 `—` 縮小按鈕（避免縮小後找不回視窗）；遇到任何視窗異常可改成 `false`。

### 釘在桌面（pin_to_desktop）

`"pin_to_desktop"`：`true`（預設）＝按 Win + D 後 DayNote 仍顯示在桌面；`false`＝和一般視窗一樣被 Win + D 隱藏。只在無邊框模式生效（實測：有邊框視窗無法同時保有工作列圖示與撐過 Win + D）。

做法是把視窗的擁有者設為 Windows 桌面（Progman），已在 Win11 實測。取捨：若 Explorer（檔案總管／工作列）當掉或重新啟動，DayNote 會跟著關閉，重新開啟即可。

程式不會、也不提供關閉憑證驗證的選項。代理伺服器沿用 Windows 系統設定。

## 執行

```bat
:: 無命令列視窗（日常使用）
pythonw D:\DayNote\daynote.pyw

:: 有命令列視窗（除錯用）
python D:\DayNote\daynote.pyw
```

### 開機自動啟動

1. `Win + R` 輸入 `shell:startup` 按 Enter，開啟「啟動」資料夾。
2. 在資料夾內按右鍵 →「新增 → 捷徑」，位置填：
   ```
   "C:\完整路徑\pythonw.exe" "D:\DayNote\daynote.pyw"
   ```
   `pythonw.exe` 的路徑可在命令列執行 `where pythonw` 查詢。
3. 捷徑名稱填 `DayNote`。

## 第一次登入

1. 啟動後按右上角「登入」，瀏覽器會開啟 Google 登入頁。
2. 出現「**Google 尚未驗證這個應用程式**」警告：因為是你自己建的、未送審的專案，屬正常現象。點「繼續」（有時要先點「進階」）。
3. 權限頁面請**兩個權限都勾選**（Tasks、查看日曆活動）。只勾一個的話，另一邊會顯示 HTTP 403 錯誤。
4. 看到「DayNote 已收到登入結果」即可關閉分頁，回到 DayNote 會自動同步。

### 約 7 天要重新登入？

專案停在「測試中（Testing）」狀態時，refresh token 約 7 天失效。失效時 DayNote 狀態列會顯示「登入已過期」，按「登入」重來一次即可。

要避免每週重登，可在 OAuth 同意畫面把發布狀態改為「**正式版（In production）**」。依官方說明，個人使用（少於 100 位使用者）的應用程式**不必送審**也能繼續使用，只是登入時仍會出現「未經驗證的應用程式」警告，點過去即可。

Cloud Console 裡切換發布狀態的按鈕位置，我沒有查證，以實際畫面為準。

## 官方文件查證結果（2026-10-04）

| 項目 | 結論 | 官方文件 |
|---|---|---|
| Device Code Flow | 只支援 `email`、`openid`、`profile`、`drive.appdata`、`drive.file`、`youtube`、`youtube.readonly`，**不支援** Tasks / Calendar，所以採用 loopback + PKCE | [Limited-input device: Allowed scopes](https://developers.google.com/identity/protocols/oauth2/limited-input-device#allowedscopes) |
| Loopback + PKCE | 支援 `http://127.0.0.1:隨機埠`；PKCE 建議用 S256 | [OAuth for Desktop apps](https://developers.google.com/identity/protocols/oauth2/native-app) |
| 測試中 7 天失效 | 「外部」使用者類型 + 發布狀態為「測試中」+ 權限範圍超出基本個人資料時，refresh token **7 天到期**。另外，6 個月沒用、撤銷授權等也會失效 | [OAuth 2.0: Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration) |
| 個人使用免審核 | 少於 100 位使用者可不送審，需點過「未經驗證」警告 | [Verification exceptions](https://support.google.com/cloud/answer/13464323?hl=en) |
| Tasks `due` | 「只記錄日期資訊，設定時時間部分會被丟棄」，程式直接取前 10 碼，不做時區轉換 | [Tasks resource](https://developers.google.com/tasks/reference/rest/v1/tasks) |
| 星號／重要 | Task 資源**沒有**星號／重要欄位 | 同上 |
| 重複性任務 | Task 資源**沒有**重複規則欄位，只能看到目前這一筆 | 同上 |
| `showCompleted` | 預設 `true`，程式設為 `false`。`showHidden` 預設 `false`；在官方 App 完成的任務要 `showHidden=true` 才會出現，但我們本來就不要已完成的，不受影響 | [tasks.list](https://developers.google.com/tasks/reference/rest/v1/tasks/list) |
| `maxResults` | Tasks 預設 20、最大 100；Calendar 預設 250、最大 2500。程式都有處理分頁 | 同上／[events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list) |
| 重複事件 | `singleEvents=true` 會把重複事件展開成單筆；`orderBy=startTime` 必須搭配它 | [events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list) |
| 日曆權限 | events.list 接受 `calendar.events.readonly`，比 `calendar.readonly` 更小，程式已改用 | 同上 |

## 已知限制

- 日曆事件只讀主日曆（primary），不讀其他訂閱的日曆；只能看，不能新增或修改。
- 有時間的事件只顯示在**開始那天**；跨日的全天事件會顯示在每一天。
- 待辦沒有「幾點」：Tasks API 只存日期。需要定時推播請另行規劃（之前討論的方案 2）。
- 不支援編輯、刪除任務；重複性任務只會看到目前這一筆。
- 子任務以一般任務顯示，不呈現階層。
- 沒有定時輪詢（只有手動同步與焦點同步）；沒有電腦端提醒、背景圖。
- 釘在桌面模式下，Explorer 重新啟動會連帶關閉 DayNote。
- 無邊框模式下視窗邊緣不能拉動，只能用右下角 `◢` 調整大小；也不支援 Windows 貼齊（Win + 方向鍵）。
- 429 / 5xx 只依 Retry-After 重試一次（最多等 10 秒），沒有完整的指數退避。
- 勾選完成時若同步剛好進行中，已完成的任務可能短暫再次出現，下次同步即修正。
- 沒有系統匣圖示（標準函式庫做不到）。
- `token.bin` 以 DPAPI 綁定目前 Windows 帳號，換帳號或換電腦需重新登入。
