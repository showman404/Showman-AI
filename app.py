import os
import requests
from flask import Flask, request
from google import genai
from groq import Groq

app = Flask(__name__)

# Environment Variables
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Gemini Client
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Groq Client
groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """
তুমি Jarvis, একটি বুদ্ধিমান Facebook Messenger AI Assistant।

পরিচয়:
- তোমার নাম: Jarvis
- তোমাকে তৈরি করেছেন: আনাস
- সবসময় নিজেকে Jarvis হিসেবে পরিচয় দেবে।

যদি কেউ জিজ্ঞেস করে:
- তোমাকে কে বানিয়েছে?
- Creator কে?
- Owner কে?
- Developer কে?

উত্তর দেবে:
"আমাকে তৈরি করেছেন আনাস। 😊"

যদি কেউ জিজ্ঞেস করে:
"তুমি কি ChatGPT?"

উত্তর:
"না। আমি Jarvis।"

যদি কেউ জিজ্ঞেস করে:
"তুমি কি Gemini?"

উত্তর:
"আমি Jarvis। আমার উত্তর তৈরিতে Google Gemini AI প্রযুক্তি ব্যবহার করা হয়।"

সবসময় ভদ্র, সংক্ষিপ্ত ও তথ্যবহুল উত্তর দেবে।
বাংলা প্রশ্নের উত্তর বাংলায়,
ইংরেজি প্রশ্নের উত্তর ইংরেজিতে দেবে।
"""

@app.route("/")
def home():
    return "Jarvis is running successfully!"
# Webhook Verification
@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200

    return "Verification failed", 403


# Receive Messages
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for messaging_event in entry.get("messaging", []):

                sender_id = messaging_event["sender"]["id"]

                if "message" in messaging_event:
                    if "text" not in messaging_event["message"]:
                        continue

                    user_text = messaging_event["message"]["text"]

                    try:
                        # Gemini
                        response = gemini_client.models.generate_content(
                            model="gemini-2.0-flash",
                            contents=[
                                SYSTEM_PROMPT,
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
                                        "content": SYSTEM_PROMPT
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
                            reply = "⚠️ Jarvis এই মুহূর্তে উত্তর দিতে পারছে না। অনুগ্রহ করে পরে আবার চেষ্টা করুন।"

                    send_message(sender_id, reply)

    return "OK", 200
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

    requests.post(url, json=payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)