# Vakit – Prayer Times API

FastAPI service that returns daily prayer times (Diyanet/Turkey method) for a given location and date. Designed to run on **Vercel** as a serverless API.

## Endpoint

**GET** `/api/timesForGPS`

| Query param       | Type   | Description |
|-------------------|--------|-------------|
| `lat`             | number | Latitude    |
| `lng`             | number | Longitude   |
| `date`            | string | Date as `YYYY-MM-DD` |
| `days`            | int    | Number of days (default `1`) |
| `timezoneOffset`  | number | Minutes, same as `new Date().getTimezoneOffset()` |
| `calculationMethod` | string | `Turkey` (Diyanet) |
| `lang`            | string | e.g. `tr` (response keys are always Turkish) |

## Response

JSON object with 24h times (`HH:MM`):

```json
{
  "imsak": "06:24",
  "gunes": "07:32",
  "ogle": "12:13",
  "ikindi": "15:22",
  "aksam": "17:05",
  "yatsi": "19:54"
}
```

## Example (client)

```ts
const res = await fetch(
  `https://your-app.vercel.app/api/timesForGPS?lat=${lat}&lng=${lng}&date=${formattedDate}&days=1&timezoneOffset=${new Date().getTimezoneOffset()}&calculationMethod=Turkey&lang=tr`
);
const data = await res.json();
// data.imsak, data.gunes, data.ogle, data.ikindi, data.aksam, data.yatsi
```

## Calculation

- **Method:** Diyanet (Turkey): Fajr 18°, Isha 17°, Asr Shafi.
- **Astronomy:** USNO approximate solar coordinates (equation of time, declination).
- Times are in **local time** using the given `timezoneOffset`.

## Run locally

```bash
pip install -r requirements.txt
uvicorn index:app --reload
```

Then open: `http://127.0.0.1:8000/api/timesForGPS?lat=41&lng=29&date=2025-02-11&days=1&timezoneOffset=-180&calculationMethod=Turkey&lang=tr`

## Deploy on Vercel

- Push to a Git repo and connect it in the Vercel dashboard, or run `vercel` in this directory.
- No extra config needed; Vercel detects FastAPI and uses `index.py` as the app entrypoint.
