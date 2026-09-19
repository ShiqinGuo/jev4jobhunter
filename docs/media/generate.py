"""Job Hunter: personal conditions change the destination of each opportunity."""

import argparse
import json
import math
from pathlib import Path

from motion import Canvas, configure_language, ease, lerp, render, spring


def canvas():
    return Canvas("#FAF7F2", "#293B53", "#7D8794", "#C16A36", "#FBE8D9", "#E5DDD4")


def profile(c, x, y, compact=False):
    c.card(x, y, 260, 270 if not compact else 234)
    c.circle(x + 43, y + 44, 21, "#FBE8D9")
    c.circle(x + 43, y + 39, 6, "#EF9560")
    c.rect((x + 32, y + 48, x + 54, y + 57), "#EF9560", 7)
    c.text(x + 80, y + 22, "YOUR NEXT ROLE", 13, c.muted, True)
    c.text(x + 80, y + 44, "Starts with you", 18, bold=True)
    c.line([(x + 22, y + 85), (x + 238, y + 85)])
    for i, value in enumerate(["Python / FastAPI", "Hangzhou", "Backend development"]):
        c.pill(x + 22, y + 105 + i * 42, value, True)
    if not compact:
        c.text(x + 22, y + 237, "Facts, goals, preferences", 14, c.muted)


def job_card(c, x, y, title, detail, active=False, small=False):
    w, h = (276, 98) if small else (340, 124)
    c.card(
        x, y, w, h, "#FFF7EF" if active else "#ffffff", "#EF9560" if active else None
    )
    c.text(x + 20, y + 17, title, 20 if not small else 18, bold=True)
    c.text(x + 20, y + 51, detail, 15, c.muted)
    if not small:
        c.line(
            [(x + 20, y + 88), (x + 202, y + 88)], "#EF9560" if active else c.edge, 4
        )
        c.line([(x + 20, y + 103), (x + 143, y + 103)], c.edge, 3)


def scene(t):
    c = canvas()
    if t < 8:
        c.chrome(
            "JOB HUNTER",
            "An opportunity is only useful if it fits you.",
            1,
            "Your conditions give every job a destination.",
            t,
        )
        profile(c, 78, 184)
        p = ease((t - 1) / 1.4)
        positions = [
            (lerp(786, 686, p), 153),
            (lerp(824, 732, p), 299),
            (lerp(852, 777, p), 428),
        ]
        paths = [
            [(338, 304), (467, 304), (467, 215), (686, 215)],
            [(338, 346), (496, 346), (496, 246), (686, 246)],
        ]
        for i, path in enumerate(paths):
            c.line(path)
            if t > 2.7 + i * 0.6:
                c.packet(path, ease((t - 2.7 - i * 0.6) / 1.3))
        x, y = positions[0]
        # The matching job moves toward the person, leaving the other jobs behind.
        if t > 5:
            x -= 110 * spring(min((t - 5) / 1.3, 1.5))
        job_card(c, x, y, "Python backend engineer", "Hangzhou  /  FastAPI", t > 4.2)
        if t > 5.8:
            c.pill(x + 20, y + 139, "Worth a closer look", True)
        x, y = positions[1]
        y += 23 * ease((t - 5) / 1)
        job_card(
            c, x, y, "Data platform engineer", "Location not specified", small=True
        )
        x, y = positions[2]
        x += 24 * ease((t - 5) / 1)
        job_card(
            c,
            x,
            y,
            "Senior backend engineer",
            "Shanghai  /  outside city preference",
            small=True,
        )
        if t > 5.8:
            c.pill(408, 434, "Keep unknowns visible", size=14)
    elif t < 16:
        q = t - 8
        c.chrome(
            "JOB HUNTER",
            "See the reason. Keep the uncertainty.",
            2,
            "Read one job in detail, then decide what happens next.",
            t,
        )
        profile(c, 64, 204, True)
        c.card(625, 178, 423, 302, "#ffffff", "#EF9560")
        c.text(649, 199, "Python backend engineer", 23, bold=True)
        c.text(649, 232, "Full job description", 14, c.muted)
        evidence = [
            ("Python / FastAPI", "Skills match", 286),
            ("Hangzhou", "Location matches", 342),
            ("Team stack details", "Ask, don't assume", 398),
        ]
        for i, (value, label, y) in enumerate(evidence):
            active = q > 0.8 + i * 1.1
            c.rect((648, y - 3, 1025, y + 36), c.soft if active and i < 2 else c.bg, 9)
            c.text(664, y + 5, value, 18)
            if active:
                if i < 2:
                    path = [
                        (324, 324 + i * 42),
                        (435 + i * 32, 324 + i * 42),
                        (435 + i * 32, y + 16),
                        (648, y + 16),
                    ]
                    c.line(path)
                    c.packet(path, ease((q - 0.8 - i * 1.1) / 0.8))
                    c.tick(1005, y + 17)
                else:
                    c.circle(1005, y + 16, 11, "#FBE8D9")
                    c.text(1001, y + 3, "?", 18, c.accent, True)
                c.text(
                    465 if i < 2 else 648,
                    258 if i == 0 else (373 if i == 1 else 451),
                    label,
                    13,
                    c.accent,
                )
        if q > 4.5:
            c.pill(91, 467, "Evidence, not a mystery score", True, 14)
    else:
        q = t - 16
        c.chrome(
            "JOB HUNTER",
            "Move forward. Remember what happened.",
            3,
            "Authorized action → verified receipt → a durable record.",
            t,
        )
        xs = [86, 431, 777]
        labels = ["A suitable job", "Platform receipt", "Local history"]
        notes = [
            "Explicit authorization",
            "Verify the outcome",
            "Resume without repeats",
        ]
        for i in range(2):
            path = [(xs[i] + 250, 318), (xs[i + 1], 318)]
            c.line(path)
            if q > 1 + i * 1.8:
                c.packet(path, ease((q - 1 - i * 1.8) / 1.2))
        for i, x in enumerate(xs):
            active = q > i * 1.8
            lift = 0
            age = q - i * 1.8
            if 0 < age < 1:
                lift = 8 * math.sin(age * math.pi)
            y = 223 - lift
            c.card(x, y, 250, 187, "#FFF7EF" if active else "#ffffff")
            if i == 0:
                c.rect((x + 23, y + 19, x + 61, y + 58), c.soft, 7)
                c.line([(x + 31, y + 30), (x + 53, y + 30)], "#EF9560", 3)
                c.line([(x + 31, y + 40), (x + 47, y + 40)], "#EF9560", 3)
            elif i == 1:
                c.rect((x + 22, y + 18, x + 66, y + 49), c.soft, 10)
                c.poly([(x + 29, y + 45), (x + 29, y + 60), (x + 43, y + 45)], c.soft)
                c.tick(x + 44, y + 33)
            else:
                for row in range(3):
                    c.rect(
                        (x + 23, y + 19 + row * 14, x + 66, y + 30 + row * 14),
                        c.soft,
                        4,
                    )
                    c.circle(x + 30, y + 24 + row * 14, 2, "#EF9560")
            c.text(x + 22, y + 79, labels[i], 21, bold=True)
            c.text(x + 22, y + 116, notes[i], 15, c.muted)
            if active:
                c.tick(x + 222, y + 36)
        if q > 4.3:
            p = spring(min((q - 4.3) / 0.9, 1.5))
            c.card(lerp(591, 740, p), 441, 307, 61, c.soft)
            c.text(
                lerp(612, 761, p), 459, "Seen before? Keep the record.", 17, c.accent
            )
        c.text(90, 469, "Unknown outcome? Reconcile first.", 16, c.muted)
    return c.end()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["zh-CN", "en", "both"], default="both")
    args = parser.parse_args()
    out = Path(__file__).parent
    translations = json.loads((out / "zh-CN.json").read_text(encoding="utf-8"))
    languages = ["zh-CN", "en"] if args.language == "both" else [args.language]
    for language in languages:
        configure_language(language, translations)
        render(scene, out, language)
