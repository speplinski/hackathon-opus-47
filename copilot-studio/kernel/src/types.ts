// Data contracts shared by every kernel operation. Field names match
// src/auditable_design/schemas.py so files written by the flow can be
// compared with files written by the Python pipeline.

export interface Utterance {
  utterance_id: string;
  source_type: string; // e.g. "app_review", "nps_comment", "interview", "survey", "complaint"
  text: string;
  date?: string | null;
  segment?: string | null;
  rating?: number | null;
  source_ref?: string | null; // link or locator of the original
}

export interface ComplaintNode {
  node_id: string;
  node_type: string;
  verbatim_quote: string;
  quote_start: number;
  quote_end: number;
}

export interface ComplaintEdge {
  src: string;
  dst: string;
  relation: string;
}

export interface ComplaintGraph {
  review_id: string;
  nodes: ComplaintNode[];
  edges: ComplaintEdge[];
}

export interface InsightCluster {
  cluster_id: string;
  label: string;
  member_review_ids: string[];
  centroid_vector_ref: string;
  representative_quotes: string[];
  ui_context?: string | null;
  html?: string | null;
  screenshot_ref?: string | null;
  representative_review_ids?: string[];
}

export interface HeuristicViolation {
  heuristic: string;
  violation: string;
  severity: number;
  evidence_review_ids: string[];
  reasoning: string;
}

export interface AuditVerdict {
  verdict_id: string;
  cluster_id: string;
  skill_id: string;
  relevant_heuristics: HeuristicViolation[];
  native_payload_ref?: string | null;
  produced_at: string;
  claude_model: string;
  skill_hash: string;
}

export interface SkillTension {
  skill_a: string;
  skill_b: string;
  axis: string;
  resolution: string;
}

export interface ReconciledVerdict {
  cluster_id: string;
  ranked_violations: HeuristicViolation[];
  tensions: SkillTension[];
}

export interface PriorityScore {
  cluster_id: string;
  dimensions: { [k: string]: number };
  meta_weights: { [k: string]: number };
  weighted_total: number;
  validation_passes: number;
  validation_delta: number;
}

export interface DesignDecision {
  decision_id: string;
  principle_id: string;
  description: string;
  before_snapshot: string;
  after_snapshot: string;
  resolves_heuristics: string[];
}

export interface OptimizationIteration {
  iteration_id: string;
  run_id: string;
  iteration_index: number;
  parent_iteration_id: string | null;
  design_artifact_ref: string;
  scores: { [skill: string]: { [heuristic: string]: number } };
  reasoning: string;
  accepted: boolean;
  regression_reason: string | null;
  delta_per_heuristic: { [heuristic: string]: number };
  informing_review_ids: string[];
  recorded_at: string;
}

// Every operation returns this envelope (serialised to a string for the
// flow). `ok:false` means the input was wrong (unknown op, missing key):
// stop the run. A model response that cannot be used comes back as
// `ok:true` with `result.status` "fallback" (or a quarantine record) and
// is written to the run folder like any other result.
export interface KernelResult {
  ok: boolean;
  operation: string;
  result: unknown;
  errors: string[];
}
