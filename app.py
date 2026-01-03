import io
import random
import sqlite3
import threading
from flask import Flask, request, jsonify, send_file, render_template_string
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# -------------------------
# GLOBALS
# -------------------------
sessions = {}
session_lock = threading.Lock()
db_lock = threading.Lock()

DB_NAME = "game.db"

# -------------------------
# DATABASE INIT
# -------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            score INTEGER,
            wins INTEGER,
            losses INTEGER,
            blasts INTEGER
        )
        """)
        con.commit()

init_db()

# -------------------------
# IMAGE RENDERING
# -------------------------
def render_board(total, eaten, blast=None, win=False):
    size = 600
    img = Image.new("RGBA", (size, size), (20, 20, 30, 255))
    draw = ImageDraw.Draw(img)

    # Border
    draw.rounded_rectangle(
        [(10,10),(590,590)],
        radius=30,
        outline=(80,200,255),
        width=4
    )

    cols = 3
    radius = 60
    gap_x = 180
    gap_y = 180
    start_x = 150
    start_y = 150

    for i in range(1, total+1):
        col = (i-1) % cols
        row = (i-1) // cols
        cx = start_x + col * gap_x
        cy = start_y + row * gap_y

        color = (90,90,90)
        if i in eaten:
            color = (0,180,90)

        draw.ellipse(
            [(cx-radius, cy-radius), (cx+radius, cy+radius)],
            fill=color
        )

        draw.text((cx-10, cy-15), str(i), fill="white")

        if i in eaten:
            draw.text((cx-15, cy+20), "✔", fill="white")

    if blast:
        draw.text((200, 280), "💥 BOOM 💥", fill="red")

    if win:
        draw.text((210, 280), "🏆 YOU WIN 🏆", fill="gold")

    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

# -------------------------
# GAME LOGIC
# -------------------------
def start_game(user_id, username):
    with session_lock:
        sessions[user_id] = {
            "username": username,
            "total": 6,
            "bomb": random.randint(1,6),
            "eaten": [],
            "score": 0,
            "active": True
        }

def end_game(user_id, win=False, blast=False):
    with session_lock:
        game = sessions.get(user_id)
        if not game:
            return

        with db_lock:
            con = sqlite3.connect(DB_NAME)
            cur = con.cursor()

            cur.execute("SELECT * FROM leaderboard WHERE user_id=?", (user_id,))
            row = cur.fetchone()

            if not row:
                cur.execute("""
                INSERT INTO leaderboard VALUES (?,?,?,?,?,?)
                """, (
                    user_id,
                    game["username"],
                    0,0,0,0
                ))

            if win:
                cur.execute("""
                UPDATE leaderboard
                SET score = score + 100,
                    wins = wins + 1
                WHERE user_id=?
                """, (user_id,))

            if blast:
                cur.execute("""
                UPDATE leaderboard
                SET score = score - 50,
                    losses = losses + 1,
                    blasts = blasts + 1
                WHERE user_id=?
                """, (user_id,))

            con.commit()
            con.close()

        game["active"] = False

# -------------------------
# ROUTES
# -------------------------
@app.route("/")
def home():
    return render_template_string("""
    <h2>Bomb Chip Game</h2>
    <form action="/start" method="post">
      User ID: <input name="user_id"><br>
      Username: <input name="username"><br>
      <button>Start Game</button>
    </form>
    <a href="/leaderboard">Leaderboard</a>
    """)

@app.route("/start", methods=["POST"])
def start():
    user_id = request.form["user_id"]
    username = request.form["username"]
    start_game(user_id, username)
    return "Game started. Go to /play?user_id=" + user_id

@app.route("/play")
def play():
    user_id = request.args.get("user_id")
    game = sessions.get(user_id)
    if not game or not game["active"]:
        return "No active game"

    img = render_board(game["total"], game["eaten"])
    return send_file(img, mimetype="image/png")

@app.route("/move", methods=["POST"])
def move():
    user_id = request.form["user_id"]
    choice = int(request.form["choice"])

    with session_lock:
        game = sessions.get(user_id)
        if not game or not game["active"]:
            return "Game over"

        if choice in game["eaten"]:
            return "Already eaten"

        if choice == game["bomb"]:
            img = render_board(game["total"], game["eaten"], blast=True)
            end_game(user_id, blast=True)
            return send_file(img, mimetype="image/png")

        game["eaten"].append(choice)
        game["score"] += 10

        if len(game["eaten"]) == game["total"] - 1:
            img = render_board(game["total"], game["eaten"], win=True)
            end_game(user_id, win=True)
            return send_file(img, mimetype="image/png")

        img = render_board(game["total"], game["eaten"])
        return send_file(img, mimetype="image/png")

@app.route("/leaderboard")
def leaderboard():
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute("""
        SELECT username, score, wins, losses, blasts
        FROM leaderboard
        ORDER BY score DESC
        """)
        rows = cur.fetchall()

    html = "<h2>Leaderboard</h2><table border=1>"
    html += "<tr><th>User</th><th>Score</th><th>Wins</th><th>Loss</th><th>Blasts</th></tr>"
    for r in rows:
        html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>"
    html += "</table>"
    return html

# -------------------------
# RUN
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
