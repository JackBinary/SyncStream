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
from fastapi.responses import HTMLResponse

app = FastAPI()

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

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SyncStream</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }

        :root {
            --bg: #0a0a0f;
            --surface: #14141f;
            --surface-hover: #1a1a2a;
            --border: #2a2a3a;
            --text: #e8e8f0;
            --text-dim: #8888a0;
            --accent: #6366f1;
            --accent-hover: #818cf8;
            --success: #22c55e;
            --error: #ef4444;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }

        .container {
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 24px;
            min-height: 100vh;
        }

        header { text-align: center; margin-bottom: 24px; }

        h1 { font-size: 1.8rem; font-weight: 600; letter-spacing: -0.02em; }
        h1 span { color: var(--accent); }

        .subtitle { color: var(--text-dim); margin-top: 4px; font-size: 0.85rem; }

        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            width: 100%;
            max-width: 1100px;
            margin-bottom: 16px;
        }

        .top-controls {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            align-items: center;
        }

        .top-controls label { color: var(--text-dim); font-size: 0.8rem; font-weight: 500; }

        input {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 14px;
            color: var(--text);
            font-size: 0.9rem;
            outline: none;
            transition: border-color 0.2s;
        }

        input:focus { border-color: var(--accent); }
        input::placeholder { color: var(--text-dim); }

        .code-input {
            width: 90px;
            text-align: center;
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 1rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
        }

        .nick-input { width: 120px; }
        .url-input { flex: 1; min-width: 180px; }

        button {
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 16px;
            font-size: 0.85rem;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            white-space: nowrap;
        }

        button:hover { background: var(--accent-hover); }
        button:active { transform: scale(0.98); }
        button:disabled { opacity: 0.5; cursor: not-allowed; }

        .btn-secondary { background: var(--surface-hover); border: 1px solid var(--border); }
        .btn-secondary:hover { background: var(--border); }
        .btn-small { padding: 6px 12px; font-size: 0.8rem; }

        .status {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 0.8rem;
            background: var(--bg);
        }

        .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-dim); }
        .status-dot.connected { background: var(--success); }
        .status-dot.error { background: var(--error); }

        .url-row { display: flex; gap: 12px; margin-top: 12px; }

        .main-content {
            display: flex;
            gap: 16px;
            width: 100%;
            max-width: 1100px;
            flex: 1;
            min-height: 0;
        }

        .left-panel { flex: 1; display: flex; flex-direction: column; gap: 16px; min-width: 0; }

        .player-wrapper {
            position: relative;
            width: 100%;
            aspect-ratio: 16/9;
            background: #000;
            border-radius: 12px;
            overflow: hidden;
            display: none;
        }

        .player-wrapper.active { display: block; }

        #playerContainer { width: 100%; height: 100%; }
        #playerContainer > div, #playerContainer > iframe { width: 100% !important; height: 100% !important; }

        video, iframe { width: 100%; height: 100%; display: block; border: none; }

        .sync-toast {
            position: absolute;
            top: 12px;
            left: 50%;
            transform: translateX(-50%) translateY(-50px);
            background: rgba(0,0,0,0.85);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 0.8rem;
            opacity: 0;
            transition: transform 0.3s, opacity 0.3s;
            pointer-events: none;
            z-index: 10;
        }

        .sync-toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }

        .viewers {
            position: absolute;
            top: 12px;
            right: 12px;
            background: rgba(0,0,0,0.7);
            padding: 6px 12px;
            border-radius: 16px;
            font-size: 0.75rem;
            color: var(--text-dim);
            z-index: 10;
        }

        .queue-panel {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 12px;
            display: none;
        }

        .queue-panel.active { display: block; }

        .queue-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .queue-header h3 { font-size: 0.85rem; font-weight: 500; color: var(--text-dim); }

        .queue-list { display: flex; flex-direction: column; gap: 6px; max-height: 150px; overflow-y: auto; }

        .queue-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px;
            background: var(--bg);
            border-radius: 6px;
            font-size: 0.8rem;
        }

        .queue-item.now-playing { border: 1px solid var(--accent); background: rgba(99, 102, 241, 0.1); }
        .queue-item .index { color: var(--text-dim); font-weight: 500; min-width: 20px; }
        .queue-item .url { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .queue-item.now-playing .index { color: var(--accent); }
        .queue-empty { color: var(--text-dim); font-size: 0.8rem; text-align: center; padding: 16px; }

        .btn-skip { background: var(--error); }
        .btn-skip:hover { background: #dc2626; }

        .chat-panel {
            width: 300px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            display: none;
            flex-direction: column;
            max-height: 500px;
        }

        .chat-panel.active { display: flex; }

        .chat-header { padding: 12px; border-bottom: 1px solid var(--border); font-size: 0.85rem; font-weight: 500; color: var(--text-dim); }

        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            min-height: 200px;
        }

        .chat-msg { font-size: 0.85rem; line-height: 1.4; word-wrap: break-word; }
        .chat-msg .nick { font-weight: 600; color: var(--accent); }
        .chat-msg .text { color: var(--text); }
        .chat-msg.system { color: var(--text-dim); font-style: italic; font-size: 0.8rem; }

        .chat-input-row { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--border); }
        .chat-input-row input { flex: 1; padding: 8px 12px; font-size: 0.85rem; }
        .chat-input-row button { padding: 8px 14px; }

        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 100;
        }

        .modal-overlay.active { display: flex; }

        .modal {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            max-width: 400px;
            width: 90%;
        }

        .modal h2 { font-size: 1.1rem; margin-bottom: 16px; }
        .modal .link-box { display: flex; gap: 8px; }
        .modal input { flex: 1; font-size: 0.85rem; }
        .modal .close-btn { margin-top: 16px; width: 100%; }

        @media (max-width: 800px) {
            .main-content { flex-direction: column; }
            .chat-panel { width: 100%; max-height: 300px; }
            .top-controls { flex-direction: column; align-items: stretch; }
            .code-input, .nick-input { width: 100%; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Sync<span>Stream</span></h1>
            <p class="subtitle">Watch together, anywhere</p>
        </header>

        <div class="card">
            <div class="top-controls">
                <label>Room</label>
                <input type="text" id="roomCode" class="code-input" maxlength="6" placeholder="ABC123">
                <button id="newRoomBtn" class="btn-secondary btn-small">New</button>
                <button id="joinBtn" class="btn-small">Join</button>
                <button id="inviteBtn" class="btn-secondary btn-small">Invite Link</button>
                <label>Nickname</label>
                <input type="text" id="nickname" class="nick-input" maxlength="20" placeholder="Guest">
                <div class="status">
                    <div class="status-dot" id="statusDot"></div>
                    <span id="statusText">Not connected</span>
                </div>
            </div>
            <div class="url-row">
                <input type="text" id="mediaUrl" class="url-input" placeholder="Paste YouTube, Twitch, or direct video URL...">
                <button id="addBtn" disabled>Add to Queue</button>
            </div>
        </div>

        <div class="main-content">
            <div class="left-panel">
                <div class="player-wrapper" id="playerWrapper">
                    <div id="playerContainer"></div>
                    <div class="sync-toast" id="syncToast"></div>
                    <div class="viewers" id="viewers">1 viewer</div>
                </div>

                <div class="queue-panel" id="queuePanel">
                    <div class="queue-header">
                        <h3>Queue</h3>
                        <button id="skipBtn" class="btn-skip btn-small">Skip</button>
                    </div>
                    <div class="queue-list" id="queueList">
                        <div class="queue-empty">Queue is empty</div>
                    </div>
                </div>
            </div>

            <div class="chat-panel" id="chatPanel">
                <div class="chat-header">Chat</div>
                <div class="chat-messages" id="chatMessages"></div>
                <div class="chat-input-row">
                    <input type="text" id="chatInput" placeholder="Type a message..." maxlength="500">
                    <button id="sendBtn">Send</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal-overlay" id="inviteModal">
        <div class="modal">
            <h2>Invite Friends</h2>
            <div class="link-box">
                <input type="text" id="inviteLink" readonly>
                <button id="copyLinkBtn">Copy</button>
            </div>
            <button class="btn-secondary close-btn" id="closeModalBtn">Close</button>
        </div>
    </div>

    <script>
        const $ = id => document.getElementById(id);

        const roomInput = $('roomCode');
        const nickInput = $('nickname');
        const urlInput = $('mediaUrl');
        const chatInput = $('chatInput');
        const newRoomBtn = $('newRoomBtn');
        const joinBtn = $('joinBtn');
        const inviteBtn = $('inviteBtn');
        const addBtn = $('addBtn');
        const skipBtn = $('skipBtn');
        const sendBtn = $('sendBtn');
        const statusDot = $('statusDot');
        const statusText = $('statusText');
        const playerWrapper = $('playerWrapper');
        const playerContainer = $('playerContainer');
        const syncToast = $('syncToast');
        const viewersEl = $('viewers');
        const queuePanel = $('queuePanel');
        const queueList = $('queueList');
        const chatPanel = $('chatPanel');
        const chatMessages = $('chatMessages');
        const inviteModal = $('inviteModal');
        const inviteLink = $('inviteLink');
        const copyLinkBtn = $('copyLinkBtn');
        const closeModalBtn = $('closeModalBtn');

        let socket = null;
        let isRemoteAction = false;
        let lastSentSeek = 0;
        let pingInterval = null;
        let latency = 0;
        let queue = [];
        let currentMedia = null;
        let ytPlayer = null;
        let ytReady = false;

        // Load YouTube API
        const ytScript = document.createElement('script');
        ytScript.src = 'https://www.youtube.com/iframe_api';
        document.head.appendChild(ytScript);
        window.onYouTubeIframeAPIReady = () => { ytReady = true; };

        function generateCode() {
            const chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
            return Array.from({length: 6}, () => chars[Math.floor(Math.random() * 36)]).join('');
        }

        function getNick() { return nickInput.value.trim() || 'Guest'; }

        function nickColor(nick) {
            // Dracula theme accent colors
            const palette = [
                '#ff79c6', // pink
                '#bd93f9', // purple
                '#8be9fd', // cyan
                '#50fa7b', // green
                '#ffb86c', // orange
                '#ff5555', // red
                '#f1fa8c', // yellow
                '#6272a4', // comment (muted blue)
            ];
            // Hash the nickname
            let hash = 0;
            for (let i = 0; i < nick.length; i++) {
                hash = ((hash << 5) - hash) + nick.charCodeAt(i);
                hash = hash & hash;
            }
            return palette[Math.abs(hash) % palette.length];
        }

        // Check URL params for room code
        const urlParams = new URLSearchParams(window.location.search);
        const roomParam = urlParams.get('room');
        if (roomParam && /^[0-9A-Z]{6}$/i.test(roomParam)) {
            roomInput.value = roomParam.toUpperCase();
        } else {
            roomInput.value = generateCode();
        }

        newRoomBtn.addEventListener('click', () => { roomInput.value = generateCode(); connect(); });
        roomInput.addEventListener('input', e => { e.target.value = e.target.value.toUpperCase().replace(/[^0-9A-Z]/g, '').slice(0, 6); });
        joinBtn.addEventListener('click', connect);
        addBtn.addEventListener('click', addToQueue);
        skipBtn.addEventListener('click', () => {
            if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'skip', nick: getNick() }));
        });
        sendBtn.addEventListener('click', sendChat);
        chatInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendChat(); });
        urlInput.addEventListener('keypress', e => { if (e.key === 'Enter') addToQueue(); });

        inviteBtn.addEventListener('click', () => {
            inviteLink.value = `${location.origin}${location.pathname}?room=${roomInput.value}`;
            inviteModal.classList.add('active');
        });
        copyLinkBtn.addEventListener('click', () => {
            inviteLink.select();
            document.execCommand('copy');
            copyLinkBtn.textContent = 'Copied!';
            setTimeout(() => copyLinkBtn.textContent = 'Copy', 2000);
        });
        closeModalBtn.addEventListener('click', () => inviteModal.classList.remove('active'));
        inviteModal.addEventListener('click', e => { if (e.target === inviteModal) inviteModal.classList.remove('active'); });

        setTimeout(connect, 100);

        function setStatus(text, state = 'disconnected') {
            statusText.textContent = text;
            statusDot.className = 'status-dot' + (state === 'connected' ? ' connected' : state === 'error' ? ' error' : '');
        }

        function showToast(msg) {
            syncToast.textContent = msg;
            syncToast.classList.add('show');
            setTimeout(() => syncToast.classList.remove('show'), 2000);
        }

        function addToQueue() {
            const url = urlInput.value.trim();
            if (!url || !socket || socket.readyState !== WebSocket.OPEN) return;
            socket.send(JSON.stringify({ type: 'queue', url, nick: getNick() }));
            urlInput.value = '';
        }

        function sendChat() {
            const text = chatInput.value.trim();
            if (!text || !socket || socket.readyState !== WebSocket.OPEN) return;
            socket.send(JSON.stringify({ type: 'chat', text, nick: getNick() }));
            chatInput.value = '';
        }

        function addChatMessage(nick, text, isSystem = false) {
            const div = document.createElement('div');
            div.className = 'chat-msg' + (isSystem ? ' system' : '');
            if (isSystem) {
                div.textContent = text;
            } else {
                const color = nickColor(nick);
                div.innerHTML = `<span class="nick" style="color:${color}">${escapeHtml(nick)}:</span> <span class="text">${escapeHtml(text)}</span>`;
            }
            chatMessages.appendChild(div);
            chatMessages.scrollTop = chatMessages.scrollHeight;
            while (chatMessages.children.length > 100) chatMessages.removeChild(chatMessages.firstChild);
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function renderQueue() {
            queuePanel.classList.toggle('active', queue.length > 0 || socket?.readyState === WebSocket.OPEN);
            if (queue.length === 0) {
                queueList.innerHTML = '<div class="queue-empty">Queue is empty</div>';
                return;
            }
            queueList.innerHTML = queue.map((item, i) => {
                const display = item.display || item.url || 'Unknown';
                const shortDisplay = display.length > 45 ? display.slice(0, 42) + '...' : display;
                const isPlaying = i === 0;
                return `<div class="queue-item${isPlaying ? ' now-playing' : ''}">
                    <span class="index">${isPlaying ? '▶' : i + 1}</span>
                    <span class="url" title="${escapeHtml(display)}">${escapeHtml(shortDisplay)}</span>
                </div>`;
            }).join('');
        }

        function connect() {
            const code = roomInput.value.trim();
            if (code.length !== 6) { setStatus('Enter 6-char code', 'error'); return; }
            if (socket) socket.close();

            setStatus('Connecting...', 'disconnected');
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            socket = new WebSocket(`${protocol}//${location.host}/ws/${code}`);

            socket.onopen = () => {
                setStatus('Connected', 'connected');
                addBtn.disabled = false;
                chatPanel.classList.add('active');
                renderQueue();
                socket.send(JSON.stringify({ type: 'join', nick: getNick() }));
                pingInterval = setInterval(() => {
                    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping', t: Date.now() }));
                }, 5000);
                socket.send(JSON.stringify({ type: 'ping', t: Date.now() }));
            };

            socket.onclose = () => { setStatus('Disconnected', 'disconnected'); addBtn.disabled = true; clearInterval(pingInterval); };
            socket.onerror = () => setStatus('Connection failed', 'error');
            socket.onmessage = e => handleMessage(JSON.parse(e.data));
        }

        function handleMessage(msg) {
            switch (msg.type) {
                case 'pong': latency = (Date.now() - msg.t) / 2; break;

                case 'state':
                    viewersEl.textContent = msg.viewers + ' viewer' + (msg.viewers === 1 ? '' : 's');
                    queue = msg.queue || [];
                    renderQueue();
                    if (msg.current) {
                        loadMedia(msg.current);
                        if (msg.position > 0) seekTo(msg.position);
                        if (msg.playing) playMedia();
                    }
                    break;

                case 'load':
                    queue = msg.queue || [];
                    renderQueue();
                    if (msg.current) { loadMedia(msg.current); showToast('Now playing'); }
                    else { clearPlayer(); showToast('Queue empty'); }
                    break;

                case 'queue_update':
                    queue = msg.queue || [];
                    renderQueue();
                    if (msg.nick) addChatMessage(null, `${msg.nick} added to queue`, true);
                    break;

                case 'play':
                    isRemoteAction = true;
                    seekTo(msg.position + latency / 1000);
                    playMedia();
                    showToast(msg.nick ? `${msg.nick} played` : 'Playing');
                    setTimeout(() => isRemoteAction = false, 100);
                    break;

                case 'pause':
                    isRemoteAction = true;
                    pauseMedia();
                    seekTo(msg.position);
                    showToast(msg.nick ? `${msg.nick} paused` : 'Paused');
                    setTimeout(() => isRemoteAction = false, 100);
                    break;

                case 'seek':
                    if (Math.abs(getPosition() - msg.position) > 0.5) {
                        isRemoteAction = true;
                        seekTo(msg.position);
                        showToast('Synced');
                        setTimeout(() => isRemoteAction = false, 100);
                    }
                    break;

                case 'viewers':
                    viewersEl.textContent = msg.count + ' viewer' + (msg.count === 1 ? '' : 's');
                    break;

                case 'chat': addChatMessage(msg.nick, msg.text); break;
                case 'system': addChatMessage(null, msg.text, true); break;
            }
        }

        function loadMedia(media) {
            currentMedia = media;
            playerWrapper.classList.add('active');
            playerContainer.innerHTML = '';

            if (media.type === 'youtube') {
                if (ytReady) {
                    const div = document.createElement('div');
                    div.id = 'ytplayer';
                    playerContainer.appendChild(div);
                    ytPlayer = new YT.Player('ytplayer', {
                        videoId: media.id,
                        playerVars: { autoplay: 0, controls: 1, rel: 0 },
                        events: { onStateChange: onYTStateChange }
                    });
                } else {
                    playerContainer.innerHTML = `<iframe src="https://www.youtube.com/embed/${media.id}?enablejsapi=1" allowfullscreen></iframe>`;
                }
            } else if (media.type === 'twitch_vod') {
                playerContainer.innerHTML = `<iframe src="https://player.twitch.tv/?video=${media.id}&parent=${location.hostname}&autoplay=false" allowfullscreen></iframe>`;
            } else if (media.type === 'twitch_live') {
                playerContainer.innerHTML = `<iframe src="https://player.twitch.tv/?channel=${media.id}&parent=${location.hostname}&autoplay=false" allowfullscreen></iframe>`;
            } else {
                const video = document.createElement('video');
                video.id = 'directVideo';
                video.controls = true;
                video.playsInline = true;
                video.src = media.url;
                playerContainer.appendChild(video);

                video.addEventListener('play', () => {
                    if (!isRemoteAction && socket?.readyState === WebSocket.OPEN)
                        socket.send(JSON.stringify({ type: 'play', position: video.currentTime, nick: getNick() }));
                });
                video.addEventListener('pause', () => {
                    if (!isRemoteAction && socket?.readyState === WebSocket.OPEN)
                        socket.send(JSON.stringify({ type: 'pause', position: video.currentTime, nick: getNick() }));
                });
                video.addEventListener('seeked', () => {
                    const now = Date.now();
                    if (!isRemoteAction && socket?.readyState === WebSocket.OPEN && now - lastSentSeek > 300) {
                        lastSentSeek = now;
                        socket.send(JSON.stringify({ type: 'seek', position: video.currentTime }));
                    }
                });
                video.addEventListener('ended', () => {
                    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ended' }));
                });
            }
        }

        function onYTStateChange(e) {
            if (isRemoteAction || !socket || socket.readyState !== WebSocket.OPEN) return;
            if (e.data === YT.PlayerState.PLAYING) {
                socket.send(JSON.stringify({ type: 'play', position: ytPlayer.getCurrentTime(), nick: getNick() }));
            } else if (e.data === YT.PlayerState.PAUSED) {
                socket.send(JSON.stringify({ type: 'pause', position: ytPlayer.getCurrentTime(), nick: getNick() }));
            } else if (e.data === YT.PlayerState.ENDED) {
                socket.send(JSON.stringify({ type: 'ended' }));
            }
        }

        function clearPlayer() {
            playerContainer.innerHTML = '';
            playerWrapper.classList.remove('active');
            ytPlayer = null;
            currentMedia = null;
        }

        function playMedia() {
            if (ytPlayer?.playVideo) ytPlayer.playVideo();
            const v = $('directVideo');
            if (v) v.play().catch(() => {});
        }

        function pauseMedia() {
            if (ytPlayer?.pauseVideo) ytPlayer.pauseVideo();
            const v = $('directVideo');
            if (v) v.pause();
        }

        function seekTo(time) {
            if (ytPlayer?.seekTo) ytPlayer.seekTo(time, true);
            const v = $('directVideo');
            if (v) v.currentTime = time;
        }

        function getPosition() {
            if (ytPlayer?.getCurrentTime) return ytPlayer.getCurrentTime();
            const v = $('directVideo');
            if (v) return v.currentTime;
            return 0;
        }
    </script>
</body>
</html>
"""

@app.get("/")
async def get_page():
    """Serve the main HTML page."""
    return HTMLResponse(HTML)

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
