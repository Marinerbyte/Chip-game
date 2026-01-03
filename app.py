import os
from flask import Flask, render_template_string

app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>TITAN OS - GAME MASTER</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root { --neon: #00f3ff; --term: #00ff41; --danger: #ff003c; --gold: #ffd700; --bg: #050505; }
        body { background: var(--bg); color: var(--neon); font-family: 'Consolas', monospace; margin: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
        
        /* LOGIN / NAV BAR - Same as your file */
        .nav-bar { background: #111; padding: 15px; border-bottom: 2px solid var(--neon); display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.8); }
        input { background: #000; border: 1px solid #333; color: var(--neon); padding: 10px; border-radius: 4px; font-size: 12px; outline: none; border: 1px solid #444; }
        
        button { padding: 10px; font-weight: bold; cursor: pointer; border: 1px solid var(--neon); background: transparent; color: var(--neon); font-size: 11px; text-transform: uppercase; }
        button:hover { background: var(--neon); color: #000; }
        .atk-btn { background: var(--term) !important; color: #000 !important; border: none !important; box-shadow: 0 0 10px var(--term); }
        .danger-btn { border-color: var(--danger); color: var(--danger); }

        /* TERMINAL AREA - Same as your file */
        .terminal-zone { flex: 1; display: flex; flex-direction: column; background: #000; overflow: hidden; }
        .term-head { background: #1a1a1a; padding: 8px 15px; font-size: 11px; color: #666; display: flex; justify-content: space-between; border-bottom: 1px solid #222; }
        .terminal-body { flex: 1; overflow-y: scroll; padding: 15px; font-size: 12px; scroll-behavior: smooth; }
        
        .line { margin-bottom: 10px; border-left: 3px solid #222; padding-left: 12px; word-break: break-all; }
        .in { color: var(--term); border-color: var(--term); } 
        .out { color: var(--neon); border-color: var(--neon); }
        .err { color: var(--danger); border-color: var(--danger); }
        .game { color: var(--gold); border-color: var(--gold); background: rgba(255, 215, 0, 0.05); }
        
        /* JSON BOX */
        .json-dump { background: #080808; padding: 10px; margin-top: 5px; border: 1px solid #1a1a1a; display: block; color: #00ccff; font-size: 11px; border-radius: 4px; white-space: pre-wrap; font-family: monospace; }
        
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 10px; }
    </style>
</head>
<body>
    <div class="nav-bar">
        <!-- LOGIN INPUTS -->
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
            <span>TITAN_GAME_ENGINE v1.0 [MINEFIELD_MODE]</span>
            <span id="stat">OFFLINE</span>
        </div>
        <div id="terminal" class="terminal-body">
            <div class="line">System ready. Enter credentials and click CONNECT BOT.</div>
            <div class="line game">Game Logic Loaded: 1-9 Grid, 2 Hidden Bombs, 7 Safe Spots.</div>
        </div>
    </div>

    <script>
        let ws; let pinger; const term = document.getElementById('terminal');

        // --- GAME VARIABLES ---
        let gameState = {
            active: false,
            player: null,
            bombs: [],     // 2 Hidden Bombs
            eaten: []      // Safe spots found
        };

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
                log("SENT >> LOGIN_REQUEST", "out", loginData);
                
                pinger = setInterval(() => { if(ws.readyState === 1) ws.send(JSON.stringify({handler:"ping"})); }, 25000);
            };

            ws.onmessage = (e) => {
                const data = JSON.parse(e.data);
                
                // Show logs just like before
                log("RECV << " + (data.handler || "EVENT"), "in", data);
                
                if(data.handler === "login_event" && data.type === "success") {
                    ws.send(JSON.stringify({handler: "room_join", id: Math.random(), name: r}));
                }

                // --- GAME LOGIC TRIGGER ---
                if(data.handler === "room_event" && data.type === "text") {
                    handleGameCommand(data.from, data.body);
                }
            };

            ws.onclose = () => {
                clearInterval(pinger);
                document.getElementById('stat').innerText = "OFFLINE";
                document.getElementById('stat').style.color = "var(--danger)";
                log("WebSocket Disconnected. Attempting Auto-Relogin...", "err");
                setTimeout(connectWS, 3000); // Auto-Reconnect
            };
        }

        // --- MAIN GAME FUNCTION ---
        function handleGameCommand(user, msg) {
            msg = msg.trim().toLowerCase();
            const myUser = document.getElementById('u').value.toLowerCase();
            if(user.toLowerCase() === myUser) return; // Don't reply to self

            // 1. START COMMAND
            if (msg === "!start") {
                if (gameState.active) {
                    return sendRoomMsg(`⚠ Game running! ${gameState.player} is playing.`);
                }
                
                // Setup New Game
                gameState.active = true;
                gameState.player = user;
                gameState.eaten = [];
                gameState.bombs = [];
                
                // Generate 2 Unique Bombs
                while(gameState.bombs.length < 2) {
                    let r = Math.floor(Math.random() * 9) + 1;
                    if(!gameState.bombs.includes(r)) gameState.bombs.push(r);
                }

                log(`GAME STARTED by ${user}. Hidden Bombs: [${gameState.bombs.join(', ')}]`, "game");
                
                const grid = renderGrid();
                sendRoomMsg(`🎮 START! Player: ${user}\\nAvoid 2 Bombs! Eat 7 Chips.\\nType !eat <number>\\n\\n${grid}`);
            }

            // 2. EAT COMMAND
            else if (msg.startsWith("!eat ")) {
                if (!gameState.active) return;
                if (user !== gameState.player) return; // Only current player

                const num = parseInt(msg.split(" ")[1]);

                // Validation
                if (isNaN(num) || num < 1 || num > 9) return sendRoomMsg(`⚠ Choose 1-9.`);
                if (gameState.eaten.includes(num)) return sendRoomMsg(`⚠ Already eaten!`);

                // Check Bomb
                if (gameState.bombs.includes(num)) {
                    gameState.active = false;
                    const finalGrid = renderGrid(true, num); // Reveal all
                    sendRoomMsg(`💥 BOOM! You ate a BOMB at #${num}!\\n💀 GAME OVER.\\nBombs were at: ${gameState.bombs.join(' & ')}\\n\\n${finalGrid}`);
                    log(`GAME OVER: ${user} died on ${num}`, "err");
                } 
                else {
                    // Safe Spot
                    gameState.eaten.push(num);
                    if (gameState.eaten.length === 7) {
                        gameState.active = false;
                        const finalGrid = renderGrid(true);
                        sendRoomMsg(`🎉 WINNER! ${user} ate all 7 chips!\\n🥔 CHAMPION!\\n\\n${finalGrid}`);
                        log(`VICTORY: ${user} won!`, "game");
                    } else {
                        const grid = renderGrid();
                        sendRoomMsg(`🥔 CRUNCH! #${num} is safe.\\n${grid}`);
                    }
                }
            }
        }

        // --- GRID RENDERER ---
        function renderGrid(reveal = false, explodedAt = null) {
            let txt = "";
            const icons = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"];
            
            for(let i=1; i<=9; i++) {
                if(reveal && i === explodedAt) txt += "💥 ";        // Exploded Bomb
                else if(reveal && gameState.bombs.includes(i)) txt += "💣 "; // Hidden Bomb Revealed
                else if(gameState.eaten.includes(i)) txt += "🥔 ";   // Eaten Safe Spot
                else txt += icons[i-1] + " ";                        // Normal Number

                if(i % 3 === 0 && i !== 9) txt += "\\n"; // New Line
            }
            return txt;
        }

        function sendRoomMsg(text) {
            const r = document.getElementById('r').value;
            if(!ws || ws.readyState !== 1) return;
            
            const pkt = {
                handler: "chat_message", 
                to: r,          // Target (Room)
                room: r,        // Room context
                type: "text", 
                body: text, 
                is_private: false 
            };
            
            ws.send(JSON.stringify(pkt));
            log("SENT_MSG >> " + text.split('\\n')[0] + "...", "out");
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