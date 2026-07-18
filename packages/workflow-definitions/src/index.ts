export const publicationStates = [
  "draft", "needs_approval", "approved", "scheduled", "preparing", "uploading",
  "platform_processing", "published", "retrying", "needs_user_action",
  "rejected_by_platform", "failed", "cancelled",
] as const;

export type PublicationState = (typeof publicationStates)[number];
