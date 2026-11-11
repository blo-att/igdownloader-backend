import os, re, subprocess, sys, threading, time
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
# Set ALLOWED_ORIGINS to your site address (e.g. https://yoursite.com) once it is live.
CORS(app, origins=os.environ.get("ALLOWED_ORIGINS", "*"))

IG = re.compile(r"^https?://(www\.)?instagram\.com/(reel|reels|p|tv)/[\w\-]+", re.I)
hits = {}  # simple per-IP rate limit: 20 requests per 10 minutes


def update_ytdlp():
    """Keep yt-dlp current. Most Instagram breakages are fixed by a newer yt-dlp."""
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                       capture_output=True, timeout=300)
    except Exception as e:
        print("yt-dlp update failed:", e, flush=True)


def updater():
    while True:
        update_ytdlp()
        time.sleep(24 * 3600)


threading.Thread(target=updater, daemon=True).start()


def limited(ip):
    now = time.time()
    h = [t for t in hits.get(ip, []) if now - t < 600]
    h.append(now)
    hits[ip] = h
    return len(h) > 20


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/download")
def download():
    url = (request.args.get("url") or "").strip()
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0]
    if limited(ip):
        return jsonify(error="Too many requests. Try again in a few minutes."), 429
    if not IG.match(url):
        return jsonify(error="Not a valid Instagram Reel, post or video link."), 400
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "-g", "-f", "best[ext=mp4]/best", url]
    if os.path.exists("cookies.txt"):  # optional, helps when Instagram blocks the server IP
        cmd[3:3] = ["--cookies", "cookies.txt"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return jsonify(error="Timed out. Try again."), 504
    if r.returncode != 0 or not r.stdout.strip():
        print("yt-dlp error:", r.stderr[-500:], flush=True)  # check this in your host logs
        return jsonify(error="Could not fetch this video. It may be private or deleted."), 422
    return jsonify(download=r.stdout.strip().splitlines()[0])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
