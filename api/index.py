import os
import json
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient
)

# Load environment variables
load_dotenv()

LINE_CHANNEL_SECRET = (os.getenv("LINE_CHANNEL_SECRET") or "").strip()
LINE_CHANNEL_ACCESS_TOKEN = (os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()

app = FastAPI(title="LINE Webhook Receiver - Pure Raw Source")

if LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN:
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)
else:
    print("Warning: LINE credentials not fully set in environment variables.")
    handler = None

@app.get("/")
@app.get("/api")
async def root():
    return {
        "status": "online",
        "phase": "Phase 1 - Pure Raw Webhook Receiver",
        "message": "Vercel server is running cleanly!"
    }

@app.post("/callback")
@app.post("/api/callback")
async def callback(request: Request):
    if not handler:
        raise HTTPException(status_code=500, detail="LINE credentials not configured.")

    signature = request.headers.get("X-Line-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body = (await request.body()).decode("utf-8")

    # 🔥 Pure Raw JSON Payload logging without external blocking API calls
    try:
        parsed_json = json.loads(body)
        print(f"[RAW WEBHOOK JSON]:\n{json.dumps(parsed_json, indent=2, ensure_ascii=False)}")
    except Exception:
        print(f"[RAW WEBHOOK BODY]: {body}")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print(f"[SIGNATURE ERROR] Signature validation failed. Body length: {len(body)}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print(f"Error handling webhook event: {e}")

    return "OK"
