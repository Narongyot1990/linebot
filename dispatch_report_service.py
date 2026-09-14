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
    PushMessageRequest,
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

# ============================================================
# FAST REGEX-BASED DISPATCH PARSER (replaces slow Gemini call)
# ============================================================

KNOWN_CUSTOMERS = {
    'brose': 'Brose', 'dts': 'DTS', 'exotic': 'Exotic Food',
    'uacj': 'UACJ', 'powertech': 'Powertech', 'siam': 'Siam Service-UACJ',
    'corbion': 'Total Corbion', 'purac': 'Purac', 'homerich': 'Homerich (HRF)',
    'cra': 'CRA', 'fscc': 'FSCC', 'zf': 'ZF', 'benz': 'Benz',
    'tect': 'TECT', 'sonic': 'Sonic', 'consol': 'Consol',
}

def _extract_delivery_date(text: str) -> str:
    """Extract delivery date from message header (e.g., 'แจ้งงานพรุ่งนี้ 14/9/2026')."""
    now_utc = datetime.now(timezone.utc)
    now_th = now_utc + timedelta(hours=7)
    today_th = now_th.date()

    # Direct date pattern: DD/MM/YYYY or DD/M/YYYY
    m = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if y > 2500:
            y -= 543
        try:
            return f"{d:02d}/{mo:02d}/{y}"
        except Exception:
            pass

    # Relative date keywords
    first_line = text.split('\n')[0].lower()
    if 'วันนี้' in first_line:
        return today_th.strftime("%d/%m/%Y")
    if 'พรุ่งนี้' in first_line:
        return (today_th + timedelta(days=1)).strftime("%d/%m/%Y")
    if 'มะรืน' in first_line:
        return (today_th + timedelta(days=2)).strftime("%d/%m/%Y")

    return ""


def _extract_customer(text: str) -> str:
    """Extract customer name from message context using known customer list."""
    lines = text.strip().split('\n')

    # Check the first few non-header lines for customer name
    for line in lines[1:5]:
        line_s = line.strip()
        if not line_s or line_s.startswith('@') or 'แจ้งงาน' in line_s:
            continue
        for key, name in KNOWN_CUSTOMERS.items():
            if key in line_s.lower():
                return line_s  # Return the full line as customer (more context)
        # If the line looks like a customer name (short, no @ or ทะเบียน)
        if len(line_s) < 50 and '@' not in line_s and 'ทะเบียน' not in line_s and 'ต้นทาง' not in line_s:
            return line_s

    # Fallback: search entire text for known customers
    for key, name in KNOWN_CUSTOMERS.items():
        if key in text.lower():
            return name

    return ""


def _extract_driver_plate(line: str):
    """Extract driver name and license plate from a line like '@Jack ทะเบียน 74-9822'."""
    driver_name = ""
    plate = ""

    if 'ทะเบียน' in line and '@' in line:
        before, after = line.split('ทะเบียน', 1)
        # Find all @mentions in the before part
        mentions = re.findall(r'@([^@]+)', before)
        if mentions:
            driver_name = mentions[-1].strip()
        # Extract plate number
        plate_m = re.search(r'(\d{2,3}-\d{3,4})', after)
        if plate_m:
            plate = plate_m.group(1)
    elif '@' in line:
        # No ทะเบียน, just @mention
        mentions = re.findall(r'@([^@\s]+(?:\s+[^@\s]+)*)', line)
        if mentions:
            driver_name = mentions[-1].strip()

    # Clean up driver name
    driver_name = driver_name.strip().rstrip('.')
    return driver_name, plate


def _extract_route(text: str) -> str:
    """Extract route from ต้นทาง/ปลายทาง or สถานที่รับ/ส่ง patterns."""
    origin = ""
    dest = ""

    m_origin = re.search(r'ต้นทาง\s*:\s*(.+)', text)
    if m_origin:
        origin = m_origin.group(1).strip()

    m_dest = re.search(r'ปลายทาง\s*:\s*(.+)', text)
    if m_dest:
        dest = m_dest.group(1).strip()

    if not origin:
        m_recv = re.search(r'สถานที่รับ(?:สินค้า)?\s*[:\s]\s*(.+)', text)
        if m_recv:
            origin = m_recv.group(1).strip()
    if not dest:
        m_send = re.search(r'สถานที่ส่ง(?:สินค้า)?\s*[:\s]\s*(.+)', text)
        if m_send:
            dest = m_send.group(1).strip()

    if origin and dest:
        return f"{origin} → {dest}"
    if origin:
        return origin
    if dest:
        return dest
    return "-"


def _extract_slot_time(text: str) -> str:
    """Extract slot/plan time from text (e.g., 'เข้าหน้างาน 08.30 น.')."""
    m = re.search(r'(\d{1,2}[.:]\d{2})\s*น\.', text)
    if m:
        return m.group(1).replace('.', ':') + " น."
    m2 = re.search(r'เวลา\s*(\d{1,2}[.:]\d{2})', text)
    if m2:
        return m2.group(1).replace('.', ':') + " น."
    return "ตามคิว/รอแจ้ง"


def _extract_shift(lines: list, driver_line_idx: int) -> str:
    """Look backwards from driver line to find shift marker (กะเช้า/กะดึก)."""
    for i in range(driver_line_idx - 1, max(driver_line_idx - 5, -1), -1):
        line = lines[i].strip().lower()
        if 'กะเช้า' in line:
            return "กะเช้า"
        if 'กะดึก' in line:
            return "กะดึก"
        if 'กะบ่าย' in line:
            return "กะบ่าย"
    return ""


def _extract_job_type(text: str) -> str:
    """Extract job type (e.g., 'ขนแร็ค', 'WIP Single Trip')."""
    m = re.search(r'วิ่งงาน\s*(.+?)(?:\n|$)', text)
    if m:
        return m.group(1).strip()
    m2 = re.search(r'งานขน(.+?)(?:\n|$)', text)
    if m2:
        return "ขน" + m2.group(1).strip()
    for kw in ['ตู้หนัก', 'ตู้เปล่า', 'ชัทเทิล', 'ขนแร็ค', 'ทอยตู้', 'ตัดหาง', 'คืนตู้', 'รับตู้']:
        if kw in text:
            return kw
    return ""


def _extract_inline_note(line: str) -> str:
    """Extract inline note from driver line (text after plate number)."""
    if 'ทะเบียน' not in line:
        return ""
    _, after = line.split('ทะเบียน', 1)
    after = re.sub(r'\s*\d{2,3}-\d{3,4}', '', after, count=1).strip()
    after = re.sub(r'จำนวน\s*\d+\s*เที่ยว', '', after).strip()
    paren = re.search(r'\((.+?)\)', after)
    paren_note = paren.group(1) if paren else ""
    after = re.sub(r'\(.+?\)', '', after).strip()
    combined = (after + (" " + paren_note if paren_note else "")).strip()
    return combined if len(combined) > 2 else ""


def _extract_trips(line: str) -> str:
    """Extract number of trips (จำนวน X เที่ยว)."""
    m = re.search(r'จำนวน\s*(\d+)\s*เที่ยว', line)
    return m.group(1) if m else ""


def _parse_single_dispatch_message(text: str, default_date: str = "") -> list:
    """Parse a single dispatch message from Bowie into structured records."""
    text = text.strip()
    if not text:
        return []

    lines = text.split('\n')

    # 1. Extract delivery date
    delivery_date = _extract_delivery_date(text) or default_date

    # 2. Extract customer from context
    customer = _extract_customer(text)

    # 3. Find all driver assignment lines (lines with @ + ทะเบียน)
    driver_line_indices = []
    for i, line in enumerate(lines):
        if 'ทะเบียน' in line and '@' in line:
            driver_line_indices.append(i)

    if not driver_line_indices:
        return []

    # 4. Check for multi-job pattern (งาน 1, งาน 2, ... under a single driver)
    full_text_after_drivers = '\n'.join(lines[driver_line_indices[-1]:])
    job_markers = list(re.finditer(r'งาน\s*(\d+)\s+(.+?)(?:\n|$)', full_text_after_drivers))

    if len(job_markers) >= 2 and len(driver_line_indices) == 1:
        # Multi-job pattern: one driver, multiple sub-jobs
        driver_name, plate = _extract_driver_plate(lines[driver_line_indices[0]])
        records = []
        for j, jm in enumerate(job_markers):
            start_pos = jm.start()
            end_pos = job_markers[j + 1].start() if j + 1 < len(job_markers) else len(full_text_after_drivers)
            sub_text = full_text_after_drivers[start_pos:end_pos]
            sub_customer = jm.group(2).strip()
            route = _extract_route(sub_text)
            slot = _extract_slot_time(sub_text)
            job_type = _extract_job_type(sub_text)
            records.append(sanitize_record({
                "delivery_date": delivery_date,
                "customer": sub_customer or customer,
                "driver_name": driver_name,
                "license_plate": plate,
                "route": route,
                "slot_time": slot,
                "job_type": job_type,
                "container_no": "", "booking_no": "", "status_note": ""
            }))
        return records

    # 5. Standard pattern: one record per driver line
    shared_route = _extract_route(text)
    shared_slot = _extract_slot_time(text)

    records = []
    for idx, dl_idx in enumerate(driver_line_indices):
        line = lines[dl_idx]
        driver_name, plate = _extract_driver_plate(line)

        if not driver_name:
            continue

        # Determine this driver's section (from this line to the next driver line or end)
        next_dl = driver_line_indices[idx + 1] if idx + 1 < len(driver_line_indices) else len(lines)
        section_text = '\n'.join(lines[dl_idx:next_dl])

        # Extract route and slot from this section
        route = _extract_route(section_text)
        slot = _extract_slot_time(section_text)
        shift = _extract_shift(lines, dl_idx)

        # If no specific route/slot found, use shared context
        if route == "-":
            route = shared_route
        if slot == "ตามคิว/รอแจ้ง" and shift:
            slot = shift
        elif slot == "ตามคิว/รอแจ้ง":
            slot = shared_slot

        # Extract inline note and trips
        note = _extract_inline_note(line)
        trips = _extract_trips(line)
        if trips:
            note_parts = [f"จำนวน {trips} เที่ยว"]
            if note:
                note_parts.append(note)
            if shift:
                note_parts.append(shift)
            note = " | ".join(note_parts)
        elif shift and not note:
            note = shift

        job_type = _extract_job_type(section_text)

        records.append(sanitize_record({
            "delivery_date": delivery_date,
            "customer": customer,
            "driver_name": driver_name,
            "license_plate": plate,
            "route": route,
            "slot_time": slot,
            "job_type": job_type,
            "container_no": "", "booking_no": "",
            "status_note": note
        }))

    return records


def extract_dispatch_records_regex(bowie_messages: list, default_date: str = "") -> list:
    """
    FAST regex-based parser for Bowie's dispatch messages.
    Replaces Gemini AI call (~5-25s) with pure regex parsing (<100ms).
    Only processes messages that look like dispatch assignments.
    """
    all_records = []
    for msg_text in bowie_messages:
        msg_lower = msg_text.lower()
        has_dispatch_marker = any(kw in msg_lower for kw in [
            'แจ้งงาน', 'ทะเบียน', 'ต้นทาง', 'ปลายทาง', 'เข้าหน้างาน'
        ])
        if has_dispatch_marker and '@' in msg_text:
            records = _parse_single_dispatch_message(msg_text, default_date)
            all_records.extend(records)
    return all_records


def extract_dispatch_records_with_gemini(messages_text_list: list, default_date: str = "") -> list:
    """Uses Gemini AI with strict JSON schema to parse raw LINE messages."""
    if not GEMINI_API_KEY:
        return []
        
    prompt = """คุณคือระบบ AI วิเคราะห์และสกัดข้อมูลการจ่ายงานขนส่ง (Transport Dispatching System)
วิเคราะห์ข้อความแจ้งงานจากผู้จ่ายงาน (Bowie / ผู้ควบคุมรถ) ต่อไปนี้

หน้าที่สำคัญที่สุด:
1. สกัด "วันที่ส่งงาน/วิ่งงานจริง (delivery_date)" จากในเนื้อหาข้อความ (เช่น ในข้อความระบุว่า "งานวันที่ 14", "งานวันจันทร์ที่ 14/09", "วิ่งงาน 14/09" ให้แปลงเป็นวันที่ DD/MM/YYYY ให้ถูกต้อง เช่น 14/09/2026 แม้ข้อความจะถูกส่งในประวัติวันที่ 12 หรือ 13 ก็ตาม)
2. สกัดข้อมูลงานแต่ละรายการออกมาเป็น JSON Array ตาม schema นี้เท่านั้น:

[
  {
    "delivery_date": "DD/MM/YYYY (วันที่ต้องวิ่งงานจริงที่ระบุในข้อความ เช่น 14/09/2026 หรือถ้าไม่ระบุให้ใส่ 'ตามคิว/รอแจ้ง')",
    "slot_time": "เวลาเข้าโหลด/Plan Time เช่น 04:30 น., 10:00 น., 13:00 น. หรือถ้าไม่ระบุให้ใส่ 'ตามคิว/รอแจ้ง'",
    "customer": "ชื่อลูกค้า เช่น Brose, DTS, Total Corbion, Purac, CRA / FSCC, Exotic Food, Homerich (HRF), Powertech, UACJ",
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
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    models_to_try = ["gemini-flash-latest", "gemini-3.7-flash"]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=25)
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
    """Builds a beautiful, modern LINE Flex Message (Card / Carousel) tailored for Operations Monitoring."""
    if not records:
        return None

    # Sort records by delivery_date, customer, slot_time
    def sort_key(r):
        return (r.get("delivery_date", ""), r.get("customer", ""), r.get("slot_time", ""))
    
    sorted_records = sorted(records, key=sort_key)

    # Group by delivery_date (วันที่ส่งงานจริง)
    date_groups = {}
    for r in sorted_records:
        d = r.get("delivery_date") or "ตามคิว/รอแจ้ง"
        if d not in date_groups:
            date_groups[d] = []
        date_groups[d].append(r)

    bubbles = []
    
    for date_key, group_items in date_groups.items():
        # Calculate summary metrics for operations monitoring
        assigned_drivers = [item for item in group_items if item.get("driver_name") and item.get("driver_name") not in ["-", "ยังไม่ระบุ พขร.", "Unknown"]]
        assigned_count = len(assigned_drivers)
        total_count = len(group_items)

        # Chunk items by 5 per bubble to keep UI clean and compact
        chunk_size = 5
        chunks = [group_items[i:i + chunk_size] for i in range(0, len(group_items), chunk_size)]
        
        for c_idx, chunk in enumerate(chunks):
            page_str = f" ({c_idx+1}/{len(chunks)})" if len(chunks) > 1 else ""
            
            job_boxes = []
            for j_idx, item in enumerate(chunk):
                # Header row: Customer + Slot
                slot_display = item['slot_time'] if item['slot_time'] else "ตามคิว/รอแจ้ง"
                slot_color = "#0284C7" if ("น." in slot_display or ":" in slot_display) else "#64748B"
                
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
                            "text": f"⏰ {slot_display}",
                            "size": "xs",
                            "color": slot_color,
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
                
                # Driver + Plate (with assignment visual check)
                is_driver_assigned = bool(item['driver_name'] and item['driver_name'] not in ["-", "ยังไม่ระบุ พขร."])
                driver_text = f"👤 {item['driver_name']}" if is_driver_assigned else "👤 ⚠️ ยังไม่ระบุ พขร."
                driver_color = "#1E293B" if is_driver_assigned else "#D97706"
                
                driver_contents = [
                    {
                        "type": "text",
                        "text": driver_text,
                        "size": "xs",
                        "color": driver_color,
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
                            "text": f"📅 วันที่ส่งงาน: {date_key}{page_str} • รวม {total_count} งาน",
                            "size": "xs",
                            "color": "#94A3B8",
                            "margin": "xs"
                        },
                        {
                            "type": "text",
                            "text": f"👤 มอบหมาย พขร. แล้ว: {assigned_count}/{total_count} คัน",
                            "size": "xxs",
                            "color": "#38BDF8" if assigned_count == total_count else "#FBBF24",
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
        
    bubbles = bubbles[:5]  # Max 5 bubbles in carousel to stay well below 25KB LINE payload limit
    
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
    """Main trigger handler for /report and /dispatch_report commands in LINE.
    Uses FAST regex parser by default. Gemini AI only via '/report ai ...'
    """
    import time
    t0 = time.time()
    
    text = getattr(event.message, "text", "").strip()
    reply_token = event.reply_token
    quote_token = getattr(event.message, "quote_token", None)
    use_ai = "ai" in text.lower().split()  # /report ai today → use Gemini

    start_utc_iso, end_utc_iso, start_th, end_th = parse_date_range(text)
    
    db = database.get_db()
    
    query = {
        "timestamp": {"$gte": start_utc_iso, "$lte": end_utc_iso},
        "message_type": {"$in": ["text", "file"]}
    }
    
    docs = list(db.messages.find(query).sort("timestamp", 1))
    t_db = time.time()
    print(f"[REPORT] DB query: {len(docs)} docs in {t_db - t0:.2f}s")
    
    if not docs:
        send_reply_text(reply_token, f"ℹ️ ไม่พบบันทึกข้อความในช่วงวันที่ {start_th} ถึง {end_th} ในฐานข้อมูลครับ", quote_token=quote_token)
        return

    # Filter Bowie's dispatch messages
    bowie_user_ids = ["U4e8e9135b6578be52a8354f02cb9f127"]
    dispatch_keywords = ["แจ้งงาน", "ทะเบียน", "ต้นทาง", "ปลายทาง", "เข้าหน้างาน"]
    
    bowie_dispatch_texts = []
    seen = set()
    for d in docs:
        uid = d.get("user_id")
        dname = str(d.get("display_name", "")).lower()
        c = (d.get("content") or "").strip()
        if not c or len(c) < 10:
            continue
        is_bowie = (uid in bowie_user_ids) or ("bowie" in dname) or ("โบวี่" in dname)
        if not is_bowie:
            continue
        has_dispatch = any(kw in c.lower() for kw in dispatch_keywords)
        if has_dispatch and c not in seen:
            seen.add(c)
            bowie_dispatch_texts.append(c)  # Full text for regex (no truncation needed)

    if not bowie_dispatch_texts:
        send_reply_text(reply_token, f"ℹ️ ไม่พบข้อความแจ้งงานจาก Bowie ในช่วง {start_th} ถึง {end_th} ครับ", quote_token=quote_token)
        return

    print(f"[REPORT] Found {len(bowie_dispatch_texts)} dispatch messages from Bowie")

    # Parse dispatch records
    if use_ai:
        # Explicit AI mode: /report ai today
        print("[REPORT] Using Gemini AI parser (user requested)")
        selected = [t[:280] for t in bowie_dispatch_texts[-15:]]
        records = extract_dispatch_records_with_gemini(selected, default_date=start_th)
    else:
        # DEFAULT: Fast regex parser
        records = extract_dispatch_records_regex(bowie_dispatch_texts, default_date=start_th)
    
    t_parse = time.time()
    print(f"[REPORT] Parsed {len(records)} records in {t_parse - t_db:.3f}s ({'AI' if use_ai else 'REGEX'})")
    
    if not records:
        send_reply_text(reply_token, f"ℹ️ ไม่พบรายการแจ้งงานในช่วง {start_th} ถึง {end_th} ครับ\n(พบ {len(bowie_dispatch_texts)} ข้อความ แต่ไม่สามารถสกัดข้อมูลได้)", quote_token=quote_token)
        return

    # Build Flex Card Message
    flex_dict = build_dispatch_flex_card(records, start_th, end_th)
    
    # Check if user explicitly asked for CSV format
    is_csv_requested = "csv" in text.lower()
    csv_content = format_records_to_csv(records) if is_csv_requested else ""
    csv_block = f"📄 **[CSV DATA สำหรับนำเข้า Excel]**\n```csv\n{csv_content[:3500]}\n```" if csv_content else ""

    # Send to LINE
    configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        
        reply_messages = []
        
        if is_csv_requested and csv_block:
            reply_messages.append(TextMessage(text=csv_block))
        elif flex_dict:
            try:
                container = FlexContainer.from_dict(flex_dict)
                flex_msg = FlexMessage(alt_text=f"🚚 สรุปการเดินรถ ({start_th} ถึง {end_th})", contents=container)
                reply_messages.append(flex_msg)
            except Exception as e:
                print(f"[FLEX CONTAINER ERROR]: {e}")
                fallback_summary = f"🚚 สรุปการเดินรถ ({start_th} ถึง {end_th}) รวม {len(records)} รายการ"
                reply_messages.append(TextMessage(text=fallback_summary))
            
        if not reply_messages:
            reply_messages.append(TextMessage(text=f"ℹ️ ประมวลผลเสร็จสิ้น พบ {len(records)} รายการ"))

        source_id = getattr(getattr(event, "source", None), "group_id", None) or getattr(getattr(event, "source", None), "user_id", None)
        try:
            api.reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=reply_messages
            ))
            t_reply = time.time()
            print(f"[REPORT] ✅ Reply sent in {t_reply - t0:.2f}s total")
        except Exception as err:
            print(f"[REPLY ERROR]: {err}. Trying push message fallback to {source_id}.")
            if source_id:
                try:
                    api.push_message(PushMessageRequest(
                        to=source_id,
                        messages=reply_messages
                    ))
                    print(f"[PUSH SUCCESSFUL to {source_id}]")
                except Exception as push_err:
                    print(f"[PUSH ERROR]: {push_err}")

def send_reply_text(reply_token: str, text: str, quote_token: str = None, source_id: str = None):
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
            if source_id:
                try:
                    api.push_message(PushMessageRequest(
                        to=source_id,
                        messages=[TextMessage(text=text)]
                    ))
                except Exception as pe:
                    print(f"[PUSH TEXT ERROR]: {pe}")

