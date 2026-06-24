# 10 — InfluxDB 時序資料庫詳解

這份文件詳細說明本專案如何使用 **InfluxDB 2**：資料模型、寫入、查詢、保留策略、免費版限制，  
以及如何在 InfluxDB Cloud 後台自己查資料。給接手的人完整理解時序資料這一層。

---

## 目錄

- [為什麼用 InfluxDB（而不是一般資料庫）](#為什麼用-influxdb而不是一般資料庫)
- [核心概念：時序資料模型](#核心概念時序資料模型)
- [本專案的資料結構](#本專案的資料結構)
- [資料怎麼寫進去（Write Path）](#資料怎麼寫進去write-path)
- [資料怎麼查出來（Flux 查詢）](#資料怎麼查出來flux-查詢)
- [降採樣：長時間範圍的關鍵](#降採樣長時間範圍的關鍵)
- [保留策略與免費版限制](#保留策略與免費版限制)
- [如何在 InfluxDB Cloud 後台查資料](#如何在-influxdb-cloud-後台查資料)
- [Cardinality（基數）陷阱](#cardinality基數陷阱)

---

## 為什麼用 InfluxDB（而不是一般資料庫）

IoT 感測器資料的特性：

- **寫多讀少**：每台設備每幾秒就寫一筆，量極大
- **時間是主軸**：幾乎所有查詢都是「某段時間內的某設備數值」
- **不太更新**：寫進去就不改，只追加（append-only）
- **需要降採樣**：查一年的資料不可能回傳幾百萬點，要自動聚合

一般關聯式資料庫（如 PostgreSQL）處理這種「高頻寫入 + 時間範圍查詢 + 聚合」會吃力。  
InfluxDB 是專為時序資料設計的，寫入吞吐高、時間範圍查詢快、內建聚合函數。

> 本專案用 **PostgreSQL 存「不太變的元資料」**（客戶、門市、設備清單），  
> 用 **InfluxDB 存「不斷產生的時序數據」**（每筆感測器讀值）。各司其職。

---

## 核心概念：時序資料模型

InfluxDB 的一筆資料（Point）由四個部分組成：

| 組成 | 說明 | 本專案範例 |
|---|---|---|
| **Measurement** | 類似「資料表名稱」 | `sensor_data` |
| **Tags** | 索引欄位（字串，用來篩選/分組）| `customer_id`, `site_id`, `device_type`, `device_uid` |
| **Fields** | 實際數值（不建索引）| `value`（浮點數）|
| **Timestamp** | 時間戳 | 設備量測當下的 UNIX 秒數 |

### 圖示

```
measurement   tags（索引，用來篩選）                              field      time
─────────── ──────────────────────────────────────────────── ───────── ────────────────
sensor_data,customer_id=C001,site_id=S001,device_type=temperature,device_uid=fridge01 value=-22.3 1750000000
            └──────────────── 可用這些 tag 快速篩選/分組 ────┘ └ 數值 ┘ └ 設備時間 ┘
```

**Tag vs Field 的關鍵差異：**
- **Tag** 有索引 → 適合放「會拿來篩選的維度」（哪個客戶、哪台設備）
- **Field** 沒索引 → 放「實際要計算的數值」（溫度、電力值）

---

## 本專案的資料結構

| 項目 | 值 |
|---|---|
| Bucket（資料桶）| `realtime` |
| Measurement | `sensor_data` |
| Tags | `customer_id`, `site_id`, `device_type`, `device_uid` |
| Field | `value`（float）|
| Timestamp | 設備量測時間（UNIX 秒，非伺服器收到時間）|

### 為什麼用設備時間而非伺服器時間？

4G 網路有延遲，設備 12:00:00 量到的值，伺服器可能 12:00:03 才收到。  
若用伺服器時間，資料點會「晚 3 秒」，失真。所以寫入時用 payload 帶的 `ts`（設備時間）。

詳見 [docs/05-技術探討.md](05-技術探討.md#感測器時間戳的重要性)

---

## 資料怎麼寫進去（Write Path）

後端訂閱到 MQTT 訊息後，用 InfluxDB Python SDK 的 `Point` 物件寫入  
（程式碼在 [`backend/mqtt_subscriber.py`](../backend/mqtt_subscriber.py)）：

```python
from influxdb_client import Point, WritePrecision

point = (
    Point("sensor_data")                      # measurement
    .tag("customer_id", customer_id)          # tag
    .tag("site_id", site_id)                  # tag
    .tag("device_type", device_type)          # tag
    .tag("device_uid", device_uid)            # tag
    .field("value", value)                    # field（數值）
    .time(ts, WritePrecision.S)               # 設備時間，精度到「秒」
)
write_api.write(bucket="realtime", org=INFLUX_ORG, record=point)
```

> 注意：`WritePrecision.S`（秒），不是 `SECONDS`——這是 SDK 的命名，曾踩過坑。

### 底層的 Line Protocol

SDK 最終會轉成 InfluxDB 的文字格式「Line Protocol」送出：

```
sensor_data,customer_id=C001,site_id=S001,device_type=temperature,device_uid=fridge01 value=-22.3 1750000000
```

---

## 資料怎麼查出來（Flux 查詢）

InfluxDB 2 用 **Flux** 語言查詢（管道式，類似 shell 的 `|`）。

### 範例 1：查某設備最新值

```flux
from(bucket: "realtime")
  |> range(start: -10m)
  |> filter(fn: (r) => r["_measurement"] == "sensor_data")
  |> filter(fn: (r) => r["device_uid"] == "fridge01")
  |> last()
```

### 範例 2：查某設備過去 1 小時歷史

```flux
from(bucket: "realtime")
  |> range(start: -1h)
  |> filter(fn: (r) => r["device_uid"] == "fridge01")
  |> sort(columns: ["_time"])
```

### 範例 3：某客戶所有設備（用 tag 篩選）

```flux
from(bucket: "realtime")
  |> range(start: -1h)
  |> filter(fn: (r) => r["customer_id"] == "C001")
```

### 範例 4：統計過去 24 小時總筆數

```flux
from(bucket: "realtime")
  |> range(start: -24h)
  |> filter(fn: (r) => r["_measurement"] == "sensor_data")
  |> count()
  |> sum()
```

這幾個查詢分別對應到後端的 `/api/sites/{id}/latest`、`/api/history`、`/admin/overview` 等端點。

---

## 降採樣：長時間範圍的關鍵

如果查「過去 30 天」的資料，每 3 秒一筆 = 約 86 萬筆，全回傳會把瀏覽器卡死。  
解法是 `aggregateWindow`：把時間切成小區間，每區間取平均，只回傳聚合後的點。

```flux
from(bucket: "realtime")
  |> range(start: -30d)
  |> filter(fn: (r) => r["device_uid"] == "meter01")
  |> aggregateWindow(every: 6h, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
```

本專案的 `/api/history` 端點會**依時間跨度自動計算聚合區間**，目標約 300 個點：

| 查詢跨度 | 聚合區間 | 約略回傳點數 |
|---|---|---|
| 15 分鐘 | 每 10 秒 | ~90 |
| 1 小時 | 每 30 秒 | ~120 |
| 24 小時 | 每 ~5 分 | ~300 |
| 7 天 | 每 ~30 分 | ~300 |
| 30 天 | 每 ~2.4 小時 | ~300 |

計算邏輯（[`backend/routers/data.py`](../backend/routers/data.py)）：`區間秒數 = 總跨度秒數 / 300`

---

## 保留策略與免費版限制

| 項目 | InfluxDB Cloud 免費版 |
|---|---|
| 資料保留期 | **30 天**（超過自動刪除）|
| 寫入速率 | 5 MB / 5 分鐘 |
| 讀取速率 | 300 MB / 5 分鐘 |
| Series 基數上限 | 有限制（見下方 Cardinality）|

> 本專案每 3 秒寫 12 筆，資料量很小，遠在免費上限內。  
> 但**資料只留 30 天**——要長期保存需付費或自架 InfluxDB。

---

## 如何在 InfluxDB Cloud 後台查資料

1. 登入 https://cloud2.influxdata.com
2. 左側選 **Data Explorer**
3. 下方可用視覺化介面點選 bucket / measurement / tag，或切到 **Script Editor** 直接寫 Flux
4. 貼上範例查詢，按 **Submit** 即可看到圖表與資料表

也可在 **Load Data → Buckets** 看 `realtime` bucket 的設定與保留期。

---

## Cardinality（基數）陷阱

**Series 基數 = tag 值的所有組合數量。** InfluxDB 的效能與記憶體和基數高度相關。

本專案的基數：
```
customer_id(2) × site_id(4) × device_type(4) × device_uid(12) 的實際組合 = 12 個 series
```
（因為一台設備只屬於一個客戶/門市/類型，所以實際是 12，不是相乘）

⚠️ **陷阱**：絕對不要把「高基數、會一直變的值」放進 tag，例如：
- ❌ 把 `request_id`、`timestamp`、`使用者ID` 放進 tag → series 爆炸，記憶體耗盡
- ✅ 這類值應該放 field，或乾脆不存

本專案的 tag 都是低基數（設備數量固定），所以很安全。

---

## 一句話總結

> InfluxDB 負責「不斷產生、以時間為主軸」的感測器數據；  
> 用 **tag 篩選、field 存值、aggregateWindow 降採樣**，  
> 是本專案即時看板與歷史查詢背後的資料引擎。
