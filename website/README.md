# LofiMellowHQ — Official Artist Website

**Domain:** [https://LofiMellowHQ.studio](https://LofiMellowHQ.studio)  
**Stack:** HTML5 · Modern CSS3 · Vanilla JavaScript (ES6)  
**Hosting:** Cloudflare Pages (static — no build step)

Premium dark, cinematic artist site for **LofiMellowHQ** — original Lo-Fi, Neo-Classical Piano, Ambient, Sleep, Rain, Study and Focus music.

---

## Project structure

```
website/
├── index.html          # Home + hero
├── music.html          # Releases, streaming, custom audio player
├── discography.html    # Album grid (future-ready)
├── about.html          # Brand story & mission
├── contact.html        # Contact form + socials
├── privacy.html        # Privacy policy
├── style.css           # Design system
├── script.js           # Nav, ambience, player, form
├── favicon.ico
├── favicon.svg
├── robots.txt
├── sitemap.xml
├── _headers            # Cloudflare security & cache
├── _redirects
├── README.md
└── assets/
    ├── covers/         # Album artwork (SVG)
    ├── icons/          # Platform / social icons
    ├── images/         # Logo, OG image
    └── music/          # Demo audio (WAV)
```

---

## Local preview

No install required. From this folder:

```bash
# Python
python3 -m http.server 8080

# or Node
npx serve .
```

Open `http://localhost:8080`.

---

## Deploy to Cloudflare Pages (free)

1. Push this repository (or only the `website` folder) to GitHub/GitLab.
2. In [Cloudflare Dashboard](https://dash.cloudflare.com/) → **Workers & Pages** → **Create** → **Pages** → connect the repo.
3. Build settings:
   - **Framework preset:** None
   - **Build command:** *(leave empty)*
   - **Build output directory:** `website`  
     *(use `/` if this folder is the repo root)*
4. Add custom domain `LofiMellowHQ.studio` and enable HTTPS.
5. Deploy. Every push to the production branch republishes automatically.

Optional: drag-and-drop the contents of `website/` via **Upload assets** for a one-off deploy.

---

## Customisation checklist

| Item | Where |
|------|--------|
| Streaming artist URLs | `music.html`, `index.html`, footers — replace platform homepage links with your verified profiles |
| Social URLs | Footer + `contact.html` social icons |
| Contact email | `hello@lofimellowhq.studio` — update everywhere if different |
| Real album audio | Drop MP3/WAV into `assets/music/` and update `data-src` on track buttons in `music.html` |
| Cover art | Replace SVGs in `assets/covers/` (keep filenames or update `src`) |
| OG image | Prefer a 1200×630 PNG at `assets/images/og-image.png` and update meta tags |
| JSON-LD `sameAs` | Real social/streaming profile URLs in `index.html` |

---

## Design system

| Token | Value |
|-------|--------|
| Background | `#0B1020` |
| Surface | `#141A2E` |
| Primary | `#7C5CFF` |
| Secondary | `#F7B267` |
| Text | `#F8FAFC` |
| Muted | `#94A3B8` |
| Fonts | Poppins + Playfair Display (Google Fonts) |

Style notes: luxury dark UI, glassmorphism, soft glow, rainy-night ambience, sticky nav, mobile hamburger with expand animation.

---

## Features

- Sticky desktop navigation; animated mobile menu
- Hero with gradient motion, particles, rain ambience (`prefers-reduced-motion` respected)
- Custom HTML5 audio player: play/pause, seek, progress, volume, duration, track list
- Discography grid with hover states
- Accessible contact form (validation + mailto handoff for static hosting)
- SEO: meta, Open Graph, Twitter Cards, JSON-LD `MusicGroup`, `robots.txt`, `sitemap.xml`
- Accessibility: skip link, ARIA labels, keyboard seek on progress bar, focus rings, WCAG-minded contrast
- Performance: lazy images, deferred JS, static assets, Cloudflare cache headers

---

## Browser support

Modern evergreen browsers (Chrome, Firefox, Safari, Edge). No React, Vue, Angular, Bootstrap, Tailwind, or jQuery.

---

## License & content

Site code is provided for the LofiMellowHQ brand deployment.  
Music, artwork, and brand name remain property of LofiMellowHQ.  
Demo WAVs in `assets/music/` are procedural placeholders for player testing — replace with official masters before public launch.

---

© LofiMellowHQ · [LofiMellowHQ.studio](https://lofimellowhq.studio)
