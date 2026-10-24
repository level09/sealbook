# Guided tour of the demo case

A short walk through every flow, using the data `stk demo-seed --full` writes. Each
section names what to click and what the analyst should notice. Everything is
synthetic or openly licensed: the people are invented, Branch 999 and Central
Prison K do not exist, and the Commons photographs carry their credits in the
evidence metadata.

## Set it up

On a fresh database:

```sh
uv run stk create-db
uv run stk install
uv run stk demo-seed --full
uv run stk worker
```

`demo-seed --full` writes `case-demo-01` (five text stand-ins) and `case-demo-02`
(the acceptance files, two messy intake sheets and the documents below). Leave the
worker running until the Sources page shows no "processing" rows; on a laptop that
takes a few minutes, mostly thumbnails, transcription of the other recordings and
embeddings. Pick `case-demo-02` in the case switcher. The colleagues
`analyst-2@example.com` and `analyst-3@example.com` exist with unshown passwords;
reset one with `stk reset` to see the case from their side.

Three runs use fixed output instead of a model, so the demo is the same every time:
the witness clip transcript (copied from the script the synthetic clip was spoken
from) and the two sheet extractions (answers written by hand per row). Their runs
say `sealbook-demo-fixed-output` as the served model and carry a `demo` note in
their inputs. Every other run is the real tool. Human decisions are signed by a demo
passkey held by `senior-analyst`, the same signature path a browser passkey uses.

## Start at the Desk

Open `/desk`. Since you left lists the colleagues' and agents' work grouped by who
and what. Needs you shows two review requests from analyst-2 (the Jdeideh arrest
place and the corrected name حسن), a mention on that correction thread, and two
assignments: the transfer order due in two days and the witness clip three days
overdue. Findings waiting lists what discovery proposed; Recent evidence shows the
newest sources with thumbnails. Notice that nothing here is a fact yet: each row
names its source and what it waits on.

The bell holds stored notifications: assigned work, replies, the mention, and
"Intake finished" for the roster batch once the worker is done with it.

## Read your inbox

Open `/inbox` from the rail. One list holds everything addressed to you, newest
first: the two requests from analyst-2 and the one you sent, the threads with
replies and the mention, the transfer order due in two days, the witness clip
overdue, and the finished intake batches. Unread items carry a dot; opening one
marks it read, and the bell and rail counts drop with it. The chips narrow the list
to requests asked of you or sent by you, threads and mentions, assigned work,
decisions or intake.

Select the corrected-name request: the claim reads as a sentence with its source
thumbnail and span, then the request and each reply with who wrote it and when.
Reply with `@analyst-2` and pick the name from the suggestions; the people in the
thread and admins are notified. Deciding happens in Review: Decide in Review opens the same
request there. Use j and k to move, r or c to reply.

## Bring evidence in

Open `/ingest` and add any file to see the per-item stages. Then open `/sources`
for what is already in the case: photos, scans, the transfer order PDF, three
recordings, two video clips, web posts, text documents and the two intake sheets.
Filter by assignee to see the workload strip: the owner, analyst-2 and analyst-3
each have open items, one of analyst-3's is done, one of analyst-2's is overdue.
The open findings column counts findings whose members come from that source.

The sheet flow: analyst-2 added `messy_people.csv` as version 1 of the dataset
"Family intake", confirmed a field for every column, and the rows were read. Then
`messy_people_v2.csv` came in as version 2: the remembered mapping applied, and the
diff against version 1 found changed rows (two dates and one place), a row gone,
two new rows, a repeated identifier and a row without one. Only new and changed
rows were read, so version 2 has proposals for those rows alone. To watch the
mapping and diff steps live, edit one cell of `acceptance/data/messy_people_v2.csv`
in a copy and add it in `/ingest` with Prepare sheet.

## See it

Open a source from Sources or the Desk. Each kind shows its claims on the span they
came from:

- `register_page.png`: name, parents, birth year and arrest place boxed on the
  scan. The twins Yousef (mother فاطمة) and Yousef (mother سعاد) are two rows.
- `transfer_order.pdf`: claims sit on the text layer of page 1 (the detainee line,
  the holding branch, the destination and the date).
- `witness_clip.ogg`: the transcript runs alongside the waveform. Select "Seen at"
  and the player jumps to 00:10, where the witness says he saw Yousef at Branch 999
  in July 2013.
- `messy_people.csv`: the sheet as a table, each proposal marked on its cell, with
  sex, age, occupation and event claims next to positions. Show it first: it is
  sensitive, and the reveal is logged.
- `aleppo_checkpoint.png` and its recompressed forward: the Copies tab shows the
  picture match. `jdeideh_2017.webm` and its 12 second excerpt match by frames and
  sound. Open either side by side.

The Metadata tab holds the origin, licence and author for Commons media, the
C2PA result, both hashes and the custody chain: who acquired it, every agent run
and every view.

## Search

Open `/search` and try:

- `Yousef Khaled Al-Ali`, `يوسف خالد العلي` and `Yusuf al-Ali`: one person in two
  scripts and several spellings, with the documents that name him.
- `Harasta` or `حرستا`: the place, the register and the claims located there.
- `Branch 999`: the facility and everything placed there.
- `تسعمية`: a word only spoken in the clip, found in its transcript with the time.
- Paste the first characters of a SHA-256 from any evidence page: the exact
  source comes first.

Exact hits rank above meaning leads; leads are labelled and never accepted.

## Discover connections

Open `/discover`. The real discovery run proposed these from accepted, dated claims
at confirmed places:

- "4 people held at Branch 999 in July 2013", once as a shared presence and once
  as a candidate incident: Yousef from the voice clip and the cell roster, Bassel
  Nader and Fadi Karam from the roster, Salim Haddad from his published statement.
  Three independent sources, so the band is strong.
- "Both held at Branch 999 in September 2013": Yousef on the transfer order and
  Bassel Nader from row 16 of the second sheet. One shared day in a month, so the
  band is weak. Notice the two Bassels are separate records until someone links
  them.
- "3 people held at Branch 999 in March 2014", still open: Lina, Reem and Rania.
  Decide it yourself.

Already decided, through the same one-passkey checklist: the March 2014 candidate
incident was accepted for Lina and Reem only, as "Women held at Branch 999, March
2014", because Rania was seen in the visiting room. The May 2013 shared presence
was rejected with the reason "One man recorded twice": both records are Yousef,
read from the appeal and its recapture. A rejected set is never proposed again.

Opening a finding writes an access record; names from sensitive sources stay
masked until you reveal that source.

## Decide together

Open `/review`. Asked of me lists the claims a colleague asked you about, due first,
each as a sentence with its source and the place in it. Select one: the source opens
at the span (the box on the register scan, the line on the transfer order PDF, the
second in the witness clip, the cell in the sheet), with the checks in plain words,
the run that proposed it and the other claims about the same person. Reveal a
sensitive source to read masked names; the reveal is logged. Accept (a) or reject
(r) with your passkey after reading the rows it signs; Undo is offered at once.
Correct (e) sends a corrected value to a colleague; Ask (s) starts a thread, which
continues in Inbox. All open lists every unconfirmed claim, and Findings decides
discovery findings. The Sources page selection can also request review of every
open proposal at once, and any finding can be assigned with bulk assign.

## Follow a person, a place and an incident over time

Open `/stories` and pick:

- Person, Yousef Khaled Al-Ali (register R1): arrested in Harasta and last seen in
  Douma on 2 May 2013, marked as sources that disagree, left for a person; a gap of
  about two months; Branch 999 in July from the clip and the roster; held there
  and transferred to Central Prison K on 17 September 2013, with a gap before it.
  The map numbers the places in order; the lines are sequence, not routes. Pending
  findings that include him show as hatched bands that open their checklist.
- Incident, "Women held at Branch 999, March 2014": the outlined band with Lina
  and Reem as its rows.
- Place, Branch 999: every person placed there, from May 2013 to March 2014.

Play runs the scrubber across the timeline and the map together. Save keeps the
query; Package turns the factual points into a draft brief.

## Open a place or an incident

Every place and incident has one page with the same layout: names and key facts on
top, its story, its relations, the sources behind it and, under `t`, thread and
signed history. Open `/places` from the rail: places with how many claims, people
and sources resolve to each. Open Branch 999: both names, Damascus from the
COD-AB boundaries, known to within 250 m, the circle on the map, fourteen people
placed there from May 2013 to March 2015 (several unnamed, because their names
come from sensitive sources), the accepted "Women held at Branch 999, March 2014",
the four open findings and the places within 5 km. Harasta shows Yousef twice,
since his two records are not yet linked. Al Jadida Quarter in `case-demo-03`
carries the bakery queue incident.

The Unresolved names tab lists spellings no confirmed place resolves (Aleppo,
Central Prison K and Douma from the intake sheet). Each already has a pending
place proposal, so the row opens it in Review; a name without one is confirmed in
place with your passkey.

Open an incident from a place page, Search or Discover: "Women held at Branch 999,
March 2014" (`case-demo-02`), "Shelling of a bakery queue, Al Jadida, 14 August
2016" (`case-demo-03`), "Men held at Block C, March 2014" (`case-demo-05`) or the
Aleppo market shelling (`case-demo-06`). Notice that its dates and place come from
the statements kept when the finding was accepted, people carry their roles, the
finding that built it is listed with its band, and the history groups the one
gesture that signed it all. The analyst who signed it can undo it there.

## Package

Open `/packages`. The family brief "Yousef Khaled Al-Ali and Lina Omar Al-Najjar:
what the records say" has two sentences, each citing accepted claims. Building it
into a verifiable package needs anchor receipts (`stk anchor`), so the demo stops
at the draft: notice that every sentence links back to its statements, and that a
sentence without factual support cannot be saved.

## More cases

`uv run stk demo-seed --more` adds four investigations beside the first two, so the
case switcher, Sources, Discover and Stories look like a deployment. It is additive
and refuses if any of them exists; `--full` is not required. Every person is
invented, and each person is written in two or three spellings across a case. The
documents are short synthetic texts in Arabic and English (a few are web posts), each
with claims pinned to the exact words, dated and placed at GeoNames fixture places
(plus Raqqa and three invented facilities in `stk/monitor/seed_places.tsv`). Some
claims are accepted by the simulated signed reviews, one place per case is rejected
with its reason, the rest are open proposals. Work is assigned across analyst-2 to
analyst-5 with different loads, and two review requests per case notify the analysts.
The photographs are real and openly licensed (credits in the evidence metadata and
`acceptance/public/ATTRIBUTION.md`); no claim in any case refers to them.

| Case | What it shows |
| --- | --- |
| `case-demo-03` Aleppo, Al Jadida quarter under siege, 2016 | Shared presence at a shelter and a clinic in July and August, an accepted candidate incident (a bakery queue shelled on 14 August) and a rejected single-roster presence, an evacuation to Aleppo, a missing-person post |
| `case-demo-04` Displacement from Raqqa, 2017 | A route over time: Raqqa, Tabqa checkpoint, Manbij, Zaytoun Camp, with the same family named in Arabic and in two English spellings; open findings at the camp; two VOA photographs of Raqqa |
| `case-demo-05` Detention and disappearance, Branch 418 near Homs, 2012 to 2014 | Arrest, holding, transfer to Block C and a released detainee's statement; two invented facilities; an accepted March 2014 incident at Block C; six Homs photographs |
| `case-demo-06` Market shelling, Aleppo, November 2016 | Victims, wounded and witnesses from a witness report, a clinic list, a register of the dead and a shopkeeper statement; an accepted incident; the souk photograph |

Each case has 9 to 16 sources. Run the worker until Sources shows nothing
processing; thumbnails, indexing and image matching are the only slow part.

`uv run stk demo-seed --scale N` adds N filler sources to `case-scale-demo`, apart
from every story case, to show the Sources table, search and case switcher at
thousands of rows. Fillers are chained like any acquisition (`stk verify` passes) but
carry no claims, no runs and no bytes: they are labelled `synthetic` in file, origin
and context, nothing is queued for them, previews stay empty, and about 8 percent are
assigned. The ledger is insert-only, so they cannot be removed from a database later.
