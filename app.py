import os
import re
import base64
import logging
import requests

from flask import Flask, request
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

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
).strip()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

ADMIN_ID = os.getenv("ADMIN_ID", "").strip()

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

def ask_groq(prompt):

    url = (
        "https://api.groq.com/openai/v1/"
        "chat/completions"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization":
            f"Bearer {GROQ_API_KEY}"
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_completion_tokens": 1024,
        "temperature": 0.5
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:

        raise RuntimeError(
            f"Groq HTTP "
            f"{response.status_code}: "
            f"{response.text[:1000]}"
        )

    data = response.json()

    choices = data.get(
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
    )

    if not answer:
        raise RuntimeError(
            "Groq returned empty response"
        )

    logging.info(
        "AI provider: Groq / %s",
        GROQ_MODEL
    )

    return answer.strip()


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

            # =================================================
            # IMAGE MESSAGE
            # =================================================

            attachments = message.get(
                "attachments",
                []
            )

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

                logging.info(
                    "Image received from %s",
                    sender_id
                )

                answer = process_news_image(
                    sender_id,
                    image_attachment
                )

                save_message(
                    sender_id,
                    "user",
                    "[News screenshot]"
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
        "groq": GROQ_MODEL,
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