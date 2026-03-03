"""
Prayer times API (Diyanet/Turkey method). Deploy to Vercel as a single FastAPI app.
"""
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from prayer_times import get_prayer_times, PrayerTimesResult, get_cached_prayer_times

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
        
        cached_prayer_times = get_cached_prayer_times(lat, lng, date)
        if cached_prayer_times:
            print("Used cached prayer times: ", cached_prayer_times)
            return cached_prayer_times
        prayer_times = get_prayer_times(
            lat=lat,
            lng=lng,
            date=date,
            timezone_offset_minutes=timezoneOffset,
            calculation_method=calculationMethod,
        )
        return prayer_times
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/")
def root():
    return {"service": "Vakit API", "docs": "/docs", "endpoint": "/api/timesForGPS"}
