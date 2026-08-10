export type EmbedKind = "team" | "workflow";

export function appOrigin(): string {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return (
    process.env.NEXT_PUBLIC_APP_URL?.replace(/\/$/, "") ||
    "http://localhost:3000"
  );
}

export function buildEmbedPaths(
  tenantSlug: string,
  kind: EmbedKind,
  resourceSlug: string,
): { chatPath: string; embedPath: string } {
  if (kind === "workflow") {
    return {
      chatPath: `/t/${tenantSlug}/workflows/${resourceSlug}`,
      embedPath: `/embed/${tenantSlug}/workflow/${resourceSlug}`,
    };
  }
  return {
    chatPath: `/t/${tenantSlug}/teams/${resourceSlug}`,
    embedPath: `/embed/${tenantSlug}/team/${resourceSlug}`,
  };
}

export function buildEmbedSnippets(
  tenantSlug: string,
  kind: EmbedKind,
  resourceSlug: string,
  origin = appOrigin(),
  inboundDomain?: string | null,
): {
  chatUrl: string;
  embedUrl: string;
  iframe: string;
  script: string;
  emailAddress: string | null;
} {
  const { chatPath, embedPath } = buildEmbedPaths(
    tenantSlug,
    kind,
    resourceSlug,
  );
  const chatUrl = `${origin}${chatPath}`;
  const embedUrl = `${origin}${embedPath}`;
  const iframe = `<iframe\n  src="${embedUrl}"\n  title="Atlas chat"\n  style="width:100%;height:640px;border:0;border-radius:12px;"\n  allow="clipboard-write"\n></iframe>`;
  const script = `<div id="atlas-chat"></div>\n<script>\n(function () {\n  var iframe = document.createElement("iframe");\n  iframe.src = ${JSON.stringify(embedUrl)};\n  iframe.title = "Atlas chat";\n  iframe.style.cssText = "width:100%;height:640px;border:0;border-radius:12px;";\n  iframe.allow = "clipboard-write";\n  var host = document.getElementById("atlas-chat");\n  if (host) host.appendChild(iframe);\n})();\n</script>`;
  const domain = (inboundDomain || "").trim().toLowerCase();
  const emailAddress = domain
    ? `${kind === "workflow" ? "workflow" : "team"}-${tenantSlug}.${resourceSlug}@${domain}`
    : null;
  return { chatUrl, embedUrl, iframe, script, emailAddress };
}
