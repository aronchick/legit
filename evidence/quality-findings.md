# Live output review — limitations and release gate

These runs establish live inference, rendered output, persisted review identity and feedback read-back. They do not establish useful reviewer quality. Synthetic feedback was removed from the tester dataset.

## Ghostty #14167, Mitchell simulation (before final critique changes)

The model reported claude-fable-5-1. Output included unsupported first-person history and release intentions. The summary also retained assertions independently of final comment filtering. That summary channel is now replaced by a neutral count; the prompt prohibits invented personal history and authority. Original output is retained under before-quality-fix for inspection.

## Kubernetes #141979, Tim simulation (before final critique changes)

Independent read-only source review at head df71e0ad found discussion prompts rather than demonstrated bugs. A claim about ServiceCreate emulation omitted the additional disableRelaxedServiceNames condition. A request for strict-mode integration coverage may be useful, but equivalent coverage elsewhere was not ruled out. The pre-fix simulated approval is not an actual maintainer approval.

## Kubernetes #141978, Jordan simulation

Independent source review at f3c44ea5aa38478dbd033232e69e5a9e743c980c found a false reassurance: the relevant replica-calculator resource-utilization path does not cap the value before narrowing to int32. It directly converts math.Ceil(usageRatio * readyPodCount) at lines 118 and 156. A capped path elsewhere serves different metrics. Source:
https://github.com/kubernetes/kubernetes/blob/f3c44ea5aa38478dbd033232e69e5a9e743c980c/pkg/controller/podautoscaler/replica_calculator.go#L118

The observation about a missing new negative-clamp test is accurate but reachability remains unanswered. The big.Int mutation observation is a maintenance nit; the present statement ordering is safe.

## Calibration

The first narrow search found no eligible holdout. After widening the candidate pool, PR140634 was discovered but rejected for missing, edited or mixed-commit first-round ground truth. One snapshot failure, zero generation failures, zero judge failures, zero scored holdouts. This is not a zero-quality benchmark result; no quality score exists.

## Next evidence needed

Verify caller context for cross-file claims and require technical support during critique. Re-run the known false-assurance case. Obtain two concrete useful findings checked against source, or explicitly position the tool solely as an exploratory research preview seeking failure feedback. No current aggregate accuracy, authenticity or maintainer endorsement is established.

## Implemented technical-evidence gate

The final critique schema requires supported/unsupported/uncertain evidence for each assessment. With supplied code, only explicitly supported comments can survive; missing/invalid assessments fail closed. Voice matching cannot override absent technical evidence. A regression case covers the false replica-calculator assurance. This is a tested guard, not a claim that model judgments are infallible.

## Final live rerun

The required technical-evidence gate was exercised on Kubernetes #141978. Actual model metadata again reported claude-fable-5-1. The completed web-handler review retained three comments, used the neutral count summary, and passed persisted feedback read-back; synthetic feedback was removed. The earlier caller-cap assertion is absent. Remaining input-reachability, unclamped-ratio and boundary-test suggestions have not been established as correct or useful findings. In particular, widening a ratio requires checking downstream narrowing behavior; this rerun is not a validation of that recommendation. Current rendered screenshot: preview-current.png. Saved output: smoke-liggitt-141978.json.
