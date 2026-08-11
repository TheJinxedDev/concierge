import type { MediaCategory } from "./categories.generated";

export { categoryDefinitionFor, categoryHasCapability, isMediaCategory, mediaCategories, mediaCategoryDefinitions } from "./categories.generated";
export type { CategoryCapability, MediaCategory } from "./categories.generated";

export const consumptionStatuses = [
  "planned",
  "currently_consuming",
  "paused",
  "finished",
  "dropped",
  "sampled",
  "rewatching",
  "rewatched",
  "not_interested",
  "avoiding",
] as const;

export const progressUnits = ["percent", "episode", "chapter", "hour", "installment"] as const;

export type ConsumptionStatus = (typeof consumptionStatuses)[number];
export type ProgressUnit = (typeof progressUnits)[number];

export interface RatingValue {
  score: number;
  rated_on: string;
  provisional?: boolean;
}

export interface ProgressValue {
  status: ConsumptionStatus;
  amount_completed?: number | null;
  unit?: ProgressUnit | null;
  recorded_on: string;
  started_on?: string | null;
  ended_on?: string | null;
  return_intent?: boolean | null;
  reason?: string | null;
}

export const observationScopes = [
  "work", "season", "edition", "platform", "adaptation", "arc", "episode_chapter",
  "character", "scene", "mechanic", "creator", "channel", "video",
] as const;
export type ObservationScope = (typeof observationScopes)[number];

export const observationPolarities = ["positive", "negative", "mixed", "neutral"] as const;
export type ObservationPolarity = (typeof observationPolarities)[number];

export const manualObservationProvenances = [
  "manual", "user_explicit", "external_reference",
] as const;
export type ManualObservationProvenance = (typeof manualObservationProvenances)[number];

export const privacyLevels = [
  "assistant_readable", "private", "exclude_from_recommendations",
] as const;
export type PrivacyLevel = (typeof privacyLevels)[number];

export interface ObservationValue {
  id: string;
  scope: ObservationScope;
  subject_id?: string | null;
  subject_label?: string | null;
  polarity: ObservationPolarity;
  dimension: string;
  text: string;
  provenance: ManualObservationProvenance | "assistant_inferred" | "imported_metadata";
  privacy?: PrivacyLevel;
  source_context?: string | null;
  confidence?: number | null;
  review_state?: "accepted" | "needs_review" | "rejected";
  observed_on: string;
}

export interface ProposalValue {
  id: string;
  target_media_item_id?: string | null;
  kind: "observation" | "metadata" | "media_item";
  proposed_observation?: ObservationValue | null;
  proposed_media_item?: MediaItem | null;
  metadata_field?: string | null;
  metadata_value?: unknown;
  source_context: string;
  confidence: number;
  review_state: "accepted" | "needs_review" | "rejected";
  proposed_on: string;
  promoted_observation_id?: string | null;
  promoted_media_item_id?: string | null;
}

export type CreatorRole =
  | "creator" | "director" | "writer" | "artist" | "developer" | "composer"
  | "performer" | "producer" | "voice_actor" | "other";

export interface CreatorValue {
  id: string;
  name: string;
  aliases?: Array<{ value: string }>;
}

export interface CreatorCreditValue {
  creator_id: string;
  role: CreatorRole;
}

export type RelationshipType =
  | "sequel" | "prequel" | "adaptation" | "remake" | "reboot" | "spin_off"
  | "same_franchise" | "same_creator" | "same_universe" | "different_season"
  | "different_edition" | "channel_video" | "game_expansion" | "main_side_story";

export interface RelationshipValue {
  relationship_type: RelationshipType;
  target_media_item_id: string;
}

export const taxonomyKinds = [
  "genre", "theme", "tone", "demographic", "platform", "label", "attribute",
] as const;
export type TaxonomyKind = (typeof taxonomyKinds)[number];

export interface TaxonomyTermValue {
  kind: TaxonomyKind;
  value: string;
}

export interface MediaItem {
  id: string;
  title: string;
  category: MediaCategory;
  status?: ConsumptionStatus;
  aliases?: Array<{ value: string }>;
  rating?: RatingValue | null;
  rating_history?: RatingValue[];
  progress_records?: ProgressValue[];
  observations?: ObservationValue[];
  terms?: TaxonomyTermValue[];
  credits?: CreatorCreditValue[];
  relationships?: RelationshipValue[];
  [key: string]: unknown;
}
