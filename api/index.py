import os
import sys
import json
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

# Ensure root directory is in sys.path so database module can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent,
    FileMessageContent,
    GroupSource,
    RoomSource,
    UserSource
)

# Load environment variables
load_dotenv()

LINE_CHANNEL_SECRET = (os.getenv("LINE_CHANNEL_SECRET") or "").strip()
LINE_CHANNEL_ACCESS_TOKEN = (os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()

app = FastAPI(title="LINE Webhook Receiver - Pure Raw Source with MongoDB Atlas")

if LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN:
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)
else:
    print("Warning: LINE credentials not fully set in environment variables.")
    handler = None

def get_source_details(event):
    source_type = event.source.type
    group_id = None
    user_id = getattr(event.source, "user_id", None) or "Unknown"

    if isinstance(event.source, GroupSource):
        group_id = event.source.group_id
    elif isinstance(event.source, RoomSource):
        group_id = event.source.room_id

    return source_type, group_id, user_id

@app.get("/")
@app.get("/api")
async def root():
    return {
        "status": "online",
        "phase": "Phase 2 - Webhook Receiver & MongoDB Atlas (Pure Text Storage)",
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

    # 🔥 Pure Raw JSON Payload logging
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

# Event Handlers
if handler:
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_text_message(event: MessageEvent):
        source_type, group_id, user_id = get_source_details(event)
        text = event.message.text
        message_id = event.message.id
        quoted_msg_id = getattr(event.message, "quoted_message_id", None)

        # Save to MongoDB Atlas
        try:
            database.save_message_record(
                source_type=source_type,
                group_id=group_id,
                user_id=user_id,
                display_name="Unknown",  # Pure raw pass-through without blocking Profile API
                message_id=message_id,
                quoted_message_id=quoted_msg_id,
                message_type="text",
                content=text,
                raw_event=json.loads(event.to_json()) if hasattr(event, "to_json") else None
            )
        except Exception as e:
            print(f"Failed to record message in MongoDB: {e}")

    @handler.add(MessageEvent, message=ImageMessageContent)
    def handle_image_message(event: MessageEvent):
        source_type, group_id, user_id = get_source_details(event)
        message_id = event.message.id

        try:
            database.save_message_record(
                source_type=source_type,
                group_id=group_id,
                user_id=user_id,
                display_name="Unknown",
                message_id=message_id,
                quoted_message_id=None,
                message_type="image",
                content=f"IMAGE_MESSAGE_ID:{message_id}",
                raw_event=json.loads(event.to_json()) if hasattr(event, "to_json") else None
            )
        except Exception as e:
            print(f"Failed to record image message in MongoDB: {e}")

    @handler.add(MessageEvent, message=FileMessageContent)
    def handle_file_message(event: MessageEvent):
        source_type, group_id, user_id = get_source_details(event)
        message_id = event.message.id
        file_name = getattr(event.message, "file_name", "file.pdf")

        try:
            database.save_message_record(
                source_type=source_type,
                group_id=group_id,
                user_id=user_id,
                display_name="Unknown",
                message_id=message_id,
                quoted_message_id=None,
                message_type="file",
                content=f"FILE_MESSAGE_ID:{message_id}:{file_name}",
                raw_event=json.loads(event.to_json()) if hasattr(event, "to_json") else None
            )
        except Exception as e:
            print(f"Failed to record file message in MongoDB: {e}")

    @handler.default()
    def handle_default(event):
        pass
