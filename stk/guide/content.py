"""The Guide's words: one scenario per task, in the order an investigator meets
them, and the chapter on why the record holds. Every claim here was checked
against the code; change the text with the behaviour it describes."""

CASE = "case-demo-02"

AREAS = [
    {"id": "bring", "title": "Bring evidence in", "icon": "ti ti-upload"},
    {"id": "find", "title": "Find", "icon": "ti ti-search"},
    {"id": "connect", "title": "Connect", "icon": "ti ti-affiliate"},
    {"id": "decide", "title": "Decide", "icon": "ti ti-signature"},
    {"id": "agents", "title": "Agents", "icon": "ti ti-robot"},
    {"id": "share", "title": "Share", "icon": "ti ti-package"},
    {"id": "trust", "title": "Trust", "icon": "ti ti-shield-check"},
]

AGENTS_OFF = (
    "Agents run on the local model route only. Without ROUTE_LOCAL_BASE_URL and "
    "AGENT_MODEL they say they are off and why."
)

SCENARIOS = [
    {
        "id": "start-at-the-desk",
        "area": "decide",
        "title": "Start your day at the Desk",
        "who": "Any analyst, first thing, or after time away from a case.",
        "steps": [
            "Pick the case in the switcher at the top. Every page follows it.",
            "Since you left groups what colleagues and agents did, in plain words.",
            "Needs you lists review requests, mentions and work assigned to you, "
            "with due and overdue dates.",
            "Findings waiting and Recent evidence show what discovery proposed and "
            "what arrived, each with its source.",
        ],
        "ai": [
            "Counts of what agents proposed since you left, such as ages read "
            "from an intake sheet."
        ],
        "you": [
            "What to open first. Nothing on the Desk is a fact yet: each row "
            "names its source and what it waits on."
        ],
        "result": "A short list of what needs you today, each row a link.",
        "links": [
            {"label": "Desk for the demo case", "href": "/desk"},
            {"label": "Inbox", "href": "/inbox"},
        ],
    },
    {
        "id": "import-a-sheet",
        "area": "bring",
        "title": "Start a case from a spreadsheet",
        "who": "A partner sent an intake sheet of family reports and you are "
        "opening the case.",
        "steps": [
            "Open Add evidence. Pick the case, or make one with New case. Typing a "
            "name never creates a case, and a name close to an existing one gets a "
            "warning.",
            "Drop the CSV or XLSX. Before anything is stored, the page shows the "
            "header row it found, the title rows it skipped, empty columns it "
            "dropped, four sample rows and a field for each column.",
            "Add it, then pick or name the dataset, the column that identifies a "
            "row and a field for every column.",
            "Check the difference against the last version: new, changed, "
            "unchanged, missing and invalid rows (blank or repeated identifiers). "
            "Confirm.",
            "Only new and changed rows are read. Each claim is pinned to its cell.",
        ],
        "ai": [
            "Up to three field suggestions for a column it has not seen, each "
            "needing your click.",
            "Name, sex, age, occupation, place and event claims for each row. "
            "Dates are read by a fixed table of Arabic and English month names, "
            "not by the model.",
        ],
        "you": [
            "The dataset, the row identifier and every column's field.",
            "Which claims become facts, in Review.",
        ],
        "result": "A dataset version with its diff and open claims, each on its "
        "cell. The next version reuses the mapping.",
        "links": [
            {"label": "Add evidence", "href": "/ingest/"},
            {"label": "The two family intake sheets", "href": "/sources?q=intake"},
        ],
        "needs": "Rows are read only when a model route is set up for the sheet's "
        "sensitivity; until then each item says it is waiting and why.",
    },
    {
        "id": "upload-a-video",
        "area": "bring",
        "title": "Add a video or a recording",
        "who": "You hold a witness clip, a voice note or a recorded call.",
        "steps": [
            "Drop the file in Add evidence. Your browser takes its SHA-256 first; "
            "the server hashes the bytes again and records nothing if they differ.",
            "Speech is turned into text on this server, line by line with times, "
            "so a sensitive recording never leaves it.",
            "The worker also makes a poster and a strip of frames, fingerprints "
            "the pictures and the sound, reads a location tag in the file if there "
            "is one, and indexes frames and sound for search by meaning.",
            "When a model is set up, claims are read from the transcript.",
            "Open the source: the transcript runs beside the player, and a claim "
            "takes the player to where it was said.",
        ],
        "ai": [
            "The transcript, and claims read from it.",
            "Copies of the clip found by picture and sound fingerprints.",
            "A location from the file's own tag.",
        ],
        "you": [
            "Which claims hold, in Review.",
            "Whether a match is the same recording. Only a confirmed same "
            "recording counts the two as one source.",
        ],
        "result": "A source with a timed transcript, thumbnails, fingerprints and "
        "open claims.",
        "links": [
            {
                "label": "A word spoken only in the witness clip",
                "href": "/search?q=%D8%AA%D8%B3%D8%B9%D9%85%D9%8A%D8%A9",
            },
            {"label": "Videos in the demo case", "href": "/sources?type=video"},
        ],
        "needs": "Transcription needs the speech model (stk speech-model). Claims "
        "need a model route.",
    },
    {
        "id": "upload-a-photo",
        "area": "bring",
        "title": "Add a photo or a scanned page",
        "who": "You have photographs, a scanned register or a PDF order.",
        "steps": [
            "Drop the files in Add evidence. Each shows its fingerprint at once, a "
            "suggested document type and Sensitive by default.",
            "Text in a picture is read, then claims are read from that text. A PDF "
            "with a text layer is read page by page.",
            "The same bytes again add one more acquisition to the source you hold "
            "and store nothing new; the page names the original. A resized or "
            "recompressed copy is matched by its picture fingerprint.",
            "A location in the photo's EXIF or C2PA data becomes a proposal, never "
            "finer than 50 m.",
            "Open the source: each claim sits on the box or the line it came from.",
        ],
        "ai": [
            "Text read from the image and claims read from it.",
            "Picture matches on the Copies tab.",
            "A location from the photo's metadata.",
        ],
        "you": [
            "The claims, the matches and the location, each in Review.",
            "The document type, if the suggestion is wrong.",
        ],
        "result": "A source with claims on the page, its copies and its custody "
        "record.",
        "links": [
            {
                "label": "Photos and scans in the demo case",
                "href": "/sources?type=image",
            },
            {"label": "Drop a file to see the duplicate check", "href": "/ingest/"},
        ],
        "needs": "A scanned PDF without a text layer is not read automatically yet.",
    },
    {
        "id": "research-the-web",
        "area": "bring",
        "title": "Capture a web page or a YouTube video",
        "who": "You want open sources about a person or a place already in the case.",
        "steps": [
            "Open Research the web on a person page, or Search the web on Add "
            "evidence.",
            "Pick or edit a proposed query. You see exactly what will be sent.",
            "Send. Before it leaves, an access record names you, the time, the case "
            "and the exact query.",
            "Results come grouped as pages, images, videos and YouTube, marked "
            "already in this case when a copy is held. A result that does not "
            "mention the subject sorts lower and says so.",
            "Add as evidence. A browser on this server visits the page once and "
            "keeps the page, a screenshot and a network log, each hashed. A YouTube "
            "video comes with its info and subtitles.",
        ],
        "ai": [
            "No model here: queries are built by rules from accepted or "
            "corroborated names, places and years from ordinary sources.",
        ],
        "you": [
            "Which query is sent and which results become evidence.",
            "Pages behind a login or a bot check are refused, never worked around.",
        ],
        "result": "Captured sources in your intake batch, each tied to the access "
        "record of the query that found it.",
        "links": [
            {"label": "Find a person to research", "href": "/people?q=Yousef"},
            {"label": "Search the web from Add evidence", "href": "/ingest/"},
        ],
        "needs": "Web search is off until BRAVE_API_KEY is set; YouTube search "
        "needs no key. Capture needs Chromium on the server.",
    },
    {
        "id": "read-the-intake-brief",
        "area": "agents",
        "title": "Read what a batch brought in",
        "who": "You, or the colleague who added a batch, once the worker finishes.",
        "steps": [
            "When a batch finishes, the linker reads it against the case, then a "
            "brief is written for the person who added it.",
            "The Desk shows the newest brief you have not read on top. Inbox has "
            "it as Intake finished.",
            "It says what arrived, who is new and who is known, where and when, "
            "what it connects to and what disagrees.",
            "Up to five next steps, each a link: review claims, decide links, "
            "confirm a place, open a finding.",
            "A line resting on a sensitive source you have not revealed says so "
            "and links to that source. Mark read and the panel goes.",
        ],
        "ai": [
            "The connections, the disagreements and the order of next steps. "
            "Counts come from the claims. A line without a valid citation is "
            "dropped.",
        ],
        "you": ["Everything the brief points at, in Review."],
        "result": "One page that tells you where to start on new evidence.",
        "links": [
            {"label": "Desk for the demo case", "href": "/desk"},
            {"label": "Inbox", "href": "/inbox"},
        ],
        "needs": AGENTS_OFF,
    },
    {
        "id": "search-a-case",
        "area": "find",
        "title": "Find a name, a word or a file",
        "who": "Anyone who needs one person, phrase or document now.",
        "steps": [
            "Type in the search box at the top, or press /.",
            "Exact identifiers come first: paste the start of a SHA-256 and that "
            "source leads.",
            "Names match across scripts and spellings: Yousef Khaled Al-Ali, "
            "يوسف خالد العلي and Yusuf al-Ali find one person.",
            "A passage opens where it is: the page of a PDF, the second of a "
            "recording.",
            "Leads by meaning follow the exact hits, labelled close, related or loose.",
        ],
        "ai": [
            "Leads by meaning across text, pictures, frames and sound, computed "
            "on this server."
        ],
        "you": [
            "Whether a lead matters. A lead is never accepted and search never "
            "changes a record."
        ],
        "result": "The source, the place in it, or the person, one click away.",
        "links": [
            {
                "label": "Search Yousef Khaled Al-Ali",
                "href": "/search?q=Yousef%20Khaled%20Al-Ali",
            },
            {"label": "Search Branch 999", "href": "/search?q=Branch%20999"},
        ],
        "needs": "Search by meaning needs the local embedding model; exact search "
        "works without it.",
    },
    {
        "id": "discover-connections",
        "area": "connect",
        "title": "See who was held together",
        "who": "You want people placed at one place in one month, across sources.",
        "steps": [
            "Open Discover. Findings are built from accepted or corroborated, dated "
            "claims at confirmed places.",
            "Each reads as a sentence, such as 4 people held at Branch 999 in July "
            "2013, with a strength band. It is never a probability.",
            "Open one: a card per person with every claim behind it and where in "
            "the source it sits.",
            "Accept it as a checklist: members, roles and an incident title. One "
            "passkey gesture signs it all.",
            "Or reject it with a reason. A rejected set is not proposed again.",
        ],
        "ai": [
            "No model here: shared presence and candidate incidents come from "
            "rules over dated, placed claims."
        ],
        "you": ["Who belongs, their roles, and whether it is an incident."],
        "result": "An incident with its people, place, dates and the statements it "
        "rests on, or a reasoned rejection.",
        "links": [
            {"label": "Discover in the demo case", "href": "/discover"},
            {"label": "Findings in Review", "href": "/review?box=findings"},
        ],
    },
    {
        "id": "links-the-rules-miss",
        "area": "connect",
        "title": "Find links the rules miss",
        "who": "A batch arrived, or you want a second read of one person.",
        "steps": [
            "After each batch the linker reads it against the case. Run it again "
            "with Find links on a person page or the evidence Intelligence panel.",
            "Open Review, All open. Each link reads as a question: Cellmates? "
            "Related documents? In this incident?",
            "The claims it rests on show as cards with the reason.",
            "Accept or reject. What is already proposed or decided is not proposed "
            "again; a rejected link returns only with a claim the rejection did "
            "not see.",
        ],
        "ai": [
            "Which candidate links hold and why. The candidates themselves come "
            "from rules: a name match plus an agreeing field, people named "
            "together and held together, shared people across documents.",
        ],
        "you": ["Every link. Nothing is accepted for you."],
        "result": "Same person, cellmate, family, incident and document links, "
        "each signed by a person.",
        "links": [{"label": "All open claims in Review", "href": "/review?box=open"}],
        "needs": AGENTS_OFF,
    },
    {
        "id": "review-a-claim",
        "area": "decide",
        "title": "Review a claim someone assigned you",
        "who": "A colleague asked you to check a claim, or work is due on you.",
        "steps": [
            "Open Review, Asked of me. Due items come first.",
            "Select one. The source opens at the span: the box on a scan, the line "
            "in a PDF, the second in a recording, the cell in a sheet.",
            "Read the checks in plain words, who proposed it and the other claims "
            "about the same person.",
            "If it rests on a sensitive source, show it first; that reveal is "
            "logged. Accept and Reject wait until then.",
            "Press a to accept or r to reject with a reason. Your passkey signs the "
            "rows shown. Undo is offered at once.",
            "Press e to correct: the corrected value goes to a colleague, and the "
            "original stays open until the correction is signed.",
        ],
        "ai": ["The claim and where it sits in the source."],
        "you": ["Accept, reject or correct. Only a person makes a fact."],
        "result": "A signed decision on the record, with Undo kept in My decision.",
        "links": [
            {"label": "Asked of me", "href": "/review?box=mine"},
            {"label": "Set up a passkey", "href": "/security"},
        ],
        "needs": "Deciding needs a passkey on your account. Without one, Review "
        "links you to set one up.",
    },
    {
        "id": "decide-together",
        "area": "decide",
        "title": "Ask a colleague and decide together",
        "who": "You are unsure, or a second reader should sign.",
        "steps": [
            "In Review press s to ask: pick a colleague and write the question.",
            "The thread continues in Inbox. Type @ and pick a name to mention someone.",
            "Assign work with a due date from Sources, one source or a selection.",
            "The bell and Inbox count what is unread. Opening an item marks it read.",
        ],
        "ai": ["Nothing. This is between people."],
        "you": ["Who to ask, what to assign, and when it is due."],
        "result": "A thread beside the claim, and work that shows on each "
        "colleague's Desk.",
        "links": [
            {"label": "Inbox", "href": "/inbox"},
            {"label": "Sources and who has what", "href": "/sources"},
        ],
        "needs": "Requests and comments are seen only by the people in the thread "
        "and administrators. Notifications carry titles and links only.",
    },
    {
        "id": "follow-over-time",
        "area": "connect",
        "title": "Follow a person, a place and an incident over time",
        "who": "You need the sequence: where someone was, and when.",
        "steps": [
            "Open Stories and pick a person, a place or an incident.",
            "Rows show each person's dated claims. Gaps longer than 30 days read "
            "as days with no evidence.",
            "The map numbers places in order. The lines show sequence, not routes.",
            "Pending findings show as hatched bands that open their checklist. "
            "Play runs the timeline and the map together.",
            "Open a place for its names, precision, people placed there, incidents "
            "and findings. An incident page shows its people by role and the "
            "history of the gesture that signed it.",
        ],
        "ai": ["Nothing new. A story only reads what is already proposed or decided."],
        "you": [
            "What to save, what to package, and which unresolved place names "
            "to confirm."
        ],
        "result": "A timeline and map you can save, or turn into a draft brief "
        "with Package.",
        "links": [
            {"label": "Stories", "href": "/stories"},
            {"label": "Places in the demo case", "href": "/places"},
        ],
    },
    {
        "id": "one-name-two-people",
        "area": "connect",
        "title": "Tell one name from two people",
        "who": "Two records share a name. Are they one person?",
        "steps": [
            "Open the person. Candidate duplicates lists other records with the "
            "fields that agree and differ.",
            "A name match that other fields contradict reads as likely two people.",
            "Same person proposals in Review compare both people field by field.",
            "Accept to join them, or mark them not the same. A not the same "
            "decision refuses a later same person link.",
        ],
        "ai": [
            "Same person proposals: a name match in any script plus at least "
            "one agreeing field."
        ],
        "you": ["Every join. Records are never merged automatically."],
        "result": "One person built from several records, or two people kept apart "
        "on purpose.",
        "links": [
            {"label": "People named Yousef", "href": "/people?q=Yousef"},
            {"label": "Open proposals in Review", "href": "/review?box=open"},
        ],
    },
    {
        "id": "watch-a-person",
        "area": "agents",
        "title": "Watch a person and keep a question open",
        "who": "A family is waiting for news of one person, or you track a theme.",
        "steps": [
            "Open Watching, find the person in any spelling and press Watch.",
            "When a new record names them, a same person proposal appears under "
            "them. A name alone never matches: a birth date, father's name or "
            "detention branch must be equal too.",
            "On the Desk, write a standing question under What should I watch for "
            "in this case? and press Watch for this.",
            "It checks again when evidence arrives and on a schedule. Check now "
            "runs it at once; Stop and Resume pause it.",
            "Matches show with their claims, masked as everywhere else. Past checks "
            "keep the history.",
        ],
        "ai": [
            "Standing question matches, each citing the claims behind it. The "
            "watch matcher uses rules only."
        ],
        "you": ["Whether a match is the person, in Review."],
        "result": "A watch that tells you when the record moves.",
        "links": [
            {"label": "Watching", "href": "/watch"},
            {"label": "Standing questions on the Desk", "href": "/desk"},
        ],
        "needs": "Standing questions need the local model route. A check that "
        "fails three times in a row pauses and tells you.",
    },
    {
        "id": "hunt-contradictions",
        "area": "agents",
        "title": "Find where sources disagree",
        "who": "Before you rely on a person's record.",
        "steps": [
            "Open a person. Sources disagree lists each open disagreement with both "
            "sides and their sources. Nothing there picks a side.",
            "Under it, press Look for contradictions to check the whole case.",
            "Each item explains the disagreement and drafts a question for a "
            "colleague, citing both sides. Copy question puts it on your "
            "clipboard; nothing is sent.",
            "Turn on Check again when evidence arrives to keep it running.",
        ],
        "ai": [
            "The explanation and the draft question. Candidates come from the "
            "claims; accepted claims rank first."
        ],
        "you": ["Which side holds, and whom to ask."],
        "result": "A list of disagreements with questions ready to send.",
        "links": [{"label": "People in the demo case", "href": "/people"}],
        "needs": AGENTS_OFF,
    },
    {
        "id": "find-gaps",
        "area": "agents",
        "title": "Find silences in a record",
        "who": "Someone was arrested and the record goes quiet.",
        "steps": [
            "Open People or a person and press Find gaps.",
            "A gap is more than 60 days with no evidence after an arrest, a "
            "detention or a missing account. A recorded release or death closes "
            "it.",
            "Each gap suggests places to look next: sources that put other people "
            "at the same place inside the gap, colleagues who worked on the "
            "claims, and searches.",
            "Search in research fills the research drawer. Only its Send button "
            "sends a search.",
        ],
        "ai": ["The suggestions, each citing the claims that bound the gap."],
        "you": ["Where to look, and whether to search."],
        "result": "A short list of the silences worth a look, longest first.",
        "links": [{"label": "People in the demo case", "href": "/people"}],
        "needs": AGENTS_OFF,
    },
    {
        "id": "connect-an-assistant",
        "area": "agents",
        "title": "Let your own assistant read and propose",
        "who": "You use an assistant that speaks MCP and want it to help.",
        "steps": [
            "Connect it and approve what it asks for on the consent page.",
            "It reads and searches, and proposes claims or same person links, "
            "on your behalf and under its own name.",
            "Every call is a sealed run with its own signing key.",
            "Connected agents lists it; Disconnect ends its access.",
        ],
        "ai": ["Proposals, which arrive in Review like any other."],
        "you": ["Every decision. No tool accepts, deletes or exports anything."],
        "result": "Help with reading, with every proposal on the record.",
        "links": [{"label": "Connected agents", "href": "/agents/"}],
    },
    {
        "id": "sensitive-sources",
        "area": "trust",
        "title": "Handle a sensitive source",
        "who": "A source is graphic, or identifies victims or witnesses.",
        "steps": [
            "Items arrive Sensitive by default. Choose ordinary only when it is.",
            "A sensitive source waits for one click, Show sensitive item, and that "
            "reveal is logged. Any other source shows at once and its first view "
            "in a session is logged.",
            "Names from a sensitive source stay masked in lists, Review and briefs "
            "until you reveal that source. You cannot sign a decision blind.",
            "The Metadata tab holds the custody chain: who acquired it, every run "
            "and every view.",
            "An administrator can erase what is known about a person, with a "
            "reason. Values go; the statements and the chain stay.",
        ],
        "ai": ["Nothing. Masking follows the source, not a guess."],
        "you": ["When to reveal, and when to erase."],
        "result": "A record of everyone who saw the source.",
        "links": [
            {
                "label": "Sensitive sources in the demo case",
                "href": "/sources?sensitive=1",
            }
        ],
        "needs": "Erasure hides values and blocks derived copies; it does not yet "
        "delete stored bytes from disk.",
    },
    {
        "id": "package-for-a-partner",
        "area": "share",
        "title": "Send findings to a partner",
        "who": "A family, a partner or a court needs what the records say.",
        "steps": [
            "Open Packages and write a brief. Every sentence cites accepted or "
            "corroborated claims; a sentence without that support cannot be "
            "saved. Package on a story starts one for you.",
            "Pick the audience. Family withholds review decisions and identity "
            "links and sends no files; partner withholds review decisions; court "
            "withholds nothing.",
            "Build it. Every cited entry must be covered by anchor receipts first.",
            "Two different people attest with their passkeys over the package's "
            "manifest hash.",
            "Send the download. The partner checks it offline, without Sealbook.",
        ],
        "ai": ["Nothing. A package holds only signed, cited statements."],
        "you": ["Every sentence, the audience, and who attests."],
        "result": "A package anyone can check: rows, chain, Merkle proofs, "
        "receipts and signatures.",
        "links": [{"label": "Packages", "href": "/packages"}],
        "needs": "The demo stops at the draft brief: building needs anchor "
        "receipts, made by stk anchor.",
    },
]

TRUST = [
    {
        "title": "Every row has a hash",
        "body": "A claim is stored as a row: who wrote it, about what, where in the "
        "source, when. Sealbook writes those fields in one fixed order and "
        "takes their SHA-256. Change any character and the row hash "
        "changes.",
    },
    {
        "title": "The value is sealed, not stored in the row",
        "body": "The row holds a commitment: the SHA-256 of a random salt and the "
        "value. The salt stops anyone guessing a name from its hash. "
        "Erasure deletes the salt and the value; the commitment stays, so "
        "the chain still checks.",
    },
    {
        "title": "Each entry links to the one before",
        "body": "The chain entry hashes the previous entry, its own number, its "
        "kind and the row hash. Rewrite one row and every entry after it "
        "stops matching.",
    },
    {
        "title": "Batches close into a Merkle tree",
        "body": "Each anchor closes a batch over every new entry and computes one "
        "root, as in RFC 6962. Proving one entry is in the batch takes a "
        "short path of sibling hashes, not the whole batch.",
    },
    {
        "title": "Roots are held outside",
        "body": "Each root goes to OpenTimestamps, which ties it to Bitcoin later, "
        "to an RFC 3161 time-stamp authority, and to copies partners hold. "
        "stk anchor --loop repeats every hour by default. A receipt fixes "
        "the root in time, so rewriting rows, chain and batches together "
        "is still caught.",
    },
    {
        "title": "Signatures say who",
        "body": "Every run gets a fresh Ed25519 key; its public half is sealed with "
        "the run and the private half never leaves the worker. Claims a run "
        "writes must carry its signature. People sign decisions with their "
        "passkey, and one gesture can sign every row it shows.",
    },
    {
        "title": "Anyone can check a package",
        "body": "python -m stk.ledger.verify_package checks a package with no "
        "database and no network: rows, values against commitments, chain "
        "links, Merkle paths, receipts, signatures and two attesters.",
    },
]
