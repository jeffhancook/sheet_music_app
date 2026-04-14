from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.middleware.proxy_fix import ProxyFix
import os, random, string, time, eventlet

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

MAX_PLAYERS = 8
MIN_PLAYERS = 3
CLUE_TIME = 25       # per-player clue turn
DECISION_TIME = 20   # vote-now vs continue
VOTE_TIME = 30       # final vote for imposter

lobbies = {}
sid_lobby = {}

WORD_CATEGORIES = {
    "Animal": ["Dog", "Cat", "Owl", "Hawk", "Dolphin", "Whale"],
    "Food": ["Pizza", "Burger", "Bread", "Toast", "Cake", "Pie", "Ice Cream",
             "Waffle", "Pancake", "Honey", "Syrup", "Butter", "Margarine",
             "Breakfast", "Brunch", "Apple", "Orange"],
    "Drink": ["Coffee", "Tea"],
    "Instrument": ["Guitar", "Piano", "Violin", "Cello", "Drum", "Tambourine"],
    "Sport or Game": ["Soccer", "Basketball", "Chess", "Checkers"],
    "Nature": ["Beach", "Pool", "Rain", "Snow", "Sun", "Moon", "River", "Lake",
               "Mountain", "Hill", "Ocean", "Sea", "Forest", "Jungle", "Thunder",
               "Lightning", "Volcano", "Geyser", "Winter", "Autumn", "Mars", "Venus"],
    "Clothing": ["Shirt", "Jacket", "Shoes", "Sandals", "Scarf", "Tie",
                 "Gloves", "Mittens", "Ring", "Bracelet"],
    "Furniture": ["Chair", "Couch", "Sofa", "Recliner", "Pillow", "Blanket",
                  "Curtain", "Blinds"],
    "Vehicle": ["Car", "Bus", "Airplane", "Helicopter", "Bicycle", "Scooter",
                "Elevator", "Stairs"],
    "Building": ["Hospital", "Clinic", "Library", "Bookstore", "Castle",
                 "Palace", "Cabin", "Tent"],
    "Writing Tool": ["Pencil", "Pen", "Crayon", "Marker"],
    "Reading Material": ["Book", "Magazine", "Newspaper", "Blog"],
    "Everyday Object": ["Candle", "Torch", "Sword", "Knife", "Camera",
                        "Telescope", "Laptop", "Tablet"],
    "Entertainment": ["Movie", "TV Show"],
    "Art or Performance": ["Painting", "Drawing", "Dance", "Ballet", "Singing", "Humming"],
    "Activity": ["Running", "Walking"],
    "Quiet Sound": ["Whisper", "Murmur"],
    "Mythical Creature": ["Vampire", "Werewolf"],
    "Profession": ["Dentist", "Doctor"],
}


def _gen_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(chars, k=4))
        if code not in lobbies:
            return code


def _lobby_view(code):
    lobby = lobbies[code]
    players = []
    for sid, p in lobby["players"].items():
        players.append({
            "sid": sid,
            "name": p["name"],
            "color_idx": p.get("color_idx", 0),
            "clue": p.get("clue"),
        })
    return {
        "code": code,
        "players": players,
        "host": lobby["host"],
        "state": lobby["state"],
        "round": lobby.get("round", 0),
        "phase": lobby.get("phase"),
        "current_turn": lobby.get("current_turn", 0),
        "turn_order": lobby.get("turn_order", []),
        "clues": lobby.get("clues", []),
    }


def _kill_timer(lobby):
    g = lobby.get("timer_greenlet")
    if g and g is not eventlet.getcurrent():
        g.kill()
    lobby["timer_greenlet"] = None


def _start_clue_phase(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    lobby["phase"] = "clue"
    lobby["round"] = lobby.get("round", 0) + 1
    lobby["clues"] = []

    lobby["turn_order"] = list(lobby["players"].keys())
    random.shuffle(lobby["turn_order"])
    lobby["current_turn"] = 0

    for p in lobby["players"].values():
        p["clue"] = None
        p["decision"] = None
        p["vote"] = None

    socketio.emit("phase_change", {
        "phase": "clue",
        "round": lobby["round"],
        "turn_order": lobby["turn_order"],
        "current_turn": 0,
    }, room=code)

    _start_clue_turn(code)


def _start_clue_turn(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    if lobby.get("phase") != "clue":
        return
    if lobby["current_turn"] >= len(lobby["turn_order"]):
        _start_decision_phase(code)
        return

    current_sid = lobby["turn_order"][lobby["current_turn"]]
    if current_sid not in lobby["players"]:
        lobby["current_turn"] += 1
        _start_clue_turn(code)
        return

    socketio.emit("clue_turn", {
        "player": current_sid,
        "time": CLUE_TIME,
        "index": lobby["current_turn"],
    }, room=code)

    _kill_timer(lobby)

    def timer():
        for remaining in range(CLUE_TIME, 0, -1):
            eventlet.sleep(1)
            if code not in lobbies or lobbies[code].get("phase") != "clue":
                return
            socketio.emit("timer_tick", {
                "remaining": remaining - 1, "phase": "clue",
            }, room=code)
        if code not in lobbies or lobbies[code].get("phase") != "clue":
            return
        p = lobby["players"].get(current_sid)
        if p and p.get("clue") is None:
            p["clue"] = "(skipped)"
            lobby["clues"].append({
                "sid": current_sid,
                "name": p["name"],
                "clue": "(skipped)",
            })
            socketio.emit("clue_submitted", {
                "sid": current_sid,
                "name": p["name"],
                "clue": "(skipped)",
            }, room=code)
            lobby["current_turn"] += 1
            eventlet.sleep(1)
            _start_clue_turn(code)

    lobby["timer_greenlet"] = eventlet.spawn(timer)


def _start_decision_phase(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    lobby["phase"] = "decision"
    _kill_timer(lobby)

    for p in lobby["players"].values():
        p["decision"] = None

    socketio.emit("phase_change", {
        "phase": "decision",
        "time": DECISION_TIME,
        "clues": lobby["clues"],
    }, room=code)

    def timer():
        for remaining in range(DECISION_TIME, 0, -1):
            eventlet.sleep(1)
            if code not in lobbies or lobbies[code].get("phase") != "decision":
                return
            socketio.emit("timer_tick", {
                "remaining": remaining - 1, "phase": "decision",
            }, room=code)
        if code in lobbies and lobbies[code].get("phase") == "decision":
            _process_decisions(code)

    lobby["timer_greenlet"] = eventlet.spawn(timer)


def _check_all_decided(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    if all(p.get("decision") is not None for p in lobby["players"].values()):
        _kill_timer(lobby)
        _process_decisions(code)


def _process_decisions(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    _kill_timer(lobby)

    vote_now = 0
    continue_count = 0
    details = {}
    for sid, p in lobby["players"].items():
        d = p.get("decision")
        details[sid] = {
            "name": p["name"],
            "decision": d or "abstain",
        }
        if d == "vote":
            vote_now += 1
        elif d == "continue":
            continue_count += 1

    go_to_vote = vote_now > continue_count

    socketio.emit("decision_result", {
        "vote_count": vote_now,
        "continue_count": continue_count,
        "details": details,
        "go_to_vote": go_to_vote,
    }, room=code)

    def after():
        eventlet.sleep(4.5)
        if code not in lobbies:
            return
        if go_to_vote:
            _start_vote_phase(code)
        else:
            _start_clue_phase(code)

    lobby["timer_greenlet"] = eventlet.spawn(after)


def _start_vote_phase(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    lobby["phase"] = "vote"
    _kill_timer(lobby)

    for p in lobby["players"].values():
        p["vote"] = None

    socketio.emit("phase_change", {
        "phase": "vote",
        "time": VOTE_TIME,
    }, room=code)

    def timer():
        for remaining in range(VOTE_TIME, 0, -1):
            eventlet.sleep(1)
            if code not in lobbies or lobbies[code].get("phase") != "vote":
                return
            socketio.emit("timer_tick", {
                "remaining": remaining - 1, "phase": "vote",
            }, room=code)
        if code in lobbies and lobbies[code].get("phase") == "vote":
            _process_votes(code)

    lobby["timer_greenlet"] = eventlet.spawn(timer)


def _check_all_voted(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    if all(p.get("vote") is not None for p in lobby["players"].values()):
        _kill_timer(lobby)
        _process_votes(code)


def _process_votes(code):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    _kill_timer(lobby)

    vote_counts = {}
    votes_detail = {}
    for sid, p in lobby["players"].items():
        target = p.get("vote")
        if target:
            vote_counts[target] = vote_counts.get(target, 0) + 1
            votes_detail[sid] = target

    eliminated = None
    is_tie = False
    if vote_counts:
        max_votes = max(vote_counts.values())
        most_voted = [s for s, c in vote_counts.items() if c == max_votes]
        if len(most_voted) == 1:
            eliminated = most_voted[0]
        else:
            is_tie = True
    else:
        is_tie = True

    result = {
        "votes": {
            sid: {
                "name": lobby["players"][sid]["name"],
                "target": t,
                "target_name": lobby["players"].get(t, {}).get("name", "?"),
            }
            for sid, t in votes_detail.items()
        },
        "vote_counts": {
            sid: {
                "name": lobby["players"][sid]["name"],
                "count": c,
            }
            for sid, c in vote_counts.items()
        },
        "eliminated": eliminated,
        "eliminated_name": lobby["players"][eliminated]["name"] if eliminated else None,
        "tie": is_tie,
    }
    socketio.emit("vote_result", result, room=code)

    def after():
        eventlet.sleep(6)
        if code not in lobbies:
            return
        if eliminated:
            was_imposter = (eliminated == lobby["imposter"])
            winner = "civilians" if was_imposter else "imposter"
            _end_game(code, winner, eliminated)
        else:
            # Tie → another clue round
            _start_clue_phase(code)

    lobby["timer_greenlet"] = eventlet.spawn(after)


def _end_game(code, winner, eliminated=None):
    if code not in lobbies:
        return
    lobby = lobbies[code]
    lobby["state"] = "end"
    lobby["phase"] = None
    _kill_timer(lobby)

    imposter_sid = lobby.get("imposter")
    socketio.emit("game_over", {
        "winner": winner,
        "imposter": imposter_sid,
        "imposter_name": lobby["players"].get(imposter_sid, {}).get("name", "?"),
        "eliminated": eliminated,
        "eliminated_name": lobby["players"].get(eliminated, {}).get("name") if eliminated else None,
        "secret_word": lobby.get("secret_word", ""),
        "category": lobby.get("category", ""),
        "rounds_played": lobby.get("round", 0),
        "players": [
            {
                "sid": sid,
                "name": p["name"],
                "role": p.get("role", "civilian"),
                "word": p.get("word") or "",
                "color_idx": p.get("color_idx", 0),
            }
            for sid, p in lobby["players"].items()
        ],
    }, room=code)


# ── Socket.IO events ──


@socketio.on("create_lobby")
def on_create(data):
    name = data.get("name", "").strip()[:20]
    if not name:
        emit("error", {"msg": "Name is required"})
        return

    code = _gen_code()
    sid = request.sid

    lobbies[code] = {
        "players": {sid: {"name": name, "color_idx": 0}},
        "host": sid,
        "state": "lobby",
        "timer_greenlet": None,
    }
    sid_lobby[sid] = code
    join_room(code)
    emit("lobby_joined", {"code": code, "you": sid, **_lobby_view(code)})


@socketio.on("join_lobby")
def on_join(data):
    code = data.get("code", "").strip().upper()
    name = data.get("name", "").strip()[:20]

    if not name:
        emit("error", {"msg": "Name is required"})
        return
    if code not in lobbies:
        emit("error", {"msg": "Lobby not found"})
        return

    lobby = lobbies[code]
    if lobby["state"] != "lobby":
        emit("error", {"msg": "Game already in progress"})
        return
    if len(lobby["players"]) >= MAX_PLAYERS:
        emit("error", {"msg": "Lobby is full"})
        return

    sid = request.sid
    used_colors = {p["color_idx"] for p in lobby["players"].values()}
    color_idx = next((i for i in range(8) if i not in used_colors), 0)
    lobby["players"][sid] = {"name": name, "color_idx": color_idx}
    sid_lobby[sid] = code
    join_room(code)

    emit("lobby_joined", {"code": code, "you": sid, **_lobby_view(code)})
    emit("lobby_update", _lobby_view(code), room=code, include_self=False)


@socketio.on("start_game")
def on_start():
    sid = request.sid
    code = sid_lobby.get(sid)
    if not code or code not in lobbies:
        return

    lobby = lobbies[code]
    if lobby["host"] != sid:
        emit("error", {"msg": "Only the host can start"})
        return
    if len(lobby["players"]) < MIN_PLAYERS:
        emit("error", {"msg": f"Need at least {MIN_PLAYERS} players"})
        return

    category = random.choice(list(WORD_CATEGORIES.keys()))
    word = random.choice(WORD_CATEGORIES[category])
    lobby["secret_word"] = word
    lobby["category"] = category

    imposter_sid = random.choice(list(lobby["players"].keys()))
    lobby["imposter"] = imposter_sid
    lobby["state"] = "playing"
    lobby["round"] = 0

    for p_sid, p in lobby["players"].items():
        p["clue"] = None
        p["decision"] = None
        p["vote"] = None
        if p_sid == imposter_sid:
            p["role"] = "imposter"
            p["word"] = None
        else:
            p["role"] = "civilian"
            p["word"] = word

    for p_sid, p in lobby["players"].items():
        socketio.emit("game_started", {
            "your_word": p["word"],
            "your_role": p["role"],
            "category": category,
            "player_count": len(lobby["players"]),
            "players": [
                {
                    "sid": s,
                    "name": lobby["players"][s]["name"],
                    "color_idx": lobby["players"][s]["color_idx"],
                }
                for s in lobby["players"]
            ],
        }, room=p_sid)

    def countdown():
        for i in range(3, 0, -1):
            socketio.emit("countdown", {"count": i}, room=code)
            eventlet.sleep(1)
        if code in lobbies:
            _start_clue_phase(code)

    lobby["timer_greenlet"] = eventlet.spawn(countdown)


@socketio.on("submit_clue")
def on_clue(data):
    sid = request.sid
    code = sid_lobby.get(sid)
    if not code or code not in lobbies:
        return

    lobby = lobbies[code]
    if lobby.get("phase") != "clue":
        return
    if lobby["current_turn"] >= len(lobby["turn_order"]):
        return
    if lobby["turn_order"][lobby["current_turn"]] != sid:
        return

    clue = data.get("clue", "").strip()[:50]
    if not clue:
        return

    lobby["players"][sid]["clue"] = clue
    lobby["clues"].append({
        "sid": sid,
        "name": lobby["players"][sid]["name"],
        "clue": clue,
    })
    lobby["current_turn"] += 1
    _kill_timer(lobby)

    socketio.emit("clue_submitted", {
        "sid": sid,
        "name": lobby["players"][sid]["name"],
        "clue": clue,
    }, room=code)

    def next_turn():
        eventlet.sleep(1.5)
        if code in lobbies and lobbies[code].get("phase") == "clue":
            _start_clue_turn(code)

    lobby["timer_greenlet"] = eventlet.spawn(next_turn)


@socketio.on("submit_decision")
def on_decision(data):
    sid = request.sid
    code = sid_lobby.get(sid)
    if not code or code not in lobbies:
        return

    lobby = lobbies[code]
    if lobby.get("phase") != "decision":
        return
    if sid not in lobby["players"]:
        return
    if lobby["players"][sid].get("decision") is not None:
        return

    choice = data.get("choice")
    if choice not in ("vote", "continue"):
        return

    lobby["players"][sid]["decision"] = choice

    decided_count = sum(1 for p in lobby["players"].values() if p.get("decision") is not None)
    socketio.emit("decision_cast", {
        "voter": sid,
        "count": decided_count,
        "total": len(lobby["players"]),
    }, room=code)

    _check_all_decided(code)


@socketio.on("submit_vote")
def on_vote(data):
    sid = request.sid
    code = sid_lobby.get(sid)
    if not code or code not in lobbies:
        return

    lobby = lobbies[code]
    if lobby.get("phase") != "vote":
        return
    if sid not in lobby["players"]:
        return
    if lobby["players"][sid].get("vote") is not None:
        return

    target = data.get("target")
    if target not in lobby["players"] or target == sid:
        return

    lobby["players"][sid]["vote"] = target

    voted_count = sum(1 for p in lobby["players"].values() if p.get("vote") is not None)
    socketio.emit("vote_cast", {
        "voter": sid,
        "voted_count": voted_count,
        "total": len(lobby["players"]),
    }, room=code)

    _check_all_voted(code)


@socketio.on("chat_msg")
def on_chat(data):
    sid = request.sid
    code = sid_lobby.get(sid)
    if not code or code not in lobbies:
        return

    lobby = lobbies[code]
    if lobby.get("phase") not in ("decision", "vote", "clue"):
        return

    msg = data.get("msg", "").strip()[:200]
    if not msg:
        return

    socketio.emit("chat_message", {
        "sid": sid,
        "name": lobby["players"][sid]["name"],
        "color_idx": lobby["players"][sid].get("color_idx", 0),
        "msg": msg,
    }, room=code)


@socketio.on("play_again")
def on_play_again():
    sid = request.sid
    code = sid_lobby.get(sid)
    if not code or code not in lobbies:
        return

    lobby = lobbies[code]
    if lobby["host"] != sid:
        return

    _kill_timer(lobby)
    lobby["state"] = "lobby"
    lobby["phase"] = None
    lobby["round"] = 0

    for p in lobby["players"].values():
        p["clue"] = None
        p["decision"] = None
        p["vote"] = None
        p.pop("role", None)
        p.pop("word", None)

    socketio.emit("return_to_lobby", _lobby_view(code), room=code)


@socketio.on("leave_lobby")
def on_leave():
    _handle_leave(request.sid)


@socketio.on("disconnect")
def on_disconnect():
    _handle_leave(request.sid)


def _handle_leave(sid):
    code = sid_lobby.pop(sid, None)
    if not code or code not in lobbies:
        return

    lobby = lobbies[code]
    lobby["players"].pop(sid, None)
    leave_room(code)

    if not lobby["players"]:
        _kill_timer(lobby)
        del lobbies[code]
        return

    if lobby["host"] == sid:
        lobby["host"] = next(iter(lobby["players"]))

    if lobby["state"] == "playing":
        if sid == lobby.get("imposter"):
            _end_game(code, "civilians")
            return

        if len(lobby["players"]) < MIN_PLAYERS:
            _end_game(code, "imposter")
            return

        if lobby.get("phase") == "clue":
            turn_order = lobby.get("turn_order", [])
            ct = lobby.get("current_turn", 0)
            if ct < len(turn_order) and turn_order[ct] == sid:
                turn_order.pop(ct)
                _kill_timer(lobby)
                eventlet.spawn(_start_clue_turn, code)
                return
            elif sid in turn_order:
                idx = turn_order.index(sid)
                turn_order.pop(idx)
                if idx < ct:
                    lobby["current_turn"] = ct - 1

    socketio.emit("lobby_update", _lobby_view(code), room=code)
    socketio.emit("player_left", {
        "sid": sid,
        "players": [
            {"sid": s, "name": p["name"], "color_idx": p.get("color_idx", 0)}
            for s, p in lobby["players"].items()
        ],
    }, room=code)


@app.route("/")
def index():
    return render_template("imposter.html")


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5008, debug=True)
