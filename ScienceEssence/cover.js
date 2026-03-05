// ===== The Essence of Science — Cover Animations (3D Book / Library) =====

document.addEventListener('DOMContentLoaded', () => {
    const scene = document.querySelector('.scene');
    const particles = document.getElementById('particles');
    const lampLight = document.querySelector('.lamp-light');
    const libraryShelves = document.getElementById('libraryShelves');
    let dialogueShown = false;

    // ───────────────────────────────────
    // 1. Generate library bookshelves
    // ───────────────────────────────────
    const bookColors = [
        '#4A3018', '#5C3818', '#3A2410', '#6B4420', '#2E1C0A',
        '#7A4828', '#3A2612', '#5A3820', '#4E3218', '#6A4228',
        '#2C1A0C', '#503010', '#624020', '#3C240E', '#584018',
        '#7B5030', '#342010', '#483018', '#5E3E20', '#6E482A',
        '#8B2020', '#3A5A50', '#283870', '#3E1860', '#4A3410',
    ];

    function generateShelves() {
        libraryShelves.innerHTML = '';
        const rows = 6;
        const shelfWidth = window.innerWidth;

        for (let r = 0; r < rows; r++) {
            const row = document.createElement('div');
            row.className = 'shelf-row';

            let filled = 0;
            while (filled < shelfWidth) {
                const book = document.createElement('div');
                book.className = 'bg-book';
                const w = 12 + Math.floor(Math.random() * 18);
                const h = 70 + Math.floor(Math.random() * 50);
                const color = bookColors[Math.floor(Math.random() * bookColors.length)];
                const brightness = 0.9 + Math.random() * 0.7;

                book.style.cssText = `
                    width: ${w}px;
                    height: ${h}px;
                    background: linear-gradient(90deg,
                        ${color} 0%,
                        ${adjustBrightness(color, 1.3)} 45%,
                        ${adjustBrightness(color, 0.8)} 55%,
                        ${color} 100%);
                    filter: brightness(${brightness});
                    margin: 0 ${Math.random() < 0.15 ? 2 + Math.floor(Math.random() * 6) : 0}px;
                `;

                // Occasional tilted book
                if (Math.random() < 0.06) {
                    const tilt = -8 + Math.random() * 16;
                    book.style.transform = `rotate(${tilt}deg)`;
                    book.style.transformOrigin = 'bottom center';
                }

                row.appendChild(book);
                filled += w + 2;
            }

            libraryShelves.appendChild(row);
        }
    }

    function adjustBrightness(hex, factor) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        const clamp = (v) => Math.min(255, Math.max(0, Math.round(v)));
        return `rgb(${clamp(r * factor)}, ${clamp(g * factor)}, ${clamp(b * factor)})`;
    }

    generateShelves();

    // ───────────────────────────────────
    // 2. Dust motes & golden particles
    // ───────────────────────────────────
    function createDustMote() {
        const mote = document.createElement('div');
        const size = 1.5 + Math.random() * 3;
        const isGolden = Math.random() < 0.3;

        // Concentrate near lamplight / center area
        const startX = 25 + Math.random() * 50;
        const startY = 15 + Math.random() * 55;

        const color = isGolden
            ? `rgba(218, 165, 32, ${0.4 + Math.random() * 0.4})`
            : `rgba(200, 180, 140, ${0.2 + Math.random() * 0.3})`;

        mote.style.cssText = `
            position: absolute;
            width: ${size}px;
            height: ${size}px;
            left: ${startX}%;
            top: ${startY}%;
            background: radial-gradient(circle, ${color}, transparent);
            border-radius: 50%;
            pointer-events: none;
            opacity: 0;
        `;

        const duration = 5000 + Math.random() * 9000;
        const driftX = (Math.random() - 0.5) * 100;
        const driftY = -30 + Math.random() * 60;
        const maxOpacity = isGolden ? 0.5 + Math.random() * 0.5 : 0.3 + Math.random() * 0.4;

        mote.animate([
            { opacity: 0, transform: `translate(0, 0) scale(${0.4 + Math.random() * 0.4})` },
            { opacity: maxOpacity, transform: `translate(${driftX * 0.3}px, ${driftY * 0.3}px) scale(1)`, offset: 0.3 },
            { opacity: maxOpacity * 0.7, transform: `translate(${driftX * 0.7}px, ${driftY * 0.7}px) scale(1.1)`, offset: 0.7 },
            { opacity: 0, transform: `translate(${driftX}px, ${driftY}px) scale(0.5)` }
        ], {
            duration,
            easing: 'ease-in-out'
        }).onfinish = () => mote.remove();

        particles.appendChild(mote);
    }

    // Initial burst
    for (let i = 0; i < 20; i++) {
        setTimeout(createDustMote, Math.random() * 3000);
    }
    // Continuous
    const dustInterval = setInterval(createDustMote, 300);

    // ───────────────────────────────────
    // 3. Lamp flicker
    // ───────────────────────────────────
    function flickerLight() {
        const intensity = 0.82 + Math.random() * 0.18;
        if (lampLight) {
            lampLight.style.opacity = String(intensity);
        }
        // Occasional deeper flicker
        const nextDelay = Math.random() < 0.05
            ? 30 + Math.random() * 60  // quick flicker
            : 80 + Math.random() * 220;
        setTimeout(flickerLight, nextDelay);
    }
    flickerLight();

    // ───────────────────────────────────
    // 4. Mouse-responsive lamplight
    // ───────────────────────────────────
    scene.addEventListener('mousemove', (e) => {
        const x = e.clientX / window.innerWidth;
        const y = e.clientY / window.innerHeight;

        if (lampLight) {
            const glowX = 38 + x * 14;
            const glowY = 45 + y * 18;
            lampLight.style.background = `radial-gradient(
                ellipse 48% 58% at ${glowX}% ${glowY}%,
                rgba(255,195,90,0.1) 0%,
                rgba(255,155,55,0.04) 32%,
                transparent 62%
            )`;
        }
    });

    // ───────────────────────────────────
    // 5. Book sparkles (golden sparks on cover)
    // ───────────────────────────────────
    const bookLink = document.querySelector('.book-link');

    function bookSparkle() {
        if (!bookLink) return;
        const rect = bookLink.getBoundingClientRect();
        if (rect.width === 0) return;

        const sparkle = document.createElement('div');
        const x = rect.left + 30 + Math.random() * (rect.width - 60);
        const y = rect.top + 15 + Math.random() * (rect.height - 30);
        const size = 2 + Math.random() * 3;

        sparkle.style.cssText = `
            position: fixed;
            left: ${x}px;
            top: ${y}px;
            width: ${size}px;
            height: ${size}px;
            background: radial-gradient(circle, rgba(218,165,32,0.9), rgba(218,165,32,0));
            border-radius: 50%;
            pointer-events: none;
            z-index: 18;
        `;

        sparkle.animate([
            { opacity: 0, transform: 'scale(0.2)' },
            { opacity: 0.9, transform: 'scale(1.8)' },
            { opacity: 0, transform: 'scale(0.4) translateY(-10px)' }
        ], {
            duration: 1000 + Math.random() * 800,
            easing: 'ease-out'
        }).onfinish = () => sparkle.remove();

        document.body.appendChild(sparkle);
    }

    // Sparkle timer (variable interval)
    function scheduleSparkle() {
        bookSparkle();
        setTimeout(scheduleSparkle, 1500 + Math.random() * 2500);
    }
    setTimeout(scheduleSparkle, 1000);

    // ───────────────────────────────────
    // 6. Larger floating light orbs
    // ───────────────────────────────────
    function createLightOrb() {
        const orb = document.createElement('div');
        const size = 6 + Math.random() * 14;
        const startX = 20 + Math.random() * 60;
        const startY = 25 + Math.random() * 45;
        const goldHue = Math.random() < 0.6;

        const color = goldHue
            ? `rgba(218, 165, 32, ${0.06 + Math.random() * 0.1})`
            : `rgba(255, 220, 160, ${0.04 + Math.random() * 0.08})`;

        orb.style.cssText = `
            position: absolute;
            width: ${size}px;
            height: ${size}px;
            left: ${startX}%;
            top: ${startY}%;
            background: radial-gradient(circle, ${color}, transparent 70%);
            border-radius: 50%;
            pointer-events: none;
            filter: blur(${1 + Math.random() * 2}px);
            opacity: 0;
        `;

        const duration = 8000 + Math.random() * 12000;
        const driftX = (Math.random() - 0.5) * 60;
        const driftY = -40 + Math.random() * 80;

        orb.animate([
            { opacity: 0, transform: 'translate(0, 0) scale(0.5)' },
            { opacity: 1, transform: `translate(${driftX * 0.4}px, ${driftY * 0.4}px) scale(1)`, offset: 0.35 },
            { opacity: 0.8, transform: `translate(${driftX * 0.75}px, ${driftY * 0.75}px) scale(1.2)`, offset: 0.7 },
            { opacity: 0, transform: `translate(${driftX}px, ${driftY}px) scale(0.7)` }
        ], {
            duration,
            easing: 'ease-in-out'
        }).onfinish = () => orb.remove();

        particles.appendChild(orb);
    }

    // Initial orbs
    for (let i = 0; i < 6; i++) {
        setTimeout(createLightOrb, Math.random() * 4000);
    }
    const orbInterval = setInterval(createLightOrb, 1800);

    // ───────────────────────────────────
    // 7. Cursor glow aura + lantern light
    // ───────────────────────────────────
    const cursorGlow = document.getElementById('cursorGlow');
    const cursorLight = document.getElementById('cursorLight');

    scene.addEventListener('mouseenter', () => {
        if (cursorGlow) cursorGlow.style.opacity = '1';
        if (cursorLight) cursorLight.style.opacity = '1';
    });
    scene.addEventListener('mouseleave', () => {
        if (cursorGlow) cursorGlow.style.opacity = '0';
        if (cursorLight) cursorLight.style.opacity = '0';
    });
    scene.addEventListener('mousemove', (e) => {
        const px = e.clientX + 'px';
        const py = e.clientY + 'px';
        if (cursorGlow) {
            cursorGlow.style.left = px;
            cursorGlow.style.top = py;
        }
        if (cursorLight) {
            cursorLight.style.left = px;
            cursorLight.style.top = py;
        }
    });

    // ───────────────────────────────────
    // 8. Resize handler for bookshelves
    // ───────────────────────────────────
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(generateShelves, 300);
    });

    // ───────────────────────────────────
    // 9. SCENE TRANSITIONS (bidirectional)
    // ───────────────────────────────────
    let transitioning = false;
    let currentScene = 'home'; // 'home' or 'newton'
    let newtonInitialized = false;
    const newtonScene = document.getElementById('newtonScene');
    const zoomBlur = document.getElementById('zoomBlur');
    const bookmark = document.querySelector('.bookmark-ribbon');
    const bookmarkTab = document.getElementById('bookmarkTab');
    const bookmarkMenu = document.getElementById('bookmarkMenu');

    function transitionTo(target) {
        if (transitioning || currentScene === target) return;
        transitioning = true;

        // Close menu
        if (bookmarkMenu) bookmarkMenu.classList.remove('menu-open');

        if (target === 'newton') {
            // ── Home → Newton ──
            clearInterval(dustInterval);
            clearInterval(orbInterval);

            // 1. Fade home to black via overlay
            zoomBlur.classList.remove('blackout-out');
            zoomBlur.classList.add('blackout-in');
            scene.classList.add('zoom-into-book');

            // 2. Once screen is black (~1s), swap scenes behind the black
            setTimeout(() => {
                scene.style.visibility = 'hidden';
                scene.classList.remove('zoom-into-book');
                scene.classList.add('zoom-reset');

                // Newton appears blurred behind the black overlay
                newtonScene.classList.add('newton-visible', 'newton-arrive-blurred');
                if (!newtonInitialized) {
                    startNewtonAnimations();
                    newtonInitialized = true;
                }
            }, 1000);

            // 3. Fade black overlay out while Newton unblurs
            setTimeout(() => {
                zoomBlur.classList.remove('blackout-in');
                zoomBlur.classList.add('blackout-out');
                newtonScene.classList.add('newton-arrive-clear');
            }, 1200);

            // 4. All done — clean up classes
            setTimeout(() => {
                zoomBlur.classList.remove('blackout-out');
                newtonScene.classList.remove('newton-arrive-blurred', 'newton-arrive-clear');
                newtonScene.style.filter = '';
                scene.classList.remove('zoom-reset');
                currentScene = 'newton';
                transitioning = false;
                updateBookmarkBtns();
                if (!dialogueShown) setTimeout(startNewtonDialogue, 600);
            }, 2600);

        } else {
            // ── Newton → Home ──

            // 1. Fade to black
            zoomBlur.classList.remove('blackout-out');
            zoomBlur.classList.add('blackout-in');
            newtonScene.classList.add('newton-zoom-out');

            // 2. Once black, swap scenes
            setTimeout(() => {
                // Reset all Newton progress
                newtonScene.classList.remove('newton-visible', 'newton-zoom-out', 'newton-zoom-to-tree', 'newton-arrive-blurred', 'newton-arrive-clear');
                newtonScene.style.filter = '';
                dialogueShown = false;
                const dBox = document.getElementById('dialogueBox');
                if (dBox) {
                    dBox.classList.remove('dialogue-visible', 'dialogue-fade-out');
                    dBox.style.display = '';
                }
                const dText = document.getElementById('dialogueText');
                if (dText) dText.textContent = '';
                const lawsOv = document.getElementById('lawsOverlay');
                if (lawsOv) lawsOv.classList.remove('laws-visible', 'law2-focused');
                newtonScene.style.transform = '';

                // Home appears behind black
                scene.style.visibility = '';
                scene.classList.add('zoom-arrive-home');
                generateShelves();
                for (let i = 0; i < 12; i++) setTimeout(createDustMote, Math.random() * 1500);
                for (let i = 0; i < 4; i++) setTimeout(createLightOrb, Math.random() * 2000);
            }, 1000);

            // 3. Fade black out to reveal home
            setTimeout(() => {
                zoomBlur.classList.remove('blackout-in');
                zoomBlur.classList.add('blackout-out');
            }, 1200);

            // 4. All done
            setTimeout(() => {
                zoomBlur.classList.remove('blackout-out');
                scene.classList.remove('zoom-arrive-home');
                currentScene = 'home';
                transitioning = false;
                updateBookmarkBtns();
            }, 2400);
        }
    }

    function updateBookmarkBtns() {
        if (!bookmarkMenu) return;
        bookmarkMenu.querySelectorAll('.bookmark-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.scene === currentScene);
        });
    }

    // Book click → go to Newton
    if (bookLink) {
        bookLink.addEventListener('click', (e) => {
            e.preventDefault();
            if (bookmark) bookmark.classList.add('bookmark-visible');
            transitionTo('newton');
        });
    }

    // Bookmark tab toggle menu
    if (bookmarkTab) {
        bookmarkTab.addEventListener('click', () => {
            bookmarkMenu.classList.toggle('menu-open');
        });
    }

    // Bookmark menu button clicks
    if (bookmarkMenu) {
        bookmarkMenu.addEventListener('click', (e) => {
            const btn = e.target.closest('.bookmark-btn');
            if (!btn) return;
            transitionTo(btn.dataset.scene);
        });

        // Close menu when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.bookmark-ribbon')) {
                bookmarkMenu.classList.remove('menu-open');
            }
        });
    }

    // ───────────────────────────────────
    // 10. NEWTON SCENE ANIMATIONS
    // ───────────────────────────────────
    function startNewtonAnimations() {
        generateGrassBlades();
        startFallingApple();
        startBirdSpawner();
        startPollenParticles();
    }

    // --- Grass blades ---
    function generateGrassBlades() {
        const container = document.getElementById('grassBlades');
        if (!container) return;
        const count = Math.floor(window.innerWidth / 8);
        for (let i = 0; i < count; i++) {
            const blade = document.createElement('div');
            blade.className = 'grass-blade';
            const h = 15 + Math.random() * 25;
            const left = (i / count) * 100 + Math.random() * (100 / count);
            const delay = Math.random() * 3;
            const hue = 90 + Math.random() * 30;
            const light = 35 + Math.random() * 15;
            blade.style.cssText = `
                left: ${left}%;
                height: ${h}px;
                background: linear-gradient(to top, hsl(${hue},50%,${light}%), hsl(${hue},60%,${light + 15}%));
                animation-delay: ${delay}s;
                animation-duration: ${2.5 + Math.random() * 2}s;
            `;
            container.appendChild(blade);
        }
    }

    // --- Falling apple (lands on Newton's head) ---
    function startFallingApple() {
        function dropApple() {
            const container = document.getElementById('fallingAppleContainer');
            const newtonFig = document.querySelector('.newton-figure');
            if (!container || !newtonFig) return;

            // Calculate Newton's head position from the rendered figure
            const figRect = newtonFig.getBoundingClientRect();
            const sceneRect = container.getBoundingClientRect();
            // Head center in SVG viewBox (180x280) mapped to rendered size
            const headX = figRect.left - sceneRect.left + (90 / 180) * figRect.width;
            const headY = figRect.top - sceneRect.top + (100 / 280) * figRect.height;

            // Apple starts from tree canopy, directly above Newton's head
            const startX = headX - 7; // center the 14px apple
            const startY = headY - 200; // up in the canopy
            const fallDist = 185; // distance to Newton's head top

            const apple = document.createElement('div');
            apple.style.cssText = `
                position: absolute;
                width: 14px;
                height: 14px;
                border-radius: 50%;
                background: radial-gradient(circle at 40% 35%, #ee4444, #cc2222 60%, #991111);
                left: ${startX}px;
                top: ${startY}px;
                z-index: 5;
            `;

            // Stem
            const stem = document.createElement('div');
            stem.style.cssText = `
                position: absolute;
                top: -4px;
                left: 6px;
                width: 2px;
                height: 5px;
                background: #3a2a0a;
                border-radius: 1px;
            `;
            apple.appendChild(stem);
            container.appendChild(apple);

            // Gravity fall — accelerating downward, straight onto head
            apple.animate([
                { transform: 'translateY(0) rotate(0deg)', opacity: 1 },
                { transform: `translateY(${fallDist}px) rotate(15deg)`, opacity: 1 }
            ], {
                duration: 900,
                easing: 'cubic-bezier(0.33, 0, 0.67, 0.33)'
            }).onfinish = () => {
                // Small bounce off Newton's head
                apple.animate([
                    { transform: `translateY(${fallDist}px) rotate(15deg)` },
                    { transform: `translateY(${fallDist - 12}px) translateX(8px) rotate(25deg)`, offset: 0.35 },
                    { transform: `translateY(${fallDist + 60}px) translateX(20px) rotate(80deg)` }
                ], {
                    duration: 500,
                    easing: 'cubic-bezier(0.2, 0, 0.8, 1)'
                }).onfinish = () => {
                    // Roll away and fade
                    apple.animate([
                        { opacity: 1, transform: `translateY(${fallDist + 60}px) translateX(20px) rotate(80deg)` },
                        { opacity: 0, transform: `translateY(${fallDist + 80}px) translateX(35px) rotate(160deg)` }
                    ], {
                        duration: 1200,
                        easing: 'ease-out'
                    }).onfinish = () => apple.remove();
                };
            };

            // Schedule next apple
            const nextDelay = 8000 + Math.random() * 7000;
            setTimeout(dropApple, nextDelay);
        }

        // First apple after a delay
        setTimeout(dropApple, 3000 + Math.random() * 3000);
    }

    // --- Flying birds ---
    function startBirdSpawner() {
        function spawnBird() {
            const container = document.getElementById('newtonBirds');
            if (!container) return;

            const bird = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            const size = 15 + Math.random() * 15;
            const topPos = 5 + Math.random() * 25;
            bird.setAttribute('viewBox', '0 0 30 15');
            bird.setAttribute('width', String(size));
            bird.setAttribute('height', String(size / 2));
            bird.style.cssText = `
                position: absolute;
                top: ${topPos}%;
                left: -30px;
                opacity: 0.5;
            `;

            // V-shape bird silhouette
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', 'M0 8 Q7 2 15 6 Q23 2 30 8');
            path.setAttribute('fill', 'none');
            path.setAttribute('stroke', '#2a2a2a');
            path.setAttribute('stroke-width', '2');
            path.setAttribute('stroke-linecap', 'round');
            bird.appendChild(path);

            // Wing flap animation on the path
            const flapAnim = document.createElementNS('http://www.w3.org/2000/svg', 'animate');
            flapAnim.setAttribute('attributeName', 'd');
            flapAnim.setAttribute('values',
                'M0 8 Q7 2 15 6 Q23 2 30 8;M0 6 Q7 10 15 6 Q23 10 30 6;M0 8 Q7 2 15 6 Q23 2 30 8');
            flapAnim.setAttribute('dur', '0.6s');
            flapAnim.setAttribute('repeatCount', 'indefinite');
            path.appendChild(flapAnim);

            container.appendChild(bird);

            const duration = 8000 + Math.random() * 7000;
            const drift = -20 + Math.random() * 40;
            bird.animate([
                { transform: `translateX(0) translateY(0)`, opacity: 0.4 },
                { transform: `translateX(${window.innerWidth * 0.5}px) translateY(${drift}px)`, opacity: 0.6, offset: 0.5 },
                { transform: `translateX(${window.innerWidth + 60}px) translateY(${drift * 1.5}px)`, opacity: 0.3 }
            ], {
                duration,
                easing: 'linear'
            }).onfinish = () => bird.remove();

            const nextDelay = 8000 + Math.random() * 7000;
            setTimeout(spawnBird, nextDelay);
        }

        setTimeout(spawnBird, 2000 + Math.random() * 3000);
    }

    // ───────────────────────────────────
    // 11. DIALOGUE ENGINE + NEWTON'S LAWS
    // ───────────────────────────────────

    function createDialogueEngine(lines, onComplete) {
        const box = document.getElementById('dialogueBox');
        const textEl = document.getElementById('dialogueText');
        const cursor = box.querySelector('.dialogue-cursor');
        let lineIdx = 0;
        let charIdx = 0;
        let typing = false;
        let typeTimer = null;

        function typeLine() {
            const line = lines[lineIdx];
            typing = true;
            charIdx = 0;
            textEl.textContent = '';
            cursor.classList.remove('cursor-visible');

            function typeChar() {
                if (charIdx < line.length) {
                    textEl.textContent += line[charIdx];
                    charIdx++;
                    typeTimer = setTimeout(typeChar, 45);
                } else {
                    typing = false;
                    cursor.classList.add('cursor-visible');
                }
            }
            typeChar();
        }

        function skipToEnd() {
            clearTimeout(typeTimer);
            textEl.textContent = lines[lineIdx];
            typing = false;
            cursor.classList.add('cursor-visible');
        }

        function advance() {
            lineIdx++;
            if (lineIdx < lines.length) {
                typeLine();
            } else {
                // All lines done
                cursor.classList.remove('cursor-visible');
                box.classList.add('dialogue-fade-out');
                box.classList.remove('dialogue-visible');
                document.removeEventListener('click', handleClick);
                setTimeout(() => {
                    box.style.display = 'none';
                    if (onComplete) onComplete();
                }, 400);
            }
        }

        function handleClick(e) {
            // Ignore clicks on bookmark ribbon
            if (e.target.closest('.bookmark-ribbon')) return;
            e.preventDefault();
            if (typing) {
                skipToEnd();
            } else {
                advance();
            }
        }

        function start() {
            box.style.display = '';
            box.classList.remove('dialogue-fade-out');
            box.classList.add('dialogue-visible');
            document.addEventListener('click', handleClick);
            typeLine();
        }

        return { start, handleClick };
    }

    function showNewtonLaws() {
        const overlay = document.getElementById('lawsOverlay');
        if (overlay) {
            overlay.classList.add('laws-visible');
        }
    }

    // Law II click → expand and show F=ma breakdown
    const lawCard2 = document.getElementById('lawCard2');
    if (lawCard2) {
        lawCard2.addEventListener('click', () => {
            const overlay = document.getElementById('lawsOverlay');
            if (!overlay || overlay.classList.contains('law2-focused')) return;
            overlay.classList.add('law2-focused');
        });
    }

    function triggerNewtonZoomAndLaws() {
        const ns = document.getElementById('newtonScene');
        if (ns) {
            ns.classList.add('newton-zoom-to-tree');
        }
        setTimeout(showNewtonLaws, 1200);
    }

    function startNewtonDialogue() {
        if (dialogueShown) return;
        dialogueShown = true;

        const lines = [
            'The world is governed by forces.',
            'In 1687, Newton published his three laws of motion.',
            'They are the foundations of physics as we know it.'
        ];

        const engine = createDialogueEngine(lines, triggerNewtonZoomAndLaws);
        engine.start();
    }

    // --- Pollen / floating particles ---
    function startPollenParticles() {
        const pollenContainer = document.getElementById('newtonPollen');
        if (!pollenContainer) return;

        function createPollen() {
            const p = document.createElement('div');
            const size = 2 + Math.random() * 3;
            const startX = Math.random() * 100;
            const startY = 20 + Math.random() * 60;
            const hue = 50 + Math.random() * 30;

            p.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                left: ${startX}%;
                top: ${startY}%;
                background: radial-gradient(circle, hsla(${hue},80%,80%,0.7), transparent);
                border-radius: 50%;
                pointer-events: none;
            `;

            pollenContainer.appendChild(p);

            const duration = 6000 + Math.random() * 8000;
            const driftX = (Math.random() - 0.5) * 120;
            const driftY = -20 + Math.random() * 40;

            p.animate([
                { opacity: 0, transform: 'translate(0,0) scale(0.5)' },
                { opacity: 0.7, transform: `translate(${driftX * 0.3}px, ${driftY * 0.3}px) scale(1)`, offset: 0.3 },
                { opacity: 0.5, transform: `translate(${driftX * 0.7}px, ${driftY * 0.7}px) scale(1.1)`, offset: 0.7 },
                { opacity: 0, transform: `translate(${driftX}px, ${driftY}px) scale(0.6)` }
            ], {
                duration,
                easing: 'ease-in-out'
            }).onfinish = () => p.remove();
        }

        // Initial burst
        for (let i = 0; i < 15; i++) {
            setTimeout(createPollen, Math.random() * 2000);
        }
        // Continuous
        setInterval(createPollen, 400);
    }
});
