"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Brand domain per toolkit key, used to fetch favicons. Toolkits without a
 * public brand (utilities, generic scrapers) fall back to a letter tile.
 */
const TOOLKIT_DOMAINS: Record<string, string> = {
  duckduckgo: "duckduckgo.com",
  web_search: "duckduckgo.com",
  wikipedia: "wikipedia.org",
  arxiv: "arxiv.org",
  hackernews: "news.ycombinator.com",
  pubmed: "pubmed.ncbi.nlm.nih.gov",
  yfinance: "finance.yahoo.com",
  youtube: "youtube.com",
  baidusearch: "baidu.com",
  openai_media: "openai.com",
  dalle: "openai.com",
  email: "gmail.com",
  tavily: "tavily.com",
  exa: "exa.ai",
  serpapi: "serpapi.com",
  serper: "serper.dev",
  searchapi: "searchapi.io",
  linkup: "linkup.so",
  bravesearch: "brave.com",
  youcom: "you.com",
  perplexity: "perplexity.ai",
  parallel: "parallel.ai",
  valyu: "valyu.network",
  jina: "jina.ai",
  firecrawl: "firecrawl.dev",
  crawl4ai: "crawl4ai.com",
  apify: "apify.com",
  agentql: "agentql.com",
  browserbase: "browserbase.com",
  brightdata: "brightdata.com",
  oxylabs: "oxylabs.io",
  scrapegraph: "scrapegraphai.com",
  spider: "spider.cloud",
  github: "github.com",
  gitlab: "gitlab.com",
  bitbucket: "bitbucket.org",
  jira: "atlassian.com",
  confluence: "atlassian.com",
  linear: "linear.app",
  notion: "notion.so",
  trello: "trello.com",
  clickup: "clickup.com",
  todoist: "todoist.com",
  calcom: "cal.com",
  slack: "slack.com",
  discord: "discord.com",
  telegram: "telegram.org",
  resend: "resend.com",
  plivo: "plivo.com",
  twilio: "twilio.com",
  webex: "webex.com",
  whatsapp: "whatsapp.com",
  zoom: "zoom.us",
  aws_ses: "aws.amazon.com",
  openweather: "openweathermap.org",
  google_maps: "maps.google.com",
  financial_datasets: "financialdatasets.ai",
  openbb: "openbb.co",
  salesforce: "salesforce.com",
  shopify: "shopify.com",
  zendesk: "zendesk.com",
  redmine: "redmine.org",
  reddit: "reddit.com",
  spotify: "spotify.com",
  unsplash: "unsplash.com",
  giphy: "giphy.com",
  x: "x.com",
  eleven_labs: "elevenlabs.io",
  replicate: "replicate.com",
  fal: "fal.ai",
  cartesia: "cartesia.ai",
  lumalab: "lumalabs.ai",
  twelvelabs: "twelvelabs.io",
  brandfetch: "brandfetch.com",
  gmail: "mail.google.com",
  google_drive: "drive.google.com",
  googlecalendar: "calendar.google.com",
  googlesheets: "sheets.google.com",
  nano_banana: "gemini.google.com",
};

const TILE_TONES = [
  "bg-teal/15 text-teal",
  "bg-info/15 text-info",
  "bg-amber/15 text-amber",
  "bg-rose/15 text-rose",
];

function tileTone(key: string): string {
  let hash = 0;
  for (const char of key) {
    hash = (hash * 31 + char.charCodeAt(0)) % 997;
  }
  return TILE_TONES[hash % TILE_TONES.length];
}

export function ToolkitLogo({
  toolkitKey,
  label,
  size = 28,
  className,
}: {
  toolkitKey: string;
  label: string;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const domain = TOOLKIT_DOMAINS[toolkitKey];

  if (!domain || failed) {
    return (
      <span
        aria-hidden
        style={{ width: size, height: size }}
        className={cn(
          "flex shrink-0 items-center justify-center rounded-lg text-sm font-semibold",
          tileTone(toolkitKey),
          className,
        )}
      >
        {label.charAt(0).toUpperCase()}
      </span>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- favicons come from many external hosts; next/image remote patterns are impractical here
    <img
      src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`}
      alt=""
      aria-hidden
      width={size}
      height={size}
      loading="lazy"
      onError={() => setFailed(true)}
      className={cn(
        "shrink-0 rounded-lg bg-white object-contain p-0.5 ring-1 ring-line/60",
        className,
      )}
    />
  );
}
