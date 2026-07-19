"""
Prayer times calculation using astronomical formulas (USNO) and Diyanet (Turkey) method.
Fajr: 18°, Isha: 17°, Asr: Shafi (shadow = 1 × object + noon shadow).
"""

import math
import os
import json
import sqlite3
import unicodedata
from typing import TypedDict
import reverse_geocoder as rg
from timezonefinder import TimezoneFinder
from datetime import datetime
import pytz
from shapely.geometry import Point, shape
from shapely import STRtree

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TURKEY_TIMES_DIR = os.path.join(BASE_DIR, "turkey-prayer-times")
PRAYER_DB_PATH = os.path.join(BASE_DIR, "prayer-times.db")
DISTRICT_GEOJSON_PATH = os.path.join(
    BASE_DIR,
    "geoBoundaries-TUR-ADM2-all",
    "geoBoundaries-TUR-ADM2_simplified.geojson",
)

# Lazily built once: spatial index over Turkish ilçe polygons.
_district_tree: STRtree | None = None
_district_names: list[str] = []
_district_geoms: list = []

# Map Turkish-specific characters to ASCII so province names match the JSON filenames.
_TURKISH_CHAR_MAP = str.maketrans({
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
    "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o",
    "ş": "s", "Ş": "s",
    "ü": "u", "Ü": "u",
})


class PrayerTimesResult(TypedDict):
    imsak: str
    gunes: str
    ogle: str
    ikindi: str
    aksam: str
    yatsi: str


def _deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def _rad2deg(r: float) -> float:
    return r * 180.0 / math.pi


def _normalize_angle_360(degrees: float) -> float:
    """Normalize angle to [0, 360)."""
    d = degrees % 360.0
    return d if d >= 0 else d + 360.0


def _normalize_hour_24(hours: float) -> float:
    """Normalize hour to [0, 24)."""
    h = hours % 24.0
    return h if h >= 0 else h + 24.0


def _decimal_hour_to_hhmm(h: float) -> str:
    """Convert decimal hours (0–24) to 'HH:MM' 24h format."""
    h = _normalize_hour_24(h)
    hour = int(math.floor(h))
    minute = int(round((h - hour) * 60))
    if minute >= 60:
        minute = 0
        hour += 1
    if hour >= 24:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _julian_date(year: int, month: int, day: int, hour_utc: float = 12.0) -> float:
    """Julian date at given UTC time (default noon UTC)."""
    if month <= 2:
        year -= 1
        month += 12
    A = math.floor(year / 100)
    B = 2 - A + math.floor(A / 4)
    jd = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + B - 1524.5
    jd += hour_utc / 24.0
    return jd


def _sun_eq_of_time_and_declination(jd: float) -> tuple[float, float]:
    """
    USNO approximate solar coordinates. Returns (equation of time in hours, declination in degrees).
    """
    D = jd - 2451545.0
    g = _normalize_angle_360(357.529 + 0.98560028 * D)
    q = _normalize_angle_360(280.459 + 0.98564736 * D)
    L = _normalize_angle_360(q + 1.915 * math.sin(_deg2rad(g)) + 0.020 * math.sin(_deg2rad(2 * g)))
    e = 23.439 - 0.00000036 * D

    # Right ascension (same quadrant as L)
    sin_L = math.sin(_deg2rad(L))
    cos_L = math.cos(_deg2rad(L))
    cos_e = math.cos(_deg2rad(e))
    RA_rad = math.atan2(cos_e * sin_L, cos_L)
    RA_hours = _normalize_hour_24(_rad2deg(RA_rad) / 15.0)

    # Equation of time: apparent solar time minus mean solar time (hours)
    EqT = q / 15.0 - RA_hours

    # Declination (degrees)
    decl_rad = math.asin(math.sin(_deg2rad(e)) * sin_L)
    decl = _rad2deg(decl_rad)

    return EqT, decl


def _dhuhr_local(
    lng_deg: float,
    timezone_offset_minutes: float,
    eqtime_hours: float,
) -> float:
    """Solar noon (Dhuhr) in local time as decimal hours. timezone_offset = UTC - local (e.g. -180 for UTC+3)."""
    # Local time zone in hours: local = UTC - offset_minutes/60
    tz_hours = -timezone_offset_minutes / 60.0
    return _normalize_hour_24(12.0 + tz_hours - lng_deg / 15.0 - eqtime_hours)


def _hour_angle_below_horizon(
    lat_deg: float,
    decl_deg: float,
    angle_below_deg: float,
) -> float | None:
    """
    Hour angle (degrees) when sun is at given angle below horizon.
    angle_below_deg: positive = below horizon (e.g. 18 for Fajr, 0.833 for sunrise).
    Returns None if the sun never reaches that angle (polar day/night).
    """
    lat_r = _deg2rad(lat_deg)
    decl_r = _deg2rad(decl_deg)
    # sin(altitude) = sin(lat)*sin(decl) + cos(lat)*cos(decl)*cos(omega)
    # For altitude = -angle_below_deg: sin(alt) = -sin(angle_below_deg)
    sin_alt = math.sin(_deg2rad(-angle_below_deg))
    cos_omega = (sin_alt - math.sin(lat_r) * math.sin(decl_r)) / (
        math.cos(lat_r) * math.cos(decl_r)
    )
    if cos_omega < -1 or cos_omega > 1:
        return None
    omega_rad = math.acos(cos_omega)
    return _rad2deg(omega_rad)


def _asr_hour_angle_shafi(lat_deg: float, decl_deg: float) -> float | None:
    """Hour angle for Asr (Shafi: shadow = 1 × height + noon shadow)."""
    lat_r = _deg2rad(lat_deg)
    decl_r = _deg2rad(decl_deg)
    # tan(sun_altitude) = 1 / (tan(|lat - decl|) + 1)
    phi_minus_d = abs(lat_deg - decl_deg)
    if phi_minus_d >= 90:
        return None
    tan_zenith = math.tan(_deg2rad(phi_minus_d))
    tan_alt = 1.0 / (tan_zenith + 1.0)
    if tan_alt <= 0:
        return None
    alt_rad = math.atan(tan_alt)
    sin_alt = math.sin(alt_rad)
    cos_omega = (sin_alt - math.sin(lat_r) * math.sin(decl_r)) / (
        math.cos(lat_r) * math.cos(decl_r)
    )
    if cos_omega < -1 or cos_omega > 1:
        return None
    omega_rad = math.acos(cos_omega)
    return _rad2deg(omega_rad)

def get_timezone_offset(lat: float, lon: float) -> int:
    """
    Minutes offset in the same sense as JavaScript Date.getTimezoneOffset():
    UTC minus local time (e.g. Turkey UTC+3 → -180).
    """
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lng=lon, lat=lat)

    if timezone_str:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        offset = now.utcoffset()
        if offset is not None:
            # Python: positive = east of UTC. JS getTimezoneOffset = -(that in minutes).
            return -int(offset.total_seconds() / 60)

    # Fallback: Turkey-style UTC+3 → JS offset -180
    return -180

# Diyanet: Fajr 18°, Isha 17°; sunrise/sunset use 0.833° (refraction)
FAJR_ANGLE = 18.0
ISHA_ANGLE = 17.0
SUNRISE_SUNSET_ANGLE = 0.833

# Diyanet Din İşleri Yüksek Kurulu — latitudes beyond 45° (see kurul.diyanet.gov.tr)
DIYANET_HIGH_LAT_DEG = 45.0
DIYANET_YATSI_MAX_AFTER_MAGHRIB_MIN = 80.0  # 1 h 20 min cap (madde b)
DIYANET_IMSAK_EXTRA_MIN = 10.0  # added to akşam–yatsı gap for imsak (madde c)


def _night_hours(sunset: float, sunrise: float) -> float:
    """Hours from astronomical sunset to the next sunrise."""
    if sunrise > sunset:
        return sunrise - sunset
    return (24.0 - sunset) + sunrise


def _fajr_angle_based(sunrise: float, sunset: float, angle_deg: float) -> float:
    """Angle-based high latitude Fajr (Pray Times / MWL default)."""
    night = _night_hours(sunset, sunrise)
    return _normalize_hour_24(sunrise - (angle_deg / 60.0) * night)


def _isha_angle_based(sunset: float, sunrise: float, angle_deg: float) -> float:
    """Angle-based high latitude Isha."""
    night = _night_hours(sunset, sunrise)
    return _normalize_hour_24(sunset + (angle_deg / 60.0) * night)


def _diyanet_yatsi_above_45(aksam_adjusted: float, sunset: float, sunrise: float) -> float:
    """
  Yatsı above 45°: one-third of the night from Maghrib, capped at 1 h 20 min after Maghrib.
    """
    third_night = _night_hours(sunset, sunrise) / 3.0
    offset = min(third_night, DIYANET_YATSI_MAX_AFTER_MAGHRIB_MIN / 60.0)
    return _normalize_hour_24(aksam_adjusted + offset)


def _diyanet_imsak_above_45_mar_sep(
    gunes: float,
    aksam_adjusted: float,
    yatsi: float,
) -> float:
    """
    Imsak above 45° (March–September): sunrise minus (akşam–yatsı interval + 10 min).
    """
    interval = yatsi - aksam_adjusted
    if interval < 0:
        interval += 24.0
    return _normalize_hour_24(gunes - interval - DIYANET_IMSAK_EXTRA_MIN / 60.0)


def get_prayer_times(
    lat: float,
    lng: float,
    date: str,
    timezone_offset_minutes: float,
    calculation_method: str = "Turkey",
) -> PrayerTimesResult:
    """
    Get prayer times for one day in Diyanet (Turkey) method.
    date: 'YYYY-MM-DD'
    timezone_offset_minutes: same as JavaScript getTimezoneOffset() (UTC - local, e.g. -180 for Turkey).
    Returns dict with imsak, gunes, ogle, ikindi, aksam, yatsi as 'HH:MM' strings.
    """
    parts = date.split("-")
    if len(parts) != 3:
        raise ValueError("date must be YYYY-MM-DD")
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    jd = _julian_date(year, month, day, 12.0)
    eqtime, decl = _sun_eq_of_time_and_declination(jd)

    dhuhr = _dhuhr_local(lng, timezone_offset_minutes, eqtime)
    # Time difference from noon in hours: omega_deg / 15
    def hours_from_noon(omega_deg: float | None) -> float:
        if omega_deg is None:
            return 0.0
        return omega_deg / 15.0

    # Sunrise (Güneş) and Sunset
    omega_sun = _hour_angle_below_horizon(lat, decl, SUNRISE_SUNSET_ANGLE)
    sunrise_offset = hours_from_noon(omega_sun) if omega_sun is not None else 0.0
    gunes = dhuhr - sunrise_offset
    sunset = dhuhr + sunrise_offset

    # Imsak (Fajr): 18° below horizon (or high-latitude fallback)
    omega_fajr = _hour_angle_below_horizon(lat, decl, FAJR_ANGLE)
    fajr_offset = hours_from_noon(omega_fajr) if omega_fajr is not None else 0.0

    # Yatsı (Isha): 17° below horizon (or high-latitude fallback)
    omega_isha = _hour_angle_below_horizon(lat, decl, ISHA_ANGLE)
    isha_offset = hours_from_noon(omega_isha) if omega_isha is not None else 0.0

    # İkindi (Asr): Shafi
    omega_asr = _asr_hour_angle_shafi(lat, decl)
    asr_offset = hours_from_noon(omega_asr) if omega_asr is not None else 0.0
    ikindi = dhuhr + asr_offset

    # Akşam (Maghrib) = Sunset (Sunni/Diyanet)
    aksam = sunset
    ogle = dhuhr

    aksam_adjusted = aksam + 7.0 / 60.0
    gunes_adjusted = gunes - 7.0 / 60.0

    # High-latitude Yatsı / Imsak when twilight angles are undefined
    if omega_isha is not None:
        yatsi = dhuhr + isha_offset
    elif abs(lat) >= DIYANET_HIGH_LAT_DEG:
        yatsi = _diyanet_yatsi_above_45(aksam_adjusted, sunset, gunes)
    else:
        yatsi = _isha_angle_based(sunset, gunes, ISHA_ANGLE)

    if omega_fajr is not None:
        imsak = dhuhr - fajr_offset
    elif abs(lat) >= DIYANET_HIGH_LAT_DEG and 3 <= month <= 9:
        imsak = _diyanet_imsak_above_45_mar_sep(gunes, aksam_adjusted, yatsi)
    else:
        imsak = _fajr_angle_based(gunes, sunset, FAJR_ANGLE)

    # Remaining Diyanet safety margins
    ikindi_adjusted = ikindi + 4.0 / 60.0
    ogle_adjusted = ogle + 5.0 / 60.0
    return PrayerTimesResult(
        imsak=_decimal_hour_to_hhmm(imsak),
        gunes=_decimal_hour_to_hhmm(gunes_adjusted),
        ogle=_decimal_hour_to_hhmm(ogle_adjusted),
        ikindi=_decimal_hour_to_hhmm(ikindi_adjusted),
        aksam=_decimal_hour_to_hhmm(aksam_adjusted),
        yatsi=_decimal_hour_to_hhmm(yatsi),
    )

def _normalize_city(name: str) -> str:
    """Turn a province name into the JSON filename stem (ascii, lowercase, no spaces)."""
    name = name.translate(_TURKISH_CHAR_MAP)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return name.strip().lower().replace(" ", "")


def _iso_to_hhmm(value: str) -> str:
    """Extract 'HH:MM' from an ISO timestamp like '2026-07-17T03:46:00+03:00'."""
    return value[11:16]


def _fetch_prayer_row(city: str, district: str, date: str) -> PrayerTimesResult | None:
    """Look up one day in prayer-times.db. Returns None if missing."""
    if not os.path.exists(PRAYER_DB_PATH):
        print(f"prayer DB not found: {PRAYER_DB_PATH}")
        return None
    try:
        conn = sqlite3.connect(f"file:{PRAYER_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT imsak, gunes, ogle, ikindi, aksam, yatsi
                FROM prayer_times
                WHERE city = ? AND district = ? AND date = ?
                """,
                (city, district, date),
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:
        print(f"prayer DB query error: {e}")
        return None

    if row is None:
        return None
    return PrayerTimesResult(
        imsak=row["imsak"],
        gunes=row["gunes"],
        ogle=row["ogle"],
        ikindi=row["ikindi"],
        aksam=row["aksam"],
        yatsi=row["yatsi"],
    )


def _load_district_index() -> tuple[STRtree, list]:
    """Load ADM2 GeoJSON once and build an STRtree for point-in-polygon lookups."""
    global _district_tree, _district_names, _district_geoms
    if _district_tree is not None:
        return _district_tree, _district_geoms

    with open(DISTRICT_GEOJSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    geoms = []
    names = []
    for feature in data["features"]:
        geom = shape(feature["geometry"])
        name = feature.get("properties", {}).get("shapeName")
        if name is None or geom.is_empty:
            continue
        geoms.append(geom)
        names.append(name)

    _district_geoms = geoms
    _district_names = names
    _district_tree = STRtree(geoms)
    return _district_tree, _district_geoms


def get_district_name(lat: float, lng: float) -> str | None:
    """
    Return the Turkish ilçe (district) name containing (lat, lng), or None.
    Uses geoBoundaries ADM2 simplified polygons (point-in-polygon).
    GeoJSON coordinates are (lng, lat).
    """
    try:
        tree, geoms = _load_district_index()
    except Exception as e:
        print(f"district GeoJSON load error: {e}")
        return None

    point = Point(lng, lat)
    # Candidate indices whose bounding boxes intersect the point.
    indices = tree.query(point)
    for idx in indices:
        i = int(idx)
        if geoms[i].covers(point):
            return _district_names[i]
    return None


def get_cached_prayer_times(lat: float, lng: float, date: str) -> PrayerTimesResult | None:
    """
    Return scraped Diyanet prayer times from prayer-times.db.

    Flow:
      1) reverse_geocoder -> province (city)
      2) ADM2 polygon lookup -> district (ilçe)
      3) If no district, query with city=district=<province>
      4) Else query with city + normalized district; if that row is missing,
         fall back to city=district=<province>

    Returns None outside Turkey or when no DB row matches — caller should
    fall back to astronomical get_prayer_times().
    """
    try:
        results = rg.search((lat, lng), mode=1, verbose=False)
    except Exception as e:
        print(f"reverse_geocoder error: {e}")
        return None

    if not results:
        return None

    place = results[0]
    if place.get("cc") != "TR":
        return None

    city = _normalize_city(place.get("admin1", ""))
    if not city:
        return None

    raw_district = get_district_name(lat, lng)
    if raw_district:
        district = _normalize_city(raw_district)
    else:
        district = city

    print(f"province={city!r} district={district!r} (raw={raw_district!r})")

    result = _fetch_prayer_row(city, district, date)
    if result is not None:
        return result

    # District known but not in DB (partial scrape) → province-level row
    if district != city:
        print(f"No DB row for {city}/{district}/{date}; trying {city}/{city}")
        return _fetch_prayer_row(city, city, date)

    return None


"""
istanbul = get_prayer_times(41.0082, 28.9784, "2026-02-20", -180)
london = get_prayer_times(51.5074, -0.1278, "2026-02-20", 0)
dubai = get_prayer_times(25.276987, 55.296233, "2026-02-20", -240)
eindhoven = get_prayer_times(51.4416, 5.4697, "2026-02-20", -60)
newyork = get_prayer_times(40.7128, -74.0060, "2026-02-20", +300)
bangkok = get_prayer_times(13.7563, 100.5018, "2026-02-20", -420)
santiago = get_prayer_times(-33.4489, -70.6693, "2026-02-20", 180)
moscow = get_prayer_times(55.7558, 37.6173, "2026-02-20", -180)
denhaag = get_prayer_times(52.0705, 4.3007, "2026-02-20", -60)
stockholm = get_prayer_times(59.3293, 18.0686, "2026-02-20", -60)
print("istanbul")
print("calculated: ",istanbul["yatsi"], "actual: 20:10")
print("london")
print("calculated: ",london["yatsi"], "actual: 19:02")
print("dubai")
print("calculated: ",dubai["yatsi"], "actual: 19:27")
print("eindhoven")
print("calculated: ",eindhoven["yatsi"], "actual: 19:40")
print("newyork")
print("calculated: ",newyork["yatsi"], "actual: 18:57")
print("bangkok")
print("calculated: ",bangkok["yatsi"], "actual: 19:32")
print("santiago")
print("calculated: ",santiago["yatsi"], "actual: 21:52")
print("moscow")
print("calculated: ",moscow["yatsi"], "actual: 19:33")
print("denhaag")
print("calculated: ",denhaag["yatsi"], "actual: 19:44")
print("stockholm")
print("calculated: ",stockholm, "actual: 18:53")
"""