"""Copy Universe Receipts Shorts to the Universe Receipts Facebook Page as Reels.

Reads the studio repository (checked out read-only at SOURCE_DIR) and never writes to it.

  python3 fbposter.py harvest          store every live Short's video as a release asset and refresh the library
  python3 fbposter.py post             publish the best unposted Short as a Reel (at most FB_PER_DAY a day)
  python3 fbposter.py backfill         on the Mac: fetch Shorts whose hosted copy is gone from YouTube with yt-dlp
  python3 fbposter.py status           print the library and what has been posted
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = Path(os.environ.get("SOURCE_DIR", ROOT / "src"))
LIBRARY = ROOT / "state" / "library.json"
POSTED = ROOT / "state" / "posted.json"
RELEASE = "videos"
GRAPH = f"https://graph.facebook.com/{os.environ.get('GRAPH_VERSION', 'v23.0')}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{os.environ.get('GRAPH_VERSION', 'v23.0')}"
PER_DAY = int(os.environ.get("FB_PER_DAY", "2"))
MIN_AGE = timedelta(hours=int(os.environ.get("FB_MIN_AGE_HOURS", "24")))
MAX_TRIES = 3
AUDIENCE_TZ = ZoneInfo("America/New_York")
CAPTION_CHARS = 2200
DISCLOSURE = "Researched and written by Universe Receipts. Narrated with a synthetic voice."


def log(*parts) -> None:
    print(*parts, flush=True)


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def released() -> set[str]:
    out = gh("release", "view", RELEASE, "--json", "assets", "-q", ".assets[].name", check=False)
    if out.returncode != 0:
        gh("release", "create", RELEASE, "--title", "Stored Shorts", "--notes",
           "Video copies of published Shorts, kept for Facebook after the studio frees its hosted copy.")
        return set()
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.removeprefix("www.")


def caption(spec: dict) -> str:
    tags = [t for t in spec.get("hashtags") or [] if t.lower().lstrip("#") not in ("shorts", "youtubeshorts", "ytshorts")][:3]
    blocks = [spec["title"].strip(), re.sub(r"https?://\S+", "", spec.get("description") or "").strip()]
    if spec.get("sources"):
        blocks.append("Sources: " + ", ".join(dict.fromkeys(domain(s) for s in spec["sources"])))
    blocks.append(DISCLOSURE)
    tail = " ".join(tags)
    text = "\n\n".join(b for b in blocks if b)
    return text[: CAPTION_CHARS - len(tail) - 2].rstrip() + ("\n\n" + tail if tail else "")


def live_shorts() -> list[dict]:
    shorts = []
    for record_path in sorted((SOURCE_DIR / "content" / "episodes").glob("*/publish.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("status") != "sent" or not record.get("youtube_url") or not record.get("sent_at"):
            continue
        spec_path = record_path.parent / "short.yaml"
        if not spec_path.exists():
            continue
        record["spec"] = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        shorts.append(record)
    return shorts


def download(url: str, dest: Path) -> bool:
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            if r.status_code != 200:
                return False
            with dest.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        return dest.stat().st_size > 100_000
    except requests.RequestException:
        return False


def from_youtube(url: str, dest: Path) -> bool:
    r = subprocess.run(["yt-dlp", "-q", "--no-warnings", "-f", "bv*[height<=1920][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
                        "--merge-output-format", "mp4", "-o", str(dest), url], capture_output=True, text=True)
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 100_000


def harvest(allow_youtube: bool = False) -> None:
    library = load(LIBRARY, {})
    stored = released()
    with tempfile.TemporaryDirectory() as tmp:
        for record in live_shorts():
            sid = record["id"]
            entry = library.setdefault(sid, {"id": sid})
            entry.update({
                "title": record["spec"]["title"],
                "caption": caption(record["spec"]),
                "youtube_url": record["youtube_url"],
                "sent_at": record["sent_at"],
                "views": (record.get("metrics") or {}).get("views", 0),
            })
            name = f"{sid}.mp4"
            if name in stored:
                entry["stored"] = True
                continue
            dest = Path(tmp) / name
            got = any(u and download(u, dest) for u in (record.get("media_url"), record.get("fetched_url")))
            if not got and allow_youtube:
                got = from_youtube(record["youtube_url"], dest)
            if got:
                gh("release", "upload", RELEASE, str(dest), "--clobber")
                entry["stored"] = True
                log(f"stored {sid} ({dest.stat().st_size // 1024} KB)")
            else:
                entry["stored"] = False
                log(f"{sid}: hosted copy gone; run `python3 fbposter.py backfill` on the Mac")
    save(LIBRARY, dict(sorted(library.items())))


def token() -> str:
    value = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not value:
        sys.exit("FB_PAGE_TOKEN is not set")
    return value


def graph(method: str, path: str, **params) -> dict:
    params["access_token"] = token()
    r = requests.request(method, f"{GRAPH}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=120)
    body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
    if r.status_code != 200 or "error" in body:
        message = (body.get("error") or {}).get("message") or r.text[:300]
        raise RuntimeError(f"Graph {method} {path.split('?')[0]}: {message}")
    return body


def choose(library: dict, posted: dict) -> dict | None:
    now = datetime.now(timezone.utc)
    ready = []
    for entry in library.values():
        state = posted.get(entry["id"], {})
        if not entry.get("stored") or state.get("video_id") or state.get("tries", 0) >= MAX_TRIES:
            continue
        sent = datetime.fromisoformat(entry["sent_at"].replace("Z", "+00:00"))
        age = now - sent
        if age < MIN_AGE:
            continue
        ready.append((entry.get("views", 0) / max(age.total_seconds() / 86400, 1.0), entry))
    ready.sort(key=lambda pair: -pair[0])
    return ready[0][1] if ready else None


def posted_today(posted: dict) -> int:
    today = datetime.now(AUDIENCE_TZ).date()
    return sum(1 for p in posted.values() if p.get("posted_at")
               and datetime.fromisoformat(p["posted_at"]).astimezone(AUDIENCE_TZ).date() == today)


def publish(entry: dict, video: Path) -> dict:
    page = graph("GET", "me", fields="id,name")
    start = graph("POST", f"{page['id']}/video_reels", upload_phase="start")
    video_id = start["video_id"]
    size = video.stat().st_size
    with video.open("rb") as f:
        r = requests.post(f"{RUPLOAD}/{video_id}", data=f, timeout=600,
                          headers={"Authorization": f"OAuth {token()}", "offset": "0", "file_size": str(size)})
    if r.status_code != 200 or not r.json().get("success"):
        raise RuntimeError(f"upload failed: {r.text[:300]}")
    graph("POST", f"{page['id']}/video_reels", upload_phase="finish", video_id=video_id,
          video_state="PUBLISHED", description=entry["caption"])
    status = {}
    for _ in range(40):
        time.sleep(15)
        status = graph("GET", video_id, fields="status,permalink_url").get("status", {})
        if status.get("video_status") in ("ready", "published") or (status.get("publishing_phase") or {}).get("status") == "complete":
            break
        if status.get("video_status") == "error":
            raise RuntimeError(f"Facebook could not process the video: {json.dumps(status)[:300]}")
    return {"video_id": video_id, "status": status.get("video_status"), "page": page["name"]}


def post() -> None:
    library, posted = load(LIBRARY, {}), load(POSTED, {})
    if posted_today(posted) >= PER_DAY:
        log(f"already posted {PER_DAY} today")
        return
    entry = choose(library, posted)
    if not entry:
        log("nothing ready to post")
        return
    state = posted.setdefault(entry["id"], {"tries": 0})
    state["tries"] += 1
    with tempfile.TemporaryDirectory() as tmp:
        try:
            gh("release", "download", RELEASE, "-p", f"{entry['id']}.mp4", "-D", tmp)
            result = publish(entry, Path(tmp) / f"{entry['id']}.mp4")
            state.update(result, posted_at=datetime.now(timezone.utc).isoformat(), error=None)
            log(f"posted {entry['id']} ({entry['title']}) as Reel {result['video_id']}: {result['status']}")
        except Exception as exc:
            state["error"] = str(exc)[:500]
            save(POSTED, posted)
            raise
    save(POSTED, posted)


def status() -> None:
    library, posted = load(LIBRARY, {}), load(POSTED, {})
    for sid, entry in library.items():
        p = posted.get(sid, {})
        mark = "posted " + p["posted_at"][:16] if p.get("video_id") else ("error: " + p["error"][:60] if p.get("error") else "")
        log(f"{sid}  stored={entry.get('stored')}  views={entry.get('views')}  {entry['title'][:40]}  {mark}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"harvest": harvest, "post": post, "status": status, "backfill": lambda: harvest(allow_youtube=True)}[command]()
