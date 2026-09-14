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
    TextMessage,
    FlexMessage,
    FlexContainer
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

def sanitize_record(r: dict, default_date: str = "") -> dict:
    """Cleans up raw AI extracted record to prevent None, null, or garbage values."""
    def clean_str(val):
        if val is None:
            return ""
        s = str(val).strip()
        if s.lower() in ["none", "null", "undefined", "-", "n/a", "ไม่มี"]:
            return ""
        return s

    del_date = clean_str(r.get("delivery_date")) or default_date
    slot_time = clean_str(r.get("slot_time")) or "ตามคิว/รอแจ้ง"
    cust = clean_str(r.get("customer")) or "ไม่ระบุลูกค้า"
    job_type = clean_str(r.get("job_type"))
    con_no = clean_str(r.get("container_no"))
    bkg_no = clean_str(r.get("booking_no"))
    route = clean_str(r.get("route")) or "-"
    driver = clean_str(r.get("driver_name")) or "-"
    plate = clean_str(r.get("license_plate"))
    note = clean_str(r.get("status_note"))

    return {
        "delivery_date": del_date,
        "slot_time": slot_time,
        "customer": cust,
        "job_type": job_type,
        "container_no": con_no,
        "booking_no": bkg_no,
        "route": route,
        "driver_name": driver,
        "license_plate": plate,
        "status_note": note
    }

def extract_dispatch_records_with_gemini(messages_text_list: list, default_date: str = "") -> list:
    """Uses Gemini AI with strict JSON schema to parse raw LINE messages."""
    if not GEMINI_API_KEY:
        return []
        
    prompt = """คุณคือระบบ AI วิเคราะห์และสกัดข้อมูลการจ่ายงานขนส่ง (Transport Dispatching System)
วิเคราะห์ข้อความแจ้งงานและการประสานงานเดินรถต่อไปนี้ แล้วแปลงเป็น JSON Array ตาม schema นี้เท่านั้น:

[
  {
    "delivery_date": "DD/MM/YYYY (วันที่ต้องวิ่งงาน เช่น 14/09/2026)",
    "slot_time": "เวลาเข้าโหลด/Plan Time เช่น 04:30 น., 10:00 น. หรือถ้าไม่ระบุให้ใส่ 'ตามคิว/รอแจ้ง'",
    "customer": "ชื่อลูกค้า เช่น Brose, DTS, Total Corbion, Purac, CRA / FSCC, Exotic Food, Homerich (HRF)",
    "job_type": "ประเภทงาน เช่น ตู้หนัก, ตู้คอนเทนเนอร์ 20GP, ขนแร็ค, ชัทเทิล, ทอยตู้เปล่า",
    "container_no": "หมายเลขตู้ (ถ้ามี) หรือเว้นว่าง string เปล่า",
    "booking_no": "หมายเลข Booking (ถ้ามี) หรือเว้นว่าง string เปล่า",
    "route": "เส้นทาง (ต้นทาง -> ปลายทาง)",
    "driver_name": "ชื่อ พขร. ที่ได้รับมอบหมาย เช่น @Jack, @สมยศ",
    "license_plate": "ทะเบียนรถ (ถ้ามี) เช่น 74-9822 หรือเว้นว่าง string เปล่า",
    "status_note": "หมายเหตุหรือรายละเอียดเพิ่มเติม (ถ้ามี)"
  }
]

ห้ามตอบเป็น markdown ข้อความอื่น ให้ตอบเฉพาะ JSON Array เท่านั้น

ข้อความแจ้งงาน:
""" + "\n---\n".join(messages_text_list)

    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }
    
    models_to_try = ["gemini-2.5-flash", "gemini-3.7-flash", "gemini-flash-latest"]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=60)
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
                    return [sanitize_record(item, default_date) for item in data]
        except Exception as e:
            print(f"[GEMINI PARSING NOTICE with {model_name}]: {e}")
            
    return []

def build_dispatch_flex_card(records: list, start_th: str, end_th: str):
    """Builds a beautiful, modern LINE Flex Message (Card / Carousel)."""
    if not records:
        return None

    # Sort records by delivery_date, customer, slot_time
    def sort_key(r):
        return (r.get("delivery_date", ""), r.get("customer", ""), r.get("slot_time", ""))
    
    sorted_records = sorted(records, key=sort_key)

    # Group by delivery_date
    date_groups = {}
    for r in sorted_records:
        d = r.get("delivery_date") or start_th
        if d not in date_groups:
            date_groups[d] = []
        date_groups[d].append(r)

    bubbles = []
    
    for date_key, group_items in date_groups.items():
        # Chunk items by 5 per bubble to keep UI clean and compact
        chunk_size = 5
        chunks = [group_items[i:i + chunk_size] for i in range(0, len(group_items), chunk_size)]
        
        for c_idx, chunk in enumerate(chunks):
            page_str = f" ({c_idx+1}/{len(chunks)})" if len(chunks) > 1 else ""
            
            job_boxes = []
            for j_idx, item in enumerate(chunk):
                # Header row: Customer + Slot
                top_row = {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"{item['customer']}",
                            "weight": "bold",
                            "size": "sm",
                            "color": "#0F172A",
                            "flex": 3,
                            "wrap": True
                        },
                        {
                            "type": "text",
                            "text": f"⏰ {item['slot_time']}",
                            "size": "xs",
                            "color": "#0284C7",
                            "align": "end",
                            "weight": "bold",
                            "flex": 2
                        }
                    ]
                }
                
                # Container / Booking / Job Type
                con_bkg_parts = []
                if item["container_no"]:
                    con_bkg_parts.append(f"ตู้: {item['container_no']}")
                if item["booking_no"]:
                    con_bkg_parts.append(f"BKG: {item['booking_no']}")
                if item["job_type"] and not item["container_no"]:
                    con_bkg_parts.append(item["job_type"])
                
                info_lines = [top_row]
                
                if con_bkg_parts:
                    info_lines.append({
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"📦 {' | '.join(con_bkg_parts)}",
                                "size": "xs",
                                "color": "#334155",
                                "wrap": True
                            }
                        ]
                    })
                
                # Route
                info_lines.append({
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"📍 {item['route']}",
                            "size": "xs",
                            "color": "#475569",
                            "wrap": True
                        }
                    ]
                })
                
                # Driver + Plate
                driver_contents = [
                    {
                        "type": "text",
                        "text": f"👤 {item['driver_name']}",
                        "size": "xs",
                        "color": "#1E293B",
                        "weight": "bold",
                        "flex": 3,
                        "wrap": True
                    }
                ]
                if item["license_plate"]:
                    driver_contents.append({
                        "type": "text",
                        "text": f"🚛 {item['license_plate']}",
                        "size": "xs",
                        "color": "#64748B",
                        "align": "end",
                        "flex": 2
                    })
                
                info_lines.append({
                    "type": "box",
                    "layout": "horizontal",
                    "contents": driver_contents
                })
                
                # Note
                if item["status_note"]:
                    info_lines.append({
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"📝 {item['status_note']}",
                                "size": "xxs",
                                "color": "#64748B",
                                "wrap": True
                            }
                        ]
                    })
                
                job_card = {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F8FAFC",
                    "cornerRadius": "8px",
                    "paddingAll": "10px",
                    "spacing": "xs",
                    "contents": info_lines
                }
                job_boxes.append(job_card)

            bubble = {
                "type": "bubble",
                "size": "mega",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#0F172A",
                    "paddingAll": "14px",
                    "contents": [
                        {
                            "type": "text",
                            "text": "🚚 สรุปการเดินรถและจ่ายงาน",
                            "weight": "bold",
                            "size": "md",
                            "color": "#38BDF8"
                        },
                        {
                            "type": "text",
                            "text": f"📅 วันที่: {date_key}{page_str} • รวม {len(group_items)} รายการ",
                            "size": "xs",
                            "color": "#94A3B8",
                            "margin": "xs"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "paddingAll": "12px",
                    "contents": job_boxes
                }
            }
            bubbles.append(bubble)

    if not bubbles:
        return None
        
    bubbles = bubbles[:10]  # Max 10 bubbles in carousel
    
    if len(bubbles) == 1:
        return bubbles[0]
    else:
        return {
            "type": "carousel",
            "contents": bubbles
        }

def format_records_to_csv(records: list) -> str:
    """Generates standard RFC-compliant CSV string."""
    if not records:
        return ""
        
    def sort_key(r):
        return (r.get("delivery_date", ""), r.get("customer", ""), r.get("slot_time", ""))
    
    sorted_records = sorted(records, key=sort_key)

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

    return csv_output.getvalue().strip()

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
        send_reply_text(reply_token, f"ℹ️ ไม่พบบันทึกข้อความในช่วงวันที่ {start_th} ถึง {end_th} ในฐานข้อมูลครับ", quote_token=quote_token)
        return

    relevant_texts = []
    keywords = ["แจ้งงาน", "bkg", "booking", "job", "ตู้", "slot", "consol", "รับตู้", "ตัดหาง", "คืนตู้", "brose", "corbion", "purac", "exotic", "powertech", "uacj", "dts"]
    
    for d in docs:
        c = d.get("content", "")
        if any(k in c.lower() for k in keywords):
            relevant_texts.append(c)

    if not relevant_texts:
        relevant_texts = [d.get("content", "") for d in docs if d.get("content")][:50]

    records = extract_dispatch_records_with_gemini(relevant_texts, default_date=start_th)
    
    if not records:
        send_reply_text(reply_token, f"ℹ️ ไม่พบรายการแจ้งงานในช่วงวันที่ {start_th} ถึง {end_th} ครับ", quote_token=quote_token)
        return

    # 1. Build Flex Card Message
    flex_dict = build_dispatch_flex_card(records, start_th, end_th)
    
    # 2. Build CSV Text Message
    csv_content = format_records_to_csv(records)
    csv_block = f"📄 **[CSV DATA สำหรับนำเข้า Excel]**\n```csv\n{csv_content[:3500]}\n```"

    # Send to LINE
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        
        reply_messages = []
        
        # Add Flex Message if valid
        if flex_dict:
            try:
                container = FlexContainer.from_dict(flex_dict)
                flex_msg = FlexMessage(alt_text=f"🚚 สรุปการเดินรถ ({start_th} ถึง {end_th})", contents=container)
                reply_messages.append(flex_msg)
            except Exception as e:
                print(f"[FLEX CONTAINER ERROR]: {e}")
                
        # Add CSV text block as secondary bubble
        if csv_content:
            reply_messages.append(TextMessage(text=csv_block))
            
        if not reply_messages:
            reply_messages.append(TextMessage(text=f"ℹ️ ประมวลผลเสร็จสิ้น พบ {len(records)} รายการ"))

        try:
            api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=reply_messages
            ))
        except Exception as err:
            print(f"[REPLY ERROR]: {err}. Trying text fallback.")
            # Fallback to plain text
            fallback_text = f"🚚 สรุปการเดินรถ ({start_th} ถึง {end_th}) รวม {len(records)} รายการ\n\n" + csv_block
            api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=fallback_text[:4500])]
            ))

def send_reply_text(reply_token: str, text: str, quote_token: str = None):
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        try:
            api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            ))
        except Exception as e:
            print(f"[SEND TEXT ERROR]: {e}")

