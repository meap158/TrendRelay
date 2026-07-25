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

export type CampaignStatus = "draft" | "active" | "archived";
export type PublicationPlanState = "needs_approval" | "approved" | "rejected" | "cancelled";
export type PublicationPlatform = "tiktok" | "instagram" | "youtube" | "douyin" | "other";

export interface Campaign {
  id: string;
  workspaceId: string;
  name: string;
  objective: string;
  audience: string;
  markets: string[];
  languages: string[];
  affiliateUrl?: string;
  status: CampaignStatus;
}

export interface PublicationPlan {
  id: string;
  workspaceId: string;
  campaignId: string;
  title: string;
  platform: PublicationPlatform;
  videoPath: string;
  videoSha256: string;
  coverPath?: string;
  coverSha256?: string;
  caption: string;
  hashtags: string[];
  affiliateUrl?: string;
  disclosure: string;
  deepLink?: string;
  scheduledAt: string;
  timezone: string;
  state: PublicationPlanState;
}
