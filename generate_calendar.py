import re
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# ============================================================
# Mike's Football Calendar
#
# Sources:
#   Live Football On TV - UK television listings
#   BBC One schedules - Match of the Day / Match of the Day 2
#
# Included:
#   - Premier League matches shown on Sky Sports
#   - Liverpool FA Cup matches
#   - Liverpool Carabao Cup matches
#   - Amazon Prime Video Champions League matches
#   - BBC Match of the Day
#   - BBC Match of the Day 2
#
# Excluded:
#   - TNT Sports
#   - Other Premier League matches
#   - Other competitions / broadcasters
# ============================================================

UK = ZoneInfo("Europe/London")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    )
}

SOURCE_URLS = {
    "premier_league": (
        "https://www.live-footballontv.com/"
        "live-premier-league-football-on-tv.html"
    ),
    "liverpool": (
        "https://www.live-footballontv.com/"
        "liverpool-on-tv.html"
    ),
    "amazon": (
        "https://www.live-footballontv.com/"
        "live-football-on-amazon.html"
    ),
}


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def parse_date(text, reference=None):
    """
    Convert things such as:
      Friday 21st August
      Sunday 20th September
    into a timezone-aware datetime.

    The source site does not normally print the year, so we infer
    the season year from the current date.
    """
    text = clean(text)

    match = re.search(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2)

    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    month = months[month_name.lower()]

    if reference is None:
        reference = datetime.now(UK)

    year = reference.year

    # August-December belong to the current season/year.
    # January-July belong to the following calendar year when
    # we're currently in the latter part of the year.
    if month < reference.month:
        year += 1

    return datetime(year, month, day, tzinfo=UK)


def parse_time(text):
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))

    if hour > 23 or minute > 59:
        return None

    return hour, minute

def extract_matches(soup):
    """
    Extract fixtures from the current Live Football On TV page.

    The site currently presents each fixture in this order:

        date
        kick-off
        fixture
        competition
        channels

    We deliberately parse the page's text sequence rather than relying
    on the site's CSS class names, which have changed over time.
    """
    text_items = [
        clean(text)
        for text in soup.stripped_strings
        if clean(text)
    ]

    date_pattern = re.compile(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r"\s+\d{1,2}(?:st|nd|rd|th)?\s+"
        r"(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+\d{4}$",
        re.IGNORECASE,
    )

    time_pattern = re.compile(
        r"^\d{1,2}:\d{2}$"
    )

    competition_names = {
        "Premier League",
        "Champions League",
        "FA Cup",
        "Carabao Cup",
        "League Cup",
    }

    fixtures = []
    current_date = None
    i = 0

    while i < len(text_items):
        item = text_items[i]

        # ----------------------------------------------------
        # Date heading
        # ----------------------------------------------------
        if date_pattern.match(item):
            current_date = parse_date(item)
            i += 1
            continue

        # ----------------------------------------------------
        # We need a date before we can parse a fixture.
        # ----------------------------------------------------
        if current_date is None:
            i += 1
            continue

        # ----------------------------------------------------
        # Kick-off time
        # ----------------------------------------------------
        if not time_pattern.match(item):
            i += 1
            continue

        time_parts = parse_time(item)
        if not time_parts:
            i += 1
            continue

        hour, minute = time_parts

        # ----------------------------------------------------
        # The next items should be:
        #   fixture
        #   competition
        #   channels...
        # ----------------------------------------------------
        if i + 2 >= len(text_items):
            break

        fixture = text_items[i + 1]
        competition = text_items[i + 2]

        # Make sure this really looks like a football fixture.
        if (
            " v " not in fixture.lower()
            or competition not in competition_names
        ):
            i += 1
            continue

        # ----------------------------------------------------
        # Collect channel names until the next time/date.
        # ----------------------------------------------------
        channels = []
        j = i + 3

        while j < len(text_items):
            next_item = text_items[j]

            if date_pattern.match(next_item):
                break

            if time_pattern.match(next_item):
                break

            # Another competition means we've probably hit
            # malformed/unexpected page structure.
            if next_item in competition_names:
                break

            channels.append(next_item)
            j += 1

        # ----------------------------------------------------
        # Only accept fixtures with at least one channel.
        # ----------------------------------------------------
        if channels:
            start = current_date.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            fixtures.append(
                {
                    "fixture": fixture,
                    "competition": competition,
                    "channels": clean(" ".join(channels)),
                    "start": start,
                }
            )

        i = max(j, i + 3)

    return fixtures
    
def fetch_live_football(url):
    soup = get_soup(url)
    return extract_matches(soup)


def is_sky(channels):
    text = channels.lower()

    return (
        "sky sports" in text
        and "tnt sports" not in text
    )


def is_amazon(channels):
    text = channels.lower()

    return (
        "amazon prime" in text
        or "prime video" in text
    )


def is_liverpool_fixture(fixture):
    text = fixture.lower()

    return (
        "liverpool" in text
        and "women" not in text
        and "u21" not in text
        and "u18" not in text
    )


def make_event(
    title,
    start,
    duration_minutes,
    description,
    source_url,
):
    end = start + timedelta(minutes=duration_minutes)

    uid_source = (
        f"{title}|{start.isoformat()}|"
        f"{description}"
    )

    uid = hashlib.sha256(
        uid_source.encode("utf-8")
    ).hexdigest()[:24]

    return {
        "uid": f"{uid}@mikes-football-calendar",
        "title": title,
        "start": start,
        "end": end,
        "description": description,
        "url": source_url,
    }


# ------------------------------------------------------------
# Calendar generation
# ------------------------------------------------------------

def build_events():

    events = []

    # --------------------------------------------------------
    # 1. Premier League on Sky Sports
    # --------------------------------------------------------

    try:
        premier_league = fetch_live_football(
            SOURCE_URLS["premier_league"]
        )

        for match in premier_league:

            if "premier league" not in (
                match["competition"].lower()
            ):
                continue

            if not is_sky(match["channels"]):
                continue

            events.append(
                make_event(
                    match["fixture"],
                    match["start"],
                    135,
                    (
                        "Premier League — Sky Sports\n"
                        f"Channels: {match['channels']}\n"
                        f"Source: {SOURCE_URLS['premier_league']}"
                    ),
                    SOURCE_URLS["premier_league"],
                )
            )

    except Exception as exc:
        print(
            f"Premier League source failed: {exc}"
        )

    # --------------------------------------------------------
    # 2. Liverpool FA Cup / Carabao Cup
    # --------------------------------------------------------

    try:
        liverpool = fetch_live_football(
            SOURCE_URLS["liverpool"]
        )

        for match in liverpool:

            competition = match["competition"].lower()

            is_cup = (
                "fa cup" in competition
                or "carabao cup" in competition
                or "league cup" in competition
            )

            if not is_cup:
                continue

            if not is_liverpool_fixture(
                match["fixture"]
            ):
                continue

            events.append(
                make_event(
                    match["fixture"],
                    match["start"],
                    135,
                    (
                        "Liverpool cup fixture\n"
                        f"Competition: {match['competition']}\n"
                        f"TV: {match['channels']}\n"
                        f"Source: {SOURCE_URLS['liverpool']}"
                    ),
                    SOURCE_URLS["liverpool"],
                )
            )

    except Exception as exc:
        print(
            f"Liverpool source failed: {exc}"
        )

    # --------------------------------------------------------
    # 3. Amazon Prime Champions League
    # --------------------------------------------------------

    try:
        amazon = fetch_live_football(
            SOURCE_URLS["amazon"]
        )

        for match in amazon:

            competition = match["competition"].lower()

            if "champions league" not in competition:
                continue

            if not is_amazon(match["channels"]):
                continue

            events.append(
                make_event(
                    match["fixture"],
                    match["start"],
                    135,
                    (
                        "UEFA Champions League — "
                        "Amazon Prime Video UK\n"
                        f"Channels: {match['channels']}\n"
                        f"Source: {SOURCE_URLS['amazon']}"
                    ),
                    SOURCE_URLS["amazon"],
                )
            )

    except Exception as exc:
        print(
            f"Amazon source failed: {exc}"
        )

    # --------------------------------------------------------
    # 4. BBC Match of the Day / Match of the Day 2
    # --------------------------------------------------------

    try:

    except Exception as exc:
        print(
            f"BBC schedule failed: {exc}"
        )

    # --------------------------------------------------------
    # Remove duplicates
    # --------------------------------------------------------

    unique = {}

    for event in events:
        unique[event["uid"]] = event

    events = list(unique.values())

    events.sort(
        key=lambda event: event["start"]
    )

    return events


# ------------------------------------------------------------
# ICS helpers
# ------------------------------------------------------------

def escape_ics(text):
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n")
    text = text.replace("\n", "\\n")
    return text


def utc_string(dt):
    return dt.astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def make_ics(events):

    now = datetime.now(timezone.utc)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Mike's Football Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Mike's Football Calendar",
        "X-WR-TIMEZONE:Europe/London",
    ]

    for event in events:

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event['uid']}",
                f"DTSTAMP:{utc_string(now)}",
                f"DTSTART:{utc_string(event['start'])}",
                f"DTEND:{utc_string(event['end'])}",
                f"SUMMARY:{escape_ics(event['title'])}",
                f"DESCRIPTION:{escape_ics(event['description'])}",
                f"URL:{event['url']}",
                "STATUS:CONFIRMED",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")

    return "\r\n".join(lines) + "\r\n"


def main():

    print("Building Mike's Football Calendar...")

    events = build_events()

    print(
        f"Found {len(events)} calendar events."
    )

    ics = make_ics(events)

    with open(
        "football.ics",
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        file.write(ics)

    print("Created football.ics")

    # Useful diagnostic output in GitHub Actions.
    for event in events:
        print(
            event["start"].strftime(
                "%Y-%m-%d %H:%M"
            ),
            "-",
            event["title"],
        )


if __name__ == "__main__":
    main()
