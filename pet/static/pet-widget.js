/* Pet widget — drop onto any page to have your pet roam it.
 * Provides window.petWidget = { refresh(), petDropFood(), getPet() }.
 * Other pages (e.g. /pet/) can call petWidget.refresh() after mutating the pet
 * via the API so the creature reflects the change instantly.
 */
(function () {
    if (window.__petWidgetLoaded) return;
    window.__petWidgetLoaded = true;

    const API = '/pet';

    // Mode: on /pet/ the pet lives in a small grassy paddock and paces;
    // elsewhere (community etc.) it roams the whole page and reacts to you.
    const MODE = window.location.pathname.startsWith('/pet/') ? 'paddock' : 'free';

    // ── CSS ──
    if (!document.getElementById('pet-widget-css')) {
        const link = document.createElement('link');
        link.id = 'pet-widget-css';
        link.rel = 'stylesheet';
        link.href = API + '/static/pet-widget.css';
        document.head.appendChild(link);
    }

    // ── Twemoji (load once, globally) ──
    function loadTwemoji() {
        return new Promise((resolve) => {
            if (window.twemoji) return resolve(window.twemoji);
            const existing = document.getElementById('pet-widget-twemoji');
            if (existing) { existing.addEventListener('load', () => resolve(window.twemoji)); return; }
            const s = document.createElement('script');
            s.id = 'pet-widget-twemoji';
            s.src = 'https://cdn.jsdelivr.net/npm/@twemoji/api@15.1.0/dist/twemoji.min.js';
            s.crossOrigin = 'anonymous';
            s.onload = () => resolve(window.twemoji);
            s.onerror = () => resolve(null);
            document.head.appendChild(s);
        });
    }

    function twemojify(el) {
        if (!el || typeof window.twemoji === 'undefined') return;
        try { window.twemoji.parse(el, { folder: 'svg', ext: '.svg' }); } catch (_) {}
    }

    // ── Pet sprite stages (loaded from pet-sprites.js) ──
    function loadSprites() {
        return new Promise((resolve) => {
            if (window.PET_SPRITES) return resolve(window.PET_SPRITES);
            const existing = document.getElementById('pet-widget-sprites');
            if (existing) {
                existing.addEventListener('load', () => resolve(window.PET_SPRITES));
                return;
            }
            const s = document.createElement('script');
            s.id = 'pet-widget-sprites';
            s.src = API + '/static/pet-sprites.js';
            s.onload = () => resolve(window.PET_SPRITES);
            s.onerror = () => resolve(null);
            document.head.appendChild(s);
        });
    }

    function petStageFor(p) {
        if (!p || typeof window.PET_SPRITES_PICK !== 'function') return null;
        return window.PET_SPRITES_PICK(p.pet_type, p.seconds_alive);
    }

    // ── DOM injection ──
    function buildCreatureMarkup(inPaddock) {
        const cls = inPaddock ? 'pet-creature in-paddock' : 'pet-creature';
        return '<div id="petCreature" class="' + cls + '" style="display:none">' +
                   '<div class="pet-sprite" id="petSprite">' +
                     '<span class="pet-emoji" id="petEmoji"></span>' +
                   '</div>' +
                   '<div class="pet-label" id="petLabel"></div>' +
               '</div>';
    }

    function ensureDOM() {
        if (MODE === 'paddock') {
            if (!document.getElementById('petPaddock')) {
                const p = document.createElement('div');
                p.id = 'petPaddock';
                p.className = 'pet-paddock';
                p.style.display = 'none';
                p.innerHTML =
                    '<div class="pet-paddock-title" id="petPaddockTitle"></div>' +
                    '<div class="pet-paddock-floor" id="petPaddockFloor">' +
                      buildCreatureMarkup(true) +
                    '</div>' +
                    '<div class="pet-paddock-actions" id="petPaddockActions"></div>';
                document.body.appendChild(p);
            }
            return;
        }
        // Free mode — full-screen roaming
        if (!document.getElementById('petCreature')) {
            const wrap = document.createElement('div');
            wrap.innerHTML = buildCreatureMarkup(false);
            document.body.appendChild(wrap.firstElementChild);
        }
        if (!document.getElementById('petFakeCursor')) {
            const fc = document.createElement('div');
            fc.id = 'petFakeCursor';
            fc.className = 'pet-fake-cursor';
            fc.style.display = 'none';
            fc.innerHTML =
                '<svg width="20" height="20" viewBox="0 0 16 16">' +
                  '<path d="M1 1 L1 12 L4 9 L7 15 L9 14 L6 8 L11 8 Z" ' +
                        'fill="#ecdfc8" stroke="#241f15" stroke-width="1" stroke-linejoin="round"/>' +
                '</svg>';
            document.body.appendChild(fc);
        }
        if (!document.getElementById('petFoodLayer')) {
            const f = document.createElement('div');
            f.id = 'petFoodLayer';
            f.className = 'pet-food-layer';
            document.body.appendChild(f);
        }
        if (!document.getElementById('petDropFoodBtn')) {
            const b = document.createElement('button');
            b.id = 'petDropFoodBtn';
            b.type = 'button';
            b.className = 'pet-drop-btn';
            b.style.display = 'none';
            b.innerHTML = '🦴 <span>Drop bone</span>';
            b.addEventListener('click', (e) => { e.preventDefault(); petDropFoodPublic(); });
            document.body.appendChild(b);
        }
    }

    const $ = (id) => document.getElementById(id);

    // ── Constants ──
    const PET_EMOJI  = { chicken: '🐓', goose: '🪿', dog: '🐕', turtle: '🐢' };
    const PET_NAMES  = { chicken: 'Chicken', goose: 'Goose', dog: 'Dog', turtle: 'Turtle' };
    // Favorite foods each pet can't resist — sneaked into your typing
    const PET_FAVORITE = {
        turtle:  { letter: 'o', emoji: '🪷' },  // lilypad
        dog:     { letter: 'i', emoji: '🦴' },  // bone
        chicken: { letter: 'e', emoji: '🥚' },  // egg
        goose:   { letter: 'a', emoji: '🥖' },  // bread
    };

    // ── State ──
    let pet = null;
    let petPendingReplace = null;     // {input, letter, emoji}
    let petTraveling = false;         // true while pet is walking to an input
    let petCrawlTimer = null;
    let petTickTimer = null;
    let petPos = { x: 100, y: 100 };
    let petBusy = false;
    let petLastBotherAt = 0;
    let petChasing = false;
    let petChaseRaf = null;
    let petChaseStart = 0;
    let petChaseHold = 0;
    let petLastTick = 0;
    let petCaught = false;
    let petCatchTimer = null;
    let petLastChaseAt = 0;
    let petFoodItems = [];
    let petEggPlaced = false;
    let petDeadPlaced = false;        // keeps the silhouette anchored on fresh page loads
    let petCurrentStage = -1;         // track stage transitions for re-render
    let petMouseX = typeof window !== 'undefined' ? window.innerWidth / 2 : 0;
    let petMouseY = typeof window !== 'undefined' ? window.innerHeight / 2 : 0;

    // ── Helpers ──
    function petVisualPos(el) {
        const t = getComputedStyle(el).transform;
        if (!t || t === 'none') return { x: petPos.x, y: petPos.y };
        try {
            const m = new DOMMatrixReadOnly(t);
            return { x: m.m41, y: m.m42 };
        } catch (_) { return { x: petPos.x, y: petPos.y }; }
    }
    function petSyncPosToVisual() {
        const el = $('petCreature');
        const v = petVisualPos(el);
        petPos.x = v.x; petPos.y = v.y;
        return v;
    }
    function petHop() {
        const s = $('petSprite');
        s.classList.remove('hop');
        void s.offsetWidth;
        s.classList.add('hop');
    }

    // ── Walk dispatcher: pick pace (paddock) or crawl (free) ──
    function petWalkStep() {
        if (MODE === 'paddock') return petPaceStep();
        return petCrawlStep();
    }

    // ── Paddock pacing: back-and-forth along the floor ──
    let paddockDir = 'right';
    function petPaceStep() {
        petCrawlTimer = null;
        if (!pet || !pet.alive || petBusy) return;
        if (pet.is_hatched === false) return;       // eggs don't pace
        if (pet.pet_type === 'dog' && petFoodItems.length > 0) { petDogSeekFood(); return; }
        const floor = $('petPaddockFloor');
        if (!floor) return;
        const rect = floor.getBoundingClientRect();
        const spriteW = 36, spriteH = 38;
        const margin = 10;
        const tx = (paddockDir === 'right')
            ? Math.max(margin, rect.width - margin - spriteW)
            : margin;
        const ty = Math.max(8, rect.height - spriteH - 4);
        const dx = tx - petPos.x, dy = ty - petPos.y;
        const dist = Math.hypot(dx, dy);
        const speed = pet.pet_type === 'turtle' ? 20 : (pet.pet_type === 'dog' ? 70 : 42);
        const dur = Math.max(1.0, Math.min(6, dist / speed));
        const sprite = $('petSprite');
        sprite.classList.toggle('facing-left', paddockDir === 'left');
        const el = $('petCreature');
        el.style.transition = 'transform ' + dur.toFixed(2) + 's linear';
        el.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        petPos.x = tx; petPos.y = ty;
        paddockDir = (paddockDir === 'right') ? 'left' : 'right';
        petCrawlTimer = setTimeout(petPaceStep, (dur + 0.35 + Math.random() * 0.5) * 1000);
    }

    // ── Paddock visibility + title + Drop-bone button ──
    function petUpdatePaddock() {
        if (MODE !== 'paddock') return;
        const paddock = $('petPaddock');
        if (!paddock) return;
        if (!pet) { paddock.style.display = 'none'; return; }
        paddock.style.display = 'block';
        const hatched = pet.is_hatched !== false;
        const name = pet.name || PET_NAMES[pet.pet_type] || 'Pet';
        const tag = pet.alive ? (hatched ? pet.pet_type : 'egg') : 'starved';
        const title = $('petPaddockTitle');
        if (title) {
            title.innerHTML = '<span>' + (name.replace(/[<>&]/g,'')) + '</span>' +
                              '<span class="small">— ' + tag + '</span>';
        }
        const actions = $('petPaddockActions');
        if (actions) {
            if (pet.pet_type === 'dog' && pet.alive && hatched) {
                actions.innerHTML = '<button class="pet-paddock-btn" id="paddockDropBtn" type="button">Drop bone 🦴</button>';
                const btn = $('paddockDropBtn');
                if (btn) {
                    btn.addEventListener('click', (e) => {
                        e.preventDefault();
                        petDropFoodPublic();
                    });
                    twemojify(btn);
                }
            } else {
                actions.innerHTML = '';
            }
        }
    }

    // ── Main loop ──
    function petCrawlStep() {
        petCrawlTimer = null;
        if (!pet || !pet.alive || petBusy || petChasing) return;
        if (pet.is_hatched === false) return;
        // Distracted by typing — go steal a vowel instead of following the cursor
        if (petPendingReplace && !petTraveling) { petTravelToInput(); return; }
        const sprite = $('petSprite');
        const el = $('petCreature');
        if (pet.pet_type === 'dog' && petFoodItems.length > 0) { petDogSeekFood(); return; }
        if (pet.pet_type === 'goose') {
            const sinceChase = (Date.now() - petLastChaseAt) / 1000;
            if (sinceChase > 30 && Math.random() < 0.14) { petStartChase(); return; }
        }
        const radius = 110 + Math.random() * 80;
        const angle = Math.random() * Math.PI * 2;
        let tx = petMouseX + Math.cos(angle) * radius - 23;
        let ty = petMouseY + Math.sin(angle) * radius - 23;
        tx = Math.max(20, Math.min(window.innerWidth - 60, tx));
        ty = Math.max(60, Math.min(window.innerHeight - 80, ty));
        const dx = tx - petPos.x, dy = ty - petPos.y;
        const dist = Math.hypot(dx, dy);
        const speed = pet.pet_type === 'turtle' ? 35 : (pet.pet_type === 'dog' ? 150 : 95);
        const dur = Math.max(0.6, Math.min(5, dist / speed));
        sprite.classList.toggle('facing-left', dx < 0);
        el.style.transition = 'transform ' + dur.toFixed(2) + 's linear';
        el.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        petPos.x = tx; petPos.y = ty;
        petCrawlTimer = setTimeout(petCrawlStep, (dur + 0.25 + Math.random() * 0.8) * 1000);
    }

    // ── Goose chase ──
    function petStartChase() {
        if (!pet || !pet.alive || petChasing) return;
        petChasing = true;
        petCaught = false;
        petLastChaseAt = Date.now();
        petChaseStart = Date.now();
        petChaseHold = 0;
        petLastTick = 0;
        const el = $('petCreature');
        const v = petSyncPosToVisual();
        el.style.transition = 'none';
        el.style.transform = 'translate(' + v.x + 'px, ' + v.y + 'px)';
        $('petSprite').classList.add('chasing');
        if (petChaseRaf) cancelAnimationFrame(petChaseRaf);
        petChaseRaf = requestAnimationFrame(petChaseTick);
    }

    function petChaseTick(ts) {
        if (!petChasing) return;
        if (!pet || !pet.alive) { petEndChase(); return; }
        if (petCaught) { petChaseRaf = requestAnimationFrame(petChaseTick); return; }
        const dt = petLastTick ? Math.min(0.06, (ts - petLastTick) / 1000) : 0.016;
        petLastTick = ts;
        const elapsed = (Date.now() - petChaseStart) / 1000;
        const targetX = petMouseX - 22;
        const targetY = petMouseY - 22;
        const dx = targetX - petPos.x, dy = targetY - petPos.y;
        const dist = Math.hypot(dx, dy);
        const speed = 260;
        const step = Math.min(speed * dt, dist);
        if (dist > 0) { petPos.x += dx / dist * step; petPos.y += dy / dist * step; }
        const sprite = $('petSprite');
        sprite.classList.toggle('facing-left', dx < 0);
        const el = $('petCreature');
        el.style.transition = 'none';
        el.style.transform = 'translate(' + petPos.x + 'px, ' + petPos.y + 'px)';
        if (dist < 32) { petChaseHold += dt; if (petChaseHold > 0.45) { petCatch(); return; } }
        else petChaseHold = 0;
        if (elapsed > 10) { petEndChase(); return; }
        petChaseRaf = requestAnimationFrame(petChaseTick);
    }

    function petCatch() {
        petCaught = true;
        const fc = $('petFakeCursor');
        document.body.classList.add('pet-hijacked');
        fc.classList.add('locked');
        fc.style.display = 'block';
        fc.style.transition = 'none';
        fc.style.transform = 'translate(' + petMouseX + 'px, ' + petMouseY + 'px)';
        petCatchTimer = setTimeout(() => petDragCursor(4 + Math.floor(Math.random() * 3)), 400);
    }

    function petDragCursor(movesLeft) {
        if (!petCaught) return;
        if (movesLeft <= 0) { petEndChase(); return; }
        const fc = $('petFakeCursor');
        const el = $('petCreature');
        const sprite = $('petSprite');
        const tx = 60 + Math.random() * Math.max(1, window.innerWidth - 120);
        const ty = 80 + Math.random() * Math.max(1, window.innerHeight - 160);
        const dur = 0.5 + Math.random() * 0.55;
        sprite.classList.toggle('facing-left', tx - 23 < petPos.x);
        fc.style.transition = 'transform ' + dur + 's ease-in-out';
        fc.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        el.style.transition = 'transform ' + dur + 's ease-in-out';
        el.style.transform = 'translate(' + (tx - 23) + 'px, ' + (ty - 23) + 'px)';
        petPos.x = tx - 23; petPos.y = ty - 23;
        petMouseX = tx; petMouseY = ty;
        petCatchTimer = setTimeout(() => petDragCursor(movesLeft - 1), dur * 1000 + 60);
    }

    function petEndChase(silent) {
        petChasing = false;
        petCaught = false;
        if (petChaseRaf) { cancelAnimationFrame(petChaseRaf); petChaseRaf = null; }
        if (petCatchTimer) { clearTimeout(petCatchTimer); petCatchTimer = null; }
        petLastTick = 0;
        const sprite = $('petSprite');
        const fc = $('petFakeCursor');
        if (sprite) sprite.classList.remove('chasing');
        document.body.classList.remove('pet-hijacked');
        if (fc) { fc.classList.remove('locked'); fc.style.display = 'none'; }
        if (!silent && pet && pet.alive && !petCrawlTimer) petCrawlTimer = setTimeout(petWalkStep, 500);
    }

    // ── Bother reactions ──
    function petProximityCheck() {
        if (MODE !== 'free') return;           // no cursor reactions in the paddock
        if (!pet || !pet.alive || petBusy || petChasing) return;
        if (pet.is_hatched === false) return;   // eggs don't flee
        // Only the turtle reacts to cursor proximity; chicken jumps on click only.
        if (pet.pet_type !== 'turtle') return;
        const now = Date.now();
        if (now - petLastBotherAt < 400) return;
        const cx = petPos.x + 22, cy = petPos.y + 22;
        const d = Math.hypot(petMouseX - cx, petMouseY - cy);
        if (d < 80) { petLastBotherAt = now; petReact(); }
    }
    function petReact() {
        if (petBusy) return;
        if (!pet || pet.is_hatched === false) return;
        if (pet.pet_type === 'turtle') petTurtleShell();
        else if (pet.pet_type === 'chicken') petChickenJump();
    }
    function petPauseCrawl() {
        if (petCrawlTimer) { clearTimeout(petCrawlTimer); petCrawlTimer = null; }
        const el = $('petCreature');
        const v = petSyncPosToVisual();
        el.style.transition = 'none';
        el.style.transform = 'translate(' + v.x + 'px, ' + v.y + 'px)';
    }
    function petTurtleShell() {
        petBusy = true;
        petPauseCrawl();
        $('petSprite').classList.add('in-shell');
        setTimeout(() => {
            $('petSprite').classList.remove('in-shell');
            petBusy = false;
            if (pet && pet.alive) petWalkStep();
        }, 3000);
    }
    function petChickenJump() {
        petBusy = true;
        if (petCrawlTimer) { clearTimeout(petCrawlTimer); petCrawlTimer = null; }
        petSyncPosToVisual();
        const cx = petPos.x + 22, cy = petPos.y + 22;
        const dx = cx - petMouseX, dy = cy - petMouseY;
        const mag = Math.hypot(dx, dy) || 1;
        const jumpDist = 200 + Math.random() * 140;
        let tx = petPos.x + (dx / mag) * jumpDist + (Math.random() * 80 - 40);
        let ty = petPos.y + (dy / mag) * jumpDist + (Math.random() * 80 - 40);
        tx = Math.max(20, Math.min(window.innerWidth - 80, tx));
        ty = Math.max(60, Math.min(window.innerHeight - 100, ty));
        const sprite = $('petSprite');
        sprite.classList.toggle('facing-left', tx < petPos.x);
        sprite.classList.add('panic');
        const el = $('petCreature');
        el.style.transition = 'transform 0.55s cubic-bezier(0.2, -0.1, 0.3, 1.2)';
        el.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        petPos.x = tx; petPos.y = ty;
        setTimeout(() => {
            sprite.classList.remove('panic');
            petBusy = false;
            if (pet && pet.alive) petWalkStep();
        }, 900);
    }

    // ── Chicken sound on click ──
    let petChickenAudio = null;
    function petPlayChickenSound() {
        try {
            if (!petChickenAudio) {
                petChickenAudio = new Audio(API + '/static/sounds/chicken_alarm.mp3');
                petChickenAudio.volume = 0.7;
                petChickenAudio.preload = 'auto';
            }
            petChickenAudio.currentTime = 0;
            const p = petChickenAudio.play();
            if (p && p.catch) p.catch(() => {});
        } catch (_) {}
    }

    function petOnClick() {
        if (MODE !== 'free') return;            // no reactions in the paddock
        if (!pet || !pet.alive) return;
        if (pet.is_hatched === false) return;   // eggs don't react to clicks
        if (pet.pet_type === 'chicken') {
            petPlayChickenSound();
            if (!petBusy) petChickenJump();
            return;
        }
        if (pet.pet_type === 'goose') {
            if (petChasing) return;
            const sprite = $('petSprite');
            sprite.classList.remove('mad');
            void sprite.offsetWidth;
            sprite.classList.add('mad');
            setTimeout(() => sprite.classList.remove('mad'), 900);
            petStartChase();
            return;
        }
        if (petBusy || petChasing) return;
        if (pet.pet_type === 'turtle') petReact();
    }

    // ── Dog food ──
    function petClearFood() {
        petFoodItems.forEach((f) => { if (f.el && f.el.parentNode) f.el.parentNode.removeChild(f.el); });
        petFoodItems = [];
    }

    function petDropFoodPublic() {
        if (!pet || !pet.alive || pet.pet_type !== 'dog' || pet.is_hatched === false) return;
        if (petFoodItems.length >= 6) return;
        let x, y, parent;
        if (MODE === 'paddock') {
            const floor = $('petPaddockFloor');
            if (!floor) return;
            const rect = floor.getBoundingClientRect();
            const spriteW = 30;
            x = 12 + Math.random() * Math.max(1, rect.width - 24 - spriteW);
            y = rect.height - 22;               // sitting on the ground
            parent = floor;
        } else {
            x = 60 + Math.random() * Math.max(1, window.innerWidth - 120);
            y = 120 + Math.random() * Math.max(1, window.innerHeight - 220);
            parent = $('petFoodLayer');
        }
        const el = document.createElement('div');
        el.className = 'pet-food';
        el.textContent = '🦴';
        el.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
        parent.appendChild(el);
        twemojify(el);
        petFoodItems.push({ x, y, el });
        if (!petChasing && !petBusy) {
            if (petCrawlTimer) { clearTimeout(petCrawlTimer); petCrawlTimer = null; }
            petDogSeekFood();
        }
        petHop();
    }

    function petDogSeekFood() {
        petCrawlTimer = null;
        if (!pet || !pet.alive || pet.pet_type !== 'dog' || petFoodItems.length === 0) {
            if (pet && pet.alive) petWalkStep();
            return;
        }
        let best = null, bestDist = Infinity;
        const cx = petPos.x + 22, cy = petPos.y + 22;
        for (const f of petFoodItems) {
            const d = Math.hypot(f.x - cx, f.y - cy);
            if (d < bestDist) { bestDist = d; best = f; }
        }
        if (!best) { petWalkStep(); return; }
        const tx = best.x - 22, ty = best.y - 22;
        const dur = Math.max(0.5, Math.min(4, bestDist / 220));
        const sprite = $('petSprite');
        sprite.classList.toggle('facing-left', tx < petPos.x);
        const el = $('petCreature');
        el.style.transition = 'transform ' + dur.toFixed(2) + 's linear';
        el.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        petPos.x = tx; petPos.y = ty;
        petCrawlTimer = setTimeout(() => petDogEat(best), dur * 1000 + 60);
    }

    async function petDogEat(food) {
        if (food.el && food.el.parentNode) food.el.parentNode.removeChild(food.el);
        petFoodItems = petFoodItems.filter((f) => f !== food);
        petHop();
        try {
            const r = await fetch(API + '/api/pet/feed', { method: 'POST' });
            if (r.ok) {
                const d = await r.json();
                pet = d.pet;
                window.dispatchEvent(new CustomEvent('pet:update', { detail: pet }));
            }
        } catch (_) {}
        petCrawlTimer = setTimeout(() => {
            if (petFoodItems.length > 0) petDogSeekFood();
            else petWalkStep();
        }, 350);
    }

    // Coord bounds for random placement — viewport in free mode, paddock floor otherwise.
    function petPlacementBounds() {
        if (MODE === 'paddock') {
            const f = $('petPaddockFloor');
            if (f) {
                const r = f.getBoundingClientRect();
                return {
                    xMin: 8,  xMax: Math.max(9, r.width  - 40),
                    yMin: 4,  yMax: Math.max(5, r.height - 40),
                };
            }
        }
        return {
            xMin: 120, xMax: Math.max(121, window.innerWidth  - 240),
            yMin: 140, yMax: Math.max(141, window.innerHeight - 280),
        };
    }

    // ── Egg placement (no walking while unhatched) ──
    function petPlaceEgg() {
        if (petEggPlaced) return;
        const b = petPlacementBounds();
        const tx = b.xMin + Math.random() * (b.xMax - b.xMin);
        const ty = b.yMin + Math.random() * (b.yMax - b.yMin);
        const el = $('petCreature');
        el.style.transition = 'none';
        el.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        petPos.x = tx; petPos.y = ty;
        petEggPlaced = true;
    }

    // Dead silhouette: keep it wherever the pet last stood; if no position yet
    // (fresh page load after death), drop it in a random spot and leave it.
    function petPlaceDead() {
        if (petDeadPlaced) return;
        const el = $('petCreature');
        const v = petVisualPos(el);
        let tx, ty;
        if (Math.abs(v.x) > 1 || Math.abs(v.y) > 1) {
            tx = v.x; ty = v.y;
        } else {
            const b = petPlacementBounds();
            tx = b.xMin + Math.random() * (b.xMax - b.xMin);
            ty = b.yMin + Math.random() * (b.yMax - b.yMin);
        }
        el.style.transition = 'none';
        el.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        petPos.x = tx; petPos.y = ty;
        petDeadPlaced = true;
    }

    // ── Apply pet state to the sprite ──
    function petApplyCreature() {
        const el = $('petCreature');
        const sprite = $('petSprite');
        const emoji = $('petEmoji');
        const label = $('petLabel');
        if (!pet) {
            el.style.display = 'none';
            if (petCrawlTimer) { clearTimeout(petCrawlTimer); petCrawlTimer = null; }
            if (petTickTimer) { clearInterval(petTickTimer); petTickTimer = null; }
            petEndChase(true);
            petClearFood();
            petBusy = false;
            petEggPlaced = false;
            petDeadPlaced = false;
            petPendingReplace = null;
            petTraveling = false;
            sprite.classList.remove('in-shell', 'panic', 'chasing', 'mad');
            petUpdatePaddock();
            const dropBtn = document.getElementById('petDropFoodBtn');
            if (dropBtn) dropBtn.style.display = 'none';
            return;
        }
        // Adoption of a new pet (was dead, now alive) — forget the old grave spot.
        if (pet.alive) petDeadPlaced = false;
        const hatched = pet.is_hatched !== false;
        el.style.display = 'block';
        // Pick the active growth stage and apply its emoji + scale.
        const stage = petStageFor(pet);
        const stageEmoji = (stage && stage.emoji) ||
            (hatched ? (PET_EMOJI[pet.pet_type] || '🐾') : '🥚');
        const stageScale = (stage && stage.scale) || 1;
        if (hatched && stage && stage.stage > 0) {
            emoji.textContent = stageEmoji;
            // Once hatched, emoji.dataset.pet stays as the species so the
            // walking-animation (chicken-peck/dog-trot/etc.) keeps playing.
            emoji.dataset.pet = pet.pet_type;
            petEggPlaced = false;
        } else {
            emoji.textContent = '🥚';
            emoji.dataset.pet = 'egg';
        }
        sprite.style.setProperty('--growth', stageScale);
        petCurrentStage = stage ? stage.stage : -1;
        twemojify(emoji);
        emoji.dataset.color = pet.color || 'natural';
        sprite.classList.toggle('dead', !pet.alive);
        sprite.classList.remove('in-shell', 'panic', 'chasing', 'mad');
        petBusy = false;
        label.textContent = (pet.name || PET_NAMES[pet.pet_type]) +
            (pet.alive ? (hatched ? '' : ' (egg)') : ' (starved)');
        if (pet.pet_type !== 'dog' || !hatched) petClearFood();
        petUpdatePaddock();
        // Free-mode floating Drop Bone button (community etc.)
        if (MODE === 'free') {
            const btn = $('petDropFoodBtn');
            if (btn) {
                if (pet.pet_type === 'dog' && pet.alive && hatched) {
                    if (btn.style.display === 'none' || !btn.style.display) {
                        btn.style.display = 'inline-flex';
                        twemojify(btn);
                    }
                } else {
                    btn.style.display = 'none';
                }
            }
        }
        if (pet.alive && hatched) {
            if (!petCrawlTimer && !petChasing) petWalkStep();
            if (!petTickTimer) petTickTimer = setInterval(petTick, 1000);
        } else if (pet.alive && !hatched) {
            if (petCrawlTimer) { clearTimeout(petCrawlTimer); petCrawlTimer = null; }
            petEndChase(true);
            petPlaceEgg();
            if (!petTickTimer) petTickTimer = setInterval(petTick, 1000);
        } else {
            if (petCrawlTimer) { clearTimeout(petCrawlTimer); petCrawlTimer = null; }
            if (petTickTimer) { clearInterval(petTickTimer); petTickTimer = null; }
            petEndChase(true);
            petClearFood();
            petPlaceDead();
        }
    }

    function petTick() {
        if (!pet || !pet.alive) return;
        pet.seconds_remaining = Math.max(0, (pet.seconds_remaining || 0) - 1);
        pet.seconds_alive = (pet.seconds_alive || 0) + 1;
        if (pet.seconds_to_hatch && pet.seconds_to_hatch > 0) {
            pet.seconds_to_hatch -= 1;
            if (pet.seconds_to_hatch <= 0) {
                pet.is_hatched = true;
                pet.seconds_to_hatch = 0;
                petApplyCreature();
                window.dispatchEvent(new CustomEvent('pet:update', { detail: pet }));
            }
        }
        if (pet.seconds_remaining === 0) {
            pet.alive = false;
            petApplyCreature();
            window.dispatchEvent(new CustomEvent('pet:update', { detail: pet }));
            return;
        }
        // Crossed a growth-stage boundary? Re-render so the sprite swaps in.
        const cur = petStageFor(pet);
        if (cur && cur.stage !== petCurrentStage) {
            petApplyCreature();
            window.dispatchEvent(new CustomEvent('pet:update', { detail: pet }));
        }
    }

    // ── Typing → vowel replacement ──
    // The pet has to travel to the input and hop onto the letter to actually
    // replace it. If the text is sent/cleared before the pet arrives, nothing
    // happens. Each pet type has one favorite vowel (PET_FAVORITE).
    function petHandleTyping(e) {
        if (MODE !== 'free') return;                 // pet stays in paddock, no word-hopping
        if (!pet || !pet.alive || pet.is_hatched === false) return;
        if (petChasing) return;                      // finish chasing first
        if (e && e.isComposing) return;              // IME compose mid-stroke
        const t = e && e.target;
        if (!t) return;
        const tag = t.tagName;
        if (tag !== 'INPUT' && tag !== 'TEXTAREA') return;
        if (tag === 'INPUT' && t.type && !['text', 'search', 'email', 'url'].includes(t.type)) return;
        if (e.inputType && e.inputType !== 'insertText') return;   // skip paste/delete
        if (!e.data || e.data.length !== 1) return;
        const fav = PET_FAVORITE[pet.pet_type];
        if (!fav) return;
        if (e.data.toLowerCase() !== fav.letter) return;
        if (Math.random() > 0.55) return;            // only some vowels trigger a trip
        petPendingReplace = { input: t, letter: fav.letter, emoji: fav.emoji };
        if (!petTraveling && !petChasing && !petBusy) petTravelToInput();
    }

    // Viewport-coords of a specific character inside a text input or textarea.
    // Uses a hidden mirror div that copies the input's typography + box so the
    // character's span lands exactly where it would inside the input.
    function petLetterPos(input, charIndex) {
        try {
            if (!input || !input.isConnected) return null;
            const value = input.value || '';
            if (charIndex < 0 || charIndex >= value.length) return null;
            const style = getComputedStyle(input);
            const rect = input.getBoundingClientRect();
            const isTextarea = input.tagName === 'TEXTAREA';
            const mirror = document.createElement('div');
            const props = [
                'boxSizing', 'width', 'height',
                'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
                'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
                'fontStyle', 'fontVariant', 'fontWeight', 'fontStretch', 'fontSize',
                'lineHeight', 'fontFamily', 'textAlign', 'textTransform',
                'letterSpacing', 'wordSpacing', 'textIndent', 'tabSize'
            ];
            props.forEach((p) => { if (style[p]) mirror.style[p] = style[p]; });
            mirror.style.position = 'absolute';
            mirror.style.visibility = 'hidden';
            mirror.style.top = '0';
            mirror.style.left = '0';
            mirror.style.pointerEvents = 'none';
            if (isTextarea) {
                mirror.style.whiteSpace = 'pre-wrap';
                mirror.style.wordWrap = 'break-word';
                mirror.style.overflowWrap = 'break-word';
            } else {
                mirror.style.whiteSpace = 'pre';     // single-line, preserve spaces
            }
            const before = value.slice(0, charIndex);
            const char = value.slice(charIndex, charIndex + 1);
            const after = value.slice(charIndex + 1);
            mirror.appendChild(document.createTextNode(before));
            const span = document.createElement('span');
            span.textContent = char;
            mirror.appendChild(span);
            // Trailing content matters for textarea wrapping — pad with a
            // non-space so the line the span lives on doesn't collapse.
            mirror.appendChild(document.createTextNode(after + '.'));
            document.body.appendChild(mirror);
            const spanRect = span.getBoundingClientRect();
            const mirrorRect = mirror.getBoundingClientRect();
            const scrollLeft = input.scrollLeft || 0;
            const scrollTop  = input.scrollTop  || 0;
            const relX = spanRect.left - mirrorRect.left;
            const relY = spanRect.top  - mirrorRect.top;
            const width = spanRect.width;
            const height = spanRect.height;
            document.body.removeChild(mirror);
            // Reject if the letter is scrolled out of the input's visible area.
            const visibleX = relX - scrollLeft;
            const visibleY = relY - scrollTop;
            if (visibleX < 0 || visibleY < 0 ||
                visibleX > rect.width || visibleY > rect.height) return null;
            return {
                x: rect.left + visibleX,
                y: rect.top  + visibleY,
                width: width,
                height: height,
            };
        } catch (_) { return null; }
    }

    function petTravelToInput() {
        if (!petPendingReplace) return;
        const input = petPendingReplace.input;
        if (!input || !input.isConnected) { petPendingReplace = null; return; }
        const rect = input.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) { petPendingReplace = null; return; }
        // Every pet lands directly above the specific letter it will replace
        // (turtle→lilypad, dog→bone, chicken→egg, goose→bread). If the letter
        // can't be located (hidden input, scrolled off, null rect), fall back
        // to the input's trailing edge.
        let tx, ty;
        const val = input.value || '';
        let idx = -1;
        for (let i = val.length - 1; i >= 0; i--) {
            if (val[i] && val[i].toLowerCase() === petPendingReplace.letter) { idx = i; break; }
        }
        const lp = idx >= 0 ? petLetterPos(input, idx) : null;
        if (lp) {
            tx = lp.x + lp.width / 2 - 23;              // sprite centered over letter
            ty = lp.y - 44;                             // hovering just above the letter
            tx = Math.max(10, Math.min(window.innerWidth - 60, tx));
            ty = Math.max(10, ty);
        } else {
            tx = rect.left + Math.max(30, rect.width - 60) - 23;
            ty = rect.top - 38;
        }
        if (petCrawlTimer) { clearTimeout(petCrawlTimer); petCrawlTimer = null; }
        petSyncPosToVisual();
        const dx = tx - petPos.x, dy = ty - petPos.y;
        const dist = Math.hypot(dx, dy);
        const speed = pet.pet_type === 'turtle' ? 35 : (pet.pet_type === 'dog' ? 150 : 95);
        const dur = Math.max(0.5, Math.min(6, dist / speed));
        const sprite = $('petSprite');
        sprite.classList.toggle('facing-left', dx < 0);
        const el = $('petCreature');
        el.style.transition = 'transform ' + dur.toFixed(2) + 's linear';
        el.style.transform = 'translate(' + tx + 'px, ' + ty + 'px)';
        petPos.x = tx; petPos.y = ty;
        petTraveling = true;
        petCrawlTimer = setTimeout(petArriveAtInput, dur * 1000 + 80);
    }

    function petArriveAtInput() {
        petCrawlTimer = null;
        petTraveling = false;
        const p = petPendingReplace;
        petPendingReplace = null;
        if (!p || !pet || !pet.alive || pet.is_hatched === false) { petCrawlStep(); return; }
        if (!p.input || !p.input.isConnected) { petCrawlStep(); return; }
        const val = p.input.value || '';
        // Replace the LATEST occurrence of the favorite letter still present
        let idx = -1;
        for (let i = val.length - 1; i >= 0; i--) {
            if (val[i] && val[i].toLowerCase() === p.letter) { idx = i; break; }
        }
        if (idx < 0) { petCrawlStep(); return; }     // sent/cleared before we arrived
        petHop();
        const selStart = p.input.selectionStart;
        const selEnd = p.input.selectionEnd;
        const newVal = val.slice(0, idx) + p.emoji + val.slice(idx + 1);
        p.input.value = newVal;
        const delta = p.emoji.length - 1;
        try {
            const ns = (selStart == null ? newVal.length : selStart + (selStart > idx ? delta : 0));
            const ne = (selEnd == null ? newVal.length : selEnd + (selEnd > idx ? delta : 0));
            p.input.setSelectionRange(ns, ne);
        } catch (_) {}
        petCrawlTimer = setTimeout(petCrawlStep, 500);
    }

    // ── Public API ──
    async function refresh() {
        try {
            const r = await fetch(API + '/api/pet');
            if (r.status === 401) { pet = null; petApplyCreature(); return null; }
            const d = await r.json();
            pet = d.pet || null;
        } catch (_) { pet = null; }
        petApplyCreature();
        return pet;
    }

    async function checkSessionAndLoad() {
        try {
            const r = await fetch(API + '/api/session');
            const d = await r.json();
            if (!d.authenticated) return;
            await refresh();
        } catch (_) {}
    }

    function init() {
        ensureDOM();
        if (MODE === 'free') {
            document.addEventListener('mousemove', (e) => {
                petMouseX = e.clientX;
                petMouseY = e.clientY;
                petProximityCheck();
            });
            document.addEventListener('input', petHandleTyping, true);
            const creature = $('petCreature');
            if (creature) creature.addEventListener('click', petOnClick);
        }
        // Paddock mode: no cursor/input reactions; pet paces and dogs eat dropped bones.
        Promise.all([loadTwemoji(), loadSprites()])
            .then(() => { checkSessionAndLoad(); });
    }

    window.petWidget = {
        refresh,
        getPet: () => pet,
        dropFood: petDropFoodPublic,
    };

    // Allow pages that mutate the pet via their own fetch to inform the widget.
    window.addEventListener('pet:refresh', refresh);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
