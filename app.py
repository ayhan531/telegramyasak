import os
import json
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional, List

from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import database
from database import SessionLocal, Account, WordFilter, UserFilter, DeletionLog, BotSetting, get_setting, set_setting
import bot_service
from matcher import check_word_match, check_user_match

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Veritabanını başlat
    database.init_db()
    print("[System] Veritabanı hazırlandı.")
    # Aktif hesapları arka planda bağla
    asyncio.create_task(bot_service.init_all_active_accounts())
    yield
    print("[System] Kapatılıyor, Telegram bağlantıları sonlandırılıyor...")
    for client in bot_service.active_clients.values():
        try:
            await client.disconnect()
        except Exception:
            pass

app = FastAPI(title="Telegram Otomatik Mesaj Silme Botu", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic modelleri
class WordFilterCreate(BaseModel):
    words: str  # Tek kelime veya virgül/satır ile çoklu
    match_type: str = "contains"
    chat_id: Optional[str] = "all"
    chat_title: Optional[str] = "Tüm Gruplar"
    send_dm: bool = False
    custom_dm_message: Optional[str] = None

class UserFilterCreate(BaseModel):
    identifier: str  # ID, telefon (+90...) veya @kullanici_adi
    chat_id: Optional[str] = "all"
    chat_title: Optional[str] = "Tüm Gruplar"
    note: Optional[str] = None
    send_dm: bool = False
    custom_dm_message: Optional[str] = None

class ManualChatCreate(BaseModel):
    chat_id: str
    chat_title: Optional[str] = None

class SettingsUpdate(BaseModel):
    default_dm_template: str
    monitor_all_chats: bool = True
    selected_chat_ids: str = ""
    ignore_admins: bool = True

class TestMessageRequest(BaseModel):
    message_text: str
    chat_id: Optional[str] = "-10012345678"
    chat_title: Optional[str] = "Örnek Grup"
    sender_id: Optional[str] = "12345678"
    sender_username: Optional[str] = "ornek_kullanici"
    sender_phone: Optional[str] = "+905551112233"

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

# --- HESAP YÖNETİMİ ---

@app.post("/api/accounts/qr-start")
async def start_qr():
    try:
        data = await bot_service.start_qr_login()
        return JSONResponse(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/accounts/qr-status")
async def qr_status(token: str = Query(...)):
    res = await bot_service.check_qr_status(token)
    return JSONResponse(res)

@app.post("/api/accounts/phone-start")
async def phone_start(payload: dict):
    phone = payload.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Telefon numarası gereklidir.")
    res = await bot_service.start_phone_login(phone)
    return JSONResponse(res)

@app.post("/api/accounts/phone-verify")
async def phone_verify(payload: dict):
    token = payload.get("token")
    code = payload.get("code")
    password = payload.get("password")
    if not token or not code:
        raise HTTPException(status_code=400, detail="Eksik bilgi.")
    res = await bot_service.complete_phone_login(token, code, password)
    return JSONResponse(res)

@app.get("/api/accounts")
def list_accounts(db=Depends(get_db)):
    accounts = db.query(Account).all()
    results = []
    for acc in accounts:
        is_running = acc.id in bot_service.active_clients and bot_service.active_clients[acc.id].is_connected()
        results.append({
            "id": acc.id,
            "phone": acc.phone,
            "username": acc.username,
            "first_name": acc.first_name,
            "telegram_id": acc.telegram_id,
            "is_active": acc.is_active,
            "is_running": is_running,
            "created_at": acc.created_at.strftime("%d.%m.%Y %H:%M") if acc.created_at else ""
        })
    return results

@app.post("/api/accounts/{id}/toggle")
async def toggle_account(id: int, db=Depends(get_db)):
    acc = db.query(Account).filter(Account.id == id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Hesap bulunamadı.")
    acc.is_active = not acc.is_active
    db.commit()

    if acc.is_active:
        await bot_service.start_account_client(acc)
    else:
        client = bot_service.active_clients.pop(acc.id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
    return {"status": "ok", "is_active": acc.is_active}

@app.delete("/api/accounts/{id}")
async def delete_account(id: int, db=Depends(get_db)):
    acc = db.query(Account).filter(Account.id == id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Hesap bulunamadı.")
    client = bot_service.active_clients.pop(acc.id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    if os.path.exists(acc.session_file):
        try:
            os.remove(acc.session_file)
        except Exception:
            pass
    db.delete(acc)
    db.commit()
    return {"status": "ok"}

@app.get("/api/accounts/{id}/dialogs")
async def get_dialogs(id: int):
    return await bot_service.get_account_dialogs(id)

# --- SOHBET / KANAL LİSTESİ ---

@app.get("/api/chats")
async def list_chats(db=Depends(get_db)):
    # 1. Aktif Telegram hesabından çek
    tg_dialogs = await bot_service.get_all_dialogs()
    # 2. Veritabanındaki manuel kayıtlı sohbetlerle birleştir
    db_chats = db.query(database.MonitoredChat).all()
    db_map = {c.chat_id: c for c in db_chats}

    results = []
    seen = set()

    # Telegram'dan gelen sohbetler
    for d in tg_dialogs:
        cid = str(d["id"])
        seen.add(cid)
        is_active = True
        if cid in db_map:
            is_active = db_map[cid].is_active
        results.append({
            "id": cid,
            "title": d["title"],
            "is_group": d.get("is_group", True),
            "is_channel": d.get("is_channel", False),
            "is_admin": d.get("is_admin", True),
            "is_active": is_active,
            "source": "telegram"
        })

    # Manuel eklenen sohbetler
    for c in db_chats:
        if c.chat_id not in seen:
            results.append({
                "id": c.chat_id,
                "title": c.chat_title or f"Chat {c.chat_id}",
                "is_group": True,
                "is_channel": False,
                "is_admin": True,
                "is_active": c.is_active,
                "source": "manual"
            })

    return results

@app.post("/api/chats")
def add_manual_chat(item: ManualChatCreate, db=Depends(get_db)):
    clean_id = item.chat_id.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Sohbet ID boş olamaz.")
    existing = db.query(database.MonitoredChat).filter(database.MonitoredChat.chat_id == clean_id).first()
    if existing:
        existing.chat_title = item.chat_title or existing.chat_title
        existing.is_active = True
    else:
        db.add(database.MonitoredChat(
            chat_id=clean_id,
            chat_title=item.chat_title or f"Sohbet {clean_id}",
            is_active=True
        ))
    db.commit()
    return {"status": "ok"}

@app.post("/api/chats/{chat_id}/toggle")
def toggle_chat(chat_id: str, db=Depends(get_db)):
    c = db.query(database.MonitoredChat).filter(database.MonitoredChat.chat_id == chat_id).first()
    if c:
        c.is_active = not c.is_active
    else:
        c = database.MonitoredChat(chat_id=chat_id, is_active=False)
        db.add(c)
    db.commit()
    return {"status": "ok", "is_active": c.is_active}

# --- YASAKLI KELİMELER ---

@app.get("/api/words")
def get_words(db=Depends(get_db)):
    return db.query(WordFilter).order_by(WordFilter.id.desc()).all()

@app.post("/api/words")
def add_words(item: WordFilterCreate, db=Depends(get_db)):
    raw_lines = item.words.replace("\r", "").split("\n")
    added_count = 0
    chat_id = (item.chat_id or "all").strip()
    chat_title = (item.chat_title or "Tüm Gruplar").strip()

    for line in raw_lines:
        words = [w.strip() for w in line.split(",") if w.strip()]
        for w in words:
            # Aynı kelime ve aynı sohbet kuralı zaten varsa atla
            existing = db.query(WordFilter).filter(
                WordFilter.word == w,
                WordFilter.chat_id == chat_id
            ).first()
            if not existing:
                db.add(WordFilter(
                    word=w,
                    match_type=item.match_type,
                    chat_id=chat_id,
                    chat_title=chat_title,
                    send_dm=item.send_dm,
                    custom_dm_message=item.custom_dm_message.strip() if item.custom_dm_message else None,
                    is_active=True
                ))
                added_count += 1
    db.commit()
    return {"status": "ok", "added": added_count}

@app.post("/api/words/{id}/toggle")
def toggle_word(id: int, db=Depends(get_db)):
    w = db.query(WordFilter).filter(WordFilter.id == id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Kelime bulunamadı.")
    w.is_active = not w.is_active
    db.commit()
    return {"status": "ok", "is_active": w.is_active}

@app.delete("/api/words/{id}")
def delete_word(id: int, db=Depends(get_db)):
    w = db.query(WordFilter).filter(WordFilter.id == id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Kelime bulunamadı.")
    db.delete(w)
    db.commit()
    return {"status": "ok"}

# --- YASAKLI KULLANICILAR / NUMARALAR ---

@app.get("/api/users")
def get_users(db=Depends(get_db)):
    return db.query(UserFilter).order_by(UserFilter.id.desc()).all()

@app.post("/api/users")
def add_user(item: UserFilterCreate, db=Depends(get_db)):
    clean_id = item.identifier.strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Kimlik bilgisi boş olamaz.")
    chat_id = (item.chat_id or "all").strip()
    chat_title = (item.chat_title or "Tüm Gruplar").strip()

    existing = db.query(UserFilter).filter(
        UserFilter.identifier == clean_id,
        UserFilter.chat_id == chat_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Bu kullanıcı/numara zaten bu grup için listede ekli.")
    uf = UserFilter(
        identifier=clean_id,
        chat_id=chat_id,
        chat_title=chat_title,
        note=item.note.strip() if item.note else None,
        send_dm=item.send_dm,
        custom_dm_message=item.custom_dm_message.strip() if item.custom_dm_message else None,
        is_active=True
    )
    db.add(uf)
    db.commit()
    return {"status": "ok", "id": uf.id}

@app.post("/api/users/{id}/toggle")
def toggle_user(id: int, db=Depends(get_db)):
    uf = db.query(UserFilter).filter(UserFilter.id == id).first()
    if not uf:
        raise HTTPException(status_code=404, detail="Kullanıcı kuralı bulunamadı.")
    uf.is_active = not uf.is_active
    db.commit()
    return {"status": "ok", "is_active": uf.is_active}

@app.delete("/api/users/{id}")
def delete_user(id: int, db=Depends(get_db)):
    uf = db.query(UserFilter).filter(UserFilter.id == id).first()
    if not uf:
        raise HTTPException(status_code=404, detail="Kullanıcı kuralı bulunamadı.")
    db.delete(uf)
    db.commit()
    return {"status": "ok"}

# --- SİLİNEN MESAJ LOGLARI ---

@app.get("/api/logs")
def get_logs(limit: int = 100, db=Depends(get_db)):
    logs = db.query(DeletionLog).order_by(DeletionLog.id.desc()).limit(limit).all()
    results = []
    for l in logs:
        results.append({
            "id": l.id,
            "chat_title": l.chat_title,
            "sender_name": l.sender_name,
            "sender_username": l.sender_username,
            "sender_id": l.sender_id,
            "message_text": l.message_text,
            "trigger_type": l.trigger_type,
            "trigger_value": l.trigger_value,
            "dm_sent": l.dm_sent,
            "dm_status": l.dm_status,
            "created_at": l.created_at.strftime("%d.%m.%Y %H:%M:%S") if l.created_at else ""
        })
    return results

@app.delete("/api/logs/clear")
def clear_logs(db=Depends(get_db)):
    db.query(DeletionLog).delete()
    db.commit()
    return {"status": "ok"}

@app.get("/api/logs/stream")
async def logs_stream():
    """Canlı SSE akışı."""
    queue = asyncio.Queue()
    bot_service.log_subscribers.append(queue)
    async def event_generator():
        try:
            while True:
                data = await queue.get()
                yield f"data: {json.dumps(data)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in bot_service.log_subscribers:
                bot_service.log_subscribers.remove(queue)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# --- AYARLAR ---

@app.get("/api/settings")
def get_all_settings(db=Depends(get_db)):
    return {
        "default_dm_template": get_setting(db, "default_dm_template"),
        "monitor_all_chats": get_setting(db, "monitor_all_chats", "true") == "true",
        "selected_chat_ids": get_setting(db, "selected_chat_ids", ""),
        "ignore_admins": get_setting(db, "ignore_admins", "true") == "true"
    }

@app.post("/api/settings")
def update_settings(item: SettingsUpdate, db=Depends(get_db)):
    set_setting(db, "default_dm_template", item.default_dm_template)
    set_setting(db, "monitor_all_chats", "true" if item.monitor_all_chats else "false")
    set_setting(db, "selected_chat_ids", item.selected_chat_ids)
    set_setting(db, "ignore_admins", "true" if item.ignore_admins else "false")
    return {"status": "ok"}

# --- CANLI TEST VE SİMÜLASYON ---

@app.post("/api/test-message")
def test_message(req: TestMessageRequest, db=Depends(get_db)):
    """Girilen mesajı ve kullanıcıyı test eder, silinip silinmeyeceğini ve gönderilecek DM'i gösterir."""
    chat_id = req.chat_id or "12345"

    # 1. Kullanıcı kontrolü
    user_filters = db.query(UserFilter).filter(UserFilter.is_active == True).all()
    matched_user = None
    for uf in user_filters:
        if not bot_service.is_chat_match(uf.chat_id, chat_id):
            continue
        if check_user_match(uf.identifier, int(req.sender_id or 0), req.sender_username, req.sender_phone):
            matched_user = uf
            break

    if matched_user:
        dm_preview = None
        if matched_user.send_dm:
            template = matched_user.custom_dm_message or get_setting(db, "default_dm_template")
            dm_preview = bot_service.format_dm_message(template, req.sender_username or "Kullanıcı", req.chat_title, f"Yasaklı Kullanıcı: {matched_user.identifier}", req.message_text)
        return {
            "would_delete": True,
            "trigger_type": "user",
            "trigger_value": matched_user.identifier,
            "matched_chat": matched_user.chat_title or "Tüm Gruplar",
            "send_dm": matched_user.send_dm,
            "dm_preview": dm_preview
        }

    # 2. Kelime kontrolü
    word_filters = db.query(WordFilter).filter(WordFilter.is_active == True).all()
    matched_word = None
    for wf in word_filters:
        if not bot_service.is_chat_match(wf.chat_id, chat_id):
            continue
        if check_word_match(wf.word, req.message_text, wf.match_type):
            matched_word = wf
            break

    if matched_word:
        dm_preview = None
        if matched_word.send_dm:
            template = matched_word.custom_dm_message or get_setting(db, "default_dm_template")
            dm_preview = bot_service.format_dm_message(template, req.sender_username or "Kullanıcı", req.chat_title, f"Yasaklı Kelime: '{matched_word.word}'", req.message_text)
        return {
            "would_delete": True,
            "trigger_type": "word",
            "trigger_value": matched_word.word,
            "matched_chat": matched_word.chat_title or "Tüm Gruplar",
            "send_dm": matched_word.send_dm,
            "dm_preview": dm_preview
        }

    return {
        "would_delete": False,
        "message": "Mesaj herhangi bir filtre kuralına takılmadı, silinmez."
    }

# --- WEB PANEL ANASAYFASI ---

@app.get("/", response_class=HTMLResponse)
def index_page():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
