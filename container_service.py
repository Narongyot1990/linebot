import os
import sys
import json
import base64
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Ensure root directory in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database

load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = (os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()

from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage
)

def analyze_images_with_gemini(image_bytes_list: list) -> str:
    """Sends image bytes list to Gemini Vision AI to extract container details."""
    if not GEMINI_API_KEY:
        return "⚠️ กรุณาตั้งค่า GEMINI_API_KEY ใน Environment Variables ก่อนใช้งานฟีเจอร์นี้ครับ"

    try:
        prompt = """คุณคือระบบสกัดข้อมูลตู้สินค้าอัตโนมัติ

คำสั่งสำคัญอย่างเคร่งครัด:
- **ห้ามมีคำเกริ่น ทักทาย หรืออารัมภบทเด็ดขาด** (เช่น ห้ามมีคำว่า 'เรียนผู้ใช้บริการ' หรือ 'นี่คือผลการวิเคราะห์')
- ตอบกลับอย่างกระชับ สั้น ตรงประเด็น ทันที
- หากพบข้อมูลไม่ตรงกัน (Mismatch) ให้ระบุเตือนสั้นๆ ในส่วนผลการตรวจสอบ

รูปแบบการตอบ (ตอบตามโครงสร้างนี้เท่านั้น):

📋 **Booking No.**: [ข้อมูล]
📦 **Container No.**: [ข้อมูล]
🔒 **Seal No.**: [ข้อมูล]
⚖️ **Tare Weight**: [ข้อมูล]
📐 **Size / Code**: [ข้อมูล]

🔍 **ผลการตรวจสอบ**: [ตรงกันถูกต้อง / หรือแจ้งข้อพบบกพร่องสั้นๆ 1-2 บรรทัด]"""

        parts = []
        for img_bytes in image_bytes_list:
            b64_str = base64.b64encode(img_bytes).decode("utf-8")
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": b64_str
                }
            })
        parts.append({"text": prompt})

        headers = {
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json"
        }

        models_to_try = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest"]
        last_err = None

        for model_name in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            res = requests.post(url, json={"contents": [{"parts": parts}]}, headers=headers, timeout=45)
            if res.status_code == 200:
                res_data = res.json()
                try:
                    text_result = res_data["candidates"][0]["content"]["parts"][0]["text"]
                    return text_result
                except Exception as e:
                    last_err = f"Error parsing response: {e}"
            else:
                last_err = f"HTTP {res.status_code}: {res.text}"

        return f"⚠️ เกิดข้อผิดพลาดจาก Gemini API: {last_err}"
    except Exception as e:
        print(f"[GEMINI ERROR]: {e}")
        return f"⚠️ เกิดข้อผิดพลาดในการประมวลผลของ AI: {str(e)}"

def handle_container_info_trigger(event):
    """Handles the /get_container_info command triggered by quoting a message."""
    quoted_msg_id = getattr(event.message, "quoted_message_id", None)
    if not quoted_msg_id:
        reply_text = "⚠️ กรุณากด Reply (Quote) ที่รูปภาพหรือข้อความตู้สินค้า แล้วพิมพ์คำสั่ง /get_container_info อีกครั้งครับ"
        send_reply(event.reply_token, reply_text)
        return

    db = database.get_db()
    quoted_doc = database.get_message_by_id(quoted_msg_id)

    target_image_msg_ids = []

    if quoted_doc and quoted_doc.get("message_type") == "image":
        target_image_msg_ids.append(quoted_msg_id)

        try:
            timestamp_str = quoted_doc.get("timestamp")
            target_time = datetime.fromisoformat(timestamp_str)
            start_time = (target_time - timedelta(seconds=60)).isoformat()
            end_time = (target_time + timedelta(seconds=60)).isoformat()

            sibling_docs = list(db.messages.find({
                "group_id": quoted_doc.get("group_id"),
                "user_id": quoted_doc.get("user_id"),
                "message_type": "image",
                "timestamp": {"$gte": start_time, "$lte": end_time}
            }))

            for s_doc in sibling_docs:
                s_id = s_doc.get("message_id")
                if s_id and s_id not in target_image_msg_ids:
                    target_image_msg_ids.append(s_id)
        except Exception as ex:
            print(f"[BATCH LOOKUP NOTICE]: {ex}")
    else:
        target_image_msg_ids.append(quoted_msg_id)

    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    image_bytes_list = []

    with ApiClient(configuration) as api_client:
        blob_api = MessagingApiBlob(api_client)
        for msg_id in target_image_msg_ids:
            try:
                img_bytes = blob_api.get_message_content(message_id=msg_id)
                image_bytes_list.append(img_bytes)
                print(f"[FETCH IMAGE SUCCESS] Downloaded {len(img_bytes)} bytes for msg_id: {msg_id}")
            except Exception as err:
                print(f"[FETCH IMAGE NOTICE] Could not fetch binary for {msg_id}: {err}")

    if not image_bytes_list:
        send_reply(event.reply_token, f"⚠️ ไม่พบรูปภาพในข้อความที่ถูก Quote (ID: {quoted_msg_id}) กรุณา Quote ที่รูปภาพตู้สินค้าหรือรูปใบ EIR ครับ")
        return

    analysis_result = analyze_images_with_gemini(image_bytes_list)
    send_reply(event.reply_token, f"🤖 **[ผลสรุปข้อมูลตู้สินค้า]**\n\n{analysis_result}")

def send_reply(reply_token: str, text: str):
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)]
        ))
