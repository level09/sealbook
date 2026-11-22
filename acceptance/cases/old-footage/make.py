"""Build the case "The footage that was two years old" into this directory.

  uv run python acceptance/cases/old-footage/make.py           # derive the copies, write case.json
  uv run python acceptance/cases/old-footage/make.py --fetch   # first download and re-encode the Commons clips
  uv run python acceptance/cases/old-footage/make.py --record  # read the clips on the live routes

The footage is real and openly licensed (Wikimedia Commons). The story around it is
invented: the place names, accounts, captions and posting dates are fictional, and no
real place is presented as the scene of the claim. The planted copies are derived from
files/original.mp4 (one from the viral copy) with ffmpeg; -bitexact and one thread keep
them byte-stable for a given ffmpeg build. --fetch depends on Commons transcodes, which
Commons may regenerate; commons.json records the SHA-256 of each one used.
ATTRIBUTION.md is written from files/commons.json.
"""

import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
FILES = HERE / "files"
UA = {"User-Agent": "sealbook-fixture-builder/1.0"}
API = "https://commons.wikimedia.org/w/api.php"

# key: (Commons file, derivative height, start s, seconds, fictional post date,
#       fictional account, fictional caption). Every non-copy is a different recording.
ORIGINAL = {
    "original": (
        "File:Linz Ebelsberg Hochwasser Traunbruecke.webm",
        480,
        0,
        21,
        "2024-06-03",
        "Ostra Valley Weather",
        "The Ostra at the Mill Bridge in Lindmark this afternoon. Water still rising.",
    ),
}
LOOKALIKES = {
    "flood_promenade": (
        "File:Hochwasser 2013 Linz Donau.webm",
        240,
        0,
        7,
        "2026-06-02",
        "Harbour Street Cam",
        "Promenade under water by the old castle, Lindmark.",
    ),
    "flood_footbridge": (
        "File:Rozvodněná Brzina u Vletic, 2010-07.webm",
        240,
        2,
        12,
        "2026-06-02",
        "Kerrow Farm",
        "Our footbridge is gone. The Ostra is higher than we have ever seen it.",
    ),
    "flood_garden": (
        "File:River-Isbourne-Flooding.ogv",
        240,
        4,
        12,
        "2026-06-01",
        "N. Ashby",
        "The brook at the bottom of the garden this morning, Lower Lindmark.",
    ),
    "flood_arches": (
        "File:Anglin en crue à Mauvières.webm",
        240,
        0,
        11,
        "2026-06-02",
        "Ostra Anglers",
        "The arches at Pell Weir. Brown water right up to the stone.",
    ),
    "flood_channel": (
        "File:Rambla de Benipila tras la DANA (20190913 130120).webm",
        240,
        0,
        12,
        "2026-06-03",
        "Lindmark Civic",
        "The flood channel by the ring road is running full.",
    ),
    "flood_ford": (
        "File:Warkworth ford in flood.webm",
        240,
        0,
        12,
        "2026-06-02",
        "Hill Road Watch",
        "Do not try the ford on Hill Road today.",
    ),
}
PILE = {
    "pile_plane_clouds": ("File:2013-08-06 Wolken über China.ogv", 240, 0, 8),
    "pile_cumulus": ("File:2022-06-03 LJUBLJANA Doma zanimivi oblaki.webm", 240, 4, 8),
    "pile_arcus": (
        "File:Arcus passing overhead 28 July 2021 Galley Common.webm",
        240,
        4,
        8,
    ),
    "pile_ghat_mist": ("File:Chorla ghat sep 2020.ogv", 240, 0, 8),
    "pile_lone_tree": (
        "File:Ash Tree-1011721, Dingle Peninsula, Co. Kerry, Ireland.webm",
        240,
        2,
        8,
    ),
    "pile_weir_pool": ("File:Čeveljuša Trebižat.webm", 240, 0, 8),
    "pile_falls_brown": ("File:Athirapally falls.webm", 240, 4, 8),
    "pile_falls_tall": ("File:Karkloof Falls.webm", 240, 0, 8),
    "pile_falls_small": ("File:Kullaojan putous 7.webm", 240, 0, 8),
    "pile_falls_cliff": ("File:Nohsngithiang Falls.webm", 240, 4, 8),
    "pile_rain_puddle": ("File:20141103Regen.ogv", 240, 2, 8),
    "pile_lake_drops": ("File:110811-water-droplets-on-lake.ogv", 240, 4, 8),
    "pile_rain_trees": ("File:Averse de pluie.webm", 240, 10, 8),
    "pile_snow_roofs": ("File:20121203Schnee Hockenheim.ogv", 240, 2, 8),
    "pile_snow_wall": ("File:2013-01-20 neige, Paris 01.ogv", 240, 10, 8),
    "pile_wide_river": ("File:A view of the Neva 7095.ogv", 240, 0, 8),
    "pile_lake_ice": ("File:Angara-Lake Baikal.ogv", 240, 0, 8),
    "pile_marsh_drone": ("File:Beaverhead-River-Drone-Shot.webm", 240, 0, 8),
    "pile_rail_line": ("File:Desiro, 2012.webm", 240, 4, 8),
    "pile_statue_fountain": ("File:Fountain.ogv", 240, 0, 8),
}
PILE_DATES = [f"2026-0{5 + i % 2}-{1 + (i * 7) % 28:02d}" for i in range(len(PILE))]

LOGO = (
    "drawbox=x=iw-iw/5-12:y=ih-ih/7-12:w=iw/5:h=ih/7:color=white@0.9:t=fill,"
    "drawbox=x=iw-iw/5-12:y=ih-ih/7-12:w=iw/5:h=ih/7:color=0x1d3557:t=4,"
    "drawbox=x=0:y=0:w=iw:h=ih/9:color=0xc1121f@0.85:t=fill"
)
# key: (transformations, how, input options, video filter, video rate, keep sound,
#       fictional post date, fictional account, fictional caption)
COPIES = {
    "copy_lowrate": (
        ["reencode", "downscale"],
        "re-encoded at 120 kbit/s and 320 pixels wide",
        [],
        "scale=320:-2",
        ["-b:v", "120k"],
        True,
        "2026-06-01",
        "Daily Weather Clips",
        "Massive flooding right now",
    ),
    "copy_trim": (
        ["trim", "reencode"],
        "trimmed to seconds 5 to 17",
        ["-ss", "5", "-t", "12"],
        "null",
        ["-crf", "30"],
        True,
        "2026-06-02",
        "Lindmark Now",
        "Mill Bridge this morning. Stay away from the river.",
    ),
    "copy_logo": (
        ["logo", "reencode"],
        "a red banner across the top and a logo block in the lower right",
        [],
        LOGO,
        ["-crf", "32"],
        True,
        "2026-06-02",
        "Breaking Weather 24",
        "BREAKING: Ostra floods Lindmark",
    ),
    "copy_crop": (
        ["crop", "reencode"],
        "cropped to the central 70% and scaled back up",
        [],
        "crop=iw*7/10:ih*7/10,scale=640:-2",
        ["-crf", "32"],
        True,
        "2026-06-02",
        "Valley Storm Chasers",
        "Close look at the river in Lindmark today",
    ),
    "copy_mirror": (
        ["mirror", "reencode"],
        "mirrored left to right",
        [],
        "hflip",
        ["-crf", "32"],
        True,
        "2026-06-02",
        "M. Ferrow",
        "Unreal scenes in Lindmark this morning",
    ),
    "copy_vertical": (
        ["crop", "vertical", "reencode"],
        "cut to an upright 9:16 frame from the centre, as a phone short",
        [],
        "crop=ih*9/16:ih,scale=360:640",
        ["-crf", "32"],
        True,
        "2026-06-03",
        "lindmark.shorts",
        "Ostra today",
    ),
    "copy_viral": (
        ["crop", "mirror", "trim", "logo", "reencode", "downscale"],
        "cropped to the central 80%, mirrored, trimmed to seconds 3 to 17, "
        "logo-stamped and re-encoded at 480 pixels wide",
        ["-ss", "3", "-t", "14"],
        "crop=iw*4/5:ih*4/5,hflip,scale=480:-2," + LOGO,
        ["-crf", "32"],
        True,
        "2026-06-02",
        "World Weather Now",
        "JUST IN: the Ostra bursts its banks in Lindmark this morning",
    ),
    "copy_viral_muted": (
        ["crop", "mirror", "trim", "logo", "reencode", "downscale", "muted"],
        "the viral copy downloaded, muted and uploaded again",
        [],
        "null",
        ["-crf", "32"],
        False,
        "2026-06-03",
        "Weather Repost Hub",
        "Lindmark right now (sound off)",
    ),
    "copy_short_muted": (
        ["crop", "vertical", "mirror", "reencode", "muted"],
        "cut to an upright 9:16 frame, mirrored, with its sound removed",
        ["-ss", "2", "-t", "15"],
        "crop=ih*9/16:ih,hflip,scale=360:640",
        ["-crf", "32"],
        False,
        "2026-06-03",
        "clipsdaily.reel",
        "Flood in Lindmark, no words",
    ),
}
# A copy made from another copy, not from the original. Order in COPIES builds parents first.
PARENT = {"copy_viral_muted": "copy_viral"}


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return "audio" in out.split()


def encode(src: Path, out: Path, pre=(), vf="null", rate=("-crf", "30"), sound=True):
    audio = (
        ["-c:a", "aac", "-b:a", "48k", "-ac", "1", "-ar", "44100"]
        if sound and has_audio(src)
        else ["-an"]
    )
    ffmpeg(
        *pre,
        "-i",
        str(src),
        "-map",
        "0:v:0",
        *(["-map", "0:a:0"] if audio[0] != "-an" else []),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        *rate,
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",
        *audio,
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        str(out),
    )
    return out


def commons(titles):
    pages = {}
    for i in range(0, len(titles), 20):
        query = urllib.parse.urlencode(
            {
                "action": "query",
                "format": "json",
                "titles": "|".join(titles[i : i + 20]),
                "prop": "videoinfo",
                "viprop": "url|derivatives|sha1|extmetadata|size",
            }
        )
        request = urllib.request.Request(f"{API}?{query}", headers=UA)
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
        for page in body["query"]["pages"].values():
            pages[page["title"]] = page["videoinfo"][0]
        time.sleep(2)
    return pages


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def fetch():
    """Download each clip's Commons transcode and cut the excerpt used here."""
    table = {
        **{k: v[:4] for k, v in ORIGINAL.items()},
        **{k: v[:4] for k, v in LOOKALIKES.items()},
        **PILE,
    }
    info = commons(sorted({v[0] for v in table.values()}))
    record = {}
    with tempfile.TemporaryDirectory(prefix="old-footage-") as directory:
        for key, (title, height, start, seconds) in table.items():
            v = info[title]
            meta = {k: x["value"] for k, x in v["extmetadata"].items()}
            src = next(
                d["src"]
                for d in v["derivatives"]
                if d["src"].endswith(f".{height}p.vp9.webm")
            )
            raw = Path(directory) / f"{key}.webm"
            # upload.wikimedia.org throttles urllib's requests; curl with retries is let through.
            subprocess.run(
                [
                    "curl",
                    "-sSfL",
                    "--retry",
                    "5",
                    "--retry-delay",
                    "10",
                    "-A",
                    UA["User-Agent"],
                    "-o",
                    str(raw),
                    src,
                ],
                check=True,
            )
            time.sleep(2)
            vf = "scale=640:-2" if key == "original" else "scale=-2:240"
            encode(
                raw, FILES / f"{key}.mp4", ["-ss", str(start), "-t", str(seconds)], vf
            )
            record[key] = {
                "title": title,
                "page": v["descriptionurl"],
                "transcode": src.split("?")[0],
                "transcode_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "excerpt_s": [start, start + seconds],
                "commons_sha1": v["sha1"],
                "artist": plain(meta.get("Artist", "")),
                "credit": plain(meta.get("Credit", "")),
                "licence": meta.get("LicenseShortName", ""),
                "licence_url": meta.get("LicenseUrl", ""),
                "date": plain(meta.get("DateTimeOriginal", "")),
                "description": plain(meta.get("ImageDescription", ""))[:400],
            }
    (FILES / "commons.json").write_text(
        json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def derive():
    for key, (_, _, pre, vf, rate, sound, *_) in COPIES.items():
        src = FILES / f"{PARENT.get(key, 'original')}.mp4"
        encode(src, FILES / f"{key}.mp4", pre, vf, rate, sound)


def attribution(record):
    lines = [
        "# Footage in this case",
        "",
        "Every clip is real footage from Wikimedia Commons, re-encoded to an excerpt.",
        "The case gives it an invented place (Lindmark, the river Ostra), invented",
        "accounts, captions and posting dates. None of it shows what the case says it",
        "shows; the real subject is the Commons description below. Credits are copied",
        "from the Commons API (`extmetadata`) by `make.py --fetch` into `commons.json`.",
        "",
        "Origin: **real** means the Commons footage, cut and re-encoded only.",
        "**Edited** means the same footage altered by `make.py` as listed. Nothing here",
        "is generated.",
        "",
    ]
    for key, r in record.items():
        lines += [
            f"## {key}.mp4",
            "",
            "- Origin: real",
            f"- Commons file: {r['title']}",
            f"- Source: {r['page']}",
            f"- Author (Artist field): {r['artist']}",
            *([f"- Credit: {r['credit']}"] if r["credit"] else []),
            f"- Licence: {r['licence']}"
            + (f", {r['licence_url']}" if r["licence_url"] else ""),
            f"- Date (DateTimeOriginal): {r['date']}",
            f"- What it really shows: {r['description'] or 'see the Commons page'}",
            f"- Excerpt: seconds {r['excerpt_s'][0]} to {r['excerpt_s'][1]} of the Commons transcode",
            "",
        ]
    r = record["original"]
    for key, (_, how, *_) in COPIES.items():
        lines += [
            f"## {key}.mp4",
            "",
            f"- Origin: edited (altered from {PARENT.get(key, 'original')}.mp4: {how})",
            f"- Commons file: {r['title']}",
            f"- Source: {r['page']}",
            f"- Author (Artist field): {r['artist']}",
            f"- Licence: {r['licence']}"
            + (f", {r['licence_url']}" if r["licence_url"] else ""),
            "",
        ]
    (FILES / "ATTRIBUTION.md").write_text("\n".join(lines), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def day(iso: str) -> str:
    d = datetime.date.fromisoformat(iso)
    return f"{d.day} {d:%B %Y}"


def posted(key, date, account, caption):
    """A clip posted with a caption, and the post as a capture keeps it: the
    account, the day and the words, which the seeder ingests beside the clip."""
    return {
        "title": f"Video posted by {account} on {day(date)}",
        "doc_type": "video",
        "sensitivity": "ordinary",
        "date": date,
        "file": f"{key}.mp4",
        "account": account,
        "caption": caption,
        "post": {
            "title": f"Post by {account} on {day(date)}",
            "doc_type": "social_post",
            "text": f"{account}\nPosted {day(date)}\n\n{caption}\n\n"
            "Fictional: the account, the caption and the date are invented.\n",
        },
    }


# What the desk types into the search box in act 3: words for what the viral clip
# shows, and the bridge the captions name. Act 4 runs the first search again.
SEARCHES = ["submerged trees", "bridge"]


def acts(sources):
    """The investigation in six acts (docs/case-studies.md, "Acts"). The clips
    arrive in posting order within each act, so each is fingerprinted against the
    clips already held; the 2024 original arrives last, as the archive is added."""

    def by_date(keys):
        return sorted(keys, key=lambda k: (sources[k]["date"], k))

    rest = set(sources) - {"copy_viral", "original"}
    day_one = by_date(k for k in rest if sources[k]["date"] <= "2026-06-02")
    inbox = by_date(rest - set(day_one))
    read = ["read", "claims"]
    return [
        {
            "title": "The clip that spread",
            "day": "2026-06-02",
            "story": "A clip of the river Ostra over its banks in Lindmark spreads "
            "under 'JUST IN' and reaches the newsroom's tip line with its post. "
            "The app listens for speech, looks at eight frames and proposes a "
            "title, a description and labels for what the clip shows. The caption "
            "is what the post reports, unconfirmed.",
            "shows": ["reading", "first pass"],
            "sources": ["copy_viral"],
            "steps": read,
            "see": ["/evidence/{source:copy_viral}"],
        },
        {
            "title": "Reposts and other floods",
            "day": "2026-06-02",
            "story": "Over the day the tip line fills with reposts, other people's "
            "flood videos and clips that only look like weather. The app reads "
            "each one as it arrives and fingerprints its picture and sound. "
            "Several reposts already match each other by their sound: one "
            "recording is spreading, but nothing here says where it began.",
            "shows": ["reading", "copies"],
            "sources": day_one,
            "steps": [*read, "matches"],
            "see": [
                "/evidence/{source:copy_trim}",
                "/evidence/{source:flood_footbridge}",
            ],
        },
        {
            "title": "Searching the pile by what it shows",
            "day": "2026-06-03",
            "story": "More clips arrive in a batch from the inbox. Instead of "
            "scrolling 35 videos, the desk searches for what the viral clip "
            "shows, submerged trees, and finds the reposts and one other flood "
            "by the app's own description of them. A search for the bridge the "
            "captions name finds two other people's bridges and one post; the "
            "app saw no bridge in the viral clip. A description says what the "
            "frames show, never where or when they were filmed.",
            "shows": ["reading", "search"],
            "sources": inbox,
            "steps": [
                *read,
                *({"do": "search", "query": q} for q in SEARCHES),
            ],
            "see": [f"/search?q={urllib.parse.quote_plus(q)}" for q in SEARCHES],
        },
        {
            "title": "The archive from two years ago",
            "day": "2026-06-03",
            "story": "A producer remembers a flood clip from June 2024 and the desk "
            "adds Ostra Valley Weather's video from the archive. Its fingerprints "
            "tie it to the viral clip and to six other reposts by their sound, and "
            "two of those by picture too. The search for submerged trees now "
            "finds the 2024 video as well.",
            "shows": ["copies"],
            "sources": ["original"],
            "steps": [
                *read,
                "matches",
                {"do": "search", "query": SEARCHES[0]},
            ],
            "see": [
                "/evidence/{source:original}",
                f"/search?q={urllib.parse.quote_plus(SEARCHES[0])}",
            ],
        },
        {
            "title": "Side by side",
            "day": "2026-06-03",
            "story": "The analyst opens the viral clip beside the 2024 video, "
            "plays them together and confirms each match between the copies "
            "and the original as the same recording, with her reason and her "
            "passkey. The app proposed nothing between clips that are not the "
            "same footage, so there is nothing to decline. The muted, cropped, "
            "mirrored short matched nothing and stays unexplained.",
            "shows": ["copies"],
            "sources": [],
            "steps": [
                {
                    "do": "confirm copies",
                    "relationship": "same_recording",
                    "why": "Side by side the same footage: the same river, trees "
                    "and buildings and the same sound, cut, mirrored or stamped "
                    "differently.",
                },
            ],
            "see": [
                "/evidence/{source:copy_viral}?with={source:original}",
                "/evidence/{source:original}",
            ],
        },
        {
            "title": "The conclusion",
            "day": "2026-06-03",
            "story": "The analyst writes that the clip shared as new is the 2024 "
            "video, on the two posts' words beside the confirmed matches, names "
            "the alternative she set aside and what is not established, and "
            "signs it; a reviewer agrees.",
            "shows": ["conclusion", "peer review"],
            "sources": [],
            "steps": ["conclusion"],
            "see": ["/cases/overview"],
        },
    ]


def case():
    sources = {}
    for key, (_, _, _, _, date, account, caption) in {**ORIGINAL, **LOOKALIKES}.items():
        sources[key] = posted(key, date, account, caption)
    for key, (_, _, _, _, _, _, date, account, caption) in COPIES.items():
        sources[key] = posted(key, date, account, caption)
    for (key, _), date in zip(PILE.items(), PILE_DATES, strict=True):
        sources[key] = {
            "title": f"Video in the tip line inbox, {key.removeprefix('pile_').replace('_', ' ')}",
            "doc_type": "video",
            "sensitivity": "ordinary",
            "date": date,
            "file": f"{key}.mp4",
        }
    for s in sources.values():
        s["sha256"] = sha256(FILES / s["file"])
        # The app reads every clip itself: speech to text, then the first pass on
        # eight frames and the transcript. The posts stay typed captures.
        s["read"] = True
    # Each caption is the event its post reports, unconfirmed: no person, and a
    # place with no coordinates.
    claims = [
        {
            "key": f"{key}.caption",
            "record": key,
            "prop": "reports",
            "event": "other",
            "value": s["caption"],
            "from": s["date"],
            "to": s["date"],
            "place": "lindmark",
            "tier": "unconfirmed",
        }
        for key, s in sources.items()
        if "caption" in s
    ]
    data = {
        "about": "Planted truth for acceptance/cases/old-footage/eval.py, by make.py. "
        "The footage is real (see files/ATTRIBUTION.md); places, accounts, captions "
        "and dates are fictional.",
        "story": {
            "title": "The footage that was two years old",
            "kind": "verification",
            "puzzle": "On the morning of 2 June 2026 a clip of the river Ostra over its "
            "banks in Lindmark spreads under the caption 'JUST IN'. A newsroom's tip "
            "line holds 36 clips from the same days, many of them brown water under "
            "grey skies, and has to say which ones are new.",
            "twist": "The viral clip is a cropped, mirrored, trimmed, logo-stamped "
            "re-encode of a video Ostra Valley Weather posted on 3 June 2024. Its "
            "picture no longer matches but its sound does, and the fingerprints tie "
            "it and seven other reposts to the two-year-old original.",
            "rests_on": ["video_fingerprints", "audio_landmarks", "media_match_review"],
            "fictional": True,
        },
        "investigation": "case-old-footage",
        "today": "2026-06-03",
        "places": {
            "lindmark": {
                "name_en": "Lindmark",
                "note": "Invented town on the invented river Ostra. The footage was "
                "filmed elsewhere; see files/ATTRIBUTION.md.",
                "lon": None,
                "lat": None,
                "precision_m": None,
                "fictional": True,
            }
        },
        "sources": sources,
        "claims": claims,
        "truth": {
            "original": "original",
            "copies": [
                {
                    "file": key,
                    "of": PARENT.get(key, "original"),
                    "transformations": t,
                    "how": how,
                    "sound": sound,
                }
                for key, (t, how, _, _, _, sound, *_) in COPIES.items()
            ],
            "lookalikes": dict.fromkeys(
                LOOKALIKES, "a different recording of a river in flood; not a copy"
            ),
            "pile": sorted(PILE),
            "known_misses": {
                "copy_short_muted": "no proposal with any member of the family: "
                "the crop and mirror defeat the frame hash and there is no sound "
                "to align",
            },
            "note": "transformations are relative to the original. Every pair inside "
            "{original, copies} is the same recording; no other pair in this set is. "
            "known_misses are measured by eval.py, not hidden.",
        },
        # The strong matches inside the planted family are accepted first ("copies");
        # a match cannot be cited, so the conclusion cites the two posts' words.
        "conclusion": {
            "text": "The clip shared as new on 2 June 2026 is a copy of the video "
            "Ostra Valley Weather posted on 3 June 2024.",
            "standard": "clear_and_convincing",
            "rests_on": ["copies", "original.caption", "copy_viral.caption"],
            "alternatives": [
                {
                    "text": "Two recordings of the same flood from the same spot",
                    "why": "The sound of the two clips lines up point for point, and "
                    "six other reposts match the 2024 video too.",
                }
            ],
            "not_established": [
                "Who made the first altered copy.",
                "One muted, cropped and mirrored copy matched nothing.",
            ],
            "peer_review": True,
        },
        "acts": acts(sources),
    }
    (HERE / "case.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return data


def main():
    FILES.mkdir(exist_ok=True)
    if "--fetch" in sys.argv:
        fetch()
    derive()
    attribution(json.loads((FILES / "commons.json").read_text(encoding="utf-8")))
    data = case()
    total = sum((FILES / s["file"]).stat().st_size for s in data["sources"].values())
    print(f"{len(data['sources'])} clips, {total / 1e6:.1f} MB")
    if "--record" in sys.argv:
        record()


def record():
    """Read the case once on the live routes, in a throwaway database, and write
    recordings.json: local speech to text on each clip with sound, then one first
    pass per clip (eight frames and the transcript) and one extraction per
    transcript with words in it. Needs OPENROUTER_API_KEY and the speech weights
    (SPEECH_MODEL_DIR, `stk speech-model`). Never edit recordings.json by hand."""
    import os
    import secrets

    sys.path.insert(0, str(HERE.parents[2]))
    from alembic import command
    from sqlalchemy.engine import make_url
    from stk.migrations import build_alembic_config, throwaway_database_url

    from sealbook.settings import Config

    with (
        throwaway_database_url(Config.SQLALCHEMY_DATABASE_URI, sync=True) as url,
        tempfile.TemporaryDirectory() as blob_dir,
    ):
        command.upgrade(build_alembic_config("sealbook", url, quiet=True), "heads")
        env = os.environ | {
            "SQLALCHEMY_DATABASE_URI": make_url(url)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False),
            "BLOB_DIR": blob_dir,
        }
        stk = ["uv", "run", "stk"]
        password = secrets.token_hex(16) + "Aa1!"
        install = [*stk, "install", "-e", "owner@example.com", "-p", password]
        subprocess.run(install, env=env, check=True)
        seed = [*stk, "demo-seed", "--case", HERE.name, "--record"]
        subprocess.run(seed, env=env, check=True)


if __name__ == "__main__":
    main()
