"""Image sets: a folder or a zip of already-extracted frames, imported as a source.

The pipeline was built around one video per project (§6). This module adds the
other way material arrives — a folder of stills, a zip from a phone or a drone,
a render sequence — without giving step 2 a second personality: the images are
copied into `input/<set>/` under a conforming, zero-padded name, and step 2
conforms them into `frames/` exactly as FFmpeg would have written them there.

Why the rename happens at *import* and not at conform time: the conformed name
is what makes the set readable by FFmpeg's `image2` demuxer as a single
sequence (`set_%04d.png`), which is what lets step 2 convert 900 images in one
subprocess with a real progress channel instead of 900 subprocesses with none.
The original filenames are not lost — `imageset.json` keeps the mapping.

Pure module: no FastAPI. cv2 is imported lazily, and only to answer whether an
alpha channel is actually used; nothing here fails if it is missing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# What may be imported as a frame. Deliberately the same set as
# `core/frames.py`: a TIFF that gets in here is converted by step 2 like any
# other source image.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

MANIFEST_NAME = "imageset.json"

# How many images are opened to find out whether the alpha channel carries
# anything. A render sequence writes RGBA whether or not it uses it, so the
# declared channel count is not the answer — but opening 900 files to find that
# out is not the answer either.
_ALPHA_SAMPLE = 3


def _pad_width(count: int) -> int:
    """The frame index is zero-padded, like FFmpeg's `frame_%04d`.

    Four digits, or as many as the set needs: `%04d` silently widens past
    9 999, and a sequence whose name changes width mid-way is no longer one
    pattern — which is exactly what the image2 demuxer reads it as.
    """
    return max(4, len(str(max(count, 1))))


# -- naming ------------------------------------------------------------------

def conform_name(raw: str) -> str:
    """A set name that is safe as a directory name and as an FFmpeg pattern.

    Lowercased, non-alphanumerics collapsed to `_`: the result is concatenated
    with `_%04d.ext` and handed to FFmpeg's image2 demuxer, so anything the
    command line or the filtergraph parser treats specially — a space, a `:`, a
    `%`, a quote — has to be gone before it gets there.
    """
    suffixes = {".zip", *IMAGE_SUFFIXES}
    stem = Path(raw).stem if Path(raw).suffix.lower() in suffixes else raw
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    slug = re.sub(r"_+", "_", slug)
    return slug or "images"


def unique_name(input_dir: Path, base: str) -> str:
    """`base`, or `base-2`, `base-3`… — the same rule as `_unique_slug` for projects."""
    candidate = base
    n = 2
    while (input_dir / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


_NUM = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """Sort `img2` before `img10`.

    A camera writes `DSC_0001`…`DSC_9999` and lexicographic order is right for
    those; a render writes `frame.1.png`…`frame.10.png` and it is not. The
    order chosen here becomes the frame index, which the overlap gate reads as
    time — getting it wrong reorders the shot.
    """
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in _NUM.split(name)
    )


def common_pattern(names: Iterable[str]) -> str:
    """How the originals were named, as one readable line: `DSC_####.JPG`."""
    listed = list(names)
    if not listed:
        return ""
    if len(listed) == 1:
        return listed[0]
    return _NUM.sub(lambda m: "#" * len(m.group()), listed[0])


# -- what an image is --------------------------------------------------------

def _png_info(fh) -> Optional[dict[str, Any]]:
    head = fh.read(26)
    if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    colour_type = head[25]
    # 4 = greyscale+alpha, 6 = truecolour+alpha. A palette image's tRNS chunk is
    # deliberately not chased: it is not what a camera or a renderer writes, and
    # the sampled read below is the authority anyway.
    return {"width": width, "height": height, "channels_alpha": colour_type in (4, 6)}


_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_info(fh) -> Optional[dict[str, Any]]:
    if fh.read(2) != b"\xff\xd8":
        return None
    while True:
        marker = fh.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return None
        length_bytes = fh.read(2)
        if len(length_bytes) < 2:
            return None
        length = struct.unpack(">H", length_bytes)[0]
        if marker[1] in _SOF:
            body = fh.read(5)
            if len(body) < 5:
                return None
            height, width = struct.unpack(">HH", body[1:5])
            return {"width": width, "height": height, "channels_alpha": False}
        fh.seek(length - 2, os.SEEK_CUR)


def read_image_info(path: Path) -> dict[str, Any]:
    """Dimensions and the *declared* alpha channel, from the header alone.

    A few kilobytes per file rather than a decode: the panel lists a whole set
    on every redraw, and decoding 900 20-megapixel PNGs to print "3840x2160"
    would cost more than the extraction it is helping to configure.
    """
    info: dict[str, Any] = {"width": None, "height": None, "channels_alpha": False}
    try:
        with path.open("rb") as fh:
            suffix = path.suffix.lower()
            if suffix == ".png":
                parsed = _png_info(fh)
            elif suffix in (".jpg", ".jpeg"):
                parsed = _jpeg_info(fh)
            else:
                parsed = None
        if parsed:
            info.update(parsed)
    except OSError:
        pass
    return info


def alpha_in_use(paths: list[Path], sample: int = _ALPHA_SAMPLE) -> Optional[bool]:
    """Whether the sampled images have any non-opaque pixel.

    None when it could not be answered (no cv2, unreadable files) — which the
    caller reports as "declared, not verified" rather than as "no alpha". The
    difference matters: this answer decides whether step 2 offers to keep the
    channel at all, and a set that declares RGBA but is fully opaque is a
    render's default output, not a mask.
    """
    if not paths:
        return None
    try:
        import cv2  # noqa: PLC0415 — optional at import time on purpose
    except ImportError:
        return None

    step = max(1, len(paths) // sample)
    picks = paths[::step][:sample] or paths[:1]
    seen = False
    for path in picks:
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim != 3 or img.shape[2] != 4:
            continue
        seen = True
        if bool((img[:, :, 3] < 255).any()):
            return True
    return False if seen else None


# -- the import --------------------------------------------------------------

def _images_in(folder: Path) -> list[Path]:
    """Every image under `folder`, recursively, in natural order.

    Recursive because a zip and a camera card both nest (`DCIM/100MEDIA/`), and
    a set is flattened on the way in: the frame index is the whole ordering the
    pipeline has, and a directory tree is not one.
    """
    files = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    ]
    return sorted(files, key=lambda p: natural_key(str(p.relative_to(folder))))


def _write_manifest(set_dir: Path, manifest: dict[str, Any]) -> None:
    (set_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def import_files(
    files: list[Path],
    input_dir: Path,
    set_name: str,
    origin: str,
    origin_name: str = "",
    origin_path: str = "",
    move: bool = False,
    progress_fn: Optional[Callable[[int, int, int], None]] = None,
    original_names: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Copy (or move) images into `input/<set_name>/` under a conforming name.

    `move` is for a zip that has just been unpacked into a staging directory —
    there is no reason to write those bytes twice. `original_names` goes with
    it: a staged file carries a de-duplicating prefix that is an artefact of the
    unpacking, and the manifest must record the name the user actually has.
    """
    from backend.core.project_ops import _report  # one progress cadence, one rule

    set_dir = input_dir / set_name
    set_dir.mkdir(parents=True, exist_ok=True)

    pad = _pad_width(len(files))
    entries: list[dict[str, Any]] = []
    total = len(files) or 1
    done_bytes = 0

    for index, source in enumerate(files, start=1):
        suffix = source.suffix.lower()
        if suffix == ".jpeg":
            suffix = ".jpg"
        target = set_dir / f"{set_name}_{index:0{pad}d}{suffix}"
        try:
            size = source.stat().st_size
        except OSError:
            size = 0
        if move:
            shutil.move(str(source), str(target))
        else:
            shutil.copy2(source, target)
        done_bytes += size
        original = (
            original_names[index - 1]
            if original_names and index <= len(original_names)
            else source.name
        )
        entries.append(
            {"index": index, "filename": target.name, "original": original}
        )
        _report(progress_fn, index, total, done_bytes, size)

    # Answered once, here, rather than on every listing: the step 2 panel
    # re-reads `input/` on each settings change, and decoding three 20-megapixel
    # PNGs per redraw to re-discover a fact that cannot change is a spinner the
    # user pays for. `describe_set` prefers this value and only samples when a
    # set has no manifest — a folder dropped into `input/` by hand.
    imported = [set_dir / e["filename"] for e in entries]
    declared = any(
        read_image_info(imported[i])["channels_alpha"]
        for i in sorted({0, len(imported) // 2, len(imported) - 1} & set(range(len(imported))))
    )

    manifest = {
        "name": set_name,
        "has_alpha": declared,
        "alpha_in_use": alpha_in_use(imported) if declared else None,
        "origin": origin,
        "origin_name": origin_name,
        "origin_path": origin_path,
        "imported_at": _now(),
        "image_count": len(entries),
        "pattern": f"{set_name}_%0{pad}d",
        "pad": pad,
        "original_pattern": common_pattern([e["original"] for e in entries]),
        "files": entries,
    }
    _write_manifest(set_dir, manifest)
    return manifest


def import_folder(
    folder: Path,
    input_dir: Path,
    set_name: str = "",
    progress_fn: Optional[Callable[[int, int, int], None]] = None,
) -> dict[str, Any]:
    """Import every image under a folder on this machine.

    The path is read server-side rather than uploaded: this app runs on the
    workstation that holds the files (§1), and pushing 20 GB of PNG through
    multipart to write it back onto the same disk is a copy with extra steps.
    """
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a folder: {folder}")
    files = _images_in(folder)
    if not files:
        raise FileNotFoundError(f"No image found in {folder}")
    name = unique_name(input_dir, conform_name(set_name or folder.name))
    return import_files(
        files, input_dir, name,
        origin="folder", origin_name=folder.name, origin_path=str(folder),
        progress_fn=progress_fn,
    )


def import_zip(
    zip_path: Path,
    input_dir: Path,
    set_name: str = "",
    progress_fn: Optional[Callable[[int, int, int], None]] = None,
    cleanup: bool = True,
    origin_name: str = "",
) -> dict[str, Any]:
    """Unpack a zip of images into `input/<set>/`.

    `origin_name` is the name to *record*, which is not `zip_path.name`: the
    upload route stages the file as `.incoming_<name>.zip`, and a panel saying
    "from .incoming_Turntable Take 3.zip" is reporting an implementation detail
    back at the user.

    Entry names are never used as paths — every image is renamed to the set's
    own pattern — so a `../../` entry has nothing to traverse into. Non-image
    entries are ignored rather than refused: a zip from a phone carries a
    thumbnail directory and a `.DS_Store`, and neither is a reason to fail.
    """
    staging = zip_path.parent / f".{zip_path.stem}_unpack"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [
                m for m in archive.infolist()
                if not m.is_dir()
                and Path(m.filename).suffix.lower() in IMAGE_SUFFIXES
                # A macOS zip carries a parallel `__MACOSX/._name` resource fork
                # for every file: same extension, a few KB, not an image.
                and not Path(m.filename).name.startswith("._")
                and "__MACOSX" not in Path(m.filename).parts
            ]
            if not members:
                raise FileNotFoundError(f"No image inside {zip_path.name}")
            staged: list[tuple[Path, str]] = []
            for position, member in enumerate(members):
                # Flattened on purpose, and prefixed by position so two entries
                # called `IMG_0001.jpg` in different directories of the same zip
                # cannot overwrite each other. The prefix is an artefact of the
                # unpacking, so the real name travels beside the path rather
                # than being read back off it.
                name = Path(member.filename).name
                flat = staging / f"{position:06d}_{name}"
                with archive.open(member) as src, flat.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                staged.append((flat, name))

        # Ordered by the *original* name: it is the one the camera or the
        # renderer numbered, and that order becomes the frame index.
        staged.sort(key=lambda pair: natural_key(pair[1]))
        name = unique_name(input_dir, conform_name(set_name or zip_path.stem))
        manifest = import_files(
            [path for path, _ in staged], input_dir, name,
            origin="zip", origin_name=origin_name or zip_path.name,
            origin_path=str(zip_path),
            move=True, progress_fn=progress_fn,
            original_names=[original for _, original in staged],
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if cleanup:
            try:
                zip_path.unlink()
            except OSError:
                pass

    return manifest


# -- reading a set back ------------------------------------------------------

def read_manifest(set_dir: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads((set_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_image_set(path: Path) -> bool:
    """A directory under `input/` holding at least one image.

    The manifest is not required: a folder dropped in by hand — or one restored
    from an archive written before this existed — is a legitimate set, it
    simply has no record of where it came from.
    """
    if not path.is_dir() or path.name.startswith("."):
        return False
    try:
        return any(
            p.suffix.lower() in IMAGE_SUFFIXES for p in path.iterdir() if p.is_file()
        )
    except OSError:
        return False


def find_image_sets(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        return []
    return sorted(
        (p for p in input_dir.iterdir() if is_image_set(p)), key=lambda p: p.name
    )


def set_images(set_dir: Path) -> list[Path]:
    """The images of a set, in the order that becomes the frame index."""
    try:
        files = [
            p for p in set_dir.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        ]
    except OSError:
        return []
    return sorted(files, key=lambda p: natural_key(p.name))


# Frames are given a nominal cadence so a set can be described in the units the
# rest of the app speaks — a duration, a timeline, "at 30 img/s this is 12 s".
# It is a presentation device: nothing in the curation of an image set reads a
# timecode, and no fps policy applies to one (§6.7).
NOMINAL_FPS = 30.0


def describe_set(set_dir: Path, slug: str = "", deep: bool = True) -> dict[str, Any]:
    """Everything step 2 needs to say about a set before it conforms it."""
    images = set_images(set_dir)
    manifest = read_manifest(set_dir)

    sizes: list[int] = []
    formats: dict[str, int] = {}
    for image in images:
        try:
            sizes.append(image.stat().st_size)
        except OSError:
            sizes.append(0)
        formats[image.suffix.lower()] = formats.get(image.suffix.lower(), 0) + 1

    total_bytes = sum(sizes)
    count = len(images)

    # The header of the first, the middle and the last: a set is normally one
    # camera at one resolution, and three reads answer "normally" honestly
    # without opening the other 897.
    picks = sorted({0, count // 2, count - 1} & set(range(count)))
    infos = [read_image_info(images[i]) for i in picks]
    dims = {(i["width"], i["height"]) for i in infos if i["width"]}
    declared_alpha = any(i["channels_alpha"] for i in infos)

    # The import already sampled the pixels; re-doing it on every listing would
    # cost three PNG decodes per redraw of the panel that reads this.
    if manifest is not None and "alpha_in_use" in manifest:
        used_alpha = manifest["alpha_in_use"]
    else:
        used_alpha = alpha_in_use(images) if (deep and declared_alpha) else None

    fallback_pattern = ""
    if images:
        fallback_pattern = _NUM.sub(lambda m: "#" * len(m.group()), images[0].name)

    return {
        "name": set_dir.name,
        "kind": "images",
        "image_count": count,
        "total_bytes": total_bytes,
        "avg_bytes": int(total_bytes / count) if count else 0,
        "formats": formats,
        "width": infos[0]["width"] if infos else None,
        "height": infos[0]["height"] if infos else None,
        "uniform_size": len(dims) <= 1,
        "duration_s": round(count / NOMINAL_FPS, 2) if count else 0.0,
        "nominal_fps": NOMINAL_FPS,
        "has_alpha": declared_alpha,
        "alpha_in_use": used_alpha,
        "pattern": (manifest or {}).get("pattern") or fallback_pattern,
        "original_pattern": (manifest or {}).get("original_pattern", ""),
        "origin": (manifest or {}).get("origin", "folder"),
        "origin_name": (manifest or {}).get("origin_name", ""),
        "origin_path": (manifest or {}).get("origin_path", ""),
        "imported_at": (manifest or {}).get("imported_at"),
        "first_image": images[0].name if images else None,
        "url": (
            f"/static/{slug}/input/{set_dir.name}/{images[0].name}"
            if (slug and images) else None
        ),
    }
