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

`demo-seed --full` writes `case-demo-01` (five text stand-ins), `case-demo-02`
(the acceptance files, two messy intake sheets and the documents below, each under
a title an analyst would use) and `case-search-eval` (the retrieval gold set's
unrelated news passages and its test tone, kept out of the demo case). Leave the
worker running until the Sources page shows no "processing" rows; on a laptop that
takes a few minutes, mostly thumbnails, transcription of the other recordings and
embeddings. Pick `case-demo-02` in the case switcher. The colleagues
`analyst-2@example.com` and `analyst-3@example.com` exist with unshown passwords;
reset one with `stk reset` to see the case from their side.

Seven runs use fixed output instead of a model, so the demo is the same every time:
the witness clip transcript (copied from the script the synthetic clip was spoken
from), the two sheet extractions (answers written by hand per row), and the linker
and intake brief on each of the two batches (a templated answer over the same
candidates the model would get). Their runs
say `sealbook-demo-fixed-output` as the served model and carry a `demo` note in
their inputs. Every other run is the real tool. Human decisions are signed by a demo
passkey held by `senior-analyst`, the same signature path a browser passkey uses.

Five of the case's documents are paper, made by `acceptance/make_demo_docs.py`
(files and ground truth in `acceptance/demo/`), every name fictional and each page
marked synthetic:

- Branch 999 detainee list, 12 February 2014: a scanned typed form (letterhead,
  reference number, a bordered table of four rows, two signatures, an office stamp).
- Dormitory 4 roster, July 2013: a scanned printed form with three names filled in
  by hand.
- Documentation note 14/2014 and Statement of a released detainee, October 2013:
  text-layer PDFs from two fictional documentation groups.
- Testimony of Reem, Branch 999: a typed Arabic transcript as a text-layer PDF. It
  is sensitive, so no cloud model sees it; the PDF reader reads it locally.

The PDFs are read by the real PDF reader at seed time and their claims sit on that
page text. The two scans were read once by the app's own OCR prompt on
`google/gemini-4-argon` (`OCR_MODEL`) on OpenRouter's zero-retention route (both are
ordinary sources) and the answer is kept in `acceptance/demo/ocr.json`. The
seed replays it through the real `ocr` tool: the run names that model, carries a
`demo` note saying when it was recorded and that no model ran at seed time, and
writes the text as a derived blob like a live run. A claim on a scan points at the
OCR text where OCR wrote the value exactly, otherwise at the value's box on the
scan. OCR wrote the list header's date as 12-02-2014, so the list's document date
is the one claim boxed on a scan; every other value on both scans was read exactly.

## The Guide

`/guide` (Guide in the rail) walks through each flow below as a task, with Open it
links that switch to `case-demo-02`, and ends with Why the record holds: one
accepted claim from this case recomputed in the browser, hash by hash, with a
control that changes one character and shows where the check breaks.

## Start at the Desk

The top panel is the intake brief for the newest batch you added that you have not
read: the dormitory roster and the released detainee's statement. It says what
arrived, who is new to the case and who is known (Yousef Khaled Al-Ali from four
other sources), where (Branch 999, known) and when (July to 2 August 2013), what the
batch connects to and up to five next steps, each a link: review the claims read
from the roster, decide the links the linker proposed, decide the July 2013 shared
presence. Lines resting on a sensitive source you have not revealed read "A line
from a sensitive source" with a link to that source; reveal it there and the line
shows. Mark read, or open it in the Inbox, and the panel goes. The colleague
`analyst-2` gets the same for the family intake sheets.

Open `/desk`. Since you left lists the colleagues' and agents' work grouped by who
and what, in plain words ("AI proposed 17 ages from Family intake sheet, version 1",
"senior-analyst accepted 26 claims"). Needs you shows two review requests from analyst-2 (the Jdeideh arrest
place and the corrected name حسن), a mention on that correction thread, and two
assignments: the transfer order due in two days and the witness clip three days
overdue. Mentions and replies name the claim and source they are about, as the
bell does. Findings waiting lists what discovery proposed; Recent evidence shows the
newest sources with their titles, kind and file name. Notice that nothing here is a fact yet: each row
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

Open Add evidence (`/ingest`). The case comes from the switcher; change it from the
case button, or make one with New case (type `case-demo-2` to see the close-name
warning point at case-demo-02). Drop a few files: each shows its fingerprint at
once, a suggested document type (the sparkle icon; your pick shows a person icon)
and Sensitive by default. Drop `register_page.png` from `acceptance/data` to see
the duplicate: after Add it names the source it repeats, when and from where, and
stores nothing new. A spreadsheet with title rows above its header (any XLSX with
a heading line) shows where the header was found and a column preview to confirm,
each column pre-filled with its reason ("header says التاريخ and values look like
dates") even with no model running. Add it without confirming and its row, its
Sources chip and its evidence page all say the columns wait for you; confirm them
on the evidence page and the panel shows what the rows wait for next.
Search the web proposes queries from case-demo-02's ordinary names; nothing is sent
until you pick one. With no model set up for a sensitive item, its row says it is
waiting for a model and why. Then open `/sources`
for what is already in the case: photos, three scans (the register page, the
detainee list, the dormitory roster), four PDFs (the transfer order, the
documentation note, the released detainee's statement, Reem's testimony), three
recordings, two video clips, web posts and the two intake sheets.
Filter by assignee to see the workload strip: the admin, analyst-2 and analyst-3
each have open items, one of analyst-3's is done, one of analyst-2's is overdue.
The open findings column counts findings whose members come from that source.

The sheet flow: analyst-2 added `messy_people.csv` as version 1 of the dataset
"Family intake", confirmed a field for every column, and the rows were read. Then
`messy_people_v2.csv` came in as version 2: the remembered mapping applied, and the
diff against version 1 found changed rows (two dates and one place), a row gone,
two new rows, a repeated identifier and a row without one. Only new and changed
rows were read, so version 2 has proposals for those rows alone. To watch the
mapping and diff steps live, edit one cell of `acceptance/data/messy_people_v2.csv`
in a copy and add it in `/ingest`; the remembered dataset is picked from its columns.

Once a batch is read, a case suggestion sits above the intake brief. Add, as
ordinary, a text file into case-demo-01 that names Yousef Khaled Al-Ali last seen
in Douma on 2 May 2013: it reads "1 of 1 source looks like case-demo-02", with the
name and the place, each with its date and the characters it was read from. A file
about an unrelated place gets a suggested new case name from that place and month.
Add to case-demo-02 records the file there too; it stays in case-demo-01.

## See it

Open a source from Sources or the Desk. Each kind shows its claims on the span they
came from:

- `register_page.png`: name, parents, birth year and arrest place boxed on the
  scan. The twins Yousef (mother فاطمة) and Yousef (mother سعاد) are two rows.
- `transfer_order.pdf`: claims sit on the text layer of page 1 (the detainee line,
  the holding branch, the destination and the date).
- Branch 999 detainee list: the scanned form, with the document date boxed in the
  header; the Read tab shows the text OCR read from it, the other 20 claims sit on
  that text. Rows 1 and 2 are the two men named محمد أحمد الخطيب.
- Dormitory 4 roster: the scanned form with the three handwritten names; its six
  claims sit on the OCR text in the Read tab.
- Documentation note, released detainee's statement and Reem's testimony: the PDF,
  with a claim's words found and marked in the page. Reem's is sensitive; show it
  first.
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

Exact hits rank above meaning leads; leads are labelled close, related or loose and
never accepted. A name query shows no photo leads: leads far below the best match
are left out.

## Discover connections

Open `/discover`. The real discovery run proposed these from accepted, dated claims
at confirmed places:

- "4 people held at Branch 999 in July 2013", once as a shared presence and once
  as a candidate incident: Yousef from the voice clip and the cell roster, Bassel
  Nader and Fadi Karam from the roster, Salim Haddad from his published statement.
  Three independent sources, so the band is strong. Each lists four people, with
  Yousef's two supporting claims on his one row, and each says it is also proposed
  as the other kind.
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

## Links the rules miss

The linker read each batch against the case and proposed, for review, what the
watchlist matcher and discovery do not: the roster and the released detainee's
statement as related documents (they name the same three men), the four men as
cellmates (named together, held at Branch 999 in July 2013), and on the sheet batch
Rania Haddad as involved in "Women held at Branch 999, March 2014" (last seen there
on 15 March 2014) and the rows that appear in both sheet versions as one person.
Open Review, All open: each reads as a question ("Cellmates?", "Related documents?",
"In this incident?"), proposed by "Linker for" the analyst who added the batch, with
the claims it rests on as cards and the run's reason. Nothing is accepted. Run it
again from a person page or the evidence Intelligence panel (Find links): what is
already proposed or decided is not proposed again, and a rejected link returns only
with a claim the rejection did not see.

## Every agent, and what resembles what

Open Agents in the rail with case-demo-02 selected. Each agent says what it does,
when it runs and how its last run went; the intake brief and linker show the seeded
demo runs. On the Noticer card press Check now (the worker must be running with a
model set). It pairs the two family intake sheet versions (67 words in a row the
same), the coastal bus voice note with its forwarded excerpt (same sound, same
words), the two captures of the missing person post, the forwarded checkpoint photo
with the original, the Jdeideh re-upload with the 2017 video, and Yousef's register
entry with the record of the same name in case-demo-01. Each says why, with a next
step; Propose a link sends one to Review, and nothing goes there without that
click. Open any of those evidence pages for This resembles. Outside assistants at
the bottom shows how to connect an MCP assistant.

## Decide together

Open `/review`. Asked of me lists the claims a colleague asked you about, due first,
each as a sentence with its source and the place in it. Select one: the source opens
at the span (the box on the register scan, the line on the transfer order PDF, the
second in the witness clip, the cell in the sheet), with the checks in plain words,
who proposed it (AI extraction, with the model as detail) and the other claims
about the same person. The corrected name reads "Name: correct عمر حسين الزين to
عمر حسن الزين". Reveal a
sensitive source to read masked names; the reveal is logged. Accept (a) or reject
(r) with your passkey after reading the rows it signs; Undo is offered at once.
Correct (e) sends a corrected value to a colleague; Ask (s) starts a thread, which
continues in Inbox. All open lists every unconfirmed claim, and Findings decides
discovery findings. The Sources page selection can also request review of every
open proposal at once, and any finding can be assigned with bulk assign.

## Follow a person, a place and an incident over time

Open `/stories` and pick:

- Person, Yousef Khaled Al-Ali (register R1): arrested in Harasta and last seen in
  Douma on 2 May 2013, read as one day (last seen, then arrested a few km away),
  not as sources that disagree; a gap of
  about two months; Branch 999 in July from the clip and the roster; held there
  and transferred to Central Prison K on 17 September 2013, with a gap before it.
  The map numbers the places in order; the lines are sequence, not routes. Pending
  findings that include him show as hatched bands that open their checklist.
- Incident, "Women held at Branch 999, March 2014": the outlined band with Lina
  and Reem as its rows.
- Place, Branch 999: every person placed there, from May 2013 to March 2014.

Play runs the scrubber across the timeline and the map together. Save keeps the
query; Package turns the factual points into a draft brief.

## See the whole case on the case map

Open Case map in the rail (`/case-map`; `/monitor` redirects there) with
`case-demo-02` selected:

- Who was where: one row per person. Branch 999 in July 2013 is a band, "held
  together, July 2013", over Yousef Khaled Al-Ali, Salim Haddad, Fadi Karam and
  Bassel Nader. Pick it: the panel lists each of them there with the roster, the
  clip or the released detainee's statement, where each came from (Yousef from
  Douma, the others with no earlier record), who left for Central Prison K on 17
  September 2013 with the transfer order, and whose record stops after July.
- The two Yousef Khaled Al-Ali rows, the two Rania Haddad rows and the two محمد أحمد الخطيب
  rows read "possibly two people": an open same-person proposal
  joins them and nobody has decided it.
- Move the slider or press Play: Branch 999 grows and shrinks by the people there
  that month, the move to Central Prison K is an arrow, and dots ride it only
  while playing (not with reduced motion). Harasta, Douma, Homs and Aleppo are
  faint areas, since each is known only to within a few km.
- Ask how لينا عمر النجار is connected to Yousef Khaled Al-Ali: two steps
  through Branch 999, the second marked weak, about 5 months apart, a shared place
  and not a shared cell.
- Case news lists both intake batches with how many people they name, the linker
  and intake brief runs, the accepted and rejected findings and claims, newest
  first. Admins get a link to Worker runs (`/monitor/runs`), the old telemetry.

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
place proposal, so the row opens it in Review; a name without one shows a
suggested place and why (one gazetteer candidate, the same spelling confirmed in
another case you can open, or which of several candidates the surrounding words
favour) and is confirmed in place with your passkey, or ticked and confirmed with
others under one prompt. Suggest approximate places asks the place finder about
names the gazetteer lacks; its suggestion is marked as the agent's and quotes the
passage it rests on.

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
(plus Raqqa and three invented facilities in `sealbook/monitor/seed_places.tsv`). Some
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
