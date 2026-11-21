"""Video and storyboard for the moa7amed-queue demo. Called by render_media.py.

The scenes below are the storyboard: what is on screen (recorded commands and their real
output, replayed), the captions in English and Arabic, and the narration script. Output:
silent VP9 WebM files with burned-in captions, WebVTT caption files, a thumbnail, and
storyboard.md. Long waits are compressed and labelled "Sped up"."""
from __future__ import annotations

import glob
import hashlib
import html
import io
import os
import subprocess

from PIL import Image
from playwright.sync_api import sync_playwright

import render_media as rm
from render_media import select, step

FPS = 10
W, H = 1280, 720
SCALE = 1.5
TERM_LINES = 19

INSTALL = "claude plugin marketplace add /path/to/moa7amed-tools"


def card(kind, seconds, en, ar, narr_en, narr_ar, **extra):
    return dict(kind=kind, seconds=seconds, en=en, ar=ar, narr_en=narr_en, narr_ar=narr_ar, **extra)


def term(label, items, en, ar, narr_en, narr_ar, **extra):
    return dict(kind="term", label=label, items=items, en=en, ar=ar, narr_en=narr_en, narr_ar=narr_ar,
                **extra)


def scenes(short=False):
    killed = rm.killed_step()
    title = card("title", 4,
                 "moa7amed-queue: a persistent task queue for Claude Code.",
                 "moa7amed-queue: طابور مهام دائم لـ Claude Code.",
                 "moa7amed-queue, by moa7amed. Queue, route, execute, verify.",
                 "moa7amed-queue من moa7amed. أضِف إلى الطابور، وجِّه، نفِّذ، تحقَّق.")
    end = card("end", 6,
               "Free and open source (MIT). moa7amed.com",
               "مجاني ومفتوح المصدر (رخصة MIT). moa7amed.com",
               "moa7amed-queue is free and open source. Install it from the moa7amed-tools "
               "marketplace. More at moa7amed.com.",
               "moa7amed-queue مجاني ومفتوح المصدر. ثبّته من سوق moa7amed-tools. المزيد على moa7amed.com.")
    if short:
        return [
            title,
            term("Queue", [(step("moa7amed-queue status", 0), None)],
                 "Three tasks queued. Each one is routed to a model and a workflow.",
                 "ثلاث مهام في الطابور، وكل مهمة موجَّهة إلى نموذج ومسار عمل.",
                 "Three tasks are queued, each routed to a model and a workflow.",
                 "ثلاث مهام في الطابور، وكل واحدة موجَّهة إلى نموذج ومسار عمل."),
            term("Run", [(step("moa7amed-queue run", 0), None)],
                 "The runner executes them one at a time, each in a fresh Claude Code session.",
                 "المشغّل ينفّذها واحدة تلو الأخرى، كلٌّ في جلسة Claude Code جديدة.",
                 "The runner executes them one at a time, each in a fresh session.",
                 "المشغّل ينفّذها واحدة تلو الأخرى، كلٌّ في جلسة جديدة.", speed_target=6),
            term("Agents", [(step("moa7amed-queue agents show"), [r"^Task #3", r"Measured:", r"^  - ", r"isolation:"])],
                 "For the security fix, two read-only analysis agents ran at the same time.",
                 "في الإصلاح الأمني، عمل وكيلا تحليل للقراءة فقط في الوقت نفسه.",
                 "For the security fix, two read-only analysis agents ran at the same time.",
                 "في الإصلاح الأمني، عمل وكيلا تحليل للقراءة فقط في الوقت نفسه."),
            term("Verified", [(step("git log"), None), (step("python -m unittest"), None)],
                 "One commit per task. The project's tests pass.",
                 "إيداع واحد لكل مهمة، واختبارات المشروع ناجحة.",
                 "Every result is committed, and the project's tests pass.",
                 "كل نتيجة محفوظة في git، واختبارات المشروع ناجحة."),
            end,
        ]
    return [
        title,
        card("problem", 6,
             "While Claude works on one task, queue the next ones instead of interrupting.",
             "بينما يعمل Claude على مهمة، أضِف المهام التالية إلى الطابور بدل مقاطعته.",
             "While Claude is busy with one thing, you think of the next three. Queue them "
             "instead of interrupting it.",
             "بينما يعمل Claude على مهمة، تخطر لك ثلاث مهام أخرى. أضفها إلى الطابور بدل مقاطعته."),
        term("Queue", [(step("moa7amed-queue add", 0), None), (step("moa7amed-queue add", 1), None),
                       (step("moa7amed-queue add", 2), None), (step("moa7amed-queue depend"), None)],
             "Each task is stored verbatim and routed by rules to a model and a workflow.",
             "تُحفظ كل مهمة كما كُتبت، وتوجّهها القواعد إلى نموذج ومسار عمل.",
             "Each task is stored exactly as written. Rules read it and pick a model and a "
             "workflow. Task 3 will wait for task 2.",
             "تُحفظ كل مهمة كما كُتبت تمامًا، وتقرؤها القواعد لتختار النموذج ومسار العمل. والمهمة 3 تنتظر المهمة 2."),
        term("Routing", [(step("moa7amed-queue model sonnet 3"), None),
                         (step("moa7amed-queue route"), [r"^Task #3", r"Model:", r"Recommended:",
                                                          r"Override:"])],
             "You can override the choice: task 3 runs on Sonnet; Opus was recommended.",
             "ويمكنك تجاوز الاختيار: المهمة 3 تعمل على Sonnet، وكان Opus هو الموصى به.",
             "You stay in control: task 3 is set to Sonnet by hand, although the router "
             "recommended Opus.",
             "القرار لك: ضُبطت المهمة 3 يدويًا على Sonnet، مع أن الموجِّه أوصى بـ Opus."),
        term("Workflow", [(step("moa7amed-queue status", 0), None), (step("moa7amed-queue agents evaluate"), None)],
             "The security fix is DEEP: planning first, with two read-only analysis agents.",
             "الإصلاح الأمني من نوع DEEP: تخطيط أولًا، مع وكيلَي تحليل للقراءة فقط.",
             "The security fix is DEEP work: a planning session comes first, and two read-only "
             "analysis agents are justified for it.",
             "الإصلاح الأمني عمل من نوع DEEP: تأتي جلسة التخطيط أولًا، ويُبرَّر له وكيلا تحليل للقراءة فقط."),
        term("Run", [(step("moa7amed-queue run", 0), None)],
             "One runner, one task at a time, each in a fresh Claude Code session.",
             "مشغّل واحد، ومهمة واحدة في كل مرة، كلٌّ في جلسة Claude Code جديدة.",
             "One runner executes one task at a time, each in a fresh Claude Code session, on its "
             "own model. The wait is sped up here.",
             "مشغّل واحد ينفّذ مهمة واحدة في كل مرة، كلٌّ في جلسة Claude Code جديدة وعلى نموذجها. الانتظار مُسرَّع هنا."),
        term("Approval", [(step("moa7amed-queue status", 1), [r"^Tasks", r"^ +ID", r"^ +3 "]),
                          (step("moa7amed-queue workflow 3"), [r"^Task #3", r"Model:", r"Planning:"]),
                          (step("moa7amed-queue workflow approve"), None)],
             "The security plan waits for your approval before any code changes.",
             "خطة الإصلاح الأمني تنتظر موافقتك قبل أي تغيير في الشيفرة.",
             "The plan for the security fix waits for the owner. Nothing changes until you approve it.",
             "خطة الإصلاح الأمني تنتظر المالك، ولا يتغيّر شيء حتى توافق عليها."),
        term("Implement", [(step("moa7amed-queue run", 1), None)],
             "After approval, the plan is implemented on the next run.",
             "بعد الموافقة، تُنفَّذ الخطة في التشغيل التالي.",
             "After approval, the next run implements the plan.",
             "بعد الموافقة، ينفّذ التشغيل التالي الخطة."),
        term("Agents", [(step("moa7amed-queue agents show"), [r"^Task #3", r"Measured:", r"^  - ", r"isolation:"])],
             "The two agents overlapped, read-only, on a snapshot. Only the task's session writes.",
             "عمل الوكيلان في الوقت نفسه، للقراءة فقط، على نسخة منفصلة. جلسة المهمة وحدها تكتب.",
             "The two analysis agents ran at the same time, read-only, on a snapshot of the last "
             "commit. Only the task's own session changes the project.",
             "عمل وكيلا التحليل في الوقت نفسه، للقراءة فقط، على نسخة من آخر إيداع. جلسة المهمة وحدها تغيّر المشروع."),
        term("Verified", [(step("git log"), None), (step("python -m unittest"), None),
                          (step("moa7amed-queue usage"), [(0, 4)])],
             "Each task reports how it verified its work. One commit per task; costs recorded.",
             "كل مهمة تذكر كيف تحقّقت من عملها. إيداع لكل مهمة، والتكلفة مسجّلة.",
             "Every task must report how it verified its work. Each result is committed, and "
             "the cost Claude Code reported is recorded.",
             "على كل مهمة أن تذكر كيف تحقّقت من عملها. تُحفظ كل نتيجة في git، وتُسجَّل التكلفة كما أبلغ عنها Claude Code."),
        term("Recovery", [(step("moa7amed-queue add", 3), None), (killed, None),
                          (step("moa7amed-queue status", rm.status_after_kill()), [(0, 3)]),
                          (step("moa7amed-queue run", rm.run_after_kill()), None)],
             "The runner is killed mid-task. The next run recovers the task and finishes it.",
             "أُوقف المشغّل أثناء مهمة. التشغيل التالي يستعيد المهمة ويكملها.",
             "Here the runner is killed while a task runs. The next run finds the stale lock, "
             "puts the interrupted task back, and finishes it.",
             "هنا أُوقف المشغّل أثناء تنفيذ مهمة. يجد التشغيل التالي القفل القديم، ويعيد المهمة المقاطَعة، ثم يكملها.",
             killed=killed),
        term("BidStudio and Replica", [(step("moa7amed-queue bid new"), None), (step("moa7amed-queue bid doc"), None),
                                       (step("moa7amed-queue replica new"), None),
                                       (step("moa7amed-queue replica ref"), None)],
             "BidStudio and Replica start from your own documents. BidStudio never submits.",
             "يبدأ BidStudio وReplica من مستنداتك أنت، وBidStudio لا يقدّم أي عطاء بنفسه.",
             "The same queue powers BidStudio for tenders and Replica for rebuilding your own "
             "applications. BidStudio never submits a bid.",
             "الطابور نفسه يشغّل BidStudio للمناقصات وReplica لإعادة بناء تطبيقاتك. BidStudio لا يقدّم أي عطاء بنفسه."),
        end,
    ]


# --------------------------------------------------------------------------- frames

PAGE_CSS = """
* { box-sizing:border-box; margin:0; }
body { width:%(w)dpx; height:%(h)dpx; background:#0b1120; font-family:%(ui)s; overflow:hidden; }
.top { height:48px; display:flex; align-items:center; justify-content:space-between; padding:0 24px;
       color:#94a3b8; font-size:16px; border-bottom:1px solid #1e293b; }
.brand { font-family:%(mono)s; color:#e2e8f0; font-weight:700; }
.badges span { margin-left:10px; padding:3px 10px; border-radius:999px; font-size:14px; }
.real { background:#1e293b; color:#cbd5e1; } .fast { background:#78350f; color:#fde68a; }
.kill { background:#7f1d1d; color:#fecaca; }
.term { position:absolute; top:60px; left:24px; right:24px; height:%(th)dpx; background:#0d1117;
        border:1px solid #1f2937; border-radius:10px; overflow:hidden; }
.term.scrolled { -webkit-mask-image:linear-gradient(to bottom, transparent 0, #000 26px); }
pre { padding:12px 16px; font-family:%(mono)s; font-size:17px; line-height:1.42; color:#c9d1d9;
      white-space:pre-wrap; word-break:break-word; }
.p { color:#7ee787; } .c { color:#f0f6fc; font-weight:600; } .o { color:#8b949e; font-style:italic; }
.ok { color:#3fb950; } .bad { color:#f85149; } .warn { color:#d29922; } .info { color:#79c0ff; }
.cur { background:#c9d1d9; color:#0d1117; }
.cap { position:absolute; left:0; right:0; bottom:0; height:%(ch)dpx; display:flex; align-items:center;
       justify-content:center; padding:0 60px; background:#111827; color:#f8fafc; font-size:27px;
       line-height:1.35; text-align:center; border-top:1px solid #1e293b; }
.cap.ar { font-family:%(ar)s; font-size:29px; }
.card { position:absolute; top:0; left:0; right:0; bottom:%(ch)dpx; display:flex; flex-direction:column;
        justify-content:center; padding:0 110px; }
.card .word { font-family:%(mono)s; font-size:72px; font-weight:700; color:#f8fafc; }
.card .tag { font-size:34px; color:#5eead4; font-weight:600; margin-top:10px; }
.card .by { font-size:22px; color:#94a3b8; margin-top:26px; }
.card .big { font-size:38px; color:#e2e8f0; line-height:1.4; }
.card code { font-family:%(mono)s; font-size:22px; color:#cbd5e1; background:#1e293b; padding:6px 12px;
             border-radius:6px; }
"""


def page_html():
    css = PAGE_CSS % {"w": W, "h": H, "ui": rm.FONT_UI, "mono": rm.FONT_MONO, "ar": rm.FONT_AR,
                      "th": H - 60 - 112 - 14, "ch": 112}
    return ("<html><head><meta charset='utf-8'><style>%s</style></head><body>"
            "<div class='top'><span class='brand'>moa7amed-queue</span><span id='label'></span>"
            "<span class='badges' id='badges'></span></div><div id='main'></div>"
            "<div class='cap' id='cap'></div></body></html>" % css)


def card_html(scene, lang):
    if scene["kind"] == "title":
        return ("<div class='card'><div class='word'>moa7amed-queue</div>"
                "<div class='tag'>Queue. Route. Execute. Verify.</div>"
                "<div class='by'>by moa7amed</div></div>")
    if scene["kind"] == "end":
        return ("<div class='card'><div class='word'>moa7amed-queue</div>"
                "<div class='tag'>moa7amed.com</div>"
                "<div class='by' style='margin-top:30px'>Install from a clone of the marketplace:</div>"
                "<div style='margin-top:12px'><code>%s</code></div>"
                "<div class='by' style='margin-top:14px;font-size:18px'>Setup and the hosted install: "
                "see the README</div></div>" % html.escape(INSTALL))
    text = scene["ar"] if lang == "ar" else scene["en"]
    return "<div class='card'><div class='big' %s>%s</div></div>" % (
        "dir='rtl' style='font-family:%s;text-align:right'" % rm.FONT_AR if lang == "ar" else "",
        html.escape(text))


def timeline(scene_list):
    """[(state, frames)] and caption cues [(start_s, end_s, scene)]."""
    states, cues, t = [], [], 0
    total_scenes = len(scene_list)
    for number, scene in enumerate(scene_list, 1):
        start = t
        if scene["kind"] != "term":
            frames = int(scene["seconds"] * FPS)
            states.append(({"scene": scene, "card": True, "number": number, "of": total_scenes}, frames))
            t += frames
        else:
            buffer, badges = [], ["real"]
            for item, keep in scene["items"]:
                lines = select(item, keep)
                prompt, output = lines[0], lines[1:]
                command = prompt[0]
                for i in range(0, len(command) + 1, 4):
                    states.append(({"scene": scene, "lines": buffer + [(command[:i], "typing")],
                                    "badges": badges, "number": number, "of": total_scenes}, 1))
                    t += 1
                states.append(({"scene": scene, "lines": buffer + [prompt], "badges": badges,
                                "number": number, "of": total_scenes}, 4))
                t += 4
                buffer = buffer + [prompt]
                real = item["seconds"]
                times = [tm for tm, _ in item["lines"]]
                if real > 5 and keep is None:
                    target = scene.get("speed_target", 9)
                    factor = max(1.0, real / target)
                    if factor < 1.5:            # short enough: replayed at its real pace, no badge
                        factor = 1.0
                        fast = list(badges)
                    else:
                        fast = ["real", "fast:%d:%d:%d" % (round(factor), round(real), round(real / factor))]
                    previous = 0.0
                    for (tm, _), line in zip(item["lines"], output):
                        gap = max(1, int((tm - previous) / factor * FPS))
                        states.append(({"scene": scene, "lines": buffer, "badges": fast, "number": number,
                                        "of": total_scenes}, gap))
                        t += gap
                        buffer = buffer + [line]
                        previous = tm
                    badges_after = fast
                else:
                    wait = max(2, int(min(real, 1.2) * FPS))
                    states.append(({"scene": scene, "lines": buffer, "badges": badges, "number": number,
                                    "of": total_scenes}, wait))
                    t += wait
                    buffer = buffer + output
                    badges_after = badges
                if scene.get("killed") is item:
                    badges_after = badges_after + ["kill"]
                hold = int(max(2.2, min(5.0, 1.3 + 0.12 * len(output))) * FPS)
                states.append(({"scene": scene, "lines": buffer + [("", "blank")], "badges": badges_after,
                                "number": number, "of": total_scenes}, hold))
                t += hold
                buffer = buffer + [("", "blank")]
        cues.append((start / FPS, t / FPS, scene))
    return states, cues


def state_script(state, lang):
    scene = state["scene"]
    caption = scene["ar"] if lang == "ar" else scene["en"]
    if state.get("card"):
        main, label, badges = card_html(scene, lang), "", ""
    else:
        rendered = []
        for text, kind in state["lines"][-TERM_LINES:]:
            if kind == "typing":
                rendered.append('<span class="p">%s</span><span class="c">%s</span><span class="cur"> </span>'
                                % (html.escape(rm.PROMPT), html.escape(text)))
            elif kind == "blank":
                rendered.append("")
            else:
                rendered.append(rm.line_html(text, kind))
        main = "<div class='term'><pre>%s</pre></div>" % "\n".join(rendered)
        label = "%d/%d · %s" % (state["number"], state["of"], scene["label"])
        parts = []
        for badge in state["badges"]:
            if badge == "real":
                parts.append("<span class='real'>Real output, replayed</span>")
            elif badge.startswith("fast"):
                _, factor, real, shown = badge.split(":")
                parts.append("<span class='fast'>Sped up ×%s · %s s shown in %s s</span>" % (factor, real, shown))
            elif badge == "kill":
                parts.append("<span class='kill'>Runner killed here (on purpose)</span>")
        badges = "".join(parts)
    return {"main": main, "label": label, "badges": badges, "caption": html.escape(caption),
            "rtl": lang == "ar"}


def ffmpeg():
    found = sorted(glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright", "ffmpeg-*",
                                          "ffmpeg*.exe")) +
                   glob.glob(os.path.expanduser("~/.cache/ms-playwright/ffmpeg-*/ffmpeg*")))
    if not found:
        raise SystemExit("Playwright's ffmpeg build was not found")
    return found[-1]


def encode(name, scene_list, lang, page):
    states, cues = timeline(scene_list)
    out = os.path.join(rm.VIDEO, name + ".webm")
    process = subprocess.Popen([ffmpeg(), "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS),
                                "-c:v", "mjpeg", "-i", "pipe:0", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "34",
                                "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p",
                                "-g", "100", out], stdin=subprocess.PIPE)
    cache, frames, thumb = {}, 0, None
    for state, count in states:
        data = state_script(state, lang)
        key = hashlib.sha1(repr(sorted(data.items())).encode("utf-8")).hexdigest()
        if key not in cache:
            page.evaluate("""d => { document.getElementById('main').innerHTML = d.main;
                const t = document.querySelector('.term'); if (t) { t.scrollTop = t.scrollHeight; if (t.scrollTop > 0) t.classList.add('scrolled'); }
                document.getElementById('label').textContent = d.label;
                document.getElementById('badges').innerHTML = d.badges;
                const c = document.getElementById('cap'); c.innerHTML = d.caption;
                c.className = d.rtl ? 'cap ar' : 'cap'; c.dir = d.rtl ? 'rtl' : 'ltr'; }""", data)
            cache = {key: page.screenshot(type="jpeg", quality=92)}
            if thumb is None:
                thumb = cache[key]
        for _ in range(count):
            process.stdin.write(cache[key])
        frames += count
    process.stdin.close()
    process.wait()
    write_vtt(os.path.join(rm.VIDEO, name + ".%s.vtt" % lang), cues, lang)
    print("%-16s %s  %d frames, %.1f s, %d KB" % (name, lang, frames, frames / FPS, os.path.getsize(out) // 1024))
    return out, thumb, cues


def stamp(seconds):
    return "%02d:%02d:%06.3f" % (seconds // 3600, (seconds % 3600) // 60, seconds % 60)


def write_vtt(path, cues, lang):
    lines = ["WEBVTT", "Language: %s" % lang, ""]
    for index, (start, end, scene) in enumerate(cues, 1):
        lines += [str(index), "%s --> %s" % (stamp(start), stamp(end)), scene["ar" if lang == "ar" else "en"], ""]
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


def render_video():
    os.makedirs(rm.VIDEO, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=rm.chromium())
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
        page.set_content(page_html())
        page.evaluate("document.fonts.ready")
        only = [name for name in os.environ.get("MQ_VIDEO_ONLY", "").split(",") if name]
        thumb = None
        for name, short, lang in (("demo-en", False, "en"), ("demo-ar", False, "ar"),
                                  ("demo-short-en", True, "en"), ("demo-short-ar", True, "ar")):
            if only and name not in only:
                continue
            _, first, _ = encode(name, scenes(short), lang, page)
            if name == "demo-en":
                thumb = first
        browser.close()
    if thumb is not None:
        image = Image.open(io.BytesIO(thumb)).convert("RGB")
        image.save(os.path.join(rm.IMAGES, "video-thumbnail.png"), optimize=True)


def storyboard():
    path = os.path.join(rm.HERE, "storyboard.md")
    out = ["# Demo storyboard and narration", "",
           "Generated by `render_media.py storyboard` from the scene list in `render_video.py`. "
           "Times are those of the rendered video. The narration is a script for a future "
           "voice-over: **no voice-over exists**; the videos are silent with burned-in captions.", "",
           "## Recording a voice-over (not done)", "",
           "1. Record each scene's narration in a quiet room, in your own voice or with a "
           "text-to-speech service whose terms allow publishing the audio. Do not imitate or "
           "clone anyone's voice without their written permission.",
           "2. Keep each recording within its scene's time span below (the captions use the same "
           "spans); speak the English and the Arabic version separately.",
           "3. Combine the clips into one track per language and add it to the matching video "
           "with a full FFmpeg build (Playwright's build has no audio encoder), for example "
           "`ffmpeg -i demo-en.webm -i narration-en.ogg -c:v copy -c:a libopus -shortest "
           "demo-en-voice.webm`.",
           "4. Play the result to the end, check that speech and captions agree, and label the "
           "voice (\"narration: <name>\" or \"synthetic voice: <service>\") in docs/demo.md.", ""]
    for name, short in (("Main video (demo-en.webm, demo-ar.webm)", False),
                        ("Short video (demo-short-en.webm, demo-short-ar.webm)", True)):
        _, cues = timeline(scenes(short))
        out += ["## %s" % name, "", "Total: %.0f s" % cues[-1][1], ""]
        for index, (start, end, scene) in enumerate(cues, 1):
            shown = ("card: " + scene["kind"]) if scene["kind"] != "term" else "terminal: " + "; ".join(
                "`` %s ``" % item["command"][:80] for item, _ in scene["items"])
            out += ["### %d. %s (%s to %s)" % (index, scene.get("label") or scene["kind"].title(),
                                               stamp(start)[3:8], stamp(end)[3:8]), "",
                    "- On screen: %s" % shown,
                    "- Caption (EN): %s" % scene["en"],
                    "- Caption (AR): <span dir=\"rtl\">%s</span>" % scene["ar"],
                    "- Narration (EN): %s" % scene["narr_en"],
                    "- Narration (AR): <span dir=\"rtl\">%s</span>" % scene["narr_ar"], ""]
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(out))
    print("wrote", path)


def thumbnail():
    """The title card with a line saying what the video is (for the README link)."""
    _, cues = timeline(scenes())
    seconds = int(round(cues[-1][1]))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=rm.chromium())
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
        page.set_content(page_html())
        page.evaluate("""d => { document.getElementById('main').innerHTML = d.main;
            const c = document.getElementById('cap'); c.innerHTML = d.caption; }""",
                      {"main": card_html({"kind": "title"}, "en"),
                       "caption": "&#9654;&nbsp; Watch the demo &middot; %d:%02d &middot; silent, captions in "
                                  "English and Arabic" % (seconds // 60, seconds % 60)})
        out = os.path.join(rm.IMAGES, "video-thumbnail.png")
        page.screenshot(path=out)
        browser.close()
    rm.optimise(out)
    print("thumbnail", out, os.path.getsize(out) // 1024, "KB")


def main(action):
    if action == "video":
        render_video()
        thumbnail()
    elif action == "thumbnail":
        thumbnail()
    storyboard()
