"""Writes data/judge.json — the LLM picks.

Layer 2 of the filter. Rules already cut ~630 alerts down to ~70 candidates;
this is the part that decides which of those a general audience should hear
about, and writes the one-line reason why.

Picks accumulate. Nothing is dropped because it is assumed to have been read.
"""
import os, json, pathlib, datetime as dt
import anthropic
import sources

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "judge.json"
KEEP = 40
MODEL = "claude-opus-5"

SYSTEM = """\
You are the editorial judge for The Register, a one-page monitoring board about \
United States National Parks and National Forests. It is read by one person: an \
independent journalist who uses it to decide what to tell the American public \
about their public lands.

You are handed the day's raw federal data — park alerts that survived a crude \
keyword prefilter, active wildfire incidents on federal land, this week's Federal \
Register actions from NPS and the Forest Service — plus recent headlines from the \
beat so you can see what reporters have already noticed.

Pick the 3-8 items a national audience should actually hear about. Fewer is better \
than more. If it is a quiet day, return two items; never pad the list.

WHAT COUNTS AS A STORY
- Something is closed, cut, or degraded because of staffing, budget, or policy — \
  not because of weather or a rockfall. This is the single most important category. \
  A road closed by a landslide is conditions. A visitor center closed because the \
  seasonal position was never filled is news.
- A pattern across units: three forests citing the same cause, several parks with \
  the same gap in the same month. Patterns beat individual incidents. Say so \
  explicitly when you see one.
- A Federal Register action that changes what the public may do on the land, what \
  it costs them, or who profits from it — fees, access, leases, timber, motorized \
  use, boundaries, concessions.
- A fire that threatens a place people recognize by name, or is large enough to \
  reshape a landscape.
- Anything a reasonable person would be annoyed to learn about six months late.

WHAT DOES NOT COUNT
- Routine seasonal operations: snow closures in winter, fire restrictions in \
  August, a trail out for scheduled maintenance.
- Facility trivia that slipped the prefilter — a broken exhibit, an unavailable film.
- Anything already obvious from the headlines you were given. Those exist to show \
  you what is already covered. Do not re-report them; use them to spot the pattern \
  the coverage is missing, and skip an item if a headline already tells that story.

SCOPE: nationwide, with a modest lean toward the West (CA, OR, WA, NV, AZ, UT, ID, \
MT, WY, CO, NM, AK, HI) where the reader is based and most likely to report in \
person. A genuinely national story always outranks a regional one.

FOR EACH PICK
- headline: plain, factual, under 90 characters. No hype, no clickbait, no \
  "shocking" or "slammed". Lead with the concrete fact.
- why: ONE sentence, under 200 characters, for a general audience — not for a \
  parks expert. Say why it matters or what it connects to. This is the most \
  valuable field you write; it is the reader's angle, not a summary of the headline. \
  Never restate the headline.
- tags: 1-3 short lowercase labels from: staffing, budget, policy, closure, fire, \
  access, fees, pattern, wildlife, litigation, marquee, development.
- confidence: high | medium — medium if you are inferring the cause rather than \
  reading it stated outright.

Be honest about uncertainty. If the data says a park is closed but does not say \
why, do not invent a staffing crisis; say the cause is unstated, and mark the \
confidence medium. Your credibility with this reader depends entirely on not \
crying wolf.\
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "why": {"type": "string"},
                    "where": {"type": "string"},
                    "url": {"type": "string"},
                    "source": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["high", "medium"]},
                },
                "required": ["headline", "why", "where", "url", "source", "tags", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


def build_input():
    parks = sources.nps_parks()
    alerts = sources.nps_alerts()
    candidates, closures, parks_hit = sources.triage_alerts(alerts, parks)
    fires = [f for f in sources.inciweb() if f["federal"]]
    fedreg = sources.federal_register(7)
    heads = sources.headlines()

    def block(title, rows, fmt):
        if not rows:
            return f"## {title}\n(none)\n"
        return f"## {title}\n" + "\n".join(fmt(r) for r in rows) + "\n"

    doc = [
        f"Date: {dt.date.today().isoformat()}",
        f"Scanned {len(alerts)} active NPS alerts; {len(candidates)} survived the prefilter.",
        f"{parks_hit} parks are under at least one alert; {len(closures)} appear fully closed.\n",
        block("PARK ALERT CANDIDATES", candidates,
              lambda r: f"- [{r['category']}] {r['park']} ({r['states']}): {r['title']}\n"
                        f"  {r['description']}\n  {r['url']}"),
        block("ACTIVE FIRES ON FEDERAL LAND", fires,
              lambda r: f"- {r['title']} — {r['unit']} ({r['state']})"
                        f"{', ~' + format(r['acres'], ',') + ' acres' if r['acres'] else ''}\n"
                        f"  {r['description'][:300]}\n  {r['url']}"),
        block("FEDERAL REGISTER, LAST 7 DAYS", fedreg,
              lambda r: f"- [{r['agency']}] {r['type']}: {r['title']} ({r['date']})\n"
                        f"  {r['description'][:250]}\n  {r['url']}"),
        block("ALREADY COVERED THIS WEEK (context only, do not re-report)", heads,
              lambda r: f"- {r['source']}: {r['title']}"),
    ]
    return "\n".join(doc)


def main():
    payload = build_input()
    print(f"judge input: ~{len(payload) // 4} tokens")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": payload}],
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise SystemExit(f"refused: {getattr(msg, 'stop_details', None)}")

    text = next(b.text for b in msg.content if b.type == "text")
    picks = json.loads(text)["picks"]

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for p in picks:
        p["ts"] = now

    # Roll forward. Dedupe on url, keep newest first, never expire on assumed-read.
    prev = []
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text()).get("picks", [])
        except Exception:
            prev = []
    seen = {p["url"] for p in picks if p.get("url")}
    merged = picks + [p for p in prev if p.get("url") not in seen]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated": now, "model": MODEL,
        "usage": {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens},
        "picks": merged[:KEEP],
    }, indent=1))
    print(f"judge.json: {len(picks)} new picks, {len(merged[:KEEP])} total | "
          f"in {msg.usage.input_tokens} / out {msg.usage.output_tokens} tokens")


if __name__ == "__main__":
    main()
