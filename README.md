# IoT Energy Cloud — 開源 IoT 能源管理平台

> 多租戶 AIoT 能源監控 SaaS 平台的開源實作參考，涵蓋從現場感測器到雲端看板的完整技術棧。

---

## 目錄

- [專案背景](#專案背景)
- [系統架構總覽](#系統架構總覽)
- [功能特色](#功能特色)
- [技術棧](#技術棧)
- [快速啟動](#快速啟動)
- [目錄結構](#目錄結構)
- [文件索引](#文件索引)
- [API 一覽](#api-一覽)
- [貢獻方式](#貢獻方式)

---

## 專案背景

本專案模擬一套商用 AIoT 能源管理雲端平台，適用於連鎖門市、銀行分行、餐飲業、校園等場域。  
系統透過現場 IoT 感測器（冷凍冷藏溫度、電力、環境溫濕度、CO₂）蒐集數據，經 MQTT 協定上傳至雲端，  
由後端即時寫入時序資料庫並推播至瀏覽器看板，同時支援遠端下指令控制現場設備。

**核心設計目標：**
- 一套後端服務同時支援多個客戶（多租戶）
- 千台設備並發連線無感掉線
- 異常告警秒級響應
- 新人 30 分鐘內能從零跑起整套系統

---

## 系統架構總覽

```
┌─────────────────────────────────────────────────────────────────┐
│  現場（Field Layer）                                              │
│                                                                   │
│  溫度感測器 ──┐                                                    │
│  電力計量器 ──┤  RS485/Modbus RTU  ┌──────────────┐              │
│  環境感測器 ──┘ ─────────────────→ │ IoT Gateway  │              │
│                                    │ (4G LTE)     │              │
│                                    └──────┬───────┘              │
└───────────────────────────────────────────│─────────────────────┘
                                            │ MQTT over TLS / 4G
┌───────────────────────────────────────────│─────────────────────┐
│  雲端（Cloud Layer）                       ↓                      │
│                                    ┌──────────────┐              │
│                                    │  EMQX Broker │              │
│                                    │  (MQTT 5)    │              │
│                                    └──────┬───────┘              │
│                                           │ subscribe            │
│                              ┌────────────▼────────────┐         │
│                              │   FastAPI Backend        │         │
│                              │                          │         │
│                         ┌────┤  MQTT Subscriber         │         │
│                         │    │  Alert Engine            │         │
│                         │    │  REST API                │         │
│                         │    │  WebSocket Push          │         │
│                         │    └────┬──────────┬──────────┘         │
│                         │         │          │                    │
│                    InfluxDB   PostgreSQL   Redis                  │
│                  (時序資料)  (設備/用戶)  (快取/排程)              │
│                                                                   │
│  瀏覽器 ←──── WebSocket ────────────────────────────────         │
│  Dashboard.html（即時看板）                                       │
└─────────────────────────────────────────────────────────────────┘
```

詳細架構說明請見 [docs/01-架構設計.md](docs/01-架構設計.md)

---

## 功能特色

| 功能 | 說明 |
|---|---|
| 多租戶隔離 | Customer → Site → Device 三層結構，資料完全隔離 |
| 即時監控 | WebSocket 推播，瀏覽器毫秒級更新 |
| 歷史查詢 | InfluxDB 時序查詢，支援任意時間範圍 |
| 告警引擎 | 設定閾值規則，超限自動寫入 AlertLog |
| 遠端控制 | MQTT QoS-2 下發指令，10 秒過期保護 |
| 排程控制 | 存 Redis，支援 Cron 表達式 |
| 模擬器 | 內建 12 台虛擬設備，1% 機率注入異常值 |
| 即時看板 | 純 HTML/JS，無需安裝，雙擊即開 |

---

## 技術棧

| 層次 | 技術 |
|---|---|
| 訊息代理 | [EMQX 5](https://www.emqx.io/) — 支援百萬級 MQTT 連線 |
| 後端框架 | [FastAPI](https://fastapi.tiangolo.com/) — Python async REST + WebSocket |
| 時序資料庫 | [InfluxDB 2.7](https://www.influxdata.com/) — Flux 查詢語言 |
| 關聯式資料庫 | [PostgreSQL 16](https://www.postgresql.org/) — 設備/用戶/告警元資料 |
| 快取 | [Redis 7](https://redis.io/) — 排程儲存、連線快取 |
| ORM | [SQLAlchemy 2](https://www.sqlalchemy.org/) |
| 容器化 | [Docker Compose](https://docs.docker.com/compose/) |
| 前端看板 | 純 HTML + Chart.js（無框架依賴）|
| MQTT 客戶端 | [paho-mqtt 2](https://pypi.org/project/paho-mqtt/) |

---

## 快速啟動

### 前置需求

- [Rancher Desktop](https://rancherdesktop.io/)（開源，選 `dockerd (moby)` 引擎）或 Docker Desktop
- Windows / macOS / Linux

### 一鍵啟動

```bash
git clone https://github.com/YORROY123/iot-energy-cloud.git
cd iot-energy-cloud

# Windows（需關閉 BuildKit 避免 WSL2 segfault）
set DOCKER_BUILDKIT=0
docker compose up --build

# macOS / Linux
docker compose up --build
```

### 啟動後驗證

| 服務 | 網址 | 帳密 |
|---|---|---|
| 即時看板 | 雙擊 `dashboard.html` | — |
| API 文件 | http://localhost:8000/docs | — |
| EMQX 管理後台 | http://localhost:18083 | admin / admin123 |
| InfluxDB | http://localhost:8086 | admin / admin123456 |

完整部署說明請見 [docs/02-部署指南.md](docs/02-部署指南.md)

---

## 目錄結構

```
iot-energy-cloud/
├── docker-compose.yml          # 一鍵啟動所有服務
├── dashboard.html              # 即時監控看板（直接瀏覽器開啟）
│
├── backend/                    # FastAPI 後端
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # 應用程式進入點、WebSocket
│   ├── ws_manager.py           # WebSocket 連線管理器
│   ├── database.py             # DB / InfluxDB / Redis 連線
│   ├── models.py               # SQLAlchemy ORM + Pydantic schemas
│   ├── mqtt_subscriber.py      # MQTT 訂閱 → 寫庫 → 告警 → 推播
│   └── routers/
│       ├── data.py             # 查詢 API（歷史/即時/告警）
│       └── control.py          # 控制 API（下指令/排程）
│
├── simulator/                  # IoT Gateway 模擬器
│   ├── Dockerfile
│   └── main.py                 # 模擬 12 台設備，3 秒送一次
│
└── docs/                       # 技術文件
    ├── 01-架構設計.md
    ├── 02-部署指南.md
    ├── 03-API文件.md
    ├── 04-開發指南.md
    └── 05-技術探討.md
```

---

## API 一覽

```
GET  /api/customers/{id}/sites          取得客戶所有門市與設備
GET  /api/sites/{id}/latest             門市所有設備最新值
GET  /api/sites/{id}/history            單一設備歷史趨勢
GET  /api/devices/{uid}/alerts          設備告警記錄
POST /api/devices/{uid}/control         遠端下指令
POST /api/devices/{uid}/schedule        設定排程
GET  /api/devices/{uid}/schedule        查詢排程
WS   /ws/{client_id}                    即時資料推播
```

完整 API 文件（含範例）請見 [docs/03-API文件.md](docs/03-API文件.md)

---

## 文件索引

| 文件 | 內容 |
|---|---|
| [01-架構設計](docs/01-架構設計.md) | 三層架構、資料流、MQTT topic 設計、多租戶策略 |
| [02-部署指南](docs/02-部署指南.md) | 本機、雲端、Rancher Desktop 疑難排解 |
| [03-API文件](docs/03-API文件.md) | 所有端點說明、請求/回應範例 |
| [04-開發指南](docs/04-開發指南.md) | 新增設備類型、擴充告警、前端改造 |
| [05-技術探討](docs/05-技術探討.md) | 為何選 EMQX、InfluxDB vs TimescaleDB、QoS 策略 |

---

## 貢獻方式

1. Fork 此 repository
2. 建立功能分支：`git checkout -b feat/your-feature`
3. 提交：`git commit -m "feat: 說明"`
4. 推送：`git push origin feat/your-feature`
5. 開 Pull Request

---

> 本專案為技術研究與學習用途，與任何商業廠商無關聯。
