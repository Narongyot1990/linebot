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

def validate_iso6346(code: str) -> str:
    """Validates 11-character Container Number according to ISO 6346 MOD 11 algorithm."""
    clean_code = "".join(c for c in code if c.isalnum()).upper()
    if len(clean_code) != 11:
        return "INVALID_FORMAT"
    
    char_map = {
        'A':10, 'B':12, 'C':13, 'D':14, 'E':15, 'F':16, 'G':17, 'H':18, 'I':19, 'J':20,
        'K':21, 'L':23, 'M':24, 'N':25, 'O':26, 'P':27, 'Q':28, 'R':29, 'S':30, 'T':31,
        'U':32, 'V':34, 'W':35, 'X':36, 'Y':37, 'Z':38
    }
    weights = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512]
    
    try:
        total = 0
        for i in range(10):
            ch = clean_code[i]
            val = char_map[ch] if ch.isalpha() else int(ch)
            total += val * weights[i]
        
        calc_check = (total % 11) % 10
        actual_check = int(clean_code[10])
        return "VALID" if calc_check == actual_check else f"INVALID (Expected {calc_check})"
    except Exception:
        return "INVALID"

def analyze_images_with_gemini(image_bytes_list: list) -> str:
    """Sends image bytes list to Gemini Vision AI to extract container details."""
    if not GEMINI_API_KEY:
        return "⚠️ กรุณาตั้งค่า GEMINI_API_KEY ใน Environment Variables ก่อนใช้งานฟีเจอร์นี้ครับ"

    try:
        prompt = """คุณคือระบบสกัดและตรวจสอบข้อมูลตู้สินค้าตามมาตรฐาน ISO 6346

คำสั่งสำคัญอย่างเคร่งครัด:
- ตอบกลับเฉพาะโครงสร้างข้อความด้านล่างนี้เท่านั้น
- ห้ามมีคำเกริ่น ห้ามทักทาย ห้ามมีคำอธิบายเพิ่มเติม
- หากฟิลด์ไหนอ่านไม่ออกหรือไม่มีในภาพ ให้ระบุ N/A
- ตรวจสอบความถูกต้องของเลขตู้ Container No ตามมาตรฐาน ISO 6346 (4 อักษร + 6 ตัวเลข + 1 ตัวเลขเช็ก) หากเอกสารระบุเลขตู้ต่างจากตัวตู้จริง ให้เลือกเลขตู้ที่ถูกต้องตาม ISO 6346

รูปแบบที่ต้องตอบ (ตอบตามโครงสร้างนี้เป๊ะๆ เท่านั้น):

[Container information]
Booking No: [ข้อมูล]
Container No: [ข้อมูล]
ISO6346: [VALID หรือ INVALID]
Seal No: [ข้อมูล]
Tare Weight: [ข้อมูล]
Size: [ข้อมูล]"""

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
                    text_result = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    
                    # Double check ISO 6346 mathematically in Python for guaranteed accuracy
                    lines = text_result.split("\n")
                    container_no = ""
                    for line in lines:
                        if line.startswith("Container No:"):
                            container_no = line.split(":", 1)[1].strip()
                            break
                    
                    if container_no and container_no != "N/A":
                        iso_status = validate_iso6346(container_no)
                        new_lines = []
                        iso_found = False
                        for line in lines:
                            if line.startswith("ISO6346:"):
                                new_lines.append(f"ISO6346: {iso_status}")
                                iso_found = True
                            else:
                                new_lines.append(line)
                                if line.startswith("Container No:") and not any(l.startswith("ISO6346:") for l in lines):
                                    new_lines.append(f"ISO6346: {iso_status}")
                        text_result = "\n".join(new_lines)

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
        send_reply(event.reply_token, reply_text, quote_token=getattr(event.message, "quote_token", None))
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

    # Extract quote_token for quoted reply targeting original quoted message or event message
    event_quote_token = getattr(event.message, "quote_token", None)
    quoted_doc_quote_token = None
    if quoted_doc:
        raw_msg = (quoted_doc.get("raw_event") or {}).get("message") or {}
        quoted_doc_quote_token = raw_msg.get("quoteToken") or raw_msg.get("quote_token")
    
    target_quote_token = quoted_doc_quote_token or event_quote_token

    if not image_bytes_list:
        send_reply(event.reply_token, f"⚠️ ไม่พบรูปภาพในข้อความที่ถูก Quote (ID: {quoted_msg_id}) กรุณา Quote ที่รูปภาพตู้สินค้าหรือรูปใบ EIR ครับ", quote_token=target_quote_token)
        return

    analysis_result = analyze_images_with_gemini(image_bytes_list)
    send_reply(event.reply_token, analysis_result, quote_token=target_quote_token)

def send_reply(reply_token: str, text: str, quote_token: str = None):
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        if quote_token:
            try:
                msg = TextMessage(text=text, quote_token=quote_token)
                api.reply_message(ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[msg]
                ))
                return
            except Exception as err:
                print(f"[REPLY WITH QUOTE NOTICE] Fallback to standard reply: {err}")

        api.reply_message(ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)]
        ))
