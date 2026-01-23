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
        const policyModal = $('policyModal');
        const closePolicyBtn = $('closePolicyBtn');

        let socket = null;
        let isRemoteAction = false;
        let lastSentSeek = 0;
        let pingInterval = null;
        let syncInterval = null;
        let latency = 0;
        let queue = [];
        let currentMedia = null;
        let ytPlayer = null;
        let ytReady = false;
        let isHost = false;

        // Sync tolerance - how far off (in seconds) before forcing a sync
        const SYNC_TOLERANCE = 3.0;  // Increased from 0.5 to 3 seconds
        // How often host reports position (ms)
        const HOST_REPORT_INTERVAL = 5000;
        // How often non-hosts check sync (ms)  
        const SYNC_CHECK_INTERVAL = 10000;

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

        closePolicyBtn.addEventListener('click', () => policyModal.classList.remove('active'));
        policyModal.addEventListener('click', e => { if (e.target === policyModal) policyModal.classList.remove('active'); });

        // Policy modal link is in footer which loads after script, so wait for DOM
        document.addEventListener('DOMContentLoaded', () => {
            const policyLink = $('policyLink');
            if (policyLink) {
                policyLink.addEventListener('click', (e) => {
                    e.preventDefault();
                    policyModal.classList.add('active');
                });
            }
        });

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

        function updateHostIndicator() {
            // Update status to show host status
            if (socket?.readyState === WebSocket.OPEN) {
                const hostText = isHost ? ' (Host)' : '';
                setStatus('Connected' + hostText, 'connected');
            }
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

        function startSyncIntervals() {
            clearInterval(syncInterval);
            
            if (isHost) {
                // Host periodically reports their position
                syncInterval = setInterval(() => {
                    if (socket?.readyState === WebSocket.OPEN && currentMedia) {
                        socket.send(JSON.stringify({ 
                            type: 'host_position', 
                            position: getPosition() 
                        }));
                    }
                }, HOST_REPORT_INTERVAL);
            } else {
                // Non-hosts periodically request sync
                syncInterval = setInterval(() => {
                    if (socket?.readyState === WebSocket.OPEN && currentMedia) {
                        socket.send(JSON.stringify({ type: 'sync_request' }));
                    }
                }, SYNC_CHECK_INTERVAL);
            }
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

            socket.onclose = () => { 
                setStatus('Disconnected', 'disconnected'); 
                addBtn.disabled = true; 
                clearInterval(pingInterval); 
                clearInterval(syncInterval);
                isHost = false;
            };
            socket.onerror = () => setStatus('Connection failed', 'error');
            socket.onmessage = e => handleMessage(JSON.parse(e.data));
        }

        function handleMessage(msg) {
            switch (msg.type) {
                case 'pong': latency = (Date.now() - msg.t) / 2; break;

                case 'state':
                    viewersEl.textContent = msg.viewers + ' viewer' + (msg.viewers === 1 ? '' : 's');
                    queue = msg.queue || [];
                    isHost = msg.isHost || false;
                    updateHostIndicator();
                    startSyncIntervals();
                    renderQueue();
                    if (msg.current) {
                        loadMedia(msg.current);
                        if (msg.position > 0) seekTo(msg.position);
                        if (msg.playing) playMedia();
                    }
                    break;

                case 'host_update':
                    const wasHost = isHost;
                    isHost = msg.isHost || false;
                    updateHostIndicator();
                    if (wasHost !== isHost) {
                        startSyncIntervals();
                        if (isHost) {
                            showToast('You are now the host');
                        }
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
                    // Use increased tolerance
                    if (Math.abs(getPosition() - msg.position) > SYNC_TOLERANCE) {
                        isRemoteAction = true;
                        seekTo(msg.position);
                        showToast('Synced');
                        setTimeout(() => isRemoteAction = false, 100);
                    }
                    break;

                case 'sync_response':
                    // Non-host received sync info - check if we need to adjust
                    if (!isHost && currentMedia) {
                        const diff = Math.abs(getPosition() - msg.position);
                        if (diff > SYNC_TOLERANCE) {
                            isRemoteAction = true;
                            seekTo(msg.position);
                            showToast('Synced to host');
                            setTimeout(() => isRemoteAction = false, 100);
                        }
                        // Also sync play state
                        if (msg.playing && isPaused()) {
                            isRemoteAction = true;
                            playMedia();
                            setTimeout(() => isRemoteAction = false, 100);
                        } else if (!msg.playing && !isPaused()) {
                            isRemoteAction = true;
                            pauseMedia();
                            setTimeout(() => isRemoteAction = false, 100);
                        }
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

        function isPaused() {
            if (ytPlayer?.getPlayerState) {
                const state = ytPlayer.getPlayerState();
                return state === YT.PlayerState.PAUSED || state === YT.PlayerState.CUED || state === -1;
            }
            const v = $('directVideo');
            if (v) return v.paused;
            return true;
        }