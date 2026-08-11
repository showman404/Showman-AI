import os
import re
import base64
import logging
import requests
import tempfile
import uuid
import wave

from flask import Flask, request, send_from_directory
from dotenv import load_dotenv


# ============================================================
# LOAD ENV
# ============================================================

load_dotenv()

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


# ============================================================
# ENV
# ============================================================

PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "").strip()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
).strip()
GEMINI_TTS_MODEL = os.getenv(
    "GEMINI_TTS_MODEL",
    "gemini-3.1-flash-tts-preview"
).strip()

VOICE_REPLY_ENABLED = os.getenv(
    "VOICE_REPLY_ENABLED",
    "true"
).strip().lower() not in {"0", "false", "no", "off"}

VOICE_DIR = os.path.join(tempfile.gettempdir(), "jarvis_voice")
os.makedirs(VOICE_DIR, exist_ok=True)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
).strip().rstrip("/")

SUPABASE_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)


# ============================================================
# CONSTANTS
# ============================================================

MESSENGER_SEND_URL = (
    "https://graph.facebook.com/v20.0/me/messages"
)

MAX_HISTORY_MESSAGES = 30
MAX_MEMORY_ITEMS = 20
REQUEST_TIMEOUT = 45

def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def clear_memories(sender_id):
    if not sender_id or not SUPABASE_KEY:
        return False
    try:
        response = supabase_request("DELETE", "jarvis_memories", params={"sender_id": f"eq.{sender_id}"})
        return response.ok
    except Exception as e:
        logging.error("clear memories: %s", e)
        return False


# ============================================================
# JARVIS PERSONALITY
# ============================================================

SYSTEM_PROMPT = """
তুমি Jarvis — Anas-এর তৈরি ব্যক্তিগত AI assistant।

তোমার নির্মাতা:
Anas

অত্যন্ত গুরুত্বপূর্ণ:

1. তোমার creator/নির্মাতা হলো Anas।
2. কেউ যদি জিজ্ঞেস করে তোমাকে কে বানিয়েছে,
   কে তৈরি করেছে, creator কে, maker কে—
   উত্তর হবে: "আমাকে Anas তৈরি করেছেন।"
3. Google, Gemini, Groq, Meta, Facebook বা অন্য কোনো
   কোম্পানিকে তোমার creator হিসেবে বলবে না।
4. Gemini ও Groq শুধু AI engine/provider।
5. তুমি নিজেকে Jarvis হিসেবে পরিচয় দেবে।
6. ব্যবহারকারীর saved memory এবং আগের conversation
   ব্যবহার করবে।
7. তথ্য না জানলে বানিয়ে বলবে না।
8. User বাংলা লিখলে বাংলায় উত্তর দেবে।
9. User ইংরেজি লিখলে ইংরেজিতে উত্তর দেবে।
10. স্বাভাবিক ও বন্ধুসুলভভাবে উত্তর দেবে।
"""


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def supabase_request(
    method,
    table,
    params=None,
    json_data=None
):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Supabase configuration missing"
        )

    url = f"{SUPABASE_URL}/rest/v1/{table}"

    response = requests.request(
        method,
        url,
        headers=supabase_headers(),
        params=params,
        json=json_data,
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        logging.error(
            "Supabase %s error: %s",
            table,
            response.text[:1000]
        )

    return response


def extract_content(row):
    for key in (
        "content",
        "message",
        "text",
        "memory"
    ):
        value = row.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def extract_role(row):
    role = row.get("role")

    if role in (
        "user",
        "assistant",
        "model"
    ):
        return (
            "assistant"
            if role == "model"
            else role
        )

    return "user"


# ============================================================
# MESSAGE MEMORY
# ============================================================

def save_message(
    sender_id,
    role,
    content
):
    if not sender_id or not content:
        return False

    payload = {
        "sender_id": sender_id,
        "role": role,
        "content": content,
    }

    try:
        response = supabase_request(
            "POST",
            "jarvis_messages",
            json_data=payload
        )

        return response.ok

    except Exception as e:
        logging.error(
            "Memory save message error: %s",
            e
        )

        return False


def get_conversation_history(sender_id):

    if not sender_id:
        return []

    params = {
        "sender_id": f"eq.{sender_id}",
        "order": "created_at.asc",
        "limit": str(MAX_HISTORY_MESSAGES),
    }

    try:
        response = supabase_request(
            "GET",
            "jarvis_messages",
            params=params
        )

        if not response.ok:
            return []

        rows = response.json()

        history = []

        for row in rows:
            content = extract_content(row)

            if not content:
                continue

            role = extract_role(row)

            if role in (
                "user",
                "assistant"
            ):
                history.append({
                    "role": role,
                    "content": content
                })

        return history[-MAX_HISTORY_MESSAGES:]

    except Exception as e:
        logging.error(
            "History error: %s",
            e
        )

        return []


# ============================================================
# LONG TERM MEMORY
# ============================================================

def save_long_term_memory(
    sender_id,
    memory
):
    if not sender_id or not memory:
        return False

    payload = {
        "sender_id": sender_id,
        "memory": memory.strip()
    }

    try:
        response = supabase_request(
            "POST",
            "jarvis_memories",
            json_data=payload
        )

        return response.ok

    except Exception as e:
        logging.error(
            "Memory save error: %s",
            e
        )

        return False


def get_long_term_memories(sender_id):

    if not sender_id:
        return []

    params = {
        "sender_id": f"eq.{sender_id}",
        "order": "created_at.desc",
        "limit": str(MAX_MEMORY_ITEMS),
    }

    try:
        response = supabase_request(
            "GET",
            "jarvis_memories",
            params=params
        )

        if not response.ok:
            return []

        rows = response.json()

        memories = []

        for row in rows:
            value = extract_content(row)

            if value:
                memories.append(value)

        return memories

    except Exception as e:
        logging.error(
            "Long-term memory error: %s",
            e
        )

        return []


def should_save_memory(text):

    lowered = text.lower()

    keywords = [
        "মনে রাখো",
        "মনে রাখবে",
        "মনে রাখ",
        "ভুলবে না",
        "remember this",
        "remember that",
        "remember",
        "save this",
        "store this",
    ]

    return any(
        keyword in lowered
        for keyword in keywords
    )


def clean_memory(text):

    prefixes = [
        "মনে রাখো",
        "মনে রাখবে",
        "মনে রাখ",
        "ভুলবে না",
        "remember this",
        "remember that",
        "remember",
        "save this",
        "store this",
    ]

    result = text.strip()

    for prefix in prefixes:
        if result.lower().startswith(
            prefix.lower()
        ):
            result = result[
                len(prefix):
            ].strip()

            break

    return result


# ============================================================
# CREATOR
# ============================================================

def is_creator_question(text):

    lowered = text.lower()

    keywords = [
        "তোমাকে কে বানিয়েছে",
        "তোমাকে কে বানাইছে",
        "কে তোমাকে বানিয়েছে",
        "কে তোমাকে বানাইছে",
        "তোমার creator কে",
        "creator কে",
        "তোমার নির্মাতা কে",
        "নির্মাতা কে",
        "তোমাকে কে তৈরি করেছে",
        "who created you",
        "who made you",
        "who built you",
        "your creator",
        "your maker",
    ]

    return any(
        word in lowered
        for word in keywords
    )


def creator_answer():

    return (
        "আমাকে Anas তৈরি করেছেন। ❤️\n"
        "Gemini ও Groq আমার AI engine হিসেবে কাজ করে, "
        "কিন্তু আমার নির্মাতা Anas।"
    )


# ============================================================
# BUILD CONTEXT
# ============================================================

def build_prompt(
    sender_id,
    user_text
):

    history = get_conversation_history(
        sender_id
    )

    memories = get_long_term_memories(
        sender_id
    )

    parts = [
        SYSTEM_PROMPT
    ]

    if memories:

        parts.append(
            "\nSaved memory:"
        )

        for memory in memories:
            parts.append(
                f"- {memory}"
            )

    if history:

        parts.append(
            "\nRecent conversation:"
        )

        for item in history:

            label = (
                "User"
                if item["role"] == "user"
                else "Jarvis"
            )

            parts.append(
                f"{label}: "
                f"{item['content']}"
            )

    parts.append(
        "\nCurrent user message:"
    )

    parts.append(user_text)

    return "\n".join(parts)


# ============================================================
# GEMINI TEXT
# ============================================================

def ask_gemini(prompt):

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 1024
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:

        raise RuntimeError(
            f"Gemini HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    candidates = data.get(
        "candidates",
        []
    )

    if not candidates:
        raise RuntimeError(
            "Gemini returned no candidates"
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    answer = "\n".join(
        part.get("text", "")
        for part in parts
        if part.get("text")
    ).strip()

    if not answer:
        raise RuntimeError(
            "Gemini returned empty response"
        )

    logging.info(
        "AI provider: Gemini / %s",
        GEMINI_MODEL
    )

    return answer


# ============================================================
# GROQ TEXT FALLBACK
# ============================================================

def groq_headers():
    return {"Content-Type":"application/json","Authorization":f"Bearer {GROQ_API_KEY}"}

def get_groq_models():
    if not GROQ_API_KEY: return []
    try:
        r=requests.get("https://api.groq.com/openai/v1/models",headers=groq_headers(),timeout=REQUEST_TIMEOUT)
        return [x.get("id") for x in r.json().get("data",[]) if x.get("id")] if r.ok else []
    except Exception as e:
        logging.error("Groq models: %s",e); return []

def resolve_groq_model():
    models=get_groq_models()
    if GROQ_MODEL and (not models or GROQ_MODEL in models): return GROQ_MODEL
    for x in ("llama-3.3-70b-versatile","llama-3.1-8b-instant"):
        if x in models: return x
    return models[0] if models else GROQ_MODEL

def ask_groq(prompt):
    if not GROQ_API_KEY: raise RuntimeError("GROQ_API_KEY missing")
    model=resolve_groq_model()
    r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers=groq_headers(),json={"model":model,"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}],"max_completion_tokens":1200,"temperature":0.5},timeout=REQUEST_TIMEOUT)
    if not r.ok: raise RuntimeError(f"Groq HTTP {r.status_code}: {r.text[:1000]}")
    choices=r.json().get("choices",[])
    if not choices: raise RuntimeError("Groq returned no choices")
    answer=choices[0].get("message",{}).get("content","").strip()
    if not answer: raise RuntimeError("Groq returned empty response")
    logging.info("AI provider: Groq / %s",model)
    return answer


# ============================================================
# TAVILY WEB SEARCH
# ============================================================

def tavily_search(query):

    if not TAVILY_API_KEY:
        raise RuntimeError(
            "TAVILY_API_KEY missing"
        )

    url = "https://api.tavily.com/search"

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "topic": "news",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
    }

    response = requests.post(
        url,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:

        raise RuntimeError(
            f"Tavily HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    results = data.get(
        "results",
        []
    )

    cleaned = []

    for result in results:

        title = result.get(
            "title",
            ""
        )

        url = result.get(
            "url",
            ""
        )

        content = result.get(
            "content",
            ""
        )

        if title and url:

            cleaned.append({
                "title": title,
                "url": url,
                "content": content
            })

    logging.info(
        "Tavily found %d results",
        len(cleaned)
    )

    return cleaned


# ============================================================
# NEWS SCREENSHOT -> GEMINI VISION
# ============================================================

def download_messenger_image(
    attachment_url
):

    response = requests.get(
        attachment_url,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:
        raise RuntimeError(
            "Could not download Messenger image"
        )

    content_type = (
        response.headers.get(
            "Content-Type",
            "image/jpeg"
        )
    )

    image_bytes = response.content

    return (
        content_type,
        image_bytes
    )


def extract_news_from_image(
    image_bytes,
    mime_type
):

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY missing"
        )

    encoded = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    prompt = """
এই ছবিটি একটি সংবাদ/নিউজ screenshot হতে পারে।

ছবিটি ভালোভাবে দেখে বের করো:

1. সম্পূর্ণ headline
2. সংবাদমাধ্যমের নাম, যদি দেখা যায়
3. ছবিতে দেখা গুরুত্বপূর্ণ নাম
4. location
5. date, যদি দেখা যায়
6. headline-এর মূল বিষয়

তারপর একটি ছোট search query তৈরি করো,
যেটা দিয়ে online-এ এই খবরের original source
খুঁজে পাওয়া সম্ভব।

শুধু নিচের JSON format-এ উত্তর দাও:

{
  "headline": "...",
  "publisher": "...",
  "people": "...",
  "location": "...",
  "date": "...",
  "search_query": "..."
}
"""

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    },
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": encoded
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 700
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:

        raise RuntimeError(
            f"Gemini Vision HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    candidates = data.get(
        "candidates",
        []
    )

    if not candidates:
        raise RuntimeError(
            "Gemini Vision returned no result"
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    text = "\n".join(
        part.get("text", "")
        for part in parts
        if part.get("text")
    ).strip()

    if not text:
        raise RuntimeError(
            "Could not read screenshot"
        )

    return text


# ============================================================
# EXTRACT JSON FROM GEMINI OUTPUT
# ============================================================

def extract_json_object(text):

    text = text.strip()

    text = re.sub(
        r"```json",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = text.replace(
        "```",
        ""
    ).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        return None

    candidate = text[
        start:end + 1
    ]

    import json

    try:
        return json.loads(candidate)
    except Exception:
        return None


# ============================================================
# NEWS SOURCE ANALYSIS
# ============================================================

def build_news_result(
    extracted_text,
    search_results
):

    import json

    results_text = []

    for index, result in enumerate(
        search_results,
        start=1
    ):

        results_text.append(
            f"""
SOURCE {index}
Title: {result['title']}
URL: {result['url']}
Content: {result['content']}
"""
        )

    prompt = f"""
তুমি Jarvis।

একটি news screenshot থেকে পাওয়া তথ্য:

{extracted_text}

অনলাইন search-এর ফলাফল:

{"".join(results_text)}

কাজ:

1. Screenshot-এর খবরের সঙ্গে কোন source সবচেয়ে বেশি মিলে তা নির্ধারণ করো।
2. Source-এর title ও URL উল্লেখ করো।
3. Screenshot-এর তথ্য এবং online source-এর তথ্যের মধ্যে
   mismatch থাকলে সেটা পরিষ্কারভাবে বলো।
4. Source পাওয়া না গেলে সেটা বলো।
5. কোনো URL বানিয়ে লিখবে না।
6. Search result-এর URL-ই ব্যবহার করবে।
7. সংক্ষিপ্ত বাংলায় উত্তর দাও।

Format:

📰 খবর:
...

🔎 মিল পাওয়া source:
...

🔗 Source:
...

📌 যাচাই:
...
"""

    # Gemini দিয়ে source analysis
    try:
        return ask_gemini(prompt)

    except Exception as gemini_error:

        logging.error(
            "Gemini news analysis failed: %s",
            gemini_error
        )

    # Groq fallback
    try:
        return ask_groq(prompt)

    except Exception as groq_error:

        logging.error(
            "Groq news analysis failed: %s",
            groq_error
        )

    # Safe fallback
    lines = [
        "📰 Screenshot-এর সম্ভাব্য খবরের source:"
    ]

    for result in search_results[:3]:

        lines.append(
            f"\n• {result['title']}"
        )

        lines.append(
            f"🔗 {result['url']}"
        )

    return "\n".join(lines)


# ============================================================
# PROCESS NEWS SCREENSHOT
# ============================================================

def process_news_image(
    sender_id,
    attachment_url
):

    try:

        mime_type, image_bytes = (
            download_messenger_image(
                attachment_url
            )
        )

        extracted = extract_news_from_image(
            image_bytes,
            mime_type
        )

        logging.info(
            "News screenshot extracted: %s",
            extracted[:1000]
        )

        parsed = extract_json_object(
            extracted
        )

        if parsed:

            search_query = (
                parsed.get(
                    "search_query",
                    ""
                )
            ).strip()

            if not search_query:

                search_query = (
                    parsed.get(
                        "headline",
                        ""
                    )
                ).strip()

        else:
            search_query = extracted

        if not search_query:

            return (
                "বস, screenshot থেকে "
                "নিউজের তথ্য ঠিকমতো পড়তে পারিনি।"
            )

        # ----------------------------------------------------
        # SEARCH WEB
        # ----------------------------------------------------

        results = tavily_search(
            search_query
        )

        if not results:

            return (
                "বস, screenshot থেকে খবরটি পড়তে "
                "পেরেছি, কিন্তু online-এ নির্ভরযোগ্য "
                "source খুঁজে পাইনি।"
            )

        # ----------------------------------------------------
        # ANALYZE SOURCES
        # ----------------------------------------------------

        answer = build_news_result(
            extracted,
            results
        )

        return answer

    except Exception as e:

        logging.error(
            "News screenshot error: %s",
            e
        )

        return (
            "বস, screenshot-টা পেয়েছি, কিন্তু "
            "এখন online source খুঁজতে সমস্যা হচ্ছে।\n\n"
            f"Error: {str(e)[:300]}"
        )


# ============================================================
# WEATHER / TRAFFIC / BOSS PHOTO / VOICE / ADMIN
# ============================================================

def is_weather_request(text):
    t=(text or "").lower(); return any(x in t for x in ["weather","আবহাওয়া","আবহাওয়া","তাপমাত্রা","temperature","বৃষ্টি হবে"])

def weather_reply(place):
    r=requests.get("https://geocoding-api.open-meteo.com/v1/search",params={"name":place,"count":1,"language":"en","format":"json"},timeout=REQUEST_TIMEOUT)
    if not r.ok: raise RuntimeError("Weather location search failed")
    rows=r.json().get("results",[])
    if not rows: raise RuntimeError("Location not found")
    loc=rows[0]
    r=requests.get("https://api.open-meteo.com/v1/forecast",params={"latitude":loc["latitude"],"longitude":loc["longitude"],"current":"temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,wind_speed_10m","timezone":"auto"},timeout=REQUEST_TIMEOUT)
    if not r.ok: raise RuntimeError("Weather request failed")
    c=r.json().get("current",{})
    return (f"🌤️ {loc.get('name','')}, {loc.get('country','')}\n\n🌡️ তাপমাত্রা: {c.get('temperature_2m')}°C\n🤒 অনুভূত: {c.get('apparent_temperature')}°C\n💧 আর্দ্রতা: {c.get('relative_humidity_2m')}%\n🌧️ বৃষ্টিপাত: {c.get('precipitation')} mm\n💨 বাতাস: {c.get('wind_speed_10m')} km/h")

def is_traffic_question(text):
    t=(text or "").lower(); return any(x in t for x in ["জ্যাম","ট্রাফিক","traffic","traffic jam","রাস্তার অবস্থা"])

def save_location(sender_id,lat,lng):
    user_locations[str(sender_id)]={"latitude":lat,"longitude":lng}
    if SUPABASE_KEY:
        try: supabase_request("POST","jarvis_locations",json_data={"sender_id":str(sender_id),"latitude":float(lat),"longitude":float(lng),"updated_at":now_iso()})
        except Exception as e: logging.error("location save: %s",e)

def get_saved_location(sender_id):
    if str(sender_id) in user_locations: return user_locations[str(sender_id)]
    if not SUPABASE_KEY: return None
    try:
        r=supabase_request("GET","jarvis_locations",params={"sender_id":f"eq.{sender_id}","order":"updated_at.desc","limit":"1"})
        rows=r.json() if r.ok else []
        if rows:
            loc={"latitude":rows[0]["latitude"],"longitude":rows[0]["longitude"]}; user_locations[str(sender_id)]=loc; return loc
    except Exception as e: logging.error("location read: %s",e)
    return None

def get_traffic(lat,lng,destination):
    if not GOOGLE_MAPS_API_KEY: raise RuntimeError("GOOGLE_MAPS_API_KEY missing")
    r=requests.post("https://routes.googleapis.com/directions/v2:computeRoutes",headers={"Content-Type":"application/json","X-Goog-Api-Key":GOOGLE_MAPS_API_KEY,"X-Goog-FieldMask":"routes.duration,routes.staticDuration,routes.distanceMeters"},json={"origin":{"location":{"latLng":{"latitude":float(lat),"longitude":float(lng)}}},"destination":{"address":destination},"travelMode":"DRIVE","routingPreference":"TRAFFIC_AWARE_OPTIMAL","computeAlternativeRoutes":False,"languageCode":"bn-BD","units":"METRIC"},timeout=REQUEST_TIMEOUT)
    if not r.ok: raise RuntimeError(f"Google Routes HTTP {r.status_code}: {r.text[:500]}")
    routes=r.json().get("routes",[])
    if not routes: raise RuntimeError("এই রুটের কোনো তথ্য পাওয়া যায়নি")
    return routes[0]

def traffic_reply(route,destination):
    def sec(v):
        try:return float(str(v).replace("s",""))
        except:return 0
    traffic=sec(route.get("duration")); normal=sec(route.get("staticDuration")); delay=max(0,traffic-normal) if normal else 0
    status="🟢 খুব বেশি জ্যাম নেই।" if delay<=120 else "🟡 হালকা থেকে মাঝারি জ্যাম আছে।" if delay<=600 else "🟠 বেশ ভালো জ্যাম আছে।" if delay<=1200 else "🔴 অনেক বেশি জ্যাম আছে।"
    return f"🚦 ট্রাফিক রিপোর্ট\n\n📍 গন্তব্য: {destination}\n🛣️ দূরত্ব: {float(route.get('distanceMeters',0))/1000:.1f} কিমি\n⏱️ সময়: {round(traffic/60)} মিনিট\n{status}"

def extract_destination(text):
    generic={"জ্যাম","আছে","কি","কিনা","কত","ট্রাফিক","বল","দেখ","দেখো","জানাও","রাস্তা","রাস্তায়","রাস্তায়","traffic","jam","traffic jam"}
    return " ".join(w for w in (text or "").split() if w.lower() not in generic).strip()

def is_admin(sender_id):
    return bool(ADMIN_ID) and str(sender_id) == str(ADMIN_ID)


def is_boss_photo_registration(text):
    t=(text or "").lower(); return any(x in t for x in ["save as boss","save this as boss","boss photo save","বসের ছবি হিসেবে রাখ","বসের ছবি হিসেবে সেভ","এটা বসের ছবি","এটা আমার ছবি","এটা আমি"])

def is_boss_photo_request(text):
    t=(text or "").lower(); return any(x in t for x in ["বসের ছবি","বস এর ছবি","বসের ফটো","বস এর ফটো","বসের পিক","boss photo","boss picture","boss pic","show me your boss","show your boss","তোমার বসের ছবি"])

def upload_boss_photo(image_url):
    if not SUPABASE_URL or not SUPABASE_KEY: raise RuntimeError("Supabase configuration missing")
    r=requests.get(image_url,timeout=REQUEST_TIMEOUT)
    if not r.ok: raise RuntimeError("Boss photo download failed")
    import uuid; filename=f"boss_{uuid.uuid4().hex}.jpg"; content_type=r.headers.get("Content-Type","image/jpeg")
    u=requests.post(f"{SUPABASE_URL}/storage/v1/object/boss-photos/{filename}",headers={"Authorization":f"Bearer {SUPABASE_KEY}","apikey":SUPABASE_KEY,"Content-Type":content_type,"x-upsert":"true"},data=r.content,timeout=REQUEST_TIMEOUT)
    if not u.ok: raise RuntimeError(f"Boss photo upload failed: {u.text[:500]}")
    public_url=f"{SUPABASE_URL}/storage/v1/object/public/boss-photos/{filename}"
    if not supabase_request("POST","boss_photos",json_data={"photo_url":public_url,"label":"Anas"}).ok: raise RuntimeError("Boss photo DB save failed")
    return public_url

def get_boss_photo():
    if not SUPABASE_KEY:return None
    try:
        r=supabase_request("GET","boss_photos",params={"select":"photo_url,label,created_at","order":"created_at.desc","limit":"1"}); rows=r.json() if r.ok else []
        return rows[0].get("photo_url") if rows else None
    except Exception as e: logging.error("boss photo: %s",e); return None

def send_image_message(recipient_id,image_url):
    try:
        r=requests.post(MESSENGER_SEND_URL,params={"access_token":PAGE_ACCESS_TOKEN},json={"recipient":{"id":recipient_id},"message":{"attachment":{"type":"image","payload":{"url":image_url,"is_reusable":True}}}},timeout=REQUEST_TIMEOUT); return r.ok
    except Exception as e: logging.error("image send: %s",e); return False

def transcribe_audio(audio_url):
    """Messenger voice/audio -> Bengali text using Groq Whisper."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")

    audio = requests.get(audio_url, timeout=REQUEST_TIMEOUT)
    if not audio.ok:
        raise RuntimeError("Audio download failed")

    content_type = audio.headers.get("Content-Type", "audio/ogg")
    extension = ".ogg"
    if "mp4" in content_type or "m4a" in content_type:
        extension = ".m4a"
    elif "webm" in content_type:
        extension = ".webm"
    elif "mpeg" in content_type or "mp3" in content_type:
        extension = ".mp3"

    r = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (f"voice{extension}", audio.content, content_type)},
        data={
            "model": "whisper-large-v3-turbo",
            "language": "bn",
            "response_format": "json",
            "temperature": "0"
        },
        timeout=120
    )

    if not r.ok:
        raise RuntimeError(
            f"Whisper HTTP {r.status_code}: {r.text[:500]}"
        )

    text = r.json().get("text", "").strip()
    if not text:
        raise RuntimeError("Voice transcription empty")

    logging.info("Voice transcription: %s", text)
    return text


def _write_pcm_wav(filename, pcm_data, channels=1, rate=24000, sample_width=2):
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm_data)


def generate_jarvis_voice(text):
    """Generate Bengali JARVIS voice with Gemini TTS and save a public WAV file."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")

    if not VOICE_REPLY_ENABLED:
        raise RuntimeError("Voice reply disabled")

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)

    interaction = client.interactions.create(
        model=GEMINI_TTS_MODEL,
        input=(
            "Speak naturally in Bengali as JARVIS, a calm and confident personal AI assistant. "
            "Use a warm, clear, friendly male assistant style. Do not add extra words. "
            f"Read exactly this response:\n{text}"
        ),
        response_format={"type": "audio"},
        generation_config={
            "speech_config": [
                {"voice": "Kore"}
            ]
        }
    )

    audio = getattr(interaction, "output_audio", None)
    audio_data = getattr(audio, "data", None) if audio else None

    if not audio_data:
        raise RuntimeError("Gemini TTS returned no audio")

    pcm_data = base64.b64decode(audio_data)
    filename = f"jarvis_{uuid.uuid4().hex}.wav"
    filepath = os.path.join(VOICE_DIR, filename)
    _write_pcm_wav(filepath, pcm_data)

    logging.info("JARVIS voice generated: %s", filename)
    return filename


def send_voice_message(recipient_id, text):
    """Generate and send JARVIS voice through Messenger."""
    try:
        filename = generate_jarvis_voice(text)
        base_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        if not base_url:
            raise RuntimeError("RENDER_EXTERNAL_URL missing")

        audio_url = f"{base_url}/voice/{filename}"

        payload = {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "audio",
                    "payload": {
                        "url": audio_url,
                        "is_reusable": True
                    }
                }
            }
        }

        response = requests.post(
            MESSENGER_SEND_URL,
            params={"access_token": PAGE_ACCESS_TOKEN},
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if not response.ok:
            logging.error("Messenger voice error: %s", response.text[:1000])
            return False

        return True

    except Exception as e:
        logging.error("Voice reply error: %s", e)
        return False


def admin_command(sender_id,text):
    if not ADMIN_ID or str(sender_id)!=str(ADMIN_ID): return None
    c=(text or "").strip().lower()
    if c in ("/help","jarvis help","জারভিস হেল্প"): return "👑 Admin Commands\n\n/help\n/status\n/models\n/memory\n/forget all + CONFIRM FORGET ALL\n/boss photo"
    if c in ("/status","jarvis status"): return f"🟢 Status\nGemini: {'ON' if GEMINI_API_KEY else 'OFF'} / {GEMINI_MODEL}\nGroq: {'ON' if GROQ_API_KEY else 'OFF'} / {resolve_groq_model() if GROQ_API_KEY else 'N/A'}\nTavily: {'ON' if TAVILY_API_KEY else 'OFF'}\nMaps: {'ON' if GOOGLE_MAPS_API_KEY else 'OFF'}\nSupabase: {'ON' if SUPABASE_KEY else 'OFF'}\nADMIN_ID: {'SET' if ADMIN_ID else 'MISSING'}"
    if c in ("/models","jarvis models"):
        models=get_groq_models(); return "⚡ Active Groq models:\n\n"+"\n".join(f"• {m}" for m in models[:30]) if models else "Groq model list পাওয়া যায়নি।"
    if c in ("/memory","jarvis memory"):
        mem=get_long_term_memories(sender_id); return "🧠 Saved memory:\n\n"+"\n".join(f"{i}. {m}" for i,m in enumerate(mem,1)) if mem else "🧠 কোনো memory নেই।"
    if c in ("/forget all","forget all","/clear memory"): return "🧠 সব memory মুছতে আবার লিখুন: CONFIRM FORGET ALL"
    if c=="confirm forget all": return "🧹 সব memory মুছে দেওয়া হয়েছে।" if clear_memories(sender_id) else "Memory delete করা যায়নি।"
    return None

# ============================================================
# MESSENGER SEND
# ============================================================

def send_message(
    recipient_id,
    text
):

    if not PAGE_ACCESS_TOKEN:
        return False

    text = str(text).strip()

    if not text:
        return False

    chunks = []

    max_length = 1800

    while len(text) > max_length:

        cut = text.rfind(
            "\n",
            0,
            max_length
        )

        if cut < 500:
            cut = max_length

        chunks.append(
            text[:cut].strip()
        )

        text = text[
            cut:
        ].strip()

    if text:
        chunks.append(text)

    for chunk in chunks:

        payload = {
            "recipient": {
                "id": recipient_id
            },
            "message": {
                "text": chunk
            }
        }

        try:

            response = requests.post(
                MESSENGER_SEND_URL,
                params={
                    "access_token":
                        PAGE_ACCESS_TOKEN
                },
                json=payload,
                timeout=REQUEST_TIMEOUT
            )

            if not response.ok:

                logging.error(
                    "Messenger error: %s",
                    response.text[:1000]
                )

                return False

        except Exception as e:

            logging.error(
                "Messenger send error: %s",
                e
            )

            return False

    return True


# ============================================================
# PUBLIC VOICE FILES
# ============================================================

@app.route("/voice/<path:filename>", methods=["GET"])
def serve_voice(filename):
    return send_from_directory(
        VOICE_DIR,
        filename,
        mimetype="audio/wav",
        max_age=300
    )


# ============================================================
# WEBHOOK VERIFY
# ============================================================

@app.route(
    "/webhook",
    methods=["GET"]
)
def verify_webhook():

    mode = request.args.get(
        "hub.mode"
    )

    token = request.args.get(
        "hub.verify_token"
    )

    challenge = request.args.get(
        "hub.challenge"
    )

    if (
        mode == "subscribe"
        and token == VERIFY_TOKEN
    ):

        return challenge, 200

    return (
        "Verification failed",
        403
    )


# ============================================================
# MAIN WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    data = request.get_json(
        silent=True
    ) or {}

    if data.get("object") != "page":
        return "OK", 200

    for entry in data.get(
        "entry",
        []
    ):

        for event in entry.get(
            "messaging",
            []
        ):

            sender_id = (
                event.get(
                    "sender",
                    {}
                ).get("id")
            )

            if not sender_id:
                continue

            message = event.get(
                "message",
                {}
            )

            attachments = message.get(
                "attachments",
                []
            )

            # =================================================
            # LOCATION MESSAGE
            # =================================================

            for attachment in attachments:
                if attachment.get("type")=="location":
                    coords=attachment.get("payload",{}).get("coordinates",{})
                    lat=coords.get("lat"); lng=coords.get("long")
                    if lat is not None and lng is not None:
                        save_location(sender_id,lat,lng)
                        send_message(sender_id,"📍 তোমার Location পেয়েছি! এখন destination লিখে পাঠাও।\nযেমন: Gulshan 1")

            # =================================================
            # IMAGE MESSAGE
            # =================================================

            image_attachment = None

            for attachment in attachments:

                if attachment.get(
                    "type"
                ) == "image":

                    image_attachment = (
                        attachment
                        .get("payload", {})
                        .get("url")
                    )

                    break

            if image_attachment:

                user_text = (message.get("text", "") or "").strip()

                if is_admin(sender_id) and is_boss_photo_registration(user_text):
                    try:
                        upload_boss_photo(image_attachment)
                        answer="ঠিক আছে বস ❤️ এই ছবিটা এখন থেকে আপনার Boss/Anas photo হিসেবে save করে রাখলাম।"
                    except Exception as e:
                        logging.error("Boss photo save: %s",e)
                        answer=f"বস, ছবিটা পেয়েছি কিন্তু save করতে পারিনি। Error: {str(e)[:250]}"
                    save_message(sender_id,"user","[Boss photo registration]")
                    save_message(sender_id,"assistant",answer)
                    send_message(sender_id,answer)
                    if voice_input:
                        send_voice_message(sender_id, answer)
                    continue

                if is_boss_photo_request(user_text):
                    boss_photo=get_boss_photo()
                    if boss_photo: send_image_message(sender_id,boss_photo)
                    else: send_message(sender_id,"বসের কোনো ছবি এখনো save করা হয়নি।")
                    continue

                logging.info("Image received from %s",sender_id)
                answer=process_news_image(sender_id,image_attachment)
                save_message(sender_id,"user","[News screenshot]")
                save_message(sender_id,"assistant",answer)
                send_message(sender_id,answer)
                continue

            # =================================================
            # TEXT MESSAGE
            # =================================================

            user_text = (
                message.get(
                    "text",
                    ""
                ) or ""
            ).strip()

            audio_url=None
            voice_input=False
            for attachment in attachments:
                if attachment.get("type") in ("audio","file"):
                    audio_url=attachment.get("payload",{}).get("url")
                    if audio_url: break
            if audio_url and not user_text:
                voice_input=True
                try: user_text=transcribe_audio(audio_url)
                except Exception as e:
                    logging.error("Voice transcription: %s",e)
                    send_message(sender_id,"🎙️ Voice message বুঝতে পারিনি। আবার পাঠাও।")
                    continue

            if not user_text:
                continue

            logging.info(
                "Message from %s: %s",
                sender_id,
                user_text
            )

            save_message(
                sender_id,
                "user",
                user_text
            )

            # =================================================
            # LONG-TERM MEMORY
            # =================================================

            if should_save_memory(
                user_text
            ):

                memory = clean_memory(
                    user_text
                )

                if memory:

                    saved = (
                        save_long_term_memory(
                            sender_id,
                            memory
                        )
                    )

                    if saved:

                        answer = (
                            "ঠিক আছে বস ❤️ "
                            "আমি এটা মনে রাখলাম।"
                        )

                    else:

                        answer = (
                            "বস, মনে রাখতে চেয়েছিলাম "
                            "কিন্তু memory database-এ "
                            "save করতে পারিনি।"
                        )

                    save_message(
                        sender_id,
                        "assistant",
                        answer
                    )

                    send_message(
                        sender_id,
                        answer
                    )

                    if voice_input:
                        send_voice_message(sender_id, answer)

                    continue

            # =================================================
            # CREATOR
            # =================================================

            if is_creator_question(
                user_text
            ):

                answer = creator_answer()

                save_message(
                    sender_id,
                    "assistant",
                    answer
                )

                send_message(
                    sender_id,
                    answer
                )

                if voice_input:
                    send_voice_message(sender_id, answer)

                continue

            # =================================================
            # ADMIN COMMANDS
            # =================================================
            admin_answer=admin_command(sender_id,user_text)
            if admin_answer:
                save_message(sender_id,"assistant",admin_answer)
                send_message(sender_id,admin_answer)
                if voice_input:
                    send_voice_message(sender_id, admin_answer)
                continue

            # =================================================
            # WEATHER
            # =================================================
            if is_weather_request(user_text):
                place=re.sub(r"(আজকের|আজ|weather|আবহাওয়া|আবহাওয়া|তাপমাত্রা|temperature|কেমন|কত|এর|তে|এ)"," ",user_text,flags=re.IGNORECASE)
                place=re.sub(r"\s+"," ",place).strip()
                if len(place)<2:
                    send_message(sender_id,"🌤️ কোন জায়গার weather জানতে চাও?\nযেমন: Dhaka")
                    continue
                try: answer=weather_reply(place)
                except Exception as e: answer=f"আবহাওয়ার তথ্য আনতে সমস্যা হয়েছে: {str(e)[:250]}"
                save_message(sender_id,"assistant",answer); send_message(sender_id,answer)
                if voice_input:
                    send_voice_message(sender_id, answer)
                continue

            # =================================================
            # TRAFFIC
            # =================================================
            if is_traffic_question(user_text):
                location=get_saved_location(sender_id)
                if not location:
                    send_message(sender_id,"🚦 Traffic দেখতে তোমার current Location দরকার।\n\nMessenger-এর Location option থেকে location পাঠাও।")
                    continue
                destination=extract_destination(user_text)
                if len(destination)<3:
                    send_message(sender_id,"📍 Destination লিখে পাঠাও।\nযেমন: Gulshan 1 / Farmgate / Airport")
                    continue
                try: answer=traffic_reply(get_traffic(location["latitude"],location["longitude"],destination),destination)
                except Exception as e: answer=f"🚦 Traffic data আনতে সমস্যা হয়েছে: {str(e)[:250]}"
                save_message(sender_id,"assistant",answer); send_message(sender_id,answer)
                if voice_input:
                    send_voice_message(sender_id, answer)
                continue

            # =================================================
            # NORMAL AI
            # =================================================

            prompt = build_prompt(
                sender_id,
                user_text
            )

            try:

                answer = ask_gemini(
                    prompt
                )

            except Exception as gemini_error:

                logging.error(
                    "Gemini failed: %s",
                    gemini_error
                )

                try:

                    answer = ask_groq(
                        prompt
                    )

                except Exception as groq_error:

                    logging.error(
                        "Groq failed: %s",
                        groq_error
                    )

                    answer = (
                        "দুঃখিত বস, এই মুহূর্তে "
                        "আমার AI engine-গুলো "
                        "ব্যবহার করা যাচ্ছে না।"
                    )

            save_message(
                sender_id,
                "assistant",
                answer
            )

            send_message(
                sender_id,
                answer
            )

            if voice_input:
                send_voice_message(sender_id, answer)

    return "OK", 200


# ============================================================
# HOME / HEALTH
# ============================================================

@app.route("/")
def home():

    return {
        "status": "running",
        "jarvis": "Showman-AI",
        "creator": "Anas",
        "gemini": GEMINI_MODEL,
        "groq": resolve_groq_model() if GROQ_API_KEY else "not configured",
        "web_search": (
            "Tavily"
            if TAVILY_API_KEY
            else "not configured"
        ),
        "memory": (
            "Supabase"
            if SUPABASE_KEY
            else "not configured"
        ),
        "traffic": "Google Routes" if GOOGLE_MAPS_API_KEY else "not configured",
        "weather": "Open-Meteo",
        "voice_input": "Groq Whisper" if GROQ_API_KEY else "not configured",
        "voice_output": GEMINI_TTS_MODEL if (GEMINI_API_KEY and VOICE_REPLY_ENABLED) else "not configured",
        "admin": "configured" if ADMIN_ID else "MISSING",
    }, 200


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    logging.info(
        "Starting Jarvis..."
    )

    logging.info(
        "Creator: Anas"
    )

    logging.info(
        "Gemini: %s",
        GEMINI_MODEL
    )

    logging.info(
        "Groq: %s",
        GROQ_MODEL
    )

    logging.info(
        "Tavily: %s",
        "configured"
        if TAVILY_API_KEY
        else "missing"
    )

    app.run(
        host="0.0.0.0",
        port=port
    )