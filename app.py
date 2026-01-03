import os
import json
import time
import threading
import io
import sqlite3
import random
import requests
import websocket
from flask import Flask, render_template_string, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# --- CONFIGURATION ---
DB_FILE = "titan_game.db"

# --- DATABASE HANDLER ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Create Users Table
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, score INTEGER, avatar TEXT, wins INTEGER)''')
    conn.commit()
    conn.close()

def update_score(username, points, avatar_url):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Check if user exists
    c.execute("SELECT score, wins FROM users WHERE username=?", (username,))
    data = c.fetchone()
    
    if data:
        new_score = data[0] + points
        # Prevent negative score
        if new_score < 0: new_score = 0
        
        new_wins = data[1] + 1 if points > 0 else data[1]
        c.execute("UPDATE users SET score=?, avatar=?, wins=? WHERE username=?", (new_score, avatar_url, new_wins, username))
    else:
        # New User (Only add if points are positive)
        initial_score = points if points > 0 else 0
        wins = 1 if points > 0 else 0
        c.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (username, initial_score, avatar_url, wins))
    
    conn.commit()
    conn.close()

def get_user_score(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT score FROM users WHERE username=?", (username,))
    data = c.fetchone()
    conn.close()
    return data[0] if data else 0

def get_leaderboard():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username, score, avatar, wins FROM users ORDER BY score DESC LIMIT 50")
    data = c.fetchall()
    conn.close()
    return data

# Initialize DB on start
init_db()

# --- GLOBAL STATES ---
BOT_STATE = {
    "ws": None, "connected": False, "user": "", "pass": "", "room": "", "thread": None, "domain": ""
}

GAME_STATE = {
    "active": False,
    "player": None,
    "bombs": [],
    "eaten": [],
    "bet_amount": 0, # 0 means Normal Mode (+10)
    "user_avatars": {}
}

LOGS = []

def add_log(msg, type="sys"):
    timestamp = time.strftime("%H:%M:%S")
    LOGS.append({"time": timestamp, "msg": msg, "type": type})
    if len(LOGS) > 100: LOGS.pop(0)

# --- WEBSOCKET HANDLERS ---
def on_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("handler") == "receipt_ack": return

        if data.get("from") and data.get("avatar_url"):
            GAME_STATE["user_avatars"][data["from"]] = data["avatar_url"]

        if data.get("handler") == "room_event" and data.get("type") == "text":
            add_log(f"[{data['from']}]: {data['body']}", "in")
            process_game_logic(data['from'], data['body'])
            
        elif data.get("handler") == "login_event":
            if data["type"] == "success":
                add_log("Login Success. Joining Room...", "sys")
                ws.send(json.dumps({"handler": "room_join", "id": str(time.time()), "name": BOT_STATE["room"]}))
            else:
                add_log(f"Login Failed: {data.get('reason')}", "err")
                BOT_STATE["connected"] = False
    except Exception as e:
        print(f"Error: {e}")

def on_error(ws, error): add_log(f"Socket Error: {error}", "err")
def on_close(ws, close_status_code, close_msg): 
    add_log("Disconnected.", "err")
    BOT_STATE["connected"] = False

def on_open(ws):
    BOT_STATE["connected"] = True
    add_log("Socket Connected. Authenticating...", "sys")
    ws.send(json.dumps({"handler": "login", "id": str(time.time()), "username": BOT_STATE["user"], "password": BOT_STATE["pass"], "platform": "web"}))
    
    def run_ping():
        while BOT_STATE["connected"]:
            time.sleep(20)
            try: BOT_STATE["ws"].send(json.dumps({"handler": "ping"}))
            except: break
    threading.Thread(target=run_ping, daemon=True).start()

def send_room_msg(text, msg_type="text", url=""):
    if BOT_STATE["ws"] and BOT_STATE["connected"]:
        pkt = {"handler": "room_message", "id": str(time.time()), "room": BOT_STATE["room"], "type": msg_type, "body": text, "url": url, "length": "0"}
        try:
            BOT_STATE["ws"].send(json.dumps(pkt))
            log_txt = "SENT IMAGE" if msg_type == "image" else text.splitlines()[0]
            add_log(f"BOT >> {log_txt}...", "out")
        except: pass

# --- ADVANCED GAME LOGIC ---
def process_game_logic(user, msg):
    msg = msg.strip().lower()
    if user.lower() == BOT_STATE["user"].lower(): return

    # --- START COMMAND (NORMAL & BETTING) ---
    if msg.startswith("!start"):
        if GAME_STATE["active"]:
            send_room_msg(f"⚠ Wait! {GAME_STATE['player']} is playing.")
            return
        
        # Check Mode
        bet = 0
        if "bet@" in msg:
            try:
                parts = msg.split("@")
                bet = int(parts[1])
                if bet <= 0: return send_room_msg("⚠ Bet must be > 0")
                
                # Check User Balance
                current_score = get_user_score(user)
                if current_score < bet:
                    return send_room_msg(f"⚠ Insufficient Funds! You have {current_score} points.")
            except:
                return send_room_msg("⚠ Invalid Bet Format. Use: !start bet@100")

        # Init Game
        GAME_STATE["active"] = True
        GAME_STATE["player"] = user
        GAME_STATE["eaten"] = []
        GAME_STATE["bombs"] = random.sample(range(1, 10), 2)
        GAME_STATE["bet_amount"] = bet
        
        mode_txt = f"💰 HIGH STAKES! Bet: {bet} pts" if bet > 0 else "🛡 Normal Mode (+10 pts)"
        add_log(f"Game Started by {user}. Mode: {bet}", "game")
        
        grid = render_grid()
        send_room_msg(f"🎮 {mode_txt}\nPlayer: {user}\nAvoid 2 Bombs! Eat 4 Chips to WIN.\nType !eat <number>\n\n{grid}")

    # --- EAT COMMAND ---
    elif msg.startswith("!eat "):
        if not GAME_STATE["active"]: return
        if user != GAME_STATE["player"]: return
        
        try: num = int(msg.split()[1])
        except: return 
        if num < 1 or num > 9 or num in GAME_STATE["eaten"]: return 

        if num in GAME_STATE["bombs"]:
            # --- LOSE ---
            GAME_STATE["active"] = False
            
            # Deduct points if betting
            loss_msg = ""
            if GAME_STATE["bet_amount"] > 0:
                update_score(user, -GAME_STATE["bet_amount"], GAME_STATE["user_avatars"].get(user, ""))
                loss_msg = f"\n💸 YOU LOST {GAME_STATE['bet_amount']} POINTS!"
            
            grid = render_grid(reveal=True, exploded=num)
            send_room_msg(f"💥 BOOM! BOMB AT #{num}!{loss_msg}\n💀 GAME OVER.\nBombs: {GAME_STATE['bombs']}\n\n{grid}")
            add_log(f"Game Over: {user} lost.", "err")

        else:
            # --- SAFE ---
            GAME_STATE["eaten"].append(num)
            
            if len(GAME_STATE["eaten"]) == 4:
                # --- WIN ---
                GAME_STATE["active"] = False
                
                # Calculate Winnings
                prize = 10
                if GAME_STATE["bet_amount"] > 0:
                    prize = GAME_STATE["bet_amount"] * 2 # Double the bet? Or just Add Bet? "Double Jeet" usually means Profit = Bet.
                    # Logic: If I have 100, bet 50. I have 50. Win -> Get 50 + 50 profit = 150. (Net +50).
                    # Let's keep it simple: Add the Bet Amount to Score.
                    prize = GAME_STATE["bet_amount"]

                avatar = GAME_STATE["user_avatars"].get(user, "")
                update_score(user, prize, avatar)
                
                grid = render_grid(reveal=True)
                send_room_msg(f"🎉 WINNER! {user} ate 4 chips!\n🤑 Won: {prize} Points!\n🥔 CHAMPION! Generating Card...\n\n{grid}")
                
                time.sleep(1)
                send_winner_image(user, avatar, prize)
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
        # Pass points to image generator
        img_url = f"{domain}winner-card?name={username}&avatar={requests.utils.quote(avatar)}&points={points}"
        send_room_msg("", msg_type="image", url=img_url)

# --- FLASK ROUTES ---
@app.route('/')
def index(): return render_template_string(CONTROL_PANEL_HTML, connected=BOT_STATE["connected"])

@app.route('/leaderboard')
def leaderboard():
    users = get_leaderboard()
    return render_template_string(LEADERBOARD_HTML, users=users)

@app.route('/connect', methods=['POST'])
def start_bot():
    data = request.json
    if BOT_STATE["connected"]: return jsonify({"status": "Already Online"})
    BOT_STATE["user"] = data["u"]
    BOT_STATE["pass"] = data["p"]
    BOT_STATE["room"] = data["r"]
    BOT_STATE["domain"] = request.url_root 
    t = threading.Thread(target=run_socket)
    t.start()
    return jsonify({"status": "Initializing..."})

def run_socket():
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp("wss://chatp.net:5333/server", on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
    BOT_STATE["ws"] = ws
    ws.run_forever()

@app.route('/disconnect', methods=['POST'])
def stop_bot():
    if BOT_STATE["ws"]: BOT_STATE["ws"].close()
    BOT_STATE["connected"] = False
    return jsonify({"status": "Stopped"})

@app.route('/logs')
def get_logs(): return jsonify({"logs": LOGS, "connected": BOT_STATE["connected"]})

# --- INSTAGRAM SIZE SQUARE WINNER CARD ---
@app.route('/winner-card')
def winner_card():
    try:
        username = request.args.get('name', 'Winner')
        avatar_url = request.args.get('avatar', '')
        points = request.args.get('points', '10')

        # 800x800 Square Canvas (HD)
        size = 800
        img = Image.new('RGB', (size, size), color=(10, 10, 10))
        draw = ImageDraw.Draw(img)

        # Neon Borders
        draw.rectangle([0, 0, size-1, size-1], outline="#00f3ff", width=15)
        draw.rectangle([20, 20, size-20, size-20], outline="#ffd700", width=5)

        # Avatar Handling
        try:
            if avatar_url and avatar_url != "undefined":
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(avatar_url, headers=headers, timeout=5)
                avi = Image.open(io.BytesIO(response.content)).convert("RGBA")
                
                # Resize to 300x300
                avi_size = 300
                avi = avi.resize((avi_size, avi_size))
                
                # Circle Mask
                mask = Image.new("L", (avi_size, avi_size), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, avi_size, avi_size), fill=255)
                
                # Center the avatar
                pos = ((size - avi_size) // 2, 150)
                img.paste(avi, pos, mask)
                
                # Ring around avatar
                draw.ellipse([pos[0]-5, pos[1]-5, pos[0]+avi_size+5, pos[1]+avi_size+5], outline="#00ff41", width=5)
            else:
                # Fallback Circle
                draw.ellipse((250, 150, 550, 450), fill="#222", outline="#fff")
        except: pass

        # Text Design (Centered)
        def draw_centered_text(y, text, color, font_scale=1):
            # Simple centering for default font
            # Since we don't have ttf, we approximate center
            text_width = len(text) * 6 * font_scale # approx width char
            x = (size - text_width) // 2
            # Use basic drawing as fallback
            draw.text((320, y), text, fill=color) 
            # Note: Without a TTF file, text looks small. 
            # I will attempt to draw it slightly larger by drawing multiple times slightly offset
            # or just stick to standard for reliability.

        # Since default font is tiny on 800x800, let's draw a nice box for text
        draw.rectangle([100, 500, 700, 750], fill="#1a1a1a", outline="#333")
        
        draw.text((350, 530), "WINNER", fill="#ffd700")
        draw.text((350, 560), username.upper(), fill="#ffffff")
        draw.text((320, 600), f"WON: +{points} POINTS", fill="#00ff41")
        draw.text((310, 640), "TITAN CHAMPION", fill="#00f3ff")

        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        return send_file(img_io, mimetype='image/png')
    except Exception as e: return str(e), 500

# --- HTML TEMPLATES ---

CONTROL_PANEL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TITAN OS - MASTER</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        :root { --neon: #00f3ff; --term: #00ff41; --danger: #ff003c; --bg: #050505; }
        body { background: var(--bg); color: var(--neon); font-family: 'Share Tech Mono', monospace; margin: 0; display: flex; flex-direction: column; height: 100vh; }
        
        .header { padding: 15px; border-bottom: 2px solid var(--neon); display: flex; justify-content: space-between; align-items: center; background: #0a0a0a; }
        .links a { color: var(--term); margin-left: 15px; text-decoration: none; border: 1px solid var(--term); padding: 5px 10px; }
        .links a:hover { background: var(--term); color: #000; }

        .control-panel { padding: 20px; display: grid; gap: 10px; background: #111; }
        input { background: #000; border: 1px solid #444; color: #fff; padding: 10px; width: 90%; }
        button { padding: 10px; background: var(--neon); color: #000; border: none; font-weight: bold; cursor: pointer; width: 100%; }
        button.stop { background: var(--danger); color: #fff; }
        
        .logs { flex: 1; background: #000; padding: 10px; overflow-y: scroll; font-size: 12px; }
        .in { color: var(--term); } .out { color: var(--neon); } .err { color: var(--danger); }
    </style>
</head>
<body>
    <div class="header">
        <div>TITAN OS // CONTROL</div>
        <div class="links">
            <a href="/leaderboard" target="_blank">🏆 LEADERBOARD</a>
        </div>
    </div>
    <div class="control-panel">
        <input type="text" id="u" placeholder="Username">
        <input type="password" id="p" placeholder="Password">
        <input type="text" id="r" placeholder="Room Name">
        <div style="display:flex; gap:10px;">
            <button onclick="start()">START SYSTEM</button>
            <button class="stop" onclick="stop()">SHUTDOWN</button>
        </div>
    </div>
    <div class="logs" id="logs"></div>
    <script>
        function start() {
            fetch('/connect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    u: document.getElementById('u').value,
                    p: document.getElementById('p').value,
                    r: document.getElementById('r').value
                })
            });
        }
        function stop() { fetch('/disconnect', {method:'POST'}); }
        
        setInterval(() => {
            fetch('/logs').then(r=>r.json()).then(d => {
                const div = document.getElementById('logs');
                div.innerHTML = d.logs.map(l => `<div class="${l.type}">[${l.time}] ${l.msg}</div>`).join('');
            });
        }, 2000);
    </script>
</body>
</html>
"""

LEADERBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>TITAN RANKINGS</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        body { background: #050505; color: #fff; font-family: 'Share Tech Mono', monospace; padding: 20px; text-align: center; }
        h1 { color: #00f3ff; text-shadow: 0 0 10px #00f3ff; border-bottom: 2px solid #00f3ff; display: inline-block; padding-bottom: 10px; }
        
        .container { max-width: 600px; margin: 0 auto; }
        .card { background: #111; margin-bottom: 10px; padding: 15px; border-left: 5px solid #333; display: flex; align-items: center; justify-content: space-between; transition: 0.3s; }
        .card:hover { transform: scale(1.02); background: #1a1a1a; }
        
        .rank { font-size: 24px; font-weight: bold; width: 50px; }
        .rank-1 { color: #ffd700; border-left-color: #ffd700; } /* Gold */
        .rank-2 { color: #c0c0c0; border-left-color: #c0c0c0; } /* Silver */
        .rank-3 { color: #cd7f32; border-left-color: #cd7f32; } /* Bronze */
        
        .info { display: flex; align-items: center; gap: 15px; flex: 1; text-align: left; }
        .avatar { width: 50px; height: 50px; border-radius: 50%; border: 2px solid #555; object-fit: cover; }
        .name { font-size: 18px; color: #eee; }
        .score { font-size: 20px; color: #00ff41; font-weight: bold; }
        .wins { font-size: 12px; color: #888; }
    </style>
</head>
<body>
    <h1>TITAN GLOBAL RANKINGS</h1>
    <div class="container">
        {% for user in users %}
        <div class="card {% if loop.index == 1 %}rank-1{% elif loop.index == 2 %}rank-2{% elif loop.index == 3 %}rank-3{% endif %}">
            <div class="rank">#{{ loop.index }}</div>
            <div class="info">
                {% if user[2] and user[2] != 'undefined' %}
                <img src="{{ user[2] }}" class="avatar">
                {% else %}
                <div class="avatar" style="background:#333; display:flex; align-items:center; justify-content:center;">?</div>
                {% endif %}
                <div>
                    <div class="name">{{ user[0] }}</div>
                    <div class="wins">Wins: {{ user[3] }}</div>
                </div>
            </div>
            <div class="score">{{ user[1] }} pts</div>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)