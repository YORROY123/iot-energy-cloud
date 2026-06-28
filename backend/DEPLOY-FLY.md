# 部署到 Fly.io（新版後端）

> 舊版仍由 Render 的 `render.yaml` 管理（Python 原生環境，不用 Dockerfile）。
> 新版由本資料夾的 `Dockerfile` + `fly.toml` 管理。兩版互不干擾。

## 0. 前置
- Fly.io 帳號（目前需綁信用卡，但本服務設定為「閒置自動關機」，費用接近 $0）。
- 從 **Render Dashboard → iot-energy-cloud → Environment** 把以下變數值複製出來備用：
  - `POSTGRES_URL`（用「外部連線字串 External Database URL」那個）
  - `INFLUX_URL`、`INFLUX_TOKEN`、`INFLUX_ORG`
  - `REDIS_URL`

## 1. 安裝 flyctl 並登入
```powershell
# Windows PowerShell
iwr https://fly.io/install.ps1 -useb | iex
fly auth login
```

## 2. 建立 app（不要馬上部署）
在 `backend/` 目錄執行：
```bash
fly launch --no-deploy --copy-config --name iot-energy-cloud --region nrt
```
> 已經有 `fly.toml`，它會沿用設定。若問是否建立 Postgres/Redis，一律選 **No**（我們用既有的外部服務）。

## 3. 設定環境變數（Secrets）
把下面尖括號換成你從 Render 複製的值：
```bash
fly secrets set \
  DEMO_MODE=false \
  RUN_SIMULATOR=true \
  MQTT_HOST=broker.hivemq.com \
  MQTT_PORT=1883 \
  INFLUX_BUCKET=realtime \
  POSTGRES_URL="<貼上 Render 的 External Database URL>" \
  INFLUX_URL="<貼上>" \
  INFLUX_TOKEN="<貼上>" \
  INFLUX_ORG="<貼上>" \
  REDIS_URL="<貼上>"
```

## 4. 部署
```bash
fly deploy
```
完成後網址是 `https://iot-energy-cloud.fly.dev`，測試：
```bash
curl https://iot-energy-cloud.fly.dev/health
# 預期：{"status":"ok","demo_mode":false}
```

## 5. 前端指到新後端
看板用網址參數覆寫後端，分享連結改成：
```
https://yorroy123.github.io/iot-energy-cloud/dashboard.html?backend=https://iot-energy-cloud.fly.dev
https://yorroy123.github.io/iot-energy-cloud/admin.html?backend=https://iot-energy-cloud.fly.dev
```

## 6. 關掉 Render 上的舊 web service（重要）
回 Render，把 **iot-energy-cloud** 這個 web service **Suspend**（設定檔 `render.yaml` 留著沒關係）。
否則它跟 line-ai-agent 兩個一起跑，下個月又會吃光 750 小時。

---
### 注意事項
- **Render 免費 PostgreSQL 約 30 天會過期刪除**。長期建議把資料庫也搬到 Neon（免費、永久），再把 `POSTGRES_URL` 換成 Neon 的連線字串。
- `fly.toml` 預設 `min_machines_running = 0`（閒置休眠、省錢）。若要 24 小時不間斷產生資料，改成 `1`。
