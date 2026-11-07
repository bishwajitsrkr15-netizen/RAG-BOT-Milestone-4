export type GuardIntent =
  | "factual"
  | "advice"
  | "performance"
  | "personal_data";

const SENSITIVE_PATTERNS = [
  /\b[A-Z]{5}[0-9]{4}[A-Z]\b/i,
  /(?<!\d)(?:\d[\s-]?){11}\d(?!\d)/,
  /\b\d{4,8}\s*(?:is\s*)?(?:otp|one[- ]time password)\b/i,
  /\b(?:otp|one[- ]time password)\s*(?:is|:|=)?\s*\d{4,8}\b/i,
  /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i,
  /(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)/,
  /\b(?:account|a\/c)\s*(?:number|no\.?|#)?\s*[:=-]?\s*\d{6,20}\b/i,
];

const PERFORMANCE_TERMS = [
  "return",
  "returns",
  "cagr",
  "annualized",
  "annualised",
  "outperform",
  "performance comparison",
  "compare performance",
  "best performing",
];

const ADVICE_PATTERNS = [
  /\bshould\s+i\b/i,
  /\b(?:buy|sell|hold)\s+(?:this|the|a|an|hdfc)\b/i,
  /\b(?:recommend|recommendation|advise|advice)\b/i,
  /\b(?:which|what)\s+(?:fund|scheme)\s+(?:should|must)\s+i\b/i,
  /\b(?:best|suitable)\s+(?:fund|scheme|portfolio)\s+for\s+me\b/i,
  /\bmy\s+portfolio\b/i,
];

export function inspectQuestion(question: string): GuardIntent {
  if (SENSITIVE_PATTERNS.some((pattern) => pattern.test(question))) {
    return "personal_data";
  }
  const lowered = question.toLocaleLowerCase();
  if (PERFORMANCE_TERMS.some((term) => lowered.includes(term))) {
    return "performance";
  }
  if (ADVICE_PATTERNS.some((pattern) => pattern.test(question))) {
    return "advice";
  }
  return "factual";
}
