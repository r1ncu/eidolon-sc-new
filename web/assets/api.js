/* 后端接口：一次 fetch 拿全部状态，回合走 SSE。 */

export async function getJSON(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

export async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

/** 一个回合：SSE，逐段吐 delta，最后一条 done / rejected / failed。 */
export async function streamTurn({ characterId, conversationId, text, photos, ownKeys, onDelta, onEvent }) {
  const params = new URLSearchParams();
  if (characterId) params.set("c", characterId);
  if (conversationId) params.set("conv", conversationId);
  const r = await fetch(`/api/turns?${params}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, photos: photos || [], own_keys: ownKeys || null }),
  });
  if (!r.ok || !r.body) throw new Error(String(r.status));
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        let payload;
        try {
          payload = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        if (payload.type === "delta") onDelta?.(payload);
        else onEvent?.(payload);
      }
    }
  }
}
