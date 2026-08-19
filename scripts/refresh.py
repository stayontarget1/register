"""Writes data/state.json — THE COUNT plus the rolled-up detail behind it.

Runs every 2 hours. No model call, so this is free and can be frequent.
"""
import json, pathlib, datetime as dt
import sources

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "state.json"


def main():
    parks = sources.nps_parks()
    alerts = sources.nps_alerts()
    candidates, closures, parks_hit = sources.triage_alerts(alerts, parks)
    fires = sources.inciweb()
    fedreg = sources.federal_register(7)
    federal_fires = [f for f in fires if f["federal"]]

    state = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "count": {
            "parks_under_alert": parks_hit,
            "full_closures": len(closures),
            "fires": len(federal_fires),
            "fedreg_7d": len(fedreg),
            "red_flag": sources.red_flag_zones(),
        },
        "totals": {"alerts_scanned": len(alerts), "candidates": len(candidates)},
        "closures": closures,
        "fires": sorted(federal_fires, key=lambda f: -f["acres"])[:40],
        "fedreg": fedreg[:40],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(state, indent=1))
    print(f"state.json: {len(alerts)} alerts scanned -> {len(candidates)} candidates, "
          f"{parks_hit} parks under alert, {len(closures)} full closures, "
          f"{len(federal_fires)} federal fires, {len(fedreg)} fedreg docs")


if __name__ == "__main__":
    main()
