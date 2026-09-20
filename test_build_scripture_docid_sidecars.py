import gzip
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.build_scripture_docid_sidecars import (
    DOCUMENT_LOCATIONS_MAGIC,
    SIDECAR_MAGIC,
    append_varuint,
    encode_scripture_docid_sidecar,
    load_document_locations,
    validate_sidecar_publication,
)


def document_locations_fixture() -> tuple[bytes, str]:
    payload = bytearray(DOCUMENT_LOCATIONS_MAGIC)
    append_varuint(payload, 2)
    for volume_id in (b"PG001", b"PL002"):
        append_varuint(payload, len(volume_id))
        payload.extend(volume_id)
    append_varuint(payload, 3)
    for volume_index, page in ((0, 10), (0, 11), (1, 4)):
        append_varuint(payload, volume_index)
        append_varuint(payload, page)
    canonical = b"0\0PG001:10\n1\0PG001:11\n2\0PL002:4\n"
    return bytes(payload), f"sha256:{hashlib.sha256(canonical).hexdigest()}"


class DocumentLocationsTest(unittest.TestCase):
    def test_decodes_native_artifact_and_checks_metadata(self):
        raw, build_id = document_locations_fixture()
        compressed = gzip.compress(raw, mtime=0)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document_locations.bin.gz"
            path.write_bytes(compressed)
            metadata = {
                "build_id": build_id,
                "documents": 3,
                "volumes": 2,
                "raw_bytes": len(raw),
                "gzip_bytes": len(compressed),
            }
            locations, actual_build_id, raw_bytes, gzip_bytes = load_document_locations(
                path, metadata
            )
        self.assertEqual(actual_build_id, build_id)
        self.assertEqual(raw_bytes, len(raw))
        self.assertEqual(gzip_bytes, len(compressed))
        self.assertEqual(locations[("PG001", 10)], 0)
        self.assertEqual(locations[("PG001", 11)], 1)
        self.assertEqual(locations[("PL002", 4)], 2)

    def test_encodes_browser_compatible_sidecar(self):
        encoded = encode_scripture_docid_sidecar(
            ["PG001", "PL002"],
            [
                ("PG001", 10, 101),
                ("PG001", 11, 99),
                ("PL002", 4, 1200),
            ],
        )
        self.assertEqual(encoded.hex(), "42534449310200020aca010103010104e012")


class PublicationValidationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary.name)
        (self.output_dir / "all").mkdir()
        raw = SIDECAR_MAGIC + b"\0"
        compressed = gzip.compress(raw, mtime=0)
        (self.output_dir / "all/joao.bin.gz").write_bytes(compressed)
        self.book = {
            "url": "all/joao.bin.gz",
            "raw_bytes": len(raw),
            "gzip_bytes": len(compressed),
            "pages_total": 100,
            "pages_mapped": 99,
            "pages_missing": 1,
        }
        self.search_manifest = {
            "indexes": [{"id": "ALL", "build_id": "sha256:test", "documents": 10}]
        }
        self.scripture_manifest = {"routes": {"joao": {"url": "joao.json.gz"}}}

    def tearDown(self):
        self.temporary.cleanup()

    def sidecar_manifest(self, build_id="sha256:test"):
        return {
            "schema": "bibliotheca-scripture-docids-v1",
            "indexes": {
                "ALL": {
                    "build_id": build_id,
                    "documents": 10,
                    "books": {"joao": self.book},
                    "coverage": {
                        "pages_total": self.book["pages_total"],
                        "pages_mapped": self.book["pages_mapped"],
                        "pages_missing": self.book["pages_missing"],
                        "ratio": self.book["pages_mapped"] / self.book["pages_total"],
                    },
                }
            },
        }

    def test_validates_build_files_and_coverage(self):
        result = validate_sidecar_publication(
            self.search_manifest,
            self.scripture_manifest,
            self.sidecar_manifest(),
            self.output_dir,
        )
        self.assertEqual(result["indexes"], 1)
        self.assertEqual(result["books"], 1)
        self.assertEqual(result["pages_mapped"], 99)
        self.assertEqual(result["ratio"], 0.99)

    def test_rejects_stale_build_or_degraded_coverage(self):
        with self.assertRaisesRegex(ValueError, "build_id"):
            validate_sidecar_publication(
                self.search_manifest,
                self.scripture_manifest,
                self.sidecar_manifest("sha256:stale"),
                self.output_dir,
            )
        self.book.update(pages_mapped=80, pages_missing=20)
        with self.assertRaisesRegex(ValueError, "cobertura 80.000% abaixo"):
            validate_sidecar_publication(
                self.search_manifest,
                self.scripture_manifest,
                self.sidecar_manifest(),
                self.output_dir,
                min_book_coverage=0.95,
            )


if __name__ == "__main__":
    unittest.main()
