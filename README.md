# DayNote

放在 Windows 桌面上的月曆＋待辦清單，會跟手機的 Google Tasks、Google 日曆同步。

<p align="center"><img src="docs/images/00-app.png" width="320" alt="DayNote"></p>

## 能做什麼

- **待辦清單**：新增、完成、修改、刪除，手機 Google Tasks 會同步看到
- **月曆**：一眼看出哪天有待辦、哪天過期沒做，假日和節日會標紅
- **日曆行程**：顯示 Google 日曆的行程（只能看，不能改）
- **提醒**：幫待辦設時間，時間到電腦右下角會跳出提醒
- **子工作**：待辦底下可以再分小項目
- **重複待辦**：每天、平日、每週、每月、每年，做完自動產生下一期
- **不佔位置**：不會出現在工作列，按 **F8** 隨時叫出來或藏起來
- **快速列**：在任何程式裡按 **Ctrl + Alt + D**，打一行「明天下午3點 開會」就新增好，不用切換視窗

**你需要：** Windows 10 或 11 電腦、一個 Gmail 帳號、大約 15 分鐘。全部免費，不用綁信用卡。

設定只要做一次，分三步：

1. [下載 DayNote](#第一步下載-daynote)
2. [跟 Google 申請金鑰](#第二步跟-google-申請金鑰)（最久，約 10 分鐘）
3. [選金鑰並登入](#第三步選金鑰並登入)

圖片裡的紅框 **①②③** 就是要點的地方，照順序點就對了。

---

## 第一步：下載 DayNote

1. 點這裡下載：**[DayNote.zip](https://github.com/MinHao1103/DayNote/releases/latest/download/DayNote.zip)**（永遠是最新版），檔案會存到「下載」資料夾

2. 打開「下載」資料夾，對 `DayNote` 按**滑鼠右鍵** → ①「解壓縮全部…」

<img src="docs/images/guide/d02-extract-menu.png" width="480" alt="解壓縮全部">

3. 按 ①「解壓縮」

<img src="docs/images/guide/d03-extract-dialog.png" width="560" alt="解壓縮">

4. 會自動打開 `DayNote` 資料夾 → 對 ①「StartDayNote」**點兩下**

<img src="docs/images/guide/d04-start-bat.png" width="480" alt="點兩下 StartDayNote">

> 跳出藍色的「Windows 已保護您的電腦」：按「其他資訊」→「仍要執行」。點兩下沒反應：改用[指令啟動](#打不開-daynote)。

5. 會跳出「首次設定」視窗。**先不要關**，接著做第二步。

<img src="docs/images/15-setup-wizard.png" alt="首次設定視窗">

---

## 第二步：跟 Google 申請金鑰

DayNote 要讀你的待辦清單，需要 Google 給一個「金鑰檔」。照下面做，最後會下載到一個檔案。

### 2-1 建立專案

1. 打開 <https://console.cloud.google.com/>，用你的 Gmail 登入（第一次會要你同意條款：打勾 → 同意並繼續）
2. 打開 <https://console.cloud.google.com/projectcreate>
3. ① 專案名稱輸入 `DayNote` → ② 按「建立」

<img src="docs/images/guide/g02-new-project.png" alt="新增專案">

4. 右上角跳出通知，按 ①「選取專案」

<img src="docs/images/guide/g03-project-created.png" alt="選取專案">

5. 確認左上角 ① 顯示 `DayNote`

<img src="docs/images/guide/g04-project-selected.png" alt="確認專案">

### 2-2 開啟「待辦清單」功能

1. ① 在最上面的搜尋框輸入 `Google Tasks API` → 點 ② 第一個結果（注意**不是** Cloud Tasks）

<img src="docs/images/guide/g05-search-tasks.png" alt="搜尋 Google Tasks API">

2. 按 ①「啟用」

<img src="docs/images/guide/g06-tasks-enable.png" alt="啟用">

3. 看到 ①「已啟用」就好了

<img src="docs/images/guide/g07-tasks-enabled.png" alt="已啟用">

### 2-3 開啟「日曆」功能

在最上面的搜尋框輸入 `Google Calendar API` → 點第一個結果 → 按 ①「啟用」

<img src="docs/images/guide/g08-calendar-enable.png" alt="啟用 Calendar API">

### 2-4 填寫基本資料

1. 打開 <https://console.cloud.google.com/auth/overview> → 按 ①「開始」

<img src="docs/images/guide/g09-auth-start.png" alt="開始">

2. ① 名稱輸入 `DayNote` → ② 電子郵件選你的 Gmail → ③「下一步」

<img src="docs/images/guide/g10-consent-1-app.png" alt="應用程式資訊">

3. ① 選「外部」→ ②「下一步」

<img src="docs/images/guide/g11-consent-2-audience.png" alt="目標對象">

4. ① 輸入你的 Gmail → ②「下一步」

<img src="docs/images/guide/g12-consent-3-contact.png" alt="聯絡資訊">

5. ① 勾選「我同意」→ ②「繼續」

<img src="docs/images/guide/g13-consent-4-agree.png" alt="同意">

6. 按 ①「建立」

<img src="docs/images/guide/g14-consent-create.png" alt="建立">

### 2-5 把自己加進使用名單

1. 點左邊的 ①「目標對象」

<img src="docs/images/guide/g15-oauth-overview.png" alt="目標對象">

2. 往下捲，按 ①「Add users」→ ② 輸入你的 Gmail，按 Enter → ③「儲存」

<img src="docs/images/guide/g16-add-test-user.png" alt="新增使用者">

3. 確認 ① 名單裡有你的 Gmail

<img src="docs/images/guide/g17-test-user-list.png" alt="使用者名單">

### 2-6 下載金鑰檔

1. 點左邊的 ①「用戶端」→ ②「建立用戶端」

<img src="docs/images/guide/g18-clients-page.png" alt="用戶端">

2. 應用程式類型選 ①「**電腦版應用程式**」（最後一個）

<img src="docs/images/guide/g19-client-type.png" alt="電腦版應用程式">

3. ② 名稱輸入 `DayNote` → ③ **不要勾** → ④「建立」

<img src="docs/images/guide/g20-client-form.png" alt="建立用戶端">

4. 按 ①「**下載 JSON**」→ 再按「確定」。檔案會存到「下載」資料夾，檔名是 `client_secret_` 開頭

<img src="docs/images/guide/g21-client-created.png" alt="下載 JSON">

> ⚠️ 一定要先按「下載 JSON」再關視窗，關掉就不能再下載了。不小心關掉：在「用戶端」頁面把 DayNote 刪掉，重做 2-6。

---

## 第三步：選金鑰並登入

1. 回到 DayNote 的「首次設定」視窗 → 按 ②「選擇金鑰檔…」→ 到「下載」資料夾選 `client_secret_` 開頭的檔案 → 按「開啟」

<img src="docs/images/15-setup-wizard.png" alt="選擇金鑰檔">

2. 按 DayNote 右上角的 ①「登入」，瀏覽器會打開 → 選你的 Gmail

<p><img src="docs/images/10-login-button.png" width="320" alt="登入"></p>

3. 出現「Google 尚未驗證這個應用程式」→ 按 ①「繼續」（這是你自己建立的，可以放心）

<img src="docs/images/11-unverified.png" alt="未經驗證">

4. ① 勾選「全選」→ ③「繼續」

<img src="docs/images/12-consent.png" alt="授權">

5. 看到 ① 這行字就可以關掉瀏覽器分頁。**設定完成！** DayNote 會顯示你的待辦清單。

<img src="docs/images/13-login-done.png" alt="完成">

設定完成後，「下載」資料夾裡的 `client_secret_` 檔案可以刪掉。

### 建議再做一件事：避免每 7 天要重新登入

Google 預設 7 天後會要你重新登入。想要一直保持登入：

打開 <https://console.cloud.google.com/auth/audience> → 按「發布應用程式」→「確認」。只有你自己用，不需要 Google 審核。

---

## 怎麼用

**打開 DayNote**：到 `DayNote` 資料夾，點兩下 `StartDayNote`。

> 點 `StartDayNote` 時會先閃一下黑色視窗，這是正常的。不想看到它，可以改點兩下 `StartDayNoteSilent`，效果一樣。

| 想做什麼 | 怎麼做 |
|---|---|
| 快速新增 | 在最下面的輸入框打字，按 Enter 或最右邊的「新增」。可以直接寫日期時間，例如 `明天 3點 開會`，輸入框上方會即時顯示辨識結果（寫法和[快速列](#快速列ctrl--alt--d)一樣）；沒寫日期就放在月曆選中的那天 |
| 完整新增 | 按輸入框旁的「詳細」：可以填詳細資訊，選日期、提醒時間、重複和清單，再按「＋ 建立」。輸入框已經打的字會自動帶進來 |
| 換清單、新增清單 | 在「詳細」頁按「📁 清單」選擇；最後一項可以新增清單。之後新增會沿用上次選的清單 |
| 設定提醒 | 打 `下午3點` 這類時間，或在「詳細」頁按「⏰」輸入時間（例如 `15:30` 或 `1530`）按 Enter，時間到右下角會跳出提醒 |
| 完成待辦 | 點待辦前面的圓圈。點錯了，5 秒內按「復原」 |
| 修改／刪除 | 點待辦的文字進去修改，改完按「← 儲存並返回」或下方「儲存」；直接關掉 DayNote 時會先問要不要儲存；上方有「刪除」 |
| 設定重複 | 點待辦進去，按「🔁 不重複 ▾」選規則，也可以自訂，例如「每週一三五」 |
| 這次不做 | 重複待辦點進去按「略過這一期」：直接跳到下一期，不會留下完成紀錄 |
| 不再重複 | 想結束整個重複：直接刪除這一期。這次還要做、做完就停：把「🔁」改成「不重複」 |
| 暫時藏起來 | 按右上角「—」，按 **F8** 叫回來 |
| 永遠在最上層 | 按右上角 📌 |
| 立即同步 | 按右上角 ⟳（平常會自動同步） |
| 登出、換帳號 | 按右上角「登出」→「是」，再按「登入」 |

**月曆上的記號**：🟤 有待辦　🔴 有過期沒做完的　淡陶土色點＝重複待辦預定的日子　🟢 只有日曆行程（淡綠）　紅色日期＝假日（數字右上小圓點＝節日）

### 快速列（Ctrl + Alt + D）

在任何程式裡（瀏覽器、Word、Excel 都可以）按 **Ctrl + Alt + D**，螢幕中間會出現一條輸入列，不用切換到 DayNote：

- **新增**：直接打一行，例如 `明天下午3點 跟客戶開會 每週二 #工作`，下方會即時顯示辨識結果（📅 明天　⏰ 15:00　🔁 每週二　📁 工作），確認沒錯按 **Enter**
- **看今天**：還沒打字時，會列出逾期和今天的待辦。用 **↑ ↓** 選，**Space** 完成，**Enter** 開啟詳細頁
- **搜尋**：打 `/` 開頭，例如 `/週報`，找出所有含「週報」的待辦
- **Tab** 開啟主視窗，**Esc** 或點到其他地方就關閉

一行裡可以寫的東西（用空白隔開，順序不拘，其餘文字就是標題）：

| 類型 | 寫法 |
|---|---|
| 日期 | `今天`、`明天`、`後天`、`3天後`、`週五`、`下週一`、`10/15`、`10月15日`、`無日期` |
| 時間 | `15:30`、`3點`、`3點半`、`下午3點15分`、`晚上8點`（日期和時間可以連在一起，例如 `明天下午3點`） |
| 重複 | `每天`、`平日`、`每週一三五`、`每月15日`、`每月最後一天`、`每年3/15` |
| 清單 | `#工作`、`#個人`（打開頭幾個字就好；沒寫就用上次的清單） |

- 沒寫上午、下午時，**1～6 點當成下午**（`3點` = 15:00），7～12 點照寫；要早上 6 點請寫 `早上6點`
- 快速列和輸入框裡的時間請寫 `15:30` 或 `3點`，不接受 `1530` 這種純數字（會跟金額、數量搞混；「詳細」頁的 ⏰ 則可以）
- 只寫時間、沒寫日期：時間還沒過就是今天，已經過了就是明天
- 什麼都沒寫：放在今天
- 日期和規則要跟標題用空白隔開：`每天 量血壓` 會設定每天重複，`每天量血壓` 則整段當成標題

### 開機自動打開

到 `DayNote` 資料夾，點兩下 `SetupAutostart` → 在跳出的視窗按鍵盤的 `1`，看到「已開啟開機自動啟動」後按任意鍵關閉。之後每次開機 DayNote 會自己打開。

不想要了：同樣點兩下 `SetupAutostart` → 按鍵盤的 `2`。

### 換電腦

在新電腦重做「第一步」，然後把舊電腦 `DayNote\data` 資料夾裡的 `config.json` 複製到新電腦的 `DayNote\data` 裡，打開 DayNote 按「登入」就好，不用重新申請金鑰。

---

## 遇到問題怎麼辦

| 狀況 | 怎麼辦 |
|---|---|
| 點兩下 `StartDayNote` 沒反應 | 看[下面的「打不開 DayNote」](#打不開-daynote) |
| DayNote 不見了 | 按 **F8**（筆電可能要按 **Fn + F8**），或按 **Ctrl + Alt + D** 再按 **Tab**；還是沒有，就重新點兩下 `StartDayNote` |
| 更新後新功能沒出現 | DayNote 還開著舊版。重新點兩下 `StartDayNote`，舊的會自動關閉換成新版 |
| 其他程式按 F8 沒反應 | DayNote 開著時會用掉 F8。那個程式需要 F8 時，先關掉 DayNote |
| 登入時出現「403 access_denied」 | 2-5 沒把自己加進名單，回去加；剛加完要等幾分鐘 |
| 登入時出現「invalid_client」 | 金鑰剛建立還沒生效，等 5 分鐘再試 |
| 顯示「登入已過期或權限已撤銷」 | 按右上角「登入」重新登入；不想每 7 天登一次，做[這一步](#建議再做一件事避免每-7-天要重新登入) |
| 顯示「HTTP 403」 | 登入時沒有「全選」。按「登出」，再「登入」一次，記得勾「全選」 |
| 顯示「設定檔有誤」，或想換一組金鑰 | 刪掉 `DayNote\data\config.json`，重新打開 DayNote，從第三步重選金鑰 |
| 顯示「網路連線失敗」 | 檢查網路，按右上角 ⟳ 重試 |
| 顯示「TLS 憑證驗證失敗」 | 公司網路擋住了，請公司 IT 協助 |
| 提醒沒有跳出來 | 提醒只在 DayNote 開著時才會跳。建議設定[開機自動打開](#開機自動打開) |
| 手機上看到待辦多了一行 `⏰ 15:00` | 正常，這是 DayNote 記錄提醒時間的方式，不要刪掉 |

### 打不開 DayNote

DayNote 會**優先用電腦上已經安裝的 Python**（3.10 以上、有 tcl/tk），找不到才用資料夾裡內附的 `runtime`。公司電腦常會擋「從下載的 zip 解壓縮出來的程式」，所以電腦上有裝 Python 的話，比較不會被擋。

公司電腦還是擋住 `StartDayNote` 或 `StartDayNoteSilent` 時，依序試試：

1. **解壓縮前先解除封鎖**：在下載的 `DayNote.zip` 上按右鍵 →「內容」→ 勾選「解除封鎖」→「確定」，再「解壓縮全部」
2. **手動啟動**：打開 `DayNote` 資料夾，點檔案總管最上面的**網址列**（顯示資料夾路徑的地方），輸入 `cmd` 按 Enter，在跳出的黑色視窗貼上下面這行按 Enter（電腦有裝 Python 時）：

```bat
start "" pythonw app\daynote.pyw
```

電腦沒有裝 Python，改貼這行（用內附的）：

```bat
start "" runtime\pythonw.exe app\daynote.pyw
```

DayNote 打開後，黑色視窗可以關掉。

還是打不開，在同一個黑色視窗貼上下面這行（沒裝 Python 就把 `python` 換成 `runtime\python.exe`），把視窗裡出現的文字截圖給維護的人：

```bat
python app\daynote.pyw
```

---

## 要知道的事

- 手機 Google Tasks 不會跳 DayNote 設的提醒，只有電腦上的 DayNote 會
- 刪除的待辦沒辦法復原
- 星號、粗體、圖片這些格式，DayNote 不支援

### 重複待辦

- DayNote 的重複規則寫在待辦「詳細資訊」的開頭，例如 `🔁 每週一`。手機上看得到，也可以直接在手機打這一行，寫法有：`每天`、`平日`、`每週一三五`、`每月15日`、`每月最後一天`、`每年3/15`
- 手機 Google Tasks 內建的「重複」也可以用：在 DayNote 修改或勾選完成，內建重複都會照常運作。但**不要**同時設內建「重複」和 DayNote 的 `🔁`，否則可能一次產生兩筆
- 在手機上打勾完成的重複待辦，要等電腦上的 DayNote 下次同步時才會補上下一期
- 下一期的日期照原本的日期往後算（週一的待辦拖到週三才做，下一期還是下週一）；拖了好幾期才完成，只會產生今天以後的那一期
- 每月 31 日遇到小月會改成月底，下個月再回到 31 日；「平日」是週一到週五，不會跳過國定假日
- 下一期會複製標題、詳細資訊和提醒時間；子工作、星號，以及手機上設定的時間和截止時間不會複製
- 在手機上不小心刪掉 `🔁` 那一行，重複就停止了
- 重複待辦同一時間只有「這一期」是真的待辦，**完成這一期後才會建立下一期**（和 Google Tasks 一樣）。為了讓你看得到之後的安排，DayNote 會在月曆上用淡陶土色點標出預定的日子，點那天會看到灰色的「預定」列；預定列不能打勾，點它會開啟目前這一期。手機上只看得到目前這一期
