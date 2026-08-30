"""Tailor the master resume to a job description via the Anthropic API.

Hard rules enforced here (both in the prompt and by code afterwards):
  * Never fabricate metrics, dates, employers, titles or achievements.
  * Only rephrase and reorder bullets/content that already exist in the master.
  * Prefer the job description's action verbs where it reads naturally.
  * Keep light-to-medium keyword overlap for ATS - not keyword stuffing.

The master resume is treated as a *source document*: the model selects a subset
of existing content, reorders it, and lightly rewords it into a one-to-two page
job-facing CV. A post-generation check flags any new numbers or any bullet that
does not map back to something in the master.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

import anthropic
from docx import Document
from docx.shared import Pt

from config import CONFIG
from pipeline.scraper import JobPosting

NUM_RE = re.compile(r"\b\d[\d,.]*\s?(?:%|percent|k\b|m\b|bn\b|\+)?", re.IGNORECASE)
SIMILARITY_FLOOR = 0.55  # difflib ratio below which a bullet is "not from the master"


# --------------------------------------------------------------------------- #
# 1. Extract the master resume into plain structured blocks
# --------------------------------------------------------------------------- #
@dataclass
class Block:
    kind: str  # "heading" | "subheading" | "bullet" | "text"
    text: str


def _classify(paragraph) -> str:
    style = ((paragraph.style.name if paragraph.style else "") or "").lower()
    text = paragraph.text.strip()
    if not text:
        return "empty"
    if "heading 1" in style or "title" in style:
        return "heading"
    if "heading" in style:
        return "subheading"
    if style.startswith("list") or text[:2] in {"- ", "• ", "* "}:
        return "bullet"
    if re.match(r"^\d+\.\s+[A-Z]", text) and len(text) < 60:
        return "heading"
    runs_bold = [r for r in paragraph.runs if r.text.strip()]
    if runs_bold and all(r.bold for r in runs_bold) and len(text) < 120:
        return "subheading"
    return "text"


def extract_master(path=None) -> list[Block]:
    path = path or CONFIG.master_resume_path
    doc = Document(str(path))
    blocks: list[Block] = []
    for para in doc.paragraphs:
        kind = _classify(para)
        if kind == "empty":
            continue
        text = re.sub(r"^[-•*]\s+", "", para.text.strip())
        blocks.append(Block(kind=kind, text=text))
    return blocks


def master_corpus(blocks: list[Block]) -> list[str]:
    return [b.text for b in blocks if b.kind in {"bullet", "text", "subheading"}]


# --------------------------------------------------------------------------- #
# 2. Ask Claude to assemble a tailored resume (JSON)
# --------------------------------------------------------------------------- #
TAILOR_SYSTEM = """You are a meticulous resume editor. You tailor an existing \
master resume to a specific job description.

ABSOLUTE RULES:
- Never invent or alter facts: no new metrics, numbers, percentages, dates, \
employers, job titles, tools, or achievements. If the master does not contain a \
number, your output must not contain one.
- You may only rephrase and reorder content that already exists in the master.
- Every bullet you output must be traceable to a specific master bullet or \
sentence. Rewording for concision and impact is allowed; adding information is not.
- Where the job description uses a particular action verb for a responsibility \
the candidate genuinely has, prefer that verb - but only when it stays truthful.
- Aim for light-to-medium keyword overlap with the job description so it passes \
ATS keyword screens. Do not stuff keywords or list skills the master lacks.
- Keep it to the most relevant ~15-20 bullets total across experience + projects.

Return ONLY a JSON object, no prose, matching exactly:
{
  "summary": "2-3 sentence professional summary, drawn from the master profile",
  "skills": ["ordered, most job-relevant skills that appear in the master"],
  "experience": [
    {"title": "...", "organisation": "...", "dates": "...",
     "bullets": ["reordered / lightly reworded existing bullets"]}
  ],
  "projects": [
    {"name": "...", "bullets": ["..."]}
  ],
  "education": ["lines copied essentially verbatim from the master"],
  "certifications": ["only if clearly relevant to this role"],
  "notes_for_human": ["anything you dropped or were unsure about"]
}
"""


@dataclass
class TailoredResume:
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    experience: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    notes_for_human: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def all_bullets(self) -> list[str]:
        out: list[str] = []
        for entry in self.experience:
            out.extend(entry.get("bullets", []))
        for entry in self.projects:
            out.extend(entry.get("bullets", []))
        return out


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        raise


def tailor_resume(job: JobPosting, blocks: list[Block] | None = None) -> TailoredResume:
    if not CONFIG.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env / README).")
    blocks = blocks or extract_master()

    master_text = "\n".join(f"[{b.kind}] {b.text}" for b in blocks)
    target_roles = ", ".join(CONFIG.target_roles)

    user = f"""TARGET ROLES (candidate is an early-career MRes AI graduate): {target_roles}

=== CANDIDATE CONSTRAINTS (must obey) ===
{CONFIG.resume_constraints}

=== JOB POSTING ===
Company: {job.company}
Title: {job.title}
URL: {job.url}

Description:
{job.description[:9000]}

=== MASTER RESUME (source of truth - do not go beyond it) ===
{master_text[:22000]}
"""

    client = anthropic.Anthropic(api_key=CONFIG.anthropic_api_key)
    message = client.messages.create(
        model=CONFIG.anthropic_model,
        max_tokens=8000,
        system=TAILOR_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = next((b.text for b in message.content if b.type == "text"), "")
    data = _parse_json(raw)

    resume = TailoredResume(
        summary=data.get("summary", ""),
        skills=data.get("skills", []),
        experience=data.get("experience", []),
        projects=data.get("projects", []),
        education=data.get("education", []),
        certifications=data.get("certifications", []),
        notes_for_human=data.get("notes_for_human", []),
    )
    _apply_constraints(resume)
    resume.warnings = _fabrication_check(resume, master_corpus(blocks))
    return resume


# Belt-and-braces enforcement of the user's corrections, even if the model slips.
#   RESUME_REPLACEMENTS  - "old text=>new text" pairs, ';'-separated (literal)
#   RESUME_DROP_PATTERNS - regex; any skill/bullet/cert line matching it is removed
def _replacements() -> list[tuple[str, str]]:
    out = []
    for pair in CONFIG.resume_replacements.split(";"):
        if "=>" in pair:
            old, new = pair.split("=>", 1)
            out.append((old.strip(), new.strip()))
    return out


def _drop_re():
    pat = CONFIG.resume_drop_patterns.strip()
    return re.compile(pat, re.IGNORECASE) if pat else None


def _rewrite(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        if old:
            text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
    return text


def _apply_constraints(resume: TailoredResume) -> None:
    pairs = _replacements()
    drop = _drop_re()

    def clean(items: list[str]) -> list[str]:
        kept = [x for x in items if not (drop and drop.search(x))]
        return [_rewrite(x, pairs) for x in kept]

    resume.summary = _rewrite(resume.summary, pairs).strip()
    resume.certifications = clean(resume.certifications)
    resume.skills = clean(resume.skills)
    resume.education = clean(resume.education)
    for entry in resume.experience + resume.projects:
        if "bullets" in entry:
            entry["bullets"] = clean(entry["bullets"])


# --------------------------------------------------------------------------- #
# 3. Guardrail: catch fabricated numbers / bullets with no master source
# --------------------------------------------------------------------------- #
def _numbers(text: str) -> set[str]:
    out = set()
    for m in NUM_RE.finditer(text):
        tok = m.group(0).strip().lower().rstrip(".,").strip()
        if tok and any(ch.isdigit() for ch in tok):
            out.add(tok)
    return out


def _best_ratio(candidate: str, corpus: list[str]) -> float:
    best = 0.0
    for source in corpus:
        r = difflib.SequenceMatcher(None, candidate.lower(), source.lower()).ratio()
        best = max(best, r)
    return best


def _fabrication_check(resume: TailoredResume, corpus: list[str]) -> list[str]:
    warnings: list[str] = []
    source_numbers: set[str] = set()
    for line in corpus:
        source_numbers |= _numbers(line)

    for text in [resume.summary, *resume.skills, *resume.all_bullets()]:
        if not text:
            continue
        new_nums = _numbers(text) - source_numbers
        # allow bare years like 2024 that appear anywhere in the corpus text
        new_nums = {n for n in new_nums if n not in "".join(corpus).lower()}
        if new_nums:
            warnings.append(f"NEW NUMBER not in master: {sorted(new_nums)} -> {text!r}")

    for bullet in resume.all_bullets():
        ratio = _best_ratio(bullet, corpus)
        if ratio < SIMILARITY_FLOOR:
            warnings.append(
                f"LOW MATCH to master ({ratio:.2f}) - verify this is a real rephrase: {bullet!r}"
            )
    return warnings


# --------------------------------------------------------------------------- #
# 4. Render the tailored resume to a clean, ATS-friendly .docx
# --------------------------------------------------------------------------- #
def build_docx(resume: TailoredResume, job: JobPosting, out_path) -> str:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    app = CONFIG.applicant
    name = doc.add_paragraph()
    run = name.add_run(app.name)
    run.bold = True
    run.font.size = Pt(18)

    contact_bits = [b for b in (app.location, app.email, app.phone) if b]
    if contact_bits:
        doc.add_paragraph(" | ".join(contact_bits)).runs[0].font.size = Pt(9)
    link_bits = [b for b in (app.linkedin, app.github) if b]
    if link_bits:
        doc.add_paragraph(" | ".join(link_bits)).runs[0].font.size = Pt(9)

    def heading(text: str) -> None:
        p = doc.add_paragraph()
        r = p.add_run(text.upper())
        r.bold = True
        r.font.size = Pt(11)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(2)

    if resume.summary:
        heading("Summary")
        doc.add_paragraph(resume.summary)

    if resume.skills:
        heading("Skills")
        doc.add_paragraph(", ".join(resume.skills))

    if resume.experience:
        heading("Experience")
        for entry in resume.experience:
            head = " — ".join(
                b for b in (entry.get("title"), entry.get("organisation")) if b
            )
            p = doc.add_paragraph()
            r = p.add_run(head)
            r.bold = True
            if entry.get("dates"):
                p.add_run(f"    {entry['dates']}").italic = True
            for bullet in entry.get("bullets", []):
                doc.add_paragraph(bullet, style="List Bullet")

    if resume.projects:
        heading("Projects")
        for entry in resume.projects:
            p = doc.add_paragraph()
            p.add_run(entry.get("name", "")).bold = True
            for bullet in entry.get("bullets", []):
                doc.add_paragraph(bullet, style="List Bullet")

    if resume.education:
        heading("Education")
        for line in resume.education:
            doc.add_paragraph(line)

    if resume.certifications:
        heading("Certifications")
        for line in resume.certifications:
            doc.add_paragraph(line, style="List Bullet")

    out_path = str(out_path)
    doc.save(out_path)
    return out_path
