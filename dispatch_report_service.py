import os
import sys
import re
import csv
import io
import json
import requests
from datetime import datetime, timedelta, timezone
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
    ReplyMessageRequest,
    TextMessage
)

def parse_date_range(text: str):
    """
    Parses start and end date from command text.
    Examples:
      /report 12/09/2026 14/09/2026
      /report 12/09 14/09
      /report 2026-09-12 2026-09-14
      /report today
      /report yesterday
      /report
    Returns: (start_utc_iso, end_utc_iso, start_th_str, end_th_str)
    """
    now_utc = datetime.now(timezone.utc)
    now_th = now_utc + timedelta(hours=7)
    today_th = now_th.date()
    
    parts = text.strip().split()
    args = parts[1:]
    
    start_date = None
    end_date = None
    
    def parse_single_date(s: str):
        s = s.strip()
        if s.lower() == "today" or s == "วันนี้":
            return today_th
        if s.lower() == "yesterday" or s == "เมื่อวาน":
            return today_th - timedelta(days=1)
        
        # Try DD/MM/YYYY or DD/MM
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?$", s)
        if m:
            day, month = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else today_th.year
            if year < 100:
                year += 2000
            if year > 2500:
                year -= 543
            return datetime(year, month, day).date()
        
        # Try YYYY-MM-DD
        m2 = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", s)
        if m2:
            year, month, day = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            if year > 2500:
                year -= 543
            return datetime(year, month, day).date()
            
        return None

    if len(args) >= 2:
        start_date = parse_single_date(args[0])
        end_date = parse_single_date(args[1])
    elif len(args) == 1:
        start_date = parse_single_date(args[0])
        end_date = start_date

    if not start_date or not end_date:
        start_date = today_th - timedelta(days=2)
        end_date = today_th
        
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    start_th_dt = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0)
    end_th_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59)
    
    start_utc_dt = start_th_dt - timedelta(hours=7)
    end_utc_dt = end_th_dt - timedelta(hours=7)
    
    return (
        start_utc_dt.isoformat(),
        end_utc_dt.isoformat(),
        start_date.strftime("%d/%m/%Y"),
        end_date.strftime("%d/%m/%Y")
    )

def extract_dispatch_records_with_gemini(messages_text_list: list) -> list:
    """Uses Gemini AI to parse raw LINE messages into structured JSON dispatch items."""
    if not GEMINI_API_KEY:
        return []
        
    prompt = """คุณคือระบบ AI ผู้เชี่ยวชาญด้านการวิเคราะห์และสรุปงานขนส่ง (Transport Dispatching System)
จงวิเคราะห์ข้อความแจ้งงานและพูดคุยการขนส่งต่อไปนี้ แล้วสกัดรายการงานขนส่งทั้งหมดออกมาเป็น JSON Array ตามโครงสร้างที่กำหนดอย่างเคร่งครัด:

โครงสร้าง JSON ที่ต้องตอบ (ห้ามมีข้อความอื่นนอกจาก JSON Array):
[
  {
    "delivery_date": "DD/MM/YYYY (วันที่ต้องวิ่งงาน/ส่งงาน เช่น 14/09/2026)",
    "slot_time": "เวลาเข้าโหลด/Plan Time เช่น 04:30 น., 11:00 น. หรือ 'ตามคิว/รอแจ้ง'",
    "customer": "ชื่อลูกค้า/เจ้าของงาน เช่น Total Corbion, Purac, Brose, DTS, Exotic Food, Powertech, UACJ",
    "job_type": "ประเภทงาน เช่น ตู้คอนเทนเนอร์, ชัทเทิล, แบตเตอรี่ (UN 3480), แร็ค, ทอยตู้เปล่า",
    "container_no": "หมายเลขตู้ (ถ้ามี) เช่น FFAU7015469",
    "booking_no": "หมายเลข Booking (ถ้ามี) เช่น 276771612",
    "route": "เส้นทาง เช่น ลาน JTC 7 -> FLS ระยอง, Powertech -> Benz, Exotic ระยอง -> แหลมฉบัง",
    "driver_name": "ชื่อ พขร./คนขับที่ได้รับมอบหมาย เช่น @Phornchai, @สมยศ, @NewZeaLand",
    "license_plate": "ทะเบียนรถหัวหรือหาง (ถ้ามี) เช่น 75-0485, 700-4894",
    "status_note": "หมายเหตุเพิ่มเติม (ถ้ามี)"
  }
]

ข้อความการทำงานทั้งหมด:
""" + "\n---\n".join(messages_text_list)

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }
    
    models_to_try = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-flash-latest"]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, headers=headers, timeout=60)
            if res.status_code == 200:
                raw_text = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()
                
                data = json.loads(raw_text)
                if isinstance(data, list):
                    return data
        except Exception as e:
            print(f"[GEMINI PARSING NOTICE with {model_name}]: {e}")
            
    return []

def format_report_to_markdown_and_csv(records: list, start_th: str, end_th: str):
    """Formats parsed records into Markdown tracking dashboard and standard CSV block."""
    if not records:
        return (
            f"ℹ️ ไม่พบข้อมูลการแจ้งงานในช่วงวันที่ {start_th} ถึง {end_th} ในระบบครับ",
            ""
        )

    def sort_key(r):
        return (r.get("delivery_date", ""), r.get("customer", ""), r.get("slot_time", ""))
    
    sorted_records = sorted(records, key=sort_key)

    md_lines = [
        f"🚚 **[รายงานสรุปการเดินรถและจ่ายงาน]**",
        f"📅 **ช่วงวันที่:** {start_th} ถึง {end_th} | **รวมทั้งหมด:** {len(sorted_records)} รายการ\n",
        "| วันที่ส่งงาน | Slot/Plan Time | ลูกค้า | เบอร์ตู้ / Booking | เส้นทาง (ต้นทาง ➔ ปลายทาง) | พขร. | ทะเบียน |",
        "| :---: | :---: | :--- | :--- | :--- | :--- | :---: |"
    ]

    for r in sorted_records:
        del_date = r.get("delivery_date", "-")
        slot = r.get("slot_time", "-")
        cust = r.get("customer", "-")
        con = r.get("container_no", "")
        bkg = r.get("booking_no", "")
        con_bkg = f"`{con}`" if con else (f"BKG: {bkg}" if bkg else "-")
        if con and bkg:
            con_bkg = f"`{con}`<br><small>BKG: {bkg}</small>"
            
        route = r.get("route", "-")
        driver = r.get("driver_name", "-")
        plate = r.get("license_plate", "-")
        
        md_lines.append(f"| {del_date} | {slot} | **{cust}** | {con_bkg} | {route} | {driver} | {plate} |")

    markdown_text = "\n".join(md_lines)

    csv_output = io.StringIO()
    writer = csv.writer(csv_output)
    writer.writerow([
        "Delivery_Date",
        "Slot_Plan_Time",
        "Customer",
        "Job_Type",
        "Container_No",
        "Booking_No",
        "Route",
        "Driver_Name",
        "License_Plate",
        "Note"
    ])

    for r in sorted_records:
        writer.writerow([
            r.get("delivery_date", ""),
            r.get("slot_time", ""),
            r.get("customer", ""),
            r.get("job_type", ""),
            r.get("container_no", ""),
            r.get("booking_no", ""),
            r.get("route", ""),
            r.get("driver_name", ""),
            r.get("license_plate", ""),
            r.get("status_note", "")
        ])

    csv_text = csv_output.getvalue().strip()
    return markdown_text, csv_text

def handle_dispatch_report_trigger(event):
    """Main trigger handler for /report and /dispatch_report commands in LINE."""
    text = getattr(event.message, "text", "").strip()
    reply_token = event.reply_token
    quote_token = getattr(event.message, "quote_token", None)

    start_utc_iso, end_utc_iso, start_th, end_th = parse_date_range(text)
    
    db = database.get_db()
    
    query = {
        "timestamp": {"$gte": start_utc_iso, "$lte": end_utc_iso},
        "message_type": {"$in": ["text", "file"]}
    }
    
    docs = list(db.messages.find(query).sort("timestamp", 1))
    
    if not docs:
        send_reply(reply_token, f"ℹ️ ไม่พบบันทึกข้อความในช่วงวันที่ {start_th} ถึง {end_th} ในฐานข้อมูลครับ", quote_token=quote_token)
        return

    relevant_texts = []
    keywords = ["แจ้งงาน", "bkg", "booking", "job", "ตู้", "slot", "consol", "รับตู้", "ตัดหาง", "คืนตู้", "brose", "corbion", "purac", "exotic", "powertech", "uacj", "dts"]
    
    for d in docs:
        c = d.get("content", "")
        if any(k in c.lower() for k in keywords):
            relevant_texts.append(c)

    if not relevant_texts:
        relevant_texts = [d.get("content", "") for d in docs if d.get("content")][:50]

    records = extract_dispatch_records_with_gemini(relevant_texts)
    
    markdown_report, csv_content = format_report_to_markdown_and_csv(records, start_th, end_th)
    
    messages_to_send = []
    
    if len(markdown_report) <= 4500:
        messages_to_send.append(markdown_report)
    else:
        chunks = [markdown_report[i:i+4000] for i in range(0, len(markdown_report), 4000)]
        messages_to_send.extend(chunks)

    if csv_content:
        csv_block = f"📄 **[CSV DATA สำหรับนำเข้า Excel]**\n```csv\n{csv_content[:3500]}\n```"
        messages_to_send.append(csv_block)

    send_reply_multi(reply_token, messages_to_send[:5], quote_token=quote_token)

def send_reply(reply_token: str, text: str, quote_token: str = None):
    send_reply_multi(reply_token, [text], quote_token=quote_token)

def send_reply_multi(reply_token: str, text_list: list, quote_token: str = None):
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        
        text_messages = []
        for idx, t in enumerate(text_list):
            if idx == 0 and quote_token:
                try:
                    text_messages.append(TextMessage(text=t, quote_token=quote_token))
                    continue
                except Exception:
                    pass
            text_messages.append(TextMessage(text=t))
            
        try:
            api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=text_messages
            ))
        except Exception as err:
            print(f"[REPLY ERROR] Trying fallback without quote: {err}")
            plain_msgs = [TextMessage(text=t) for t in text_list]
            api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=plain_msgs
            ))
