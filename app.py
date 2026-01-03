import os
import io
import requests
from flask import Flask, render_template_string, request, send_file
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# --- PYTHON IMAGE GENERATOR (PILLOW) ---
@app.route('/winner-card')
def winner_card():
    try:
        # Get Query Parameters
        username = request.args.get('name', 'Winner')
        avatar_url = request.args.get('avatar', '')

        # 1. Create Base Image (Dark Background)
        width, height = 600, 300
        img = Image.new('RGB', (width, height), color=(20, 20, 20))
        draw = ImageDraw.Draw(img)

        # 2. Add Border (Gold/Neon)
        draw.rectangle([0, 0, width-1, height-1], outline="#00f3ff", width=5)
        draw.rectangle([10, 10, width-10, height-10], outline="#ffd700", width=2)

        # 3. Process Avatar
        try:
            if avatar_url and avatar_url != "undefined" and avatar_url != "":
                # Fake a Browser Request to avoid 403 Forbidden Error
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = requests.get(avatar_url, headers=headers, timeout=5)
                
                avi = Image.open(io.BytesIO(response.content)).convert("RGBA")
                avi = avi.resize((150, 150))
                
                # Make Avatar Circular
                mask = Image.new("L", (150, 150), 0)
                draw_mask = ImageDraw.Draw(mask)
                draw_mask.ellipse((0, 0, 150, 150), fill=255)
                
                img.paste(avi, (50, 75), mask)
            else:
                # Fallback (If no photo found)
                draw.ellipse((50, 75, 200, 225), fill="#444", outline="#fff")
                draw.text((90, 140), "NO IMG", fill="#fff")
        except Exception as e:
            print(f"Avatar Error: {e}")
            draw.ellipse((50, 75, 200, 225), fill="#444", outline="#fff")

        # 4. Add Text
        # Note: Using default PIL font
        draw.text((230, 80), "--- WINNER ---", fill="#ffd700") 
        draw.text((230, 110), f"Name: {username}", fill="#ffffff")
        draw.text((230, 140), "Result: 4 Safe Chips", fill="#00ff41") # UPDATED TEXT
        draw.text((230, 170), "Status: TITAN CHAMPION", fill="#00f3ff")

        # 5. Save to Buffer
        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        return send_file(img_io, mimetype='image/png')

    except Exception as e:
        return str(e), 500

# --- FRONTEND HTML/JS ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>TITAN OS - GAME MASTER</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root { --neon: #00f3ff; --term: #00ff41; --danger: #ff003c; --gold: #ffd700; --bg: #050505; }
        body { background: var(--bg); color: var(--neon); font-family: 'Consolas', monospace; margin: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
        
        .nav-bar { background: #111; padding: 15px; border-bottom: 2px solid var(--neon); display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.8); }
        input { background: #000; border: 1px solid #333; color: var(--neon); padding: 10px; border-radius: 4px; font-size: 12px; outline: none; border: 1px solid #444; }
        
        button { padding: 10px; font-weight: bold; cursor: pointer; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-size: 11px; text-transform: uppercase; }
        button:hover { background: var(--neon); color: #000; }
        .danger-btn { border-color: var(--danger); color: var(--danger); }

        .terminal-zone { flex: 1; display: flex; flex-direction: column; background: #000; overflow: hidden; }
        .term-head { background: #1a1a1a; padding: 8px 15px; font-size: 11px; color: #666; display: flex; justify-content: space-between; border-bottom: 1px solid #222; }
        .terminal-body { flex: 1; overflow-y: scroll; padding: 15px; font-size: 12px; scroll-behavior: smooth; }
        
        .line { margin-bottom: 10px; border-left: 3px solid #222; padding-left: 12px; word-break: break-all; }
        .in { color: var(--term); border-color: var(--term); } 
        .out { color: var(--neon); border-color: var(--neon); }
        .err { color: var(--danger); border-color: var(--danger); }
        .game { color: var(--gold); border-color: var(--gold); background: rgba(255, 215, 0, 0.05); }
        
        .json-dump { background: #080808; padding: 10px; margin-top: 5px; border: 1px solid #1a1a1a; display: block; color: #00ccff; font-size: 11px; border-radius: 4px; white-space: pre-wrap; font-family: monospace; }
        
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 10px; }
    </style>
</head>
<body>
    <div class="nav-bar">
        <input type="text" id="u" placeholder="Bot User">
        <input type="password" id="p" placeholder="Bot Pass">
        <input type="text" id="r" placeholder="Room Name">
        
        <div style="display:flex; gap:5px;">
            <button onclick="connectWS()">CONNECT BOT</button>
            <button class="danger-btn" onclick="resetGameLocally()">RESET SYSTEM</button>
            <button onclick="document.getElementById('terminal').innerHTML=''" style="border-color:#444;color:#444">CLEAR</button>
        </div>
    </div>

    <div class="terminal-zone">
        <div class="term-head">
            <span>TITAN_GAME_ENGINE v4.0 [EASY_MODE: 4_TO_WIN]</span>
            <span id="stat">OFFLINE</span>
        </div>
        <div id="terminal" class="terminal-body">
            <div class="line">System ready. Enter credentials and click CONNECT BOT.</div>
            <div class="line game">Game Rules: 2 Bombs, Win at 4 Safe Chips.</div>
        </div>
    </div>

    <script>
        let ws; let pinger; const term = document.getElementById('terminal');

        let gameState = {
            active: false,
            player: null,
            bombs: [],     
            eaten: []      
        };

        // Cache for User Avatars
        let userAvatars = {};

        function log(msg, type='sys', payload=null) {
            const div = document.createElement('div');
            div.className = `line ${type}`;
            let html = `<b>[${new Date().toLocaleTimeString()}]</b> ${msg}`;
            if(payload) {
                html += `<div class="json-dump">${JSON.stringify(payload, null, 2)}</div>`;
            }
            div.innerHTML = html;
            term.appendChild(div);
            term.scrollTop = term.scrollHeight;
        }

        function connectWS() {
            const u = document.getElementById('u').value, p = document.getElementById('p').value, r = document.getElementById('r').value;
            if(!u || !p || !r) return log("Error: Missing Username, Password or Room!", "err");

            ws = new WebSocket("wss://chatp.net:5333/server");

            ws.onopen = () => {
                document.getElementById('stat').innerText = "ONLINE";
                document.getElementById('stat').style.color = "var(--term)";
                log("Socket Connected. Tunnel Established.", "sys");
                
                const loginData = {handler: "login", id: Math.random(), username: u, password: p, platform: "web"};
                ws.send(JSON.stringify(loginData));
                pinger = setInterval(() => { if(ws.readyState === 1) ws.send(JSON.stringify({handler:"ping"})); }, 20000);
            };

            ws.onmessage = (e) => {
                const data = JSON.parse(e.data);
                if(data.handler === "receipt_ack") return; 

                // --- CAPTURE AVATAR ---
                if(data.from && data.avatar_url) {
                    userAvatars[data.from] = data.avatar_url;
                }

                log("RECV << " + (data.handler || "EVENT"), "in", data);
                
                if(data.handler === "login_event" && data.type === "success") {
                    ws.send(JSON.stringify({handler: "room_join", id: Math.random(), name: r}));
                }

                if(data.handler === "room_event" && data.type === "text") {
                    handleGameCommand(data.from, data.body);
                }
            };

            ws.onclose = () => {
                clearInterval(pinger);
                document.getElementById('stat').innerText = "OFFLINE";
                document.getElementById('stat').style.color = "var(--danger)";
                setTimeout(connectWS, 3000); 
            };
        }

        function handleGameCommand(user, msg) {
            msg = msg.trim().toLowerCase();
            const myUser = document.getElementById('u').value.toLowerCase();
            if(user.toLowerCase() === myUser) return; 

            // 1. START COMMAND
            if (msg === "!start") {
                if (gameState.active) {
                    return sendRoomMsg(`⚠ Game running! ${gameState.player} is playing.`);
                }
                
                gameState.active = true;
                gameState.player = user;
                gameState.eaten = [];
                gameState.bombs = [];
                
                while(gameState.bombs.length < 2) {
                    let r = Math.floor(Math.random() * 9) + 1;
                    if(!gameState.bombs.includes(r)) gameState.bombs.push(r);
                }

                log(`GAME STARTED by ${user}. Bombs: [${gameState.bombs.join(', ')}]`, "game");
                const grid = renderGrid();
                // UPDATED TEXT: Eat 4 Chips
                sendRoomMsg(`🎮 START! Player: ${user}\\nAvoid 2 Bombs! Eat 4 Chips to WIN.\\nType !eat <number>\\n\\n${grid}`);
            }

            // 2. EAT COMMAND
            else if (msg.startsWith("!eat ")) {
                if (!gameState.active) return;
                if (user !== gameState.player) return; 

                const num = parseInt(msg.split(" ")[1]);

                if (isNaN(num) || num < 1 || num > 9) return sendRoomMsg(`⚠ Choose 1-9.`);
                if (gameState.eaten.includes(num)) return sendRoomMsg(`⚠ Already eaten!`);

                if (gameState.bombs.includes(num)) {
                    // --- LOSE ---
                    gameState.active = false;
                    const finalGrid = renderGrid(true, num); 
                    sendRoomMsg(`💥 BOOM! BOMB AT #${num}!\\n💀 GAME OVER.\\nBombs: ${gameState.bombs.join(' & ')}\\n\\n${finalGrid}`);
                    log(`GAME OVER: ${user} died on ${num}`, "err");
                } 
                else {
                    // --- SAFE ---
                    gameState.eaten.push(num);
                    
                    // WIN CONDITION CHANGED TO 4
                    if (gameState.eaten.length === 4) { 
                        gameState.active = false;
                        const finalGrid = renderGrid(true);
                        
                        sendRoomMsg(`🎉 WINNER! ${user} ate 4 chips!\\n🥔 CHAMPION! Generating Prize...\\n\\n${finalGrid}`);
                        
                        setTimeout(() => {
                            sendWinnerImage(user);
                        }, 1000);

                        log(`VICTORY: ${user} won!`, "game");
                    } else {
                        const grid = renderGrid();
                        // UPDATED TEXT: (x/4)
                        sendRoomMsg(`🥔 SAFE! (${gameState.eaten.length}/4)\\n${grid}`);
                    }
                }
            }
        }

        function renderGrid(reveal = false, explodedAt = null) {
            let txt = "";
            const icons = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"];
            for(let i=1; i<=9; i++) {
                if(reveal && i === explodedAt) txt += "💥 ";        
                else if(reveal && gameState.bombs.includes(i)) txt += "💣 "; 
                else if(gameState.eaten.includes(i)) txt += "🥔 ";   
                else txt += icons[i-1] + " ";                        
                if(i % 3 === 0 && i !== 9) txt += "\\n"; 
            }
            return txt;
        }

        function sendRoomMsg(text) {
            const r = document.getElementById('r').value;
            if(!ws || ws.readyState !== 1) return;
            const pkt = {
                handler: "room_message", id: Math.random(), room: r, type: "text", body: text, url: "", length: ""
            };
            ws.send(JSON.stringify(pkt));
            log("SENT_MSG >> " + text.split('\\n')[0] + "...", "out");
        }

        function sendWinnerImage(winnerName) {
            const r = document.getElementById('r').value;
            const avatar = userAvatars[winnerName] || "";
            const myUrl = window.location.origin + "/winner-card?name=" + winnerName + "&avatar=" + encodeURIComponent(avatar);

            log("GENERATING IMG >> " + myUrl, "game");

            const pkt = {
                handler: "room_message", 
                id: Math.random(),
                room: r,
                type: "image",     
                body: "", 
                url: myUrl,        
                length: "0"
            };
            ws.send(JSON.stringify(pkt));
        }
        
        function resetGameLocally() {
            gameState.active = false;
            log("System Reset Locally.", "sys");
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_PAGE)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
