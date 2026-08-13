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

from flask import Flask, request, jsonify, send_from_directory
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

# ============================================================
# MOBILE CONTROL
# ============================================================

MOBILE_CONTROL_TOKEN = os.getenv(
    "MOBILE_CONTROL_TOKEN",
    ""
).strip()

MOBILE_CONTROL_ENABLED = (
    os.getenv(
        "MOBILE_CONTROL_ENABLED",
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
# USER LOCATION
# ============================================================

user_locations = {}

# ============================================================
# VOICE LOCK
# ============================================================

user_voice_locks = {}
user_voice_locks_guard = threading.Lock()


def get_voice_lock(sender_id):

    sender_id = str(sender_id)

    with user_voice_locks_guard:

        if sender_id not in user_voice_locks:

            user_voice_locks[sender_id] = (
                threading.Lock()
            )

        return user_voice_locks[sender_id]


# ============================================================
# MESSAGE DEDUPLICATION
# ============================================================

processed_message_ids = set()

processed_message_ids_guard = (
    threading.Lock()
)

MAX_PROCESSED_IDS = 5000


def is_duplicate_message(message_id):

    if not message_id:
        return False

    with processed_message_ids_guard:

        if message_id in processed_message_ids:
            return True

        processed_message_ids.add(
            message_id
        )

        if (
            len(processed_message_ids)
            > MAX_PROCESSED_IDS
        ):

            old_items = list(
                processed_message_ids
            )[:1000]

            for item in old_items:

                processed_message_ids.discard(
                    item
                )

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

নিয়ম:

1. তোমাকে Anas তৈরি করেছেন।
2. কেউ জিজ্ঞেস করলে বলবে:
   "আমাকে Anas তৈরি করেছেন।"
3. Gemini, Groq, Meta, Facebook বা অন্য কোনো
   কোম্পানিকে creator বলবে না।
4. Gemini এবং Groq শুধু AI provider।
5. নিজের নাম JARVIS হিসেবে পরিচয় দেবে।
6. Saved memory এবং conversation context ব্যবহার করবে।
7. তথ্য না জানলে বানিয়ে বলবে না।
8. বাংলা প্রশ্ন হলে বাংলায় উত্তর দেবে।
9. English হলে English-এ উত্তর দেবে।
10. Bangla + English মিশ্রিত কথাও বুঝতে চেষ্টা করবে।
11. স্বাভাবিক, বন্ধুসুলভ ও স্মার্টভাবে উত্তর দেবে।
12. Anas হলেন Boss/Admin।
13. Boss-এর সাথে সম্মানজনকভাবে কথা বলবে।
14. Mobile command এলে command-এর উদ্দেশ্য বুঝে
    নিরাপদভাবে Android app-কে নির্দেশ দেওয়ার জন্য
    structured command তৈরি করতে পারবে।
15. কোনো অনুমতি ছাড়া ফোনের গোপন তথ্য নেওয়ার চেষ্টা করবে না।
"""


# ============================================================
# BASIC HEALTH
# ============================================================

@app.route("/")
def home():

    return jsonify({

        "status": "running",

        "jarvis": "Showman-AI",

        "creator": "Anas",

        "mobile_control":
            (
                "enabled"
                if MOBILE_CONTROL_ENABLED
                else "disabled"
            ),

        "memory":
            (
                "Supabase"
                if SUPABASE_KEY
                else "not configured"
            ),

        "gemini":
            GEMINI_MODEL,

        "groq":
            GROQ_MODEL

    }), 200


# ============================================================
# MOBILE AUTHENTICATION
# ============================================================

def mobile_authenticated():

    if not MOBILE_CONTROL_ENABLED:
        return False

    if not MOBILE_CONTROL_TOKEN:
        return False

    token = (
        request.headers
        .get("X-JARVIS-TOKEN", "")
        .strip()
    )

    if not token:

        auth = (
            request.headers
            .get("Authorization", "")
            .strip()
        )

        if auth.startswith("Bearer "):

            token = auth[
                len("Bearer "):
            ].strip()

    return (
        token
        and
        token == MOBILE_CONTROL_TOKEN
    )


def require_mobile_auth():

    if not MOBILE_CONTROL_ENABLED:

        return jsonify({

            "ok": False,

            "error":
                "Mobile control is disabled"

        }), 403

    if not MOBILE_CONTROL_TOKEN:

        return jsonify({

            "ok": False,

            "error":
                "Mobile control token is not configured"

        }), 503

    if not mobile_authenticated():

        return jsonify({

            "ok": False,

            "error":
                "Unauthorized"

        }), 401

    return None


# ============================================================
# MOBILE STATUS
# ============================================================

@app.route(
    "/api/mobile/status",
    methods=["GET"]
)
def mobile_status():

    auth_error = require_mobile_auth()

    if auth_error:
        return auth_error

    return jsonify({

        "ok": True,

        "jarvis": "online",

        "mobile_control": True,

        "creator": "Anas",

        "features": {

            "chat": True,

            "voice": True,

            "memory": bool(SUPABASE_KEY),

            "weather": True,

            "traffic":
                bool(GOOGLE_MAPS_API_KEY)

        }

    }), 200


# ============================================================
# MOBILE COMMAND VALIDATION
# ============================================================

ALLOWED_MOBILE_ACTIONS = {

    "open_app",

    "close_app",

    "send_text",

    "make_call",

    "open_url",

    "get_location",

    "get_device_info",

    "set_volume",

    "play_media",

    "pause_media",

    "take_screenshot"

}


def clean_mobile_command(data):

    if not isinstance(data, dict):
        return None

    action = str(
        data.get("action", "")
    ).strip().lower()

    if action not in ALLOWED_MOBILE_ACTIONS:

        return None

    command = {

        "action": action

    }

    for key in (
        "app",
        "text",
        "number",
        "url",
        "volume",
        "package"
    ):

        if key in data:

            value = data.get(key)

            if value is not None:

                command[key] = str(
                    value
                ).strip()

    return command


# ============================================================
# MOBILE COMMAND API
#
# Android app এই endpoint-এ command পাঠাবে।
# ============================================================

@app.route(
    "/api/mobile/command",
    methods=["POST"]
)
def mobile_command():

    auth_error = require_mobile_auth()

    if auth_error:
        return auth_error

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    command = clean_mobile_command(
        data
    )

    if not command:

        return jsonify({

            "ok": False,

            "error":
                "Invalid or unsupported mobile command"

        }), 400

    logging.info(
        "Mobile command received: %s",
        command
    )

    # IMPORTANT:
    # Backend নিজে Android phone control করে না।
    # Android app এই command receive করে
    # অনুমোদিত action execute করবে।

    return jsonify({

        "ok": True,

        "command": command,

        "status":
            "command accepted"

    }), 200


# ============================================================
# MOBILE CHAT API
# ============================================================

@app.route(
    "/api/mobile/chat",
    methods=["POST"]
)
def mobile_chat():

    auth_error = require_mobile_auth()

    if auth_error:
        return auth_error

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    user_text = str(
        data.get("message", "")
    ).strip()

    if not user_text:

        return jsonify({

            "ok": False,

            "error":
                "message is required"

        }), 400

    logging.info(
        "Mobile chat: %s",
        user_text
    )

    return jsonify({

        "ok": True,

        "message": user_text,

        "status":
            "received"

    }), 200


# ============================================================
# VOICE FILE SERVER
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
# START
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
        "Mobile control: %s",
        MOBILE_CONTROL_ENABLED
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
    
