"""
Prayer times API (Diyanet/Turkey method). Deploy to Vercel as a single FastAPI app.
"""
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware

from prayer_times import get_prayer_times, PrayerTimesResult, get_cached_prayer_times, get_timezone_offset

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
        
        #cached_prayer_times = get_cached_prayer_times(lat, lng, date)
        #if cached_prayer_times:
        #    print("Used cached prayer times: ", cached_prayer_times)
        #    return cached_prayer_times
        
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


@app.get("/")
def root():
    return {
        "service": "Vakit API",
        "docs": "/docs",
        "single": "GET /api/timesForGPS",
        "batch": "POST /api/timesForGPS?multiple=1&date=YYYY-MM-DD (JSON array body)",
    }
