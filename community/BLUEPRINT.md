# Personal Pages — Blueprint

## Concept
Every user gets a public page at `hanzchau.com/p/username` — a customizable personal webpage like MySpace. AI generates a starting layout from a personality quiz, then users have deep control to make it their own.

## Software Stack
- **Python / Flask** — backend web framework
- **Flask-SocketIO** — real-time chat (existing)
- **SQLAlchemy / SQLite** — database ORM and storage
- **Gunicorn** — production Python web server
- **Anthropic SDK (Claude Haiku)** — AI page generation (~$0.0025/page)
- **Bleach** — HTML sanitization for user-generated content
- **Jinja2** — server-side template rendering for public pages
- **Nginx** — reverse proxy, routes `/p/` to community app
- **Systemd** — keeps services running
- **Let's Encrypt** — HTTPS certificates
- **Vanilla JS** — no frameworks, direct DOM manipulation

## Database Models

### UserPage
- `id` (PK)
- `user_id` (FK users, unique — one page per user)
- `slug` (unique string, defaults to username)
- `page_data` (TEXT — JSON document defining all sections/styling)
- `is_published` (boolean, default False)
- `meta_title` (string)
- `meta_description` (string)
- `quiz_answers` (TEXT — JSON)
- `view_count` (integer, default 0)
- `created_at`, `updated_at` (datetime)

### GuestbookEntry
- `id` (PK)
- `page_id` (FK user_pages)
- `author_name` (string)
- `author_user_id` (FK users, nullable)
- `content` (TEXT, max 500 chars)
- `created_at` (datetime)

### PageImage
- `id` (PK)
- `user_id` (FK users)
- `file_path` (string)
- `file_size` (integer)
- `created_at` (datetime)

## Page JSON Schema
```json
{
  "version": 1,
  "theme": {
    "background": "#241f15",
    "gradient": "linear-gradient(...)",
    "textColor": "#d6c8b4",
    "accentColor": "#c2593e",
    "secondaryAccent": "#b8924a",
    "fontFamily": "Georgia, serif",
    "headingFont": "Georgia, serif"
  },
  "layout": {
    "maxWidth": "900px",
    "style": "single-column"
  },
  "sections": [
    {
      "id": "uuid",
      "type": "hero|about|gallery|music|video|blog|friends|guestbook|links|portfolio|quote|custom_html",
      "order": 0,
      "visible": true,
      "content": { ... },
      "style": {
        "padding": "2rem",
        "background": null,
        "textColor": null,
        "fontFamily": null
      }
    }
  ],
  "custom_css": ""
}
```

## Section Types

| Section | Content Fields | Description |
|---------|---------------|-------------|
| Hero | name, tagline, showAvatar | Header with name and tagline |
| About Me | html (sanitized) | Rich text bio |
| Photo Gallery | images[] (id, caption) | Grid/slideshow of uploaded images |
| Music Player | tracks[] (title, source/portfolioItemId/externalUrl) | Audio player for MP3s |
| Video | videos[] (title, embedUrl/portfolioItemId) | YouTube/Vimeo embeds or MP4s |
| Blog | posts[] (title, html, date, pinned) | Journal entries |
| Top Friends | friendIds[] (max 8) | Classic MySpace top friends display |
| Guestbook | (dynamic from DB) | Visitors leave comments |
| Social Links | links[] (platform, url, label) | Icons for YouTube, Instagram, etc. |
| Portfolio | portfolioItemIds[] | Showcase from existing portfolio |
| Quote | text, attribution | Pull-quote or motto |
| Custom HTML | html (sandboxed iframe) | Raw HTML for power users |

## Onboarding Quiz (8 questions)
1. What could you do for hours? (text)
2. Pick your energy (choice: calm/restless/dreamy/intense/playful)
3. Your life's color palette? (text)
4. Perfect Saturday in one sentence (text)
5. Place where you feel most yourself (text)
6. What's on your playlist? (text)
7. Pick a page vibe (choice: minimal/cozy/bold/retro/artistic/chaotic)
8. What would you say to a visitor? (text)

## AI Generation
- Uses Claude Haiku for cost efficiency (~$0.0025/page)
- Generates: page theme (gradient, colors, fonts), section list with content, tagline, about text
- Simpler backgrounds (gradient + glow spots, no complex SVGs) for speed
- Each generation unique based on quiz answers

## API Routes
| Method | Route | Auth | Purpose |
|--------|-------|------|---------|
| POST | `/api/page/generate` | Yes | Generate page from quiz |
| GET | `/api/page` | Yes | Get own page data |
| PATCH | `/api/page` | Yes | Save page data |
| PATCH | `/api/page/publish` | Yes | Toggle publish |
| POST | `/api/page/images` | Yes | Upload page image |
| DELETE | `/api/page/images/<id>` | Yes | Delete page image |
| GET | `/p/<slug>` | No | View public page |
| GET | `/p/img/<path>` | No | Serve page images |
| GET | `/p/media/<item_id>` | No | Serve public portfolio items |
| GET | `/api/page/guestbook/<slug>` | No | Get guestbook entries |
| POST | `/api/page/guestbook/<slug>` | No | Leave guestbook entry |
| DELETE | `/api/page/guestbook/<id>` | Yes | Delete guestbook entry |

## Editor
- Two-panel layout: controls (left) + live preview (right)
- Section reorder via move up/down buttons
- Add/remove/hide sections
- Per-section styling: background, text color, font, padding, borders
- Page-level theming: global colors, fonts, max width, layout mode
- Custom CSS textarea with live preview
- Mobile: stack panels with edit/preview toggle

## Security
- Rich text sanitized with bleach (allow: p, br, b, i, em, strong, a, ul, ol, li, h1-h6, blockquote, img)
- Custom HTML in sandboxed iframe (srcdoc, no allow-scripts)
- Custom CSS stripped of @import, external url(), expression()
- Guestbook: plain text only, rate-limited (1/IP/page/hour)

## Files
- `community/models.py` — add UserPage, GuestbookEntry, PageImage models
- `community/app.py` — add API routes, AI prompt, public page serving
- `community/templates/page_view.html` — public page renderer (Jinja2)
- `community/templates/page_editor.html` — visual editor
- `community/templates/community.html` — add quiz UI + "Create Page" button

## Implementation Phases
1. **MVP:** Quiz, AI generation, Hero/About/Links/Guestbook sections, basic editor, public page
2. **Media:** Gallery uploads, music player, video embeds, portfolio showcase
3. **Visual Customization:** Color pickers, font selectors, per-section styling, custom CSS
4. **Advanced:** Top Friends, blog, custom HTML, visitor counter, SEO
5. **Polish:** Mobile optimization, section presets, AI regeneration, image cropping

## Nginx Addition
```nginx
location /p/ {
    proxy_pass http://127.0.0.1:5004/p/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
}
```
