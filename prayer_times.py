"""
Prayer times calculation using astronomical formulas (USNO) and Diyanet (Turkey) method.
Fajr: 18°, Isha: 17°, Asr: Shafi (shadow = 1 × object + noon shadow).
"""

import math
from typing import TypedDict


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


# Diyanet: Fajr 18°, Isha 17°; sunrise/sunset use 0.833° (refraction)
FAJR_ANGLE = 18.0
ISHA_ANGLE = 17.0
SUNRISE_SUNSET_ANGLE = 0.833


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

    # Imsak (Fajr): 18° below horizon
    omega_fajr = _hour_angle_below_horizon(lat, decl, FAJR_ANGLE)
    fajr_offset = hours_from_noon(omega_fajr) if omega_fajr is not None else 0.0
    imsak = dhuhr - fajr_offset

    # Yatsı (Isha): 17° below horizon
    omega_isha = _hour_angle_below_horizon(lat, decl, ISHA_ANGLE)
    isha_offset = hours_from_noon(omega_isha) if omega_isha is not None else 0.0
    yatsi = dhuhr + isha_offset

    # İkindi (Asr): Shafi
    omega_asr = _asr_hour_angle_shafi(lat, decl)
    asr_offset = hours_from_noon(omega_asr) if omega_asr is not None else 0.0
    ikindi = dhuhr + asr_offset

    # Akşam (Maghrib) = Sunset (Sunni/Diyanet)
    aksam = sunset
    ogle = dhuhr

    # Gunes: subtract 7 minutes (safety margin before Fajr)
    gunes_adjusted = gunes - 7.0 / 60.0
    # Aksam: add 7 minutes (safety margin after Sunset)
    aksam_adjusted = aksam + 7.0 / 60.0
    # Ikindi: add 4 minutes (safety margin after Asr)
    ikindi_adjusted = ikindi + 4.0 / 60.0
    # Ogle: add 5 minutes (safety margin after Ikindi)
    ogle_adjusted = ogle + 5.0 / 60.0
    # Yatsi: If latitude is higher than 45 or less than -45, calculate differently
    if lat > 45 or lat < -45:
        yatsi = aksam_adjusted + 92.0 / 60.0 # Add 1 saat 32 dakika
    return PrayerTimesResult(
        imsak=_decimal_hour_to_hhmm(imsak),
        gunes=_decimal_hour_to_hhmm(gunes_adjusted),
        ogle=_decimal_hour_to_hhmm(ogle_adjusted),
        ikindi=_decimal_hour_to_hhmm(ikindi_adjusted),
        aksam=_decimal_hour_to_hhmm(aksam_adjusted),
        yatsi=_decimal_hour_to_hhmm(yatsi),
    )
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