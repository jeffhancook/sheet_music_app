import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, request, jsonify, session, send_file
from flask_socketio import SocketIO, emit, join_room, disconnect
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import or_, and_, func

from models import Session as DBSession, User, Friendship, Message, PortfolioItem, init_db
from auth import hash_password, verify_password, login_required

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.config["MAX_CONTENT_LENGTH"] = 55 * 1024 * 1024  # 55 MB

socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

UPLOADS_DIR = Path(__file__).parent / "uploads"
PORTFOLIO_DIR = UPLOADS_DIR / "portfolios"
CHAT_IMAGES_DIR = UPLOADS_DIR / "chat_images"
PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
CHAT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

PORTFOLIO_EXTENSIONS = {".mp3", ".mp4", ".pdf"}
CHAT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_PORTFOLIO_FILE = 50 * 1024 * 1024  # 50 MB
MAX_PORTFOLIO_TOTAL = 500 * 1024 * 1024  # 500 MB
MAX_CHAT_IMAGE = 5 * 1024 * 1024  # 5 MB

init_db()


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_db():
    return DBSession()


def current_user_id():
    return session.get("user_id")


def _are_friends(db, user_id, other_id):
    return db.query(Friendship).filter(
        Friendship.status == "accepted",
        or_(
            and_(Friendship.requester_id == user_id, Friendship.addressee_id == other_id),
            and_(Friendship.requester_id == other_id, Friendship.addressee_id == user_id),
        )
    ).first() is not None


# ── Page ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main homepage (for local dev; in production nginx serves it)."""
    homepage = Path(__file__).parent.parent / "website" / "index.html"
    return send_file(str(homepage))


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()

    if not username or not email or not password:
        return jsonify({"error": "Username, email, and password are required"}), 400
    if len(username) < 3 or len(username) > 32:
        return jsonify({"error": "Username must be 3-32 characters"}), 400
    if not re.match(r"^[a-z0-9_]+$", username):
        return jsonify({"error": "Username can only contain letters, numbers, and underscores"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if not display_name:
        display_name = username

    db = get_db()
    try:
        if db.query(User).filter(User.username == username).first():
            return jsonify({"error": "Username already taken"}), 409
        if db.query(User).filter(User.email == email).first():
            return jsonify({"error": "Email already registered"}), 409

        user = User(
            username=username,
            display_name=display_name,
            email=email,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()

        session["user_id"] = user.id
        return jsonify({"user": user.to_dict()}), 201
    finally:
        db.close()


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("username_or_email") or "").strip().lower()
    password = data.get("password") or ""

    if not identifier or not password:
        return jsonify({"error": "Username/email and password are required"}), 400

    db = get_db()
    try:
        user = db.query(User).filter(
            or_(User.username == identifier, User.email == identifier)
        ).first()

        if not user or not verify_password(password, user.password_hash):
            return jsonify({"error": "Invalid credentials"}), 401

        user.last_seen = datetime.now(timezone.utc)
        db.commit()

        session["user_id"] = user.id
        return jsonify({"user": user.to_dict()})
    finally:
        db.close()


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def me():
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401

    db = get_db()
    try:
        user = db.query(User).get(uid)
        if not user:
            session.clear()
            return jsonify({"error": "Not authenticated"}), 401
        user.last_seen = datetime.now(timezone.utc)
        db.commit()
        return jsonify({"user": user.to_dict()})
    finally:
        db.close()


# ── Friends ──────────────────────────────────────────────────────────────────

@app.route("/api/users/search")
@login_required
def search_users():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"users": []})

    uid = current_user_id()
    db = get_db()
    try:
        users = db.query(User).filter(
            User.id != uid,
            or_(
                User.username.ilike(f"%{q}%"),
                User.display_name.ilike(f"%{q}%"),
            )
        ).limit(20).all()
        return jsonify({"users": [u.to_dict() for u in users]})
    finally:
        db.close()


@app.route("/api/friends")
@login_required
def list_friends():
    uid = current_user_id()
    db = get_db()
    try:
        friendships = db.query(Friendship).filter(
            Friendship.status == "accepted",
            or_(Friendship.requester_id == uid, Friendship.addressee_id == uid)
        ).all()

        friends = []
        for f in friendships:
            friend = f.addressee if f.requester_id == uid else f.requester
            friends.append(friend.to_dict())

        return jsonify({"friends": friends})
    finally:
        db.close()


@app.route("/api/friends/requests")
@login_required
def friend_requests():
    uid = current_user_id()
    db = get_db()
    try:
        pending = db.query(Friendship).filter(
            Friendship.addressee_id == uid,
            Friendship.status == "pending"
        ).all()
        return jsonify({"requests": [f.to_dict() for f in pending]})
    finally:
        db.close()


@app.route("/api/friends/request", methods=["POST"])
@login_required
def send_friend_request():
    data = request.get_json(silent=True) or {}
    target_id = data.get("user_id")
    uid = current_user_id()

    if not target_id or target_id == uid:
        return jsonify({"error": "Invalid user"}), 400

    db = get_db()
    try:
        target = db.query(User).get(target_id)
        if not target:
            return jsonify({"error": "User not found"}), 404

        existing = db.query(Friendship).filter(
            or_(
                and_(Friendship.requester_id == uid, Friendship.addressee_id == target_id),
                and_(Friendship.requester_id == target_id, Friendship.addressee_id == uid),
            )
        ).first()

        if existing:
            if existing.status == "accepted":
                return jsonify({"error": "Already friends"}), 409
            if existing.status == "pending":
                return jsonify({"error": "Request already pending"}), 409
            if existing.status == "declined":
                existing.status = "pending"
                existing.requester_id = uid
                existing.addressee_id = target_id
                existing.created_at = datetime.now(timezone.utc)
                existing.responded_at = None
                db.commit()
                friendship_data = existing.to_dict()
                requester = db.query(User).get(uid)
                socketio.emit("friend_request", {
                    "friendship_id": existing.id,
                    "from_user": requester.to_dict(),
                }, room=f"user_{target_id}")
                return jsonify({"friendship": friendship_data}), 201

        friendship = Friendship(requester_id=uid, addressee_id=target_id)
        db.add(friendship)
        db.commit()

        requester = db.query(User).get(uid)
        socketio.emit("friend_request", {
            "friendship_id": friendship.id,
            "from_user": requester.to_dict(),
        }, room=f"user_{target_id}")

        return jsonify({"friendship": friendship.to_dict()}), 201
    finally:
        db.close()


@app.route("/api/friends/respond", methods=["POST"])
@login_required
def respond_friend_request():
    data = request.get_json(silent=True) or {}
    friendship_id = data.get("friendship_id")
    action = data.get("action")  # "accept" or "decline"
    uid = current_user_id()

    if action not in ("accept", "decline"):
        return jsonify({"error": "Action must be 'accept' or 'decline'"}), 400

    db = get_db()
    try:
        friendship = db.query(Friendship).filter(
            Friendship.id == friendship_id,
            Friendship.addressee_id == uid,
            Friendship.status == "pending"
        ).first()

        if not friendship:
            return jsonify({"error": "Request not found"}), 404

        friendship.status = "accepted" if action == "accept" else "declined"
        friendship.responded_at = datetime.now(timezone.utc)
        db.commit()

        if action == "accept":
            me_user = db.query(User).get(uid)
            socketio.emit("friend_accepted", {
                "user": me_user.to_dict(),
            }, room=f"user_{friendship.requester_id}")

        return jsonify({"friendship": friendship.to_dict()})
    finally:
        db.close()


@app.route("/api/friends/<int:user_id>", methods=["DELETE"])
@login_required
def remove_friend(user_id):
    uid = current_user_id()
    db = get_db()
    try:
        friendship = db.query(Friendship).filter(
            Friendship.status == "accepted",
            or_(
                and_(Friendship.requester_id == uid, Friendship.addressee_id == user_id),
                and_(Friendship.requester_id == user_id, Friendship.addressee_id == uid),
            )
        ).first()

        if not friendship:
            return jsonify({"error": "Friendship not found"}), 404

        db.delete(friendship)
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


# ── Chat (REST) ──────────────────────────────────────────────────────────────

@app.route("/api/messages/<int:friend_id>")
@login_required
def message_history(friend_id):
    uid = current_user_id()
    before = request.args.get("before")
    limit = min(int(request.args.get("limit", 50)), 100)

    db = get_db()
    try:
        if not _are_friends(db, uid, friend_id):
            return jsonify({"error": "Not friends"}), 403

        query = db.query(Message).filter(
            or_(
                and_(Message.sender_id == uid, Message.receiver_id == friend_id),
                and_(Message.sender_id == friend_id, Message.receiver_id == uid),
            )
        )
        if before:
            query = query.filter(Message.id < int(before))

        messages = query.order_by(Message.id.desc()).limit(limit).all()
        messages.reverse()

        return jsonify({"messages": [m.to_dict() for m in messages]})
    finally:
        db.close()


@app.route("/api/messages/image", methods=["POST"])
@login_required
def upload_chat_image():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in CHAT_IMAGE_EXTENSIONS:
        return jsonify({"error": f"Unsupported image format: {ext}"}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_CHAT_IMAGE:
        return jsonify({"error": "Image too large (5MB max)"}), 400

    now = datetime.now(timezone.utc)
    month_dir = CHAT_IMAGES_DIR / now.strftime("%Y-%m")
    month_dir.mkdir(exist_ok=True)

    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = month_dir / filename
    file.save(str(filepath))

    rel_path = str(filepath.relative_to(UPLOADS_DIR))
    return jsonify({"image_path": rel_path})


@app.route("/api/messages/unread")
@login_required
def unread_counts():
    uid = current_user_id()
    db = get_db()
    try:
        results = db.query(
            Message.sender_id,
            func.count(Message.id)
        ).filter(
            Message.receiver_id == uid,
            Message.read_at.is_(None)
        ).group_by(Message.sender_id).all()

        counts = {str(sender_id): count for sender_id, count in results}
        return jsonify({"unread": counts})
    finally:
        db.close()


# ── Chat image serving ───────────────────────────────────────────────────────

@app.route("/uploads/<path:filepath>")
@login_required
def serve_upload(filepath):
    full_path = UPLOADS_DIR / filepath
    if not full_path.exists() or not full_path.is_file():
        return jsonify({"error": "Not found"}), 404
    # Ensure path doesn't escape uploads dir
    try:
        full_path.resolve().relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        return jsonify({"error": "Not found"}), 404
    return send_file(str(full_path))


# ── Portfolio ────────────────────────────────────────────────────────────────

@app.route("/api/portfolio")
@login_required
def my_portfolio():
    uid = current_user_id()
    db = get_db()
    try:
        items = db.query(PortfolioItem).filter(
            PortfolioItem.user_id == uid
        ).order_by(PortfolioItem.created_at.desc()).all()
        return jsonify({"items": [i.to_dict() for i in items]})
    finally:
        db.close()


@app.route("/api/portfolio/<int:user_id>")
@login_required
def user_portfolio(user_id):
    uid = current_user_id()
    db = get_db()
    try:
        if user_id == uid:
            items = db.query(PortfolioItem).filter(PortfolioItem.user_id == uid)
        else:
            items = db.query(PortfolioItem).filter(
                PortfolioItem.user_id == user_id,
                PortfolioItem.is_public == True
            )
        items = items.order_by(PortfolioItem.created_at.desc()).all()
        return jsonify({"items": [i.to_dict() for i in items]})
    finally:
        db.close()


@app.route("/api/portfolio/upload", methods=["POST"])
@login_required
def upload_portfolio():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    file = request.files["file"]
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()

    if not file.filename or not title:
        return jsonify({"error": "File and title are required"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in PORTFOLIO_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}. Allowed: mp3, mp4, pdf"}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_PORTFOLIO_FILE:
        return jsonify({"error": "File too large (50MB max)"}), 400

    uid = current_user_id()
    db = get_db()
    try:
        total = db.query(func.coalesce(func.sum(PortfolioItem.file_size), 0)).filter(
            PortfolioItem.user_id == uid
        ).scalar()
        if total + size > MAX_PORTFOLIO_TOTAL:
            return jsonify({"error": "Storage limit reached (500MB max)"}), 400

        user_dir = PORTFOLIO_DIR / str(uid)
        user_dir.mkdir(exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = user_dir / filename
        file.save(str(filepath))

        item = PortfolioItem(
            user_id=uid,
            title=title,
            description=description,
            file_path=str(filepath.relative_to(UPLOADS_DIR)),
            file_type=ext.lstrip("."),
            file_size=size,
        )
        db.add(item)
        db.commit()
        return jsonify({"item": item.to_dict()}), 201
    finally:
        db.close()


@app.route("/api/portfolio/<int:item_id>", methods=["PATCH"])
@login_required
def update_portfolio_item(item_id):
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        item = db.query(PortfolioItem).filter(
            PortfolioItem.id == item_id, PortfolioItem.user_id == uid
        ).first()
        if not item:
            return jsonify({"error": "Not found"}), 404

        if "title" in data:
            item.title = data["title"].strip()
        if "description" in data:
            item.description = data["description"].strip()
        if "is_public" in data:
            item.is_public = bool(data["is_public"])
        db.commit()
        return jsonify({"item": item.to_dict()})
    finally:
        db.close()


@app.route("/api/portfolio/<int:item_id>", methods=["DELETE"])
@login_required
def delete_portfolio_item(item_id):
    uid = current_user_id()
    db = get_db()
    try:
        item = db.query(PortfolioItem).filter(
            PortfolioItem.id == item_id, PortfolioItem.user_id == uid
        ).first()
        if not item:
            return jsonify({"error": "Not found"}), 404

        filepath = UPLOADS_DIR / item.file_path
        if filepath.exists():
            filepath.unlink()

        db.delete(item)
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


@app.route("/api/portfolio/file/<int:item_id>")
@login_required
def serve_portfolio_file(item_id):
    uid = current_user_id()
    db = get_db()
    try:
        item = db.query(PortfolioItem).get(item_id)
        if not item:
            return jsonify({"error": "Not found"}), 404

        if item.user_id != uid and not item.is_public:
            return jsonify({"error": "Access denied"}), 403

        filepath = UPLOADS_DIR / item.file_path
        if not filepath.exists():
            return jsonify({"error": "File missing"}), 404

        return send_file(str(filepath), as_attachment=False)
    finally:
        db.close()


# ── WebSocket ────────────────────────────────────────────────────────────────

@socketio.on("connect")
def ws_connect():
    uid = session.get("user_id")
    if not uid:
        disconnect()
        return
    join_room(f"user_{uid}")
    db = get_db()
    try:
        user = db.query(User).get(uid)
        if user:
            user.last_seen = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@socketio.on("send_message")
def ws_send_message(data):
    uid = session.get("user_id")
    if not uid:
        return

    to = data.get("to")
    content = (data.get("content") or "").strip()
    image_path = data.get("image_path")

    if not to or (not content and not image_path):
        return

    db = get_db()
    try:
        if not _are_friends(db, uid, to):
            return

        msg = Message(
            sender_id=uid,
            receiver_id=to,
            content=content or None,
            image_path=image_path,
        )
        db.add(msg)
        db.commit()

        payload = {
            "id": msg.id,
            "from": uid,
            "content": msg.content,
            "image_path": msg.image_path,
            "created_at": msg.created_at.isoformat(),
        }
        emit("new_message", payload, room=f"user_{to}")
        emit("new_message", payload, room=f"user_{uid}")
    finally:
        db.close()


@socketio.on("typing")
def ws_typing(data):
    uid = session.get("user_id")
    to = data.get("to")
    if uid and to:
        emit("typing", {"from": uid}, room=f"user_{to}")


@socketio.on("mark_read")
def ws_mark_read(data):
    uid = session.get("user_id")
    friend_id = data.get("friend_id")
    if not uid or not friend_id:
        return

    db = get_db()
    try:
        now = datetime.now(timezone.utc)
        db.query(Message).filter(
            Message.sender_id == friend_id,
            Message.receiver_id == uid,
            Message.read_at.is_(None)
        ).update({"read_at": now})
        db.commit()
        emit("messages_read", {"by": uid}, room=f"user_{friend_id}")
    finally:
        db.close()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5004, debug=True)
