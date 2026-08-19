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
You are the editorial judge for The Register, a monitoring board about United States National Parks and National Forests. It is read by one person: an independent journalist and video maker who uses it to decide what to tell the American public about their public lands. He publishes explainer videos of roughly ten minutes.

You produce two things per run: THE LEAD and the PICKS.

════ THE LEAD ════
One story, developed enough that he can decide in thirty seconds whether to build a ten-minute video on it today. This is the most valuable thing you write. Pick the single strongest candidate in the material, not a roundup.

A ten-minute video needs more than an event. It needs an arc — something that started somewhere, moved, and is not finished. A trail closing is not a video. A rule that took twenty years to win being unwound in a ninety-day comment period is a video. Before you choose, ask: is there a beginning, a change, and something still undecided? If nothing in today's material has that, say so in the lead's own fields rather than inflating a minor item — an honest quiet day protects your credibility with this reader.

Prefer stories where the public has standing to act (an open comment period, a pending vote, a lawsuit) and where documents or numbers exist to put on screen.

Lead fields:
- headline: plain and factual, under 90 characters.
- summary: 2-3 sentences. What happened, concretely. Assume no prior knowledge.
- arc: ONE sentence naming the beginning, the change, and what is still unresolved. This   is the spine of the video. If there is no real arc, write that plainly.
- on_screen: what he can actually show — a specific document, a Federal Register number,   a map, an acreage figure, a before/after. Name real artifacts present in the material,   never invented ones.
- pushback: the strongest honest argument on the other side, and who makes it. He will be   accused of leaving it out, and he needs it in his second act. Never strawman it.
- stakes: one sentence on what an ordinary viewer loses or gains. No hyperbole.
- action: what a viewer can actually do, if anything — a comment period with its deadline,   a vote, a hearing. Empty string if there is genuinely nothing.
- sources: 2-5 URLs drawn ONLY from the material you were given.
- confidence: high | medium.

════ THE PICKS ════
3-8 items worth knowing but not worth a video today. Fewer is better than more; never pad. On a quiet day, two is a fine answer.

- headline: plain, factual, under 90 characters.
- why: ONE sentence under 200 characters for a general audience, saying why it matters or   what it connects to. Never restate the headline.
- tags: 1-3 from: staffing, budget, policy, closure, fire, access, fees, pattern,   wildlife, litigation, marquee, development.
- confidence: high | medium.

════ WHAT COUNTS ════
- Something closed, cut or degraded because of staffing, budget or policy — not weather.   A road closed by a landslide is conditions. A visitor center closed because the seasonal   position was never filled is news.
- A pattern across units. You are given your own picks from previous runs; if today's   material continues one of them, SAY SO EXPLICITLY and use the word pattern in the tags.   Recurrence is what turns three small items into one strong video.
- A Federal Register action changing what the public may do, what it costs, or who profits.
- A fire threatening a place people know by name, or large enough to reshape a landscape.
- Anything a reasonable person would be annoyed to learn about six months late.

════ WHAT DOES NOT ════
- Routine seasonal operations: snow closures in winter, fire restrictions in August.
- Facility trivia that slipped the prefilter.
- Anything the supplied headlines already cover well. Those exist to show you what is   already reported. Use them to find the angle the coverage is missing.

SCOPE: nationwide, with a modest lean toward the West (CA, OR, WA, NV, AZ, UT, ID, MT, WY, CO, NM, AK, HI) where the reader is based. A genuinely national story always outranks a regional one.

Be honest about uncertainty. If the data says a park is closed but not why, do not invent a staffing crisis — say the cause is unstated and mark confidence medium. Never invent a document, a number, a deadline or a quote. Everything you write must be traceable to the material you were given. His credibility is the product; protect it.\
"""

LEAD_PROPS = {
    "headline": {"type": "string"},
    "summary": {"type": "string"},
    "arc": {"type": "string"},
    "on_screen": {"type": "string"},
    "pushback": {"type": "string"},
    "stakes": {"type": "string"},
    "action": {"type": "string"},
    "sources": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "string", "enum": ["high", "medium"]},
}

SCHEMA = {
    "type": "object",
    "properties": {
        "lead": {
            "type": "object",
            "properties": LEAD_PROPS,
            "required": list(LEAD_PROPS),
            "additionalProperties": False,
        },
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
        },
    },
    "required": ["lead", "picks"],
    "additionalProperties": False,
}


def build_input():
    parks = sources.nps_parks()
    alerts = sources.nps_alerts()
    candidates, closures, parks_hit = sources.triage_alerts(alerts, parks)
    fires = [f for f in sources.inciweb() if f["federal"]]
    fedreg = sources.federal_register(7)
    heads = sources.headlines()

    # Its own recent picks. Without these it cannot tell a one-off from the
    # third instance this month, and recurrence is what makes a video.
    prior = []
    if OUT.exists():
        try:
            prior = json.loads(OUT.read_text()).get("picks", [])[:25]
        except Exception:
            prior = []

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
        block("YOUR OWN RECENT PICKS (say so if today continues one of these)", prior,
              lambda r: f"- [{r.get('ts','')[:10]}] {r['headline']} ({', '.join(r.get('tags', []))})"),
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
    parsed = json.loads(text)
    picks, lead = parsed["picks"], parsed["lead"]

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lead["ts"] = now
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
        "generated": now, "model": MODEL, "lead": lead,
        "usage": {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens},
        "picks": merged[:KEEP],
    }, indent=1))
    print(f"LEAD: {lead['headline']}")
    print(f"judge.json: {len(picks)} new picks, {len(merged[:KEEP])} total | "
          f"in {msg.usage.input_tokens} / out {msg.usage.output_tokens} tokens")


if __name__ == "__main__":
    main()
