export type ExecutionMode = "dry_run" | "run_once" | "loop";

export interface RiskProfileOption {
  value: string;
  label: string;
  description: string;
  allocation: string;
}

interface StartStrategyConfigInput {
  serverName: string;
  totalAmountQuote: string;
  frequencySec: string;
  executionMode: ExecutionMode;
  riskProfile: string;
  maxPositionSize: string;
  maxOpenExecutors: string;
  maxDrawdown: string;
}

const PROFILE_LABELS: Record<string, string> = {
  guardian: "Safest",
  balanced: "Slightly risky",
  hunter: "More risky",
};

function titleCase(value: string): string {
  return value
    .split(/[_-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function getRiskProfileOptions(
  agentConfig: Record<string, unknown>,
): RiskProfileOption[] {
  const profiles = agentConfig.profiles;
  if (!profiles || typeof profiles !== "object" || Array.isArray(profiles)) return [];

  return Object.entries(profiles).flatMap(([value, rawProfile]) => {
    if (!rawProfile || typeof rawProfile !== "object" || Array.isArray(rawProfile)) return [];

    const profile = rawProfile as Record<string, unknown>;
    const core = Number(profile.core_pct);
    const satellite = Number(profile.satellite_pct);
    const runner = Number(profile.runner_pct);
    if (![core, satellite, runner].every(Number.isFinite)) return [];

    return [{
      value,
      label: PROFILE_LABELS[value] ?? titleCase(value),
      description: titleCase(value),
      allocation: `${core}% core · ${satellite}% satellite · ${runner}% runner`,
    }];
  });
}

export function buildStartStrategyConfig({
  serverName,
  totalAmountQuote,
  frequencySec,
  executionMode,
  riskProfile,
  maxPositionSize,
  maxOpenExecutors,
  maxDrawdown,
}: StartStrategyConfigInput): Record<string, unknown> {
  const config: Record<string, unknown> = {
    server_name: serverName,
    total_amount_quote: Number(totalAmountQuote) || 100,
    frequency_sec: Number(frequencySec) || 60,
    execution_mode: executionMode,
    risk_limits: {
      max_position_size_quote: Number(maxPositionSize) || 500,
      max_open_executors: Number(maxOpenExecutors) || 5,
      max_drawdown_pct: Number(maxDrawdown),
    },
  };

  if (riskProfile) config.risk_profile = riskProfile;
  return config;
}
