import os
from datetime import datetime
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGODB_DB_NAME", "linebot_db")

_client = None

def get_db():
    global _client
    if not MONGODB_URI:
        raise ValueError("MONGODB_URI is not set in environment variables.")
    if _client is None:
        _client = MongoClient(MONGODB_URI)
    
    try:
        db = _client.get_default_database()
        if db is not None:
            return db
    except Exception:
        pass
    
    return _client[DB_NAME]

def init_db():
    db = get_db()
    # Create unique index on message_id for messages collection
    db.messages.create_index([("message_id", ASCENDING)], unique=True)
    db.messages.create_index([("group_id", ASCENDING)])
    db.messages.create_index([("timestamp", ASCENDING)])
    print("MongoDB Atlas indexes created successfully.")

def save_message_record(source_type: str, group_id: str, user_id: str, display_name: str, message_id: str, quoted_message_id: str, message_type: str, content: str, raw_event: dict = None):
    db = get_db()
    doc = {
        "timestamp": datetime.utcnow().isoformat(),
        "source_type": source_type,
        "group_id": group_id,
        "user_id": user_id,
        "display_name": display_name,
        "message_id": message_id,
        "quoted_message_id": quoted_message_id,
        "message_type": message_type,
        "content": content,
        "raw_event": raw_event
    }
    try:
        db.messages.insert_one(doc)
        print(f"[DB SUCCESS] Recorded {message_type} message (ID: {message_id}) into MongoDB Atlas.")
    except DuplicateKeyError:
        print(f"[DB NOTICE] Message ID {message_id} already exists in MongoDB Atlas.")
    except Exception as e:
        print(f"[DB ERROR] Failed to save message {message_id}: {e}")

def get_message_by_id(message_id: str):
    db = get_db()
    return db.messages.find_one({"message_id": message_id})

if __name__ == "__main__":
    init_db()
