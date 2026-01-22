"""SyncStream - Watch together, anywhere.

A FastAPI-based synchronized video watching application with WebSocket support.
"""

import json
import re
import secrets
import time
from collections import defaultdict

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

rooms: dict = {}
MAX_ROOMS = 1000
MAX_QUEUE_LENGTH = 50
MAX_NICK_LENGTH = 20
MAX_MSG_LENGTH = 500
MAX_URL_LENGTH = 2000

# Rate limiting: {ip: {action: [timestamps]}}
rate_limits: dict = defaultdict(lambda: defaultdict(list))
RATE_LIMITS = {
    "message": (10, 10),   # 10 messages per 10 seconds
    "queue": (5, 60),      # 5 queue adds per minute
    "connect": (10, 60),   # 10 connections per minute
}

def check_rate_limit(ip: str, action: str) -> bool:
    """Returns True if rate limited"""
    if action not in RATE_LIMITS:
        return False
    max_count, window = RATE_LIMITS[action]
    now = time.time()
    # Clean old entries
    rate_limits[ip][action] = [t for t in rate_limits[ip][action] if now - t < window]
    if len(rate_limits[ip][action]) >= max_count:
        return True
    rate_limits[ip][action].append(now)
    return False

def sanitize_nick(nick: str) -> str:
    """Sanitize nickname"""
    return re.sub(r'[<>&"\']', '', nick or 'Guest')[:MAX_NICK_LENGTH] or 'Guest'

def is_safe_url(url: str) -> bool:
    """Check if URL is safe (http/https only)"""
    url = url.strip().lower()
    return url.startswith('http://') or url.startswith('https://')

def generate_room_code() -> str:
    """Generate a random 6-character room code."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return ''.join(secrets.choice(chars) for _ in range(6))

def parse_video_url(url: str) -> dict | None:
    """Parse URL and return type + ID for YouTube/Twitch, or raw URL for direct video.
    Returns None if URL is invalid/unsafe."""
    url = url.strip()[:MAX_URL_LENGTH]

    if not is_safe_url(url):
        return None

    # YouTube: validate ID is exactly 11 alphanumeric/dash/underscore chars
    yt_pattern = r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)'
    yt_pattern += r'([a-zA-Z0-9_-]{11})(?:[&?]|$)'
    yt_match = re.search(yt_pattern, url)
    if yt_match:
        vid_id = yt_match.group(1)
        # Extra validation - must be alphanumeric with dash/underscore only
        if re.fullmatch(r'[a-zA-Z0-9_-]{11}', vid_id):
            return {"type": "youtube", "id": vid_id}

    # Twitch VOD: validate ID is numeric only
    twitch_vod = re.search(r'twitch\.tv/videos/(\d{1,12})(?:[?]|$)', url)
    if twitch_vod:
        return {"type": "twitch_vod", "id": twitch_vod.group(1)}

    # Twitch channel: validate channel name format
    twitch_channel = re.search(r'twitch\.tv/([a-zA-Z0-9_]{1,25})(?:[?]|$)', url)
    reserved_paths = ('videos', 'directory', 'settings')
    if twitch_channel and twitch_channel.group(1).lower() not in reserved_paths:
        return {"type": "twitch_live", "id": twitch_channel.group(1)}

    # Direct video URL - must be http(s)
    return {"type": "direct", "url": url}

@app.get("/")
async def get_page():
    """Serve the main HTML page."""
    return FileResponse("static/index.html")

@app.websocket("/ws/{room_code}")
async def websocket_handler(websocket: WebSocket, room_code: str):
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Handle WebSocket connections for room synchronization."""
    # Get client IP for rate limiting
    client_ip = websocket.client.host if websocket.client else "unknown"

    # Rate limit connections
    if check_rate_limit(client_ip, "connect"):
        await websocket.close(code=1008, reason="Rate limited")
        return

    await websocket.accept()

    # Validate room code format
    room_code = re.sub(r'[^0-9A-Z]', '', room_code.upper())[:6]
    if len(room_code) != 6:
        await websocket.close(code=1008, reason="Invalid room code")
        return

    # Check max rooms
    if room_code not in rooms and len(rooms) >= MAX_ROOMS:
        await websocket.close(code=1008, reason="Server full")
        return

    if room_code not in rooms:
        rooms[room_code] = {
            "clients": {},
            "queue": [],
            "playing": False,
            "position": 0.0,
            "last_update": time.time()
        }

    room = rooms[room_code]
    room["clients"][websocket] = {"nick": "Guest", "join_time": time.time(), "ip": client_ip}

    current = room["queue"][0] if room["queue"] else None
    await websocket.send_text(json.dumps({
        "type": "state",
        "current": current,
        "queue": room["queue"],
        "playing": room["playing"],
        "position": room["position"],
        "viewers": len(room["clients"])
    }))

    await broadcast(
        room_code, {"type": "viewers", "count": len(room["clients"])}, exclude=websocket
    )

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            nick = sanitize_nick(msg.get("nick", "Guest"))
            room["clients"][websocket]["nick"] = nick

            if msg["type"] == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "t": msg["t"]}))

            elif msg["type"] == "join":
                await broadcast(
                    room_code, {"type": "system", "text": f"{nick} joined"},
                    exclude=websocket
                )

            elif msg["type"] == "chat":
                if check_rate_limit(client_ip, "message"):
                    continue  # Silently drop rate-limited messages
                text = msg.get("text", "")[:MAX_MSG_LENGTH].strip()
                if text:
                    await broadcast(room_code, {"type": "chat", "nick": nick, "text": text})

            elif msg["type"] == "queue":
                if check_rate_limit(client_ip, "queue"):
                    rate_msg = {"type": "system", "text": "Rate limited, slow down"}
                    await websocket.send_text(json.dumps(rate_msg))
                    continue
                if len(room["queue"]) >= MAX_QUEUE_LENGTH:
                    full_msg = {"type": "system", "text": "Queue is full"}
                    await websocket.send_text(json.dumps(full_msg))
                    continue
                url = msg.get("url", "").strip()
                if url:
                    parsed = parse_video_url(url)
                    if parsed is None:
                        err = {"type": "system", "text": "Invalid URL (must be http/https)"}
                        await websocket.send_text(json.dumps(err))
                        continue
                    if parsed["type"] == "youtube":
                        parsed["display"] = f"YouTube: {parsed['id']}"
                    elif parsed["type"] == "twitch_vod":
                        parsed["display"] = f"Twitch VOD: {parsed['id']}"
                    elif parsed["type"] == "twitch_live":
                        parsed["display"] = f"Twitch: {parsed['id']}"
                    else:
                        parsed["display"] = url[:50]

                    room["queue"].append(parsed)

                    if len(room["queue"]) == 1:
                        room["position"] = 0
                        room["playing"] = False
                        load_msg = {"type": "load", "current": parsed, "queue": room["queue"]}
                        await broadcast(room_code, load_msg)
                    else:
                        update_msg = {
                            "type": "queue_update", "queue": room["queue"], "nick": nick
                        }
                        await broadcast(room_code, update_msg)

            elif msg["type"] == "skip":
                if room["queue"]:
                    room["queue"].pop(0)
                    room["position"] = 0
                    room["playing"] = False
                    current = room["queue"][0] if room["queue"] else None
                    load_msg = {"type": "load", "current": current, "queue": room["queue"]}
                    await broadcast(room_code, load_msg)
                    skip_msg = {"type": "system", "text": f"{nick} skipped"}
                    await broadcast(room_code, skip_msg)

            elif msg["type"] == "play":
                room["playing"] = True
                pos = msg.get("position", 0)
                room["position"] = max(0, float(pos)) if isinstance(pos, (int, float)) else 0
                room["last_update"] = time.time()
                play_msg = {"type": "play", "position": room["position"], "nick": nick}
                await broadcast(room_code, play_msg, exclude=websocket)

            elif msg["type"] == "pause":
                room["playing"] = False
                pos = msg.get("position", 0)
                room["position"] = max(0, float(pos)) if isinstance(pos, (int, float)) else 0
                room["last_update"] = time.time()
                pause_msg = {"type": "pause", "position": room["position"], "nick": nick}
                await broadcast(room_code, pause_msg, exclude=websocket)

            elif msg["type"] == "seek":
                pos = msg.get("position", 0)
                room["position"] = max(0, float(pos)) if isinstance(pos, (int, float)) else 0
                room["last_update"] = time.time()
                seek_msg = {"type": "seek", "position": room["position"]}
                await broadcast(room_code, seek_msg, exclude=websocket)

            elif msg["type"] == "ended":
                if room["queue"]:
                    room["queue"].pop(0)
                    room["position"] = 0
                    room["playing"] = False
                    current = room["queue"][0] if room["queue"] else None
                    load_msg = {"type": "load", "current": current, "queue": room["queue"]}
                    await broadcast(room_code, load_msg)

    except WebSocketDisconnect:
        pass
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        print(f"WebSocket message error: {exc}")
    except (RuntimeError, ConnectionError) as exc:
        print(f"WebSocket connection error: {exc}")
    finally:
        nick = room["clients"].get(websocket, {}).get("nick", "Guest")
        rooms[room_code]["clients"].pop(websocket, None)
        remaining = len(rooms[room_code]["clients"])
        if remaining == 0:
            del rooms[room_code]
        else:
            await broadcast(room_code, {"type": "viewers", "count": remaining})
            await broadcast(room_code, {"type": "system", "text": f"{nick} left"})

async def broadcast(room_code: str, message: dict, exclude: WebSocket = None):
    """Broadcast a message to all clients in a room except the excluded one."""
    if room_code not in rooms:
        return
    payload = json.dumps(message)
    dead = []
    for ws in rooms[room_code]["clients"]:
        if ws == exclude:
            continue
        try:
            await ws.send_text(payload)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            dead.append(ws)
    for ws in dead:
        rooms[room_code]["clients"].pop(ws, None)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
