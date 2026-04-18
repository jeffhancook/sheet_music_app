import os
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from models import Session as DBSession, Pet, PET_TYPES, PET_COLORS, init_db


PET_STARVE_HOURS = 24
PET_CHEAT_HOURS = 18
PET_HATCH_HOURS = 72


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
# Must match the community SECRET_KEY so session cookies round-trip between apps.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
app.config["SESSION_COOKIE_PATH"] = "/"

init_db()


def get_db():
    return DBSession()


def current_user_id():
    return session.get("user_id")


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_user_id():
            return jsonify({"error": "Authentication required"}), 401
        return f(*a, **kw)
    return wrapper


def _pet_status(pet):
    if not pet:
        return None
    d = pet.to_dict()
    now = datetime.now(timezone.utc)
    last_fed = pet.last_fed_at
    if last_fed and last_fed.tzinfo is None:
        last_fed = last_fed.replace(tzinfo=timezone.utc)
    if pet.alive and last_fed:
        elapsed = (now - last_fed).total_seconds()
        remaining = PET_STARVE_HOURS * 3600 - elapsed
        if remaining <= 0:
            pet.alive = False
            pet.died_at = now
            d["alive"] = False
            d["died_at"] = now.isoformat()
            d["seconds_remaining"] = 0
        else:
            d["seconds_remaining"] = int(remaining)
    else:
        d["seconds_remaining"] = 0
    d["starve_hours"] = PET_STARVE_HOURS

    created = pet.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created:
        if pet.alive:
            d["seconds_alive"] = int((now - created).total_seconds())
        else:
            died = pet.died_at
            if died and died.tzinfo is None:
                died = died.replace(tzinfo=timezone.utc)
            end = died or now
            d["seconds_alive"] = max(0, int((end - created).total_seconds()))
    else:
        d["seconds_alive"] = 0

    hatch_secs = PET_HATCH_HOURS * 3600
    d["hatch_hours"] = PET_HATCH_HOURS
    d["is_hatched"] = d["seconds_alive"] >= hatch_secs
    d["seconds_to_hatch"] = max(0, hatch_secs - d["seconds_alive"])
    return d


@app.route("/")
def index():
    return render_template("pet.html")


@app.route("/api/session")
def api_session():
    uid = current_user_id()
    return jsonify({"authenticated": bool(uid), "user_id": uid})


@app.route("/api/pet")
@login_required
def get_pet():
    uid = current_user_id()
    db = get_db()
    try:
        pet = db.query(Pet).filter(Pet.user_id == uid).first()
        if not pet:
            return jsonify({"pet": None})
        status = _pet_status(pet)
        db.commit()
        return jsonify({"pet": status})
    finally:
        db.close()


@app.route("/api/pet", methods=["POST"])
@login_required
def select_pet():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    pet_type = (data.get("pet_type") or "").strip().lower()
    name = (data.get("name") or "").strip()[:32]
    color = (data.get("color") or "natural").strip().lower()
    if color not in PET_COLORS:
        color = "natural"
    if pet_type not in PET_TYPES:
        return jsonify({"error": "Invalid pet type"}), 400
    if not name:
        return jsonify({"error": "Please give your pet a name — it can't be changed later."}), 400

    db = get_db()
    try:
        pet = db.query(Pet).filter(Pet.user_id == uid).first()
        now = datetime.now(timezone.utc)
        if pet:
            if pet.alive:
                return jsonify({"error": "Your pet is still alive. You can't change pets until it dies."}), 403
            pet.pet_type = pet_type
            pet.name = name
            pet.color = color
            pet.last_fed_at = now
            pet.alive = True
            pet.died_at = None
            pet.created_at = now
        else:
            pet = Pet(
                user_id=uid,
                pet_type=pet_type,
                name=name,
                color=color,
                last_fed_at=now,
                alive=True,
            )
            db.add(pet)
        db.commit()
        return jsonify({"pet": _pet_status(pet)})
    finally:
        db.close()


@app.route("/api/pet", methods=["PATCH"])
@login_required
def patch_pet():
    uid = current_user_id()
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        pet = db.query(Pet).filter(Pet.user_id == uid).first()
        if not pet:
            return jsonify({"error": "No pet"}), 404
        if "color" in data:
            c = (data.get("color") or "").strip().lower()
            if c in PET_COLORS:
                pet.color = c
        # Name is set at adoption and cannot be changed afterward.
        db.commit()
        return jsonify({"pet": _pet_status(pet)})
    finally:
        db.close()


@app.route("/api/pet/feed", methods=["POST"])
@login_required
def feed_pet():
    uid = current_user_id()
    db = get_db()
    try:
        pet = db.query(Pet).filter(Pet.user_id == uid).first()
        if not pet:
            return jsonify({"error": "No pet yet"}), 404
        status = _pet_status(pet)
        if not pet.alive:
            db.commit()
            return jsonify({"error": "Your pet has starved", "pet": status}), 400
        pet.last_fed_at = datetime.now(timezone.utc)
        db.commit()
        return jsonify({"pet": _pet_status(pet)})
    finally:
        db.close()


@app.route("/api/pet/cheat", methods=["POST"])
@login_required
def cheat_pet():
    uid = current_user_id()
    db = get_db()
    try:
        pet = db.query(Pet).filter(Pet.user_id == uid).first()
        if not pet:
            return jsonify({"error": "No pet yet"}), 404
        status = _pet_status(pet)
        if not pet.alive:
            db.commit()
            return jsonify({"error": "Your pet has starved", "pet": status}), 400
        delta = timedelta(hours=PET_CHEAT_HOURS)
        last_fed = pet.last_fed_at
        if last_fed and last_fed.tzinfo is None:
            last_fed = last_fed.replace(tzinfo=timezone.utc)
        pet.last_fed_at = last_fed - delta
        created = pet.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created:
            pet.created_at = created - delta
        db.commit()
        return jsonify({"pet": _pet_status(pet)})
    finally:
        db.close()


@app.route("/api/pet", methods=["DELETE"])
@login_required
def delete_pet():
    uid = current_user_id()
    db = get_db()
    try:
        pet = db.query(Pet).filter(Pet.user_id == uid).first()
        if pet:
            if pet.alive:
                return jsonify({"error": "Your pet is still alive."}), 403
            db.delete(pet)
            db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5010, debug=True)
