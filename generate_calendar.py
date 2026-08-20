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
    Extract fixtures from Live Football On TV.

    The site uses:
      .span12.matchdate
      .span4.matchfixture
      .span4.competition
      .span1.kickofftime
      .span3.channels
    """

    all_tags = list(soup.find_all(True))
    positions = {id(tag): i for i, tag in enumerate(all_tags)}

    dates = []

    for tag in soup.select(".span12.matchdate"):
        parsed = parse_date(tag.get_text(" ", strip=True))
        if parsed:
            dates.append((positions.get(id(tag), 0), parsed))

    fixtures = []

    for fixture_tag in soup.select(".span4.matchfixture"):

        # Find the nearest parent containing the other fields.
        container = fixture_tag

        for _ in range(8):
            if not container.parent:
                break

            container = container.parent

            if (
                container.select_one(".span1.kickofftime")
                and container.select_one(".span4.competition")
                and container.select_one(".span3.channels")
            ):
                break

        fixture = clean(fixture_tag.get_text(" ", strip=True))

        competition_tag = container.select_one(".span4.competition")
        kickoff_tag = container.select_one(".span1.kickofftime")
        channels_tag = container.select_one(".span3.channels")

        if not competition_tag or not kickoff_tag or not channels_tag:
            continue

        competition = clean(
            competition_tag.get_text(" ", strip=True)
        )
        kickoff_text = clean(
            kickoff_tag.get_text(" ", strip=True)
        )
        channels = clean(
            channels_tag.get_text(" ", strip=True)
        )

        time_parts = parse_time(kickoff_text)
        if not time_parts:
            continue

        # Find the most recent date heading before this fixture.
        fixture_position = positions.get(id(fixture_tag), 0)

        matching_dates = [
            date
            for pos, date in dates
            if pos < fixture_position
        ]

        if not matching_dates:
            continue

        match_date = matching_dates[-1]

        hour, minute = time_parts
        start = match_date.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        fixtures.append(
            {
                "fixture": fixture,
                "competition": competition,
                "channels": channels,
                "start": start,
            }
        )

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
# BBC Match of the Day
# ------------------------------------------------------------

def fetch_bbc_schedule(day):
    """
    BBC One schedule.

    p00fzl9m is the BBC One TV schedule.
    """

    url = (
        "https://www.bbc.co.uk/schedules/"
        f"p00fzl9m/{day:%Y/%m/%d}"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    if response.status_code != 200:
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    programmes = []

    # BBC schedule pages contain programme titles and
    # start times. We deliberately use broad text matching
    # because BBC's page markup changes occasionally.
    for element in soup.find_all(
        string=re.compile(
            r"Match of the Day",
            re.IGNORECASE,
        )
    ):
        title = clean(element)

        if not title:
            continue

        # Ignore links/articles that merely mention MOTD.
        if len(title) > 100:
            continue

        parent = element.parent

        # Look around the programme entry for a time.
        container = parent

        for _ in range(6):
            if not container:
                break

            text = clean(
                container.get_text(" ", strip=True)
            )

            time_match = re.search(
                r"\b(\d{1,2}):(\d{2})\b",
                text,
            )

            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))

                if hour <= 23 and minute <= 59:
                    programmes.append(
                        (
                            title,
                            hour,
                            minute,
                        )
                    )
                    break

            container = container.parent

    return programmes


def fetch_bbc_motd():
    events = []

    today = datetime.now(UK).date()

    # Look ahead roughly four months.
    # Checking Fri/Sat/Sun catches the normal MOTD/MOTD2
    # slots while allowing for changes in scheduling.
    for offset in range(0, 120):

        day = today + timedelta(days=offset)

        if day.weekday() not in (4, 5, 6):
            continue

        programmes = fetch_bbc_schedule(day)

        for title, hour, minute in programmes:

            title_lower = title.lower()

            if "match of the day 2" in title_lower:
                programme_name = "Match of the Day 2"

            elif "match of the day" in title_lower:
                programme_name = "Match of the Day"

            else:
                continue

            start = datetime(
                day.year,
                day.month,
                day.day,
                hour,
                minute,
                tzinfo=UK,
            )

            events.append(
                make_event(
                    programme_name,
                    start,
                    100,
                    (
                        "BBC football highlights. "
                        "Automatically scheduled from the "
                        "BBC One TV listings."
                    ),
                    "https://www.bbc.co.uk/sport/football",
                )
            )

    return events


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
        events.extend(fetch_bbc_motd())

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
