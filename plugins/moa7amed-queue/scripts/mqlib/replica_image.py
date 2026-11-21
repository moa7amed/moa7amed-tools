"""Screenshot comparison for Replica: measured, with the method stated.

Images are read with Pillow when it is installed (PNG, JPEG, WebP...) and otherwise with
the PNG reader below (standard library only; 8-bit, non-interlaced PNG). Nothing here
calls a model.

The comparison is pixel based. It says how many pixels differ beyond a tolerance, where
they are (a grid of regions, worst first), how the sizes differ, and produces a
difference image. It is not a perceptual metric: anti-aliasing, fonts and dynamic content
move it, and a match says nothing about whether the application works.
"""
from __future__ import annotations

import struct
import zlib

TOLERANCE = 24          # per channel, out of 255
GRID = (8, 6)           # columns, rows of the region grid
MAX_SIDE = 1600         # larger images are compared at a reduced scale (stated in the method)


class ImageError(Exception):
    pass


class Image(object):
    """width, height and rows of (r, g, b) tuples."""

    def __init__(self, width, height, pixels):
        self.width, self.height, self.pixels = width, height, pixels

    def pixel(self, x, y):
        return self.pixels[y][x]


# --------------------------------------------------------------------------- reading

def _pillow():
    try:
        from PIL import Image as PILImage  # noqa: F401
        return PILImage
    except Exception:  # noqa: BLE001 - optional
        return None


def load(path, max_side=MAX_SIDE):
    """Image at `path`, scaled down (nearest neighbour) so its longer side is at most
    `max_side`. Returns (image, scale)."""
    pil = _pillow()
    if pil is not None:
        with pil.open(path) as handle:
            picture = handle.convert("RGB")
            width, height = picture.size
            scale = 1.0
            if max(width, height) > max_side:
                scale = max_side / float(max(width, height))
                picture = picture.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                                         pil.NEAREST)
                width, height = picture.size
            data = picture.tobytes()
        rows = [[tuple(data[(y * width + x) * 3:(y * width + x) * 3 + 3]) for x in range(width)]
                for y in range(height)]
        return Image(width, height, rows), scale
    image = read_png(path)
    if max(image.width, image.height) > max_side:
        scale = max_side / float(max(image.width, image.height))
        return resize(image, max(1, int(image.width * scale)), max(1, int(image.height * scale))), scale
    return image, 1.0


def read_png(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageError("%s is not a PNG file (install Pillow to compare other formats)" % path)
    position, chunks, palette = 8, [], None
    width = height = depth = color = interlace = None
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"IDAT":
            chunks.append(body)
        elif kind == b"IEND":
            break
    if depth != 8 or interlace:
        raise ImageError("%s: only 8-bit, non-interlaced PNG is read without Pillow" % path)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if channels is None:
        raise ImageError("%s: unsupported PNG colour type %s" % (path, color))
    raw = zlib.decompress(b"".join(chunks))
    stride = width * channels
    rows, previous = [], bytearray(stride)
    offset = 0
    for _ in range(height):
        kind = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        offset += 1 + stride
        if kind == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + predictor) & 255
        previous = line
        if color == 2:
            row = [tuple(line[i:i + 3]) for i in range(0, stride, 3)]
        elif color == 6:
            row = [_over_white(line[i:i + 4]) for i in range(0, stride, 4)]
        elif color == 0:
            row = [(value, value, value) for value in line]
        elif color == 4:
            row = [_over_white((line[i], line[i], line[i], line[i + 1])) for i in range(0, stride, 2)]
        else:
            row = [palette[index] for index in line]
        rows.append(row)
    return Image(width, height, rows)


def _over_white(rgba):
    r, g, b, a = rgba
    return (r * a + 255 * (255 - a)) // 255, (g * a + 255 * (255 - a)) // 255, \
        (b * a + 255 * (255 - a)) // 255


def resize(image, width, height):
    rows = []
    for y in range(height):
        source = image.pixels[min(image.height - 1, y * image.height // height)]
        rows.append([source[min(image.width - 1, x * image.width // width)] for x in range(width)])
    return Image(width, height, rows)


def write_png(path, image):
    raw = b"".join(b"\x00" + bytes(value for pixel in row for value in pixel)
                   for row in image.pixels)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(
            ">I", zlib.crc32(kind + body) & 0xffffffff)

    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)))
        handle.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        handle.write(chunk(b"IEND", b""))


def solid(width, height, color):
    return Image(width, height, [[tuple(color)] * width for _ in range(height)])


# --------------------------------------------------------------------------- comparing

def _background(image):
    """The most frequent colour of an image (its background, as an estimate)."""
    counts = {}
    step_y = max(1, image.height // 200)
    step_x = max(1, image.width // 200)
    for y in range(0, image.height, step_y):
        row = image.pixels[y]
        for x in range(0, image.width, step_x):
            counts[row[x]] = counts.get(row[x], 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _differs(a, b, tolerance):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) > tolerance


SHIFTS = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))


def _measure_pure(reference, candidate, tolerance, grid, want_diff, shift=1):
    width, height = min(reference.width, candidate.width), min(reference.height, candidate.height)
    background = _background(reference)
    columns, rows = grid
    cells = [[[0, 0] for _ in range(columns)] for _ in range(rows)]   # [differing, content]
    differing = content = 0
    diff_rows = [] if want_diff else None
    for y in range(height):
        ref_row, cand_row = reference.pixels[y], candidate.pixels[y]
        cell_row = cells[min(rows - 1, y * rows // height)]
        out_row = [] if want_diff else None
        for x in range(width):
            a, b = ref_row[x], cand_row[x]
            differ = _differs(a, b, tolerance)
            if differ and shift:
                for dx, dy in SHIFTS[1:]:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height and \
                            not _differs(a, candidate.pixels[ny][nx], tolerance):
                        differ = False
                        break
            is_content = differ or _differs(a, background, tolerance) or \
                _differs(b, background, tolerance)
            cell = cell_row[min(columns - 1, x * columns // width)]
            if is_content:
                content += 1
                cell[1] += 1
            if differ:
                differing += 1
                cell[0] += 1
            if out_row is not None:
                if differ:
                    out_row.append((230, 30, 30))
                else:
                    grey = 200 + ((a[0] + a[1] + a[2]) // 3) * 55 // 255
                    out_row.append((grey, grey, grey))
        if diff_rows is not None:
            diff_rows.append(out_row)
    return {"width": width, "height": height, "differing": differing, "content": content,
            "cells": cells, "diff": Image(width, height, diff_rows) if want_diff else None,
            "background": background}


def _measure_pillow(reference_path, candidate_path, tolerance, grid, diff_path, shift=1):
    """The same measurement with Pillow's image operations (fast)."""
    from PIL import Image as PILImage, ImageChops
    with PILImage.open(reference_path) as left_file, PILImage.open(candidate_path) as right_file:
        left, right = left_file.convert("RGB"), right_file.convert("RGB")
    sizes = (left.size, right.size)
    notes = []
    longest = max(left.size + right.size)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / float(longest)
        left = left.resize((max(1, int(left.width * scale)), max(1, int(left.height * scale))),
                           PILImage.NEAREST)
        right = right.resize((max(1, int(right.width * scale)), max(1, int(right.height * scale))),
                             PILImage.NEAREST)
        notes.append("compared at reduced scale (longer side %d px)" % MAX_SIDE)
    width, height = min(left.width, right.width), min(left.height, right.height)
    a, b = left.crop((0, 0, width, height)), right.crop((0, 0, width, height))
    small = left.resize((min(200, left.width), min(200, left.height)), PILImage.NEAREST)
    background = max(small.getcolors(small.width * small.height), key=lambda item: item[0])[1]

    def mask(first, second):
        delta = ImageChops.difference(first, second)
        r, g, bl = delta.split()
        top = ImageChops.lighter(ImageChops.lighter(r, g), bl)
        return top.point(lambda value: 255 if value > tolerance else 0)

    plain = PILImage.new("RGB", (width, height), background)
    exact = mask(a, b)
    differ = exact
    if shift:
        # a pixel matches when the candidate matches it here or one pixel away
        for dx, dy in SHIFTS[1:]:
            moved = Image_shift(b, dx, dy, PILImage)
            differ = ImageChops.darker(differ, mask(a, moved))
    content = ImageChops.lighter(ImageChops.lighter(mask(a, plain), mask(b, plain)), exact)
    columns, rows = grid
    cells = [[[0, 0] for _ in range(columns)] for _ in range(rows)]
    for row in range(rows):
        for column in range(columns):
            box = (column * width // columns, row * height // rows,
                   (column + 1) * width // columns, (row + 1) * height // rows)
            cells[row][column] = [differ.crop(box).histogram()[255],
                                  content.crop(box).histogram()[255]]
    if diff_path:
        faded = a.convert("L").point(lambda value: 200 + value * 55 // 255).convert("RGB")
        red = PILImage.new("RGB", (width, height), (230, 30, 30))
        PILImage.composite(red, faded, differ).save(diff_path)
    return {"width": width, "height": height, "differing": differ.histogram()[255],
            "content": content.histogram()[255], "cells": cells, "notes": notes,
            "sizes": [list(sizes[0]), list(sizes[1])], "background": background,
            "scaled": [list(left.size), list(right.size)]}


def Image_shift(image, dx, dy, pil):
    """The image moved by (dx, dy), the uncovered edge filled from the original."""
    moved = image.copy()
    moved.paste(image, (dx, dy))
    return moved


def compare(reference_path, candidate_path, diff_path=None, tolerance=TOLERANCE, grid=GRID,
            shift=1):
    """Measure how a candidate screenshot differs from the reference.

    The score is the share of *content* pixels that match: pixels that are not the
    reference's background colour in either image. A large shared background does not
    inflate it, so two different screens with the same background do not score as
    similar. The whole-image share is reported as well."""
    notes = []
    if _pillow() is not None:
        measured = _measure_pillow(reference_path, candidate_path, tolerance, grid, diff_path,
                                   shift)
        notes += measured.pop("notes")
        ref_size, cand_size = measured["scaled"]
        original_sizes = measured["sizes"]
        reader = "Pillow"
    else:
        reference, scale_ref = load(reference_path)
        candidate, scale_cand = load(candidate_path)
        if scale_ref != 1.0 or scale_cand != 1.0:
            notes.append("compared at reduced scale (longer side %d px)" % MAX_SIDE)
        measured = _measure_pure(reference, candidate, tolerance, grid, bool(diff_path), shift)
        if diff_path:
            write_png(diff_path, measured["diff"])
        ref_size, cand_size = [reference.width, reference.height], [candidate.width, candidate.height]
        original_sizes = [ref_size, cand_size]
        reader = "built-in PNG reader"
    width, height = measured["width"], measured["height"]
    union = max(ref_size[0], cand_size[0]) * max(ref_size[1], cand_size[1])
    compared = width * height
    outside = union - compared
    if ref_size != cand_size:
        notes.append("sizes differ: reference %dx%d, candidate %dx%d; the common top-left %dx%d "
                     "area is compared and the rest counts as different"
                     % (original_sizes[0][0], original_sizes[0][1], original_sizes[1][0],
                        original_sizes[1][1], width, height))
    differing = measured["differing"] + outside
    content = measured["content"] + outside
    score = 1.0 if content == 0 else (content - differing) / float(content)
    whole = 0.0 if union == 0 else (union - differing) / float(union)
    regions = []
    columns, rows = grid
    for row in range(rows):
        for column in range(columns):
            bad, total = measured["cells"][row][column]
            if bad:
                regions.append({"row": row, "column": column,
                                "share": round(bad / float(max(total, 1)), 4), "pixels": bad,
                                "box": [column * width // columns, row * height // rows,
                                        (column + 1) * width // columns, (row + 1) * height // rows]})
    regions.sort(key=lambda item: -item["pixels"])
    background = measured["background"]
    return {
        "score": round(score, 4), "whole_image_score": round(whole, 4),
        "differing_pixels": differing, "content_pixels": content, "compared_pixels": compared,
        "union_pixels": union, "background": "#%02x%02x%02x" % tuple(background[:3]),
        "reference_size": list(original_sizes[0]), "candidate_size": list(original_sizes[1]),
        "regions": regions[:8], "notes": notes, "diff": diff_path, "reader": reader,
        "method": ("pixel comparison of content: share of matching pixels among those that are "
                   "not the reference background (%s) in either image; a pixel matches when no "
                   "RGB channel differs by more than %d/255%s; regions on a %dx%d grid, ranked by "
                   "differing pixels. Not perceptual; sensitive to fonts, anti-aliasing and "
                   "dynamic content; says nothing about behaviour."
                   % ("#%02x%02x%02x" % tuple(background[:3]), tolerance,
                      " at the same position or one pixel away" if shift else "", columns, rows)),
    }


def describe_regions(result, limit=4):
    """Human-readable locations of the worst regions."""
    names = []
    columns, rows = GRID
    for region in result.get("regions", [])[:limit]:
        vertical = ("top" if region["row"] < rows / 3.0 else "bottom"
                    if region["row"] >= 2 * rows / 3.0 else "middle")
        horizontal = ("left" if region["column"] < columns / 3.0 else "right"
                      if region["column"] >= 2 * columns / 3.0 else "centre")
        names.append("%s-%s area x%d-%d y%d-%d (%.0f%% of its pixels differ)" % (
            vertical, horizontal, region["box"][0], region["box"][2], region["box"][1],
            region["box"][3], region["share"] * 100))
    return names


def palette(path, colors=8):
    """The most frequent colours of an image (quantised to 16 levels per channel), with
    their share. Evidence for design tokens, not a design decision."""
    image, _ = load(path, max_side=400)
    counts = {}
    for row in image.pixels:
        for r, g, b in row:
            key = (r & 0xF0 | 0x08, g & 0xF0 | 0x08, b & 0xF0 | 0x08)
            counts[key] = counts.get(key, 0) + 1
    total = float(image.width * image.height) or 1.0
    ranked = sorted(counts.items(), key=lambda item: -item[1])[:colors]
    return [{"value": "#%02x%02x%02x" % color, "share": round(count / total, 4)}
            for color, count in ranked]
