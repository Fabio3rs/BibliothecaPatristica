"""Reference agreement diagnostics shared by history selection and OCR A/B."""
from html import escape

BLOCK_TYPES = "cabecalho texto_principal aparato_critico rodape nota nota_marginal outro".split()
BLOCK_SCRIPTS = "latino grego copta siriaco cirilico ethiopico armenio arabe hebraico misto desconhecido".split()


def content_reference(layout):
    """Freeze reference reading order independently of the model's output boxes.

    Separate blocks retain line-break hyphen boundaries. This diagnostic cannot
    confirm geometry or decide whether a literal Tesseract reading is correct.
    """
    return "<pagina>" + "".join("<bloco>" + escape(b["text"]) + "</bloco>"
                                for b in layout["blocks"]) + "</pagina>"


def content_profile(comparison):
    reference = comparison["coverage"]["reference"]
    candidate = comparison["coverage"]["candidate"]
    ref, cand = reference["by_status"], candidate["by_status"]
    literal = ref.get("equal", 0) + ref.get("normalized_equal", 0)
    return {"reference_tokens": reference["total_tokens"], "candidate_tokens": candidate["total_tokens"],
            "missing_reference_tokens": ref.get("reference_only", 0),
            "uncertain_reference_tokens": ref.get("uncertain_substitution", 0) + ref.get("unresolved", 0),
            "unmatched_candidate_tokens": cand.get("candidate_only", 0),
            "uncertain_candidate_tokens": cand.get("uncertain_substitution", 0) + cand.get("unresolved", 0),
            "literal_reference_tokens": literal,
            "literal_reference_fraction": literal / max(1, reference["total_tokens"])}


def diagnostic_flags(spatial, content):
    flags = []
    if (spatial["missing_reference_tokens"] >= 20 and
            spatial["missing_reference_tokens"] > 3 * max(1, content["missing_reference_tokens"])):
        flags.append("spatial_alignment_confounded_check_boxes_and_reading_order")
    if content["reference_tokens"] >= 20 and content["literal_reference_fraction"] >= .98:
        flags.append("near_verbatim_tesseract_agreement_requires_image_review")
    return flags

