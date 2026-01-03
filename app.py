import os
import json
import time
import threading
import io
import random
import requests
import websocket
import psycopg2
from flask import Flask, render_template_string, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# =============================================================================
# 1. CONFIGURATION & DATABASE SETUP
# =============================================================================

# Database Connection (Prioritizes Neon PostgreSQL)
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_SQLITE = False if DATABASE_URL else True
DB_FILE = "titan_game.db"

# Font for Image Generator (Downloads automatically)
FONT_PATH = "gaming_font.ttf"
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/blacksopsone/BlackOpsOne-Regular.ttf"

def download_font():
    if not os.path.exists(FONT_PATH):
        try:
            r = requests.get(FONT_URL)
            with open(FONT_PATH, 'wb') as f: f.write(r.content)
            print(">> Custom Font Downloaded.")
        except: print(">> Font download failed, using default.")

download_font()

def get_db_connection():
    if USE_SQLITE:
        import sqlite3
        return sqlite3.connect(DB_FILE)
    else:
        return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        # Common SQL for both DBs
        query = '''CREATE TABLE IF NOT EXISTS users 
                   (username VARCHAR(255) PRIMARY KEY, score INTEGER, avatar TEXT, wins INTEGER)'''
        c.execute(query)
        conn.commit()
        conn.close()
        print(f">> Database Initialized ({'SQLite' if USE_SQLITE else 'PostgreSQL'})")
    except Exception as e:
        print(f">> DB Error: {e}")

# Initialize Database on Start
init_db()

# Database Helper Functions
def update_score(username, points, avatar_url):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        ph = "?" if USE_SQLITE else "%s" # Placeholder
        
        c.execute(f"SELECT score, wins FROM users WHERE username={ph}", (username,))
        data = c.fetchone()
        
        if data:
            new_score = data[0] + points
            if new_score < 0: new_score = 0
            new_wins = data[1] + 1 if points > 0 else data[1]
            c.execute(f"UPDATE users SET score={ph}, avatar={ph}, wins={ph} WHERE username={ph}", 
                      (new_score, avatar_url, new_wins, username))
        else:
            initial_score = points if points > 0 else 0
            wins = 1 if points > 0 else 0
            c.execute(f"INSERT INTO users (username, score, avatar, wins) VALUES ({ph}, {ph}, {ph}, {ph})", 
                      (username, initial_score, avatar_url, wins))
        
        conn.commit()
        conn.close()
    except Exception as e: print(f"Update Error: {e}")

def get_user_score(username):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        ph = "?" if USE_SQLITE else "%s"
        c.execute(f"SELECT score FROM users WHERE username={ph}", (username,))
        data = c.fetchone()
        conn.close()
        return data[0] if data else 0
    except: return 0

def get_leaderboard_data():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT username, score, avatar, wins FROM users ORDER BY score DESC LIMIT 50")
        data = c.fetchall()
        conn.close()
        return data
    except: return []

# =============================================================================
# 2. BOT LOGIC & STATE
# =============================================================================

BOT_STATE = {
    "ws": None, "connected": False, "user": "", "pass": "", 
    "room": "", "thread": None, "domain": ""
}

GAME_STATE = {
    "active": False, "player": None, "bombs": [], 
    "eaten": [], "bet_amount": 0, "user_avatars": {}
}

LOGS = []

def add_log(msg, type="sys"):
    timestamp = time.strftime("%H:%M:%S")
    LOGS.append({"time": timestamp, "msg": msg, "type": type})
    if len(LOGS) > 100: LOGS.pop(0)

# Websocket Event Handlers
def on_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("handler") == "receipt_ack": return

        # Capture Avatar URL for Winner Card
        if data.get("from") and data.get("avatar_url"):
            GAME_STATE["user_avatars"][data["from"]] = data["avatar_url"]

        # Handle Room Messages
        if data.get("handler") == "room_event" and data.get("type") == "text":
            add_log(f"[{data['from']}]: {data['body']}", "in")
            process_game_logic(data['from'], data['body'])
            
        # Handle Login Success
        elif data.get("handler") == "login_event":
            if data["type"] == "success":
                add_log("Login Success. Joining Room...", "sys")
                join_pkt = {"handler": "room_join", "id": str(time.time()), "name": BOT_STATE["room"]}
                ws.send(json.dumps(join_pkt))
            else:
                add_log(f"Login Failed: {data.get('reason')}", "err")
                BOT_STATE["connected"] = False

    except Exception as e: print(f"WS Error: {e}")

def on_error(ws, error): add_log(f"Error: {error}", "err")
def on_close(ws, c, m): 
    add_log("Disconnected from server.", "err")
    BOT_STATE["connected"] = False

def on_open(ws):
    BOT_STATE["connected"] = True
    add_log("Connection Established. Authenticating...", "sys")
    login_pkt = {
        "handler": "login", "id": str(time.time()), 
        "username": BOT_STATE["user"], "password": BOT_STATE["pass"], "platform": "web"
    }
    ws.send(json.dumps(login_pkt))
    
    # Keep Alive Thread
    def pinger():
        while BOT_STATE["connected"]:
            time.sleep(20)
            try: ws.send(json.dumps({"handler": "ping"}))
            except: break
    threading.Thread(target=pinger, daemon=True).start()

def send_room_msg(text, msg_type="text", url=""):
    if BOT_STATE["ws"] and BOT_STATE["connected"]:
        pkt = {
            "handler": "room_message", "id": str(time.time()), 
            "room": BOT_STATE["room"], "type": msg_type, 
            "body": text, "url": url, "length": "0"
        }
        try:
            BOT_STATE["ws"].send(json.dumps(pkt))
            log_text = text.split('\n')[0] if msg_type == "text" else "IMAGE SENT"
            add_log(f"BOT >> {log_text}...", "out")
        except: pass

# =============================================================================
# 3. GAME ENGINE (MINEFIELD 1-9)
# =============================================================================

def process_game_logic(user, msg):
    msg = msg.strip().lower()
    if user.lower() == BOT_STATE["user"].lower(): return

    # --- COMMAND: !HELP ---
    if msg == "!help":
        help_txt = (
            "🤖 TITAN OS COMMANDS:\n"
            "-------------------\n"
            "🎮 !start -> Play Normal Mode (+10 pts)\n"
            "💰 !start bet@50 -> Bet 50 pts (Win double/Lose bet)\n"
            "🥔 !eat <number> -> Eat a chip (1-9)\n"
            "🏆 !score -> Check your points\n"
            "📊 !rank -> View Leaderboard Link"
        )
        send_room_msg(help_txt)

    # --- COMMAND: !SCORE ---
    elif msg == "!score":
        score = get_user_score(user)
        send_room_msg(f"💳 {user}, your balance is: {score} points.")

    # --- COMMAND: !RANK ---
    elif msg == "!rank":
        domain = BOT_STATE.get("domain", "")
        send_room_msg(f"🏆 GLOBAL LEADERBOARD:\n{domain}leaderboard")

    # --- COMMAND: !START ---
    elif msg.startswith("!start"):
        if GAME_STATE["active"]:
            return send_room_msg(f"⚠ Game in progress! {GAME_STATE['player']} is playing.")
        
        bet = 0
        if "bet@" in msg:
            try:
                bet = int(msg.split("@")[1])
                if bet <= 0: return send_room_msg("⚠ Bet amount must be positive.")
                user_balance = get_user_score(user)
                if user_balance < bet:
                    return send_room_msg(f"⚠ Insufficient Funds! You have {user_balance} pts.")
            except:
                return send_room_msg("⚠ Invalid Format. Usage: !start bet@100")

        # Initialize Game
        GAME_STATE["active"] = True
        GAME_STATE["player"] = user
        GAME_STATE["eaten"] = []
        GAME_STATE["bombs"] = random.sample(range(1, 10), 2) # 2 Unique Bombs
        GAME_STATE["bet_amount"] = bet
        
        mode_text = f"💰 HIGH STAKES! Bet: {bet} pts" if bet > 0 else "🛡 Normal Mode"
        add_log(f"Game Started by {user} ({mode_text}). Bombs: {GAME_STATE['bombs']}", "game")
        
        grid = render_grid()
        send_room_msg(f"🎮 {mode_text}\nPlayer: {user}\nAvoid 2 Bombs! Eat 4 Chips to WIN.\nType !eat <number>\n\n{grid}")

    # --- COMMAND: !EAT ---
    elif msg.startswith("!eat "):
        if not GAME_STATE["active"]: return
        if user != GAME_STATE["player"]: return
        
        try: num = int(msg.split()[1])
        except: return 
        if num < 1 or num > 9 or num in GAME_STATE["eaten"]: return 

        if num in GAME_STATE["bombs"]:
            # --- PLAYER LOST ---
            GAME_STATE["active"] = False
            
            loss_txt = ""
            if GAME_STATE["bet_amount"] > 0:
                update_score(user, -GAME_STATE["bet_amount"], GAME_STATE["user_avatars"].get(user, ""))
                loss_txt = f"\n💸 LOST {GAME_STATE['bet_amount']} POINTS!"
            
            grid = render_grid(reveal=True, exploded=num)
            send_room_msg(f"💥 BOOM! BOMB AT #{num}!{loss_txt}\n💀 GAME OVER.\nBombs: {GAME_STATE['bombs']}\n\n{grid}")
            add_log(f"Game Over: {user} hit bomb.", "err")

        else:
            # --- PLAYER SAFE ---
            GAME_STATE["eaten"].append(num)
            
            # WIN CONDITION: 4 CHIPS
            if len(GAME_STATE["eaten"]) == 4:
                GAME_STATE["active"] = False
                
                # Determine Prize
                prize = GAME_STATE["bet_amount"] if GAME_STATE["bet_amount"] > 0 else 10
                
                # Update DB
                avatar = GAME_STATE["user_avatars"].get(user, "")
                update_score(user, prize, avatar)
                
                grid = render_grid(reveal=True)
                send_room_msg(f"🎉 WINNER! {user} ate 4 chips!\n🤑 Won: +{prize} Points!\n🥔 CHAMPION! Generating Card...\n\n{grid}")
                
                # Send Image (Async delay to prevent block)
                threading.Timer(1.0, send_winner_image, [user, avatar, prize]).start()
                add_log(f"Victory: {user} (+{prize})", "game")
            else:
                grid = render_grid()
                send_room_msg(f"🥔 SAFE! ({len(GAME_STATE['eaten'])}/4)\n{grid}")

def render_grid(reveal=False, exploded=None):
    icons = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
    txt = ""
    for i in range(1, 10):
        if reveal and i == exploded: txt += "💥 "
        elif reveal and i in GAME_STATE["bombs"]: txt += "💣 "
        elif i in GAME_STATE["eaten"]: txt += "🥔 "
        else: txt += icons[i-1] + " "
        if i % 3 == 0 and i != 9: txt += "\n"
    return txt

def send_winner_image(username, avatar, points):
    domain = BOT_STATE.get("domain", "")
    if domain:
        img_url = f"{domain}winner-card?name={username}&avatar={requests.utils.quote(avatar)}&points={points}"
        send_room_msg("", msg_type="image", url=img_url)

# =============================================================================
# 4. FLASK ROUTES & IMAGE GENERATION
# =============================================================================

@app.route('/')
def index():
    return render_template_string(HTML_DASHBOARD, connected=BOT_STATE["connected"])

@app.route('/leaderboard')
def leaderboard():
    data = get_leaderboard_data()
    return render_template_string(HTML_LEADERBOARD, users=data)

@app.route('/connect', methods=['POST'])
def connect():
    if BOT_STATE["connected"]: return jsonify({"status": "Already Connected"})
    d = request.json
    BOT_STATE.update({"user": d["u"], "pass": d["p"], "room": d["r"], "domain": request.url_root})
    
    def run_ws():
        websocket.enableTrace(False)
        ws = websocket.WebSocketApp("wss://chatp.net:5333/server",
            on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
        BOT_STATE["ws"] = ws
        ws.run_forever()
        
    BOT_STATE["thread"] = threading.Thread(target=run_ws)
    BOT_STATE["thread"].start()
    return jsonify({"status": "Connecting..."})

@app.route('/disconnect', methods=['POST'])
def disconnect():
    if BOT_STATE["ws"]: BOT_STATE["ws"].close()
    return jsonify({"status": "Disconnected"})

@app.route('/logs')
def get_logs():
    return jsonify({"logs": LOGS, "connected": BOT_STATE["connected"]})

@app.route('/winner-card')
def winner_card():
    try:
        username = request.args.get('name', 'Winner')
        avatar_url = request.args.get('avatar', '')
        points = request.args.get('points', '10')

        size = 800
        img = Image.new('RGB', (size, size), color=(15, 15, 15))
        draw = ImageDraw.Draw(img)

        # Draw Neon Borders
        draw.rectangle([0, 0, size-1, size-1], outline="#00f3ff", width=15)
        draw.rectangle([30, 30, size-30, size-30], outline="#ffd700", width=4)

        # Avatar Handling
        try:
            if avatar_url and avatar_url != "undefined":
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(avatar_url, headers=headers, timeout=5)
                avi = Image.open(io.BytesIO(response.content)).convert("RGBA")
                avi = avi.resize((350, 350))
                
                # Circular Mask
                mask = Image.new("L", (350, 350), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 350, 350), fill=255)
                
                img.paste(avi, (225, 120), mask)
                # Green Ring
                draw.ellipse([220, 115, 580, 475], outline="#00ff41", width=8)
            else:
                draw.ellipse([225, 120, 575, 470], fill="#333", outline="#555")
        except: pass

        # Load Font
        try:
            font_title = ImageFont.truetype(FONT_PATH, 100)
            font_name = ImageFont.truetype(FONT_PATH, 70)
            font_score = ImageFont.truetype(FONT_PATH, 60)
        except:
            font_title = ImageFont.load_default()
            font_name = ImageFont.load_default()
            font_score = ImageFont.load_default()

        # Helper to center text
        def draw_centered(text, y, font, color):
            # Fallback length calculation
            try: text_width = draw.textlength(text, font=font)
            except: text_width = len(text) * 15 
            x = (size - text_width) / 2
            draw.text((x, y), text, font=font, fill=color)

        draw_centered("WINNER", 530, font_title, "#ffd700")
        draw_centered(username.upper(), 640, font_name, "#ffffff")
        draw_centered(f"+{points} POINTS", 720, font_score, "#00ff41")

        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        return send_file(img_io, mimetype='image/png')
    except Exception as e: return str(e), 500

# =============================================================================
# 5. UI TEMPLATES (HTML/CSS)
# =============================================================================

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TITAN OS // CONTROL</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Rajdhani:wght@500;700&display=swap" rel="stylesheet">
    <style>
        :root { --neon: #00f3ff; --term: #00ff41; --danger: #ff003c; --bg: #050505; --panel: #0f0f0f; }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--bg); color: var(--neon); font-family: 'Rajdhani', sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        
        /* Cyberpunk Grid Background */
        body::before { content: ""; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: linear-gradient(rgba(0, 243, 255, 0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 243, 255, 0.03) 1px, transparent 1px); background-size: 30px 30px; z-index: -1; pointer-events: none; }

        .header { padding: 15px 20px; border-bottom: 2px solid var(--neon); display: flex; justify-content: space-between; align-items: center; background: rgba(10,10,10,0.9); box-shadow: 0 0 15px rgba(0, 243, 255, 0.2); }
        .brand { font-family: 'Orbitron', sans-serif; font-size: 24px; font-weight: bold; letter-spacing: 2px; text-shadow: 0 0 10px var(--neon); }
        .status { padding: 5px 12px; border-radius: 4px; font-weight: bold; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
        .online { background: rgba(0, 255, 65, 0.2); color: var(--term); border: 1px solid var(--term); box-shadow: 0 0 10px var(--term); }
        .offline { background: rgba(255, 0, 60, 0.2); color: var(--danger); border: 1px solid var(--danger); box-shadow: 0 0 10px var(--danger); }

        .main { display: flex; flex: 1; padding: 20px; gap: 20px; overflow: hidden; }
        
        /* Control Panel */
        .controls { width: 300px; background: var(--panel); border: 1px solid #333; padding: 20px; display: flex; flex-direction: column; gap: 15px; box-shadow: 10px 10px 0px rgba(0,0,0,0.5); }
        h3 { margin: 0 0 10px 0; color: #fff; border-bottom: 1px solid #333; padding-bottom: 5px; }
        
        .input-group label { display: block; font-size: 12px; color: #888; margin-bottom: 5px; }
        input { width: 100%; background: #000; border: 1px solid #444; color: #fff; padding: 10px; font-family: inherit; font-size: 16px; outline: none; transition: 0.3s; }
        input:focus { border-color: var(--neon); box-shadow: 0 0 8px rgba(0, 243, 255, 0.3); }

        .btn { width: 100%; padding: 12px; border: none; font-weight: bold; font-size: 16px; cursor: pointer; text-transform: uppercase; font-family: 'Orbitron', sans-serif; transition: 0.2s; clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px); }
        .btn-start { background: var(--term); color: #000; margin-top: 10px; }
        .btn-start:hover { background: #00ff55; transform: scale(1.02); box-shadow: 0 0 15px var(--term); }
        .btn-stop { background: var(--danger); color: #fff; margin-top: 10px; }
        .btn-stop:hover { background: #ff3366; transform: scale(1.02); box-shadow: 0 0 15px var(--danger); }
        
        .link-btn { display: block; text-align: center; margin-top: 20px; color: #ffd700; text-decoration: none; border: 1px solid #ffd700; padding: 10px; transition: 0.3s; }
        .link-btn:hover { background: rgba(255, 215, 0, 0.1); box-shadow: 0 0 10px #ffd700; }

        /* Terminal */
        .terminal { flex: 1; background: #000; border: 1px solid #333; display: flex; flex-direction: column; position: relative; }
        .term-bar { background: #111; padding: 5px 10px; font-size: 12px; color: #666; border-bottom: 1px solid #222; display: flex; justify-content: space-between; }
        .logs-area { flex: 1; overflow-y: auto; padding: 15px; font-family: 'Consolas', monospace; font-size: 13px; scroll-behavior: smooth; }
        
        .log-line { margin-bottom: 6px; border-left: 2px solid transparent; padding-left: 8px; line-height: 1.4; word-wrap: break-word; }
        .sys { color: #888; border-color: #444; }
        .in { color: var(--term); border-color: var(--term); }
        .out { color: var(--neon); border-color: var(--neon); }
        .err { color: var(--danger); border-color: var(--danger); }
        .game { color: #ffd700; border-color: #ffd700; background: rgba(255, 215, 0, 0.05); }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #000; }
        ::-webkit-scrollbar-thumb { background: #333; border: 1px solid #000; }
        
        @media (max-width: 768px) { .main { flex-direction: column; } .controls { width: 100%; } }
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">TITAN OS <span style="font-size:12px;color:#666;">v4.5</span></div>
        <div id="status" class="status offline">OFFLINE</div>
    </div>

    <div class="main">
        <div class="controls">
            <h3>ACCESS PANEL</h3>
            <div class="input-group">
                <label>BOT USERNAME</label>
                <input type="text" id="u" placeholder="Enter Bot ID">
            </div>
            <div class="input-group">
                <label>ACCESS KEY</label>
                <input type="password" id="p" placeholder="Enter Password">
            </div>
            <div class="input-group">
                <label>TARGET ROOM</label>
                <input type="text" id="r" placeholder="Room Name">
            </div>
            
            <button class="btn btn-start" onclick="startBot()">INITIALIZE SYSTEM</button>
            <button class="btn btn-stop" onclick="stopBot()">EMERGENCY HALT</button>
            
            <a href="/leaderboard" target="_blank" class="link-btn">🏆 VIEW LEADERBOARD</a>
        </div>

        <div class="terminal">
            <div class="term-bar">
                <span>SYSTEM_LOGS</span>
                <span onclick="document.getElementById('logs').innerHTML=''" style="cursor:pointer">[CLEAR]</span>
            </div>
            <div id="logs" class="logs-area">
                <div class="log-line sys">Titan OS Loaded. Waiting for input...</div>
            </div>
        </div>
    </div>

    <script>
        function startBot() {
            const u = document.getElementById('u').value, p = document.getElementById('p').value, r = document.getElementById('r').value;
            if(!u || !p || !r) return alert("Please fill all fields!");
            
            fetch('/connect', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({u, p, r})
            }).then(r=>r.json()).then(d=>console.log(d));
        }

        function stopBot() {
            if(confirm("Shut down the bot?")) fetch('/disconnect', {method: 'POST'});
        }

        let autoScroll = true;
        const logDiv = document.getElementById('logs');
        logDiv.addEventListener('scroll', () => {
            autoScroll = (logDiv.scrollTop + logDiv.clientHeight >= logDiv.scrollHeight - 20);
        });

        setInterval(() => {
            fetch('/logs').then(r=>r.json()).then(data => {
                const badge = document.getElementById('status');
                badge.className = data.connected ? "status online" : "status offline";
                badge.innerText = data.connected ? "ONLINE" : "OFFLINE";

                const html = data.logs.map(l => `<div class="log-line ${l.type}"><b>[${l.time}]</b> ${l.msg}</div>`).join('');
                if(logDiv.innerHTML !== html) {
                    logDiv.innerHTML = html;
                    if(autoScroll) logDiv.scrollTop = logDiv.scrollHeight;
                }
            });
        }, 1500);
    </script>
</body>
</html>
"""

HTML_LEADERBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>TITAN RANKINGS</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;700&display=swap" rel="stylesheet">
    <style>
        body { background: #050505; color: #fff; font-family: 'Rajdhani', sans-serif; margin: 0; padding: 20px; overflow-x: hidden; }
        
        h1 { text-align: center; font-size: 3rem; color: #00f3ff; text-transform: uppercase; letter-spacing: 5px; text-shadow: 0 0 20px #00f3ff; margin-bottom: 40px; }
        
        .container { max-width: 800px; margin: 0 auto; perspective: 1000px; }
        
        .card { 
            background: rgba(20, 20, 20, 0.8); 
            margin-bottom: 15px; 
            padding: 15px 25px; 
            border-radius: 12px; 
            display: flex; 
            align-items: center; 
            justify-content: space-between;
            border: 1px solid #333;
            box-shadow: 0 5px 15px rgba(0,0,0,0.5);
            transition: transform 0.3s, box-shadow 0.3s, background 0.3s;
            backdrop-filter: blur(5px);
            position: relative;
            overflow: hidden;
        }
        
        .card:hover { transform: scale(1.03) rotateX(2deg); background: rgba(30, 30, 30, 0.9); box-shadow: 0 10px 25px rgba(0, 243, 255, 0.15); border-color: #00f3ff; }
        
        /* Rank Styles */
        .rank { font-size: 24px; font-weight: bold; width: 50px; text-align: center; }
        .rank-1 { border-left: 5px solid #ffd700; background: linear-gradient(90deg, rgba(255, 215, 0, 0.1), transparent); }
        .rank-1 .rank { color: #ffd700; text-shadow: 0 0 10px #ffd700; font-size: 30px; }
        
        .rank-2 { border-left: 5px solid #c0c0c0; background: linear-gradient(90deg, rgba(192, 192, 192, 0.1), transparent); }
        .rank-2 .rank { color: #c0c0c0; text-shadow: 0 0 10px #c0c0c0; }
        
        .rank-3 { border-left: 5px solid #cd7f32; background: linear-gradient(90deg, rgba(205, 127, 50, 0.1), transparent); }
        .rank-3 .rank { color: #cd7f32; text-shadow: 0 0 10px #cd7f32; }
        
        .profile { display: flex; align-items: center; gap: 20px; flex: 1; }
        .avatar { width: 60px; height: 60px; border-radius: 50%; object-fit: cover; border: 2px solid #444; box-shadow: 0 0 10px rgba(0,0,0,0.8); }
        .info h2 { margin: 0; font-size: 20px; letter-spacing: 1px; }
        .info span { font-size: 14px; color: #888; }
        
        .score-box { text-align: right; }
        .points { font-size: 24px; color: #00ff41; font-weight: bold; text-shadow: 0 0 5px #00ff41; }
        .label { font-size: 12px; color: #666; text-transform: uppercase; }

        /* Animation */
        @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .card { animation: fadeIn 0.5s ease forwards; }
    </style>
</head>
<body>
    <h1>Global Rankings</h1>
    <div class="container">
        {% for user in users %}
        <div class="card {% if loop.index == 1 %}rank-1{% elif loop.index == 2 %}rank-2{% elif loop.index == 3 %}rank-3{% endif %}" style="animation-delay: {{ loop.index0 * 0.1 }}s">
            <div class="rank">#{{ loop.index }}</div>
            <div class="profile">
                {% if user[2] and user[2] != 'undefined' %}
                <img src="{{ user[2] }}" class="avatar">
                {% else %}
                <div class="avatar" style="background:#222; display:flex; align-items:center; justify-content:center; color:#555;">?</div>
                {% endif %}
                <div class="info">
                    <h2>{{ user[0] }}</h2>
                    <span>Wins: {{ user[3] }}</span>
                </div>
            </div>
            <div class="score-box">
                <div class="points">{{ user[1] }}</div>
                <div class="label">PTS</div>
            </div>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)