#!/usr/bin/env python3

import argparse
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 800, 480
TIMEZONE = ZoneInfo("America/Los_Angeles")
STEPS_GOAL = 11_000
CALORIES_GOAL = 3_000
ACTIVE_ZONE_MINUTES_GOAL = 22

BLACK = "#000000"
WHITE = "#FFFFFF"
# These match Inky's saturated seven-colour quantization palette.
GREEN = "#3A5B46"
GRAY = BLACK
BLUE = "#3D3B5E"
RUST = "#9C484B"
PURPLE = BLUE

CLIENT_SECRET = Path("client_secret.json")
TOKEN = Path("token.json")
OUTPUT = Path("healthipi_output.png")
API = "https://health.googleapis.com/v4/users/me/dataTypes"

SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]


def save_credentials(credentials):
    TOKEN.write_text(credentials.to_json(), encoding="utf-8")


def authenticate():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    credentials = flow.run_local_server(port=8765, access_type="offline", prompt="consent")
    save_credentials(credentials)
    print(f"Saved {TOKEN}")


def google_session():
    if not TOKEN.exists():
        raise RuntimeError("Run: python healthipi.py --auth")

    credentials = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not credentials.valid:
        credentials.refresh(Request())
        save_credentials(credentials)
    return AuthorizedSession(credentials)


def civil_time(value):
    return {
        "date": {"year": value.year, "month": value.month, "day": value.day},
        "time": {},
    }


def daily_rollup(session, data_type, day):
    response = session.post(
        f"{API}/{data_type}/dataPoints:dailyRollUp",
        json={
            "range": {
                "start": civil_time(day),
                "end": civil_time(day + timedelta(days=1)),
            },
            "windowSizeDays": 1,
        },
        timeout=30,
    )
    response.raise_for_status()
    points = response.json().get("rollupDataPoints", [])
    return points[0] if points else {}


def list_points(session, data_type, query):
    points = []
    params = {"filter": query, "pageSize": 25 if data_type == "sleep" else 100}
    while True:
        response = session.get(f"{API}/{data_type}/dataPoints", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        points.extend(data.get("dataPoints", []))
        if not data.get("nextPageToken"):
            return points
        params["pageToken"] = data["nextPageToken"]


def daily_values(session, data_type, filter_field, json_field, value_field, day):
    first_day = day - timedelta(days=29)
    points = list_points(
        session,
        data_type,
        f'{filter_field}.date >= "{first_day}" AND '
        f'{filter_field}.date < "{day + timedelta(days=1)}"',
    )
    values = {}
    for point in points:
        data = point.get(json_field, {})
        date = data.get("date", {})
        key = f'{date.get("year", 0):04}-{date.get("month", 0):02}-{date.get("day", 0):02}'
        value = data.get(value_field)
        values[key] = float(value) if value is not None else None
    return [values.get(str(first_day + timedelta(days=i))) for i in range(30)]


def get_metrics(day):
    session = google_session()
    tomorrow = day + timedelta(days=1)

    steps = daily_rollup(session, "steps", day).get("steps", {}).get("countSum")
    calories = (
        daily_rollup(session, "total-calories", day)
        .get("totalCalories", {})
        .get("kcalSum")
    )

    resting_hr_history = daily_values(
        session,
        "daily-resting-heart-rate",
        "daily_resting_heart_rate",
        "dailyRestingHeartRate",
        "beatsPerMinute",
        day,
    )
    hrv_history = daily_values(
        session,
        "daily-heart-rate-variability",
        "daily_heart_rate_variability",
        "dailyHeartRateVariability",
        "averageHeartRateVariabilityMilliseconds",
        day,
    )
    resting_hr = resting_hr_history[-1]
    hrv = hrv_history[-1]

    zones = daily_rollup(session, "active-zone-minutes", day).get(
        "activeZoneMinutes", {}
    )
    zone_values = [
        zones.get("sumInFatBurnHeartZone"),
        zones.get("sumInCardioHeartZone"),
        zones.get("sumInPeakHeartZone"),
    ]
    active_zone_minutes = (
        sum(int(value) for value in zone_values if value is not None)
        if any(value is not None for value in zone_values)
        else None
    )

    first_sleep_day = day - timedelta(days=6)
    sleep = list_points(
        session,
        "sleep",
        f'sleep.interval.civil_end_time >= "{first_sleep_day}" AND '
        f'sleep.interval.civil_end_time < "{tomorrow}"',
    )
    sleep_values = {}
    for point in sleep:
        data = point.get("sleep", {})
        interval = data.get("interval", {})
        date = interval.get("civilEndTime", {}).get("date")
        if date:
            key = f'{date["year"]:04}-{date["month"]:02}-{date["day"]:02}'
        elif interval.get("endTime"):
            end = datetime.fromisoformat(interval["endTime"].replace("Z", "+00:00"))
            key = str(end.astimezone(TIMEZONE).date())
        else:
            continue
        value = data.get("summary", {}).get("minutesAsleep")
        if value is not None:
            sleep_values[key] = max(sleep_values.get(key, 0), int(value))
    sleep_history = [
        sleep_values.get(str(first_sleep_day + timedelta(days=i))) for i in range(7)
    ]

    return {
        "steps": int(steps) if steps is not None else None,
        "calories": round(float(calories)) if calories is not None else None,
        "resting_hr": int(resting_hr) if resting_hr is not None else None,
        "resting_hr_history": resting_hr_history,
        "hrv": round(float(hrv)) if hrv is not None else None,
        "hrv_history": hrv_history,
        "sleep": sleep_history[-1],
        "sleep_history": sleep_history,
        "active_zone_minutes": active_zone_minutes,
    }


@lru_cache(maxsize=64)
def font(size, bold=False):
    filename = "arialbd.ttf" if bold else "arial.ttf"
    linux_name = "Arial_Bold.ttf" if bold else "Arial.ttf"
    names = [
        Path(__file__).parent / "fonts" / filename,
        Path("C:/Windows/Fonts") / filename,
        Path("/usr/share/fonts/truetype/msttcorefonts") / linux_name,
    ]
    path = next((name for name in names if Path(name).exists()), None)
    if not path:
        raise RuntimeError("Arial is missing. Copy arial.ttf and arialbd.ttf into the fonts folder.")
    return ImageFont.truetype(path, size)


def display_value(value, unit=""):
    if value is None:
        return "--"
    return f"{value:,}{' ' + unit if unit else ''}"


def fitted_font(draw, text, size, width):
    face = font(size, True)
    while draw.textlength(text, font=face) > width and size > 12:
        size -= 1
        face = font(size, True)
    return face


def draw_value(draw, x, baseline, value, unit="", size=40, width=300, unit_size=17):
    unit_font = font(unit_size, True)
    unit_width = draw.textlength(unit, font=unit_font) + 10 if unit else 0
    face = fitted_font(draw, value, size, width - unit_width)
    draw.text((x, baseline), value, font=face, fill=BLACK, anchor="ls")
    if unit:
        x += draw.textlength(value, font=face) + 10
        draw.text((x, baseline), unit, font=unit_font, fill=BLACK, anchor="ls")


def text(draw, x, y, value, size=14, bold=True, color=BLACK, anchor="lt"):
    draw.text((x, y), value, font=font(size, bold), fill=color, anchor=anchor)


def completion(draw, x, baseline, value, goal, unit, color, size=24, detail_size=17):
    label = "--%" if value is None else f"{value / goal:.0%}"
    text(draw, x, baseline, label, size, True, color, "ls")
    x += draw.textlength(label, font=font(size, True)) + 6
    text(draw, x, baseline, f"of {goal:,} {unit}", detail_size, color=GRAY, anchor="ls")


def progress_bar(draw, box, value, goal, color, ticks=False):
    x1, y1, x2, y2 = box
    draw.rectangle(box, fill=WHITE, outline=BLACK, width=2)
    progress = min(max((value or 0) / goal, 0), 1)
    if progress:
        draw.rectangle(
            (x1 + 2, y1 + 2, x1 + 2 + (x2 - x1 - 4) * progress, y2 - 2),
            fill=color,
        )
    if ticks:
        draw.line((x1, y2 + 9, x2, y2 + 9), fill=BLACK, width=2)
        for fraction, anchor in ((0, "lt"), (0.5, "mt"), (1, "rt")):
            x = x1 + (x2 - x1) * fraction
            draw.line((x, y2 + 5, x, y2 + 14), fill=BLACK, width=2)
            text(draw, x, y2 + 18, f"{round(goal * fraction):,}", 12, anchor=anchor)


def history_chart(draw, box, values, color, bars=False):
    x1, y1, x2, y2 = box
    valid = [value for value in values if value is not None]
    if not valid:
        text(draw, (x1 + x2) / 2, (y1 + y2) / 2, "NO HISTORY", 12, anchor="mm")
        return
    draw.line((x1, y2, x2, y2), fill=BLACK, width=2)
    if bars:
        spacing = (x2 - x1) / len(values)
        for i, value in enumerate(values):
            if value is not None and value > 0:
                x = x1 + i * spacing + 2
                y = y2 - (y2 - y1) * value / max(max(valid), 1)
                draw.rounded_rectangle((x, y, x + spacing - 4, y2), radius=1, fill=color)
        return

    low, high = min(valid), max(valid)
    padding = max((high - low) * 0.15, 1)
    low, high = low - padding, high + padding
    previous = None
    for i, value in enumerate(values):
        if value is None:
            previous = None
            continue
        x = x1 + i * (x2 - x1) / max(len(values) - 1, 1)
        y = y2 - (value - low) / (high - low) * (y2 - y1)
        if previous:
            draw.line((previous, (x, y)), fill=color, width=4)
        draw.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=color)
        previous = (x, y)


def make_image(metrics):
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    now = datetime.now(TIMEZONE)
    text(draw, 10, 10, f"{now:%a, %b %d}".upper(), 15, anchor="lt")
    text(draw, 724, 9, "LAST UPDATED", 14, anchor="rt")
    text(draw, 786, 7, f"{now:%H:%M}", 18, True, anchor="rt")

    draw.rectangle((7, 32, 793, 473), fill=WHITE, outline=BLACK, width=2)
    draw.line((421, 32, 421, 298), fill=BLACK, width=2)
    draw.line((421, 154, 793, 154), fill=BLACK, width=2)
    draw.line((7, 298, 793, 298), fill=BLACK, width=2)
    draw.line((269, 298, 269, 473), fill=BLACK, width=2)
    draw.line((531, 298, 531, 473), fill=BLACK, width=2)

    steps = metrics["steps"] or 0
    text(draw, 22, 46, "STEPS", 25, True)
    text(draw, 404, 46, f"GOAL {STEPS_GOAL:,}", 14, anchor="rt")
    draw_value(draw, 20, 176, display_value(steps), size=136, width=386)
    completion(draw, 23, 229, steps, STEPS_GOAL, 'steps', GREEN, size=33, detail_size=25)
    progress_bar(draw, (23, 240, 404, 260), steps, STEPS_GOAL, GREEN, ticks=True)

    text(draw, 440, 44, "SLEEP", 21, True)
    text(draw, 440, 67, "TOTAL SLEEP", 14)
    sleep = metrics["sleep"] or 0
    sleep_text = "--" if sleep is None else f"{sleep // 60}h {sleep % 60:02d}m"
    draw_value(draw, 439, 135, sleep_text, size=51, width=194)
    history_chart(draw, (645, 90, 779, 129), metrics.get("sleep_history", []), BLUE, bars=True)
    text(draw, 712, 135, "LAST 7 NIGHTS", 12, anchor="mt")

    calories = metrics["calories"] or 0
    text(draw, 440, 165, "CALORIES", 20, True)
    text(draw, 440, 188, "TOTAL BURN", 14)
    text(draw, 779, 165, f"GOAL {CALORIES_GOAL:,}", 14, anchor="rt")
    draw_value(draw, 439, 256, display_value(calories), size=56, width=153)
    completion(draw, 440, 290, calories, CALORIES_GOAL, 'calories', RUST, size=24, detail_size=18)
    progress_bar(draw, (602, 224, 779, 240), calories, CALORIES_GOAL, RUST, ticks=True)

    for x, title, subtitle, key, unit in (
        (23, "RHR", "RESTING HEART RATE", "resting_hr", "bpm"),
        (284, "HRV", "HEART RATE VARIABILITY", "hrv", "ms"),
    ):
        text(draw, x, 310, title, 20, True)
        text(draw, x, 335, subtitle, 14)
        today = metrics[key] or 0
        draw_value(draw, x, 412, display_value(today), unit, size=65, width=225, unit_size=26)
        history_chart(draw, (x, 426, x + 229, 459), metrics.get(f"{key}_history", []), GREEN)
        text(draw, x + 229, 462, "30 DAYS", 10, anchor="rt")

    minutes = metrics["active_zone_minutes"] or 0
    text(draw, 549, 310, "ACTIVE ZONE MINUTES", 20, True)
    text(draw, 550, 335, f"GOAL  {ACTIVE_ZONE_MINUTES_GOAL} MIN", 14)
    draw_value(draw, 548, 412, display_value(minutes), "min", size=65, width=230, unit_size=26)
    completion(draw, 550, 443, minutes, ACTIVE_ZONE_MINUTES_GOAL, 'mins', PURPLE, size=25, detail_size=18)
    progress_bar(draw, (550, 450, 778, 462), minutes, ACTIVE_ZONE_MINUTES_GOAL, PURPLE)
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true", help="authorize Google Health")
    parser.add_argument("--dry-run", action="store_true", help="save image without updating Inky")
    parser.add_argument("--mock", action="store_true", help="use sample data")
    args = parser.parse_args()

    if args.auth:
        authenticate()
        return

    day = datetime.now(TIMEZONE).date()
    metrics = (
        {
            "steps": 8624,
            "sleep": 446,
            "sleep_history": [421, 465, 432, 478, 407, 453, 446],
            "resting_hr": 58,
            "resting_hr_history": [63, 62, 64, 63, 61, 60, 62, 61, 59, 60, 58, 61, 60, 62, 63, 61, 64, 65, 62, 60, 58, 63, 61, 59, 60, 58, 57, 59, 60, 58],
            "hrv": 46,
            "hrv_history": [35, 33, 36, 38, 34, 37, 39, 36, 40, 38, 41, 39, 42, 40, 43, 41, 32, 36, 34, 37, 33, 40, 38, 42, 40, 36, 43, 40, 42, 46],
            "calories": 2145,
            "active_zone_minutes": 16,
        }
        if args.mock
        else get_metrics(day)
    )
    image = make_image(metrics)
    image.save(OUTPUT)
    print(f"Saved {OUTPUT}")

    if not args.dry_run:
        from inky.auto import auto

        display = auto()
        if tuple(display.resolution) != (WIDTH, HEIGHT):
            raise RuntimeError(f"Expected 800x480 Inky, found {display.resolution}")
        try:
            display.set_image(image, saturation=1.0)
        except TypeError:
            display.set_image(image)
        display.show()


if __name__ == "__main__":
    main()
