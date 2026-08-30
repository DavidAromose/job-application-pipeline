"""Find 3-4 relevant people at the target company.

We look for three archetypes and return the best match for each (plus one extra):
  * recruiter / talent acquisition (esp. early-careers for graduate roles)
  * hiring manager for the target role (engineering / data / ML manager)
  * department head (Head of / Director / VP Engineering or Data)

Providers (set CONTACTS_PROVIDER in .env):
  * "hunter"  - Hunter.io domain search. Free tier = 25 searches/month and it
                returns names, job titles AND email addresses. Recommended.
                Key: https://hunter.io/api-keys
  * "apollo"  - Apollo.io people search. NOTE: Apollo's *free* plan returns 403
                for every search endpoint (people, mixed_people, match). You need
                a paid Apollo plan for this to work. Apollo's organizations/enrich
                endpoint does work on free and we use it to resolve the domain.
  * "auto"    - try Apollo, fall back to Hunter (default)
  * "none"    - skip contact lookup entirely

Respect each provider's API terms and rate limits. This module makes a handful of
requests per run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from config import CONFIG

APOLLO_BASE = "https://api.apollo.io/api/v1"
HUNTER_BASE = "https://api.hunter.io/v2"

RECRUITER_TITLES = [
    "recruiter", "recruiting", "recruitment", "talent acquisition", "talent partner",
    "talent lead", "university recruit", "campus recruit", "early careers",
    "graduate recruitment", "people partner", "people team", "hr business partner",
    "human resources",
]
HIRING_MANAGER_TITLES = [
    "engineering manager", "software engineering manager", "data science manager",
    "machine learning manager", "ai manager", "team lead", "engineering lead",
    "analytics manager", "data manager", "lead engineer", "lead data",
    "principal engineer", "principal developer", "staff engineer", "product lead",
    "product manager", "research lead",
]
DEPARTMENT_HEAD_TITLES = [
    "head of engineering", "vp engineering", "vp of engineering",
    "director of engineering", "head of data", "head of data science",
    "head of ai", "head of analytics", "head of research", "chief technology officer",
    "cto", "chief data", "director of data", "director of research", "vp data",
    "vp product", "head of product",
]
_BUCKETS = [
    ("recruiter", RECRUITER_TITLES),
    ("hiring_manager", HIRING_MANAGER_TITLES),
    ("department_head", DEPARTMENT_HEAD_TITLES),
]


@dataclass
class Contact:
    name: str
    title: str
    email: str
    linkedin: str
    archetype: str
    source: str = ""

    @property
    def email_is_real(self) -> bool:
        return bool(self.email) and "not_unlocked" not in self.email and "@" in self.email


class ContactsError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Domain resolution
# --------------------------------------------------------------------------- #
def _apollo_enrich_domain(company: str, hint_url: str = "") -> str | None:
    if not CONFIG.apollo_api_key:
        return None
    try:
        resp = requests.post(
            f"{APOLLO_BASE}/organizations/enrich",
            headers={"Content-Type": "application/json", "x-api-key": CONFIG.apollo_api_key},
            json={"name": company},
            timeout=20,
        )
        if resp.ok:
            org = resp.json().get("organization") or {}
            return org.get("primary_domain") or org.get("website_url", "").split("//")[-1].split("/")[0] or None
    except requests.RequestException:
        pass
    return None


def _guess_domain(company: str) -> str:
    slug = "".join(ch for ch in company.lower() if ch.isalnum())
    return f"{slug}.com" if slug else ""


def resolve_domain(company: str, hint_url: str = "") -> str | None:
    return _apollo_enrich_domain(company, hint_url) or _guess_domain(company) or None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def _classify_title(title: str) -> str | None:
    t = (title or "").lower()
    for archetype, needles in _BUCKETS:
        if any(n in t for n in needles):
            return archetype
    return None


def _pick(people: list[Contact], limit: int) -> list[Contact]:
    picked: list[Contact] = []
    seen: set[str] = set()
    # one per archetype first
    for archetype, _ in _BUCKETS:
        for c in people:
            key = c.name.lower()
            if c.archetype == archetype and c.name and key not in seen:
                picked.append(c)
                seen.add(key)
                break
    # backfill
    for c in people:
        if len(picked) >= limit:
            break
        key = c.name.lower()
        if c.name and key not in seen:
            picked.append(c)
            seen.add(key)
    return picked[:limit]


# --------------------------------------------------------------------------- #
# Hunter.io provider
# --------------------------------------------------------------------------- #
_DEPT_ARCHETYPE = {"hr": "recruiter", "executive": "department_head", "management": "department_head"}


def _hunter_query(domain: str, department: str | None) -> list[dict]:
    params = {
        "domain": domain,
        "api_key": CONFIG.hunter_api_key,
        "limit": CONFIG.hunter_limit,  # free plan caps at 10
        "type": "personal",
    }
    if department:
        params["department"] = department
    for attempt in range(2):
        resp = requests.get(f"{HUNTER_BASE}/domain-search", params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(3)
            continue
        if not resp.ok:
            raise ContactsError(f"Hunter domain-search -> {resp.status_code}: {resp.text[:300]}")
        return resp.json().get("data", {}).get("emails", [])
    raise ContactsError("Hunter domain-search rate-limited")


def _hunter_people(domain: str) -> list[Contact]:
    if not CONFIG.hunter_api_key:
        raise ContactsError("HUNTER_API_KEY is not set (see .env / README).")

    # One query for recruiters (HR dept), one for technical leaders + decision makers.
    raw: dict[str, dict] = {}
    for dept in ("hr", "it,executive,management"):
        for e in _hunter_query(domain, dept):
            if e.get("value"):
                raw.setdefault(e["value"], e)

    out: list[Contact] = []
    for e in raw.values():
        name = " ".join(p for p in (e.get("first_name"), e.get("last_name")) if p).strip()
        title = e.get("position") or ""
        dept = (e.get("department") or "").lower()
        archetype = _classify_title(title) or _DEPT_ARCHETYPE.get(dept)
        if not name or not archetype:
            continue
        out.append(
            Contact(
                name=name,
                title=title or dept.title(),
                email=e.get("value") or "",
                linkedin=e.get("linkedin") or "",
                archetype=archetype,
                source="hunter",
            )
        )
    # Recruiters first, then hiring managers, then heads (matches _BUCKETS order).
    order = {a: i for i, (a, _) in enumerate(_BUCKETS)}
    out.sort(key=lambda c: order.get(c.archetype, 9))  # Hunter already returns by confidence
    return out


# --------------------------------------------------------------------------- #
# Apollo provider (paid plans only for search)
# --------------------------------------------------------------------------- #
def _apollo_people(domain: str) -> list[Contact]:
    if not CONFIG.apollo_api_key:
        raise ContactsError("APOLLO_API_KEY is not set.")
    headers = {"Content-Type": "application/json", "x-api-key": CONFIG.apollo_api_key}
    out: list[Contact] = []
    for archetype, titles in _BUCKETS:
        resp = requests.post(
            f"{APOLLO_BASE}/mixed_people/search",
            headers=headers,
            json={
                "q_organization_domains_list": [domain],
                "person_titles": titles,
                "page": 1,
                "per_page": 5,
            },
            timeout=30,
        )
        if resp.status_code == 403:
            raise ContactsError(
                "Apollo search is not available on your plan (free plans get 403 "
                "on all search endpoints). Set CONTACTS_PROVIDER=hunter in .env, "
                "or upgrade Apollo."
            )
        if not resp.ok:
            raise ContactsError(f"Apollo mixed_people/search -> {resp.status_code}: {resp.text[:200]}")
        for person in resp.json().get("people", []):
            name = person.get("name") or ""
            email = person.get("email") or ""
            out.append(
                Contact(
                    name=name.strip(),
                    title=(person.get("title") or "").strip(),
                    email=email.strip(),
                    linkedin=(person.get("linkedin_url") or "").strip(),
                    archetype=archetype,
                    source="apollo",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def find_contacts(
    company: str, domain: str | None = None, hint_url: str = "", limit: int = 4
) -> list[Contact]:
    provider = CONFIG.contacts_provider.lower()
    if provider == "none":
        return []

    domain = domain or resolve_domain(company, hint_url)
    if not domain:
        raise ContactsError(
            f"Could not resolve a domain for {company!r}. Pass --domain explicitly."
        )

    if provider == "apollo":
        people = _apollo_people(domain)
    elif provider == "hunter":
        people = _hunter_people(domain)
    else:  # auto
        try:
            people = _apollo_people(domain)
        except ContactsError:
            people = _hunter_people(domain)

    return _pick(people, limit)
