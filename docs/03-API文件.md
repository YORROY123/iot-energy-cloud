# 03 — API 文件

Base URL：`http://localhost:8000`  
互動式文件：`http://localhost:8000/docs`（Swagger UI）

---

## 目錄

- [查詢類 API](#查詢類-api)
- [控制類 API](#控制類-api)
- [WebSocket 即時推播](#websocket-即時推播)
- [資料結構說明](#資料結構說明)
- [錯誤碼](#錯誤碼)

---

## 查詢類 API

### `GET /api/customers/{customer_id}/sites`

取得指定客戶的所有門市，包含門市下的設備清單。

**Path parameters**

| 參數 | 型態 | 說明 |
|---|---|---|
| `customer_id` | integer | 客戶 ID |

**Response 200**

```json
[
  {
    "id": 1,
    "name": "忠孝門市",
    "location": "台北市大安區",
    "devices": [
      {
        "id": 1,
        "device_type": "temperature",
        "device_uid": "fridge01",
        "name": "冷藏櫃01",
        "is_online": true,
        "last_seen": "2026-06-24T10:30:00+00:00"
      }
    ]
  }
]
```

---

### `GET /api/sites/{site_id}/latest`

取得門市所有設備的最新量測值（從 InfluxDB 查 `last()`）。

**Path parameters**

| 參數 | 型態 | 說明 |
|---|---|---|
| `site_id` | integer | 門市 ID |

**Response 200**

```json
[
  {
    "device_uid": "fridge01",
    "device_type": "temperature",
    "value": -22.3,
    "time": "2026-06-24T10:30:00Z"
  },
  {
    "device_uid": "meter01",
    "device_type": "power",
    "value": 1240.5,
    "time": "2026-06-24T10:30:00Z"
  }
]
```

---

### `GET /api/sites/{site_id}/history`

取得單一設備在指定時間範圍內的歷史資料序列。

**Path parameters**

| 參數 | 型態 | 說明 |
|---|---|---|
| `site_id` | integer | 門市 ID |

**Query parameters**

| 參數 | 型態 | 預設 | 說明 |
|---|---|---|---|
| `device_uid` | string | **必填** | 設備唯一識別碼 |
| `hours` | integer | `24` | 查詢過去幾小時 |

**Request 範例**

```
GET /api/sites/1/history?device_uid=fridge01&hours=6
```

**Response 200**

```json
[
  {"time": "2026-06-24T04:00:00Z", "value": -22.1},
  {"time": "2026-06-24T04:03:00Z", "value": -22.4},
  {"time": "2026-06-24T04:06:00Z", "value": -22.2}
]
```

**說明**：時間序列以 3 秒間隔（模擬器頻率）呈現。生產場域可依需求降採樣（aggregateWindow）。

---

### `GET /api/devices/{device_uid}/alerts`

取得設備最近 50 筆告警記錄。

**Path parameters**

| 參數 | 型態 | 說明 |
|---|---|---|
| `device_uid` | string | 設備唯一識別碼 |

**Response 200**

```json
[
  {
    "id": 42,
    "rule_id": 3,
    "device_uid": "fridge01",
    "value": -8.5,
    "triggered_at": "2026-06-24T10:15:33+00:00",
    "resolved_at": null
  }
]
```

---

## 控制類 API

### `POST /api/devices/{device_uid}/control`

透過 MQTT QoS-2 向設備下發控制指令。

**Path parameters**

| 參數 | 型態 | 說明 |
|---|---|---|
| `device_uid` | string | 設備唯一識別碼 |

**Request Body**

```json
{
  "action": "turn_off",
  "params": {
    "duration": 300
  }
}
```

| 欄位 | 型態 | 說明 |
|---|---|---|
| `action` | string | 指令名稱，如 `turn_off`、`set_temp`、`reboot` |
| `params` | object | 可選的附加參數 |

**Response 200**

```json
{
  "status": "published",
  "topic": "iess/control/fridge01",
  "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "expire_at": 1750000010
}
```

**說明**：
- 使用 QoS-2（恰好一次）確保指令不重複送達
- `expire_at` 為 UNIX timestamp，設備應忽略過期指令（防止離線重連後執行舊指令）
- `request_id` 用於對帳，設備回傳狀態時帶回此 ID

---

### `POST /api/devices/{device_uid}/schedule`

設定設備排程（儲存至 Redis）。

**Request Body**

```json
{
  "action": "turn_on",
  "cron": "0 8 * * 1-5",
  "enabled": true
}
```

| 欄位 | 型態 | 說明 |
|---|---|---|
| `action` | string | 排程執行的動作 |
| `cron` | string | Cron 表達式（UTC 時間）|
| `enabled` | boolean | 是否啟用 |

**Response 200**

```json
{"status": "ok"}
```

---

### `GET /api/devices/{device_uid}/schedule`

查詢設備目前的排程設定。

**Response 200**

```json
{
  "action": "turn_on",
  "cron": "0 8 * * 1-5",
  "enabled": true,
  "created_at": "2026-06-24T10:00:00+00:00"
}
```

**Response 404**（尚未設定排程）

```json
{"detail": "no schedule"}
```

---

## WebSocket 即時推播

### 連線

```
ws://localhost:8000/ws/{client_id}
```

- `client_id`：任意字串，如 `dashboard`、`mobile-user-123`
- 同一 `client_id` 只保留最後一個連線（前一個自動斷開）
- 每 30 秒 server 主動送 `ping` keepalive

### 接收訊息格式（Server → Client）

**感測器更新**（每次 MQTT on_message 都廣播給所有 WebSocket 客戶端）

```json
{
  "type": "sensor_update",
  "customer_id": "C001",
  "site_id": "S001",
  "device_type": "temperature",
  "device_uid": "fridge01",
  "value": -22.3,
  "ts": 1750000000
}
```

**Keepalive Ping**（30 秒無訊息時）

```json
{"type": "ping"}
```

### 發送訊息格式（Client → Server）

可傳任意 JSON，Server 會回傳 ack：

```json
// Client 傳
{"action": "subscribe", "device_uid": "fridge01"}

// Server 回
{"type": "ack", "received": {"action": "subscribe", "device_uid": "fridge01"}}
```

> 目前 Server 不處理 Client 發來的訊息（只 ack），訂閱過濾邏輯可按需求擴充。

### JavaScript 連線範例

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/my-dashboard');

ws.onopen = () => console.log('Connected');

ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === 'sensor_update') {
    console.log(`${msg.device_uid}: ${msg.value}`);
  }
};

ws.onclose = () => setTimeout(() => connect(), 3000); // 自動重連
```

---

## 資料結構說明

### Device Types

| `device_type` | 說明 | 單位 | 正常範圍 |
|---|---|---|---|
| `temperature` | 冷凍冷藏溫度 | °C | -25 ～ -15（冷凍），0 ～ 10（冷藏）|
| `power` | 電力（kW 或 W） | W | 依設備規格 |
| `humidity` | 相對濕度 | % | 40 ～ 70 |
| `co2` | 二氧化碳濃度 | ppm | < 1000（室內良好）|

### Cron 表達式範例

| 表達式 | 說明 |
|---|---|
| `0 8 * * 1-5` | 每週一至週五早上 8 點 |
| `0 22 * * *` | 每天晚上 10 點 |
| `*/15 * * * *` | 每 15 分鐘 |
| `0 0 1 * *` | 每月 1 號午夜 |

---

## 錯誤碼

| HTTP 狀態碼 | 含義 |
|---|---|
| `200` | 成功 |
| `404` | 資源不存在（門市、設備、排程） |
| `422` | 請求 Body 格式錯誤（Pydantic 驗證失敗） |
| `500` | 伺服器內部錯誤（查 `docker logs` 排查） |
