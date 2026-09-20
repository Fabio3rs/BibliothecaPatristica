from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import infer
import main2
import review
from fix_time_breaks import page_file_exists
from scripts.backfill_versions import find_image_for_txt, parse_page_num


def test_alternate_ocr_resolvers_prefer_canonical_over_uuid_padding(
    tmp_path: Path,
) -> None:
    text_dir = tmp_path / "PG013" / "text"
    image_dir = tmp_path / "PG013" / "images"
    text_dir.mkdir(parents=True)
    image_dir.mkdir()
    legacy = text_dir / "bd07f101-534d-403a-b3ff-5b6ded5abf67-0001.txt"
    canonical = text_dir / "PG013-001.txt"
    legacy.write_text("legado", encoding="utf-8")
    canonical.write_text("canônico", encoding="utf-8")
    image = image_dir / "PG013-001.png"
    image.write_bytes(b"png")

    assert review.txt_path_for_image(image, text_dir) == canonical
    assert infer.txt_path_for_image(image, text_dir) == canonical
    assert find_image_for_txt(legacy, image_dir) == image
    assert page_file_exists("PG013", 1, root=tmp_path) is True


def test_resolvers_accept_more_than_four_page_digits(tmp_path: Path) -> None:
    text_dir = tmp_path / "PG999" / "text"
    image_dir = tmp_path / "PG999" / "images"
    text_dir.mkdir(parents=True)
    image_dir.mkdir()
    text = text_dir / "PG999-12345.txt"
    image = image_dir / "PG999-12345.png"
    text.write_text("página", encoding="utf-8")
    image.write_bytes(b"png")

    assert review.parse_page_num_from_filename(image) == 12345
    assert infer.parse_page_num_from_filename(image) == 12345
    assert main2.parse_page_num_from_filename(image) == 12345
    assert parse_page_num(text) == 12345
    assert review.txt_path_for_image(image, text_dir) == text
