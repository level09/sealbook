# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Investigators and analysts at human rights documentation teams who document
detention, disappearance and attacks. Daily, they take in photos, videos, scans,
spreadsheets, voice notes and web posts about a case, read them, link people,
places and events across sources, and sign what they can defend. They work in
Arabic and English, often with sensitive material and witnesses at risk.
sealbook.org speaks to them first (confirmed 2026-10-31); team leads and funders
deciding a pilot read the same page.

## Product Purpose

Sealbook turns raw evidence into findings an analyst can defend. Models read every
file and the case around it and propose: claims pinned to the exact words, page,
region or second they came from, a first pass in the analyst's own method (triage,
title, description, labels), same-person and same-event links, and incidents. A
person accepts, rejects or corrects each proposal with a passkey signature, and
every decision can be undone. Success is an analyst who spends their time judging,
not transcribing, and a finding that survives scrutiny years later.

## Positioning

AI that proposes and never decides, on a custody record that can be verified
offline: every fact traces to its source span, every decision to a named person,
and a signed package proves later that not one word changed.

## Operating Context

Built on the method human rights teams already use (Bayanat's open methodology and
label lists, credited). Intake by drop, paste, phone camera, link, web search,
spreadsheet or recorded voice note into a case. Work happens in To do (what waits
on you), the evidence page, Discover (patterns and same-event groups), and the case
pages (sources, people, places, stories, case map, incidents). Sensitive sources
stay masked until a logged reveal.

## Capabilities and Constraints

- Hosted models only on zero-retention routes by default; a private endpoint is
  optional. OCR, speech to text (local), picture and sound fingerprints, semantic
  search across text, images, frames and audio.
- The site is static HTML, CSS and JS with no build step, self-hosted fonts and no
  third-party requests; a Cloudflare Worker serves it and stores invite requests.
- The site's demos run on a fictional case with openly licensed media; nothing on
  the site may be presented as real case evidence.
- Status is pre-pilot: no measured agreement with analysts yet.

## Brand Commitments

Name Sealbook. Plain, short, factual prose; no em or en dashes; no hype or claims
the product docs do not make. The page is sealed the way Sealbook seals evidence
(a hash chain over its sections, with a tamper demonstration).

## Evidence on Hand

- The walkthrough film (site/media/walkthrough.mp4, 1:15) recorded from the real
  app on a fictional case.
- Nineteen live demos on the fictional case (site/js/demo-*.js, site/data/case.json).
- docs/status.md (what is built, with limitations) and docs/agent-eval.md
  (model comparisons on synthetic data).
- No customers, testimonials, usage numbers or press yet: none may be invented.

## Product Principles

1. Propose, never decide: every model output is a proposal a person signs.
2. Every fact points back to the words, region or second it came from.
3. Show the analyst's real work, on real screens, never a mock.
4. Honest about limits: what is not built or not measured is said.
5. Privacy before convenience: sensitive material is masked until a logged reveal.

## Accessibility & Inclusion

Arabic and English, right to left handled per line; works on a phone; respects
reduced motion.
