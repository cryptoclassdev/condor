import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Clock,
  MessageSquareText,
  Pause,
  Play,
  Server,
  Square,
  X,
  Zap,
} from "lucide-react";
import { useState } from "react";

import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api } from "@/lib/api";

import {
  buildStartStrategyConfig,
  getRiskProfileOptions,
  type ExecutionMode,
} from "./startSessionConfig";

// ── Start Session Dialog ──

export function StartSessionDialog({
  open,
  onClose,
  slug,
  sslug,
  agentConfig,
  defaultContext,
}: {
  open: boolean;
  onClose: () => void;
  slug: string;
  sslug: string;
  agentConfig: Record<string, unknown>;
  defaultContext: string;
}) {
  const queryClient = useQueryClient();
  useEscapeKey(open, onClose);
  const riskDefaults = (agentConfig.risk_limits || {}) as Record<string, unknown>;
  const riskProfileOptions = getRiskProfileOptions(agentConfig);
  const configuredRiskProfile = String(agentConfig.risk_profile ?? "");

  const [executionMode, setExecutionMode] = useState<ExecutionMode>("loop");
  const [riskProfile, setRiskProfile] = useState(
    riskProfileOptions.some((profile) => profile.value === configuredRiskProfile)
      ? configuredRiskProfile
      : riskProfileOptions[0]?.value ?? "",
  );
  const [context, setContext] = useState(defaultContext);
  const [serverName, setServerName] = useState((agentConfig.server_name as string) || "");
  const [totalAmountQuote, setTotalAmountQuote] = useState(String(agentConfig.total_amount_quote ?? 100));
  const [frequencySec, setFrequencySec] = useState(String(agentConfig.frequency_sec ?? 60));
  const [maxPositionSize, setMaxPositionSize] = useState(String(riskDefaults.max_position_size_quote ?? 500));
  const [maxOpenExecutors, setMaxOpenExecutors] = useState(String(riskDefaults.max_open_executors ?? 5));
  const [maxDrawdown, setMaxDrawdown] = useState(String(riskDefaults.max_drawdown_pct ?? -1));

  const { data: servers, isLoading: serversLoading, isError: serversError, refetch: refetchServers } = useQuery({
    queryKey: ["servers"],
    queryFn: () => api.getServers(),
    enabled: open,
  });

  const startMut = useMutation({
    mutationFn: () => {
      const config = buildStartStrategyConfig({
        serverName,
        totalAmountQuote,
        frequencySec,
        executionMode,
        riskProfile,
        maxPositionSize,
        maxOpenExecutors,
        maxDrawdown,
      });
      return api.startStrategy(slug, sslug, config, context);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
      onClose();
    },
  });

  if (!open) return null;

  const inputClass =
    "w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] transition-colors focus-visible:border-[var(--color-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]/40";
  const labelClass = "mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]";
  const focusRing = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg)]";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close start session dialog"
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="start-session-title"
        onSubmit={(event) => {
          event.preventDefault();
          startMut.mutate();
        }}
        className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="start-session-title" className="text-lg font-semibold text-[var(--color-text)]">Start New Session</h2>
          <button type="button" onClick={onClose} className={`flex h-10 w-10 items-center justify-center rounded-lg text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] ${focusRing}`} title="Close" aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="space-y-5">
          {/* Execution Mode */}
          <fieldset>
            <legend className={labelClass}>Execution Mode</legend>
            <div className="flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
              {([
                { value: "dry_run", label: "Dry Run", desc: "Simulate" },
                { value: "run_once", label: "Run Once", desc: "Single tick" },
                { value: "loop", label: "Loop", desc: "Continuous" },
              ] as const).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setExecutionMode(opt.value)}
                  aria-pressed={executionMode === opt.value}
                  className={`min-h-10 flex-1 rounded-md px-3 py-2 text-center text-xs font-medium transition-colors ${focusRing} ${
                    executionMode === opt.value
                      ? opt.value === "dry_run"
                        ? "bg-blue-500/15 text-blue-400"
                        : opt.value === "run_once"
                          ? "bg-amber-500/15 text-amber-400"
                          : "bg-emerald-500/15 text-emerald-400"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  <div>{opt.label}</div>
                  <div className="mt-0.5 text-[10px] opacity-60">{opt.desc}</div>
                </button>
              ))}
            </div>
          </fieldset>

          {riskProfileOptions.length > 0 && (
            <fieldset>
              <legend className={labelClass}>
                <Zap className="h-3.5 w-3.5" aria-hidden="true" />
                Risk Profile
              </legend>
              <p className="mb-3 text-xs text-[var(--color-text-muted)]">
                Choose how the available risk capital is split across the three LP sleeves.
              </p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {riskProfileOptions.map((profile) => (
                  <label key={profile.value} className="relative cursor-pointer">
                    <input
                      type="radio"
                      name="risk-profile"
                      value={profile.value}
                      checked={riskProfile === profile.value}
                      onChange={() => setRiskProfile(profile.value)}
                      className="peer sr-only"
                    />
                    <span className="flex min-h-24 flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 transition-colors hover:border-[var(--color-primary)]/60 peer-checked:border-[var(--color-primary)] peer-checked:bg-[var(--color-primary)]/10 peer-focus-visible:ring-2 peer-focus-visible:ring-[var(--color-primary)] peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-[var(--color-bg)]">
                      <span className="text-sm font-semibold text-[var(--color-text)]">{profile.label}</span>
                      <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">{profile.description}</span>
                      <span className="mt-auto pt-2 text-xs leading-5 text-[var(--color-text-muted)]">{profile.allocation}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {/* Trading Context */}
          <div>
            <label htmlFor="trading-context" className={labelClass}>
              <MessageSquareText className="h-3.5 w-3.5" aria-hidden="true" />
              Trading Context
            </label>
            <p className="mb-2 text-xs text-[var(--color-text-muted)]">
              Describe what this session should focus on. This guides the agent's trading decisions.
            </p>
            <textarea
              id="trading-context"
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="e.g. Focus on SOL meme coins, ride momentum for 5-10% gains, tight stops at 3%..."
              rows={3}
              className={`${inputClass} resize-none`}
            />
          </div>

          {/* Server row */}
          <div>
            <label htmlFor="server-name" className={labelClass}>
              <Server className="h-3.5 w-3.5" aria-hidden="true" />
              Server
            </label>
            <select
              id="server-name"
              value={serverName}
              onChange={(e) => setServerName(e.target.value)}
              className={inputClass}
              disabled={serversLoading}
            >
              <option value="">{serversLoading ? "Loading servers…" : "Auto (current default)"}</option>
              {servers?.map((s) => (
                <option key={s.name} value={s.name} disabled={!s.online}>
                  {s.name} {s.online ? "" : "(offline)"}
                </option>
              ))}
            </select>
            {serversError && (
              <p className="mt-2 text-xs text-red-400">
                Could not load servers. You can use the current default or{" "}
                <button type="button" onClick={() => refetchServers()} className={`font-medium underline underline-offset-2 ${focusRing}`}>
                  try again
                </button>.
              </p>
            )}
          </div>

          {/* Budget + Frequency row */}
          <div className={`grid gap-4 ${executionMode === "loop" ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
            <div>
              <label htmlFor="total-amount-quote" className={labelClass}>
                Total Amount Quote
              </label>
              <input
                id="total-amount-quote"
                type="text"
                inputMode="decimal"
                value={totalAmountQuote}
                onChange={(e) => setTotalAmountQuote(e.target.value)}
                className={inputClass}
              />
            </div>
            {executionMode === "loop" && (
              <div>
                <label htmlFor="frequency-sec" className={labelClass}>
                  <Clock className="h-3.5 w-3.5" aria-hidden="true" />
                  Frequency (sec)
                </label>
                <input
                  id="frequency-sec"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  value={frequencySec}
                  onChange={(e) => setFrequencySec(e.target.value)}
                  className={inputClass}
                />
              </div>
            )}
          </div>

          {/* Risk Limits */}
          <fieldset>
            <legend className={`${labelClass} mb-3`}>
              <Zap className="h-3.5 w-3.5" aria-hidden="true" />
              Risk Limits
            </legend>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <label htmlFor="max-position-size" className="mb-1 block text-[10px] text-[var(--color-text-muted)]">Max Position ($)</label>
                <input
                  id="max-position-size"
                  type="text"
                  inputMode="decimal"
                  value={maxPositionSize}
                  onChange={(e) => setMaxPositionSize(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label htmlFor="max-open-executors" className="mb-1 block text-[10px] text-[var(--color-text-muted)]">Max Executors</label>
                <input
                  id="max-open-executors"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  value={maxOpenExecutors}
                  onChange={(e) => setMaxOpenExecutors(e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label htmlFor="max-drawdown" className="mb-1 block text-[10px] text-[var(--color-text-muted)]">Max Drawdown %</label>
                <input
                  id="max-drawdown"
                  type="text"
                  inputMode="decimal"
                  value={maxDrawdown}
                  onChange={(e) => setMaxDrawdown(e.target.value)}
                  className={inputClass}
                />
              </div>
            </div>
          </fieldset>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className={`min-h-10 rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] ${focusRing}`}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={startMut.isPending}
            aria-busy={startMut.isPending}
            className={`flex min-h-10 items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors disabled:cursor-wait disabled:opacity-40 ${focusRing} ${
              executionMode === "dry_run" ? "bg-blue-600 hover:bg-blue-500" : executionMode === "run_once" ? "bg-amber-600 hover:bg-amber-500" : "bg-emerald-600 hover:bg-emerald-500"
            }`}
          >
            <Play className="h-3.5 w-3.5" aria-hidden="true" />
            {startMut.isPending
              ? "Starting..."
              : executionMode === "dry_run"
                ? "Run Dry Test"
                : executionMode === "run_once"
                  ? "Execute Once"
                  : "Start Session"}
          </button>
        </div>
        {startMut.isError && (
          <p role="alert" className="mt-3 text-xs text-red-400">
            Could not start the session. {startMut.error.message}
          </p>
        )}
      </form>
    </div>
  );
}

// ── Agent Controls ──

export function AgentControls({ slug, sslug, status, defaultContext, agentConfig }: { slug: string; sslug: string; status: string; defaultContext: string; agentConfig: Record<string, unknown> }) {
  const queryClient = useQueryClient();
  const [showStartDialog, setShowStartDialog] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const stopMut = useMutation({
    mutationFn: () => api.stopStrategy(slug, sslug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
      setConfirmStop(false);
    },
  });
  const pauseMut = useMutation({
    mutationFn: () => api.pauseStrategy(slug, sslug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] }),
  });
  const resumeMut = useMutation({
    mutationFn: () => api.resumeStrategy(slug, sslug),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] }),
  });

  const loading = stopMut.isPending || pauseMut.isPending || resumeMut.isPending;
  const controlError = stopMut.error || pauseMut.error || resumeMut.error;

  const stopControls = confirmStop ? (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => stopMut.mutate()}
        disabled={stopMut.isPending}
        className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-red-500 disabled:opacity-40"
      >
        {stopMut.isPending ? "Stopping..." : "Confirm"}
      </button>
      <button
        onClick={() => setConfirmStop(false)}
        disabled={stopMut.isPending}
        className="rounded-lg px-3 py-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)] disabled:opacity-40"
      >
        Cancel
      </button>
    </div>
  ) : (
    <button
      onClick={() => setConfirmStop(true)}
      disabled={loading}
      className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:opacity-40"
    >
      <Square className="h-3.5 w-3.5" /> Stop
    </button>
  );

  return (
    <>
      <div className="flex items-center gap-2">
        {status === "idle" || status === "stopped" ? (
          <button
            onClick={() => setShowStartDialog(true)}
            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-emerald-500"
          >
            <Play className="h-3.5 w-3.5" /> Start
          </button>
        ) : status === "running" ? (
          <>
            <button
              onClick={() => pauseMut.mutate()}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-semibold text-amber-400 transition-all hover:bg-amber-500/20 disabled:opacity-40"
            >
              <Pause className="h-3.5 w-3.5" /> Pause
            </button>
            {stopControls}
          </>
        ) : status === "paused" ? (
          <>
            <button
              onClick={() => resumeMut.mutate()}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-all hover:bg-emerald-500 disabled:opacity-40"
            >
              <Play className="h-3.5 w-3.5" /> Resume
            </button>
            {stopControls}
          </>
        ) : null}
        {controlError && (
          <p className="text-xs text-red-400">{controlError.message}</p>
        )}
      </div>

      <StartSessionDialog
        open={showStartDialog}
        onClose={() => setShowStartDialog(false)}
        slug={slug}
        sslug={sslug}
        agentConfig={agentConfig}
        defaultContext={defaultContext}
      />
    </>
  );
}
