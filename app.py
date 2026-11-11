import json, os, re, subprocess, sys, threading, time
from urllib.parse import urlparse, quote
import requests
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
# Set ALLOWED_ORIGINS to your site address (e.g. https://yoursite.com) once it is live.
CORS(app, origins=os.environ.get("ALLOWED_ORIGINS", "*"))

IG = re.compile(r"^https?://(www\.)?instagram\.com/(reel|reels|p|tv)/[\w\-]+", re.I)
CDN = re.compile(r"(^|\.)(cdninstagram\.com|fbcdn\.net)$")  # only Instagram video servers are allowed
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


@app.get("/")
def home():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


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
    link = r.stdout.strip().splitlines()[0]
    return jsonify(download="/api/file?u=" + quote(link, safe=""))


@app.get("/api/info")
def info():
    """Returns title, thumbnail and every format this video really offers."""
    url = (request.args.get("url") or "").strip()
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0]
    if limited(ip):
        return jsonify(error="Too many requests. Try again in a few minutes."), 429
    if not IG.match(url):
        return jsonify(error="Not a valid Instagram Reel, post or video link."), 400
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "-J", url]
    if os.path.exists("cookies.txt"):
        cmd[3:3] = ["--cookies", "cookies.txt"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout)
    except Exception:
        data = None
    if not data:
        print("yt-dlp error:", (r.stderr[-500:] if 'r' in locals() else "no output"), flush=True)
        return jsonify(error="Could not fetch this video. It may be private or deleted."), 422
    if data.get("entries"):
        data = data["entries"][0]
    dur = data.get("duration")
    name = re.sub(r"[^\w\- ]", "", data.get("title") or "instagram-video").strip()[:60] or "instagram-video"

    def link(u, ext, inline=False):
        q = "/api/file?u=" + quote(u, safe="") + "&n=" + quote(name) + "&e=" + (ext or "mp4")
        return q + ("&inline=1" if inline else "")

    def size_of(f):
        sz = f.get("filesize") or f.get("filesize_approx")
        if not sz and f.get("tbr") and dur:
            sz = int(f["tbr"] * 1000 * dur / 8)
        return sz

    labels = {2160: "2160p 4K", 1440: "1440p 2K", 1080: "1080p Full HD", 720: "720p HD"}
    vids, auds, seen = [], [], set()
    for f in data.get("formats") or []:
        u = f.get("url")
        if not u or f.get("protocol") not in ("https", "http"):
            continue
        vc, ac = f.get("vcodec") or "none", f.get("acodec") or "none"
        ext = f.get("ext") or "mp4"
        if vc != "none" and ac != "none":
            h = f.get("height") or 0
            lab = labels.get(h, f"{h}p" if h else "Video")
            key = ("v", lab, ext)
            if key not in seen:
                seen.add(key)
                vids.append({"label": lab, "ext": ext.upper(), "size": size_of(f), "url": link(u, ext), "h": h})
        elif vc == "none" and ac != "none":
            lab = f"{round(f['abr'])} kbps" if f.get("abr") else "Audio"
            key = ("a", lab, ext)
            if key not in seen:
                seen.add(key)
                auds.append({"label": lab, "ext": ext.upper(), "size": size_of(f), "url": link(u, ext), "h": f.get("abr") or 0})
    if not vids and data.get("url"):
        ext = data.get("ext") or "mp4"
        vids.append({"label": "Best quality", "ext": ext.upper(), "size": None, "url": link(data["url"], ext), "h": 0})
    vids.sort(key=lambda x: -x["h"])
    auds.sort(key=lambda x: -x["h"])
    th = data.get("thumbnail")
    return jsonify(title=data.get("title") or "Instagram video",
                   uploader=data.get("uploader") or data.get("channel") or "",
                   duration=dur, thumbnail=link(th, "jpg", True) if th else "",
                   video=vids, audio=auds)


@app.get("/api/file")
def file():
    """Streams a file. By default as a download, with inline=1 so it can show as a picture."""
    u = request.args.get("u", "")
    p = urlparse(u)
    if p.scheme != "https" or not CDN.search(p.hostname or ""):
        return jsonify(error="Invalid file link."), 400
    n = re.sub(r"[^\w\- ]", "", request.args.get("n", "instagram-video"))[:60] or "instagram-video"
    e = re.sub(r"[^a-z0-9]", "", request.args.get("e", "mp4").lower())[:4] or "mp4"
    try:
        up = requests.get(u, stream=True, timeout=30)
    except Exception:
        return jsonify(error="Could not fetch the file."), 502
    h = {"Content-Type": up.headers.get("Content-Type", "application/octet-stream")}
    if not request.args.get("inline"):
        h["Content-Disposition"] = 'attachment; filename="%s.%s"' % (n, e)
    if up.headers.get("Content-Length"):
        h["Content-Length"] = up.headers["Content-Length"]
    return Response(stream_with_context(up.iter_content(65536)), status=up.status_code, headers=h)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
