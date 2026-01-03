import os
import json
import time
import threading
import io
import requests
import websocket
from flask import Flask, render_template_string, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# --- GLOBAL VARIABLES (STATE STORAGE) ---
BOT_STATE = {
    "ws": None,
    "connected": False,
    "user": "",
    "pass": "",
    "room": "",
    "thread": None
}

GAME_STATE = {
    "active": False,
    "player": None,
    "bombs": [],
    "eaten": [],
    "user_avatars": {} # Cache avatars
}

# Logs store karne ke liye taaki browser par dikha sakein
LOGS = []

def add_log(msg, type="sys"):
    timestamp = time.strftime("%H:%M:%S")
    LOGS.append({"time": timestamp, "msg": msg, "type": type})
    # Keep only last 100 logs to save memory
    if len(LOGS) > 100:
        LOGS.pop(0)

# --- WEBSOCKET CLIENT LOGIC (RUNS ON SERVER) ---

def on_message(ws, message):
    try:
        data = json.loads(message)
        
        # Ack packet ignore karein logs clear rakhne ke liye
        if data.get("handler") == "receipt_ack":
            return

        # Avatar Capture
        if data.get("from") and data.get("avatar_url"):
            GAME_STATE["user_avatars"][data["from"]] = data["avatar_url"]

        # Log incoming (Sirf text msg ya important events)
        if data.get("handler") == "room_event" and data.get("type") == "text":
            add_log(f"[{data['from']}]: {data['body']}", "in")
            process_game_logic(data['from'], data['body'])
        elif data.get("handler") == "login_event":
            if data["type"] == "success":
                add_log("Login Success. Joining Room...", "sys")
                # Join Room
                join_pkt = {
                    "handler": "room_join", 
                    "id": str(time.time()), 
                    "name": BOT_STATE["room"]
                }
                ws.send(json.dumps(join_pkt))
            else:
                add_log(f"Login Failed: {data.get('reason')}", "err")

    except Exception as e:
        print(f"Error: {e}")

def on_error(ws, error):
    add_log(f"Error: {error}", "err")

def on_close(ws, close_status_code, close_msg):
    add_log("Disconnected from Server.", "err")
    BOT_STATE["connected"] = False

def on_open(ws):
    BOT_STATE["connected"] = True
    add_log("Socket Connected. Logging in...", "sys")
    
    # Login Packet
    login_pkt = {
        "handler": "login",
        "id": str(time.time()),
        "username": BOT_STATE["user"],
        "password": BOT_STATE["pass"],
        "platform": "web"
    }
    ws.send(json.dumps(login_pkt))
    
    # Start Ping Thread
    def run_ping():
        while BOT_STATE["connected"]:
            time.sleep(20)
            try:
                ws.send(json.dumps({"handler": "ping"}))
            except:
                break
    threading.Thread(target=run_ping, daemon=True).start()

# --- SEND FUNCTION ---
def send_room_msg(text, msg_type="text", url=""):
    if BOT_STATE["ws"] and BOT_STATE["connected"]:
        pkt = {
            "handler": "room_message",
            "id": str(time.time()),
            "room": BOT_STATE["room"],
            "type": msg_type,
            "body": text,
            "url": url,
            "length": "0"
        }
        try:
            BOT_STATE["ws"].send(json.dumps(pkt))
            if msg_type == "text":
                add_log(f"BOT >> {text}", "out")
            else:
                add_log(f"BOT >> SENT IMAGE", "out")
        except Exception as e:
            add_log(f"Send Error: {e}", "err")

# --- GAME LOGIC (PYTHON SIDE) ---
def process_game_logic(user, msg):
    msg = msg.strip().lower()
    
    # Ignore Self
    if user.lower() == BOT_STATE["user"].lower():
        return

    # !START
    if msg == "!start":
        if GAME_STATE["active"]:
            send_room_msg(f"⚠ Game running! {GAME_STATE['player']} is playing.")
            return
        
        import random
        GAME_STATE["active"] = True
        GAME_STATE["player"] = user
        GAME_STATE["eaten"] = []
        GAME_STATE["bombs"] = random.sample(range(1, 10), 2) # 2 Unique Bombs
        
        add_log(f"Game Started by {user}. Bombs: {GAME_STATE['bombs']}", "game")
        grid = render_grid()
        send_room_msg(f"🎮 START! Player: {user}\nAvoid 2 Bombs! Eat 4 Chips to WIN.\nType !eat <number>\n\n{grid}")

    # !EAT
    elif msg.startswith("!eat "):
        if not GAME_STATE["active"]: return
        if user != GAME_STATE["player"]: return
        
        try:
            num = int(msg.split()[1])
        except:
            return send_room_msg("⚠ Invalid number.")

        if num < 1 or num > 9: return send_room_msg("⚠ Choose 1-9.")
        if num in GAME_STATE["eaten"]: return send_room_msg("⚠ Already eaten!")

        # Check Bomb
        if num in GAME_STATE["bombs"]:
            GAME_STATE["active"] = False
            grid = render_grid(reveal=True, exploded=num)
            send_room_msg(f"💥 BOOM! You ate a BOMB at #{num}!\n💀 GAME OVER.\nBombs: {GAME_STATE['bombs']}\n\n{grid}")
            add_log(f"Game Over: {user} hit bomb.", "err")
        else:
            GAME_STATE["eaten"].append(num)
            
            # Win Condition: 4 Chips
            if len(GAME_STATE["eaten"]) == 4:
                GAME_STATE["active"] = False
                grid = render_grid(reveal=True)
                send_room_msg(f"🎉 WINNER! {user} ate 4 chips!\n🥔 CHAMPION! Generating Prize...\n\n{grid}")
                
                # Wait 1 sec then send image
                time.sleep(1)
                avatar = GAME_STATE["user_avatars"].get(user, "")
                # NOTE: For server side, we need full public URL of THIS app
                # Since we don't know the exact Render URL in code dynamically easily without request context,
                # We will rely on the user navigating to it, OR use relative paths if the chat client supports it (unlikely).
                # We will construct it based on request host when available, but here we run in thread.
                # Hack: We will pass the domain from the connect request or just log it.
                
                # For now, let's assume the user has to set the domain or we just use text if image fails.
                # Better approach: The Flask app knows its own URL.
                pass 
                # Trigger Image Send Logic is slightly complex from thread without app context.
                # We will just fire the image message using a helper.
                send_winner_image(user, avatar)
                
                add_log(f"Victory: {user}", "game")
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

def send_winner_image(username, avatar):
    # This URL needs to be your Render URL. 
    # Since we are inside a thread, we can't get 'request.url_root'.
    # We will use a placeholder or try to capture it during connect.
    # For now, let's just use the current domain stored in BOT_STATE
    domain = BOT_STATE.get("domain", "")
    if domain:
        img_url = f"{domain}winner-card?name={username}&avatar={requests.utils.quote(avatar)}"
        send_room_msg("", msg_type="image", url=img_url)
    else:
        send_room_msg("🏆 [Winner Image Generation Failed - Domain Unknown] 🏆")

# --- FLASK ROUTES ---

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/connect', methods=['POST'])
def start_bot():
    data = request.json
    if BOT_STATE["connected"]:
        return jsonify({"status": "Already Connected"})

    BOT_STATE["user"] = data["u"]
    BOT_STATE["pass"] = data["p"]
    BOT_STATE["room"] = data["r"]
    # Save the domain to generate image links later
    BOT_STATE["domain"] = request.url_root 

    def run_ws():
        websocket.enableTrace(False)
        ws = websocket.WebSocketApp("wss://chatp.net:5333/server",
                                  on_open=on_open,
                                  on_message=on_message,
                                  on_error=on_error,
                                  on_close=on_close)
        BOT_STATE["ws"] = ws
        ws.run_forever()

    t = threading.Thread(target=run_ws)
    t.start()
    BOT_STATE["thread"] = t
    
    return jsonify({"status": "Connecting..."})

@app.route('/logs')
def get_logs():
    return jsonify(LOGS)

@app.route('/winner-card')
def winner_card():
    # ... (Same Pillow Logic as before) ...
    try:
        username = request.args.get('name', 'Winner')
        avatar_url = request.args.get('avatar', '')

        width, height = 600, 300
        img = Image.new('RGB', (width, height), color=(20, 20, 20))
        draw = ImageDraw.Draw(img)

        draw.rectangle([0, 0, width-1, height-1], outline="#00f3ff", width=5)
        draw.rectangle([10, 10, width-10, height-10], outline="#ffd700", width=2)

        try:
            if avatar_url and avatar_url != "undefined":
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(avatar_url, headers=headers, timeout=5)
                avi = Image.open(io.BytesIO(response.content)).convert("RGBA")
                avi = avi.resize((150, 150))
                mask = Image.new("L", (150, 150), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, 150, 150), fill=255)
                img.paste(avi, (50, 75), mask)
            else:
                draw.ellipse((50, 75, 200, 225), fill="#444", outline="#fff")
        except:
            draw.ellipse((50, 75, 200, 225), fill="#444", outline="#fff")

        draw.text((230, 80), "--- WINNER ---", fill="#ffd700") 
        draw.text((230, 110), f"Name: {username}", fill="#ffffff")
        draw.text((230, 140), "Result: 4 Safe Chips", fill="#00ff41")
        draw.text((230, 170), "Status: TITAN CHAMPION", fill="#00f3ff")

        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        return send_file(img_io, mimetype='image/png')

    except Exception as e:
        return str(e), 500

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>TITAN OS - SERVER BOT</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root { --neon: #00f3ff; --term: #00ff41; --danger: #ff003c; --bg: #050505; }
        body { background: var(--bg); color: var(--neon); font-family: 'Consolas', monospace; margin: 0; display: flex; flex-direction: column; height: 100vh; }
        .nav-bar { background: #111; padding: 15px; border-bottom: 2px solid var(--neon); display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
        input { background: #000; border: 1px solid #444; color: var(--neon); padding: 10px; outline: none; }
        button { padding: 10px; font-weight: bold; cursor: pointer; border: 1px solid var(--neon); background: transparent; color: var(--neon); }
        button:hover { background: var(--neon); color: #000; }
        .terminal-zone { flex: 1; background: #000; overflow-y: scroll; padding: 15px; font-size: 12px; }
        .line { margin-bottom: 5px; border-left: 2px solid #333; padding-left: 10px; }
        .in { color: var(--term); } .out { color: var(--neon); } .err { color: var(--danger); } .game { color: #ffd700; }
    </style>
</head>
<body>
    <div class="nav-bar">
        <input type="text" id="u" placeholder="Bot User">
        <input type="password" id="p" placeholder="Bot Pass">
        <input type="text" id="r" placeholder="Room Name">
        <button onclick="startBot()">START SERVER BOT</button>
    </div>
    <div class="terminal-zone" id="term">
        <div class="line sys">Ready. Click START to run bot on server (24/7).</div>
    </div>
    <script>
        function startBot() {
            const u = document.getElementById('u').value;
            const p = document.getElementById('p').value;
            const r = document.getElementById('r').value;
            
            fetch('/connect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({u, p, r})
            }).then(res => res.json()).then(data => {
                alert(data.status);
            });
        }

        // Poll for logs every 2 seconds
        setInterval(() => {
            fetch('/logs').then(res => res.json()).then(logs => {
                const term = document.getElementById('term');
                term.innerHTML = "";
                logs.forEach(l => {
                    const div = document.createElement('div');
                    div.className = `line ${l.type}`;
                    div.innerHTML = `<b>[${l.time}]</b> ${l.msg}`;
                    term.appendChild(div);
                });
                // Auto scroll only if near bottom (simplified)
                // term.scrollTop = term.scrollHeight; 
            });
        }, 2000);
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
