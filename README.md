# Setup Canali (Canonico) — Dispatcharr Plugin

A Dispatcharr plugin that builds your channel lineup from a **canonical channel
list** instead of guessing channels from raw provider streams. You define the
channels you want (grouped, in order); the plugin attaches every matching stream
from all your active sources as failover, sorted by quality.

It replaces the fragile "derive channels from stream names" approach with a
predictable, declarative one: **you own the list, the plugin fills it in.**

---

## Background — why I built this

I built this plugin with the help of **Claude (Anthropic)** to solve a very
concrete, real-world problem with my own multi-provider IPTV setup.

Providers almost never expose a channel once. The same channel shows up many
times, split across **different qualities** (`RAI 1 SD`, `RAI 1 HD`,
`RAI 1 FULL HD`, `RAI 1 4K`) and often under **slightly different names** from
each account. On top of that, if you subscribe to several bouquets of the **same
IPTV provider**, you get the **same channels duplicated** across those bouquets —
which is actually useful, because each duplicate is a potential **backup feed**.

Out of the box this results in a messy lineup: dozens of near-identical entries,
no clear "best" version, and no automatic fallback when one feed dies or the
account hits its connection limit.

This plugin **reunifies all of that into a single channel**. Every stream that
refers to the same channel — regardless of quality label, naming quirk, account
or bouquet — is collapsed under **one canonical channel**, ordered from best to
worst quality (and with Xtream sources before plain M3U). Because Dispatcharr
does per-channel failover across the attached streams, this ordering gives you
**automatic failover for free**: it always starts from the best available
source, and if that one is down or saturated it silently walks to the next,
including the duplicate feeds coming from your other bouquets of the same
provider.

In short: **many messy, repeated, multi-quality streams → one clean channel with
automatic, quality-ordered failover.**

---

## Why this plugin

If you aggregate several IPTV providers, the usual pain is that the same channel
("Rai 1") appears under dozens of slightly different stream names
(`[S+] RAI 1 FULL HD`, `RAI 1 HD`, `Rai 1 4K HEVC H265`, …) across accounts.
Deriving channels from those names produces duplicates, inconsistent groups, and
broken failover.

This plugin inverts the process:

1. You provide a **canonical list**: `{group: [channel names]}`.
2. For every stream from every active source, the plugin finds the canonical
   channel whose name is contained in the stream name and attaches it.
3. Streams that match nothing go to an **"Other Channels"** group.
4. Inside each channel, streams are ordered by **quality** (4K → FHD → HD → SD),
   with **Xtream (XC)** accounts before **plain M3U (STD)** ones.

Everything is driven from the plugin **Settings** in the Dispatcharr Web UI — no
SSH, no scripts, no cron required.

---

## Features

- **Canonical channel list** — you decide which channels exist, their groups and
  their order.
- **Substring matching, longest-wins** — `SKY SPORT 24` is matched before
  `SKY SPORT`, so the most specific channel always wins and every stream is
  assigned exactly once.
- **Aliases** — normalise inconsistent naming before matching
  (e.g. `SKY TG 24` → `SKY TG24`).
- **Exclusions** — drop specific streams by exact name (e.g. a dead SD feed).
- **Quality ordering** — 4K/UHD/HEVC → Full HD → HD → SD, XC before STD, so
  failover always tries the best source first.
- **Logo assignment** — reuses existing logos from the Logo Manager (by stream
  logo URL, then by channel name).
- **EPG tvg_id inheritance** — each channel inherits the `tvg_id` from its best
  stream, so Dispatcharr's EPG auto-match works out of the box.
- **User assignment** — assign the resulting profile to all users, or only to a
  named subset.
- **Dry-run** — preview the whole result (counts + groups) without touching
  anything.

---

## Installation

1. Download `setup_canali.zip` from the
   [Releases](../../releases) page.
2. In Dispatcharr open **Plugins**, use the import/upload control and select the
   ZIP.
3. **Enable** the plugin with its toggle.
4. Open the plugin **Settings** and fill in the fields (see below).

> The plugin runs inside the Dispatcharr backend and uses the internal Django
> ORM. It needs no API credentials.

---

## Settings

| Field | Type | Description |
|-------|------|-------------|
| **Nome profilo canali** | text | The channel profile to (re)build and assign. Default `IPTV`. |
| **Gruppo per stream non riconosciuti** | text | Group name for unmatched streams. Default `🔵 Altri Canali`. |
| **Utenti a cui assegnare il profilo** | text | Comma-separated usernames. Empty = all users. Users not listed have the profile removed. |
| **Lista canonica canali** | JSON | `{group: [channel, …]}` — your canonical lineup. |
| **Alias / rename** | JSON | `{search: replace}` applied to stream names before matching. |
| **Stream da escludere** | JSON | `[exact stream name, …]` dropped before matching. |

### Actions

- **Anteprima (Dry-run)** — computes and reports the result without writing.
- **Ricostruisci** — deletes all channels and rebuilds them from scratch, then
  assigns users and starts EPG auto-match. Asks for confirmation.

> ⚠️ **Ricostruisci deletes every channel** (`Channel.objects.all().delete()`)
> and recreates the lineup. Back up your database before the first run.

---

## Configuration examples

### Canonical channel list (`canali_json`)

```json
{
  "🇮🇹 TOP ITALIA": ["RAI 1", "RAI 2", "RAI 3", "RETE 4", "CANALE 5", "ITALIA 1"],
  "📰 NOTIZIE": ["SKY TG24", "TGCOM24", "RAI NEWS 24"],
  "🔥 DAZN": ["DAZN 1", "ZONA DAZN"]
}
```

Order matters: channels are numbered progressively following this list, group by
group. Anything not listed here (and not excluded) ends up in the "Other
Channels" group, grouped by identical uppercased name.

### Aliases (`alias_json`)

Aliases are plain, case-insensitive search/replace rules applied to the stream
name **before** matching. Use them to reconcile provider naming quirks:

```json
{
  "SKY TG 24": "SKY TG24",
  "RAI NEWS24": "RAI NEWS 24"
}
```

Example: a stream named `SKY TG 24 HD` becomes `SKY TG24 HD`, which then matches
the canonical `SKY TG24`. Rules are applied longest-first, so more specific
rules win.

### Exclusions (`exclude_json`)

Exact stream names (as they appear in Dispatcharr) to drop entirely — before
alias and match. Handy to keep only the good feeds of a channel:

```json
[
  "SKY UNO 4K HEVC H265",
  "SKY UNO FULL HD",
  "SKY UNO HD",
  "SKY UNO SD"
]
```

With the list above, only `[S+] SKY UNO …` variants survive for the SKY UNO
channel; every other SKY UNO feed is discarded.

---

## How matching works

For each active stream:

1. **Exclude** — if the exact (uppercased) stream name is in `exclude_json`,
   drop it.
2. **Alias** — apply case-insensitive replacements to the name.
3. **Normalise** — uppercase, collapse multiple spaces to one.
4. **Match** — find the first canonical channel (canonical names sorted by
   length, descending) whose normalised name is a **substring** of the stream
   name. Longest canonical name wins, so `SKY SPORT 24` beats `SKY SPORT`.
5. **Unmatched** — the stream goes to the "Other Channels" group, grouped with
   other streams sharing the same uppercased name.

Within each channel, streams are sorted by:

- **Quality**: 4K/UHD/HEVC/H265 (0) → Full HD/FHD (1) → HD (2) → unspecified (3)
  → SD (4)
- **Account type**: Xtream (XC) before plain M3U (STD)

so failover always starts from the best available source.

### Worked example

Canonical: `"📰 NOTIZIE": ["SKY TG24"]`, alias `{"SKY TG 24": "SKY TG24"}`.

| Stream (source) | After alias+norm | Result |
|-----------------|------------------|--------|
| `SKY TG 24 4K HEVC H265` (camcam, XC) | `SKY TG24 4K HEVC H265` | SKY TG24, quality 0 |
| `[S+] SKY TG24 FULL HD` (antitesi, XC) | `[S+] SKY TG24 FULL HD` | SKY TG24, quality 1 |
| `SKY TG 24 HD` (list, STD) | `SKY TG24 HD` | SKY TG24, quality 2 (after XC) |
| `TG NORBA 24 HD` | `TG NORBA 24 HD` | not matched → Other Channels |

All SKY TG24 feeds collapse into one channel with correct failover order;
`TG NORBA 24` is left untouched.

---

## Logos and EPG

- **Logos**: for each channel the plugin looks up an existing logo by the first
  stream's `logo_url`, then by channel name (exact, then case-insensitive). It
  does **not** create new logos — populate the Logo Manager first if needed.
- **EPG**: each channel inherits the `tvg_id` of its best stream, then the
  plugin triggers Dispatcharr's `match_selected_channels_epg` task for the newly
  created channels. EPG only matches where a stream `tvg_id` corresponds to an
  entry in your active EPG sources.

---

## Requirements

- Dispatcharr with the plugin system (recent versions).
- At least one active M3U/Xtream source.
- For logos: entries already present in the Logo Manager.
- For EPG: configured and refreshed EPG source(s).

## Notes & caveats

- **Full rebuild**: `Ricostruisci` removes and recreates all channels. It is
  idempotent (safe to re-run) but destructive of the current lineup. Back up
  first.
- **Timeshift (+1)**: `RAI 1 +1` is a substring of `RAI 1`, so it will attach to
  `RAI 1` unless you exclude it or drop it upstream at the provider.
- **The Web UI has no multi-select**: user selection is a comma-separated text
  field by design (Dispatcharr plugin fields don't support dynamic multi-select).

## License

Provided as-is under the MIT License. See `LICENSE`.
