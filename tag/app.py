import os
import time
import string
import random
import eventlet
from collections import Counter
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())

socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

lobbies = {}
sid_to_lobby = {}

MAX_PLAYERS = 4
GAME_DURATION = 180
AVAILABLE_MAPS = ["plains", "factory", "sky_temple", "dimensional_rift"]


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
            "mapVote": info.get("mapVote"),
        })
    taken = [info["avatar"] for info in lobby["players"].values() if info["avatar"]]
    # Count votes
    votes = {}
    for m in AVAILABLE_MAPS:
        votes[m] = sum(1 for p in lobby["players"].values() if p.get("mapVote") == m)
    return {
        "code": code,
        "players": players,
        "maxPlayers": MAX_PLAYERS,
        "takenAvatars": taken,
        "mapVotes": votes,
        "availableMaps": AVAILABLE_MAPS,
    }


def get_winning_map(code):
    """Pick a map using weighted random — each vote increases that map's probability."""
    lobby = lobbies.get(code)
    if not lobby:
        return "plains"
    votes = Counter(p.get("mapVote") for p in lobby["players"].values() if p.get("mapVote"))
    if not votes:
        return "plains"
    # Weighted random: each vote = 1 ticket
    maps = list(votes.keys())
    weights = [votes[m] for m in maps]
    return random.choices(maps, weights=weights, k=1)[0]


def game_timer_loop(code):
    lobby = lobbies.get(code)
    if not lobby:
        return
    start = lobby["game_start_time"]
    while code in lobbies and lobbies[code].get("state") == "playing":
        elapsed = time.time() - start
        remaining = max(0, GAME_DURATION - int(elapsed))
        socketio.emit("time_sync", {"remaining": remaining}, room=code)
        if remaining <= 0:
            tagger = lobby.get("tagger")
            tagger_name = ""
            if tagger and tagger in lobby["players"]:
                tagger_name = lobby["players"][tagger]["name"]
            socketio.emit("game_end", {"tagger": tagger, "taggerName": tagger_name}, room=code)
            if code in lobbies:
                lobby = lobbies[code]
                lobby["state"] = "lobby"
                lobby["game_start_time"] = None
                lobby["timer_greenlet"] = None
                for p in lobby["players"].values():
                    p["ready"] = False
                eventlet.sleep(3)
                if code in lobbies:
                    socketio.emit("return_to_lobby", lobby_state(code), room=code)
            return
        eventlet.sleep(1)


def check_auto_start(code):
    lobby = lobbies.get(code)
    if not lobby or lobby.get("state") == "playing":
        return
    players = lobby["players"]
    total = len(players)
    if total < 1:  # TODO: change back to 2 for release
        return
    if any(not p["avatar"] for p in players.values()):
        return
    ready_count = sum(1 for p in players.values() if p["ready"])
    if ready_count == total:
        winning_map = get_winning_map(code)
        lobby["selected_map"] = winning_map
        socketio.emit("game_starting", {"countdown": 3, "map": winning_map}, room=code)
        eventlet.spawn_after(3, start_game, code)


def start_game(code):
    lobby = lobbies.get(code)
    if not lobby:
        return
    lobby["state"] = "playing"
    lobby["game_start_time"] = time.time()
    sids = list(lobby["players"].keys())
    tagger = random.choice(sids) if sids else None
    lobby["tagger"] = tagger
    # Seed for deterministic powerup/spawn placement across all clients
    lobby["seed"] = random.randint(0, 999999)
    # Track which powerups are active (by index)
    lobby["powerups_taken"] = set()
    socketio.emit("game_started", {
        "duration": GAME_DURATION,
        "map": lobby.get("selected_map", "plains"),
        "tagger": tagger,
        "seed": lobby["seed"],
        "playerOrder": sids,  # for assigning spawn points
    }, room=code)
    lobby["timer_greenlet"] = eventlet.spawn(game_timer_loop, code)


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
        "players": {sid: {"avatar": None, "ready": False, "name": name, "mapVote": "plains"}},
        "host": sid,
        "state": "lobby",
        "game_start_time": None,
        "timer_greenlet": None,
        "selected_map": "plains",
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
    if lobby.get("state") == "playing":
        emit("error", {"msg": "Game already in progress"})
        return
    if len(lobby["players"]) >= MAX_PLAYERS:
        emit("error", {"msg": "Lobby is full"})
        return
    if sid in sid_to_lobby:
        on_leave_lobby()
    lobby["players"][sid] = {"avatar": None, "ready": False, "name": name, "mapVote": "plains"}
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
    if avatar not in ("ninja", "chicken", "stickman", "flappy", "pacman"):
        emit("error", {"msg": "Invalid avatar"})
        return
    for other_sid, info in lobby["players"].items():
        if other_sid != sid and info["avatar"] == avatar:
            emit("error", {"msg": "Avatar already taken"})
            return
    lobby["players"][sid]["avatar"] = avatar
    socketio.emit("lobby_update", lobby_state(code), room=code)


@socketio.on("vote_map")
def on_vote_map(data):
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    if sid not in lobby["players"]:
        return
    map_name = data.get("map")
    if map_name not in AVAILABLE_MAPS:
        return
    lobby["players"][sid]["mapVote"] = map_name
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
    if not lobby["players"][sid]["avatar"]:
        emit("error", {"msg": "Pick an avatar first"})
        return
    lobby["players"][sid]["ready"] = bool(data.get("ready", False))
    socketio.emit("lobby_update", lobby_state(code), room=code)
    check_auto_start(code)


@socketio.on("player_move")
def on_player_move(data):
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    if sid not in lobby["players"]:
        return
    # Track mega state for tag immunity
    lobby["players"][sid]["mega"] = data.get("mega", False)
    emit("remote_move", {
        "sid": sid,
        "x": data.get("x", 0),
        "y": data.get("y", 0),
        "vx": data.get("vx", 0),
        "vy": data.get("vy", 0),
        "facing": data.get("facing", 1),
        "invis": data.get("invis", False),
        "mega": data.get("mega", False),
        "stunned": data.get("stunned", False),
        "avatar": lobby["players"][sid]["avatar"],
        "name": lobby["players"][sid]["name"],
    }, room=code, include_self=False)


@socketio.on("pickup_powerup")
def on_pickup_powerup(data):
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    idx = data.get("idx")
    ptype = data.get("type")
    if idx in lobby.get("powerups_taken", set()):
        return
    lobby["powerups_taken"].add(idx)
    socketio.emit("powerup_picked", {
        "idx": idx, "type": ptype, "sid": sid,
    }, room=code)
    if ptype == "swap":
        sids = list(lobby["players"].keys())
        shuffled = sids[:]
        random.shuffle(shuffled)
        socketio.emit("swap_positions", {"order": shuffled}, room=code)


@socketio.on("stun_player")
def on_stun_player(data):
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    target = data.get("target")
    lobby = lobbies[code]
    if target not in lobby["players"] or target == sid:
        return
    socketio.emit("player_stunned", {"sid": target, "duration": 5000}, room=code)


@socketio.on("tag_player")
def on_tag_player(data):
    """Tagger tags another player — transfer the tag."""
    sid = request.sid
    code = sid_to_lobby.get(sid)
    if not code or code not in lobbies:
        return
    lobby = lobbies[code]
    if lobby.get("tagger") != sid:
        return
    target = data.get("target")
    if target not in lobby["players"] or target == sid:
        return
    # Mega mode = immune to being tagged
    if lobby["players"][target].get("mega"):
        return
    lobby["tagger"] = target
    socketio.emit("tagger_update", {"tagger": target}, room=code)


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
        if lobby.get("timer_greenlet"):
            lobby["timer_greenlet"].kill()
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
        if lobby.get("timer_greenlet"):
            lobby["timer_greenlet"].kill()
        del lobbies[code]
    else:
        if lobby["host"] == sid:
            lobby["host"] = next(iter(lobby["players"]))
        socketio.emit("lobby_update", lobby_state(code), room=code)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5007, debug=True)
