"""Build the OCR-verified PL083 section-006 chunk fragment."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path("/homessddata/Projects/pdfocr")
SOURCE_ROOT = ROOT / "teste/PL083/text"
OUTPUT = ROOT / "data/index_intermediate_payloads/PL083/chunks/section_006_part_001.json"
CHUNK_ID = "PL083:chunk:006:001"
SECTION_ID = "PL083:candidate-section:006"
SECTION_KEY = "PL083:section:tome_vii:ordo_rerum:001"
FINGERPRINT = "78ef708e375088d93f056f970d0b8e4bae8ff948299173efd3488c5f8827752c"
FILE_693 = str(SOURCE_ROOT / "e2804b08-0ec4-4919-ad0c-00f9162d567c-693.txt")
FILE_694 = str(SOURCE_ROOT / "e2804b08-0ec4-4919-ad0c-00f9162d567c-694.txt")


PAGE_693 = [
    ("DE NATURA RERUM.", "work_entry"),
    ("Præfatio.", "subwork_entry"),
    ("CAPUT PRIMUM. — De diebus. 963", "chapter_entry"),
    ("CAP. II. — De nocte. Ibid.", "chapter_entry"),
    ("CAP. III. — De hebdomada. 967", "chapter_entry"),
    ("CAP. IV. — De mensibus. Ibid.", "chapter_entry"),
    ("CAP. V. — De concordia mensium. 968", "chapter_entry"),
    ("CAP. VI. — De annis. 971", "chapter_entry"),
    ("CAP. VII. — De temporibus. Ibid.", "chapter_entry"),
    ("CAP. VIII. — De solstitio et æquinoctio. 974", "chapter_entry"),
    ("CAP. IX. — De mundo. 977", "chapter_entry"),
    ("CAP. X. — De quinque circulis Ibid.", "chapter_entry"),
    ("CAP. XI. — De partibus mundi. 978", "chapter_entry"),
    ("CAP. XII. — De celo. 979", "chapter_entry"),
    ("CAP. XIII. — De septem planetis cœli et eorum conversi 981", "chapter_entry"),
    ("CAP. XIV. — De aquis quæ super cœlos sunt. 986", "chapter_entry"),
    ("CAP. XV. — De natura solis. 987", "chapter_entry"),
    ("CAP. XVI. — De quantitate solis et lunæ. Ibid.", "chapter_entry"),
    ("CAP. XVII. — De cursu solis. 989", "chapter_entry"),
    ("CAP. XVIII. — De lumine lunæ. 991", "chapter_entry"),
    ("CAP. XIX. — De lunae cursu. 992", "chapter_entry"),
    ("CAP. XX. — De eclipsi solis. 993", "chapter_entry"),
    ("CAP. XXI. — De lunae eclipsi. 994", "chapter_entry"),
    ("CAP. XXII. — De cursu stellarum. Ibid.", "chapter_entry"),
    ("CAP. XXIII. — De positione septem stellarum errantiun. 995", "chapter_entry"),
    ("CAP. XXIV. — De lumine stellarum. Ibid.", "chapter_entry"),
    ("CAP. XXV. — De lapsu stellarum. 997", "chapter_entry"),
    ("CAP. XXVI. — De dominibus astrorum. 998", "chapter_entry"),
    ("CAP. XXVII. — Utuntur sidera animalium flubent. 1000", "chapter_entry"),
    ("CAP. XXVIII. — De nocte. 1001", "chapter_entry"),
    ("CAP. XXIX. — De tonitruo. 1002", "chapter_entry"),
    ("CAP. XXX. — De arcu. 1003", "chapter_entry"),
    ("CAP. XXXI. — De nubibus. 1005", "chapter_entry"),
    ("CAP. XXXII. — De pluviis. 1003", "chapter_entry"),
    ("CAP. XXXIII. — Cap. XXXIV. — De nive. Ibid.", "chapter_entry"),
    ("CAP. XXXV. — De grandine. 1006", "chapter_entry"),
    ("CAP. XXXVI. — De ventis. Ibid.", "chapter_entry"),
    ("CAP. XXXVII. — De nominibus ventorum. 1007", "chapter_entry"),
    ("CAP. XXXVIII. — De signis tempestatis vel serenitatis. Ibid.", "chapter_entry"),
    ("CAP. XXXIX. — De pestilentia. 1009", "chapter_entry"),
    ("CAP. XL. — De Oceano. 1010", "chapter_entry"),
    ("CAP. XLI. — Cur mare non crescat. 1011", "chapter_entry"),
    ("CAP. XLII. — Quare mare salisam habeat aquam. 1012", "chapter_entry"),
    ("CAP. XLIII. — De Nilo. 1013", "chapter_entry"),
    ("CAP. XLIV. — De nominibus maris et fluminum. Ibid.", "chapter_entry"),
    ("CAP. XLV. — De positione terræ. 1014", "chapter_entry"),
    ("CAP. XLVI. — De terrae motu. 1015", "chapter_entry"),
    ("CAP. XLVII. — De monte Ætna. 1016", "chapter_entry"),
    ("CAP. XLVIII. — De partibus terræ. 1017", "chapter_entry"),
    ("CHRONICON.", "work_entry"),
    ("DE REGIBUS GOTHORUM, VANDALORUM, etc. 1017", "subwork_entry"),
    ("DE VIRIS ILLUSTRIBUS. 1057", "work_entry"),
    ("AD OPERA S. ISIDORI APPENDICES. 1107", "structural_entry"),
    ("APPENDIX PRIMA. — S. Ildefonso, Toletani episcopi, liber de vitiis illustrium scriptis. Ibid.", "appendix_entry"),
    ("APPENDIX II. — Archiepiscopi Toletani veteres. Ibid.", "appendix_entry"),
    ("APPENDIX III. — Carmena S. Isidori descriptia. 1113", "appendix_entry"),
    ("APPENDIX IV. — Chronica S. Wigbaldorum. 1115", "appendix_entry"),
    ("APPENDIX V. — Chronologia et series Gothicorum regum ex codice Regionumctano. 1115", "appendix_entry"),
    ("APPENDIX VI. — Expositio in Canticum canticorum. 1119", "appendix_entry"),
    ("APPENDIX VII. — De Conditione vitiorum et virtutum. 1131", "appendix_entry"),
    ("APPENDIX VIII. — Expositio in missæ. 1145", "appendix_entry"),
    ("APPENDIX IX. — Sententiarum liber quartus. 1157", "appendix_entry"),
    ("APPENDIX X. — Exhortatio humilitatis. Ex editione Piorezil. 1201", "appendix_entry"),
    ("APPENDIX XI. — Testimonia divinæ Scripturæ et Patrum. 1225", "appendix_entry"),
]

PAGE_694 = [
    ("CAPUT PRIMUM. — De uno Deo.", "chapter_entry"),
    ("CAP. II. — De distinctione personarum Patris, Filii, et Spiritus sancti.", "chapter_entry"),
    ("CAP. III. — De conversione et contempta sæculi.", "chapter_entry"),
    ("CAP. IV. — De timore Domini.", "chapter_entry"),
    ("CAP. V. — Juste judicandum.", "chapter_entry"),
    ("CAP. VI. — Benefaciendum pauperi.", "chapter_entry"),
    ("CAP. VII. — Non ambulandum in dolo.", "chapter_entry"),
    ("CAP. VIII. — De filio superbo.", "chapter_entry"),
    ("CAP. IX. — De thesauro.", "chapter_entry"),
    ("CAP. X. — De eleemosynis.", "chapter_entry"),
    ("CAP. XI. — De correptione et de superbia.", "chapter_entry"),
    ("CAP. XII. — De mansuetudine, de patientia et de humilitate.", "chapter_entry"),
    ("CAP. XIII. — De sapientia.", "chapter_entry"),
    ("CAP. XIV. — De conversatione juvenum.", "chapter_entry"),
    ("CAP. XV. — Non jurandudm, et non diceadum falsum, et non mentiendum.", "chapter_entry"),
    ("CAP. XVI. — Non fornicandum.", "chapter_entry"),
    ("CAP. XVII. — Item de continentia oris.", "chapter_entry"),
    ("CAP. XVIII. — Fugiendam ebrietatem.", "chapter_entry"),
    ("CAP. XIX. — Parvulos emendandos inscientes.", "chapter_entry"),
    ("CAP. XX. — Justas mensuras et pondera.", "chapter_entry"),
    ("CAP. XXI. — De voto.", "chapter_entry"),
    ("CAP. XXII. — Non fraudandum, et (de) non gloriando.", "chapter_entry"),
    ("CAP. XXIII. — Altiora non scrutari, neque audire hæreticos.", "chapter_entry"),
    ("CAP. XXIV. — De jejunio.", "chapter_entry"),
    ("CAP. XXV. — Homini exemplum formicæ vel apis sine pigritia laboris.", "chapter_entry"),
    ("CAP. XXVI. — De stulto.", "chapter_entry"),
    ("CAP. XXVII. — Iracundiam deponendam.", "chapter_entry"),
    ("CAP. XXVIII. — Fiec misti.", "chapter_entry"),
    ("CAP. XXIX. — Testimonia Patrum.", "chapter_entry"),
    ("CAP. XXX. — Testimonia de Sententiis Evagrii ad Fratres.", "chapter_entry"),
    ("CAP. XXXI. — Testimonia de libro sancti Martini, non illius Turonici episcopi, sed iste pater fuit monarchorum.", "chapter_entry"),
    ("CAP. XXXII. — De libro sancti Ambrosii.", "chapter_entry"),
    ("CAP. XXXIII. — Testimonia de libro Prosperi.", "chapter_entry"),
    ("CAP. XXXIV. — De jejunio.", "chapter_entry"),
    ("CAP. XXXV. — De libro Sententiarum.", "chapter_entry"),
    ("APPENDIX XIII. — Sermones.", "appendix_entry"),
    ("SERMO PRIMUS. — Ad carnes tollendas.", "subwork_entry"),
    ("SERMO II. — Contra Arrianos.", "subwork_entry"),
    ("SERMO III. — Homilia.", "subwork_entry"),
    ("SERMO IV. — De corpore et sanguine Domini in Pascha.", "subwork_entry"),
    ("APPENDIX XIII. — De ecclesiasticis dogmatibus.", "appendix_entry"),
    ("CAPUT PRIMUM. — De fide Trinitatis.", "chapter_entry"),
    ("CAP. II. — De incarnatione Christi.", "chapter_entry"),
    ("CAP. III. — Quod æternus Deus est Virgine natus sit.", "chapter_entry"),
    ("CAP. IV. — De Trinitate perfecta.", "chapter_entry"),
    ("CAP. V. — De homousio.", "chapter_entry"),
    ("CAP. VI. — De resurrectione.", "chapter_entry"),
    ("CAP. VII. — Resurrectio omnium communis.", "chapter_entry"),
    ("CAP. VIII. — De judicio vivorum et mortuorum.", "chapter_entry"),
    ("CAP. IX. — Peraccio judicio non expectanda restitutio.", "chapter_entry"),
    ("CAP. X. — Creatio mundi. Angelorum creatio.", "chapter_entry"),
    ("CAP. XI. — Solus Deus incorporeus.", "chapter_entry"),
    ("CAP. XII. — Omnis creatura corporea.", "chapter_entry"),
    ("CAP. XIII. — Intellectuales naturæ immortales.", "chapter_entry"),
    ("CAP. XIV. — Anima hominis quomodo creatur.", "chapter_entry"),
    ("CAP. XV. — Quod duæ animæ non sunt in homine.", "chapter_entry"),
    ("CAP. XVI. — Anima hominis immortalis.", "chapter_entry"),
    ("CAP. XVII. — Animae brutorum.", "chapter_entry"),
    ("CAP. XVIII. — Infunditur anima.", "chapter_entry"),
    ("CAP. XIX. — Duæ substantiae hominis.", "chapter_entry"),
    ("CAP. XX. — Anima et spiritus idem.", "chapter_entry"),
    ("CAP. XXI. — De libero arbitrio.", "chapter_entry"),
    ("CAP. XXII. — Gratia.", "chapter_entry"),
    ("CAP. XXIII. — Nemo bonus per se.", "chapter_entry"),
    ("CAP. XXIV. — Perseverantia in bonis a Deo.", "chapter_entry"),
    ("CAP. XXV. — Liberum arbitrium.", "chapter_entry"),
    ("CAP. XXVI. — Meritis.", "chapter_entry"),
    ("CAP. XXVII. — Quidquid boni ex Deo.", "chapter_entry"),
    ("CAP. XXVIII. — Gratia vis.", "chapter_entry"),
    ("CAP. XXIX. — Intelligentia ex gratia.", "chapter_entry"),
    ("CAP. XXX. — Preces sacerdotum.", "chapter_entry"),
    ("CAP. XXXI. — Exorcismus et sufflatio.", "chapter_entry"),
    ("CAP. XXXII. — Gratia et liberum arbitrium.", "chapter_entry"),
    ("CAP. XXXIII. — Adam primo immortalis.", "chapter_entry"),
    ("CAP. XXXIV. — De baptizandis infantibus.", "chapter_entry"),
    ("CAP. XXXV. — Omnes vere sumus peccatores.", "chapter_entry"),
    ("CAP. XXXVI. — Omnibus orandum pro dimittendis peccatis.", "chapter_entry"),
    ("CAP. XXXVII. — Dimittuntur debita etiam sanctis.", "chapter_entry"),
    ("CAP. XXXVIII. — Lapsus Adæ.", "chapter_entry"),
    ("CAP. XXXIX. — Peccatum Adæ in posterus.", "chapter_entry"),
    ("CAP. XL. — Gratia.", "chapter_entry"),
    ("CAP. XLI. — Voluntas bona a Deo.", "chapter_entry"),
    ("CAP. XLII. — Gratia operator omnia.", "chapter_entry"),
    ("CAP. XLIII. — Gratia omnis deberi.", "chapter_entry"),
    ("CAP. XLIV. — Hominem nihil posse sine Deo.", "chapter_entry"),
    ("CAP. XLV. — Solus Deus trahit hominem.", "chapter_entry"),
    ("CAP. XLVI. — Liberum arbitrium.", "chapter_entry"),
    ("CAP. XLVII. — Gratia.", "chapter_entry"),
    ("CAP. XLVIII. — An gratia sit natura.", "chapter_entry"),
    ("CAP. XLIX. — Repetitio superiorum.", "chapter_entry"),
    ("CAP. L. — Voluntas hominis post baptismum.", "chapter_entry"),
    ("CAP. LI. — Initium meritiorum unde.", "chapter_entry"),
    ("CAP. LII. — Qui sint habendi pro baptizatis.", "chapter_entry"),
    ("CAP. LIII. — De Eucharistia.", "chapter_entry"),
    ("CAP. LIV. — De poenitentia.", "chapter_entry"),
    ("CAP. LV. — De divinis promissionibus.", "chapter_entry"),
    ("CAP. LVI. — Quod nullus salvetur, nisi Deo auctore.", "chapter_entry"),
    ("CAP. LVII. — Quod malum non sit a Deo.", "chapter_entry"),
    ("CAP. LVIII. — Nihil non mutabile præter Deum.", "chapter_entry"),
    ("CAP. LIX. — Stabilitas in angelis.", "chapter_entry"),
    ("CAP. LX. — Natura nihil est malum.", "chapter_entry"),
    ("CAP. LXI. — Felicitas angelorum.", "chapter_entry"),
    ("CAP. LXII. — Libera voluntas angelorum.", "chapter_entry"),
    ("CAP. LXIII. — Nupitiæ.", "chapter_entry"),
    ("CAP. LXIV. — Continentia.", "chapter_entry"),
    ("CAP. LXV. — Virginitas.", "chapter_entry"),
    ("CAP. LXVI. — Ciborum delectus.", "chapter_entry"),
    ("CAP. LXVII. — Nuptiæ et cibis.", "chapter_entry"),
    ("CAP. LXVIII. — Heretia et absentia.", "chapter_entry"),
    ("CAP. LXIX. — Beatam Virginem semper fuisse virgineam.", "chapter_entry"),
    ("CAP. LXX. — De elementis mundi.", "chapter_entry"),
    ("CAP. LXXI. — De facultatibus organodis.", "chapter_entry"),
    ("CAP. LXXII. — Impedimenta clericatus.", "chapter_entry"),
    ("CAP. LXXIII. — De reliquiis sanctorum.", "chapter_entry"),
    ("CAP. LXXIV. — De catechumenis defunctis, et martyribus non baptizatis.", "chapter_entry"),
    ("CAP. LXXV. — Qualiter Eucharistia offeratur.", "chapter_entry"),
    ("CAP. LXXVI. — De humana carne.", "chapter_entry"),
    ("CAP. LXXVII. — Resurrectio.", "chapter_entry"),
    ("CAP. LXXVIII. — Animæ sanctorum ante passionem Domini.", "chapter_entry"),
    ("CAP. LXXIX. — Animæ sanctorum post ascensionem.", "chapter_entry"),
    ("CAP. LXXX. — De peccatorum venia.", "chapter_entry"),
    ("CAP. LXXXI. — Quod diabolum lætant cogitationes.", "chapter_entry"),
    ("CAP. LXXXII. — Cogitationes.", "chapter_entry"),
    ("CAP. LXXXIII. — Dæmones quomodo occupent homines.", "chapter_entry"),
    ("CAP. LXXXIV. — De signis et virtutibus.", "chapter_entry"),
    ("CAP. LXXXV. — Quod nullus signis sanctus sit.", "chapter_entry"),
    ("CAP. LXXXVI. — Sacci quoque peccatore.", "chapter_entry"),
    ("CAP. LXXXVII. — Pascha.", "chapter_entry"),
    ("CAP. LXXXVIII. — Imago Dei anima.", "chapter_entry"),
    ("APPENDIX XIV. — Sententiae differentiarium de vita æque contemplativa.", "appendix_entry"),
    ("APPENDIX XV. — Normæ vivendi.", "appendix_entry"),
    ("APPENDIX XVI. — Exhortatio penitentiei cum consolatione et misericordia Dei, ad animam futura judicia formidantem.", "appendix_entry"),
    ("APPENDIX XVII. — Lamentum penitentiae.", "appendix_entry"),
    ("APPENDIX XVIII. — Oratio pro correctione vitæ et semper peccata.", "appendix_entry"),
    ("APPENDIX XIX. — Oratio contra insidias diaboli.", "appendix_entry"),
    ("APPENDIX XX. — Liber de ortu et obitu Patrum.", "appendix_entry"),
    ("APPENDIX XXI. — Liber de numeris.", "appendix_entry"),
    ("APPENDIX XXII. — Glosæ in sacram Scripturam.", "appendix_entry"),
    ("APPENDIX XXIII. — Differentiarum, sive de propriisæ sermonum libror.", "appendix_entry"),
    ("APPENDIX XXIV. — Liber Glossarum.", "appendix_entry"),
    ("FINIS TOMI OCTOGESIMI TERTII.", "editorial_note"),
]


def locator_fields(raw: str) -> tuple[str, str | None, int | None]:
    match = re.search(r"(?:\s+)(Ibid\.|\d+)$", raw)
    if not match:
        return raw, None, None
    locator = match.group(1)
    return raw[: match.start()].rstrip(), locator, int(locator) if locator.isdigit() else None


def build_entry(order: int, raw: str, kind: str, source_file: str) -> dict[str, object]:
    target, locator, locator_int = locator_fields(raw)
    confidence = "medium" if raw.startswith("CAP. XXXIII. — Cap. XXXIV.") else "high"
    return {
        "entry_key": f"{SECTION_KEY}:{CHUNK_ID}:{order:03d}",
        "entry_order": order,
        "entry_raw": raw,
        "target_raw": target,
        "target_file": None,
        "page_ref_raw": locator,
        "page_ref_int": locator_int,
        "page_ref_col": None,
        "note_raw": None,
        "confidence": confidence,
        "raw_json": {"source_files": [source_file], "entry_kind": kind},
    }


def main() -> None:
    entries = []
    for raw, kind in PAGE_693:
        entries.append(build_entry(len(entries) + 1, raw, kind, FILE_693))
    for raw, kind in PAGE_694:
        entries.append(build_entry(len(entries) + 1, raw, kind, FILE_694))
    payload = {
        "schema_version": 2,
        "volume_id": "PL083",
        "section_id": SECTION_ID,
        "chunk_id": CHUNK_ID,
        "input_fingerprint": FINGERPRINT,
        "status": "complete",
        "physical_files": [FILE_693, FILE_694],
        "numbering_semantics": {
            "physical_file_fields": "physical_files_and_explicit_file_locators",
            "entry_number_system": "editorial",
            "numeric_equality_mapping_forbidden": True,
        },
        "boundary_decisions": [{
            "physical_left_file": FILE_693,
            "physical_right_file": FILE_694,
            "decision": "not_same_entry",
            "reason": "File 693 ends with the complete Appendix XI title and editorial locator 1225; file 694 begins a distinct CAPUT PRIMUM line. No lexical continuation or catchword supports joining them.",
        }],
        "works": [],
        "sections": [{
            "section_key": SECTION_KEY,
            "work_key": None,
            "scope_kind": "volume_contents",
            "index_kind": "ORDO RERUM",
            "heading_raw": "ORDO RERUM QUÆ IN SEPTIMO S. ISIDORI OPERUM TOMO CONTINENTUR.",
            "heading_norm": "Ordo rerum quæ in septimo S. Isidori operum tomo continentur",
            "page_start": None,
            "page_end": None,
            "file_start": FILE_693,
            "file_end": FILE_694,
            "confidence": "medium",
            "raw_json": {
                "source_files": [FILE_693, FILE_694],
                "adjacent_context_files_checked": [
                    str(SOURCE_ROOT / f"e2804b08-0ec4-4919-ad0c-00f9162d567c-{n}.txt")
                    for n in (689, 690, 691, 692, 695, 696, 697, 698)
                ],
                "extra_searches_checked": [],
                "section_kind": "retrospective_tome_contents_table",
                "numbering_note": "Bare numbers and Ibid. are printed editorial locators, not OCR-file identifiers. The source has conflicting header pagination across scans; no physical-file number was used as an editorial reference.",
                "ocr_uncertainties": [
                    "CAP. XIII ends as ‘conversi’ before printed 981.",
                    "The source prints ‘CAP. XXXIII. — Cap. XXXIV. — De nive. Ibid.’ as one damaged line.",
                    "File 694 prints APPENDIX XIII twice; the literal is retained.",
                ],
                "entries_status": "complete",
                "entries_status_reason": "All 205 readable line items in the two owned physical OCR files were serialized; headings, running headers, and the final printer line were not treated as table entries.",
            },
            "entries": entries,
        }],
        "notes": [
            "The inspected physical context 689–692 is the preceding Liber Glossarum material; 695–698 are blank/end matter. It confirms that only the lower contents-table block of file 693 and both columns of file 694 belong to this chunk's extraction.",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
