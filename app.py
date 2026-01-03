import threading
import sqlite3
import json
import time
import random
import string
import ssl
import io
import os
import math
import requests
from flask import Flask, render_template_string, request, jsonify, redirect, url_for
import websocket
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from requests_toolbelt.multipart.encoder import MultipartEncoder

# ==============================================================================
# 1. SYSTEM CONFIGURATION
# ==============================================================================
DB_FILE = "bombchip_pro.db"
WS_URL = "wss://chatp.net:5333/server"
FILE_UPLOAD_URL = "https://cdn.talkinchat.com/post.php"

# Game Mechanics
TOTAL_CHIPS = 6
WIN_REWARD = 100
SAFE_REWARD = 20
LOSS_PENALTY = 50

# Visual Theme (Cyberpunk Palette)
C_BG_START = (10, 10, 18)
C_BG_END = (20, 20, 35)
C_ACCENT = (0, 243, 255)   # Cyan Neon
C_SAFE = (57, 255, 20)     # Neon Green
C_BOMB = (255, 42, 109)    # Neon Red
C_CHIP_OFF = (40, 45, 60)
C_TEXT = (255, 255, 255)

# ==============================================================================
# 2. DATABASE ENGINE (With Streaks)
# ==============================================================================
db_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS players (
            username TEXT PRIMARY KEY,
            score INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            bombs INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            max_streak INTEGER DEFAULT 0,
            avatar TEXT
        )''')
        conn.commit()
        conn.close()

init_db()

def update_stats(user, avatar, result, score_delta):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Ensure user exists
        c.execute("SELECT streak, max_streak, score FROM players WHERE username=?", (user,))
        row = c.fetchone()
        
        if not row:
            c.execute("INSERT INTO players (username, score, avatar) VALUES (?, 0, ?)", (user, avatar))
            curr_streak, max_streak, curr_score = 0, 0, 0
        else:
            curr_streak, max_streak, curr_score = row
            
        # Update Logic
        new_score = curr_score + score_delta
        if new_score < 0: new_score = 0
        
        if result == 'WIN':
            curr_streak += 1
            if curr_streak > max_streak: max_streak = curr_streak
            c.execute("""
                UPDATE players SET 
                score=?, wins=wins+1, streak=?, max_streak=?, avatar=? 
                WHERE username=?""", (new_score, curr_streak, max_streak, avatar, user))
                
        elif result == 'LOSS':
            curr_streak = 0
            c.execute("""
                UPDATE players SET 
                score=?, losses=losses+1, bombs=bombs+1, streak=0, avatar=? 
                WHERE username=?""", (new_score, avatar, user))
                
        elif result == 'SAFE':
            c.execute("UPDATE players SET score=?, avatar=? WHERE username=?", (new_score, avatar, user))
            
        conn.commit()
        conn.close()

def get_leaderboard():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # Sort by Score -> Then by Win Rate
        c.execute("SELECT * FROM players ORDER BY score DESC, wins DESC LIMIT 50")
        rows = [dict(row) for row in c.fetchall()]
        conn.close()
        return rows

# ==============================================================================
# 3. ADVANCED GRAPHICS ENGINE (The "Wow" Factor)
# ==============================================================================
class GfxEngine:
    
    @staticmethod
    def draw_gradient(draw, w, h):
        """Draws a vertical gradient background"""
        for y in range(h):
            r = int(C_BG_START[0] + (C_BG_END[0] - C_BG_START[0]) * y / h)
            g = int(C_BG_START[1] + (C_BG_END[1] - C_BG_START[1]) * y / h)
            b = int(C_BG_START[2] + (C_BG_END[2] - C_BG_START[2]) * y / h)
            draw.line([(0, y), (w, y)], fill=(r, g, b))

    @staticmethod
    def draw_grid(draw, w, h):
        """Draws a cyberpunk grid overlay"""
        step = 40
        color = (255, 255, 255, 15) # Very faint white
        for x in range(0, w, step):
            draw.line([(x, 0), (x, h)], fill=color)
        for y in range(0, h, step):
            draw.line([(0, y), (w, y)], fill=color)

    @staticmethod
    def draw_gem(draw, cx, cy, size, color):
        """Draws a geometric gem (Safe)"""
        r = size // 2
        points = [
            (cx, cy - r), (cx + r*0.8, cy), (cx, cy + r), (cx - r*0.8, cy)
        ]
        draw.polygon(points, fill=color, outline=(255,255,255), width=2)
        # Shine
        draw.line([(cx - r*0.3, cy - r*0.3), (cx, cy - r*0.8)], fill=(255,255,255), width=2)

    @staticmethod
    def draw_explosion(draw, cx, cy, size):
        """Draws a spiky explosion (Bomb)"""
        points = []
        import math
        spikes = 12
        outer_r = size // 2
        inner_r = size // 4
        
        for i in range(spikes * 2):
            angle = (math.pi * i) / spikes
            r = outer_r if i % 2 == 0 else inner_r
            x = cx + math.cos(angle) * r
            y = cy + math.sin(angle) * r
            points.append((x, y))
            
        draw.polygon(points, fill=C_BOMB, outline=(255, 200, 0), width=2)
        draw.ellipse([cx-5, cy-5, cx+5, cy+5], fill=(255, 255, 0))

    @staticmethod
    def generate_board(states, message="SELECT TARGET"):
        W, H = 800, 600
        img = Image.new("RGBA", (W, H))
        draw = ImageDraw.Draw(img)
        
        # 1. Background & Grid
        GfxEngine.draw_gradient(draw, W, H)
        GfxEngine.draw_grid(draw, W, H)
        
        # 2. Header
        try:
            # Try loading a better font if available on linux, else default
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if os.path.exists(font_path):
                font_l = ImageFont.truetype(font_path, 40)
                font_m = ImageFont.truetype(font_path, 28)
            else:
                font_l = ImageFont.load_default()
                font_m = ImageFont.load_default()
        except:
            font_l = ImageFont.load_default()
            font_m = ImageFont.load_default()

        # 3. Draw Chips (Layout)
        cols, rows = 3, 2
        chip_w, chip_h = 180, 140
        gap_x, gap_y = 50, 50
        
        start_x = (W - (cols * chip_w + (cols-1) * gap_x)) // 2
        start_y = 100

        for i in range(1, TOTAL_CHIPS + 1):
            r = (i - 1) // cols
            c = (i - 1) % cols
            
            x = start_x + c * (chip_w + gap_x)
            y = start_y + r * (chip_h + gap_y)
            rect = [x, y, x+chip_w, y+chip_h]
            
            state = states.get(i, 'UNKNOWN')
            
            # Base Button Shape
            if state == 'UNKNOWN':
                fill_c = C_CHIP_OFF
                border_c = (100, 100, 120)
                glow = False
            elif state == 'SAFE':
                fill_c = (20, 50, 20)
                border_c = C_SAFE
                glow = True
            elif state == 'BOMB':
                fill_c = (50, 20, 20)
                border_c = C_BOMB
                glow = True
            
            # Draw Button Shadow
            draw.rounded_rectangle([x+5, y+5, x+chip_w+5, y+chip_h+5], radius=20, fill=(0,0,0,100))
            
            # Draw Button Body
            draw.rounded_rectangle(rect, radius=20, fill=fill_c, outline=border_c, width=3)
            
            # Draw Content
            cx, cy = x + chip_w//2, y + chip_h//2
            
            if state == 'UNKNOWN':
                # Draw Number
                t_w = draw.textlength(str(i), font=font_l)
                draw.text((cx - t_w/2, cy - 20), str(i), fill=(200, 200, 200), font=font_l)
            elif state == 'SAFE':
                GfxEngine.draw_gem(draw, cx, cy, 50, C_SAFE)
            elif state == 'BOMB':
                GfxEngine.draw_explosion(draw, cx, cy, 60)

        # 4. Message Bar (Glassmorphism)
        bar_h = 80
        draw.rectangle([0, H-bar_h, W, H], fill=(0, 0, 0, 200))
        draw.line([0, H-bar_h, W, H-bar_h], fill=C_ACCENT, width=2)
        
        # Centered Text
        t_w = draw.textlength(message, font=font_m)
        draw.text(((W-t_w)/2, H - 55), message, fill=C_ACCENT, font=font_m)

        # Save
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf

# ==============================================================================
# 4. ROBUST UPLOADER
# ==============================================================================
def upload_cdn(buf, bot_name, room):
    try:
        filename = f"chip_{int(time.time())}.png"
        boundary = '----WebKitFormBoundary' + ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        
        m = MultipartEncoder(fields={
            'file': (filename, buf, 'image/png'),
            'jid': bot_name,
            'is_private': 'no',
            'room': room,
            'device_id': "bot_device_01"
        }, boundary=boundary)

        # 5 Second Timeout to prevent bot freezing
        res = requests.post(FILE_UPLOAD_URL, data=m, headers={'Content-Type': m.content_type}, timeout=6)
        
        if res.status_code == 200:
            return res.text.strip()
    except Exception as e:
        print(f"[UPLOAD FAIL] {e}")
    return None

# ==============================================================================
# 5. GAME LOGIC (Fair Play)
# ==============================================================================
class Session:
    def __init__(self, user):
        self.user = user
        self.chips = {i: 'UNKNOWN' for i in range(1, TOTAL_CHIPS+1)}
        self.bomb = -1
        self.moves = 0
        self.active = True
        
    def play(self, choice):
        if not self.active: return None, "Game Finished."
        
        # Safe Start: Bomb is assigned AFTER first move, ensuring first pick is never bomb
        if self.moves == 0:
            opts = [i for i in range(1, TOTAL_CHIPS+1) if i != choice]
            self.bomb = random.choice(opts)
            
        self.moves += 1
        
        if choice == self.bomb:
            self.chips[choice] = 'BOMB'
            self.active = False
            return 'LOSS', f"BOOM! BOMB HIT! (-{LOSS_PENALTY})"
        else:
            self.chips[choice] = 'SAFE'
            # Win if all safes found (Total - 1)
            safe_found = sum(1 for x in self.chips.values() if x == 'SAFE')
            if safe_found >= (TOTAL_CHIPS - 1):
                self.active = False
                return 'WIN', f"JACKPOT! CLEARED! (+{WIN_REWARD})"
            return 'SAFE', f"SAFE! (+{SAFE_REWARD}) Next?"

# ==============================================================================
# 6. BOT CLIENT (Non-Blocking)
# ==============================================================================
class Bot:
    def __init__(self):
        self.active = False
        self.ws = None
        self.creds = {}
        self.sessions = {} # Room_User -> Session
        
    def start(self, u, p, r):
        self.creds = {'u': u, 'p': p, 'r': r}
        self.active = True
        t = threading.Thread(target=self.connect)
        t.daemon = True
        t.start()
        
    def connect(self):
        while self.active:
            try:
                self.ws = websocket.WebSocketApp(WS_URL,
                    on_open=self.on_open, on_message=self.on_msg, on_error=self.on_err)
                self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
                time.sleep(5)
            except: time.sleep(10)
            
    def on_open(self, ws):
        print("[BOT] Connecting...")
        ws.send(json.dumps({"handler": "login", "username": self.creds['u'], 
                            "password": self.creds['p'], "id": self.id()}))
                            
    def on_msg(self, ws, msg):
        try:
            d = json.loads(msg)
            h = d.get('handler')
            
            if h == 'login_event' and d.get('type') == 'success':
                print("[BOT] Joined Room.")
                ws.send(json.dumps({"handler": "room_join", "id": self.id(), "name": self.creds['r']}))
                
            if h == 'room_message' or d.get('type') == 'text':
                # Handle Logic in new thread to avoid blocking heartbeat
                threading.Thread(target=self.process, args=(d,)).start()
        except: pass
        
    def process(self, d):
        sender = d.get('from') or d.get('username')
        if sender == self.creds['u']: return
        
        txt = str(d.get('body') or d.get('text') or "").strip().lower()
        room = self.creds['r']
        av = d.get('avatar_url') or d.get('icon') or ""
        sid = f"{room}_{sender}"
        
        # COMMAND: START
        if txt == "!bombchip":
            if sid in self.sessions:
                self.reply_txt(room, f"@{sender} Finish your current game first!")
                return
            
            self.sessions[sid] = Session(sender)
            self.reply_with_board(room, sid, f"@{sender} GAME START! Pick 1-6")
            
        # COMMAND: MOVE
        elif txt.isdigit() and sid in self.sessions:
            num = int(text)
            if 1 <= num <= TOTAL_CHIPS:
                sess = self.sessions[sid]
                # If already open
                if sess.chips.get(num) != 'UNKNOWN':
                    self.reply_txt(room, f"@{sender} Chip {num} already opened!")
                    return
                
                res, msg = sess.play(num)
                
                # Update DB
                score = 0
                if res == 'WIN': score = WIN_REWARD
                elif res == 'LOSS': score = -LOSS_PENALTY
                elif res == 'SAFE': score = SAFE_REWARD
                update_stats(sender, av, res, score)
                
                # Reply
                self.reply_with_board(room, sid, f"@{sender} {msg}")
                
                if res in ['WIN', 'LOSS']:
                    del self.sessions[sid]

        # COMMAND: LADDER
        elif txt == "!ladder":
            self.reply_txt(room, f"🏆 LEADERBOARD: {request.host_url}leaderboard")

    def reply_with_board(self, room, sid, txt):
        try:
            # Generate Image
            sess = self.sessions[sid]
            buf = GfxEngine.generate_board(sess.chips, txt.replace('@'+sess.user, '').strip())
            
            # Upload
            url = upload_cdn(buf, self.creds['u'], room)
            
            if url:
                # Send Image
                self.ws.send(json.dumps({
                    "handler": "room_message", "id": self.id(), "room": room,
                    "type": "image", "body": "", "url": url, "length": ""
                }))
            else:
                # Upload Failed, Send Text Fallback
                self.ws.send(json.dumps({
                    "handler": "room_message", "id": self.id(), "room": room,
                    "type": "text", "body": f"[IMG FAIL] {txt}", "url": "", "length": ""
                }))
        except Exception as e:
            print(f"Reply Error: {e}")

    def reply_txt(self, room, txt):
        self.ws.send(json.dumps({
            "handler": "room_message", "id": self.id(), "room": room,
            "type": "text", "body": txt, "url": "", "length": ""
        }))

    def id(self): return ''.join(random.choices(string.ascii_letters, k=16))
    def on_err(self, ws, e): print(f"Err: {e}")

bot = Bot()

# ==============================================================================
# 7. FLASK WEB UI (The Premium Dashboard)
# ==============================================================================
app = Flask(__name__)

HTML_DASH = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BOMBCHIP // ULTIMATE</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;900&family=Poppins:wght@300;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0b15; --card: #151525; --neon: #00f3ff; --gold: #ffd700; --red: #ff2a6d;
        }
        body { margin: 0; background: var(--bg); color: #fff; font-family: 'Poppins', sans-serif; overflow-x: hidden; }
        
        /* Animated Background */
        .bg { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: -1; 
              background: radial-gradient(circle at 50% 50%, #1a1a2e 0%, #000 100%); }
        .orb { position: absolute; width: 300px; height: 300px; border-radius: 50%; background: var(--neon); filter: blur(150px); opacity: 0.2; animation: float 10s infinite; }
        @keyframes float { 0%{transform:translate(0,0);} 50%{transform:translate(50px, -50px);} 100%{transform:translate(0,0);} }

        /* Login Card */
        .center-wrap { height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-card {
            background: rgba(255,255,255,0.05); backdrop-filter: blur(10px);
            padding: 40px; border-radius: 20px; border: 1px solid rgba(255,255,255,0.1);
            text-align: center; width: 320px; box-shadow: 0 0 30px rgba(0,243,255,0.1);
        }
        h1 { font-family: 'Orbitron'; letter-spacing: 2px; color: var(--neon); margin: 0 0 10px 0; }
        input { 
            width: 100%; padding: 12px; margin: 8px 0; background: #000; 
            border: 1px solid #333; color: #fff; border-radius: 5px; box-sizing: border-box;
        }
        button {
            width: 100%; padding: 15px; background: linear-gradient(45deg, var(--neon), #0066ff);
            border: none; color: #000; font-weight: bold; border-radius: 5px; margin-top: 15px;
            cursor: pointer; font-family: 'Orbitron'; transition: 0.3s;
        }
        button:hover { transform: scale(1.05); box-shadow: 0 0 20px var(--neon); }

        /* Leaderboard */
        .lb-container { max-width: 800px; margin: 50px auto; padding: 0 20px; }
        .lb-header { text-align: center; margin-bottom: 40px; }
        .lb-header h2 { font-family: 'Orbitron'; font-size: 2.5rem; margin: 0; text-transform: uppercase; background: -webkit-linear-gradient(var(--neon), #fff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        
        .player-card {
            display: flex; align-items: center; background: rgba(255,255,255,0.03);
            border-bottom: 1px solid rgba(255,255,255,0.05); padding: 15px; margin-bottom: 5px;
            border-radius: 10px; transition: 0.3s;
        }
        .player-card:hover { transform: translateX(10px); background: rgba(255,255,255,0.08); border-left: 3px solid var(--neon); }
        
        .rank { font-family: 'Orbitron'; font-size: 1.5rem; width: 50px; text-align: center; color: #555; }
        .rank-1 { color: var(--gold); text-shadow: 0 0 10px var(--gold); font-size: 2rem; }
        .rank-2 { color: silver; }
        .rank-3 { color: #cd7f32; }
        
        .avatar { width: 50px; height: 50px; border-radius: 50%; margin: 0 20px; border: 2px solid #333; object-fit: cover; }
        .info { flex: 1; }
        .name { font-weight: 600; font-size: 1.1rem; }
        .meta { font-size: 0.8rem; color: #888; }
        .score { font-family: 'Orbitron'; font-size: 1.5rem; color: var(--neon); text-shadow: 0 0 5px var(--neon); }
    </style>
</head>
<body>
    <div class="bg"><div class="orb"></div></div>

    {% if page == 'login' %}
    <div class="center-wrap">
        <div class="login-card">
            <h1>BOMB CHIP</h1>
            <p style="color:#888; font-size:0.8rem; margin-bottom:20px;">SYSTEM CONTROL</p>
            <form action="/start" method="POST">
                <input name="u" placeholder="Bot Username" required>
                <input name="p" type="password" placeholder="Bot Password" required>
                <input name="r" placeholder="Room Name" required>
                <button>ACTIVATE BOT</button>
            </form>
            <br>
            <a href="/leaderboard" style="color:#fff; text-decoration:none; font-size:0.8rem;">VIEW LIVE RANKINGS</a>
        </div>
    </div>
    {% else %}
    <div class="lb-container">
        <div class="lb-header">
            <h2>Global Rankings</h2>
            <p style="color:#888;">LIVE DATA STREAM</p>
        </div>
        <div id="board">
            <center style="color:#666; margin-top:50px;">CONNECTING TO SERVER...</center>
        </div>
    </div>
    <script>
        function load() {
            fetch('/api/data').then(r=>r.json()).then(d=>{
                let h = '';
                d.forEach((u, i)=>{
                    let r = i+1;
                    let cls = r<=3 ? 'rank-'+r : '';
                    let av = u.avatar || `https://ui-avatars.com/api/?name=${u.username}&background=random`;
                    h += `
                    <div class="player-card">
                        <div class="rank ${cls}">#${r}</div>
                        <img src="${av}" class="avatar">
                        <div class="info">
                            <div class="name">${u.username}</div>
                            <div class="meta">Wins: ${u.wins} | Streak: ${u.streak} 🔥</div>
                        </div>
                        <div class="score">${u.score}</div>
                    </div>`;
                });
                document.getElementById('board').innerHTML = h || '<center>NO DATA</center>';
            });
        }
        setInterval(load, 3000);
        load();
    </script>
    {% endif %}
</body>
</html>
"""

@app.route('/')
def index(): return render_template_string(HTML_DASH, page='login')

@app.route('/leaderboard')
def ladder(): return render_template_string(HTML_DASH, page='ladder')

@app.route('/api/data')
def api(): return jsonify(get_leaderboard())

@app.route('/start', methods=['POST'])
def start():
    u, p, r = request.form['u'], request.form['p'], request.form['r']
    if not bot.active:
        bot.start(u, p, r)
        return redirect(url_for('ladder'))
    return "BOT ACTIVE. <a href='/leaderboard'>Go to Ladder</a>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)