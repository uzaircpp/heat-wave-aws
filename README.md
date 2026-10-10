# UrbanShield

**Heat and flood early warning for Delhi's 290 wards.** Built for Hackathon 2026, Track 2: Heat and Water.

UrbanShield turns live weather, satellite land cover, terrain and citizen reports into ward-level warnings:
safe working hours for outdoor workers, a flood simulator, flood-aware routes, a citizen reporting app with
photo verification, and a ward-officer console with AI-drafted alerts in English and Hindi.

- `app/` – FastAPI backend (Python)  ·  `web/` – the built website  ·  `frontend/` – website source
- `scripts/prepare_data.py` – builds the city data from open sources  ·  `deploy/` – one-command AWS deploy
- `tests/` – 28 end-to-end tests (all external services faked)

# UrbanShield backend

Heatwave and waterlogging early warning for Delhi, built on open data. FastAPI + SQLite (or Postgres).
It also serves the website from `web/`, so one deploy gives you the site and the API on the same address.

## What is real

| Feature | Source | Key needed |
|---|---|---|
| Ward boundaries (290 Delhi wards) | DataMeet Municipal Spatial Data (ODbL) | No |
| Live weather, 7-day forecast, last 30 days | Open-Meteo (CC BY 4.0), refreshed every 15 min on a 6 x 6 grid over Delhi, blended to each ward | No (free for non-commercial use) |
| Terrain for the flood model | Copernicus DEM GLO-90 through the Open-Meteo elevation API | No |
| Water points, shelters, parks, hospitals, underpasses, drains, Yamuna, green cover | OpenStreetMap through Overpass | No |
| Official warnings | SACHET, NDMA's CAP feed (IMD, CWC, state authorities), filtered to Delhi | No |
| Forecast warnings | Open-Meteo forecast checked against IMD thresholds (heat wave 45 C, heavy rain 64.5 mm/24 h, bursts of 30 mm in 2 h) | No |
| Citizen reports with photos | Your own database | No |
| Report clusters raise alerts | 3+ waterlogging reports within 400 m in 2 h | No |
| Safer routes | OSRM road routing, each alternative scored against live flood reports and flood-prone underpasses | No |
| Addresses and search | Nominatim (OpenStreetMap) | No (contact email in settings) |
| Safe hours for outdoor work | WBGT from forecast temperature, humidity and sunlight (Stull wet bulb), compared with ACGIH limits per workload | No |
| Officer queue, fix times, CSV export | Your own database, every status change logged | Officer token you choose |
| Broadcasts | Live app feed for everyone; SMS / WhatsApp through Twilio | Twilio, optional |

Two parts are models, and the API says so in each response: the **flood simulator** ranks wards from
real terrain, land cover and rainfall (a screening model, not a hydraulic simulation), and the **heat
simulator** recomputes feels-like for a chosen temperature with the real humidity and wind.

## Run it on your computer (Windows, macOS or Linux)

You need Python 3.11 or newer.

```bash
cd urbanshield-backend
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
# edit .env: set OFFICER_TOKEN and CONTACT_EMAIL
python scripts/prepare_data.py  # 2-5 minutes, downloads elevation and OpenStreetMap layers
uvicorn app.main:app --reload
```

Open http://localhost:8000 for the site and http://localhost:8000/docs for the interactive API.
Weather loads within a few seconds of start-up; alerts follow straight after.

### Tree cover from satellites

`prepare_data.py` measures each ward's tree, vegetation, built-up and water share from **ESA WorldCover 2021**
(10 m land cover classified from Sentinel-1 and Sentinel-2 imagery, CC BY 4.0). It reads only the Delhi window
of the 3 x 3 degree tile from AWS, a few MB, and caches it in `data/cache/`. To add or refresh it without
redoing elevation and OpenStreetMap:

```bash
python scripts/prepare_data.py --landcover-only
```

The heat numbers then add a screening urban heat adjustment per ward, relative to the city average:
+2.0 C for a fully built-up ward and -4.0 C for full tree canopy (about -0.4 C per 10% more canopy), capped at
+/-2 C (tune with `UHI_BUILT_C`, `UHI_TREE_C`, `UHI_MAX_C`). The flood model uses the same cover for runoff.
OpenStreetMap parks are only used if the satellite step fails. Limits: 10 m pixels catch tree clumps and
avenues but miss single street trees, and the map is from 2021.

The site shows a green **Live** chip when it is talking to this server. Every screen then uses the API:
weather, alerts, reports (with photo upload), the flood and heat models, routes, safe hours and the
7-day forecast. **Ward ops** asks for the `OFFICER_TOKEN` from `.env` once and keeps it in that browser.
Opened any other way (for example as a file, or the published preview), the site falls back to example
data and says so. To point a copy of the site at another server, open it with `?api=https://your-server`.

Run the tests (no internet needed, every external API is faked with real response shapes):

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Deploy

**Render (free):** push this folder to GitHub, then in Render choose New > Blueprint and pick the repo.
`render.yaml` creates the web service and a Postgres database and generates `OFFICER_TOKEN`.
Set `CONTACT_EMAIL`. Commit the files from `scripts/prepare_data.py` (`data/wards.json`,
`data/places.json`, `data/waterways.json`) so the server starts instantly.

Photos are stored on local disk in `data/uploads`. Render's free disk is wiped on redeploy, so for a real
pilot add a Render persistent disk mounted at `/app/data/uploads` (paid) or move photos to object storage.

**Docker anywhere:** `docker build -t urbanshield . && docker run -p 8000:8000 --env-file .env urbanshield`

## API

Public
- `GET /api/health`, `GET /api/meta` (sources, channels, roles)
- `GET /api/wards` (GeoJSON), `/api/wards/search?q=`, `/api/wards/locate?lat=&lon=`
- `GET /api/weather/city`, `/api/weather/ward?ward_id=|lat=&lon=&hours=48`
- `GET /api/layers/{feels_like|rain_6h|temperature}?offset_h=0` (one value per ward)
- `GET /api/forecast?ward_id=` (7 days with risk labels)
- `GET /api/safe-hours?role=rider|construction|vendor&lat=&lon=` (hour by hour, plus nearest water and shade)
- `GET /api/history/unsafe-hours?ward_id=&days=30&threshold_c=42`
- `GET /api/alerts`
- `GET /api/places?lat=&lon=&kinds=drinking_water,shelter,community_centre,park`, `GET /api/underpasses`
- `GET /api/route?from=lat,lon&to=lat,lon`
- `GET /api/geocode?q=`, `GET /api/reverse?lat=&lon=`
- `POST /api/simulate/flood` `{rain_mm, hours, drains_clean, extra_storage_mm}`
- `POST /api/simulate/heat` `{air_temp_c, hour, extra_green_pct, cool_roofs}`
- `POST /api/reports` (multipart: kind, lat, lon, depth, description, photo), `GET /api/reports`, `GET /api/reports/{ref}`
- `POST /api/subscribe` `{phone, lat, lon, audiences, whatsapp}`, `DELETE /api/subscribe?phone=`
- `GET /api/stream` (Server-Sent Events: `report`, `alert`, `broadcast`)

Officer (header `Authorization: Bearer <OFFICER_TOKEN>`)
- `PATCH /api/reports/{id}` `{status, assignee, severity}`
- `GET /api/ops/summary`, `GET /api/ops/export.csv`
- `POST /api/broadcasts` `{message, ward_id, audiences, channels}`, `GET /api/broadcasts`
- `GET /api/simulate/flood/calibration?rain_mm=80` (how well the flood model matches real reports)

## Limits to know before going public

- Open-Meteo, Overpass, OSRM and Nominatim public servers are free with fair-use limits. The backend
  caches everything and calls each a few times an hour, which is well within them. For heavy traffic or
  commercial use, buy Open-Meteo's API plan and self-host OSRM and Nominatim.
- Weather models resolve a few kilometres, so wards next to each other share a forecast. The small
  urban heat adjustment from green cover is documented in `app/services/weather.py` (set `UHI_MAX_C=0` to remove it).
- SMS in India needs DLT registration of your sender and templates with TRAI before Twilio or any Indian
  provider will deliver. WhatsApp needs an approved WhatsApp Business sender.
- The officer token is a single shared secret, fine for a pilot. Use proper logins before a city rollout.

## Deploy on AWS (one command, from AWS CloudShell)

1. In the AWS console, switch to **Asia Pacific (Mumbai) ap-south-1** and open **CloudShell** (the `>_` icon).
2. Actions → Upload file → `urbanshield-aws.zip`.
3. Run:

```bash
unzip -q urbanshield-aws.zip && cd urbanshield-backend && bash deploy/aws_deploy.sh
```

It takes about 25–30 minutes the first time (the database is the slow part) and prints the HTTPS link and the
officer token. Run it again after code changes to update. `bash deploy/aws_logs.sh` follows the server log;
`bash deploy/aws_destroy.sh` deletes everything so credits stop being used.

| AWS service | What UrbanShield uses it for |
|---|---|
| Elastic Beanstalk + EC2 (Docker) | runs the FastAPI server, built from the Dockerfile on AWS |
| Amazon RDS for PostgreSQL | reports, history, alerts, broadcasts, subscribers |
| Amazon S3 | report photos (private; served through short-lived signed links) |
| Amazon CloudFront | HTTPS for the site, needed for phone location and notifications |
| AWS Secrets Manager | database URL and officer token, read by the server at start-up |
| Amazon CloudWatch Logs | server logs, 7-day retention |
| Amazon Rekognition | checks report photos: blocks unsafe images, marks photos that show water as verified |
| Amazon Translate | broadcasts go out in English and Hindi |
| Amazon Bedrock | drafts alerts and 72-hour officer briefings from live data (officer reviews before sending) |
| Amazon Location Service | road routes, including one that avoids every flooded spot; address search |
| AWS Open Data (ESA WorldCover) | satellite tree and built-up cover per ward |
| IAM | least-privilege role for the server |

Approximate cost while running: EC2 t3.small + RDS db.t4g.micro + small extras, about US$1–2 a day.
