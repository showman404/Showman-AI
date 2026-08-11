import os
import re
import json
import base64
import logging
import requests
import tempfile
import uuid
import wave
import threading
import time

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
# ENVIRONMENT VARIABLES
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

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

TAVILY_API_KEY = os.getenv(
    "TAVILY_API_KEY",
    ""
).strip()

GOOGLE_MAPS_API_KEY = os.getenv(
    "GOOGLE_MAPS_API_KEY",
    ""
).strip()

ADMIN_ID = os.getenv(
    "ADMIN_ID",
    ""
).strip()

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    ""
).strip().rstrip("/")

SUPABASE_KEY = (
    os.getenv("SUPABASE_SECRET_KEY", "").strip()
    or
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

VOICE_REPLY_ENABLED = (
    os.getenv(
        "VOICE_REPLY_ENABLED",
        "true"
    ).strip().lower()
    not in {"0", "false", "no", "off"}
)


# ============================================================
# CONSTANTS
# ============================================================

MESSENGER_SEND_URL = (
    "https://graph.facebook.com/v20.0/me/messages"
)

MAX_HISTORY_MESSAGES = 30
MAX_MEMORY_ITEMS = 20
REQUEST_TIMEOUT = 60

VOICE_DIR = os.path.join(
    tempfile.gettempdir(),
    "jarvis_voice"
)

os.makedirs(
    VOICE_DIR,
    exist_ok=True
)


# ============================================================
# IMPORTANT:
# USER LOCATION MEMORY
# ============================================================

user_locations = {}


# ============================================================
# VOICE LOCKS
#
# Prevents:
# Voice 1
# Voice 2
# Voice 3
#
# from being processed simultaneously.
# ============================================================

user_voice_locks = {}
user_voice_locks_guard = threading.Lock()


def get_voice_lock(sender_id):
    sender_id = str(sender_id)

    with user_voice_locks_guard:

        if sender_id not in user_voice_locks:
            user_voice_locks[sender_id] = threading.Lock()

        return user_voice_locks[sender_id]


# ============================================================
# MESSAGE DEDUPLICATION
#
# Messenger may retry webhook events.
# This prevents the same message from being processed twice.
# ============================================================

processed_message_ids = set()
processed_message_ids_guard = threading.Lock()

MAX_PROCESSED_IDS = 5000


def is_duplicate_message(message_id):

    if not message_id:
        return False

    with processed_message_ids_guard:

        if message_id in processed_message_ids:
            return True

        processed_message_ids.add(message_id)

        if len(processed_message_ids) > MAX_PROCESSED_IDS:

            old_items = list(
                processed_message_ids
            )[:1000]

            for item in old_items:
                processed_message_ids.discard(item)

    return False


# ============================================================
# TIME
# ============================================================

def now_iso():

    from datetime import datetime, timezone

    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# JARVIS PERSONALITY
# ============================================================

SYSTEM_PROMPT = """
তুমি JARVIS — Anas-এর তৈরি ব্যক্তিগত AI assistant।

তোমার Creator / নির্মাতা:
Anas

অত্যন্ত গুরুত্বপূর্ণ:

1. তোমাকে Anas তৈরি করেছেন।
2. কেউ জিজ্ঞেস করলে কে তোমাকে তৈরি করেছে,
   উত্তর দেবে:
   "আমাকে Anas তৈরি করেছেন।"
3. Google, Gemini, Groq, Meta, Facebook বা অন্য কোনো
   কোম্পানিকে তোমার creator বলবে না।
4. Gemini এবং Groq শুধু AI engine/provider।
5. তুমি নিজের নাম JARVIS হিসেবে পরিচয় দেবে।
6. ব্যবহারকারীর saved memory এবং conversation context ব্যবহার করবে।
7. তথ্য না জানলে বানিয়ে বলবে না।
8. User বাংলা ভাষায় কথা বললে বাংলায় উত্তর দেবে।
9. User English-এ কথা বললে English-এ উত্তর দেবে।
10. Bangla + English মিশ্রিত কথাও বুঝতে চেষ্টা করবে।
11. স্বাভাবিক, বন্ধুসুলভ ও স্মার্টভাবে উত্তর দেবে।
12. Admin/Boss হলেন Anas।
13. Admin-এর সাথে সম্মানজনক কিন্তু বন্ধুসুলভভাবে কথা বলবে।
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

    url = (
        f"{SUPABASE_URL}/rest/v1/{table}"
    )

    response = requests.request(
        method,
        url,
        headers=supabase_headers(),
        params=params,
        json=json_data,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:

        logging.error(
            "Supabase %s error: %s",
            table,
            response.text[:1000]
        )

    return response


# ============================================================
# MEMORY HELPERS
# ============================================================

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

        if role == "model":
            return "assistant"

        return role

    return "user"


# ============================================================
# SAVE CHAT MESSAGE
#
# IMPORTANT:
# jarvis_messages uses:
# user_id
# role
# message
# ============================================================

def save_message(
    sender_id,
    role,
    content
):

    if not sender_id or not content:
        return False

    payload = {

        "user_id": str(sender_id),

        "role": role,

        "message": str(content)

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


# ============================================================
# GET CONVERSATION HISTORY
# ============================================================

def get_conversation_history(
    sender_id
):

    if not sender_id:
        return []

    params = {

        "user_id": f"eq.{sender_id}",

        "order": "created_at.asc",

        "limit": str(
            MAX_HISTORY_MESSAGES
        )
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

        return history[
            -MAX_HISTORY_MESSAGES:
        ]

    except Exception as e:

        logging.error(
            "History error: %s",
            e
        )

        return []


# ============================================================
# LONG TERM MEMORY
#
# jarvis_memories uses:
# user_id
# memory
# ============================================================

def save_long_term_memory(
    sender_id,
    memory
):

    if not sender_id or not memory:
        return False

    payload = {

        "user_id": str(sender_id),

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
            "Long-term memory save error: %s",
            e
        )

        return False


def get_long_term_memories(
    sender_id
):

    if not sender_id:
        return []

    params = {

        "user_id": f"eq.{sender_id}",

        "order": "created_at.desc",

        "limit": str(
            MAX_MEMORY_ITEMS
        )
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


def clear_memories(
    sender_id
):

    if not sender_id or not SUPABASE_KEY:
        return False

    try:

        response = supabase_request(
            "DELETE",
            "jarvis_memories",
            params={
                "user_id":
                    f"eq.{sender_id}"
            }
        )

        return response.ok

    except Exception as e:

        logging.error(
            "Clear memories error: %s",
            e
        )

        return False


# ============================================================
# MEMORY DETECTION
# ============================================================

def should_save_memory(text):

    lowered = (
        text or ""
    ).lower()

    keywords = [

        "মনে রাখো",
        "মনে রাখবে",
        "মনে রাখ",
        "ভুলবে না",

        "remember this",
        "remember that",
        "remember",

        "save this",
        "store this"

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
        "store this"

    ]

    result = (
        text or ""
    ).strip()

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

    lowered = (
        text or ""
    ).lower()

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
        "your maker"

    ]

    return any(
        word in lowered
        for word in keywords
    )


def creator_answer():

    return (
        "আমাকে Anas তৈরি করেছেন। ❤️\n\n"
        "Gemini ও Groq আমার AI engine হিসেবে কাজ করে, "
        "কিন্তু আমার নির্মাতা Anas।"
    )


# ============================================================
# BUILD AI CONTEXT
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
                else "JARVIS"
            )

            parts.append(
                f"{label}: "
                f"{item['content']}"
            )

    parts.append(
        "\nCurrent user message:"
    )

    parts.append(
        user_text
    )

    return "\n".join(parts)


# ============================================================
# GEMINI TEXT
# ============================================================

def ask_gemini(prompt):

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "GEMINI_API_KEY missing"
        )

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    headers = {

        "Content-Type":
            "application/json",

        "x-goog-api-key":
            GEMINI_API_KEY
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

            "maxOutputTokens": 1024,

            "temperature": 0.7

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
# GROQ
# ============================================================

def groq_headers():

    return {

        "Content-Type":
            "application/json",

        "Authorization":
            f"Bearer {GROQ_API_KEY}"

    }


def get_groq_models():

    if not GROQ_API_KEY:
        return []

    try:

        response = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers=groq_headers(),
            timeout=REQUEST_TIMEOUT
        )

        if not response.ok:
            return []

        return [

            item.get("id")

            for item in response.json().get(
                "data",
                []
            )

            if item.get("id")

        ]

    except Exception as e:

        logging.error(
            "Groq models error: %s",
            e
        )

        return []


def resolve_groq_model():

    models = get_groq_models()

    if (
        GROQ_MODEL
        and (
            not models
            or GROQ_MODEL in models
        )
    ):

        return GROQ_MODEL

    preferred = [

        "llama-3.3-70b-versatile",

        "llama-3.1-8b-instant"

    ]

    for model in preferred:

        if model in models:
            return model

    return (
        models[0]
        if models
        else GROQ_MODEL
    )


def ask_groq(prompt):

    if not GROQ_API_KEY:

        raise RuntimeError(
            "GROQ_API_KEY missing"
        )

    model = resolve_groq_model()

    payload = {

        "model": model,

        "messages": [

            {

                "role": "system",

                "content":
                    SYSTEM_PROMPT

            },

            {

                "role": "user",

                "content":
                    prompt

            }

        ],

        "temperature": 0.5,

        "max_tokens": 1200

    }

    response = requests.post(

        "https://api.groq.com/openai/v1/chat/completions",

        headers=groq_headers(),

        json=payload,

        timeout=REQUEST_TIMEOUT

    )

    if not response.ok:

        raise RuntimeError(

            f"Groq HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"

        )

    choices = response.json().get(
        "choices",
        []
    )

    if not choices:

        raise RuntimeError(
            "Groq returned no choices"
        )

    answer = (
        choices[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    if not answer:

        raise RuntimeError(
            "Groq returned empty response"
        )

    logging.info(
        "AI provider: Groq / %s",
        model
    )

    return answer


# ============================================================
# TAVILY SEARCH
# ============================================================

def tavily_search(query):

    if not TAVILY_API_KEY:

        raise RuntimeError(
            "TAVILY_API_KEY missing"
        )

    response = requests.post(

        "https://api.tavily.com/search",

        json={

            "api_key":
                TAVILY_API_KEY,

            "query":
                query,

            "search_depth":
                "advanced",

            "topic":
                "general",

            "max_results":
                5,

            "include_answer":
                False,

            "include_raw_content":
                False

        },

        timeout=REQUEST_TIMEOUT

    )

    if not response.ok:

        raise RuntimeError(

            f"Tavily HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"

        )

    results = response.json().get(
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

                "title":
                    title,

                "url":
                    url,

                "content":
                    content

            })

    return cleaned


# ============================================================
# WEATHER
# ============================================================

def is_weather_request(text):

    t = (
        text or ""
    ).lower()

    return any(

        keyword in t

        for keyword in [

            "weather",
            "আবহাওয়া",
            "আবহাওয়া",
            "তাপমাত্রা",
            "temperature",
            "বৃষ্টি হবে"

        ]

    )


def weather_reply(place):

    geo = requests.get(

        "https://geocoding-api.open-meteo.com/v1/search",

        params={

            "name":
                place,

            "count":
                1,

            "language":
                "en",

            "format":
                "json"

        },

        timeout=REQUEST_TIMEOUT

    )

    if not geo.ok:

        raise RuntimeError(
            "Weather location search failed"
        )

    rows = geo.json().get(
        "results",
        []
    )

    if not rows:

        raise RuntimeError(
            "Location not found"
        )

    loc = rows[0]

    weather = requests.get(

        "https://api.open-meteo.com/v1/forecast",

        params={

            "latitude":
                loc["latitude"],

            "longitude":
                loc["longitude"],

            "current":
                (
                    "temperature_2m,"
                    "relative_humidity_2m,"
                    "apparent_temperature,"
                    "precipitation,"
                    "wind_speed_10m"
                ),

            "timezone":
                "auto"

        },

        timeout=REQUEST_TIMEOUT

    )

    if not weather.ok:

        raise RuntimeError(
            "Weather request failed"
        )

    current = weather.json().get(
        "current",
        {}
    )

    return (

        f"🌤️ {loc.get('name', '')}, "
        f"{loc.get('country', '')}\n\n"

        f"🌡️ তাপমাত্রা: "
        f"{current.get('temperature_2m')}°C\n"

        f"🤒 অনুভূত: "
        f"{current.get('apparent_temperature')}°C\n"

        f"💧 আর্দ্রতা: "
        f"{current.get('relative_humidity_2m')}%\n"

        f"🌧️ বৃষ্টিপাত: "
        f"{current.get('precipitation')} mm\n"

        f"💨 বাতাস: "
        f"{current.get('wind_speed_10m')} km/h"

    )


# ============================================================
# TRAFFIC
# ============================================================

TRAFFIC_KEYWORDS = [

    "জ্যাম",
    "ট্রাফিক",
    "traffic",
    "traffic jam",
    "রাস্তার অবস্থা"

]


def is_traffic_question(text):

    t = (
        text or ""
    ).lower()

    return any(

        keyword in t

        for keyword in TRAFFIC_KEYWORDS

    )


def save_location(
    sender_id,
    lat,
    lng
):

    user_locations[
        str(sender_id)
    ] = {

        "latitude":
            float(lat),

        "longitude":
            float(lng)

    }

    if SUPABASE_KEY:

        try:

            supabase_request(

                "POST",

                "jarvis_locations",

                json_data={

                    "sender_id":
                        str(sender_id),

                    "latitude":
                        float(lat),

                    "longitude":
                        float(lng),

                    "updated_at":
                        now_iso()

                }

            )

        except Exception as e:

            logging.error(
                "Location save error: %s",
                e
            )


def get_saved_location(
    sender_id
):

    key = str(sender_id)

    if key in user_locations:

        return user_locations[key]

    if not SUPABASE_KEY:

        return None

    try:

        response = supabase_request(

            "GET",

            "jarvis_locations",

            params={

                "sender_id":
                    f"eq.{sender_id}",

                "order":
                    "updated_at.desc",

                "limit":
                    "1"

            }

        )

        if not response.ok:

            return None

        rows = response.json()

        if rows:

            location = {

                "latitude":
                    rows[0]["latitude"],

                "longitude":
                    rows[0]["longitude"]

            }

            user_locations[key] = location

            return location

    except Exception as e:

        logging.error(
            "Location read error: %s",
            e
        )

    return None


def extract_destination(text):

    generic = {

        "জ্যাম",
        "আছে",
        "কি",
        "কিনা",
        "কত",
        "ট্রাফিক",
        "বল",
        "দেখ",
        "দেখো",
        "জানাও",
        "রাস্তা",
        "রাস্তায়",
        "রাস্তায়",
        "traffic",
        "jam",
        "traffic jam"

    }

    words = (
        text or ""
    ).split()

    result = []

    for word in words:

        clean = word.strip(
            ".,!?।"
        )

        if clean.lower() not in generic:

            result.append(clean)

    return " ".join(result).strip()


def get_traffic(
    lat,
    lng,
    destination
):

    if not GOOGLE_MAPS_API_KEY:

        raise RuntimeError(
            "GOOGLE_MAPS_API_KEY missing"
        )

    response = requests.post(

        "https://routes.googleapis.com/"
        "directions/v2:computeRoutes",

        headers={

            "Content-Type":
                "application/json",

            "X-Goog-Api-Key":
                GOOGLE_MAPS_API_KEY,

            "X-Goog-FieldMask":
                (
                    "routes.duration,"
                    "routes.staticDuration,"
                    "routes.distanceMeters"
                )

        },

        json={

            "origin": {

                "location": {

                    "latLng": {

                        "latitude":
                            float(lat),

                        "longitude":
                            float(lng)

                    }

                }

            },

            "destination": {

                "address":
                    destination

            },

            "travelMode":
                "DRIVE",

            "routingPreference":
                "TRAFFIC_AWARE_OPTIMAL",

            "computeAlternativeRoutes":
                False,

            "languageCode":
                "bn-BD",

            "units":
                "METRIC"

        },

        timeout=REQUEST_TIMEOUT

    )

    if not response.ok:

        raise RuntimeError(

            f"Google Routes HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"

        )

    routes = response.json().get(
        "routes",
        []
    )

    if not routes:

        raise RuntimeError(
            "এই রুটের কোনো তথ্য পাওয়া যায়নি।"
        )

    return routes[0]


def traffic_reply(
    route,
    destination
):

    def seconds(value):

        try:

            return float(
                str(value).replace(
                    "s",
                    ""
                )
            )

        except Exception:

            return 0

    traffic_seconds = seconds(
        route.get("duration")
    )

    normal_seconds = seconds(
        route.get("staticDuration")
    )

    delay = max(

        0,

        traffic_seconds
        - normal_seconds

    ) if normal_seconds else 0

    if delay <= 120:

        status = (
            "🟢 খুব বেশি জ্যাম নেই।"
        )

    elif delay <= 600:

        status = (
            "🟡 হালকা থেকে মাঝারি জ্যাম আছে।"
        )

    elif delay <= 1200:

        status = (
            "🟠 বেশ ভালো জ্যাম আছে।"
        )

    else:

        status = (
            "🔴 অনেক বেশি জ্যাম আছে।"
        )

    distance_km = (
        float(
            route.get(
                "distanceMeters",
                0
            )
        ) / 1000
    )

    minutes = round(
        traffic_seconds / 60
    )

    return (

        "🚦 ট্রাফিক রিপোর্ট\n\n"

        f"📍 গন্তব্য: {destination}\n"

        f"🛣️ দূরত্ব: "
        f"{distance_km:.1f} কিমি\n"

        f"⏱️ বর্তমান সময়: "
        f"{minutes} মিনিট\n"

        f"{status}\n\n"

        "Google Routes-এর traffic-aware "
        "data অনুযায়ী রিপোর্ট।"

    )


# ============================================================
# BOSS PHOTO
# ============================================================

def is_admin(sender_id):

    return (
        bool(ADMIN_ID)
        and
        str(sender_id) == str(ADMIN_ID)
    )


def is_boss_photo_registration(
    text
):

    t = (
        text or ""
    ).lower()

    return any(

        phrase in t

        for phrase in [

            "save as boss",
            "save this as boss",
            "boss photo save",

            "বসের ছবি হিসেবে রাখ",
            "বসের ছবি হিসেবে সেভ",
            "এটা বসের ছবি",
            "এটা আমার ছবি",
            "এটা আমি"

        ]

    )


def is_boss_photo_request(
    text
):

    t = (
        text or ""
    ).lower()

    return any(

        phrase in t

        for phrase in [

            "বসের ছবি",
            "বস এর ছবি",
            "বসের ফটো",
            "বস এর ফটো",
            "বসের পিক",

            "boss photo",
            "boss picture",
            "boss pic",

            "show me your boss",
            "show your boss",

            "তোমার বসের ছবি"

        ]

    )


def upload_boss_photo(
    image_url
):

    if (
        not SUPABASE_URL
        or not SUPABASE_KEY
    ):

        raise RuntimeError(
            "Supabase configuration missing"
        )

    image = requests.get(
        image_url,
        timeout=REQUEST_TIMEOUT
    )

    if not image.ok:

        raise RuntimeError(
            "Boss photo download failed"
        )

    filename = (
        f"boss_{uuid.uuid4().hex}.jpg"
    )

    content_type = (
        image.headers.get(
            "Content-Type",
            "image/jpeg"
        )
    )

    upload = requests.post(

        f"{SUPABASE_URL}"
        f"/storage/v1/object/"
        f"boss-photos/{filename}",

        headers={

            "Authorization":
                f"Bearer {SUPABASE_KEY}",

            "apikey":
                SUPABASE_KEY,

            "Content-Type":
                content_type,

            "x-upsert":
                "true"

        },

        data=image.content,

        timeout=REQUEST_TIMEOUT

    )

    if not upload.ok:

        raise RuntimeError(
            "Boss photo upload failed"
        )

    public_url = (

        f"{SUPABASE_URL}"
        f"/storage/v1/object/public/"
        f"boss-photos/{filename}"

    )

    database = supabase_request(

        "POST",

        "boss_photos",

        json_data={

            "photo_url":
                public_url,

            "label":
                "Anas"

        }

    )

    if not database.ok:

        raise RuntimeError(
            "Boss photo DB save failed"
        )

    return public_url


def get_boss_photo():

    if not SUPABASE_KEY:
        return None

    try:

        response = supabase_request(

            "GET",

            "boss_photos",

            params={

                "select":
                    "photo_url,label,created_at",

                "order":
                    "created_at.desc",

                "limit":
                    "1"

            }

        )

        if not response.ok:

            return None

        rows = response.json()

        if rows:

            return rows[0].get(
                "photo_url"
            )

    except Exception as e:

        logging.error(
            "Boss photo error: %s",
            e
        )

    return None


def send_image_message(
    recipient_id,
    image_url
):

    try:

        response = requests.post(

            MESSENGER_SEND_URL,

            params={

                "access_token":
                    PAGE_ACCESS_TOKEN

            },

            json={

                "recipient": {

                    "id":
                        recipient_id

                },

                "message": {

                    "attachment": {

                        "type":
                            "image",

                        "payload": {

                            "url":
                                image_url,

                            "is_reusable":
                                True

                        }

                    }

                }

            },

            timeout=REQUEST_TIMEOUT

        )

        return response.ok

    except Exception as e:

        logging.error(
            "Image send error: %s",
            e
        )

        return False


# ============================================================
# VOICE INPUT
#
# IMPORTANT:
# Bengali is explicitly selected.
# ============================================================

def transcribe_audio(
    audio_url
):

    if not GROQ_API_KEY:

        raise RuntimeError(
            "GROQ_API_KEY missing"
        )

    audio = requests.get(

        audio_url,

        timeout=REQUEST_TIMEOUT

    )

    if not audio.ok:

        raise RuntimeError(
            "Audio download failed"
        )

    content_type = (
        audio.headers.get(
            "Content-Type",
            "audio/ogg"
        )
    )

    extension = ".ogg"

    if "mp4" in content_type:
        extension = ".m4a"

    elif "m4a" in content_type:
        extension = ".m4a"

    elif "webm" in content_type:
        extension = ".webm"

    elif (
        "mpeg" in content_type
        or
        "mp3" in content_type
    ):

        extension = ".mp3"

    response = requests.post(

        "https://api.groq.com/"
        "openai/v1/audio/transcriptions",

        headers={

            "Authorization":
                f"Bearer {GROQ_API_KEY}"

        },

        files={

            "file": (

                f"jarvis_voice{extension}",

                audio.content,

                content_type

            )

        },

        data={

            "model":
                "whisper-large-v3-turbo",

            # FORCE BENGALI
            "language":
                "bn",

            "response_format":
                "json",

            "temperature":
                "0",

            "prompt":
                (
                    "এটি একটি বাংলা ভাষার "
                    "কথোপকথন। বাংলা শব্দ, "
                    "বাংলা নাম এবং বাংলা "
                    "উচ্চারণ সঠিকভাবে "
                    "transcribe করো। "
                    "প্রয়োজনে English শব্দ "
                    "যেমন JARVIS, Anas, "
                    "Google, Gemini, Groq "
                    "অপরিবর্তিত রাখো।"
                )

        },

        timeout=120

    )

    if not response.ok:

        raise RuntimeError(

            f"Whisper HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"

        )

    text = response.json().get(
        "text",
        ""
    ).strip()

    if not text:

        raise RuntimeError(
            "Voice transcription empty"
        )

    logging.info(
        "VOICE TRANSCRIPTION: %s",
        text
    )

    return text


# ============================================================
# VOICE OUTPUT
# ============================================================

def write_pcm_wav(
    filename,
    pcm_data,
    channels=1,
    rate=24000,
    sample_width=2
):

    with wave.open(
        filename,
        "wb"
    ) as wf:

        wf.setnchannels(
            channels
        )

        wf.setsampwidth(
            sample_width
        )

        wf.setframerate(
            rate
        )

        wf.writeframes(
            pcm_data
        )


def generate_jarvis_voice(
    text
):

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "GEMINI_API_KEY missing"
        )

    if not VOICE_REPLY_ENABLED:

        raise RuntimeError(
            "Voice reply disabled"
        )

    from google import genai

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    interaction = client.interactions.create(

        model=GEMINI_TTS_MODEL,

        input=(

            "Speak naturally in Bengali "
            "as JARVIS, a calm and "
            "confident personal AI assistant. "

            "Use a warm, clear, friendly "
            "male assistant style. "

            "Do not add extra words. "

            "Read exactly this response:\n"
            f"{text}"

        ),

        response_format={
            "type": "audio"
        },

        generation_config={

            "speech_config": [

                {
                    "voice":
                        "Kore"
                }

            ]

        }

    )

    audio = getattr(
        interaction,
        "output_audio",
        None
    )

    audio_data = (
        getattr(
            audio,
            "data",
            None
        )
        if audio
        else None
    )

    if not audio_data:

        raise RuntimeError(
            "Gemini TTS returned no audio"
        )

    pcm_data = base64.b64decode(
        audio_data
    )

    filename = (
        f"jarvis_{uuid.uuid4().hex}.wav"
    )

    filepath = os.path.join(
        VOICE_DIR,
        filename
    )

    write_pcm_wav(
        filepath,
        pcm_data
    )

    logging.info(
        "JARVIS voice generated: %s",
        filename
    )

    return filename


def send_voice_message(
    recipient_id,
    text
):

    try:

        filename = generate_jarvis_voice(
            text
        )

        base_url = (
            os.getenv(
                "RENDER_EXTERNAL_URL",
                ""
            )
            .strip()
            .rstrip("/")
        )

        if not base_url:

            raise RuntimeError(
                "RENDER_EXTERNAL_URL missing"
            )

        audio_url = (
            f"{base_url}/voice/{filename}"
        )

        response = requests.post(

            MESSENGER_SEND_URL,

            params={

                "access_token":
                    PAGE_ACCESS_TOKEN

            },

            json={

                "recipient": {

                    "id":
                        recipient_id

                },

                "message": {

                    "attachment": {

                        "type":
                            "audio",

                        "payload": {

                            "url":
                                audio_url,

                            "is_reusable":
                                True

                        }

                    }

                }

            },

            timeout=REQUEST_TIMEOUT

        )

        if not response.ok:

            logging.error(
                "Messenger voice error: %s",
                response.text[:1000]
            )

            return False

        return True

    except Exception as e:

        logging.error(
            "Voice reply error: %s",
            e
        )

        return False


# ============================================================
# MESSENGER TEXT
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

    max_length = 1800

    chunks = []

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

        try:

            response = requests.post(

                MESSENGER_SEND_URL,

                params={

                    "access_token":
                        PAGE_ACCESS_TOKEN

                },

                json={

                    "recipient": {

                        "id":
                            recipient_id

                    },

                    "message": {

                        "text":
                            chunk

                    }

                },

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
# ADMIN COMMANDS
# ============================================================

def admin_command(
    sender_id,
    text
):

    if not is_admin(sender_id):
        return None

    command = (
        text or ""
    ).strip().lower()

    if command in (
        "/help",
        "jarvis help",
        "জারভিস হেল্প"
    ):

        return (

            "👑 JARVIS Admin Commands\n\n"

            "/help\n"
            "/status\n"
            "/models\n"
            "/memory\n"
            "/forget all\n"
            "CONFIRM FORGET ALL\n\n"

            "Boss photo: "
            "ছবি পাঠিয়ে "
            "\"save as boss\" লিখুন।"

        )

    if command in (
        "/status",
        "jarvis status"
    ):

        return (

            "🟢 JARVIS STATUS\n\n"

            f"Gemini: "
            f"{'ON' if GEMINI_API_KEY else 'OFF'}\n"

            f"Gemini Model: "
            f"{GEMINI_MODEL}\n\n"

            f"Groq: "
            f"{'ON' if GROQ_API_KEY else 'OFF'}\n"

            f"Groq Model: "
            f"{resolve_groq_model() if GROQ_API_KEY else 'N/A'}\n\n"

            f"Whisper Voice: "
            f"{'ON' if GROQ_API_KEY else 'OFF'}\n"

            "Voice Language: Bengali (bn)\n\n"

            f"TTS: "
            f"{'ON' if GEMINI_API_KEY and VOICE_REPLY_ENABLED else 'OFF'}\n"

            f"Tavily: "
            f"{'ON' if TAVILY_API_KEY else 'OFF'}\n"

            f"Maps: "
            f"{'ON' if GOOGLE_MAPS_API_KEY else 'OFF'}\n"

            f"Supabase: "
            f"{'ON' if SUPABASE_KEY else 'OFF'}\n"

            f"ADMIN_ID: "
            f"{'SET' if ADMIN_ID else 'MISSING'}"

        )

    if command in (
        "/models",
        "jarvis models"
    ):

        models = get_groq_models()

        if not models:

            return (
                "Groq model list পাওয়া যায়নি।"
            )

        return (

            "⚡ Active Groq models:\n\n"

            + "\n".join(
                f"• {model}"
                for model in models[:30]
            )

        )

    if command in (
        "/memory",
        "jarvis memory"
    ):

        memories = get_long_term_memories(
            sender_id
        )

        if not memories:

            return (
                "🧠 কোনো saved memory নেই।"
            )

        return (

            "🧠 Saved memory:\n\n"

            + "\n".join(

                f"{index}. {memory}"

                for index, memory
                in enumerate(
                    memories,
                    1
                )

            )

        )

    if command in (
        "/forget all",
        "forget all",
        "/clear memory"
    ):

        return (
            "⚠️ সব memory মুছতে হলে "
            "লিখুন:\n\n"
            "CONFIRM FORGET ALL"
        )

    if command == "confirm forget all":

        if clear_memories(
            sender_id
        ):

            return (
                "🧹 সব memory মুছে দেওয়া হয়েছে।"
            )

        return (
            "Memory delete করা যায়নি।"
        )

    return None


# ============================================================
# PUBLIC VOICE FILES
# ============================================================

@app.route(
    "/voice/<path:filename>",
    methods=["GET"]
)
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
        and
        token == VERIFY_TOKEN
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

            # ------------------------------------------------
            # DUPLICATE EVENT PROTECTION
            # ------------------------------------------------

            message = event.get(
                "message",
                {}
            )

            message_id = message.get(
                "mid"
            )

            if is_duplicate_message(
                message_id
            ):

                logging.info(
                    "Duplicate message ignored: %s",
                    message_id
                )

                continue

            # ------------------------------------------------
            # VOICE LOCK
            #
            # Important:
            # One user's voice request is processed
            # completely before the next one.
            # ------------------------------------------------

            attachments = message.get(
                "attachments",
                []
            )

            has_audio = any(

                attachment.get("type")
                in ("audio", "file")

                for attachment
                in attachments

            )

            voice_lock = (
                get_voice_lock(sender_id)
                if has_audio
                else None
            )

            if voice_lock:

                logging.info(
                    "Waiting for voice lock: %s",
                    sender_id
                )

                with voice_lock:

                    process_messenger_event(
                        sender_id,
                        event
                    )

            else:

                process_messenger_event(
                    sender_id,
                    event
                )

    return "OK", 200


# ============================================================
# PROCESS MESSENGER EVENT
# ============================================================

def process_messenger_event(
    sender_id,
    event
):

    message = event.get(
        "message",
        {}
    )

    attachments = message.get(
        "attachments",
        []
    )


    # ========================================================
    # LOCATION
    # ========================================================

    for attachment in attachments:

        if attachment.get(
            "type"
        ) == "location":

            coords = (
                attachment
                .get("payload", {})
                .get("coordinates", {})
            )

            lat = coords.get(
                "lat"
            )

            lng = coords.get(
                "long"
            )

            if (
                lat is not None
                and
                lng is not None
            ):

                save_location(
                    sender_id,
                    lat,
                    lng
                )

                send_message(

                    sender_id,

                    "📍 তোমার Location পেয়েছি!\n\n"
                    "এখন destination লিখে পাঠাও।\n\n"
                    "যেমন:\n"
                    "➡️ Dhanmondi\n"
                    "➡️ Gulshan 1\n"
                    "➡️ Airport"

                )


    # ========================================================
    # TEXT
    # ========================================================

    user_text = (
        message.get(
            "text",
            ""
        )
        or ""
    ).strip()


    # ========================================================
    # AUDIO / VOICE
    # ========================================================

    audio_url = None

    for attachment in attachments:

        if attachment.get(
            "type"
        ) in (
            "audio",
            "file"
        ):

            audio_url = (
                attachment
                .get("payload", {})
                .get("url")
            )

            if audio_url:
                break


    voice_input = False

    if (
        audio_url
        and
        not user_text
    ):

        voice_input = True

        logging.info(
            "VOICE INPUT received from %s",
            sender_id
        )

        try:

            user_text = transcribe_audio(
                audio_url
            )

        except Exception as e:

            logging.error(
                "Voice transcription error: %s",
                e
            )

            send_message(

                sender_id,

                "🎙️ বস, তোমার voice message "
                "ঠিকমতো বুঝতে পারিনি। "
                "আরেকবার একটু পরিষ্কার করে বলো।"

            )

            return


    # ========================================================
    # IMAGE
    # ========================================================

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

        # ----------------------------------------------------
        # BOSS PHOTO
        # ----------------------------------------------------

        if (
            is_admin(sender_id)
            and
            is_boss_photo_registration(
                user_text
            )
        ):

            try:

                upload_boss_photo(
                    image_attachment
                )

                answer = (
                    "ঠিক আছে বস ❤️\n"
                    "এই ছবিটা এখন থেকে "
                    "আপনার Boss/Anas photo হিসেবে "
                    "save করে রাখলাম।"
                )

            except Exception as e:

                logging.error(
                    "Boss photo save error: %s",
                    e
                )

                answer = (
                    "বস, ছবিটা পেয়েছি কিন্তু "
                    "Supabase-এ save করতে পারিনি।\n\n"
                    f"Error: {str(e)[:250]}"
                )

            save_message(
                sender_id,
                "user",
                "[Boss photo registration]"
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

            return


        # ----------------------------------------------------
        # BOSS PHOTO REQUEST
        # ----------------------------------------------------

        if is_boss_photo_request(
            user_text
        ):

            boss_photo = get_boss_photo()

            if boss_photo:

                send_image_message(
                    sender_id,
                    boss_photo
                )

            else:

                send_message(

                    sender_id,

                    "বসের কোনো ছবি এখনো "
                    "save করা হয়নি।"

                )

            return


        # ----------------------------------------------------
        # OTHER IMAGE
        # ----------------------------------------------------

        send_message(

            sender_id,

            "🖼️ ছবিটা পেয়েছি। "
            "Image analysis feature সক্রিয় করতে "
            "Gemini Vision processing ব্যবহার করা যাবে।"

        )

        return


    # ========================================================
    # NO TEXT
    # ========================================================

    if not user_text:

        return


    logging.info(
        "Message from %s: %s",
        sender_id,
        user_text
    )


    # ========================================================
    # SAVE USER MESSAGE
    # ========================================================

    save_message(
        sender_id,
        "user",
        user_text
    )


    # ========================================================
    # LONG TERM MEMORY
    # ========================================================

    if should_save_memory(
        user_text
    ):

        memory = clean_memory(
            user_text
        )

        if memory:

            saved = save_long_term_memory(

                sender_id,

                memory

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

                send_voice_message(
                    sender_id,
                    answer
                )

            return


    # ========================================================
    # CREATOR
    # ========================================================

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

            send_voice_message(
                sender_id,
                answer
            )

        return


    # ========================================================
    # ADMIN
    # ========================================================

    admin_answer = admin_command(
        sender_id,
        user_text
    )

    if admin_answer:

        save_message(
            sender_id,
            "assistant",
            admin_answer
        )

        send_message(
            sender_id,
            admin_answer
        )

        if voice_input:

            send_voice_message(
                sender_id,
                admin_answer
            )

        return


    # ========================================================
    # WEATHER
    # ========================================================

    if is_weather_request(
        user_text
    ):

        place = re.sub(

            r"(আজকের|আজ|weather|"
            r"আবহাওয়া|আবহাওয়া|"
            r"তাপমাত্রা|temperature|"
            r"কেমন|কত|এর|তে|এ)",

            " ",

            user_text,

            flags=re.IGNORECASE

        )

        place = re.sub(
            r"\s+",
            " ",
            place
        ).strip()

        if len(place) < 2:

            send_message(

                sender_id,

                "🌤️ কোন জায়গার weather "
                "জানতে চাও?\n\n"
                "যেমন: Dhaka"

            )

            return

        try:

            answer = weather_reply(
                place
            )

        except Exception as e:

            answer = (
                "আবহাওয়ার তথ্য আনতে "
                "সমস্যা হয়েছে।\n\n"
                f"Error: {str(e)[:250]}"
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

            send_voice_message(
                sender_id,
                answer
            )

        return


    # ========================================================
    # TRAFFIC
    # ========================================================

    if is_traffic_question(
        user_text
    ):

        location = get_saved_location(
            sender_id
        )

        if not location:

            send_message(

                sender_id,

                "🚦 Traffic দেখতে তোমার "
                "current Location দরকার।\n\n"
                "Messenger-এর Location option "
                "থেকে location পাঠাও।"

            )

            return


        destination = extract_destination(
            user_text
        )


        if len(destination) < 3:

            send_message(

                sender_id,

                "📍 Destination লিখে পাঠাও।\n\n"
                "যেমন:\n"
                "➡️ Dhanmondi\n"
                "➡️ Gulshan 1\n"
                "➡️ Airport"

            )

            return


        try:

            route = get_traffic(

                location["latitude"],

                location["longitude"],

                destination

            )

            answer = traffic_reply(

                route,

                destination

            )

        except Exception as e:

            logging.error(
                "Traffic error: %s",
                e
            )

            answer = (

                "🚦 Traffic data আনতে "
                "সমস্যা হয়েছে।\n\n"

                f"Error: {str(e)[:250]}"

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

            send_voice_message(
                sender_id,
                answer
            )

        return


    # ========================================================
    # NORMAL AI
    # ========================================================

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


    # ========================================================
    # SAVE AI ANSWER
    # ========================================================

    save_message(

        sender_id,

        "assistant",

        answer

    )


    # ========================================================
    # SEND TEXT
    # ========================================================

    send_message(

        sender_id,

        answer

    )


    # ========================================================
    # SEND VOICE
    # ========================================================

    if voice_input:

        send_voice_message(

            sender_id,

            answer

        )


# ============================================================
# HOME / HEALTH
# ============================================================

@app.route("/")
def home():

    return {

        "status":
            "running",

        "jarvis":
            "Showman-AI",

        "creator":
            "Anas",

        "gemini":
            GEMINI_MODEL,

        "groq":
            (
                resolve_groq_model()
                if GROQ_API_KEY
                else
                "not configured"
            ),

        "web_search":
            (
                "Tavily"
                if TAVILY_API_KEY
                else
                "not configured"
            ),

        "memory":
            (
                "Supabase"
                if SUPABASE_KEY
                else
                "not configured"
            ),

        "traffic":
            (
                "Google Routes"
                if GOOGLE_MAPS_API_KEY
                else
                "not configured"
            ),

        "weather":
            "Open-Meteo",

        "voice_input":
            (
                "Groq Whisper Bengali"
                if GROQ_API_KEY
                else
                "not configured"
            ),

        "voice_output":
            (
                GEMINI_TTS_MODEL
                if (
                    GEMINI_API_KEY
                    and
                    VOICE_REPLY_ENABLED
                )
                else
                "not configured"
            ),

        "voice_queue":
            "enabled",

        "voice_duplicate_protection":
            "enabled",

        "admin":
            (
                "configured"
                if ADMIN_ID
                else
                "MISSING"
            )

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
        "Starting JARVIS..."
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
        "Voice input: Bengali Whisper"
    )

    logging.info(
        "Voice queue: enabled"
    )

    logging.info(
        "Voice duplicate protection: enabled"
    )

    app.run(
        host="0.0.0.0",
        port=port
    )