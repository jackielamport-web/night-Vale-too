#!/usr/bin/env python3
"""Find the parts of new Welcome to Night Vale episodes to skip at bedtime
(host intro, weather, host outro, ad breaks) and write them to data/episodes.json.

Runs in GitHub Actions. Needs:
  FEED_URL           repository variable: the podcast's RSS feed
  ANTHROPIC_API_KEY  repository secret
Optional: MAX_NEW (episodes per run, default 3), KEEP_EPISODES (default 40),
          WHISPER_MODEL (default base.en), CLAUDE_MODEL (default claude-haiku-5-5)
"""
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

import feedparser
import requests
from faster_whisper import WhisperModel

FEED_URL = os.environ.get("FEED_URL", "").strip()
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or "claude-haiku-5-5"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL") or "base.en"
MAX_NEW = int(os.environ.get("MAX_NEW") or 3)
KEEP = int(os.environ.get("KEEP_EPISODES") or 40)
MAX_ATTEMPTS = 3
LABELS = {"intro", "weather", "outro", "ad"}
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "episodes.json")
UA = {"User-Agent": "Mozilla/5.0 (personal bedtime player)"}

# Edit this if you want to keep something (e.g. the credits).
SYSTEM = """You mark the parts of a Welcome to Night Vale episode that a listener wants skipped while falling asleep. Each transcript line starts with its start time in seconds.

Typical layout:
1. Host intro: Joseph Fink (not Cecil, the radio host) talks at the start: announcements, live shows, Patreon, sponsors. Skip it.
2. The episode itself, narrated by Cecil, often opening with a short cold open before "Welcome to Night Vale." Keep everything Cecil says, including the cold open.
3. The weather: Cecil hands over to "the weather", then a song plays. Skip from Cecil's line introducing the weather to the first line where Cecil speaks again after the song.
4. The rest of the episode, ending with Cecil's sign-off (usually "Good night, Night Vale. Good night."). Keep it.
5. Outro: after the sign-off come credits and another block of host announcements and ads. Skip it.
6. A short final piece, usually "Today's proverb". Keep it.
Also skip any ad break inserted anywhere else.

Rules:
- start = timestamp of the first line to skip; end = timestamp of the first line to keep after it. Copy timestamps from the transcript. If a skip runs to the end of the episode, end = the episode duration.
- An intro skip starts at 0.
- Leave out any section the episode doesn't have. When unsure where a boundary is, keep content rather than skip it.
- Reply with JSON only, no other text:
{"skips": [{"label": "intro|weather|outro|ad", "start": 0, "end": 0}]}"""


def load_data():
    try:
        with open(DATA_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"episodes": []}


def save_data(episodes):
    data = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "episodes": episodes}
    with open(DATA_PATH, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def entry_record(entry):
    enclosures = entry.get("enclosures") or []
    url = enclosures[0].get("href") if enclosures else None
    if not url:
        return None
    pub = entry.get("published_parsed")
    return {
        "id": entry.get("id") or url,
        "title": entry.get("title", "Untitled"),
        "published": datetime(*pub[:6], tzinfo=timezone.utc).isoformat() if pub else None,
        "url": url,
    }


def download(url, path):
    with requests.get(url, headers=UA, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)


def transcribe(model, path):
    segments, info = model.transcribe(
        path, language="en", beam_size=1, condition_on_previous_text=False
    )
    lines = [(s.start, s.text.strip()) for s in segments if s.text.strip()]
    return info.duration, lines


def ask_claude(title, duration, lines):
    transcript = "\n".join(f"[{t:.1f}] {text}" for t, text in lines)
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1500,
        "system": SYSTEM,
        "messages": [{
            "role": "user",
            "content": f"Episode: {title}\nDuration: {duration:.1f} s\n\nTranscript:\n{transcript}",
        }],
    }
    headers = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    for attempt in range(3):
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=body, timeout=180)
        if r.status_code in (429, 500, 502, 503, 529) and attempt < 2:
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"No JSON in Claude reply: {text[:200]}")
        return json.loads(match.group(0))


def clean_skips(raw, duration):
    skips = []
    for s in raw.get("skips", []):
        try:
            start, end = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        label = s.get("label") if s.get("label") in LABELS else "ad"
        start = 0.0 if label == "intro" else max(0.0, min(start, duration))
        end = max(0.0, min(end, duration))
        if end - start >= 3:
            skips.append({"label": label, "start": round(start, 1), "end": round(end, 1)})
    skips.sort(key=lambda s: s["start"])
    merged = []
    for s in skips:
        if merged and s["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
        else:
            merged.append(s)
    return merged


def main():
    if not FEED_URL:
        sys.exit("Set the FEED_URL repository variable to the podcast's RSS feed URL.")
    if not API_KEY:
        sys.exit("Add an ANTHROPIC_API_KEY repository secret.")

    resp = requests.get(FEED_URL, headers=UA, timeout=60)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        sys.exit(f"No episodes found at {FEED_URL}")

    old = {e["id"]: e for e in load_data().get("episodes", [])}
    episodes = []
    for entry in feed.entries[:KEEP]:
        rec = entry_record(entry)
        if not rec:
            continue
        prev = old.get(rec["id"], {})
        for key in ("duration", "skips", "status", "attempts", "processed_at", "error"):
            if key in prev:
                rec[key] = prev[key]
        episodes.append(rec)
    episodes.sort(key=lambda e: e.get("published") or "", reverse=True)

    todo = [e for e in episodes if e.get("status") != "ready" and e.get("attempts", 0) < MAX_ATTEMPTS][:MAX_NEW]
    save_data(episodes)
    if not todo:
        print("Nothing new to process.")
        return

    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    for ep in todo:
        print(f"Processing: {ep['title']}", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "episode.mp3")
            try:
                download(ep["url"], path)
                duration, lines = transcribe(model, path)
                skips = clean_skips(ask_claude(ep["title"], duration, lines), duration)
                ep.update({
                    "status": "ready",
                    "duration": round(duration, 1),
                    "skips": skips,
                    "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                ep.pop("error", None)
                for s in skips:
                    print(f"  {s['label']:8} {s['start']:7.1f} -> {s['end']:7.1f}")
            except Exception as exc:  # keep going with the other episodes
                ep["attempts"] = ep.get("attempts", 0) + 1
                ep["status"] = "failed"
                ep["error"] = str(exc)[:300]
                print(f"  failed: {exc}", flush=True)
        save_data(episodes)


if __name__ == "__main__":
    main()
