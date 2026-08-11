import { consumptionStatuses, isMediaCategory, progressUnits, type CreatorRole, type CreatorValue, type MediaCategory, type MediaItem, type ObservationValue, type ProgressValue, type ProposalValue, type RatingValue, type RelationshipType } from "./media";
import { caseFold } from "unicode-case-folding";

export interface BackupReceipt {
  backup_version: string;
  items: number;
  verified: boolean;
}

export class BackupReceiptError extends Error {}

export interface PortableExport {
  schema_version: string;
  exported_on: string;
  creators: unknown[];
  proposals: unknown[];
  recommendations: unknown[];
  media_items: unknown[];
  capture_proposals?: unknown[];
}

export type PortableImportDocument = Record<string, unknown> & {
  schema_version: "1.0" | "1.1" | "1.2" | "1.3" | "1.4" | "1.5" | "1.6" | "1.7" | "1.8";
  exported_on: string;
  media_items: unknown[];
  creators?: unknown[];
  proposals?: unknown[];
  recommendations?: unknown[];
  capture_proposals?: unknown[];
};

export interface ImportReviewEntry {
  id: string;
  label: string;
  action: "create" | "update" | "unchanged" | "remove" | "replay" | "conflict";
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface ImportReviewCollection {
  mode: "merge" | "replace" | "preserve";
  entries: ImportReviewEntry[];
  preserved_ids: string[];
  current_ids: string[];
}

export interface ImportReview {
  review_schema_version: "1.0";
  schema_version: PortableImportDocument["schema_version"];
  review_token: string;
  can_import: boolean;
  blocking_reasons: string[];
  media_items: ImportReviewCollection;
  creators: ImportReviewCollection;
  proposals: ImportReviewCollection;
  recommendations: ImportReviewCollection;
  capture_proposals?: ImportReviewCollection;
}

export interface ImportReceipt {
  imported: number;
}

export class ImportCommitUncertainError extends Error {}
export class ImportReviewStaleError extends Error {}

function hasNoncanonicalContractWhitespace(value: unknown, preserveStrings = false): boolean {
  if (typeof value === "string") return !preserveStrings && value !== value.trim();
  if (Array.isArray(value)) return value.some((entry) => hasNoncanonicalContractWhitespace(entry, preserveStrings));
  if (!isPlainObject(value)) return false;
  return Object.entries(value).some(([key, entry]) =>
    hasNoncanonicalContractWhitespace(entry, preserveStrings || key === "metadata_value"),
  );
}

export function parsePortableImportDocument(payload: unknown): PortableImportDocument {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("The selected file is not a portable export document.");
  }
  const document = payload as Record<string, unknown>;
  const version = document.schema_version;
  if (
    typeof version !== "string" ||
    !["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"].includes(version)
  ) {
    throw new Error("The selected file uses an unsupported schema version.");
  }
  if (
    typeof document.exported_on !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(document.exported_on) ||
    !Array.isArray(document.media_items)
  ) {
    throw new Error("The selected file is not a portable export document.");
  }
  const hasCreators = Array.isArray(document.creators);
  const hasProposals = Array.isArray(document.proposals);
  const hasRecommendations = Array.isArray(document.recommendations);
  if (
    (["1.3", "1.4", "1.5", "1.6", "1.7", "1.8"].includes(String(version)) && !hasCreators) ||
    ((version === "1.0" || version === "1.1" || version === "1.2") && "creators" in document) ||
    (["1.4", "1.5", "1.6", "1.7", "1.8"].includes(String(version)) && !hasProposals) ||
    (!["1.4", "1.5", "1.6", "1.7", "1.8"].includes(String(version)) && "proposals" in document) ||
    (["1.6", "1.7", "1.8"].includes(String(version)) && !hasRecommendations) ||
    (!["1.6", "1.7", "1.8"].includes(String(version)) && "recommendations" in document) ||
    (version === "1.8" && !Array.isArray(document.capture_proposals)) ||
    (version !== "1.8" && "capture_proposals" in document)
  ) {
    throw new Error("The selected file does not match its declared schema version.");
  }
  if (hasNoncanonicalContractWhitespace(payload)) {
    throw new Error("The portable export must use canonical surrounding whitespace.");
  }
  return payload as PortableImportDocument;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]) {
  const actual = Object.keys(value).sort();
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index]);
}

function compareCodePointStrings(left: string, right: string) {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0)!);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0)!);
  for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
  }
  return leftPoints.length - rightPoints.length;
}

function canonicalJson(value: unknown): string {
  const normalize = (candidate: unknown): unknown => {
    if (Array.isArray(candidate)) return candidate.map(normalize);
    if (!isPlainObject(candidate)) return candidate;
    return Object.fromEntries(
      Object.keys(candidate).sort().map((key) => [key, normalize(candidate[key])]),
    );
  };
  return JSON.stringify(normalize(value));
}

function parseImportReviewEntry(value: unknown, actions: string[]): ImportReviewEntry {
  if (!isPlainObject(value) || !hasExactKeys(value, ["id", "label", "action", "before", "after"]) ||
      typeof value.id !== "string" || !value.id.trim() ||
      typeof value.label !== "string" || !value.label.trim() ||
      typeof value.action !== "string" || !actions.includes(value.action) ||
      !(value.before === null || isPlainObject(value.before)) ||
      !(value.after === null || isPlainObject(value.after))) {
    throw new Error("invalid import review entry");
  }
  const beforeRequired = ["update", "unchanged", "remove", "replay", "conflict"].includes(value.action);
  const afterRequired = ["create", "update", "unchanged", "replay", "conflict"].includes(value.action);
  if ((value.action === "remove" && value.label !== value.id) ||
      (beforeRequired && value.before === null) || (!beforeRequired && value.before !== null) ||
      (afterRequired && value.after === null) || (!afterRequired && value.after !== null) ||
      (value.before !== null && value.before.id !== value.id) ||
      (value.after !== null && value.after.id !== value.id)) {
    throw new Error("invalid import review snapshots");
  }
  const snapshotsMatch = value.before !== null && value.after !== null &&
    canonicalJson(value.before) === canonicalJson(value.after);
  if ((["unchanged", "replay"].includes(value.action) && !snapshotsMatch) ||
      (["update", "conflict"].includes(value.action) && snapshotsMatch)) {
    throw new Error("invalid import review action");
  }
  return value as unknown as ImportReviewEntry;
}

function parseImportReviewCollection(
  value: unknown,
  mode: ImportReviewCollection["mode"],
  actions: string[],
): ImportReviewCollection {
  if (!isPlainObject(value) || !hasExactKeys(value, ["mode", "entries", "preserved_ids", "current_ids"]) ||
      value.mode !== mode || !Array.isArray(value.entries) || !Array.isArray(value.preserved_ids) ||
      !Array.isArray(value.current_ids) ||
      value.preserved_ids.some((id) => typeof id !== "string" || !id.trim()) ||
      value.current_ids.some((id) => typeof id !== "string" || !id.trim())) {
    throw new Error("invalid import review collection");
  }
  const entries = value.entries.map((entry) => parseImportReviewEntry(entry, actions));
  const ids = entries.map((entry) => entry.id);
  const preservedIds = value.preserved_ids as string[];
  const currentIds = value.current_ids as string[];
  const sortedUnique = (values: string[]) => new Set(values).size === values.length &&
    !values.some((id, index) => index > 0 && compareCodePointStrings(values[index - 1], id) > 0);
  const representedCurrentIds = [
    ...preservedIds,
    ...entries.filter((entry) => entry.before !== null).map((entry) => entry.id),
  ].sort(compareCodePointStrings);
  if (new Set(ids).size !== ids.length || ids.some((id, index) => index > 0 && compareCodePointStrings(ids[index - 1], id) > 0) ||
      !sortedUnique(preservedIds) || !sortedUnique(currentIds) ||
      preservedIds.some((id) => ids.includes(id)) ||
      (mode === "replace" && preservedIds.length > 0) ||
      canonicalJson(representedCurrentIds) !== canonicalJson(currentIds)) {
    throw new Error("invalid import review ordering");
  }
  return { mode, entries, preserved_ids: preservedIds, current_ids: currentIds };
}

function cloneContractValue(value: unknown, preserveNulls = false): unknown {
  if (value === null) return preserveNulls ? null : undefined;
  if (Array.isArray(value)) return value.map((entry) => cloneContractValue(entry, preserveNulls));
  if (!isPlainObject(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, entry]) => [key, cloneContractValue(entry, preserveNulls || key === "metadata_value")])
      .filter(([, entry]) => entry !== undefined),
  );
}

function normalizeRating(value: unknown): unknown {
  if (!isPlainObject(value)) return value;
  return { ...cloneContractValue(value) as Record<string, unknown>, provisional: value.provisional ?? false };
}

function normalizeObservation(value: unknown): unknown {
  if (!isPlainObject(value)) return value;
  return {
    ...cloneContractValue(value) as Record<string, unknown>,
    privacy: value.privacy ?? "assistant_readable",
    review_state: value.review_state ?? "accepted",
  };
}

function normalizeIncomingRecord(source: Record<string, unknown>, collection: "media" | "creators" | "proposals" | "recommendations" | "capture_proposals") {
  const normalized = cloneContractValue(source) as Record<string, unknown>;
  if (collection === "creators") {
    normalized.aliases = Array.isArray(source.aliases) ? source.aliases.map((value) => cloneContractValue(value)) : [];
  } else if (collection === "media") {
    normalized.aliases = Array.isArray(source.aliases) ? source.aliases.map((value) => cloneContractValue(value)) : [];
    normalized.terms = Array.isArray(source.terms) ? source.terms.map((value) => cloneContractValue(value)) : [];
    normalized.relationships = Array.isArray(source.relationships) ? source.relationships.map((value) => cloneContractValue(value)) : [];
    normalized.credits = Array.isArray(source.credits) ? source.credits.map((value) => cloneContractValue(value)) : [];
    normalized.progress_records = Array.isArray(source.progress_records)
      ? source.progress_records.map((value) => cloneContractValue(value)) : [];
    normalized.observations = Array.isArray(source.observations)
      ? source.observations.map(normalizeObservation) : [];
    const rating = source.rating === null || source.rating === undefined ? undefined : normalizeRating(source.rating);
    const history = Array.isArray(source.rating_history)
      ? source.rating_history.map(normalizeRating)
      : rating === undefined ? [] : [rating];
    normalized.rating_history = history;
    if (rating !== undefined) normalized.rating = rating;
    else if (history.length > 0) normalized.rating = history.at(-1);
  } else if (collection === "proposals") {
    normalized.review_state = source.review_state ?? "needs_review";
    if (source.proposed_observation !== null && source.proposed_observation !== undefined) {
      normalized.proposed_observation = normalizeObservation(source.proposed_observation);
    }
  } else if (collection === "capture_proposals") {
    return normalized;
  } else {
    normalized.evidence = Array.isArray(source.evidence) ? source.evidence.map((value) => cloneContractValue(value)) : [];
    normalized.outcomes = Array.isArray(source.outcomes) ? source.outcomes.map((value) => cloneContractValue(value)) : [];
  }
  return normalized;
}

function validateIncomingEffects(
  collection: ImportReviewCollection,
  incoming: unknown[],
  labelKey: "title" | "name" | "id",
  collectionKind: "media" | "creators" | "proposals" | "recommendations" | "capture_proposals",
) {
  if (incoming.some((value) => !isPlainObject(value) || typeof value.id !== "string" || !value.id.trim())) {
    throw new Error("invalid portable import records");
  }
  const incomingRecords = incoming as Record<string, unknown>[];
  const incomingIds = incomingRecords.map((value) => value.id as string).sort(compareCodePointStrings);
  const effectIds = collection.entries
    .filter((entry) => entry.after !== null)
    .map((entry) => entry.id)
    .sort(compareCodePointStrings);
  if (new Set(incomingIds).size !== incomingIds.length || canonicalJson(incomingIds) !== canonicalJson(effectIds)) {
    throw new Error("invalid import review effects");
  }
  const effectById = new Map(collection.entries.map((entry) => [entry.id, entry]));
  for (const record of incomingRecords) {
    const id = record.id as string;
    const effect = effectById.get(id);
    const expectedLabel = labelKey === "id" ? id : record[labelKey];
    if (!effect || effect.after === null || typeof expectedLabel !== "string" ||
        effect.label !== expectedLabel ||
        canonicalJson(normalizeIncomingRecord(record, collectionKind)) !== canonicalJson(effect.after)) {
      throw new Error("invalid import review effects");
    }
  }
}

function bindCurrentReviewSnapshots(review: ImportReview, current: PortableImportDocument) {
  const collections = [
    [review.media_items, current.media_items, "media"],
    [review.creators, current.creators ?? [], "creators"],
    [review.proposals, current.proposals ?? [], "proposals"],
    [review.recommendations, current.recommendations ?? [], "recommendations"],
  ] as Array<[ImportReviewCollection, unknown[], "media" | "creators" | "proposals" | "recommendations" | "capture_proposals"]>;
  if (review.capture_proposals) {
    collections.push([review.capture_proposals, current.capture_proposals ?? [], "capture_proposals"]);
  }
  for (const [collection, records, kind] of collections) {
    if (records.some((record) => !isPlainObject(record) || typeof record.id !== "string" || !record.id.trim())) {
      throw new Error("invalid current portable records");
    }
    const currentRecords = records as Record<string, unknown>[];
    const currentIds = currentRecords.map((record) => record.id as string).sort(compareCodePointStrings);
    if (new Set(currentIds).size !== currentIds.length ||
        canonicalJson(currentIds) !== canonicalJson(collection.current_ids)) {
      throw new Error("current portable records do not match the review");
    }
    const currentById = new Map(currentRecords.map((record) => [record.id as string, record]));
    for (const entry of collection.entries) {
      if (entry.before === null) continue;
      const currentRecord = currentById.get(entry.id);
      if (!currentRecord || canonicalJson(normalizeIncomingRecord(currentRecord, kind)) !== canonicalJson(entry.before)) {
        throw new Error("current portable snapshot does not match the review");
      }
    }
  }
}

function parseImportReview(payload: unknown, document: PortableImportDocument): ImportReview {
  const expectedKeys = [
    "review_schema_version", "schema_version", "review_token", "can_import", "blocking_reasons", "media_items", "creators", "proposals", "recommendations",
    ...(document.schema_version === "1.8" ? ["capture_proposals"] : []),
  ];
  if (!isPlainObject(payload) || !hasExactKeys(payload, expectedKeys) || payload.review_schema_version !== "1.0" || payload.schema_version !== document.schema_version ||
      typeof payload.review_token !== "string" || !/^[0-9a-f]{64}$/.test(payload.review_token) ||
      typeof payload.can_import !== "boolean" || !Array.isArray(payload.blocking_reasons) ||
      payload.blocking_reasons.some((reason) => typeof reason !== "string" || !reason.trim()) ||
      new Set(payload.blocking_reasons).size !== payload.blocking_reasons.length) {
    throw new Error("invalid import review envelope");
  }
  const proposalMode = ["1.4", "1.5", "1.6", "1.7", "1.8"].includes(document.schema_version) ? "replace" : "preserve";
  const recommendationMode = ["1.6", "1.7", "1.8"].includes(document.schema_version) ? "merge" : "preserve";
  const captureProposalMode = document.schema_version === "1.8" ? "replace" : "preserve";
  const review: ImportReview = {
    review_schema_version: "1.0",
    schema_version: document.schema_version,
    review_token: payload.review_token,
    can_import: payload.can_import,
    blocking_reasons: payload.blocking_reasons as string[],
    media_items: parseImportReviewCollection(payload.media_items, "merge", ["create", "update", "unchanged"]),
    creators: parseImportReviewCollection(payload.creators, "merge", ["create", "update", "unchanged"]),
    proposals: parseImportReviewCollection(
      payload.proposals,
      proposalMode,
      proposalMode === "replace" ? ["create", "update", "unchanged", "remove"] : [],
    ),
    recommendations: parseImportReviewCollection(
      payload.recommendations,
      recommendationMode,
      recommendationMode === "merge" ? ["create", "replay", "conflict"] : [],
    ),
  };
  if (document.schema_version === "1.8") {
    review.capture_proposals = parseImportReviewCollection(
      payload.capture_proposals,
      captureProposalMode,
      ["create", "update", "unchanged", "remove"],
    );
  }
  validateIncomingEffects(review.media_items, document.media_items, "title", "media");
  validateIncomingEffects(review.creators, document.creators ?? [], "name", "creators");
  validateIncomingEffects(review.proposals, document.proposals ?? [], "id", "proposals");
  validateIncomingEffects(review.recommendations, document.recommendations ?? [], "id", "recommendations");
  if (review.capture_proposals) {
    validateIncomingEffects(review.capture_proposals, document.capture_proposals ?? [], "id", "capture_proposals");
  }
  const hasConflict = review.recommendations.entries.some((entry) => entry.action === "conflict");
  const expectedCanImport = review.blocking_reasons.length === 0 && !hasConflict;
  if (review.can_import !== expectedCanImport ||
      (!review.can_import && review.blocking_reasons.length === 0) ||
      (proposalMode === "preserve" && review.proposals.entries.length > 0) ||
      (recommendationMode === "preserve" && review.recommendations.entries.length > 0) ||
      (captureProposalMode === "preserve" && review.capture_proposals?.entries.length)) {
    throw new Error("invalid import review decision");
  }
  return review;
}

export async function reviewImportLibrary(document: PortableImportDocument): Promise<ImportReview> {
  const response = await fetch("/api/import/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(document),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = isPlainObject(payload) && typeof payload.detail === "string" ? payload.detail : undefined;
    throw new Error(detail || "The portable import could not be reviewed.");
  }
  try {
    const review = parseImportReview(payload, document);
    const currentResponse = await fetch("/api/export");
    const currentPayload: unknown = await currentResponse.json().catch(() => null);
    if (!currentResponse.ok) throw new Error("current portable export unavailable");
    bindCurrentReviewSnapshots(review, parsePortableImportDocument(currentPayload));
    return review;
  } catch (error) {
    throw new Error(`The portable import review could not be verified: ${error instanceof Error ? error.message : "unknown error"}`);
  }
}

export async function importLibrary(
  document: PortableImportDocument,
  reviewToken: string,
): Promise<ImportReceipt> {
  let response: Response;
  try {
    response = await fetch(`/api/import?review_token=${encodeURIComponent(reviewToken)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(document),
    });
  } catch {
    throw new ImportCommitUncertainError(
      "The import request could not be confirmed. The library will be refreshed before editing continues.",
    );
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    const message = typeof detail === "string" ? detail : "The portable export was rejected.";
    if (response.status === 409 && response.headers.get("X-Error-Code") === "import-review-stale") {
      throw new ImportReviewStaleError(message);
    }
    throw new Error(message);
  }
  const imported = payload && typeof payload === "object" && "imported" in payload
    ? (payload as { imported?: unknown }).imported
    : undefined;
  if (!Number.isInteger(imported) || Number(imported) < 0) {
    throw new ImportCommitUncertainError(
      "The import may have completed, but its receipt could not be verified.",
    );
  }
  return { imported: Number(imported) };
}

export async function exportLibrary(): Promise<PortableExport> {
  const response = await fetch("/api/export");
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    throw new Error(typeof detail === "string" ? detail : "The portable export could not be created.");
  }
  let document: PortableImportDocument;
  try {
    document = parsePortableImportDocument(payload);
  } catch {
    throw new Error("The portable export could not be verified.");
  }
  if (!["1.6", "1.7", "1.8"].includes(document.schema_version)) {
    throw new Error("The portable export could not be verified.");
  }
  return document as PortableExport;
}

function parseBackupReceipt(payload: unknown): BackupReceipt {
  if (
    !payload ||
    typeof payload !== "object" ||
    !("backup_version" in payload) ||
    typeof (payload as { backup_version?: unknown }).backup_version !== "string" ||
    !(payload as { backup_version: string }).backup_version.trim() ||
    (payload as { verified?: unknown }).verified !== true ||
    !Number.isInteger((payload as { items?: unknown }).items) ||
    Number((payload as { items?: unknown }).items) < 0
  ) {
    throw new BackupReceiptError("The local backup could not be verified.");
  }
  return payload as BackupReceipt;
}

async function backupRequest(url: string, failureMessage: string): Promise<BackupReceipt> {
  const response = await fetch(url, { method: "POST" });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    throw new Error(typeof detail === "string" ? detail : failureMessage);
  }
  return parseBackupReceipt(payload);
}

export async function createBackup(): Promise<BackupReceipt> {
  return backupRequest("/api/backup", "The local backup could not be created.");
}

export async function restoreBackup(): Promise<BackupReceipt> {
  return backupRequest("/api/backup/restore", "The local backup could not be restored.");
}

export async function saveCreator(creator: CreatorValue): Promise<CreatorValue> {
  const response = await fetch("/api/creators", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(creator),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "The creator could not be saved.");
  }
  return response.json() as Promise<CreatorValue>;
}

export async function listMediaForCreator(creatorId: string, includeArchived = false): Promise<MediaItem[]> {
  const suffix = includeArchived ? "?include_archived=true" : "";
  const response = await fetch(`/api/creators/${encodeURIComponent(creatorId)}/media${suffix}`);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error("The creator work index is unavailable.");
  if (!Array.isArray(payload) || payload.some((item) => (
    !item || typeof item !== "object" ||
    typeof (item as { id?: unknown }).id !== "string" || !(item as { id: string }).id.trim() ||
    typeof (item as { title?: unknown }).title !== "string" || !(item as { title: string }).title.trim()
  )) || new Set(payload.map((item) => (item as { id: string }).id)).size !== payload.length) {
    throw new Error("The creator work index could not be verified.");
  }
  return payload as MediaItem[];
}

export async function listCreators(signal?: AbortSignal): Promise<CreatorValue[]> {
  const response = await fetch("/api/creators", { signal });
  if (!response.ok) throw new Error("Creator identities are unavailable.");
  return response.json() as Promise<CreatorValue[]>;
}

export async function listProposals(signal?: AbortSignal): Promise<ProposalValue[]> {
  const response = await fetch("/api/proposals", { signal });
  if (!response.ok) throw new Error("Inference proposals are unavailable.");
  return response.json() as Promise<ProposalValue[]>;
}

export async function reviewProposal(
  proposalId: string,
  outcome: "accept" | "reject",
): Promise<ProposalValue> {
  const response = await fetch(`/api/proposals/${encodeURIComponent(proposalId)}/${outcome}`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("The inference review could not be saved.");
  return response.json() as Promise<ProposalValue>;
}

export async function promoteProposal(
  proposalId: string,
): Promise<{ proposal: ProposalValue; media_item: MediaItem }> {
  const response = await fetch(`/api/proposals/${encodeURIComponent(proposalId)}/promote`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("The accepted inference could not be promoted.");
  return response.json() as Promise<{ proposal: ProposalValue; media_item: MediaItem }>;
}

export async function promoteMediaProposal(
  proposalId: string,
): Promise<{ proposal: ProposalValue; media_item: MediaItem }> {
  const response = await fetch(`/api/proposals/${encodeURIComponent(proposalId)}/promote-media`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("The accepted media candidate could not be promoted.");
  return response.json() as Promise<{ proposal: ProposalValue; media_item: MediaItem }>;
}

export interface DuplicateCandidate {
  media_item_id: string;
  candidate_media_item_id: string;
  matched_titles: string[];
  certainty: "possible";
  rationale: string;
}

export async function listDuplicateCandidates(includeArchived = false): Promise<DuplicateCandidate[]> {
  const response = await fetch(
    includeArchived ? "/api/duplicates/candidates?include_archived=true" : "/api/duplicates/candidates",
  );
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error("Possible duplicates could not be loaded.");
  }
  if (!Array.isArray(payload) || payload.some((candidate) => (
    !candidate || typeof candidate !== "object" ||
    typeof (candidate as { media_item_id?: unknown }).media_item_id !== "string" ||
    !(candidate as { media_item_id: string }).media_item_id.trim() ||
    typeof (candidate as { candidate_media_item_id?: unknown }).candidate_media_item_id !== "string" ||
    !(candidate as { candidate_media_item_id: string }).candidate_media_item_id.trim() ||
    !Array.isArray((candidate as { matched_titles?: unknown }).matched_titles) ||
    (candidate as { matched_titles: unknown[] }).matched_titles.length === 0 ||
    !(candidate as { matched_titles: unknown[] }).matched_titles.every((title) => (
      typeof title === "string" && Boolean(title.trim())
    )) ||
    (candidate as { certainty?: unknown }).certainty !== "possible" ||
    typeof (candidate as { rationale?: unknown }).rationale !== "string" ||
    !(candidate as { rationale: string }).rationale.trim()
  ))) {
    throw new Error("Possible duplicate evidence could not be verified.");
  }
  return payload as DuplicateCandidate[];
}

export interface RatingHistoryProfileEntry {
  media_item_id: string;
  title: string;
  category: MediaCategory;
  current_rating: RatingValue;
  rating_history: RatingValue[];
  supporting_evidence: ObservationValue[];
  contradictory_evidence: ObservationValue[];
  context_evidence: ObservationValue[];
}

export interface RatingHistoryProfile { entries: RatingHistoryProfileEntry[]; }

function isIsoCalendarDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function isRating(value: unknown): value is RatingValue {
  if (!value || typeof value !== "object") return false;
  const rating = value as Record<string, unknown>;
  return typeof rating.score === "number" && Number.isFinite(rating.score) &&
    rating.score >= 1 && rating.score <= 10 &&
    isIsoCalendarDate(rating.rated_on) &&
    (rating.provisional === undefined || typeof rating.provisional === "boolean");
}

function isProfileObservation(value: unknown): value is ObservationValue {
  if (!value || typeof value !== "object") return false;
  const observation = value as Record<string, unknown>;
  return typeof observation.id === "string" && Boolean(observation.id.trim()) &&
    typeof observation.dimension === "string" && Boolean(observation.dimension.trim()) &&
    typeof observation.text === "string" && Boolean(observation.text.trim()) &&
    ["positive", "negative", "mixed", "neutral"].includes(String(observation.polarity)) &&
    isIsoCalendarDate(observation.observed_on);
}

export async function loadRatingHistoryProfile(includeArchived = false): Promise<RatingHistoryProfile> {
  const response = await fetch(
    includeArchived ? "/api/profile/rating-history?include_archived=true" : "/api/profile/rating-history",
  );
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error("Cited rating history could not be loaded.");
  return parseRatingHistoryProfile(payload);
}

function parseRatingHistoryProfile(payload: unknown): RatingHistoryProfile {
  if (!payload || typeof payload !== "object" || !Array.isArray((payload as { entries?: unknown }).entries)) {
    throw new Error("Cited rating history could not be verified.");
  }
  const entries = (payload as { entries: unknown[] }).entries;
  const seenIds = new Set<string>();
  for (const value of entries) {
    if (!value || typeof value !== "object") throw new Error("Cited rating history could not be verified.");
    const entry = value as Record<string, unknown>;
    if (
      typeof entry.media_item_id !== "string" || !entry.media_item_id.trim() || seenIds.has(entry.media_item_id) ||
      typeof entry.title !== "string" || !entry.title.trim() ||
      !isMediaCategory(entry.category) ||
      !isRating(entry.current_rating) || !Array.isArray(entry.rating_history) || entry.rating_history.length === 0 ||
      !entry.rating_history.every(isRating) ||
      !Array.isArray(entry.supporting_evidence) || !entry.supporting_evidence.every(isProfileObservation) ||
      !entry.supporting_evidence.every((observation) => observation.polarity === "positive") ||
      !Array.isArray(entry.contradictory_evidence) || !entry.contradictory_evidence.every(isProfileObservation) ||
      !entry.contradictory_evidence.every((observation) => observation.polarity === "negative") ||
      !Array.isArray(entry.context_evidence) || !entry.context_evidence.every(isProfileObservation) ||
      !entry.context_evidence.every((observation) => observation.polarity === "mixed" || observation.polarity === "neutral")
    ) throw new Error("Cited rating history could not be verified.");
    const evidence = [
      ...(entry.supporting_evidence as ObservationValue[]),
      ...(entry.contradictory_evidence as ObservationValue[]),
      ...(entry.context_evidence as ObservationValue[]),
    ];
    if (new Set(evidence.map((observation) => observation.id)).size !== evidence.length) {
      throw new Error("Cited rating history could not be verified.");
    }
    const history = entry.rating_history as RatingValue[];
    const current = entry.current_rating as RatingValue;
    const latest = history[history.length - 1];
    if (
      history.some((rating, index) => index > 0 && rating.rated_on < history[index - 1].rated_on) ||
      current.score !== latest.score || current.rated_on !== latest.rated_on ||
      Boolean(current.provisional) !== Boolean(latest.provisional)
    ) throw new Error("Cited rating history could not be verified.");
    seenIds.add(entry.media_item_id);
  }
  return payload as RatingHistoryProfile;
}

export interface DimensionProfileEntry {
  media_item_id: string;
  title: string;
  category: MediaCategory;
  current_rating: RatingValue | null;
  supporting_evidence: ObservationValue[];
  contradictory_evidence: ObservationValue[];
  context_evidence: ObservationValue[];
}

export interface DimensionProfile {
  dimension: string;
  entries: DimensionProfileEntry[];
}

// Python's str.strip() uses Unicode White_Space plus U+001C–U+001F (29 code points total);
// JavaScript trim() differs at those controls and at U+FEFF.
const pythonStripWhitespace = /^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028-\u2029\u202F\u205F\u3000]+|[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028-\u2029\u202F\u205F\u3000]+$/gu;

export function normalizeDimension(value: string): string {
  return caseFold(value.replace(pythonStripWhitespace, ""));
}

function dimensionsMatch(first: string, second: string): boolean {
  return normalizeDimension(first) === normalizeDimension(second);
}

export async function loadDimensionProfile(dimension: string, includeArchived = false): Promise<DimensionProfile> {
  const requested = dimension.trim();
  if (!requested) throw new Error("Enter an evidence dimension to review.");
  const path = `/api/profile/dimensions/${encodeURIComponent(requested)}`;
  const response = await fetch(includeArchived ? `${path}?include_archived=true` : path);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error("The cited dimension profile could not be loaded.");
  return parseDimensionProfile(payload, requested);
}

function parseDimensionProfile(payload: unknown, requested: string): DimensionProfile {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("The cited dimension profile could not be verified.");
  }
  const profile = payload as Record<string, unknown>;
  if (
    typeof profile.dimension !== "string" || !profile.dimension.trim() ||
    profile.dimension !== profile.dimension.trim() ||
    profile.dimension !== profile.dimension.toLowerCase() ||
    !dimensionsMatch(profile.dimension, requested) ||
    !Array.isArray(profile.entries)
  ) throw new Error("The cited dimension profile could not be verified.");

  const seenIds = new Set<string>();
  for (const value of profile.entries) {
    if (!value || typeof value !== "object") throw new Error("The cited dimension profile could not be verified.");
    const entry = value as Record<string, unknown>;
    if (
      typeof entry.media_item_id !== "string" || !entry.media_item_id.trim() || seenIds.has(entry.media_item_id) ||
      typeof entry.title !== "string" || !entry.title.trim() ||
      !isMediaCategory(entry.category) ||
      !(entry.current_rating === null || isRating(entry.current_rating)) ||
      !Array.isArray(entry.supporting_evidence) || !entry.supporting_evidence.every(isProfileObservation) ||
      !entry.supporting_evidence.every((observation) => observation.polarity === "positive") ||
      !Array.isArray(entry.contradictory_evidence) || !entry.contradictory_evidence.every(isProfileObservation) ||
      !entry.contradictory_evidence.every((observation) => observation.polarity === "negative") ||
      !Array.isArray(entry.context_evidence) || !entry.context_evidence.every(isProfileObservation) ||
      !entry.context_evidence.every((observation) => observation.polarity === "mixed" || observation.polarity === "neutral")
    ) throw new Error("The cited dimension profile could not be verified.");
    const evidence = [
      ...(entry.supporting_evidence as ObservationValue[]),
      ...(entry.contradictory_evidence as ObservationValue[]),
      ...(entry.context_evidence as ObservationValue[]),
    ];
    if (
      evidence.length === 0 ||
      new Set(evidence.map((observation) => observation.id)).size !== evidence.length ||
      evidence.some((observation) => !dimensionsMatch(observation.dimension, profile.dimension as string))
    ) throw new Error("The cited dimension profile could not be verified.");
    seenIds.add(entry.media_item_id);
  }
  return payload as DimensionProfile;
}

export interface TasteProfileReport {
  rating_history: RatingHistoryProfile;
  progress_context: ProgressContext;
  creator_context: CreatorContext;
  relationship_context: RelationshipContext;
  dimensions: DimensionProfile[];
}

export interface ResolvedRelationship { relationship_type: RelationshipType; target_media_item_id: string; target_title: string; target_category: MediaCategory; }
export interface RelationshipContextEntry { media_item_id: string; title: string; category: MediaCategory; relationships: ResolvedRelationship[]; }
export interface RelationshipContext { entries: RelationshipContextEntry[]; }

const relationshipTypes: RelationshipType[] = ["sequel", "prequel", "adaptation", "remake", "reboot", "spin_off", "same_franchise", "same_creator", "same_universe", "different_season", "different_edition", "channel_video", "game_expansion", "main_side_story"];

function parseRelationshipContext(value: unknown): RelationshipContext {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== 1 || !Array.isArray((value as { entries?: unknown }).entries)) throw new Error("invalid relationship context");
  const entries = (value as { entries: unknown[] }).entries;
  const seenMedia = new Set<string>();
  for (const candidate of entries) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) throw new Error("invalid relationship entry");
    const entry = candidate as Record<string, unknown>;
    if (Object.keys(entry).length !== 4 || Object.keys(entry).some((key) => !["media_item_id", "title", "category", "relationships"].includes(key)) || typeof entry.media_item_id !== "string" || !entry.media_item_id.trim() || seenMedia.has(entry.media_item_id) || typeof entry.title !== "string" || !entry.title.trim() || !isMediaCategory(entry.category) || !Array.isArray(entry.relationships) || entry.relationships.length === 0) throw new Error("invalid relationship entry");
    const seenRelationships = new Set<string>();
    for (const candidateRelationship of entry.relationships) {
      if (!candidateRelationship || typeof candidateRelationship !== "object" || Array.isArray(candidateRelationship)) throw new Error("invalid resolved relationship");
      const relationship = candidateRelationship as Record<string, unknown>;
      const identity = `${String(relationship.target_media_item_id)}\u0000${String(relationship.relationship_type)}`;
      if (Object.keys(relationship).length !== 4 || Object.keys(relationship).some((key) => !["relationship_type", "target_media_item_id", "target_title", "target_category"].includes(key)) || !relationshipTypes.includes(relationship.relationship_type as RelationshipType) || typeof relationship.target_media_item_id !== "string" || !relationship.target_media_item_id.trim() || relationship.target_media_item_id === entry.media_item_id || typeof relationship.target_title !== "string" || !relationship.target_title.trim() || !isMediaCategory(relationship.target_category) || seenRelationships.has(identity)) throw new Error("invalid resolved relationship");
      seenRelationships.add(identity);
    }
    seenMedia.add(entry.media_item_id);
  }
  return value as RelationshipContext;
}

export interface ResolvedCreatorCredit { creator_id: string; creator_name: string; role: CreatorRole; }
export interface CreatorContextEntry { media_item_id: string; title: string; category: MediaCategory; credits: ResolvedCreatorCredit[]; }
export interface CreatorContext { entries: CreatorContextEntry[]; }

const creatorRoles: CreatorRole[] = ["creator", "director", "writer", "artist", "developer", "composer", "performer", "producer", "voice_actor", "other"];

function parseCreatorContext(value: unknown): CreatorContext {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== 1 || !Array.isArray((value as { entries?: unknown }).entries)) {
    throw new Error("invalid creator context");
  }
  const entries = (value as { entries: unknown[] }).entries;
  const seenMedia = new Set<string>();
  const creatorNames = new Map<string, string>();
  for (const candidate of entries) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) throw new Error("invalid creator entry");
    const entry = candidate as Record<string, unknown>;
    const keys = Object.keys(entry);
    if (keys.length !== 4 || keys.some((key) => !["media_item_id", "title", "category", "credits"].includes(key)) ||
        typeof entry.media_item_id !== "string" || !entry.media_item_id.trim() || seenMedia.has(entry.media_item_id) ||
        typeof entry.title !== "string" || !entry.title.trim() ||
        !isMediaCategory(entry.category) ||
        !Array.isArray(entry.credits) || entry.credits.length === 0) throw new Error("invalid creator entry");
    const seenCredits = new Set<string>();
    for (const candidateCredit of entry.credits) {
      if (!candidateCredit || typeof candidateCredit !== "object" || Array.isArray(candidateCredit)) throw new Error("invalid creator credit");
      const credit = candidateCredit as Record<string, unknown>;
      const creditKeys = Object.keys(credit);
      const identity = `${String(credit.creator_id)}\u0000${String(credit.role)}`;
      if (creditKeys.length !== 3 || creditKeys.some((key) => !["creator_id", "creator_name", "role"].includes(key)) ||
          typeof credit.creator_id !== "string" || !credit.creator_id.trim() || typeof credit.creator_name !== "string" || !credit.creator_name.trim() ||
          !creatorRoles.includes(credit.role as CreatorRole) || seenCredits.has(identity)) throw new Error("invalid creator credit");
      const knownName = creatorNames.get(credit.creator_id);
      if (knownName !== undefined && knownName !== credit.creator_name) throw new Error("inconsistent creator identity");
      creatorNames.set(credit.creator_id, credit.creator_name);
      seenCredits.add(identity);
    }
    seenMedia.add(entry.media_item_id);
  }
  return value as CreatorContext;
}

export interface ProgressContextEntry {
  media_item_id: string;
  title: string;
  category: MediaCategory;
  current_status: ProgressValue["status"];
  progress_history: ProgressValue[];
}

export interface ProgressContext { entries: ProgressContextEntry[]; }

function isProgress(value: unknown): value is ProgressValue {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const progress = value as Record<string, unknown>;
  const keys = Object.keys(progress);
  const allowedKeys = ["status", "amount_completed", "unit", "recorded_on", "started_on", "ended_on", "return_intent", "reason"];
  if (keys.length !== allowedKeys.length || keys.some((key) => !allowedKeys.includes(key))) return false;
  if (
    !consumptionStatuses.includes(progress.status as ProgressValue["status"]) ||
    !isIsoCalendarDate(progress.recorded_on) ||
    !(progress.amount_completed === null ||
      (typeof progress.amount_completed === "number" && Number.isFinite(progress.amount_completed) && progress.amount_completed >= 0)) ||
    !(progress.unit === null || progressUnits.includes(progress.unit as NonNullable<ProgressValue["unit"]>)) ||
    !(progress.started_on === null || isIsoCalendarDate(progress.started_on)) ||
    !(progress.ended_on === null || isIsoCalendarDate(progress.ended_on)) ||
    !(progress.return_intent === null || typeof progress.return_intent === "boolean") ||
    !(progress.reason === null || (typeof progress.reason === "string" && Boolean(progress.reason.trim())))
  ) return false;
  return !(typeof progress.started_on === "string" && typeof progress.ended_on === "string" && progress.ended_on < progress.started_on);
}

function parseProgressContext(value: unknown): ProgressContext {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== 1 || !Array.isArray((value as { entries?: unknown }).entries)) {
    throw new Error("invalid progress context");
  }
  const entries = (value as { entries: unknown[] }).entries;
  const seenIds = new Set<string>();
  for (const candidate of entries) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) throw new Error("invalid progress entry");
    const entry = candidate as Record<string, unknown>;
    const entryKeys = Object.keys(entry);
    if (
      entryKeys.length !== 5 || entryKeys.some((key) => !["media_item_id", "title", "category", "current_status", "progress_history"].includes(key)) ||
      typeof entry.media_item_id !== "string" || !entry.media_item_id.trim() || seenIds.has(entry.media_item_id) ||
      typeof entry.title !== "string" || !entry.title.trim() ||
      !isMediaCategory(entry.category) ||
      !consumptionStatuses.includes(entry.current_status as ProgressValue["status"]) ||
      !Array.isArray(entry.progress_history) || entry.progress_history.length === 0 || !entry.progress_history.every(isProgress)
    ) throw new Error("invalid progress entry");
    seenIds.add(entry.media_item_id);
  }
  return value as ProgressContext;
}

export async function loadTasteProfileReport(includeArchived = false): Promise<TasteProfileReport> {
  const response = await fetch(
    includeArchived ? "/api/profile/report?include_archived=true" : "/api/profile/report",
  );
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error("The cited taste report could not be loaded.");
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("The cited taste report could not be verified.");
  }
  const report = payload as Record<string, unknown>;
  const reportKeys = Object.keys(report);
  if (reportKeys.length !== 5 || reportKeys.some((key) => !["rating_history", "progress_context", "creator_context", "relationship_context", "dimensions"].includes(key)) || !Array.isArray(report.dimensions)) {
    throw new Error("The cited taste report could not be verified.");
  }
  try {
    const ratingHistory = parseRatingHistoryProfile(report.rating_history);
    const progressContext = parseProgressContext(report.progress_context);
    const creatorContext = parseCreatorContext(report.creator_context);
    const relationshipContext = parseRelationshipContext(report.relationship_context);
    const dimensions = report.dimensions.map((value) => {
      if (!value || typeof value !== "object" || typeof (value as { dimension?: unknown }).dimension !== "string") {
        throw new Error("invalid dimension profile");
      }
      const profile = parseDimensionProfile(value, (value as { dimension: string }).dimension);
      if (profile.entries.length === 0) throw new Error("empty report dimension");
      return profile;
    });
    const normalizedDimensions = dimensions.map((profile) => normalizeDimension(profile.dimension));
    if (new Set(normalizedDimensions).size !== normalizedDimensions.length) {
      throw new Error("duplicate report dimension");
    }
    return { rating_history: ratingHistory, progress_context: progressContext, creator_context: creatorContext, relationship_context: relationshipContext, dimensions };
  } catch {
    throw new Error("The cited taste report could not be verified.");
  }
}

export async function listMedia(
  query = "",
  signal?: AbortSignal,
  includeArchived = false,
): Promise<MediaItem[]> {
  const params = new URLSearchParams();
  if (query) params.set("title", query);
  if (includeArchived) params.set("include_archived", "true");
  const queryString = params.toString();
  const url = queryString ? `/api/media?${queryString}` : "/api/media";
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error("The local library is unavailable.");
  }
  return response.json() as Promise<MediaItem[]>;
}

export async function saveMedia(item: MediaItem): Promise<MediaItem> {
  const response = await fetch(`/api/media/${encodeURIComponent(item.id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: unknown }
      | null;
    const detail = typeof payload?.detail === "string" ? payload.detail : "";
    throw new Error(detail || "The local library could not save this record.");
  }
  return response.json() as Promise<MediaItem>;
}

export async function createMedia(item: MediaItem): Promise<MediaItem> {
  const response = await fetch("/api/media", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { detail?: unknown }
      | null;
    const detail = typeof payload?.detail === "string" ? payload.detail : "";
    throw new Error(detail || "The local library could not create this record.");
  }
  return response.json() as Promise<MediaItem>;
}

export async function archiveMedia(itemId: string): Promise<MediaItem> {
  const response = await fetch(`/api/media/${encodeURIComponent(itemId)}/archive`, {
    method: "POST",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "The record could not be archived.");
  }
  return response.json() as Promise<MediaItem>;
}

export async function restoreMedia(itemId: string): Promise<MediaItem> {
  const response = await fetch(`/api/media/${encodeURIComponent(itemId)}/restore`, {
    method: "POST",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "The record could not be restored.");
  }
  return response.json() as Promise<MediaItem>;
}

export type RecommendationSource = "assistant" | "user" | "external";
export type RecommendationOutcomeKind = "initial_response" | "tried" | "opinion" | "success_assessment";

export interface RecommendationEvidenceRef {
  media_item_id: string;
  observation_id: string;
}

export interface RecommendationOutcomeEvent {
  id: string;
  kind: RecommendationOutcomeKind;
  recorded_on: string;
  text?: string;
  successful?: boolean;
}

export interface RecommendationRecord {
  id: string;
  media_item_id: string;
  recommended_on: string;
  source: RecommendationSource;
  source_context?: string;
  rationale: string;
  evidence?: RecommendationEvidenceRef[];
  confidence?: number;
  outcomes?: RecommendationOutcomeEvent[];
}

export interface RecommendationMutationReceipt {
  created: boolean;
  recommendation: RecommendationRecord;
}

const recommendationSources: RecommendationSource[] = ["assistant", "user", "external"];
const recommendationOutcomeKinds: RecommendationOutcomeKind[] = ["initial_response", "tried", "opinion", "success_assessment"];

function isNonblankText(value: unknown): value is string {
  return typeof value === "string" && Boolean(value.trim());
}

function isRecommendationOutcome(value: unknown): value is RecommendationOutcomeEvent {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const event = value as Record<string, unknown>;
  const keys = Object.keys(event);
  if (keys.some((key) => !["id", "kind", "recorded_on", "text", "successful"].includes(key)) ||
      !isNonblankText(event.id) ||
      !recommendationOutcomeKinds.includes(event.kind as RecommendationOutcomeKind) ||
      !isIsoCalendarDate(event.recorded_on)) return false;
  if (event.kind === "initial_response" || event.kind === "opinion") {
    return isNonblankText(event.text) && event.successful === undefined;
  }
  if (event.kind === "tried") return event.text === undefined && event.successful === undefined;
  return typeof event.successful === "boolean" && (event.text === undefined || isNonblankText(event.text));
}

function sameRecommendationOutcome(left: RecommendationOutcomeEvent, right: RecommendationOutcomeEvent) {
  return left.id === right.id && left.kind === right.kind && left.recorded_on === right.recorded_on &&
    left.text === right.text && left.successful === right.successful;
}

function parseRecommendation(value: unknown): RecommendationRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid recommendation");
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => ![
    "id", "media_item_id", "recommended_on", "source", "source_context", "rationale", "evidence", "confidence", "outcomes",
  ].includes(key)) ||
      !isNonblankText(record.id) || !isNonblankText(record.media_item_id) ||
      !isIsoCalendarDate(record.recommended_on) ||
      !recommendationSources.includes(record.source as RecommendationSource) ||
      !isNonblankText(record.rationale) ||
      !(record.source_context === undefined || isNonblankText(record.source_context)) ||
      !(record.confidence === undefined ||
        (typeof record.confidence === "number" && Number.isFinite(record.confidence) && record.confidence >= 0 && record.confidence <= 1))) {
    throw new Error("invalid recommendation");
  }
  if ((record.source === "assistant" || record.source === "external") && !isNonblankText(record.source_context)) {
    throw new Error("invalid recommendation source");
  }
  if (record.source === "assistant" && typeof record.confidence !== "number") {
    throw new Error("invalid assistant confidence");
  }
  const evidence = record.evidence ?? [];
  if (!Array.isArray(evidence) || evidence.some((candidate) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return true;
    const ref = candidate as Record<string, unknown>;
    return Object.keys(ref).length !== 2 || !isNonblankText(ref.media_item_id) || !isNonblankText(ref.observation_id);
  })) throw new Error("invalid recommendation evidence");
  const evidenceKeys = evidence.map((candidate) => {
    const ref = candidate as RecommendationEvidenceRef;
    return `${ref.media_item_id}\u0000${ref.observation_id}`;
  });
  if (new Set(evidenceKeys).size !== evidenceKeys.length) throw new Error("duplicate recommendation evidence");

  const outcomes = record.outcomes ?? [];
  if (!Array.isArray(outcomes) || !outcomes.every(isRecommendationOutcome)) throw new Error("invalid recommendation outcomes");
  const outcomeRecords = outcomes as RecommendationOutcomeEvent[];
  if (new Set(outcomeRecords.map((event) => event.id)).size !== outcomeRecords.length ||
      outcomeRecords.some((event) => event.recorded_on < (record.recommended_on as string)) ||
      outcomeRecords.some((event, index) => index > 0 && outcomeRecords[index - 1].recorded_on > event.recorded_on) ||
      outcomeRecords.filter((event) => event.kind === "initial_response").length > 1 ||
      outcomeRecords.filter((event) => event.kind === "tried").length > 1) {
    throw new Error("invalid recommendation outcome history");
  }
  const tried = outcomeRecords.find((event) => event.kind === "tried");
  if (outcomeRecords.some((event) =>
    (event.kind === "opinion" || event.kind === "success_assessment") &&
    (!tried || tried.recorded_on > event.recorded_on))) {
    throw new Error("invalid recommendation outcome sequence");
  }
  return value as RecommendationRecord;
}

function parseRecommendationList(value: unknown): RecommendationRecord[] {
  if (!Array.isArray(value)) throw new Error("invalid recommendation list");
  const records = value.map(parseRecommendation);
  if (new Set(records.map((record) => record.id)).size !== records.length ||
      records.some((record, index) => index > 0 && records[index - 1].recommended_on > record.recommended_on)) {
    throw new Error("invalid recommendation ordering");
  }
  return records;
}

export async function listRecommendations(): Promise<RecommendationRecord[]> {
  const response = await fetch("/api/recommendations");
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error("Recommendation history is unavailable.");
  try {
    return parseRecommendationList(payload);
  } catch {
    throw new Error("Recommendation history could not be verified.");
  }
}

export async function appendRecommendationOutcome(
  recommendationId: string,
  outcome: RecommendationOutcomeEvent,
): Promise<RecommendationMutationReceipt> {
  if (!isRecommendationOutcome(outcome)) throw new Error("The recommendation outcome is invalid.");
  const response = await fetch(`/api/recommendations/${encodeURIComponent(recommendationId)}/outcomes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(outcome),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    throw new Error(typeof detail === "string" ? detail : "The recommendation outcome could not be recorded.");
  }
  try {
    if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
        Object.keys(payload).length !== 2 || typeof (payload as { created?: unknown }).created !== "boolean") {
      throw new Error("invalid receipt");
    }
    const recommendation = parseRecommendation((payload as { recommendation?: unknown }).recommendation);
    if (recommendation.id !== recommendationId ||
        !(recommendation.outcomes ?? []).some((event) => sameRecommendationOutcome(event, outcome))) {
      throw new Error("impossible receipt");
    }
    return { created: (payload as { created: boolean }).created, recommendation };
  } catch {
    throw new Error("The recommendation outcome receipt could not be verified.");
  }
}
