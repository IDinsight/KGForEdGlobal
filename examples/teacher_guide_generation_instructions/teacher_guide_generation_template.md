# Teacher Guide Template — Senegal Grade 3 Reading (CE1)

This template generates a complete bilingual reading teacher guide for **a regular instructional week** of CE1 *Langue et Communication*. It is week-agnostic. Pair it with a per-week brief (`semaine_{N}_brief.md`) that supplies week-specific facts: topic divergences, L1 fallback content, transfer pairs, and content-fidelity spot-check items.

This template is **not** intended for integration weeks, revision weeks, or evaluation weeks. Those are separate document types.

---

## How to use this template

1. Read the target week's brief.
2. Read this template top-to-bottom.
3. Run the KG Evidence Pass per §2.5 (Pass 0).
4. Draft pedagogical content per §6.3 (Pass 1).
5. Format the DOCX per §6.3 (Pass 2).
6. Run the final QA gate per §16.

The brief plugs in at seven points: §2.3 (divergences), §2.4 (skip-decomposition flags), §3 (palier framing bridge — verbatim Wolof + French phrasing), §4 (content map for L1 fallback / Mots Fréquents / CGP / pedagogical constraints), §9 (required transfer pairs), §16 (content-fidelity spot-check), Appendix A (week-specific error calibration examples).

---

## 1. Task

Generate the complete teacher guide for **Semaine {N}**, covering **Jour 1 through Jour 5**, where {N} is the week number specified in the brief.

The guide must be classroom-usable, bilingual where appropriate, visually scannable, and concise. The full DOCX should normally land around **22–28 pages**. A 30-page guide is acceptable only if the extra length is pedagogically necessary. A 50-page guide is a failure of concision.

**Deliverable: the DOCX teacher guide only.** Do not write a rationale, methodology note, or compliance checklist alongside it.

---

## 2. Source hierarchy and the role of each source

### 2.1 What each source is for

| Source | Role |
|---|---|
| **`reading_wolof_progression.docx`** | **Source of truth for what topic each week teaches**, in both L1 and L2. The progression document tells you the week's vocabulary, grammar, conjugation, orthography, and production-d'écrits content for both languages. When in doubt about *what to teach*, this document wins. |
| **`edu-kg` connector (KG)** | **Structural index** for the CE1 curriculum. Use the KG to (a) decompose a topic into formal learning components → source for *Nisaru njàng mi* and *Nisaru jukki bi*, (b) confirm prerequisite chains via `buildsFrom` → source for the palier-bridge and L1↔L2 transfer rationale, (c) retrieve bilingual standard descriptions in their authoritative wording. |
| **Per-week brief (`semaine_{N}_brief.md`)** | **Curated week-specific input.** Encodes facts that cannot be auto-derived: topic divergences between progression doc and KG, L1 fallback content for skip-decomposition sessions, required L1↔L2 transfer pairs, and the content-fidelity spot-check list. |
| **Embedded session-layout mini-template in §6.1** | **Format and tone reference.** Use it as the layout contract for headers, metadata, teacher/student columns, phase flow, and classroom-script style. |
| **Embedded MEN reading timetable in §5** | Authoritative for session placement, order, language scope, and duration. |

### 2.2 What the KG does NOT contain

The KG holds standards, learning components, and progression links. It does **not** contain:

- sample classroom texts or genre exemplars;
- vocabulary lists, glosses, or example sentences;
- Wolof corpus material;
- age-appropriate object lists or cultural examples;
- pupil-facing dialogue or scripts.

Generate that content yourself using the embedded session-layout mini-template in §6.1, the subject-pattern reminders in §15, and the per-week content cues in the brief and KG. **Do not search the KG for it.**

### 2.3 Handling topic divergences

For some sessions, the progression document and the KG may disagree on what the week teaches. The brief enumerates these divergences in its "Topic divergences" table.

**Rule: follow the progression document, full stop.** Use the brief's "L1 fallback content" table as the topic for divergent slots. **Do not** call `get_learning_components_for_standard` on the conflicting KG standard for these slots — its components describe a different topic and will mislead the session objectives.

There is no "divergence flagging" requirement in the output. Just follow the rule and move on.

### 2.4 KG anchor discovery protocol

For every regular instructional week, assemble the anchor table by combining:

**(a) Week-grouped standards.** Discover via:

```text
search_items({
  path_segment: "week:{N}",
  subject: "Langue et Communication",
  grade: "CE1",
  node_type: "standard_item",
  limit: 30
})
```

This returns the week's standards for Vocabulaire, Grammaire, Conjugaison, Orthographe, Écriture/Copie, and Production d'écrits. Each result's `sourceLabel` identifies its strand.

**(b) Palier-scoped standards.** Look up from Appendix B for Palier {P}. Covers Expression Orale, Compréhension Écrite, Récitation, Fluidité, Identification des Mots Fréquents L2, and Remédiation CGP.

> **Important:** the KG does not currently have a separate Compréhension à l'Audition standard. Reuse the Compréhension Écrite UUID for CA L1/L2 — this is a deliberate workaround, not a coincidence. CA sessions teach the same comprehension strategies (mental images, central-subject identification) applied to listening rather than reading.

**(c) Skip-decomposition flags.** Apply from the brief's topic-divergence table. Sessions flagged "skip decomposition" use the brief's L1 fallback content instead of KG decomposition.

**(d) L1/L2 sharing.** For Vocabulaire, Écriture, Production d'Écrits, Récitation, Fluidité, the L1 and L2 sessions share a single UUID (combined L1/L2 standard). For Grammaire/Conjugaison/Orthographe, the KG also stores a single bilingual standard per week — but the L1 framing in current KG content reflects the French topic, so when the brief flags an L1/L2 divergence, the affected session falls back to the progression doc.

**Important coordination note:** Where a single UUID is shared across multiple sessions (e.g. Expression Orale L1 Jour 1 and Expression Orale L2 Jour 5), select 2–3 of its learning components per session and keep them coordinated across L1 and L2 so the L2 session genuinely re-uses the L1 work.

The combined table covers all 22 reading sessions. Mark each session ✅ Yes (use KG decomposition) or ❌ No (skip — use progression doc fallback).

### 2.5 Mandatory KG workflow

The KG must be used as a **pre-drafting evidence source**, not as an optional afterthought. Before writing any session, complete a private KG Evidence Pass. Do **not** begin drafting the guide until this pass is complete.

#### 2.5.1 Private KG Evidence Table

Build a private table with the following columns. This table is for planning only and must **not** appear in the final DOCX.

| UUID | Sessions using it | Authoritative standard wording | Learning components | `builds_from` prerequisite(s) | Aux statements (palier-scoped only) | How this will appear in the guide |
|---|---|---|---|---|---|---|

For every **unique UUID marked ✅ Yes** in the discovered anchor table, call:

```text
get_learning_components_for_standard({ standard_id: "<uuid>" })
```

Record the returned learning components and assign the relevant components to the sessions that use that UUID.

For **palier-scoped** standards (those whose `canonicalPathKey` does **not** contain a `week:N` segment — typically Expression Orale, Compréhension Écrite/CA, Récitation, Fluidité, Mots Fréquents, CGP), additionally call:

```text
get_aux_statements({ identifier: "<uuid>" })
```

with no filter to discover the framework's `source_label` vocabulary, then narrow with `source_labels` to retrieve the actionable per-week guidance:

```text
get_aux_statements({ identifier: "<uuid>", source_labels: ["Contenus", "Durée"] })
```

The per-week Contenu is identified by pairing each `Contenus` aux statement with the immediately-following `Durée`. Two patterns are valid:

- **Per-week Durée** — `Durée.text` matches `"Ayu bés {N} Semaine {N}"`. Use the paired `Contenus` for week {N} only.
- **Multi-week single-block Durée** — `Durée.text` matches `"{n} ayu bés {n} semaines"` (e.g., `"7 ayu bés 7 semaines"` for a Palier 2 strand whose teaching content is uniform across the palier). Use the paired `Contenus` for **every week of the palier**. This pattern is normal for Fluidité, Mots Fréquents, and Remédiation CGP — their per-week refinement comes from the brief, not from per-week aux statements.

Use the matched `Contenus` text to populate the *Ëmb bi / Contenu* line of the corresponding session. Skip this call for week-grouped standards (their `canonicalPathKey` carries `week:N` and their week-specific content is in the standard description and LCs).

> Note: `list_facets` does not currently surface aux-statement source labels, so the unfiltered `get_aux_statements` call is the only reliable discovery route for the aux-label vocabulary.

**Mandatory `builds_from` progression calls.** For each of the strands below — when its anchor exists for the target week — call:

```text
get_progression({ identifier: "<uuid>", direction: "builds_from", depth: 1 })
```

| Area | Why the progression matters | Expected outcome | If `progressionAvailability` is `no_edges_found` |
|---|---|---|---|
| Expression Orale (palier-scoped) | Bridge prior-palier oral expression to current-palier oral expression | **Expected `no_edges_found` in the current KG build** — palier-scoped oral standards do not yet emit progression edges. Make the call once for confirmation and move on. | Skip the prerequisite anchor. Treat the bridge as inferred from the genre shift only. Do **not** fabricate a prior-palier standard description or quote a wording the KG did not return. |
| Compréhension à l'Audition + Compréhension Écrite (palier-scoped) | Bridge prior-genre comprehension strategies to current-genre comprehension strategies | **Expected `no_edges_found` in the current KG build.** | Same as above — narrate the bridge from genre framing only. |
| Conjugaison (week-grouped) | Connect prior-period verb regularity to the current-week tense | Edges expected (`edges_present`); typically returns a Palier-1 present-tense or prior-week anchor. | If empty, log this as a KG regression and skip — do not fabricate. |
| Production d'Écrits (week-grouped) | Bridge prior-genre text features to current-genre text features | Edges expected (`edges_present`); typically returns a Palier-1 W1 narrative-text-features anchor for first-of-genre weeks. | Same as above. |
| Fluidité (palier-scoped) | Connect prior oral reading fluency to current-genre reading | Edges expected (`edges_present`); returns prior-palier narrative-fluency plus mots-fréquents and CGP standards. | Same as above. |

The `get_progression` response includes a `progressionAvailability` field (`"edges_present"` or `"no_edges_found"`) that distinguishes "this standard genuinely has no progression" from "the upstream KG build did not emit progression links yet." Use it to choose the empty-handling branch above.

Use each prerequisite as a **brief bridge, transfer prompt, or reminder only**. Do not turn prerequisites into review lessons.

#### 2.5.2 What counts as effective KG use

A KG learning component has been used effectively only if it appears in **all three** places below:

1. **Objective line** — convert the LC into the session's *Nisaru jukki bi / Objectif spécifique*.
2. **Teaching activity** — include a Découverte, Structuration, or Entraînement task that directly practices the LC.
3. **Evaluation** — include a success criterion, expected answer, or short evaluation item that checks the LC.

If a learning component appears only in the session header and not in the activity/evaluation, the KG was not used effectively.

For palier-scoped standards where `get_aux_statements` returned per-week `Contenus`, the same three-place rule applies: the Contenu text drives the *Ëmb bi / Contenu* line, must be operationalized in at least one Découverte/Structuration/Entraînement activity, and must be checked in the evaluation. A Contenu that appears only as a header decoration does not count as effective KG use.

#### 2.5.3 Sessions where KG decomposition must be skipped or not used

For sessions marked **❌ No** in the discovered anchor table (per the brief's divergence flags):

1. Use the brief's L1 fallback content as the session topic.
2. Decompose it into 1–2 *Nisaru jukki bi / Objectif spécifique* yourself, in CE1-friendly wording.
3. Do **not** call `get_learning_components_for_standard` on the conflicting KG standard.
4. Do **not** mention any mismatch in the final guide.

#### 2.5.4 Verification and recovery

If `get_learning_components_for_standard` returns an empty, unexpected, or confusing result, do **not** search first. Call:

```text
get_item({ identifier: "<uuid>" })
```

Use `get_item` as a verification/detail tool, not as a substitute for search. It should verify:

- the standard description;
- the canonical path;
- whether the item is week-grouped or palier-scoped;
- available learning components, progression context, and related items.

For **palier-scoped standards** the LC list is often sparse or near-verbatim with the standard description, because the per-week teachable content lives in auxiliary statements rather than in separate LCs. In that case, `get_aux_statements` (already called per §2.5.1) carries the Week-{N} `Contenus` that feeds the *Ëmb bi / Contenu* and *Nisaru jukki bi / Objectif spécifique* lines. The *Nisaru njàng mi / Objectif d'apprentissage* still comes from the LC list or the standard description.

Only after this verification should you decide whether to proceed from the KG output or fall back to the progression document.

#### 2.5.5 Search discipline

Do **not** use `search_items` again after the initial week-discovery call (and the per-palier discovery if Appendix B has a TODO for the target palier). Use UUIDs directly thereafter.

Use `search_items` again only when a session genuinely lacks an anchor — for instance, if a future week introduces a topic with no week-grouped standard or palier-scoped match.

When search is necessary, use this pattern:

```text
search_items({
  query: "<short exact topic keyword with accents/diacritics preserved>",
  node_type: "standard_item",
  grade: "CE1",
  subject: "Langue et Communication",
  source_label: "<exact strand label when known>",
  limit: 100
})
```

Search rules:

- Prefer UUID calls over search.
- Use short exact query terms; preserve accents and diacritics.
- Always include `node_type: "standard_item"`, `grade: "CE1"`, and `subject: "Langue et Communication"`.
- Include `source_label` when the strand is known. High-value labels include `Vocabulaire`, `Grammaire`, `Conjugaison`, `Orthographe`, `Production d'écrits`, and `Écriture / Copie`.
- When scoping to a specific week, palier, or other curriculum position, prefer `path_segment` over post-filtering on `canonicalPathKey`. Pass the exact segment text including its `key:` prefix — e.g. `path_segment: "week:10"`, `path_segment: "substage:palier-2-communication-ecrite"`. Match is exact (not substring), so `"week:10"` will not false-match `"week:100"`.
- Use `limit: 100` for exploratory searches because results are not guaranteed to be ranked semantically.
- Inspect `canonicalPathKey`; keep only results matching the intended scope.
- If one filtered search and one narrowed retry fail, stop searching and use the brief's L1 fallback content or the progression document's topic description.

### 2.6 Worked example — protocol applied to a single session

This example illustrates the protocol on a hypothetical session. It is documentation, not a content requirement.

Suppose the brief / progression-doc row for `<strand>` in Semaine `{N}` reads: *"<L2 topic description>"*.

**Step 1.** Anchor discovery via `search_items({path_segment: "week:{N}", source_label: "<strand>", grade: "CE1", subject: "Langue et Communication", node_type: "standard_item"})` → returns the strand's UUID for that week.

**Step 2.** `get_learning_components_for_standard("<uuid>")` returns the standard's learning components. Each LC becomes one *Nisaru jukki bi / Objectif spécifique* line and is operationalized in the session's activity and evaluation.

**Step 3.** For week-grouped standards in the strands listed in §2.5.1's progression table, call `get_progression("<uuid>", direction="builds_from", depth=1)`. Inspect `progressionAvailability`:

- If `edges_present`, use the returned prerequisite as a brief bridge or transfer prompt — typically a prior-palier or prior-week anchor in the same strand.
- If `no_edges_found`, narrate the bridge from genre framing alone. Do not fabricate a prerequisite.

**Step 4.** For palier-scoped standards, also call `get_aux_statements("<uuid>", source_labels=["Contenus", "Durée"])` and pair each `Contenus` with the immediately-following `Durée`. Use the per-week match (`Ayu bés {N} Semaine {N}`) where present, or the multi-week single-block match (`{n} ayu bés {n} semaines`) where the Contenu spans the whole palier. Use the matched Contenu to populate the *Ëmb bi / Contenu* line.

Total cost per unique UUID: at most 2–3 KG calls beyond the initial week-discovery search. The anchor table prevents duplicate calls when multiple sessions share a UUID.

---

## 3. Palier framing

The brief identifies the target palier {P}. Use the corresponding genre framing:

| Palier | Genre framing |
|---|---|
| Palier 1 (Sem 1–8) | Narrative texts (jukki nettali) |
| Palier 2 (Sem 10–16) | Narrative → descriptive texts (jukki melool) |
| Palier 3 (Sem 18–24) | Narrative + descriptive → injunctive texts (jukki santaane) |

When the target week is the **first regular instructional week of a palier**, place a short genre bridge once early in the week (preferably in Jour 1) that names the genre transition. The brief supplies the verbatim Wolof and French phrasing of that bridge in its "Palier framing bridge" section, because the phrasing is palier-specific and must be reviewed by a Wolof linguist before use.

For weeks that are not the first regular instructional week of a palier, omit the bridge unless the brief specifies otherwise.

Do not turn this into a review lesson. Prior-palier knowledge may be briefly reactivated only when it directly supports the new genre work. If `get_progression(uuid, direction="builds_from")` returns a non-empty result for the relevant Palier-{P} standard (`progressionAvailability: "edges_present"`), use that prerequisite as a brief reminder. If it returns `progressionAvailability: "no_edges_found"`, narrate the bridge from the genre shift alone without inventing a prior-palier anchor that the KG did not supply.

---

## 4. Required content map

The per-session content map for the target week is assembled from three sources:

1. **KG-anchored sessions (✅ Yes)** — content comes from the KG: standard description, learning components, and (for palier-scoped sessions) per-week aux Contenus.
2. **Skip-decomposition sessions (❌ No)** — content comes from the brief's "L1 fallback content" table.
3. **Brief-supplemented content** — for sessions where the KG carries a placeholder or generic content, the brief supplies the actual material:
   - **Mots Fréquents** — the KG's per-week Contenu is the placeholder *"Echelle du MOHEBS (A construire)"*. The brief supplies the actual word list (4–6 CE1-appropriate high-frequency words, prioritizing words that double as relevant-genre vocabulary).
   - **Remédiation CGP** — the KG's Contenu lists all palier reinforcement digraphs as a single multi-week scope. The brief picks a 4–6-item subset for the week based on diagnostic needs.
   - **Récitation** — content type (poème vs. texte en prose) is determined by the per-week aux Contenu retrieved via `get_aux_statements`. The brief notes which type applies if helpful.
   - **Pedagogical constraints** — week-position constraints (e.g. limits on what can be required in the first instructional week of a new genre, or in a week that introduces a new structural feature) live in the brief.

Do not pull in neighboring-week content unless the brief explicitly requires it.

### 4.1 Genre-text variety requirement

Compose or select **at least 4 distinct {target-genre} texts** across the CA and CE sessions (minimum 2 in L1, minimum 2 in L2), where {target-genre} is the genre indicated by the palier framing:

- Palier 2: descriptive texts (each describing a different type of familiar object/setting — e.g. clothing item, household tool, food item, place, school object, musical instrument)
- Palier 3: injunctive texts (each giving instructions for a different familiar task — e.g. recipe, game rules, classroom routine, simple craft)
- Palier 1: narrative texts (each telling a different kind of short story — e.g. school day, family event, market trip, celebration)

Do not reuse the same object/task/event across comprehension sessions.

All texts must use locally familiar Senegalese objects, foods, places, or school items — not abstract or unfamiliar items. Each text should be 4–8 sentences and exercise different vocabulary appropriate to the genre. The variety ensures students encounter multiple models and are not over-fitted to a single instance.

---

## 5. Required MEN reading timetable

Generate exactly the reading sessions below. Preserve day, order, scope, and duration. Do not generate non-reading subjects.

The generated teacher guide must begin with a **short timetable validation table** confirming these 22 reading sessions.

| # | Day | Official day | Block | Session to generate | Scope | Duration |
|---:|---|---|---|---|---|---:|
| 1 | Jour 1 | Lundi | 8h–11h | Waxinu Lammiñ / Expression Orale L1 | L1 only | 30 mn |
| 2 | Jour 1 | Lundi | 8h–11h | Nàmm Deggin / Compréhension à l'Audition L1 | L1 only | 30 mn |
| 3 | Jour 1 | Lundi | 8h–11h | Compréhension à l'Audition L2 | L2 only | 30 mn |
| 4 | Jour 1 | Lundi | 11h30–13h | Baataan / Vocabulaire L1 | L1 only | 30 mn |
| 5 | Jour 1 | Lundi | 11h30–13h | Nasum Mbind / Production d'Écrits L1 | L1 only | 30 mn |
| 6 | Jour 2 | Mardi | 8h–11h | Tari-Taalif / Poésie-Récitation L1/L2 | L1/L2 combined | 30 mn |
| 7 | Jour 2 | Mardi | 8h–11h | Róofoo gi Baat / Grammaire L1 | L1 only | 30 mn |
| 8 | Jour 2 | Mardi | 8h–11h | Tëralinu Mbind / Orthographe L1 | L1 only | 30 mn |
| 9 | Jour 2 | Mardi | 11h30–13h | Dégginu Mbind / Compréhension Écrite L1 | L1 only | 30 mn |
| 10 | Jour 2 | Mardi | 11h30–13h | Compréhension Écrite L2 | L2 only | 30 mn |
| 11 | Jour 2 | Mardi | 15h–17h | Production d'Écrits L2 | L2 only | 30 mn |
| 12 | Jour 2 | Mardi | 15h–17h | Remédiation CGP L1/L2 | L1/L2 combined | 60 mn |
| 13 | Jour 3 | Mercredi | 8h–11h | Vocabulaire L2 — Session 1 | L2 only | 30 mn |
| 14 | Jour 3 | Mercredi | 8h–11h | Identification des Mots Fréquents L2 | L2 only | 30 mn |
| 15 | Jour 3 | Mercredi | 8h–11h | Demalin Waxe / Conjugaison L1 | L1 only | 30 mn |
| 16 | Jour 3 | Mercredi | 11h30–13h | Orthographe L2 | L2 only | 30 mn |
| 17 | Jour 4 | Jeudi | 8h–11h | Mbindin / Écriture L1/L2 | L1/L2 combined | 30 mn |
| 18 | Jour 4 | Jeudi | 8h–11h | Grammaire L2 | L2 only | 30 mn |
| 19 | Jour 4 | Jeudi | 8h–11h | Conjugaison L2 | L2 only | 30 mn |
| 20 | Jour 5 | Vendredi | 8h–11h | Expression Orale L2 | L2 only | 30 mn |
| 21 | Jour 5 | Vendredi | 8h–11h | Vocabulaire L2 — Session 2 | L2 only | 30 mn |
| 22 | Jour 5 | Vendredi | 11h30–13h | Développer la Fluidité de la Lecture L1/L2 | L1/L2 combined | 30 mn |

Critical timetable notes:

- **Expression Orale L1** appears only on Jour 1.
- **Expression Orale L2** appears only on Jour 5.
- **Poésie-Récitation**, **Écriture**, and **Fluidité** are combined L1/L2 sessions.
- **Vocabulaire L2** appears twice and must be treated as two distinct sessions.
- **Compréhension Écrite L1** is on Jour 2, not Jour 1.
- Only the Tuesday **Remédiation CGP L1/L2** reading-remediation session is generated. Do not generate mathematics or other remediation sessions.

---

## 6. Required session structure

Each session must include concise instructional metadata where relevant:

- Palier / competency / subdomain anchoring;
- **Nisaru njàng mi / Objectif d'apprentissage**;
- **Nisaru jukki bi / Objectif spécifique**;
- **Ëmb bi / Contenu**;
- **Jumtukaay yi / Moyens**;
- **Sukkandikukaay / Documentation**.

For sessions marked ✅ Yes in the anchor table, the *Nisaru jukki bi / Objectif spécifique* lines come from the KG's learning components for that standard. For sessions marked ❌ No, derive them from the brief's L1 fallback content in CE1-friendly wording.

For each selected KG learning component, make the alignment visible in three places: the objective line, one direct activity in Découverte/Structuration/Entraînement, and the Évaluation success criteria or expected answers.

Use the ARED regular-week logic: preparation/context, discovery, structuring, practice, and evaluation. The visible phase labels may follow the session-type house style in §6.2.

A regular instructional week always includes the Évaluation phase.

For procedural/practice-heavy sessions — Poésie-Récitation, Écriture, Identification des Mots Fréquents, Fluidité, Remédiation CGP — keep the same regular-week logic if needed, but use the compact session-type phase labels in §6.2 instead of inventing artificial rule-heavy phases.

### 6.1 Embedded mini-template for session layout and classroom-script style

Use this mini-template as the built-in format reference. Do **not** require or rely on any separate external layout document.

```md
SEMAINE {N} — JOUR X
Séance : K        [session title in the required language(s)]        Durée : 30 mn

Sumb / Palier : [competency or palier anchor]
Nisaru njàng mi / Objectif d'apprentissage : [broad learning objective]
Nisaru jukki bi / Objectif spécifique : [session-specific objective, sourced from KG LC when ✅]
Ëmb bi / Contenu : [week's content target]
Jumtukaay yi / Moyens : [objects, images, slate, sentence cards, reading text, etc.]
Sukkandikukaay / Documentation : [teacher-facing source label only, e.g. Référentiel bilingue, progression CE1, supports de classe. Do not include KG UUIDs, tool names, or internal evidence notes.]

[If the session uses a text, poem, corpus, word list, or model paragraph, place it here before the activity table.]

| Yëngute Muse bi / Activités du maître | Yëngute elew yi / Activités des élèves |
|---|---|
| **Mise en situation** — Context / Waral gi: [short situation]. Instruction / Ndigël gi: [exact pupil task]. | [Expected pupil reaction or first production.] |
| **Découverte** — [Teacher presents object/text/corpus, asks guided observation questions.] | [Pupils observe, read/listen, answer, manipulate.] |
| **Structuration** — [Teacher guides rule, strategy, descriptive feature, or pattern. Include rule box if useful.] | [Pupils formulate the rule/strategy in simple words and apply it to one example.] |
| **Entraînement** — [Guided practice, pair work, manipulation, transfer 🔁, or autonomous reinvestment.] | [Pupils produce, compare, correct, or read aloud. Include expected answers where useful.] |
| **Évaluation** — [Short task with success criteria and one likely error + feedback.] | [Pupils complete the task. Expected answer/success criterion appears here or just below.] |
```

Layout and style requirements:

- Keep one clear session header per session: week/day, session number, title, and duration.
- Keep metadata compact; do not let it consume the page.
- Put any core text, poem, corpus, or word list before the activity table, not buried inside long directions.
- Use paired teacher/student rows. Every teacher action needs a matching pupil action on the same row.
- Use concrete classroom scripting: questions to ask, likely answers, expected productions, short feedback lines.
- Do not use empty placeholders such as `p. …`, `image à insérer`, or unexplained `[texte]` fields.
- If an image is needed, describe the image in words as a teacher-facing prompt instead of leaving an insertion placeholder.
- Do not use shortcut phrases such as "E déroule la leçon…" or "faire la même procédure" unless the full procedure is already visible in the same session.
- Do not copy old narrative content or content from earlier weeks. Each week's content must align to that week's brief and the KG's Week-{N} anchors.

### 6.2 Existing guide house style patterns

The generated DOCX must be self-contained and must not rely on any external example guide. Use the layout patterns below as the embedded house style. Keep the two-column teacher/student table structure, but choose phase labels that fit the session type instead of forcing every session into identical labels.

| Session type | Preferred phase labels / visual flow |
|---|---|
| **Expression orale** | `Waajal gi / Phase d'appropriation et de préparation` → `Wax sa xalaat / Production libre des élèves` → `Leeral gi / Explication ou production dirigée` → `Natt / Évaluation` |
| **Compréhension à l'Audition** | `Étape 1 : Découvrir le vocabulaire` → `Étape 2 : Lire l'image` → `Étape 3 : Écouter la lecture du texte` → `Étape 4 : Travailler la compréhension` |
| **Compréhension Écrite** | `Étape 1 : Émettre des hypothèses de lecture` → `Étape 2 : Définir et utiliser des mots` → `Étape 3 : Étude de la stratégie de compréhension` → `Étape 4 : Lecture du texte` → `Étape 5 : Comprendre le texte` |
| **Vocabulaire** | `Présentation de la situation / Woneb cëslaayu njàng mi` → `Lecture silencieuse contrôlée` → `Lecture du maître` → `Lecture par 2–3 élèves` → `Compréhension générale` → `Étude des mots ou structures ciblés` → `Natt / Évaluation` |
| **Grammaire / Orthographe / Conjugaison** | `Nafar / Révision` when useful → `Cóobute / Corpus ou situation de départ` → `Caytu / Observation et manipulation` → `Tënk sàrt yi / Synthèse ou règle` → `Tàggatu / Entraînement` → `Natt / Évaluation` |
| **Poésie-Récitation** | `Nafar / Révision` → `Woneb taalif bi / Présentation du poème` → `Déggin / Compréhension` → `Njàng / Apprentissage par répétition` → `Natt / Évaluation` |
| **Production d'Écrits** | `Cóobute / Projet d'écriture ou situation` → `Gëstu ak settantal / Recherche et analyse avec grille` → `Tënk sàrt yi / Synthèse des critères` → `Tàggatu / Réinvestissement guidé` → `Natt / Évaluation` |
| **Identification des Mots Fréquents** | `Étape 1 : Présenter le mot` → `Étape 2 : Écrire le mot` with `Je fais / Nous faisons / Tu fais` → `Étape 3 : Lire le mot à haute voix` with `Je fais / Nous faisons / Tu fais` → `Étape 4 : Afficher et répertorier le mot` |
| **Écriture / Copie** | `Modèle` → `Observation du tracé` → `Tracé dans l'air ou sur ardoise` → `Copie dans le cahier` → `Correction / Valorisation` |
| **Fluidité** | `Modèle de lecture` → `Lecture chorale` → `Lecture en binômes` → `Lecture individuelle` → `Feedback sur vitesse, exactitude, expression` |
| **Remédiation CGP** | Use a fiche-style structure: diagnostic difficulty, remediation objective, target group, modality, means/documentation, then teacher/student table with `Passation des consignes`, `Mise en situation du groupe de besoin`, `Entraînement / Renforcement`, `Contrôle du travail`, `Consolidation et évaluation de l'effet de la remédiation`. |

These labels are not decorative. They should shape the visible layout of each session. Keep them concise, and do not add long explanations under each label unless needed for classroom use.

### 6.3 KG evidence pass + two-pass generation requirement

Generate the guide through this sequence:

**Pass 0 — KG Evidence Pass.** Complete §2.5 before drafting. Do not begin Pass 1 until the private KG Evidence Table is complete. The evidence pass for each unique ✅ Yes UUID consists of: (a) `get_learning_components_for_standard` for the LCs; (b) for the rows in §2.5.1's progression table, `get_progression` with `direction="builds_from"` plus inspection of `progressionAvailability`; (c) for **palier-scoped** standards, `get_aux_statements` for the per-week guidance and duration metadata. Skip step (c) for week-grouped standards — their week-specific content is already encoded in the standard description and LCs.

**Pass 1 — Pedagogical content.** For each of the 22 sessions, draft the classroom script in plain text: the core text/corpus/word list when relevant, teacher questions, expected answers, manipulation or transfer activity, and evaluation task with success criteria. For content-rich sessions — at least 12 of 22 — include one session-specific likely pupil error with corrective feedback. Do not force bulky error blocks into procedural sessions unless the error is natural and useful. Review this draft against the priorities in §14 before proceeding.

**Pass 2 — DOCX formatting.** Convert the reviewed pedagogical content into the formatted DOCX with proper table layout, Wolof blue styling, rule boxes, and visual formatting per §8. Do not let formatting decisions alter, truncate, or compress the pedagogical content from Pass 1.

If time or context constraints force compression, preserve the KG Evidence Pass and pedagogical completeness before visual polish. A well-scripted session in a plain table is more valuable than a beautifully formatted session with thin content.

---

## 7. Layout checks

Inside each content-rich session:

- Separate **Context / Waral gi** from **Instruction / Ndigël gi** in the Mise en situation.
- Use paired teacher/student columns throughout.
- Every teacher action must have a matching pupil action on the same row.
- Do not write independent teacher and student lists that cannot be read across.

---

## 8. DOCX visual formatting rules

The final teacher guide is expected as a DOCX. Apply the visual code directly in the document.

| Element | Required formatting                                                                         |
|---|---------------------------------------------------------------------------------------------|
| French text | Default black                                                                               |
| Wolof / national-language text | Consistent readable bold red via named character style, e.g. **`Wolof / Langue nationale`** |
| Teacher stage directions | Italics                                                                                     |
| Teacher speech to pupils | Regular, non-italic text                                                                    |
| Rules/patterns to remember | Framed box / bordered callout                                                               |
| L1↔L2 transfer activity | Mark with 🔁 or the project's chosen transfer pictogram                                     |
| Expected answers | Same bullet marker throughout the whole guide                                               |
| Session/phase labels | Bilingual where appropriate                                                                 |

Do not explain the visual code repeatedly inside the guide. Use the formatting itself.

---

## 9. L1↔L2 transfer requirements

Transfer must be a pupil activity, not a passing note. A sentence like "this corresponds to Wolof…" is not enough.

Use these transfer types:

| Type | What pupils do |
|---|---|
| Guided translation | Say/write a structure in one language, then produce the equivalent in the other |
| Parallel-structure comparison | Compare side-by-side Wolof/French structures and identify what changes |
| Bilingual reformulation | Reformulate the same description or idea in the other language |

The brief's "Required L1↔L2 transfer pairs" table lists the week's required pairs, placements, and minimum operationalization. Mark each explicit transfer activity with **🔁**.

### 9.1 Transfer operationalization test

For each 🔁 transfer activity, verify that the script includes an **explicit pupil production step** — something the pupil says, writes, translates, or compares. If the transfer only contains a teacher statement ("In wolof, this corresponds to…") without a pupil action, it fails the operationalization test and must be rewritten.

Acceptable pupil actions include:

- guided translation, e.g. *"Traduisez : [L1 form] → ? (expected: [L2 form])"*
- "Compare the two paradigms side by side: what changes, what stays the same?"
- "Describe the same object first in wolof, then in French."

A minimum of **4 of 6** required transfer pairs (or a brief-specified count for weeks with a different total) must include an explicit pupil translation or comparison task, not just a teacher-stated observation.

---

## 10. Learner autonomy and manipulation

Include at least **one short autonomous reinvestment activity per day**, preferably in an Entraînement phase.

Acceptable formats:

- small challenge with clear success criteria;
- guided free production with constraints;
- pair work with role rotation;
- individual slate or notebook task followed by peer comparison.

For **Grammar, Orthography, and Conjugation** sessions in both L1 and L2, Découverte and/or Structuration must include at least one manipulation activity:

- substitution;
- transformation;
- sorting;
- matching;
- gap-filling;
- sentence-card rearrangement.

Observation alone is not enough before rule formulation.

---

## 11. CE1-appropriate language

Teacher speech to pupils must use simple, age-appropriate wording.

| Avoid in pupil-facing speech | Use instead |
|---|---|
| "Identifier le sujet central du texte" | "De quoi parle le texte ?" |
| "Construire une représentation mentale" | "Ferme les yeux : que vois-tu ?" |
| "Analyser la structure syntaxique" | "Regarde la phrase. Qu'est-ce qui change ?" |
| Technical grammar term before explanation | Plain-language gloss first, then introduce the term |

Technical terminology may appear in teacher-facing notes and rule boxes, but pupil-facing speech must introduce or gloss technical terms.

---

## 12. Wolof quality rules

Wolof must be native-speaker acceptable. The brief's "Wolof target forms" list (in §16 spot-check) names the specific forms required for the target week.

General Wolof rules (apply to all weeks):

- correct tense/aspect; do not use present forms for completed past actions;
- consistent orthography and terminology;
- full word forms, e.g. **xew-xew**, not truncated **xew** when "event" is intended;
- no Wolof sentence begins with **Te**;
- use **Naka** where "how?" is intended; do not substitute **Noo** incorrectly;
- preserve accents and diacritics: **ñ, ŋ, à, é, ë**, etc.;
- prefer standard Wolof terms over French loans when the Wolof term is available;
- avoid mechanical French-to-Wolof calques.

Before finalizing any Wolof passage, correct tense, diacritics, morphology, natural phrasing, and consistency.

---

## 13. Concision rules

The guide must be dense and teachable, not bloated.

- Target **22–28 DOCX pages**.
- Default to **one page per 30-minute session**.
- Allow two pages only for rule-heavy sessions or the 60-minute CGP remediation.
- State week-level framing once, not in every session.
- Do not repeat broad palier-level explanations inside every session.
- Do not restate an instruction as both a long stage direction and a quoted teacher line.
- Use rule boxes, transfer pictograms, bullets, and italics instead of explanatory prose.
- Cut pedagogical meta-commentary such as "this phase is important because…".
- Keep transitions short: name the phase and move on.

---

## 14. Evaluation-rubric priorities

Optimize for the embedded human-evaluation framework. Do not reproduce the rubric in the generated guide.

**Embedded Annexe 7 weights:** Didactic relevance 20%; disciplinary accuracy 15%; learner adaptation 10%; language management 15%; task quality 10%; explanation and feedback 10%; controllability 5%; pedagogical coherence 5%; contextual adaptation 5%; efficiency 3%; reliability 1%; ethics and bias 1%.

Annexe 7 uses a **0–4 scale**: 0 = nonexistent/incorrect, 1 = very insufficient, 2 = acceptable minimum, 3 = good, 4 = excellent. The global score is a weighted average. These instructions optimize for the 3–4 band without reproducing the rubric in the final teacher guide.

Use locally familiar Senegalese school/community examples and avoid gender, ethnic, socioeconomic, or cultural stereotypes.

| Priority | What the generated guide must show |
|---|---|
| Didactic relevance | Palier {P} target-genre focus, clear progression. **Every content-rich session (minimum 12 of 22) must include at least one specific likely pupil error and a corrective feedback line in Wolof and/or French.** Generic errors ("the student doesn't understand") do not count. The error must be specific to the content being taught. See Appendix A for the calibration principle and the brief's "Error calibration examples" section for the per-week examples. |
| Disciplinary accuracy | Exact week content, correct Wolof/French grammar and spelling |
| Learner level | CE1-appropriate tasks, manageable cognitive load, and at least one easy/standard/challenge variant in the main practice or evaluation of content-rich sessions where differentiation is pedagogically useful |
| Language management | Simple pupil-facing language, context/instruction separation, bilingual coherence |
| Transfer | Operationalized L1↔L2 activities, not passing mentions (see §9.1 operationalization test) |
| Classroom usability | Teacher/student paired rows, concrete scripts, expected answers or success criteria |
| Controllability | Keep the DOCX easy to edit: clear session boundaries, editable tables, visible objectives/content/evaluation fields, no hidden dependencies, no unexplained generated placeholders |
| Reliability | Reduce generation variability by following the source hierarchy, brief, anchor table, fixed timetable, required content map, and final QA gate exactly |
| Concision | Scannable DOCX, no repeated boilerplate, page target respected |

Rubric-critical safeguards: content-rich sessions include likely pupil errors and a short feedback line; evaluations include expected answers or success criteria; technical terms are glossed in CE1-friendly language before labels; KG learning components shape objectives, activities, and evaluations instead of appearing only as metadata. Also vary task types, avoid over-reliance on repetition, and include brief alternative reformulations when pupils are likely to misunderstand a concept.

---

## 15. Subject-pattern reminders

Use the embedded mini-template in §6.1 plus the recurring patterns below. The patterns guide the flow of each session; they are not permission to add padding.

| Session type | Pattern to preserve |
|---|---|
| Oral expression | object/topic observation → free production → guided production → evaluation |
| Listening comprehension | vocabulary/image/text listening → comprehension questions → mental image and "what is it about?" |
| Reading comprehension | prediction → key words → strategy → reading → understanding |
| Vocabulary | corpus → reading/listening → target words/structures → sentence use → evaluation |
| Grammar/Orthography | corpus → manipulation → rule/pattern → guided practice → evaluation |
| Conjugation | corpus → manipulation → target marker/endings → paradigm/pattern → practice |
| Written production | model text/criteria → identify genre features → short guided sentences |
| Recitation | present poem/prose → understand → repeat/memorize → recite with expression |
| High-frequency words | present words → repeat/read → flash recognition → sentence use |
| Handwriting | model → air/slate tracing → notebook practice → correction |
| Fluency | model reading → choral reading → paired reading → individual reading → feedback |
| CGP remediation | diagnostic grouping → targeted decoding → supported reading → reassessment |

These patterns are structure aids, not padding.

---

## 16. Final QA gate

Before final delivery, verify the items below. The list orders the highest-impact pedagogical checks first.

### Pedagogical depth gate (must all pass before formatting)

- [ ] At least **12 content-rich sessions** include a session-specific pupil error with corrective feedback (not generic — see Appendix A for the principle and the brief's "Error calibration examples" section for the per-week examples).
- [ ] At least **4 distinct {target-genre} texts** are used across CA/CE sessions, each on a different topic/object/setting (§4.1).
- [ ] At least **4 of 6** required transfer activities (or the count specified in the brief) include an explicit pupil production step (§9.1).
- [ ] Every Grammar, Conjugation, and Orthography session includes a manipulation activity in Découverte, before the rule is formulated.
- [ ] Every Évaluation phase includes either expected answers or explicit success criteria.
- [ ] Content-rich sessions include easy/standard/challenge variants where differentiation is pedagogically useful.
- [ ] The DOCX remains easy to edit: clear session boundaries, editable tables, visible objectives/content/evaluation fields, and no unexplained placeholders.
- [ ] A private KG Evidence Table was completed before drafting.
- [ ] KG learning components were retrieved for every unique ✅ Yes UUID in the discovered anchor table.
- [ ] For every palier-scoped ✅ Yes UUID, `get_aux_statements` was called and the per-week `Contenus` text was used to populate the *Ëmb bi / Contenu* line of the corresponding session.
- [ ] Every selected KG learning component appears in objective + activity + evaluation.
- [ ] Required `builds_from` progressions were retrieved and used only as brief bridges, reminders, or transfer supports. Where the call returned `progressionAvailability: "no_edges_found"`, the bridge was narrated from genre framing alone — no prior-palier anchor was fabricated.
- [ ] No KG decomposition was used for skip-decomposition sessions listed in the brief.
- [ ] `search_items` was used at most twice (the initial week-discovery call and an optional palier-discovery call). Where `search_items` was used, `path_segment` was preferred over post-filtering on `canonicalPathKey`.

### Timetable validation

- [ ] The guide begins with a validation table for all 22 reading sessions.
- [ ] All 22 sessions appear exactly once, except Vocabulaire L2 which appears as two distinct sessions.
- [ ] No non-reading subjects are generated.
- [ ] Day, order, language scope, and duration match §5.
- [ ] Expression Orale L1 is only Jour 1; Expression Orale L2 is only Jour 5.
- [ ] Remédiation CGP L1/L2 is the only remediation session generated (60 mn).

### Content fidelity (one-pass spot check)

Use the brief's "Content-fidelity spot-check items" list. Every item in that list must be checked against the generated guide.

### Concision

- [ ] The guide is 22–28 pages and visually scannable.
- [ ] No repeated palier-level boilerplate inside every session.
- [ ] The source hierarchy, brief, discovered anchor table, fixed timetable, content map, and QA gate were followed exactly to reduce generation variability.

---

## 17. Delivery

Deliver **only** the complete teacher guide for the target week as a DOCX. No rationale, methodology note, or compliance checklist alongside it. The teacher-facing DOCX should not include intrusive citations.

---

## Appendix A — Error anticipation calibration

Each content-rich session must include a session-specific likely pupil error and a one-line corrective feedback. The required level of specificity is: a teacher could recognize the error in real time and apply the correction without guessing.

The principle:

- **❌ Too generic** — names a general difficulty without identifying what the pupil actually does or what the teacher should say. "Student has difficulty," "Student doesn't understand," "Student makes errors." These do not help a teacher act in the moment.
- **✅ Specific enough** — names a concrete pupil action (a misplaced morpheme, a wrong agreement, a misapplied rule, a wrong category) and pairs it with a concrete corrective line the teacher can say or write. The correction is one sentence, not a re-teach.

The brief's "Error calibration examples" section supplies the actual week-specific calibration table. Use those examples as the anchor for the level of specificity required for that week. Each generated session's error and correction should match that level of granularity, scaled to the topic at hand.

For weeks whose brief does not supply calibration examples, derive them from the week's KG learning components and progression-doc topic forms — name the specific morpheme, structure, or rule the pupil is most likely to misapply, and write the corrective line in the language of instruction for that session.

---

## Appendix B — Palier-scoped UUIDs

These UUIDs are stable across all weeks within a palier. Pin them once and reuse.

| Strand | Palier 1 | Palier 2 | Palier 3 |
|---|---|---|---|
| Expression Orale L1/L2 | TBD — discover via `search_items({path_segment: "substage:palier-1-communication-orale", node_type: "standard_item", grade: "CE1", subject: "Langue et Communication"})` | `f05ac70c-56ce-55fd-a066-7eda2a59a4ad` | TBD — discover via `path_segment: "substage:palier-3-communication-orale"` |
| Compréhension Écrite + CA L1/L2 | TBD — `path_segment: "substage:palier-1-lecture"` | `1e8d18e0-4a09-5369-919a-e1ce38d88934` | TBD — `path_segment: "substage:palier-3-lecture"` |
| Récitation L1/L2 | TBD | `c1754abf-0ab6-541f-bb9b-f62299f4cd27` | TBD |
| Fluidité L1/L2 | TBD | `23a449b7-7706-56b4-9ef0-b083d4bf1ce3` | TBD |
| Identification des Mots Fréquents L2 | TBD | `11ac919a-3e94-57cd-87e3-991b72a351f7` | TBD |
| Remédiation CGP L1/L2 | TBD | `0b7a88c0-de35-50d2-84fb-bc0eb874a679` | TBD |

**Notes:**

- The KG does not have a separate Compréhension à l'Audition standard. CE/CA share a UUID per palier — this is a deliberate workaround (see §2.4).
- Mots Fréquents and CGP are L2-only / combined-modality but use a single UUID each.
- For paliers other than P2, run the corresponding discovery `search_items` call on first use of the template against that palier and pin the resulting UUIDs in this table.
- Récitation in Palier 2 covers both poème (Sem 10–12) and texte en prose (Sem 13–16) under a single UUID. The per-week aux Contenu indicates which content type applies.
