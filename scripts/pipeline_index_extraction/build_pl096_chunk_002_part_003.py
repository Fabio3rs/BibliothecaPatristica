#!/usr/bin/env python3
"""Build the bounded PL096 closing-ORDO fragment for chunk 002:003."""

from __future__ import annotations

import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment


WORKPLAN = ROOT / "data/index_intermediate_payloads/PL096/workplan.json"
OUTPUT = ROOT / "data/index_intermediate_payloads/PL096/chunks/section_002_part_003.json"
CHUNK_ID = "PL096:chunk:002:003"
SECTION_ID = "PL096:candidate-section:002"
SECTION_KEY = "PL096:section:002:volume-end-ordo"
FINGERPRINT = "da7351e1a8b7bde2fb6946788177127f5b49b5e54321a71323a584678b221ab0"
PREFIX = ROOT / "teste/PL096/text/2aa2c784-255e-4d16-957f-a50d57583e3b-"
FILE_797 = str(PREFIX) + "797.txt"
FILE_798 = str(PREFIX) + "798.txt"
FILE_799 = str(PREFIX) + "799.txt"
FILE_800 = str(PREFIX) + "800.txt"
FILE_801 = str(PREFIX) + "801.txt"


# OCR literals are retained; only proven typographic word-wraps are joined.
ITEMS = [
    ("CAP. XXXVIII. — De consilio pessimo Brunchildis, et quia Theudericus fratrem suum occidit. 1436", "chapter_entry"),
    ("CAP. XXXIX. — Quod Theudericus neptem suam ad conjugium sociare voluit, et quia Brunchildis ipsum Theudericum occidit. 1456", "chapter_entry"),
    ("CAP. XL. — Quod Austrasii et reliqui Franci Chlodacharium in monarchiam elevavunt et Brunchildem morte condemnant. 1457", "chapter_entry"),
    ("CAP. XLI. — Ubi Saxones adversus Dagobertum pugnam inenunt, ducemque eorum Chlodacharins interficit, et non longiorem hominem ex eis reliquit quam spata sua erat. 1457", "chapter_entry"),
    ("CAP. XLII. — De morte Chlodacharii, et regnum Dagoberti. 1458", "chapter_entry"),
    ("CAP. XLIII. — De morte Dagoberti et Sigiberti, et regnum Chlodovei. 1459", "chapter_entry"),
    ("CAP. XLIV. — Quo tempore regnum Francorum concidit, et de morte Chlodovei, et regnum Chlodacharii. 1459", "chapter_entry"),
    ("CAP. XLV. — Ubi Ebroinus major domus eligitur, Chlodchariisque rex moritur, regnumque Theudericus et Childericus assumunt. 1460", "chapter_entry"),
    ("CAP. XLVI. — Uodu Martinus et Pippinus duces Austrasiorum bellum agunt contra Ebroinum et Theudericum 1461", "chapter_entry"),
    ("CAP. XLVII. — Ubi Ebroinus occiditur, Waralto in majore domo domas assumitur, et sanctus Audonius ad Dominum migravit. 1461", "chapter_entry"),
    ("CAP. XLVIII. — Quod Waralto moritur, et Bertharius in loco ejus constituitur, Pippinusque eos devictos majori domatum in sua redegit potestate. 1462", "chapter_entry"),
    ("CAP. XLIX. — De obitu Theuderici et regnum Chlodovei et Childeberti, et Grimoaldo majore domus. 1462", "chapter_entry"),
    ("CAP. L. — De transitū Childeberti, et regnum Dagoberti, et quia Grimoaldus interfecit, et honorem patris sui Theudoladus ambitur. 1462", "chapter_entry"),
    ("CAP. LI. — De morte Pippini, et bella Francorum inter se, et Theudolado fugato Ragamfredus in principatum est elevatus. 1463", "chapter_entry"),
    ("CAP. LII. — De morte Dagoberti, et regnum Chilperici, et quod bellum gessit Carlus contra Rachbod. 1463", "chapter_entry"),
    ("CAP. LIII. — Quod Carlus pugnam gessit contra Chilperico et Ragamfredo, in loco nuncupante Viniciaco; eosque devictos atque fugatos, omne regnum Francorum in sua redegit potestate. 1463", "chapter_entry"),
    ("APPENDIX A Theodorico II usque ad Pippinum regem. 1464", "appendix_entry"),
    ("IX. — CHRONICON EPISCOPORUM METENSIUM. 1465", "work_entry"),
    ("APPENDIX PRIMA, ab anno 1190 ad 1200 circiter. 1471", "appendix_entry"),
    ("APPENDIX SECUNDA, ad annum 1260 deducta. 1473", "appendix_entry"),
    ("OCTAVI SÆCULI MONUMENTA ECCLESIASTICA.", "contents_heading"),
    ("EXPOSITIO MISSÆ ROMANÆ. 1481", "work_entry"),
    ("PIPPINI ET CAROLOMANNI FRANCORUM REGUM DIPLOMATA NEC NON QUORUMDAM INTER ILLUSTIORES FRANCOS ET GERMANOS SÆCULI VIII MONUMENTA SELECTA.", "contents_heading"),
    ("I. — Carolomanni principis Capitulare an. 742. 1501", "document_entry"),
    ("II. — Carolomanni principis Capitulare Liliticense (an. 743). 1503", "document_entry"),
    ("III. — Pippini principis Capitulare Suessionense (an. 744). 1504", "document_entry"),
    ("IV. — Pippini regis Capitulare Vermeriense (an. 753.) 1506", "document_entry"),
    ("V. — Pippini regis Capitulare Verense duplex (an. 755.) 1508", "document_entry"),
    ("VI. — Pippini regis Capitulare Compendiensc (an. 757.) 1510", "document_entry"),
    ("VII. — Conventus Autinicensis (an. 765.) 1513", "document_entry"),
    ("VIII. — Pippini regis Capitulare inscret anni. 1515", "document_entry"),
    ("IX. — Pippini regis Capitulare Aquitanicum an. 768. 1519", "document_entry"),
    ("X. — Pippini regis placitum, quo Abbiacum villa pagi Cenonensis sit sacrario pro in Matracensi Dionysiani nonachis vindicatur (an. 752). 1520", "document_entry"),
    ("XI. — Preceptum Pippini regis impetratum a Sigobaldo abbate Anisolenis cenobii (anno 752). 1521", "document_entry"),
    ("XII. — Regale preceptum Pippini sub Bonifacio de rebus ecclesiæ sancti Martini (anno 753). 1522", "document_entry"),
    ("XIII. — Praeceptum Pippini regis pro Morbacensi monasterio. 1523", "document_entry"),
    ("XIV. — Diploma Pippini regis pro mundinis sancti Dionysii (an. 753). 1524", "document_entry"),
    ("XV. — Pippini regis praeceptum pro Tabernaculi in pago Parisiiaco villæ confirmatione (an. 754). 1525", "document_entry"),
    ("XVI. — Praeceptum Pippini regis donantis monasterio sancti Dionysii castellum ad montem sancti Michaelis in pago Virdunensi (an. 755). 1528", "document_entry"),
    ("XVII. — Diploma Pippini regis pro monasterio Nantuacensi (an. 757). 1529", "document_entry"),
    ("XVIII. — Pippini placitum de teloneis omnibus in mercato sancti Dionysii ad ipsum monasterium evidicatis, adversus Gerardum comitem Parisiensum (an. 758). 1531", "document_entry"),
    ("XIX. — Diploma Pippini regis Francorum, quo monasterio Honaungiensis possessiones confirmat (an. 759). 1531", "document_entry"),
    ("XX. — Pippini regis diploma quo bona et jura Morbacensis abbatiae confirmat (an. 760). 1532", "document_entry"),
    ("XXI. — Diploma Pippini regis Francorum pro monasterio Honaungensi, datum versus 760. 1533", "document_entry"),
    ("XXII. — Pippinus rex Wiomado archiepiscopo et ecclesiæ Trevirensi confirmat ecclesias SS. Maximini, Paullini, Eucharii, Oreun, S. Martini, ac quidquid ecclesia Trevirensis habet et habebit citra Rhenum et Ligerim, et quod sit libera a tallis et teloneis (an. 761). 1535", "document_entry"),
    ("XXIII. — Diploma Pippini regis, quo villam Tinninge Fuldensi ecclesiae donat (an 762). 1536", "document_entry"),
    ("XXIV. — Testamentum sancti Salvatoris, quod Pippinus rex fieri jussit abbatiae Prumiensi (an. 762). 1537", "document_entry"),
    ("XXV. — Pippini regis diploma pro constructione et dotatione monasterii Prumiensis (an. 762). 1541", "document_entry"),
    ("XXVI. — Diploma quo Pippinus rex confirmat monasterio Honaungiensis omnes suas possessiones (incerto anno). 1545", "document_entry"),
    ("XXVII. — Immunitas abbatiae Prumiensi per regem Pippinum concessa. 1545", "document_entry"),
    ("XXVIII. — Testamentum Heddonis episcopi Argentinensis, seu charta fundationis et donationis monasteriorum Elenbeimensis (an. 765). 1547", "document_entry"),
    ("XXIX. — Egidius vir illuster factus monachus Prumiensi monasterio insignem donationem facit (an. 764). 1532", "document_entry"),
    ("XXX. — Pippini regis privilegium pro monasterio sancti Maximini (an. 765). 1534", "document_entry"),
    ("XXXI. — Tellonis episcopi Curiensis testamentum (an. 766). 1535", "document_entry"),
    ("XXXII. — Praeceptum Pippini regis, quo villam Exonam monasterio sancti Dionysii restituit (an. 766). 1536", "document_entry"),
    ("XXXIII. — Donatio Pippini monasterio sancti Antonini (an. 767). 1537", "document_entry"),
    ("XXXIV. — Pippini regis praeceptum, quo Fuldensi ecclesiæ Aumundi statu villam largitur (an. 768). 1539", "document_entry"),
    ("XXXV. — Sigfridi dynastæ in Alsacia litteræ ad filium Altmannum, qua monasterio Gregoriano plurima bona assignat (an. 768). 1541", "document_entry"),
    ("XXXVI. — Praeceptum Pippini regis, quo silva Aqualina monasterio San-Dionyisano conceditur (an. 768). 1565", "document_entry"),
    ("XXXVII. — Praeceptum Pippini regis de immunitate nostareii sancti Dionysii (an. 768). 1566", "document_entry"),
    ("XXXVIII. — Praeceptum Pippini de confirmatione privilegiorum monasterii sancti Dionysii (an. 768). 1567", "document_entry"),
    ("XXXIX. — Diploma Pippini regis Francorum, quo Fulrado abbati sancti Dionysii et capellano suo confirmat traditionem illam a Widone Alsace dynasta in Germar, sancto Hippolyto, Ensehim, Scheffersheim, Grusheim et Rappoltsweiler (an. 768). 1570", "document_entry"),
    ("XL. — Praeceptum Carolomanni regis, fratris Caroli Magni, quo monasterii Dionysiani immunitates et a teloneis exemptionem omnibus ad festivitatem seu mercatum sancti Dionysii concurrentibus confirmat (an. 769). 1572", "document_entry"),
    ("XLI. — Carolomanni regis charta pro monasterio sancti Gregorii (an. 769). 1574", "document_entry"),
    ("XLII. — Praeceptum Carolomanni regis de immunitate ecclesiæ Argentoli (an. 769). 1575", "document_entry"),
    ("XLIII. — Carolomanni justitiam reddidit monasterio Prumiensi de loco Beaufeld (an. 770). 1576", "document_entry"),
    ("XLIV. — Carolomanni Austrasiæ regis charta, qua monasteri Honaungensis bona confirmat (an. 770). 1577", "document_entry"),
    ("XLV. — Charta Carolomanni regis, qui monasterio Winonensi seu Ebersheimensi confirmat terras ad Adralrici aus Alsatice eidem monasterio concesserat (an. 770). 1578", "document_entry"),
    ("XLVI. — Carolomanni regis privilegium pro monasterio Grandsavellensi (an. 770). 1579", "document_entry"),
    ("XLVII. — Charta Carolomanni regis, qua villas Faberrolas et Norontem concedit Dionysiano confert (an. 771). 1581", "document_entry"),
    ("XLVIII. — Testamentum Remigii episcopi Argentinensis, seu charta donationis monasteriorum Aschoviensis, et ecclesiam Argentinensem (an. 778). 1582", "document_entry"),
]


def locator_fields(raw: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"\s+(\d+)$", raw)
    if not match:
        return raw, None, None
    locator = match.group(1)
    return raw[:match.start()].rstrip(), locator, int(locator)


def main() -> None:
    workplan = _read_json(WORKPLAN)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    owned = [str(value) for value in chunk["physical_files"]]
    entries = []
    for order, (raw, kind) in enumerate(ITEMS, 1):
        target, page_raw, page_int = locator_fields(raw)
        entries.append({
            "entry_key": f"{SECTION_KEY}:{CHUNK_ID}:{order:03d}",
            "entry_order": order,
            "entry_raw": raw,
            "target_raw": target,
            "target_file": None,
            "page_ref_raw": page_raw,
            "page_ref_int": page_int,
            "page_ref_col": None,
            "note_raw": "heading" if kind == "contents_heading" else None,
            "normalized_target": target,
            "confidence": "medium" if order in {9, 10, 31, 59} else "high",
            "raw_json": {"source_files": [FILE_798], "entry_kind": kind},
        })

    payload = {
        "schema_version": 2,
        "volume_id": "PL096",
        "section_id": SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": FINGERPRINT,
        "status": "complete",
        "physical_files": owned,
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [
            {"physical_left_file": FILE_797, "physical_right_file": FILE_798, "decision": "joined_entry", "reason": "File 797 ends the CAP. XXXVII entry with 'et bellum.' and file 798 begins its grammatical continuation 'Theudoberli contra Chlotarilum.'; the entry belongs to the preceding chunk and is not re-emitted here."},
            {"physical_left_file": FILE_798, "physical_right_file": FILE_799, "decision": "not_same_entry", "reason": "File 798 closes the table cleanly with FINIS TOMI NONAGESIMI SEXTI and the printer line; file 799 is an empty guard page."},
            {"physical_left_file": FILE_799, "physical_right_file": FILE_800, "decision": "not_same_entry", "reason": "The empty guard page 799 has no unfinished table item, while 800 contains only repeated library stamp text."},
            {"physical_left_file": FILE_800, "physical_right_file": FILE_801, "decision": "not_same_entry", "reason": "Files 800 and 801 are cover/guard matter with independent library markings, not continuations of editorial contents entries."},
        ],
        "works": [],
        "sections": [{
            "section_key": SECTION_KEY,
            "work_key": None,
            "scope_kind": "volume_end",
            "index_kind": "ORDO RERUM",
            "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "heading_norm": "Ordo rerum quæ in hoc tomo continentur",
            "page_start": None,
            "page_end": None,
            "file_start": FILE_798,
            "file_end": FILE_801,
            "confidence": "high",
            "raw_json": {
                "source_files": owned,
                "adjacent_context_files_checked": [str(value) for value in chunk["context_files"]],
                "extra_file_evidence": "Read physical files 793–798 and 802 with the XML OCR view. File 797 proves the incoming CAP. XXXVII continuation; file 798 has header '1611 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR. 1612' and closes the TOC; 799–802 are blank, cover, guard, or library-marking matter. No target locator was run.",
                "numbering_note": "The header's 1611 and 1612 and all terminal numerals in entries are printed editorial locators; none was inferred from an OCR filename suffix.",
                "ocr_uncertainties": ["The OCR literals 'Uodu', 'Chlodacharins', 'domas', 'inscret', 'nostareii', and 'monasteri Honaungensis' are preserved.", "Proven typographic wraps such as Theu-dericum, Chlodacha-rium, and Di-o nysii were joined; the lexical form 'San-Dionyisano' is retained."],
                "entries_status": "complete",
                "entries_status_reason": "All 71 readable contents-table items that begin on owned file 798 are serialized. The inherited first line from file 797, running header/footer, FINIS TOMI line, printer line, and non-editorial guard-page markings are not table entries.",
            },
            "entries": entries,
        }],
        "notes": [
            "Bounded fragment only: this output does not assemble the volume.",
            "Files 799–801 are included as owned physical coverage but contain no readable contents-table line items.",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUTPUT, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()
