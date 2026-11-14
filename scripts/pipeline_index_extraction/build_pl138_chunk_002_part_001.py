#!/usr/bin/env python3
"""Build the OCR-reviewed bounded PL138 closing ORDO RERUM fragment."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from scripts.run_index_extraction_chunks import _read_json, _validate_fragment


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "teste/PL138/text"
PREFIX = "325902ea-b62d-4525-a755-89e3b00d742e-"
FILE_679 = str(SOURCE / f"{PREFIX}679.txt")
FILE_680 = str(SOURCE / f"{PREFIX}680.txt")
OUT = ROOT / "data/index_intermediate_payloads/PL138/chunks/section_002_part_001.json"
WORKPLAN = ROOT / "data/index_intermediate_payloads/PL138/workplan.json"
SECTION_ID = "PL138:candidate-section:002"
SECTION_KEY = "PL138:section:volume_end:ordo_rerum:002"
CHUNK_ID = "PL138:chunk:002:001"
FINGERPRINT = "cb0a1463d80dd05986d5e2c7269d38e328c5be99ee6ee4cc4b1051ff944a851d"


# One quoted line is one logical table item.  The source OCR has no reliable XML
# line IDs, so the literal printed item is retained in both entry_raw and source_lines.
ITEMS_679 = r"""
RICHERUS S. REMIGII MONACHUS.
De Richero et ejus Chronicio notitia. 9
HISTORIARUM LIBRI QUATUOR.
Prologus. 17
LIBER PRIMUS 17
LIBER II. 49
LIBER III. 89
LIBER IV. 125
APPENDIX AD SÆCULUM X.
SÆCULI X AUCTORES ANNIN CERTI.
GERARDUS S. MBGAARDI SUESSIONENSIS DECANUS.
Prologus in Vitam S. Romani. 171
Admonitio. 173
Vita. 175
ALBINUS EREMITA.
Epistola ad Helerbrunth. 186
TERALDUS.
Epistola ad Guidonem. 186
Amblaudus abbas Sollemniacensis. Epistola ad Herveum. 187
SÆCULI X OPERA ADESTITOKA.
DE FUNDATIONE MONASTERII S. ARNULPHI METENSIS. 189
SERMO DE S. CLODOALDO CONFESSORE. 199
LIBELLUS DE SUCCESSORIB. S. HILDULPHI IN OXAGO. 201
DE GESTIS EPISCOPORUM ANTISSIODORENSIUM. 219
CARMEN DE MONASTERIO GEMMETICENSI. 394
Pesuneme collectione canonica Mai montium. 597
CANONUM PRISCA COLLECTIO, 597
Prefatio. 597
Liber I. — De ordinatione clericorum. 597
LIBER II. — De celebratione concilii, etc. 403
LIBER III. — De ministeriis ecclesiasticis. 412
LIBER IV. — De monachis. 415
LIBER V. — De gratia baptizandorum. 417
LIBER VI. — De diversis. 421
LIBER VII. — De homicidio, matrimonio, jurejurando jejuno, eleemosyna et oratione. 428
LIBER VIII. De oeneritic et gratia communis etiam in ultimis, etc. 434
LIBER IX. — De judicio poenitentium etc 437
EPISTOLA CANONICA. 443
MONUMENTA DIPLOMATICA.
ANGLO-SAXONUM REGUM LEGES ECCLESIASTICÆ.
LEGES ECCLESIASTICÆ ALFREDI REGIS ANGLORUM. 447
LEGES ECCLESIASTICÆ EDWARDI SENIORIS ANGLORUM REGIS ET GUTHRUMI REGIS DANORUM. 459
LEGES ECCLESIASTICÆ ÆTHELSTANI ANGLOORUM REGIS. 461
LEGES ECCLESIASTICÆ H0ELI-DNA, NEGIS SEU PRINCIPIS TOTIUS WALLIÆ. 471
LEGES ECCLESIASTICÆ EDMUNDI ANGLORUM REGIS. 480
LEGES ECCLESIASTICÆ EDGARI REGIS ANGLORUM. 485
Charta ejusdem. 488
Leges Ecclesiasticæ. 496
Prisco versio legum. 497
Cronones edit sub Edgardo. 499
Charta ejusdem in concilio Londoniensi, anno 9vo vel 972 dato. 518
Charta Malmesburiensi concessio consecta anno 974. 510
LEGES PRESBYTERORUM NUNITI MEMBREN S. 522
FRANCORUM REGUM IMPERATORUM DIVISO IMPERIO CAPITULARIA.
LOTHARI I Conventus in Legehlsemensis. 525
HULDOVICI II ET KAROLI II pactum Argentostensis. 527
KAROLI II Conventus in villa Coloniâ. 528
Synodus apud Tolovam 532
HLODHARI I HLODOVICI II et KAROLI II Conventus ad Theodonius villam. 538
KAROLI I Concilum in Verno palatio. 540
Ejusdem synodus Belsovancensis. 544
HLDOVICI II Italiae regis capitulum. 546
"""

ITEMS_680_LEFT = r"""
KAROLI II Conventus in villa Sparnaco. 546
HLOTHARII, HLUDOWICI et KAROLI conventus apud Marsnam. 554
HLUDOWICI II imperatoris conventus Ticinensis. 557
HLOTHARII, HLUDOWICI et KAROLI conventus apud Marsnam II. 575
HLUDOWICI Germaniæ regis conventus Moguntinus. 579
KAROLI II. — Synodus Suessionnensis 589
Ejusdem synodus apud verneream. 595
HLOTHARII et KAROLI conventus apud Valentianas. 600
Conventus apud Leudicam. 605
KAROLI II. Conventus Attiniacensis. 608
HLUDOWICI II. Imperatoris conventus Ticinensis II. 609
Conventus Ticinensis III. 617
Constitutiones. 622
Capitula excerpta. 627
KAROLI II. — Capitula ad Francos et Aquitanos missa de Carisiaco. 631
Procerum consilium. 635
Capitula ad Francos et Aquitanos missa. 637
Coronatio Judithæ filiæ suæ. 639
Conventus Carisiacensis. 641
KAROLI II et HLOTHARII. Conventus apud Quintiniacum. 649
Conventus Carisiacensis. 651
Synodus Mettensis. 655
Conventus apud Saponarias. 658
HLOTHARII II. Synodus Aquensis. 664
Synodus Aquensis II. 668
HLUDOWICI, KAROLI, HLOTHARII conventus in Basilica S. Castoris apud Confluentes. 669
KAROLI II. Edictum de tributo Nordmannico. 680
Edictum Carisiacense. 682
Synodus Pistensis. 684
HLUDOWICI, KAROLI et HLOTHARII conventus ad Sablonarias. 692
KAROLI II edictum Pictense. 701
HLUDOWICI GERMANICI et KAROLI II pactum Tusiacense. 718
HLOTHARII II, sacramentum de Theodberga recipienda. 724
HLudowici II constitutio de exercitu Beneventum promovendo. 725
Coronatio Hermíntrudis reginæ. 727
HLUDOWICI et KAROLI conventus Mettis. 751
KAROLI II conventus apud Pistas. 751
Ejus coronatio in regno Hlotharii 757
HLUDOWICI GERMANICI et KAROLI. II pactio Aquensis. 742
Divisio regni Hlotharii. 744
KAROLI II, conventus apud Gundulphi villam. 745
Capitulare Carisiacense. 748
Conventus Attiniacensis. 752
HLUDOWICI II capitula diversa. 753
KAROLI II conventus Ticinensis. 762
Synodus Pontigonensis. 767
Edictum in tributo Nordmannico. 772
Conventus Carisiacensis. 775
HLUDOWICI II coronatio. 781
HLUDOWICI JUNIORIS ET HLUDOWICI KAROLI filii conventus Furonensis. 785
BOSONIS regis electio. 788
KARLOMANNI HLUDOWICI II filii Galliæ regis promissio apud Carisiacum. 791
Capitula in Brolio compendii. 792
Capitula apud Vernis palatium. 794
ODONIS REGIS ELECTIO. 797
WIDONIS REGIS ELECTIO. 797
LEGES WIDONIS. 801
LUDOVICI REGIS ARELATENSIS ELECTIO. 804
ANNULFI CONCILIUM TRIBURIENSE. 806
CORONATIO EJUS. 809
LAMBERTI IMPERATORIS CONVENTUS RAVENNAS. 800
KAROLI III CAPITULA DE TUNGRENSI EPISCOPATU. 814
HENRICI I ET KAROLI conventus Confluentiæ. 817
Conventus Diusburgensis. 820
Conventus Erfordiensis. 820
TRIUM OTTONUM CONSTITUTIONES ECCLES. 821
OTTONIS MAGNI CONSTITUTIONES. 821
Synodus Ingelheimensis. 821
Constitutio Francofurtana. 831
Conventus Augustanus. 835
"""

ITEMS_680_RIGHT = r"""
Coronatio Romana. 83
Ottonis Magni pactum cum Joanne XII. 83
Synodus romana. 84
Ottonis I imperatoris et Ottonis II regis edictum. 84
Ottonis magni mandatum de inthronizatione archiepiscopi Magdeburgensis. 84
Conventus Papiensis. 85
Ottonis II imp. pax cum Venetis. 85
Ottonis III imperatoris edictum. 85
Ottonis III constitutio Ticinensis. 85
MONUMENTA LITURGICA.
MISSÆ GALLICANÆ UNDECIM 86
LITURGIÆ GALLICANÆ FRAGMENTA. 87
LITANIÆ ET ORATIONES ANTIQUÆ.
Litaniæ Carolinæ. 88
Litaniæ Arelatenses 88
Litaniæ a sanctis Patribus constitutæ. 88
Litaniæ Byzantinæ. 89
Laudes seu Acclamationes. 90
Orationes quædam. 90
Pœnitentiale Gerundense. 90
Necrologium Gerundense. 90
Missa Æthiopum. 90
Benedictio puerperæ secundum usum Æthiopum. 92
Ordo baptismi secundum usum Æthiopum. 95
MONUMENTA VARIA QUÆ AD CELEBRATIONEM SACRAMENTORUM ET OFFICIA QUÆ QUE ECCLESIASTICA PERTINENT.
ORDO BAPTISMATIS. 95
ORDO CONFIRMATIONIS. 95
JUDICIUM PŒNITENTIÆ. 95
Ordo ad dandam pœnitentiam. 96
Ordo ad reconciliandos pœnitentes. 96
Orationes ad visitandum infirmum. 96
Ordo ad visitandum infirmum. 96
Ordo de sacris Ordinibus. 100
Ordo quomodo Ecclesia debeat dedicari. 101
Benedictio salis et aquæ. 103
Benedictio cereorum. 103
Benedictio cinerum. 103
Benedictiones palmarum. 103
Benedictio olei. 106
Benedictiones variæ. 107
Versus in Cœna Domini et Parasceve, et Sabbato sancto. 107
BENEDICTIONES, CONSECRATIONES ET ORDINATIONES VARIÆ. 109
Consecratio viduæ. 109
Ordinatio abbatissæ. 110
Consecratio sacræ virginis. 110
Benedictio et oratio ad monachas faciendas. 110
Professio regularis. 110
Ordinatio abbatis. 110
Ordo ad regem benedicendum. 111
Benedictio reginæ. 111
Benedictio armorum. 112
ORDO EXCOMMUNICATIONIS. 112
PROBATIONES PER AQUAM, IGNITOS VOMERES, etc 112
BENEDICTIONES VARIÆ. 113
ORATIONES CONTRA DÆMONIACUM. 114
OBSEQUIUM CIRCA MORIENTES. 115
EXPOSITIO SUPER MISSAM. 116
KALENDARIA ET MARTYROLOGIA ANTIQUA.
Kalendarium Floriacense. 118
— Antiquissimum. 119
— Stabulense. 120
— Verdunense. 120
Martyrologium insignis ecclesiæ Antissiodorensis. 120
Kalendarium Mantuanum. 125
— Brixianum. 126
— Vallumbrosanum. 127
Fragmentum alterius multo antiquioris Vallumbrosani Kalendarii. 128
Lucensis Kalendaris Fragmentum. 129
Incipit Bedæ martyrologium. 129
In sequentem missam et missæ fragmentum cardinalis Bonæ admonitio. 130
MISSA LATINA. 130
Fragmentum missæ. 133
MONUMENTA MONASTICA.
DE PRANDIO MONACHORUM. 1348
STATUTA CANONICORUM TURONENSIUM cum vivebant in communi. 1349
"""


def lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def target_and_ref(raw: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"\s+(\d{1,4})$", raw)
    if not match:
        return raw, None, None
    ref = match.group(1)
    return raw[:match.start()].rstrip(), ref, int(ref)


def is_heading(target: str) -> bool:
    letters = [char for char in target if char.isalpha()]
    return bool(letters) and all(char == char.upper() for char in letters)


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().lower()


def entry(raw: str, file_path: str, order: int) -> dict:
    target, ref_raw, ref_int = target_and_ref(raw)
    confidence = "medium" if any(token in raw for token in ("ANNIN", "MBGAARDI", "ADESTITOKA", "oeneritic", "ANGLOORUM", "H0ELI", "NEGIS", "NUNITI", "MEMBREN", "Legehlsemensis", "HLDOVICI", "HLODHARI", "HLudowici")) else "high"
    return {
        "entry_key": f"{SECTION_KEY}:{CHUNK_ID}:{order:03d}",
        "entry_order": order,
        "entry_raw": raw,
        "target_raw": target,
        "target_file": None,
        "page_ref_raw": ref_raw,
        "page_ref_int": ref_int,
        "page_ref_col": "col." if ref_raw is not None else None,
        "note_raw": "heading" if is_heading(target) else None,
        "normalized_target": normalized(target),
        "confidence": confidence,
        "raw_json": {
            "source_files": [file_path],
            "source_lines": [raw],
            "entry_kind": "contents_heading" if is_heading(target) else "contents_entry",
            "reference_system": "Migne editorial column locator" if ref_raw is not None else None,
        },
    }


def main() -> None:
    workplan = _read_json(WORKPLAN)
    chunk = next(item for item in workplan["chunks"] if item["chunk_id"] == CHUNK_ID)
    raws = [(raw, FILE_679) for raw in lines(ITEMS_679)]
    raws += [(raw, FILE_680) for raw in lines(ITEMS_680_LEFT)]
    raws += [(raw, FILE_680) for raw in lines(ITEMS_680_RIGHT)]
    entries = [entry(raw, file_path, order) for order, (raw, file_path) in enumerate(raws, 1)]

    # Preserve line-wrap proof for the entries reconstructed from two OCR lines.
    source_lines = {
        "LIBELLUS DE SUCCESSORIB. S. HILDULPHI IN OXAGO. 201": ["LIBELLUS DE SUCCESSORIB. S. HILDULPHI IN", "OXAGO. 201"],
        "LIBER VII. — De homicidio, matrimonio, jurejurando jejuno, eleemosyna et oratione. 428": ["LIBER VII. — De homicidio, matrimonio, jurejurando", "jejuno, eleemosyna et oratione. 428"],
        "LEGES ECCLESIASTICÆ ALFREDI REGIS ANGLORUM. 447": ["LEGES ECCLESIASTICÆ ALFREDI REGIS ANGLO- 447", "RUM."],
        "LEGES ECCLESIASTICÆ ÆTHELSTANI ANGLOORUM REGIS. 461": ["LEGES ECCLESIASTICÆ ÆTHELSTANI ANGLOORUM 461", "REGIS."],
        "LEGES ECCLESIASTICÆ H0ELI-DNA, NEGIS SEU PRINCIPIS TOTIUS WALLIÆ. 471": ["LEGES ECCLESIASTICÆ H0ELI-DNA, NEGIS SEU 461", "PRINCIPIS TOTIUS WALLIÆ. 471"],
        "LEGES ECCLESIASTICÆ EDMUNDI ANGLORUM REGIS. 480": ["LEGES ECCLESIASTICÆ EDMUNDI ANGLORUM RE- 480", "GIS."],
        "HULDOVICI II ET KAROLI II pactum Argentostensis. 527": ["HULDOVICI II ET KAROLI II pactum Argentos- 527", "tensis."],
        "STATUTA CANONICORUM TURONENSIUM cum vivebant in communi. 1349": ["STATUTA CANONICORUM TURONENSIUM cum vita-", "bant in communi. 1349"],
    }
    for item in entries:
        if item["entry_raw"] in source_lines:
            item["raw_json"]["source_lines"] = source_lines[item["entry_raw"]]

    section = {
        "section_key": SECTION_KEY,
        "work_key": None,
        "scope_kind": "volume_end",
        "index_kind": "ORDO_RERUM",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "Ordo rerum quae in hoc tomo continentur",
        "page_start": 1350,
        "page_end": None,
        "file_start": FILE_679,
        "file_end": FILE_680,
        "confidence": "high",
        "raw_json": {
            "source_files": [FILE_679, FILE_680],
            "adjacent_context_files_checked": [str(SOURCE / f"{PREFIX}{number}.txt") for number in range(675, 685)],
            "header_evidence": {
                "679": "1349 1350 / ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
                "680": "1351 ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR 135.",
            },
            "numbering_note": "The table begins in the lower block of physical file 679, after the current text ending at editorial 1349. File 680 has a readable left header 1351 and a damaged right-page reading `135.`; no filename suffix was used to supply an editorial number.",
            "extra_file_evidence": "Files 675-678 were read to establish that they are body text preceding the ORDO. Files 681-684 were read to establish end matter/blank covers after FINIS TOMI. No target locator was run.",
            "reading_order": "Physical file 680 was transcribed left column then right column, following the printed table order.",
            "entries_status": "complete",
            "entries_status_reason": "Every readable table item in the owned files is serialized, including structural headings and wrapped entries. The non-table body text above the lower ORDO block on file 679 and digitization/end-matter footers were excluded.",
        },
        "entries": entries,
    }
    payload = {
        "schema_version": 2,
        "volume_id": "PL138",
        "section_id": SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": FINGERPRINT,
        "status": "complete",
        "physical_files": [FILE_679, FILE_680],
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [
            {
                "physical_left_file": FILE_679,
                "physical_right_file": FILE_680,
                "decision": "not_same_entry",
                "reason": "File 679 ends with the complete item `HLDOVICI II Italiae regis capitulum. 546`; file 680 begins the independent complete item `KAROLI II Conventus in villa Sparnaco. 546`. The matching editorial locator does not make them one entry.",
            }
        ],
        "works": [],
        "sections": [section],
        "notes": [
            "Bounded closing-ORDO fragment only; it does not assemble the volume.",
            "OCR literals and suspect locators (including the truncated 83–85 values in the right column of file 680) are preserved as editorial data rather than corrected from physical-file numbers.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _validate_fragment(OUT, workplan, chunk, require_input_fingerprint=True)


if __name__ == "__main__":
    main()
