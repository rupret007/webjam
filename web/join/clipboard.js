/** Copy join URLs in the browser with clipboard API and a textarea fallback. */

export const COPIED_NOTICE = "Copied! Send it to your bandmates";
export const CLIPBOARD_DENIED_NOTICE = "Select the link below and copy it yourself.";

/**
 * @param {string} text
 * @param {{ clipboard?: { writeText?: (value: string) => Promise<void> }, document?: Document }} [env]
 * @returns {Promise<{ ok: boolean, usedFallback: boolean, notice: string }>}
 */
export async function copyInviteText(text, env = globalThis) {
  const value = String(text || "");
  if (!value) {
    return { ok: false, usedFallback: true, notice: CLIPBOARD_DENIED_NOTICE };
  }
  const clipboard = env.clipboard;
  if (clipboard && typeof clipboard.writeText === "function") {
    try {
      await clipboard.writeText(value);
      return { ok: true, usedFallback: false, notice: COPIED_NOTICE };
    } catch {
      /* fall through to execCommand */
    }
  }
  const doc = env.document;
  if (!doc || typeof doc.createElement !== "function") {
    return { ok: false, usedFallback: true, notice: CLIPBOARD_DENIED_NOTICE };
  }
  const field = doc.createElement("textarea");
  field.value = value;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.left = "-9999px";
  doc.body.appendChild(field);
  field.select();
  let ok = false;
  try {
    ok = doc.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    field.remove();
  }
  return {
    ok,
    usedFallback: !ok,
    notice: ok ? COPIED_NOTICE : CLIPBOARD_DENIED_NOTICE,
  };
}
