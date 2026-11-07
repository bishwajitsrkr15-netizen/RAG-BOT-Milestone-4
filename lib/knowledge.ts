export const SOURCES = [
  {
    id: "hdfc-mutual-fund-amc",
    url: "https://groww.in/mutual-funds/amc/hdfc-mutual-funds",
  },
  {
    id: "hdfc-large-cap",
    url: "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
  },
  {
    id: "hdfc-flexi-cap",
    url: "https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth",
  },
  {
    id: "hdfc-elss-tax-saver",
    url: "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
  },
  {
    id: "hdfc-mid-cap",
    url: "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth",
  },
] as const;

export const SCHEME_ALIASES: Record<string, string[]> = {
  "hdfc-mutual-fund-amc": [
    "HDFC Mutual Fund",
    "HDFC AMC",
    "HDFC Asset Management Company",
  ],
  "hdfc-large-cap": ["HDFC Large Cap", "HDFC Large Cap Fund", "Large Cap"],
  "hdfc-flexi-cap": ["HDFC Flexi Cap", "HDFC Equity Fund", "Flexi Cap"],
  "hdfc-elss-tax-saver": [
    "HDFC ELSS",
    "HDFC Tax Saver",
    "ELSS",
    "Tax Saver",
  ],
  "hdfc-mid-cap": ["HDFC Mid Cap", "HDFC Midcap", "Mid Cap"],
};

export const TOP_K = 4;
export const MAX_COSINE_DISTANCE = 0.55;

export function identifyScheme(question: string): string | undefined {
  const lowered = question.toLocaleLowerCase();
  const matches = Object.entries(SCHEME_ALIASES)
    .filter(([, aliases]) =>
      aliases.some((alias) => lowered.includes(alias.toLocaleLowerCase())),
    )
    .map(([sourceId, aliases]) => ({
      sourceId,
      length: Math.max(...aliases.map((alias) => alias.length)),
    }))
    .sort((left, right) => right.length - left.length);

  if (matches.length > 1 && matches[0].length === matches[1].length) {
    return undefined;
  }
  return matches[0]?.sourceId;
}

export function approvedCitation(
  sourceId?: string,
): (typeof SOURCES)[number]["url"] {
  return (
    SOURCES.find((source) => source.id === sourceId)?.url ?? SOURCES[0].url
  );
}
