import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, func
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = os.environ.get("DATA_DIR", ".")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = "."

if not os.access(DATA_DIR, os.W_OK):
    DATA_DIR = "."

DB_PATH = os.path.join(DATA_DIR, "telegram_yasak.db")

DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True, nullable=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=True)
    session_file = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class WordFilter(Base):
    __tablename__ = "word_filters"
    
    id = Column(Integer, primary_key=True, index=True)
    word = Column(String, index=True, nullable=False)
    match_type = Column(String, default="contains")  # 'contains', 'exact', 'regex'
    chat_id = Column(String, default="all", nullable=True)  # 'all' veya '-100...'
    chat_title = Column(String, default="Tüm Gruplar", nullable=True)
    send_dm = Column(Boolean, default=False)
    custom_dm_message = Column(Text, nullable=True)
    action = Column(String, default="delete")  # 'delete', 'delete_and_mute', 'delete_and_ban'
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserFilter(Base):
    __tablename__ = "user_filters"
    
    id = Column(Integer, primary_key=True, index=True)
    identifier = Column(String, index=True, nullable=False)  # User ID, @username, or phone
    chat_id = Column(String, default="all", nullable=True)  # 'all' veya '-100...'
    chat_title = Column(String, default="Tüm Gruplar", nullable=True)
    note = Column(String, nullable=True)
    send_dm = Column(Boolean, default=False)
    custom_dm_message = Column(Text, nullable=True)
    action = Column(String, default="delete")  # 'delete', 'delete_and_ban'
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class MonitoredChat(Base):
    __tablename__ = "monitored_chats"
    
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, unique=True, index=True, nullable=False)
    chat_title = Column(String, nullable=True)
    chat_username = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DeletionLog(Base):
    __tablename__ = "deletion_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, nullable=True)
    chat_title = Column(String, nullable=True)
    sender_id = Column(String, nullable=True)
    sender_name = Column(String, nullable=True)
    sender_username = Column(String, nullable=True)
    message_id = Column(Integer, nullable=True)
    message_text = Column(Text, nullable=True)
    trigger_type = Column(String, nullable=True)  # 'word' or 'user'
    trigger_value = Column(String, nullable=True)
    dm_sent = Column(Boolean, default=False)
    dm_status = Column(String, default="skipped")
    created_at = Column(DateTime, default=datetime.utcnow)

class BotSetting(Base):
    __tablename__ = "bot_settings"
    
    key = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=True)

def init_db():
    Base.metadata.create_all(bind=engine)
    
    # Varsayılan ayarları yükle
    db = SessionLocal()
    try:
        defaults = {
            "default_dm_template": "⚠️ Sayın {user_name},\n\n{time} tarihinde **{chat_title}** grubuna gönderdiğiniz mesaj yasaklı kural ihlali nedeniyle ({reason}) otomatik olarak silinmiştir.\n\nDetaylı bilgi için grup/kanal yöneticisi ile iletişime geçiniz.",
            "monitor_all_chats": "true",
            "selected_chat_ids": "",
            "ignore_admins": "true",
            "auto_delete_service_messages": "false"
        }
        for k, v in defaults.items():
            setting = db.query(BotSetting).filter(BotSetting.key == k).first()
            if not setting:
                db.add(BotSetting(key=k, value=v))
        db.commit()
    finally:
        db.close()

def get_setting(db, key: str, default: str = "") -> str:
    setting = db.query(BotSetting).filter(BotSetting.key == key).first()
    return setting.value if setting else default

def set_setting(db, key: str, value: str):
    setting = db.query(BotSetting).filter(BotSetting.key == key).first()
    if setting:
        setting.value = value
    else:
        setting = BotSetting(key=key, value=value)
        db.add(setting)
    db.commit()
