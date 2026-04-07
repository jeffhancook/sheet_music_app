import os
import re
import json
import uuid
import random
import subprocess
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, request, jsonify, session, send_file, render_template
from flask_socketio import SocketIO, emit, join_room, disconnect
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import or_, and_, func

import bleach
from models import (Session as DBSession, User, Friendship, Message, PortfolioItem,
                    UserBackground, GroupChat, GroupMember, GroupMessage,
                    UserPage, GuestbookEntry, PageImage, init_db)

ALLOWED_TAGS = ["p", "br", "b", "i", "em", "strong", "a", "ul", "ol", "li",
                "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "img", "span", "div"]
ALLOWED_ATTRS = {"a": ["href", "title", "target"], "img": ["src", "alt", "width", "height"],
                 "span": ["style"], "div": ["style"]}
from auth import hash_password, verify_password, login_required

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.config["MAX_CONTENT_LENGTH"] = None  # enforced per-user in routes

socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

UPLOADS_DIR = Path(__file__).parent / "uploads"
PORTFOLIO_DIR = UPLOADS_DIR / "portfolios"
CHAT_IMAGES_DIR = UPLOADS_DIR / "chat_images"
PAGE_UPLOADS = UPLOADS_DIR / "pages"
PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
CHAT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
PAGE_UPLOADS.mkdir(parents=True, exist_ok=True)

PORTFOLIO_EXTENSIONS = {".mp3", ".mp4", ".pdf"}
CHAT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_PORTFOLIO_FILE = 50 * 1024 * 1024  # 50 MB
MAX_PORTFOLIO_TOTAL = 500 * 1024 * 1024  # 500 MB
MAX_CHAT_IMAGE = 5 * 1024 * 1024  # 5 MB

init_db()


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_db():
    return DBSession()


UNLIMITED_USERNAMES = {"AFlipperStory"}


def current_user_id():
    return session.get("user_id")


def _is_unlimited_user(db):
    uid = current_user_id()
    if not uid:
        return False
    user = db.query(User).get(uid)
    return user and user.username in UNLIMITED_USERNAMES


def _transcode_to_h264(filepath, item_id):
    """Transcode HEVC/H.265 MP4 to H.264 in background for browser compatibility."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(filepath)],
            capture_output=True, text=True, timeout=30
        )
        codec = probe.stdout.strip()
        if codec not in ("hevc", "h265", "vp9", "av1"):
            return  # already compatible

        tmp = filepath.with_suffix(".h264.mp4")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(filepath),
             "-c:v", "libx264", "-preset", "medium", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", str(tmp)],
            capture_output=True, text=True, timeout=3600
        )
        if result.returncode == 0 and tmp.exists():
            tmp.replace(filepath)
            # Update file_size in DB
            db = DBSession()
            try:
                item = db.query(PortfolioItem).get(item_id)
                if item:
                    item.file_size = filepath.stat().st_size
                    db.commit()
            finally:
                db.close()
        else:
            if tmp.exists():
                tmp.unlink()
    except Exception:
        pass


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
    return render_template("community.html")


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

        # Auto-friend with AFlipperStory
        owner = db.query(User).filter(
            func.lower(User.username) == "aflipperstory"
        ).first()
        if owner and owner.id != user.id:
            auto_friendship = Friendship(
                requester_id=owner.id,
                addressee_id=user.id,
                status="accepted",
                responded_at=datetime.now(timezone.utc),
            )
            db.add(auto_friendship)
            db.commit()

        session["user_id"] = user.id
        return jsonify({"user": user.to_dict()}), 201
    finally:
        db.close()


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username_or_email") or "").strip().lower()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    db = get_db()
    try:
        user = db.query(User).filter(User.username == username).first()

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
    db = get_db()
    try:
        unlimited = _is_unlimited_user(db)
    finally:
        db.close()
    if not unlimited and size > MAX_CHAT_IMAGE:
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


# ── File serving ──────────────────────────────────────────────────────────────

@app.route("/uploads/pages/<path:filepath>")
def serve_page_image(filepath):
    return send_file(str(PAGE_UPLOADS / filepath))


@app.route("/uploads/avatars/<path:filepath>")
def serve_avatar(filepath):
    return send_file(str(UPLOADS_DIR / "avatars" / filepath))


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

@app.route("/api/profile/avatar", methods=["POST"])
@login_required
def upload_avatar():
    uid = current_user_id()
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return jsonify({"error": "Only images allowed"}), 400
    user_dir = UPLOADS_DIR / "avatars"
    user_dir.mkdir(parents=True, exist_ok=True)
    filename = f"avatar_{uid}_{uuid.uuid4().hex[:6]}{ext}"
    filepath = user_dir / filename
    f.save(str(filepath))
    avatar_url = f"/community/uploads/avatars/{filename}"
    db = get_db()
    try:
        user = db.query(User).get(uid)
        user.avatar_url = avatar_url
        db.commit()
        return jsonify({"avatar_url": avatar_url, "user": user.to_dict()})
    finally:
        db.close()


@app.route("/api/profile", methods=["PATCH"])
@login_required
def update_profile():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        user = db.query(User).get(uid)
        if not user:
            return jsonify({"error": "Not found"}), 404
        if "bio" in data:
            user.bio = (data["bio"] or "").strip()[:500]
        if "display_name" in data:
            dn = (data["display_name"] or "").strip()
            if dn:
                user.display_name = dn[:64]
        db.commit()
        return jsonify({"user": user.to_dict()})
    finally:
        db.close()


@app.route("/api/portfolio")
@login_required
def my_portfolio():
    uid = current_user_id()
    db = get_db()
    try:
        user = db.query(User).get(uid)
        items = db.query(PortfolioItem).filter(
            PortfolioItem.user_id == uid
        ).order_by(PortfolioItem.created_at.desc()).all()
        return jsonify({"user": user.to_dict(), "items": [i.to_dict() for i in items]})
    finally:
        db.close()


@app.route("/api/portfolio/<int:user_id>")
@login_required
def user_portfolio(user_id):
    uid = current_user_id()
    db = get_db()
    try:
        user = db.query(User).get(uid)
        target = db.query(User).get(user_id)
        if not target:
            return jsonify({"error": "User not found"}), 404
        if user_id == uid:
            items = db.query(PortfolioItem).filter(PortfolioItem.user_id == uid)
        else:
            items = db.query(PortfolioItem).filter(
                PortfolioItem.user_id == user_id,
                PortfolioItem.is_public == True
            )
        items = items.order_by(PortfolioItem.created_at.desc()).all()
        return jsonify({"user": target.to_dict(), "items": [i.to_dict() for i in items]})
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

    uid = current_user_id()
    db = get_db()
    try:
        unlimited = _is_unlimited_user(db)

        if not unlimited and size > MAX_PORTFOLIO_FILE:
            return jsonify({"error": "File too large (50MB max)"}), 400

        if not unlimited:
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
        item_dict = item.to_dict()
        item_id = item.id

        # Transcode MP4 to H.264 in background
        if ext == ".mp4":
            threading.Thread(
                target=_transcode_to_h264,
                args=(filepath, item_id),
                daemon=True
            ).start()

        return jsonify({"item": item_dict}), 201
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

        mimemap = {
            "mp3": "audio/mpeg",
            "mp4": "video/mp4",
            "pdf": "application/pdf",
        }
        mime = mimemap.get(item.file_type, "application/octet-stream")
        return send_file(str(filepath), mimetype=mime, as_attachment=False)
    finally:
        db.close()


# ── WebSocket ────────────────────────────────────────────────────────────────

# ── Personal Pages ────────────────────────────────────────────────────────────

PAGE_GEN_PROMPT = """You are designing a personal webpage for someone named {display_name}. Based on their quiz answers, generate a page layout as JSON.

**Answers:**
- Passions: {passion}
- Energy: {energy}
- Color palette: {palette}
- Perfect day: {saturday}
- Place they feel like themselves: {place}
- Music taste: {music}
- Page vibe: {vibe}
- Visitor greeting vibe: {greeting}

Generate a JSON object with:
1. "theme": {{
     "gradient": "A dark CSS gradient (max luminance 25%) using colors from their palette answer. 3-4 stops.",
     "accentColor": "hex accent from palette",
     "secondaryAccent": "hex secondary accent",
     "textColor": "#d6c8b4",
     "fontFamily": "pick one: Georgia, serif / Courier New, monospace / system-ui, sans-serif / Palatino, serif",
     "headingFont": "same options"
   }}
2. "backgroundSymbols": An array of 6-10 simple SVG strings representing their hobbies/passions. Each SVG should be 30-50px, use the accent colors, and depict a recognizable symbol of their interests (music notes, paintbrushes, code brackets, cameras, game controllers, books, etc). Keep SVGs simple.
3. "sections": An ordered array of section objects. Include 4-6 from: hero, about, links, guestbook, quote, music, gallery.
   Each section: {{"type": "...", "content": {{...}}}}
   - hero: {{"name": "{display_name}", "tagline": a poetic/creative tagline about them based on their answers}}
   - about: {{"html": "<p>Write 2-3 paragraphs in THIRD PERSON about {display_name}. Start with something evocative like 'A creative and soulful spirit, {display_name}...' or 'Driven by curiosity and a love for [passion], {display_name}...' Reference their passions, energy, ideal place, music taste. Make it read like a beautifully written character introduction. Do NOT use first person (I/me/my). Use they/them or {display_name}.</p>"}}
   - quote: {{"text": "a quote that fits their vibe", "attribution": "who said it"}}
   - links: {{"links": [{{"platform": "spotify|youtube|instagram|github|twitter|custom", "url": "", "label": "display text"}}]}}
   - guestbook: {{}}
   - music: {{"description": "a third-person note about their music taste"}}
   - gallery: {{"description": "a third-person note about what they might share"}}
4. "meta_title": "{display_name}'s Page" or something creative
5. "meta_description": One sentence describing this person in third person

IMPORTANT: All text must be THIRD PERSON. Never use I/me/my. Write about {display_name} as if introducing them to the world.

Return ONLY valid JSON, no markdown."""


def generate_page(answers, display_name="Someone"):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": PAGE_GEN_PROMPT.format(
            display_name=display_name,
            passion=answers.get("passion", "creating things"),
            energy=answers.get("energy", "calm"),
            palette=answers.get("palette", "warm tones"),
            saturday=answers.get("saturday", "relaxing"),
            place=answers.get("place", "home"),
            music=answers.get("music", "anything"),
            vibe=answers.get("vibe", "cozy"),
            greeting=answers.get("greeting", "welcome!"),
        )}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if "```" in raw:
            raw = raw[:raw.rfind("```")]
    return json.loads(raw.strip())


@app.route("/api/page/generate", methods=["POST"])
@login_required
def generate_user_page():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    answers = {k: (data.get(k) or "").strip()[:200] for k in
               ["passion", "energy", "palette", "saturday", "place", "music", "vibe", "greeting"]}
    if not answers.get("passion"):
        return jsonify({"error": "Answer at least the first question."}), 400

    db = get_db()
    try:
        user = db.query(User).get(uid)
        existing = db.query(UserPage).filter(UserPage.user_id == uid).first()

        try:
            page_result = generate_page(answers, display_name=user.display_name)
        except Exception as e:
            app.logger.error(f"Page generation failed: {e}")
            return jsonify({"error": "Generation failed. Try again."}), 500

        # Build page_data JSON
        sections = []
        for i, s in enumerate(page_result.get("sections", [])):
            sections.append({
                "id": str(uuid.uuid4())[:8],
                "type": s.get("type", "about"),
                "order": i,
                "visible": True,
                "content": s.get("content", {}),
                "style": {},
            })

        theme = page_result.get("theme", {})
        page_data = {
            "version": 1,
            "theme": theme,
            "layout": {"maxWidth": "900px", "style": "single-column"},
            "sections": sections,
            "backgroundSymbols": page_result.get("backgroundSymbols", []),
            "drawings": [],
            "insertedElements": [],
            "backgroundAudio": None,
            "custom_css": "",
        }

        if existing:
            existing.page_data = json.dumps(page_data)
            existing.quiz_answers = json.dumps(answers)
            existing.meta_title = page_result.get("meta_title", f"{user.display_name}'s Page")
            existing.meta_description = page_result.get("meta_description", "")
            existing.is_published = True
            existing.updated_at = datetime.now(timezone.utc)
            db.commit()
            return jsonify({"page": existing.to_dict()})
        else:
            # Ensure unique slug
            slug = user.username
            conflict = db.query(UserPage).filter(UserPage.slug == slug).first()
            if conflict:
                slug = f"{user.username}-{user.id}"
            page = UserPage(
                user_id=uid,
                slug=slug,
                page_data=json.dumps(page_data),
                quiz_answers=json.dumps(answers),
                meta_title=page_result.get("meta_title", f"{user.display_name}'s Page"),
                meta_description=page_result.get("meta_description", ""),
                is_published=True,
            )
            db.add(page)
            db.commit()
            return jsonify({"page": page.to_dict()}), 201
    finally:
        db.close()


@app.route("/api/page")
@login_required
def get_my_page():
    uid = current_user_id()
    db = get_db()
    try:
        page = db.query(UserPage).filter(UserPage.user_id == uid).first()
        if not page:
            return jsonify({"page": None})
        return jsonify({"page": page.to_dict()})
    finally:
        db.close()


@app.route("/api/page", methods=["PATCH"])
@login_required
def save_page():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        page = db.query(UserPage).filter(UserPage.user_id == uid).first()
        if not page:
            return jsonify({"error": "No page found. Generate one first."}), 404
        if "page_data" in data:
            # Sanitize HTML in sections
            pd = json.loads(data["page_data"]) if isinstance(data["page_data"], str) else data["page_data"]
            for s in pd.get("sections", []):
                if s.get("type") in ("about", "blog") and "html" in s.get("content", {}):
                    s["content"]["html"] = bleach.clean(
                        s["content"]["html"], tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
            page.page_data = json.dumps(pd)
        if "meta_title" in data:
            page.meta_title = (data["meta_title"] or "")[:128]
        if "meta_description" in data:
            page.meta_description = (data["meta_description"] or "")[:256]
        if "slug" in data:
            new_slug = re.sub(r'[^a-z0-9_-]', '', (data["slug"] or "").strip().lower())[:32]
            if new_slug and new_slug != page.slug:
                conflict = db.query(UserPage).filter(UserPage.slug == new_slug, UserPage.id != page.id).first()
                if not conflict:
                    page.slug = new_slug
        page.updated_at = datetime.now(timezone.utc)
        db.commit()
        return jsonify({"page": page.to_dict()})
    finally:
        db.close()


@app.route("/api/page/delete", methods=["POST"])
@login_required
def delete_page():
    uid = current_user_id()
    db = get_db()
    try:
        page = db.query(UserPage).filter(UserPage.user_id == uid).first()
        if not page:
            return jsonify({"error": "No page"}), 404
        db.query(GuestbookEntry).filter(GuestbookEntry.page_id == page.id).delete()
        db.delete(page)
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


@app.route("/api/page/publish", methods=["PATCH"])
@login_required
def toggle_publish():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        page = db.query(UserPage).filter(UserPage.user_id == uid).first()
        if not page:
            return jsonify({"error": "No page found."}), 404
        page.is_published = bool(data.get("publish", not page.is_published))
        page.updated_at = datetime.now(timezone.utc)
        db.commit()
        return jsonify({"page": page.to_dict()})
    finally:
        db.close()


@app.route("/p/<slug>")
def view_page(slug):
    db = get_db()
    try:
        page = db.query(UserPage).filter(UserPage.slug == slug).first()
        if not page or not page.is_published:
            return "Page not found", 404
        page.view_count += 1
        db.commit()
        user = db.query(User).get(page.user_id)
        page_data = json.loads(page.page_data)
        return render_template("page_view.html",
            page=page, user=user, page_data=page_data,
            meta_title=page.meta_title, meta_description=page.meta_description)
    finally:
        db.close()


@app.route("/api/page/guestbook/<slug>")
def get_guestbook(slug):
    db = get_db()
    try:
        page = db.query(UserPage).filter(UserPage.slug == slug).first()
        if not page:
            return jsonify({"error": "Not found"}), 404
        entries = db.query(GuestbookEntry).filter(
            GuestbookEntry.page_id == page.id
        ).order_by(GuestbookEntry.created_at.desc()).limit(50).all()
        return jsonify({"entries": [e.to_dict() for e in entries]})
    finally:
        db.close()


@app.route("/api/page/guestbook/<slug>", methods=["POST"])
def post_guestbook(slug):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Anonymous").strip()[:64]
    content = (data.get("content") or "").strip()[:500]
    if not content:
        return jsonify({"error": "Message is required."}), 400

    db = get_db()
    try:
        page = db.query(UserPage).filter(UserPage.slug == slug).first()
        if not page or not page.is_published:
            return jsonify({"error": "Not found"}), 404
        uid = session.get("user_id")
        entry = GuestbookEntry(
            page_id=page.id, author_name=name,
            author_user_id=uid, content=bleach.clean(content, tags=[], strip=True),
        )
        db.add(entry)
        db.commit()
        return jsonify({"entry": entry.to_dict()}), 201
    finally:
        db.close()


@app.route("/api/page/guestbook/<int:entry_id>", methods=["DELETE"])
@login_required
def delete_guestbook_entry(entry_id):
    uid = current_user_id()
    db = get_db()
    try:
        entry = db.query(GuestbookEntry).get(entry_id)
        if not entry:
            return jsonify({"error": "Not found"}), 404
        page = db.query(UserPage).get(entry.page_id)
        if not page or page.user_id != uid:
            return jsonify({"error": "Not authorized"}), 403
        db.delete(entry)
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


@app.route("/api/page/audio", methods=["POST"])
@login_required
def upload_page_audio():
    uid = current_user_id()
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".mp3", ".wav", ".ogg"):
        return jsonify({"error": "Only MP3, WAV, OGG allowed"}), 400
    user_dir = PAGE_UPLOADS / str(uid)
    user_dir.mkdir(parents=True, exist_ok=True)
    filename = f"audio_{uuid.uuid4().hex[:8]}{ext}"
    filepath = user_dir / filename
    f.save(str(filepath))
    rel_path = f"pages/{uid}/{filename}"
    return jsonify({"audio_path": f"/community/uploads/{rel_path}"})


@app.route("/api/page/image", methods=["POST"])
@login_required
def upload_page_image():
    uid = current_user_id()
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return jsonify({"error": "Only images allowed"}), 400
    user_dir = PAGE_UPLOADS / str(uid)
    user_dir.mkdir(parents=True, exist_ok=True)
    filename = f"img_{uuid.uuid4().hex[:8]}{ext}"
    filepath = user_dir / filename
    f.save(str(filepath))
    rel_path = f"pages/{uid}/{filename}"
    return jsonify({"image_path": f"/community/uploads/{rel_path}"})


@app.route("/editor")
@login_required
def page_editor():
    return render_template("page_editor.html")


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
        # Join all group rooms
        memberships = db.query(GroupMember).filter(GroupMember.user_id == uid).all()
        for m in memberships:
            join_room(f"group_{m.group_id}")
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


@socketio.on("send_group_message")
def ws_send_group_message(data):
    uid = session.get("user_id")
    group_id = data.get("group_id")
    content = (data.get("content") or "").strip()
    image_path = data.get("image_path")
    if not uid or not group_id:
        return
    if not content and not image_path:
        return

    db = get_db()
    try:
        member = db.query(GroupMember).filter(
            GroupMember.group_id == group_id, GroupMember.user_id == uid
        ).first()
        if not member:
            return
        user = db.query(User).get(uid)
        msg = GroupMessage(
            group_id=group_id, sender_id=uid,
            content=content if content else None,
            image_path=image_path,
        )
        db.add(msg)
        db.commit()
        emit("new_group_message", {
            "id": msg.id, "group_id": group_id, "from": uid,
            "sender_name": user.display_name if user else "Unknown",
            "sender_color": user.avatar_color if user else "#c2593e",
            "content": msg.content, "image_path": msg.image_path,
            "created_at": msg.created_at.isoformat(),
        }, room=f"group_{group_id}")
    finally:
        db.close()


@socketio.on("group_typing")
def ws_group_typing(data):
    uid = session.get("user_id")
    group_id = data.get("group_id")
    if not uid or not group_id:
        return
    db = get_db()
    try:
        user = db.query(User).get(uid)
        name = user.display_name if user else "Someone"
    finally:
        db.close()
    emit("group_typing", {"group_id": group_id, "from": uid, "name": name},
         room=f"group_{group_id}", include_self=False)


# ── Group Chat API ───────────────────────────────────────────────────────────

@app.route("/api/groups")
@login_required
def list_groups():
    uid = current_user_id()
    db = get_db()
    try:
        memberships = db.query(GroupMember).filter(GroupMember.user_id == uid).all()
        groups = []
        for m in memberships:
            g = db.query(GroupChat).get(m.group_id)
            if not g:
                continue
            gd = g.to_dict(include_members=True)
            # Get last message
            last = db.query(GroupMessage).filter(
                GroupMessage.group_id == g.id
            ).order_by(GroupMessage.created_at.desc()).first()
            if last:
                gd["last_message"] = last.to_dict()
            gd["member_count"] = len(g.members)
            groups.append(gd)
        return jsonify({"groups": groups})
    finally:
        db.close()


@app.route("/api/groups", methods=["POST"])
@login_required
def create_group():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:64]
    member_ids = data.get("member_ids", [])

    if not name:
        return jsonify({"error": "Group name is required."}), 400
    if not member_ids or len(member_ids) < 1:
        return jsonify({"error": "Add at least one friend."}), 400

    db = get_db()
    try:
        group = GroupChat(name=name, creator_id=uid)
        db.add(group)
        db.flush()

        # Add creator as member
        db.add(GroupMember(group_id=group.id, user_id=uid))
        # Add other members (must be friends)
        for mid in member_ids:
            if mid == uid:
                continue
            if _are_friends(db, uid, mid):
                db.add(GroupMember(group_id=group.id, user_id=mid))
        db.commit()

        # Notify members via socket
        for m in group.members:
            socketio.emit("group_created", {"group_id": group.id}, room=f"user_{m.user_id}")

        return jsonify({"group": group.to_dict(include_members=True)}), 201
    finally:
        db.close()


@app.route("/api/groups/<int:group_id>/messages")
@login_required
def group_messages(group_id):
    uid = current_user_id()
    db = get_db()
    try:
        member = db.query(GroupMember).filter(
            GroupMember.group_id == group_id, GroupMember.user_id == uid
        ).first()
        if not member:
            return jsonify({"error": "Not a member"}), 403

        limit = min(int(request.args.get("limit", 50)), 100)
        msgs = db.query(GroupMessage).filter(
            GroupMessage.group_id == group_id
        ).order_by(GroupMessage.created_at.desc()).limit(limit).all()
        msgs.reverse()
        return jsonify({"messages": [m.to_dict() for m in msgs]})
    finally:
        db.close()


@app.route("/api/groups/<int:group_id>/leave", methods=["POST"])
@login_required
def leave_group(group_id):
    uid = current_user_id()
    db = get_db()
    try:
        member = db.query(GroupMember).filter(
            GroupMember.group_id == group_id, GroupMember.user_id == uid
        ).first()
        if not member:
            return jsonify({"error": "Not a member"}), 404

        group = db.query(GroupChat).get(group_id)

        # If creator leaves, delete the whole group
        if group and group.creator_id == uid:
            db.query(GroupMessage).filter(GroupMessage.group_id == group_id).delete()
            db.query(GroupMember).filter(GroupMember.group_id == group_id).delete()
            db.delete(group)
        else:
            db.delete(member)
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


# ── AI Background Generation ─────────────────────────────────────────────────

import anthropic

BACKGROUND_PROMPT = """You are an expert CSS/SVG artist designing immersive backgrounds for a dark-themed web app. Here's an example of the QUALITY LEVEL expected — the app's homepage uses:

- Floating SVG illustrations (music notes, treble clefs) at various positions with slow bobbing animations
- Abstract SVG brush-stroke curves drifting across the background
- Geometric "constellation" clusters — nodes connected by lines, slowly drifting
- Tiny colored dust particles twinkling at different speeds
- Glowing motes that float upward and fade
- Subtle staff-line fragments spanning the width
- A dark warm gradient base with radial color accents

Your job: generate a background of EQUAL complexity that is a visual portrait of this person. EVERY answer should influence the result.

**What's something you could do for hours without getting bored?** {passion}
**Pick a word that describes your energy:** {energy}
**If your life had a color palette, what would it look like?** {palette}
**Describe your perfect Saturday in one sentence:** {saturday}
**What's a place (real or imaginary) where you feel most like yourself?** {place}

HOW EACH ANSWER SHOULD SHAPE THE BACKGROUND:
- **Passion answer** → The floating SVG elements MUST be recognizable "remnants" of this activity. If they said violin/music: draw music notes (ellipse noteheads + stems), treble clefs, staff fragments. If coding: angle brackets, curly braces, semicolons. If painting: small brush shapes, paint splatters, palette shapes. If hiking: mountain silhouettes, trail markers, pine trees. These should be IMMEDIATELY recognizable, not abstract.
- **Energy answer** → Controls animation speeds and density. "calm" = slow floats, sparse. "intense" = faster, denser. "dreamy" = very slow, ethereal. "playful" = varied speeds.
- **Palette answer** → DIRECTLY determines the gradient colors and accent colors. Use the colors they described. "deep ocean blues and silver" = blue gradient with silver accents. "sunset oranges" = dark orange/amber gradient.
- **Saturday answer** → Influences the constellation shapes and overall mood. Cozy Saturday = warm clustered nodes. Adventure Saturday = spread-out expansive shapes.
- **Place answer** → Influences the brush stroke shapes and glow colors. A forest = organic flowing strokes with green-tinted glows. A city rooftop = angular strokes with urban-tinted glows.

Generate a JSON object with these fields:

1. "name": Creative 2-3 word name that captures the person's vibe

2. "gradient": Complex CSS gradient using colors from their palette answer. 4-6 dark color stops. Max luminance ~25%.

3. "accent1": Primary accent hex color drawn from their palette/passion
4. "accent2": Secondary accent hex color (complementary/analogous)

5. "heroIllustration": A SINGLE large SVG illustration that fills most of the screen as a static background piece — like a faded wallpaper or etching. This is the CENTERPIECE of the entire background. It should be a detailed scene that combines the user's passion AND place answers into one composition. Examples:
   - Violinist who loves concert halls: A full sheet music page with staff lines, notes, clefs, bar lines, dynamic markings — filling the viewport like an old manuscript page. Add subtle architectural arches of a concert hall framing the edges.
   - Hiker who loves mountain cabins: A panoramic mountain ridge with pine trees, a winding trail, and a small cabin silhouette — spanning the full width.
   - Coder who loves their desk at night: A large code editor layout with bracket pairs, indented lines, a terminal prompt, with a window frame showing stars.
   - Reader who loves libraries: Floor-to-ceiling bookshelves with hundreds of book spines, an arched library doorway.
   The SVG should:
   - Use width="100%" height="100%" with a viewBox like "0 0 1200 800" to fill the screen
   - Use ONLY the accent colors for strokes and fills (stroke-width 0.5-1.5)
   - Be DETAILED — use many lines, shapes, and paths to create a rich scene. Draw 20-40+ individual elements.
   - Feel like a faded blueprint, etching, or vintage illustration
   Return as:
   - "svg": The full inline SVG string
   - "opacity": 0.04-0.08 (subtle but visible)

6. "floatingElements": Array of 6-8 smaller SVGs that are LITERAL REMNANTS of their passion. Each has:
   - "svg": Inline SVG with viewBox. Draw recognizable icons of their hobby/passion using simple shapes (ellipse, line, path, circle). For example, a music note = ellipse notehead + vertical stem line. Keep SVGs simple but RECOGNIZABLE. 20-50px wide.
   - "top": CSS position like "8%"
   - "left": CSS position like "12%" (use "right" key instead for right-positioned ones)
   - "opacity": 0.08-0.15 (must be visible! not too faint)
   - "animDuration": 6-10 (seconds)
   - "animDelay": 0-5 (seconds)

6. "constellations": Array of 2 geometric cluster objects. Each has:
   - "svg": Inline SVG (200-280px wide) with connected lines and circles at nodes. Use accent colors, 5-7 nodes. Keep paths simple.
   - "top", "left" (or "right"): CSS positions
   - "opacity": 0.18-0.28
   - "animDuration": 20-28

7. "brushStrokes": Array of 2 abstract SVG curves. Each has:
   - "svg": SVG with a single flowing bezier path (300-400px wide). Accent colors, stroke-width 2-3, fill none.
   - "top", "left": CSS positions
   - "opacity": 0.04-0.07
   - "animDuration": 10-16

8. "particles": Array of 10-14 dust particles. Each has:
   - "top", "left": CSS positions spread across viewport
   - "size": 2-5 (px)
   - "color": hex color
   - "opacity": 0.07-0.18
   - "animDuration": 3.5-7

9. "motes": Array of 6-8 floating light motes. Each has:
   - "top", "left": CSS positions
   - "size": 3-5 (px)
   - "color": hex color
   - "floatDuration": 7-14
   - "floatDistance": 50-90 (px)
   - "driftX": -15 to 15 (px)
   - "opacity": 0.2-0.35

10. "glows": Array of 2-3 ambient glow spots:
    - "color": hex color
    - "top", "left" (or "right", "bottom"): position
    - "size": 150-300 (px)
    - "opacity": 0.06-0.15

CRITICAL RULES:
- ALL colors must be dark enough that light text (#d6c8b4) is readable
- SVGs must use the accent colors, NOT white
- Floating elements must be RECOGNIZABLE and themed — not generic shapes
- Make every generation UNIQUE — vary positions, colors, shapes, sizes
- The constellations should feel like abstract art, not literal depictions
- Spread elements across the full viewport (0%-95% for positions)
- Keep the small floating SVGs concise. The heroIllustration can be more detailed but keep path data reasonable — prefer many simple shapes over few complex paths.

Return ONLY valid JSON, no markdown fences, no explanation."""


def generate_background(answers):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": BACKGROUND_PROMPT.format(
                passion=answers.get("passion", "creating things"),
                energy=answers.get("energy", "calm"),
                palette=answers.get("palette", "warm earthy tones"),
                saturday=answers.get("saturday", "relaxing at home"),
                place=answers.get("place", "a quiet room"),
            ),
        }],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]
        elif "```" in raw:
            raw = raw[:raw.rfind("```")]
    raw = raw.strip()

    css = json.loads(raw)

    # Validate required fields
    required = ["name", "gradient", "accent1", "accent2",
                "heroIllustration", "floatingElements", "constellations", "particles", "motes", "glows"]
    for field in required:
        if field not in css:
            raise ValueError(f"Missing field: {field}")

    return css


# ── Background API ───────────────────────────────────────────────────────────

@app.route("/api/backgrounds")
@login_required
def list_backgrounds():
    uid = current_user_id()
    db = get_db()
    try:
        bgs = db.query(UserBackground).filter(
            UserBackground.user_id == uid
        ).order_by(UserBackground.created_at.desc()).all()
        user = db.query(User).get(uid)
        return jsonify({
            "backgrounds": [b.to_dict() for b in bgs],
            "active_id": user.active_background_id,
        })
    finally:
        db.close()


@app.route("/api/backgrounds", methods=["POST"])
@login_required
def create_background():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    answers = {
        "passion": (data.get("passion") or "").strip()[:200],
        "energy": (data.get("energy") or "").strip()[:50],
        "palette": (data.get("palette") or "").strip()[:200],
        "saturday": (data.get("saturday") or "").strip()[:200],
        "place": (data.get("place") or "").strip()[:200],
    }
    if not answers["passion"]:
        return jsonify({"error": "Please answer at least the first question."}), 400

    db = get_db()
    try:
        count = db.query(UserBackground).filter(
            UserBackground.user_id == uid
        ).count()
        if count >= 5:
            return jsonify({"error": "Maximum 5 backgrounds. Delete one first."}), 400

        try:
            css = generate_background(answers)
        except Exception as e:
            app.logger.error(f"Background generation failed: {e}")
            return jsonify({"error": "Generation failed. Try again."}), 500

        bg = UserBackground(
            user_id=uid,
            name=css["name"],
            quiz_answers=json.dumps(answers),
            css_data=json.dumps(css),
        )
        db.add(bg)
        db.commit()

        # Auto-apply the new background
        user = db.query(User).get(uid)
        user.active_background_id = bg.id
        db.commit()

        return jsonify({"background": bg.to_dict(), "active_id": bg.id}), 201
    finally:
        db.close()


@app.route("/api/backgrounds/<int:bg_id>", methods=["DELETE"])
@login_required
def delete_background(bg_id):
    uid = current_user_id()
    db = get_db()
    try:
        bg = db.query(UserBackground).filter(
            UserBackground.id == bg_id,
            UserBackground.user_id == uid,
        ).first()
        if not bg:
            return jsonify({"error": "Not found"}), 404

        user = db.query(User).get(uid)
        if user.active_background_id == bg_id:
            user.active_background_id = None

        db.delete(bg)
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


@app.route("/api/backgrounds/active", methods=["PATCH"])
@login_required
def set_active_background():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    bg_id = data.get("background_id")  # null to reset

    db = get_db()
    try:
        user = db.query(User).get(uid)
        if bg_id is not None:
            bg = db.query(UserBackground).filter(
                UserBackground.id == bg_id,
                UserBackground.user_id == uid,
            ).first()
            if not bg:
                return jsonify({"error": "Not found"}), 404
        user.active_background_id = bg_id
        db.commit()
        return jsonify({"active_id": bg_id})
    finally:
        db.close()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5004, debug=True)
