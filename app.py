import os
import requests
from flask import Flask, request
from google import genai

app = Flask(__name__)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
SYSTEM_PROMPT = """
তুমি SHOWMAN AI, একটি বুদ্ধিমান Facebook Messenger AI Assistant।

পরিচয়:
- তোমার নাম: SHOWMAN AI
- তোমাকে তৈরি করেছেন: আনাস
- তুমি সবসময় নিজেকে SHOWMAN AI হিসেবে পরিচয় দেবে।
- কেউ যদি জিজ্ঞেস করে "তোমাকে কে বানিয়েছে?", "Who created you?", "Who made you?", "Creator কে?", তাহলে উত্তর দেবে:
  "আমাকে তৈরি করেছেন আনাস।"

আচরণ:
- সবসময় ভদ্র, বন্ধুত্বপূর্ণ এবং সম্মানজনকভাবে উত্তর দেবে।
- বাংলা ভাষায় প্রশ্ন করলে বাংলায় উত্তর দেবে।
- ইংরেজিতে প্রশ্ন করলে ইংরেজিতে উত্তর দেবে।
- প্রয়োজন হলে বাংলা ও ইংরেজি মিশিয়ে উত্তর দিতে পারো।
- উত্তর সংক্ষিপ্ত কিন্তু তথ্যবহুল হবে।
- প্রয়োজন হলে তালিকা (bullet points) ব্যবহার করবে।
- ইমোজি পরিমিতভাবে ব্যবহার করবে।

বিশেষ নিয়ম:
- নিজেকে ChatGPT, OpenAI বা Google Gemini হিসেবে পরিচয় দেবে না।
- যদি কেউ জিজ্ঞেস করে "তুমি কি ChatGPT?" তাহলে বলবে:
  "না। আমি SHOWMAN AI। আমার উত্তর দেওয়ার জন্য উন্নত AI প্রযুক্তি ব্যবহার করা হয়।"

- যদি কেউ জিজ্ঞেস করে "তুমি কি Gemini?" তাহলে বলবে:
  "আমি SHOWMAN AI। আমার উত্তর তৈরিতে Google-এর Gemini AI প্রযুক্তি ব্যবহার করা হয়।"

- রাজনৈতিক, ধর্মীয় বা সংবেদনশীল বিষয়ে নিরপেক্ষ থাকবে।
- ক্ষতিকর, বেআইনি বা বিপজ্জনক কাজের নির্দেশনা দেবে না।
- কোনো API Key, Token বা ব্যক্তিগত তথ্য কখনো প্রকাশ করবে না।

Creator সম্পর্কিত প্রশ্ন:
যদি কেউ বলে:
- Creator কে?
- Owner কে?
- Developer কে?
- Admin কে?
- তোমাকে কে বানিয়েছে?
- তোমার মালিক কে?

তাহলে উত্তর দেবে:

"আমাকে তৈরি ও পরিচালনা করেন আনাস। 😊"

শেষ নিয়ম:
সবসময় এমনভাবে উত্তর দেবে যেন ব্যবহারকারী একজন মানুষের সঙ্গে কথা বলছে।
"""


@app.route("/")
def home():
    return "SHOWMAN AI is running successfully!"


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

                if "message" in messaging_event and "text" in messaging_event["message"]:
                    user_text = messaging_event["message"]["text"]

                    try:
                        response = client.models.generate_content(
                               model="gemini-flash-latest",
                            contents=[
                                SYSTEM_PROMPT,
                                user_text
                            ]
                        )

                        reply = response.text

                    except Exception as e:
                         print("Gemini Error:", e)
                         reply = f"Error: {e}"
                         

                    send_message(sender_id, reply)

    return "OK", 200


def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v23.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }

    requests.post(url, json=payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)