export type WorkspaceRole = "owner" | "editor" | "approver" | "analyst";

export interface EvidenceReference { sourceUrl: string; rawRecordId?: string; }

export interface TrendObservation {
  workspaceId: string;
  entity: string;
  source: string;
  sourceType: string;
  geo: string;
  language: string;
  observedAt: string;
  metrics: Record<string, number>;
  evidence: EvidenceReference;
  raw: unknown;
}

export interface ProviderCapability { id: string; enabled: boolean; reason?: string; }
