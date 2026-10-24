// Animated diagrams of each mechanism, one per <figure data-diagram="name">. They use
// made-up values, not case.json, and loop only while on screen. With reduced motion
// each runs once and shows its end state.
(() => {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const NS = "http://www.w3.org/2000/svg";
  const wait = (ms) => new Promise((r) => setTimeout(r, reduced ? 0 : ms));
  const hex = () => Math.random().toString(16).slice(2, 8);
  const s = (tag, attrs, parent, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    if (parent) parent.append(n);
    return n;
  };
  // Plex Mono has no Arabic, so any line with Arabic is set in the Arabic face.
  const AR = /[\u0600-\u06ff]/;
  const txt = (p, x, y, text, cls = "", anchor = "start") =>
    s("text", { x, y, class: `${cls}${AR.test(text) ? " ar" : ""}`, "text-anchor": anchor }, p, text);
  const box = (p, x, y, w, h, text, cls = "", center = false) => {
    const g = s("g", { class: `bx ${cls}`, transform: `translate(${x},${y})` }, p);
    s("rect", { width: w, height: h, rx: 4 }, g);
    g.t = txt(g, center ? w / 2 : 10, h / 2 + 4, text, "", center ? "middle" : "start");
    return g;
  };
  const curve = (p, x1, y1, x2, y2, cls = "") => {
    const mx = (x1 + x2) / 2;
    return s("path", { class: `ln ${cls}`, pathLength: 1, d: `M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}` }, p);
  };
  const on = (...els) => els.forEach((e) => e.classList.add("in"));

  function scene(name, label, w, h, build) {
    const fig = document.querySelector(`[data-diagram="${name}"]`);
    if (!fig) return;
    const svg = s("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": label });
    const cap = document.createElement("figcaption");
    cap.setAttribute("aria-hidden", "true");
    fig.append(svg);
    const say = (t, tone = "") => { cap.textContent = t; cap.className = tone; };
    const pass = build(svg, say, fig);
    fig.append(cap);
    // Each pass starts from the classes the scene was built with.
    const built = [...svg.querySelectorAll("[class]")].map((n) => [n, n.getAttribute("class")]);
    let visible = false, wake = null;
    new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      if (visible && wake) { wake(); wake = null; }
    }).observe(fig);
    (async () => {
      do {
        if (!visible && !reduced) await new Promise((r) => (wake = r));
        for (const [n, c] of built) n.setAttribute("class", c);
        await pass();
      } while (!reduced);
    })();
  }

  // A file arrives
  scene("arrive", "A scanned register page is fingerprinted, its acquisition and content credentials recorded and its text read. The AI proposes claims pinned to page regions, and a person accepts one.", 720, 210, (svg, say) => {
    const file = box(svg, 10, 76, 150, 56, "register_page.png", "f");
    file.t.setAttribute("y", 26); txt(file, 10, 42, "scanned register", "lbl");
    const facts = ["sha-256  44677237…6d0d", "acquired  analyst-1, scanner-01", "credentials  no C2PA manifest", "text  Arabic, read"]
      .map((t, i) => [box(svg, 200, 14 + i * 48, 230, 32, t, "f"), curve(svg, 160, 104, 200, 30 + i * 48)]);
    const claims = ["region 1 · name · يوسف خالد العلي", "region 3 · mother · فاطمة", "region 4 · born · 1991"]
      .map((t, i) => [box(svg, 480, 34 + i * 56, 230, 34, t, "f dash"), curve(svg, 430, 174, 480, 51 + i * 56)]);
    const seal = txt(svg, 480, 24, "accepted by a reviewer, signed", "f g");
    return async () => {
      on(file); say("A file arrives.");
      await wait(900);
      for (const [b, l] of facts) { on(b, l); await wait(260); }
      say("Fingerprinted, how it was acquired recorded, content credentials checked, text read.");
      await wait(1800);
      for (const [b, l] of claims) { on(b, l); await wait(260); }
      say("The AI proposes claims, each pinned to the region of the page it came from. None is a fact yet.");
      await wait(2400);
      claims[0][0].classList.add("solid"); on(seal);
      say("A person accepts one. The others stay proposals.", "good");
      await wait(4200);
    };
  });

  // A messy sheet
  scene("sheet", "An intake spreadsheet is stored unread until a person names a field for every column. One cell holding several facts yields proposals pinned to its characters. A later version re-reads only added and changed rows.", 720, 236, (svg, say) => {
    const X = [60, 300, 380, 460], W = [240, 80, 80, 90], H = 28, Y = (r) => 8 + r * H;
    const fileHeads = ["الشخص / person", "المكان", "التاريخ", "مصدر المعلومة"], fields = ["name, sex, age, job", "place", "date", "source"];
    const rows = [["سامر خليل النجار - ذكر - 29 سنة - نجار", "حرستا", "2013-05-12", "اتصال هاتفي"],
      ["Rania Haddad (female), teacher, 41", "Branch 999", "15/3/2014", "family"],
      ["يوسف عمر الحلبي ١٧ سنة طالب", "دوما", "اذار 2013", ""]];
    X.forEach((x, c) => box(svg, x, Y(0), W[c], H, fileHeads[c]));
    const map = X.map((x, c) => box(svg, x, Y(1), W[c], H, "?", "dash"));
    txt(svg, 50, Y(1) + 18, "field", "lbl", "end");
    const cells = rows.map((row, r) => row.map((v, c) => box(svg, X[c], Y(r + 2), W[c], H, v)));
    const marks = rows.map((_, r) => txt(svg, 50, Y(r + 2) + 18, "", "lbl f", "end"));
    const added = ["ام سليم حوالي ستين سنة ربة منزل", "بابا عمرو", "شباط 2012", ""].map((v, c) => box(svg, X[c], Y(5), W[c], H, v, "f"));
    const addMark = txt(svg, 50, Y(5) + 18, "added", "lbl v f", "end");
    const facts = [["name · سامر خليل النجار", "A1, chars 1-16"], ["sex · ذكر", "A1, chars 20-22"], ["age · 29", "A1, chars 26-27"], ["job · نجار", "A1, chars 35-38"]]
      .map(([t, at], i) => {
        const b = box(svg, 570, Y(2) + i * 38 - 14, 145, 34, t, "f dash");
        b.t.setAttribute("y", 15); txt(b, 10, 28, at, "lbl");
        return [b, curve(svg, 550, Y(2) + 14, 570, Y(2) + i * 38 + 3)];
      });
    return async () => {
      map.forEach((b) => (b.t.textContent = "?"));
      cells[2][3].t.textContent = "";
      say("The sheet is stored as evidence. Nothing is read yet.");
      await wait(1600);
      for (const [c, b] of map.entries()) { b.t.textContent = fields[c]; b.classList.add("solid"); await wait(380); }
      say("A person names a field for every column.");
      await wait(1400);
      cells[0][0].classList.add("on");
      for (const [b, l] of facts) { on(b, l); await wait(260); }
      say("Each row is read. One cell holds four facts, and each comes back as a proposal pinned to its characters.");
      await wait(3000);
      facts.forEach(([b, l]) => { b.classList.add("gone"); l.classList.remove("in"); }); cells[0][0].classList.remove("on");
      marks.forEach((m, r) => { m.textContent = r === 2 ? "changed" : "same"; m.classList.toggle("v", r === 2); on(m); });
      cells[2][3].t.textContent = "family"; cells[2].forEach((b) => b.classList.add("on"));
      on(...added, addMark); added.forEach((b) => b.classList.add("on"));
      say("A later version is compared row by row first. Only added and changed rows are read again.");
      await wait(4200);
    };
  });

  // A batch, read as a whole
  scene("brief", "Two local agents read a finished batch against the accepted case facts. An uncited sentence is dropped and a proposed link goes to Review as a question.", 720, 240, (svg, say) => {
    const names = ["Dormitory 4 roster, July 2013", "Released detainee statement"];
    const files = names.map((n, i) => box(svg, 10, 40 + i * 44, 210, 30, n, "f"));
    const ctx = box(svg, 10, 190, 210, 30, "accepted case facts", "f v");
    const brief = box(svg, 250, 40, 110, 40, "intake brief", "f", true), linker = box(svg, 250, 160, 110, 40, "linker", "f", true);
    const local = [txt(svg, 305, 96, "local model", "lbl f", "middle"), txt(svg, 305, 216, "local model", "lbl f", "middle")];
    const edges = [...names.flatMap((_, i) => [curve(svg, 220, 55 + i * 44, 250, 60), curve(svg, 220, 55 + i * 44, 250, 180)]),
      curve(svg, 220, 205, 250, 60, "v"), curve(svg, 220, 205, 250, 180, "v")];
    const lines = [["Yousef Khaled Al-Ali, already known  [name]", ""], ["Bassel Nader, new to the case  [name]", ""],
      ["probably the man in the witness clip", ""], ["next: review Branch 999, July 2013 →", "v"]]
      .map(([t, c], i) => txt(svg, 400, 30 + i * 28, t, `f ${c}`));
    const drop = txt(svg, 400, 30 + 2 * 28 + 14, "no citation, dropped", "lbl r f");
    const link = box(svg, 400, 150, 310, 60, "same person in the roster and the statement?", "f dash");
    link.t.setAttribute("y", 24); txt(link, 10, 44, "rests on 2 name claims · goes to Review", "lbl");
    return async () => {
      lines[2].classList.remove("r");
      for (const f of files) { on(f); await wait(160); }
      on(ctx); say("A batch finishes.");
      await wait(1000);
      on(brief, linker, ...edges, ...local);
      say("Two agents read it against the rest of the case, on a local model.");
      await wait(1800);
      for (const l of lines) { on(l); await wait(380); }
      say("The intake brief cites a claim for every sentence.");
      await wait(1400);
      lines[2].classList.add("strike", "r"); on(drop);
      say("A sentence the model writes without a citation is dropped.", "bad");
      await wait(2000);
      lines[2].classList.add("gone"); on(link);
      say("The linker proposes links the claims support. Each reaches Review as a question, with the claims it rests on.");
      await wait(4200);
    };
  });

  // One search, grouped
  scene("search", "A Latin-script query returns exact matches in either script first, then leads by meaning labelled close, related or loose. A lead far below the best match is left out.", 720, 260, (svg, say) => {
    const q = box(svg, 10, 14, 200, 34, "", "v");
    const rows = [["exact", "person", "يوسف خالد العلي", "21 facts", 1, ""], ["exact", "person", "Yousef Khaled Al-Ali", "2 facts", 1, ""],
      ["exact", "passage", "Dormitory 4 roster, July 2013", "line 1", 1, ""], ["exact", "audio", "Witness clip, recorder 04", "at 00:09", 1, ""],
      ["close", "person", "يوسف عمر الحلبي", "", 0.74, "v"], ["related", "place", "الفرع 999", "", 0.58, "v"],
      ["loose", "finding", "Shared presence, July 2013", "", 0.41, "v"], ["loose", "passage", "Documentation note 14/2014", "", 0.12, "r"]];
    const Yr = (i) => 26 + i * 26 + (i > 3 ? 26 : 0);
    const div = [s("path", { class: "ln", pathLength: 1, d: `M240 ${Yr(4) - 20} H710` }, svg), txt(svg, 240, Yr(4) - 26, "leads by meaning", "lbl f")];
    const items = rows.map(([k, kind, t, meta, v, c], i) => {
      const g = s("g", { class: "f" }, svg);
      txt(g, 240, Yr(i), k, `lbl ${i > 3 ? "v" : ""}`); txt(g, 300, Yr(i), kind, "lbl");
      const label = txt(g, 360, Yr(i), t); txt(g, 600, Yr(i), meta, "lbl", "end");
      const bar = s("rect", { class: `bar ${c}`, x: 620, y: Yr(i) - 8, width: 0, height: 6, rx: 1 }, g);
      return { g, label, bar, w: 90 * v };
    });
    const out = txt(svg, 710, Yr(7) + 16, "far below the best match, left out", "lbl r f", "end");
    const opens = txt(svg, 10, 72, "each result opens at its", "lbl f"), opens2 = txt(svg, 10, 86, "character, page or second", "lbl f");
    return async () => {
      q.t.textContent = "";
      items.forEach((it) => it.bar.setAttribute("width", 0));
      for (const ch of "yousef khaled al-ali") { q.t.textContent += ch; await wait(60); }
      await wait(500);
      for (const it of items.slice(0, 4)) { on(it.g); it.bar.setAttribute("width", it.w); await wait(250); }
      say("Exact matches first: a name in any recorded spelling, in either script.");
      await wait(2200);
      on(...div);
      for (const it of items.slice(4)) { on(it.g); it.bar.setAttribute("width", it.w); await wait(300); }
      say("Then leads by meaning, labelled close, related or loose. A similar name is a lead, not a match.");
      await wait(2000);
      items[7].g.classList.add("gone"); items[7].label.classList.add("strike"); on(out, opens, opens2);
      say("A lead far below the best match is left out.");
      await wait(4200);
    };
  });

  // Speech, located to the second: the real transcript of the synthetic witness clip
  scene("speech", "The witness clip's waveform with a playhead. The transcript line under the playhead is highlighted with its start time.", 720, 90, (svg, say, fig) => {
    const DUR = 33.7, BARS = 144, bw = 720 / BARS;
    const lines = [[0, 8.54, "اسمي سامر انا من دومة اعتقلوني بربيع الالفين وتلتعش وضليت محجوز كم شهر"],
      [9.94, 18.12, "بشهر تموز من سنة الالفين وتلتعش شفت يوسف خالد العلي بالفرع تسعمية وتسع وتسعين"],
      [19.37, 26.59, "كلي انه اعتقلوه على حاجز بحرسته وانه اهله ما بيعرفوا وينه"],
      [26.59, 32.61, "انا مستعد اشهد باللي شفته جدام اي جهة"]];
    const fmt = (t) => `00:${String(Math.floor(t)).padStart(2, "0")}.${Math.round(t * 10) % 10}`;
    const list = document.createElement("ol");
    list.className = "lines"; list.setAttribute("aria-hidden", "true");
    const items = lines.map(([a, , t]) => {
      const li = document.createElement("li"), b = document.createElement("b"), sp = document.createElement("span");
      b.textContent = fmt(a); sp.textContent = t; sp.lang = "ar"; sp.dir = "rtl"; li.append(b, sp);
      return list.appendChild(li);
    });
    fig.append(list);
    const bars = Array.from({ length: BARS }, (_, i) => {
      const t = (i + 0.5) / BARS * DUR, talk = lines.some(([a, b]) => t >= a && t <= b);
      const h = talk ? 14 + Math.random() * 50 * Math.abs(Math.sin(i * 0.7)) + Math.random() * 12 : 3 + Math.random() * 6;
      return { r: s("rect", { class: `wv${talk ? " seg" : ""}`, x: i * bw + 0.6, y: 45 - h / 2, width: bw - 1.2, height: h, rx: 1 }, svg), t };
    });
    const head = s("line", { class: "playhead", y1: 0, y2: 90 }, svg);
    const render = (t) => {
      head.setAttribute("x1", t / DUR * 720); head.setAttribute("x2", t / DUR * 720);
      const cur = lines.findIndex(([a, b]) => t >= a && t < b);
      items.forEach((li, k) => li.classList.toggle("now", k === cur));
      bars.forEach(({ r, t: bt }) => r.classList.toggle("now", cur >= 0 && bt >= lines[cur][0] && bt <= t));
    };
    say("Each transcript line keeps its start and end time, so a claim drawn from it opens the recording at that second.");
    return () => {
      if (reduced) return render(14);
      return new Promise((done) => {
        const t0 = performance.now();
        const tick = (now) => {
          const t = (now - t0) / 1000;
          if (t >= DUR) return done();
          render(t); requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    };
  });

  // Same footage, different sources: the case's PDQ distances against the app's threshold
  scene("dupes", "PDQ picture fingerprints of four copies are compared with the original. The re-encoded and half-size copies fall under the threshold of 31; both crops do not.", 720, 250, (svg, say) => {
    const N = 256, THR = 31, base = Array.from({ length: N }, () => Math.random() < 0.45);
    const pick = (k) => [...Array(N).keys()].sort(() => Math.random() - 0.5).slice(0, k);
    const copies = [["original", []], ["re-encoded", pick(4)], ["half size", pick(12)], ["cropped 3%", pick(60)], ["cropped 8%", pick(118)]];
    const grids = copies.map(([name, flips], c) => {
      const x0 = 20 + c * 142;
      txt(svg, x0, 14, name, c ? "" : "hd");
      return [...Array(N).keys()].map((k) => s("rect", { class: `bit${base[k] !== flips.includes(k) ? " one" : ""}`, x: x0 + (k % 16) * 5.5, y: 24 + Math.floor(k / 16) * 5.5, width: 4.6, height: 4.6 }, svg));
    });
    const sx = (d) => 30 + d * 5.2, Y = 172;
    s("line", { class: "ed", x1: sx(0), x2: sx(128), y1: Y, y2: Y }, svg);
    [0, 32, 64, 96, 128].forEach((d) => txt(svg, sx(d), Y + 32, String(d), "lbl", "middle"));
    txt(svg, sx(128), Y + 48, "bits that differ, of 256", "lbl", "end");
    s("line", { class: "ed on", x1: sx(THR), x2: sx(THR), y1: Y - 40, y2: Y + 16, "stroke-dasharray": "3 3" }, svg);
    txt(svg, sx(THR) + 6, Y - 32, "threshold 31", "lbl v");
    const dots = copies.slice(1).map(([name, flips], i) => {
      const d = flips.length, hit = d <= THR;
      const g = s("g", { class: "f slide" }, svg);
      s("circle", { class: `dot ${hit ? "v" : "bad"}`, cx: 0, cy: Y, r: 6 }, g);
      txt(g, 0, i % 2 ? Y + 20 : Y - 12, `${d}`, `lbl ${hit ? "v" : "r"}`, "middle");
      return { g, d, flips, c: i + 1 };
    });
    return async () => {
      dots.forEach(({ g }) => (g.style.transform = `translateX(${sx(0)}px)`));
      say("Each copy's picture fingerprint is compared with the original's, bit by bit.");
      await wait(1400);
      for (const { g, d, flips, c } of dots) {
        flips.forEach((k) => grids[c][k].classList.add("bad"));
        on(g); await wait(60); g.style.transform = `translateX(${sx(d)}px)`;
        await wait(1100);
      }
      say("Re-encoded and half-size copies fall under the threshold. A crop shifts every block and is not caught. A distance is not a probability, and a person confirms every link.");
      await wait(5200);
    };
  });

  // One street, two years apart: the trimmed and cropped copies of the 2015 clip
  scene("video", "A trimmed copy of the 2015 clip matches the original frame by frame and by sound, starting 4 seconds in. A cropped copy fails by picture while its soundtrack still matches.", 720, 200, (svg, say) => {
    const F = 10, M = 6, step = 66, fw = 58, fh = 34, X0 = 20;
    const bits = Array.from({ length: F }, () => Array.from({ length: 24 }, () => Math.random() < 0.45));
    const marks = Array.from({ length: F }, () => [[Math.random() * 50, Math.random() * 16], [Math.random() * 50, Math.random() * 16]]);
    const strip = (g, y, idx, above, scrambled) => idx.map((o, i) => {
      const x = i * step;
      const fr = s("rect", { class: "frame", x, y, width: fw, height: fh, rx: 3 }, g);
      bits[o].forEach((one, k) => s("rect", { class: `bit${(scrambled ? (k * 7 + o) % 3 === 0 : one) ? " one" : ""}`, x: x + 4 + (k % 8) * 6.4, y: y + 4 + Math.floor(k / 8) * 9, width: 5.4, height: 8, rx: 1 }, g));
      marks[o].forEach(([mx, my]) => s("circle", { class: "mk", cx: x + 4 + mx, cy: above ? y - 24 + my : y + fh + 8 + my, r: 2.5 }, g));
      return fr;
    });
    txt(svg, X0, 14, "original, 2015 clip", "lbl");
    const A = s("g", { transform: `translate(${X0},0)` }, svg);
    const fa = strip(A, 22, [...Array(F).keys()], false, false);
    const modes = [{ off: 4, crop: false, label: "copy: trimmed to start 4 seconds in" }, { off: 0, crop: true, label: "copy: cropped to the central 75%" }].map((m) => {
      const g = s("g", { class: "f slide" }, svg);
      const idx = Array.from({ length: M }, (_, j) => j + m.off);
      const fb = strip(g, 134, idx, true, m.crop);
      const lines = idx.map((o) => {
        const x = X0 + o * step + fw / 2;
        const [mx, my] = marks[o][0], lx = X0 + o * step + 4 + mx;
        return [s("path", { class: "ln v", pathLength: 1, d: `M${x} ${22 + fh} V134` }, svg), s("path", { class: "ln v", pathLength: 1, d: `M${lx} ${64 + my} V${110 + my}` }, svg)];
      });
      return { ...m, g, fb, lines };
    });
    const label = txt(svg, X0, 190, "", "lbl");
    let n = 0;
    return async () => {
      const m = modes[reduced ? 0 : n++ % 2];
      label.textContent = m.label;
      m.g.style.transition = "none"; m.g.style.transform = `translate(${X0 + (m.off + 3) * step}px,0)`; m.g.getBBox();
      m.g.style.transition = ""; on(m.g);
      say("A frame is sampled every second and fingerprinted, and the sound is reduced to landmarks.");
      await wait(900);
      m.g.style.transform = `translate(${X0 + m.off * step}px,0)`;
      await wait(1700);
      for (const [j, [pic, snd]] of m.lines.entries()) {
        fa[j + m.off].classList.add(m.crop ? "bad" : "on"); m.fb[j].classList.add(m.crop ? "bad" : "on");
        if (!m.crop) on(pic);
        on(snd); await wait(140);
      }
      say(m.crop ? "Cropped: every block of the picture moves, so no frame matches. The unchanged soundtrack still does." : "Trimmed: frames and sound both match, and the copy is placed 4 seconds into the original.", m.crop ? "bad" : "good");
      await wait(4200);
    };
  });

  // One name, two people: P1 and P2 of the case
  scene("people", "Two records carry exactly the same name and father's name, but their mothers, birth years and birthplaces differ, so they stay two people unless a reviewer decides otherwise.", 720, 250, (svg, say) => {
    const fields = [["name", "يوسف خالد العلي", "يوسف خالد العلي", "identical", 1], ["father", "خالد", "خالد", "identical", 1],
      ["mother", "فاطمة", "سعاد", "differ", 0], ["born", "1991", "1985", "6 years apart", 0], ["birthplace", "دوما", "حمص", "differ", 0]];
    const card = (x, title) => { const g = s("g", { class: "f" }, svg); s("rect", { class: "frame", x, y: 6, width: 230, height: 184, rx: 6 }, g); txt(g, x + 12, 28, title, "hd"); return g; };
    const L = card(10, "person 1, the missing man"), R = card(480, "person 2");
    fields.forEach(([f, a, b], i) => {
      const y = 58 + i * 28;
      txt(L, 22, y, f, "lbl"); txt(L, 100, y, a); txt(R, 492, y, f, "lbl"); txt(R, 570, y, b);
    });
    const cmp = fields.map(([, , , why, same], i) => {
      const y = 54 + i * 28;
      return [s("path", { class: `ln ${same ? "v" : "r"}`, pathLength: 1, d: `M240 ${y} H480` }, svg), txt(svg, 360, y - 6, why, `f ${same ? "v" : "r"}`, "middle")];
    });
    const verdict = box(svg, 160, 206, 400, 36, "two people · similar names are never merged on their own", "f dash", true);
    return async () => {
      on(L, R); say("Two records with exactly the same name.");
      await wait(1200);
      for (const [l, t] of cmp) { on(l, t); await wait(600); }
      say("Name and father's name are identical. Mother, birth year and birthplace are not.");
      await wait(1600);
      on(verdict);
      say("Identity candidates are proposed with what supports them. Every side stays listed until a reviewer decides.");
      await wait(4200);
    };
  });

  // Connections, with their reasons: the case's July 2013 finding
  scene("connections", "Accepted, dated and placed claims on a month and place grid. Four people from three sources at Branch 999 in July 2013 become a proposed finding with its reason and a strength band.", 720, 220, (svg, say) => {
    const months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep"], places = ["Branch 999", "Harasta", "Central Prison K"];
    const mx = (m) => 160 + m * 50, py = (p) => 54 + p * 56;
    months.forEach((m, i) => txt(svg, mx(i), 18, m, "lbl", "middle"));
    txt(svg, mx(5) + 30, 18, "2013", "lbl");
    places.forEach((p, i) => { txt(svg, 10, py(i) + 4, p, "lbl"); s("line", { class: "ed", x1: 130, x2: 440, y1: py(i), y2: py(i) }, svg); });
    const claims = [[1, 1, "A"], [1, 0, "B"], [3, 0, "A", -21], [3, 0, "A", -7], [3, 0, "B", 7], [3, 0, "C", 21], [5, 0, "B"], [5, 2, "D"], [2, 1, "E"]];
    const dots = claims.map(([m, p, src, dx = 0]) => {
      const g = s("g", { class: "f" }, svg);
      s("circle", { class: "dot", cx: mx(m) + dx, cy: py(p), r: 7 }, g);
      txt(g, mx(m) + dx, py(p) + 4, src, "lbl", "middle");
      return g;
    });
    const cluster = s("rect", { class: "ln v", pathLength: 1, x: mx(3) - 34, y: py(0) - 16, width: 68, height: 32, rx: 10 }, svg);
    const card = s("g", { class: "f" }, svg);
    s("rect", { class: "frame on", x: 470, y: 30, width: 240, height: 112, rx: 6 }, card);
    txt(card, 482, 52, "shared presence", "lbl v");
    ["4 people held at Branch 999", "in July 2013", "3 sources, overlapping 31 days"].forEach((t, i) => txt(card, 482, 74 + i * 18, t));
    txt(card, 482, 130, "strength: strong · not a probability", "lbl v");
    const link = curve(svg, mx(3) + 34, py(0), 470, 86, "v");
    return async () => {
      for (const d of dots) { on(d); await wait(160); }
      say("Discovery reads claims that are accepted or corroborated, dated and placed. Letters are their sources.");
      await wait(1800);
      on(cluster); await wait(600); on(link, card);
      say("Four people from three independent sources at one place in one month: a proposed finding, with its reason in one sentence and a strength band.");
      await wait(4800);
    };
  });

  // A page for every place and incident: Branch 999
  scene("places", "The Branch 999 page gathers its names in each script, its precision and position source, the people placed there, its incident, open findings and sources.", 720, 250, (svg, say) => {
    const cx = 360, cy = 116;
    s("circle", { class: "ring", cx, cy, r: 78 }, svg);
    txt(svg, cx, cy + 94, "±250 m, position set by an analyst", "lbl", "middle");
    const hub = box(svg, cx - 70, cy - 24, 140, 48, "Branch 999", "v", true);
    hub.t.setAttribute("y", 20); txt(hub, 70, 38, "الفرع 999", "v", "middle");
    const spokes = [[10, 20, "2 spellings, both gazetteer"], [10, 103, "11 people, 2013 to 2015"], [10, 186, "18 claims, 9 sources"],
      [510, 20, "incident, March 2014"], [510, 103, "finding, July 2013, strong"], [510, 186, "kind: facility"]]
      .map(([x, y, t]) => { const left = x < cx; return [box(svg, x, y, 200, 28, t, "f"), curve(svg, left ? 210 : 510, y + 14, left ? cx - 70 : cx + 70, cy, "v")]; });
    const queue = box(svg, cx - 120, 220, 240, 28, "unmatched names wait here", "f dash", true);
    return async () => {
      say("Each place has a page built from the evidence.");
      await wait(1000);
      for (const [b, l] of spokes) { on(b, l); await wait(380); }
      say("Its names in each script, how precisely it is known and where that came from, the people placed there, its incident, findings and sources.");
      await wait(2800);
      on(queue);
      say("Names the gazetteer cannot match wait in a list for an analyst to confirm.");
      await wait(4200);
    };
  });

  // A person over time: Yousef Khaled Al-Ali in 2013
  scene("story", "Yousef Khaled Al-Ali's accepted claims in 2013: two neighbouring places on one day that do not fork, gaps of 60 and 78 days without evidence, a transfer, and a hatched band for a finding awaiting review.", 720, 210, (svg, say) => {
    const defs = s("defs", {}, svg);
    const pat = s("pattern", { id: "dg-hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
    s("line", { class: "hatch-line", x1: 0, y1: 0, x2: 0, y2: 6 }, pat);
    const dx = (m, d) => 40 + ((m - 4) + (d - 1) / 30) * 105, Y = 120;
    ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"].forEach((m, i) => txt(svg, dx(4 + i, 1), 200, m, "lbl", "middle"));
    txt(svg, dx(10, 1) + 24, 200, "2013", "lbl");
    const band = s("g", { class: "f" }, svg);
    s("rect", { x: dx(7, 1), y: Y - 74, width: dx(7, 31) - dx(7, 1), height: 30, fill: "url(#dg-hatch)", rx: 3 }, band);
    txt(band, dx(7, 1), Y - 80, "shared presence, awaiting review", "lbl v");
    const may = dx(5, 2), jul = dx(7, 1), sep = dx(9, 17);
    const split = [s("path", { class: "ln", pathLength: 1, d: `M${may - 30} ${Y} C${may - 15} ${Y} ${may - 15} ${Y - 14} ${may} ${Y - 14} C${may + 15} ${Y - 14} ${may + 15} ${Y} ${may + 30} ${Y}` }, svg),
      s("path", { class: "ln", pathLength: 1, d: `M${may - 30} ${Y} C${may - 15} ${Y} ${may - 15} ${Y + 14} ${may} ${Y + 14} C${may + 15} ${Y + 14} ${may + 15} ${Y} ${may + 30} ${Y}` }, svg)];
    const near = [txt(svg, may, Y - 22, "حرستا", "lbl", "middle"), txt(svg, may, Y + 30, "دوما", "lbl", "middle"),
      txt(svg, may, Y + 46, "one day, two neighbours: within precision, no fork", "lbl f", "middle")];
    near.slice(0, 2).forEach((t) => t.classList.add("f"));
    const gaps = [[may + 30, jul, "60 days, no evidence"], [jul, sep, "78 days, no evidence"]].map(([a, b, t]) =>
      [s("path", { class: "ed f", d: `M${a} ${Y} H${b}`, "stroke-dasharray": "4 4" }, svg), txt(svg, (a + b) / 2, Y - 10, t, "lbl f", "middle")]);
    const ev = (x, t) => { const g = s("g", { class: "f" }, svg); s("circle", { class: "dot", cx: x, cy: Y, r: 5 }, g); txt(g, x, Y + 22, t, "lbl", "middle"); return g; };
    const pJul = ev(jul, "الفرع 999"), pSep = ev(sep, "الفرع 999");
    const transfer = s("path", { class: "ln", pathLength: 1, d: `M${sep} ${Y} H${sep + 60}` }, svg);
    const pK = ev(sep + 60, ""), kT = txt(svg, sep + 60, Y - 12, "سجن ك المركزي", "lbl f", "middle"), trT = txt(svg, sep + 30, Y + 40, "transfer, 17 Sep", "lbl f", "middle");
    return async () => {
      say("Yousef Khaled Al-Ali's accepted claims, in time order.");
      on(...split, near[0], near[1]);
      await wait(1200);
      on(near[2]);
      say("Two places on one day fork only when they are farther apart than their stated precision. Harasta and Douma are neighbours.");
      await wait(2600);
      on(...gaps[0]); await wait(300); on(pJul); await wait(400); on(...gaps[1]); await wait(300); on(pSep);
      say("A stretch of more than a month with no evidence is labelled as a gap.");
      await wait(2000);
      on(transfer, pK, kT, trT); await wait(800);
      on(band); say("A finding still waiting for review appears as a hatched band.");
      await wait(4200);
    };
  });

  // Web research that keeps custody: Lina Omar al-Najjar
  scene("research", "An analyst edits a query built only from accepted values. An access record is written before it leaves. A chosen result is captured locally, hashed and enters intake. A case marked sensitive sends nothing.", 720, 236, (svg, say) => {
    const vals = box(svg, 10, 14, 160, 60, "accepted values", "f");
    vals.t.setAttribute("y", 20); txt(vals, 10, 36, "name, place, year", "lbl"); txt(vals, 10, 50, "not: 5 unconfirmed", "lbl r");
    const query = box(svg, 200, 28, 220, 34, "", "f v");
    query.t.classList.add("ar");
    const ledger = box(svg, 200, 100, 220, 34, "", "f");
    const toQ = curve(svg, 170, 45, 200, 45), toL = curve(svg, 310, 62, 310, 100), toW = curve(svg, 420, 45, 450, 45);
    const web = box(svg, 450, 28, 90, 34, "open web", "f", true);
    const res = ["web    2 results", "image  already held", "video  1 result"].map((t, i) => box(svg, 560, 12 + i * 30, 150, 24, t, `f ${i === 1 ? "v" : ""}`));
    const cap = ["address", "status", "page", "shot", "log"].map((t, i) => box(svg, 300 + i * 82, 160, 74, 26, `# ${t}`, "f", true));
    const toC = curve(svg, 635, 102, 520, 160);
    const intake = box(svg, 300, 200, 402, 28, "intake, as the analyst's own acquisition", "f solid", true);
    let n = 0;
    return async () => {
      const sensitive = !reduced && n++ % 2 === 1;
      query.t.textContent = "لينا عمر النجار";
      ledger.t.textContent = sensitive ? "case marked sensitive" : "ledger: access record";
      on(vals, toQ, query);
      say("A query is proposed only from accepted or corroborated values, gazetteer names and years.");
      await wait(1600);
      query.t.textContent = "لينا عمر النجار 2014";
      say("The analyst edits it and sees exactly what will be sent.");
      await wait(1400);
      on(toL, ledger);
      if (sensitive) {
        ledger.classList.add("bad");
        say("This case is marked sensitive, so nothing from it is sent to a search or rendering service.", "bad");
        await wait(4200);
        return;
      }
      ledger.classList.add("ok");
      say("The access record is written to the ledger before the query leaves.", "good");
      await wait(1600);
      on(toW, web); await wait(500);
      for (const r of res) { on(r); await wait(220); }
      say("Results come back with domain, date and reason, flagged when the case already holds them.");
      await wait(1800);
      on(toC); for (const c of cap) { on(c); await wait(160); }
      await wait(300); on(intake);
      say("A local headless browser captures the page once. Each file is hashed and enters intake, so no third party sits in the chain of custody.");
      await wait(4200);
    };
  });

  // Review together
  scene("review", "A claim is sent to a colleague, who replies in a private discussion. The decision lists the rows a passkey will sign, the claim is accepted, and a signed undo restores what came before.", 720, 230, (svg, say) => {
    const claim = box(svg, 10, 30, 210, 34, "arrested at · حرستا", "f dash");
    const ask = box(svg, 250, 14, 210, 30, "@analyst-2 does row 1 agree?", "f");
    const reply = box(svg, 270, 54, 190, 30, "yes, and the sheet agrees", "f");
    const priv = txt(svg, 250, 102, "private to the people in it", "lbl f");
    const toAsk = curve(svg, 220, 47, 250, 29);
    const panel = s("g", { class: "f" }, svg);
    s("rect", { class: "frame", x: 490, y: 10, width: 220, height: 118, rx: 6 }, panel);
    txt(panel, 502, 30, "the passkey will sign", "lbl");
    const rows = ["claim → accepted", "tier → corroborated", "statement for the ledger"].map((t, i) => txt(panel, 502, 54 + i * 22, t));
    const signed = box(svg, 490, 140, 220, 30, "signed with a passkey", "f ok", true);
    const undo = box(svg, 490, 184, 220, 30, "signed undo", "f v", true);
    return async () => {
      on(claim); say("A proposed claim.");
      await wait(1000);
      on(toAsk, ask); await wait(900); on(reply, priv);
      say("Ask a colleague. Requests and replies arrive in one inbox, and the discussion stays out of history and exports.");
      await wait(2400);
      on(panel); say("Each decision shows the rows the passkey will sign before it asks.");
      await wait(1800);
      on(signed); claim.classList.add("solid");
      say("Signed. The claim is accepted.", "good");
      await wait(2000);
      on(undo); rows.forEach((r) => r.classList.add("strike")); claim.classList.remove("solid");
      say("A signed undo restores what came before.");
      await wait(4200);
    };
  });

  // Proof: one statement edited changes every hash up to the batch root
  scene("proof", "Eight statements hash up to one batch root. Editing one statement changes every hash on its path to the root.", 720, 250, (svg, say) => {
    const levels = [8, 4, 2, 1], ys = [196, 132, 72, 16], W = 72, H = 26;
    const xs = (n) => Array.from({ length: n }, (_, i) => (720 / n) * (i + 0.5));
    const nodes = levels.map(() => []), edges = levels.map(() => []);
    levels.forEach((n, l) => xs(n).forEach((x, i) => {
      if (l) for (const c of [2 * i, 2 * i + 1]) {
        const cx = xs(levels[l - 1])[c];
        edges[l - 1][c] = s("line", { class: "ed", x1: cx, y1: ys[l - 1], x2: x, y2: ys[l] + H }, svg);
      }
    }));
    levels.forEach((n, l) => xs(n).forEach((x, i) => {
      nodes[l][i] = box(svg, x - W / 2, ys[l], W, H, "", l === 3 ? "v" : "", true);
      if (!l) txt(svg, x, ys[0] + H + 16, `statement ${i + 1}`, "lbl", "middle");
    }));
    txt(svg, 360 + W / 2 + 10, 33, "batch root", "lbl");
    const scramble = async (t) => { for (let k = 0; k < 6; k++) { t.textContent = hex(); await wait(45); } };
    return async () => {
      nodes.forEach((lv) => lv.forEach((n) => (n.t.textContent = hex())));
      say(`Root ${nodes[3][0].t.textContent} verifies.`, "good");
      await wait(2200);
      let i = 3 + Math.floor(Math.random() * 2);
      for (let l = 0; l < 4; l++) {
        nodes[l][i].classList.add("bad");
        await scramble(nodes[l][i].t);
        if (l < 3) edges[l][i].classList.add("bad");
        if (!l) say(`Statement ${i + 1} edited.`, "bad");
        i >>= 1;
        await wait(260);
      }
      say("The root changed, so the edit shows against every timestamp receipt and partner copy.", "bad");
      await wait(4000);
    };
  });
})();
