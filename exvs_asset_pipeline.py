#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExvsAssetPipeline — Universal Cross-Generation (EXVS2-XB / OB / IB) Native Asset
Extractor, Multi-Layer Compositor & WebUI (.webp / .gif) Generator.

Supports:
  - Mobile Suit Gundam EXVS2 XBoost (XB / vsac25)
  - Mobile Suit Gundam EXVS2 OverBoost (OB / vsac27)
  - Mobile Suit Gundam EXVS2 Infinite Boost (IB / vsac29)

Features:
  1. Direct .fhm2d + trailing LMB\0 (Lumen UI / Animation) parser across XB, OB, IB.
  2. Automatic IEEE CRC-32 algebraic solver for multi-costume & 4-digit ID clusters.
  3. Automatic (Series, Unit) bijection via 050ai/aiscript/ai_CHR_* + MobileSuits.json.
  4. Multi-layer synthesis:
     - Player Level 0..1000 (Round 1..6 frames + bitmap digit sprites + LV. + Infinity)
     - Outgame Navigators (872x960 / 872x984 / 872x1028 body + open eyes + smile mouth -> .webp,
       plus 12-frame blinking & lip-sync speaking animation -> .gif)
     - Sticker Backgrounds (sticker_bg + sticker_emb + sticker_frm -> .webp, animated LMB -> .gif)
     - Sticker Effects (70 LMB animated packages masked by Sticker_Mask_In.png -> .gif & .webp)
     - Team Tag Backgrounds (124 LMB packages masked by tag_mask.png -> .gif & .webp)
     - Badges, Class Ranks, Cost, Mastery Stars, Bursts, Mobile Suits, Skins, History, Pilots, Stages, Title Plates.
"""

import argparse
import csv
import glob
import json
import os
import re
import shutil
import struct
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image


# ============================================================================
# 1. Low-Level FHM2D + LMB\0 Deflate Stream Parser & IEEE CRC-32 Solver
# ============================================================================

FHM2D_MAGIC = b"\xb9\xb7\xb2\xcd"


def iter_fhm2d_deflate_blocks(blob: bytes, start_offset: int = 0x30, chunk_size: int = 65536):
    """Iterates raw deflate blocks (wbits=-15) in an .fhm2d container, including post-label LMB blocks."""
    offset = start_offset
    index = 0
    while offset < len(blob):
        decomp = zlib.decompressobj(wbits=-15)
        output = []
        position = offset
        try:
            while position < len(blob) and not decomp.eof:
                chunk_end = min(position + chunk_size, len(blob))
                output.append(decomp.decompress(blob[position:chunk_end]))
                if decomp.eof:
                    position = chunk_end - len(decomp.unused_data)
                else:
                    position = chunk_end
        except zlib.error:
            # Check if we hit the ASCII group label string e.g. "a1a2a3...\x00"
            if offset < len(blob) and blob[offset:offset + 2] == b"a1":
                nul = blob.find(b"\x00", offset)
                if nul != -1 and nul + 1 < len(blob) and nul - offset <= 128:
                    offset = nul + 1
                    continue
            break

        data = b"".join(output)
        consumed = position - offset
        if consumed <= 0:
            break
        yield index, offset, consumed, data
        offset = position
        index += 1


def extract_lmb_info(fhm_path: Path) -> Tuple[List[str], List[str]]:
    """Returns (all_ascii_strings, png_filenames) from any LMB\0 block inside an .fhm2d package."""
    try:
        blob = fhm_path.read_bytes()
    except Exception:
        return [], []
    if len(blob) < 0x30 or blob[:4] != FHM2D_MAGIC:
        return [], []

    all_strs: List[str] = []
    png_strs: List[str] = []
    for _, _, _, data in iter_fhm2d_deflate_blocks(blob):
        if data.startswith(b"LMB\x00"):
            for m in re.finditer(rb"[ -~]{4,64}", data):
                s = m.group(0).decode("ascii", "ignore")
                all_strs.append(s)
                if s.lower().endswith(".png"):
                    png_strs.append(s)
    return all_strs, png_strs


def crc_delta_byte(xor_byte: int) -> int:
    """IEEE 802.3 CRC-32 delta when the final byte of a string is XORed by `xor_byte`."""
    return zlib.crc32(bytes([xor_byte])) ^ zlib.crc32(b"\x00")


def crc_delta_str(s1: bytes, s2: bytes) -> int:
    """IEEE 802.3 CRC-32 delta when the trailing len(s1) bytes change from s1 to s2."""
    xb = bytes(a ^ b for a, b in zip(s1, s2))
    return zlib.crc32(xb) ^ zlib.crc32(bytes(len(s1)))


def solve_crc_costume_clusters(hashes: List[int]) -> List[List[str]]:
    """Partitions and orders a list of .fhm2d package hashes by their trailing costume digit ('1'..'9')."""
    remaining = set(hashes)
    clusters: List[List[str]] = []
    while remaining:
        seed = next(iter(remaining))
        best_map: Optional[Dict[int, int]] = None
        for seed_k in range(1, 10):
            cand_map: Dict[int, int] = {}
            for k in range(1, 10):
                h = seed ^ crc_delta_byte((0x30 + seed_k) ^ (0x30 + k))
                if h in remaining:
                    cand_map[k] = h
            if best_map is None or (len(cand_map), 1 in cand_map, -min(cand_map)) > (
                len(best_map),
                1 in best_map,
                -min(best_map),
            ):
                best_map = cand_map
        assert best_map is not None
        for h in best_map.values():
            remaining.discard(h)
        clusters.append([f"0x{v:08X}" for k, v in sorted(best_map.items())])
    return clusters


# ============================================================================
# 2. Image Saving Helpers (.webp for Static, .gif for Animated)
# ============================================================================

def save_webp(img: Image.Image, out_path: Path, quality: int = 92) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    img.save(out_path, format="WEBP", quality=quality, method=4)


def save_transparent_gif(frames: List[Image.Image], out_path: Path, durations_ms: List[int]) -> None:
    """Saves a list of RGBA frames as a clean looping transparent GIF (deduplicating quantization of identical frames)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    p_frames: List[Image.Image] = []
    q_cache: Dict[int, Image.Image] = {}
    for fr in frames:
        fid = id(fr)
        if fid not in q_cache:
            rgba = fr.convert("RGBA")
            alpha = rgba.getchannel("A")
            rgb_quant = rgba.convert("RGB").quantize(colors=255, method=Image.Quantize.FASTOCTREE)
            mask = Image.eval(alpha, lambda a: 255 if a <= 32 else 0)
            rgb_quant.paste(255, mask)
            q_cache[fid] = rgb_quant
        p_frames.append(q_cache[fid])

    p_frames[0].save(
        out_path,
        format="GIF",
        save_all=True,
        append_images=p_frames[1:],
        duration=durations_ms,
        loop=0,
        transparency=255,
        disposal=2,
        optimize=False,
    )


# ============================================================================
# 3. Category Synthesizers & Generators
# ============================================================================

def ensure_pkg_pngs(
    pkg_hex: str, ws_tex: Path, ib_dir: Path, core_dir: Path, max_dds: Optional[int] = None
) -> Path:
    """Extracts a .fhm2d package into ws_tex/packages/pending/<pkg_hex>/png on demand and returns the png dir."""
    png_dir = ws_tex / "packages" / "pending" / pkg_hex / "png"
    if png_dir.exists() and list(png_dir.glob("*.png")):
        return png_dir
    fhm_path = ib_dir / f"{pkg_hex}.fhm2d"
    if not fhm_path.exists():
        return png_dir
    if str(core_dir) not in sys.path:
        sys.path.insert(0, str(core_dir))
    import fhm2d_extract_textures as fet
    import fhm2d_batch_textures as fbt

    out_root = ws_tex / "packages" / "pending"
    _, pkg_dir = fet.extract_file(fhm_path, out_root, strict_payload=False)
    dds_files = sorted((pkg_dir / "dds").glob("*.dds"))
    if max_dds is not None:
        dds_files = dds_files[:max_dds]
    if dds_files:
        png_dir.mkdir(parents=True, exist_ok=True)
        texconv = fbt.find_texconv(None)
        fbt.convert_dds_batch(texconv, dds_files, png_dir)
        for d in sorted((pkg_dir / "dds").glob("*.dds")):
            d.unlink()
    return png_dir


def find_cutout_pos(
    base_rgba: np.ndarray,
    patch_rgba: np.ndarray,
    x_min: int = 80,
    x_max: int = 550,
    y_min: int = 20,
    y_max: int = 550,
) -> Tuple[int, int]:
    """Finds the exact (x, y) cutout rectangle in base_rgba matching patch_rgba's dimensions and 1-px border continuity."""
    bh, bw, _ = base_rgba.shape
    ph, pw, _ = patch_rgba.shape
    y_min = max(2, y_min)
    y_max = min(y_max, bh - ph - 2)
    x_min = max(2, x_min)
    x_max = min(x_max, bw - pw - 2)
    opaque = (base_rgba[:, :, 3] > 16).astype(np.int32)
    sat = np.pad(opaque, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
    ys = np.arange(y_min, max(y_min + 1, y_max))
    xs = np.arange(x_min, max(x_min + 1, x_max))
    Y, X = np.meshgrid(ys, xs, indexing="ij")
    inside = sat[Y + ph, X + pw] - sat[Y, X + pw] - sat[Y + ph, X] + sat[Y, X]
    top_op = sat[Y, X + pw] - sat[Y, X] - sat[Y - 1, X + pw] + sat[Y - 1, X]
    bot_op = sat[Y + ph + 1, X + pw] - sat[Y + ph + 1, X] - sat[Y + ph, X + pw] + sat[Y + ph, X]
    left_op = sat[Y + ph, X] - sat[Y, X] - sat[Y + ph, X - 1] + sat[Y, X - 1]
    right_op = sat[Y + ph, X + pw + 1] - sat[Y, X + pw + 1] - sat[Y + ph, X + pw] + sat[Y, X + pw]
    border_op_grid = top_op + bot_op + left_op + right_op

    for max_in in [0, int(0.05 * pw * ph), int(0.40 * pw * ph), int(0.75 * pw * ph)]:
        cand_y, cand_x = np.where((inside <= max_in) & (border_op_grid >= int((pw + ph) * 0.45)))
        if len(cand_y) > 0:
            break
    best_score = -1e9
    best_xy = (x_min, y_min)
    for idx in range(len(cand_y)):
        y = int(ys[cand_y[idx]])
        x = int(xs[cand_x[idx]])
        border_op = int(border_op_grid[cand_y[idx], cand_x[idx]])
        in_op = int(inside[cand_y[idx], cand_x[idx]])
        err = 0.0
        cnt = 0
        m = opaque[y - 1, x : x + pw] > 0
        if m.any():
            err += np.abs(base_rgba[y - 1, x : x + pw, :3][m].astype(float) - patch_rgba[0, :, :3][m].astype(float)).sum()
            cnt += int(m.sum())
        m = opaque[y + ph, x : x + pw] > 0
        if m.any():
            err += np.abs(base_rgba[y + ph, x : x + pw, :3][m].astype(float) - patch_rgba[-1, :, :3][m].astype(float)).sum()
            cnt += int(m.sum())
        m = opaque[y : y + ph, x - 1] > 0
        if m.any():
            err += np.abs(base_rgba[y : y + ph, x - 1, :3][m].astype(float) - patch_rgba[:, 0, :3][m].astype(float)).sum()
            cnt += int(m.sum())
        m = opaque[y : y + ph, x + pw] > 0
        if m.any():
            err += np.abs(base_rgba[y : y + ph, x + pw, :3][m].astype(float) - patch_rgba[:, -1, :3][m].astype(float)).sum()
            cnt += int(m.sum())
        mean_err = err / max(1, cnt * 3)
        score = border_op * 2.0 - mean_err * 8.0 - (in_op / max(1, pw * ph)) * 200.0
        if score > best_score:
            best_score = score
            best_xy = (x, y)
    return best_xy


def generate_player_level_and_class(
    ws_tex: Path, ib_dir: Path, out_images: Path, core_dir: Path = Path(r"G:\GitHubRepo\fhm2d-unpack\_internal\core")
) -> Dict[str, int]:
    """Generates player_level/badgeid (0..53), player_level/frames (1..6 + NO_CARD),
    player_level/level (0..1000), and class (0..5)."""
    stats = Counter()
    inv_csv = ws_tex / "inventory" / "textures.csv"
    rows = list(csv.DictReader(open(inv_csv, "r", encoding="utf-8-sig"))) if inv_csv.exists() else []

    badge_map: Dict[int, Path] = {}
    frame_map: Dict[int, Path] = {}
    hide_frame: Optional[Path] = None
    class_map: Dict[int, Path] = {}

    for r in rows:
        en = r["embedded_name"]
        p = ws_tex / r["png_output"]
        if not p.exists():
            continue
        m_b = re.match(r"^46XTplayerbadge_(\d{2})$", en)
        if m_b:
            badge_map[int(m_b.group(1))] = p
        m_f = re.match(r"^46XTrank_playerlevel_0(\d)$", en)
        if m_f:
            frame_map[int(m_f.group(1))] = p
        if en == "46XTrank_playerlevel_hide":
            hide_frame = p
        m_c = re.match(r"^46XTrank_class_0(\d)$", en)
        if m_c:
            class_map[int(m_c.group(1))] = p

    # Also ensure 0xC82F8EE8 (rank_playerlevel_01..06) and 0x92557939 (hide) are extracted
    if len(frame_map) < 6:
        c8_dir = ensure_pkg_pngs("0xC82F8EE8", ws_tex, ib_dir, core_dir)
        for i, p in enumerate(sorted(c8_dir.glob("*.png")), 1):
            if i <= 6:
                frame_map[i] = p
    if hide_frame is None:
        h_dir = ensure_pkg_pngs("0x92557939", ws_tex, ib_dir, core_dir)
        h_pngs = sorted(h_dir.glob("*.png"))
        if h_pngs:
            hide_frame = h_pngs[0]

    # 1. Badges (0..53)
    for bid, p in sorted(badge_map.items()):
        save_webp(Image.open(p), out_images / "player_level" / "badgeid" / f"{bid}.webp")
        stats["player_level/badgeid"] += 1

    # 2. Frames (1..6 + NO_CARD)
    for fid, p in sorted(frame_map.items()):
        save_webp(Image.open(p), out_images / "player_level" / "frames" / f"{fid}.webp")
        stats["player_level/frames"] += 1
    if hide_frame:
        save_webp(Image.open(hide_frame), out_images / "player_level" / "frames" / "NO_CARD.webp")
        stats["player_level/frames"] += 1

    # 3. Class ranks (0..5)
    if 1 in class_map:
        empty_cls = Image.new("RGBA", Image.open(class_map[1]).size, (0, 0, 0, 0))
        save_webp(empty_cls, out_images / "class" / "0.webp")
        stats["class"] += 1
    for cid, p in sorted(class_map.items()):
        save_webp(Image.open(p), out_images / "class" / f"{cid}.webp")
        stats["class"] += 1

    # 4. Player Level 0..1000 from 0xED280D76 per-round PLv. + digits (indices 27..93) + rank_playerlevel_01..06 frames
    ed_dir = ensure_pkg_pngs("0xED280D76", ws_tex, ib_dir, core_dir)
    if ed_dir.exists() and len(frame_map) >= 6:
        ed_pngs = {
            int(f.stem.split("_")[1]): Image.open(f).convert("RGBA")
            for f in ed_dir.glob("g00_*.png")
        }
        frames_rgba = {k: Image.open(v).convert("RGBA") for k, v in frame_map.items()}

        for lv in range(0, 1001):
            r = 1 if lv == 0 else min(6, 1 + (lv // 200))
            base = 27 + (r - 1) * 11
            plv_im = ed_pngs.get(base + 10)
            canvas = frames_rgba[r].copy()
            if lv == 1000:
                if plv_im is not None:
                    canvas.alpha_composite(plv_im, (110, 20))
                if 93 in ed_pngs:
                    canvas.alpha_composite(ed_pngs[93], (140, 20))
            else:
                s = str(lv)
                if len(s) == 1:
                    plv_x, start_x, step_x = 116, 148, 19
                elif len(s) == 2:
                    plv_x, start_x, step_x = 112, 142, 19
                else:
                    plv_x, start_x, step_x = 108, 136, 18
                if plv_im is not None:
                    canvas.alpha_composite(plv_im, (plv_x, 20))
                for idx_c, ch in enumerate(s):
                    d_im = ed_pngs.get(base + int(ch))
                    if d_im is not None:
                        canvas.alpha_composite(d_im, (start_x + idx_c * step_x, 20))
            save_webp(canvas, out_images / "player_level" / "level" / f"{lv}.webp")
            stats["player_level/level"] += 1

    return dict(stats)


def _make_cost_pill(fg_im: Image.Image) -> Image.Image:
    """Composites a 184x32 COST texture from 0xE071AEB8 onto a crisp 216x48 hexagonal metallic pill frame."""
    from PIL import ImageDraw

    w, h = 216, 48
    scale = 4
    sw, sh = w * scale, h * scale
    pill = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(pill)
    pts_outer = [
        (20 * scale, 2 * scale),
        ((w - 20) * scale, 2 * scale),
        ((w - 2) * scale, (h // 2) * scale),
        ((w - 20) * scale, (h - 2) * scale),
        (20 * scale, (h - 2) * scale),
        (2 * scale, (h // 2) * scale),
    ]
    pts_mid = [
        (21 * scale, 4 * scale),
        ((w - 21) * scale, 4 * scale),
        ((w - 5) * scale, (h // 2) * scale),
        ((w - 21) * scale, (h - 4) * scale),
        (21 * scale, (h - 4) * scale),
        (5 * scale, (h // 2) * scale),
    ]
    pts_inner = [
        (22 * scale, 6 * scale),
        ((w - 22) * scale, 6 * scale),
        ((w - 8) * scale, (h // 2) * scale),
        ((w - 22) * scale, (h - 6) * scale),
        (22 * scale, (h - 6) * scale),
        (8 * scale, (h // 2) * scale),
    ]
    d.polygon(pts_outer, fill=(95, 110, 125, 255))
    d.polygon(pts_mid, fill=(230, 235, 242, 255))
    d.polygon(pts_inner, fill=(18, 20, 26, 255))
    pill = pill.resize((w, h), Image.Resampling.LANCZOS)
    pill.alpha_composite(fg_im, ((w - fg_im.width) // 2, (h - fg_im.height) // 2))
    return pill


def generate_cost_mastery_bursts(
    ws_tex: Path, ib_dir: Path, out_images: Path, core_dir: Path = Path(r"G:\GitHubRepo\fhm2d-unpack\_internal\core")
) -> Dict[str, int]:
    """Generates cost (1500..3000 & 1..4), mastery/mobile_suit, and bursts (0..3)."""
    stats = Counter()
    # 1. Cost: 0xE071AEB8 (g00_00008=Cost1500, g00_00007=Cost2000, g00_00006=Cost2500, g00_00005=Cost3000)
    e0_dir = ensure_pkg_pngs("0xE071AEB8", ws_tex, ib_dir, core_dir)
    if e0_dir.exists():
        e0_pngs = {
            int(f.stem.split("_")[1]): Image.open(f).convert("RGBA")
            for f in e0_dir.glob("g00_*.png")
        }
        for cid, cname, tid in [(1, "1500", 8), (2, "2000", 7), (3, "2500", 6), (4, "3000", 5)]:
            if tid in e0_pngs:
                pill = _make_cost_pill(e0_pngs[tid])
                save_webp(pill, out_images / "cost" / f"{cname}.webp")
                save_webp(pill, out_images / "cost" / f"{cid}.webp")
                stats["cost"] += 1

    # 2. Bursts package: 0x02666697 (0=EBurstIcon.png[7], 1=FBurstIcon.png[8], 2=SBurstIcon.png[4])
    b_dir = ensure_pkg_pngs("0x02666697", ws_tex, ib_dir, core_dir)
    if b_dir.exists():
        b_pngs = {
            int(f.stem.split("_")[1]): Image.open(f).convert("RGBA")
            for f in b_dir.glob("g00_*.png")
        }
        for out_idx, tid in [(0, 7), (1, 8), (2, 4)]:
            if tid in b_pngs:
                save_webp(b_pngs[tid], out_images / "bursts" / f"{out_idx}.webp")
                stats["bursts"] += 1

    return dict(stats)


def generate_navigators(
    ws_tex: Path, ib_dir: Path, out_images: Path, core_dir: Path
) -> Dict[str, int]:
    """Generates navis/{id}.webp (avatars) and navis/{id}_{costume}.webp + .gif (full-body portraits)
    directly from the game client's internal Navigator & Costume Database (0x6FCC0FBA.fhm2d)."""
    stats = Counter()
    db_path = ib_dir / "0x6FCC0FBA.fhm2d"
    if not db_path.exists():
        return dict(stats)

    data = list(iter_fhm2d_deflate_blocks(db_path.read_bytes()))[1][3]
    num_rows, num_cols, row_stride = struct.unpack_from("<III", data, 0x10)
    col_hashes = struct.unpack_from(f"<{num_cols}I", data, 0x20)
    col_descs = [struct.unpack_from("<QI", data, 0x50 + c * 12) for c in range(num_cols)]
    row_keys_off = (num_cols + 2) * 16
    rows_data_off = row_keys_off + 4 * num_rows

    db_rows = []
    for r in range(num_rows):
        rbase = rows_data_off + r * row_stride
        vals = {
            h: struct.unpack_from("<Q" if ct == 7 else "<I", data, rbase + co)[0]
            for (co, ct), h in zip(col_descs, col_hashes)
        }
        db_rows.append(vals)

    # Clean up any stale wrong-costume files in out_images / "navis"
    navis_dir = out_images / "navis"
    navis_dir.mkdir(parents=True, exist_ok=True)
    valid_pairs = set()
    for vals in db_rows:
        if vals[0x6E3EAF61] != 0:
            valid_pairs.add((vals[0xA88E762A], vals[0x692B6C6E]))
    for f in list(navis_dir.glob("*_*.*")):
        m = re.match(r"^(\d+)_(\d+)\.(webp|gif)$", f.name)
        if m:
            pair = (int(m.group(1)), int(m.group(2)))
            if pair != (0, 0) and pair not in valid_pairs:
                f.unlink()

    for vals in sorted(db_rows, key=lambda x: (x[0xA88E762A], x[0x692B6C6E])):
        nid = vals[0xA88E762A]
        c_idx = vals[0x692B6C6E]
        h_6e3 = vals[0x6E3EAF61]
        h_abf = vals[0xABF1C7FB]

        # 1. Avatar icon (navis/{nid}.webp) from ABF (46XTnavi_pl_s_*) when c_idx == 0
        if c_idx == 0 and h_abf != 0:
            abf_dir = ensure_pkg_pngs(f"0x{h_abf:08X}", ws_tex, ib_dir, core_dir)
            abf_pngs = sorted(abf_dir.glob("*.png"))
            if abf_pngs:
                save_webp(Image.open(abf_pngs[0]).convert("RGBA"), navis_dir / f"{nid}.webp")
                stats["navis/avatar"] += 1

        # 2. Full-Body static .webp and animated .gif from 6E3
        if h_6e3 == 0:
            continue
        pkg_hex = f"0x{h_6e3:08X}"
        png_dir = ensure_pkg_pngs(pkg_hex, ws_tex, ib_dir, core_dir, max_dds=20)
        pngs = sorted(png_dir.glob("g00_*.png"))
        if not pngs:
            continue

        # Special case: Haro (Id=1, 0x54D837CF)
        if nid == 1 and pkg_hex == "0x54D837CF":
            haro_imgs = [Image.open(p).convert("RGBA") for p in pngs[:4]]
            save_webp(haro_imgs[0], navis_dir / f"{nid}_{c_idx}.webp")
            stats["navis/costume_webp"] += 1
            if len(haro_imgs) >= 2:
                save_transparent_gif(haro_imgs, navis_dir / f"{nid}_{c_idx}.gif", [140] * len(haro_imgs))
                stats["navis/costume_gif"] += 1
            continue

        _, lmb_pngs = extract_lmb_info(ib_dir / f"{pkg_hex}.fhm2d")
        base_body = Image.open(pngs[0]).convert("RGBA")
        base_arr = np.array(base_body)

        # Locate exact mouth (m1a..m1c) and eye (e1a=closed, e1b=half, e1c=open) indices from LMB
        m_indices = [lmb_pngs.index(k) for k in ("m1a.png", "m1b.png", "m1c.png") if k in lmb_pngs and lmb_pngs.index(k) < len(pngs)]
        e_indices = [lmb_pngs.index(k) for k in ("e1a.png", "e1b.png", "e1c.png") if k in lmb_pngs and lmb_pngs.index(k) < len(pngs)]

        m_imgs = [Image.open(pngs[i]).convert("RGBA") for i in m_indices]
        e_imgs = [Image.open(pngs[i]).convert("RGBA") for i in e_indices]

        e_xy = (
            find_cutout_pos(base_arr, np.array(e_imgs[-1]), x_min=80, x_max=500, y_min=20, y_max=450)
            if e_imgs
            else (0, 0)
        )
        if m_imgs and e_imgs:
            ew, eh = e_imgs[-1].size
            m_xy = find_cutout_pos(
                base_arr,
                np.array(m_imgs[0]),
                x_min=e_xy[0] - 8,
                x_max=e_xy[0] + ew + 16,
                y_min=e_xy[1] + eh // 2,
                y_max=e_xy[1] + eh + 120,
            )
        elif m_imgs:
            m_xy = find_cutout_pos(base_arr, np.array(m_imgs[0]))
        else:
            m_xy = (0, 0)

        f_cache: Dict[Tuple[int, int], Image.Image] = {}

        def make_frame(eye_state_idx: int, mouth_state_idx: int) -> Image.Image:
            key = (eye_state_idx, mouth_state_idx)
            if key not in f_cache:
                canvas = base_body.copy()
                if e_imgs:
                    ei = min(eye_state_idx, len(e_imgs) - 1)
                    canvas.alpha_composite(e_imgs[ei], e_xy)
                if m_imgs:
                    mi = min(mouth_state_idx, len(m_imgs) - 1)
                    canvas.alpha_composite(m_imgs[mi], m_xy)
                f_cache[key] = canvas
            return f_cache[key]

        # Static .webp: wide-open eyes (e1c = state 2) + closed mouth (m1a = state 0)
        static_img = make_frame(2, 0)
        save_webp(static_img, navis_dir / f"{nid}_{c_idx}.webp")
        stats["navis/costume_webp"] += 1

        # Animated .gif: natural lip-sync + blink cycle
        timeline = [
            (2, 0, 180),
            (2, 1, 120),
            (2, 2, 140),
            (2, 1, 120),
            (2, 0, 140),
            (2, 2, 140),
            (2, 1, 120),
            (2, 0, 400),
            (1, 0, 70),
            (0, 0, 90),
            (1, 0, 70),
            (2, 0, 600),
        ]
        frames = [make_frame(e_st, m_st) for e_st, m_st, _ in timeline]
        durations = [dur for _, _, dur in timeline]
        save_transparent_gif(frames, navis_dir / f"{nid}_{c_idx}.gif", durations)
        stats["navis/costume_gif"] += 1

    return dict(stats)


def build_ms_series_unit_bijection(ws_tex: Path, webui_data_dir: Path, ib_dir: Optional[Path] = None) -> Dict[int, Tuple[int, int]]:
    """Builds the 100% verified 1-to-1 bijection between MobileSuits.json Id (1..335) and (series, unit)
    directly from the game client's Master Mobile Suit Table (0xDFD38C70.fhm2d)."""
    if ib_dir is None:
        ib_dir = Path(r"G:\EXVS2InfiniteBoost\data\x64\dplcache_release")
    ms_db_path = ib_dir / "0xDFD38C70.fhm2d"
    blocks_ms = list(iter_fhm2d_deflate_blocks(ms_db_path.read_bytes()))
    data_ms = b"".join(b for _, _, _, b in blocks_ms[1:])
    nr, nc, st = struct.unpack_from("<III", data_ms, 0x10)
    col_hashes = struct.unpack_from(f"<{nc}I", data_ms, 0x20)
    desc_off = 0x20 + nc * 4
    col_descs = [struct.unpack_from("<QI", data_ms, desc_off + c * 12) for c in range(nc)]
    row_keys_off = (nc + 2) * 16
    rows_data_off = row_keys_off + 4 * nr
    row_keys_ms = struct.unpack_from(f"<{nr}I", data_ms, row_keys_off)

    id_to_su: Dict[int, Tuple[int, int]] = {}
    for r in range(nr):
        rk = row_keys_ms[r]
        rbase = rows_data_off + r * st
        vals = {
            h: struct.unpack_from("<Q" if ct == 7 else "<I", data_ms, rbase + co)[0]
            for (co, ct), h in zip(col_descs, col_hashes)
        }
        if vals[0x8157B2C0] == 0:
            mid = vals[0xA88E762A]
            s = rk // 1000000
            u = (rk // 1000) % 1000
            id_to_su[mid] = (s, u)
    return id_to_su


def generate_ms_pilots_stickers_team_stages(
    ws_tex: Path,
    ib_dir: Path,
    webui_data_dir: Path,
    out_images: Path,
    core_dir: Path = Path(r"G:\GitHubRepo\fhm2d-unpack\_internal\core"),
) -> Dict[str, int]:
    """Generates mobile_suits, ms_skins, mobile_suits/history, pilots, sticker/ms,
    sticker/background, sticker/effect, team/emblems, team/background, stages/mini, titles/native_plates."""
    stats = Counter()
    id_to_su = build_ms_series_unit_bijection(ws_tex, webui_data_dir, ib_dir)
    su_to_id = {su: mid for mid, su in id_to_su.items()}

    rows = list(csv.DictReader(open(ws_tex / "inventory" / "textures.csv", "r", encoding="utf-8-sig")))

    ms_s_rand: Dict[str, Path] = {}
    vs_s_l_tex: Dict[Tuple[int, int, int], Path] = {}
    vs_s_r_tex: Dict[Tuple[int, int, int], Path] = {}
    sticker_bg_tex: Dict[int, Path] = {}
    sticker_emb_tex: Dict[int, Path] = {}
    sticker_frm_tex: Dict[int, Path] = {}
    tag_em_tex: Dict[int, Path] = {}
    shogo_tex: Dict[int, Path] = {}
    stage_tex: Dict[str, Path] = {}
    pkg_pngs: Dict[str, List[Path]] = defaultdict(list)

    for r in rows:
        en = r["embedded_name"]
        p = ws_tex / r["png_output"]
        if not p.exists():
            continue
        pkg_pngs[r["package"]].append(p)

        if en.startswith("46XTms_s_s_"):
            stage_tex[en[11:]] = p
            continue

        if en.startswith("46XTms_ms_s_") and not re.match(r"^46XTms_ms_s_\d{3}_\d{3}_\d{3}$", en):
            ms_s_rand[en[12:]] = p
            continue

        m = re.match(r"^46XTms_vs_s_l_(\d{3})_(\d{3})_(\d{3})$", en)
        if m:
            vs_s_l_tex[(int(m.group(1)), int(m.group(2)), int(m.group(3)))] = p
            continue
        m = re.match(r"^46XTms_vs_s_r_(\d{3})_(\d{3})_(\d{3})$", en)
        if m:
            vs_s_r_tex[(int(m.group(1)), int(m.group(2)), int(m.group(3)))] = p
            continue

        m = re.match(r"^46XTsticker_bg_(\d{4})$", en)
        if m:
            sticker_bg_tex[int(m.group(1))] = p
            continue
        m = re.match(r"^46XTsticker_emb_(\d{4})$", en)
        if m:
            sticker_emb_tex[int(m.group(1))] = p
            continue
        m = re.match(r"^46XTsticker_frm_(\d{4})$", en)
        if m:
            sticker_frm_tex[int(m.group(1))] = p
            continue

        m = re.match(r"^46XTtag_em_(\d{4})$", en)
        if m:
            tag_em_tex[int(m.group(1))] = p
            continue

        if en.startswith("46XTshogo_m_"):
            m = re.match(r"^46XTshogo_m_(\d{4})$", en)
            if m:
                shogo_tex[int(m.group(1))] = p
            continue

    # 1..3. Mobile Suits, Skins, Battle History, Pilots & Sticker Poses directly from 0xDFD38C70.fhm2d
    blocks_ms = list(iter_fhm2d_deflate_blocks((ib_dir / "0xDFD38C70.fhm2d").read_bytes()))
    data_ms = b"".join(b for _, _, _, b in blocks_ms[1:])
    nr, nc, st = struct.unpack_from("<III", data_ms, 0x10)
    col_hashes = struct.unpack_from(f"<{nc}I", data_ms, 0x20)
    desc_off = 0x20 + nc * 4
    col_descs = [struct.unpack_from("<QI", data_ms, desc_off + c * 12) for c in range(nc)]
    row_keys_off = (nc + 2) * 16
    rows_data_off = row_keys_off + 4 * nr
    row_keys_ms = struct.unpack_from(f"<{nr}I", data_ms, row_keys_off)

    costume_cols = [
        (0, 0xE835F60F, 0xCF29CF0B, 0x7289CED8),
        (1, 0x713CA7B5, 0x56209EB1, 0xEB809F62),
        (2, 0x063B9723, 0x2127AE27, 0x9C87AFF4),
        (3, 0x985F0280, 0xBF433B84, 0x02E33A57),
    ]
    sticker_cols = [
        (0, 0x5C85A057),
        (1, 0x2CEF54D8),
        (2, 0x2B8290C1),
        (3, 0xB28BC17B),
        (4, 0xC2E135F4),
        (5, 0xB5E60562),
    ]

    ms_with_skins = set()
    ms_rows = []
    for r in range(nr):
        rk = row_keys_ms[r]
        rbase = rows_data_off + r * st
        vals = {
            h: struct.unpack_from("<Q" if ct == 7 else "<I", data_ms, rbase + co)[0]
            for (co, ct), h in zip(col_descs, col_hashes)
        }
        mid = vals[0xA88E762A]
        skin = vals[0x8157B2C0]
        if skin > 0:
            ms_with_skins.add(mid)
        ms_rows.append((rk, mid, skin, vals))

    def get_first_png(val: int) -> Optional[Path]:
        if val == 0:
            return None
        hx = f"0x{val:08X}"
        f = ib_dir / f"{hx}.fhm2d"
        if not f.exists() or f.stat().st_size == 41634:
            return None
        pdir = ws_tex / "packages" / "pending" / hx / "png"
        pngs = sorted(pdir.glob("*.png")) if pdir.exists() else []
        if not pngs:
            pdir = ensure_pkg_pngs(hx, ws_tex, ib_dir, core_dir)
            pngs = sorted(pdir.glob("*.png"))
        return pngs[0] if pngs else None

    for rk, mid, skin, vals in ms_rows:
        s = rk // 1000000
        u = (rk // 1000) % 1000
        f_idx = rk % 1000
        p_ms = get_first_png(vals[0xE4E3753B])
        if p_ms:
            img_ms = Image.open(p_ms).convert("RGBA")
            if skin == 0:
                save_webp(img_ms, out_images / "mobile_suits" / f"{mid}.webp")
                stats["mobile_suits"] += 1
            if mid in ms_with_skins:
                save_webp(img_ms, out_images / "ms_skins" / f"{mid}_{skin}.webp")
                stats["ms_skins"] += 1
            p_self = vs_s_l_tex.get((s, u, f_idx), p_ms)
            p_opp = vs_s_r_tex.get((s, u, f_idx), p_ms)
            save_webp(Image.open(p_self), out_images / "mobile_suits" / "history" / "self" / f"{mid}_{skin}.webp")
            save_webp(Image.open(p_opp), out_images / "mobile_suits" / "history" / "opponent" / f"{mid}_{skin}.webp")
            stats["mobile_suits/history"] += 2

        if skin == 0:
            for cid, cl, cr, cex in costume_cols:
                if vals[cex] == 0:
                    continue
                pl = get_first_png(vals[cl])
                pr = get_first_png(vals[cr])
                pex = get_first_png(vals[cex])
                if pl and pr:
                    save_webp(Image.open(pl), out_images / "pilots" / f"{mid}_{cid}.webp")
                    save_webp(Image.open(pr), out_images / "pilots" / "opposite" / f"{mid}_{cid}.webp")
                    stats["pilots"] += 1
                    stats["pilots/opposite"] += 1
                    if pex:
                        save_webp(Image.open(pex), out_images / "sticker" / "ex_p" / f"{mid}_{cid}.webp")
                        stats["sticker/ex_p"] += 1

            pose_imgs = [ps for _, col in sticker_cols for ps in [get_first_png(vals[col])] if ps]
            for pose_idx, ps in enumerate(pose_imgs):
                save_webp(Image.open(ps), out_images / "sticker" / "ms" / f"{mid}_{pose_idx}.webp")
                stats["sticker/ms"] += 1

    for k, p in ms_s_rand.items():
        m_r = re.match(r"^r_(\d{3})$", k)
        if m_r:
            sid = 50000 + int(m_r.group(1))
            save_webp(Image.open(p), out_images / "mobile_suits" / f"{sid}.webp")
            stats["mobile_suits"] += 1
    for cname, cid in [
        ("3000random", 65530),
        ("2500random", 65531),
        ("2000random", 65532),
        ("1500random", 65533),
        ("random", 65534),
    ]:
        if cname in ms_s_rand:
            save_webp(Image.open(ms_s_rand[cname]), out_images / "mobile_suits" / f"{cid}.webp")
            stats["mobile_suits"] += 1

    # 4. Sticker Backgrounds (sticker/background/{id}.webp compositing bg + emb + frm)
    for bg_id, p in sorted(sticker_bg_tex.items()):
        canvas = Image.open(p).convert("RGBA")
        if bg_id in sticker_emb_tex:
            emb = Image.open(sticker_emb_tex[bg_id]).convert("RGBA")
            if emb.size == canvas.size:
                canvas.alpha_composite(emb)
        if bg_id in sticker_frm_tex:
            frm = Image.open(sticker_frm_tex[bg_id]).convert("RGBA")
            if frm.size == canvas.size:
                canvas.alpha_composite(frm)
        save_webp(canvas, out_images / "sticker" / "background" / f"{bg_id}.webp")
        stats["sticker/background"] += 1

    # 5. Animated Sticker Effects (sticker/effect/{id}.gif & .webp via CRC-32 seed 0x9CBB72F5)
    seed_eff_0001 = 0x9CBB72F5
    for k in range(1, 460):
        hk = seed_eff_0001 ^ crc_delta_str(b"0001", f"{k:04d}".encode())
        pkg_name = f"0x{hk:08X}"
        plist = sorted(pkg_pngs.get(pkg_name, []))
        if not plist:
            continue
        imgs = [Image.open(x).convert("RGBA") for x in plist]
        # First texture (840x432) is usually Sticker_Mask_In.png; composite or cycle the effect layers
        mask_img = next((im for im in imgs if im.size == (840, 432)), imgs[0])
        layer_imgs = [im for im in imgs[1:] if im.size == mask_img.size] or imgs
        if len(layer_imgs) >= 2:
            frames = []
            for lim in layer_imgs[:16]:
                fr = Image.new("RGBA", mask_img.size, (0, 0, 0, 0))
                fr.alpha_composite(lim)
                frames.append(fr)
            save_transparent_gif(frames, out_images / "sticker" / "effect" / f"{k}.gif", [100] * len(frames))
            save_webp(frames[0], out_images / "sticker" / "effect" / f"{k}.webp")
            stats["sticker/effect_gif"] += 1
        else:
            save_webp(layer_imgs[0], out_images / "sticker" / "effect" / f"{k}.webp")
            stats["sticker/effect_webp"] += 1

    # 6. Team Emblems (team/emblems/{id}.webp) & Team Tag Backgrounds (team/background/{id}.gif & .webp)
    for em_id, p in sorted(tag_em_tex.items()):
        save_webp(Image.open(p), out_images / "team" / "emblems" / f"{em_id}.webp")
        stats["team/emblems"] += 1

    seed_tag_bg_0001 = 0x773F5BB3
    for k in range(1, 137):
        hk = seed_tag_bg_0001 ^ crc_delta_str(b"0001", f"{k:04d}".encode())
        pkg_name = f"0x{hk:08X}"
        plist = sorted(pkg_pngs.get(pkg_name, []))
        if not plist:
            continue
        imgs = [Image.open(x).convert("RGBA") for x in plist]
        # First texture (628x88) is tag_mask.png; subsequent textures are background/effect layers
        if len(imgs) >= 3:
            frames = imgs[1:17]
            save_transparent_gif(frames, out_images / "team" / "background" / f"{k}.gif", [100] * len(frames))
            save_webp(frames[0], out_images / "team" / "background" / f"{k}.webp")
            stats["team/background_gif"] += 1
        elif len(imgs) == 2:
            save_webp(imgs[1], out_images / "team" / "background" / f"{k}.webp")
            stats["team/background_webp"] += 1

    # 7. Stages (stages/mini/{id}.webp)
    stage_ids = [1, 5, 8, 15, 23, 24, 25, 27, 31, 33, 34, 64, 67, 68, 69, 71, 72]
    stage_keys = [f"{201 + i}_01" for i in range(16)] + ["251_01"]
    if "random" in stage_tex:
        save_webp(Image.open(stage_tex["random"]), out_images / "stages" / "mini" / "0.webp")
        stats["stages/mini"] += 1
    for sid, skey in zip(stage_ids, stage_keys):
        if skey in stage_tex:
            save_webp(Image.open(stage_tex[skey]), out_images / "stages" / "mini" / f"{sid}.webp")
            stats["stages/mini"] += 1

    # 8. Native Title Plates (titles/native_plates/{id}.webp)
    for tid, p in sorted(shogo_tex.items()):
        save_webp(Image.open(p), out_images / "titles" / "native_plates" / f"{tid}.webp")
        stats["titles/native_plates"] += 1

    return dict(stats)


def ensure_ui_workspace(ws_tex: Path, ib_dir: Path, core_dir: Path) -> None:
    """Directly scans .fhm2d containers in ib_dir via fhm2d_native_index and extracts any missing UI textures on demand."""
    if str(core_dir) not in sys.path:
        sys.path.insert(0, str(core_dir))
    import fhm2d_native_index as fni
    import fhm2d_extract_textures as fet
    import fhm2d_batch_textures as fbt

    inv_csv = ws_tex / "inventory" / "textures.csv"
    ui_prefixes = (
        "46XTms_s_s_",
        "46XTms_ms_s_",
        "46XTms_vs_s_l_",
        "46XTms_vs_s_r_",
        "46XTvs_p_l_",
        "46XTvs_p_r_",
        "46XTsticker_ex_p_",
        "46XTms_sticker_",
        "46XTsticker_bg_",
        "46XTsticker_emb_",
        "46XTsticker_frm_",
        "46XTtag_em_",
        "46XTshogo_m_",
        "46XTplayerbadge_",
        "46XTrank_playerlevel_",
        "46XTrank_class_",
        "46XTnavi_pl_s_",
        "46XTnavi_bt_s_",
    )
    need_scan = not inv_csv.exists()
    if not need_scan:
        existing_rows = list(csv.DictReader(open(inv_csv, "r", encoding="utf-8-sig")))
        if len(existing_rows) < 1000:
            need_scan = True

    if need_scan:
        print(f"[ExvsAssetPipeline] Scanning {ib_dir} directly via fhm2d_native_index for UI textures...")
        inv_csv.parent.mkdir(parents=True, exist_ok=True)
        texconv = fbt.find_texconv(None)
        rows_out = []
        for p in sorted(ib_dir.glob("*.fhm2d")):
            try:
                with p.open("rb") as f:
                    f.seek(0x30)
                    head_chunk = f.read(65536)
                    d0 = zlib.decompressobj(-15)
                    idx_bytes = d0.decompress(head_chunk)
                    while not d0.eof:
                        more = f.read(65536)
                        if not more:
                            break
                        idx_bytes += d0.decompress(more)
                    block_area = f.tell() - len(d0.unused_data)
                    parsed = fni.parse(idx_bytes)
                    matched_pkg = False
                    for r0 in parsed["records"]:
                        if r0["length"] < 0xB0 or not r0["sizes"]:
                            continue
                        last_off = block_area + r0["first_block"] + sum(s & 0xFFFFFFFF for s in r0["sizes"][:-1])
                        last_sz = r0["sizes"][-1] & 0xFFFFFFFF
                        f.seek(last_off)
                        last_blk = zlib.decompress(f.read(last_sz), -15)
                        if len(last_blk) >= 0xB0:
                            meta = last_blk[-0xB0:]
                            if meta[0x40:0x44] == b"46XT":
                                en = meta[0x40:0x80].split(b"\x00", 1)[0].decode("ascii", "ignore")
                                if en.startswith(ui_prefixes):
                                    matched_pkg = True
                                    break
                    if not matched_pkg:
                        continue
                pkg_dir = ws_tex / "packages" / "pending" / p.stem
                png_dir = pkg_dir / "png"
                if not png_dir.exists() or not list(png_dir.glob("*.png")):
                    fet.extract_file(p, pkg_dir, strict_payload=False)
                    dds_files = sorted((pkg_dir / "dds").glob("*.dds"))
                    if dds_files:
                        fbt.convert_dds_batch(texconv, dds_files, png_dir)
                        for d in dds_files:
                            d.unlink()
                manifest_path = pkg_dir / "manifest.json"
                if manifest_path.exists():
                    mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for t in mdata.get("textures", []):
                        png_name = Path(t["output"]).with_suffix(".png").name
                        rel_png = f"packages/pending/{p.stem}/png/{png_name}"
                        rows_out.append({
                            "package": p.stem,
                            "embedded_name": t["embedded_name"],
                            "width": t["width"],
                            "height": t["height"],
                            "png_output": rel_png,
                        })
            except Exception:
                continue
        with inv_csv.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["package", "embedded_name", "width", "height", "png_output"])
            writer.writeheader()
            writer.writerows(rows_out)


def generate_gauges(workspace: Path, game_dir: Path, out_images: Path) -> Dict[str, int]:
    """
    Parses 0x54440854.fhm2d (Master Customize UI Gauge Table: 60 rows in IB)
    and synthesizes 1920x1080 battle HUD gauge preview images (`images/gauges/{id}.webp`)
    from the 15 native RGBA8 46XT textures inside each Gauge package:
      - TeamBase.png   (600x200) -> (0, 0)
      - EnemyBase.png  (600x200) -> (0, 0)
      - TimerBase.png  (300x200) -> (468, 0)
      - RadarBase.png  (400x400) -> (1520, 0)
      - ArmorBase.png  (500x348) -> (0, 732)
      - ExBurstBase.png(428x228) -> (373, 852)
      - BoostBase.png  (600x228) -> (660, 852)
      - WeaponBase.png (348x156) -> (1572, 702), (1572, 802), (1572, 902)
      - ChargeBase.png (252x104) -> (1566, 770)
    """
    gauge_db = game_dir / "0x54440854.fhm2d"
    if not gauge_db.exists():
        return {"gauges_webp": 0}

    gauges_dir = out_images / "gauges"
    gauges_dir.mkdir(parents=True, exist_ok=True)

    # Parse 0x54440854.fhm2d SDB1 table
    blocks = list(iter_fhm2d_deflate_blocks(gauge_db.read_bytes()))
    blob = blocks[1][3]
    num_rows, num_cols, row_stride = struct.unpack_from("<III", blob, 0x10)
    col_hashes = list(struct.unpack_from(f"<{num_cols}I", blob, 0x20))
    desc_off = 0x20 + num_cols * 4
    col_descs = [struct.unpack_from("<QI", blob, desc_off + c * 12) for c in range(num_cols)]
    rows_data_off = (num_cols + 2) * 16 + 4 * num_rows

    gauge_pkgs: Dict[int, int] = {}
    for r in range(num_rows):
        rbase = rows_data_off + r * row_stride
        row_dict = {}
        for (col_off, col_type), col_hash in zip(col_descs, col_hashes):
            val = struct.unpack_from("<I", blob, rbase + col_off)[0]
            row_dict[col_hash] = val
        gid = row_dict.get(0x1111D441, 0)
        ghash = row_dict.get(0x338558B9, 0)
        if ghash != 0 and (game_dir / f"0x{ghash:08X}.fhm2d").exists():
            gauge_pkgs[gid] = ghash

    def extract_gauge_rgba_textures(fhm_path: Path) -> List[np.ndarray]:
        blks = list(iter_fhm2d_deflate_blocks(fhm_path.read_bytes()))
        pay = b"".join(b for _, _, _, b in blks[1:])
        texs: List[np.ndarray] = []
        for idx in range(15):
            tag = f"46XTimg-{idx:05d}".encode("ascii")
            pos = pay.find(tag)
            if pos == -1:
                break
            ft_off = pos - 0x40
            w = int.from_bytes(pay[ft_off + 0x84 : ft_off + 0x88], "little")
            h = int.from_bytes(pay[ft_off + 0x88 : ft_off + 0x8C], "little")
            dsz = int.from_bytes(pay[ft_off + 0xA4 : ft_off + 0xA8], "little")
            arr = np.frombuffer(pay[ft_off - dsz : ft_off], dtype=np.uint8).reshape((h, w, 4))
            texs.append(arr)
        return texs

    def build_gauge_canvas(fhm_path: Path) -> np.ndarray:
        dds = extract_gauge_rgba_textures(fhm_path)
        if len(dds) < 15:
            return np.zeros((1080, 1920, 4), dtype=np.float32)
        if dds[0].shape[1] == 428 and dds[1].shape[1] == 600:
            dds[0], dds[1] = dds[1], dds[0]
        canvas = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        placements = [
            (12, 0, 0),      # EnemyBase
            (11, 0, 0),      # TeamBase
            (13, 468, 0),    # TimerBase
            (14, 1520, 0),   # RadarBase
            (10, 0, 732),    # ArmorBase
            (1,  373, 852),  # ExBurstBase
            (0,  660, 852),  # BoostBase
            (2,  1572, 702), # WeaponBase 1
            (2,  1572, 802), # WeaponBase 2
            (2,  1572, 902), # WeaponBase 3
            (3,  1566, 770), # ChargeBase Slot 1
        ]
        for idx, x, y in placements:
            canvas.alpha_composite(Image.fromarray(dds[idx], "RGBA"), (x, y))
        return np.array(canvas, dtype=np.float32)

    template_dir = workspace.parent / "gauge_template"
    bg_path = template_dir / "hud_bg.webp"
    fg_path = template_dir / "hud_fg.webp"
    has_template = bg_path.exists() and fg_path.exists()
    if has_template:
        B = np.array(Image.open(bg_path).convert("RGB"), dtype=np.float32)
        fg = np.array(Image.open(fg_path).convert("RGBA"), dtype=np.float32)
        alpha_f = fg[:, :, 3:4] / 255.0
        T_F = 1.0 - alpha_f
        F_pre = fg[:, :, :3] * alpha_f

    cnt = 0
    for gid, ghash in sorted(gauge_pkgs.items()):
        out_p = gauges_dir / f"{gid}.webp"
        if out_p.exists():
            cnt += 1
            continue
        fhm_p = game_dir / f"0x{ghash:08X}.fhm2d"
        g = build_gauge_canvas(fhm_p)
        if has_template:
            c = g[:, :, :3]
            a = g[:, :, 3:4] / 255.0
            m = a * c + (1.0 - a) * B
            pred = np.clip(F_pre + T_F * m, 0.0, 255.0).astype(np.uint8)
            Image.fromarray(pred, "RGB").save(out_p, "WEBP", quality=90)
        else:
            Image.fromarray(g.astype(np.uint8), "RGBA").save(out_p, "WEBP", quality=90)
        cnt += 1

    return {"gauges_webp": cnt}


def main():
    parser = argparse.ArgumentParser(description="ExvsAssetPipeline: XB / OB / IB Native Asset Generator")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(r"G:\GitHubRepo\fhm2d-unpack\workspace\all-textures"),
        help="Path to unpacked all-textures workspace",
    )
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path(r"G:\EXVS2InfiniteBoost\data\x64\dplcache_release"),
        help="Path to game dplcache_release directory (.fhm2d files)",
    )
    parser.add_argument(
        "--core-dir",
        type=Path,
        default=Path(r"G:\GitHubRepo\fhm2d-unpack\_internal\core"),
        help="Path to fhm2d-unpack core directory",
    )
    parser.add_argument(
        "--webui-root",
        type=Path,
        default=Path(r"G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot"),
        help="Path to WebUI wwwroot directory",
    )
    parser.add_argument(
        "--out-images",
        type=Path,
        default=None,
        help="Optional explicit output images directory (e.g. G:\\EXVS2InfiniteBoost\\images)",
    )
    args = parser.parse_args()

    out_images = args.out_images if args.out_images else (args.webui_root / "images")
    webui_data = args.webui_root / "data"

    ensure_ui_workspace(args.workspace, args.game_dir, args.core_dir)

    print("[ExvsAssetPipeline] Starting full native asset generation...")
    total_stats = {}

    s1 = generate_player_level_and_class(args.workspace, args.game_dir, out_images, args.core_dir)
    total_stats.update(s1)
    print("  [1/4] Player Level, Badges, Frames & Class:", s1)

    s2 = generate_cost_mastery_bursts(args.workspace, args.game_dir, out_images, args.core_dir)
    total_stats.update(s2)
    print("  [2/4] Cost, Mastery & Bursts:", s2)

    s3 = generate_navigators(args.workspace, args.game_dir, out_images, args.core_dir)
    total_stats.update(s3)
    print("  [3/4] Navigators (Avatars + Full-Body Static .webp + Blinking/Speaking .gif):", s3)

    s4 = generate_ms_pilots_stickers_team_stages(args.workspace, args.game_dir, webui_data, out_images)
    total_stats.update(s4)
    print("  [4/5] Mobile Suits, Skins, History, Pilots, Stickers, Team & Title Plates:", s4)

    s5 = generate_gauges(args.workspace, args.game_dir, out_images)
    total_stats.update(s5)
    print("  [5/5] Customize Battle HUD Gauges (0x54440854.fhm2d):", s5)

    print("\n[ExvsAssetPipeline] All categories generated successfully!")
    print(json.dumps(total_stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
