"""Entity extraction and relation inference for memory content.

Extracts named entities (people, companies, roles, amounts, dates, technologies)
from text and infers typed relationships between co-occurring entities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple


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
