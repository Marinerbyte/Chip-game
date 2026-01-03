import threading
import sqlite3
import json
import time
import random
import string
import ssl
import io
import os
import requests
from flask import Flask, render_template_string, request, jsonify, redirect, url_for
import websocket # pip install websocket-client
from PIL import Image, ImageDraw, ImageFont # pip install Pillow
from requests_toolbelt.multipart.encoder import MultipartEncoder # pip install requests_toolbelt

# ==============================================================================
# 1. CONFIGURATION & ASSETS
# ==============================================================================
DB_FILE = "bombchip.db"
WS_URL = "wss://chatp.net:5333/server"
FILE_UPLOAD_URL = "https://cdn.talkinchat.com/post.php"

# Game Constants
TOTAL_CHIPS = 6  # 3x2 Grid for best mobile view
SAFE_REWARD = 10
WIN_REWARD = 100
LOSS_PENALTY = 50

# Colors
C_BG = (20, 20, 30)
C_CHIP_UNKNOWN = (50, 60, 80)
C_CHIP_SAFE = (46, 204, 113)
C_CHIP_BOMB = (231, 76, 60)
C_TEXT = (255, 255, 255)

# ==============================================================================
# 2. DATABASE ENGINE
# ==============================================================================
db_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS stats 
                     (username TEXT PRIMARY KEY, score INTEGER DEFAULT 0, 
                      wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, 
                      bombs INTEGER DEFAULT 0, avatar TEXT)''')
        conn.commit()
        conn.close()

init_db()

def update_stats(user, avatar, result, score_delta):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Check if user exists
        c.execute("SELECT * FROM stats WHERE username=?", (user,))
        exists = c.fetchone()
        
        if not exists:
            # Create new user
            c.execute("INSERT INTO stats (username, score, wins, losses, bombs, avatar) VALUES (?, 0, 0, 0, 0, ?)", (user, avatar))
        
        # Update Stats
        if result == 'WIN':
            c.execute("UPDATE stats SET score=score+?, wins=wins+1, avatar=? WHERE username=?", (score_delta, avatar, user))
        elif result == 'LOSS':
            c.execute("UPDATE stats SET score=score-?, losses=losses+1, bombs=bombs+1, avatar=? WHERE username=?", (abs(score_delta), avatar, user))
        elif result == 'SAFE':
            c.execute("UPDATE stats SET score=score+?, avatar=? WHERE username=?", (score_delta, avatar, user))
            
        conn.commit()
        conn.close()

def get_leaderboard():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM stats ORDER BY score DESC LIMIT 50")
        rows = [dict(row) for row in c.fetchall()]
        conn.close()
        return rows

# ==============================================================================
# 3. IMAGE GENERATOR & UPLOADER
# ==============================================================================
class ImageEngine:
    @staticmethod
    def generate_board(states, message="PICK A CHIP 1-6"):
        """
        states: dict {1: 'UNKNOWN', 2: 'SAFE', 3: 'BOMB'...}
        """
        W, H = 600, 450
        img = Image.new("RGB", (W, H), C_BG)
        draw = ImageDraw.Draw(img)
        
        # Grid settings for 6 chips (3 columns, 2 rows)
        cols = 3
        rows = 2
        chip_size = 130
        gap = 40
        start_x = (W - (cols * chip_size + (cols-1) * gap)) // 2
        start_y = 60

        try:
            font = ImageFont.truetype("arial.ttf", 40)
            msg_font = ImageFont.truetype("arial.ttf", 25)
        except:
            font = ImageFont.load_default()
            msg_font = ImageFont.load_default()

        for i in range(1, TOTAL_CHIPS + 1):
            r = (i - 1) // cols
            c = (i - 1) % cols
            
            x = start_x + c * (chip_size + gap)
            y = start_y + r * (chip_size + gap)
            
            state = states.get(i, 'UNKNOWN')
            color = C_CHIP_UNKNOWN
            
            if state == 'SAFE': color = C_CHIP_SAFE
            elif state == 'BOMB': color = C_CHIP_BOMB
            
            # Draw Chip Circle
            draw.ellipse([x, y, x+chip_size, y+chip_size], fill=color, outline=(255,255,255), width=2)
            
            # Draw Text/Icon
            text = str(i)
            if state == 'SAFE': text = "✔"
            if state == 'BOMB': text = "💥"
            
            # Center Text
            bbox = draw.textbbox((0, 0), text, font=font)
            tx = x + (chip_size - (bbox[2] - bbox[0])) // 2
            ty = y + (chip_size - (bbox[3] - bbox[1])) // 2
            draw.text((tx, ty - 5), text, fill=C_TEXT, font=font)

        # Draw Bottom Message Bar
        draw.rectangle([0, H-50, W, H], fill=(10,10,15))
        bbox = draw.textbbox((0, 0), message, font=msg_font)
        mx = (W - (bbox[2] - bbox[0])) // 2
        draw.text((mx, H-35), message, fill=(0, 255, 255), font=msg_font)

        # Save to Buffer
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf

def upload_to_talkinchat(image_buffer, bot_username, room_name):
    """Uploads generated image to TalkinChat CDN"""
    try:
        filename = f"bomb_{int(time.time())}.png"
        boundary = '----WebKitFormBoundary' + ''.join(random.sample(string.ascii_letters + string.digits, 16))
        
        m = MultipartEncoder(fields={
            'file': (filename, image_buffer, 'image/png'),
            'jid': bot_username,
            'is_private': 'no',
            'room': room_name,
            'device_id': ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
        }, boundary=boundary)

        headers = {'Content-Type': m.content_type}
        response = requests.post(FILE_UPLOAD_URL, data=m, headers=headers)
        
        if response.status_code == 200:
            return response.text.strip() # Returns the URL
    except Exception as e:
        print(f"Upload Error: {e}")
    return None

# ==============================================================================
# 4. GAME LOGIC
# ==============================================================================
class GameSession:
    def __init__(self, user):
        self.user = user
        self.chips = {i: 'UNKNOWN' for i in range(1, TOTAL_CHIPS + 1)}
        self.bomb_pos = -1
        self.moves = 0
        self.active = True

    def play(self, choice):
        if not self.active: return None, "Game Over"
        
        # Set bomb on first move (user never loses on first turn)
        if self.moves == 0:
            possible_bombs = [i for i in range(1, TOTAL_CHIPS + 1) if i != choice]
            self.bomb_pos = random.choice(possible_bombs)
        
        self.moves += 1
        
        if choice == self.bomb_pos:
            self.chips[choice] = 'BOMB'
            self.active = False
            return 'LOSS', f"BOOM! Bomb was at {choice}. (-{LOSS_PENALTY} pts)"
        else:
            self.chips[choice] = 'SAFE'
            # Win Condition: All chips cleared except bomb
            safe_count = sum(1 for k, v in self.chips.items() if v == 'SAFE')
            if safe_count == (TOTAL_CHIPS - 1):
                self.active = False
                return 'WIN', f"VICTORY! Board Cleared! (+{WIN_REWARD} pts)"
            
            return 'SAFE', f"Safe! +{SAFE_REWARD} pts. Next?"

# ==============================================================================
# 5. BOT CLIENT
# ==============================================================================
class BombBot:
    def __init__(self):
        self.ws = None
        self.active = False
        self.creds = {}
        self.sessions = {} # { room_id+user_id : GameSession }

    def start_bot(self, u, p, r):
        self.creds = {"u": u, "p": p, "r": r}
        self.active = True
        t = threading.Thread(target=self.run_socket)
        t.daemon = True
        t.start()

    def run_socket(self):
        while self.active:
            try:
                self.ws = websocket.WebSocketApp(WS_URL,
                    on_open=self.on_open, on_message=self.on_message, on_error=self.on_error)
                self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
                time.sleep(5)
            except: time.sleep(5)

    def on_open(self, ws):
        print("[BOT] Login...")
        ws.send(json.dumps({"handler": "login", "username": self.creds['u'], 
                            "password": self.creds['p'], "id": self.rnd_id()}))

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            handler = data.get('handler')

            if handler == 'login_event' and data.get('type') == 'success':
                print("[BOT] Login OK. Joining Room...")
                ws.send(json.dumps({"handler": "room_join", "id": self.rnd_id(), "name": self.creds['r']}))

            if handler == 'room_message' or data.get('type') == 'text':
                threading.Thread(target=self.process_msg, args=(data,)).start()

        except Exception as e: print(f"Error: {e}")

    def process_msg(self, data):
        sender = data.get('from') or data.get('username')
        if sender == self.creds['u']: return
        
        text = str(data.get('body') or data.get('text') or "").strip().lower()
        room = self.creds['r']
        avatar = data.get('avatar_url') or data.get('icon') or f"https://ui-avatars.com/api/?name={sender}"
        
        sid = f"{room}_{sender}"

        # --- COMMANDS ---
        
        # 1. Start Game
        if text == "!bombchip":
            if sid in self.sessions:
                self.send_txt(room, f"@{sender} You have a game active! Pick a number.")
                return
            
            self.sessions[sid] = GameSession(sender)
            
            # Generate & Send Initial Board
            buf = ImageEngine.generate_board(self.sessions[sid].chips, "GAME START: Pick 1-6")
            url = upload_to_talkinchat(buf, self.creds['u'], room)
            if url: self.send_img(room, url)
            self.send_txt(room, f"@{sender} Game Started! Type a number 1-6.")

        # 2. Play Move (Number 1-6)
        elif text.isdigit() and sid in self.sessions:
            num = int(text)
            if 1 <= num <= TOTAL_CHIPS:
                session = self.sessions[sid]
                status, msg = session.play(num)
                
                if status:
                    # Logic
                    score = 0
                    if status == 'WIN': score = WIN_REWARD
                    elif status == 'LOSS': score = -LOSS_PENALTY
                    elif status == 'SAFE': score = SAFE_REWARD
                    
                    update_stats(sender, avatar, status, score)
                    
                    # Image
                    buf = ImageEngine.generate_board(session.chips, msg)
                    url = upload_to_talkinchat(buf, self.creds['u'], room)
                    if url: self.send_img(room, url)
                    
                    # Cleanup
                    if status in ['WIN', 'LOSS']:
                        del self.sessions[sid]
            
        # 3. Leaderboard Link
        elif text == "!ladder":
             self.send_txt(room, f"🏆 LEADERBOARD: {request.host_url}leaderboard")

    def send_txt(self, room, msg):
        self.ws.send(json.dumps({"handler": "room_message", "id": self.rnd_id(), 
                                 "room": room, "type": "text", "body": msg, "url": "", "length": ""}))

    def send_img(self, room, url):
        self.ws.send(json.dumps({"handler": "room_message", "id": self.rnd_id(), 
                                 "room": room, "type": "image", "body": "", "url": url, "length": ""}))

    def rnd_id(self): return ''.join(random.choices(string.ascii_lowercase + string.digits, k=20))
    def on_error(self, ws, err): print(f"Err: {err}")

bot = BombBot()

# ==============================================================================
# 6. FLASK WEB UI (The "Titan OS" Style)
# ==============================================================================
app = Flask(__name__)

HTML_LOGIN = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TITAN // BOMBCHIP</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;900&display=swap" rel="stylesheet">
    <style>
        body { background: #020202; color: #00f3ff; font-family: 'Orbitron', sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; overflow: hidden; }
        .grid { position: absolute; width: 200vw; height: 200vh; background: linear-gradient(rgba(0, 243, 255, 0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 243, 255, 0.05) 1px, transparent 1px); background-size: 40px 40px; transform: perspective(500px) rotateX(60deg) translateY(-100px) translateZ(-200px); animation: move 10s linear infinite; z-index: -1; }
        @keyframes move { from {transform: perspective(500px) rotateX(60deg) translateY(0);} to {transform: perspective(500px) rotateX(60deg) translateY(40px);} }
        .card { background: rgba(10, 12, 16, 0.9); border: 1px solid #333; border-left: 3px solid #00f3ff; padding: 30px; width: 300px; box-shadow: 0 0 20px rgba(0, 243, 255, 0.1); }
        input { width: 100%; background: #050505; border: 1px solid #444; color: #00f3ff; padding: 10px; margin: 10px 0; font-family: monospace; }
        button { width: 100%; background: rgba(0, 243, 255, 0.1); border: 1px solid #00f3ff; color: #00f3ff; padding: 15px; cursor: pointer; font-weight: bold; margin-top: 10px; transition: 0.3s; }
        button:hover { background: #00f3ff; color: #000; box-shadow: 0 0 15px #00f3ff; }
        h1 { letter-spacing: 5px; margin: 0 0 20px 0; text-align: center; }
    </style>
</head>
<body>
    <div class="grid"></div>
    <div class="card">
        <h1>BOMB CHIP</h1>
        <form action="/start" method="POST">
            <label>OPERATOR ID</label>
            <input name="u" placeholder="Bot Username" required>
            <label>ACCESS KEY</label>
            <input name="p" type="password" placeholder="Bot Password" required>
            <label>TARGET SECTOR</label>
            <input name="r" placeholder="Room Name" required>
            <button>INITIALIZE PROTOCOL</button>
        </form>
        <br>
        <center><a href="/leaderboard" style="color:#666; font-size:12px; text-decoration:none;">VIEW GLOBAL RANKINGS</a></center>
    </div>
</body>
</html>
"""

HTML_LADDER = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GLOBAL RANKINGS</title>
    <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@500;900&display=swap" rel="stylesheet">
    <style>
        body { background: #050505; color: #fff; font-family: 'Orbitron', sans-serif; margin: 0; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; border-bottom: 1px solid #333; padding-bottom: 20px; }
        h1 { color: #00f3ff; text-shadow: 0 0 10px #00f3ff; margin: 0; }
        .sub { color: #555; font-family: 'Share Tech Mono'; font-size: 12px; letter-spacing: 2px; }
        
        .row { display: flex; align-items: center; background: #0a0a0a; border: 1px solid #222; margin-bottom: 10px; padding: 10px; border-radius: 4px; transition: 0.2s; }
        .row:hover { border-color: #00f3ff; background: rgba(0, 243, 255, 0.05); transform: translateX(5px); }
        
        .rank { width: 40px; font-size: 20px; font-weight: bold; color: #444; text-align: center; }
        .rank-1 { color: #ffd700; text-shadow: 0 0 10px #ffd700; }
        .rank-2 { color: #c0c0c0; }
        .rank-3 { color: #cd7f32; }
        
        .pic { width: 50px; height: 50px; border-radius: 50%; border: 2px solid #333; margin: 0 15px; object-fit: cover; }
        .info { flex: 1; font-family: 'Share Tech Mono'; }
        .name { font-size: 18px; color: #eee; }
        .stats { font-size: 12px; color: #666; }
        .score { font-size: 24px; color: #00f3ff; font-weight: bold; }
        
        .btn { position: fixed; bottom: 20px; right: 20px; padding: 10px 20px; background: #00f3ff; color: #000; text-decoration: none; font-weight: bold; border-radius: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>TOP OPERATIVES</h1>
        <div class="sub">BOMB CHIP TACTICAL DATA</div>
    </div>
    
    <div id="list">Loading Data...</div>
    <a href="/" class="btn">LOGIN</a>

    <script>
        fetch('/api/stats').then(r => r.json()).then(data => {
            let html = '';
            data.forEach((u, i) => {
                let r = i + 1;
                let cls = r <= 3 ? 'rank-'+r : '';
                let av = u.avatar || `https://ui-avatars.com/api/?name=${u.username}&background=random`;
                html += `
                <div class="row">
                    <div class="rank ${cls}">#${r}</div>
                    <img src="${av}" class="pic">
                    <div class="info">
                        <div class="name">${u.username}</div>
                        <div class="stats">W: ${u.wins} | L: ${u.losses} | 💣: ${u.bombs}</div>
                    </div>
                    <div class="score">${u.score}</div>
                </div>`;
            });
            document.getElementById('list').innerHTML = html || '<center style="color:#444">NO DATA FOUND</center>';
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_LOGIN)

@app.route('/leaderboard')
def leaderboard():
    return render_template_string(HTML_LADDER)

@app.route('/api/stats')
def api_stats():
    return jsonify(get_leaderboard())

@app.route('/start', methods=['POST'])
def start_bot_route():
    u = request.form.get('u')
    p = request.form.get('p')
    r = request.form.get('r')
    if not bot.active:
        bot.start_bot(u, p, r)
        return redirect(url_for('leaderboard'))
    return "Bot already active. <a href='/'>Back</a>"

if __name__ == '__main__':
    # Render binds to $PORT, default 5000
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
