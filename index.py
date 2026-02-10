from fastapi import FastAPI, Query
from adhan import adhan
from adhan.methods import MuslimWorldLeague, ASR_STANDARD
from datetime import datetime, timedelta

app = FastAPI(
    title="Prayer Times API",
    description="API service for calculating Islamic prayer times",
    version="1.0.0"
)

@app.get("/")
def root():
    return {
        "service": "Prayer Times API",
        "status": "online",
        "endpoints": {
            "/api/timesForGPS": "Get prayer times for GPS coordinates"
        }
    }

def get_turkey_params():
    # Matches Diyanet's standard angles
    params = MuslimWorldLeague.copy()
    params['fajr_angle'] = 18
    params['isha_angle'] = 17
    params.update(ASR_STANDARD) # Shafi/Standard shadow method
    return params

@app.get("/api/timesForGPS")
def get_times_for_gps(
    lat: float, 
    lng: float, 
    date: str, 
    days: int = 1, 
    timezoneOffset: int = 0, # Minutes, e.g., 180
    calculationMethod: str = "Turkey",
    lang: str = "tr"
):
    # Convert minutes to hours (e.g., 180 -> 3.0)
    offset_hours = timezoneOffset / 60.0
    start_date = datetime.strptime(date, "%Y-%m-%d")
    params = get_turkey_params()
    
    response_times = {}

    for i in range(days):
        current_day = start_date + timedelta(days=i)
        date_key = current_day.strftime("%Y-%m-%d")
        
        # Calculate for the day
        calc = adhan(
            day=current_day.date(),
            location=(lat, lng),
            parameters=params,
            timezone_offset=offset_hours
        )
        
        # Exact array structure your JS code expects:
        # [0]: Imsak, [1]: Gunes, [2]: Ogle, [3]: Ikindi, [4]: Aksam, [5]: Yatsi
        response_times[date_key] = [
            calc['fajr'].strftime("%H:%M"),
            calc['shuruq'].strftime("%H:%M"),
            calc['zuhr'].strftime("%H:%M"),
            calc['asr'].strftime("%H:%M"),
            calc['maghrib'].strftime("%H:%M"),
            calc['isha'].strftime("%H:%M")
        ]

    return {"times": response_times}

