#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EXVS2 Infinite Boost - SDB1 Database Diff, Audit & WebUI JSON CRUD Manager
==========================================================================
Directly parses the game client's `.fhm2d` SDB1 binary database tables:
  - 0xDFD38C70.fhm2d : Master Mobile Suit, Pilot Costume, Skin & Sticker Pose Table
  - 0x6FCC0FBA.fhm2d : Master Navigator & Navigator Costume Table
  - 0xED5C1B36.fhm2d : Master Communication Stamp Table
  - 0x89407144.fhm2d : Master Title Table
  - 0x0C948B3A.fhm2d : Master Title Plate Texture Table

Features:
  1. `scan` / `diff`: Detect newly added/modified/removed Mobile Suits, Pilot Costumes,
     Skins, Sticker Poses, Navigators, Navigator Costumes, and Stamps after a game update,
     printing their exact runtime IDs (`MsId`, `CostumeId`, `SkinId`, `NaviId`, etc.) and
     `.fhm2d` asset package hashes.
  2. `sync`: Automatically append new entries from SDB1 into `WebUI-Infinite/Client/wwwroot/data/*.json`.
  3. `audit`: Verify 1-to-1 consistency between SDB1 binary tables, `wwwroot/data/*.json`,
     and `wwwroot/images/` (0 missing, 0 extra).
  4. `ms` / `pilot` / `navi`: Full CRUD (Add / Update / Delete / List / Show) CLI for
     `MobileSuits.json` and `Navigators.json`.
  5. `interactive`: Interactive console menu for inspecting updates and editing JSON files.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Default paths on Windows workstation
DEFAULT_IB_DIR = Path(r"G:\EXVS2InfiniteBoost\data\x64\dplcache_release")
DEFAULT_WEBUI_DATA = Path(r"G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\data")
DEFAULT_WEBUI_IMAGES = Path(r"G:\AGiteeRepo\exvs2-ib\WebUI-Infinite\Client\wwwroot\images")

# Known SDB1 Series ID -> (SeriesJP, SeriesEN, SeriesCN) mapping in Infinite Boost
SERIES_ID_MAP: Dict[int, Tuple[str, str, str]] = {
    1: ("機動戦士ガンダム", "Mobile Suit Gundam", "机动战士高达"),
    2: ("機動戦士Zガンダム", "Mobile Suit Z Gundam", "机动战士Z高达"),
    3: ("機動戦士ガンダムZZ", "Mobile Suit Gundam ZZ", "机动战士高达ZZ"),
    4: ("機動戦士ガンダムF91", "Mobile Suit Gundam F91", "机动战士高达F91"),
    5: ("機動戦士Vガンダム", "Mobile Suit Victory Gundam", "机动战士V高达"),
    7: ("機動新世紀ガンダムX", "After War Gundam X", "机动新世纪高达X"),
    8: ("機動戦士ガンダム 第08MS小隊", "Mobile Suit Gundam: The 08th MS Team", "机动战士高达 第08MS小队"),
    10: ("∀ガンダム", "Turn A Gundam", "∀高达"),
    12: ("機動戦士ガンダム0080 ポケットの中の戦争", "Mobile Suit Gundam 0080 War in the Pocket", "机动战士高达0080 口袋里的战争"),
    13: ("機動戦士ガンダム0083 STARDUST MEMORY", "Mobile Suit Gundam 0083: Stardust Memory", "机动战士高达0083 星尘的回忆"),
    14: ("機動戦士ガンダム00", "Mobile Suit Gundam 00", "机动战士高达00"),
    15: ("機動戦士ガンダムUC", "Mobile Suit Gundam Unicorn", "机动战士高达UC"),
    16: ("新機動戦記ガンダムW Endless Waltz", "Mobile Suit Gundam Wing Endless Waltz", "新机动战记高达W 无尽的华尔兹"),
    17: ("機動戦士ガンダム 逆襲のシャア", "Mobile Suit Gundam: Char's Counterattack", "机动战士高达 逆袭的夏亚"),
    18: ("機動武闘伝Gガンダム", "Mobile Fighter G Gundam", "机动武斗传G高达"),
    20: ("機動戦士ガンダムSEED", "Mobile Suit Gundam SEED", "机动战士高达SEED"),
    21: ("機動戦士ガンダムSEED DESTINY", "Mobile Suit Gundam SEED Destiny", "机动战士高达SEED DESTINY"),
    22: ("機動戦士ガンダムSEED ASTRAY", "Mobile Suit Gundam SEED ASTRAY", "机动战士高达SEED ASTRAY"),
    23: ("機動戦士クロスボーン・ガンダム", "Mobile Suit Crossbone Gundam", "机动战士海盗高达"),
    24: ("機動戦士ガンダム外伝 THE BLUE DESTINY", "Mobile Suit Gundam Side Story: The Blue Destiny", "机动战士高达外传 THE BLUE DESTINY"),
    25: ("機動戦士ガンダム MS IGLOO", "Mobile Suit Gundam MS IGLOO", "机动战士高达 MS IGLOO"),
    26: ("機動戦士ガンダム 逆襲のシャア ベルトーチカ・チルドレン", "Mobile Suit Gundam Char's Counter Attack - Beltorchika's Children", "机动战士高达 逆袭的夏亚 贝托蒂嘉的子嗣"),
    27: ("劇場版 機動戦士ガンダム00 -A wakening of the Trailblazer-", "Mobile Suit Gundam 00 The Movie -Awakening of the Trailblazer-", "剧场版 机动战士高达00 -先驱者的觉醒-"),
    28: ("新機動戦記ガンダムW", "Mobile Suit Gundam Wing", "新机动战记高达W"),
    29: ("機動戦士ガンダムSEED C.E.73 -STARGAZER-", "Mobile Suit Gundam SEED C.E. 73: Stargazer", "机动战士高达SEED C.E.73 -观星者-"),
    30: ("ガンダム・センチネル", "Gundam Sentinel", "高达前哨战"),
    31: ("機動戦士ガンダム 閃光のハサウェイ", "Mobile Suit Gundam: Hathaway's Flash", "机动战士高达 闪光的哈萨维"),
    33: ("機動戦士ガンダムAGE", "Mobile Suit Gundam AGE", "机动战士高达AGE"),
    34: ("ガンダムEXA", "Gundam EXA", "高达EXA"),
    42: ("ガンダム Gのレコンギスタ", "Gundam Reconguista in G", "高达 G之复国运动"),
    43: ("機動戦士ガンダム 鉄血のオルフェンズ", "Mobile Suit Gundam: Iron-Blooded Orphans", "机动战士高达 铁血的奥尔芬斯"),
    45: ("機動戦士ガンダム外伝 ミッシングリンク", "Mobile Suit Gundam Side Story: Missing Link", "机动战士高达外传 缺失的一环"),
    46: ("機動戦士ガンダム サンダーボルト", "Mobile Suit Gundam Thunderbolt", "机动战士高达 雷霆宙域"),
    49: ("機動戦士ガンダム 鉄血のオルフェンズ", "Mobile Suit Gundam: Iron-Blooded Orphans", "机动战士高达 铁血的奥尔芬斯"),
    51: ("ガンダムビルドファイターズ", "Gundam Build Fighters", "高达创战者"),
    52: ("ガンダムビルドファイターズA-R", "Gundam Build Fighters A-R", "高达创战者A-R"),
    53: ("ガンダムビルドファイターズトライ", "Gundam Build Fighters Try", "高达创战者TRY"),
    55: ("SDガンダム外伝", "SD Gundam Gaiden", "SD高达外传"),
    56: ("機動戦士ガンダムNT", "Mobile Suit Gundam Narrative", "机动战士高达NT"),
    57: ("ガンダムビルドダイバーズ", "Gundam Build Divers", "高达创形者"),
    58: ("機動戦士ガンダム ヴァルプルギス", "Mobile Suit Gundam Valpurgis", "机动战士高达 瓦尔普吉斯"),
    59: ("Project N-EXTREME", "Project N-EXTREME", "Project N-EXTREME"),
    62: ("ガンダムビルドダイバーズRe:RISE", "Gundam Build Divers Re:Rise", "高达创形者Re:RISE"),
    66: ("機動戦士ガンダム 水星の魔女", "Mobile Suit Gundam: The Witch from Mercury", "机动战士高达 水星的魔女"),
    67: ("機動戦士ガンダム 復讐のレクイエム", "Mobile Suit Gundam: Requiem for Vengeance", "机动战士高达 复仇的安魂曲"),
    68: ("機動戦士ガンダムSEED FREEDOM", "Mobile Suit Gundam SEED FREEDOM", "机动战士高达SEED FREEDOM"),
    69: ("機動戦士Gundam GQuuuuuuX", "Mobile Suit Gundam GQuuuuuuX", "机动战士高达 GQuuuuuuX"),
}


# ============================================================================
# Low-level FHM2D & SDB1 Binary Table Reader
# ============================================================================

def iter_fhm2d_deflate_blocks(blob: bytes, start_offset: int = 0x30, chunk_size: int = 65536) -> Iterable[Tuple[int, int, int, bytes]]:
    """Iterates raw deflate blocks (wbits=-15) in an .fhm2d container."""
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
            if offset < len(blob) and blob[offset : offset + 2] == b"a1":
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


def decode_sdb1_str(raw: bytes) -> str:
    """
    Decodes an obfuscated UTF-8 string from an SDB1 string pool (`col_type == 7`):
      1. Odd byte indices are bitwise inverted (`b ^= 0xFF`).
      2. Adjacent bit pairs are swapped (`((b & 0xAA) >> 1) | ((b & 0x55) << 1)`).
      3. Each byte at index `i` is rotated right by `i % 7` bits.
    """
    out = bytearray()
    for i, b in enumerate(raw):
        if i & 1:
            b ^= 0xFF
        b = ((b & 0xAA) >> 1) | ((b & 0x55) << 1)
        r = i % 7
        if r:
            b = ((b >> r) | (b << (8 - r))) & 0xFF
        out.append(b)
    return out.decode("utf-8", errors="replace")


def parse_sdb1_blob(blob: bytes) -> Tuple[List[int], List[int], List[Dict[int, int]]]:
    """
    Parses a decompressed SDB1 / FHM binary table blob.
    Returns (row_keys, col_hashes, rows_as_dicts_keyed_by_col_hash).
    """
    if len(blob) < 0x20:
        raise ValueError(f"Invalid SDB1 header: {blob[:16]!r}")
    num_rows, num_cols, row_stride = struct.unpack_from("<III", blob, 0x10)
    col_hashes = list(struct.unpack_from(f"<{num_cols}I", blob, 0x20))
    desc_off = 0x20 + num_cols * 4
    col_descs = [struct.unpack_from("<QI", blob, desc_off + c * 12) for c in range(num_cols)]
    row_keys_off = (num_cols + 2) * 16
    rows_data_off = row_keys_off + 4 * num_rows
    row_keys = list(struct.unpack_from(f"<{num_rows}I", blob, row_keys_off))

    rows: List[Dict[int, int]] = []
    for r in range(num_rows):
        rbase = rows_data_off + r * row_stride
        row_dict: Dict[int, int] = {}
        for (col_off, col_type), col_hash in zip(col_descs, col_hashes):
            if col_type == 7:
                val = struct.unpack_from("<Q", blob, rbase + col_off)[0]
            elif col_type in (0, 2, 9):
                val = struct.unpack_from("<B", blob, rbase + col_off)[0]
            elif col_type == 3:
                val = struct.unpack_from("<H", blob, rbase + col_off)[0]
            else:
                val = struct.unpack_from("<I", blob, rbase + col_off)[0]
            row_dict[col_hash] = val
        rows.append(row_dict)
    return row_keys, col_hashes, rows


def probe_embedded_name(ib_dir: Path, pkg_hash: int) -> Optional[str]:
    """Extracts the embedded 46XT / g00 texture name from a .fhm2d package if present."""
    if pkg_hash == 0:
        return None
    f = ib_dir / f"0x{pkg_hash:08X}.fhm2d"
    if not f.exists() or f.stat().st_size <= 64:
        return None
    raw = f.read_bytes()
    for _, _, _, dec in iter_fhm2d_deflate_blocks(raw):
        m = re.search(rb"(46XT[A-Za-z0-9_]+|g00_[A-Za-z0-9_]+)", dec)
        if m:
            return m.group(1).decode("ascii", errors="ignore")
    return None


def is_valid_texture_pkg(ib_dir: Path, pkg_hash: int) -> bool:
    """Returns True if pkg_hash points to a real, non-stub, non-dummy .fhm2d texture package."""
    if pkg_hash == 0:
        return False
    f = ib_dir / f"0x{pkg_hash:08X}.fhm2d"
    if not f.exists():
        return False
    sz = f.stat().st_size
    # 63 bytes = empty stub; 41634 bytes = 46XTvs_p_l_999_999_c99 placeholder dummy
    if sz <= 64 or sz == 41634:
        return False
    return True


# ============================================================================
# Data Models for Parsed SDB1 Entities
# ============================================================================

@dataclass
class SdbPilotCostume:
    costume_id: int
    left_hash: int
    right_hash: int
    ex_p_hash: int
    embedded_name: Optional[str] = None


@dataclass
class SdbMobileSuit:
    ms_id: int
    row_key: int
    series_id: int
    unit_idx: int
    cost: int
    ms_select_hash: int
    embedded_name: Optional[str] = None
    skins: List[Tuple[int, int, Optional[str]]] = field(default_factory=list)
    costumes: List[SdbPilotCostume] = field(default_factory=list)
    poses: List[Tuple[int, int, Optional[str]]] = field(default_factory=list)


@dataclass
class SdbNaviCostume:
    costume_id: int
    fullbody_hash: int
    avatar_hash: int
    embedded_name: Optional[str] = None


@dataclass
class SdbNavigator:
    navi_id: int
    char_code: str
    avatar_hash: int
    costumes: List[SdbNaviCostume] = field(default_factory=list)


# ============================================================================
# SDB1 Master Table Parsers
# ============================================================================

def load_sdb1_mobile_suits(ib_dir: Path, include_embedded_names: bool = False) -> Dict[int, SdbMobileSuit]:
    """
    Parses 0xDFD38C70.fhm2d (Master Mobile Suit Table).
    Returns {ms_id: SdbMobileSuit}.
    """
    ms_db_path = ib_dir / "0xDFD38C70.fhm2d"
    if not ms_db_path.exists():
        raise FileNotFoundError(f"Master Mobile Suit DB not found: {ms_db_path}")

    blocks = list(iter_fhm2d_deflate_blocks(ms_db_path.read_bytes()))
    blob = b"".join(b for _, _, _, b in blocks[1:])
    row_keys, _, rows = parse_sdb1_blob(blob)

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

    suits: Dict[int, SdbMobileSuit] = {}
    skin_rows: List[Tuple[int, int, int, Dict[int, int]]] = []

    for rk, vals in zip(row_keys, rows):
        mid = vals[0xA88E762A]
        skin_id = vals[0x8157B2C0]
        series_id = rk // 1000000
        unit_idx = (rk // 1000) % 1000
        cost = vals.get(0xFC33DA00, 0)
        ms_hash = vals.get(0xE4E3753B, 0)

        if skin_id == 0:
            ms_obj = SdbMobileSuit(
                ms_id=mid,
                row_key=rk,
                series_id=series_id,
                unit_idx=unit_idx,
                cost=cost,
                ms_select_hash=ms_hash,
                embedded_name=probe_embedded_name(ib_dir, ms_hash) if include_embedded_names else None,
            )
            for cid, cl, cr, cex in costume_cols:
                hl, hr, hexp = vals.get(cl, 0), vals.get(cr, 0), vals.get(cex, 0)
                if hexp != 0 and is_valid_texture_pkg(ib_dir, hl) and is_valid_texture_pkg(ib_dir, hr):
                    ms_obj.costumes.append(
                        SdbPilotCostume(
                            costume_id=cid,
                            left_hash=hl,
                            right_hash=hr,
                            ex_p_hash=hexp,
                            embedded_name=probe_embedded_name(ib_dir, hl) if include_embedded_names else None,
                        )
                    )
            valid_poses = []
            for _, col_h in sticker_cols:
                ph = vals.get(col_h, 0)
                if is_valid_texture_pkg(ib_dir, ph):
                    valid_poses.append((ph, probe_embedded_name(ib_dir, ph) if include_embedded_names else None))
            for pidx, (ph, pname) in enumerate(valid_poses):
                ms_obj.poses.append((pidx, ph, pname))

            suits[mid] = ms_obj
        else:
            skin_rows.append((mid, skin_id, rk, vals))

    for mid, skin_id, rk, vals in skin_rows:
        if mid in suits:
            s_obj = suits[mid]
            if not s_obj.skins:
                s_obj.skins.append((0, s_obj.ms_select_hash, s_obj.embedded_name))
            sh = vals.get(0xE4E3753B, 0)
            s_obj.skins.append((skin_id, sh, probe_embedded_name(ib_dir, sh) if include_embedded_names else None))

    return dict(sorted(suits.items()))


def load_sdb1_navigators(ib_dir: Path, include_embedded_names: bool = False) -> Dict[int, SdbNavigator]:
    """
    Parses 0x6FCC0FBA.fhm2d Block #1 (Navigator & Costume Table: 144 rows).
      - col 0xA88E762A : NaviId (1..52)
      - col 0x692B6C6E : CostumeId (0..7)
      - col 0x6E3EAF61 : Full-body sprite package hash (.fhm2d)
      - col 0xABF1C7FB : Avatar icon package hash (46XTnavi_pl_s_*)
    Returns {navi_id: SdbNavigator}.
    """
    navi_db_path = ib_dir / "0x6FCC0FBA.fhm2d"
    if not navi_db_path.exists():
        raise FileNotFoundError(f"Master Navigator DB not found: {navi_db_path}")

    blocks = list(iter_fhm2d_deflate_blocks(navi_db_path.read_bytes()))
    _, _, rows = parse_sdb1_blob(blocks[1][3])

    navis: Dict[int, SdbNavigator] = {}
    for vals in sorted(rows, key=lambda x: (x[0xA88E762A], x[0x692B6C6E])):
        nid = vals[0xA88E762A]
        cid = vals[0x692B6C6E]
        h_body = vals[0x6E3EAF61]
        h_avatar = vals[0xABF1C7FB]
        if nid <= 0 or h_body == 0 or not is_valid_texture_pkg(ib_dir, h_body):
            continue

        ename = probe_embedded_name(ib_dir, h_avatar or h_body) if include_embedded_names else None
        if nid not in navis:
            navis[nid] = SdbNavigator(
                navi_id=nid,
                char_code=ename or f"navi_{nid:03d}",
                avatar_hash=h_avatar,
            )
        if cid == 0 and h_avatar != 0:
            navis[nid].avatar_hash = h_avatar
        navis[nid].costumes.append(
            SdbNaviCostume(
                costume_id=cid,
                fullbody_hash=h_body,
                avatar_hash=h_avatar,
                embedded_name=ename,
            )
        )

    return dict(sorted(navis.items()))


def load_sdb1_stamps(ib_dir: Path) -> Dict[int, int]:
    """Parses 0xED5C1B36.fhm2d (Master Communication Stamp Table). Returns {stamp_id: pkg_hash}."""
    stamp_db = ib_dir / "0xED5C1B36.fhm2d"
    if not stamp_db.exists():
        return {}
    blocks = list(iter_fhm2d_deflate_blocks(stamp_db.read_bytes()))
    rkeys, _, rows = parse_sdb1_blob(blocks[1][3])
    out: Dict[int, int] = {}
    for rk, vals in zip(rkeys, rows):
        st_id = vals.get(0x1111D441, 0)
        h = vals.get(0xC5A29798, 0)
        if st_id > 0 and is_valid_texture_pkg(ib_dir, h):
            out[st_id] = h
    return out


def load_sdb1_stamp_labels(ib_dir: Path) -> Dict[int, str]:
    """Parses 0xED5C1B36.fhm2d string column 0xD139963F and returns {stamp_id: label_str}."""
    stamp_db = ib_dir / "0xED5C1B36.fhm2d"
    if not stamp_db.exists():
        return {}
    blocks = list(iter_fhm2d_deflate_blocks(stamp_db.read_bytes()))
    blob = blocks[1][3]
    num_rows, num_cols, row_stride = struct.unpack_from("<III", blob, 0x10)
    rows_data_off = (num_cols + 2) * 16 + 4 * num_rows
    out: Dict[int, str] = {}
    for r in range(num_rows):
        rbase = rows_data_off + r * row_stride
        st_id = struct.unpack_from("<I", blob, rbase + 0)[0]
        str_off = struct.unpack_from("<Q", blob, rbase + 12)[0]
        if 0 < str_off < len(blob):
            end = blob.find(b"\x00", str_off)
            raw_lbl = decode_sdb1_str(blob[str_off:end]).strip()
            # Strip leading numeric ID prefix like "440  "
            parts = raw_lbl.split(None, 1)
            out[st_id] = parts[1].strip() if len(parts) == 2 and parts[0].isdigit() else raw_lbl
    return out


def load_sdb1_gauges(ib_dir: Path) -> Dict[int, int]:
    """Parses 0x54440854.fhm2d (Master Customize UI Gauge Table). Returns {gauge_id: pkg_hash}."""
    gauge_db = ib_dir / "0x54440854.fhm2d"
    if not gauge_db.exists():
        return {}
    blocks = list(iter_fhm2d_deflate_blocks(gauge_db.read_bytes()))
    _, _, rows = parse_sdb1_blob(blocks[1][3])
    out: Dict[int, int] = {}
    for vals in rows:
        gid = vals.get(0x1111D441, 0)
        h = vals.get(0x338558B9, 0)
        if is_valid_texture_pkg(ib_dir, h):
            out[gid] = h
    return dict(sorted(out.items()))


# ============================================================================
# Helper for Reading JSON Item Sub-Ids (supports `Id`, `ID`, and `CostumeId`/`SkinId`/`PoseId`)
# ============================================================================

def _sub_id(item: Dict[str, Any], alt_key: str = "Id") -> int:
    if "Id" in item:
        return item["Id"]
    if "ID" in item:
        return item["ID"]
    return item.get(alt_key, 0)


def _is_existing_item(item: Dict[str, Any]) -> bool:
    ex = item.get("Existence", True)
    if ex in (False, "Not Existed", "None", None):
        return False
    return _sub_id(item) > 0


def _ms_costume_ids_from_json(ms_entry: Dict[str, Any]) -> Set[int]:
    c_list = ms_entry.get("Costumes")
    if not c_list:
        return {0}
    return {_sub_id(c, "CostumeId") for c in c_list}


# ============================================================================
# Diff & Update Scanner (`scan` / `diff`)
# ============================================================================

def scan_updates(
    ib_dir: Path,
    webui_data_dir: Path,
    report_out: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Compares the game client's SDB1 binary databases against WebUI JSON files
    and reports any newly added, modified, or removed Mobile Suits, Pilot Costumes,
    Skins, Poses, Navigators, Navigator Costumes, and Stamps.
    """
    sdb_ms = load_sdb1_mobile_suits(ib_dir, include_embedded_names=False)
    sdb_navis = load_sdb1_navigators(ib_dir, include_embedded_names=False)
    sdb_stamps = load_sdb1_stamps(ib_dir)
    sdb_gauges = load_sdb1_gauges(ib_dir)

    ms_json_path = webui_data_dir / "MobileSuits.json"
    navi_json_path = webui_data_dir / "Navigators.json"
    stamps_json_path = webui_data_dir / "messages" / "Stamps.json"
    gauges_json_path = webui_data_dir / "Gauges.json"

    ms_json = json.loads(ms_json_path.read_text(encoding="utf-8-sig"))
    navi_json = json.loads(navi_json_path.read_text(encoding="utf-8-sig"))
    stamps_json = json.loads(stamps_json_path.read_text(encoding="utf-8-sig")) if stamps_json_path.exists() else []
    gauges_json = json.loads(gauges_json_path.read_text(encoding="utf-8-sig")) if gauges_json_path.exists() else []

    json_ms_map = {x["Id"]: x for x in ms_json if 0 < x["Id"] < 50000}
    json_navi_map = {x["Id"]: x for x in navi_json if x["Id"] > 0}
    json_stamp_ids = {_sub_id(x) for x in stamps_json if _is_existing_item(x)}
    json_gauge_ids = {_sub_id(x) for x in gauges_json if x.get("Existence") not in (False, "Not Existed", "None", None)}

    diff_result: Dict[str, Any] = {
        "new_mobile_suits": [],
        "removed_mobile_suits": [],
        "new_pilot_costumes": [],
        "removed_pilot_costumes": [],
        "new_ms_skins": [],
        "pose_count_changes": [],
        "new_navigators": [],
        "new_navigator_costumes": [],
        "removed_navigator_costumes": [],
        "new_stamps": [],
        "new_gauges": [],
    }

    # 1. Compare Mobile Suits, Pilot Costumes, Skins, Poses
    for mid, sdb_unit in sdb_ms.items():
        s_jp, s_en, s_cn = SERIES_ID_MAP.get(sdb_unit.series_id, ("Unknown", "Unknown", "未知作品"))
        if mid not in json_ms_map:
            ename = probe_embedded_name(ib_dir, sdb_unit.ms_select_hash)
            diff_result["new_mobile_suits"].append({
                "MsId": mid,
                "RowKey": sdb_unit.row_key,
                "SeriesId": sdb_unit.series_id,
                "SeriesCN": s_cn,
                "SeriesEN": s_en,
                "Cost": sdb_unit.cost,
                "MsSelectPkg": f"0x{sdb_unit.ms_select_hash:08X}",
                "EmbeddedName": ename,
                "CostumeIds": [c.costume_id for c in sdb_unit.costumes],
                "SkinIds": [s[0] for s in sdb_unit.skins],
                "PoseIds": [p[0] for p in sdb_unit.poses],
            })
            continue

        j_unit = json_ms_map[mid]
        j_cids = _ms_costume_ids_from_json(j_unit)
        s_cids = {c.costume_id for c in sdb_unit.costumes}

        for c in sdb_unit.costumes:
            if c.costume_id not in j_cids:
                diff_result["new_pilot_costumes"].append({
                    "MsId": mid,
                    "MsNameCN": j_unit.get("ValueCN", ""),
                    "PilotCN": j_unit.get("PilotCN", ""),
                    "CostumeId": c.costume_id,
                    "LeftPkg": f"0x{c.left_hash:08X}",
                    "RightPkg": f"0x{c.right_hash:08X}",
                    "ExPPkg": f"0x{c.ex_p_hash:08X}",
                    "EmbeddedName": probe_embedded_name(ib_dir, c.left_hash),
                })
        for cid in sorted(j_cids - s_cids):
            diff_result["removed_pilot_costumes"].append({
                "MsId": mid,
                "MsNameCN": j_unit.get("ValueCN", ""),
                "CostumeId": cid,
            })

        j_skins = {_sub_id(s, "SkinId") for s in (j_unit.get("Skins") or [])}
        for sid, shash, _ in sdb_unit.skins:
            if sid not in j_skins:
                diff_result["new_ms_skins"].append({
                    "MsId": mid,
                    "MsNameCN": j_unit.get("ValueCN", ""),
                    "SkinId": sid,
                    "Pkg": f"0x{shash:08X}",
                    "EmbeddedName": probe_embedded_name(ib_dir, shash),
                })

        j_poses = {_sub_id(p, "PoseId") for p in (j_unit.get("Poses") or [])}
        s_poses = {p[0] for p in sdb_unit.poses}
        if j_poses != s_poses:
            diff_result["pose_count_changes"].append({
                "MsId": mid,
                "MsNameCN": j_unit.get("ValueCN", ""),
                "JsonPoses": sorted(j_poses),
                "SdbPoses": sorted(s_poses),
            })

    for mid in sorted(set(json_ms_map.keys()) - set(sdb_ms.keys())):
        diff_result["removed_mobile_suits"].append({
            "MsId": mid,
            "MsNameCN": json_ms_map[mid].get("ValueCN", ""),
        })

    # 2. Compare Navigators & Navigator Costumes
    for nid, sdb_nav in sdb_navis.items():
        if nid not in json_navi_map:
            ename = probe_embedded_name(ib_dir, sdb_nav.avatar_hash)
            diff_result["new_navigators"].append({
                "NaviId": nid,
                "CharCode": ename or sdb_nav.char_code,
                "AvatarPkg": f"0x{sdb_nav.avatar_hash:08X}",
                "Costumes": [
                    {
                        "CostumeId": c.costume_id,
                        "FullBodyPkg": f"0x{c.fullbody_hash:08X}",
                    }
                    for c in sdb_nav.costumes
                ],
            })
            continue

        j_nav = json_navi_map[nid]
        j_cids = {_sub_id(c, "CostumeId") for c in (j_nav.get("Costumes") or [])}
        s_cids = {c.costume_id for c in sdb_nav.costumes}

        for c in sdb_nav.costumes:
            if c.costume_id not in j_cids:
                diff_result["new_navigator_costumes"].append({
                    "NaviId": nid,
                    "NaviNameCN": j_nav.get("ValueCN", ""),
                    "CostumeId": c.costume_id,
                    "FullBodyPkg": f"0x{c.fullbody_hash:08X}",
                })
        for cid in sorted(j_cids - s_cids):
            diff_result["removed_navigator_costumes"].append({
                "NaviId": nid,
                "NaviNameCN": j_nav.get("ValueCN", ""),
                "CostumeId": cid,
            })

    # 3. Compare Stamps & Gauges
    for st_id in sorted(set(sdb_stamps.keys()) - json_stamp_ids):
        diff_result["new_stamps"].append({
            "StampId": st_id,
            "Pkg": f"0x{sdb_stamps[st_id]:08X}",
        })
    for g_id in sorted(set(sdb_gauges.keys()) - json_gauge_ids):
        diff_result["new_gauges"].append({
            "GaugeId": g_id,
            "Pkg": f"0x{sdb_gauges[g_id]:08X}",
        })

    # Print formatted summary
    print("=" * 76)
    print(" EXVS2 Infinite Boost - SDB1 游戏更新增量检测报告")
    print(f" 数据源目录: {ib_dir}")
    print(f" WebUI JSON: {webui_data_dir}")
    print("=" * 76)
    print(f"  [统计] 客户端总表可玩机体数 : {len(sdb_ms)} 台 (JSON: {len(json_ms_map)} 台)")
    print(f"  [统计] 客户端总表领航员数   : {len(sdb_navis)} 名 (JSON: {len(json_navi_map)} 名)")
    print(f"  [统计] 客户端总表通信印章数 : {len(sdb_stamps)} 个 (JSON: {len(json_stamp_ids)} 个)")
    print(f"  [统计] 客户端总表UI仪表盘数 : {len(sdb_gauges)} 个 (JSON: {len(json_gauge_ids)} 个)")
    print("-" * 76)
    print(f"  + 新增机体 (New Mobile Suits)         : {len(diff_result['new_mobile_suits'])}")
    for item in diff_result["new_mobile_suits"]:
        print(
            f"      -> MsId={item['MsId']} | RowKey={item['RowKey']} | Cost={item['Cost']} | "
            f"Series={item['SeriesCN']} | Pkg={item['MsSelectPkg']} ({item['EmbeddedName']}) | "
            f"Costumes={item['CostumeIds']}"
        )

    print(f"  + 新增驾驶员服装 (New Pilot Costumes) : {len(diff_result['new_pilot_costumes'])}")
    for item in diff_result["new_pilot_costumes"]:
        print(
            f"      -> MsId={item['MsId']} ({item['MsNameCN']} - {item['PilotCN']}) | "
            f"CostumeId={item['CostumeId']} | LeftPkg={item['LeftPkg']} ({item['EmbeddedName']})"
        )

    print(f"  + 新增机体换色皮肤 (New MS Skins)     : {len(diff_result['new_ms_skins'])}")
    for item in diff_result["new_ms_skins"]:
        print(
            f"      -> MsId={item['MsId']} ({item['MsNameCN']}) | SkinId={item['SkinId']} | "
            f"Pkg={item['Pkg']} ({item['EmbeddedName']})"
        )

    print(f"  + 新增领航员 (New Navigators)         : {len(diff_result['new_navigators'])}")
    for item in diff_result["new_navigators"]:
        c_str = ", ".join(str(c["CostumeId"]) for c in item["Costumes"])
        print(
            f"      -> NaviId={item['NaviId']} | CharCode={item['CharCode']} | "
            f"AvatarPkg={item['AvatarPkg']} | Costumes=[{c_str}]"
        )

    print(f"  + 新增领航员服装 (New Navi Costumes)  : {len(diff_result['new_navigator_costumes'])}")
    for item in diff_result["new_navigator_costumes"]:
        print(
            f"      -> NaviId={item['NaviId']} ({item['NaviNameCN']}) | "
            f"CostumeId={item['CostumeId']} | FullBodyPkg={item['FullBodyPkg']}"
        )

    print(f"  + 新增通信印章 (New Stamps)           : {len(diff_result['new_stamps'])}")
    print(f"  + 新增UI仪表盘 (New Gauges)           : {len(diff_result['new_gauges'])}")
    print("=" * 76)

    if report_out:
        report_out.write_text(json.dumps(diff_result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存详细 JSON 增量报告至: {report_out}")

    return diff_result


# ============================================================================
# Auto-Sync SDB1 -> WebUI JSON (`sync`)
# ============================================================================

def sync_sdb1_to_json(ib_dir: Path, webui_data_dir: Path) -> None:
    """
    Synchronizes any new Mobile Suits, Pilot Costumes, Skins, Sticker Poses,
    Navigators, Navigator Costumes, and Stamps from SDB1 into WebUI JSON files.
    """
    sdb_ms = load_sdb1_mobile_suits(ib_dir, include_embedded_names=False)
    sdb_navis = load_sdb1_navigators(ib_dir, include_embedded_names=False)
    sdb_stamps = load_sdb1_stamps(ib_dir)

    # 1. Sync MobileSuits.json
    ms_json_path = webui_data_dir / "MobileSuits.json"
    ms_list = json.loads(ms_json_path.read_text(encoding="utf-8-sig"))
    ms_by_id = {x["Id"]: x for x in ms_list}
    added_ms, added_p_costumes, added_skins, updated_poses = 0, 0, 0, 0

    for mid, sdb_u in sdb_ms.items():
        s_jp, s_en, s_cn = SERIES_ID_MAP.get(sdb_u.series_id, ("Unknown", "Unknown", "未知作品"))
        if mid not in ms_by_id:
            new_entry: Dict[str, Any] = {
                "Id": mid,
                "Value": f"New Mobile Suit {mid}",
                "ValueJP": f"新規機体 {mid}",
                "ValueCN": f"新增机体 {mid}",
                "ValueTC": f"新增機體 {mid}",
                "ValueTCTW": f"新增機體 {mid}",
                "Pilot": f"Pilot {mid}",
                "PilotJP": f"パイロット {mid}",
                "PilotCN": f"机师 {mid}",
                "PilotTC": f"機師 {mid}",
                "PilotTC2": f"機師 {mid}",
                "PilotSeiyuu": "-",
                "PilotSeiyuuJP": "-",
                "PilotSeiyuuCN": "-",
                "PilotSeiyuuTC": "-",
                "PilotSeiyuuTC2": "-",
                "Series": s_en,
                "SeriesJP": s_jp,
                "SeriesCN": s_cn,
                "SeriesTC": s_cn,
                "SeriesTCTW": s_cn,
                "Cost": sdb_u.cost,
                "Poses": [
                    {"Id": p_id, "Value": "Default" if p_id == 0 else f"ポーズ{p_id}"}
                    for p_id, _, _ in sdb_u.poses
                ],
            }
            if len(sdb_u.costumes) > 1:
                new_entry["Costumes"] = [
                    {"Id": c.costume_id, "Value": "デフォルト" if c.costume_id == 0 else f"衣装{c.costume_id}"}
                    for c in sdb_u.costumes
                ]
            if sdb_u.skins:
                new_entry["Skins"] = [
                    {"Id": s_id, "Value": "デフォルト" if s_id == 0 else f"アナザーカラー{s_id}"}
                    for s_id, _, _ in sdb_u.skins
                ]
            ms_list.append(new_entry)
            ms_by_id[mid] = new_entry
            added_ms += 1
        else:
            entry = ms_by_id[mid]
            # Costumes
            cur_c_ids = _ms_costume_ids_from_json(entry)
            sdb_c_ids = {c.costume_id for c in sdb_u.costumes}
            if cur_c_ids != sdb_c_ids:
                cur_costumes = {_sub_id(c, "CostumeId"): c for c in (entry.get("Costumes") or [])}
                new_c_list = []
                for c in sdb_u.costumes:
                    if c.costume_id in cur_costumes:
                        new_c_list.append(cur_costumes[c.costume_id])
                    else:
                        new_c_list.append({
                            "Id": c.costume_id,
                            "Value": "デフォルト" if c.costume_id == 0 else f"衣装{c.costume_id}",
                        })
                        if c.costume_id != 0:
                            added_p_costumes += 1
                entry["Costumes"] = new_c_list

            # Skins
            if sdb_u.skins:
                cur_skins = {_sub_id(s, "SkinId"): s for s in (entry.get("Skins") or [])}
                if set(cur_skins.keys()) != {s[0] for s in sdb_u.skins}:
                    new_s_list = []
                    for sid, _, _ in sdb_u.skins:
                        if sid in cur_skins:
                            new_s_list.append(cur_skins[sid])
                        else:
                            new_s_list.append({
                                "Id": sid,
                                "Value": "デフォルト" if sid == 0 else f"アナザーカラー{sid}",
                            })
                            added_skins += 1
                    entry["Skins"] = new_s_list

            # Poses
            cur_poses = {_sub_id(p, "PoseId"): p for p in (entry.get("Poses") or [])}
            if set(cur_poses.keys()) != {p[0] for p in sdb_u.poses}:
                entry["Poses"] = [
                    cur_poses.get(
                        pid,
                        {"Id": pid, "Value": "Default" if pid == 0 else f"ポーズ{pid}"},
                    )
                    for pid, _, _ in sdb_u.poses
                ]
                updated_poses += 1

    ms_list.sort(key=lambda x: x["Id"])
    ms_json_path.write_text(json.dumps(ms_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 2. Sync Navigators.json
    navi_json_path = webui_data_dir / "Navigators.json"
    navi_list = json.loads(navi_json_path.read_text(encoding="utf-8-sig"))
    navi_by_id = {x["Id"]: x for x in navi_list}
    added_navis, added_n_costumes = 0, 0

    for nid, sdb_n in sdb_navis.items():
        if nid not in navi_by_id:
            new_nav = {
                "Id": nid,
                "Value": f"Navigator {nid}",
                "ValueJP": f"ナビ {nid}",
                "ValueCN": f"领航员 {nid}",
                "ValueTC": f"領航員 {nid}",
                "ValueTCTW": f"領航員 {nid}",
                "Series": "-",
                "SeriesJP": "-",
                "SeriesCN": "-",
                "SeriesTC": "-",
                "SeriesTCTW": "-",
                "Seiyuu": "-",
                "SeiyuuJP": "-",
                "SeiyuuCN": "-",
                "SeiyuuTC": "-",
                "SeiyuuTC2": "-",
                "Costumes": [
                    {
                        "Id": c.costume_id,
                        "Value": "Default" if c.costume_id == 0 else f"Costume {c.costume_id}",
                        "ValueJP": "デフォルト" if c.costume_id == 0 else f"衣装{c.costume_id}",
                    }
                    for c in sdb_n.costumes
                ],
            }
            navi_list.append(new_nav)
            navi_by_id[nid] = new_nav
            added_navis += 1
        else:
            entry = navi_by_id[nid]
            cur_costumes = {_sub_id(c, "CostumeId"): c for c in (entry.get("Costumes") or [])}
            if set(cur_costumes.keys()) != {c.costume_id for c in sdb_n.costumes}:
                new_c_list = []
                for c in sdb_n.costumes:
                    if c.costume_id in cur_costumes:
                        new_c_list.append(cur_costumes[c.costume_id])
                    else:
                        new_c_list.append({
                            "Id": c.costume_id,
                            "Value": "Default" if c.costume_id == 0 else f"Costume {c.costume_id}",
                            "ValueJP": "デフォルト" if c.costume_id == 0 else f"衣装{c.costume_id}",
                        })
                        added_n_costumes += 1
                entry["Costumes"] = new_c_list

    navi_list.sort(key=lambda x: x["Id"])
    navi_json_path.write_text(json.dumps(navi_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 3. Sync messages/Stamps.json
    stamps_json_path = webui_data_dir / "messages" / "Stamps.json"
    added_stamps = 0
    if stamps_json_path.exists() and sdb_stamps:
        stamp_labels = load_sdb1_stamp_labels(ib_dir)
        stamps_list = json.loads(stamps_json_path.read_text(encoding="utf-8-sig"))
        stamps_by_id = {_sub_id(x): x for x in stamps_list}
        for st_id in sorted(sdb_stamps.keys()):
            lbl = stamp_labels.get(st_id, "")
            if st_id not in stamps_by_id:
                stamps_list.append({
                    "ID": st_id,
                    "Value": lbl or f"通信スタンプ {st_id}",
                    "Existence": "IB",
                    "Clues": lbl,
                })
                added_stamps += 1
            elif not _is_existing_item(stamps_by_id[st_id]):
                stamps_by_id[st_id]["Existence"] = "IB"
                if lbl and not stamps_by_id[st_id].get("Clues"):
                    stamps_by_id[st_id]["Clues"] = lbl
                added_stamps += 1
        stamps_list.sort(key=lambda x: _sub_id(x))
        stamps_json_path.write_text(json.dumps(stamps_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"[Sync 完成] MobileSuits: +{added_ms} 机体, +{added_p_costumes} 机师服装, "
        f"+{added_skins} 换色皮肤, 更新 {updated_poses} 贴纸姿势 | "
        f"Navigators: +{added_navis} 领航员, +{added_n_costumes} 领航员服装 | "
        f"Stamps: +{added_stamps} 通信印章"
    )


# ============================================================================
# Full Consistency Auditor (`audit`)
# ============================================================================

def audit_data_and_images(webui_data_dir: Path, webui_images_dir: Path) -> bool:
    """Verifies 1-to-1 matching between all JSON files in webui_data_dir and images in webui_images_dir."""
    ms_json = json.loads((webui_data_dir / "MobileSuits.json").read_text(encoding="utf-8-sig"))
    navi_json = json.loads((webui_data_dir / "Navigators.json").read_text(encoding="utf-8-sig"))
    stamps_json = json.loads((webui_data_dir / "messages" / "Stamps.json").read_text(encoding="utf-8-sig"))
    s_bg_json = json.loads((webui_data_dir / "sticker" / "Backgrounds.json").read_text(encoding="utf-8-sig"))
    s_eff_json = json.loads((webui_data_dir / "sticker" / "Effects.json").read_text(encoding="utf-8-sig"))
    t_bg_json = json.loads((webui_data_dir / "team" / "Backgrounds.json").read_text(encoding="utf-8-sig"))
    t_em_json = json.loads((webui_data_dir / "team" / "Emblems.json").read_text(encoding="utf-8-sig"))
    titles_json = json.loads((webui_data_dir / "titles" / "Titles.json").read_text(encoding="utf-8-sig"))
    gauges_json = json.loads((webui_data_dir / "Gauges.json").read_text(encoding="utf-8-sig"))

    def stems(rel: str, ext: str = "*.webp") -> Set[str]:
        d = webui_images_dir / rel
        return {
            p.stem
            for p in d.glob(ext)
            if p.stem not in ("0", "0_0") and not p.stem.startswith("ori_")
        } if d.exists() else set()

    expected_pilots = {
        f"{x['Id']}_{cid}"
        for x in ms_json
        if 0 < x["Id"] < 50000
        for cid in _ms_costume_ids_from_json(x)
    }

    checks = [
        (
            "MobileSuits -> mobile_suits/",
            {str(x["Id"]) for x in ms_json if x["Id"] > 0},
            stems("mobile_suits"),
        ),
        (
            "MobileSuits.Costumes -> pilots/",
            expected_pilots,
            stems("pilots"),
        ),
        (
            "MobileSuits.Costumes -> pilots/opposite/",
            expected_pilots,
            stems("pilots/opposite"),
        ),
        (
            "MobileSuits.Costumes -> sticker/ex_p/",
            expected_pilots,
            stems("sticker/ex_p"),
        ),
        (
            "MobileSuits.Skins -> ms_skins/",
            {f"{x['Id']}_{_sub_id(s, 'SkinId')}" for x in ms_json if 0 < x["Id"] < 50000 for s in (x.get("Skins") or [])},
            stems("ms_skins"),
        ),
        (
            "MobileSuits.Poses -> sticker/ms/",
            {f"{x['Id']}_{_sub_id(p, 'PoseId')}" for x in ms_json if 0 < x["Id"] < 50000 for p in (x.get("Poses") or [])},
            stems("sticker/ms"),
        ),
        (
            "Navigators -> navis/{Id}.webp",
            {str(x["Id"]) for x in navi_json if x["Id"] > 0},
            {s for s in stems("navis") if "_" not in s},
        ),
        (
            "Navigators.Costumes -> navis/{Id}_{C}",
            {f"{x['Id']}_{_sub_id(c, 'CostumeId')}" for x in navi_json if x["Id"] > 0 for c in (x.get("Costumes") or [])},
            {s for s in (stems("navis", "*.webp") | stems("navis", "*.gif")) if "_" in s},
        ),
        (
            "Stamps.json -> stamps/",
            {str(_sub_id(x)) for x in stamps_json if _is_existing_item(x)},
            stems("stamps"),
        ),
        (
            "sticker/Backgrounds.json -> sticker/background/",
            {str(_sub_id(x)) for x in s_bg_json if _is_existing_item(x)},
            stems("sticker/background"),
        ),
        (
            "sticker/Effects.json -> sticker/effect/",
            {str(_sub_id(x)) for x in s_eff_json if _is_existing_item(x)},
            stems("sticker/effect"),
        ),
        (
            "team/Backgrounds.json -> team/background/",
            {str(_sub_id(x)) for x in t_bg_json if _is_existing_item(x)},
            stems("team/background", "*.webp") | stems("team/background", "*.gif"),
        ),
        (
            "team/Emblems.json -> team/emblems/",
            {str(_sub_id(x)) for x in t_em_json if _is_existing_item(x)},
            stems("team/emblems"),
        ),
        (
            "titles/Titles.json -> titles/",
            {str(_sub_id(x)) for x in titles_json if _is_existing_item(x)},
            stems("titles", "*.webp") | stems("titles", "*.gif"),
        ),
        (
            "Gauges.json -> gauges/",
            {str(_sub_id(x)) for x in gauges_json if x.get("Existence") not in (False, "Not Existed", "None", None)},
            {p.stem for p in (webui_images_dir / "gauges").glob("*.webp")},
        ),
    ]

    all_ok = True
    print("=" * 80)
    print(" WebUI-Infinite Data JSON vs Images 全量一致性校验报告")
    print("=" * 80)
    for label, expected, actual in checks:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        status = "OK" if (not missing and not extra) else "MISMATCH"
        if status != "OK":
            all_ok = False
        print(
            f"  [{status:8s}] {label:42s} | JSON={len(expected):4d} | "
            f"Img={len(actual):4d} | Missing={len(missing)} | Extra={len(extra)}"
        )
        if missing:
            print(f"             Missing: {missing[:15]}")
        if extra:
            print(f"             Extra  : {extra[:15]}")
    print("=" * 80)
    return all_ok


# ============================================================================
# JSON CRUD Operations (`ms`, `pilot`, `navi`)
# ============================================================================

def crud_ms_list(webui_data_dir: Path, series_filter: Optional[str] = None, cost_filter: Optional[int] = None) -> None:
    ms_list = json.loads((webui_data_dir / "MobileSuits.json").read_text(encoding="utf-8-sig"))
    print(f"{'Id':>5s} | {'Cost':>4s} | {'Costumes':>8s} | {'Skins':>5s} | {'Poses':>5s} | {'机体名称 (CN)':<24s} | {'驾驶员 (CN)':<18s} | {'所属作品 (CN)'}")
    print("-" * 110)
    for x in ms_list:
        if x["Id"] <= 0 or x["Id"] >= 50000:
            continue
        if series_filter and series_filter.lower() not in (
            (x.get("SeriesCN") or "") + (x.get("Series") or "") + (x.get("SeriesJP") or "")
        ).lower():
            continue
        if cost_filter and x.get("Cost") != cost_filter:
            continue
        c_cnt = len(_ms_costume_ids_from_json(x))
        s_cnt = len(x.get("Skins") or [])
        p_cnt = len(x.get("Poses") or [])
        print(
            f"{x['Id']:5d} | {x.get('Cost', 0):4d} | {c_cnt:8d} | {s_cnt:5d} | {p_cnt:5d} | "
            f"{x.get('ValueCN', ''):<24s} | {x.get('PilotCN', ''):<18s} | {x.get('SeriesCN', '')}"
        )


def crud_ms_show(webui_data_dir: Path, ms_id: int) -> None:
    ms_list = json.loads((webui_data_dir / "MobileSuits.json").read_text(encoding="utf-8-sig"))
    for x in ms_list:
        if x["Id"] == ms_id:
            print(json.dumps(x, ensure_ascii=False, indent=2))
            return
    print(f"[错误] 未找到 MsId={ms_id}", file=sys.stderr)


def crud_ms_upsert(
    webui_data_dir: Path,
    ms_id: int,
    name_jp: Optional[str] = None,
    name_en: Optional[str] = None,
    name_cn: Optional[str] = None,
    pilot_jp: Optional[str] = None,
    pilot_en: Optional[str] = None,
    pilot_cn: Optional[str] = None,
    series_en: Optional[str] = None,
    series_jp: Optional[str] = None,
    series_cn: Optional[str] = None,
    cost: Optional[int] = None,
) -> None:
    path = webui_data_dir / "MobileSuits.json"
    ms_list = json.loads(path.read_text(encoding="utf-8-sig"))
    by_id = {x["Id"]: x for x in ms_list}

    if ms_id in by_id:
        entry = by_id[ms_id]
        action = "更新"
    else:
        entry = {
            "Id": ms_id,
            "Value": name_en or f"Mobile Suit {ms_id}",
            "ValueJP": name_jp or f"機体 {ms_id}",
            "ValueCN": name_cn or f"机体 {ms_id}",
            "ValueTC": name_cn or f"機體 {ms_id}",
            "ValueTCTW": name_cn or f"機體 {ms_id}",
            "Pilot": pilot_en or "",
            "PilotJP": pilot_jp or "",
            "PilotCN": pilot_cn or "",
            "PilotTC": pilot_cn or "",
            "PilotTC2": pilot_cn or "",
            "PilotSeiyuu": "-",
            "PilotSeiyuuJP": "-",
            "PilotSeiyuuCN": "-",
            "PilotSeiyuuTC": "-",
            "PilotSeiyuuTC2": "-",
            "Series": series_en or "",
            "SeriesJP": series_jp or "",
            "SeriesCN": series_cn or "",
            "SeriesTC": series_cn or "",
            "SeriesTCTW": series_cn or "",
            "Cost": cost or 2000,
            "Costumes": [{"Id": 0, "Value": "デフォルト"}],
            "Poses": [{"Id": 0, "Value": "Default"}],
        }
        ms_list.append(entry)
        action = "新增"

    if name_en is not None:
        entry["Value"] = name_en
    if name_jp is not None:
        entry["ValueJP"] = name_jp
    if name_cn is not None:
        entry["ValueCN"] = name_cn
    if pilot_en is not None:
        entry["Pilot"] = pilot_en
    if pilot_jp is not None:
        entry["PilotJP"] = pilot_jp
    if pilot_cn is not None:
        entry["PilotCN"] = pilot_cn
    if series_en is not None:
        entry["Series"] = series_en
    if series_jp is not None:
        entry["SeriesJP"] = series_jp
    if series_cn is not None:
        entry["SeriesCN"] = series_cn
    if cost is not None:
        entry["Cost"] = cost

    ms_list.sort(key=lambda x: x["Id"])
    path.write_text(json.dumps(ms_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[成功] 已{action}机体 Id={ms_id} ({entry.get('ValueCN')} / {entry.get('PilotCN')})")


def crud_ms_delete(webui_data_dir: Path, ms_id: int) -> None:
    path = webui_data_dir / "MobileSuits.json"
    ms_list = json.loads(path.read_text(encoding="utf-8-sig"))
    before = len(ms_list)
    ms_list = [x for x in ms_list if x["Id"] != ms_id]
    if len(ms_list) == before:
        print(f"[警告] 未找到机体 Id={ms_id}")
        return
    path.write_text(json.dumps(ms_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[成功] 已删除机体 Id={ms_id}")


def crud_pilot_costume_upsert(
    webui_data_dir: Path,
    ms_id: int,
    costume_id: int,
    name_jp: Optional[str] = None,
    name_en: Optional[str] = None,
    name_cn: Optional[str] = None,
) -> None:
    path = webui_data_dir / "MobileSuits.json"
    ms_list = json.loads(path.read_text(encoding="utf-8-sig"))
    for entry in ms_list:
        if entry["Id"] == ms_id:
            costumes = entry.get("Costumes") or [{"Id": 0, "Value": "デフォルト"}]
            by_cid = {_sub_id(c, "CostumeId"): c for c in costumes}
            val_str = name_cn or name_jp or name_en or f"衣装{costume_id}"
            if costume_id in by_cid:
                c_obj = by_cid[costume_id]
                c_obj["Value"] = val_str
                action = "更新"
            else:
                c_obj = {"Id": costume_id, "Value": val_str}
                costumes.append(c_obj)
                costumes.sort(key=lambda c: _sub_id(c, "CostumeId"))
                entry["Costumes"] = costumes
                action = "新增"
            path.write_text(json.dumps(ms_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[成功] 已{action}机体 Id={ms_id} ({entry.get('ValueCN')}) 的机师服装 Id={costume_id} ({c_obj['Value']})")
            return
    print(f"[错误] 未找到机体 Id={ms_id}", file=sys.stderr)


def crud_pilot_costume_delete(webui_data_dir: Path, ms_id: int, costume_id: int) -> None:
    path = webui_data_dir / "MobileSuits.json"
    ms_list = json.loads(path.read_text(encoding="utf-8-sig"))
    for entry in ms_list:
        if entry["Id"] == ms_id:
            costumes = entry.get("Costumes") or []
            entry["Costumes"] = [c for c in costumes if _sub_id(c, "CostumeId") != costume_id]
            path.write_text(json.dumps(ms_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[成功] 已删除机体 Id={ms_id} 的机师服装 Id={costume_id}")
            return
    print(f"[错误] 未找到机体 Id={ms_id}", file=sys.stderr)


def crud_navi_list(webui_data_dir: Path) -> None:
    navi_list = json.loads((webui_data_dir / "Navigators.json").read_text(encoding="utf-8-sig"))
    print(f"{'Id':>4s} | {'Costumes':>14s} | {'领航员名称 (CN)':<20s} | {'声优 (CN)':<16s} | {'所属作品 (CN)'}")
    print("-" * 95)
    for x in navi_list:
        if x["Id"] <= 0:
            continue
        cids = ",".join(str(_sub_id(c, "CostumeId")) for c in (x.get("Costumes") or []))
        print(
            f"{x['Id']:4d} | {cids:>14s} | {x.get('ValueCN', ''):<20s} | "
            f"{x.get('SeiyuuCN', ''):<16s} | {x.get('SeriesCN', '')}"
        )


def crud_navi_show(webui_data_dir: Path, navi_id: int) -> None:
    navi_list = json.loads((webui_data_dir / "Navigators.json").read_text(encoding="utf-8-sig"))
    for x in navi_list:
        if x["Id"] == navi_id:
            print(json.dumps(x, ensure_ascii=False, indent=2))
            return
    print(f"[错误] 未找到 NaviId={navi_id}", file=sys.stderr)


def crud_navi_upsert(
    webui_data_dir: Path,
    navi_id: int,
    name_jp: Optional[str] = None,
    name_en: Optional[str] = None,
    name_cn: Optional[str] = None,
    series_en: Optional[str] = None,
    series_jp: Optional[str] = None,
    series_cn: Optional[str] = None,
    seiyuu_en: Optional[str] = None,
    seiyuu_jp: Optional[str] = None,
    seiyuu_cn: Optional[str] = None,
) -> None:
    path = webui_data_dir / "Navigators.json"
    navi_list = json.loads(path.read_text(encoding="utf-8-sig"))
    by_id = {x["Id"]: x for x in navi_list}

    if navi_id in by_id:
        entry = by_id[navi_id]
        action = "更新"
    else:
        entry = {
            "Id": navi_id,
            "Value": name_en or f"Navigator {navi_id}",
            "ValueJP": name_jp or f"ナビ {navi_id}",
            "ValueCN": name_cn or f"领航员 {navi_id}",
            "ValueTC": name_cn or f"領航員 {navi_id}",
            "ValueTCTW": name_cn or f"領航員 {navi_id}",
            "Series": series_en or "-",
            "SeriesJP": series_jp or "-",
            "SeriesCN": series_cn or "-",
            "SeriesTC": series_cn or "-",
            "SeriesTCTW": series_cn or "-",
            "Seiyuu": seiyuu_en or "-",
            "SeiyuuJP": seiyuu_jp or "-",
            "SeiyuuCN": seiyuu_cn or "-",
            "SeiyuuTC": seiyuu_cn or "-",
            "SeiyuuTC2": seiyuu_cn or "-",
            "Costumes": [{"Id": 0, "Value": "Default", "ValueJP": "デフォルト"}],
        }
        navi_list.append(entry)
        action = "新增"

    if name_en is not None:
        entry["Value"] = name_en
    if name_jp is not None:
        entry["ValueJP"] = name_jp
    if name_cn is not None:
        entry["ValueCN"] = name_cn
    if series_en is not None:
        entry["Series"] = series_en
    if series_jp is not None:
        entry["SeriesJP"] = series_jp
    if series_cn is not None:
        entry["SeriesCN"] = series_cn
    if seiyuu_en is not None:
        entry["Seiyuu"] = seiyuu_en
    if seiyuu_jp is not None:
        entry["SeiyuuJP"] = seiyuu_jp
    if seiyuu_cn is not None:
        entry["SeiyuuCN"] = seiyuu_cn

    navi_list.sort(key=lambda x: x["Id"])
    path.write_text(json.dumps(navi_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[成功] 已{action}领航员 Id={navi_id} ({entry.get('ValueCN')})")


def crud_navi_delete(webui_data_dir: Path, navi_id: int) -> None:
    path = webui_data_dir / "Navigators.json"
    navi_list = json.loads(path.read_text(encoding="utf-8-sig"))
    navi_list = [x for x in navi_list if x["Id"] != navi_id]
    path.write_text(json.dumps(navi_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[成功] 已删除领航员 Id={navi_id}")


def crud_navi_costume_upsert(
    webui_data_dir: Path,
    navi_id: int,
    costume_id: int,
    name_jp: Optional[str] = None,
    name_en: Optional[str] = None,
    name_cn: Optional[str] = None,
) -> None:
    path = webui_data_dir / "Navigators.json"
    navi_list = json.loads(path.read_text(encoding="utf-8-sig"))
    for entry in navi_list:
        if entry["Id"] == navi_id:
            costumes = entry.get("Costumes") or []
            by_cid = {_sub_id(c, "CostumeId"): c for c in costumes}
            if costume_id in by_cid:
                c_obj = by_cid[costume_id]
                if name_en or name_cn:
                    c_obj["Value"] = name_en or name_cn
                if name_jp:
                    c_obj["ValueJP"] = name_jp
                action = "更新"
            else:
                c_obj = {
                    "Id": costume_id,
                    "Value": name_en or name_cn or f"Costume {costume_id}",
                    "ValueJP": name_jp or name_cn or f"衣装{costume_id}",
                }
                costumes.append(c_obj)
                costumes.sort(key=lambda c: _sub_id(c, "CostumeId"))
                entry["Costumes"] = costumes
                action = "新增"
            path.write_text(json.dumps(navi_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[成功] 已{action}领航员 Id={navi_id} ({entry.get('ValueCN')}) 的服装 Id={costume_id} ({c_obj.get('ValueJP')})")
            return
    print(f"[错误] 未找到领航员 Id={navi_id}", file=sys.stderr)


def crud_navi_costume_delete(webui_data_dir: Path, navi_id: int, costume_id: int) -> None:
    path = webui_data_dir / "Navigators.json"
    navi_list = json.loads(path.read_text(encoding="utf-8-sig"))
    for entry in navi_list:
        if entry["Id"] == navi_id:
            costumes = entry.get("Costumes") or []
            entry["Costumes"] = [c for c in costumes if _sub_id(c, "CostumeId") != costume_id]
            path.write_text(json.dumps(navi_list, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[成功] 已删除领航员 Id={navi_id} 的服装 Id={costume_id}")
            return
    print(f"[错误] 未找到领航员 Id={navi_id}", file=sys.stderr)


# ============================================================================
# Interactive Menu Mode
# ============================================================================

def run_interactive_menu(ib_dir: Path, webui_data_dir: Path, webui_images_dir: Path) -> None:
    while True:
        print("\n" + "=" * 68)
        print("  EXVS2 Infinite Boost - 游戏数据库检测与 JSON 增删改管理工具")
        print("=" * 68)
        print("  1. 检测游戏更新 (对比客户端 SDB1 数据库 vs WebUI JSON)")
        print("  2. 一键同步新增机体/机师服装/领航员/印章到 JSON")
        print("  3. 全量校验 JSON 与图片序号一致性 (Audit)")
        print("  4. 查看/搜索机体列表 (MobileSuits.json)")
        print("  5. 新增或修改机体信息 (Add/Update Mobile Suit)")
        print("  6. 新增或修改机师服装 (Add/Update Pilot Costume)")
        print("  7. 查看领航员列表 (Navigators.json)")
        print("  8. 新增或修改领航员及服装 (Add/Update Navigator / Costume)")
        print("  0. 退出 (Exit)")
        print("-" * 68)
        choice = input("请选择操作编号 [0-8]: ").strip()

        if choice == "0":
            break
        elif choice == "1":
            scan_updates(ib_dir, webui_data_dir)
        elif choice == "2":
            sync_sdb1_to_json(ib_dir, webui_data_dir)
        elif choice == "3":
            audit_data_and_images(webui_data_dir, webui_images_dir)
        elif choice == "4":
            kw = input("输入作品名过滤 (直接回车显示全部): ").strip() or None
            crud_ms_list(webui_data_dir, series_filter=kw)
        elif choice == "5":
            mid = int(input("输入机体 Id (如 335): ").strip())
            crud_ms_show(webui_data_dir, mid)
            name_cn = input("新中文机体名 (直接回车保持不变): ").strip() or None
            name_jp = input("新日文机体名 (直接回车保持不变): ").strip() or None
            name_en = input("新英文机体名 (直接回车保持不变): ").strip() or None
            pilot_cn = input("新中文机师名 (直接回车保持不变): ").strip() or None
            cost_s = input("新 Cost 值 (1500/2000/2500/3000, 直接回车保持不变): ").strip()
            cost_v = int(cost_s) if cost_s else None
            crud_ms_upsert(
                webui_data_dir,
                ms_id=mid,
                name_jp=name_jp,
                name_en=name_en,
                name_cn=name_cn,
                pilot_cn=pilot_cn,
                cost=cost_v,
            )
        elif choice == "6":
            mid = int(input("输入机体 Id: ").strip())
            cid = int(input("输入机师服装 CostumeId (0..3): ").strip())
            name_cn = input("服装名称: ").strip() or None
            crud_pilot_costume_upsert(webui_data_dir, mid, cid, name_cn=name_cn)
        elif choice == "7":
            crud_navi_list(webui_data_dir)
        elif choice == "8":
            nid = int(input("输入领航员 Id: ").strip())
            crud_navi_show(webui_data_dir, nid)
            sub = input("修改领航员本体信息输入 1, 新增/修改领航员服装输入 2: ").strip()
            if sub == "1":
                name_cn = input("新中文名 (回车保持不变): ").strip() or None
                name_jp = input("新日文名 (回车保持不变): ").strip() or None
                name_en = input("新英文名 (回车保持不变): ").strip() or None
                series_cn = input("新作品中文名 (回车保持不变): ").strip() or None
                crud_navi_upsert(webui_data_dir, nid, name_jp=name_jp, name_en=name_en, name_cn=name_cn, series_cn=series_cn)
            elif sub == "2":
                cid = int(input("输入领航员服装 CostumeId: ").strip())
                name_en = input("英文服装名: ").strip() or None
                name_jp = input("日文/中文服装名: ").strip() or None
                crud_navi_costume_upsert(webui_data_dir, nid, cid, name_jp=name_jp, name_en=name_en)


# ============================================================================
# CLI Entrypoint
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="EXVS2 Infinite Boost - SDB1 Database Update Detector & WebUI JSON CRUD Manager"
    )
    p.add_argument("--ib-dir", type=Path, default=DEFAULT_IB_DIR, help="Path to dplcache_release directory")
    p.add_argument("--webui-data", type=Path, default=DEFAULT_WEBUI_DATA, help="Path to WebUI wwwroot/data directory")
    p.add_argument("--webui-images", type=Path, default=DEFAULT_WEBUI_IMAGES, help="Path to WebUI wwwroot/images directory")

    sub = p.add_subparsers(dest="cmd")

    # scan / diff
    p_scan = sub.add_parser("scan", aliases=["diff"], help="Scan SDB1 binary tables for newly added/changed units, pilots, and navigators")
    p_scan.add_argument("--report", type=Path, default=None, help="Optional path to save JSON diff report")

    # sync
    sub.add_parser("sync", help="Automatically synchronize new SDB1 entries into WebUI JSON files")

    # audit
    sub.add_parser("audit", help="Verify 1-to-1 consistency between WebUI JSON files and images/")

    # interactive
    sub.add_parser("interactive", help="Launch interactive console menu")

    # ms CRUD
    p_ms = sub.add_parser("ms", help="MobileSuits.json CRUD operations")
    ms_sub = p_ms.add_subparsers(dest="ms_cmd", required=True)

    ms_list_p = ms_sub.add_parser("list", help="List playable Mobile Suits")
    ms_list_p.add_argument("--series", type=str, default=None)
    ms_list_p.add_argument("--cost", type=int, default=None)

    ms_show_p = ms_sub.add_parser("show", help="Show JSON details for a Mobile Suit")
    ms_show_p.add_argument("id", type=int)

    ms_set_p = ms_sub.add_parser("set", aliases=["add", "update"], help="Add or update a Mobile Suit in MobileSuits.json")
    ms_set_p.add_argument("id", type=int)
    ms_set_p.add_argument("--name-jp", type=str, default=None)
    ms_set_p.add_argument("--name-en", type=str, default=None)
    ms_set_p.add_argument("--name-cn", type=str, default=None)
    ms_set_p.add_argument("--pilot-jp", type=str, default=None)
    ms_set_p.add_argument("--pilot-en", type=str, default=None)
    ms_set_p.add_argument("--pilot-cn", type=str, default=None)
    ms_set_p.add_argument("--series-en", type=str, default=None)
    ms_set_p.add_argument("--series-jp", type=str, default=None)
    ms_set_p.add_argument("--series-cn", type=str, default=None)
    ms_set_p.add_argument("--cost", type=int, default=None)

    ms_del_p = ms_sub.add_parser("delete", help="Delete a Mobile Suit by Id")
    ms_del_p.add_argument("id", type=int)

    # pilot costume CRUD
    p_pilot = sub.add_parser("pilot", help="Pilot Costume CRUD operations in MobileSuits.json")
    pi_sub = p_pilot.add_subparsers(dest="pilot_cmd", required=True)

    pi_set_p = pi_sub.add_parser("set-costume", aliases=["add-costume", "update-costume"], help="Add or update a Pilot Costume")
    pi_set_p.add_argument("ms_id", type=int)
    pi_set_p.add_argument("costume_id", type=int)
    pi_set_p.add_argument("--name-jp", type=str, default=None)
    pi_set_p.add_argument("--name-en", type=str, default=None)
    pi_set_p.add_argument("--name-cn", type=str, default=None)

    pi_del_p = pi_sub.add_parser("delete-costume", help="Delete a Pilot Costume")
    pi_del_p.add_argument("ms_id", type=int)
    pi_del_p.add_argument("costume_id", type=int)

    # navi CRUD
    p_navi = sub.add_parser("navi", help="Navigators.json CRUD operations")
    nv_sub = p_navi.add_subparsers(dest="navi_cmd", required=True)

    nv_sub.add_parser("list", help="List all Navigators")

    nv_show_p = nv_sub.add_parser("show", help="Show JSON details for a Navigator")
    nv_show_p.add_argument("id", type=int)

    nv_set_p = nv_sub.add_parser("set", aliases=["add", "update"], help="Add or update a Navigator")
    nv_set_p.add_argument("id", type=int)
    nv_set_p.add_argument("--name-jp", type=str, default=None)
    nv_set_p.add_argument("--name-en", type=str, default=None)
    nv_set_p.add_argument("--name-cn", type=str, default=None)
    nv_set_p.add_argument("--series-en", type=str, default=None)
    nv_set_p.add_argument("--series-jp", type=str, default=None)
    nv_set_p.add_argument("--series-cn", type=str, default=None)
    nv_set_p.add_argument("--seiyuu-en", type=str, default=None)
    nv_set_p.add_argument("--seiyuu-jp", type=str, default=None)
    nv_set_p.add_argument("--seiyuu-cn", type=str, default=None)

    nv_del_p = nv_sub.add_parser("delete", help="Delete a Navigator by Id")
    nv_del_p.add_argument("id", type=int)

    nv_cset_p = nv_sub.add_parser("set-costume", aliases=["add-costume", "update-costume"], help="Add or update a Navigator Costume")
    nv_cset_p.add_argument("navi_id", type=int)
    nv_cset_p.add_argument("costume_id", type=int)
    nv_cset_p.add_argument("--name-jp", type=str, default=None)
    nv_cset_p.add_argument("--name-en", type=str, default=None)
    nv_cset_p.add_argument("--name-cn", type=str, default=None)

    nv_cdel_p = nv_sub.add_parser("delete-costume", help="Delete a Navigator Costume")
    nv_cdel_p.add_argument("navi_id", type=int)
    nv_cdel_p.add_argument("costume_id", type=int)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.cmd or args.cmd == "interactive":
        run_interactive_menu(args.ib_dir, args.webui_data, args.webui_images)
        return

    if args.cmd in ("scan", "diff"):
        scan_updates(args.ib_dir, args.webui_data, args.report)
    elif args.cmd == "sync":
        sync_sdb1_to_json(args.ib_dir, args.webui_data)
    elif args.cmd == "audit":
        ok = audit_data_and_images(args.webui_data, args.webui_images)
        sys.exit(0 if ok else 1)
    elif args.cmd == "ms":
        if args.ms_cmd == "list":
            crud_ms_list(args.webui_data, args.series, args.cost)
        elif args.ms_cmd == "show":
            crud_ms_show(args.webui_data, args.id)
        elif args.ms_cmd in ("set", "add", "update"):
            crud_ms_upsert(
                args.webui_data,
                args.id,
                name_jp=args.name_jp,
                name_en=args.name_en,
                name_cn=args.name_cn,
                pilot_jp=args.pilot_jp,
                pilot_en=args.pilot_en,
                pilot_cn=args.pilot_cn,
                series_en=args.series_en,
                series_jp=args.series_jp,
                series_cn=args.series_cn,
                cost=args.cost,
            )
        elif args.ms_cmd == "delete":
            crud_ms_delete(args.webui_data, args.id)
    elif args.cmd == "pilot":
        if args.pilot_cmd in ("set-costume", "add-costume", "update-costume"):
            crud_pilot_costume_upsert(
                args.webui_data,
                args.ms_id,
                args.costume_id,
                name_jp=args.name_jp,
                name_en=args.name_en,
                name_cn=args.name_cn,
            )
        elif args.pilot_cmd == "delete-costume":
            crud_pilot_costume_delete(args.webui_data, args.ms_id, args.costume_id)
    elif args.cmd == "navi":
        if args.navi_cmd == "list":
            crud_navi_list(args.webui_data)
        elif args.navi_cmd == "show":
            crud_navi_show(args.webui_data, args.id)
        elif args.navi_cmd in ("set", "add", "update"):
            crud_navi_upsert(
                args.webui_data,
                args.id,
                name_jp=args.name_jp,
                name_en=args.name_en,
                name_cn=args.name_cn,
                series_en=args.series_en,
                series_jp=args.series_jp,
                series_cn=args.series_cn,
                seiyuu_en=args.seiyuu_en,
                seiyuu_jp=args.seiyuu_jp,
                seiyuu_cn=args.seiyuu_cn,
            )
        elif args.navi_cmd == "delete":
            crud_navi_delete(args.webui_data, args.id)
        elif args.navi_cmd in ("set-costume", "add-costume", "update-costume"):
            crud_navi_costume_upsert(
                args.webui_data,
                args.navi_id,
                args.costume_id,
                name_jp=args.name_jp,
                name_en=args.name_en,
                name_cn=args.name_cn,
            )
        elif args.navi_cmd == "delete-costume":
            crud_navi_costume_delete(args.webui_data, args.navi_id, args.costume_id)


if __name__ == "__main__":
    main()
