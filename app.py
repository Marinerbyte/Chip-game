import io, random, sqlite3, threading, uuid
from flask import Flask, request, send_file, jsonify, redirect, url_for, render_template_string
from PIL import Image, ImageDraw

app = Flask(__name__)

# =========================
# GLOBALS
# =========================
DB = "bombchip.db"
room_locks = {}
sessions = {}  # sessions[room_id][user_id] -> game state

# =========================
# DATABASE
# =========================
def init_db():
    with sqlite3.connect(DB) as con:
        c = con.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS rooms(
            room_id TEXT PRIMARY KEY,
            room_name TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS players(
            room_id TEXT,
            user_id TEXT,
            username TEXT,
            score INT DEFAULT 0,
            wins INT DEFAULT 0,
            losses INT DEFAULT 0,
            blasts INT DEFAULT 0,
            games INT DEFAULT 0,
            PRIMARY KEY(room_id, user_id)
        )""")
        con.commit()

init_db()

# =========================
# IMAGE RENDER
# =========================
def render_board(eaten, blast=False, win=False):
    size = 600
    img = Image.new("RGBA", (size, size), (15, 18, 30))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle((10,10,590,590), radius=30, outline=(0,200,255), width=4)

    chips = 6
    positions = [
        (200,200),(400,200),
        (200,350),(400,350),
        (200,500),(400,500)
    ]

    for i,(x,y) in enumerate(positions, start=1):
        color = (80,80,80)
        if i in eaten:
            color = (0,180,100)
        d.ellipse((x-50,y-50,x+50,y+50), fill=color)
        d.text((x-5,y-10), str(i), fill="white")
        if i in eaten:
            d.text((x-15,y+20),"✔", fill="white")

    if blast:
        d.text((220,280),"💥 BOOM 💥", fill="red")
    if win:
        d.text((215,280),"🏆 YOU WIN 🏆", fill="gold")

    bio = io.BytesIO()
    img.save(bio,"PNG")
    bio.seek(0)
    return bio

# =========================
# GAME LOGIC
# =========================
def new_game(room_id, user_id, username):
    if room_id not in sessions:
        sessions[room_id] = {}
    sessions[room_id][user_id] = {
        "username": username,
        "bomb": random.randint(1,6),
        "eaten": [],
        "active": True
    }

def update_score(room_id, user_id, win=False, blast=False):
    with sqlite3.connect(DB) as con:
        c = con.cursor()
        c.execute("SELECT * FROM players WHERE room_id=? AND user_id=?", (room_id,user_id))
        if not c.fetchone():
            c.execute("""
            INSERT INTO players(room_id,user_id,username)
            VALUES(?,?,?)
            """,(room_id,user_id,sessions[room_id][user_id]["username"]))

        if win:
            c.execute("""
            UPDATE players SET
            score=score+100,wins=wins+1,games=games+1
            WHERE room_id=? AND user_id=?""",(room_id,user_id))

        if blast:
            c.execute("""
            UPDATE players SET
            score=score-50,losses=losses+1,blasts=blasts+1,games=games+1
            WHERE room_id=? AND user_id=?""",(room_id,user_id))

        con.commit()

# =========================
# UI PAGES
# =========================
@app.route("/", methods=["GET","POST"])
def join():
    if request.method=="POST":
        username = request.form["username"]
        room = request.form["room"]
        room_id = room.lower().replace(" ","_")

        with sqlite3.connect(DB) as con:
            c = con.cursor()
            c.execute("INSERT OR IGNORE INTO rooms VALUES (?,?)",(room_id,room))
            con.commit()

        return redirect(url_for("room_page", room_id=room_id, username=username))

    return render_template_string("""
    <h2>Join Bomb Chip Room</h2>
    <form method="post">
    <input name="username" placeholder="Username" required><br><br>
    <input name="room" placeholder="Room name" required><br><br>
    <button>Enter Room</button>
    </form>
    """)

@app.route("/room/<room_id>")
def room_page(room_id):
    username = request.args.get("username")
    user_id = str(uuid.uuid4())[:8]

    if room_id not in room_locks:
        room_locks[room_id] = threading.Lock()

    new_game(room_id, user_id, username)

    return render_template_string("""
    <h2>Room: {{room}}</h2>
    <p>User: {{user}}</p>
    <img src="/api/board/{{room}}/{{uid}}">
    <form action="/api/play" method="post">
      <input type="hidden" name="room" value="{{room}}">
      <input type="hidden" name="user" value="{{uid}}">
      <input name="choice" placeholder="Chip number">
      <button>Play</button>
    </form>
    <br>
    <a href="/room/{{room}}/leaderboard">View Leaderboard</a>
    """,room=room_id,user=username,uid=user_id)

# =========================
# GAME API
# =========================
@app.route("/api/board/<room>/<user>")
def board(room,user):
    game = sessions[room][user]
    return send_file(render_board(game["eaten"]), mimetype="image/png")

@app.route("/api/play", methods=["POST"])
def play():
    room = request.form["room"]
    user = request.form["user"]
    choice = int(request.form["choice"])

    with room_locks[room]:
        game = sessions[room][user]
        if not game["active"]:
            return "Game over"

        if choice == game["bomb"]:
            game["active"] = False
            update_score(room,user,blast=True)
            return send_file(render_board(game["eaten"], blast=True),"image/png")

        game["eaten"].append(choice)

        if len(game["eaten"]) == 5:
            game["active"] = False
            update_score(room,user,win=True)
            return send_file(render_board(game["eaten"], win=True),"image/png")

        return send_file(render_board(game["eaten"]), "image/png")

# =========================
# LEADERBOARD
# =========================
@app.route("/room/<room_id>/leaderboard")
def ladder(room_id):
    return render_template_string("""
    <h2>Leaderboard – {{room}}</h2>
    <div id="board"></div>
    <script>
    fetch("/api/leaderboard/{{room}}")
    .then(r=>r.json())
    .then(d=>{
      let html="<table border=1><tr><th>User</th><th>Score</th><th>W</th><th>L</th><th>B</th></tr>";
      d.forEach(x=>{
        html+=`<tr><td>${x.username}</td><td>${x.score}</td><td>${x.wins}</td><td>${x.losses}</td><td>${x.blasts}</td></tr>`;
      });
      html+="</table>";
      document.getElementById("board").innerHTML=html;
    })
    </script>
    """,room=room_id)

@app.route("/api/leaderboard/<room_id>")
def api_ladder(room_id):
    with sqlite3.connect(DB) as con:
        c = con.cursor()
        c.execute("""
        SELECT username,score,wins,losses,blasts
        FROM players WHERE room_id=?
        ORDER BY score DESC
        """,(room_id,))
        rows=c.fetchall()
    return jsonify([
        dict(username=r[0],score=r[1],wins=r[2],losses=r[3],blasts=r[4])
        for r in rows
    ])

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
