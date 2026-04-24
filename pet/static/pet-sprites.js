// pet-sprites.js — single source of truth for every pet's growth stages.
//
// Each pet goes through 8 stages (0..7). Stage 0 is always the egg, stage 7
// is full adulthood. The widget picks the highest stage whose `min_age_hours`
// the pet has crossed (using `seconds_alive` from /api/pet).
//
// Each entry:
//   stage          — index 0..7
//   name           — short label shown in the pet zone status (e.g. "chick")
//   emoji          — visible sprite (rendered via Twemoji for cross-OS parity)
//   scale          — CSS scale applied to the sprite via a --growth variable.
//                    1.0 = normal sprite size; 0.5 = half size; 1.15 = bigger.
//   min_age_hours  — pet must be at least this old (hours since the most
//                    recent adoption) for this stage to activate.
//
// Stage 0's `min_age_hours` is always 0; stage 1 always lines up with the
// hatch threshold (PET_HATCH_HOURS = 72 in app.py). Subsequent stages happen
// every 24 hours, so the pet reaches adulthood roughly 6 days after hatching
// (9 days from adoption).

(function () {
    if (window.PET_SPRITES) return;

    window.PET_SPRITES = {
        chicken: [
            { stage: 0, name: 'egg',       emoji: '🥚', scale: 1.00, min_age_hours: 0   },
            { stage: 1, name: 'hatchling', emoji: '🐣', scale: 0.55, min_age_hours: 72  },
            { stage: 2, name: 'chick',     emoji: '🐤', scale: 0.60, min_age_hours: 96  },
            { stage: 3, name: 'big chick', emoji: '🐥', scale: 0.72, min_age_hours: 120 },
            { stage: 4, name: 'pullet',    emoji: '🐔', scale: 0.72, min_age_hours: 144 },
            { stage: 5, name: 'young hen', emoji: '🐔', scale: 0.88, min_age_hours: 168 },
            { stage: 6, name: 'cockerel',  emoji: '🐓', scale: 0.95, min_age_hours: 192 },
            { stage: 7, name: 'rooster',   emoji: '🐓', scale: 1.10, min_age_hours: 216 },
        ],
        goose: [
            { stage: 0, name: 'egg',         emoji: '🥚', scale: 1.00, min_age_hours: 0   },
            { stage: 1, name: 'hatchling',   emoji: '🐣', scale: 0.55, min_age_hours: 72  },
            { stage: 2, name: 'gosling',     emoji: '🐤', scale: 0.60, min_age_hours: 96  },
            { stage: 3, name: 'fluffling',   emoji: '🐥', scale: 0.72, min_age_hours: 120 },
            { stage: 4, name: 'young goose', emoji: '🪿', scale: 0.65, min_age_hours: 144 },
            { stage: 5, name: 'juvenile',    emoji: '🪿', scale: 0.82, min_age_hours: 168 },
            { stage: 6, name: 'adolescent',  emoji: '🪿', scale: 0.95, min_age_hours: 192 },
            { stage: 7, name: 'goose',       emoji: '🪿', scale: 1.15, min_age_hours: 216 },
        ],
        dog: [
            { stage: 0, name: 'egg',        emoji: '🥚', scale: 1.00, min_age_hours: 0   },
            { stage: 1, name: 'newborn',    emoji: '🐣', scale: 0.55, min_age_hours: 72  },
            { stage: 2, name: 'puppy',      emoji: '🐶', scale: 0.62, min_age_hours: 96  },
            { stage: 3, name: 'big puppy',  emoji: '🐶', scale: 0.78, min_age_hours: 120 },
            { stage: 4, name: 'young dog',  emoji: '🐕', scale: 0.65, min_age_hours: 144 },
            { stage: 5, name: 'adolescent', emoji: '🐕', scale: 0.82, min_age_hours: 168 },
            { stage: 6, name: 'sub-adult',  emoji: '🐕', scale: 0.95, min_age_hours: 192 },
            { stage: 7, name: 'adult dog',  emoji: '🐕', scale: 1.15, min_age_hours: 216 },
        ],
        turtle: [
            { stage: 0, name: 'egg',        emoji: '🥚', scale: 1.00, min_age_hours: 0   },
            { stage: 1, name: 'hatchling',  emoji: '🐣', scale: 0.50, min_age_hours: 72  },
            { stage: 2, name: 'tiny',       emoji: '🐢', scale: 0.50, min_age_hours: 96  },
            { stage: 3, name: 'small',      emoji: '🐢', scale: 0.65, min_age_hours: 120 },
            { stage: 4, name: 'juvenile',   emoji: '🐢', scale: 0.78, min_age_hours: 144 },
            { stage: 5, name: 'sub-adult',  emoji: '🐢', scale: 0.90, min_age_hours: 168 },
            { stage: 6, name: 'adolescent', emoji: '🐢', scale: 1.00, min_age_hours: 192 },
            { stage: 7, name: 'adult',      emoji: '🐢', scale: 1.15, min_age_hours: 216 },
        ],
    };

    // Pick the active stage for a given pet age. Always returns an entry
    // (falls back to stage 0 if seconds_alive is missing or species unknown).
    window.PET_SPRITES_PICK = function (pet_type, seconds_alive) {
        const stages = window.PET_SPRITES[pet_type];
        if (!stages || !stages.length) return null;
        const ageH = Math.max(0, (seconds_alive || 0) / 3600);
        let chosen = stages[0];
        for (let i = 0; i < stages.length; i++) {
            if (ageH >= stages[i].min_age_hours) chosen = stages[i];
            else break;
        }
        return chosen;
    };
})();
