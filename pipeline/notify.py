"""Post the weekend outlook to a webhook (Discord / Slack / generic).

Env: DIGEST_WEBHOOK (required), DIGEST_COUNTRIES (default GB).
Posts on Fridays only unless --force. Reads already-built forecast JSON.

    python3 notify.py --data ../site/data [--force]
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request


def load(path):
    with open(path) as f:
        return json.load(f)


def format_digest(cc, fc):
    top = "\n".join(
        f"{i + 1}. {h['name']} — {h['index']}"
        for i, h in enumerate(fc["top"][:5])
    )
    sp = ", ".join(s["species"] for s in fc["species"][:5])
    text = f"**{cc} rarity outlook — {fc['week']}**\n{top}"
    if sp:
        text += f"\nSpecies to watch: {sp}"
    if fc.get("explainer"):
        text += f"\n_{fc['explainer']}_"
    return text


def post(webhook, cc, text):
    if "discord" in webhook:
        payload = {"embeds": [{
            "title": f"Rarebird forecast — {cc}",
            "description": text,
            "color": 0xF0D75A,
        }]}
    elif "slack" in webhook:
        payload = {"text": text}
    else:
        payload = {"text": text, "message": text}
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../site/data")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not args.force and dt.date.today().isoweekday() != 5:
        print("not Friday — skipping (use --force to override)")
        return

    webhook = os.environ.get("DIGEST_WEBHOOK", "").strip()
    if not webhook:
        print("DIGEST_WEBHOOK not set — skipping")
        return
    countries = os.environ.get("DIGEST_COUNTRIES", "GB").split(",")

    for cc in countries:
        path = os.path.join(args.data, f"{cc}.json")
        if not os.path.exists(path):
            print(f"  {cc}: no forecast file")
            continue
        try:
            status = post(webhook, cc, format_digest(cc, load(path)))
            print(f"  {cc}: posted ({status})")
        except Exception as e:
            print(f"  {cc}: post failed {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
