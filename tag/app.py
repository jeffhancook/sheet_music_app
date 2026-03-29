import os
import string
import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())

socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

# { code: { players: { sid: {avatar, ready, name} }, host: sid } }
lobbies = {}
sid_to_lobby = {}

MAX_PLAYERS = 4


def gen_code():
    chars = string.ascii_uppercase + string.digits
    for _ in range(100):
        code = "".join(random.choices(chars, k=4))
        if code not in lobbies:
            return code
    return None


def lobby_state(code):
    lobby = lobbies.get(code)
    if not lobby:
        return None
    players = []
    for sid, info in lobby["players"].items():
        players.append({
            "sid": sid,
            "avatar": info["avatar"],
            "name": info["name"],
            "ready": info["ready"],
            "isHost": sid == lobby["host"],
        })
    taken = [info["avatar"] for info in lobby["players"].values() if info["avatar"]]
    return {
        "code": code,
        "players": players,
        "maxPlayers": MAX_PLAYERS,
        "takenAvatars": taken,
    }


def check_auto_start(code):
    lobby = lobbies.get(code)
    if not lobby:
        return
    players = lobby["players"]
    total = len(players)
    if total < 2:
        return
    # All must have an avatar to start
    if any(not p["avatar"] for p in players.values()):
        return
    ready_count = sum(1 for p in players.values() if p["ready"])
    if ready_count == total:
        socketio.emit("game_starting", {"countdown": 3}, room=code)


@app.route("/")
def index():
    return render_template("tag.html")


@socketio.on("create_lobby")
def on_create_lobby(data):
    name = data.get("name", "Player")
    sid = request.sid

    if sid in sid_to_lobby:
        on_leave_lobby()

    code = gen_code()
    if not code:
        emit("error", {"msg": "Could not create lobby"})
        return

    lobbies[code] = {
        "players": {sid: {"avatar": None, "ready": False, "name": name}},
        "host": sid,
    }
    sid_to_lobby[sid] = code
    join_room(code)
    emit("lobby_joined", lobby_state(code))


@socketio.on("join_lobby")
def on_join_lobby(data):
    code = data.get("code", "").upper().strip()
    name = data.get("name", "Player")
    sid = request.sid

    if code not in lobbies:
        emit("error", {"msg": "Lobby not found"})
        return

    lobby = lobbies[code]
    if len(lobby["players"]) >= MAX_PLAYERS:
        emit("error", {"msg": "Lobby is full"})
        return

    if sid in sid_to_lobby:
        on_leave_lobby()

    lobby["players"][sid] = {"avatar": None, "ready": False, "name": name}
    sid_to_lobby[sid] = code
    join_room(code)
    emit("lobby_joined", lobby_state(code))
    socketio.emit("lobby_update", lobby_state(code), room=code)


@socketio.on("pick_avatar")
def on_pick_avatar(data):
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    if sid not in lobby["players"]:
        return

    avatar = data.get("avatar")
    if avatar not in ("ninja", "chicken", "stickman"):
        emit("error", {"msg": "Invalid avatar"})
        return

    # Check if already taken by someone else
    for other_sid, info in lobby["players"].items():
        if other_sid != sid and info["avatar"] == avatar:
            emit("error", {"msg": "Avatar already taken"})
            return

    lobby["players"][sid]["avatar"] = avatar
    socketio.emit("lobby_update", lobby_state(code), room=code)


@socketio.on("set_ready")
def on_set_ready(data):
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    if sid not in lobby["players"]:
        return
    # Must have avatar to ready up
    if not lobby["players"][sid]["avatar"]:
        emit("error", {"msg": "Pick an avatar first"})
        return
    lobby["players"][sid]["ready"] = bool(data.get("ready", False))
    socketio.emit("lobby_update", lobby_state(code), room=code)
    check_auto_start(code)


@socketio.on("player_move")
def on_player_move(data):
    """Relay player position to everyone else in the lobby."""
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    if sid not in lobby["players"]:
        return
    # Broadcast to room (skip_sid sends to everyone except sender)
    emit("remote_move", {
        "sid": sid,
        "x": data.get("x", 0),
        "y": data.get("y", 0),
        "facing": data.get("facing", 1),
        "avatar": lobby["players"][sid]["avatar"],
        "name": lobby["players"][sid]["name"],
    }, room=code, include_self=False)


@socketio.on("leave_lobby")
def on_leave_lobby():
    sid = request.sid
    code = sid_to_lobby.pop(sid, None)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    lobby["players"].pop(sid, None)
    leave_room(code)

    if not lobby["players"]:
        del lobbies[code]
    else:
        if lobby["host"] == sid:
            lobby["host"] = next(iter(lobby["players"]))
        socketio.emit("lobby_update", lobby_state(code), room=code)


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    code = sid_to_lobby.pop(sid, None)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    lobby["players"].pop(sid, None)

    if not lobby["players"]:
        del lobbies[code]
    else:
        if lobby["host"] == sid:
            lobby["host"] = next(iter(lobby["players"]))
        socketio.emit("lobby_update", lobby_state(code), room=code)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5007, debug=True)
