"""
Prayer times API (Diyanet/Turkey method). Deploy to Vercel as a single FastAPI app.
"""
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware

from prayer_times import (
    get_prayer_times,
    PrayerTimesResult,
    get_cached_prayer_times,
    get_cached_prayer_times_range,
    get_timezone_offset,
)

app = FastAPI(
    title="Vakit API",
    description="Prayer times by GPS (Diyanet/Turkey calculation)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Turkey has no DST and is fixed at UTC+3, so cached local times are 180 min ahead of UTC.
TURKEY_UTC_OFFSET_MIN = 180


def _to_utc_hhmm(hhmm: str, local_offset_minutes: int = TURKEY_UTC_OFFSET_MIN) -> str:
    """Convert an 'HH:MM' in a fixed local offset to UTC 'HH:MM' (wraps around midnight)."""
    total = (int(hhmm[:2]) * 60 + int(hhmm[3:5]) - local_offset_minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


@app.get("/api/timesForGPS", response_model=PrayerTimesResult)
def times_for_gps(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    date: str = Query(..., description="Date YYYY-MM-DD"),
    days: int = Query(1, ge=1, le=365, description="Number of days (first day used for single-object response)"),
    timezoneOffset: float = Query(..., description="Timezone offset in minutes (e.g. getTimezoneOffset())"),
    calculationMethod: str = Query("Turkey", description="Calculation method (Turkey = Diyanet)"),
    lang: str = Query("tr", description="Language (response keys are always Turkish)"),
) -> PrayerTimesResult:
    """
    Returns prayer times for the given date and location.
    Keys: imsak, gunes, ogle, ikindi, aksam, yatsi (24h format HH:MM).
    """
    if not (-90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="lat must be between -90 and 90")
    if not (-180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="lng must be between -180 and 180")
    try:
        
        cached_prayer_times = get_cached_prayer_times(lat, lng, date)
        if cached_prayer_times:
            print("Used cached prayer times: ", cached_prayer_times)
            return cached_prayer_times
        
        tz = get_timezone_offset(lat, lng)
        print("Timezone offset: ", tz)
        prayer_times = get_prayer_times(
            lat=lat,
            lng=lng,
            date=date,
            timezone_offset_minutes=tz,
            calculation_method=calculationMethod,
        )
        return prayer_times
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/timesForGPS")
def times_for_gps_batch(
    multiple: int = Query(...),
    date: str = Query(...),
    days: int = Query(1, ge=1, le=365),
    calculationMethod: str = Query("Turkey"),
    lang: str = Query("tr"),
    body: list = Body(...),
):
    """
    Body: [{"user_id": 1, "latitude": 54.07, "longitude": 11.04}, ...]
    Response: list of dicts with id + imsak, gunes, ogle, ikindi, aksam, yatsi (UTC).
    """
    if multiple != 1:
        raise HTTPException(status_code=400, detail="multiple must be 1")
    if not body:
        raise HTTPException(status_code=400, detail="body must be a non-empty JSON array")

    out = []
    for row in body:
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail="each item must be a JSON object")
        user_id = row.get("user_id")
        try:
            lat = float(row["latitude"])
            lng = float(row["longitude"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"each item needs user_id, latitude, longitude (bad row: {row!r})",
            ) from None

        if not (-90 <= lat <= 90):
            raise HTTPException(status_code=400, detail=f"latitude out of range for id={user_id!r}")
        if not (-180 <= lng <= 180):
            raise HTTPException(status_code=400, detail=f"longitude out of range for id={user_id!r}")

        # Prefer exact scraped Diyanet times for Turkish provinces (stored in UTC+3 → convert to UTC).
        cached = get_cached_prayer_times(lat, lng, date)
        if cached:
            out.append(
                {
                    "user_id": user_id,
                    "imsak": _to_utc_hhmm(cached["imsak"]),
                    "gunes": _to_utc_hhmm(cached["gunes"]),
                    "ogle": _to_utc_hhmm(cached["ogle"]),
                    "ikindi": _to_utc_hhmm(cached["ikindi"]),
                    "aksam": _to_utc_hhmm(cached["aksam"]),
                    "yatsi": _to_utc_hhmm(cached["yatsi"]),
                }
            )
            continue

        try:
            pt = get_prayer_times(
                lat=lat,
                lng=lng,
                date=date,
                timezone_offset_minutes=0.0,
                calculation_method=calculationMethod,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"id={user_id!r}: {e}") from e

        out.append(
            {
                "user_id": user_id,
                "imsak": pt["imsak"],
                "gunes": pt["gunes"],
                "ogle": pt["ogle"],
                "ikindi": pt["ikindi"],
                "aksam": pt["aksam"],
                "yatsi": pt["yatsi"],
            }
        )
    return out


@app.get("/api/timesForLocation")
def times_for_location(
    city: str = Query(..., description="Province / il (e.g. istanbul)"),
    district: str = Query(..., description="District / ilçe (e.g. kucukcekmece); use city name for province-level"),
    start_date: str = Query(..., alias="start-date", description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., alias="end-date", description="End date YYYY-MM-DD"),
):
    """
    Cached Diyanet times only (Turkey local / GMT+3).
    Response: { "YYYY-MM-DD": { imsak, gunes, ogle, ikindi, aksam, yatsi }, ... }
    Returns 404 if any day in the range is missing from the cache.
    """
    try:
        return get_cached_prayer_times_range(city, district, start_date, end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/")
def root():
    return {
        "service": "Vakit API",
        "docs": "/docs",
        "single": "GET /api/timesForGPS",
        "batch": "POST /api/timesForGPS?multiple=1&date=YYYY-MM-DD (JSON array body)",
        "location": "GET /api/timesForLocation?city=&district=&start-date=&end-date=",
    }
