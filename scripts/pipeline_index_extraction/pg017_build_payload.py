#!/usr/bin/env python3
"""Build the PG017 alphabetical-index payload from OCR notes and helper output.

Run:
  python scripts/pipeline_index_extraction/pg017_build_payload.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PG017/text"
OUTPUT = ROOT / "data/alphabetical_index_payloads/PG017_alphabetical_indices.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PG017_helper_output.json"

TOC_PREFIX = SOURCE_ROOT / "a04dd7ce-7925-462f-a6c8-fc2272af6ed1"
TOC_FILE_MAP = {
    676: str(TOC_PREFIX.with_name("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-676.txt")),
    677: str(TOC_PREFIX.with_name("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-677.txt")),
    678: str(TOC_PREFIX.with_name("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-678.txt")),
    679: str(TOC_PREFIX.with_name("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-679.txt")),
    680: str(TOC_PREFIX.with_name("a04dd7ce-7925-462f-a6c8-fc2272af6ed1-680.txt")),
}


def norm(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("æ", "ae").replace("œ", "oe")
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def helper_entry_map() -> dict[str, dict]:
    data = json.loads(HELPER_OUTPUT.read_text(encoding="utf-8"))
    out = {}
    for entry in data["entries"]:
        out[entry["entry_id"]] = entry
    return out


def helper_best(helper: dict, entry_id: str) -> dict:
    item = helper[entry_id]
    best = item["best_candidate"]
    return {
        "status": item["status"],
        "candidate_role": best.get("candidate_role"),
        "probability": best.get("probability"),
        "file": best.get("file"),
        "file_seq": best.get("file_seq"),
        "reason_summary": best.get("reason_summary"),
    }


def mk_node(node_key, section_key, parent, order, kind, raw, node_level):
    return {
        "node_key": node_key,
        "section_key": section_key,
        "parent_node_key": parent,
        "node_order": order,
        "node_kind": kind,
        "label_raw": raw,
        "label_norm": norm(raw),
        "label_sort": norm(raw),
        "node_level": node_level,
        "confidence": 0.95,
        "raw_json": {"source_token": raw, "section_kind": "ordo_rerum"},
    }


def main() -> None:
    helper = helper_entry_map()
    section_key = "PG017:alpha:ordo_rerum:001"
    section_start_file = TOC_FILE_MAP[676]
    section_end_file = TOC_FILE_MAP[680]

    section = {
        "section_key": section_key,
        "volume_id": "PG017",
        "work_key": None,
        "section_order": 1,
        "section_kind": "ordo_rerum",
        "heading_raw": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
        "heading_norm": "ordo rerum quae in hoc tomo continentur",
        "heading_letter": None,
        "page_start": 1531,
        "page_end": 1560,
        "file_start": TOC_FILE_MAP[676],
        "file_end": TOC_FILE_MAP[680],
        "confidence": 0.96,
        "raw_json": {
            "section_start_heading": "ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.",
            "section_kind_reason": "Recoverable Ordo Rerum block spanning the final index pages; the OCR headers show pagination drift and a final excerpt on page 1383 inside the closing page 680.",
            "helper_used": True,
        },
    }

    nodes = [
        mk_node("PG017:node:001", section_key, None, 1, "heading_group", "ORIGENES.", 1),
        mk_node("PG017:node:002", section_key, "PG017:node:001", 2, "heading_group", "SUPPLEMENTUM AD ORIGENIS EXEGETICA.", 2),
        mk_node("PG017:node:003", section_key, "PG017:node:001", 3, "heading_group", "SPURIA.", 2),
        mk_node("PG017:node:004", section_key, "PG017:node:001", 4, "heading_group", "OPERA AD ORIGENEM SPECTANTIA.", 2),
        mk_node("PG017:node:005", section_key, None, 5, "heading_group", "P. DANIELIS HUETII ORIGENIANA.", 1),
        mk_node("PG017:node:006", section_key, "PG017:node:005", 6, "heading_group", "LIBER PRIMUS, CONTINENS ORIGENIS VITAM.", 2),
        mk_node("PG017:node:007", section_key, "PG017:node:005", 7, "heading_group", "LIBER SECUNDUS, CONTINENS ORIGENIS DOCTRINAM.", 2),
        mk_node("PG017:node:008", section_key, "PG017:node:005", 8, "heading_group", "LIBER TERTIUS, continens Origenis scripta.", 2),
        mk_node("PG017:node:009", section_key, "PG017:node:007", 9, "heading_group", "CAPITIS QUARTI PARTITIO.", 3),
        mk_node("PG017:node:010", section_key, "PG017:node:008", 10, "heading_group", "CAPITIS SECUNDI PARTITIO.", 3),
        mk_node("PG017:node:011", section_key, "PG017:node:008", 11, "heading_group", "CAPITIS TERTII PARTITIO.", 3),
    ]

    record_lines = [
        # entry_key, helper_id, entry_kind, parent_node, anchor_seq, page_ref, lemma_raw, entry_raw, context_raw, confidence
        ("PG017:entry:001", "pg017_monitum_9", "lemma", "PG017:node:002", 676, 9, "Monitum", "Monitum. 9", None, 0.79),
        ("PG017:entry:002", "pg017_adnotationes_genesim_11", "lemma", "PG017:node:002", 676, 11, "Adnotationes in Genesim", "Adnotationes in Genesim. 11", None, 1.0),
        ("PG017:entry:003", "pg017_adnotationes_exodum_15", "lemma", "PG017:node:002", 676, 15, "Adnotationes in Exodum", "Adnotationes in Exodum. 15", None, 1.0),
        ("PG017:entry:004", "pg017_adnotationes_leviticum_17", "lemma", "PG017:node:002", 676, 17, "Adnotationes in Leviticum", "Adnotationes in Leviticum. 17", None, 0.999),
        ("PG017:entry:005", "pg017_adnotationes_numeros_21", "lemma", "PG017:node:002", 676, 21, "Adnotationes in Numeros", "Adnotationes in Numeros. 21", None, 1.0),
        ("PG017:entry:006", "pg017_adnotationes_deuteronomium_25", "lemma", "PG017:node:002", 676, 25, "Adnotationes in Deuteronomium", "Adnotationes in Deuteronomium. 25", None, 0.87),
        ("PG017:entry:007", "pg017_adnotationes_jesum_filium_nave_35", "lemma", "PG017:node:002", 676, 35, "Adnotationes in Jesum filium Nave", "Adnotationes in Jesum filium Nave. 35", None, 0.999),
        ("PG017:entry:008", "pg017_adnotationes_judices_37", "lemma", "PG017:node:002", 676, 37, "Adnotationes in Judices", "Adnotationes in Judices. 37", None, 0.999),
        ("PG017:entry:009", "pg017_adnotationes_regum_i_39", "lemma", "PG017:node:002", 676, 39, "Adnotationes in librum I Regum", "Adnotationes in librum I Regum. 39", None, 0.999),
        ("PG017:entry:010", "pg017_adnotationes_regum_ii_47", "lemma", "PG017:node:002", 676, 47, "Adnotationes in librum II Regum", "Adnotationes in librum II Regum. 47", None, 0.993),
        ("PG017:entry:011", "pg017_enarrationes_job_55", "lemma", "PG017:node:002", 676, 55, "Enarrationes in Job", "Enarrationes in Job. 55", None, 0.998),
        ("PG017:entry:012", "pg017_adnotationes_regum_iii_57", "lemma", "PG017:node:002", 676, 57, "Adnotationes in librum III Regum", "Adnotationes in librum III Regum. 57", None, 0.838),
        ("PG017:entry:013", "pg017_excerpta_psalmos_106", "lemma", "PG017:node:002", 676, 106, "Excerpta in Psalmos", "Excerpta in Psalmos. 106", None, 1.0),
        ("PG017:entry:014", "pg017_excerpta_psalmos_106", "lemma", "PG017:node:002", 676, 106, "In psalmum IX", "In psalmum IX. 106", "In psalmum IX is grouped with Excerpta in Psalmos in the same TOC block.", 0.95),
        ("PG017:entry:015", "pg017_in_psalmum_xii_107", "lemma", "PG017:node:002", 676, 107, "In psalmum XII", "In psalmum XII. 107", None, 0.942),
        ("PG017:entry:016", "pg017_in_psalmos_xv_xvi_xvii_110", "lemma", "PG017:node:002", 676, 110, "In psalmos XV, XVI, XVII", "In psalmos XV, XVI, XVII. 110", None, 0.999),
        ("PG017:entry:017", "pg017_in_psalmum_xxii_111", "lemma", "PG017:node:002", 676, 111, "In psalmum XXII", "In psalmum XXII. 111", None, 0.467),
        ("PG017:entry:018", "pg017_in_psalmum_xxiii_114", "lemma", "PG017:node:002", 676, 114, "In psalmum XXIII", "In psalmum XXIII. 114", None, 0.622),
        ("PG017:entry:019", "pg017_in_psalmum_xxvii_115", "lemma", "PG017:node:002", 676, 115, "In psalmum XXVII", "In psalmum XXVII. 115", None, 0.353),
        ("PG017:entry:020", "pg017_in_psalmum_xxxvi_118", "lemma", "PG017:node:002", 676, 118, "In psalmum XXXVI", "In psalmum XXXVI. 118", None, 0.648),
        ("PG017:entry:021", "pg017_in_psalmum_xli_135", "lemma", "PG017:node:002", 676, 135, "In psalmum XLI", "In psalmum XLI. 135", None, 0.946),
        ("PG017:entry:022", "pg017_in_psalmum_l_138", "lemma", "PG017:node:002", 676, 138, "In psalmum L", "In psalmum L. 138", None, 0.835),
        ("PG017:entry:023", "pg017_in_psalmum_lxxvii_139", "lemma", "PG017:node:002", 676, 139, "In psalmum LXXVII", "In psalmum LXXVII. 139", None, 0.948),
        ("PG017:entry:024", "pg017_in_psalmum_lxx_ci_150", "lemma", "PG017:node:002", 676, 150, "In psalmum LXX", "In psalmum LXX. 150", None, 0.533),
        ("PG017:entry:025", "pg017_in_psalmum_lxx_ci_150", "lemma", "PG017:node:002", 676, 150, "In psalmum CI", "In psalmum CI. 150", None, 0.533),
        ("PG017:entry:026", "pg017_fragmenta_proverbia_150", "lemma", "PG017:node:002", 676, 150, "Fragmenta in Proverbia", "Fragmenta in Proverbia. 150", None, 0.996),
        ("PG017:entry:027", "pg017_expositio_proverbia_161", "lemma", "PG017:node:002", 676, 161, "Expositio in Proverbia", "Expositio in Proverbia. 161", None, 1.0),
        ("PG017:entry:028", "pg017_scholia_cantica_254", "lemma", "PG017:node:002", 676, 254, "Scholia in Cantica canticorum", "Scholia in Cantica canticorum. 254", None, 1.0),
        ("PG017:entry:029", "pg017_scholia_matthaeum_290", "lemma", "PG017:node:002", 676, 290, "Scholia in Matthæum", "Scholia in Matthæum. 290", None, 1.0),
        ("PG017:entry:030", "pg017_scholia_lucam_512", "lemma", "PG017:node:002", 676, 512, "Scholia in Lucam", "Scholia in Lucam. 512", None, 0.974),
        ("PG017:entry:031", "pg017_scholia_i_joannis_570", "lemma", "PG017:node:002", 676, 570, "Scholia in Epistolam I Joannis", "Scholia in Epistolam I Joannis. 570", None, 0.813),
        ("PG017:entry:032", "pg017_commentarius_anonymi_job_571", "lemma", "PG017:node:003", 676, 571, "Commentarius anonymi in Job", "Commentarius anonymi in Job. 571", None, 0.949),
        ("PG017:entry:033", "pg017_admonitio_apologeticum_521", "lemma", "PG017:node:004", 676, 521, "Admonitio ad Apologeticum Libellum S. Pamphili martyris pro Origene", "Admonitio ad Apologeticum Libellum S. Pamphili martyris pro Origene. 521", None, 1.0),
        ("PG017:entry:034", "pg017_praefatio_rufini_530", "lemma", "PG017:node:004", 676, 530, "Præfatio Rufini in Apologeticum S. Pamphili", "Præfatio Rufini in Apologeticum S. Pamphili. 530", None, 0.835),
        ("PG017:entry:035", "pg017_apologia_s_pamphili_541", "lemma", "PG017:node:004", 676, 541, "APOLOGIA S. PAMPHILI PRO ORIGENE", "APOLOGIA S. PAMPHILI PRO ORIGENE. 541", None, 0.973),
        ("PG017:entry:036", "pg017_praefatio_confessores_541", "lemma", "PG017:node:004", 676, 541, "Præfatio. — Ad confessores ad metalla Palæstinæ damnatos", "Præfatio. — Ad confessores ad metalla Palæstinæ damnatos. 541", None, 1.0),
        ("PG017:entry:037", "pg017_catalogus_prædicationis_549", "heading_group", "PG017:node:004", 676, 549, "CAPUT PRIMUM. — Catalogus ecclesiasticæ prædicationis", "CAPUT PRIMUM. — Catalogus ecclesiasticæ prædicationis. 549", None, 0.99),
        ("PG017:entry:038", "pg017_deo_patre_557", "heading_group", "PG017:node:004", 676, 557, "CAP. II. — De Deo Patre omnipotente", "CAP. II. — De Deo Patre omnipotente. 557", None, 0.999),
        ("PG017:entry:039", "pg017_deitate_filii_559", "heading_group", "PG017:node:004", 676, 559, "CAP. III. — De deitate Filii Dei", "CAP. III. — De deitate Filii Dei. 559", None, 0.999),
        ("PG017:entry:040", "pg017_spiritu_sancto_561", "heading_group", "PG017:node:004", 676, 561, "CAP. IV. — De Spiritu sancto", "CAP. IV. — De Spiritu sancto. 561", None, 0.958),
        ("PG017:entry:041", "pg017_incarnatione_verbi_572", "heading_group", "PG017:node:004", 676, 572, "CAP. V. — De Incarnatione Verbi Dei", "CAP. V. — De Incarnatione Verbi Dei. 572", None, 0.998),
        ("PG017:entry:042", "pg017_scripturis_referuntur_589", "heading_group", "PG017:node:004", 676, 589, "CAP. VI. — Quod ea quæ in Scripturis referuntur, etiam secundum litteram gesta sint", "CAP. VI. — Quod ea quæ in Scripturis referuntur, etiam secundum litteram gesta sint. 589", None, 0.438),
        ("PG017:entry:043", "pg017_resurrectione_594", "heading_group", "PG017:node:004", 676, 594, "CAP. VII. — Quomodo sentiebat de resurrectione", "CAP. VII. — Quomodo sentiebat de resurrectione. 594", None, 0.998),
        ("PG017:entry:044", "pg017_poenis_peccatorum_601", "heading_group", "PG017:node:004", 676, 601, "CAP. VIII. — De pœnis peccatorum", "CAP. VIII. — De pœnis peccatorum. 601", None, 1.0),
        ("PG017:entry:045", "pg017_anima_604", "heading_group", "PG017:node:004", 676, 604, "CAP. IX. — De anima", "CAP. IX. — De anima. 604", None, 0.996),
        ("PG017:entry:046", "pg017_transmutatione_animarum_608", "heading_group", "PG017:node:004", 676, 608, "CAP. X. — De transmutatione animarum", "CAP. X. — De transmutatione animarum 608", None, 0.997),
        ("PG017:entry:047", "pg017_epilogus_rufini_615", "heading_group", "PG017:node:004", 676, 615, "Epilogus Rufini, seu Liber De adulteratione librorum Origenis", "Epilogus Rufini, seu Liber De adulteratione librorum Origenis. 615", None, 0.998),
        ("PG017:entry:048", "pg017_caput_primum_633", "heading_group", "PG017:node:006", 677, 633, "CAPUT PRIMUM. — Quidquid natales inter Origenis et Severi mortem intercessit, complexum", "CAPUT PRIMUM. — Quidquid natales inter Origenis et Severi mortem intercessit, complexum. I. Origenis patria, ætas, parentes. II. Nomen. III. Cognomenta. IV. Institutio puerilis, indoles. V. Præceptores et studia. VI. Utrum Ammonium audiverit. VII. An plures fuerint Origenes, et plures Adamantii. VIII. Leonidæ martyrium. IX. Origenes grammaticam publice profitetur, catechumenos instituit, martyribus præsto est. X. Utrum hoc tempore Cæsaream Cappadociæ iverit. XI. Grammaticæ docendæ munus abdicat. XII. Piæ ejus exercitationes. Plurimi ex ejus discipulis martyrium obeunt. XIII. Se ipse evirat. 633", None, 0.99),
        ("PG017:entry:049", "pg017_libri_primi_partitio_633", "heading_group", "PG017:node:006", 677, 633, "Libri primi partitio", "Libri primi partitio. 633", None, 0.997),
        ("PG017:entry:050", "pg017_cap_ii_651", "heading_group", "PG017:node:006", 677, 651, "CAP. II. — Pertinens ab obitu Severi, ad initia imperii Maximini", "CAP. II. — Pertinens ab obitu Severi, ad initia imperii Maximini. I. Severi imperatoris obitus. Origenes Romam proficiscitur. II. Alexandriam redit. III. Hebraicam linguam condiscit. IV. Ambrosium ad Christi fidem convertit. V. Accersitur in Arabiam, redit Alexandriam. VI. Palæstinam petit, Alexandriam redit. VII. A Mammæa accersitur Antiochiam, Alexandriam revertitur. VIII. Scripturam sacram Commentariis illustrare aggreditur. IX. Commentarios in Joannem, et in alios Scripturæ libros inchoat. X. In Achajam per Palæstinam proficiscitur. XI. Athenis discedens Epheso iter habet. XII. Cæsareæ presbyter jam antea fuerat ordinatus Alexandriæ pellitur. XIII. Cum, ne vitaretur ab Æthiope, idolis sacrificasset. XIV. Cæsaream Palæstinæ concedit. XV. Confutantur nonnulli, qui Origenem ab Heracla Alexandria depulsum fuisse scripserunt. XVI. Ecclesiastica munia obit; a multis favetur Ecclesiis. XVII. Palæstinam perlustrat. XVIII. Utrum viginti et octo annos Tyri transegerit. XIX. Gregorium Thaumaturgum et Athenodorum fratres, aliosque in litteris sacris et profanis instituit : Isaiam et Ezechielem Commentariis exponit. 651", None, 0.997),
        ("PG017:entry:051", "pg017_cap_iii_675", "heading_group", "PG017:node:006", 677, 675, "CAP. III. — Continens res Origenis a Maximini primordiis ad Philippi necem gestas", "CAP. III. — Continens res Origenis a Maximini primordiis ad Philippi necem gestas. I. Alexandro Severo succedit Maximinus. Sexta persecutio. II. Gregorius et Athenodorus Neocæsaream repetunt. Origenes Cæsaream Cappadociæ ad Firmilianum confugit, latet apud Julianam. III. Hexapla inchoat. IV. Scribit librum De martyrio. V. Maximinus perit, succedit Gordianus. VI. Origenes iterum Athenas proficiscitur, Ambrosius obiter adit Nicomediæ, Athenis absolvit Commentarios suos in Joannem et in Ezechielem, alios inchoat in Canticum canticorum. VII. Cæsaream Stratonis repetit. Commentarios in Canticum absolvit, Firmilianum iterum in disciplinam recipit. VIII. Ad convincendum Berylli hæresim Bostram accersitur, in Palæstinam redit. IX. Obiit Gordianus, succedit Philippus. X. Utrum a Dionysio Alexandrino oppugnatus fuerit Origenes. XI. Origenes sexagenario major scribit contra Celsum, in Matthæum, et in duodecim prophetas; item innumeras epistolas. XII. Utrum Philippus imperator fuerit Christianus. XIII. Homilias suas tum primum excipi patitur Origenes. Fidei suæ professionem mittit ad Fabianum papam, et alios episcopos. XIV. Vocatur ad concilium adversus Arabum hæresim, scribit adversus Helcesaitas, Apellitas profligat. 675", None, 0.998),
        ("PG017:entry:052", "pg017_cap_iv_philippi_687", "heading_group", "PG017:node:006", 677, 687, "CAP. IV. — A Philippi morte exorsum, in Origenis casu desinens", "CAP. IV. — A Philippi morte exorsum, in Origenis casu desinens. I. Philippus imperator interficitur. Subrogatur Decius. Septima persecutio. II. Origenes pro Christo gravissimos cruciatus sustinet. III. Librum De martyrio scribit ad eum Dionysius Alexandrinus. IV. Origenis modestia. Utrum ad vitandum Æthiopis stuprum Christi fidem abjurarit, et quo id tempore contigerit. V. Castigatur criticorum nonnullorum temeritas. VI. Mortuo Decio vinculis solvitur Origenes. Decio succedunt Gallus et Volusianus. VII. Obiit Origenes anno ætatis 69. Pereunt Gallus et Volusianus. VIII. Elogium Origenis. IX. Sepelitur Tyri. Vana de ejus salute quæstio prætermittitur. X. Ipsius discipuli recensentur. 687", None, 0.492),
        ("PG017:entry:053", "pg017_libri_secundi_partitio_697", "heading_group", "PG017:node:007", 677, 697, "Libri secundi partitio", "Libri secundi partitio. 697", None, 0.999),
        ("PG017:entry:054", "pg017_eruditionem_complexum_699", "heading_group", "PG017:node:007", 677, 699, "CAPUT PRIMUM. — Origenis eruditionem complexum", "CAPUT PRIMUM. — Origenis eruditionem complexum. I. Scripturæ callentissimus fuit Origenes. II. Sed Hebraicæ linguæ parum consultus, Samaritanæ vero penitus ignarus. III. Scripturæ interpretes, scriptoresque ecclesiasticos studiose legit. IV. Sacræ doctrinæ causa excoluit etiam philosophiam præsertim. V. Sed et reliquas disciplinas. 699", None, 0.957),
        ("PG017:entry:055", "pg017_dogmata_complexum_703", "heading_group", "PG017:node:007", 677, 703, "CAP. II. — Origenis dogmata complexum. Capitis secundi prologus et partitio", "CAP. II. — Origenis dogmata complexum. Capitis secundi prologus et partitio. 703", None, 0.984),
        ("PG017:entry:056", "pg017_quaestio_prima_deo_703", "heading_group", "PG017:node:007", 677, 703, "QUAESTIO PRIMA. — De Deo", "QUAESTIO PRIMA. — De Deo. I. Utrum circumscriptam esse Dei potentiam Adamantius dixerit. II. Quemadmodum ipsi a Patribus quibusdam objicitur. III. Cui et nonnulli assensi sunt. Ex Academia manavit isthæc doctrina. V. Utrum corporeum esse Deum ratus sit. VI. Ab hæresibus hujus suspicione vindicatur. VII. Objecta diluuntur. VIII. Multos hic error infecit. 703", None, 0.485),
        ("PG017:entry:057", "pg017_quaestio_ii_trinitate_709", "heading_group", "PG017:node:007", 677, 709, "QUAESTIO II. — De sanctissima Trinitate", "QUAESTIO II. — De sanctissima Trinitate. I. In multos circa SS. Trinitatis mysterium errores incidisse fertur Origenes, quorum gravissimi notantur. II. Hoc nomine Patrum multorum reprehensiones expertus est, et defenditur. III. Utrum SS. Trinitatis personas substantia differre opinatus sit. IV. Nonnulla in ejus defensionem afferuntur. V. Eruitur germana ipsius sententia. VI. Utrum Filium per prolationem genitum esse arbitratus sit. VII. Utrum Filium Patre, Spiritum sanctum Filio inferiorem esse dixerit. VIII. Ita ut jactatur a plurimis. IX. Utriusque dignitatem videtur nonnunquam tueri. X. Patrum multorum suffragio gaudet. XI. Recte eum de Filii et Spiritus sancti dignitate sensisse Patres nonnulli testantur. XII. Platonicis deliriis orthodoxam doctrinam oblimavit. XIII. Utrum Patrem primarum rerum conditorem, Filium vero secundarum, et Patris ministrum crediderit. XIV. Vetustorum aliquot Patrum consensu sublevatur. XV. Quo sensu Filium dixerit non esse absolute bonum. XVI. Et non esse absolute veritatem. XVII. Utrum æqua sit multorum criminatio, Origenem dixisse quærentium Patrem a Filio, Filium a Spiritu sancto videri non posse. XVIII. Quam ab eo depellere conatur Rufinus. XIX. Utrum Filii cognitionem cognitione Patris, Spiritus sancti cognitionem cognitione Filii inferiorem dixerit. XX. Quod aliquando videtur negasse. XXI Utrum Filium et Spiritum sanctum existimaverit esse creatos. XXII. Ut vulgo fertur. XXIII. Plurima culpa huic elevandæ proponuntur. XXIV. Aperitur genuina Origenis sententia. XXV. Patres aliquot assentientes habet. XXVI. Quo sensu dixerit duos seraphimos (Isaiæ cap. vi, vers. 2) esse Christum, et Spiritum sanctum. XXVII. Crimini datur Origeni, quod dixerit Patrem rebus universis, Filium ratione præditis, Spiritum sanctum sanctis dumtaxat præesse. XXVIII. Sed defenditur. XXIX. Utrum Filium non orandum esse pronuntiaverit. XXX. Utrum finxerit sibi Spiritum sanctum genus humanum redimere non potuisse. 709", None, 0.999),
        ("PG017:entry:058", "pg017_quaestio_iii_christo_796", "heading_group", "PG017:node:007", 677, 796, "QUAESTIO III. — De Christo, ejusque Incarnatione et εἰσαγωγῆς", "QUAESTIO III. — De Christo, ejusque Incarnatione et εἰσαγωγῆς. I. Plurima de Christo absque Origenes opinatus est. ... 796", None, 0.999),
        ("PG017:entry:059", "pg017_quaestio_iv_mariae_838", "heading_group", "PG017:node:007", 677, 838, "QUAESTIO IV. — De beata Maria Virgine", "QUAESTIO IV. — De beata Maria Virgine. I. Utrum Christum et beatam Mariam Virginem purgatione post partum opus habuisse Origenes existimaverit. II. Utrum claustrum virginitatis beatæ Mariæ in partu reseratum opinatus sit. III. Utrum beatam Mariam peccatis obnoxiam putaverit. 838", None, 0.899),
        ("PG017:entry:060", "pg017_quaestio_v_angelis_844", "heading_group", "PG017:node:007", 677, 844, "QUAESTIO V. — De angelis", "QUAESTIO V. — De angelis. I. Vix certi quidquam Origenis ætate de angelis fuerat definitum. II. Quæritur quo tempore angelos, et rationalis compotes naturas reliquas a Deo conditas, et in peccatum delapsas ratus sit. III. Utrum angelos corporeos esse censuerit. IV. Sibi videtur aliquando non constare. V. Sed conciliantur discordantes loci. VI. Utrum animam illis inesse, in ipsos esse animas dixerit. VII. Angelorum inter et dæmonum corpora discrimen aliquod tenuitatis constituit. VIII. Consentientes habet Patres bene multos. IX. Origenianæ sententiæ radix investigatur. X. Utrum aquas quæ supra et infra firmamentum sunt, angelos esse arbitratus sit. XI. Equid de angelorum libertate, meritis, gratia, remuneratione ac pœnis statuerit. XII. Patrum multorum adversus hanc ipsius doctrinam convicia. XIII. Utrum priorem crediderit hominem an dæmonum naturam, exploratur. XIV. Ex ipsis Origenis verbis sententia illius super angelorum libertate ac meritis aperitur. XV. In qua tamen videtur nonnunquam titubare. XVI. Causæ huic Patres aliquot suffragantur. XVII. Origeni favent alia quædam. XVIII. Utrum angelos ab hominibus erudiri persuasum habuerit. XIX. Expenditur ejus sententia de angelis judicandis. XX. Cujus fundamenta aperiuntur. XXI. Eam frustra excusare conatur S. Thomas. XXII. Quo tempore extrema supplicia dæmonibus vel inflictæ vel infligenda Origenes crediderit. XXIII. Origeni assentiuntur Patres plerique. XXIV. Utrum hominum sanctitate forentum animas angelos esse senserit. XXV. Nonnullorum criminationibus opinionis hujus causa fuit obnoxius. XXVI. Ventilatur ejus sententia de tutelaribus angelis; ac primum gentium. XXVII. Ecclesiarum XXVIII. Hominum singulorum. XXIX. Et rerum anima carentium. XXX. Utrum unicuique genti et homini angelos duos, bonum unum, malum alterum simul assistere putaverit. XXXI. Fons Origenianarum de angelis tutelaribus sententiarum ostenditur. XXXII. Astipulatores Patres recensentur. XXXIII. Eæ et ex Origenis fluctuatione excusari possunt. XXXIV. Quid de angelis φύσει κακός opinatus sit, indagatur. XXXV. Quæritur quid senserit de angelis remunerationum et pœnarum administris. XXXVI. Utrum angelos neutiquam invocandos esse putaverit. XXXVII. Utrum et quomodo cherubinos Filii cogitationes esse dixerit. XXXVIII. Utrum dæmones nidore et sanguine pasci existimaverit. 844", None, 0.999),
        ("PG017:entry:061", "pg017_quaestio_vi_anima_893", "heading_group", "PG017:node:007", 678, 893, "QUAESTIO VI. — De anima", "QUAESTIO VI. — De anima. I. De animæ origine quid statuerit Origenes certum non habuit. II. Ex dogmate πριαρχίας, et perpetui libertatis usus maxima Origenianorum errorum pars profluxit. III. Utrum animas rationis compotes e substantia divinæ deitatis esse asseruerit. IV. An eas corpore antiquiores, in illudque pro peccatis demissas putaverit. V. Unde nomen ψυχή factum autumaverit. VI. Ex his multorum criminationibus est appetitus. VII. Ad doctrinæ hujus fontes digitus intenditur. VIII. Quam variis Scripturæ locis fulcire conatus est Origenes. IX. Multi licet eam funditus labefaciant. X. Patres ejus assertores recensentur. XI. Nihil his temporibus fuerat ab Ecclesia super hoc argumento determinatum. XII. Nec multo recentioribus. XIII. Utrum animas corporeas, et quali corpore præditas crediderit. XIV Origenem Patrum multorum assensus, et sua excusare potest hæsitatio. XV. Ex antiqua philosophia opinionem suam deprompsit. Intricatus Methodi locus explicatur. XVI. Utrum solam animam hominem constituere dixerit. XVII. Examinatur Origenis παραινέσεις. XVIII. Pythagoricam μετατεμψύχωσιν propugnasse a plurimis dictus est Adamantius. XIX. Sed multis purgatur. XX. Metempsychōseos auctores producuntur. 893", None, 0.999),
        ("PG017:entry:062", "pg017_quaestio_vii_libero_arbitrio_919", "heading_group", "PG017:node:007", 678, 919, "QUAESTIO VII. — De libero arbitrio, gratia et prædestinatione", "QUAESTIO VII. — De libero arbitrio, gratia et prædestinatione. I. Sententia Origenis de libero arbitrio naturæ rationalis et gratiæ Dei summam proponitur. II. Eadem fusius explanatur. III. In quo positam arbitrii libertatem voluerit. IV. Statum naturæ integræ a statu naturæ lapsæ non distinxit. V. Utrum et quomodo liberum arbitrium regere putaverit bonos et pravos motus in animo suscitatos. VI. Æquiusnam sit ille spiritus adversus quem Paulus carnem ait concupiscere. VII. Utrum anima media inter spiritum et carnem dici possit. VIII. Quæritur utrum affectus aliqui boni ex carne naturaliter prodeant, et de lege naturæ. IX. Patres multi ex vi naturæ boni aliquid oriri posse senserunt. X. Origenes legi naturæ nimium tribuit. XI. Quemadmodum et legi Moysis. XII. Investigatur ejus sententia de gratiæ auxilio, quam hominibus a Deo imperitiri censuit, propter recte ante vitam gesta. XIII. Et in hac vita mortali. XIV. Gratiam excitantem non agnovit. XV. Perperam interpretata quibusdam Scripturæ locis in eam sententiam adductus est. XVI. Quæ merito reprehenduntur. XVII. Paucilla quædam in Origenis favorem colliguntur. XVIII. Utrum et quomodo perfectos homines posse non peccare ratus sit. XIX. Hujus dogmatis causa vapulat. XX. Utrum post acceptam gratiam iteratæ pœnitentiæ locum non superesse autumarit. XXI. Utrum præceptis divinis morem geri non posse senserit. XXII. Suppetiæ feruntur Origeni. XXIII. Utrum affirmaverit homines sola fide justos effici. XXIV. Investigatur ejus dogma de peccato originis, et fine baptismi. XXV. Quid ipsi de prædestinatione placuerit, disputatur. XXVI. Hic quoque nonnullis pœnas dat, sed in aliquibus juvatur. 919", None, 0.923),
        ("PG017:entry:063", "pg017_quaestio_viii_astris_975", "heading_group", "PG017:node:007", 678, 975, "QUAESTIO VIII. — De astris", "QUAESTIO VIII. — De astris. I. Utrum astra animata esse, Deum cognoscere et precari, peccare posse, judicatum iri, ac salutem sperare et posse consequi Origenes affirmaverit. II. A multis hic arguitur. III. Nec defensione tamen caret. IV. Viam ipsi ad id credendum straverunt antiqui. V. Utrum sideribus futurorum significationem a Deo impressam putaverit. VI. An tres priores creationis dies sine sole, luna et stellis non fuisse decreverit. 975", None, 0.975),
        ("PG017:entry:064", "pg017_quaestio_ix_resurrectione_980", "heading_group", "PG017:node:007", 678, 980, "QUAESTIO IX. — De resurrectione mortuorum", "QUAESTIO IX. — De resurrectione mortuorum. I. Status quæstionis proponitur. II. Doctrina Origenis de resurrectione mortuorum enucleatur. III. Utrum resurrectionem absolute sustulerit. IV. Hoc crimine purgatur. V. Utrum corporis resurrectionem admiserit, carnis inficiatus sit. VI. In eo etiam hæsitat, et multorum præterea assensu gaudet. VII. Utrum et quomodo corpora in resurrectione mutatum iri existimaverit. VIII. Hic quoque Patrum aliquot consensione se tuetur. IX. An corpora in resurrectione sphærica futura autumaverit. X. Utrum impios affirmaverit neutiquam in vitam redituros. XI. Unde suam de resurrectione doctrinam hauserit. XII. Nonnulla ad ejus excusationem præter superiora colliguntur. XIII. Quo sensu dixerit peccatorum causam ad corpus referri. 980", None, 0.973),
        ("PG017:entry:065", "pg017_quaestio_x_judicio_996", "heading_group", "PG017:node:007", 678, 996, "QUAESTIO X. — De postremo judicio", "QUAESTIO X. — De postremo judicio. I. Exploratur placitum Origenis de rebus supremo Dei arbitrio judicandis. II. Et de ratione ac loco judicii postremi. 996", None, 0.999),
        ("PG017:entry:066", "pg017_quaestio_xi_poenis_premiis_998", "heading_group", "PG017:node:007", 678, 998, "QUAESTIO XI. — De pœnis et præmiis", "QUAESTIO XI. — De pœnis et præmiis. I. Origenis opinio ex ipsis principiis summam deducitur. II. Eadem deinde fusius ipsius verbis declaratur. Ac primum de pœnis. Omnes homines igne examinandos fore sensit. III. Deinde in varia loca pro meritis dimittendos. IV. Ac torquendos. V. Quales pœnas futuras arbitratus sit; quid ignem æternum. VI. Æquas præter ignem pœnas mortuis infligendas decreverit. VII. Ex his variæ adversus eum criminationes. VIII. Sed sui tamen ei non desunt astipulatores, suaque defensio. IX. De præmiis agitur, et in quo positam sanctorum beatitatem voluerit Origenes, explicatur. X. Recensentur varii gradus per quos ad eam perveniri sensil. XI. De loco, quo animas sanctorum conquiescere docuit, disputatur : nempe de cœlo novo et terra nova. XII. Ac de paradiso. XIII. Unde hæc transtulerit, exquiritur. XIV. Hinc in Patrum multorum reprehensiones incurrit. XV. Sed aliqua tamen in ejus excusationem afferri possunt. XVI. Et quomodo pœnis damnatorum finem impositum iri, et omnia unum in Deo per ἀποκατάστασιν futura ratus sit. XVII. Non alias pœnas quam purgatorias admisit. XVIII. Quid sit, juxta Origenem, novissima inimica mors, deque dæmonum pœnis quæritur. XIX. Utrum beatitatis quoque futuræ spatium terminis circumscripserit. XX. Horum dogmatum fontes reserantur. XXI. Propter illa a Patribus graviter reprehensus est. XXII. An fuerit in hæresi Chiliastarum. XXIII. Nonnulla Origeni defendendo adducuntur. XXIV. Nonnunquam solius diaboli pœnas fore æternas asseverat. XXV. Ab Origenianis super pœnis damnatorum opinione Patres aliquot non multum recedunt. XXVI. Investigatur significatio vocis αἰώνιος. 998", None, 0.999),
        ("PG017:entry:067", "pg017_quaestio_xii_mundo_1017", "heading_group", "PG017:node:007", 678, 1017, "QUAESTIO XII. — De mundo, paradiso terrestri et Adamo", "QUAESTIO XII. — De mundo, paradiso terrestri et Adamo. I. Duo de mundo quæri possunt. II. Primum, utrum et quo sensu dixerit Origenes mundum propter rationales naturas fuisse a Deo conditum. III. Ita ut existimasse eum Patres aliqui testificantur. IV. Alterum, utrum plures vel fuisse, vel esse, vel fore mundos existimaverit. V. Quemadmodum ipsi a Patribus quibusdam objectum est. VI. Quanquam ejus causam nonnulla adjuvant. VII. Exploratur Origenis sententia de constitutione paradisi terrestris. VIII. Et de scorteis tunicis, quibus post peccatum Adamus indutus est. IX. Quæritur utrum dixerit Adamum per peccatum similitudinem Dei amisisse. X. Et utrum homines præadamitas extitisse sibi finxerit. 1017", None, 0.956),
        ("PG017:entry:068", "pg017_quaestio_xiii_allegorica_1063", "heading_group", "PG017:node:007", 678, 1063, "QUAESTIO XIII. — De allegorica Scripturæ interpretatione", "QUAESTIO XIII. — De allegorica Scripturæ interpretatione. I. Allegoriis nimis indulsisse Origenem Patres clamant. II. Varia afferuntur loca in quibus litteram visus est pessumdedisse. III. A quibus morem hunc interpretandæ Scripturæ acceperit, exquiritur. IV. Idem aliquando suam litteræ dignitatem servat. V. Refelluntur Eustathii Antiocheni adversus Origenis allegorias querelæ. 1063", None, 0.999),
        ("PG017:entry:069", "pg017_quaestio_xiv_quaestiunculae_1074", "heading_group", "PG017:node:007", 678, 1074, "QUAESTIO XIV. — Quæstiunculæ aliquot quasi per saturam complexa", "QUAESTIO XIV. — Quæstiunculæ aliquot quasi per saturam complexa. I. Quæritur Origenis sententia de ligandi et solvendi potestate sacerdotibus concessa. II. Excutitur ejusdem de Eucharistia opinio. III. Quædam ipsius de matrimonio dogmata notantur. IV. Utrum magicis artibus faverit, exploratur. V. Et de engastrimyho quid statuerit. VI. Equid de mendacio. VII. Et de jurejurando. 1074", None, 0.817),
        ("PG017:entry:070", "pg017_generale_doctrinae_examen_1094", "heading_group", "PG017:node:007", 678, 1094, "CAP. III. — Generale Origenianæ doctrinæ examen", "CAP. III. — Generale Origenianæ doctrinæ examen. I. Iniqua fere pro Origene, vel contra Origenem judicia. II. Recensentur ipsius defensores. III. Multa ad Origeniani nominis oppugnatores confutandos generatim proponuntur. Mutua criminationum repugnantia. IV. Origenianarum errorum Origeni afficti. V. Rufini interpretis perfidia. VI. Patrum falsis criminibus appetitorum exemplum. VII. Philocalia a Gregorio Theologo et Basilio ex Origenis scriptis excerpta. VIII. Allegoricæ ipsius interpretationes. IX. Frustra hæreseon fons appellatus est. X. Librorum ipsius depravatio. XI. Multa quoque ad ipsum excusandum in universum adducuntur : perpetua ipsius in proponendis sententiis hæsitatio; ejusdem modestia. XII. Constans hæreseon insectandi studium. XIII. Nimia in scribendo festinatio. XIV. Theologicæ quæstiones ipsius temporibus nondum satis excussæ, nec per Ecclesiam definitæ. XV. Eum tandem temere dictorum pœnituit. XVI. Immerito Rufinum reprehendit Hieronymus propter inscriptum Origenis Apologiæ Pamphili nomen. XVII. In multis peccasse Origenem fatendum est. XVIII. Quo numero libri Origenis habendi sint, disputatur. XIX. Et utrum inter hæreticos ponendus ipse sit. 1094", None, 0.999),
        ("PG017:entry:071", "pg017_sectio_prima_fortuna_1115", "heading_group", "PG017:node:009", 679, 1115, "SECTIO PRIMA. — Procellarum adversus Origenis doctrinam", "SECTIO PRIMA. — I. Procellarum adversus Origenis elamnum vinentis doctrinam concitarum series summam repetitur. II. Sedata Origenis morte odia non multo post recrudescunt. III. Pierius Alexandrinus Origenes junior dictus. Theognostum Alexandrinum inter Origenis asseclas ponit Photius. IV. Arianis temporibus Origenis doctrina denuo impugnari cœpta est ab orthodoxis, quod ejus sibi patrocinium asciscerent Ariani. V. Quam tamen benigne interpretareri maluerunt Athanasius, Basilius et Gregorius Nazianzenus. VI. Origenianas partes tuentur Hilarius, Euzoius, Titus Bostrenus, Didymus, Ambrosius, Eusebius Vercellensis, Victorinus Petabionensis, Gregorius Nyssenus, et ipse etiam Hieronymus, qui subinde tamen mutavit consilium; quas oppugnant Theodorus Mopsuestenus et Apollinaris. VII. Duplex Origenistarum genus, orthodoxorum et heterodoxorum. VIII. Origenistæ heterodoxi ex Ægypti monasteriis fere prodierunt. IX. Joannis Jerosolymitani cum Epiphanio et Hieronymo dissensio. X. Et Hieronymi cum Rufino. XI. Quæ in gratiam reducere studuerunt Archelaus et Theophilus. XII. Scribit ad Pammachium Hieronymus adversus errores Joannis Jerosolymitani. XIII. Et ad Theophilum. XIV. Augustinum quoque per litteras ab Origenismo absterret. XV. Romam revertitur Melania cum comite Rufino. XVI. [ui] cum Apologiam Pamphili, libellum De adulteratione operum Origenis, et ejus libros περὶ ἀρχῶν in Urbe publicasset, a Marcella repressus est. XVII. Certior de his factus Hieronymus libros περὶ ἀρχῶν interpretatur. XVIII. Tres libros contra Hieronymum scribit Rufinus. XIX. Anastasius papa damnat errores Origenis. XX. Et ipsum Rufinum. XXI. Hieronymus et Rufinus scriptis mutuo se lacessunt. XXII. Mortem oppetunt Melania senior et Rufinus. XXIII. Origenismi tradux Pelagianismus, Pelagianismi Nestorianismus. XXIV. Renascitur in Hispania Origenismus, sed editur opera Augustini; penitusque tandem in Occidente profligatur. 1115", None, 0.999),
        ("PG017:entry:072", "pg017_sectio_secunda_1149", "heading_group", "PG017:node:009", 679, 1149, "SECTIO II. — Theophilus Alexandrinus monachos quosdam Nitrienses vexat", "SECTIO II. — I. Theophilus Alexandrinus monachos quosdam Nitrienses vexat. II. Et affixo ipsis Origenismo Origenistas una insectatur. III. Aliæ dissidii hujus causæ proferuntur. Synodus Alexandrina Origenistas damnat, quos Theophilus ex Ægypto deturbat. IV. Æquum de his concertationibus Posthumiani judicium. V. Theophilii factum approbant Anastasius, Epiphanius et Hieronymus. VI. Utris pius adhibendum in hac historia fidei, Palladio, Socrati, Sozomeno, Georgio Alexandrino, anonymæ Vitæ Chrysostomi scriptori, et Simeoni Metaphrastæ, an Epiphanio et Hieronymo, disquiritur. VII. Theophili synodicam epistolam et Paschales quatuor convertit Hieronymus. VIII. Quæritur utri prius Constantinopolim profecti sint [Origenistæ, an Theophili legati. IX. Origenistas benigne excipiunt Joannes Chrysostomus et Eudoxia. X. Synodum in Cypro adversus Origenistas cogit Epiphanius. XI. Theophilienses legati et Origenistæ apud imperatorem mutuo se accusant. XII. Epiphanius Constantinopolim appellit, et Chrysostomum male habet. XIII. Altercatur cum Eudoxia, deinde Cyprum repetens fato concedit. XIV. Rei gestæ summa repetitur ex Polybio teste oculato. XV. Constantinopolim advent Theophilus, et conciliabulum cogit ad Quercum. XVI. Accusatus, et a Quercum Chrysostomus. XVII. Actorum pseudosynodi ad Quercum habitæ fides exploratur. XVIII. Crimine liberantur Nitrienses, et in gratiam cum Theophilo redeunt. XIX. Exauctorantur Chrysostomus, et in exsilio diem multo post obiit. XX. In Chrysostomi defuncti nomen grassatur Theophilus, renitente Isidoro Pelusiota. XXI. Chrysostomum prosequitur laudibus Synesius, probris Hieronymus. XXII. Diem claudit : succedit Cyrillus, et avunculi inimicitias persequitur, sed ab Isidoro reprehensus factum mutat. Scribit adversus Origenem Hammon Adrianopolitanus. XXIV. Joannis Hierosolymitani, et Hieronymi obitus. Male audit Origenis doctrina in Oriente et Occidente. XXV. Suos tamen fautores habet Philastrium, Theodoretum, Socratem, Sozomenum, Sidonium, auctorem Prædestinati, et Eutychetem. 1149", None, 0.954),
        ("PG017:entry:073", "pg017_sectio_tertia_1160", "heading_group", "PG017:node:009", 679, 1160, "SECTIO III. — Rursus e Palæstinæ monasteriis Origenismus emergit", "SECTIO III. I. Rursus e Palæstinæ monasteriis Origenismus emergit. II. Origenistas apud Justinianum accusat Sabas. III. Origenismum passim spargunt Nonnus et Leontius. IV. Et aliquanto post Nonni discipuli Theodorus et Domitianus Origenismi causa crudelia multa perpetrant. V. Sarabaitæ unde dicti. VI. Origenistarum gesta imperatori renuntiaturus Gelasius lauræ præfectus, ad ejus aditu excluditur; huic in reditu defuncto succedit Georgius Origenista; Georgio Cassianus, Cassiano Conon, orthodoxo orthodoxo. Antiochena synodus. VII. Epistolam ad Menam adversus Origenem scribit Justinianus. VIII. Cujus epistolæ summa repræsentatur. IX. Theopaschitæ somnium Joannes Philoponus Origenizat. X. Trium capitulorum causa aliquanto post ventilari cœpta est. XI. Edictum adversus tria capitula promulgat imperator, unde schismata multa oriuntur. XII. Reflorescit Origenismus in Palæstina. Synodus quinta celebratur. XIII. Damnata tria capitula. Hinc magnis motibus Ecclesia conturbatur. XIV. Utrum in quinta synodo de Origene actum sit. XV. Quid in ea adversus Origenem constitutum sit, exponitur. XVI. Nova in Origenismum facta odii accessio. XVII. Quæ prohibentibus subinde annis paulatim obsolevit. Infensos sibi tamen nonnullos identidem Origenes nactus est. XVIII. Græculos præcipue : a Latinis vero benignius exceptus est. XIX. Oraculum divinitus editum S. Mechtildi de salute Origenis. Guidonis et Platinæ de Origene judicium. Hujus causam tuentur Joannes Picus, et Joannes Nauclerus. Favet ipsi quoque Joannes Tritemius, sed citra errorum ipsius defensionem. XX. Primus Origenis Opera prelo committit Jacobus Merlinus, et pro eo Apologiam scribit; unde Masæi et Bedæ conviciis appetitur. XXI. Defendunt Origenem Erasmus, Ferrarius, Sixtus Senensis, Genebrardus et Halloixius : eamdem impugnant Baronius et Bellarminus, Lutherus, et Beza : incerta Sculteti ratio : æquiores in eum se gerunt Espencæus, Possevinus, Gretserus, et Rinetus. 1160", None, 1.0),
        ("PG017:entry:074", "pg017_exegeticis_1185", "heading_group", "PG017:node:008", 679, 1185, "CAPUT PRIMUM. — Ubi disputatur in universum de Origenis scriptis", "CAPUT PRIMUM. — Ubi disputatur in universum de Origenis scriptis. I. Stylus Origenis redundans et incultus. II. Plus æquum ac dictanti aderant notarii : totidem librarii et pellæ opera ejus nitidius exarabant. III. Quicunque supersunt Origenis libri, an vitiati et corrupti sint. IV. Sua quibusque Origenis operibus auctoritas, suus valor assignatur. V. De Origenianarum script ionum numero disseritur. VI. Quibus Scripturæ editionibus ull solitus fuerit Origenes aperitur. 1185", None, 0.997),
        ("PG017:entry:075", "pg017_cap_ii_exegeticis_1189", "heading_group", "PG017:node:008", 679, 1189, "CAP. II. — De Origenis exegeticis et ἑρμηνευτικῶν", "CAP. II. — De Origenis exegeticis et ἑρμηνευτικῶν. 1189", None, 0.489),
        ("PG017:entry:076", "pg017_sectio_prima_hermeneutica_1189", "heading_group", "PG017:node:008", 679, 1189, "SECTIO PRIMA. — Ubi varia Origenianorum ἑρμηνευτικὰ genera percensentur", "SECTIO PRIMA. — Ubi varia Origenianorum ἑρμηνευτικὰ genera percensentur. I. Scripturam sacram Ambrosii rogatu Origenes interpretatus est. Confulentur nonnulli, qui primum Scripturæ sacram interpretem ipsum fuisse volunt. II. Dividuntur Origenis exegetica in scholia, homilias et tomos. III. De quibus sigillatim quæritur. IV. Quid sint ὁμιλίαις disputatur. V. Ἑρμηνευτικὰ pars ἱστορική. Epistolas inter syntagmata collocamus. VI. Origenes historicum, mysticum et moralem sensum perscrutari solet. VII. Cur minor in Novo quam in Veteri Testamento ab Ambrosio dictus sit, investigatur. 1189", None, 0.439),
        ("PG017:entry:077", "pg017_sectio_secunda_1196", "heading_group", "PG017:node:008", 679, 1196, "SECTIO II. — Ubi singula Origenis ἑρμηνευτικῶν enumerantur", "SECTIO II. — Ubi singula Origenis ἑρμηνευτικῶν, quorum ad nos pervenit notitia, enumerantur. I. Enumerantur Origenis exegetica in Pentateuchum. II. In Josue, Judices, libros Regum, Paralipomenon, Esdræ et Job. III. In Psalterium. IV. In Proverbia, Ecclesiasten et Canticum. V. In majores prophetas quatuor. VI. In minores duodecim. VII. Item in Matthæum, Lucam, Joannem et Ac a apostolorum. VIII. Epistolas Pauli, et Apocalypsim. 1196", None, 0.934),
        ("PG017:entry:078", "pg017_sectio_tertia_1207", "heading_group", "PG017:node:008", 679, 1207, "SECTIO III. — De Origenis exegeticis quæ supersunt", "SECTIO III. — De Origenis exegeticis quæ supersunt, deque vetustis ipsorum interpretationibus. I. De exegeticis in Genesim. II. Exodum. III. Leviticum. IV. Et Numeros. V. In Josue, Judices, Reges. VI. Psalmos et Proverbia. VII. Canticum canticorum. VIII. Isaiam. IX. Jeremiam. X. Ezechielem. XI. Et Oseam. XII. In Matthæum. XIII. Lucam. XIV. Joannem et Acta apostolorum. XV. Et in Epistolas ad Romanos, Colossenses, Titum et Hebræos. 1207", None, 1.0),
        ("PG017:entry:079", "pg017_sectio_quarta_1229", "heading_group", "PG017:node:008", 679, 1229, "SECTIO IV. — De Origenis Tetraplis, Hexaplis et Octaplis", "SECTIO IV. — De Origenis Tetraplis, Hexaplis et Octaplis. I. Proponitur Epiphanii sententia de Origene Tetraplas, Hexaplis et Octaplis. II. Asseritur propositus duarum Hebraicarum columnarum in Hexaplis et Octaplis situs. III. Quæritur quare in Tetraplis, Hexaplis et Octaplis Theodotionem Symmachus præcesserit. IV. Investigatur mens Eusebii de Tetraplis, Hexaplis et Octaplis. V. Unum et idem opus fuerunt Hexapla et Octapla. VI. Ex superioribus recentiorum multorum errores arguuntur. VII. Editionem Interpretum Septuaginta, quæ in Hexaplis habebatur, asteriscis, obelis, lemniscis et hypolemniscis Origenes distinxit. Et præterea quæ in Tetraplis scholia adjecit. VIII. Editionem τῶν O' Hexaplis intextam emendarunt Eusebius et Pamphilus, et primi seorsum vulgaverunt. Inde triplex illo tempore editio τῶν O', Origeneana, Eusebiana, et καινὴ. IX. Eadem circiter tempestate καινὴ resarcit Lucianus, resarcit et Hesychius. Hinc quintuplex καινὴ editio. X. Suas quoque editiones asteriscis et obelis discriminant Lucianus, et Hesychius; ut et suam τῶν O' interpretationem Hieronymus. Inquinatæ sunt et vitiatae hodiernæ omnes τῶν O' editiones. 1229", None, 1.0),
        ("PG017:entry:080", "pg017_cap_iii_syntagmata_1231", "heading_group", "PG017:node:008", 679, 1231, "CAP. III. — Origenis syntagmata", "CAP. III. — Origenis syntagmata. 1231", None, 0.503),
        ("PG017:entry:081", "pg017_sectio_prima_syntagmata_1231", "heading_group", "PG017:node:008", 679, 1231, "SECTIO PRIMA. — Singula Origenis syntagmata", "SECTIO PRIMA. — Singula Origenis syntagmata, quorum memoria superest, enumerantur. I. Recensentur Origenis syntagmata ad Eusebio commemorata. II. Ex iis aliqua expenduntur accuratius; ac primum libri De resurrectione. III. Ἑρμηνευτικά. IV. De martyrio. V. Dialogi. VI. Epistolæ. VII. Interpretatio Hebraicorum nominum Novi Testamenti. VIII. Liber De oratione. IX. Disputationes adversus hæreticos, in iisque Parvus Labyrinthus. X. Quæritur quid sint Origenis Monobiblia, et quid ipsius pro se Apologia a Vincentio [Bellovacensi commemorata. XI. Philocaliam quoque in Origenianorum operum censu ponimus. 1231", None, 0.808),
        ("PG017:entry:082", "pg017_appendix_1261", "heading_group", "PG017:node:008", 680, 1261, "APPENDIX. Libri Origeni falso vel dubitanter ascripti", "APPENDIX. Libri Origeni falso vel dubitanter ascripti. I. Unde factum sit ut Origenis nomen pleræque sibi scriptiones falso ascriverint, aperitur. II. De tribus libris in Job, deque vetusto ipsorum interprete. III. De posteriore in Job commentario. IV. De commentario in Marcum. V. Homiliis in diversos. VI. Homilia quæ in codice Vaticano inscribitur, τῆς σωζομένης πλάσεως. VII. Scholiis in Orationem Dominicam, et Canticis B. Virginis, Zachariæ et Simeonis. VIII. De Lamento Origenis. IX. Dialogo De orthodoxa fide. X. Alio quodam vetusto dialogo. XI. Libellis De hæresibus. XII. De singularitate clericorum. XIII. De astrologia, et de Breviario, et sermone de Catechesi. 1271", None, 0.984),
        ("PG017:entry:083", "pg017_cap_iv_quo_ordine_1261", "heading_group", "PG017:node:008", 680, 1261, "CAP. IV. — Quo ordine, quibus temporibus Origenis libri lucubrati sint exploratur", "CAP. IV. — Quo ordine, quibus temporibus Origenis libri lucubrati sint exploratur. I. Variis Origenis scriptio- nibus suus ordo, sua tempora ex Eusebio assignantur. II. Quo tempore Tetrapla, Hexapla et Octapla concinnave- rit, investigatur. III. Notantur nonnulla circa ordinem ac tempus exegeticon, ac syntagmatum ipsius quotumdam. IV. Distinguuntur ejusdem homiliæ extemporales, et in otio elaboratæ. 1261", None, 0.984),
        ("PG017:entry:084", "pg017_excerptum_bulli_1383", "lemma", "PG017:node:008", 680, 1383, "EXCERPTUM EX GEORGII BULLI PRESB. ANGLIC. DEFENSIONE FIDEI NICÆNÆ", "EXCERPTUM EX GEORGII BULLI PRESB. ANGLIC. DEFENSIONE FIDEI NICÆNÆ. Origenis doctrinam de Filii Dei vera divinitate omnino catholicam et NICÆNÆ fidei plane consonam fuisse, præcipue ex indubitato ejus, et maxime incorrupto, atque ab ipso jam sene accuratiori diligentia elucubrato opere contra Celsum fuse et luculenter ostenditur. 1383", None, 0.989),
    ]

    entries = []
    refs = []
    for order, (entry_key, helper_id, entry_kind, parent_node, anchor_seq, page_ref, lemma_raw, entry_raw, context_raw, confidence) in enumerate(record_lines, start=1):
        info = helper_best(helper, helper_id)
        entries.append(
            {
                "entry_key": entry_key,
                "section_key": section_key,
                "parent_node_key": parent_node,
                "entry_order": order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma_raw,
                "lemma_display": lemma_raw,
                "lemma_norm": norm(lemma_raw),
                "lemma_sort": norm(lemma_raw),
                "entry_raw": entry_raw,
                "context_raw": context_raw,
                "heading_letter": None,
                "inferred_printed_page": page_ref,
                "section_start_file": section_start_file,
                "editorial_anchor_file": TOC_FILE_MAP[anchor_seq],
                "target_file_best": info["file"],
                "confidence": confidence,
                "raw_json": {
                    "helper_entry_id": helper_id,
                    "helper_status": info["status"],
                    "helper_candidate_role": info["candidate_role"],
                    "helper_probability": info["probability"],
                    "helper_best_candidate": {
                        "file": info["file"],
                        "file_seq": info["file_seq"],
                        "reason_summary": info["reason_summary"],
                    },
                    "editorial_anchor_seq": anchor_seq,
                    "section_kind": "ordo_rerum",
                },
            }
        )
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": 1,
                "ref_kind": "editorial_page",
                "ref_raw": str(page_ref),
                "page_ref_raw": str(page_ref),
                "page_ref_int": page_ref,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": None,
                "range_end_raw": None,
                "target_file": info["file"],
                "target_file_probability": info["probability"],
                "section_start_file": section_start_file,
                "editorial_anchor_file": TOC_FILE_MAP[anchor_seq],
                "confidence": confidence,
                "raw_json": {
                    "helper_entry_id": helper_id,
                    "helper_status": info["status"],
                    "helper_candidate_role": info["candidate_role"],
                    "helper_reason_summary": info["reason_summary"],
                },
            }
        )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PG017",
            "collection": "PG",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PG017",
            "notes": [
                "Recovered the Ordo Rerum block at the end of the volume and serialized its page-anchored contents entries.",
                "Pagination in the OCR is drifted across the closing pages; helper evidence was used conservatively to anchor target files.",
            ],
        },
        "sections": [section],
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "recovered",
            "entries_status_reason": "Recovered the final Ordo Rerum / table-of-contents block from the provided tail window and preserved the visible page-anchored entries with helper-backed target files.",
            "evidence_files": [
                TOC_FILE_MAP[676],
                TOC_FILE_MAP[677],
                TOC_FILE_MAP[678],
                TOC_FILE_MAP[679],
                TOC_FILE_MAP[680],
            ],
        },
        "notes": [
            "The payload models the volume's closing Ordo Rerum as one section with structural nodes for the main work headings.",
            "Long section titles and question headings remain in OCR literal form; page references are kept separate from the physical OCR file anchors.",
            "The closing page contains both TOC material and the final excerpt/finis material; helper evidence preserved that drift explicitly.",
        ],
    }

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
