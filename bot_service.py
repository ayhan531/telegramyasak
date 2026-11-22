import os
import asyncio
import io
import re
import datetime
import base64
import uuid
import qrcode
from typing import Dict, List, Optional
from telethon import TelegramClient, events, types, errors
from telethon.errors import SessionPasswordNeededError

from database import SessionLocal, Account, WordFilter, UserFilter, DeletionLog, BotSetting, MonitoredChat, get_setting, set_setting, DATA_DIR
from matcher import check_word_match, check_user_match

API_ID = int(os.environ.get("API_ID", "37347062"))
API_HASH = os.environ.get("API_HASH", "9bc48bc153c6c5351cc2aa7335851695")

active_clients: Dict[int, TelegramClient] = {}
bot_client: Optional[TelegramClient] = None
bot_info: dict = {}
qr_sessions: Dict[str, dict] = {}
phone_sessions: Dict[str, dict] = {}
log_subscribers: List[asyncio.Queue] = []

async def delete_after_delay(msg, delay: int = 10):
    """Gruptaki uyarı mesajını birkaç saniye sonra silerek grubu temiz tutar."""
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception:
        pass

def is_chat_match(rule_chat_id: str, incoming_chat_id: str) -> bool:
    """Kuralın belirli bir sohbete mi yoksa tüm sohbetlere mi ait olduğunu denetler."""
    if not rule_chat_id or str(rule_chat_id).strip().lower() in ["all", "tüm gruplar", ""]:
        return True
    r = str(rule_chat_id).strip().replace("-100", "").replace("-", "")
    i = str(incoming_chat_id).strip().replace("-100", "").replace("-", "")
    return r == i

async def register_or_update_chat(chat_id: str, chat_title: str, is_channel: bool = False):
    """Botun bulunduğu grubu/kanalı veritabanına kaydeder veya günceller."""
    db = SessionLocal()
    try:
        chat = db.query(MonitoredChat).filter(MonitoredChat.chat_id == chat_id).first()
        if not chat:
            chat = MonitoredChat(
                chat_id=chat_id,
                chat_title=chat_title or f"Grup {chat_id}",
                is_active=True
            )
            db.add(chat)
            db.commit()
            print(f"[Bot Service] Yeni grup/kanal otomatik eklendi: {chat_title} ({chat_id})")
        elif chat_title and chat.chat_title != chat_title:
            chat.chat_title = chat_title
            db.commit()
    except Exception as e:
        print(f"[Bot Service] register_chat error: {e}")
    finally:
        db.close()
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

async def start_qr_login() -> dict:
    """Yeni bir QR kod ile giriş akışı başlatır."""
    temp_id = str(uuid.uuid4())
    session_file = os.path.join(DATA_DIR, f"temp_{temp_id}.session")
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    
    qr_login = await client.qr_login()
    qr_sessions[temp_id] = {
        "client": client,
        "qr": qr_login,
        "status": "pending",
        "created_at": datetime.datetime.utcnow(),
        "session_file": session_file
    }
    
    # Arka planda QR okutulmasını bekle
    asyncio.create_task(_watch_qr_session(temp_id))
    
    return {
        "token": temp_id,
        "qr_image": make_qr_b64(qr_login.url)
    }

async def _watch_qr_session(temp_id: str):
    sess = qr_sessions.get(temp_id)
    if not sess:
        return
    client = sess["client"]
    qr = sess["qr"]
    try:
        # 120 saniye QR taranmasını bekle
        user = await qr.wait(timeout=120)
        # Giriş başarılı!
        sess["status"] = "success"
        sess["user"] = user
        # Kalıcı oturumu veritabanına kaydet
        db = SessionLocal()
        try:
            acc = db.query(Account).filter(Account.telegram_id == user.id).first()
            if not acc:
                acc = Account(
                    phone=user.phone,
                    username=user.username,
                    first_name=user.first_name,
                    telegram_id=user.id,
                    session_file=sess["session_file"],
                    is_active=True
                )
                db.add(acc)
            else:
                acc.session_file = sess["session_file"]
                acc.is_active = True
                acc.username = user.username
                acc.first_name = user.first_name
            db.commit()
            db.refresh(acc)
            
            attach_handlers(client)
            active_clients[acc.id] = client
            sess["account_id"] = acc.id
            print(f"[Bot Service] QR Giriş Başarılı: {user.first_name} (ID: {user.id})")
        finally:
            db.close()
    except asyncio.TimeoutError:
        sess["status"] = "expired"
        try:
            await client.disconnect()
        except Exception:
            pass
    except SessionPasswordNeededError:
        sess["status"] = "2fa_needed"
    except Exception as e:
        print(f"[Bot Service] QR login hatası: {e}")
        sess["status"] = "error"
        sess["error"] = str(e)

async def check_qr_status(temp_id: str) -> dict:
    sess = qr_sessions.get(temp_id)
    if not sess:
        return {"status": "not_found"}
    return {
        "status": sess.get("status", "pending"),
        "error": sess.get("error"),
        "account_id": sess.get("account_id")
    }

async def start_phone_login(phone: str) -> dict:
    temp_id = str(uuid.uuid4())
    session_file = os.path.join(DATA_DIR, f"temp_{temp_id}.session")
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    try:
        sent_code = await client.send_code_request(phone)
        phone_sessions[temp_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "session_file": session_file,
            "status": "code_sent"
        }
        return {"token": temp_id, "status": "code_sent"}
    except Exception as e:
        await client.disconnect()
        return {"status": "error", "message": str(e)}

async def start_bot_token(token: str) -> dict:
    """Telegram @BotFather Bot Token'ı ile istemciyi başlatır."""
    global bot_client, bot_info
    clean_token = token.strip()
    if not clean_token:
        return {"status": "error", "message": "Bot Token boş olamaz."}

    session_path = os.path.join(DATA_DIR, "botfather.session")
    client = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await client.start(bot_token=clean_token)
        me = await client.get_me()
        bot_info = {
            "id": me.id,
            "first_name": me.first_name,
            "username": me.username,
            "is_bot": True,
            "token": clean_token,
            "is_running": True
        }
        attach_handlers(client)
        bot_client = client

        # Token'ı ayarlara kaydet
        db = SessionLocal()
        try:
            set_setting(db, "bot_token", clean_token)
        finally:
            db.close()

        print(f"[Bot Service] Telegram Botu Başlatıldı: @{me.username} ({me.id})")
        return {"status": "success", "bot": bot_info}
    except Exception as e:
        print(f"[Bot Service] Bot Token başlatma hatası: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return {"status": "error", "message": str(e)}

async def stop_bot_token():
    global bot_client, bot_info
    if bot_client:
        try:
            await bot_client.disconnect()
        except Exception:
            pass
        bot_client = None
    bot_info["is_running"] = False
    db = SessionLocal()
    try:
        set_setting(db, "bot_token", "")
    finally:
        db.close()
    return {"status": "ok"}


async def broadcast_log(log_entry: dict):
    """Canlı web paneline silme olayını yayınlar."""
    for queue in log_subscribers:
        try:
            await queue.put(log_entry)
        except Exception:
            pass

def format_dm_message(template: str, user_name: str, chat_title: str, reason: str, message_text: str) -> str:
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    preview = (message_text[:100] + "...") if len(message_text) > 100 else message_text
    msg = template.replace("{user_name}", user_name or "Kullanıcı")
    msg = msg.replace("{chat_title}", chat_title or "Grup")
    msg = msg.replace("{time}", now_str)
    msg = msg.replace("{reason}", reason)
    msg = msg.replace("{message_preview}", preview)
    return msg

def is_chat_match(rule_chat_id: str, incoming_chat_id: str) -> bool:
    """Kuralın belirli bir sohbete mi yoksa tüm sohbetlere mi ait olduğunu denetler."""
    if not rule_chat_id or str(rule_chat_id).strip().lower() in ["all", "tüm gruplar", ""]:
        return True
    r = str(rule_chat_id).strip().replace("-100", "").replace("-", "")
    i = str(incoming_chat_id).strip().replace("-100", "").replace("-", "")
    return r == i

async def handle_incoming_message(client: TelegramClient, event):
    """Gelen veya düzenlenen her mesajı filtre kurallarına göre inceler ve gerekiyorsa siler."""
    try:
        # Özel mesajları (kullanıcı ile bot arasındaki birebir sohbetleri) atla
        if event.is_private:
            return

        chat = await event.get_chat()
        chat_id = str(event.chat_id)
        chat_title = getattr(chat, "title", "Grup/Kanal")

        # Botun olduğu grubu otomatik olarak veritabanına kaydet/güncelle
        asyncio.create_task(register_or_update_chat(chat_id, chat_title, getattr(event, "is_channel", False)))

        sender = await event.get_sender()
        if not sender:
            return

        sender_id = getattr(sender, "id", None)
        sender_username = getattr(sender, "username", None)
        sender_phone = getattr(sender, "phone", None)
        sender_name = f"{getattr(sender, 'first_name', '') or ''} {getattr(sender, 'last_name', '') or ''}".strip()
        if not sender_name:
            sender_name = sender_username or str(sender_id)

        # Mesaj botun kendi hesabı tarafından atıldıysa silme
        me = await client.get_me()
        if sender_id == me.id:
            return

        db = SessionLocal()
        try:
            # Grup izleme filtresi kontrolü
            monitor_all = get_setting(db, "monitor_all_chats", "true") == "true"
            selected_chats = get_setting(db, "selected_chat_ids", "").strip()
            if not monitor_all and selected_chats:
                allowed_ids = [c.strip() for c in selected_chats.split(",") if c.strip()]
                matched_allowed = any(is_chat_match(allowed_id, chat_id) for allowed_id in allowed_ids)
                if not matched_allowed:
                    return

            # Yönetici mesajlarını yoksayma ayarı
            ignore_admins = get_setting(db, "ignore_admins", "true") == "true"
            if ignore_admins and hasattr(event, "chat"):
                try:
                    participant = await client.get_permissions(event.chat_id, sender_id)
                    if participant and (participant.is_admin or participant.is_creator):
                        return
                except Exception:
                    pass

            message_text = event.raw_text or event.message.message or ""

            # 1. KULLANICI / NUMARA FİLTRESİ KONTROLÜ
            user_filters = db.query(UserFilter).filter(UserFilter.is_active == True).all()
            matched_user_rule = None
            for uf in user_filters:
                # Kural belirli bir gruba özel tanımlanmışsa sadece o grupta çalışsın
                if not is_chat_match(uf.chat_id, chat_id):
                    continue

                if check_user_match(uf.identifier, sender_id, sender_username, sender_phone):
                    matched_user_rule = uf
                    break

            if matched_user_rule:
                # Yasaklı kullanıcı yakalandı, mesajı sil!
                await event.delete()
                
                dm_status = "skipped"
                dm_sent = False
                
                if matched_user_rule.send_dm:
                    template = matched_user_rule.custom_dm_message or get_setting(
                        db, "default_dm_template",
                        "Sayın {user_name}, {time} tarihinde {chat_title} grubundaki mesajınız yasaklı kullanıcı kuralı nedeniyle silinmiştir."
                    )
                    dm_text = format_dm_message(template, sender_name, chat_title, f"Yasaklı Kullanıcı: {matched_user_rule.identifier}", message_text)
                    try:
                        await client.send_message(sender_id, dm_text)
                        dm_status = "sent (Özelden DM)"
                        dm_sent = True
                    except Exception as e:
                        try:
                            warn_msg = await event.respond(f"⚠️ {sender_name}, bu grupta mesaj göndermeniz yetkili tarafından engellenmiştir.")
                            asyncio.create_task(delete_after_delay(warn_msg, 8))
                            dm_status = "sent (Grupta 8sn Geçici Uyarı)"
                            dm_sent = True
                        except Exception as e2:
                            dm_status = f"failed: {str(e)[:40]}"

                log_entry = DeletionLog(
                    chat_id=chat_id,
                    chat_title=chat_title,
                    sender_id=str(sender_id),
                    sender_name=sender_name,
                    sender_username=sender_username,
                    message_id=event.id,
                    message_text=message_text,
                    trigger_type="user",
                    trigger_value=matched_user_rule.identifier,
                    dm_sent=dm_sent,
                    dm_status=dm_status,
                    created_at=datetime.datetime.utcnow()
                )
                db.add(log_entry)
                db.commit()

                await broadcast_log({
                    "id": log_entry.id,
                    "chat_title": chat_title,
                    "sender_name": sender_name,
                    "message_text": message_text,
                    "trigger_type": "user",
                    "trigger_value": matched_user_rule.identifier,
                    "dm_status": dm_status,
                    "created_at": datetime.datetime.now().strftime("%H:%M:%S")
                })
                return

            # 2. YASAKLI KELİME / CÜMLE FİLTRESİ KONTROLÜ
            if not message_text:
                return

            word_filters = db.query(WordFilter).filter(WordFilter.is_active == True).all()
            matched_word_rule = None
            for wf in word_filters:
                # Kural belirli bir gruba özel tanımlanmışsa sadece o grupta çalışsın
                if not is_chat_match(wf.chat_id, chat_id):
                    continue

                if check_word_match(wf.word, message_text, wf.match_type):
                    matched_word_rule = wf
                    break

            if matched_word_rule:
                # Yasaklı kelime bulundu, mesajı hemen sil!
                await event.delete()

                dm_status = "skipped"
                dm_sent = False

                if matched_word_rule.send_dm:
                    template = matched_word_rule.custom_dm_message or get_setting(
                        db, "default_dm_template",
                        "Sayın {user_name}, {time} tarihinde {chat_title} grubundaki mesajınız yasaklı kelime ({reason}) nedeniyle silinmiştir."
                    )
                    dm_text = format_dm_message(template, sender_name, chat_title, f"Yasaklı Kelime: '{matched_word_rule.word}'", message_text)
                    try:
                        await client.send_message(sender_id, dm_text)
                        dm_status = "sent (Özelden DM)"
                        dm_sent = True
                    except Exception as e:
                        # Eğer kullanıcı bota özelden /start basmadıysa grupta geçici etiketleyerek uyar
                        try:
                            warn_msg = await event.respond(f"⚠️ {sender_name}, gönderdiğiniz mesaj silindi (Yasaklı kelime: {matched_word_rule.word}).")
                            asyncio.create_task(delete_after_delay(warn_msg, 8))
                            dm_status = "sent (Grupta 8sn Geçici Uyarı)"
                            dm_sent = True
                        except Exception as e2:
                            dm_status = f"failed: {str(e)[:40]}"

                log_entry = DeletionLog(
                    chat_id=chat_id,
                    chat_title=chat_title,
                    sender_id=str(sender_id),
                    sender_name=sender_name,
                    sender_username=sender_username,
                    message_id=event.id,
                    message_text=message_text,
                    trigger_type="word",
                    trigger_value=matched_word_rule.word,
                    dm_sent=dm_sent,
                    dm_status=dm_status,
                    created_at=datetime.datetime.utcnow()
                )
                db.add(log_entry)
                db.commit()

                await broadcast_log({
                    "id": log_entry.id,
                    "chat_title": chat_title,
                    "sender_name": sender_name,
                    "message_text": message_text,
                    "trigger_type": "word",
                    "trigger_value": matched_word_rule.word,
                    "dm_status": dm_status,
                    "created_at": datetime.datetime.now().strftime("%H:%M:%S")
                })
                return

        finally:
            db.close()

    except Exception as e:
        print(f"[Bot Service Error] handle_incoming_message: {e}")

def attach_handlers(client: TelegramClient):
    """Yeni mesaj ve mesaj düzenleme olaylarını istemciye bağlar."""
    @client.on(events.NewMessage)
    async def new_message_handler(event):
        await handle_incoming_message(client, event)

    @client.on(events.MessageEdited)
    async def message_edited_handler(event):
        await handle_incoming_message(client, event)

async def start_account_client(account: Account) -> Optional[TelegramClient]:
    """Kayıtlı hesabı başlatır ve dinlemeye alır."""
    session_path = account.session_file
    if not os.path.isabs(session_path):
        session_path = os.path.join(DATA_DIR, session_path)

    client = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print(f"[Bot Service] Hesap yetkili değil veya oturum süresi dolmuş: {account.id}")
            return None

        me = await client.get_me()
        print(f"[Bot Service] Başarıyla bağlandı: {me.first_name} (@{me.username}) ID:{me.id}")
        attach_handlers(client)
        active_clients[account.id] = client
        return client
    except Exception as e:
        print(f"[Bot Service] Hesap başlatma hatası ({account.id}): {e}")
        return None

async def init_all_active_accounts():
    """Tüm aktif hesapları ve kayıtlı bot token'ı başlatır."""
    db = SessionLocal()
    try:
        # 1. Kayıtlı Bot Token varsa botu başlat
        saved_bot_token = get_setting(db, "bot_token", "").strip()
        if saved_bot_token:
            print("[Bot Service] Kayıtlı bot token başlatılıyor...")
            await start_bot_token(saved_bot_token)

        # 2. Kayıtlı kullanıcı hesaplarını başlat
        accounts = db.query(Account).filter(Account.is_active == True).all()
        for acc in accounts:
            await start_account_client(acc)
    finally:
        db.close()

async def get_all_dialogs() -> List[dict]:
    """Tüm aktif hesapların ve botun üye/yönetici olduğu grup ve kanalları toplar."""
    dialogs = []
    seen_ids = set()

    clients_to_check = list(active_clients.values())
    if bot_client and bot_client.is_connected():
        clients_to_check.append(bot_client)

    for client in clients_to_check:
        if not client.is_connected():
            continue
        try:
            async for dialog in client.iter_dialogs():
                if dialog.is_group or dialog.is_channel:
                    c_id = str(dialog.id)
                    if c_id not in seen_ids:
                        seen_ids.add(c_id)
                        is_admin = True
                        try:
                            perms = await client.get_permissions(dialog.entity, 'me')
                            is_admin = perms.is_admin or perms.is_creator
                        except Exception:
                            pass
                            
                        dialogs.append({
                            "id": c_id,
                            "title": dialog.name or "İsimsiz Sohbet",
                            "is_group": dialog.is_group,
                            "is_channel": dialog.is_channel,
                            "is_admin": is_admin
                        })
        except Exception as e:
            print(f"[Bot Service] get_all_dialogs error: {e}")
    return dialogs
