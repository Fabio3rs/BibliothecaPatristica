#!/usr/bin/env python3
"""
Quick reader for the PorIBP SWORD lexicon module (zLD format).
Pysword does not support zLD, so we parse the module files directly.
"""

import argparse
import pathlib
import re
import struct
import zlib
from typing import Dict, List, Tuple


def load_blocks(module_dir: pathlib.Path) -> List[bytes]:
    zdx_path = module_dir / "dict.zdx"
    zdt_path = module_dir / "dict.zdt"
    zdx = zdx_path.read_bytes()
    ints = struct.unpack("<" + "I" * (len(zdx) // 4), zdx)
    starts_and_sizes = list(zip(ints[0::2], ints[1::2]))
    zdt = zdt_path.read_bytes()

    blocks: List[bytes] = []
    for start, size in starts_and_sizes:
        compressed = zdt[start : start + size]
        blocks.append(zlib.decompress(compressed))
    return blocks


def extract_entries(block: bytes) -> List[str]:
    first_tag = block.find(b"<entry")
    if first_tag == -1:
        return []
    payload = block[first_tag:]
    matches = re.findall(b"<entryFree[^>]*>.*?</entryFree>", payload, re.DOTALL)
    return [m.decode("utf-8") for m in matches]


def load_key_index(module_dir: pathlib.Path) -> List[Tuple[str, int, int]]:
    dat = (module_dir / "dict.dat").read_bytes()
    records: List[Tuple[str, int, int]] = []
    idx = 0
    while idx < len(dat):
        try:
            end = dat.index(b"\r\n", idx)
        except ValueError:
            break
        key = dat[idx:end].decode("utf-8")
        idx = end + 2
        block_id, entry_id = struct.unpack("<II", dat[idx : idx + 8])
        idx += 8
        if dat[idx : idx + 2] == b"\r\n":
            idx += 2
        records.append((key, block_id, entry_id))
    return records


def build_lookup(records: List[Tuple[str, int, int]]) -> Dict[str, Tuple[int, int]]:
    lookup: Dict[str, Tuple[int, int]] = {}
    for key, block_id, entry_id in records:
        lookup[key] = (block_id, entry_id)
        norm = key.casefold()
        if norm not in lookup:
            lookup[norm] = (block_id, entry_id)
    return lookup


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read entries from the PorIBP zLD lexicon module.")
    parser.add_argument("key", nargs="?", help="Key to fetch (e.g., 'AARÃO').")
    parser.add_argument("--module-dir", default=None, help="Path to PorIBP module directory containing dict.dat/.idx/.zdt/.zdx.")
    parser.add_argument("--plain", action="store_true", help="Strip XML/OSIS tags from output.")
    parser.add_argument("--list", action="store_true", help="List available keys and exit.")
    parser.add_argument("--contains", help="When listing, only show keys containing this substring (case-insensitive).")

    args = parser.parse_args()

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    default_dir = repo_root / "PorIBP/modules/lexdict/zld/poribp"
    module_dir = pathlib.Path(args.module_dir) if args.module_dir else default_dir

    blocks = load_blocks(module_dir)
    entries_by_block = [extract_entries(b) for b in blocks]
    records = load_key_index(module_dir)
    lookup = build_lookup(records)

    if args.list:
        substring = args.contains.casefold() if args.contains else None
        keys = [k for k, _, _ in records]
        if substring:
            keys = [k for k in keys if substring in k.casefold()]
        for k in keys:
            print(k)
        return

    if not args.key:
        parser.error("provide a key or use --list")

    key = args.key
    entry_loc = lookup.get(key) or lookup.get(key.casefold())
    if not entry_loc:
        close = [k for k, _, _ in records if key.casefold() in k.casefold()]
        if close:
            print("Key not found. Did you mean:")
            for cand in close[:10]:
                print(f"  {cand}")
        else:
            print("Key not found.")
        return

    block_id, entry_id = entry_loc
    try:
        entry_xml = entries_by_block[block_id][entry_id]
    except IndexError:
        raise SystemExit(f"Invalid mapping for key {key}: block {block_id}, entry {entry_id}.")

    print(strip_tags(entry_xml) if args.plain else entry_xml)


if __name__ == "__main__":
    main()
