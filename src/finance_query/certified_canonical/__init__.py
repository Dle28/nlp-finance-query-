"""Hash-bound, fail-closed foundations for the Certified Canonical Layer."""

from .pipeline import (
    CERTIFIED_CANONICAL_PROTOCOL,
    CertifiedCanonicalError,
    CertifiedCanonicalResult,
    run_certified_canonical,
)
from .bakeoff import BAKEOFF_PROTOCOL, BakeoffResult, build_bakeoff_job
from .model_execution import (
    ModelExecutionResult,
    ProposalValidationResult,
    run_model_route,
    validate_raw_responses,
)
from .evidence_graph import (
    EVIDENCE_GRAPH_PROTOCOL,
    EvidenceGraphError,
    build_packet_evidence_graph,
    validate_evidence_graph,
    validate_proposal_selection,
)
from .phase45 import (
    PHASE45_PROTOCOL,
    Phase45VerificationResult,
    verify_phase45_proposals,
)
from .phase4_layout import (
    PHASE4_LAYOUT_PROTOCOL,
    Phase4HeadingSpanResult,
    materialize_phase4_heading_spans,
)
from .phase5_profiles import (
    PHASE5_PROFILE_PROTOCOL,
    Phase5TableTopicProfileResult,
    build_phase5_table_topic_profiles,
)
from .phase5_header_components import (
    PHASE5_HEADER_COMPONENT_PROTOCOL,
    Phase5HeaderComponentResult,
    verify_phase5_header_components,
)
from .phase5_semantic_compatibility import (
    PHASE5_SEMANTIC_COMPATIBILITY_PROTOCOL,
    Phase5SemanticCompatibilityResult,
    verify_phase5_source_profile_compatibility,
)
from .phase5_semantic_routing import (
    PHASE5_SEMANTIC_ROUTING_PROTOCOL,
    Phase5SemanticRoutingResult,
    build_phase5_source_first_semantic_routes,
)
from .phase5_note_context import (
    PHASE5_NOTE_CONTEXT_PROTOCOL,
    Phase5NumberedNoteContextResult,
    materialize_phase5_numbered_note_context,
)
from .phase4_row_label_context import (
    PHASE4_ROW_LABEL_CONTEXT_PROTOCOL,
    Phase4RowLabelContextResult,
    materialize_phase4_row_label_context,
)
from .phase5_table_structure_context import (
    PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL,
    Phase5TableStructureContextResult,
    materialize_phase5_table_structure_context,
)
from .phase5_component_selection import (
    PHASE5_COMPONENT_SELECTION_PROTOCOL,
    Phase5ComponentSelectionPacketResult,
    Phase5ComponentSelectionValidationResult,
    build_phase5_component_selection_packets,
    render_phase5_component_selection_prompt,
    validate_phase5_component_selection_responses,
)
from .phase5_component_selection_smoke import (
    PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
    Phase5ComponentSelectionSmokeJobResult,
    build_phase5_component_selection_smoke_job,
)

__all__ = [
    "CERTIFIED_CANONICAL_PROTOCOL",
    "BAKEOFF_PROTOCOL",
    "EVIDENCE_GRAPH_PROTOCOL",
    "PHASE45_PROTOCOL",
    "PHASE4_LAYOUT_PROTOCOL",
    "PHASE5_PROFILE_PROTOCOL",
    "PHASE5_HEADER_COMPONENT_PROTOCOL",
    "PHASE5_SEMANTIC_COMPATIBILITY_PROTOCOL",
    "PHASE5_SEMANTIC_ROUTING_PROTOCOL",
    "PHASE5_NOTE_CONTEXT_PROTOCOL",
    "PHASE4_ROW_LABEL_CONTEXT_PROTOCOL",
    "PHASE5_TABLE_STRUCTURE_CONTEXT_PROTOCOL",
    "PHASE5_COMPONENT_SELECTION_PROTOCOL",
    "PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL",
    "BakeoffResult",
    "ModelExecutionResult",
    "ProposalValidationResult",
    "Phase45VerificationResult",
    "Phase4HeadingSpanResult",
    "Phase5TableTopicProfileResult",
    "Phase5HeaderComponentResult",
    "Phase5SemanticCompatibilityResult",
    "Phase5SemanticRoutingResult",
    "Phase5NumberedNoteContextResult",
    "Phase4RowLabelContextResult",
    "Phase5TableStructureContextResult",
    "Phase5ComponentSelectionPacketResult",
    "Phase5ComponentSelectionValidationResult",
    "Phase5ComponentSelectionSmokeJobResult",
    "CertifiedCanonicalError",
    "EvidenceGraphError",
    "CertifiedCanonicalResult",
    "run_certified_canonical",
    "build_bakeoff_job",
    "run_model_route",
    "validate_raw_responses",
    "build_packet_evidence_graph",
    "validate_evidence_graph",
    "validate_proposal_selection",
    "verify_phase45_proposals",
    "materialize_phase4_heading_spans",
    "build_phase5_table_topic_profiles",
    "verify_phase5_header_components",
    "verify_phase5_source_profile_compatibility",
    "build_phase5_source_first_semantic_routes",
    "materialize_phase5_numbered_note_context",
    "materialize_phase4_row_label_context",
    "materialize_phase5_table_structure_context",
    "build_phase5_component_selection_packets",
    "render_phase5_component_selection_prompt",
    "validate_phase5_component_selection_responses",
    "build_phase5_component_selection_smoke_job",
]
