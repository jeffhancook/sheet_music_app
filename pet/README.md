# Pet — Virtual Companion App

A virtual pet that lives on `hanzchau.com`. Pick one of four species (chicken,
goose, dog, turtle), name it permanently, wait 3 days for its egg to hatch,
then keep it alive by feeding it every 24 hours. Once hatched it roams the
community page with you and paces in a little grassy paddock on its own page.

- **Standalone Flask app** on port **5010**.
- **Shared session** with `/community/` via a common `SECRET_KEY` — log in on
  the community site once, and the pet app knows who you are.
- **Own database** (`pet.db`) separate from community.
- **Reusable widget** (`static/pet-widget.js` / `.css`) that other pages can
  drop in with one `<script>` tag. The widget injects its own DOM and handles
  every pet behaviour on its own.

---

## Directory layout

```
pet/
├── app.py                       # Flask app + JSON API
├── models.py                    # SQLAlchemy Pet model + one-time import
├── requirements.txt
├── pet.db                       # Runtime SQLite (not tracked)
├── templates/
│   └── pet.html                 # The /pet/ zone page (hero card + widget)
├── static/
│   ├── pet-widget.js            # Shared creature widget (free + paddock)
│   ├── pet-widget.css           # Widget styles (creature, paddock, drop-bone)
│   └── sounds/
│       └── chicken_alarm.mp3    # Played when you click a chicken
└── README.md                    # This file
```

---

## Backend

### Data model (`models.py`)

```python
class Pet:
    id           : int
    user_id      : int       # references community.users.id (not enforced across DBs)
    pet_type     : "chicken" | "goose" | "dog" | "turtle"
    name         : str       # required at adoption, immutable afterward
    color        : one of PET_COLORS  # natural/white/red/orange/green/blue/purple/pink
    last_fed_at  : datetime  # every feed resets this; starvation kicks in 24h later
    alive        : bool      # flips false once seconds_remaining hits 0
    died_at      : datetime? # when alive flipped to false
    created_at   : datetime  # reset on each fresh adoption, used to age the egg
```

`init_db()` creates the schema and, on first run, **imports any pre-existing
pets from `community/community.db`** so users who adopted before the split
keep their pet.

### Timers (constants in `app.py`)

| Constant            | Value | Meaning                                     |
|---------------------|-------|---------------------------------------------|
| `PET_STARVE_HOURS`  | 24    | Feed within this window or the pet dies.    |
| `PET_HATCH_HOURS`   | 72    | 3 days of egg before the animal appears.    |
| `PET_CHEAT_HOURS`   | 18    | `/api/pet/cheat` shaves this off every timer. |

### Status computation (`_pet_status`)

Every response runs the pet through `_pet_status`, which:
1. Lazily flips `alive → False` + stamps `died_at` if `now - last_fed_at` has
   exceeded the starve window (so death doesn't need a cron job).
2. Adds `seconds_remaining` (until starvation) and `seconds_alive` (age, frozen
   at `died_at` once dead).
3. Adds `is_hatched` (`seconds_alive >= PET_HATCH_HOURS * 3600`) and
   `seconds_to_hatch`.

### API

All endpoints require a community session (the `user_id` key in Flask session).

| Method | Path                    | Behaviour                                                     |
|--------|-------------------------|---------------------------------------------------------------|
| GET    | `/api/session`          | `{ authenticated: bool, user_id: int? }` — never 401s.        |
| GET    | `/api/pet`              | `{ pet: ... | null }`. `null` if the user hasn't adopted.     |
| POST   | `/api/pet`              | Adopt / re-adopt (body: `pet_type`, `name`, `color?`). **Refuses if an alive pet already exists.** Re-adoption resets `created_at`. |
| PATCH  | `/api/pet`              | Update `color` only. `name` is intentionally **ignored**.     |
| POST   | `/api/pet/feed`         | Set `last_fed_at = now`. 400 if the pet is already starved.   |
| POST   | `/api/pet/cheat`        | Subtracts 18 h from **both** `last_fed_at` and `created_at`. Used to speed up hatch or starvation for testing. |
| DELETE | `/api/pet`              | Delete a **dead** pet record (so the user can adopt fresh). Refuses if alive. |

### Session sharing

`SECRET_KEY` is set identically in both `/etc/systemd/system/community.service`
and `/etc/systemd/system/pet.service`. Flask's default session cookie (name
`session`, path `/`) is readable by either app. No re-login required when
crossing between `/community/` and `/pet/`.

---

## Frontend — the widget

`pet-widget.js` is a single self-contained IIFE. Any page can load it:

```html
<script src="/pet/static/pet-widget.js" defer></script>
```

The widget injects everything it needs (Twemoji script, `<link>` to
`pet-widget.css`, creature DOM, food layer, fake cursor). It also exposes:

```js
window.petWidget = {
    refresh(),                   // re-fetch /api/pet and re-render
    getPet(),                    // returns the cached pet object
    dropFood(),                  // triggers a bone drop for a hatched dog
};
```

Pages that mutate the pet (`pet.html`) call `window.petWidget.refresh()` after
a successful POST/PATCH so the creature reflects the change immediately.

### Modes

```js
const MODE = window.location.pathname.startsWith('/pet/') ? 'paddock' : 'free';
```

- **`free`** (community and anywhere else): pet is `position: fixed`, roams
  the full viewport, reacts to your cursor, clicks, and typing. A floating
  `🦴 Drop bone` button appears bottom-right when the pet is a hatched dog.
- **`paddock`** (`/pet/`): pet lives inside a small grassy pen in the
  bottom-right of the page. Paces left↔right, doesn't react to the cursor,
  doesn't follow typing, doesn't chase. Dogs get an inline Drop bone button
  inside the paddock.

### Egg stage

For the first 72 hours of a pet's life, `pet.is_hatched === false`:
- Widget swaps the creature emoji to `🥚` with `data-pet="egg"`.
- Pacing/crawling is disabled — the egg sits still at a random spot (in
  paddock mode, inside the paddock; in free mode, somewhere in the viewport),
  wobbling gently via the `egg-wobble` keyframe.
- Clicking / cursor proximity / typing all do nothing.
- `pet.html`'s hero card renders a CSS-drawn egg themed per species
  (mottled green for turtle, speckled cream for chicken, smooth blue-white
  for goose) or a wood-and-heat-lamp **incubator** for the dog.

When `seconds_to_hatch` ticks to zero client-side, `is_hatched` flips to
`true`, `petApplyCreature()` re-runs with the animal emoji, and roaming/
pacing kicks in.

### Dead silhouette

When the pet starves, the sprite:
- Freezes in place (`transform: none !important`).
- Drops all idle animations (`animation: none !important`).
- Gets a **gray silhouette filter**:
  `filter: brightness(0) saturate(0) invert(0.58) drop-shadow(...)`.
  `brightness(0)` flattens the Twemoji SVG's coloured pixels to black while
  preserving alpha; `invert(0.58)` lifts them back to a muted mid-gray;
  drop-shadows add a faint halo so the silhouette reads.

A `petDeadPlaced` flag makes sure the silhouette stays exactly where the pet
died if it died mid-session, or picks a random stable spot if the user first
loads the page after death. The flag only resets when the pet flips back to
`alive`, so the grave doesn't wander between page loads.

### Per-species behaviour (free mode)

| Species | Idle animation | Walk speed | Extra behaviour |
|---------|----------------|------------|-----------------|
| 🐓 Chicken | `chicken-peck` (double-pecks the ground, then a pause) | 95 px/s | Click → plays `chicken_alarm.mp3` and flaps/hops to a new spot. No cursor-proximity reactions. |
| 🐕 Dog     | `dog-trot` (bouncy ±3° lean, 4 px hop) | 150 px/s | `petWidget.dropFood()` spawns a 🦴 bone. Dog paths to the nearest bone, eats, and auto-feeds (resets starvation timer). |
| 🐢 Turtle  | `turtle-plod` (slow sway) | 35 px/s  | Cursor within 80 px → `in-shell` animation: fade out, a CSS-drawn green shell dome appears, wobbles, then the turtle re-emerges. |
| 🪿 Goose   | `pet-waddle` (rocking ±11°) | 95 px/s | ~14% chance per crawl step (after a 30 s cooldown) to chase the cursor. If it stays within 32 px of the cursor for 0.45 s → `petCatch` locks the fake red cursor for a moment, then drags it across the screen 4–6 times before releasing. Click → `mad` + instant chase. |

All species also walk a vague "comfort ring" (110–190 px) around the cursor
in free mode — not stuck to it, just in the neighbourhood.

### Reactions are skipped for:
- Eggs (`is_hatched === false`) — clicks, proximity, and typing all no-op.
- Dead pets (silhouette stays frozen).
- Paddock mode (`MODE !== 'free'`) — mousemove, click, and input listeners
  aren't attached at all.

### Vowel replacement

Each species has a favourite vowel:

```js
const PET_FAVORITE = {
    turtle:  { letter: 'o', emoji: '🪷' },   // lilypad
    dog:     { letter: 'i', emoji: '🦴' },   // bone
    chicken: { letter: 'e', emoji: '🥚' },   // egg
    goose:   { letter: 'a', emoji: '🥖' },   // bread
};
```

In **free mode**, the widget attaches a document-wide `input` event listener
(capture phase). When you type a single character (no paste, no backspace,
no IME composition) into an `<input type="text|search|email|url">` or
`<textarea>` and that character matches your pet's favourite letter, there's
a 55 % chance it queues a `petPendingReplace` job.

`petCrawlStep` then sees the pending job and hands off to
`petTravelToInput()`, which:

1. Locates the **latest** matching letter in the input's current value.
2. Calls `petLetterPos(input, charIndex)` — a helper that builds an invisible
   mirror `<div>` copying the input's `boxSizing`, padding, border, fonts,
   line-height, letter-spacing, text-align and white-space rules; wraps the
   target character in a `<span>`; appends to the DOM long enough to read the
   span's `getBoundingClientRect()`; subtracts `input.scrollLeft/Top` to
   handle scrolled inputs; then returns the letter's viewport coordinates
   plus its width/height.
3. Aims the sprite so its bottom edge sits directly on top of the letter
   (`tx = lp.x + lp.width/2 - 23; ty = lp.y - 44;`).
4. Walks the pet there at the species' usual speed.

On arrival, `petArriveAtInput()`:
- If the letter is gone (input was sent/cleared), cancels and returns to
  normal follow.
- Otherwise, hops, and does the in-place string replacement:
  ```js
  input.value = val.slice(0, idx) + p.emoji + val.slice(idx + 1);
  ```
  while preserving the caret position.

This is what the user sees as: *"type a vowel, the pet sprints over and hops
on it, the letter flips into its favourite food."*

---

## `/pet/` page (pet zone)

`templates/pet.html` is a small standalone page with a single centered hero
card:

- **No pet** → species grid + name input (required, permanent) + Adopt button.
- **Egg stage** → CSS-drawn themed egg (or incubator for dog), hatch countdown.
- **Hatched** → pet emoji with a subtle bob, species tag, alive-counter.
- **Starved** → gray silhouette, `Adopt a new pet` button.

Alive cards always show the **Feed 🥣** button, and an additional
**Drop bone 🦴** button when the pet is a hatched dog. Below that, a small
dashed **Cheat 18h (test)** button calls `/api/pet/cheat` — useful for
watching the hatch/starve lifecycle without waiting.

A "→ Community" link sits in the top bar so users can bounce between the
two apps; the session cookie means no re-login.

The paddock (grassy pen) is injected by the widget into this page too, so
the pet is visible pacing as you manage it.

---

## Deployment

### VPS services

```
# /etc/systemd/system/pet.service
[Unit]
Description=Pet Flask App
After=network.target

[Service]
WorkingDirectory=/opt/apps/pet
Environment=SECRET_KEY=<shared with community>
ExecStart=/opt/apps/pet/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:5010 --timeout 120 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### nginx

```
location /pet/ {
    proxy_pass http://127.0.0.1:5010/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    client_max_body_size 0;
}
```

### Deploy

```bash
rsync -av --exclude='pet.db' --exclude='__pycache__' --exclude='venv' \
  pet/ root@5.161.189.215:/opt/apps/pet/
ssh root@5.161.189.215 'systemctl restart pet'
```

---

## Local development

```bash
cd pet
python3 -m venv venv
venv/bin/pip install -r requirements.txt
SECRET_KEY=dev venv/bin/python app.py   # listens on 0.0.0.0:5010
```

Because sessions are shared via `SECRET_KEY`, running `community/` on port
5004 and `pet/` on port 5010 against the same cookie jar (both `localhost`,
both path=/) lets you log in once and browse both.

---

## Design notes

- **Why extract into its own app?** The community app was getting tangled
  with pet-specific routes, models, HTML, CSS and JS. Isolating the pet into
  `/pet/` on port 5010 keeps community focused on social features and lets
  the pet feature iterate on its own deploy cadence.
- **Why a reusable widget?** The pet is supposed to feel like a companion
  that follows you around. A single `<script src="/pet/static/pet-widget.js">`
  means any new page on the site can opt in.
- **Why two modes?** On `/community/` the whole page is the pet's playground.
  On `/pet/` the hero card is already busy, so a small grassy pen keeps the
  pet visible but contained.
- **Why a one-time community.db → pet.db import?** Pre-existing pets would
  otherwise vanish at the split. The import runs inside `init_db()` only if
  `pet.db` is empty, so it's idempotent.
