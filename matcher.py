import re
import unicodedata

def turkish_lower(text: str) -> str:
    """Türkçe karakterleri dikkate alarak küçük harfe dönüştürür."""
    if not text:
        return ""
    # Türkçe özel harf dönüşümleri
    replacements = {
        'İ': 'i',
        'I': 'ı',
        'Ğ': 'ğ',
        'Ü': 'ü',
        'Ş': 'ş',
        'Ö': 'ö',
        'Ç': 'ç'
    }
    for upper, lower in replacements.items():
        text = text.replace(upper, lower)
    return text.lower()

def normalize_text(text: str) -> str:
    """Aralıklı yazılan harfleri, özel karakterleri temizler ve standartlaştırır."""
    if not text:
        return ""
    t = turkish_lower(text)
    # Unicode normalize
    t = unicodedata.normalize('NFKD', t)
    # Harf ve rakamlar haricindeki karakterleri boşluğa dönüştür
    # Örn: "s.a.l.a.k" -> "s a l a k"
    return t

def check_word_match(word_rule: str, message_text: str, match_type: str = "contains") -> bool:
    """
    Yasaklı kelime / cümle eşleşmesini kontrol eder.
    match_type:
      - 'contains': Mesajın herhangi bir yerinde geçiyorsa
      - 'exact': Tam kelime eşleşmesi (\bkelime\b)
      - 'regex': Düzenli ifade (regex)
    """
    if not word_rule or not message_text:
        return False
    
    t_rule = turkish_lower(word_rule.strip())
    t_msg = turkish_lower(message_text)
    
    if match_type == "regex":
        try:
            return bool(re.search(word_rule, message_text, re.IGNORECASE))
        except Exception:
            return False
            
    elif match_type == "exact":
        # Kelime sınırlarıyla tam eşleşme
        pattern = r'(?<!\w)' + re.escape(t_rule) + r'(?!\w)'
        if re.search(pattern, t_msg):
            return True
        # Ayrıca noktalama işaretleri veya boşlukla ayrılmış hali kontrol et
        # Örn "s a l a k" veya "s.a.l.a.k"
        condensed_rule = re.sub(r'\s+', '', t_rule)
        condensed_msg = re.sub(r'[\s\.\-_,\*]+', '', t_msg)
        if len(condensed_rule) >= 3 and condensed_rule in condensed_msg:
            return True
        return False
        
    else:  # contains
        # İçerir eşleşmesi
        if t_rule in t_msg:
            return True
        # Harf araları açılmış mesajları da yakala (örn: s a l a k)
        condensed_rule = re.sub(r'\s+', '', t_rule)
        condensed_msg = re.sub(r'[\s\.\-_,\*]+', '', t_msg)
        if len(condensed_rule) >= 3 and condensed_rule in condensed_msg:
            return True
        return False

def check_user_match(rule_identifier: str, sender_id: int, sender_username: str = None, sender_phone: str = None) -> bool:
    """
    Kullanıcı ID, telefon numarası veya @kullanıcı_adı eşleşmesini kontrol eder.
    """
    if not rule_identifier:
        return False
    
    clean_rule = rule_identifier.strip().lower().replace("@", "").replace("+", "").replace(" ", "")
    
    # 1. User ID kontrolü
    if str(sender_id) == clean_rule:
        return True
        
    # 2. Username kontrolü
    if sender_username:
        clean_user = sender_username.strip().lower().replace("@", "")
        if clean_rule == clean_user:
            return True
            
    # 3. Telefon numarası kontrolü
    if sender_phone:
        clean_phone = sender_phone.strip().replace("+", "").replace(" ", "")
        if clean_rule in clean_phone or clean_phone in clean_rule:
            return True
            
    return False
