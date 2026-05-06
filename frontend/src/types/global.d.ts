/** @file This file contains global type definitions for the application. */

declare global {
  type ISODateString = string;
  type KgNodeKind =
    | "framework"
    | "standard_item"
    | "learning_component"
    | "unknown";
  type RelationshipEntity = string;
  type RelationshipEntityKey = string;
  type RelationshipType =
    | "hasChild"
    | "supports"
    | "buildsTowards"
    | "relatesTo";
  type UUIDString = string;

  interface GraphNode {
    id: string;
    labels: string[];
    properties: NodeProperties;
  }

  interface GraphRelationship {
    end: UUIDString;
    id: UUIDString;
    properties: RelationshipProperties;
    start: UUIDString;
    type: RelationshipType;
  }

  interface KnowledgeGraph {
    doc_key: string;
    export_dialect: string;
    generated_at: string;
    graph_type: string;
    included_graph_types: string[];
    nodes: GraphNode[];
    relationships: GraphRelationship[];
  }

  interface KnowledgeGraphIndexes {
    frameworks: GraphNode[];
    lcByIdentifier: Map<string, GraphNode>;
    learningComponents: GraphNode[];
    nodesById: Map<string, GraphNode>;
    relsByEnd: Map<string, GraphRelationship[]>;
    relsByStart: Map<string, GraphRelationship[]>;
    sfis: GraphNode[];
    sfisByIdentifier: Map<string, GraphNode>;
    unknownNodes: GraphNode[];
  }

  interface KnowledgeGraphContext extends KnowledgeGraphIndexes {
    kg: KnowledgeGraph;
  }

  interface NodeMetadata {
    // Metadata is intentionally extensible.
    [key: string]: unknown;

    // Common StandardsFrameworkItem metadata.
    bbox?: number[];
    bbox_ref?: string;
    canonical_node_id?: UUIDString;
    canonical_path_key?: string;
    // Framework metadata.
    decision_set_id?: string;
    doc_key?: string;
    export_dialect?: string;
    // Common LearningComponent metadata.
    id_source_kind?: string;
    identity_basis?: string;
    llm_rationale?: string;
    local_code?: string | null;
    normalized_text?: string;

    page_indices?: number[];
    pdf_name?: string;
    progression_context?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    provenance_context?: Record<string, unknown>;
    role?: string;
    source_decision_ids?: string[];
    source_label?: string;
    source_segment_ids?: string[];
    split_display_text?: string;
    split_hash?: string;
    split_id_text?: string;
    split_index?: number;
    split_policy?: string;
    split_truncated?: boolean;
    supporting_sfi_aux_statements?: unknown[];
    supporting_sfi_canonical_path_key?: string;
    supporting_sfi_case_uuid?: UUIDString;
    supporting_sfi_grade_level?: string[];

    supporting_sfi_in_language?: string;
    supporting_sfi_normalized_statement_type?: string;
    supporting_sfi_progression_context?: Record<string, unknown>;
    supporting_sfi_role?: string;
    supporting_sfi_source_label?: string;

    supporting_sfi_statement_type?: string;
  }

  interface NodeProperties {
    [key: string]: unknown;
    academic_subject?: string;
    adoption_status?: string;
    attribution_statement?: string;
    author?: string;
    case_identifier_uri?: string;
    case_identifier_uuid?: UUIDString;
    date_created?: ISODateString | null;
    date_modified?: ISODateString | null;
    description?: string | null;
    grade_level?: string[];
    identifier: UUIDString;
    in_language?: string;
    jurisdiction?: string;
    license?: string;
    metadata?: NodeMetadata;
    name?: string;
    normalized_statement_type?:
      | "Standard"
      | "Standard Grouping"
      | "Other"
      | (string & {});
    notes?: string | null;
    provider?: string;
    statement_code?: string | null;

    statement_type?: string | null;
  }

  interface RelationshipMetadata {
    // Metadata is intentionally extensible.
    [key: string]: unknown;

    // relatesTo metadata.
    bidirectional_confirmed?: boolean;
    candidate_order_index?: number;
    candidate_uid?: string;
    canonical_child_id?: UUIDString;
    canonical_edge_source_decision_ids?: string[];
    canonical_edge_source_segment_ids?: string[];
    // hasChild metadata.
    canonical_order_index?: number;

    canonical_parent_id?: UUIDString;

    confidence?: number;
    dedupe?: Record<string, unknown>;
    evidence?: string | Record<string, unknown>;
    export_order_index?: number;
    export_parent_id?: UUIDString;
    inference_context_scope?: string;
    inference_source?: string;
    inference_type?: string;
    level_label?: string;
    // buildsTowards metadata.
    lp_bucket_key?: string;
    lp_thread_key?: string;
    // Learning progression metadata: buildsTowards/relatesTo.
    phase?: string;
    progression_subtype?: string;
    progression_subtype_source?: string;

    sampled_a_count?: number;
    sampled_a_sfi_uuids?: UUIDString[];
    sampled_b_count?: number;
    sampled_b_sfi_uuids?: UUIDString[];
    source_kg?: string;
    source_sfi_context?: Record<string, unknown>;
    source_sfi_inference_context?: Record<string, unknown>;

    subject_a?: string;
    subject_b?: string;
    subject_label?: string;
    // supports metadata.
    supporting_sfi_case_uuid?: UUIDString;
    target_sfi_context?: Record<string, unknown>;
    target_sfi_inference_context?: Record<string, unknown>;
    thread_a_path?: string;
    thread_b_path?: string;
    topic_path_examples?: unknown[];

    topic_path_keys?: string[];
  }

  interface RelationshipProperties {
    [key: string]: unknown;
    attribution_statement?: string;
    author?: string;
    date_created?: ISODateString | null;
    date_modified?: ISODateString | null;
    description?: string;
    identifier?: UUIDString;
    license?: string;
    metadata?: RelationshipMetadata;
    order_index?: number;
    provider?: string;
    relationship_type?: RelationshipType;
    source_entity?: RelationshipEntity;
    source_entity_key?: RelationshipEntityKey;
    source_entity_value?: UUIDString;
    target_entity?: RelationshipEntity;
    target_entity_key?: RelationshipEntityKey;

    target_entity_value?: UUIDString;
  }
}


