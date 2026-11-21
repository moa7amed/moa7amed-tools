"""Design tokens from evidence: CSS and HTML reference files, the computed styles a
browser capture recorded, and the colours of screenshots.

Every token says where it was seen (OBSERVED in a capture or screenshot, DOCUMENTED in a
supplied stylesheet) and how often. Frequencies are counts, not design intent: the
replica-design skill turns them into a design specification and says what it inferred.
"""
from __future__ import annotations

import re

COLOR = re.compile(r"#(?:[0-9a-fA-F]{3,4}){1,2}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")
DECLARATION = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+)")
MEDIA = re.compile(r"@media[^{]*\((?:min|max)-width\s*:\s*([0-9.]+)(px|em|rem)\)")
VARIABLE = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+)")
STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
INLINE_STYLE = re.compile(r"\sstyle\s*=\s*\"([^\"]*)\"", re.I)
DIRECTION = re.compile(r"<html[^>]*\sdir\s*=\s*\"?(rtl|ltr)", re.I)

GROUPS = {
    "color": ("color", "background-color", "background", "border-color", "fill", "stroke"),
    "font-family": ("font-family",),
    "font-size": ("font-size",),
    "font-weight": ("font-weight",),
    "spacing": ("margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
                "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
                "gap", "row-gap", "column-gap"),
    "radius": ("border-radius",),
    "shadow": ("box-shadow",),
    "line-height": ("line-height",),
}


def _normal_color(value):
    value = value.strip().lower()
    match = re.match(r"^#([0-9a-f]{3})$", value)
    if match:
        return "#" + "".join(ch * 2 for ch in match.group(1))
    match = re.match(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+))?\)$", value)
    if match:
        alpha = match.group(4)
        if alpha is not None and float(alpha) == 0:
            return None
        return "#%02x%02x%02x" % tuple(int(match.group(i)) for i in (1, 2, 3))
    return value


IGNORED = ("inherit", "initial", "unset", "none", "0", "0px", "normal", "auto", "transparent",
           "currentcolor", "0px 0px", "0px 0px 0px 0px")


def _count(counter, group, value, source):
    value = re.sub(r"\s*!important\s*$", "", value.strip())
    if not value or value in IGNORED:
        return
    entry = counter.setdefault((group, value), {"count": 0, "sources": set()})
    entry["count"] += 1
    entry["sources"].add(source)


def from_css(text, source, counter=None):
    """Count token values in a stylesheet (or the style blocks and attributes of HTML)."""
    counter = {} if counter is None else counter
    bodies = [text]
    if "<" in text and ">" in text:
        bodies = STYLE_BLOCK.findall(text) + INLINE_STYLE.findall(text)
        direction = DIRECTION.search(text)
        if direction:
            _count(counter, "direction", direction.group(1).lower(), source)
    for body in bodies:
        for name, value in VARIABLE.findall(body):
            _count(counter, "variable", "%s: %s" % (name, value.strip()), source)
        for prop, value in DECLARATION.findall(body):
            prop = prop.lower()
            for group, props in GROUPS.items():
                if prop in props:
                    if group == "color":
                        for found in COLOR.findall(value):
                            color = _normal_color(found)
                            if color:
                                _count(counter, "color", color, source)
                    else:
                        _count(counter, group, value, source)
        for size, unit in MEDIA.findall(body):
            _count(counter, "breakpoint", "%s%s" % (size.rstrip("0").rstrip(".") if "." in size
                                                    else size, unit), source)
    return counter


def from_computed(styles, source, counter=None):
    """Count values from a capture's computed-style summary: {"property": {value: n}}."""
    counter = {} if counter is None else counter
    for prop, values in (styles or {}).items():
        for group, props in GROUPS.items():
            if prop not in props:
                continue
            for value, count in values.items():
                found = ([_normal_color(item) for item in COLOR.findall(value)]
                         if group == "color" else [value.strip()])
                for item in found:
                    if not item or item in IGNORED:
                        continue
                    entry = counter.setdefault((group, item), {"count": 0, "sources": set()})
                    entry["count"] += int(count)
                    entry["sources"].add(source)
    return counter


def tokens(counter, evidence_of, limit=8):
    """The most frequent values per group as token records. `evidence_of(source)` gives
    the evidence class of a reference id."""
    out = []
    groups = {}
    for (group, value), entry in counter.items():
        groups.setdefault(group, []).append((entry["count"], value, entry["sources"]))
    for group in sorted(groups):
        ranked = sorted(groups[group], key=lambda item: (-item[0], item[1]))[:limit]
        for index, (count, value, sources) in enumerate(ranked, 1):
            classes = sorted({evidence_of(source) for source in sources})
            out.append({"group": group, "name": "%s-%d" % (group, index), "value": value,
                        "count": count, "refs": sorted(sources),
                        "evidence": "OBSERVED" if "OBSERVED" in classes else
                        (classes[0] if classes else "INFERRED")})
    return out
