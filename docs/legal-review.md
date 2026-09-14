# e-GP terms — research brief for §8's legal check

**This is not legal advice, and I am not qualified to give it.** §8 says to get
this reviewed by someone qualified, and that still stands. What follows is the
reading done so that a Bangladeshi lawyer's first hour is spent on judgement
rather than on finding documents.

Researched 2026-09-14 against the live portal.

## Documents that exist

| Document | URL |
|---|---|
| Terms and Conditions of e-GP System user agreement | `eprocure.gov.bd/TermsNConditions.jsp` |
| Disclaimer and Privacy Policy | `eprocure.gov.bd/PrivacyPolicy.jsp` |
| e-GP Guidelines (Revised) 2025 | `eprocure.gov.bd/help/guidelines/eGP_Guidelines.pdf` |
| Information Security Policy | `eprocure.gov.bd/help/guidelines/info_sec.pdf` |

The portal is owned and operated by the **Bangladesh Public Procurement
Authority (BPPA)**, under IMED, Ministry of Planning.

## What the documents actually say

**1. The terms read as a user agreement, not a general site policy.**
The acceptance sentence is scoped to "accessing and using this e-GP *user
services*", and everything that follows it concerns registration: email
verification, credential document verification, passwords, account details,
bank payments, tender submission. Registration costs Tk 5,000 with Tk 2,000
annual renewal.

Nothing in either document appeared to address automated access, scraping,
crawl rates, bulk download, or redistribution by name. That absence is itself
the central question for a lawyer — it is not the same as permission.

**2. There is a broad copyright assertion.**
The materials on the portal, explicitly including *the information* as well as
the software, are stated to be copyrighted to BPPA / IMED / the Government. The
footer reads "Copyright © 2011 Bangladesh Public Procurement Authority (BPPA).
All Rights Reserved."

This is the clause that bears on what we store and re-display, and it is the
one I would put in front of counsel first.

**3. There is a "do not inhibit the system" clause.**
Use must be lawful and must not restrict or inhibit use of the system by third
parties. The listed examples are conduct-based — misleading, defamatory,
harassing, obscene content — and include *interruption of the normal flow of
content within the e-GP System*.

That last phrase is the one a rate-limited crawler is measured against. Our
crawler is deliberately modest: ~38 requests per 30-minute sweep at size 100,
serialized with a 1-second minimum gap, exponential backoff on 429/503, and a
User-Agent naming the project with a real contact address.

**4. Browsing is explicitly not tracked to an identity.**
The privacy policy states that mere browsing does not capture individually
identifying data. It also records that IP addresses and all activity from login
to logout are logged for audit and non-repudiation — that is framed around
logged-in sessions.

**5. There is no robots.txt.**
`/robots.txt` returns the portal's session page with HTTP 200, not a robots
file. So there is no machine-readable crawl directive to comply with or
violate, in either direction.

## Is there an official data-sharing route? (§8's second question)

**No public API or open-data endpoint was found** on either eprocure.gov.bd or
bppa.gov.bd.

**But there is a statutory route.** BPPA publishes a full Right to Information
structure at `bppa.gov.bd`: an Information Officer, a Designated Officer, an
Alternative Designated Officer, an appeals path, and the RTI Act rules and
documents. Bangladesh's Right to Information Act 2009 obliges public
authorities to designate these officers and to respond to information requests.

**This converts §8's "somehow" into a named, official channel.** A written RTI
request to BPPA's Designated Officer asking (a) whether bulk or programmatic
access to published tender notices can be granted, and (b) what conditions
BPPA places on redistribution, would produce a documented answer from the data
owner. Whatever it says, it is worth more than inference — and a refusal is
itself useful, because it is specific.

**Also relevant precedent:** BPPA distributes procurement information through
its own mobile app ("Sarkari Kroy Dorpon"), which has its own published privacy
policy. A public authority already redistributing this data through an app is a
fact worth having in the conversation.

## What we already do that reduces exposure

These were design rules from the start, not retrofits:

- **Government documents are linked, never rehosted** (§8.3). Tender PDFs stay
  on the government portal; we store a URL.
- **The crawler identifies itself** with the project name and a monitored
  contact address (§8.1), so BPPA can reach us rather than block us.
- **Rate limited and backing off**, with no aggressive retry against a
  government host (§8.7).
- **Raw responses are archived** (§8.4), so if a question is ever raised about
  what we fetched and when, there is a precise record.
- Every tender page states the source and advises verifying against the
  original notice before bidding.

## The questions a lawyer should actually answer

1. **Do the terms bind a non-registered visitor at all?** They are framed
   around user services and registration. If they bind only account holders,
   a public crawler may sit outside them entirely — but that is exactly the
   judgement call I cannot make.
2. **Does the copyright assertion extend to the facts in a tender notice** —
   closing dates, package numbers, procuring entity names — or only to the
   portal's compilation, presentation and software? Factual data and creative
   compilation are usually treated differently, and the answer decides what we
   may store and re-display.
3. **Does a 30-minute sweep of ~38 requests fall anywhere near "interruption
   of the normal flow of content"?** It seems plainly not, but the standard is
   BPPA's to apply, not ours.
4. **Does the RTI Act give a positive right to this data** in bulk, given it is
   already published to the public one page at a time?
5. **What do the incumbents rely on?** BDTender has operated ~19 years and
   Alltender is established. Their basis — licence, RTI, tolerated practice, or
   nothing — is worth asking about directly.

## Recommended sequence

1. **File the RTI request** with BPPA's Designated Officer. It is cheap, it is
   the official channel, and it produces a written answer from the data owner.
2. **Take questions 1–3 to a Bangladeshi lawyer** with IP and IT law
   experience, with this brief so the billable time goes on judgement.
3. **Do both before the site is public**, not before the code is written. The
   crawler running privately at a polite rate is a materially different posture
   from a public service built on the data, and §4's gates mean nothing is
   public until Gate 1 anyway.

Until 1 and 2 are done, §8's warning stands unchanged: the crawler is polite,
identifiable and rate-limited, but that is engineering, not legal cover.
