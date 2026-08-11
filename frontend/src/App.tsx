import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from "react";

import { archiveMedia, BackupReceiptError, createBackup, createMedia, type DimensionProfile, type DimensionProfileEntry, type DuplicateCandidate, exportLibrary, ImportCommitUncertainError, importLibrary, type ImportReview, type ImportReviewCollection, ImportReviewStaleError, listCreators, listDuplicateCandidates, listMedia, listMediaForCreator, listProposals, loadDimensionProfile, loadRatingHistoryProfile, loadTasteProfileReport, normalizeDimension, parsePortableImportDocument, type PortableImportDocument, promoteMediaProposal, promoteProposal, type RatingHistoryProfileEntry, restoreBackup, restoreMedia, reviewImportLibrary, reviewProposal, saveCreator, saveMedia, type TasteProfileReport } from "./api";
import {
  categoryHasCapability,
  consumptionStatuses,
  manualObservationProvenances,
  mediaCategories,
  observationPolarities,
  observationScopes,
  privacyLevels,
  progressUnits,
  taxonomyKinds,
  type ConsumptionStatus,
  type CreatorRole,
  type CreatorValue,
  type ManualObservationProvenance,
  type MediaCategory,
  type MediaItem,
  type ObservationPolarity,
  type ObservationScope,
  type ObservationValue,
  type PrivacyLevel,
  type ProposalValue,
  type ProgressUnit,
  type ProgressValue,
  type RatingValue,
  type RelationshipType,
  type TaxonomyKind,
  type TaxonomyTermValue,
} from "./media";
import { RecommendationJournal } from "./RecommendationJournal";

const maximumImportBytes = 5 * 1024 * 1024;

export function emptyLibraryActionDisabled(editorLocked: boolean, dirty: boolean) {
  return editorLocked || dirty;
}

export function EmptyLibraryState({
  editorLocked,
  dirty,
  onStartCreate,
  onReviewPortableExport,
  onRestoreLocalBackup,
}: {
  editorLocked: boolean;
  dirty: boolean;
  onStartCreate: () => void;
  onReviewPortableExport: () => void;
  onRestoreLocalBackup: () => void;
}) {
  const actionsDisabled = emptyLibraryActionDisabled(editorLocked, dirty);
  return (
    <main className="editor empty-state first-library-state">
      <p className="eyebrow">Welcome to Concierge</p>
      <h1>Your library begins here</h1>
      <p>
        Start with one thing you remember. Concierge keeps the details, changes of mind, and evidence you add over time without asking you to fill the whole shelf at once.
      </p>
      <div className="first-library-actions" aria-label="Ways to begin your library">
        <button className="primary" type="button" disabled={actionsDisabled} onClick={onStartCreate}>
          Add your first entry
        </button>
        <button className="secondary" type="button" disabled={actionsDisabled} onClick={onReviewPortableExport}>
          Review a portable export
        </button>
        <button className="secondary" type="button" disabled={actionsDisabled} onClick={onRestoreLocalBackup}>
          Restore a local backup
        </button>
      </div>
      <p className="first-library-note">
        Portable exports are reviewed before merge; restoring uses only the latest verified local backup.
      </p>
    </main>
  );
}

function labelFor(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function ImportReviewCollectionView({
  title,
  collection,
}: {
  title: string;
  collection: ImportReviewCollection;
}) {
  return (
    <section className="import-review-collection" aria-label={`${title} import changes`}>
      <div className="import-review-collection-heading">
        <h4>{title}</h4>
        <span>{labelFor(collection.mode)}</span>
      </div>
      {collection.entries.length === 0 ? <p>No changes in this collection.</p> : (
        <ul>
          {collection.entries.map((entry) => (
            <li key={entry.id} className={`import-review-entry import-review-${entry.action}`}>
              <strong>{labelFor(entry.action)} · {entry.label}</strong>
              <span>{entry.id}</span>
              <details>
                <summary>Inspect exact snapshots</summary>
                <div className="import-review-snapshots">
                  <div>
                    <span>Before</span>
                    <pre>{entry.before === null ? "Not present" : JSON.stringify(entry.before, null, 2)}</pre>
                  </div>
                  <div>
                    <span>After</span>
                    <pre>{entry.after === null ? "Not present" : JSON.stringify(entry.after, null, 2)}</pre>
                  </div>
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
      {collection.preserved_ids.length > 0 ? (
        <div className="import-review-preserved">
          <strong>Preserved outside this document</strong>
          <ul>
            {collection.preserved_ids.map((id) => <li key={id}>{id}</li>)}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function compareCodePointStrings(left: string, right: string) {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0)!);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0)!);
  for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
  }
  return leftPoints.length - rightPoints.length;
}

function reportProgressSummary(record: NonNullable<MediaItem["progress_records"]>[number]) {
  const parts = [`${record.recorded_on}: ${labelFor(record.status)}`];
  if (record.amount_completed !== undefined && record.amount_completed !== null) {
    const unit = record.unit ? labelFor(record.unit).toLowerCase() : "completed";
    parts.push(`${record.amount_completed} ${unit}${record.amount_completed === 1 || unit === "completed" ? "" : "s"}`);
  } else if (record.unit) {
    parts.push(`unit: ${labelFor(record.unit)}`);
  }
  if (record.started_on) parts.push(`started ${record.started_on}`);
  if (record.ended_on) parts.push(`ended ${record.ended_on}`);
  if (record.return_intent === true) parts.push("plans to return");
  if (record.return_intent === false) parts.push("does not plan to return");
  if (record.reason) parts.push(record.reason);
  return parts.join(" · ");
}

function suggestCreatorId(name: string) {
  const slug = name.trim().toLowerCase().normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "");
  return slug ? `creator-${slug}` : "";
}

function emptyMediaItem(): MediaItem {
  return { id: "", title: "", category: "movie", status: "planned" };
}

function suggestStableId(category: MediaCategory, title: string) {
  const slug = title
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-|-$/g, "");
  return slug ? `${category}-${slug}` : "";
}

interface AliasValue {
  value: string;
}

function localCalendarDate() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function aliasesFor(item: MediaItem): AliasValue[] {
  if (!Array.isArray(item.aliases)) return [];
  return item.aliases.filter(
    (alias): alias is AliasValue =>
      typeof alias === "object" && alias !== null && typeof alias.value === "string",
  );
}

function ratingHistoryFor(item: MediaItem): RatingValue[] {
  return Array.isArray(item.rating_history) ? item.rating_history as RatingValue[] : [];
}

function progressRecordsFor(item: MediaItem): ProgressValue[] {
  return Array.isArray(item.progress_records) ? item.progress_records : [];
}

function observationsFor(item: MediaItem): ObservationValue[] {
  return Array.isArray(item.observations) ? item.observations : [];
}

function taxonomyTermsFor(item: MediaItem): TaxonomyTermValue[] {
  return Array.isArray(item.terms) ? item.terms : [];
}

function nextObservationId(item: MediaItem) {
  const existing = new Set(observationsFor(item).map((observation) => observation.id));
  let sequence = observationsFor(item).length + 1;
  let candidate = `obs-${item.id}-${sequence}`;
  while (existing.has(candidate)) {
    sequence += 1;
    candidate = `obs-${item.id}-${sequence}`;
  }
  return candidate;
}

function progressSummary(record: ProgressValue) {
  const status = labelFor(record.status);
  if (record.amount_completed != null && record.unit) {
    const unit = `${record.unit}${record.amount_completed === 1 ? "" : "s"}`;
    return `${status} · ${record.amount_completed} ${unit}`;
  }
  if (record.amount_completed != null) return `${status} · ${record.amount_completed} completed`;
  if (record.unit) return `${status} · Unit: ${labelFor(record.unit)}`;
  return status;
}

function progressLifecycleSummary(record: ProgressValue) {
  return [
    record.started_on ? `Started ${record.started_on}` : null,
    record.ended_on ? `Ended ${record.ended_on}` : null,
  ].filter(Boolean).join(" · ");
}

function currentRatingFor(item: MediaItem): RatingValue | null {
  if (
    typeof item.rating !== "object" ||
    item.rating === null ||
    !("score" in item.rating) ||
    !("rated_on" in item.rating) ||
    typeof item.rating.score !== "number" ||
    typeof item.rating.rated_on !== "string"
  ) return null;
  return item.rating as unknown as RatingValue;
}

export function App() {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [selected, setSelected] = useState<MediaItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [editorMode, setEditorMode] = useState<"edit" | "create">("edit");
  const [createBaseline, setCreateBaseline] = useState<MediaItem | null>(null);
  const [stableIdTouched, setStableIdTouched] = useState(false);
  const [aliasDraft, setAliasDraft] = useState("");
  const [taxonomyKind, setTaxonomyKind] = useState<TaxonomyKind>("genre");
  const [taxonomyValue, setTaxonomyValue] = useState("");
  const [ratingScore, setRatingScore] = useState("");
  const [ratingDate, setRatingDate] = useState(localCalendarDate);
  const [ratingProvisional, setRatingProvisional] = useState(false);
  const [progressStatus, setProgressStatus] = useState<ConsumptionStatus>("planned");
  const [progressAmount, setProgressAmount] = useState("");
  const [progressUnit, setProgressUnit] = useState<ProgressUnit | "">("");
  const [progressDate, setProgressDate] = useState(localCalendarDate);
  const [progressStartedOn, setProgressStartedOn] = useState("");
  const [progressEndedOn, setProgressEndedOn] = useState("");
  const [progressReturnIntent, setProgressReturnIntent] = useState<"" | "true" | "false">("");
  const [progressReason, setProgressReason] = useState("");
  const [observationScope, setObservationScope] = useState<ObservationScope>("work");
  const [observationSubjectId, setObservationSubjectId] = useState("");
  const [observationSubjectLabel, setObservationSubjectLabel] = useState("");
  const [observationPolarity, setObservationPolarity] = useState<ObservationPolarity>("mixed");
  const [observationDimension, setObservationDimension] = useState("");
  const [observationText, setObservationText] = useState("");
  const [observationProvenance, setObservationProvenance] =
    useState<ManualObservationProvenance>("manual");
  const [observationPrivacy, setObservationPrivacy] =
    useState<PrivacyLevel>("assistant_readable");
  const [observationSourceContext, setObservationSourceContext] = useState("");
  const [observationDate, setObservationDate] = useState(localCalendarDate);
  const [observationAttempted, setObservationAttempted] = useState(false);
  const [proposals, setProposals] = useState<ProposalValue[]>([]);
  const [proposalsLoaded, setProposalsLoaded] = useState(false);
  const [proposalsLoading, setProposalsLoading] = useState(false);
  const [proposalError, setProposalError] = useState("");
  const [reviewingProposalId, setReviewingProposalId] = useState("");
  const [promotingProposalId, setPromotingProposalId] = useState("");
  const [proposalReviewMessage, setProposalReviewMessage] = useState("");
  const [proposalHistoryOpen, setProposalHistoryOpen] = useState(false);
  const [recommendationPending, setRecommendationPending] = useState(false);
  const [creators, setCreators] = useState<CreatorValue[]>([]);
  const [creatorsLoaded, setCreatorsLoaded] = useState(false);
  const [creatorsLoading, setCreatorsLoading] = useState(false);
  const [creatorWorkIndex, setCreatorWorkIndex] = useState<{ creator: CreatorValue; items: MediaItem[] } | null>(null);
  const [creatorWorkLoadingId, setCreatorWorkLoadingId] = useState("");
  const [creatorWorkError, setCreatorWorkError] = useState("");
  const [creatorError, setCreatorError] = useState("");
  const [creditCreatorId, setCreditCreatorId] = useState("");
  const [creditRole, setCreditRole] = useState<CreatorRole>("creator");
  const [creatorNameDraft, setCreatorNameDraft] = useState("");
  const [creatorIdDraft, setCreatorIdDraft] = useState("");
  const [creatorIdTouched, setCreatorIdTouched] = useState(false);
  const [creatorSaving, setCreatorSaving] = useState(false);
  const [relationshipTargetId, setRelationshipTargetId] = useState("");
  const [relationshipType, setRelationshipType] = useState<RelationshipType>("same_franchise");
  const [relationshipEditorOpen, setRelationshipEditorOpen] = useState(false);
  const [lifecyclePending, setLifecyclePending] = useState<"" | "archive" | "restore">("");
  const [lifecycleError, setLifecycleError] = useState("");
  const [backupAction, setBackupAction] = useState<"" | "create" | "export" | "review" | "import" | "restore">("");
  const [backupMessage, setBackupMessage] = useState("");
  const [backupError, setBackupError] = useState("");
  const [importPreview, setImportPreview] = useState<{
    fileName: string;
    document: PortableImportDocument;
    review: ImportReview;
  } | null>(null);
  const [duplicateCandidates, setDuplicateCandidates] = useState<DuplicateCandidate[]>([]);
  const [duplicateReviewOpen, setDuplicateReviewOpen] = useState(false);
  const [duplicateLoading, setDuplicateLoading] = useState(false);
  const [duplicateError, setDuplicateError] = useState("");
  const [ratingProfileEntries, setRatingProfileEntries] = useState<RatingHistoryProfileEntry[]>([]);
  const [ratingProfileOpen, setRatingProfileOpen] = useState(false);
  const [ratingProfileLoading, setRatingProfileLoading] = useState(false);
  const [ratingProfileError, setRatingProfileError] = useState("");
  const [dimensionDraft, setDimensionDraft] = useState("");
  const [dimensionProfile, setDimensionProfile] = useState<DimensionProfile | null>(null);
  const [dimensionProfileOpen, setDimensionProfileOpen] = useState(false);
  const [dimensionProfileLoading, setDimensionProfileLoading] = useState(false);
  const [dimensionProfileError, setDimensionProfileError] = useState("");
  const [tasteReport, setTasteReport] = useState<TasteProfileReport | null>(null);
  const [tasteReportOpen, setTasteReportOpen] = useState(false);
  const [tasteReportLoading, setTasteReportLoading] = useState(false);
  const [tasteReportError, setTasteReportError] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [saveSuccessMessage, setSaveSuccessMessage] = useState("");
  const [saveError, setSaveError] = useState("");
  const [loadError, setLoadError] = useState("");
  const validationSummaryRef = useRef<HTMLDivElement>(null);
  const loadRequestIdRef = useRef(0);
  const lifecyclePendingRef = useRef(false);
  const backupPendingRef = useRef(false);
  const proposalMutationPendingRef = useRef(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const importFileRequestIdRef = useRef(0);
  const duplicateRequestIdRef = useRef(0);
  const duplicatePendingRef = useRef(false);
  const ratingProfilePendingRef = useRef(false);
  const ratingProfileRequestIdRef = useRef(0);
  const dimensionProfilePendingRef = useRef(false);
  const dimensionProfileRequestIdRef = useRef(0);
  const tasteReportPendingRef = useRef(false);
  const tasteReportRequestIdRef = useRef(0);
  const creatorWorkPendingRef = useRef(false);
  const creatorWorkRequestIdRef = useRef(0);
  const ratingDefaultDateRef = useRef(ratingDate);
  const progressDefaultStatusRef = useRef<ConsumptionStatus>(progressStatus);
  const progressDefaultDateRef = useRef(progressDate);
  const observationDefaultDateRef = useRef(observationDate);
  const saving = saveState === "saving";
  const reviewPending = Boolean(reviewingProposalId || promotingProposalId);
  const editorLocked = saving || reviewPending || creatorSaving || recommendationPending || duplicateLoading || ratingProfileLoading || dimensionProfileLoading || tasteReportLoading || Boolean(creatorWorkLoadingId) || Boolean(lifecyclePending) || Boolean(backupAction);

  function setRecommendationOperationPending(nextPending: boolean) {
    if (nextPending) {
      if (backupPendingRef.current) return false;
      backupPendingRef.current = true;
    } else {
      backupPendingRef.current = false;
    }
    setRecommendationPending(nextPending);
    return true;
  }

  const titleInvalid = selected ? !selected.title.trim() : false;
  const persisted = selected
    ? items.find((item) => item.id === selected.id) ?? null
    : null;
  const creating = editorMode === "create";
  const supportsConsumption = selected ? categoryHasCapability(selected.category, "consumption") : false;
  const supportsCreatorCredits = selected ? categoryHasCapability(selected.category, "creator_credits") : false;
  const supportsRelationships = selected ? categoryHasCapability(selected.category, "relationships") : false;
  const baseline = creating ? createBaseline : persisted;
  function categoryTransitionBlocked(category: MediaCategory) {
    if (creating || !baseline || category === baseline.category) return false;
    if (!categoryHasCapability(category, "consumption") && (baseline.status !== undefined || (baseline.progress_records?.length ?? 0) > 0)) return true;
    if (!categoryHasCapability(category, "creator_credits") && (baseline.credits?.length ?? 0) > 0) return true;
    if (!categoryHasCapability(category, "relationships") && (baseline.relationships?.length ?? 0) > 0) return true;
    return false;
  }
  const recordDirty = Boolean(
    selected && baseline && JSON.stringify(selected) !== JSON.stringify(baseline),
  );
  const ratingDraftDirty = Boolean(
    ratingScore || ratingProvisional || ratingDate !== ratingDefaultDateRef.current,
  );
  const progressDraftDirty = supportsConsumption && Boolean(
    progressStatus !== progressDefaultStatusRef.current ||
    progressAmount ||
    progressUnit ||
    progressDate !== progressDefaultDateRef.current ||
    progressStartedOn ||
    progressEndedOn ||
    progressReturnIntent ||
    progressReason,
  );
  const observationDraftDirty = Boolean(
    observationScope !== "work" || observationSubjectId || observationSubjectLabel ||
    observationPolarity !== "mixed" || observationDimension || observationText ||
    observationProvenance !== "manual" || observationPrivacy !== "assistant_readable" ||
    observationSourceContext || observationDate !== observationDefaultDateRef.current,
  );
  const creditDraftDirty = supportsCreatorCredits && Boolean(creditCreatorId || creatorNameDraft || creatorIdDraft);
  const relationshipDraftDirty = supportsRelationships && Boolean(relationshipTargetId);
  const taxonomyDraftDirty = Boolean(taxonomyValue);
  const dirty = recordDirty || Boolean(aliasDraft) || ratingDraftDirty || progressDraftDirty ||
    observationDraftDirty || creditDraftDirty || relationshipDraftDirty || taxonomyDraftDirty;
  const idInvalid = selected ? !selected.id.trim() : false;
  const selectedAliases = selected ? aliasesFor(selected) : [];
  const selectedTaxonomyTerms = selected ? taxonomyTermsFor(selected) : [];
  const selectedRatingHistory = selected ? ratingHistoryFor(selected) : [];
  const currentRating = selected ? currentRatingFor(selected) : null;
  const currentProgress = selected ? progressRecordsFor(selected).at(-1) ?? null : null;
  const currentObservation = selected ? observationsFor(selected).at(-1) ?? null : null;
  const selectedCredits = selected?.credits ?? [];
  const selectedRelationships = selected?.relationships ?? [];
  const pendingObservationProposals = selected ? proposals.filter((proposal) =>
    proposal.target_media_item_id === selected.id &&
    proposal.kind === "observation" &&
    proposal.review_state === "needs_review" &&
    proposal.proposed_observation?.provenance === "assistant_inferred"
  ) : [];
  const acceptedObservationProposals = selected ? proposals.filter((proposal) =>
    proposal.target_media_item_id === selected.id &&
    proposal.kind === "observation" &&
    proposal.review_state === "accepted" &&
    !proposal.promoted_observation_id &&
    proposal.proposed_observation?.provenance === "assistant_inferred"
  ) : [];
  const pendingMediaProposals = proposals.filter((proposal) =>
    proposal.kind === "media_item" && proposal.review_state === "needs_review" && proposal.proposed_media_item
  );
  const acceptedMediaProposals = proposals.filter((proposal) =>
    proposal.kind === "media_item" && proposal.review_state === "accepted" &&
    !proposal.promoted_media_item_id && proposal.proposed_media_item
  );
  const reviewedProposals = selected ? proposals.filter((proposal) =>
    proposal.target_media_item_id === selected.id &&
    (
      proposal.review_state === "rejected" ||
      Boolean(proposal.promoted_observation_id) ||
      (proposal.kind === "metadata" && proposal.review_state === "accepted")
    )
  ).sort((left, right) =>
    right.proposed_on.localeCompare(left.proposed_on) || right.id.localeCompare(left.id)
  ) : [];
  const latestRatingDate = selectedRatingHistory.at(-1)?.rated_on;
  const ratingScoreNumber = Number(ratingScore);
  const ratingScoreInvalid = Boolean(
    ratingScore &&
    (!Number.isFinite(ratingScoreNumber) || ratingScoreNumber < 1 || ratingScoreNumber > 10),
  );
  const ratingDateMissing = Boolean(ratingScore && !ratingDate);
  const ratingDateInvalid = Boolean(
    ratingScore && latestRatingDate && ratingDate && ratingDate < latestRatingDate,
  );
  const progressAmountNumber = Number(progressAmount);
  const progressAmountInvalid = Boolean(
    progressAmount && (!Number.isFinite(progressAmountNumber) || progressAmountNumber < 0),
  );
  const progressDateMissing = !progressDate;
  const progressLifecycleInvalid = Boolean(
    progressStartedOn && progressEndedOn && progressEndedOn < progressStartedOn,
  );
  const observationNeedsSubject = observationScope !== "work";
  const observationSubjectInvalid = observationNeedsSubject && (
    !observationSubjectId.trim() || !observationSubjectLabel.trim()
  );
  const observationRequiredMissing = Boolean(
    !observationDimension.trim() || !observationText.trim() || !observationDate,
  );
  const observationDimensionInvalid = observationAttempted && !observationDimension.trim();
  const observationTextInvalid = observationAttempted && !observationText.trim();
  const observationDateInvalid = observationAttempted && !observationDate;
  const aliasCandidate = aliasDraft.trim();
  const aliasDuplicate = selected
    ? selectedAliases.some(
        (alias) => alias.value.trim().toLocaleLowerCase() === aliasCandidate.toLocaleLowerCase(),
      )
    : false;
  const taxonomyCandidate = taxonomyValue.trim();
  const taxonomyDuplicate = selectedTaxonomyTerms.some((term) =>
    term.kind === taxonomyKind &&
    term.value.trim().toLocaleLowerCase() === taxonomyCandidate.toLocaleLowerCase()
  );

  function addTaxonomyTerm(event: FormEvent) {
    event.preventDefault();
    if (!selected || !taxonomyCandidate || taxonomyDuplicate) return;
    changeSelected({
      ...selected,
      terms: [...selectedTaxonomyTerms, { kind: taxonomyKind, value: taxonomyCandidate }],
    });
    setTaxonomyKind("genre");
    setTaxonomyValue("");
  }

  function removeTaxonomyTerm(index: number) {
    if (!selected) return;
    changeSelected({
      ...selected,
      terms: selectedTaxonomyTerms.filter((_, termIndex) => termIndex !== index),
    });
  }

  async function loadMedia(searchQuery = "", signal?: AbortSignal, includeArchived = showArchived) {
    duplicateRequestIdRef.current += 1;
    if (duplicatePendingRef.current) {
      duplicatePendingRef.current = false;
      backupPendingRef.current = false;
    }
    setDuplicateReviewOpen(false);
    setDuplicateCandidates([]);
    setDuplicateError("");
    setDuplicateLoading(false);
    ratingProfileRequestIdRef.current += 1;
    if (ratingProfilePendingRef.current) {
      ratingProfilePendingRef.current = false;
      backupPendingRef.current = false;
    }
    setRatingProfileOpen(false);
    setRatingProfileEntries([]);
    setRatingProfileError("");
    setRatingProfileLoading(false);
    dimensionProfileRequestIdRef.current += 1;
    if (dimensionProfilePendingRef.current) {
      dimensionProfilePendingRef.current = false;
      backupPendingRef.current = false;
    }
    setDimensionProfile(null);
    setDimensionProfileOpen(false);
    setDimensionProfileError("");
    setDimensionProfileLoading(false);
    tasteReportRequestIdRef.current += 1;
    if (tasteReportPendingRef.current) {
      tasteReportPendingRef.current = false;
      backupPendingRef.current = false;
    }
    setTasteReport(null);
    setTasteReportOpen(false);
    setTasteReportError("");
    setTasteReportLoading(false);
    creatorWorkRequestIdRef.current += 1;
    if (creatorWorkPendingRef.current) {
      creatorWorkPendingRef.current = false;
      backupPendingRef.current = false;
    }
    setCreatorWorkIndex(null);
    setCreatorWorkError("");
    setCreatorWorkLoadingId("");
    const requestId = ++loadRequestIdRef.current;
    setLoading(true);
    setLoadError("");
    try {
      const loadedItems = await listMedia(searchQuery, signal, includeArchived);
      if (requestId !== loadRequestIdRef.current) return false;
      setItems(loadedItems);
      setSelected(loadedItems[0] ?? null);
      setProposalError("");
      setProposalReviewMessage("");
      resetProgressDraft(loadedItems[0]?.status ?? "planned");
      resetObservationDraft();
      return true;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return false;
      if (requestId !== loadRequestIdRef.current) return false;
      setLoadError("The local library is unavailable.");
      return false;
    } finally {
      if (requestId === loadRequestIdRef.current) setLoading(false);
    }
  }

  async function loadCreatorCredits() {
    setCreatorsLoading(true);
    setCreatorError("");
    try {
      setCreators(await listCreators());
      setCreatorsLoaded(true);
    } catch (error) {
      setCreatorError(error instanceof Error ? error.message : "Creator identities are unavailable.");
    } finally {
      setCreatorsLoading(false);
    }
  }

  async function loadCreatorWorkIndex(creator: CreatorValue) {
    if (creatorWorkPendingRef.current || backupPendingRef.current || query.trim()) return;
    creatorWorkPendingRef.current = true;
    backupPendingRef.current = true;
    const requestId = ++creatorWorkRequestIdRef.current;
    setCreatorWorkLoadingId(creator.id);
    setCreatorWorkIndex(null);
    setCreatorWorkError("");
    try {
      const responseItems = await listMediaForCreator(creator.id, showArchived);
      if (requestId !== creatorWorkRequestIdRef.current) return;
      const canonicalItems = responseItems.map((responseItem) => items.find((item) => item.id === responseItem.id));
      if (canonicalItems.some((item) => (
        !item || !(item.credits ?? []).some((credit) => credit.creator_id === creator.id)
      ))) {
        throw new Error("The creator work index refers to unverifiable visible-library evidence.");
      }
      setCreatorWorkIndex({ creator, items: canonicalItems as MediaItem[] });
    } catch (error) {
      if (requestId !== creatorWorkRequestIdRef.current) return;
      setCreatorWorkError(error instanceof Error ? error.message : "The creator work index is unavailable.");
    } finally {
      if (requestId === creatorWorkRequestIdRef.current) {
        creatorWorkPendingRef.current = false;
        backupPendingRef.current = false;
        setCreatorWorkLoadingId("");
      }
    }
  }

  async function createCreatorIdentity(event: FormEvent) {
    event.preventDefault();
    if (backupPendingRef.current) return;
    const name = creatorNameDraft.trim();
    const id = creatorIdDraft.trim();
    if (!name || !id || creators.some((creator) => creator.id === id)) return;
    backupPendingRef.current = true;
    setCreatorSaving(true);
    setCreatorError("");
    try {
      const created = await saveCreator({ id, name });
      setCreators((current) => [...current, created].sort((left, right) =>
        left.name.localeCompare(right.name) || left.id.localeCompare(right.id)
      ));
      setCreditCreatorId(created.id);
      setCreatorNameDraft("");
      setCreatorIdDraft("");
      setCreatorIdTouched(false);
    } catch (error) {
      setCreatorError(error instanceof Error ? error.message : "The creator could not be saved.");
    } finally {
      backupPendingRef.current = false;
      setCreatorSaving(false);
    }
  }

  function addCreatorCredit(event: FormEvent) {
    event.preventDefault();
    if (!selected || !creditCreatorId) return;
    const duplicate = selectedCredits.some((credit) =>
      credit.creator_id === creditCreatorId && credit.role === creditRole
    );
    if (duplicate) return;
    changeSelected({
      ...selected,
      credits: [...selectedCredits, { creator_id: creditCreatorId, role: creditRole }],
    });
    setCreditCreatorId("");
    setCreditRole("creator");
  }

  function removeCreatorCredit(index: number) {
    if (!selected) return;
    changeSelected({ ...selected, credits: selectedCredits.filter((_, creditIndex) => creditIndex !== index) });
  }

  function addRelationship(event: FormEvent) {
    event.preventDefault();
    if (!selected || !relationshipTargetId || relationshipTargetId === selected.id) return;
    const duplicate = selectedRelationships.some((relationship) =>
      relationship.target_media_item_id === relationshipTargetId &&
      relationship.relationship_type === relationshipType
    );
    if (duplicate) return;
    changeSelected({
      ...selected,
      relationships: [
        ...selectedRelationships,
        { relationship_type: relationshipType, target_media_item_id: relationshipTargetId },
      ],
    });
    setRelationshipTargetId("");
    setRelationshipType("same_franchise");
  }

  function removeRelationship(index: number) {
    if (!selected) return;
    changeSelected({
      ...selected,
      relationships: selectedRelationships.filter((_, relationshipIndex) => relationshipIndex !== index),
    });
  }

  async function loadInferenceProposals() {
    setProposalsLoading(true);
    setProposalError("");
    setProposalReviewMessage("");
    try {
      setProposals(await listProposals());
      setProposalsLoaded(true);
    } catch (error) {
      setProposalError(
        error instanceof Error ? error.message : "Inference proposals are unavailable.",
      );
    } finally {
      setProposalsLoading(false);
    }
  }

  async function reviewInference(proposalId: string, outcome: "accept" | "reject") {
    if (backupPendingRef.current || proposalMutationPendingRef.current) return;
    proposalMutationPendingRef.current = true;
    backupPendingRef.current = true;
    setReviewingProposalId(proposalId);
    setProposalError("");
    try {
      const reviewed = await reviewProposal(proposalId, outcome);
      setProposals((current) => current.map((proposal) =>
        proposal.id === reviewed.id ? reviewed : proposal
      ));
      setProposalReviewMessage(
        outcome === "accept"
          ? "Inference accepted as a review outcome; media evidence was not changed."
          : "Inference rejected; media evidence was not changed.",
      );
    } catch (error) {
      setProposalError(
        error instanceof Error ? error.message : "The inference review could not be saved.",
      );
    } finally {
      proposalMutationPendingRef.current = false;
      backupPendingRef.current = false;
      setReviewingProposalId("");
    }
  }

  async function promoteInference(proposalId: string) {
    if (backupPendingRef.current || proposalMutationPendingRef.current) return;
    proposalMutationPendingRef.current = true;
    backupPendingRef.current = true;
    setPromotingProposalId(proposalId);
    setProposalError("");
    try {
      const promoted = await promoteProposal(proposalId);
      setProposals((current) => current.map((proposal) =>
        proposal.id === promoted.proposal.id ? promoted.proposal : proposal
      ));
      setItems((current) => current.map((item) =>
        item.id === promoted.media_item.id ? promoted.media_item : item
      ));
      if (selected?.id === promoted.media_item.id) setSelected(promoted.media_item);
      setProposalReviewMessage("Inference promoted to canonical evidence.");
    } catch (error) {
      setProposalError(
        error instanceof Error ? error.message : "The accepted inference could not be promoted.",
      );
    } finally {
      proposalMutationPendingRef.current = false;
      backupPendingRef.current = false;
      setPromotingProposalId("");
    }
  }

  async function promoteMediaCandidate(proposalId: string) {
    if (backupPendingRef.current || proposalMutationPendingRef.current || dirty) return;
    proposalMutationPendingRef.current = true;
    backupPendingRef.current = true;
    setPromotingProposalId(proposalId);
    setProposalError("");
    try {
      const promoted = await promoteMediaProposal(proposalId);
      setProposals((current) => current.map((proposal) =>
        proposal.id === promoted.proposal.id ? promoted.proposal : proposal
      ));
      setItems((current) => [...current, promoted.media_item].sort((left, right) => left.title.localeCompare(right.title)));
      setProposalReviewMessage(`${promoted.media_item.title} was promoted into the canonical library.`);
    } catch (error) {
      setProposalError(error instanceof Error ? error.message : "The accepted media candidate could not be promoted.");
    } finally {
      proposalMutationPendingRef.current = false;
      backupPendingRef.current = false;
      setPromotingProposalId("");
    }
  }

  async function archiveSelected() {
    if (!selected || creating || dirty || lifecyclePendingRef.current || backupPendingRef.current) return;
    if (!window.confirm(`Archive ${selected.title}? It can be restored later.`)) return;
    lifecyclePendingRef.current = true;
    backupPendingRef.current = true;
    loadRequestIdRef.current += 1;
    setLifecyclePending("archive");
    setLifecycleError("");
    try {
      await archiveMedia(selected.id);
      await loadMedia(query.trim(), undefined, showArchived);
    } catch (error) {
      setLifecycleError(error instanceof Error ? error.message : "The record could not be archived.");
    } finally {
      lifecyclePendingRef.current = false;
      backupPendingRef.current = false;
      setLifecyclePending("");
    }
  }

  async function restoreSelected() {
    if (!selected || creating || dirty || lifecyclePendingRef.current || backupPendingRef.current) return;
    lifecyclePendingRef.current = true;
    backupPendingRef.current = true;
    loadRequestIdRef.current += 1;
    setLifecyclePending("restore");
    setLifecycleError("");
    try {
      await restoreMedia(selected.id);
      await loadMedia(query.trim(), undefined, showArchived);
    } catch (error) {
      setLifecycleError(error instanceof Error ? error.message : "The record could not be restored.");
    } finally {
      lifecyclePendingRef.current = false;
      backupPendingRef.current = false;
      setLifecyclePending("");
    }
  }

  function toggleArchivedVisibility() {
    if (backupPendingRef.current) return;
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    const next = !showArchived;
    setShowArchived(next);
    void loadMedia(query.trim(), undefined, next);
  }

  async function toggleTasteReport() {
    if (tasteReportPendingRef.current || backupPendingRef.current || query.trim()) return;
    if (tasteReportOpen) {
      tasteReportRequestIdRef.current += 1;
      setTasteReportOpen(false);
      setTasteReport(null);
      setTasteReportError("");
      setTasteReportLoading(false);
      return;
    }
    const requestId = ++tasteReportRequestIdRef.current;
    tasteReportPendingRef.current = true;
    backupPendingRef.current = true;
    setTasteReportOpen(true);
    setTasteReport(null);
    setTasteReportLoading(true);
    setTasteReportError("");
    try {
      const report = await loadTasteProfileReport(showArchived);
      if (requestId !== tasteReportRequestIdRef.current) return;
      const unresolvedRelationshipTargetIds = [...new Set(items.flatMap((item) =>
        (item.relationships ?? []).map((relationship) => relationship.target_media_item_id),
      ).filter((targetId) => !items.some((item) => item.id === targetId)))];
      const allKnownItems = unresolvedRelationshipTargetIds.length > 0 && !showArchived
        ? await listMedia("", undefined, true)
        : items;
      if (requestId !== tasteReportRequestIdRef.current) return;
      const hasUnknownRelationshipTarget = unresolvedRelationshipTargetIds.some(
        (targetId) => !allKnownItems.some((item) => item.id === targetId),
      );
      const ratingMatches = (first: { score: number; rated_on: string; provisional?: boolean }, second: { score: number; rated_on: string; provisional?: boolean }) => (
        first.score === second.score && first.rated_on === second.rated_on && Boolean(first.provisional) === Boolean(second.provisional)
      );
      const acceptedEvidence = (item: MediaItem, dimension?: string) => (item.observations ?? []).filter((observation) => (
        observation.privacy === "assistant_readable" && observation.review_state === "accepted" &&
        (dimension === undefined || normalizeDimension(observation.dimension) === dimension)
      ));
      const bucket = (evidence: ReturnType<typeof acceptedEvidence>, kind: "supporting" | "contradictory" | "context") => evidence.filter((observation) => (
        kind === "supporting" ? observation.polarity === "positive" :
          kind === "contradictory" ? observation.polarity === "negative" :
            observation.polarity === "mixed" || observation.polarity === "neutral"
      ));
      const exactEvidence = (expected: ReturnType<typeof acceptedEvidence>, actual: DimensionProfileEntry["supporting_evidence"]) => (
        expected.length === actual.length && expected.every((canonical, index) => {
          const evidence = actual[index];
          return canonical.id === evidence.id && canonical.text === evidence.text &&
            canonical.dimension === evidence.dimension && canonical.polarity === evidence.polarity &&
            canonical.observed_on === evidence.observed_on;
        })
      );
      const expectedRated = items.filter((item) => item.rating);
      let unverifiable = hasUnknownRelationshipTarget;
      if (expectedRated.length !== report.rating_history.entries.length) unverifiable = true;
      if (!unverifiable) {
        unverifiable = report.rating_history.entries.some((entry) => {
          const item = expectedRated.find((candidate) => candidate.id === entry.media_item_id);
          const history = item?.rating_history ?? [];
          if (!item || item.id !== entry.media_item_id || item.title !== entry.title || item.category !== entry.category ||
              !item.rating || !ratingMatches(item.rating, entry.current_rating) || history.length !== entry.rating_history.length ||
              !history.every((rating, ratingIndex) => ratingMatches(rating, entry.rating_history[ratingIndex]))) return true;
          const evidence = acceptedEvidence(item);
          return !exactEvidence(bucket(evidence, "supporting"), entry.supporting_evidence) ||
            !exactEvidence(bucket(evidence, "contradictory"), entry.contradictory_evidence) ||
            !exactEvidence(bucket(evidence, "context"), entry.context_evidence);
        });
      }
      const expectedProgress = items.filter((item) => (item.progress_records ?? []).length > 0).sort((first, second) => compareCodePointStrings(first.id, second.id));
      if (expectedProgress.length !== report.progress_context.entries.length) unverifiable = true;
      if (!unverifiable) {
        unverifiable = report.progress_context.entries.some((entry, entryIndex) => {
          const item = expectedProgress[entryIndex];
          const history = item?.progress_records ?? [];
          return !item || item.id !== entry.media_item_id || item.title !== entry.title || item.category !== entry.category || item.status !== entry.current_status ||
            history.length !== entry.progress_history.length || history.some((record, index) => {
              const projected = entry.progress_history[index];
              return record.status !== projected.status || record.recorded_on !== projected.recorded_on ||
                record.amount_completed !== projected.amount_completed ||
                record.unit !== projected.unit ||
                record.started_on !== projected.started_on ||
                record.ended_on !== projected.ended_on ||
                record.return_intent !== projected.return_intent ||
                record.reason !== projected.reason;
            });
        });
      }
      const expectedCreatorContext = items.filter((item) => (item.credits ?? []).length > 0).sort((first, second) => compareCodePointStrings(first.id, second.id));
      if (expectedCreatorContext.length !== report.creator_context.entries.length) unverifiable = true;
      if (!unverifiable) {
        unverifiable = report.creator_context.entries.some((entry, entryIndex) => {
          const item = expectedCreatorContext[entryIndex];
          const credits = item?.credits ?? [];
          return !item || item.id !== entry.media_item_id || item.title !== entry.title || item.category !== entry.category || credits.length !== entry.credits.length ||
            credits.some((credit, creditIndex) => credit.creator_id !== entry.credits[creditIndex].creator_id || credit.role !== entry.credits[creditIndex].role);
        });
      }
      const expectedRelationshipContext = items.map((item) => ({
        item,
        relationships: (item.relationships ?? []).filter((relationship) => items.some((target) => target.id === relationship.target_media_item_id)),
      })).filter(({ relationships }) => relationships.length > 0).sort((first, second) => compareCodePointStrings(first.item.id, second.item.id));
      if (expectedRelationshipContext.length !== report.relationship_context.entries.length) unverifiable = true;
      if (!unverifiable) {
        unverifiable = report.relationship_context.entries.some((entry, entryIndex) => {
          const expected = expectedRelationshipContext[entryIndex];
          return !expected || expected.item.id !== entry.media_item_id || expected.item.title !== entry.title || expected.item.category !== entry.category || expected.relationships.length !== entry.relationships.length ||
            expected.relationships.some((relationship, relationshipIndex) => {
              const projected = entry.relationships[relationshipIndex];
              const target = items.find((item) => item.id === relationship.target_media_item_id);
              return !target || relationship.relationship_type !== projected.relationship_type || target.id !== projected.target_media_item_id || target.title !== projected.target_title || target.category !== projected.target_category;
            });
        });
      }
      const expectedDimensions = new Set(items.flatMap((item) => acceptedEvidence(item).map((observation) => normalizeDimension(observation.dimension))));
      const actualDimensions = new Set(report.dimensions.map((profile) => normalizeDimension(profile.dimension)));
      if (expectedDimensions.size !== actualDimensions.size || [...expectedDimensions].some((dimension) => !actualDimensions.has(dimension))) unverifiable = true;
      if (!unverifiable) {
        unverifiable = report.dimensions.some((profile) => {
          const dimension = normalizeDimension(profile.dimension);
          const expectedItems = items.filter((item) => acceptedEvidence(item, dimension).length);
          if (expectedItems.length !== profile.entries.length) return true;
          return profile.entries.some((entry) => {
            const item = expectedItems.find((candidate) => candidate.id === entry.media_item_id);
            if (!item || item.id !== entry.media_item_id || item.title !== entry.title || item.category !== entry.category ||
                (entry.current_rating === null ? Boolean(item.rating) : !item.rating || !ratingMatches(item.rating, entry.current_rating))) return true;
            const evidence = acceptedEvidence(item, dimension);
            return !exactEvidence(bucket(evidence, "supporting"), entry.supporting_evidence) ||
              !exactEvidence(bucket(evidence, "contradictory"), entry.contradictory_evidence) ||
              !exactEvidence(bucket(evidence, "context"), entry.context_evidence);
          });
        });
      }
      if (unverifiable) throw new Error("The cited taste report does not match the complete visible-library evidence.");
      setTasteReport(report);
    } catch (error) {
      if (requestId !== tasteReportRequestIdRef.current) return;
      setTasteReportError(error instanceof Error ? error.message : "The cited taste report could not be loaded.");
    } finally {
      if (requestId === tasteReportRequestIdRef.current) {
        tasteReportPendingRef.current = false;
        backupPendingRef.current = false;
        setTasteReportLoading(false);
      }
    }
  }

  async function toggleDimensionProfile() {
    if (dimensionProfilePendingRef.current || backupPendingRef.current) return;
    if (dimensionProfileOpen) {
      dimensionProfileRequestIdRef.current += 1;
      setDimensionProfileOpen(false);
      setDimensionProfile(null);
      setDimensionProfileError("");
      setDimensionProfileLoading(false);
      return;
    }
    const requested = dimensionDraft.trim();
    if (!requested || query.trim()) return;
    const requestId = ++dimensionProfileRequestIdRef.current;
    dimensionProfilePendingRef.current = true;
    backupPendingRef.current = true;
    setDimensionProfileOpen(true);
    setDimensionProfile(null);
    setDimensionProfileLoading(true);
    setDimensionProfileError("");
    try {
      const profile = await loadDimensionProfile(requested, showArchived);
      if (requestId !== dimensionProfileRequestIdRef.current) return;
      const ratingMatches = (first: { score: number; rated_on: string; provisional?: boolean }, second: { score: number; rated_on: string; provisional?: boolean }) => (
        first.score === second.score && first.rated_on === second.rated_on &&
        Boolean(first.provisional) === Boolean(second.provisional)
      );
      const evidenceMatches = (item: MediaItem, evidence: DimensionProfileEntry["supporting_evidence"][number]) => {
        const canonical = (item.observations ?? []).find((observation) => observation.id === evidence.id);
        return Boolean(
          canonical && canonical.text === evidence.text && canonical.dimension === evidence.dimension &&
          canonical.polarity === evidence.polarity && canonical.observed_on === evidence.observed_on &&
          canonical.privacy === "assistant_readable" && canonical.review_state === "accepted"
        );
      };
      const unverifiable = profile.entries.some((entry) => {
        const item = items.find((candidate) => candidate.id === entry.media_item_id);
        if (!item || item.title !== entry.title || item.category !== entry.category) return true;
        if (entry.current_rating === null ? Boolean(item.rating) : !item.rating || !ratingMatches(item.rating, entry.current_rating)) return true;
        return ![...entry.supporting_evidence, ...entry.contradictory_evidence, ...entry.context_evidence]
          .every((evidence) => evidenceMatches(item, evidence));
      });
      if (unverifiable) {
        throw new Error("The cited dimension profile refers to unverifiable visible-library evidence.");
      }
      setDimensionProfile(profile);
    } catch (error) {
      if (requestId !== dimensionProfileRequestIdRef.current) return;
      setDimensionProfileError(error instanceof Error ? error.message : "The cited dimension profile could not be loaded.");
    } finally {
      if (requestId === dimensionProfileRequestIdRef.current) {
        dimensionProfilePendingRef.current = false;
        backupPendingRef.current = false;
        setDimensionProfileLoading(false);
      }
    }
  }

  async function toggleRatingProfile() {
    if (ratingProfilePendingRef.current || backupPendingRef.current || query.trim()) return;
    if (ratingProfileOpen) {
      ratingProfileRequestIdRef.current += 1;
      setRatingProfileOpen(false);
      setRatingProfileEntries([]);
      setRatingProfileError("");
      setRatingProfileLoading(false);
      return;
    }
    const requestId = ++ratingProfileRequestIdRef.current;
    ratingProfilePendingRef.current = true;
    backupPendingRef.current = true;
    setRatingProfileOpen(true);
    setRatingProfileLoading(true);
    setRatingProfileError("");
    try {
      const profile = await loadRatingHistoryProfile(showArchived);
      if (requestId !== ratingProfileRequestIdRef.current) return;
      const ratingMatches = (first: { score: number; rated_on: string; provisional?: boolean }, second: { score: number; rated_on: string; provisional?: boolean }) => (
        first.score === second.score && first.rated_on === second.rated_on &&
        Boolean(first.provisional) === Boolean(second.provisional)
      );
      const evidenceMatches = (item: MediaItem, evidence: RatingHistoryProfileEntry["supporting_evidence"][number]) => {
        const canonical = (item.observations ?? []).find((observation) => observation.id === evidence.id);
        return Boolean(
          canonical && canonical.text === evidence.text && canonical.dimension === evidence.dimension &&
          canonical.polarity === evidence.polarity && canonical.observed_on === evidence.observed_on &&
          canonical.privacy === "assistant_readable" && canonical.review_state === "accepted"
        );
      };
      const unverifiable = profile.entries.some((entry) => {
        const item = items.find((candidate) => candidate.id === entry.media_item_id);
        return !item || item.title !== entry.title || item.category !== entry.category || !item.rating ||
          !ratingMatches(item.rating, entry.current_rating) ||
          (item.rating_history ?? []).length !== entry.rating_history.length ||
          !entry.rating_history.every((rating, index) => ratingMatches(rating, (item.rating_history ?? [])[index])) ||
          ![...entry.supporting_evidence, ...entry.contradictory_evidence, ...entry.context_evidence]
            .every((evidence) => evidenceMatches(item, evidence));
      });
      if (unverifiable) {
        throw new Error("Cited rating history refers to unverifiable visible-library evidence.");
      }
      setRatingProfileEntries(profile.entries);
    } catch (error) {
      if (requestId !== ratingProfileRequestIdRef.current) return;
      setRatingProfileError(error instanceof Error ? error.message : "Cited rating history could not be loaded.");
    } finally {
      if (requestId === ratingProfileRequestIdRef.current) {
        ratingProfilePendingRef.current = false;
        backupPendingRef.current = false;
        setRatingProfileLoading(false);
      }
    }
  }

  async function toggleDuplicateReview() {
    if (duplicatePendingRef.current || backupPendingRef.current) return;
    if (duplicateReviewOpen) {
      duplicateRequestIdRef.current += 1;
      setDuplicateReviewOpen(false);
      setDuplicateCandidates([]);
      setDuplicateError("");
      setDuplicateLoading(false);
      return;
    }
    const requestId = ++duplicateRequestIdRef.current;
    duplicatePendingRef.current = true;
    backupPendingRef.current = true;
    setDuplicateReviewOpen(true);
    setDuplicateLoading(true);
    setDuplicateError("");
    try {
      const candidates = await listDuplicateCandidates(showArchived);
      if (requestId !== duplicateRequestIdRef.current) return;
      if (candidates.some((candidate) => (
        !items.some((item) => item.id === candidate.media_item_id) ||
        !items.some((item) => item.id === candidate.candidate_media_item_id)
      ))) {
        throw new Error("Possible duplicate evidence refers to a record outside the visible library.");
      }
      setDuplicateCandidates(candidates);
    } catch (error) {
      if (requestId !== duplicateRequestIdRef.current) return;
      setDuplicateError(error instanceof Error ? error.message : "Possible duplicates could not be loaded.");
    } finally {
      if (requestId === duplicateRequestIdRef.current) {
        duplicatePendingRef.current = false;
        backupPendingRef.current = false;
        setDuplicateLoading(false);
      }
    }
  }

  async function createLocalBackup() {
    if (dirty || backupPendingRef.current || lifecyclePendingRef.current || proposalMutationPendingRef.current) return;
    backupPendingRef.current = true;
    setBackupAction("create");
    setBackupMessage("");
    setBackupError("");
    try {
      const receipt = await createBackup();
      if (!receipt.verified) throw new Error("The local backup could not be verified.");
      setBackupMessage(
        `Verified backup created for ${receipt.items} ${receipt.items === 1 ? "record" : "records"}.`,
      );
    } catch (error) {
      setBackupError(error instanceof Error ? error.message : "The local backup could not be created.");
    } finally {
      backupPendingRef.current = false;
      setBackupAction("");
    }
  }

  async function downloadPortableExport() {
    if (dirty || backupPendingRef.current || lifecyclePendingRef.current || proposalMutationPendingRef.current) return;
    backupPendingRef.current = true;
    setBackupAction("export");
    setBackupMessage("");
    setBackupError("");
    try {
      const documentToExport = await exportLibrary();
      const blob = new Blob([`${JSON.stringify(documentToExport, null, 2)}\n`], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      try {
        anchor.href = url;
        anchor.download = `concierge-export-${documentToExport.exported_on}.json`;
        document.body.appendChild(anchor);
        anchor.click();
      } finally {
        anchor.remove();
        URL.revokeObjectURL(url);
      }
      setBackupMessage(
        `Portable export downloaded with ${documentToExport.media_items.length} ${documentToExport.media_items.length === 1 ? "record" : "records"}.`,
      );
    } catch (error) {
      setBackupError(error instanceof Error ? error.message : "The portable export could not be created.");
    } finally {
      backupPendingRef.current = false;
      setBackupAction("");
    }
  }

  async function selectImportFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    if (backupPendingRef.current) {
      input.value = "";
      return;
    }
    const file = input.files?.[0];
    const requestId = ++importFileRequestIdRef.current;
    setImportPreview(null);
    setBackupMessage("");
    setBackupError("");
    if (!file) return;
    if (file.size > maximumImportBytes) {
      input.value = "";
      setBackupError("The selected export is larger than the 5 MiB import limit.");
      return;
    }
    backupPendingRef.current = true;
    setBackupAction("review");
    try {
      const payload: unknown = JSON.parse(await file.text());
      if (requestId !== importFileRequestIdRef.current) return;
      const documentToReview = parsePortableImportDocument(payload);
      const review = await reviewImportLibrary(documentToReview);
      if (requestId !== importFileRequestIdRef.current) return;
      setImportPreview({ fileName: file.name, document: documentToReview, review });
    } catch (error) {
      if (requestId !== importFileRequestIdRef.current) return;
      input.value = "";
      setBackupError(
        error instanceof SyntaxError
          ? "The selected file is not valid JSON."
          : error instanceof Error ? error.message : "The selected file could not be reviewed.",
      );
    } finally {
      if (requestId === importFileRequestIdRef.current) {
        backupPendingRef.current = false;
        setBackupAction("");
      }
    }
  }

  function resetImportPreview() {
    importFileRequestIdRef.current += 1;
    setImportPreview(null);
    if (importInputRef.current) importInputRef.current.value = "";
  }

  function clearImportPreview() {
    if (backupPendingRef.current) return;
    resetImportPreview();
  }

  async function mergeLibraryFromImport() {
    if (
      !importPreview || !importPreview.review.can_import || dirty || backupPendingRef.current || lifecyclePendingRef.current ||
      proposalMutationPendingRef.current
    ) return;
    const proposalEffect = ["1.4", "1.5", "1.6", "1.7", "1.8"].includes(importPreview.document.schema_version)
      ? "its proposal queue replaces the local proposal queue"
      : "the existing local proposal queue remains";
    const recommendationEffect = ["1.6", "1.7", "1.8"].includes(importPreview.document.schema_version)
      ? "its recommendations merge create-only by stable ID and conflicting IDs are rejected"
      : "the existing local recommendation ledger remains";
    const captureProposalEffect = importPreview.document.schema_version === "1.8"
      ? "and its typed capture-proposal queue replaces the local typed capture queue"
      : "";
    const confirmation = window.prompt(
      `Merge this document? Media and creators update by stable ID; unrelated records stay, ${proposalEffect}, ${recommendationEffect} ${captureProposalEffect}. Type MERGE to continue.`,
    );
    if (confirmation !== "MERGE") return;
    backupPendingRef.current = true;
    loadRequestIdRef.current += 1;
    setBackupAction("import");
    setBackupMessage("");
    setBackupError("");
    const invalidateImportedCaches = () => {
      setCreators([]);
      setCreatorsLoaded(false);
      setProposals([]);
      setProposalsLoaded(false);
      setQuery("");
      setShowArchived(false);
    };
    try {
      const receipt = await importLibrary(
        importPreview.document,
        importPreview.review.review_token,
      );
      if (receipt.imported !== importPreview.document.media_items.length) {
        throw new ImportCommitUncertainError(
          "The import may have completed, but its record count could not be verified.",
        );
      }
      invalidateImportedCaches();
      const refreshed = await loadMedia("", undefined, false);
      if (!refreshed) {
        setItems([]);
        setSelected(null);
        throw new ImportCommitUncertainError(
          "The import completed, but the library could not be refreshed.",
        );
      }
      resetImportPreview();
      const typedCaptureMessage = importPreview.document.schema_version === "1.8"
        ? " and typed capture-proposal replacement followed the previewed rules"
        : " followed the previewed rules";
      setBackupMessage(
        `Merged and verified ${receipt.imported} imported ${receipt.imported === 1 ? "record" : "records"}; unrelated media and creator records were preserved; proposal replacement and recommendation merge${typedCaptureMessage}.`,
      );
    } catch (error) {
      if (error instanceof ImportReviewStaleError) {
        try {
          const refreshedReview = await reviewImportLibrary(importPreview.document);
          setImportPreview({ ...importPreview, review: refreshedReview });
          setBackupError(
            `${error.message} The change review was recalculated against the current library; inspect it again before merging.`,
          );
        } catch (reviewError) {
          setBackupError(
            `${error.message} ${reviewError instanceof Error ? reviewError.message : "A fresh review could not be loaded."}`,
          );
        }
      } else if (error instanceof ImportCommitUncertainError) {
        invalidateImportedCaches();
        const refreshed = await loadMedia("", undefined, false);
        resetImportPreview();
        if (!refreshed) {
          setItems([]);
          setSelected(null);
          setBackupError(
            `${error.message} The refreshed library could not be verified. Reload the page before editing.`,
          );
        } else {
          setBackupError(
            `${error.message} The authoritative library was refreshed; reload the page to confirm before editing.`,
          );
        }
      } else {
        setBackupError(error instanceof Error ? error.message : "The portable export was rejected.");
      }
    } finally {
      backupPendingRef.current = false;
      setBackupAction("");
    }
  }

  async function restoreLocalBackup() {
    if (dirty || backupPendingRef.current || lifecyclePendingRef.current || proposalMutationPendingRef.current) return;
    const confirmation = window.prompt(
      "Restore the latest local backup? This replaces the current library. Type RESTORE to continue.",
    );
    if (confirmation !== "RESTORE") return;
    backupPendingRef.current = true;
    loadRequestIdRef.current += 1;
    setBackupAction("restore");
    setBackupMessage("");
    setBackupError("");
    const invalidateRestoredCaches = () => {
      setCreators([]);
      setCreatorsLoaded(false);
      setProposals([]);
      setProposalsLoaded(false);
      setQuery("");
      setShowArchived(false);
    };
    try {
      const receipt = await restoreBackup();
      invalidateRestoredCaches();
      const refreshed = await loadMedia("", undefined, false);
      if (!refreshed) {
        setItems([]);
        setSelected(null);
        throw new Error("The backup was restored, but the library could not be refreshed. Reload the page.");
      }
      setBackupMessage(
        `Verified backup restored with ${receipt.items} ${receipt.items === 1 ? "record" : "records"}.`,
      );
    } catch (error) {
      if (error instanceof BackupReceiptError) {
        invalidateRestoredCaches();
        const refreshed = await loadMedia("", undefined, false);
        if (!refreshed) {
          setItems([]);
          setSelected(null);
          setBackupError(
            "The restore request completed, but its receipt and refreshed library could not be verified. Reload the page before editing.",
          );
        } else {
          setBackupError(
            "The restore request completed and the library was refreshed, but its receipt could not be verified. Reload the page to confirm.",
          );
        }
      } else {
        setBackupError(error instanceof Error ? error.message : "The local backup could not be restored.");
      }
    } finally {
      backupPendingRef.current = false;
      setBackupAction("");
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void loadMedia("", controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (saveError) validationSummaryRef.current?.focus();
  }, [saveError]);

  useEffect(() => {
    duplicateRequestIdRef.current += 1;
    if (duplicatePendingRef.current) {
      duplicatePendingRef.current = false;
      backupPendingRef.current = false;
    }
    setDuplicateReviewOpen(false);
    setDuplicateCandidates([]);
    setDuplicateError("");
    setDuplicateLoading(false);
    ratingProfileRequestIdRef.current += 1;
    if (ratingProfilePendingRef.current) {
      ratingProfilePendingRef.current = false;
      backupPendingRef.current = false;
    }
    setRatingProfileOpen(false);
    setRatingProfileEntries([]);
    setRatingProfileError("");
    setRatingProfileLoading(false);
    dimensionProfileRequestIdRef.current += 1;
    if (dimensionProfilePendingRef.current) {
      dimensionProfilePendingRef.current = false;
      backupPendingRef.current = false;
    }
    setDimensionProfile(null);
    setDimensionProfileOpen(false);
    setDimensionProfileError("");
    setDimensionProfileLoading(false);
    tasteReportRequestIdRef.current += 1;
    if (tasteReportPendingRef.current) {
      tasteReportPendingRef.current = false;
      backupPendingRef.current = false;
    }
    setTasteReport(null);
    setTasteReportOpen(false);
    setTasteReportError("");
    setTasteReportLoading(false);
    creatorWorkRequestIdRef.current += 1;
    if (creatorWorkPendingRef.current) {
      creatorWorkPendingRef.current = false;
      backupPendingRef.current = false;
    }
    setCreatorWorkIndex(null);
    setCreatorWorkError("");
    setCreatorWorkLoadingId("");
  }, [items]);

  useEffect(() => {
    setLifecycleError("");
    setProposalHistoryOpen(false);
    setCreditCreatorId("");
    setCreditRole("creator");
    setCreatorNameDraft("");
    setCreatorIdDraft("");
    setCreatorIdTouched(false);
    setRelationshipTargetId("");
    setRelationshipType("same_franchise");
  }, [selected?.id]);

  useEffect(() => {
    if (!dirty) return;
    function protectDraft(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", protectDraft);
    return () => window.removeEventListener("beforeunload", protectDraft);
  }, [dirty]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (backupPendingRef.current) return;
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    setAliasDraft("");
    setTaxonomyKind("genre");
    setTaxonomyValue("");
    resetRatingDraft();
    void loadMedia(query.trim());
  }

  function changeSelected(patch: Partial<MediaItem>) {
    if (!selected) return;
    setSelected({ ...selected, ...patch });
    setSaveState("idle");
    setSaveSuccessMessage("");
    setSaveError("");
  }

  function changeCurrentStatus(status: ConsumptionStatus) {
    const progressWasPristine = !progressDraftDirty;
    changeSelected({ status });
    if (progressWasPristine) {
      progressDefaultStatusRef.current = status;
      setProgressStatus(status);
    }
  }
  function addAlias(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const value = aliasDraft.trim();
    if (!value || aliasDuplicate) return;
    changeSelected({ aliases: [...aliasesFor(selected), { value }] });
    setAliasDraft("");
  }

  function removeAlias(indexToRemove: number) {
    if (!selected) return;
    changeSelected({
      aliases: aliasesFor(selected).filter((_, index) => index !== indexToRemove),
    });
  }

  function recordRating() {
    if (!selected) return;
    const score = ratingScoreNumber;
    if (
      !ratingDate ||
      !Number.isFinite(score) ||
      score < 1 ||
      score > 10 ||
      ratingDateInvalid
    ) return;
    const rating: RatingValue = {
      score,
      rated_on: ratingDate,
      ...(ratingProvisional ? { provisional: true } : {}),
    };
    changeSelected({
      rating,
      rating_history: [...ratingHistoryFor(selected), rating],
    });
    setRatingScore("");
    setRatingProvisional(false);
    ratingDefaultDateRef.current = ratingDate;
  }

  function resetRatingDraft() {
    const date = localCalendarDate();
    ratingDefaultDateRef.current = date;
    setRatingScore("");
    setRatingDate(date);
    setRatingProvisional(false);
  }

  function recordProgress() {
    if (!selected || progressDateMissing || progressAmountInvalid || progressLifecycleInvalid) return;
    const record: ProgressValue = {
      status: progressStatus,
      recorded_on: progressDate,
      ...(progressAmount ? { amount_completed: progressAmountNumber } : {}),
      ...(progressUnit ? { unit: progressUnit } : {}),
      ...(progressStartedOn ? { started_on: progressStartedOn } : {}),
      ...(progressEndedOn ? { ended_on: progressEndedOn } : {}),
      ...(progressReturnIntent ? { return_intent: progressReturnIntent === "true" } : {}),
      ...(progressReason.trim() ? { reason: progressReason.trim() } : {}),
    };
    changeSelected({
      status: progressStatus,
      progress_records: [...progressRecordsFor(selected), record],
    });
    resetProgressDraft(progressStatus, progressDate);
  }

  function resetProgressDraft(
    status: ConsumptionStatus,
    date = localCalendarDate(),
  ) {
    progressDefaultStatusRef.current = status;
    progressDefaultDateRef.current = date;
    setProgressStatus(status);
    setProgressAmount("");
    setProgressUnit("");
    setProgressDate(date);
    setProgressStartedOn("");
    setProgressEndedOn("");
    setProgressReturnIntent("");
    setProgressReason("");
  }

  function recordObservation() {
    setObservationAttempted(true);
    if (!selected || observationSubjectInvalid || observationRequiredMissing) return;
    const observation: ObservationValue = {
      id: nextObservationId(selected),
      scope: observationScope,
      ...(observationNeedsSubject ? {
        subject_id: observationSubjectId.trim(),
        subject_label: observationSubjectLabel.trim(),
      } : {}),
      polarity: observationPolarity,
      dimension: observationDimension.trim(),
      text: observationText.trim(),
      provenance: observationProvenance,
      privacy: observationPrivacy,
      ...(observationSourceContext.trim() ? {
        source_context: observationSourceContext.trim(),
      } : {}),
      review_state: "accepted",
      observed_on: observationDate,
    };
    changeSelected({ observations: [...observationsFor(selected), observation] });
    resetObservationDraft(observationDate);
  }

  function resetObservationDraft(date = localCalendarDate()) {
    observationDefaultDateRef.current = date;
    setObservationScope("work");
    setObservationSubjectId("");
    setObservationSubjectLabel("");
    setObservationPolarity("mixed");
    setObservationDimension("");
    setObservationText("");
    setObservationProvenance("manual");
    setObservationPrivacy("assistant_readable");
    setObservationSourceContext("");
    setObservationDate(date);
    setObservationAttempted(false);
  }

  async function save() {
    if (!selected || backupPendingRef.current) return;
    backupPendingRef.current = true;
    const wasCreating = creating;
    setSaveState("saving");
    setSaveError("");
    try {
      const savedItem = wasCreating ? await createMedia(selected) : await saveMedia(selected);
      setSelected(savedItem);
      setItems((currentItems) => {
        if (wasCreating) {
          return [...currentItems, savedItem].sort((left, right) => left.id.localeCompare(right.id));
        }
        return currentItems.map((item) => (item.id === savedItem.id ? savedItem : item));
      });
      if (wasCreating) {
        setEditorMode("edit");
        setCreateBaseline(null);
        setStableIdTouched(false);
      }
      setSaveSuccessMessage(wasCreating ? "Created locally" : "Saved locally");
      setSaveState("saved");
    } catch (error) {
      setSaveError(
        error instanceof TypeError
          ? "The local library is unavailable. Your draft is still here."
          : error instanceof Error
            ? error.message
            : "The local library could not save this record.",
      );
      setSaveState("idle");
    } finally {
      backupPendingRef.current = false;
    }
  }

  function cancelEdits() {
    if (backupPendingRef.current) return;
    if (!selected) return;
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    setAliasDraft("");
    setTaxonomyKind("genre");
    setTaxonomyValue("");
    resetRatingDraft();
    resetProgressDraft(
      creating ? items[0]?.status ?? "planned" : persisted?.status ?? selected.status ?? "planned",
    );
    resetObservationDraft();
    setCreditCreatorId("");
    setCreditRole("creator");
    setCreatorNameDraft("");
    setCreatorIdDraft("");
    setCreatorIdTouched(false);
    setRelationshipTargetId("");
    setRelationshipType("same_franchise");
    if (creating) {
      setSelected(items[0] ?? null);
      setEditorMode("edit");
      setCreateBaseline(null);
      setStableIdTouched(false);
      setSaveState("idle");
      setSaveError("");
      return;
    }
    if (!persisted || !dirty) return;
    setSelected(persisted);
    setSaveState("idle");
    setSaveError("");
  }

  function openItem(item: MediaItem) {
    if (backupPendingRef.current) return;
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    setSelected(item);
    setProposalError("");
    setProposalReviewMessage("");
    setEditorMode("edit");
    setCreateBaseline(null);
    setStableIdTouched(false);
    setAliasDraft("");
    setTaxonomyKind("genre");
    setTaxonomyValue("");
    resetRatingDraft();
    resetProgressDraft(item.status ?? "planned");
    resetObservationDraft();
    setSaveState("idle");
    setSaveError("");
  }

  function startCreate() {
    if (backupPendingRef.current) return;
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    const draft = emptyMediaItem();
    setSelected(draft);
    setProposalError("");
    setProposalReviewMessage("");
    setCreateBaseline(draft);
    setEditorMode("create");
    setStableIdTouched(false);
    setAliasDraft("");
    setTaxonomyKind("genre");
    setTaxonomyValue("");
    resetRatingDraft();
    resetProgressDraft("planned");
    resetObservationDraft();
    setSaveState("idle");
    setSaveError("");
  }

  function goHome() {
    if (backupPendingRef.current) return;
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    setAliasDraft("");
    setTaxonomyKind("genre");
    setTaxonomyValue("");
    resetRatingDraft();
    setQuery("");
    void loadMedia("");
  }

  if (loading) {
    return (
      <main className="center-state" aria-busy="true">
        <span className="brand-mark" aria-hidden="true">✦</span>
        <p>Loading your library…</p>
      </main>
    );
  }

  if (loadError) {
    return (
      <main className="center-state">
        <span className="brand-mark" aria-hidden="true">✦</span>
        <h1>Library out of reach</h1>
        <p role="alert">{loadError}</p>
        <p>Start the loopback service, then try the shelf again.</p>
        <button className="primary" type="button" onClick={() => void loadMedia(query.trim())}>
          Try again
        </button>
      </main>
    );
  }

  const titleForMediaId = (id: string) => items.find((item) => item.id === id)?.title ?? id;
  const openMediaById = (id: string) => {
    const item = items.find((candidate) => candidate.id === id);
    if (item) openItem(item);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <button
          className="brand"
          type="button"
          aria-label="Concierge home"
          disabled={editorLocked}
          onClick={goHome}
        >
          <span className="brand-mark" aria-hidden="true">✦</span>
          <span>Concierge</span>
        </button>
        <span className={dirty ? "draft-state dirty" : "draft-state"}>
          {dirty ? "Unsaved changes" : "Local library"}
        </span>
      </header>

      {selected ? (
        <main className="editor">
          <div className="editor-heading">
            <div>
              <p className="eyebrow">{creating ? "New media record" : "Media record · core fields"}</p>
              <h1>{selected.title || "Untitled record"}</h1>
              <p>
                Change the durable record without flattening the history and evidence already attached to it.
              </p>
            </div>
          </div>

          <section className="panel" aria-labelledby="core-heading">
            <div className="panel-heading">
              <div>
                <h2 id="core-heading">Identity and current state</h2>
                <p>Required values sent through the local application API.</p>
              </div>
              <span className="required-note">4 core fields</span>
            </div>
            {saveError ? (
              <div
                ref={validationSummaryRef}
                className="validation-summary"
                role="alert"
                tabIndex={-1}
              >
                <strong>Couldn’t save this record</strong>
                <p>{saveError}</p>
              </div>
            ) : null}
            <div className="field-grid">
              <div className="field field-wide">
                <label htmlFor="title">Title</label>
                <input
                  id="title"
                  value={selected.title}
                  disabled={editorLocked}
                  aria-invalid={titleInvalid}
                  aria-describedby={titleInvalid ? "title-error" : undefined}
                  onChange={(event) => {
                    const title = event.target.value;
                    changeSelected({
                      title,
                      ...(creating && !stableIdTouched
                        ? { id: suggestStableId(selected.category, title) }
                        : {}),
                    });
                  }}
                />
                {titleInvalid ? (
                  <p className="field-error" id="title-error">Title is required.</p>
                ) : null}
              </div>

              <div className="field field-wide">
                <label htmlFor="stable-id">Stable ID</label>
                <input
                  id="stable-id"
                  value={selected.id}
                  readOnly={!creating}
                  disabled={editorLocked}
                  aria-invalid={creating && idInvalid}
                  aria-describedby={creating && idInvalid ? "stable-id-error" : "stable-id-help"}
                  onChange={(event) => {
                    setStableIdTouched(true);
                    changeSelected({ id: event.target.value });
                  }}
                />
                {creating && idInvalid ? (
                  <p className="field-error" id="stable-id-error">Stable ID is required.</p>
                ) : null}
                <p className="field-help" id="stable-id-help">
                  {creating
                    ? stableIdTouched
                      ? "Your custom stable ID will not be replaced by later title changes."
                      : "Suggested from category and title until you edit it."
                    : "Existing stable IDs are immutable; renaming needs an atomic API workflow."}
                </p>
              </div>

              <div className="field">
                <label htmlFor="category">Category</label>
                <select
                  id="category"
                  value={selected.category}
                  disabled={editorLocked}
                  onChange={(event) => {
                    const category = event.target.value as MediaCategory;
                    const categorySupportsConsumption = categoryHasCapability(category, "consumption");
                    changeSelected({
                      category,
                      status: categorySupportsConsumption ? selected.status ?? "planned" : undefined,
                      ...(creating && !stableIdTouched
                        ? { id: suggestStableId(category, selected.title) }
                        : {}),
                    });
                  }}
                >
                  {mediaCategories.map((category) => (
                    <option key={category} value={category} disabled={categoryTransitionBlocked(category)}>
                      {labelFor(category)}
                    </option>
                  ))}
                </select>
              </div>

              {supportsConsumption ? (
                <div className="field">
                  <label htmlFor="status">Status</label>
                  <select
                    id="status"
                    value={selected.status}
                    disabled={editorLocked}
                    onChange={(event) => changeCurrentStatus(event.target.value as ConsumptionStatus)}
                  >
                    {consumptionStatuses.map((status) => (
                      <option key={status} value={status}>{labelFor(status)}</option>
                    ))}
                  </select>
                </div>
              ) : null}
            </div>
          </section>

          <section className="alias-panel" aria-labelledby="aliases-heading">
            <div className="alias-heading">
              <div>
                <p className="eyebrow">Useful optional detail</p>
                <h2 id="aliases-heading">Alternate titles</h2>
              </div>
              <span
                aria-label={`${selectedAliases.length} alternate ${selectedAliases.length === 1 ? "title" : "titles"}`}
              >
                {selectedAliases.length}
              </span>
            </div>
            {selectedAliases.length ? (
              <ul className="alias-list">
                {selectedAliases.map((alias, index) => (
                  <li key={`${alias.value}-${index}`}>
                    <span>{alias.value}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${alias.value}`}
                      disabled={editorLocked}
                      onClick={() => removeAlias(index)}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="alias-empty">No alternate titles recorded.</p>
            )}
            <form className="alias-form" onSubmit={addAlias}>
              <label htmlFor="new-alias">New alternate title</label>
              <div>
                <input
                  id="new-alias"
                  value={aliasDraft}
                  disabled={editorLocked}
                  aria-invalid={aliasDuplicate}
                  aria-describedby={aliasDuplicate ? "alias-error alias-help" : "alias-help"}
                  onChange={(event) => setAliasDraft(event.target.value)}
                />
                <button type="submit" disabled={editorLocked || !aliasDraft.trim() || aliasDuplicate}>
                  Add alternate title
                </button>
              </div>
              {aliasDuplicate ? (
                <p className="field-error" id="alias-error">
                  That alternate title is already recorded.
                </p>
              ) : null}
              <p className="field-help" id="alias-help">
                Press Enter or use Add. Finish or clear this field before saving.
              </p>
            </form>
          </section>

          <section className="taxonomy-panel" aria-labelledby="taxonomy-heading">
            <div className="alias-heading">
              <div>
                <p className="eyebrow">Structured discovery detail</p>
                <h2 id="taxonomy-heading">Taxonomy</h2>
              </div>
              <span>{selectedTaxonomyTerms.length}</span>
            </div>
            {selectedTaxonomyTerms.length > 0 ? (
              <ul className="taxonomy-list">
                {selectedTaxonomyTerms.map((term, index) => (
                  <li key={`${term.kind}:${term.value}:${index}`}>
                    <span>{labelFor(term.kind)} · {term.value}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${term.kind} ${term.value} taxonomy term`}
                      disabled={editorLocked}
                      onClick={() => removeTaxonomyTerm(index)}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            ) : <p className="alias-empty">No taxonomy terms recorded.</p>}
            <form className="taxonomy-form" onSubmit={addTaxonomyTerm}>
              <div className="field">
                <label htmlFor="taxonomy-kind">Taxonomy kind</label>
                <select id="taxonomy-kind" value={taxonomyKind} disabled={editorLocked}
                  onChange={(event) => setTaxonomyKind(event.target.value as TaxonomyKind)}>
                  {taxonomyKinds.map((kind) => <option key={kind} value={kind}>{labelFor(kind)}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="taxonomy-value">Taxonomy value</label>
                <input id="taxonomy-value" value={taxonomyValue} disabled={editorLocked}
                  aria-invalid={taxonomyDuplicate}
                  aria-describedby={taxonomyDuplicate ? "taxonomy-duplicate-error" : undefined}
                  onChange={(event) => setTaxonomyValue(event.target.value)} />
                {taxonomyDuplicate ? (
                  <p className="field-error" id="taxonomy-duplicate-error">
                    This taxonomy term is already attached.
                  </p>
                ) : null}
              </div>
              <button type="submit" disabled={editorLocked || !taxonomyCandidate || taxonomyDuplicate}>
                Add taxonomy term
              </button>
            </form>
          </section>

          <section className="rating-panel" aria-labelledby="rating-heading">
            <div className="rating-heading">
              <div>
                <p className="eyebrow">Opinion over time</p>
                <h2 id="rating-heading">Rating</h2>
              </div>
              {currentRating ? (
                <div className="current-rating">
                  <strong>{String(currentRating.score)} / 10</strong>
                  <span>{currentRating.rated_on}</span>
                  {currentRating.provisional ? <em>Provisional</em> : null}
                </div>
              ) : (
                <span>Not rated</span>
              )}
            </div>
            <form
              className="rating-form"
              onSubmit={(event) => {
                event.preventDefault();
                recordRating();
              }}
            >
              <div className="field">
                <label htmlFor="rating-score">Rating score</label>
                <input
                  id="rating-score"
                  type="number"
                  min="1"
                  max="10"
                  step="any"
                  value={ratingScore}
                  disabled={editorLocked}
                  aria-invalid={ratingScoreInvalid}
                  aria-describedby={ratingScoreInvalid ? "rating-score-error" : undefined}
                  onChange={(event) => setRatingScore(event.target.value)}
                />
                {ratingScoreInvalid ? (
                  <p className="field-error" id="rating-score-error">
                    Rating must be between 1 and 10.
                  </p>
                ) : null}
              </div>
              <div className="field">
                <label htmlFor="rating-date">Rating date</label>
                <input
                  id="rating-date"
                  type="date"
                  value={ratingDate}
                  disabled={editorLocked}
                  aria-invalid={ratingDateMissing || ratingDateInvalid}
                  aria-describedby={
                    ratingDateMissing
                      ? "rating-date-required"
                      : ratingDateInvalid
                        ? "rating-date-error"
                        : undefined
                  }
                  onChange={(event) => setRatingDate(event.target.value)}
                />
                {ratingDateMissing ? (
                  <p className="field-error" id="rating-date-required">
                    Rating date is required.
                  </p>
                ) : null}
                {ratingDateInvalid ? (
                  <p className="field-error" id="rating-date-error">
                    Rating date cannot be earlier than the latest history entry.
                  </p>
                ) : null}
              </div>
              <label className="rating-provisional">
                <input
                  type="checkbox"
                  checked={ratingProvisional}
                  disabled={editorLocked}
                  onChange={(event) => setRatingProvisional(event.target.checked)}
                />
                Provisional rating
              </label>
              <button
                type="submit"
                disabled={
                  editorLocked ||
                  !ratingScore ||
                  !ratingDate ||
                  ratingScoreInvalid ||
                  ratingDateInvalid
                }
              >
                Record rating
              </button>
            </form>
          </section>

          {supportsConsumption ? (
            <section className="progress-panel" aria-labelledby="progress-heading">
            <div className="progress-heading">
              <div>
                <p className="eyebrow">Consumption over time</p>
                <h2 id="progress-heading">Progress</h2>
              </div>
              {currentProgress ? (
                <div className="current-progress">
                  <strong>{progressSummary(currentProgress)}</strong>
                  <span>{currentProgress.recorded_on}</span>
                </div>
              ) : (
                <span>No progress recorded</span>
              )}
            </div>
            {currentProgress && (currentProgress.started_on || currentProgress.ended_on) ? (
              <p className="progress-lifecycle">{progressLifecycleSummary(currentProgress)}</p>
            ) : null}
            {currentProgress && (currentProgress.return_intent != null || currentProgress.reason) ? (
              <div className="progress-context">
                {currentProgress.return_intent != null ? (
                  <span className="progress-intent">
                    {currentProgress.return_intent ? "Plans to return" : "No return planned"}
                  </span>
                ) : null}
                {currentProgress.reason ? <p>{currentProgress.reason}</p> : null}
              </div>
            ) : null}
            <form
              className="progress-form"
              onSubmit={(event) => {
                event.preventDefault();
                recordProgress();
              }}
            >
              <div className="field">
                <label htmlFor="progress-status">Progress status</label>
                <select
                  id="progress-status"
                  value={progressStatus}
                  disabled={editorLocked}
                  onChange={(event) => setProgressStatus(event.target.value as ConsumptionStatus)}
                >
                  {consumptionStatuses.map((status) => (
                    <option key={status} value={status}>{labelFor(status)}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="progress-date">Progress date</label>
                <input
                  id="progress-date"
                  type="date"
                  value={progressDate}
                  disabled={editorLocked}
                  aria-invalid={progressDateMissing}
                  aria-describedby={progressDateMissing ? "progress-date-error" : undefined}
                  onChange={(event) => setProgressDate(event.target.value)}
                />
                {progressDateMissing ? (
                  <p className="field-error" id="progress-date-error">Progress date is required.</p>
                ) : null}
              </div>
              <div className="field">
                <label htmlFor="progress-amount">Amount completed</label>
                <input
                  id="progress-amount"
                  type="number"
                  min="0"
                  step="any"
                  value={progressAmount}
                  disabled={editorLocked}
                  aria-invalid={progressAmountInvalid}
                  aria-describedby={progressAmountInvalid ? "progress-amount-error" : undefined}
                  onChange={(event) => setProgressAmount(event.target.value)}
                />
                {progressAmountInvalid ? (
                  <p className="field-error" id="progress-amount-error">
                    Amount completed cannot be negative.
                  </p>
                ) : null}
              </div>
              <div className="field">
                <label htmlFor="progress-unit">Progress unit</label>
                <select
                  id="progress-unit"
                  value={progressUnit}
                  disabled={editorLocked}
                  onChange={(event) => setProgressUnit(event.target.value as ProgressUnit | "")}
                >
                  <option value="">Unspecified</option>
                  {progressUnits.map((unit) => (
                    <option key={unit} value={unit}>{labelFor(unit)}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="progress-started">Started on</label>
                <input
                  id="progress-started"
                  type="date"
                  value={progressStartedOn}
                  disabled={editorLocked}
                  onChange={(event) => setProgressStartedOn(event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="progress-ended">Ended on</label>
                <input
                  id="progress-ended"
                  type="date"
                  value={progressEndedOn}
                  disabled={editorLocked}
                  aria-invalid={progressLifecycleInvalid}
                  aria-describedby={progressLifecycleInvalid ? "progress-ended-error" : undefined}
                  onChange={(event) => setProgressEndedOn(event.target.value)}
                />
                {progressLifecycleInvalid ? (
                  <p className="field-error" id="progress-ended-error">
                    Ended on cannot be before Started on.
                  </p>
                ) : null}
              </div>
              <div className="field">
                <label htmlFor="progress-return">Return intent</label>
                <select
                  id="progress-return"
                  value={progressReturnIntent}
                  disabled={editorLocked}
                  onChange={(event) => setProgressReturnIntent(event.target.value as "" | "true" | "false")}
                >
                  <option value="">Unspecified</option>
                  <option value="true">Plans to return</option>
                  <option value="false">Does not plan to return</option>
                </select>
              </div>
              <div className="field field-wide">
                <label htmlFor="progress-reason">Progress reason</label>
                <textarea
                  id="progress-reason"
                  value={progressReason}
                  disabled={editorLocked}
                  placeholder="Why paused, dropped, resumed, or changed pace?"
                  onChange={(event) => setProgressReason(event.target.value)}
                />
              </div>
              <button
                type="submit"
                disabled={
                  editorLocked || progressDateMissing || progressAmountInvalid || progressLifecycleInvalid
                }
              >
                Record progress
              </button>
            </form>
            </section>
          ) : null}

          <section className="observation-panel" aria-labelledby="observation-heading">
            <div className="observation-heading">
              <div>
                <p className="eyebrow">Evidence and opinion over time</p>
                <h2 id="observation-heading">Observations</h2>
              </div>
              {currentObservation ? (
                <div className="current-observation">
                  <strong>{labelFor(currentObservation.polarity)} · {currentObservation.dimension}</strong>
                  <span>{currentObservation.observed_on}</span>
                </div>
              ) : <span>No observations recorded</span>}
            </div>
            {currentObservation ? <p className="observation-current-text">{currentObservation.text}</p> : null}
            <form
              className="observation-form"
              onSubmit={(event) => {
                event.preventDefault();
                recordObservation();
              }}
            >
              <div className="field">
                <label htmlFor="observation-scope">Observation scope</label>
                <select
                  id="observation-scope"
                  value={observationScope}
                  disabled={editorLocked}
                  onChange={(event) => setObservationScope(event.target.value as ObservationScope)}
                >
                  {observationScopes.map((scope) => (
                    <option key={scope} value={scope}>{labelFor(scope)}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="observation-polarity">Observation polarity</label>
                <select
                  id="observation-polarity"
                  value={observationPolarity}
                  disabled={editorLocked}
                  onChange={(event) => setObservationPolarity(event.target.value as ObservationPolarity)}
                >
                  {observationPolarities.map((polarity) => (
                    <option key={polarity} value={polarity}>{labelFor(polarity)}</option>
                  ))}
                </select>
              </div>
              {observationNeedsSubject ? (
                <>
                  <div className="field">
                    <label htmlFor="observation-subject-id">Subject ID</label>
                    <input
                      id="observation-subject-id"
                      value={observationSubjectId}
                      disabled={editorLocked}
                      aria-invalid={observationSubjectInvalid}
                      aria-describedby={observationSubjectInvalid ? "observation-subject-error" : undefined}
                      onChange={(event) => setObservationSubjectId(event.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="observation-subject-label">Subject label</label>
                    <input
                      id="observation-subject-label"
                      value={observationSubjectLabel}
                      disabled={editorLocked}
                      aria-invalid={observationSubjectInvalid}
                      aria-describedby={observationSubjectInvalid ? "observation-subject-error" : undefined}
                      onChange={(event) => setObservationSubjectLabel(event.target.value)}
                    />
                  </div>
                  {observationSubjectInvalid ? (
                    <p className="field-error field-wide" id="observation-subject-error">
                      Subject ID and label are required outside whole-work scope.
                    </p>
                  ) : null}
                </>
              ) : null}
              <div className="field">
                <label htmlFor="observation-dimension">Observation dimension</label>
                <input
                  id="observation-dimension"
                  value={observationDimension}
                  disabled={editorLocked}
                  aria-invalid={observationDimensionInvalid}
                  aria-describedby={observationDimensionInvalid ? "observation-dimension-error" : undefined}
                  onChange={(event) => setObservationDimension(event.target.value)}
                />
                {observationDimensionInvalid ? (
                  <p className="field-error" id="observation-dimension-error">
                    Observation dimension is required.
                  </p>
                ) : null}
              </div>
              <div className="field">
                <label htmlFor="observation-date">Observed on</label>
                <input
                  id="observation-date"
                  type="date"
                  value={observationDate}
                  disabled={editorLocked}
                  aria-invalid={observationDateInvalid}
                  aria-describedby={observationDateInvalid ? "observation-date-error" : undefined}
                  onChange={(event) => setObservationDate(event.target.value)}
                />
                {observationDateInvalid ? (
                  <p className="field-error" id="observation-date-error">Observed on is required.</p>
                ) : null}
              </div>
              <div className="field field-wide">
                <label htmlFor="observation-text">Observation text</label>
                <textarea
                  id="observation-text"
                  value={observationText}
                  disabled={editorLocked}
                  aria-invalid={observationTextInvalid}
                  aria-describedby={observationTextInvalid ? "observation-text-error" : undefined}
                  placeholder="What specifically worked, did not work, or changed?"
                  onChange={(event) => setObservationText(event.target.value)}
                />
                {observationTextInvalid ? (
                  <p className="field-error" id="observation-text-error">
                    Observation text is required.
                  </p>
                ) : null}
              </div>
              <div className="field">
                <label htmlFor="observation-provenance">Observation provenance</label>
                <select
                  id="observation-provenance"
                  value={observationProvenance}
                  disabled={editorLocked}
                  onChange={(event) => setObservationProvenance(
                    event.target.value as ManualObservationProvenance,
                  )}
                >
                  {manualObservationProvenances.map((provenance) => (
                    <option key={provenance} value={provenance}>{labelFor(provenance)}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="observation-privacy">Observation privacy</label>
                <select
                  id="observation-privacy"
                  value={observationPrivacy}
                  disabled={editorLocked}
                  onChange={(event) => setObservationPrivacy(event.target.value as PrivacyLevel)}
                >
                  {privacyLevels.map((privacy) => (
                    <option key={privacy} value={privacy}>{labelFor(privacy)}</option>
                  ))}
                </select>
              </div>
              <div className="field field-wide">
                <label htmlFor="observation-source">Source context</label>
                <textarea
                  id="observation-source"
                  value={observationSourceContext}
                  disabled={editorLocked}
                  placeholder="Optional note, conversation, reference, or other evidence trail"
                  onChange={(event) => setObservationSourceContext(event.target.value)}
                />
              </div>
              <button
                type="submit"
                disabled={editorLocked || observationSubjectInvalid}
              >
                Record observation
              </button>
            </form>
          </section>

          {supportsCreatorCredits ? (
            <section className="credit-panel" aria-labelledby="credit-heading">
            <div className="proposal-heading">
              <div>
                <p className="eyebrow">People behind the work</p>
                <h2 id="credit-heading">Creator credits</h2>
              </div>
              <button
                type="button"
                aria-expanded={creatorsLoaded}
                aria-controls="credit-editor"
                disabled={editorLocked || creatorsLoading}
                onClick={() => void loadCreatorCredits()}
              >
                {creatorsLoading ? "Loading creators…" : "Manage creator credits"}
              </button>
            </div>
            {creatorError ? <p className="field-error" role="alert">{creatorError}</p> : null}
            {creatorsLoaded ? (
              <div id="credit-editor">
                {selectedCredits.length > 0 ? (
                  <ul className="credit-list">
                    {selectedCredits.map((credit, index) => {
                      const creator = creators.find((candidate) => candidate.id === credit.creator_id);
                      const creatorName = creator?.name ?? credit.creator_id;
                      return (
                        <li key={`${credit.creator_id}:${credit.role}`}>
                          <span>{creatorName} · {labelFor(credit.role)}</span>
                          {creator ? (
                            <button
                              type="button"
                              aria-label={`View ${creatorName} works`}
                              disabled={editorLocked || Boolean(query.trim())}
                              onClick={() => void loadCreatorWorkIndex(creator)}
                            >
                              {creatorWorkLoadingId === creator.id ? "Loading works…" : "View works"}
                            </button>
                          ) : null}
                          <button
                            type="button"
                            aria-label={`Remove ${creatorName} ${credit.role} credit`}
                            disabled={editorLocked}
                            onClick={() => removeCreatorCredit(index)}
                          >
                            Remove
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : <p className="proposal-empty">No creator credits recorded.</p>}
                {creatorWorkLoadingId ? <p role="status">Loading directly credited works…</p> : null}
                {creatorWorkError ? <p role="alert" className="field-error">{creatorWorkError}</p> : null}
                {creatorWorkIndex ? (
                  <section
                    className="creator-work-index"
                    aria-label={`${creatorWorkIndex.creator.name} work index`}
                  >
                    <strong>{creatorWorkIndex.creator.name} · directly credited works</strong>
                    {creatorWorkIndex.items.length ? (
                      <div className="creator-work-list">
                        {creatorWorkIndex.items.map((item) => (
                          <button key={item.id} type="button" onClick={() => openItem(item)}>
                            {item.title}
                          </button>
                        ))}
                      </div>
                    ) : <p>No matching visible works.</p>}
                  </section>
                ) : null}
                <form className="creator-form" onSubmit={(event) => void createCreatorIdentity(event)}>
                  <label htmlFor="creator-name">Creator name</label>
                  <input
                    id="creator-name"
                    value={creatorNameDraft}
                    disabled={editorLocked || creatorSaving}
                    onChange={(event) => {
                      setCreatorNameDraft(event.target.value);
                      if (!creatorIdTouched) setCreatorIdDraft(suggestCreatorId(event.target.value));
                    }}
                  />
                  <label htmlFor="creator-id">Creator stable ID</label>
                  <input
                    id="creator-id"
                    value={creatorIdDraft}
                    disabled={editorLocked || creatorSaving}
                    onChange={(event) => {
                      setCreatorIdTouched(true);
                      setCreatorIdDraft(event.target.value);
                    }}
                  />
                  <button
                    type="submit"
                    disabled={
                      editorLocked || creatorSaving || !creatorNameDraft.trim() || !creatorIdDraft.trim() ||
                      creators.some((creator) => creator.id === creatorIdDraft.trim())
                    }
                  >
                    {creatorSaving ? "Creating creator…" : "Create creator"}
                  </button>
                </form>
                <form className="credit-form" onSubmit={addCreatorCredit}>
                  <label htmlFor="credit-creator">Creator</label>
                  <select
                    id="credit-creator"
                    value={creditCreatorId}
                    disabled={editorLocked}
                    onChange={(event) => setCreditCreatorId(event.target.value)}
                  >
                    <option value="">Choose a creator</option>
                    {creators.map((creator) => (
                      <option key={creator.id} value={creator.id}>{creator.name}</option>
                    ))}
                  </select>
                  <label htmlFor="credit-role">Credit role</label>
                  <select
                    id="credit-role"
                    value={creditRole}
                    disabled={editorLocked}
                    onChange={(event) => setCreditRole(event.target.value as CreatorRole)}
                  >
                    {[
                      "creator", "director", "writer", "artist", "developer", "composer",
                      "performer", "producer", "voice_actor", "other",
                    ].map((role) => <option key={role} value={role}>{labelFor(role)}</option>)}
                  </select>
                  <button type="submit" disabled={editorLocked || !creditCreatorId}>
                    Add creator credit
                  </button>
                </form>
              </div>
            ) : null}
            </section>
          ) : null}

          {supportsRelationships ? (
            <section className="relationship-panel" aria-labelledby="relationship-heading">
            <div className="proposal-heading">
              <div>
                <p className="eyebrow">Connected works</p>
                <h2 id="relationship-heading">Media relationships</h2>
              </div>
              <button
                type="button"
                aria-expanded={relationshipEditorOpen}
                aria-controls="relationship-editor"
                onClick={() => setRelationshipEditorOpen((open) => !open)}
              >
                {relationshipEditorOpen ? "Hide relationship editor" : "Manage media relationships"}
              </button>
            </div>
            {selectedRelationships.length > 0 ? (
              <ul className="relationship-list">
                {selectedRelationships.map((relationship, index) => {
                  const target = items.find((item) => item.id === relationship.target_media_item_id);
                  const targetTitle = target?.title ?? relationship.target_media_item_id;
                  return (
                    <li key={`${relationship.target_media_item_id}:${relationship.relationship_type}`}>
                      <span>{targetTitle} · {labelFor(relationship.relationship_type)}</span>
                      <button
                        type="button"
                        aria-label={`Remove ${targetTitle} ${relationship.relationship_type} relationship`}
                        disabled={editorLocked}
                        onClick={() => removeRelationship(index)}
                      >
                        Remove
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : <p className="proposal-empty">No media relationships recorded.</p>}
            {relationshipEditorOpen ? (
              <form id="relationship-editor" className="relationship-form" onSubmit={addRelationship}>
              <label htmlFor="relationship-target">Related media</label>
              <select
                id="relationship-target"
                value={relationshipTargetId}
                disabled={editorLocked}
                onChange={(event) => setRelationshipTargetId(event.target.value)}
              >
                <option value="">Choose a media record</option>
                {items.filter((item) => item.id !== selected.id).map((item) => (
                  <option key={item.id} value={item.id}>{item.title}</option>
                ))}
              </select>
              <label htmlFor="relationship-type">Relationship type</label>
              <select
                id="relationship-type"
                value={relationshipType}
                disabled={editorLocked}
                onChange={(event) => setRelationshipType(event.target.value as RelationshipType)}
              >
                {[
                  "sequel", "prequel", "adaptation", "remake", "reboot", "spin_off",
                  "same_franchise", "same_creator", "same_universe", "different_season",
                  "different_edition", "channel_video", "game_expansion", "main_side_story",
                ].map((type) => <option key={type} value={type}>{labelFor(type)}</option>)}
              </select>
              <button type="submit" disabled={editorLocked || !relationshipTargetId}>
                Add relationship
              </button>
              </form>
            ) : null}
            </section>
          ) : null}

          <section className="proposal-panel" aria-labelledby="proposal-heading">
            <div className="proposal-heading">
              <div>
                <p className="eyebrow">Assistant distillation · review required</p>
                <h2 id="proposal-heading">Inference proposals</h2>
              </div>
              <button
                type="button"
                disabled={proposalsLoading || saving || reviewPending}
                onClick={() => void loadInferenceProposals()}
              >
                {proposalsLoading ? "Loading proposals…" : "Review inference proposals"}
              </button>
            </div>
            {proposalError ? <p className="field-error" role="alert">{proposalError}</p> : null}
            {proposalReviewMessage ? (
              <p className="proposal-review-message" role="status">{proposalReviewMessage}</p>
            ) : null}
            {proposalsLoaded && pendingObservationProposals.length === 0 ? (
              <p className="proposal-empty">No pending observation proposals for this record.</p>
            ) : null}
            <div className="proposal-list">
              {pendingObservationProposals.map((proposal) => {
                const observation = proposal.proposed_observation!;
                return (
                  <article className="proposal-card" key={proposal.id}>
                    <div className="proposal-summary">
                      <strong>{labelFor(observation.polarity)} · {observation.dimension}</strong>
                      <span>{Math.round(proposal.confidence * 100)}% confidence</span>
                    </div>
                    <p>{observation.text}</p>
                    <dl>
                      <div><dt>Scope</dt><dd>{labelFor(observation.scope)}</dd></div>
                      {observation.subject_label ? (
                        <div><dt>Subject</dt><dd>{observation.subject_label}</dd></div>
                      ) : null}
                      <div><dt>Provenance</dt><dd>{labelFor(observation.provenance)}</dd></div>
                      <div><dt>Privacy</dt><dd>{labelFor(observation.privacy ?? "assistant_readable")}</dd></div>
                      <div><dt>Evidence source</dt><dd>{proposal.source_context}</dd></div>
                      {observation.source_context ? (
                        <div><dt>Distilled evidence</dt><dd>{observation.source_context}</dd></div>
                      ) : null}
                      <div><dt>Proposed on</dt><dd>{proposal.proposed_on}</dd></div>
                    </dl>
                    <div className="proposal-actions">
                      <button
                        type="button"
                        disabled={reviewPending}
                        onClick={() => void reviewInference(proposal.id, "reject")}
                      >
                        Reject inference
                      </button>
                      <button
                        type="button"
                        disabled={reviewPending}
                        onClick={() => void reviewInference(proposal.id, "accept")}
                      >
                        {reviewingProposalId === proposal.id ? "Saving review…" : "Accept inference"}
                      </button>
                    </div>
                  </article>
                );
              })}
              {acceptedObservationProposals.map((proposal) => {
                const observation = proposal.proposed_observation!;
                return (
                  <article className="proposal-card promotion-card" key={proposal.id}>
                    <div className="proposal-summary">
                      <strong>Accepted inference awaiting promotion</strong>
                      <span>{Math.round(proposal.confidence * 100)}% confidence</span>
                    </div>
                    <p>{observation.text}</p>
                    <dl>
                      <div><dt>Scope</dt><dd>{labelFor(observation.scope)}</dd></div>
                      {observation.subject_label ? (
                        <div><dt>Subject</dt><dd>{observation.subject_label}</dd></div>
                      ) : null}
                      <div><dt>Provenance</dt><dd>{labelFor(observation.provenance)}</dd></div>
                      <div><dt>Privacy</dt><dd>{labelFor(observation.privacy ?? "assistant_readable")}</dd></div>
                      <div><dt>Evidence source</dt><dd>{proposal.source_context}</dd></div>
                      {observation.source_context ? (
                        <div><dt>Distilled evidence</dt><dd>{observation.source_context}</dd></div>
                      ) : null}
                      <div><dt>Proposed on</dt><dd>{proposal.proposed_on}</dd></div>
                    </dl>
                    <button
                      type="button"
                      disabled={dirty || reviewPending}
                      onClick={() => void promoteInference(proposal.id)}
                    >
                      {promotingProposalId === proposal.id ? "Promoting…" : "Promote to evidence"}
                    </button>
                  </article>
                );
              })}
              {pendingMediaProposals.map((proposal) => {
                const candidate = proposal.proposed_media_item!;
                return (
                  <article className="proposal-card" key={proposal.id}>
                    <div className="proposal-summary">
                      <strong>New library candidate · {candidate.title}</strong>
                      <span>{Math.round(proposal.confidence * 100)}% confidence</span>
                    </div>
                    <dl>
                      <div><dt>Category</dt><dd>{labelFor(candidate.category)}</dd></div>
                      {candidate.status ? <div><dt>Status</dt><dd>{labelFor(candidate.status)}</dd></div> : null}
                      <div><dt>Conversation source</dt><dd>{proposal.source_context}</dd></div>
                      <div><dt>Proposed on</dt><dd>{proposal.proposed_on}</dd></div>
                    </dl>
                    <div className="proposal-actions">
                      <button type="button" disabled={reviewPending} onClick={() => void reviewInference(proposal.id, "reject")}>Reject candidate</button>
                      <button type="button" disabled={reviewPending} onClick={() => void reviewInference(proposal.id, "accept")}>
                        {reviewingProposalId === proposal.id ? "Saving review…" : "Accept candidate"}
                      </button>
                    </div>
                  </article>
                );
              })}
              {acceptedMediaProposals.map((proposal) => {
                const candidate = proposal.proposed_media_item!;
                return (
                  <article className="proposal-card promotion-card" key={proposal.id}>
                    <div className="proposal-summary"><strong>Accepted candidate awaiting promotion · {candidate.title}</strong><span>{Math.round(proposal.confidence * 100)}% confidence</span></div>
                    <p>Promotion creates this record once in the canonical library.</p>
                    <button type="button" disabled={dirty || reviewPending} onClick={() => void promoteMediaCandidate(proposal.id)}>
                      {promotingProposalId === proposal.id ? "Promoting…" : "Promote to library"}
                    </button>
                  </article>
                );
              })}
            </div>
            {reviewedProposals.length > 0 ? (
              <div className="proposal-history-disclosure">
                <button
                  type="button"
                  aria-expanded={proposalHistoryOpen}
                  aria-controls="proposal-history"
                  onClick={() => setProposalHistoryOpen((open) => !open)}
                >
                  {proposalHistoryOpen ? "Hide" : "Show"} proposal history ({reviewedProposals.length})
                </button>
                {proposalHistoryOpen ? (
                  <section id="proposal-history" aria-labelledby="proposal-history-heading">
                    <h3 id="proposal-history-heading">Proposal history</h3>
                    <div className="proposal-list">
                      {reviewedProposals.map((proposal) => {
                        const observation = proposal.proposed_observation;
                        return (
                          <article className="proposal-card proposal-history-card" key={proposal.id}>
                            <div className="proposal-summary">
                              <strong>
                                {proposal.promoted_observation_id
                                  ? `Promoted as ${proposal.promoted_observation_id}`
                                  : labelFor(proposal.review_state)}
                              </strong>
                              <span>{Math.round(proposal.confidence * 100)}% confidence</span>
                            </div>
                            {observation ? <p>{observation.text}</p> : null}
                            <dl>
                              <div><dt>Kind</dt><dd>{labelFor(proposal.kind)}</dd></div>
                              {proposal.kind === "metadata" && proposal.metadata_field ? (
                                <>
                                  <div><dt>Field</dt><dd>{labelFor(proposal.metadata_field)}</dd></div>
                                  <div>
                                    <dt>Proposed value</dt>
                                    <dd><code>{JSON.stringify(proposal.metadata_value)}</code></dd>
                                  </div>
                                </>
                              ) : null}
                              <div><dt>Evidence source</dt><dd>{proposal.source_context}</dd></div>
                              <div><dt>Proposed on</dt><dd>{proposal.proposed_on}</dd></div>
                            </dl>
                          </article>
                        );
                      })}
                    </div>
                  </section>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="future-panel lifecycle-panel" aria-label="Library lifecycle">
            <div>
              <strong>Library lifecycle</strong>
              {selected.archived_on ? (
                <span>Archived on {String(selected.archived_on)}. Restore returns it to ordinary library views.</span>
              ) : (
                <span>Archive hides this record from ordinary library views without deleting it.</span>
              )}
            </div>
            {lifecycleError ? <p className="field-error" role="alert">{lifecycleError}</p> : null}
            <button
              type="button"
              disabled={editorLocked || creating || dirty}
              onClick={() => void (selected.archived_on ? restoreSelected() : archiveSelected())}
            >
              {lifecyclePending === "archive"
                ? "Archiving…"
                : lifecyclePending === "restore"
                  ? "Restoring…"
                  : selected.archived_on
                    ? "Restore record"
                    : "Archive record"}
            </button>
          </section>

          <div className="actions">
            <button
              className="secondary"
              type="button"
              disabled={(!dirty && !creating) || editorLocked}
              onClick={cancelEdits}
            >
              Cancel edits
            </button>
            <button
              className="primary"
              type="button"
              disabled={
                editorLocked ||
                idInvalid ||
                titleInvalid ||
                saveState !== "idle" ||
                !recordDirty ||
                Boolean(aliasDraft) ||
                ratingDraftDirty ||
                progressDraftDirty ||
                observationDraftDirty ||
                creditDraftDirty ||
                relationshipDraftDirty ||
                taxonomyDraftDirty
              }
              onClick={() => void save()}
            >
              {creating
                ? saveState === "saving"
                  ? "Creating…"
                  : "Create entry"
                : saveState === "saving"
                  ? "Saving…"
                  : saveState === "saved"
                    ? "Saved"
                    : "Save changes"}
            </button>
          </div>
          <div className="messages" aria-live="polite">
            <p role="status">{saveState === "saved" ? saveSuccessMessage : ""}</p>
          </div>
        </main>
      ) : items.length === 0 && !query.trim() && !showArchived ? (
        <EmptyLibraryState
          editorLocked={editorLocked}
          dirty={dirty}
          onStartCreate={startCreate}
          onReviewPortableExport={() => importInputRef.current?.click()}
          onRestoreLocalBackup={() => void restoreLocalBackup()}
        />
      ) : (
        <main className="editor empty-state">
          <p className="eyebrow">Active library</p>
          <h1>No records found</h1>
          <p>Try a broader title or clear the search.</p>
        </main>
      )}

      <aside className="library" aria-label="Media library">
        <div className="library-heading">
          <div>
            <p className="eyebrow">Your shelf</p>
            <strong>
              {showArchived
                ? `${items.length} visible ${items.length === 1 ? "record" : "records"}`
                : `${items.length} active ${items.length === 1 ? "record" : "records"}`}
            </strong>
          </div>
          <button className="new-entry" type="button" disabled={editorLocked} onClick={startCreate}>
            New entry
          </button>
        </div>
        <form className="search" role="search" onSubmit={search}>
          <label htmlFor="library-search">Search library</label>
          <div className="search-row">
            <input
              id="library-search"
              type="search"
              value={query}
              disabled={editorLocked}
              placeholder="Title or alias"
              onChange={(event) => setQuery(event.target.value)}
            />
            <button type="submit" disabled={editorLocked}>Search</button>
          </div>
        </form>
        <button
          className="archive-visibility"
          type="button"
          disabled={editorLocked}
          aria-pressed={showArchived}
          onClick={toggleArchivedVisibility}
        >
          {showArchived ? "Hide archived records" : "Show archived records"}
        </button>
        <section className="taste-report-panel" aria-label="Taste report review">
          <button
            className="taste-report-toggle"
            type="button"
            aria-expanded={tasteReportOpen}
            disabled={editorLocked || Boolean(query.trim())}
            onClick={() => void toggleTasteReport()}
          >
            {tasteReportLoading
              ? "Loading cited taste report…"
              : tasteReportOpen
                ? "Close cited taste report"
                : query.trim()
                  ? "Clear search to review taste report"
                  : "Review cited taste report"}
          </button>
          {tasteReportOpen ? (
            <div role="region" aria-label="Cited taste report" className="taste-report-results">
              <h2>Taste evidence report</h2>
              <p>This read-only composition preserves canonical recorded history and accepted visible evidence. It does not generate a personality claim or taste score.</p>
              {tasteReportLoading ? <p role="status">Loading complete visible-library evidence…</p> : null}
              {tasteReportError ? <p role="alert">{tasteReportError}</p> : null}
              {tasteReport ? (
                <>
                  <section className="taste-report-section" aria-labelledby="taste-report-ratings">
                    <h3 id="taste-report-ratings">Rating history</h3>
                    {tasteReport.rating_history.entries.length === 0 ? <p>No rated visible works.</p> : null}
                    {tasteReport.rating_history.entries.map((entry) => (
                      <article className="taste-report-work" key={entry.media_item_id}>
                        <h4><button type="button" onClick={() => openMediaById(entry.media_item_id)}>{entry.title}</button></h4>
                        <p>Current rating {entry.current_rating.score}/10 on {entry.current_rating.rated_on}</p>
                        <ol>
                          {entry.rating_history.map((rating, index) => (
                            <li key={`${rating.rated_on}-${index}`}>{rating.rated_on}: {rating.score}/10{rating.provisional ? " · provisional" : ""}</li>
                          ))}
                        </ol>
                      </article>
                    ))}
                  </section>
                  <section className="taste-report-section" aria-labelledby="taste-report-progress">
                    <h3 id="taste-report-progress">Progress context</h3>
                    <p>Recorded consumption history only; no motivation or preference is inferred.</p>
                    {tasteReport.progress_context.entries.length === 0 ? <p>No progress history in the visible library.</p> : null}
                    {tasteReport.progress_context.entries.map((entry) => (
                      <article className="taste-report-work" key={entry.media_item_id}>
                        <h4><button type="button" onClick={() => openMediaById(entry.media_item_id)}>{entry.title}</button></h4>
                        <p>Current library status: {labelFor(entry.current_status)}</p>
                        <ol>
                          {entry.progress_history.map((record, index) => (
                            <li key={`${record.recorded_on}-${index}`}>{reportProgressSummary(record)}</li>
                          ))}
                        </ol>
                      </article>
                    ))}
                  </section>
                  <section className="taste-report-section" aria-labelledby="taste-report-creators">
                    <h3 id="taste-report-creators">Creator credits</h3>
                    <p>Recorded attribution only; no creator affinity or recommendation weight is inferred.</p>
                    {tasteReport.creator_context.entries.length === 0 ? <p>No creator credits in the visible library.</p> : null}
                    {tasteReport.creator_context.entries.map((entry) => (
                      <article className="taste-report-work" key={entry.media_item_id}>
                        <h4><button type="button" onClick={() => openMediaById(entry.media_item_id)}>{entry.title}</button></h4>
                        <ul>{entry.credits.map((credit) => <li key={`${credit.creator_id}:${credit.role}`}>{credit.creator_name} · {labelFor(credit.role)}</li>)}</ul>
                      </article>
                    ))}
                  </section>
                  <section className="taste-report-section" aria-labelledby="taste-report-relationships">
                    <h3 id="taste-report-relationships">Relationship context</h3>
                    <p>Stored directed links only; no preference or unstated relationship is inferred.</p>
                    {tasteReport.relationship_context.entries.length === 0 ? <p>No visible stored relationships.</p> : null}
                    {tasteReport.relationship_context.entries.map((entry) => (
                      <article className="taste-report-work" key={entry.media_item_id}>
                        <h4><button type="button" onClick={() => openMediaById(entry.media_item_id)}>{entry.title}</button></h4>
                        <ul>{entry.relationships.map((relationship) => (
                          <li key={`${relationship.target_media_item_id}:${relationship.relationship_type}`}>
                            {labelFor(relationship.relationship_type)} · <button type="button" onClick={() => openMediaById(relationship.target_media_item_id)}>{relationship.target_title}</button> · {labelFor(relationship.target_category)}
                          </li>
                        ))}</ul>
                      </article>
                    ))}
                  </section>
                  <section className="taste-report-section" aria-label="Cited dimensions">
                    <h3>Observed dimensions</h3>
                    {tasteReport.dimensions.length === 0 ? <p>No accepted assistant-readable evidence in the visible library.</p> : null}
                    {tasteReport.dimensions.map((profile) => (
                      <details className="taste-report-dimension" open key={profile.dimension}>
                        <summary><h3>{labelFor(profile.dimension)}</h3><span>{profile.entries.length} cited {profile.entries.length === 1 ? "work" : "works"}</span></summary>
                        {profile.entries.map((entry) => (
                          <article className="taste-report-work" key={entry.media_item_id}>
                            <h4><button type="button" onClick={() => openMediaById(entry.media_item_id)}>{entry.title}</button></h4>
                            <p>{entry.current_rating ? `Current rating ${entry.current_rating.score}/10 on ${entry.current_rating.rated_on}` : "No rating recorded."}</p>
                            {([
                              ["Supporting evidence", entry.supporting_evidence],
                              ["Contradictory evidence", entry.contradictory_evidence],
                              ["Context evidence", entry.context_evidence],
                            ] as const).map(([heading, evidence]) => evidence.length ? (
                              <div className="taste-report-evidence" key={heading}>
                                <h4>{heading}</h4>
                                <ul>{evidence.map((observation) => <li key={observation.id}><span>{observation.observed_on}</span><p>{observation.text}</p></li>)}</ul>
                              </div>
                            ) : null)}
                          </article>
                        ))}
                      </details>
                    ))}
                  </section>
                </>
              ) : null}
            </div>
          ) : null}
        </section>
        <section className="dimension-profile-panel" aria-label="Dimension evidence review">
          <label htmlFor="evidence-dimension">Evidence dimension</label>
          <input
            id="evidence-dimension"
            type="text"
            value={dimensionDraft}
            disabled={editorLocked}
            placeholder="e.g. pacing, visuals, tone"
            onChange={(event) => setDimensionDraft(event.target.value)}
          />
          <button
            className="dimension-profile-toggle"
            type="button"
            aria-expanded={dimensionProfileOpen}
            disabled={editorLocked || Boolean(query.trim()) || (!dimensionDraft.trim() && !dimensionProfileOpen)}
            onClick={() => void toggleDimensionProfile()}
          >
            {dimensionProfileLoading
              ? "Loading cited dimension…"
              : dimensionProfileOpen
                ? "Close cited dimension profile"
                : query.trim()
                  ? "Clear search to review a dimension"
                  : dimensionDraft.trim()
                    ? "Review cited dimension"
                    : "Enter a dimension to review"}
          </button>
          {dimensionProfileOpen ? (
            <div role="region" aria-label="Cited dimension profile" className="dimension-profile-results">
              {dimensionProfileLoading ? <p role="status">Loading accepted evidence for this dimension…</p> : null}
              <p>Read-only cited evidence across visible works. No dimension score or hidden aggregation is generated.</p>
              {dimensionProfileError ? <p role="alert">{dimensionProfileError}</p> : null}
              {dimensionProfile ? <h2>{labelFor(dimensionProfile.dimension)}</h2> : null}
              {!dimensionProfileLoading && !dimensionProfileError && dimensionProfile?.entries.length === 0 ? (
                <p>No accepted evidence found for this dimension in the visible library.</p>
              ) : null}
              {dimensionProfile?.entries.map((entry) => (
                <article className="dimension-profile-card" key={entry.media_item_id}>
                  <button type="button" onClick={() => openMediaById(entry.media_item_id)}>{entry.title}</button>
                  <span>{labelFor(entry.category)}</span>
                  <p>{entry.current_rating ? `Current rating ${entry.current_rating.score}/10 on ${entry.current_rating.rated_on}` : "No rating recorded."}</p>
                  {([
                    ["Supporting evidence", entry.supporting_evidence],
                    ["Contradictory evidence", entry.contradictory_evidence],
                    ["Context evidence", entry.context_evidence],
                  ] as const).map(([heading, evidence]) => (
                    <div className="dimension-profile-evidence" key={heading}>
                      <h3>{heading}</h3>
                      {evidence.length ? (
                        <ul>
                          {evidence.map((observation) => (
                            <li key={observation.id}>
                              <span>{observation.observed_on}</span>
                              <p>{observation.text}</p>
                            </li>
                          ))}
                        </ul>
                      ) : <p>None recorded.</p>}
                    </div>
                  ))}
                </article>
              ))}
            </div>
          ) : null}
        </section>
        <RecommendationJournal
          disabled={editorLocked || dirty}
          onPendingChange={setRecommendationOperationPending}
        />
        <section className="rating-profile-panel" aria-label="Rating evidence review">
          <button
            className="rating-profile-toggle"
            type="button"
            aria-expanded={ratingProfileOpen}
            disabled={editorLocked || Boolean(query.trim())}
            onClick={() => void toggleRatingProfile()}
          >
            {ratingProfileLoading
              ? "Loading cited rating history…"
              : ratingProfileOpen
                ? "Close cited rating history"
                : query.trim()
                  ? "Clear search to review rating history"
                  : "Review cited rating history"}
          </button>
          {ratingProfileOpen ? (
            <div role="region" aria-label="Cited rating history" className="rating-profile-results">
              {ratingProfileLoading ? <p role="status">Loading ratings and accepted cited evidence…</p> : null}
              <p>Read-only history with accepted, assistant-readable citations. No aggregate taste score is generated.</p>
              {ratingProfileError ? <p role="alert">{ratingProfileError}</p> : null}
              {!ratingProfileLoading && !ratingProfileError && ratingProfileEntries.length === 0 ? (
                <p>No rated works found in the visible library.</p>
              ) : null}
              {ratingProfileEntries.map((entry) => (
                <article className="rating-profile-card" key={entry.media_item_id}>
                  <button type="button" onClick={() => openMediaById(entry.media_item_id)}>{entry.title}</button>
                  <span>{labelFor(entry.category)} · Current rating {entry.current_rating.score}/10 on {entry.current_rating.rated_on}</span>
                  <div className="rating-profile-history">
                    <h3>Rating history</h3>
                    <ol>
                      {entry.rating_history.map((rating, index) => (
                        <li key={`${rating.rated_on}:${index}`}>
                          {rating.score}/10 · {rating.rated_on}{rating.provisional ? " · Provisional" : ""}
                        </li>
                      ))}
                    </ol>
                  </div>
                  {([
                    ["Supporting evidence", entry.supporting_evidence],
                    ["Contradictory evidence", entry.contradictory_evidence],
                    ["Context evidence", entry.context_evidence],
                  ] as const).map(([heading, evidence]) => (
                    <div className="rating-profile-evidence" key={heading}>
                      <h3>{heading}</h3>
                      {evidence.length ? (
                        <ul>
                          {evidence.map((observation) => (
                            <li key={observation.id}>
                              <span>{labelFor(observation.dimension)} · {observation.observed_on}</span>
                              <p>{observation.text}</p>
                            </li>
                          ))}
                        </ul>
                      ) : <p>None recorded.</p>}
                    </div>
                  ))}
                </article>
              ))}
            </div>
          ) : null}
        </section>
        <section className="duplicate-panel" aria-label="Duplicate review">
          <button
            className="duplicate-toggle"
            type="button"
            aria-expanded={duplicateReviewOpen}
            disabled={editorLocked || Boolean(query.trim())}
            onClick={() => void toggleDuplicateReview()}
          >
            {duplicateLoading
              ? "Checking possible duplicates…"
              : duplicateReviewOpen
                ? "Close duplicate review"
                : query.trim()
                  ? "Clear search to review duplicates"
                  : "Review possible duplicates"}
          </button>
          {duplicateReviewOpen ? (
            <div role="region" aria-label="Duplicate candidates" className="duplicate-candidates">
              {duplicateLoading ? <p role="status">Checking the visible library for possible duplicates…</p> : null}
              <p>Possible identity collisions only. Review the evidence; no records are changed here.</p>
              {duplicateError ? <p role="alert">{duplicateError}</p> : null}
              {!duplicateLoading && !duplicateError && duplicateCandidates.length === 0 ? (
                <p>No possible duplicates found in the visible library.</p>
              ) : null}
              {duplicateCandidates.map((candidate) => (
                <article
                  className="duplicate-card"
                  key={`${candidate.media_item_id}:${candidate.candidate_media_item_id}`}
                >
                  <strong>Possible duplicate</strong>
                  <div className="duplicate-records">
                    <button type="button" onClick={() => openMediaById(candidate.media_item_id)}>
                      Open {titleForMediaId(candidate.media_item_id)}
                    </button>
                    <span aria-hidden="true">↔</span>
                    <button type="button" onClick={() => openMediaById(candidate.candidate_media_item_id)}>
                      Open {titleForMediaId(candidate.candidate_media_item_id)}
                    </button>
                  </div>
                  <span>Matched identity: {candidate.matched_titles.join(", ")}</span>
                  <p>{candidate.rationale}</p>
                </article>
              ))}
            </div>
          ) : null}
        </section>
        <section className="backup-panel" aria-label="Portability and recovery">
          <div>
            <strong>Portability and recovery</strong>
            <span>Create or restore snapshots, or move a validated portable library document.</span>
          </div>
          <button
            type="button"
            disabled={editorLocked || dirty}
            onClick={() => void createLocalBackup()}
          >
            {backupAction === "create" ? "Creating backup…" : "Create local backup"}
          </button>
          <button
            className="backup-export"
            type="button"
            disabled={editorLocked || dirty}
            onClick={() => void downloadPortableExport()}
          >
            {backupAction === "export" ? "Preparing export…" : "Download portable export"}
          </button>
          <label className="import-file" htmlFor="portable-import">
            Choose portable export
            <input
              ref={importInputRef}
              id="portable-import"
              aria-label="Choose portable export"
              type="file"
              accept=".json,application/json"
              disabled={editorLocked || dirty}
              onChange={(event) => void selectImportFile(event)}
            />
            <span>{backupAction === "review"
              ? "Reviewing the selected document against the local library…"
              : "JSON only · maximum 5 MiB · selecting a file does not write"}</span>
          </label>
          {importPreview ? (
            <section className="import-preview" aria-label="Import preview">
              <strong>{importPreview.fileName}</strong>
              <span>Schema {importPreview.document.schema_version}</span>
              <span>Exported {importPreview.document.exported_on}</span>
              <span>{importPreview.document.media_items.length} {importPreview.document.media_items.length === 1 ? "media record" : "media records"}</span>
              <span>{importPreview.document.creators?.length ?? 0} {(importPreview.document.creators?.length ?? 0) === 1 ? "creator" : "creators"}</span>
              <span>{importPreview.document.proposals?.length ?? 0} {(importPreview.document.proposals?.length ?? 0) === 1 ? "proposal" : "proposals"}</span>
              <span>{importPreview.document.recommendations?.length ?? 0} {(importPreview.document.recommendations?.length ?? 0) === 1 ? "recommendation" : "recommendations"}</span>
              {importPreview.document.schema_version === "1.8" ? (
                <span>{importPreview.document.capture_proposals?.length ?? 0} {(importPreview.document.capture_proposals?.length ?? 0) === 1 ? "typed capture proposal" : "typed capture proposals"}</span>
              ) : null}
              <p>
                Media and creators add or update by stable ID; unrelated records stay. {["1.4", "1.5", "1.6", "1.7", "1.8"].includes(importPreview.document.schema_version)
                  ? "This proposal queue replaces the local proposal queue."
                  : "The local proposal queue remains unchanged for this legacy document."} {importPreview.document.schema_version === "1.6"
                  ? "Recommendations merge create-only by stable ID; unrelated history remains and conflicting IDs are rejected."
                  : ["1.7", "1.8"].includes(importPreview.document.schema_version)
                    ? "Recommendations merge create-only by stable ID; unrelated history remains and conflicting IDs are rejected."
                    : "The local recommendation ledger remains unchanged for this legacy document."} {importPreview.document.schema_version === "1.8"
                  ? "The typed capture-proposal queue replaces the local typed capture queue."
                  : ""}
              </p>
              <div className="import-change-review">
                <h3>Deterministic change review</h3>
                <p>
                  These effects were calculated against one local-library snapshot. Exact before/after
                  records are available below; applying the import rechecks that snapshot atomically.
                </p>
                <ImportReviewCollectionView title="Media records" collection={importPreview.review.media_items} />
                <ImportReviewCollectionView title="Creators" collection={importPreview.review.creators} />
                <ImportReviewCollectionView title="Proposals" collection={importPreview.review.proposals} />
                <ImportReviewCollectionView title="Recommendations" collection={importPreview.review.recommendations} />
                {importPreview.review.capture_proposals ? (
                  <ImportReviewCollectionView title="Typed capture proposals" collection={importPreview.review.capture_proposals} />
                ) : null}
                {!importPreview.review.can_import ? (
                  <div className="field-error" role="alert">
                    <strong>This reviewed document cannot be merged.</strong>
                    <ul>
                      {importPreview.review.blocking_reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
              <div className="import-actions">
                <button
                  className="backup-restore"
                  type="button"
                  disabled={editorLocked || dirty || !importPreview.review.can_import}
                  onClick={() => void mergeLibraryFromImport()}
                >
                  {backupAction === "import" ? "Merging import…" : "Merge portable import"}
                </button>
                <button
                  className="backup-export"
                  type="button"
                  disabled={editorLocked}
                  onClick={clearImportPreview}
                >
                  Clear selected import
                </button>
              </div>
            </section>
          ) : null}
          <button
            className="backup-restore"
            type="button"
            disabled={editorLocked || dirty}
            onClick={() => void restoreLocalBackup()}
          >
            {backupAction === "restore" ? "Restoring backup…" : "Restore latest backup"}
          </button>
          {backupError ? <p className="field-error" role="alert">{backupError}</p> : null}
          {backupMessage ? <p className="backup-status" role="status">{backupMessage}</p> : (
            <p className="backup-status" aria-hidden="true" />
          )}
        </section>
        <nav className="record-list" aria-label={showArchived ? "Active and archived media records" : "Active media records"}>
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={item.id === selected?.id ? "record-card active" : "record-card"}
              aria-current={item.id === selected?.id ? "page" : undefined}
              disabled={editorLocked}
              onClick={() => openItem(item)}
            >
              <strong>{item.title}</strong>
              <span>
                {labelFor(item.category)}{item.status ? ` · ${labelFor(item.status)}` : ""}
                {item.archived_on ? " · Archived" : ""}
              </span>
            </button>
          ))}
        </nav>
      </aside>

      {selected ? (
        <aside className="preview" aria-label="Outgoing record preview">
          <div className="preview-heading">
            <div>
              <p className="eyebrow">Exact outgoing record</p>
              <h2>MediaItem preview</h2>
            </div>
            <span className="contract-pill">{creating ? "POST" : "PUT"}</span>
          </div>
          <p className="preview-note">
            Advanced fields are preserved unchanged. Preview never writes.
          </p>
          <pre data-testid="record-preview">{JSON.stringify(selected, null, 2)}</pre>
        </aside>
      ) : null}
    </div>
  );
}
