import os
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent,
    GroupSource,
    RoomSource,
    UserSource
)

# Load environment variables
load_dotenv()

LINE_CHANNEL_SECRET = (os.getenv("LINE_CHANNEL_SECRET") or "").strip()
LINE_CHANNEL_ACCESS_TOKEN = (os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()

app = FastAPI(title="LINE Webhook Receiver - Phase 1")

if LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN:
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    api_client = ApiClient(configuration)
    messaging_api = MessagingApi(api_client)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)
else:
    print("Warning: LINE credentials not fully set in environment variables.")
    handler = None

def get_sender_details(event):
    source_type = event.source.type
    group_id = None
    user_id = getattr(event.source, "user_id", None) or "Unknown"
    display_name = "Unknown User"

    if isinstance(event.source, GroupSource):
        group_id = event.source.group_id
        if user_id != "Unknown" and messaging_api:
            try:
                profile = messaging_api.get_group_member_profile(group_id, user_id)
                display_name = profile.display_name
            except Exception as e:
                print(f"Could not fetch group profile for {user_id}: {e}")
    elif isinstance(event.source, RoomSource):
        group_id = event.source.room_id
        if user_id != "Unknown" and messaging_api:
            try:
                profile = messaging_api.get_room_member_profile(group_id, user_id)
                display_name = profile.display_name
            except Exception as e:
                print(f"Could not fetch room profile for {user_id}: {e}")
    elif isinstance(event.source, UserSource):
        if user_id != "Unknown" and messaging_api:
            try:
                profile = messaging_api.get_profile(user_id)
                display_name = profile.display_name
            except Exception as e:
                print(f"Could not fetch user profile for {user_id}: {e}")

    return source_type, group_id, user_id, display_name

@app.get("/")
@app.get("/api")
async def root():
    return {
        "status": "online",
        "phase": "Phase 1 - LINE Webhook Receiver",
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
        source_type, group_id, user_id, display_name = get_sender_details(event)
        text = event.message.text
        message_id = event.message.id
        quoted_msg_id = getattr(event.message, "quoted_message_id", None)

        quote_info = f" | QuotedMsgID: {quoted_msg_id}" if quoted_msg_id else ""
        print(f"[TEXT EVENT] Name: '{display_name}' (UserID: {user_id}) | GroupID: {group_id} | MessageID: {message_id}{quote_info} | Message: {text}")

    @handler.add(MessageEvent, message=ImageMessageContent)
    def handle_image_message(event: MessageEvent):
        source_type, group_id, user_id, display_name = get_sender_details(event)
        message_id = event.message.id

        print(f"[IMAGE EVENT] Name: '{display_name}' (UserID: {user_id}) | GroupID: {group_id} | ImageMsgID: {message_id}")
