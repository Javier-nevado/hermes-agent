"""Entity extraction and relation inference for memory content.

Extracts named entities (people, companies, roles, amounts, dates, technologies)
from text and infers typed relationships between co-occurring entities.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Entity:
    name: str
    type: str

@dataclass
class Edge:
    source: Entity
    target: Entity
    relation: str


KNOWN_TECHNOLOGIES = {
    "postgresql", "postgres", "mysql", "mongodb", "redis", "sqlite",
    "react", "vue", "angular", "svelte", "nextjs",
    "python", "javascript", "typescript", "rust", "golang",
    "docker", "kubernetes", "terraform", "ansible",
    "hermes", "proxmox", "opnsense", "pihole",
    "telegram", "slack", "discord", "whatsapp",
    "brevo", "exchange", "sharepoint", "teams",
    "linux", "debian", "ubuntu",
    "supabase", "payload", "wordpress",
    "openai", "anthropic", "claude", "gemini", "llama",
    "aws", "azure", "gcp",
    "fastapi", "flask", "django",
    "pgvector", "onnx",
}

KNOWN_ROLES = {
    "ceo", "cto", "cfo", "coo", "cmo", "cio", "cpo", "cso",
    "vp", "director", "manager", "head", "lead",
    "consultant", "advisor", "partner", "founder",
    "developer", "engineer", "designer", "analyst", "architect",
    "assistant", "intern", "executive",
}

ROLE_RELATION_MAP = {
    "ceo": "CEO_OF",
    "founder": "FOUNDED",
    "cto": "CTO_OF",
    "cfo": "CFO_OF",
    "coo": "COO_OF",
}

# Company suffixes for matching
COMPANY_SUFFIXES = {"limited", "ltd", "inc", "gmbh", "ag", "sa", "bv", "nv", "plc", "corp", "corporation", "llc"}


class EntityExtractor:
    """Extract named entities and infer relations from text."""

    def extract(self, text: str) -> List[Entity]:
        """Extract entities from text, deduplicated by name (best type wins)."""
        # name_lower -> Entity (first match wins for same name)
        seen: Dict[str, Entity] = {}
        # Track which names are already claimed by higher-priority types
        claimed: Dict[str, str] = {}  # name_lower -> type

        # Pass 1: Known technologies (highest priority)
        text_lower = text.lower()
        for tech in KNOWN_TECHNOLOGIES:
            if re.search(r'\b' + re.escape(tech) + r'\b', text_lower):
                key = tech.lower()
                seen[key] = Entity(name=tech, type="technology")
                claimed[key] = "technology"

        # Pass 2: Known roles — match base word first, then check compound
        for match in re.finditer(r'\b([A-Za-z][\w\-]*)\b', text):
            word = match.group(1)
            if word.lower() in KNOWN_ROLES:
                key = word.lower()
                if key not in claimed:
                    seen[key] = Entity(name=word, type="role")
                    claimed[key] = "role"

        # Pass 3: Structured patterns (email, amounts, dates, companies)
        structured_patterns: List[Tuple[str, re.Pattern]] = [
            ("person", re.compile(r'\b([A-Za-z0-9._%+-]+)@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
            ("amount", re.compile(r'([€$]\s?[\d,]+(?:\.\d{1,2})?\s*(?:K|M|B|k|m|b)?(?:/mo|/yr)?)\b')),
            ("amount", re.compile(r'\b((?:EUR|USD|GBP)\s?[\d,]+(?:\.\d{1,2})?\s*(?:K|M|B)?)\b', re.IGNORECASE)),
            ("date", re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')),
            ("date", re.compile(r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b', re.IGNORECASE)),
            ("date", re.compile(r'\b(Q[1-4]\s+\d{4})\b', re.IGNORECASE)),
            ("company", re.compile(r'\b((?:[A-Z][a-zA-Z0-9&]+\s+)+(?:Limited|Ltd|Inc|GmbH|AG|SA|BV|NV|PLC|Corp|Corporation|LLC))\b')),
            ("project", re.compile(r'"([^"]{2,50})"')),
        ]

        for entity_type, pattern in structured_patterns:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                if not name or len(name) < 2:
                    continue
                name = re.sub(r'\s+', ' ', name).strip()
                key = name.lower()
                if key not in claimed:
                    seen[key] = Entity(name=name, type=entity_type)
                    claimed[key] = entity_type

        # Pass 4: Person names (lowest priority, respects existing claims)
        person_patterns: List[re.Pattern] = [
            re.compile(r'(?:by|from|for|of|at|is|was|named)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)'),
            re.compile(r'(?:^|(?:\.\s+))([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)'),
        ]

        for pattern in person_patterns:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                if not name or len(name.split()) < 2:
                    continue
                # Skip if any word is a company suffix (already claimed as company)
                words = name.split()
                if any(w.lower() in COMPANY_SUFFIXES for w in words):
                    continue
                # Skip long fragments
                if len(words) > 4:
                    continue
                key = name.lower()
                if key not in claimed:
                    seen[key] = Entity(name=name, type="person")
                    claimed[key] = "person"

        return list(seen.values())

    def infer_relations(self, entities: List[Entity], text: str) -> List[Edge]:
        """Infer relationships between co-occurring entities."""
        if len(entities) < 2:
            return []

        edges: List[Edge] = []
        persons = [e for e in entities if e.type == "person"]
        companies = [e for e in entities if e.type == "company"]
        roles = [e for e in entities if e.type == "role"]
        amounts = [e for e in entities if e.type == "amount"]
        techs = [e for e in entities if e.type == "technology"]
        projects = [e for e in entities if e.type in ("project", "product")]
        dates = [e for e in entities if e.type == "date"]

        # Person + Role
        for person in persons:
            for role in roles:
                relation = "HAS_ROLE"
                for rk, mapped in ROLE_RELATION_MAP.items():
                    if rk in role.name.lower():
                        relation = mapped
                        break
                edges.append(Edge(source=person, target=role, relation=relation))

        # Person + Company
        for person in persons:
            for company in companies:
                relation = "WORKS_FOR"
                for role in roles:
                    for rk, mapped in ROLE_RELATION_MAP.items():
                        if rk in role.name.lower():
                            relation = mapped
                            break
                edges.append(Edge(source=person, target=company, relation=relation))

        # Company + Amount
        for company in companies:
            for amount in amounts:
                tl = text.lower()
                if any(kw in tl for kw in ("mrr", "revenue", "arr")):
                    relation = "REVENUE"
                elif any(kw in tl for kw in ("pay", "cost", "spend")):
                    relation = "PAYS"
                else:
                    relation = "RELATED_TO"
                edges.append(Edge(source=company, target=amount, relation=relation))

        # Company + Technology
        for company in companies:
            for tech in techs:
                edges.append(Edge(source=company, target=tech, relation="USES"))

        # Person + Project
        for person in persons:
            for project in projects:
                edges.append(Edge(source=person, target=project, relation="WORKS_ON"))

        # Amount + Date
        for amount in amounts:
            for date in dates:
                edges.append(Edge(source=amount, target=date, relation="AS_OF"))

        # Deduplicate
        seen_edges = set()
        deduped = []
        for edge in edges:
            key = (edge.source.name.lower(), edge.target.name.lower(), edge.relation)
            if key not in seen_edges:
                seen_edges.add(key)
                deduped.append(edge)

        return deduped


# ---------------------------------------------------------------------------
# LLM-based extraction (nightly dreamer batch; regex ``extract`` stays the
# hot-path default). The prompt + validation live here so they are unit-
# testable without an LLM; the dreamer wraps them in a hardened batch call.
# ---------------------------------------------------------------------------

# Exactly the types the ``abi_entities.type`` CHECK constraint allows
# (abi/sql/002_memory_intelligence.sql). The LLM is steered to this set so nothing
# it emits can violate the constraint; the synonym map below folds stray labels
# (organization, metric, event, …) back into an allowed type.
LLM_ENTITY_TYPES = (
    "person", "company", "role", "project", "product",
    "amount", "date", "location", "technology", "other",
)

LLM_EXTRACT_SYSTEM_PROMPT = (
    "You extract named entities from a knowledge-worker agent's memory notes to build a "
    "retrieval graph. For EACH memory id, return the durable, specific entities only.\n"
    "Rules:\n"
    "- `type` MUST be one of: " + ", ".join(LLM_ENTITY_TYPES) + ".\n"
    "- Extract ONLY noun phrases and proper nouns (people, organizations, tools, "
    "products, places, dates, amounts, named projects). NEVER extract verbs, actions, "
    "clauses, or sentences (e.g. 'merges', 'self-organizes', 'no cleanup' are NOT entities).\n"
    "- Keep canonical proper-noun casing (e.g. 'PostgreSQL', 'Javier Nevado', 'Opteia', "
    "'ABI', 'Microsoft 365').\n"
    "- Merge surface variants to one canonical entity ('M365' == 'Microsoft 365').\n"
    "- amounts: currency+value+period as written ('€149/mo', 'EUR10K MRR').\n"
    "- dates: ISO 'YYYY-MM-DD' or 'Month YYYY' or 'Qn YYYY'.\n"
    "- Use 'other' for specific quantities/metrics that are not money or dates "
    "(e.g. '29KB', '266 lines', '8 agents').\n"
    "- Use 'company' for organizations, teams, and groups.\n"
    "- Skip generic words, pronouns, stop-words, transient tokens, and the memory's own "
    "boilerplate. A memory may legitimately have zero entities.\n"
    "- Aim for 2-6 entities per memory where they exist.\n"
    "Respond with STRICT JSON ONLY — no prose — in this exact shape:\n"
    '{"items":[{"id":"<id>","entities":[{"name":"...","type":"..."}]}]}'
)

# Synonyms → an allowed LLM_ENTITY_TYPE. Lenient on purpose: the LLM is the noisy
# input, this is the deterministic gate before anything hits the DB constraint.
_TYPE_SYNONYMS = {
    "tech": "technology", "tool": "technology", "tools": "technology",
    "framework": "technology", "software": "technology", "language": "technology",
    "platform": "technology", "library": "technology",
    "service": "product", "feature": "product", "app": "product", "application": "product",
    # organizations/teams/groups → company (no 'organization' type in schema)
    "organization": "company", "organisation": "company", "org": "company",
    "team": "company", "group": "company", "department": "company",
    # quantities/metrics that aren't money → other
    "metric": "other", "metrics": "other", "kpi": "other", "measure": "other",
    "measurement": "other", "stat": "other", "statistic": "other", "quantity": "other",
    "size": "other", "count": "other",
    # events → other (no 'event' type in schema)
    "event": "other", "meeting": "other", "milestone": "other",
    "appointment": "other", "conference": "other",
    "money": "amount", "currency": "amount", "price": "amount",
    "revenue": "amount", "cost": "amount", "fee": "amount",
    "place": "location", "country": "location", "city": "location",
    "region": "location", "address": "location",
    "people": "person", "human": "person", "user": "person", "customer": "person",
    "client": "person",
    "initiative": "project", "task": "project", "program": "project",
    "title": "role", "position": "role", "job": "role",
    "time": "date", "period": "date", "deadline": "date", "timestamp": "date",
}


def _build_type_aliases() -> Dict[str, str]:
    """Precompute {alias → canonical type} including common plural forms."""
    aliases: Dict[str, str] = {}
    for t in LLM_ENTITY_TYPES:
        aliases[t] = t
        if t.endswith("y"):                       # company→companies, technology→technologies
            aliases[t[:-1] + "ies"] = t
        aliases[t + "s"] = t                       # products, events, metrics, roles…
    for k, v in _TYPE_SYNONYMS.items():
        aliases.setdefault(k, v)
    return aliases


_TYPE_ALIASES = _build_type_aliases()


def _canonical_entity_type(raw: str) -> Optional[str]:
    """Map an LLM-supplied type string to a canonical type, or None to drop it."""
    t = (raw or "").strip().lower()
    if not t:
        return None
    if t in _TYPE_ALIASES:
        return _TYPE_ALIASES[t]
    # Final lenient fallback: strip a plural suffix and retry (covers ad-hoc plurals
    # of synonyms the LLM may emit, e.g. 'frameworks', 'positions').
    for suf in ("ies", "es", "s"):
        if t.endswith(suf) and len(t) > len(suf) + 2:
            stem = t[:-len(suf)]
            cand = stem + ("y" if suf == "ies" else "")
            if cand in _TYPE_ALIASES:
                return _TYPE_ALIASES[cand]
    return None


def parse_llm_entity_items(data: Any) -> Dict[str, List[Entity]]:
    """Validate raw LLM JSON → ``{memory_id: [Entity, ...]}``.

    Drops anything malformed, mistyped, blank, or a duplicate within one memory.
    Never raises — bad items simply don't appear.
    """
    out: Dict[str, List[Entity]] = {}
    if not isinstance(data, dict):
        return out
    items = data.get("items")
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id") or "").strip()
        if not mid:
            continue
        ents: List[Entity] = []
        seen = set()
        for e in (it.get("entities") or []):
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").strip()
            etype = _canonical_entity_type(str(e.get("type") or ""))
            # Filter junk: too short, too long, or a bare number/symbol.
            if not name or len(name) < 2 or len(name) > 80 or not etype:
                continue
            if not re.search(r"[A-Za-zÀ-ÿ]", name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            ents.append(Entity(name=name, type=etype))
        out[mid] = ents
    return out


def extract_llm(text: str, client: Any, model: str,
                timeout: float = 60.0, max_tokens: int = 800) -> List[Entity]:
    """LLM entity extraction for a single text (nightly batch / testing).

    Returns ``[]`` on any failure — regex ``EntityExtractor.extract`` remains the
    hot-path default; this is the offline enrichment. The dreamer's batched
    ``extract_llm_batch`` is the production entry point (hardened retries/timeout).
    """
    prompt = "Extract entities. Input JSON:\n" + json.dumps(
        {"items": [{"id": "1", "content": (text or "")[:10000]}]}
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": LLM_EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        content = (resp.choices[0].message.content or "{}") if resp.choices else "{}"
        return parse_llm_entity_items(json.loads(content)).get("1", [])
    except Exception:
        return []

