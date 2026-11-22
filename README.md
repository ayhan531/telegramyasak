# Telegram Otomatik Yasak Mesaj Silme ve Özelden Uyarı Botu

Telegram gruplarında ve kanallarında 7/24 moderasyon sağlayan, belirlenen yasaklı kelimeleri, cümleleri ve yasaklı kullanıcı/numaraları tespit ettiği anda mesajı silip isteğe bağlı olarak gönderene özel mesajla (DM) bildirim gönderen gelişmiş bot ve yönetim paneli.

---

## 🚀 Özellikler

1. **Yasaklı Kelime & Cümle Filtresi**:
   - Belirlenen kelime veya cümleler grupta paylaşıldığında mesaj **anında otomatik silinir**.
   - **Gelişmiş Türkçe Karakter Uyumu**: Büyük/küçük harf duyarsızlığı (`İ`/`i`, `I`/`ı`, `ş`/`s`, vb.).
   - Harflerin arasına boşluk veya nokta koyarak filtreyi aşmaya çalışanları (`s a l a k`, `s.a.l.a.k`) otomatik yakalar.
   - Eşleşme türleri: **İçerir**, **Tam Kelime**, **Regex**.

2. **Yasaklı Numara ve Kullanıcı Filtresi**:
   - Belirli bir telefon numarası (`+90...`), Telegram User ID'si veya `@kullanıcı_adı` mesaj attığı anda mesajı siler.

3. **Kişiye Özelden (DM) Otomatik Uyarı**:
   - Her kelime veya kullanıcı kuralı için **Özelden Mesaj Gönderilsin mi?** seçeneği bağımsız olarak açılıp kapatılabilir.
   - Genel şablon veya kelimeye özel uyarı metni tanımlanabilir.
   - Örnek:
     > *"Sayın Ahmet, 22.11.2026 14:35:10 tarihinde **Kripto Sohbet Grubu** grubuna gönderdiğiniz mesaj yasaklı kural ihlali nedeniyle (Yasaklı Kelime: 'salak') otomatik olarak silinmiştir. Detaylı bilgi için grup yetkilisi ile iletişime geçiniz."*

4. **Yönetici Koruması**:
   - Grup kurucusu ve yöneticilerinin mesajlarının yanlışlıkla silinmesini engeller.

5. **Modern Web Yönetim Paneli**:
   - **QR Kod ile Tek Tıkla Giriş**: Telefonunuzdan Telegram Cihaz Bağlama QR kodunu okutarak anında bağlanın.
   - **Telefon No ile Giriş**: SMS/Telegram kodu ile giriş desteği.
   - **Canlı Silinen Mesaj Günlüğü (Audit Log)**: Silinen tüm mesajlar, gönderen bilgileri ve neden silindiği anlık akar.
   - **Canlı Kural Simülatörü**: Bir mesajın veya kişinin kurallara takılıp takılmayacağını panelden anında test edin.

---

## 🛠️ Kurulum ve Çalıştırma

### 1. Gereksinimler
- Python 3.10+
- Telegram API ID ve Hash (Önceden tanımlanmıştır: `37347062` / `9bc48bc153c6c5351cc2aa7335851695`)

### 2. Yerel Çalıştırma
```bash
pip install -r requirements.txt
python app.py
```
Tarayıcınızda `http://localhost:10000` adresine gidin.

---

## ☁️ Render.com Üzerinde Canlı Dağıtım (Deploy)

Render üzerinde doğrudan Web Service olarak çalışır.
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python app.py`
- **Environment Variables**:
  - `API_ID`: `37347062`
  - `API_HASH`: `9bc48bc153c6c5351cc2aa7335851695`
  - `PORT`: `10000`
