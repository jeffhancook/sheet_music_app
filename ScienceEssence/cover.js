// ===== The Essence of Science — Cover Animations (3D Book / Library) =====

document.addEventListener('DOMContentLoaded', () => {
    const scene = document.querySelector('.scene');
    const particles = document.getElementById('particles');
    const lampLight = document.querySelector('.lamp-light');
    const libraryShelves = document.getElementById('libraryShelves');

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
    setInterval(createDustMote, 300);

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
    setInterval(createLightOrb, 1800);

    // ───────────────────────────────────
    // 7. Cursor glow aura
    // ───────────────────────────────────
    const cursorGlow = document.getElementById('cursorGlow');
    if (cursorGlow) {
        scene.addEventListener('mouseenter', () => { cursorGlow.style.opacity = '1'; });
        scene.addEventListener('mouseleave', () => { cursorGlow.style.opacity = '0'; });
        scene.addEventListener('mousemove', (e) => {
            cursorGlow.style.left = e.clientX + 'px';
            cursorGlow.style.top = e.clientY + 'px';
        });
    }

    // ───────────────────────────────────
    // 8. Resize handler for bookshelves
    // ───────────────────────────────────
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(generateShelves, 300);
    });
});
