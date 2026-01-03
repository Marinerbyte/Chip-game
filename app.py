"""
BOMB CHIP CHALLENGE - ULTIMATE EDITION
Single File Application (Flask + Pillow + Threading)
Author: AI Architect
Version: 3.0 (Production Grade)
"""

import os
import io
import time
import uuid
import random
import threading
import base64
import math
import logging
from datetime import datetime
from functools import wraps

# --- 1. DEPENDENCY CHECK & SETUP ---
try:
    from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
except ImportError as e:
    print(f"CRITICAL ERROR: Missing libraries. Install them: pip install flask pillow gunicorn")
    exit(1)

# Initialize Flask
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'titan_os_secret_key_v99')

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 2. GLOBAL THREAD-SAFE DATABASE (IN-MEMORY) ---

# Thread Lock for Data Integrity (Prevents Race Conditions)
DATA_LOCK = threading.Lock()

# User Database: { user_id: { name, avatar, score, wins, losses, bombs, games_played, join_date } }
USERS_DB = {}

# Active Sessions: { user_id: GameInstance_Object }
GAME_SESSIONS = {}

# --- 3. GAME LOGIC ENGINE ---

class GameInstance:
    """
    Manages the state of a single game session.
    Encapsulated to ensure isolation per user.
    """
    def __init__(self, user_id, difficulty="normal"):
        self.user_id = user_id
        self.start_time = time.time()
        self.last_interaction = time.time()
        
        # Game Config
        self.total_chips = 12
        self.bomb_count = 3
        
        # Grid Generation (0=Safe, 1=Bomb)
        self.grid = [0] * (self.total_chips - self.bomb_count) + [1] * self.bomb_count
        random.shuffle(self.grid)
        
        # State Tracking
        self.revealed = [False] * self.total_chips
        self.status = "ACTIVE" # ACTIVE, WON, LOST
        self.score_gain = 0
        self.message = "Game Started. Good Luck."

    def process_move(self, chip_index):
        """Returns True if move valid, False otherwise."""
        self.last_interaction = time.time()
        
        if not (0 <= chip_index < self.total_chips):
            return "INVALID_INDEX"
        if self.revealed[chip_index]:
            return "ALREADY_OPEN"
        
        # Execute Move
        self.revealed[chip_index] = True
        is_bomb = (self.grid[chip_index] == 1)
        
        if is_bomb:
            self.status = "LOST"
            self.score_gain = -50
            return "BOMB"
        else:
            # Check Win Condition
            safe_chips_total = self.total_chips - self.bomb_count
            safe_chips_found = sum(1 for i in range(self.total_chips) if self.revealed[i] and self.grid[i] == 0)
            
            if safe_chips_found == safe_chips_total:
                self.status = "WON"
                self.score_gain = 100 # Jackpot
                return "WIN"
            
            self.score_gain = 10
            return "SAFE"

# --- 4. GRAPHICS ENGINE (PILLOW) ---

class GraphicsEngine:
    """
    Generates high-quality procedural assets on the fly.
    Uses gradients, shadows, and vector math for crisp visuals.
    """
    
    # Color Palette
    C_BG_START = (20, 20, 30)
    C_BG_END = (10, 10, 15)
    C_CHIP_BASE = (40, 45, 60)
    C_CHIP_SHADOW = (0, 0, 0, 100)
    C_CHIP_GLOW = (255, 255, 255, 30)
    C_TEXT = (255, 255, 255)
    C_SAFE = (0, 230, 118)   # Bright Green
    C_BOMB = (255, 23, 68)   # Bright Red
    C_GOLD = (255, 215, 0)
    
    @staticmethod
    def create_board_image(game: GameInstance):
        """Draws the complete game board state."""
        W, H = 800, 600
        # Create Background with subtle gradient
        img = Image.new("RGBA", (W, H), GraphicsEngine.C_BG_START)
        draw = ImageDraw.Draw(img)
        
        # Draw decorative grid lines
        for i in range(0, W, 40):
            draw.line([(i, 0), (i, H)], fill=(255, 255, 255, 5), width=1)
        for i in range(0, H, 40):
            draw.line([(0, i), (W, i)], fill=(255, 255, 255, 5), width=1)

        # Layout Calculation
        cols = 4
        rows = 3
        margin_x = 50
        margin_y = 60
        gap = 20
        
        # Calculate chip size dynamic
        avail_w = W - (margin_x * 2) - (gap * (cols - 1))
        chip_size = avail_w // cols
        
        # Font Handling
        try:
            # Try loading a system font if available for better look
            font_size = int(chip_size * 0.4)
            font = ImageFont.truetype("arial.ttf", font_size)
            status_font = ImageFont.truetype("arial.ttf", 30)
        except:
            font = ImageFont.load_default()
            status_font = ImageFont.load_default()

        # Render Chips
        for idx in range(game.total_chips):
            r = idx // cols
            c = idx % cols
            
            x = margin_x + (c * (chip_size + gap))
            y = margin_y + (r * (chip_size + gap))
            
            box = (x, y, x + chip_size, y + chip_size)
            
            is_revealed = game.revealed[idx]
            is_bomb = (game.grid[idx] == 1)
            
            if not is_revealed:
                GraphicsEngine._draw_closed_chip(draw, box, str(idx + 1), font)
            else:
                if is_bomb:
                    GraphicsEngine._draw_bomb(draw, box)
                else:
                    GraphicsEngine._draw_safe_check(draw, box)

        # Draw Status Bar
        bar_h = 60
        draw.rectangle([(0, H - bar_h), (W, H)], fill=(0, 0, 0, 200))
        
        status_msg = f"SAFE: +10 | BOMB: -50 | WIN: +100"
        status_color = (200, 200, 200)
        
        if game.status == "WON":
            status_msg = "VICTORY! ALL SAFE CHIPS FOUND!"
            status_color = GraphicsEngine.C_SAFE
        elif game.status == "LOST":
            status_msg = "MISSION FAILED! BOMB DETONATED!"
            status_color = GraphicsEngine.C_BOMB
            
        # Center Text logic
        bbox = draw.textbbox((0, 0), status_msg, font=status_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text(((W - text_w) / 2, H - bar_h + (bar_h - text_h) / 2 - 5), status_msg, fill=status_color, font=status_font)

        # Final Polish
        return GraphicsEngine._to_base64(img)

    @staticmethod
    def _draw_closed_chip(draw, box, text, font):
        x, y, x2, y2 = box
        w = x2 - x
        radius = 20
        
        # Shadow
        shadow_offset = 8
        draw.rounded_rectangle([x+shadow_offset, y+shadow_offset, x2+shadow_offset, y2+shadow_offset], radius=radius, fill=GraphicsEngine.C_CHIP_SHADOW)
        
        # Base
        draw.rounded_rectangle([x, y, x2, y2], radius=radius, fill=GraphicsEngine.C_CHIP_BASE)
        
        # Inner Bevel (Highlight top-left)
        draw.arc([x, y, x2, y2], 180, 270, fill=GraphicsEngine.C_CHIP_GLOW, width=3)
        
        # Text
        bbox = draw.textbbox((0,0), text, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.text((x + (w-tw)/2, y + (w-th)/2 - 5), text, fill=GraphicsEngine.C_TEXT, font=font)

    @staticmethod
    def _draw_safe_check(draw, box):
        x, y, x2, y2 = box
        # Dimmed Base
        draw.rounded_rectangle([x, y, x2, y2], radius=20, fill=(30, 35, 45))
        
        # Draw Tick
        w = x2 - x
        # Points for tick
        p1 = (x + w*0.25, y + w*0.55)
        p2 = (x + w*0.45, y + w*0.75)
        p3 = (x + w*0.8, y + w*0.3)
        
        draw.line([p1, p2, p3], fill=GraphicsEngine.C_SAFE, width=10, joint='curve')

    @staticmethod
    def _draw_bomb(draw, box):
        x, y, x2, y2 = box
        # Dimmed Base
        draw.rounded_rectangle([x, y, x2, y2], radius=20, fill=(40, 20, 20))
        
        cx, cy = (x+x2)/2, (y+y2)/2
        radius = (x2-x) * 0.35
        
        # Explosion Polygon (Starburst)
        points = []
        spikes = 16
        inner_r = radius * 0.5
        outer_r = radius * 1.2
        
        import math
        for i in range(spikes * 2):
            angle = (math.pi * i) / spikes
            r = outer_r if i % 2 == 0 else inner_r
            px = cx + math.cos(angle) * r
            py = cy + math.sin(angle) * r
            points.append((px, py))
            
        draw.polygon(points, fill=GraphicsEngine.C_BOMB, outline=(255, 200, 0))
        draw.ellipse([cx-5, cy-5, cx+5, cy+5], fill=(255, 255, 0))

    @staticmethod
    def _to_base64(img):
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

# --- 5. BACKEND CONTROLLERS ---

def get_stats(uid, name="Agent", avatar=""):
    """Thread-safe retrieval/creation of user stats."""
    with DATA_LOCK:
        if uid not in USERS_DB:
            USERS_DB[uid] = {
                "name": name,
                "avatar": avatar,
                "score": 0,
                "wins": 0,
                "losses": 0,
                "bombs": 0,
                "total_games": 0,
                "created_at": str(datetime.now())
            }
        return USERS_DB[uid]

def update_stats(uid, result, score_change):
    """Thread-safe update of stats."""
    with DATA_LOCK:
        if uid in USERS_DB:
            u = USERS_DB[uid]
            u['score'] = max(0, u['score'] + score_change) # No negative scores
            if result == "WIN": u['wins'] += 1
            if result == "LOST": 
                u['losses'] += 1
                u['bombs'] += 1
            if result in ["WIN", "LOST"]:
                u['total_games'] += 1

def handle_command(uid, cmd, name, avatar):
    """Main Command Processor."""
    cmd = cmd.strip().lower()
    
    # 1. Start Game
    if cmd == "/bombchip":
        GAME_SESSIONS[uid] = GameInstance(uid)
        img = GraphicsEngine.create_board_image(GAME_SESSIONS[uid])
        return [{
            "type": "image", 
            "content": img, 
            "caption": "⚠️ <b>MISSION START</b><br>There are 3 Hidden Bombs.<br>Tap a number or type 1-12."
        }]

    # 2. Game Move
    if uid in GAME_SESSIONS and GAME_SESSIONS[uid].status == "ACTIVE":
        try:
            val = int(cmd) - 1
            game = GAME_SESSIONS[uid]
            
            res = game.process_move(val)
            
            if res == "INVALID_INDEX":
                return [{"type": "text", "content": "🚫 Invalid Chip ID. Range: 1-12"}]
            if res == "ALREADY_OPEN":
                return [{"type": "text", "content": "🚫 Chip already eaten. Pick another."}]
            
            # Valid Move
            update_stats(uid, game.status, game.score_gain)
            img = GraphicsEngine.create_board_image(game)
            
            if game.status == "WON":
                del GAME_SESSIONS[uid] # Cleanup
                return [{"type": "image", "content": img, "caption": "🏆 <b>MISSION ACCOMPLISHED!</b><br>+100 Bonus. /bombchip to replay."}]
            
            if game.status == "LOST":
                del GAME_SESSIONS[uid] # Cleanup
                return [{"type": "image", "content": img, "caption": "💥 <b>CRITICAL FAILURE!</b><br>Bomb Detonated. Score Deducted."}]
                
            return [{"type": "image", "content": img, "caption": "✅ <b>SAFE!</b> Proceed with caution..."}]

        except ValueError:
            pass # Input was not a number

    # 3. Standard Commands
    if cmd == "/score":
        s = get_stats(uid)
        return [{"type": "text", "content": f"📝 <b>DOSSIER: {s['name']}</b><br>Score: {s['score']}<br>Wins: {s['wins']} | Losses: {s['losses']}"}]
    
    if cmd == "/ladder" or cmd == "/leaderboard":
        return [{"type": "link", "content": "/leaderboard", "text": "🌐 OPEN GLOBAL LEADERBOARD"}]

    return [{"type": "text", "content": "🤖 System: Unknown Command.<br>Type <b>/bombchip</b> to start."}]

# --- 6. FLASK ROUTES ---

@app.route('/')
def index():
    if 'uid' in session: return redirect(url_for('gameroom'))
    return render_template_string(HTML_LOGIN)

@app.route('/login', methods=['POST'])
def login():
    name = request.form.get('username', 'Agent').strip()
    if not name: return redirect(url_for('index'))
    
    # Create Session
    uid = str(uuid.uuid4())
    session['uid'] = uid
    session['name'] = name
    # Generate unique avatar URL
    session['avatar'] = f"https://api.dicebear.com/7.x/bottts/svg?seed={uid}"
    
    # Init Stats
    get_stats(uid, name, session['avatar'])
    return redirect(url_for('gameroom'))

@app.route('/game')
def gameroom():
    if 'uid' not in session: return redirect(url_for('index'))
    return render_template_string(HTML_GAME, 
                                  username=session['name'], 
                                  avatar=session['avatar'])

@app.route('/leaderboard')
def leaderboard():
    return render_template_string(HTML_LEADERBOARD)

@app.route('/api/message', methods=['POST'])
def api_msg():
    if 'uid' not in session: return jsonify({"error": "Auth Required"}), 401
    
    data = request.json
    msg = data.get('message', '')
    responses = handle_command(session['uid'], msg, session['name'], session['avatar'])
    return jsonify({"data": responses})

@app.route('/api/ladder')
def api_ladder():
    with DATA_LOCK:
        # Sort by Score DESC, then Wins DESC
        all_users = list(USERS_DB.values())
        all_users.sort(key=lambda x: (x['score'], x['wins']), reverse=True)
        
        # Add Rank & Win Rate
        export = []
        for i, u in enumerate(all_users):
            wr = 0
            if u['total_games'] > 0:
                wr = int((u['wins'] / u['total_games']) * 100)
            
            entry = u.copy()
            entry['rank'] = i + 1
            entry['win_rate'] = wr
            export.append(entry)
            
        return jsonify(export[:50]) # Top 50

# --- 7. UI TEMPLATES (EMBEDDED HTML/CSS/JS) ---

HTML_LOGIN = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BombChip // Access</title>
    <style>
        :root { --p: #00f3ff; --s: #ff003c; --bg: #050505; }
        body { margin:0; font-family:'Segoe UI', sans-serif; background:var(--bg); color:#fff; display:flex; justify-content:center; align-items:center; height:100vh; overflow:hidden; }
        
        /* Animated Background Grid */
        .grid-bg { position:fixed; top:0; left:0; width:200vw; height:200vh; background: linear-gradient(rgba(0, 243, 255, 0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 243, 255, 0.05) 1px, transparent 1px); background-size:30px 30px; transform: perspective(500px) rotateX(60deg) translateY(-100px) translateZ(-200px); animation: move 20s linear infinite; z-index:-1; }
        @keyframes move { 0% {transform: perspective(500px) rotateX(60deg) translateY(0);} 100% {transform: perspective(500px) rotateX(60deg) translateY(30px);} }
        
        .card { width:320px; background:rgba(10,10,15,0.9); border:1px solid #333; padding:40px; border-radius:12px; backdrop-filter:blur(10px); box-shadow:0 0 30px rgba(0,0,0,0.8); text-align:center; position:relative; overflow:hidden; }
        .card::before { content:''; position:absolute; top:0; left:0; width:100%; height:3px; background:linear-gradient(90deg, var(--p), var(--s)); }
        
        h1 { margin:0 0 5px; font-weight:800; font-size:28px; letter-spacing:2px; }
        h1 span { color:var(--p); }
        .sub { color:#666; font-size:12px; margin-bottom:30px; text-transform:uppercase; letter-spacing:1px; }
        
        input { width:100%; padding:15px; background:#000; border:1px solid #333; color:var(--p); border-radius:6px; font-size:16px; margin-bottom:20px; box-sizing:border-box; outline:none; transition:0.3s; text-align:center; }
        input:focus { border-color:var(--p); box-shadow:0 0 15px rgba(0,243,255,0.1); }
        
        button { width:100%; padding:15px; background:rgba(0,243,255,0.1); border:1px solid var(--p); color:var(--p); font-weight:bold; font-size:14px; text-transform:uppercase; cursor:pointer; letter-spacing:1px; transition:0.3s; }
        button:hover { background:var(--p); color:#000; box-shadow:0 0 20px var(--p); }
    </style>
</head>
<body>
    <div class="grid-bg"></div>
    <div class="card">
        <h1>BOMB<span>CHIP</span></h1>
        <div class="sub">Tactical Defusal Simulation</div>
        <form action="/login" method="POST">
            <input type="text" name="username" placeholder="ENTER CODENAME" required maxlength="15" autocomplete="off">
            <button type="submit">Initialize Session</button>
        </form>
    </div>
</body>
</html>
"""

HTML_GAME = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>BombChip // Live</title>
    <style>
        :root { --bg: #09090b; --msg-bg: #18181b; --accent: #00f3ff; --me: #22c55e; }
        * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
        
        body { margin:0; background:var(--bg); color:#eee; font-family:'Segoe UI', Roboto, sans-serif; display:flex; flex-direction:column; height:100vh; overflow:hidden; }
        
        /* HEADER */
        header { height:60px; background:rgba(20,20,25,0.95); border-bottom:1px solid #333; display:flex; align-items:center; justify-content:space-between; padding:0 15px; z-index:10; box-shadow:0 2px 10px rgba(0,0,0,0.3); }
        .id-badge { display:flex; align-items:center; gap:10px; font-size:14px; font-weight:600; }
        .avatar { width:32px; height:32px; border-radius:50%; border:1px solid var(--accent); background:#000; }
        .rank-btn { color:#888; text-decoration:none; font-size:11px; border:1px solid #333; padding:5px 10px; border-radius:20px; transition:0.2s; }
        .rank-btn:hover { border-color:var(--accent); color:var(--accent); }

        /* CHAT */
        #feed { flex:1; overflow-y:auto; padding:20px 15px; display:flex; flex-direction:column; gap:15px; scroll-behavior:smooth; }
        
        .msg { display:flex; gap:10px; max-width:85%; animation:pop 0.3s ease; }
        .msg.me { align-self:flex-end; flex-direction:row-reverse; }
        .msg.bot { align-self:flex-start; }
        
        .bubble { padding:12px 16px; border-radius:12px; font-size:14px; line-height:1.5; position:relative; box-shadow:0 2px 5px rgba(0,0,0,0.2); }
        .me .bubble { background:rgba(34, 197, 94, 0.1); border:1px solid var(--me); color:#fff; border-bottom-right-radius:0; }
        .bot .bubble { background:#202025; border:1px solid #333; color:#ccc; border-bottom-left-radius:0; }
        
        .game-img { width:100%; max-width:320px; border-radius:8px; display:block; margin-bottom:8px; border:1px solid #444; }
        .caption { font-size:12px; color:var(--accent); font-weight:bold; border-top:1px solid #333; padding-top:6px; margin-top:6px; }

        /* ACTION BAR */
        .controls { background:#000; border-top:1px solid #333; padding:10px; z-index:20; }
        
        .keypad { display:grid; grid-template-columns:repeat(6, 1fr); gap:6px; margin-bottom:10px; transition:0.3s; height:0; overflow:hidden; opacity:0; }
        .keypad.show { height:auto; opacity:1; margin-bottom:10px; }
        
        .key { background:#1a1a1a; border:1px solid #333; color:#fff; padding:12px 0; text-align:center; border-radius:6px; font-weight:bold; cursor:pointer; font-family:monospace; font-size:16px; transition:0.2s; }
        .key:active { background:var(--accent); color:#000; transform:scale(0.95); }
        
        .input-row { display:flex; gap:10px; }
        input { flex:1; background:#111; border:1px solid #333; padding:14px; border-radius:30px; color:#fff; outline:none; font-size:16px; padding-left:20px; }
        input:focus { border-color:var(--accent); }
        button.send { width:50px; background:var(--accent); border:none; border-radius:50%; color:#000; font-size:20px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
        
        @keyframes pop { from{opacity:0; transform:translateY(10px);} to{opacity:1; transform:translateY(0);} }
    </style>
</head>
<body>

<header>
    <div class="id-badge">
        <img src="{{ avatar }}" class="avatar">
        <span>{{ username }}</span>
    </div>
    <a href="/leaderboard" target="_blank" class="rank-btn">🏆 LEADERBOARD</a>
</header>

<div id="feed">
    <div class="msg bot">
        <div class="bubble">
            System Online. Welcome, Agent <b>{{ username }}</b>.<br>
            Command: Type <b>/bombchip</b> to start mission.
        </div>
    </div>
</div>

<div class="controls">
    <div class="keypad" id="kp">
        <!-- JS Generates Keys -->
    </div>
    <div class="input-row">
        <input type="text" id="txt" placeholder="Command or Number..." autocomplete="off">
        <button class="send" onclick="send()">➤</button>
    </div>
</div>

<script>
    const feed = document.getElementById('feed');
    const txt = document.getElementById('txt');
    const kp = document.getElementById('kp');
    let gameActive = false;

    // Init Keypad
    let keysHTML = '';
    for(let i=1; i<=12; i++) {
        keysHTML += `<div class="key" onclick="tapKey(${i})">${i}</div>`;
    }
    kp.innerHTML = keysHTML;

    txt.addEventListener('keyup', (e) => { if(e.key === 'Enter') send(); });

    function tapKey(n) {
        txt.value = n;
        send();
    }

    async function send() {
        let val = txt.value.trim();
        if(!val) return;
        
        appendMsg(val, 'me');
        txt.value = '';
        
        // Toggle Keypad
        if(val.toLowerCase() === '/bombchip') {
            kp.classList.add('show');
            gameActive = true;
        }

        try {
            let req = await fetch('/api/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ message: val })
            });
            let res = await req.json();
            
            res.data.forEach(item => {
                if(item.type === 'text') appendMsg(item.content, 'bot');
                if(item.type === 'image') appendImg(item.content, item.caption);
                if(item.type === 'link') appendLink(item.content, item.text);
                
                // Auto hide keypad on game over
                if(item.caption && (item.caption.includes("VICTORY") || item.caption.includes("FAILURE"))) {
                    kp.classList.remove('show');
                }
            });
        } catch(e) {
            appendMsg("Error: Connection Failed", "bot");
        }
    }

    function appendMsg(html, type) {
        let d = document.createElement('div');
        d.className = 'msg ' + type;
        d.innerHTML = `<div class="bubble">${html}</div>`;
        feed.appendChild(d);
        scrollToBottom();
    }

    function appendImg(b64, cap) {
        let d = document.createElement('div');
        d.className = 'msg bot';
        d.innerHTML = `<div class="bubble" style="background:transparent; border:none; padding:0;">
            <img src="${b64}" class="game-img">
            <div class="bubble" style="margin-top:-5px; border-top-left-radius:0; border-top-right-radius:0;">${cap}</div>
        </div>`;
        feed.appendChild(d);
        scrollToBottom();
    }
    
    function appendLink(url, txt) {
         let d = document.createElement('div');
        d.className = 'msg bot';
        d.innerHTML = `<div class="bubble" style="background:linear-gradient(45deg, #00f3ff, #0066ff); color:#000; font-weight:bold; cursor:pointer;" onclick="window.open('${url}')">${txt}</div>`;
        feed.appendChild(d);
        scrollToBottom();
    }

    function scrollToBottom() {
        feed.scrollTop = feed.scrollHeight;
    }
</script>
</body>
</html>
"""

HTML_LEADERBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Global Rankings</title>
    <style>
        /* GLASSMORPHISM THEME */
        body {
            background: radial-gradient(circle at top center, #1a1a2e 0%, #000 100%);
            color: #fff;
            font-family: 'Segoe UI', Roboto, sans-serif;
            margin: 0; padding: 20px;
            min-height: 100vh;
        }

        .container { max-width: 600px; margin: 0 auto; }
        
        .header { text-align: center; margin-bottom: 30px; }
        h1 { font-size: 32px; margin: 0; background: linear-gradient(to right, #00f3ff, #0066ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; text-transform: uppercase; letter-spacing: 2px; }
        p { color: #888; font-size: 12px; letter-spacing: 1px; margin-top: 5px; }

        .glass-panel {
            background: rgba(255, 255, 255, 0.03);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 20px 50px rgba(0,0,0,0.5);
        }

        .row {
            display: flex; align-items: center;
            padding: 15px; margin-bottom: 10px;
            background: rgba(0,0,0,0.4);
            border: 1px solid transparent;
            border-radius: 12px;
            transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            opacity: 0; animation: slideIn 0.5s forwards;
        }
        
        .row:hover { transform: scale(1.02) translateX(5px); border-color: #00f3ff; background: rgba(0, 243, 255, 0.05); }

        .rank { width: 40px; font-size: 18px; font-weight: 800; color: #555; text-align: center; }
        .rank-1 { color: #ffd700; text-shadow: 0 0 10px rgba(255, 215, 0, 0.5); font-size: 22px; }
        .rank-2 { color: #c0c0c0; text-shadow: 0 0 10px rgba(192, 192, 192, 0.5); }
        .rank-3 { color: #cd7f32; text-shadow: 0 0 10px rgba(205, 127, 50, 0.5); }

        .u-pic { width: 45px; height: 45px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.1); margin: 0 15px; }
        .u-info { flex: 1; }
        .u-name { font-weight: 700; font-size: 15px; color: #eee; }
        .u-meta { font-size: 11px; color: #aaa; margin-top: 2px; display: flex; gap: 10px; }
        
        .u-score { text-align: right; }
        .score-val { font-size: 18px; font-weight: 700; color: #00f3ff; font-family: monospace; }
        .win-bar-bg { width: 60px; height: 4px; background: #333; margin-top: 5px; border-radius: 2px; margin-left: auto; }
        .win-bar-fill { height: 100%; background: #22c55e; border-radius: 2px; }

        @keyframes slideIn { from{opacity:0; transform:translateX(-20px);} to{opacity:1; transform:translateX(0);} }
    </style>
</head>
<body>

<div class="container">
    <div class="header">
        <h1>Elite Operatives</h1>
        <p>GLOBAL RANKINGS // UPDATED LIVE</p>
    </div>
    
    <div class="glass-panel" id="board">
        <div style="text-align:center; padding:20px; color:#666;">Decrypting Data...</div>
    </div>
</div>

<script>
    async function loadData() {
        try {
            let req = await fetch('/api/ladder');
            let users = await req.json();
            
            const board = document.getElementById('board');
            
            if(users.length === 0) {
                board.innerHTML = '<div style="text-align:center; padding:40px; color:#666;">No active agents found.<br>Be the first to play!</div>';
                return;
            }

            let html = '';
            users.forEach((u, index) => {
                let rankClass = '';
                if(u.rank === 1) rankClass = 'rank-1';
                if(u.rank === 2) rankClass = 'rank-2';
                if(u.rank === 3) rankClass = 'rank-3';

                let icon = '';
                if(u.rank === 1) icon = '👑 ';

                html += `
                <div class="row" style="animation-delay: ${index * 0.1}s">
                    <div class="rank ${rankClass}">${u.rank}</div>
                    <img src="${u.avatar}" class="u-pic">
                    <div class="u-info">
                        <div class="u-name">${icon}${u.name}</div>
                        <div class="u-meta">
                            <span>Wins: ${u.wins}</span>
                            <span style="color:#ff003c">Bombs: ${u.bombs}</span>
                        </div>
                    </div>
                    <div class="u-score">
                        <div class="score-val">${u.score}</div>
                        <div class="win-bar-bg">
                            <div class="win-bar-fill" style="width: ${u.win_rate}%"></div>
                        </div>
                        <div style="font-size:9px; color:#666; margin-top:2px;">${u.win_rate}% WR</div>
                    </div>
                </div>`;
            });
            
            board.innerHTML = html;
        } catch(e) {
            console.error("Load failed");
        }
    }

    loadData();
    setInterval(loadData, 5000); // Auto refresh
</script>

</body>
</html>
"""

if __name__ == '__main__':
    # Run in threaded mode to support multiple users simultaneously
    app.run(host='0.0.0.0', port=5000, threaded=True)