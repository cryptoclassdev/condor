import assert from "node:assert/strict";
import test from "node:test";

import {
  buildStartStrategyConfig,
  getRiskProfileOptions,
} from "../src/components/agent/startSessionConfig.ts";

const agentConfig = {
  total_amount_quote: 800,
  frequency_sec: 60,
  risk_profile: "balanced",
  profiles: {
    guardian: {
      core_pct: 80,
      satellite_pct: 10,
      runner_pct: 10,
    },
    balanced: {
      core_pct: 60,
      satellite_pct: 20,
      runner_pct: 20,
    },
    hunter: {
      core_pct: 40,
      satellite_pct: 40,
      runner_pct: 20,
    },
  },
};

test("exposes the three Meteora profiles with their allocation split", () => {
  assert.deepEqual(getRiskProfileOptions(agentConfig), [
    {
      value: "guardian",
      label: "Safest",
      description: "Guardian",
      allocation: "80% core · 10% satellite · 10% runner",
    },
    {
      value: "balanced",
      label: "Slightly risky",
      description: "Balanced",
      allocation: "60% core · 20% satellite · 20% runner",
    },
    {
      value: "hunter",
      label: "More risky",
      description: "Hunter",
      allocation: "40% core · 40% satellite · 20% runner",
    },
  ]);
});

test("includes the selected profile in the session start config", () => {
  const config = buildStartStrategyConfig({
    serverName: "main",
    totalAmountQuote: "800",
    frequencySec: "60",
    executionMode: "loop",
    riskProfile: "hunter",
    maxPositionSize: "500",
    maxOpenExecutors: "5",
    maxDrawdown: "-1",
  });

  assert.equal(config.risk_profile, "hunter");
  assert.deepEqual(config.risk_limits, {
    max_position_size_quote: 500,
    max_open_executors: 5,
    max_drawdown_pct: -1,
  });
});

test("does not invent a risk profile for strategies without one", () => {
  const config = buildStartStrategyConfig({
    serverName: "",
    totalAmountQuote: "100",
    frequencySec: "60",
    executionMode: "run_once",
    riskProfile: "",
    maxPositionSize: "500",
    maxOpenExecutors: "5",
    maxDrawdown: "-1",
  });

  assert.equal("risk_profile" in config, false);
});
