"""Entity extraction route."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ..deps import get_extractor
from ..schemas import EntityExtractRequest, EntityExtractResponse, EntityInfo, EdgeInfo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/entities/extract", response_model=EntityExtractResponse)
def extract_entities(req: EntityExtractRequest):
    """Extract entities and infer relations from text."""
    extractor = get_extractor()

    entities = extractor.extract(req.content)
    edges = extractor.infer_relations(entities, req.content)

    return EntityExtractResponse(
        entities=[EntityInfo(name=e.name, type=e.type) for e in entities],
        edges=[EdgeInfo(source=e.source.name, target=e.target.name, relation=e.relation) for e in edges],
        entity_count=len(entities),
        edge_count=len(edges),
    )
