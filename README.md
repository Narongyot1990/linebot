# LINE Webhook Receiver Server - Phase 1 (Vercel + FastAPI)

โปรเจกต์ Phase 1: เซิร์ฟเวอร์สำหรับรับ Webhook จาก LINE Messaging API และประมวลผลบน Vercel (ยังไม่เชื่อมต่อ Database)

---

## 🛠️ โครงสร้างไฟล์โปรเจกต์ (Phase 1)

```
linebot/
├── api/
│   └── index.py         # FastAPI Serverless Function สำหรับรับ Webhook
├── requirements.txt      # Dependencies (fastapi, line-bot-sdk, python-dotenv)
├── vercel.json           # คอนฟิก Serverless Routing สำหรับ Vercel
├── .env.example          # ตัวอย่างไฟล์ตั้งค่า Environment Variables
└── README.md             # คู่มือการใช้งาน Phase 1
```

---

## 🚀 ขั้นตอนการติดตั้งและการนำขึ้น Vercel

### 1. ตั้งค่า LINE Developers Console
1. ไปที่ [LINE Developers Console](https://developers.line.biz/)
2. สร้าง **Messaging API Channel**
3. คัดลอกค่า:
   - **Channel Secret** (จากแท็บ Basic settings)
   - **Channel Access Token** (จากแท็บ Messaging API -> กด Issue)
4. เปิดสวิตช์ **Allow bot to join group chats** เป็น `Enabled`

### 2. นำขึ้น Vercel (Deploy)
1. Push โปรเจกต์นี้ขึ้น **GitHub Repository**
2. ไปที่ [Vercel Dashboard](https://vercel.com/) -> กด **Add New Project** -> เลือก Repo นี้
3. ในหน้าตั้งค่าโปรเจกต์ ให้ใส่ **Environment Variables**:
   - `LINE_CHANNEL_SECRET`: (ค่า Channel Secret)
   - `LINE_CHANNEL_ACCESS_TOKEN`: (ค่า Channel Access Token)
4. กด **Deploy**

### 3. ตั้งค่า Webhook URL ใน LINE
1. คัดลอก Domain URL ที่ได้จาก Vercel เช่น `https://your-app.vercel.app`
2. กลับไปที่ LINE Developers Console -> แท็บ Messaging API
3. ใส่ **Webhook URL**: `https://your-app.vercel.app/api/callback`
4. กด **Verify** (ต้องขึ้นสถานะ Success)
5. เปิดสวิตช์ **Use webhook** เป็น `Enabled`

---

## 🔍 การทดสอบระบบ (Logs Inspection)
- เมื่อส่งข้อความหรือรูปภาพในกลุ่ม LINE คุณสามารถเข้าไปที่ **Vercel Dashboard** -> แท็บ **Logs** เพื่อดูข้อความและ Event ที่วิ่งเข้ามาแบบ Real-time ได้ทันที!
