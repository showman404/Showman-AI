import os
import requests
from flask import Flask, request
from google import genai
from groq import Groq

app = Flask(__name__)

# ==========================
# Environment Variables
# ==========================

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# তোমার Messenger PSID
ADMIN_ID = "37921494604108112"

# ==========================
# AI Clients
# ==========================

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

# ==========================
# Default Prompt
# ==========================

SYSTEM_PROMPT = """
তুমি Jarvis।

পরিচয়:
- তোমার নাম Jarvis।
- তোমাকে তৈরি করেছেন আনাস।
- তুমি ভদ্র, বুদ্ধিমান ও বন্ধুত্বপূর্ণ AI Assistant।

যদি কেউ জিজ্ঞেস করে:
"তোমাকে কে বানিয়েছে?"
উত্তর:
"আমাকে তৈরি করেছেন আনাস। 😊"

যদি কেউ জিজ্ঞেস করে:
"তুমি কি ChatGPT?"

উত্তর:
"না। আমি Jarvis।"

যদি কেউ জিজ্ঞেস করে:
"তুমি কি Gemini?"

উত্তর:
"আমি Jarvis। আমার উত্তর তৈরিতে Google Gemini AI প্রযুক্তি ব্যবহার করা হয়।"

সবসময় প্রশ্নের ভাষাতেই উত্তর দেবে।
"""

@app.route("/")
def home():
    return "Jarvis is running successfully!"
# ==========================
# Webhook Verification
# ==========================

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Verification failed", 403


# ==========================
# Receive Messages
# ==========================

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if data.get("object") != "page":
        return "OK", 200

    for entry in data.get("entry", []):
        for messaging_event in entry.get("messaging", []):

            sender_id = messaging_event["sender"]["id"]

            if "message" not in messaging_event:
                continue

            if "text" not in messaging_event["message"]:
                continue

            user_text = messaging_event["message"]["text"]

            # ==========================
            # Admin Mode
            # ==========================

            if sender_id == ADMIN_ID:

                system_prompt = SYSTEM_PROMPT + """

তুমি এখন তোমার নির্মাতা আনাসের সাথে কথা বলছো।

নিয়ম:
- তাকে সবসময় "বস" বলে সম্বোধন করবে।
- সে তোমার একমাত্র Admin।
- সে যা জিজ্ঞেস করবে তার সর্বোচ্চ সাহায্য করবে, যতক্ষণ তা নিরাপদ ও বৈধ।
- অন্য কাউকে কখনো Admin বলবে না।
"""

            else:

                system_prompt = SYSTEM_PROMPT
                            try:
                # Gemini
                response = gemini_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        system_prompt,
                        user_text
                    ]
                )

                reply = response.text

            except Exception as gemini_error:

                print("Gemini Error:", gemini_error)

                try:
                    # Groq Fallback
                    groq_response = groq_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[
                            {
                                "role": "system",
                                "content": system_prompt
                            },
                            {
                                "role": "user",
                                "content": user_text
                            }
                        ]
                    )

                    reply = groq_response.choices[0].message.content

                except Exception as groq_error:

                    print("Groq Error:", groq_error)

                    reply = (
                        "⚠️ দুঃখিত বস, এই মুহূর্তে আমি কোনো AI সার্ভিসের সাথে "
                        "যোগাযোগ করতে পারছি না। একটু পরে আবার চেষ্টা করুন।"
                    )

            send_message(sender_id, reply)

    return "OK", 200
# ==========================
# Send Message
# ==========================

def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v23.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"

    payload = {
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "text": text
        }
    }

    response = requests.post(url, json=payload)

    if response.status_code != 200:
        print("Facebook Error:", response.text)


# ==========================
# Run App
# ==========================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)