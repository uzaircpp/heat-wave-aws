# Rahat website (source)

`web/index.html` in the repo root is the built, single-file site that the server serves.
This folder holds its source:

- `template.html` – page structure, styles and the 3D / animation code
- `live.js` – connects every app screen to the Rahat API (falls back to example data when no server is reachable)
- `data/delhi.json` – Delhi ward shapes projected for the SVG maps
- `assets/` – NASA Visible Earth textures for the globe; `photos/` – section images
- `media/ravi-poster.jpg` – first frame of the Ravi film; the films themselves are in `web/media/` (720p desktop, 480p phones)
- `build.py` – inlines icons, charts, images and data into one `index.html`

Libraries: Three.js r160, GSAP + ScrollTrigger, Motion, Anime.js v4, bklit charts, Phosphor icons.
`build.py` expects the bklit bundle and Phosphor icon SVGs at the paths set at its top; adjust them for your machine.
