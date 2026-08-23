/**
 * How much longer, learned from the work already done.
 *
 * A progress bar says how far; the question people actually have is how long.
 * The only honest source for that is this machine on this batch, so the model
 * here is fitted from jobs that have just finished and refitted as more do.
 *
 * What the real timings look like
 * -------------------------------
 * Measured against this workspace's own transcription history:
 *
 *     media 12s  -> 14.2s of work    (ratio 1.14)
 *     media 220s -> 78.6s of work    (ratio 0.36)
 *     media 145s -> 69.2s of work    (ratio 0.48, a different mode)
 *     media 220s ->  8.6s of work    (ratio 0.04, model already warm)
 *
 * Two things fall out of that, and both shape this file.
 *
 * **A single ratio is wrong.** "Ten seconds per sixty seconds of video" fits
 * one row and is out by a factor of twenty-eight on another. The spread is not
 * noise: it is startup. A short clip is almost all fixed cost - loading a
 * model, opening the file - and a long one amortises it away. So the fit is
 * `fixed + rate x media`, which collapses to the simple ratio only when the
 * fixed part happens to be small.
 *
 * **Different work runs at different rates.** Reading speech and reading
 * on-screen text are not the same job, and averaging them produces a number
 * true of neither. Samples are grouped by shape and never mixed.
 *
 * Least squares is deliberately not used. Two of the rows above are the same
 * clip processed twice, once cold and once warm, and a line through them has a
 * negative slope - an estimate that says a longer video finishes sooner. The
 * fit here is resistant to that by construction: it takes the two ends of the
 * sorted samples, refuses a negative slope, and falls back to the median when
 * the evidence will not support a line.
 */

/** One job that has finished, and what it cost. */
export type Finished = {
  /**
   * What kind of work this was.
   *
   * Free-form, and the caller's job to make meaningful - "speech", "ocr",
   * "speech+ocr", "caption-burn". Two jobs share a shape only if their
   * durations are comparable, because that is the whole assumption.
   */
  shape: string;
  /** Seconds of media it processed. Null when the length was never measured. */
  mediaSeconds: number | null;
  /** Seconds of wall clock it actually took. */
  workSeconds: number;
};

/** One job still to finish, or partly through. */
export type Pending = {
  shape: string;
  mediaSeconds: number | null;
  /** 0 to 1 from the worker, when it reports one. */
  progress?: number | null;
  /** Seconds since a worker picked it up. Null or 0 when it has not started. */
  elapsedSeconds?: number | null;
};

export type Confidence = "rough" | "fair" | "good";

export type Estimate = {
  /** Seconds still to go, never negative. */
  remainingSeconds: number;
  confidence: Confidence;
  /** Where the number came from, in words, for a tooltip. */
  basis: string;
};

/** What a shape's finished jobs say about how long the next one takes. */
export type Model = {
  shape: string;
  /** Seconds a job of this shape costs before it processes anything. */
  fixedSeconds: number;
  /** Seconds of work per second of media, on top of the fixed cost. */
  perMediaSecond: number;
  /** What to expect when the media length is unknown: the median job. */
  flatSeconds: number;
  /** How many finished jobs this was fitted from. */
  samples: number;
  /**
   * How many of those had a measured length behind them.
   *
   * Separate from `samples` because the two can be wildly different, and the
   * choice between the rate and the median turns on it. Measured live: 195
   * finished face blurs, exactly one of which had been queued since lengths
   * started being recorded. That one point set a rate, and a rate beats a
   * median if you let it - so a 60-second clip was estimated at 43 seconds
   * against an observed median of 157.
   */
  measuredSamples: number;
};

/**
 * Below this, a worker's own progress is not yet worth extrapolating.
 *
 * The first fraction of a percent arrives while a model is still loading, and
 * dividing by it produces hours. The model's estimate carries the job until
 * enough of it is done for its own pace to mean something.
 */
const PROGRESS_FLOOR = 0.08;

/** Fewer finished jobs than this and a line through them is not worth fitting. */
const ENOUGH_FOR_A_LINE = 3;

function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]!
    : (sorted[middle - 1]! + sorted[middle]!) / 2;
}

/**
 * Fit one shape's finished jobs.
 *
 * The line is taken from the medians of the shorter and longer halves rather
 * than from every point, which is what makes one warm-cache outlier unable to
 * tip the slope. A slope that still comes out negative is discarded rather
 * than shown: whatever those samples are measuring, it is not length.
 */
export function fit(finished: Finished[], shape: string): Model {
  const mine = finished.filter(
    (item) => item.shape === shape && Number.isFinite(item.workSeconds) && item.workSeconds >= 0,
  );
  const flatSeconds = median(mine.map((item) => item.workSeconds));
  const measured = mine
    .filter((item) => (item.mediaSeconds ?? 0) > 0)
    .sort((a, b) => a.mediaSeconds! - b.mediaSeconds!);

  if (measured.length < ENOUGH_FOR_A_LINE) {
    // Not enough to separate startup from per-second cost. A plain ratio is
    // the honest reading of one or two points - it says "about this much per
    // second" without pretending to know what the first second costs.
    const ratios = measured.map((item) => item.workSeconds / item.mediaSeconds!);
    return {
      shape,
      fixedSeconds: 0,
      perMediaSecond: ratios.length ? median(ratios) : 0,
      flatSeconds,
      samples: mine.length,
      measuredSamples: measured.length,
    };
  }

  const half = Math.floor(measured.length / 2);
  const shorter = measured.slice(0, half);
  const longer = measured.slice(measured.length - half);
  const lowMedia = median(shorter.map((item) => item.mediaSeconds!));
  const lowWork = median(shorter.map((item) => item.workSeconds));
  const highMedia = median(longer.map((item) => item.mediaSeconds!));
  const highWork = median(longer.map((item) => item.workSeconds));

  const span = highMedia - lowMedia;
  const slope = span > 0 ? (highWork - lowWork) / span : 0;
  if (slope <= 0) {
    // The samples disagree with length entirely - the same clip run cold and
    // warm, most likely. Fall back to a ratio, which at least cannot claim a
    // longer video is quicker.
    const ratios = measured.map((item) => item.workSeconds / item.mediaSeconds!);
    return {
      shape,
      fixedSeconds: 0,
      perMediaSecond: median(ratios),
      flatSeconds,
      samples: mine.length,
      measuredSamples: measured.length,
    };
  }

  // Startup is whatever the line says a zero-length clip would still cost.
  // Never below zero: a negative fixed cost would make short clips free.
  const fixedSeconds = Math.max(0, lowWork - slope * lowMedia);
  return {
    shape,
    fixedSeconds,
    perMediaSecond: slope,
    flatSeconds,
    samples: mine.length,
    measuredSamples: measured.length,
  };
}

/**
 * What the model alone expects a job of this length to cost, start to finish.
 *
 * Two summaries of the same shape compete here, and the tie-break is which of
 * them describes the jobs the median is drawn from.
 *
 * The rate wins when most of the population was measured, because then both
 * are talking about the same work and the rate is the one that scales. It
 * loses when the measured jobs are a small minority - a rate fitted from one
 * clip is one clip's opinion, and letting it outrank a median of two hundred
 * is how a face blur that reliably takes two and a half minutes came to be
 * advertised as forty-three seconds. That is a real reading from this
 * workspace: 195 finished renders, one of them queued since lengths started
 * being recorded.
 *
 * A majority rather than a count, because a count cannot tell those apart -
 * three measured of three is confident, three of two hundred is an anecdote.
 * It also self-corrects: as the unmeasured history ages out of the window, the
 * measured share climbs and length takes over on its own.
 */
export function expectedSeconds(model: Model, mediaSeconds: number | null): number | null {
  const byLength = mediaSeconds !== null && mediaSeconds > 0 && model.perMediaSecond > 0
    ? model.fixedSeconds + model.perMediaSecond * mediaSeconds
    : null;
  const representative = model.measuredSamples * 2 >= model.samples;
  if (byLength !== null && representative) return byLength;
  if (model.flatSeconds > 0) return model.flatSeconds;
  return byLength;
}

function confidenceOf(model: Model, progress: number | null): Confidence {
  if (progress !== null && progress >= 0.5) return "good";
  if (model.samples >= ENOUGH_FOR_A_LINE) return "good";
  if (model.samples >= 1) return "fair";
  return "rough";
}

/**
 * How much longer one job has to go.
 *
 * Two sources, blended by how far along the job is. Early on, the model built
 * from finished work is all there is. As the job progresses its own pace
 * becomes the better evidence - it is measuring this file, on this machine,
 * right now - so it takes over gradually rather than at a threshold, which is
 * what keeps the number from jumping the moment a bar crosses a percentage.
 *
 * Returns null rather than a guess when nothing has finished yet and the
 * worker reports no progress. A countdown with nothing behind it is worse than
 * no countdown: it will be wrong, and it will have looked authoritative.
 */
export function estimateOne(job: Pending, model: Model): Estimate | null {
  const elapsed = Math.max(0, job.elapsedSeconds ?? 0);
  const progress = typeof job.progress === "number" && Number.isFinite(job.progress)
    ? Math.max(0, Math.min(1, job.progress))
    : null;
  const expected = expectedSeconds(model, job.mediaSeconds);

  if (progress !== null && progress >= 1) {
    return { remainingSeconds: 0, confidence: "good", basis: "Finished." };
  }

  // What this job's own pace says, once there is enough of it to divide by.
  const fromItself = progress !== null && progress >= PROGRESS_FLOOR && elapsed > 0
    ? (elapsed * (1 - progress)) / progress
    : null;
  // What the finished jobs say, less the time already spent on this one.
  const fromModel = expected !== null ? Math.max(0, expected - elapsed) : null;

  if (fromItself === null && fromModel === null) return null;
  if (fromItself === null) {
    return {
      remainingSeconds: fromModel!,
      confidence: confidenceOf(model, progress),
      basis: model.samples
        ? `From ${model.samples} finished ${model.samples === 1 ? "item" : "items"} in this batch.`
        : "From this item's own pace.",
    };
  }
  if (fromModel === null) {
    return {
      remainingSeconds: fromItself,
      confidence: confidenceOf(model, progress),
      basis: "From this item's own pace so far.",
    };
  }

  // The crossover. At the floor the model still carries it; by the halfway
  // mark the job's own pace does. Squared so the handover is gentle at the
  // start, where a progress fraction is least reliable.
  const trust = Math.min(1, ((progress! - PROGRESS_FLOOR) / (0.5 - PROGRESS_FLOOR)) ** 2);
  return {
    remainingSeconds: fromItself * trust + fromModel * (1 - trust),
    confidence: confidenceOf(model, progress),
    basis: `From this item's pace and ${model.samples} finished before it.`,
  };
}

/**
 * How much longer a whole batch has to go.
 *
 * Not the sum: `concurrency` items run at once, so the batch takes about the
 * total work divided by however many workers are turning. Not the maximum
 * either - that is only right when everything fits in one round.
 *
 * The longest single item is a floor on the answer, because no amount of
 * parallelism finishes a batch before its slowest member.
 */
export function estimateBatch(
  jobs: Pending[],
  finished: Finished[],
  concurrency = 1,
): Estimate | null {
  const models = new Map<string, Model>();
  const each = jobs.map((job) => {
    if (!models.has(job.shape)) models.set(job.shape, fit(finished, job.shape));
    return estimateOne(job, models.get(job.shape)!);
  });
  const known = each.filter((item): item is Estimate => item !== null);
  if (!known.length) return null;

  const total = known.reduce((sum, item) => sum + item.remainingSeconds, 0);
  // Unknown items are not free. Charge each one the average of what is known,
  // so a batch does not report a shrinking time while items nobody can
  // estimate are still queued behind it.
  const average = total / known.length;
  const charged = total + (each.length - known.length) * average;

  const slowest = Math.max(...known.map((item) => item.remainingSeconds));
  const shared = charged / Math.max(1, concurrency);
  const worst = known.reduce<Confidence>((lowest, item) => (
    lowest === "rough" || item.confidence === "rough" ? "rough"
      : lowest === "fair" || item.confidence === "fair" ? "fair"
        : "good"
  ), "good");

  return {
    remainingSeconds: Math.max(shared, slowest),
    confidence: worst,
    basis: `${jobs.length} item${jobs.length === 1 ? "" : "s"} left, ${finished.length
      } finished so far.`,
  };
}

/**
 * A duration a person reads at a glance, rounded to its own precision.
 *
 * Rounded coarser the longer it gets, because a nine-minute estimate is not
 * accurate to the second and printing "9m 13s" claims it is. The rounding is
 * upward at the boundaries so a countdown does not sit on "1m" for ninety
 * seconds and then jump to "done".
 */
export function readableSeconds(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  if (value < 10) return "a few seconds";
  if (value < 60) return `${Math.ceil(value / 5) * 5}s`;
  if (value < 600) {
    const minutes = Math.floor(value / 60);
    const rest = Math.round((value % 60) / 15) * 15;
    if (rest === 60) return `${minutes + 1}m`;
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }
  if (value < 5400) return `${Math.round(value / 60)}m`;
  const hours = Math.floor(value / 3600);
  const minutes = Math.round((value % 3600) / 600) * 10;
  if (minutes === 60) return `${hours + 1}h`;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

/**
 * The whole thing said in one line, or nothing when there is nothing to say.
 *
 * "About" on anything but a confident estimate, because the difference between
 * a measurement and a guess should survive into what the reader sees.
 */
export function etaLabel(estimate: Estimate | null): string {
  if (!estimate) return "";
  const time = readableSeconds(estimate.remainingSeconds);
  if (estimate.remainingSeconds < 1) return "Finishing";
  return estimate.confidence === "good" ? `${time} left` : `about ${time} left`;
}
