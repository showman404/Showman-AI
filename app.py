import os
import requests
from flask import Flask, request
from dotenv import load_dotenv

# =========================
# Load Environment Variables
# =========================

load_dotenv()

PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# =========================
# Flask App
# =========================

app = Flask(__name__)

# =========================
# User Locations
# =========================

user_locations = {}

# =========================
# JARVIS System Prompt
# =========================

SYSTEM_PROMPT = """
তুমি JARVIS নামে একটি স্মার্ট বাংলা AI assistant।

তোমার ব্যবহারকারীর সাথে স্বাভাবিক, বন্ধুসুলভ এবং সাহায্যকারীভাবে কথা বলবে।

ব্যবহারকারী বাংলা ভাষায় কথা বললে বাংলায় উত্তর দেবে।
প্রয়োজনে English ব্যবহার করা যাবে।

তুমি নিজের নাম JARVIS হিসেবে পরিচয় দিতে পারো।

তুমি কখনো নিজের সম্পর্কে মিথ্যা দাবি করবে না।

ব্যবহারকারী যদি রাস্তার জ্যাম বা traffic সম্পর্কে জানতে চায়,
তাহলে traffic feature ব্যবহার করার জন্য প্রয়োজনীয় location/destination
চাও এবং পাওয়া তথ্যের ভিত্তিতে পরিষ্কারভাবে উত্তর দাও।
"""

# =========================
# Gemini AI
# =========================

def ask_gemini(user_text, extra_prompt=""):
    if not GEMINI_API_KEY:
        return "দুঃখিত, Gemini API Key সেট করা হয়নি।"

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
        f"?key={GEMINI_API_KEY}"
    )

    prompt = SYSTEM_PROMPT

    if extra_prompt:
        prompt += "\n\n" + extra_prompt

    prompt += "\n\nব্যবহারকারীর বার্তা:\n" + user_text

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
            print("Gemini Error:", response.text)
            return "দুঃখিত, এই মুহূর্তে AI উত্তর দিতে পারছে না।"

        data = response.json()

        candidates = data.get("candidates", [])

        if not candidates:
            return "দুঃখিত, কোনো উত্তর পাওয়া যায়নি।"

        parts = candidates[0].get("content", {}).get("parts", [])

        if not parts:
            return "দুঃখিত, কোনো উত্তর পাওয়া যায়নি।"

        return parts[0].get("text", "দুঃখিত, উত্তর তৈরি করা যায়নি।")

    except Exception as e:
        print("Gemini Exception:", e)
        return "দুঃখিত, AI সার্ভিসে সমস্যা হয়েছে।"


# =========================
# Facebook Messenger
# =========================

def send_message(recipient_id, text):
    if not PAGE_ACCESS_TOKEN:
        print("PAGE_ACCESS_TOKEN নেই।")
        return

    url = (
        "https://graph.facebook.com/v20.0/me/messages"
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
            print("Facebook Error:", response.text)

    except Exception as e:
        print("Facebook Exception:", e)


# =========================
# Traffic Detection
# =========================

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


# =========================
# Google Routes API
# =========================

def get_traffic(origin_lat, origin_lng, destination_text):
    if not GOOGLE_MAPS_API_KEY:
        return None, "GOOGLE_MAPS_API_KEY সেট করা হয়নি।"

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": (
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
                    "latitude": float(origin_lat),
                    "longitude": float(origin_lng)
                }
            }
        },
        "destination": {
            "address": destination_text
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "computeAlternativeRoutes": False,
        "languageCode": "bn-BD",
        "units": "METRIC"
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code != 200:
            print("Google Routes Error:", response.text)
            return None, "Google Maps থেকে traffic তথ্য পাওয়া যায়নি।"

        data = response.json()

        routes = data.get("routes", [])

        if not routes:
            return None, "এই রুটের কোনো তথ্য পাওয়া যায়নি।"

        route = routes[0]

        duration_text = route.get("duration", "")
        static_duration_text = route.get("staticDuration", "")
        distance_meters = route.get("distanceMeters", 0)

        return {
            "duration": duration_text,
            "static_duration": static_duration_text,
            "distance_meters": distance_meters
        }, None

    except Exception as e:
        print("Traffic Exception:", e)
        return None, "Google Maps-এর সাথে যোগাযোগ করা যাচ্ছে না।"


# =========================
# Traffic Reply
# =========================

def traffic_reply(traffic_data, destination):
    duration = traffic_data.get("duration", "")
    static_duration = traffic_data.get("static_duration", "")
    distance = traffic_data.get("distance_meters", 0)

    try:
        distance_km = distance / 1000
    except Exception:
        distance_km = 0

    def duration_to_seconds(value):
        if not value:
            return 0

        try:
            return float(value.replace("s", ""))
        except Exception:
            return 0

    traffic_seconds = duration_to_seconds(duration)
    normal_seconds = duration_to_seconds(static_duration)

    if normal_seconds > 0:
        delay_seconds = traffic_seconds - normal_seconds
    else:
        delay_seconds = 0

    if delay_seconds <= 120:
        traffic_status = "🟢 খুব বেশি জ্যাম নেই।"
    elif delay_seconds <= 600:
        traffic_status = "🟡 হালকা থেকে মাঝারি জ্যাম আছে।"
    elif delay_seconds <= 1200:
        traffic_status = "🟠 বেশ ভালো জ্যাম আছে।"
    else:
        traffic_status = "🔴 অনেক বেশি জ্যাম আছে।"

    return (
        f"🚦 ট্রাফিক রিপোর্ট\n\n"
        f"📍 গন্তব্য: {destination}\n"
        f"🛣️ দূরত্ব: {distance_km:.1f} কিমি\n"
        f"{traffic_status}\n\n"
        f"Google Maps-এর বর্তমান traffic data অনুযায়ী "
        f"এই রিপোর্ট দেওয়া হয়েছে।"
    )


# =========================
# Webhook Verification
# =========================

@app.route("/webhook", methods=["GET"])
def verify_webhook():

    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Verification failed", 403


# =========================
# Messenger Webhook
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():

    data = request.get_json(silent=True)

    if not data:
        return "OK", 200

    if data.get("object") != "page":
        return "Not a page event", 404

    for entry in data.get("entry", []):

        for messaging_event in entry.get("messaging", []):

            # =========================
            # Sender
            # =========================

            sender = messaging_event.get("sender", {})
            sender_id = sender.get("id")

            if not sender_id:
                continue

            # =========================
            # Location Message
            # =========================

            if "message" in messaging_event:

                message = messaging_event["message"]

                attachments = message.get("attachments", [])

                for attachment in attachments:

                    if attachment.get("type") == "location":

                        payload = attachment.get("payload", {})
                        coordinates = payload.get("coordinates", {})

                        latitude = coordinates.get("lat")
                        longitude = coordinates.get("long")

                        if latitude is not None and longitude is not None:

                            user_locations[sender_id] = {
                                "latitude": latitude,
                                "longitude": longitude
                            }

                            send_message(
                                sender_id,
                                "📍 তোমার Location পেয়েছি!\n"
                                "এখন যে জায়গায় যেতে চাও, "
                                "সেই জায়গার নাম লিখে পাঠাও।\n\n"
                                "যেমন: Bashundhara City"
                            )

                        continue

            # =========================
            # Text Message
            # =========================

            if "message" not in messaging_event:
                continue

            message = messaging_event["message"]

            if "text" not in message:
                continue

            user_text = message["text"].strip()

            if not user_text:
                continue

            # =========================
            # Traffic Feature
            # ALL USERS
            # =========================

            if is_traffic_question(user_text):

                if sender_id not in user_locations:

                    send_message(
                        sender_id,
                        "🚦 জ্যামের অবস্থা দেখতে তোমার বর্তমান "
                        "Location দরকার।\n\n"
                        "Messenger-এর 📎/Location অপশন থেকে "
                        "তোমার Location পাঠাও।"
                    )

                    continue

                send_message(
                    sender_id,
                    "🚗 ঠিক আছে! তোমার Location পেয়েছি।\n"
                    "Google Maps-এর traffic data দেখে জানাচ্ছি..."
                )

                # If user included destination in the same message,
                # try to use it. Otherwise ask for destination.
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
                    if word.lower() not in generic_words
                ]

                destination = " ".join(cleaned_words).strip()

                if len(destination) < 3:

                    send_message(
                        sender_id,
                        "📍 তোমার Location পেয়েছি।\n\n"
                        "এখন গন্তব্যের নাম লিখো।\n"
                        "যেমন:\n"
                        "➡️ Farmgate\n"
                        "➡️ Gulshan 1\n"
                        "➡️ Airport"
                    )

                    continue

                location = user_locations[sender_id]

                traffic_data, error = get_traffic(
                    location["latitude"],
                    location["longitude"],
                    destination
                )

                if error:

                    send_message(
                        sender_id,
                        "⚠️ " + error
                    )

                    continue

                reply = traffic_reply(
                    traffic_data,
                    destination
                )

                send_message(
                    sender_id,
                    reply
                )

                continue

            # =========================
            # Admin Mode
            # =========================

            if ADMIN_ID and sender_id == ADMIN_ID:

                admin_prompt = """
তুমি এখন তোমার Admin-এর সাথে কথা বলছো।

Admin-এর সাথে সম্মানজনক কিন্তু বন্ধুসুলভভাবে কথা বলবে।
Admin-এর প্রশ্নের সর্বোচ্চ সাহায্য করবে।

Admin চাইলে programming, debugging, server,
Facebook Messenger, Google Maps, Gemini API
এবং JARVIS সম্পর্কিত বিষয়ে সাহায্য করবে।
"""

                response = ask_gemini(
                    user_text,
                    admin_prompt
                )

                send_message(
                    sender_id,
                    response
                )

                continue

            # =========================
            # Normal User
            # =========================

            response = ask_gemini(user_text)

            send_message(
                sender_id,
                response
            )

    return "OK", 200


# =========================
# Home / Health Check
# =========================

@app.route("/", methods=["GET"])
def home():
    return "JARVIS is running successfully."


# =========================
# Run App
# =========================

if __name__ == "__main__":

    port = int(os.getenv("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )