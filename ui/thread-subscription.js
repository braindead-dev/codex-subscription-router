const CODEX_MUX_THREAD_API = "http://127.0.0.1:__CODEX_MUX_CONTROL_PORT__/v1";
const CODEX_MUX_THREAD_TOKEN = "__CODEX_MUX_CONTROL_TOKEN__";

function CodexMuxThreadSubscription() {
  const route = $n(sr);
  const threadId =
    route.value.routeKind === "local-thread" ? route.value.conversationId : null;
  const [account, setAccount] = TE.useState(null);

  TE.useEffect(() => {
    let active = true;
    if (!threadId) {
      setAccount(null);
      return () => {
        active = false;
      };
    }

    const refresh = async () => {
      try {
        const response = await fetch(
          `${CODEX_MUX_THREAD_API}/thread-account?threadId=${encodeURIComponent(threadId)}`,
          { headers: { "X-Codex-Mux-Token": CODEX_MUX_THREAD_TOKEN } },
        );
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        const body = await response.json();
        if (active) setAccount(body.account || null);
      } catch {
        if (active) setAccount(null);
      }
    };

    refresh();
    const unsubscribe = globalThis.codexMuxSubscribe?.((payload) => {
      if (
        payload.type === "account-updated" ||
        (["thread-moved", "thread-failed-over"].includes(payload.type) &&
          payload.data?.threadId === threadId)
      ) {
        refresh();
      }
    });
    const warmupTimer = setTimeout(refresh, 2_000);
    const timer = setInterval(refresh, 30_000);
    return () => {
      active = false;
      clearTimeout(warmupTimer);
      clearInterval(timer);
      unsubscribe?.();
    };
  }, [threadId]);

  if (!account) return null;
  const weekly = codexMuxThreadWeeklyWindow(account.rateLimits);
  const remaining = weekly == null ? null : Math.max(0, 100 - weekly.usedPercent);
  const credits = globalThis.codexMuxCredits?.(account.rateLimits) ?? null;
  const depleted = remaining === 0 && credits == null;
  const AccountAvatar = globalThis.CodexMuxAccountAvatar;
  return (0, zE.jsx)(K.Section, {
    sectionKey: "codex-mux-subscription",
    title: "Subscription",
    children: (0, zE.jsxs)("div", {
      className: "flex min-h-9 items-center justify-between gap-3 py-1 text-sm",
      children: [
        (0, zE.jsxs)("div", {
          className: "flex min-w-0 items-center gap-2",
          children: [
            AccountAvatar
              ? (0, zE.jsx)(AccountAvatar, {
                  imageUrl: account.profileImageUrl,
                  label: account.label,
                  className: "size-5 shrink-0",
                })
              : null,
            (0, zE.jsx)("span", {
              className: "truncate text-token-text-primary",
              children: account.planLabel
                ? `${account.label} · ${account.planLabel}`
                : account.label,
            }),
          ],
        }),
        (0, zE.jsx)("span", {
          className: "shrink-0 tabular-nums text-token-description-foreground",
          children:
            remaining == null
              ? "Usage unavailable"
              : depleted
                ? "Depleted"
                : remaining === 0
                  ? credits
                  : `${Math.round(remaining)}% remaining`,
        }),
      ],
    }),
  });
}

function codexMuxThreadWeeklyWindow(rateLimits) {
  const windows = [rateLimits?.primary, rateLimits?.secondary].filter(Boolean);
  windows.sort(
    (left, right) =>
      (left.windowDurationMins || 0) - (right.windowDurationMins || 0),
  );
  return windows.at(-1) || null;
}
