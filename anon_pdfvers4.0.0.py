# -*- coding: utf-8 -*-
"""AnonLGPD – GUI – ThreadSafe (PyMuPDF)

Versão: 4.0

Melhorias centrais da v2.2:
- Redaction orientada por palavra (word-level), evitando apagar parágrafos inteiros em OCR.
- Limitação geométrica estrita dos retângulos de anonimização.
- OCR opcional em português para PDFs-imagem.
- Validação formal da IE/PE (7+2 com dígitos verificadores).
- Regras mais conservadoras para nomes/razão social e assinaturas.
- Suporte a Tesseract portátil ao lado do script/executável.
"""

import os
import re
import sys
import time
import shutil
import tempfile
import threading
import traceback
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

try:
    import fitz
except Exception as e:
    raise SystemExit(
        "PyMuPDF (fitz) não está instalado ou falhou ao carregar.\n"
        "Instale com: pip install --upgrade pymupdf"
    ) from e

APP_VERSION = "4.0"
REPLACEMENT_TEXT = "XXXXXXXXXX"
IMAGE_PLACEHOLDER = "IMAGEM"
OCR_LANGUAGE = "por"
OCR_DPI = 300
_HAS_IMG_REDACT = hasattr(fitz, "PDF_REDACT_IMAGE_PIXELS")

# ===================== PATTERNS =====================

def build_targets():
    return [
        ("PROCESSO / AÇÃO FISCAL / INTIMAÇÃO", [
            r"(?<!\d)\d{4}\.\d{12}-\d{2}(?!\d)",
            r"(?<!\d)\d{4}\.\d{9,12}(?:-\d{1,2})?(?!\d)",
        ]),
        ("CPF", [
            r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)",
            r"(?<!\d)\d{11}(?!\d)",
        ]),
        ("CEP", [
            r"(?<!\d)\d{5}-\d{3}(?!\d)",
            r"(?<!\d)\d{2}\.\d{3}-\d{3}(?!\d)",
        ]),
    ]


def compile_targets(targets):
    compiled = []
    for label, patlist in targets:
        pats = []
        for p in patlist:
            pats.append(p if isinstance(p, re.Pattern) else re.compile(p, re.IGNORECASE))
        compiled.append((label, pats))
    return compiled


ANEXOS_HEADERS = [
    "ANEXOS", "ARQUIVOS ANEXOS", "LISTA DE ARQUIVOS ANEXADOS", "LISTA DE ARQUIVOS ANEXOS",
    "RELAÇÃO DE ANEXOS", "RELACAO DE ANEXOS", "TABELA DE ANEXOS",
]
STOP_AFTER = [
    "OBSERVAÇÕES", "OBSERVACOES", "CONCLUSÃO", "CONCLUSAO", "NOTIFICAÇÃO", "NOTIFICACAO",
    "TERMO", "DISPOSIÇÕES", "DISPOSICOES",
]
SIG_DIGITAL_HEADERS = [
    "ASSINATURA DIGITAL", "DOCUMENTO ASSINADO DIGITALMENTE", "ASSINADO DIGITALMENTE",
    "ASSINATURA ELETRÔNICA", "ASSINATURA ELETRONICA", "ASSINADO ELETRONICAMENTE",
    "ASSINADO ELETRONICAMENTE POR", "DOCUMENTO ELETRÔNICO", "DOCUMENTO ELETRONICO",
    "CARIMBO DO TEMPO", "CARIMBO DE TEMPO", "ICP-BRASIL", "ICP BRASIL", "CERTIFICADO DIGITAL",
    "CERTIFICADO ICP", "ASSINADO POR", "DIGITALMENTE ASSINADO POR", "VERIFICADOR", "HASH",
    "CÓDIGO DE VERIFICAÇÃO", "CODIGO DE VERIFICACAO", "CÓDIGO VERIFICADOR", "CODIGO VERIFICADOR",
    "CÓDIGO DE AUTENTICIDADE", "CODIGO DE AUTENTICIDADE", "GOV.BR", "GOV BR", "GOVBR",
    "MEU GOV.BR", "MEUGOV", "MEU GOV", "ASSINATURA GOV.BR", "ASSINADO VIA GOV.BR",
    "ASSINADO COM GOV.BR", "VALIDAR EM GOV.BR", "VALIDAÇÃO GOV.BR", "VALIDACAO GOV.BR",
    "ASSINATURA MEU GOV.BR", "ASSINADO COM MEU GOV.BR",
]
SIG_COMMON_HEADERS = [
    "ASSINATURA", "ASSINATURA DO CONTRIBUINTE", "ASSINATURA DO AUDITOR",
    "ASSINATURA DO REPRESENTANTE", "FIRMA", "PROCURADOR", "ADVOGADO",
    "REPRESENTANTE LEGAL", "FUNCIONÁRIO", "FUNCIONARIO", "AUDITOR FISCAL",
]

COMPANY_SUFFIX_UNION = r"(?:LTDA\.?|Ltda\.?|S\.?\s*A\.?|SA|S/A|EIRELI|EIRELLI|ME|M[Ee])"
COMPANY_FULL_REGEX = re.compile(
    rf"([A-Z0-9ÁÂÃÀÉÊÍÓÔÕÚÇ&.,\-/\s]{{2,}}?\s+{COMPANY_SUFFIX_UNION}\b)",
    re.IGNORECASE,
)
CNPJ_REGEX = re.compile(r"(?<!\d)\d{2}\D?\d{3}\D?\d{3}\D?\d{4}\D?\d{2}(?!\d)")
CPF_REGEX = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
PROCESS_ID_REGEX = re.compile(r"(?<!\d)\d{4}\.\d{9,12}(?:-\d{1,2})?(?!\d)")
CEP_REGEX = re.compile(r"(?<!\d)(?:\d{5}-\d{3}|\d{2}\.\d{3}-\d{3})(?!\d)")
PE_IE_LITERAL_REGEX = re.compile(r"(?<!\d)(\d{7}\s*-\s*\d{2})(?!\d)")
PE_IE_BARE_REGEX = re.compile(r"(?<!\d)(\d{9})(?!\d)")
PE_IE_CONTEXT_KEYWORDS = [
    "INSCRIÇÃO ESTADUAL", "INSCRICAO ESTADUAL", "INSC. ESTADUAL", "INSC ESTADUAL", "IE",
    "Nº DO DOCUMENTO", "NO DO DOCUMENTO", "NUMERO DO DOCUMENTO", "CADASTRO ESTADUAL",
]
RAZAO_SOCIAL_FIELD_KEYWORDS = [
    "INTERESSADO", "RAZÃO SOCIAL", "RAZAO SOCIAL", "EMPRESA", "CONTRIBUINTE", "C.N.A.E", "CNAE",
]

WATERMARK_TEXT_KEYWORDS = [
    "DIGITALIZADO COM CAMSCANNER", "CAMSCANNER", "CONFIDENCIAL", "RASCUNHO",
    "COPIA", "CÓPIA", "DOCUMENTO INTERNO", "SOMENTE PARA USO INTERNO",
]


# ===================== HELPERS =====================


def digits_flexible_pattern(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if not digits:
        return ""
    return r"(?<!\d)" + r"\D*?".join(re.escape(d) for d in digits) + r"(?!\d)"


def literal_flexible_pattern(value: str) -> str:
    value = nsoft(value or "")
    if not value:
        return ""
    parts = [re.escape(part) for part in value.split()]
    if not parts:
        return ""
    return r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)"


def build_user_custom_patterns(raw_text: str):
    patterns = []
    seen = set()
    for raw_item in (raw_text or "").split(","):
        item = raw_item.strip()
        if not item:
            continue
        key = nsoft(item).lower()
        if not key or key in seen:
            continue
        seen.add(key)

        digits = re.sub(r"\D+", "", item)
        pattern = ""
        replacement = "DADO SENSÍVEL"
        label = f"String personalizada: {item}"

        if PROCESS_ID_REGEX.fullmatch(item):
            pattern = re.escape(item)
            replacement = "Nº PROCESSO"
            label = "String personalizada (processo/ação)"
        elif len(digits) == 14:
            pattern = digits_flexible_pattern(item)
            replacement = "CNPJ"
            label = "String personalizada (CNPJ)"
        elif len(digits) == 11:
            pattern = digits_flexible_pattern(item)
            replacement = "CPF"
            label = "String personalizada (CPF)"
        elif len(digits) == 9:
            pattern = digits_flexible_pattern(item)
            replacement = "IE"
            label = "String personalizada (IE/PE)"
        elif len(digits) == 8:
            pattern = digits_flexible_pattern(item)
            replacement = "CEP"
            label = "String personalizada (CEP)"
        else:
            pattern = literal_flexible_pattern(item)

        if pattern:
            patterns.append({
                "label": label,
                "regex": re.compile(pattern, re.IGNORECASE),
                "replacement": replacement,
                "source": item,
            })
    return patterns


def nsoft(s: str) -> str:
    s2 = re.sub(r"[\u2010-\u2015–—−]+", "-", s)
    s2 = re.sub(r"[._/\\]+", " ", s)
    s2 = re.sub(r"\s+", " ", s)
    return s2.strip()


def get_app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configure_tesseract_runtime(log=None):
    base = get_app_base_dir()
    candidates = [base / "tesseract", base / "Tesseract-OCR", base]
    env_home = os.environ.get("TESSERACT_HOME", "").strip()
    if env_home:
        candidates.insert(0, Path(env_home))

    tesseract_exe = None
    tesseract_dir = None
    for folder in candidates:
        try:
            if not folder.exists():
                continue
        except Exception:
            continue
        for exe_name in ("tesseract.exe", "tesseract"):
            candidate = folder / exe_name
            if candidate.exists():
                tesseract_exe = candidate
                tesseract_dir = folder
                break
        if tesseract_exe:
            break

    if not tesseract_exe:
        existing = shutil.which("tesseract")
        if existing:
            tesseract_exe = Path(existing)
            tesseract_dir = tesseract_exe.parent

    tessdata_dir = None
    if tesseract_dir:
        td = tesseract_dir / "tessdata"
        if td.exists():
            tessdata_dir = td
        path_entries = os.environ.get("PATH", "").split(os.pathsep)
        if str(tesseract_dir) not in path_entries:
            os.environ["PATH"] = str(tesseract_dir) + os.pathsep + os.environ.get("PATH", "")

    if tessdata_dir:
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)

    por_ok = bool(tessdata_dir and (tessdata_dir / "por.traineddata").exists())
    if log:
        log.add_log(f"OCR: Tesseract {'localizado em: ' + str(tesseract_exe) if tesseract_exe else 'não localizado automaticamente.'}")
        log.add_log(f"OCR: tessdata {'em: ' + str(tessdata_dir) if tessdata_dir else 'não localizada automaticamente.'}")
        if not por_ok:
            log.add_log("OCR: arquivo 'por.traineddata' não encontrado. O OCR em português poderá falhar.")
    return {
        "tesseract_exe": str(tesseract_exe) if tesseract_exe else None,
        "tesseract_dir": str(tesseract_dir) if tesseract_dir else None,
        "tessdata_dir": str(tessdata_dir) if tessdata_dir else None,
        "por_ok": por_ok,
    }


def build_text_context(page, enable_ocr: bool, log=None):
    native_tp = page.get_textpage()
    native_text = page.get_text("text", textpage=native_tp).strip()
    if native_text or not enable_ocr:
        return {"textpage": native_tp, "text": native_text, "ocr_used": False}

    runtime = configure_tesseract_runtime(log=log)
    ocr_tp = page.get_textpage_ocr(language=OCR_LANGUAGE, dpi=OCR_DPI, full=True, tessdata=runtime.get("tessdata_dir"))
    ocr_text = page.get_text("text", textpage=ocr_tp).strip()
    return {"textpage": ocr_tp, "text": ocr_text, "ocr_used": True}


def words_from_page(page, textpage=None):
    words = page.get_text("words", textpage=textpage) or []
    words.sort(key=lambda w: (w[5], w[6], w[7], round(w[1], 1), round(w[0], 1)))
    items = []
    chars = []
    pos = 0
    prev_block = prev_line = None
    for w in words:
        x0, y0, x1, y1, txt, block_no, line_no, word_no = w[:8]
        txt = (txt or "").strip()
        if not txt:
            continue
        sep = ""
        if items:
            if block_no != prev_block:
                sep = "\n"
            elif line_no != prev_line:
                sep = "\n"
            else:
                sep = " "
        if sep:
            chars.append(sep)
            pos += len(sep)
        start = pos
        chars.append(txt)
        pos += len(txt)
        end = pos
        items.append({
            "rect": fitz.Rect(x0, y0, x1, y1),
            "text": txt,
            "block": block_no,
            "line": line_no,
            "word": word_no,
            "start": start,
            "end": end,
        })
        prev_block, prev_line = block_no, line_no
    return {"text": "".join(chars), "items": items}


def _rect_is_reasonable(rect, page_rect):
    page_w = max(page_rect.width, 1)
    page_h = max(page_rect.height, 1)
    if rect.width > page_w * 0.35:
        return False
    if rect.height > page_h * 0.05:
        return False
    return True


def _merge_word_rects(word_items, gap_x=3.0, gap_y=2.0):
    if not word_items:
        return []
    word_items = sorted(word_items, key=lambda it: (it["block"], it["line"], it["word"]))
    merged = []
    cur = [word_items[0]]
    for item in word_items[1:]:
        last = cur[-1]
        same_line = item["block"] == last["block"] and item["line"] == last["line"]
        close = abs(item["rect"].x0 - last["rect"].x1) <= gap_x and abs(item["rect"].y0 - last["rect"].y0) <= gap_y
        if same_line and close:
            cur.append(item)
        else:
            merged.append(cur)
            cur = [item]
    merged.append(cur)

    rects = []
    for grp in merged:
        r = fitz.Rect(grp[0]["rect"])
        for gi in grp[1:]:
            r |= gi["rect"]
        rects.append(r)
    return rects


def rects_for_regex_matches(page, regex, full_text, word_index, page_rect, context_window=0, context_predicate=None):
    rects = []
    for m in regex.finditer(full_text):
        start, end = m.span(0)
        overlap = [it for it in word_index if not (it["end"] <= start or it["start"] >= end)]
        if not overlap:
            continue
        if context_predicate:
            c0 = max(0, start - context_window)
            c1 = min(len(full_text), end + context_window)
            ctx = full_text[c0:c1]
            if not context_predicate(m.group(0), ctx):
                continue
        for r in _merge_word_rects(overlap):
            if _rect_is_reasonable(r, page_rect):
                rects.append(r)
    return rects


def add_redactions_from_rects(page, rects, replacement=None, align=fitz.TEXT_ALIGN_LEFT, inflate=0.4):
    count = 0
    page_rect = page.rect
    for r in rects:
        rr = fitz.Rect(
            max(page_rect.x0, r.x0 - inflate),
            max(page_rect.y0, r.y0 - inflate),
            min(page_rect.x1, r.x1 + inflate),
            min(page_rect.y1, r.y1 + inflate),
        )
        if replacement:
            page.add_redact_annot(rr, text=replacement, fill=(1, 1, 1), text_color=(0, 0, 0), align=align)
        else:
            page.add_redact_annot(rr, fill=(1, 1, 1))
        count += 1
    return count

# ===================== REDACTIONS =====================

def redact_attachment_tables(page, textpage=None):
    removed = 0
    txt = page.get_text("text", textpage=textpage)
    if not txt:
        return 0
    for header in ANEXOS_HEADERS:
        rects = page.search_for(header, quads=False, textpage=textpage)
        for hr in rects:
            if not _rect_is_reasonable(hr, page.rect):
                continue
            page.add_redact_annot(hr, fill=(1, 1, 1))
            removed += 1
            page_rect = page.rect
            band_top = hr.y1 + 2
            band_bottom = min(page_rect.y1, band_top + 600)
            band = fitz.Rect(page_rect.x0 + 10, band_top, page_rect.x1 - 10, band_bottom)
            for stop in STOP_AFTER:
                for sr in page.search_for(stop, quads=False, textpage=textpage):
                    if hr.y1 < sr.y0 < band_bottom:
                        band = fitz.Rect(page_rect.x0 + 10, band_top, page_rect.x1 - 10, sr.y0 - 4)
            page.add_redact_annot(band, fill=(1, 1, 1))
            removed += 1
    return removed


def redact_images_with_placeholder(page, inflate=0.0, max_area_ratio=0.08):
    cnt = 0
    try:
        info = page.get_text("dict")
    except Exception:
        return 0
    page_area = max(page.rect.width * page.rect.height, 1)
    for block in info.get("blocks", []):
        if block.get("type", 0) == 1 and "bbox" in block:
            x0, y0, x1, y1 = block["bbox"]
            width = max(0, x1 - x0)
            height = max(0, y1 - y0)
            area_ratio = (width * height) / page_area
            if area_ratio > max_area_ratio:
                continue
            r = fitz.Rect(x0 - inflate, y0 - inflate, x1 + inflate, y1 + inflate)
            page.add_redact_annot(r, text=IMAGE_PLACEHOLDER, fill=(1, 1, 1), text_color=(0, 0, 0), align=fitz.TEXT_ALIGN_CENTER)
            cnt += 1
    return cnt


def _is_probable_logo_image(rect, page_rect):
    page_w = max(page_rect.width, 1)
    page_h = max(page_rect.height, 1)
    area_ratio = (rect.width * rect.height) / max(page_w * page_h, 1)
    if area_ratio <= 0 or area_ratio > 0.12:
        return False
    x0r = rect.x0 / page_w
    x1r = rect.x1 / page_w
    y0r = rect.y0 / page_h
    y1r = rect.y1 / page_h
    wr = rect.width / page_w
    hr = rect.height / page_h
    if wr > 0.45 or hr > 0.22:
        return False
    in_header = y0r <= 0.30
    in_footer = y1r >= 0.86
    in_margin = x0r <= 0.18 or x1r >= 0.82
    return in_header or in_footer or in_margin


def _is_probable_watermark_text_rect(rect, page_rect):
    page_w = max(page_rect.width, 1)
    page_h = max(page_rect.height, 1)
    wr = rect.width / page_w
    hr = rect.height / page_h
    if wr > 0.55 or hr > 0.06:
        return False
    y0r = rect.y0 / page_h
    y1r = rect.y1 / page_h
    return y0r <= 0.12 or y1r >= 0.90 or (0.15 <= y0r <= 0.55 and 0.15 <= rect.x0 / page_w <= 0.70)


def redact_logos_and_watermarks(page, textpage=None):
    cnt = 0
    page_rect = page.rect
    try:
        info = page.get_text("dict")
    except Exception:
        info = {}

    for block in info.get("blocks", []):
        if block.get("type", 0) == 1 and "bbox" in block:
            r = fitz.Rect(block["bbox"])
            if _is_probable_logo_image(r, page_rect):
                page.add_redact_annot(r, fill=(1, 1, 1))
                cnt += 1

    wordmap = words_from_page(page, textpage=textpage)
    full_text = wordmap["text"]
    words = wordmap["items"]
    seen = set()
    for kw in WATERMARK_TEXT_KEYWORDS:
        rx = re.compile(re.escape(kw), re.IGNORECASE)
        rects = rects_for_regex_matches(page, rx, full_text, words, page_rect)
        for r in rects:
            if not _is_probable_watermark_text_rect(r, page_rect):
                continue
            key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
            if key in seen:
                continue
            seen.add(key)
            page.add_redact_annot(r, fill=(1, 1, 1))
            cnt += 1
    return cnt


def _is_likely_signature_region(rect, page_rect, mode="common"):
    page_h = max(page_rect.height, 1)
    page_w = max(page_rect.width, 1)
    y_ratio = rect.y0 / page_h
    w_ratio = rect.width / page_w
    h_ratio = rect.height / page_h
    if w_ratio > 0.20 or h_ratio > 0.03:
        return False
    if mode == "digital":
        return y_ratio >= 0.78
    return y_ratio >= 0.82


def redact_signature_blocks(page, textpage=None):
    removed = 0
    page_rect = page.rect
    wordmap = words_from_page(page, textpage=textpage)
    full_text = wordmap["text"]
    words = wordmap["items"]

    def _make_rects(keywords, mode):
        out = []
        for kw in keywords:
            rx = re.compile(re.escape(kw), re.IGNORECASE)
            for r in rects_for_regex_matches(page, rx, full_text, words, page_rect):
                if _is_likely_signature_region(r, page_rect, mode=mode):
                    out.append(r)
        return out

    digital_rects = _make_rects(SIG_DIGITAL_HEADERS, "digital")
    common_rects = _make_rects(SIG_COMMON_HEADERS, "common")
    removed += add_redactions_from_rects(page, digital_rects, replacement=None)
    removed += add_redactions_from_rects(page, common_rects, replacement=None)
    return removed


def anonymize_razao_social(page, text=None, textpage=None, ocr_used=False):
    text = text if text is not None else page.get_text("text", textpage=textpage)
    if not text:
        return 0

    # Regra conservadora: em páginas OCR, anonimize apenas quando houver contexto de campo.
    upper_text = text.upper()
    has_field_context = any(k in upper_text for k in RAZAO_SOCIAL_FIELD_KEYWORDS)
    if ocr_used and not has_field_context:
        return 0

    wordmap = words_from_page(page, textpage=textpage)
    full_text = wordmap["text"]
    words = wordmap["items"]
    rects = []
    for m in COMPANY_FULL_REGEX.finditer(full_text):
        s, e = m.span(1)
        ctx = full_text[max(0, s - 80):min(len(full_text), e + 80)].upper()
        if ocr_used and not any(k in ctx for k in RAZAO_SOCIAL_FIELD_KEYWORDS):
            continue
        overlap = [it for it in words if not (it["end"] <= s or it["start"] >= e)]
        for r in _merge_word_rects(overlap):
            if _rect_is_reasonable(r, page.rect):
                rects.append(r)
    return add_redactions_from_rects(page, rects, replacement="Razao social", align=fitz.TEXT_ALIGN_LEFT)


def anonymize_cnpjs(page, text=None, textpage=None):
    text = text if text is not None else page.get_text("text", textpage=textpage)
    if not text:
        return 0
    wordmap = words_from_page(page, textpage=textpage)
    rects = rects_for_regex_matches(page, CNPJ_REGEX, wordmap["text"], wordmap["items"], page.rect)
    return add_redactions_from_rects(page, rects, replacement="CNPJ", align=fitz.TEXT_ALIGN_CENTER)


def anonymize_cpfs(page, text=None, textpage=None):
    text = text if text is not None else page.get_text("text", textpage=textpage)
    if not text:
        return 0
    wordmap = words_from_page(page, textpage=textpage)
    rects = rects_for_regex_matches(page, CPF_REGEX, wordmap["text"], wordmap["items"], page.rect)
    return add_redactions_from_rects(page, rects, replacement="CPF", align=fitz.TEXT_ALIGN_CENTER)


def anonymize_process_ids(page, text=None, textpage=None):
    text = text if text is not None else page.get_text("text", textpage=textpage)
    if not text:
        return 0
    wordmap = words_from_page(page, textpage=textpage)
    rects = rects_for_regex_matches(page, PROCESS_ID_REGEX, wordmap["text"], wordmap["items"], page.rect)
    return add_redactions_from_rects(page, rects, replacement="Nº PROCESSO", align=fitz.TEXT_ALIGN_CENTER)


def anonymize_ceps(page, text=None, textpage=None):
    text = text if text is not None else page.get_text("text", textpage=textpage)
    if not text:
        return 0
    wordmap = words_from_page(page, textpage=textpage)
    rects = rects_for_regex_matches(page, CEP_REGEX, wordmap["text"], wordmap["items"], page.rect)
    return add_redactions_from_rects(page, rects, replacement="CEP", align=fitz.TEXT_ALIGN_CENTER)


def normalize_pe_ie(candidate: str) -> str:
    digits = re.sub(r"\D+", "", candidate or "")
    if len(digits) != 9:
        return ""
    return f"{digits[:7]}-{digits[7:]}"


def is_valid_pe_ie(candidate: str) -> bool:
    digits = re.sub(r"\D+", "", candidate or "")
    if len(digits) != 9:
        return False
    base7 = digits[:7]
    dv1 = int(digits[7])
    dv2 = int(digits[8])
    s1 = sum(int(d) * w for d, w in zip(base7, range(8, 1, -1)))
    r1 = s1 % 11
    calc1 = 11 - r1
    if calc1 in (10, 11):
        calc1 = 0
    if calc1 != dv1:
        return False
    base8 = digits[:8]
    s2 = sum(int(d) * w for d, w in zip(base8, range(9, 1, -1)))
    r2 = s2 % 11
    calc2 = 11 - r2
    if calc2 in (10, 11):
        calc2 = 0
    return calc2 == dv2


def _ie_context_ok(candidate: str, ctx: str) -> bool:
    return any(kw in ctx.upper() for kw in PE_IE_CONTEXT_KEYWORDS)


def anonymize_ie_pe(page, text=None, textpage=None):
    text = text if text is not None else page.get_text("text", textpage=textpage)
    if not text:
        return 0
    wordmap = words_from_page(page, textpage=textpage)
    full_text = wordmap["text"]
    words = wordmap["items"]
    rects = []

    for m in PE_IE_LITERAL_REGEX.finditer(full_text):
        raw = m.group(1)
        if not is_valid_pe_ie(raw):
            continue
        ctx = full_text[max(0, m.start(1) - 120):min(len(full_text), m.end(1) + 120)]
        if not _ie_context_ok(raw, ctx):
            continue
        overlap = [it for it in words if not (it["end"] <= m.start(1) or it["start"] >= m.end(1))]
        for r in _merge_word_rects(overlap):
            if _rect_is_reasonable(r, page.rect):
                rects.append(r)

    for m in PE_IE_BARE_REGEX.finditer(full_text):
        raw = m.group(1)
        if not is_valid_pe_ie(raw):
            continue
        ctx = full_text[max(0, m.start(1) - 120):min(len(full_text), m.end(1) + 120)]
        if not _ie_context_ok(raw, ctx):
            continue
        overlap = [it for it in words if not (it["end"] <= m.start(1) or it["start"] >= m.end(1))]
        for r in _merge_word_rects(overlap):
            if _rect_is_reasonable(r, page.rect):
                rects.append(r)

    return add_redactions_from_rects(page, rects, replacement="IE", align=fitz.TEXT_ALIGN_CENTER)


def redact_by_regex(page, patterns, replacement=REPLACEMENT_TEXT, text=None, textpage=None):
    text = text if text is not None else page.get_text("text", textpage=textpage)
    if not text:
        return 0
    wordmap = words_from_page(page, textpage=textpage)
    total = 0
    seen = set()
    for pat in patterns:
        rects = rects_for_regex_matches(page, pat, wordmap["text"], wordmap["items"], page.rect)
        uniq = []
        for r in rects:
            key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1), replacement)
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        total += add_redactions_from_rects(page, uniq, replacement=replacement, align=fitz.TEXT_ALIGN_CENTER)
    return total

# ===================== SAVE =====================

def safe_save_pdf(doc, output_path: Path, log):
    start = time.time()
    p = Path(output_path)
    try:
        if getattr(doc, "name", None):
            in_path = Path(doc.name)
            if in_path.resolve() == p.resolve():
                p = in_path.with_name(in_path.stem + "_anon.pdf")
                log.add_log(f"Aviso: saída igual à entrada. Alterado para: {p}")
    except Exception:
        pass

    try:
        if len(str(p)) > 240:
            short_name = p.stem[:60] + "_anon.pdf"
            alt = Path(tempfile.gettempdir()) / short_name
            log.add_log(f"Aviso: caminho muito longo ({len(str(p))} chars). Redirecionando para: {alt}")
            p = alt
    except Exception as e:
        log.add_log(f"Aviso ao checar comprimento de caminho: {e}")

    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(p.suffix + ".tmp")
    try:
        if temp.exists():
            temp.unlink()
    except Exception:
        pass

    log.add_log("→ Salvando (linear=True, deflate=True, garbage=2)…")
    try:
        doc.save(temp, deflate=True, garbage=2, linear=True)
    except Exception as e:
        log.add_log(f"Aviso: save rápido falhou ({e}). Tentando fallback sem linear…")
        doc.save(temp, deflate=True, garbage=2)

    try:
        os.replace(temp, p)
        log.add_log(f"✓ Salvo em: {p}")
        return str(p)
    except PermissionError:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        alt = p.with_stem(p.stem + f"_{ts}")
        os.replace(temp, alt)
        log.add_log(f"Aviso: arquivo de saída em uso/escaneado. Salvo como: {alt}")
        return str(alt)
    finally:
        log.add_log(f"⌛ Tempo de save: {round(time.time() - start, 2)}s")

# ===================== CORE =====================

def process_pdf(input_path: Path, output_path: Path, log, progress_cb, enable_ocr=False, custom_terms_raw="", remove_logos=False):
    targets = compile_targets(build_targets())
    log.add_log(f"Abrindo: {input_path}")
    doc = fitz.open(input_path)
    total = doc.page_count
    log.add_log(f"Páginas: {total}")
    log.add_log(f"OCR habilitado: {'sim' if enable_ocr else 'não'}")
    log.add_log(f"Remoção opcional de logotipos/marcas d'água: {'sim' if remove_logos else 'não'}")
    custom_patterns = build_user_custom_patterns(custom_terms_raw)
    log.add_log(f"Strings personalizadas informadas: {len(custom_patterns)}")

    counters = {
        "anexos": 0, "imagens": 0, "assinaturas": 0, "empresas": 0, "sem_texto": 0,
        "campos": 0, "ocr_paginas": 0, "ie_pe": 0, "cpf": 0, "processos": 0, "ceps": 0,
        "customizados": 0, "logos": 0,
    }

    try:
        for i, page in enumerate(doc, start=1):
            log.add_log(f"[{i}/{total}] Processando página…")
            text_ctx = None
            try:
                text_ctx = build_text_context(page, enable_ocr=enable_ocr, log=log)
                if text_ctx["ocr_used"]:
                    counters["ocr_paginas"] += 1
                    log.add_log(f"  - OCR aplicado na página (idioma: {OCR_LANGUAGE}).")
            except Exception as e:
                counters["sem_texto"] += 1
                log.add_log(f"  ! Falha ao preparar texto/OCR da página: {e}")

            textpage = text_ctx["textpage"] if text_ctx else None
            page_text = text_ctx["text"] if text_ctx else ""
            ocr_used = bool(text_ctx and text_ctx.get("ocr_used"))

            try:
                counters["anexos"] += redact_attachment_tables(page, textpage=textpage)
            except Exception as e:
                log.add_log(f"  ! Falha anexos: {e}")

            try:
                if not ocr_used:
                    counters["imagens"] += redact_images_with_placeholder(page)
            except Exception as e:
                log.add_log(f"  ! Falha imagens: {e}")

            try:
                if remove_logos:
                    c = redact_logos_and_watermarks(page, textpage=textpage)
                    if c:
                        log.add_log(f"  - Logotipos/marcas d'água: {c} ocorrência(s)")
                    counters["logos"] += c
            except Exception as e:
                log.add_log(f"  ! Falha logotipos/marcas d'água: {e}")

            try:
                counters["assinaturas"] += redact_signature_blocks(page, textpage=textpage)
            except Exception as e:
                log.add_log(f"  ! Falha assinaturas: {e}")

            if not page_text:
                counters["sem_texto"] += 1
                log.add_log("  ! Página sem texto útil. Habilite o OCR para cobrir PDFs em imagem." if not enable_ocr else "  ! Página permaneceu sem texto mesmo com OCR habilitado.")
            else:
                for label, pats in targets:
                    try:
                        c = redact_by_regex(page, pats, text=page_text, textpage=textpage)
                        if c:
                            log.add_log(f"  - {label}: {c} ocorrência(s)")
                        counters["campos"] += c
                    except Exception as e:
                        log.add_log(f"  ! Erro '{label}': {e}")

                try:
                    c = anonymize_razao_social(page, text=page_text, textpage=textpage, ocr_used=ocr_used)
                    if c:
                        log.add_log(f"  - Razao social: {c} ocorrência(s)")
                    counters["empresas"] += c
                except Exception as e:
                    log.add_log(f"  ! Erro Razao social: {e}")

                try:
                    cnpj_c = anonymize_cnpjs(page, text=page_text, textpage=textpage)
                    if cnpj_c:
                        log.add_log(f"  - CNPJ: {cnpj_c} ocorrência(s)")
                    counters["campos"] += cnpj_c
                except Exception as e:
                    log.add_log(f"  ! Erro CNPJ: {e}")

                try:
                    c = anonymize_cpfs(page, text=page_text, textpage=textpage)
                    if c:
                        log.add_log(f"  - CPF: {c} ocorrência(s)")
                    counters["cpf"] += c
                except Exception as e:
                    log.add_log(f"  ! Erro CPF: {e}")

                try:
                    c = anonymize_process_ids(page, text=page_text, textpage=textpage)
                    if c:
                        log.add_log(f"  - Nº de processo/ação: {c} ocorrência(s)")
                    counters["processos"] += c
                except Exception as e:
                    log.add_log(f"  ! Erro processo/ação: {e}")

                try:
                    c = anonymize_ceps(page, text=page_text, textpage=textpage)
                    if c:
                        log.add_log(f"  - CEP: {c} ocorrência(s)")
                    counters["ceps"] += c
                except Exception as e:
                    log.add_log(f"  ! Erro CEP: {e}")

                try:
                    ie_c = anonymize_ie_pe(page, text=page_text, textpage=textpage)
                    if ie_c:
                        log.add_log(f"  - IE/PE validada (7+2): {ie_c} ocorrência(s)")
                    counters["ie_pe"] += ie_c
                except Exception as e:
                    log.add_log(f"  ! Erro IE/PE: {e}")

                for cp in custom_patterns:
                    try:
                        c = redact_by_regex(page, [cp["regex"]], replacement=cp["replacement"], text=page_text, textpage=textpage)
                        if c:
                            log.add_log(f"  - {cp['label']}: {c} ocorrência(s)")
                        counters["customizados"] += c
                    except Exception as e:
                        log.add_log(f"  ! Erro {cp['label']}: {e}")

            try:
                if _HAS_IMG_REDACT:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
                else:
                    page.apply_redactions()
            except Exception:
                page.apply_redactions()

            if total > 0:
                progress_cb(int(i / total * 100))

        log.add_log("Resumo final:")
        log.add_log(f"  • OCR em páginas: {counters['ocr_paginas']}")
        log.add_log(f"  • Páginas sem texto útil: {counters['sem_texto']}")
        log.add_log(f"  • Redações de campos: {counters['campos']}")
        log.add_log(f"  • IE/PE anonimizada: {counters['ie_pe']}")
        log.add_log(f"  • Strings personalizadas anonimizada(s): {counters['customizados']}")
        log.add_log(f"  • CPF anonimizado: {counters['cpf']}")
        log.add_log(f"  • Nºs de processo/ação: {counters['processos']}")
        log.add_log(f"  • CEP anonimizado: {counters['ceps']}")
        log.add_log(f"  • Razões sociais anonimizada(s): {counters['empresas']}")
        log.add_log(f"  • Assinaturas removidas: {counters['assinaturas']}")
        log.add_log(f"  • Imagens tratadas: {counters['imagens']}")
        log.add_log(f"  • Logotipos / marcas d'água removidos: {counters['logos']}")
        log.add_log(f"  • Seções de anexos removidas: {counters['anexos']}")
        log.add_log("Salvando arquivo…")
        final_path = safe_save_pdf(doc, output_path, log)
        log.add_log(f"OK! PDF anonimizado salvo em:\n{final_path}")
        return final_path
    finally:
        try:
            doc.close()
        except Exception:
            pass



def process_pdf_manual_regions(input_path: Path, output_path: Path, regions_by_page: dict, log, progress_cb):
    log.add_log(f"Abrindo para anonimização manual: {input_path}")
    doc = fitz.open(input_path)
    total = doc.page_count
    log.add_log(f"Páginas: {total}")

    counters = {"marcacoes": 0}

    try:
        for i, page in enumerate(doc, start=1):
            page_index = i - 1
            page_regions = regions_by_page.get(page_index, []) or []
            log.add_log(f"[{i}/{total}] Aplicando marcações manuais…")

            for region in page_regions:
                try:
                    rect = fitz.Rect(region["x0"], region["y0"], region["x1"], region["y1"])
                    rect = rect & page.rect
                    if rect.is_empty or rect.width < 1 or rect.height < 1:
                        continue
                    page.add_redact_annot(rect, fill=(1, 1, 1))
                    counters["marcacoes"] += 1
                except Exception as e:
                    log.add_log(f"  ! Falha em uma marcação manual: {e}")

            try:
                if _HAS_IMG_REDACT:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
                else:
                    page.apply_redactions()
            except Exception:
                page.apply_redactions()

            if total > 0:
                progress_cb(int(i / total * 100))

        log.add_log("Resumo final:")
        log.add_log(f"  • Marcações manuais aplicadas: {counters['marcacoes']}")
        log.add_log("Salvando arquivo…")
        final_path = safe_save_pdf(doc, output_path, log)
        log.add_log(f"OK! PDF anonimizado manualmente salvo em:\n{final_path}")
        return final_path
    finally:
        try:
            doc.close()
        except Exception:
            pass

# ===================== GUI =====================

class ManualRedactionEditor(tk.Toplevel):
    def __init__(self, master, pdf_path: Path):
        super().__init__(master)
        self.title("Marcação manual para anonimização")
        self.geometry("1180x860")
        self.minsize(900, 650)
        self.transient(master)
        self.pdf_path = Path(pdf_path)
        self.doc = fitz.open(self.pdf_path)
        self.page_index = 0
        self.zoom = 1.35
        self.result = None
        self.regions_by_page = {}
        self.page_image = None
        self.start_x = None
        self.start_y = None
        self.current_rect_id = None
        self.page_image_id = None

        self.protocol("WM_DELETE_WINDOW", self.cancel)

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="◀ Página anterior", command=self.prev_page).pack(side="left")
        ttk.Button(top, text="Próxima página ▶", command=self.next_page).pack(side="left", padx=(6, 12))
        ttk.Button(top, text="Desfazer última", command=self.undo_last).pack(side="left")
        ttk.Button(top, text="Limpar página", command=self.clear_page).pack(side="left", padx=(6, 12))
        ttk.Button(top, text="− Zoom", command=lambda: self.change_zoom(-0.15)).pack(side="left")
        ttk.Button(top, text="+ Zoom", command=lambda: self.change_zoom(0.15)).pack(side="left", padx=(6, 12))
        ttk.Button(top, text="Cancelar", command=self.cancel).pack(side="right")
        ttk.Button(top, text="Concluir marcação", command=self.finish).pack(side="right", padx=(0, 6))

        self.lbl_page = ttk.Label(top, text="")
        self.lbl_page.pack(side="right", padx=12)

        info = ttk.Label(
            self,
            text="Clique e arraste sobre o documento para marcar apenas os trechos que deverão ser anonimizados.",
            padding=(10, 0, 10, 6)
        )
        info.pack(anchor="w")

        canvas_wrap = ttk.Frame(self)
        canvas_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.canvas = tk.Canvas(canvas_wrap, bg="#808080", highlightthickness=0)
        self.hbar = ttk.Scrollbar(canvas_wrap, orient="horizontal", command=self.canvas.xview)
        self.vbar = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="we")
        canvas_wrap.columnconfigure(0, weight=1)
        canvas_wrap.rowconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)

        self.render_page()

    def _canvas_xy(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _on_mousewheel(self, event):
        step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")

    def _on_shift_mousewheel(self, event):
        step = -1 if event.delta > 0 else 1
        self.canvas.xview_scroll(step, "units")

    def render_page(self):
        page = self.doc[self.page_index]
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        self.page_image = tk.PhotoImage(data=pix.tobytes("ppm"))
        self.canvas.delete("all")
        self.page_image_id = self.canvas.create_image(0, 0, anchor="nw", image=self.page_image)
        self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))
        self.lbl_page.config(text=f"Página {self.page_index + 1} / {self.doc.page_count}  |  Zoom: {round(self.zoom, 2)}x")

        for region in self.regions_by_page.get(self.page_index, []):
            x0 = region["x0"] * self.zoom
            y0 = region["y0"] * self.zoom
            x1 = region["x1"] * self.zoom
            y1 = region["y1"] * self.zoom
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="red", width=2)

        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def on_mouse_down(self, event):
        x, y = self._canvas_xy(event)
        self.start_x = x
        self.start_y = y
        self.current_rect_id = self.canvas.create_rectangle(
            x, y, x, y, outline="red", width=2
        )

    def on_mouse_drag(self, event):
        if self.current_rect_id is None:
            return
        x, y = self._canvas_xy(event)
        self.canvas.coords(self.current_rect_id, self.start_x, self.start_y, x, y)

    def on_mouse_up(self, event):
        if self.current_rect_id is None:
            return

        x, y = self._canvas_xy(event)
        x0 = min(self.start_x, x) / self.zoom
        y0 = min(self.start_y, y) / self.zoom
        x1 = max(self.start_x, x) / self.zoom
        y1 = max(self.start_y, y) / self.zoom

        if abs(x1 - x0) < 2 or abs(y1 - y0) < 2:
            self.canvas.delete(self.current_rect_id)
            self.current_rect_id = None
            return

        self.regions_by_page.setdefault(self.page_index, []).append({
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
        })
        self.current_rect_id = None
        self.render_page()

    def prev_page(self):
        if self.page_index > 0:
            self.page_index -= 1
            self.render_page()

    def next_page(self):
        if self.page_index < self.doc.page_count - 1:
            self.page_index += 1
            self.render_page()

    def undo_last(self):
        regs = self.regions_by_page.get(self.page_index, [])
        if regs:
            regs.pop()
            self.render_page()

    def clear_page(self):
        if self.page_index in self.regions_by_page:
            self.regions_by_page[self.page_index] = []
        self.render_page()

    def change_zoom(self, delta):
        new_zoom = max(0.5, min(3.0, self.zoom + delta))
        if abs(new_zoom - self.zoom) > 0.001:
            self.zoom = new_zoom
            self.render_page()

    def finish(self):
        cleaned = {k: v for k, v in self.regions_by_page.items() if v}
        if not cleaned:
            messagebox.showwarning("Aviso", "Nenhuma área foi marcada.")
            return
        self.result = cleaned
        self._close_doc()
        self.destroy()

    def cancel(self):
        self.result = None
        self._close_doc()
        self.destroy()

    def _close_doc(self):
        try:
            if getattr(self, "doc", None) is not None:
                self.doc.close()
                self.doc = None
        except Exception:
            pass

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f"Anonimizador LGPD – PDF (v{APP_VERSION})")
        self.geometry("800x560")
        self.resizable(True, True)
        self.in_path = tk.StringVar()
        self.out_path = tk.StringVar()
        self.enable_ocr = tk.BooleanVar(value=True)
        self.remove_logos = tk.BooleanVar(value=False)

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="PDF de entrada:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.in_path, width=88).grid(row=1, column=0, columnspan=2, sticky="we", pady=2)
        ttk.Button(frm, text="Escolher…", command=self.pick_input).grid(row=1, column=2, sticky="e")

        ttk.Label(frm, text="PDF de saída:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=self.out_path, width=88).grid(row=3, column=0, columnspan=2, sticky="we", pady=2)
        ttk.Button(frm, text="Alterar…", command=self.pick_output).grid(row=3, column=2, sticky="e")

        ttk.Checkbutton(frm, text="Habilitar OCR para PDFs em imagem (Português)", variable=self.enable_ocr).grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="w")
        ttk.Checkbutton(frm, text="Remover logotipos, imagens de logomarca e marcas d'água (opcional)", variable=self.remove_logos).grid(row=5, column=0, columnspan=3, pady=(4, 0), sticky="w")

        ttk.Label(frm, text="Strings adicionais para anonimização (separe por vírgulas):").grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.custom_txt = tk.Text(frm, height=3, wrap="word")
        self.custom_txt.grid(row=7, column=0, columnspan=2, sticky="we", pady=2)

        help_wrap = ttk.Frame(frm)
        help_wrap.grid(row=7, column=2, sticky="ne", padx=(8, 0))
        self.help_canvas = tk.Canvas(
            help_wrap,
            width=44,
            height=44,
            highlightthickness=0,
            bd=0,
            bg=self.cget("bg"),
            cursor="hand2"
        )
        self.help_canvas.pack()
        self._help_btn_bg = "#2F6FED"
        self._help_btn_bg_hover = "#1F57C8"
        self._help_btn_outline = "#1746A2"
        self._help_shadow = self.help_canvas.create_oval(7, 9, 39, 41, fill="#B7C7F3", outline="")
        self._help_circle = self.help_canvas.create_oval(5, 5, 37, 37, fill=self._help_btn_bg, outline=self._help_btn_outline, width=1)
        self._help_text = self.help_canvas.create_text(21, 21, text="?", fill="white", font=("Segoe UI", 16, "bold"))
        self.help_canvas.bind("<Button-1>", lambda e: self.show_help())
        self.help_canvas.bind("<Enter>", self._on_help_enter)
        self.help_canvas.bind("<Leave>", self._on_help_leave)
        self.help_canvas.bind("<Configure>", self._sync_help_canvas_bg)

        buttons = ttk.Frame(frm)
        buttons.grid(row=8, column=0, pady=10, sticky="w")
        ttk.Button(buttons, text="Anonimização automática", command=self.run_thread).pack(side="left")
        ttk.Button(buttons, text="Marcação manual", command=self.open_manual_editor).pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(frm, mode="determinate", length=320)
        self.progress.grid(row=8, column=1, sticky="w")

        ttk.Label(frm, text="Logs:").grid(row=9, column=0, sticky="w", pady=(10, 0))
        self.txt = tk.Text(frm, height=18, wrap="word", state="disabled")
        self.txt.grid(row=10, column=0, columnspan=3, sticky="nsew")
        scroll = ttk.Scrollbar(frm, command=self.txt.yview)
        scroll.grid(row=10, column=3, sticky="ns")
        self.txt["yscrollcommand"] = scroll.set

        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(10, weight=1)


    def _sync_help_canvas_bg(self, _event=None):
        try:
            self.help_canvas.configure(bg=self.cget("bg"))
        except Exception:
            pass

    def _on_help_enter(self, _event=None):
        try:
            self.help_canvas.itemconfigure(self._help_circle, fill=self._help_btn_bg_hover)
            self.help_canvas.move(self._help_shadow, 1, 1)
            self.help_canvas.move(self._help_circle, 1, 1)
            self.help_canvas.move(self._help_text, 1, 1)
        except Exception:
            pass

    def _on_help_leave(self, _event=None):
        try:
            self.help_canvas.coords(self._help_shadow, 7, 9, 39, 41)
            self.help_canvas.coords(self._help_circle, 5, 5, 37, 37)
            self.help_canvas.coords(self._help_text, 21, 21)
            self.help_canvas.itemconfigure(self._help_circle, fill=self._help_btn_bg)
        except Exception:
            pass

    def add_log(self, msg: str):
        def _append():
            self.txt.configure(state="normal")
            self.txt.insert("end", str(msg) + "\n")
            self.txt.see("end")
            self.txt.configure(state="disabled")
        self.after(0, _append)

    def set_progress(self, value: int):
        self.after(0, lambda: self.progress.configure(value=max(0, min(100, int(value)))))

    def _info(self, title: str, text: str):
        self.after(0, lambda: messagebox.showinfo(title, text))

    def _error(self, title: str, text: str):
        self.after(0, lambda: messagebox.showerror(title, text))


    def show_help(self):
        message = (
            "Campo de strings adicionais:\n"
            "Informe dados que você deseje anonimizar, separados por vírgulas.\n\n"
            "Exemplos válidos:\n"
            "• CNPJ: 12.345.678/0001-90 ou 12345678000190\n"
            "• CPF: 123.456.789-09 ou 12345678909\n"
            "• Inscrição Estadual/PE: 0321418-40 ou 032141840\n"
            "• CEP: 50000-000 ou 50000000\n"
            "• Nº de processo/ação fiscal: 2025.000009857102-17\n"
            "• Nome, razão social, endereço ou qualquer frase específica.\n\n"
            "OCR:\n"
            "OCR é a tecnologia que tenta ler texto em PDFs escaneados ou em formato imagem. "
            "Quando habilitado, ele amplia a cobertura da anonimização, mas pode gerar pequenos desvios de leitura "
            "em documentos com baixa resolução, carimbos, inclinação ou manchas.\n\n"
            "Conferência final:\n"
            "O modo de Marcação Manual permite ao usuário selecionar visualmente, diretamente no documento exibido na tela, os trechos"
            " que deverão ser anonimados. Após abrir o PDF, navegue entre as páginas utilizando os botões de navegação e, com o mouse,"
            "clique e arraste sobre a área que deseja ocultar, formando um retângulo de seleção. Cada retângulo criado representa uma "
            "região que será anonimizada no processamento final. Caso necessário, utilize os comandos “Desfazer última” para remover a"
            " marcação mais recente ou “Limpar página” para excluir todas as marcações da página atual. Ao concluir a seleção das áreas"
            "desejadas, clique em “Concluir marcação”, momento em que o sistema aplicará a anonimização exclusivamente nas regiões marcadas,"
            "preservando o restante do documento. Esse modo é especialmente útil para anonimizar apenas trechos específicos ou para reprocessar"
            "documentos previamente tratados pela rotina automática, quando algum dado sensível não tiver sido identificado pelo processamento "
            "anterior.\n"
            "Mesmo após a anonimização, revise o PDF final. Caso permaneça algum dado sensível, rode o arquivo novamente "
            "informando a string remanescente neste campo. Em PDFs escaneados, pode ocorrer repetição superposta do processo "
            "de anonimização quando um dado residual for tratado em nova execução. Isso é normal e serve como reforço da limpeza final.\n\n"
            "Aplicativo elaborado pelo Grupo Executivo de Ação Fiscal 2 - DRR IIRF - SEFAZ -PE - proibido  uso comercial🚫\n\n"
            "versão 4.0"
        )
        messagebox.showinfo("Help – orientação de uso", message)

    def pick_input(self):
        path = filedialog.askopenfilename(title="Selecionar PDF", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.in_path.set(path)
        p = Path(path)
        self.out_path.set(str(p.with_name(p.stem + "_anon.pdf")))

    def pick_output(self):
        path = filedialog.asksaveasfilename(title="Salvar PDF anonimizado como…", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if path:
            self.out_path.set(path)

    def run_thread(self):
        ip = self.in_path.get().strip()
        op = self.out_path.get().strip()
        if not ip:
            messagebox.showwarning("Aviso", "Escolha um PDF de entrada.")
            return
        if not op:
            messagebox.showwarning("Aviso", "Informe o caminho de saída.")
            return
        self.set_progress(0)
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        custom_terms_raw = self.custom_txt.get("1.0", "end").strip()
        t = threading.Thread(target=self._run_safe, args=(Path(ip), Path(op), bool(self.enable_ocr.get()), custom_terms_raw, bool(self.remove_logos.get())), daemon=True)
        t.start()

    def open_manual_editor(self):
        ip = self.in_path.get().strip()
        op = self.out_path.get().strip()
        if not ip:
            messagebox.showwarning("Aviso", "Escolha um PDF de entrada.")
            return
        if not op:
            messagebox.showwarning("Aviso", "Informe o caminho de saída.")
            return

        editor = ManualRedactionEditor(self, Path(ip))
        self.wait_window(editor)

        if not getattr(editor, "result", None):
            self.add_log("Marcação manual cancelada ou sem áreas selecionadas.")
            return

        self.set_progress(0)
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

        t = threading.Thread(
            target=self._run_manual_safe,
            args=(Path(ip), Path(op), editor.result),
            daemon=True
        )
        t.start()

    def _run_manual_safe(self, input_path: Path, output_path: Path, regions_by_page: dict):
        try:
            final_path = process_pdf_manual_regions(
                input_path=input_path,
                output_path=output_path,
                regions_by_page=regions_by_page,
                log=self,
                progress_cb=self.set_progress
            )
            self._info("Concluído", f"Arquivo salvo em:\n{final_path}")
        except Exception:
            self.add_log("ERRO:\n" + traceback.format_exc())
            self._error("Erro", "Ocorreu um erro na anonimização manual. Veja os logs.")

    def _run_safe(self, input_path: Path, output_path: Path, enable_ocr: bool, custom_terms_raw: str, remove_logos: bool):
        try:
            final_path = process_pdf(input_path, output_path, log=self, progress_cb=self.set_progress, enable_ocr=enable_ocr, custom_terms_raw=custom_terms_raw, remove_logos=remove_logos)
            self._info("Concluído", f"Arquivo salvo em:\n{final_path}")
        except Exception:
            self.add_log("ERRO:\n" + traceback.format_exc())
            self._error("Erro", "Ocorreu um erro. Veja os logs para detalhes.")


if __name__ == "__main__":
    App().mainloop()
