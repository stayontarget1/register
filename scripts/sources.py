"""Data collection for The Register. Runs server-side in GitHub Actions only.

Nothing in here ever ships to the browser, which is why the NPS API key can
live in Actions secrets instead of in the public page.
"""
import os, re, json, html, datetime as dt
from urllib.parse import quote
import requests

NPS_KEY = os.environ.get("NPS_API_KEY", "")
UA = {"User-Agent": "TheRegister/1.0 (+https://stayontarget1.github.io/register/)"}
TIMEOUT = 30

# Parks the general public already has a mental image of. A closure at Zion is
# a story; the identical closure at Hovenweep is not. West-weighted per scope.
MARQUEE = {
    "yell","yose","grca","zion","grsm","romo","glac","acad","arch","jotr","seki",
    "olym","grte","brca","ever","dena","deva","badl","mora","crla","cany","care",
    "pinn","chis","lavo","redw","sagu","bibe","grba","grsa","meve","maca","hale",
    "havo","shen","cave","voya","thro","wica","glba","katm","wrst","noca","kefj",
    "gate","jeff","cuva","indu","zion","band","chcu","colm","dino","flfo",
}

WEST = {"CA","OR","WA","NV","AZ","UT","ID","MT","WY","CO","NM","AK","HI"}

# Alerts that are real but are not stories. Cheap string pass, runs before the
# model so we are not paying to have Opus read about a broken orientation film.
NOISE = re.compile(
    r"restroom|water fountain|orientation film|wi-?fi|cell (service|phone)|"
    r"vending|gift shop|bookstore|atm |pit toilet|porta|single campsite|"
    r"drinking water (is )?(temporarily )?unavailable|elevator|"
    r"credit card|cash (is )?not accepted|photo (session|permit)|"
    r"junior ranger|passport stamp|brochure|audio (tour|description)",
    re.I,
)

# Language that suggests the closure is about people and money, not weather.
# This is the watchdog trigger — it does not decide, it just promotes for review.
WATCHDOG = re.compile(
    r"staff|staffing|unstaffed|shortage|vacan|hiring|hire|position|personnel|"
    r"budget|funding|fund|appropriat|shutdown|lapse|furlough|layoff|"
    r"reduced (services|hours|operations)|limited services|"
    r"indefinite|until further notice|for the (season|foreseeable)|permanently|"
    r"deferred maintenance|backlog|infrastructure fail|water system|sewage|"
    r"vandal|looting|theft|damage|fatalit|death|died|missing person|search and rescue",
    re.I,
)

# Whole-unit closures, as opposed to "a thing inside the unit is closed".
# Deliberately narrow. Two traps this has to survive:
#   1. The NPS "Park Closure" category is applied to broken exhibit films, so
#      the category field alone is worthless as a signal.
#   2. "Munising Falls Trail within Pictured Rocks National Lakeshore is closed"
#      is a trail closure, not a park closure. The grammatical subject is the
#      trail; the unit name only appears because it is the container.
# So: require the full designation immediately before "is closed", then reject
# if a containment or partial word appears just before it.
UNIT = (
    r"National\s+(?:Park|Monument|Preserve|Seashore|Lakeshore|Recreation\s+Area|"
    r"Historic(?:al)?\s+Park|Historic\s+Site|Memorial|Battlefield(?:\s+Park)?|"
    r"Military\s+Park|Historical\s+Reserve|Scenic\s+Riverway|Grassland|Parkway|Reserve)"
)
FULL_CLOSURE = re.compile(
    UNIT + r"\s+is\s+(?:currently\s+|temporarily\s+|now\s+)?closed", re.I
)
# A containment or fraction word in the run-up means the subject is something
# inside the unit, not the unit itself.
PARTIAL_CUE = re.compile(
    r"\b(within|inside|in|at|near|portion|section|part|parts|most|much|some|"
    r"area|areas|half|side|level|loop|trail|road|drive|campground|museum|"
    r"overlook|center|centre|falls|beach|lot|wing|room|exhibit)\b", re.I
)


def is_full_closure(blob):
    """True only when the unit itself is closed, not something inside it."""
    for m in FULL_CLOSURE.finditer(blob):
        lead = blob[max(0, m.start() - 70):m.start()]
        if not PARTIAL_CUE.search(lead):
            return True
    return False


def _clean(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def nps_parks():
    """parkCode -> {name, designation, states}. One call, cached into state.json."""
    out = {}
    for start in (0, 300):
        r = requests.get(
            "https://developer.nps.gov/api/v1/parks",
            params={"limit": 300, "start": start, "api_key": NPS_KEY},
            headers=UA, timeout=TIMEOUT,
        )
        r.raise_for_status()
        for p in r.json().get("data", []):
            out[p["parkCode"]] = {
                "name": p.get("fullName", p["parkCode"]),
                "designation": p.get("designation", ""),
                "states": p.get("states", ""),
            }
    return out


def nps_alerts():
    """Every active alert. ~630 of them, which is exactly the problem."""
    out, start = [], 0
    while True:
        r = requests.get(
            "https://developer.nps.gov/api/v1/alerts",
            params={"limit": 500, "start": start, "api_key": NPS_KEY},
            headers=UA, timeout=TIMEOUT,
        )
        r.raise_for_status()
        j = r.json()
        data = j.get("data", [])
        out.extend(data)
        start += len(data)
        if not data or start >= int(j.get("total", 0)):
            break
    return out


def triage_alerts(alerts, parks):
    """Layer 1: rules. ~630 -> ~60 candidates, plus the rolled-up counts.

    Rules do volume reduction only. They never decide what is a story — that
    is the model's job, because 'closed for a rockfall' and 'closed because
    nobody was hired' are the same string to a regex.
    """
    candidates, closures = [], []
    parks_hit = set()

    for a in alerts:
        cat = a.get("category", "")
        title = _clean(a.get("title"))
        desc = _clean(a.get("description"))
        code = a.get("parkCode", "")
        blob = f"{title} {desc}"
        meta = parks.get(code, {"name": code, "designation": "", "states": ""})

        if cat != "Information":
            parks_hit.add(code)

        is_full = is_full_closure(blob)
        if is_full:
            closures.append({
                "park": meta["name"], "code": code, "states": meta["states"],
                "title": title, "url": a.get("url", ""),
            })

        # --- triage ---
        if cat == "Information" and not WATCHDOG.search(blob):
            continue
        if NOISE.search(blob) and not is_full:
            continue

        score = 0
        if is_full:
            score += 5
        if code in MARQUEE:
            score += 4
        if WATCHDOG.search(blob):
            score += 4
        if cat == "Danger":
            score += 2
        if any(s.strip() in WEST for s in meta["states"].split(",")):
            score += 1
        if score < 4:
            continue

        candidates.append({
            "id": a.get("id", ""), "kind": "nps_alert", "score": score,
            "park": meta["name"], "code": code, "states": meta["states"],
            "designation": meta["designation"], "category": cat,
            "title": title, "description": desc[:700],
            "url": a.get("url") or f"https://www.nps.gov/{code}/planyourvisit/conditions.htm",
        })

    candidates.sort(key=lambda c: -c["score"])
    closures.sort(key=lambda c: c["park"])
    return candidates[:70], closures, len(parks_hit)


def inciweb():
    """Active wildfire incidents, filtered to federal land units."""
    r = requests.get("https://inciweb.wildfire.gov/incidents/rss.xml", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for raw in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        def tag(t):
            m = re.search(rf"<{t}>(.*?)</{t}>", raw, re.S)
            return _clean(m.group(1)) if m else ""
        title, link, desc = tag("title"), tag("link"), tag("description")
        if not title or not link:
            continue
        unit = ""
        m = re.search(r"following unit\(s\)\s*(.+?)\.\s", desc)
        if m:
            unit = m.group(1).strip()
        state = ""
        m = re.search(r"State:\s*([A-Za-z ,]+?)\s*---", desc)
        if m:
            state = m.group(1).strip()
        acres = 0
        m = re.search(r"([\d,]{3,})\s*acres", desc, re.I)
        if m:
            try:
                acres = int(m.group(1).replace(",", ""))
            except ValueError:
                acres = 0
        federal = bool(re.search(r"National (Forest|Park|Grassland|Preserve|Monument|Recreation)", unit, re.I))
        overview = ""
        m = re.search(r"Incident Overview:\s*(.+)$", desc, re.S)
        if m:
            overview = _clean(m.group(1))[:600]
        out.append({
            "kind": "fire", "id": link, "title": title, "url": link,
            "unit": unit, "state": state, "acres": acres,
            "federal": federal, "description": overview,
        })
    return out


# The Federal Register carries a steady stream of legally-required routine
# notices — NAGPRA museum inventories, weekly National Register nominations —
# that are real filings but are not policy news. Left in, they are ~80% of the
# feed and bury the fee changes and travel rules that matter.
FEDREG_ROUTINE = re.compile(
    r"Notice of Inventory Completion|Notice of Inten(?:ded|t to) Repatriat|"
    r"Notice of Intended Disposition|National Register of Historic Places;\s*"
    r"(?:Notification of Pending Nominations|Weekly List)",
    re.I,
)


def federal_register(days=7):
    """NPS + USFS rulemaking, minus the routine notice stream."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out = []
    for agency, label in (("national-park-service", "NPS"), ("forest-service", "USFS")):
        try:
            r = requests.get(
                "https://www.federalregister.gov/api/v1/documents.json",
                params={
                    "per_page": 40, "order": "newest",
                    "conditions[agencies][]": agency,
                    "conditions[publication_date][gte]": since,
                    "fields[]": ["title", "html_url", "publication_date", "type", "abstract"],
                },
                headers=UA, timeout=TIMEOUT,
            )
            r.raise_for_status()
            for d in r.json().get("results", []):
                if FEDREG_ROUTINE.search(d.get("title") or ""):
                    continue
                out.append({
                    "kind": "fedreg", "id": d.get("html_url", ""),
                    "agency": label, "title": _clean(d.get("title")),
                    "url": d.get("html_url", ""), "date": d.get("publication_date", ""),
                    "type": d.get("type", ""),
                    "description": _clean(d.get("abstract"))[:500],
                })
        except Exception:
            continue
    return out


def red_flag_zones():
    try:
        r = requests.get(
            "https://api.weather.gov/alerts/active",
            params={"event": "Red Flag Warning"}, headers=UA, timeout=TIMEOUT,
        )
        r.raise_for_status()
        return len(r.json().get("features", []))
    except Exception:
        return 0


def headlines():
    """Recent coverage from the beat, so the judge can spot cross-source patterns."""
    feeds = [
        ("National Parks Traveler", "https://www.nationalparkstraveler.org/rss.xml"),
        ("High Country News", "https://www.hcn.org/feed/"),
        ("The Land Desk", "https://www.landdesk.org/feed"),
        ("Wildfire Today", "https://wildfiretoday.com/feed/"),
        ("Western Priorities", "https://westernpriorities.org/feed/"),
        ("PEER", "https://peer.org/feed/"),
        ("Wilderness Watch", "https://wildernesswatch.org/feed/"),
        ("Wilderness Society", "https://www.wilderness.org/rss.xml"),
    ]
    out = []
    for name, url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            for raw in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:8]:
                m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S)
                l = re.search(r"<link>(.*?)</link>", raw, re.S)
                if m and l:
                    out.append({"source": name, "title": _clean(m.group(1)), "url": _clean(l.group(1))})
        except Exception:
            continue
    return out
