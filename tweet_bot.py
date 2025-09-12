import os
import random
import time
import requests
import json
from typing import List, Dict, Any
from datetime import datetime
import tweepy

# ---------------------------
# Read secrets
# ---------------------------
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET")

HF_API_KEY = os.getenv("HF_API_KEY")
HF_MODEL = os.getenv("HF_MODEL", "google/flan-t5-base")

# ---------------------------
# Init Twitter client
# ---------------------------
twitter = tweepy.Client(
    consumer_key=TWITTER_API_KEY,
    consumer_secret=TWITTER_API_SECRET,
    access_token=TWITTER_ACCESS_TOKEN,
    access_token_secret=TWITTER_ACCESS_SECRET,
    wait_on_rate_limit=True,
)

# ---------------------------
# Config
# ---------------------------
MAX_TWEET_LEN = 280

TOPICS = [
    "phishing awareness",
    "ransomware basics for non-tech users",
    "password hygiene and MFA",
    "social engineering red flags",
    "mobile banking safety",
    "cloud account hardening for small teams",
    "insider threats: human factors",
    "safe software updates and patching",
    "public Wi-Fi risks and VPN basics",
    "data privacy and oversharing",
]

HASHTAG_BUCKETS = [
    ["#CyberSecurity", "#InfoSec", "#DataPrivacy"],
    ["#CyberAwareness", "#SecurityTips", "#OnlineSafety"],
    ["#Phishing", "#Ransomware", "#Malware"],
]

CTA = "Was this useful? Like, Share & Comment to help others stay safe."
LOG_FILE = "posted_log.json"

# ---------------------------
# Load Fallback tips from file
# ---------------------------
def load_fallback_threads(filename="fallback.json"):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and all(isinstance(t, list) for t in data):
            return data
        else:
            raise ValueError("Invalid fallback.json format")
    except Exception as e:
        print("⚠️ Failed to load fallback.json, using default tip:", e)
        return [[
            "Cybersecurity awareness matters.",
            "Always double-check links before clicking.",
            "Enable MFA to protect your accounts.",
            "Keep your software updated.",
            "Was this useful? Like, Share & Comment to help others stay safe. #CyberAwareness"
        ]]

# ---------------------------
# Logging helpers
# ---------------------------
def load_log() -> List[Dict[str, Any]]:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_log(entry: Dict[str, Any]):
    history = load_log()
    history.append(entry)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

# ---------------------------
# Pick fallback ensuring no repeats
# ---------------------------
def pick_fallback(fallback_threads: List[List[str]]) -> List[str]:
    history = load_log()
    used = {tuple(h["tweets"]) for h in history if h["source"] == "fallback"}
    unused = [t for t in fallback_threads if tuple(t) not in used]
    if unused:
        chosen = random.choice(unused)
    else:
        chosen = random.choice(fallback_threads)
    return chosen

# ---------------------------
# Helpers
# ---------------------------
def clamp_tweet(text: str) -> str:
    if len(text) <= MAX_TWEET_LEN:
        return text.strip()
    cut = text[:MAX_TWEET_LEN - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return (cut + "…").strip()

def pick_hashtags() -> str:
    bucket = random.choice(HASHTAG_BUCKETS)
    k = random.choice([2, 3])
    return " ".join(random.sample(bucket, k))

def build_prompt(topic: str) -> str:
    return f"""
Write a 5-part Twitter thread about \"{topic}\" for non-technical users.
Rules:
- Each tweet <= 280 characters.
- Include causes, human mistakes, and clear tips.
- End with: \"{CTA}\" and 2-3 hashtags.
Output format: list tweets 1-5, each on its own line.
"""

def call_hf_inference(prompt: str) -> str:
    url = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 400}}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HF API error {resp.status_code}: {resp.text}")
    data = resp.json()
    if isinstance(data, list) and "generated_text" in data[0]:
        return data[0]["generated_text"]
    return str(data)

def parse_thread_list(raw: str) -> List[str]:
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    tweets = []
    for ln in lines:
        if ln[0].isdigit():
            ln = ln.lstrip("12345). -")
        tweets.append(clamp_tweet(ln))
    tweets = tweets[:5]
    while len(tweets) < 5:
        tweets.append("Cybersecurity awareness matters. Stay safe online.")
    # Ensure hashtags in last tweet
    last = tweets[-1]
    ht = pick_hashtags()
    if CTA not in last:
        last = clamp_tweet(f"{last} {CTA}")
    if len(last) + 1 + len(ht) <= MAX_TWEET_LEN:
        last = f"{last} {ht}"
    tweets[-1] = clamp_tweet(last)
    return tweets

def post_thread(tweets: List[str]) -> str:
    first_id = None
    parent_id = None
    for text in tweets:
        for attempt in range(3):
            try:
                if parent_id:
                    res = twitter.create_tweet(text=text, in_reply_to_tweet_id=parent_id)
                else:
                    res = twitter.create_tweet(text=text)
                tid = str(res.data["id"])
                if first_id is None:
                    first_id = tid
                parent_id = tid
                break
            except Exception as e:
                time.sleep(2 + attempt * 2)
                if attempt == 2:
                    raise e
        time.sleep(2)
    return first_id

# ---------------------------
# Main
# ---------------------------
def main():
    run_mode = os.getenv("RUN_MODE", "").strip()
    fallback_threads = load_fallback_threads()

    tweets = None
    source = ""

    if run_mode == "0 20 * * *":   # Evening = fallback only
        tweets = pick_fallback(fallback_threads)
        source = "fallback"
        print("🌙 Evening post: Using fallback thread.")
    else:  # Morning or manual run = AI attempt
        topic = random.choice(TOPICS)
        prompt = build_prompt(topic)
        try:
            raw = call_hf_inference(prompt)
            tweets = parse_thread_list(raw)
            source = "ai"
            print("☀️ Morning post: AI-generated thread.")
        except Exception as e:
            print("⚠️ HF API failed, using fallback tips:", e)
            tweets = pick_fallback(fallback_threads)
            source = "fallback"

    first_id = post_thread(tweets)
    print("Thread posted. First tweet ID:", first_id)

    # Log entry
    log_entry = {
        "time": datetime.utcnow().isoformat() + "Z",
        "source": source,
        "tweets": tweets,
        "first_tweet_id": first_id
    }
    save_log(log_entry)

if __name__ == "__main__":
    main()
