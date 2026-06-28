# 部署到 Hugging Face Spaces（免綁卡的新版後端）

> 為什麼選 HF：Fly.io 新帳號一定要綁信用卡；HF Spaces 免費、免卡、用 Docker 跑。
> 舊版仍在 Render（`render.yaml`），三版設定互不干擾（Render / Fly / HF）。

## Space 資訊
- Space：`YORROY123/iot-energy-cloud`（Docker 類型）
- 後端網址：`https://yorroy123-iot-energy-cloud.hf.space`
- 健康檢查：`GET /health`

## Space 需要的檔案（放在 Space repo 根目錄）
- `Dockerfile`：HF 版（建立 uid 1000 的非 root 使用者；CMD 跑 uvicorn）
- `README.md`：含 HF front matter（`sdk: docker`、`app_port: 8000`）
- 後端原始碼：`main.py`、`database.py`、`models.py`、`mqtt_subscriber.py`、
  `simulator_pub.py`、`ws_manager.py`、`requirements.txt`、`routers/`

> 注意：本 GitHub repo 把後端放在 `backend/` 子目錄；HF Space 則把這些檔案放在
> **根目錄**（因為 HF 從 repo 根讀 README front matter 與 Dockerfile）。

## 環境變數（Space → Settings → Variables and secrets）
Secrets（隱藏）：`INFLUX_TOKEN`、`REDIS_URL`、`POSTGRES_URL`、`ADMIN_KEY`
Variables（公開）：`DEMO_MODE=false`、`RUN_SIMULATOR=true`、`MQTT_HOST=broker.hivemq.com`、
`MQTT_PORT=1883`、`INFLUX_URL`、`INFLUX_ORG`、`INFLUX_BUCKET=realtime`

> `POSTGRES_URL` 要用 Render 的 **External** 連線字串（主機名含 `.oregon-postgres.render.com`），
> 不能用 Internal（HF 在 Render 機房外，連不到 internal）。

## 用 huggingface_hub 部署（程式化）
```python
from huggingface_hub import HfApi
api = HfApi(token="hf_...")  # write token
api.create_repo("YORROY123/iot-energy-cloud", repo_type="space", space_sdk="docker", exist_ok=True)
api.add_space_secret(repo_id="YORROY123/iot-energy-cloud", key="POSTGRES_URL", value="...")
api.add_space_variable(repo_id="YORROY123/iot-energy-cloud", key="DEMO_MODE", value="false")
api.upload_folder(folder_path="space/", repo_id="YORROY123/iot-energy-cloud", repo_type="space")
```

## 前端指到新後端
```
https://yorroy123.github.io/iot-energy-cloud/dashboard.html?backend=https://yorroy123-iot-energy-cloud.hf.space
https://yorroy123.github.io/iot-energy-cloud/admin.html?backend=https://yorroy123-iot-energy-cloud.hf.space
```

## 保持喚醒
免費 Space 閒置 48 小時會休眠。用外部 cron 定時打 `/health`（cron-job.org / UptimeRobot /
GitHub Actions 皆可）即可保活。

## 切換後
記得到 Render 把舊的 `iot-energy-cloud` web service **Suspend**，否則跟 line-ai-agent
兩個一起跑又會吃光每月 750 小時。
