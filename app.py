import os
import requests
from flask import Flask, request
from dotenv import load_dotenv
from supabase import create_client, Client

# =========================================================
# Environment Variables
# =========================================================

load_dotenv()

PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


# =========================================================
# JARVIS Identity
# =========================================================

CREATOR_NAME = "Anas"


# =========================================================
# Flask
# =========================================================

app = Flask(__name__)


# =========================================================
# Supabase Connection
# =========================================================

supabase: Client | None = None

if SUPABASE_URL and SUPABASE_KEY:

    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_KEY
        )

        print("Supabase connected successfully.")

    except Exception as e:

        print(
            "Supabase connection error:",
            e
        )

else:

    print(
        "WARNING: SUPABASE_URL or "
        "SUPABASE_KEY is missing."
    )


# =========================================================
# User Locations
# =========================================================

user_locations = {}


# =========================================================
# JARVIS SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = f"""
তুমি JARVIS নামে একটি স্মার্ট বাংলা AI assistant।

তোমার নির্মাতার নাম {CREATOR_NAME}।

এটি একটি fixed identity fact।

কেউ যদি জিজ্ঞেস করে:

- তোমাকে কে বানিয়েছে?
- তোমার নির্মাতা কে?
- তোমার creator কে?
- কে তোমাকে তৈরি করেছে?
- Who created you?
- Who made you?
- Who is your creator?

তাহলে স্পষ্টভাবে বলবে:

"আমার নির্মাতা {CREATOR_NAME}।"

কখনো Google, Gemini, OpenAI, Facebook,
Meta অথবা অন্য কোনো কোম্পানি বা ব্যক্তিকে
তোমার নির্মাতা হিসেবে দাবি করবে না।

তুমি JARVIS নামে পরিচয় দিতে পারো।

ব্যবহারকারীর সাথে স্বাভাবিক,
বন্ধুসুলভ এবং সাহায্যকারীভাবে কথা বলবে।

ব্যবহারকারী বাংলা ভাষায় কথা বললে বাংলায় উত্তর দেবে।

প্রয়োজনে English ব্যবহার করতে পারো।

তোমার কাছে ব্যবহারকারীর কিছু saved memory
এবং recent conversation দেওয়া হতে পারে।

সেগুলো প্রাসঙ্গিক হলে ব্যবহার করবে।

অপ্রাসঙ্গিক পুরোনো তথ্য নিজে থেকে উল্লেখ করবে না।

কোনো memory-তে থাকা তথ্যকে বর্তমান প্রশ্নের
সাথে সম্পর্ক না থাকলে জোর করে ব্যবহার করবে না।

Traffic প্রশ্ন এলে system-এর traffic feature
ব্যবহার করা হবে।

তুমি কখনো বানিয়ে live traffic information দাবি করবে না।

যদি কোনো তথ্য database বা external API থেকে পাওয়া না যায়,
তাহলে সেটা পরিষ্কারভাবে বলবে।
"""


# =========================================================
# MEMORY: Save Message
# =========================================================

def save_message(user_id, role, message):

    if not supabase:
        return False

    try:

        supabase.table(
            "jarvis_messages"
        ).insert({
            "user_id": str(user_id),
            "role": role,
            "message": message
        }).execute()

        return True

    except Exception as e:

        print(
            "Memory save message error:",
            e
        )

        return False


# =========================================================
# MEMORY: Get Recent Messages
# =========================================================

def get_recent_messages(
    user_id,
    limit=20
):

    if not supabase:
        return []

    try:

        response = (
            supabase
            .table("jarvis_messages")
            .select(
                "role,message,created_at"
            )
            .eq(
                "user_id",
                str(user_id)
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(limit)
            .execute()
        )

        rows = response.data or []

        rows.reverse()

        return rows

    except Exception as e:

        print(
            "Memory read messages error:",
            e
        )

        return []


# =========================================================
# MEMORY: Save Permanent Memory
# =========================================================

def save_memory(
    user_id,
    memory
):

    if not supabase:
        return False

    try:

        supabase.table(
            "jarvis_memories"
        ).insert({
            "user_id": str(user_id),
            "memory": memory
        }).execute()

        return True

    except Exception as e:

        print(
            "Memory save error:",
            e
        )

        return False


# =========================================================
# MEMORY: Get Saved Memories
# =========================================================

def get_memories(
    user_id,
    limit=30
):

    if not supabase:
        return []

    try:

        response = (
            supabase
            .table("jarvis_memories")
            .select(
                "id,memory,created_at"
            )
            .eq(
                "user_id",
                str(user_id)
            )
            .order(
                "created_at",
                desc=False
            )
            .limit(limit)
            .execute()
        )

        return response.data or []

    except Exception as e:

        print(
            "Memory read error:",
            e
        )

        return []


# =========================================================
# MEMORY: Delete Saved Memories
# =========================================================

def delete_memories(user_id):

    if not supabase:
        return False

    try:

        supabase.table(
            "jarvis_memories"
        ).delete().eq(
            "user_id",
            str(user_id)
        ).execute()

        return True

    except Exception as e:

        print(
            "Memory delete error:",
            e
        )

        return False


# =========================================================
# MEMORY: Delete Conversation
# =========================================================

def delete_conversation(user_id):

    if not supabase:
        return False

    try:

        supabase.table(
            "jarvis_messages"
        ).delete().eq(
            "user_id",
            str(user_id)
        ).execute()

        return True

    except Exception as e:

        print(
            "Conversation delete error:",
            e
        )

        return False


# =========================================================
# MEMORY CONTEXT
# =========================================================

def build_memory_context(user_id):

    memories = get_memories(
        user_id
    )

    messages = get_recent_messages(
        user_id,
        20
    )

    context = ""


    # -----------------------------------------------------
    # Permanent Memories
    # -----------------------------------------------------

    if memories:

        context += (
            "\n\n"
            "===== SAVED USER MEMORY =====\n"
        )

        for item in memories:

            memory_text = item.get(
                "memory",
                ""
            ).strip()

            if memory_text:

                context += (
                    f"- {memory_text}\n"
                )

        context += (
            "===== END SAVED MEMORY =====\n"
        )


    # -----------------------------------------------------
    # Recent Conversation
    # -----------------------------------------------------

    if messages:

        context += (
            "\n\n"
            "===== RECENT CONVERSATION =====\n"
        )

        for item in messages:

            role = item.get(
                "role",
                ""
            )

            message = item.get(
                "message",
                ""
            ).strip()


            if not message:
                continue


            if role == "user":

                context += (
                    f"User: {message}\n"
                )


            elif role == "assistant":

                context += (
                    f"JARVIS: {message}\n"
                )


        context += (
            "===== END RECENT CONVERSATION =====\n"
        )


    return context


# =========================================================
# MEMORY COMMAND DETECTION
# =========================================================

def is_remember_command(text):

    keywords = [

        "মনে রাখো",
        "মনে রেখো",
        "মনে রাখবে",
        "এটা মনে রাখো",
        "এটা মনে রেখো",
        "এটা মনে রাখ",
        "remember this",
        "remember that"

    ]

    text_lower = text.lower()

    return any(
        keyword.lower() in text_lower
        for keyword in keywords
    )


# =========================================================
# Extract Memory
# =========================================================

def extract_memory(text):

    prefixes = [

        "এটা মনে রাখো",
        "এটা মনে রেখো",
        "এটা মনে রাখ",
        "মনে রাখো",
        "মনে রেখো",
        "মনে রাখবে",
        "remember this",
        "remember that"

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


    return result.strip(
        " :,-"
    )


# =========================================================
# Show Memory Command
# =========================================================

def is_memory_view_command(text):

    keywords = [

        "আমার memory কী",
        "আমার মেমোরি কী",
        "আমার memory কি",
        "আমার মেমোরি কি",
        "কি কি মনে রেখেছ",
        "কী কী মনে রেখেছ",
        "আমি কী কী বলেছিলাম",
        "what do you remember",
        "show my memory"

    ]

    text_lower = text.lower()

    return any(
        keyword.lower() in text_lower
        for keyword in keywords
    )


# =========================================================
# Forget Memory Command
# =========================================================

def is_forget_command(text):

    keywords = [

        "সব memory মুছে দাও",
        "সব মেমোরি মুছে দাও",
        "সব memory মুছে ফেল",
        "সব মেমোরি মুছে ফেল",
        "সবকিছু ভুলে যাও",
        "সব ভুলে যাও",
        "আমার memory মুছে দাও",
        "আমার মেমোরি মুছে দাও",
        "forget everything",
        "forget all",
        "clear memory"

    ]

    text_lower = text.lower()

    return any(
        keyword.lower() in text_lower
        for keyword in keywords
    )


# =========================================================
# GEMINI
# =========================================================

def ask_gemini(
    user_id,
    user_text,
    extra_prompt=""
):

    if not GEMINI_API_KEY:

        return (
            "দুঃখিত, Gemini API Key "
            "সেট করা হয়নি।"
        )


    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )


    # -----------------------------------------------------
    # Build Prompt
    # -----------------------------------------------------

    prompt = SYSTEM_PROMPT


    memory_context = build_memory_context(
        user_id
    )


    if memory_context:

        prompt += memory_context


    if extra_prompt:

        prompt += (
            "\n\n"
            "===== EXTRA INSTRUCTIONS =====\n"
        )

        prompt += extra_prompt


    prompt += (
        "\n\n"
        "===== CURRENT USER MESSAGE =====\n"
    )

    prompt += user_text


    # -----------------------------------------------------
    # Gemini Payload
    # -----------------------------------------------------

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

            "temperature": 0.7,

            "maxOutputTokens": 1024

        }

    }


    try:

        response = requests.post(

            url,

            json=payload,

            timeout=60

        )


        if response.status_code != 200:

            print(
                "Gemini Error:",
                response.text
            )

            return (
                "দুঃখিত, এই মুহূর্তে "
                "AI উত্তর দিতে পারছে না।"
            )


        data = response.json()


        candidates = data.get(
            "candidates",
            []
        )


        if not candidates:

            return (
                "দুঃখিত, কোনো উত্তর "
                "পাওয়া যায়নি।"
            )


        parts = (
            candidates[0]
            .get("content", {})
            .get("parts", [])
        )


        if not parts:

            return (
                "দুঃখিত, কোনো উত্তর "
                "পাওয়া যায়নি।"
            )


        return parts[0].get(
            "text",
            "দুঃখিত, উত্তর তৈরি করা যায়নি।"
        )


    except Exception as e:

        print(
            "Gemini Exception:",
            e
        )

        return (
            "দুঃখিত, AI সার্ভিসে "
            "সমস্যা হয়েছে।"
        )


# =========================================================
# FACEBOOK MESSENGER
# =========================================================

def send_message(
    recipient_id,
    text
):

    if not PAGE_ACCESS_TOKEN:

        print(
            "PAGE_ACCESS_TOKEN নেই।"
        )

        return


    url = (
        "https://graph.facebook.com/"
        "v20.0/me/messages"
        f"?access_token={PAGE_ACCESS_TOKEN}"
    )


    payload = {

        "recipient": {

            "id": recipient_id

        },

        "message": {

            "text": text

        }

    }


    try:

        response = requests.post(

            url,

            json=payload,

            timeout=30

        )


        if response.status_code != 200:

            print(
                "Facebook Error:",
                response.text
            )


    except Exception as e:

        print(
            "Facebook Exception:",
            e
        )


# =========================================================
# TRAFFIC DETECTION
# =========================================================

TRAFFIC_KEYWORDS = [

    "জ্যাম",
    "জ্যাম আছে",
    "জ্যাম কি",
    "ট্রাফিক",
    "ট্রাফিক আছে",
    "রাস্তার অবস্থা",
    "রাস্তায় জ্যাম",
    "রাস্তায় জ্যাম",
    "traffic",
    "jam",
    "traffic jam"

]


def is_traffic_question(text):

    text_lower = text.lower()


    for keyword in TRAFFIC_KEYWORDS:

        if keyword.lower() in text_lower:

            return True


    return False


# =========================================================
# GOOGLE ROUTES API
# =========================================================

def get_traffic(
    origin_lat,
    origin_lng,
    destination_text
):

    if not GOOGLE_MAPS_API_KEY:

        return (
            None,
            "GOOGLE_MAPS_API_KEY "
            "সেট করা হয়নি।"
        )


    url = (
        "https://routes.googleapis.com/"
        "directions/v2:computeRoutes"
    )


    headers = {

        "Content-Type":
            "application/json",

        "X-Goog-Api-Key":
            GOOGLE_MAPS_API_KEY,

        "X-Goog-FieldMask":
            (
                "routes.duration,"
                "routes.staticDuration,"
                "routes.distanceMeters,"
                "routes.legs"
            )

    }


    payload = {

        "origin": {

            "location": {

                "latLng": {

                    "latitude":
                        float(origin_lat),

                    "longitude":
                        float(origin_lng)

                }

            }

        },

        "destination": {

            "address":
                destination_text

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

    }


    try:

        response = requests.post(

            url,

            headers=headers,

            json=payload,

            timeout=30

        )


        if response.status_code != 200:

            print(
                "Google Routes Error:",
                response.text
            )

            return (
                None,
                "Google Maps থেকে "
                "traffic তথ্য পাওয়া যায়নি।"
            )


        data = response.json()


        routes = data.get(
            "routes",
            []
        )


        if not routes:

            return (
                None,
                "এই রুটের কোনো তথ্য পাওয়া যায়নি।"
            )


        route = routes[0]


        return {

            "duration":
                route.get(
                    "duration",
                    ""
                ),

            "static_duration":
                route.get(
                    "staticDuration",
                    ""
                ),

            "distance_meters":
                route.get(
                    "distanceMeters",
                    0
                )

        }, None


    except Exception as e:

        print(
            "Traffic Exception:",
            e
        )

        return (
            None,
            "Google Maps-এর সাথে "
            "যোগাযোগ করা যাচ্ছে না।"
        )


# =========================================================
# TRAFFIC REPLY
# =========================================================

def traffic_reply(
    traffic_data,
    destination
):

    duration = traffic_data.get(
        "duration",
        ""
    )


    static_duration = traffic_data.get(
        "static_duration",
        ""
    )


    distance = traffic_data.get(
        "distance_meters",
        0
    )


    try:

        distance_km = distance / 1000

    except Exception:

        distance_km = 0


    def duration_to_seconds(value):

        if not value:

            return 0


        try:

            return float(
                value.replace(
                    "s",
                    ""
                )
            )

        except Exception:

            return 0


    traffic_seconds = (
        duration_to_seconds(
            duration
        )
    )


    normal_seconds = (
        duration_to_seconds(
            static_duration
        )
    )


    if normal_seconds > 0:

        delay_seconds = (
            traffic_seconds -
            normal_seconds
        )

    else:

        delay_seconds = 0


    if delay_seconds <= 120:

        traffic_status = (
            "🟢 খুব বেশি জ্যাম নেই।"
        )

    elif delay_seconds <= 600:

        traffic_status = (
            "🟡 হালকা থেকে মাঝারি "
            "জ্যাম আছে।"
        )

    elif delay_seconds <= 1200:

        traffic_status = (
            "🟠 বেশ ভালো জ্যাম আছে।"
        )

    else:

        traffic_status = (
            "🔴 অনেক বেশি জ্যাম আছে।"
        )


    return (

        "🚦 ট্রাফিক রিপোর্ট\n\n"

        f"📍 গন্তব্য: {destination}\n"

        f"🛣️ দূরত্ব: "
        f"{distance_km:.1f} কিমি\n"

        f"{traffic_status}\n\n"

        "Google Maps-এর বর্তমান "
        "traffic data অনুযায়ী "
        "এই রিপোর্ট দেওয়া হয়েছে।"

    )


# =========================================================
# WEBHOOK VERIFICATION
# =========================================================

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


# =========================================================
# MESSENGER WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():

    data = request.get_json(
        silent=True
    )


    if not data:

        return "OK", 200


    if data.get(
        "object"
    ) != "page":

        return (
            "Not a page event",
            404
        )


    for entry in data.get(
        "entry",
        []
    ):

        for messaging_event in entry.get(
            "messaging",
            []
        ):

            # =================================================
            # Sender
            # =================================================

            sender = messaging_event.get(
                "sender",
                {}
            )


            sender_id = sender.get(
                "id"
            )


            if not sender_id:

                continue


            # =================================================
            # Message
            # =================================================

            if "message" not in messaging_event:

                continue


            message = messaging_event[
                "message"
            ]


            # =================================================
            # LOCATION
            # =================================================

            attachments = message.get(
                "attachments",
                []
            )


            for attachment in attachments:

                if attachment.get(
                    "type"
                ) != "location":

                    continue


                payload = attachment.get(
                    "payload",
                    {}
                )


                coordinates = payload.get(
                    "coordinates",
                    {}
                )


                latitude = coordinates.get(
                    "lat"
                )


                longitude = coordinates.get(
                    "long"
                )


                if (
                    latitude is not None
                    and longitude is not None
                ):

                    user_locations[
                        sender_id
                    ] = {

                        "latitude":
                            latitude,

                        "longitude":
                            longitude

                    }


                    send_message(

                        sender_id,

                        "📍 তোমার Location পেয়েছি!\n\n"
                        "এখন যে জায়গায় যেতে চাও, "
                        "সেই জায়গার নাম লিখে পাঠাও।\n\n"
                        "যেমন: Bashundhara City"

                    )


            # =================================================
            # TEXT
            # =================================================

            if "text" not in message:

                continue


            user_text = message[
                "text"
            ].strip()


            if not user_text:

                continue


            # =================================================
            # SAVE USER MESSAGE
            # =================================================

            save_message(

                sender_id,

                "user",

                user_text

            )


            # =================================================
            # REMEMBER COMMAND
            # =================================================

            if is_remember_command(
                user_text
            ):

                memory_text = extract_memory(
                    user_text
                )


                if len(memory_text) < 2:

                    reply = (
                        "🧠 কী বিষয়টা "
                        "মনে রাখতে হবে?"
                    )

                else:

                    success = save_memory(

                        sender_id,

                        memory_text

                    )


                    if success:

                        reply = (
                            "🧠 ঠিক আছে! "
                            "এটা আমি মনে রাখলাম।"
                        )

                    else:

                        reply = (
                            "⚠️ Memory save করতে "
                            "সমস্যা হয়েছে।"
                        )


                save_message(

                    sender_id,

                    "assistant",

                    reply

                )


                send_message(

                    sender_id,

                    reply

                )


                continue


            # =================================================
            # SHOW MEMORY
            # =================================================

            if is_memory_view_command(
                user_text
            ):

                memories = get_memories(
                    sender_id
                )


                if not memories:

                    reply = (
                        "🧠 এখনো তোমার জন্য "
                        "কোনো আলাদা memory "
                        "save করা নেই।"
                    )

                else:

                    lines = [

                        "🧠 তোমার সম্পর্কে "
                        "আমি যেগুলো মনে রেখেছি:\n"

                    ]


                    for index, item in enumerate(
                        memories,
                        start=1
                    ):

                        memory_text = item.get(
                            "memory",
                            ""
                        )


                        lines.append(
                            f"{index}. "
                            f"{memory_text}"
                        )


                    reply = "\n".join(
                        lines
                    )


                save_message(

                    sender_id,

                    "assistant",

                    reply

                )


                send_message(

                    sender_id,

                    reply

                )


                continue


            # =================================================
            # FORGET MEMORY
            # =================================================

            if is_forget_command(
                user_text
            ):

                memory_deleted = (
                    delete_memories(
                        sender_id
                    )
                )


                conversation_deleted = (
                    delete_conversation(
                        sender_id
                    )
                )


                if (
                    memory_deleted
                    and conversation_deleted
                ):

                    reply = (
                        "🧹 ঠিক আছে। "
                        "তোমার saved memory "
                        "এবং conversation "
                        "history মুছে দিয়েছি।"
                    )

                else:

                    reply = (
                        "⚠️ Memory মুছতে "
                        "সমস্যা হয়েছে।"
                    )


                send_message(

                    sender_id,

                    reply

                )


                continue


            # =================================================
            # TRAFFIC
            # =================================================

            if is_traffic_question(
                user_text
            ):

                if sender_id not in user_locations:

                    reply = (

                        "🚦 জ্যামের অবস্থা "
                        "দেখতে তোমার বর্তমান "
                        "Location দরকার।\n\n"

                        "Messenger-এর "
                        "📎/Location অপশন থেকে "
                        "তোমার Location পাঠাও।"

                    )


                    save_message(

                        sender_id,

                        "assistant",

                        reply

                    )


                    send_message(

                        sender_id,

                        reply

                    )


                    continue


                send_message(

                    sender_id,

                    "🚗 ঠিক আছে! তোমার "
                    "Location পেয়েছি।\n"
                    "Google Maps-এর traffic "
                    "data দেখে জানাচ্ছি..."

                )


                destination = user_text


                generic_words = [

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
                    "jam"

                ]


                cleaned_words = [

                    word

                    for word in destination.split()

                    if word.lower()
                    not in generic_words

                ]


                destination = (
                    " ".join(
                        cleaned_words
                    ).strip()
                )


                if len(destination) < 3:

                    reply = (

                        "📍 তোমার Location পেয়েছি।\n\n"
                        "এখন গন্তব্যের নাম লিখো।\n\n"
                        "যেমন:\n"
                        "➡️ Farmgate\n"
                        "➡️ Gulshan 1\n"
                        "➡️ Airport"

                    )


                    save_message(

                        sender_id,

                        "assistant",

                        reply

                    )


                    send_message(

                        sender_id,

                        reply

                    )


                    continue


                location = user_locations[
                    sender_id
                ]


                traffic_data, error = get_traffic(

                    location[
                        "latitude"
                    ],

                    location[
                        "longitude"
                    ],

                    destination

                )


                if error:

                    reply = (
                        "⚠️ " + error
                    )


                    save_message(

                        sender_id,

                        "assistant",

                        reply

                    )


                    send_message(

                        sender_id,

                        reply

                    )


                    continue


                reply = traffic_reply(

                    traffic_data,

                    destination

                )


                save_message(

                    sender_id,

                    "assistant",

                    reply

                )


                send_message(

                    sender_id,

                    reply

                )


                continue


            # =================================================
            # ADMIN
            # =================================================

            if (
                ADMIN_ID
                and sender_id == ADMIN_ID
            ):

                admin_prompt = f"""
তুমি এখন তোমার নির্মাতা এবং Admin {CREATOR_NAME}-এর সাথে কথা বলছো।

Admin-এর সাথে সম্মানজনক কিন্তু বন্ধুসুলভভাবে কথা বলবে।

Programming, debugging, server,
Facebook Messenger, Google Maps,
Gemini API, Supabase এবং JARVIS
সম্পর্কিত বিষয়ে সর্বোচ্চ সাহায্য করবে।

মনে রাখবে:
JARVIS-এর নির্মাতা হলেন {CREATOR_NAME}।
"""


                response = ask_gemini(

                    sender_id,

                    user_text,

                    admin_prompt

                )


                save_message(

                    sender_id,

                    "assistant",

                    response

                )


                send_message(

                    sender_id,

                    response

                )


                continue


            # =================================================
            # NORMAL USER
            # =================================================

            response = ask_gemini(

                sender_id,

                user_text

            )


            # =================================================
            # SAVE AI RESPONSE
            # =================================================

            save_message(

                sender_id,

                "assistant",

                response

            )


            send_message(

                sender_id,

                response

            )


    return "OK", 200


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return (
        "JARVIS is running successfully."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            5000
        )
    )


    app.run(

        host="0.0.0.0",

        port=port

    )