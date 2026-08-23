# LINE Webhook Receiver Server - Phase 1 (Vercel + FastAPI)

โปรเจกต์ Phase 1: เซิร์ฟเวอร์สำหรับรับ Webhook จาก LINE Messaging API และประมวลผลบน Vercel (พิมพ์ Raw JSON Payload สำหรับประมวลผลร่วมกับ AI)

---

## 🛠️ โครงสร้างไฟล์โปรเจกต์ (Phase 1)

```
linebot/
├── api/
│   └── index.py         # FastAPI Serverless Function สำหรับรับ Webhook และพิมพ์ Raw JSON
├── requirements.txt      # Dependencies (fastapi, line-bot-sdk, python-dotenv)
├── vercel.json           # คอนฟิก Serverless Routing สำหรับ Vercel
├── .env.example          # ตัวอย่างไฟล์ตั้งค่า Environment Variables
└── README.md             # คู่มือการใช้งาน Phase 1
```

---

## 🚀 ขั้นตอนการใช้งาน

1. โครงสร้างนี้รองรับการ Push ผ่าน Git ขึ้น GitHub แล้ว Vercel จะทำ **Auto Deploy** อัตโนมัติทันที
2. Webhook URL สำหรับ LINE Developers Console:
   `https://linebot-snowy-ten.vercel.app/api/callback`
