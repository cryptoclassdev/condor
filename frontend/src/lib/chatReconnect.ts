export type ChatReconnectAction = "retry" | "logout";

/**
 * Decide whether a closed chat socket may reconnect after an authenticated
 * REST probe. Browsers surface a WebSocket handshake rejection only as a
 * generic close, so the probe is the reliable way to distinguish an expired
 * session from a temporarily unavailable server.
 */
export function classifyChatReconnect(
  authStatus: number | null,
): ChatReconnectAction {
  return authStatus === 401 || authStatus === 403 ? "logout" : "retry";
}
