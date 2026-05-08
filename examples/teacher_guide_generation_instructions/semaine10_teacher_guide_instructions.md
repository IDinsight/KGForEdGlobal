# Instructions for Generating the Teacher Guide for Senegal Grade 3 Reading — Semaine 10

## 1. Task

Generate the complete teacher guide for **Semaine 10**, covering **Jour 1 through Jour 5**.

Semaine 10 is:

- the **first regular instructional week of Palier 2 / Jéego 2**;
- the week that introduces **descriptive-text work** for the first time;
- a **model-week-level opening guide** for the palier;
- **not** an integration week, revision week, evaluation week, or a single numbered "Lesson 10."

The guide must be classroom-usable, bilingual where appropriate, visually scannable, and concise. The full DOCX should normally land around **22–28 pages**. A 30-page guide is acceptable only if the extra length is pedagogically necessary. A 50-page guide is a failure of concision.

**Deliverable: the DOCX teacher guide only.** Do not write a rationale, methodology note, or compliance checklist alongside it.

---

## 2. Source hierarchy and the role of each source

### 2.1 What each source is for

| Source | Role |
|---|---|
| **`reading_wolof_progression.docx`** | **Source of truth for what topic each week teaches**, in both L1 and L2. The progression document tells you the Week 10 vocabulary, grammar, conjugation, orthography, and production-d'écrits content for both languages. When in doubt about *what to teach*, this document wins. |
| **`edu-kg` connector (KG)** | **Structural index** for the CE1 curriculum. Use the KG to (a) decompose a topic into formal learning components → source for *Nisaru njàng mi* and *Nisaru jukki bi*, (b) confirm prerequisite chains via `buildsFrom` → source for the Palier 1 → 2 bridge and L1↔L2 transfer rationale, (c) retrieve bilingual standard descriptions in their authoritative wording. |
| **Embedded session-layout mini-template in §6.1** | **Format and tone reference.** Use it as the layout contract for headers, metadata, teacher/student columns, phase flow, and classroom-script style. |
| **Embedded MEN reading timetable in §5 of this file** | Authoritative for session placement, order, language scope, and duration. |

### 2.2 What the KG does NOT contain

The KG holds standards, learning components, and progression links. It does **not** contain:

- sample classroom texts or descriptive-text exemplars;
- vocabulary lists, glosses, or example sentences;
- Wolof corpus material;
- age-appropriate object lists or cultural examples;
- pupil-facing dialogue or scripts.

Generate that content yourself using the embedded session-layout mini-template in §6.1, the subject-pattern reminders in §15, and the Week 10 content map. **Do not search the KG for it.**

### 2.3 Handling Week 10 topic divergences

For two language-tool slots, the progression document and the KG disagree on which topic Week 10 teaches:

| Slot | Progression doc says | KG Week-10 standard says |
|---|---|---|
| Grammar L1/L2 | Possessive markers (*sama, sa, -am* / *mon, ma, mes…*) | Gender and number of nouns |
| Conjugation L1 | *Dégtal / énonciatif sotti weesu* with *-oon* (Wolof-native model) | "Imparfait de l'indicatif" (French framing of the same general region) |

**Rule: follow the progression document, full stop.** Use the progression document's content description verbatim as the topic for that session. **Do not** call `get_learning_components_for_standard` on the conflicting Week-10 KG standard for these two slots — its components describe a different topic and will mislead the session objectives. Use the anchor table in §2.4 to see which sessions get KG decomposition and which take their objectives directly from the progression document.

There is no "divergence flagging" requirement in the output. Just follow the rule and move on.

### 2.4 Week 10 KG anchor table

For rows marked ✅ Yes, use the listed UUID as `standard_id` for `get_learning_components_for_standard` and as `identifier` for `get_progression`. Discovery is not required. For rows marked ❌ No, do not call KG decomposition even if a UUID is shown.

| Session | KG standard description | UUID | Use KG decomposition? |
|---|---|---|---|
| Expression Orale **L1 and L2** (same standard) | Melool këfin, ay pàccam, màndargaam, ak ay njariñam / Décrire un objet, ses éléments, ses caractéristiques, ses fonctions | `f05ac70c-56ce-55fd-a066-7eda2a59a4ad` | ✅ Yes — 4 LCs, palier-scoped |
| Compréhension Écrite **L1 and L2** | Tabax déggin sukkandiku ci ay jukkiy melool / Construire du sens à partir de textes descriptifs | `1e8d18e0-4a09-5369-919a-e1ce38d88934` | ✅ Yes — palier-scoped |
| Compréhension à l'Audition **L1 and L2** | (Same palier-scoped reading-comprehension standard above) | `1e8d18e0-4a09-5369-919a-e1ce38d88934` | ✅ Yes |
| Vocabulaire **L1 and L2** | Acquérir le sens de mots liés au thème | `ecdc9079-d12c-5bbb-b646-1a65f2bb696d` | ✅ Yes — week-grouped |
| Grammaire **L1 and L2** | KG topic = gender/number of nouns; **progression doc = possessives** | `2de5debd-ed90-5d87-80c3-fb632cf4e0bb` | ❌ **No.** Topic mismatch — derive objectives from progression doc directly |
| Conjugaison **L1** | Progression doc = dégtal sotti weesu with *-oon* (Wolof-native); KG framing is French | n/a | ❌ **No.** Derive objectives from progression doc directly |
| Conjugaison **L2** | Imparfait de l'indicatif, verbes 1er groupe | `cd2a856e-941e-5d37-ba0b-22e94af60f57` | ✅ Yes — 2 LCs, valid for L2 |
| Orthographe **L1** | Progression doc = use of possessive markers in writing | n/a | ❌ Derive from progression doc directly |
| Orthographe **L2** | Féminin des noms et adjectifs (règle générale) | `a00c8443-0196-5806-ac39-d629ca0e49e5` | ✅ Yes |
| Écriture **L1 and L2** | Les majuscules cursives : P, B, D, F, R, L, S — copier des textes descriptifs (3–4 lignes) | `d31af96e-503d-546f-ab38-233c284f910a` | ✅ Yes |
| Production d'Écrits **L1 and L2** | Identifier les caractéristiques d'un texte descriptif | `9ee79d26-94ce-5bc1-bb2b-c4cbed1fdcaf` | ✅ Yes |
| Récitation **L1 and L2** | Tari taalif / Restituer un poème (Palier 2) | `c1754abf-0ab6-541f-bb9b-f62299f4cd27` | ✅ Yes — palier-scoped |
| Fluidité **L1 and L2** | Jàng ci kaw ab jukkib melool ak gaawaay, njub ak waxeef | `23a449b7-7706-56b4-9ef0-b083d4bf1ce3` | ✅ Yes — palier-scoped |
| Identification des Mots Fréquents L2 | Reconnaître instantanément des mots fréquents et savoir les orthographier | `11ac919a-3e94-57cd-87e3-991b72a351f7` | ✅ Yes — 2 LCs, palier-scoped |
| Remédiation CGP L1/L2 | Lire/écrire des lettres-sons, syllabes, mots inconnus | `0b7a88c0-de35-50d2-84fb-bc0eb874a679` | ✅ Yes — 6 LCs, palier-scoped |

**Important:** Expression Orale L1 (Jour 1) and Expression Orale L2 (Jour 5) share the **same** KG standard `f05ac70c`. Its four learning components are the four facets a single oral-expression lesson can target — pick 2–3 per session and keep them coordinated across the L1 and L2 sessions so the L2 session genuinely re-uses the L1 work.

### 2.5 Mandatory KG workflow

The KG must be used as a **pre-drafting evidence source**, not as an optional afterthought. Before writing any session, complete a private KG Evidence Pass. Do **not** begin drafting the guide until this pass is complete.

#### 2.5.1 Private KG Evidence Table

Build a private table with the following columns. This table is for planning only and must **not** appear in the final DOCX.

| UUID | Sessions using it | Authoritative standard wording | Learning components | `builds_from` prerequisite(s) | Aux statements (palier-scoped only) | How this will appear in the guide |
|---|---|---|---|---|---|---|

For every **unique UUID marked ✅ Yes** in §2.4, call:

```text
get_learning_components_for_standard({ standard_id: "<uuid>" })
```

Record the returned learning components and assign the relevant components to the sessions that use that UUID.

For the curriculum areas where progression is instructionally important, also call:

```text
get_progression({ identifier: "<uuid>", direction: "builds_from", depth: 1 })
```

For **palier-scoped** standards (Expression Orale, Compréhension à l'Audition, Compréhension Écrite, Récitation, Fluidité — i.e. those whose `canonicalPathKey` does **not** contain a `week:N` segment), additionally call:

```text
get_aux_statements({ identifier: "<uuid>" })
```

with no filter to discover the framework's `source_label` vocabulary, then narrow with `source_labels` to retrieve the actionable per-week guidance. Record the matching aux statements in the evidence table and use their `text` to populate the *Ëmb bi / Contenu* line for the corresponding week's session. Skip this call for week-grouped standards (their `canonicalPathKey` carries `week:N` and their week-specific content is in the standard description and LCs).

Mandatory `builds_from` progression calls:

| Area | UUID | Why the progression matters | If `progressionAvailability` is `no_edges_found` |
|---|---|---|---|
| Expression Orale L1/L2 | `f05ac70c-56ce-55fd-a066-7eda2a59a4ad` | Bridge Palier 1 narrative oral expression to Palier 2 descriptive oral expression. | Skip the prerequisite anchor. Treat the bridge as inferred from the genre shift only (narrative → descriptive). Do **not** fabricate a Palier 1 standard description or quote a wording the KG did not return. |
| Compréhension à l'Audition + Compréhension Écrite L1/L2 | `1e8d18e0-4a09-5369-919a-e1ce38d88934` | Bridge narrative comprehension strategies to descriptive comprehension strategies. | Same as above — narrate the bridge from genre framing only, no fabricated Palier 1 anchor. |
| Conjugaison L2 | `cd2a856e-941e-5d37-ba0b-22e94af60f57` | Connect present-tense regularity from Palier 1 to imparfait regularity in Week 10. | Real edges expected. If empty, log this as a KG regression and skip — do not fabricate. |
| Production d'Écrits L1/L2 | `9ee79d26-94ce-5bc1-bb2b-c4cbed1fdcaf` | Bridge narrative-text features to descriptive-text features. | Same as above. |
| Fluidité L1/L2 | `23a449b7-7706-56b4-9ef0-b083d4bf1ce3` | Connect prior oral reading fluency to descriptive-text reading. | Same as above. |

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

For sessions marked **❌ No** in §2.4 — Grammar L1/L2, Conjugaison L1, Orthographe L1 — do not use KG decomposition. Some are known KG/topic mismatches; others have no valid Week 10 KG anchor.

1. Use the progression document's content description verbatim as the session topic.
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

For **palier-scoped standards** — Expression Orale, Compréhension à l'Audition, Compréhension Écrite, Récitation, Fluidité — the LC list is often sparse or near-verbatim with the standard description, because the per-week teachable content lives in auxiliary statements rather than in separate LCs. After `get_learning_components_for_standard`, also call:

```text
get_aux_statements({ identifier: "<uuid>" })
```

with no filter first, to discover which `source_label` values the framework uses for week-specific guidance. Then narrow with `source_labels` to retrieve the entries that carry actionable session content (typical labels include `"Contenus"` for teachable content and `"Durée"` for the intended week placement, but the vocabulary is framework-specific — confirm via the unfiltered call or via `list_facets`):

```text
get_aux_statements({ identifier: "<uuid>", source_labels: ["Contenus", "Durée"] })
```

The `Contenus` text feeds the *Ëmb bi / Contenu* and *Nisaru jukki bi / Objectif spécifique* lines for that week. The `Durée` text confirms the week placement (e.g. "Semaine 10"). The *Nisaru njàng mi / Objectif d'apprentissage* still comes from the LC list or the standard description.

Only after this verification should you decide whether to proceed from the KG output or fall back to the progression document.

#### 2.5.5 Search discipline

Do **not** use `search_items` when a UUID is already provided in §2.4. Use the UUID directly. Every row in the §2.4 anchor table now carries a pinned UUID; there are no remaining "search if needed" rows.

Use `search_items` only when a session genuinely lacks an anchor — for instance, if a future Week introduces a topic with no §2.4 entry, or if you need a sibling/related standard not pinned in the table.

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
- Inspect `canonicalPathKey`; keep only results matching `week:10`, `palier-2`, or the explicitly intended strand.
- If one filtered search and one narrowed retry fail, stop searching and use the progression document's topic description.

### 2.6 Worked example — Conjugaison L2

Progression doc row: *"Conjuguer à l'imparfait de l'indicatif des verbes du 1er groupe en repérant les régularités selon les personnes."*

**Step 1.** Anchor table → `cd2a856e-941e-5d37-ba0b-22e94af60f57`.

**Step 2.** `get_learning_components_for_standard("cd2a856e-941e-5d37-ba0b-22e94af60f57")` returns:

- "Conjuguer des verbes du 1er groupe à l'imparfait de l'indicatif selon les différentes personnes"
- "Repérer les régularités des terminaisons de l'imparfait de l'indicatif selon les personnes"

Use these as the two *Objectifs spécifiques* for the Conjugaison L2 session.

**Step 3.** `get_progression("cd2a856e-941e-5d37-ba0b-22e94af60f57", direction="builds_from", depth=1)` returns:

- "Conjuguer au présent de l'indicatif des verbes d'action du 1er groupe en repérant les régularités selon les personnes" (week 6, Palier 1).

Use this as the explicit Palier 1 anchor in the Conjugaison L2 session and as the L1↔L2 transfer hook: *présent → imparfait* in French mirrors *teew → weesu* in the Wolof *dégtal* model taught in the L1 session.

Total cost: 2 KG calls. Objectives, sub-skills, and the Palier 1 prerequisite are all sourced.

---

## 3. Palier 2 framing

Briefly state once that pupils are moving from **narrative texts** to **descriptive texts**.

Use one short genre bridge somewhere early in the week, preferably in Jour 1:

> Wolof example: **Bii ayu-bés, dunu nettali ay xew-xew. Danuy melool ay këfin.**
> French equivalent: **Cette semaine, nous n'allons pas raconter des événements. Nous allons décrire des objets.**

Do not turn this into a review lesson. Palier 1 knowledge may be briefly reactivated only when it directly supports new descriptive work. If `get_progression(uuid, direction="builds_from")` returns a non-empty result for the relevant Palier 2 standard (`progressionAvailability: "edges_present"`), use that prerequisite as a brief reminder. If it returns `progressionAvailability: "no_edges_found"`, narrate the bridge from the genre shift alone (narrative texts → descriptive texts) without inventing a Palier 1 anchor that the KG did not supply.

---

## 4. Required Week 10 content map

Use the table below as the content checklist. Do not pull in neighboring-week content unless a source explicitly requires it.

| Strand/session | Required Week 10 content |
|---|---|
| **Expression Orale L1 / Waxinu Lammiñ** | *Melool këfin / Décrire un objet*. Describe an object, its parts, characteristics, and functions. Focus this week: respect of theme and articulation. |
| **Expression Orale L2** | Same objective in French: describe a familiar object, its parts, characteristics, and functions. |
| **Poésie-Récitation L1/L2 / Tari-Taalif** | Restitute a **poem** from memory for Week 10, with expression, suitable intonation, and gesture. Do not switch to prose unless an explicit source requires it. |
| **Compréhension à l'Audition L1/L2** | Understand short descriptive texts read aloud. Build mental images from information in the text. Identify what the text is about using CE1-friendly wording. |
| **Compréhension Écrite L1/L2** | Read and understand short descriptive texts. Use mental images and central-subject identification. |
| **Vocabulaire L1 / Baataan** | Use vocabulary linked to a descriptive text and Week 10 Wolof descriptive structures: **dafa… / dañu, dañoo…**, **am na… / am nañu**. |
| **Vocabulaire L2** | Acquire words linked to the descriptive theme and use them in meaningful sentences. Two distinct sessions are required. |
| **Grammaire L1 / Róofoo gi Baat** | Recognize Wolof possessive markers: **sama**, **sa**, **-am**. |
| **Grammaire L2** | Recognize possessive adjectives: **mon, ma, mes / ton, ta, tes / son, sa, ses**. |
| **Conjugaison L1 / Demalin Waxe** | Conjugate in **dégtal / énonciatif sotti weesu** and recognize the **-oon** marker. |
| **Conjugaison L2** | Conjugate first-group verbs in the **imparfait de l'indicatif** and identify regularities by person. |
| **Orthographe L1 / Tëralinu Mbind** | Use possessive markers in writing, especially **sama… / -am**, as stated in the progression; reinforce **sa** only as linked grammar/transfer. |
| **Orthographe L2** | Apply the general feminine rule for nouns and adjectives. |
| **Production d'Écrits L1/L2 / Nasum Mbind** | First identify characteristics of descriptive texts: object/subject described, parts, qualities, function/use, precise vocabulary. Then produce short guided descriptive sentences. Do not require full descriptive compositions in Week 10. |
| **Écriture / Mbindin** | Practice **cursive capital letters P, B, D, F, R, L, S** and copy short descriptive texts (3–4 lines) with varied copy types, using words/sentences linked to the week's descriptive and language-tool targets. |
| **Identification des Mots Fréquents L2** | Continue the Palier 2 high-frequency-word program. Use a new batch; include useful descriptive words when possible. |
| **Développer la Fluidité L1/L2** | Read a short descriptive text aloud with speed, accuracy, and expression. |
| **Remédiation CGP L1/L2** | Continue targeted phonics remediation based on diagnostic needs. Choose a small subset from the Palier 2 reinforcement set, e.g. **au, eau, eu, en, an, em, am, ai, ei, in, on, om, ien, oin, ion, ain, aim, oir, ch, gn, ph, qu, gu**. |

Note on Vocabulaire L1: Treat **dafa / dañu / dañoo** and **am na / am nañu** as descriptive-production structures practiced in context, not as isolated vocabulary items.

### 4.1 Descriptive text variety requirement

Compose or select **at least 4 distinct descriptive texts** across the CA and CE sessions (minimum 2 in L1, minimum 2 in L2). Each text must describe a **different type of familiar object or setting** — for example, a clothing item, a household tool, a food item, a place, a school object, a musical instrument. Do not reuse the same object across comprehension sessions.

All texts must use locally familiar Senegalese objects, foods, places, or school items — not abstract or unfamiliar items. Each text should be 4–8 sentences and exercise different descriptive vocabulary: color, shape, texture, size, parts, function. The variety ensures students encounter multiple descriptive models and are not over-fitted to a single object's characteristics.

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

For sessions where the anchor table in §2.4 says "✅ Yes," the *Nisaru jukki bi / Objectif spécifique* lines come from the KG's learning components for that standard. For sessions marked "❌ No," derive them from the progression document's topic description in CE1-friendly wording.

For each selected KG learning component, make the alignment visible in three places: the objective line, one direct activity in Découverte/Structuration/Entraînement, and the Évaluation success criteria or expected answers.

Use the ARED regular-week logic: preparation/context, discovery, structuring, practice, and evaluation. The visible phase labels may follow the session-type house style in §6.2.

Semaine 10 is a regular instructional week, so the Évaluation phase is present.

For procedural/practice-heavy sessions — Poésie-Récitation, Écriture, Identification des Mots Fréquents, Fluidité, Remédiation CGP — keep the same regular-week logic if needed, but use the compact session-type phase labels in §6.2 instead of inventing artificial rule-heavy phases.

### 6.1 Embedded mini-template for session layout and classroom-script style

Use this mini-template as the built-in format reference. Do **not** require or rely on any separate external layout document.

```md
SEMAINE 10 — JOUR X
Séance : N        [session title in the required language(s)]        Durée : 30 mn

Sumb / Palier : [competency or palier anchor]
Nisaru njàng mi / Objectif d'apprentissage : [broad learning objective]
Nisaru jukki bi / Objectif spécifique : [session-specific objective, sourced from KG LC when §2.4 says ✅]
Ëmb bi / Contenu : [Week 10 content target]
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
- Do not use shortcut phrases such as “E déroule la leçon…” or “faire la même procédure” unless the full procedure is already visible in the same session.
- Do not copy old narrative content or old Week 1–8 texts. Semaine 10 content must be descriptive and aligned to §4.


### 6.2 Existing guide house style patterns

The generated DOCX must be self-contained and must not rely on any external example guide. Use the layout patterns below as the embedded house style for Semaine 10. Keep the two-column teacher/student table structure, but choose phase labels that fit the session type instead of forcing every session into identical labels.

| Session type | Preferred phase labels / visual flow |
|---|---|
| **Expression orale** | `Waajal gi / Phase d’appropriation et de préparation` → `Wax sa xalaat / Production libre des élèves` → `Leeral gi / Explication ou production dirigée` → `Natt / Évaluation` |
| **Compréhension à l’Audition** | `Étape 1 : Découvrir le vocabulaire` → `Étape 2 : Lire l’image` → `Étape 3 : Écouter la lecture du texte` → `Étape 4 : Travailler la compréhension` |
| **Compréhension Écrite** | `Étape 1 : Émettre des hypothèses de lecture` → `Étape 2 : Définir et utiliser des mots` → `Étape 3 : Étude de la stratégie de compréhension` → `Étape 4 : Lecture du texte` → `Étape 5 : Comprendre le texte` |
| **Vocabulaire** | `Présentation de la situation / Woneb cëslaayu njàng mi` → `Lecture silencieuse contrôlée` → `Lecture du maître` → `Lecture par 2–3 élèves` → `Compréhension générale` → `Étude des mots ou structures ciblés` → `Natt / Évaluation` |
| **Grammaire / Orthographe / Conjugaison** | `Nafar / Révision` when useful → `Cóobute / Corpus ou situation de départ` → `Caytu / Observation et manipulation` → `Tënk sàrt yi / Synthèse ou règle` → `Tàggatu / Entraînement` → `Natt / Évaluation` |
| **Poésie-Récitation** | `Nafar / Révision` → `Woneb taalif bi / Présentation du poème` → `Déggin / Compréhension` → `Njàng / Apprentissage par répétition` → `Natt / Évaluation` |
| **Production d’Écrits** | `Cóobute / Projet d’écriture ou situation` → `Gëstu ak settantal / Recherche et analyse avec grille` → `Tënk sàrt yi / Synthèse des critères` → `Tàggatu / Réinvestissement guidé` → `Natt / Évaluation` |
| **Identification des Mots Fréquents** | `Étape 1 : Présenter le mot` → `Étape 2 : Écrire le mot` with `Je fais / Nous faisons / Tu fais` → `Étape 3 : Lire le mot à haute voix` with `Je fais / Nous faisons / Tu fais` → `Étape 4 : Afficher et répertorier le mot` |
| **Écriture / Copie** | `Modèle` → `Observation du tracé` → `Tracé dans l’air ou sur ardoise` → `Copie dans le cahier` → `Correction / Valorisation` |
| **Fluidité** | `Modèle de lecture` → `Lecture chorale` → `Lecture en binômes` → `Lecture individuelle` → `Feedback sur vitesse, exactitude, expression` |
| **Remédiation CGP** | Use a fiche-style structure: diagnostic difficulty, remediation objective, target group, modality, means/documentation, then teacher/student table with `Passation des consignes`, `Mise en situation du groupe de besoin`, `Entraînement / Renforcement`, `Contrôle du travail`, `Consolidation et évaluation de l’effet de la remédiation`. |

These labels are not decorative. They should shape the visible layout of each session. Keep them concise, and do not add long explanations under each label unless needed for classroom use.

### 6.3 KG evidence pass + two-pass generation requirement

Generate the guide through this sequence:

**Pass 0 — KG Evidence Pass.** Complete §2.5 before drafting. Do not begin Pass 1 until the private KG Evidence Table is complete. The evidence pass for each unique ✅ Yes UUID consists of: (a) `get_learning_components_for_standard` for the LCs; (b) for the rows in §2.5.1, `get_progression` with `direction="builds_from"` plus inspection of `progressionAvailability`; (c) for **palier-scoped** standards (Expression Orale, Compréhension à l'Audition, Compréhension Écrite, Récitation, Fluidité), `get_aux_statements` for the per-week guidance and duration metadata. Skip step (c) for week-grouped standards — their week-specific content is already encoded in the standard description and LCs.

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

| Element | Required formatting |
|---|---|
| French text | Default black |
| Wolof / national-language text | Consistent readable dark blue via named character style, e.g. **`Wolof / Langue nationale`** |
| Teacher stage directions | Italics |
| Teacher speech to pupils | Regular, non-italic text |
| Rules/patterns to remember | Framed box / bordered callout |
| L1↔L2 transfer activity | Mark with 🔁 or the project's chosen transfer pictogram |
| Expected answers | Same bullet marker throughout the whole guide |
| Session/phase labels | Bilingual where appropriate |

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

Required Week 10 transfer placements:

| Pair | Required transfer activity | Minimum placement |
|---|---|---|
| Grammaire L1: **sama / sa / -am** ↔ Grammaire L2: **mon/ma/mes, ton/ta/tes, son/sa/ses** | Guided translation + parallel-structure comparison | Grammaire L2 |
| Conjugaison L1: **dégtal / énonciatif sotti weesu** and **-oon** ↔ Conjugaison L2: **imparfait** | Parallel comparison + bilingual reformulation | Conjugaison L2 |
| Orthographe L1 possessives ↔ Orthographe L2 feminine rule | Contrast activity: how each language marks meaning/form differently | Orthographe L2 |
| Vocabulaire L1 ↔ Vocabulaire L2 | Bilingual reformulation using the same familiar object | Vocabulaire L2 Session 1 or 2 |
| Expression Orale L1 → Expression Orale L2 | Re-describe the same object in French | Expression Orale L2 |
| Compréhension L1 → Compréhension L2 | Reuse mental-image and "What is the text about?" strategies | CA/CE L2 sessions |

Mark each explicit transfer activity with **🔁**.

### 9.1 Transfer operationalization test

For each 🔁 transfer activity, verify that the script includes an **explicit pupil production step** — something the pupil says, writes, translates, or compares. If the transfer only contains a teacher statement ("In wolof, this corresponds to…") without a pupil action, it fails the operationalization test and must be rewritten.

Acceptable pupil actions include:

- "Traduisez : Sama téere → ? (Mon livre)"
- "Compare the two paradigms side by side: what changes, what stays the same?"
- "Describe the same object first in wolof, then in French."

A minimum of **4 of the 6 required transfer pairs** must include an explicit pupil translation or comparison task, not just a teacher-stated observation.

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
| "Adjectif possessif" before explanation | "Petit mot qui dit à qui c'est," then introduce the term |

Technical terminology may appear in teacher-facing notes and rule boxes, but pupil-facing speech must introduce or gloss technical terms.

---

## 12. Wolof quality rules

Wolof must be native-speaker acceptable.

Check especially:

- correct tense/aspect; do not use present forms for completed past actions;
- correct use of Week 10 forms: **sama**, **sa**, **-am**, **-oon**, **dafa**, **dañu / dañoo**, **am na / am nañu**;
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
- Do not repeat broad Palier 2 explanations inside every session.
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
| Didactic relevance | Palier 2 descriptive focus, clear progression. **Every content-rich session (minimum 12 of 22) must include at least one specific likely pupil error and a corrective feedback line in Wolof and/or French.** Generic errors ("the student doesn't understand") do not count. The error must be specific to the content being taught. See Appendix A for calibration examples. |
| Disciplinary accuracy | Exact Week 10 content, correct Wolof/French grammar and spelling |
| Learner level | CE1-appropriate tasks, manageable cognitive load, and at least one easy/standard/challenge variant in the main practice or evaluation of content-rich sessions where differentiation is pedagogically useful |
| Language management | Simple pupil-facing language, context/instruction separation, bilingual coherence |
| Transfer | Operationalized L1↔L2 activities, not passing mentions (see §9.1 operationalization test) |
| Classroom usability | Teacher/student paired rows, concrete scripts, expected answers or success criteria |
| Controllability | Keep the DOCX easy to edit: clear session boundaries, editable tables, visible objectives/content/evaluation fields, no hidden dependencies, no unexplained generated placeholders |
| Reliability | Reduce generation variability by following the source hierarchy, UUID anchor table, fixed timetable, required content map, and final QA gate exactly |
| Concision | Scannable DOCX, no repeated boilerplate, page target respected |

Rubric-critical safeguards: content-rich sessions include likely pupil errors and a short feedback line; evaluations include expected answers or success criteria; technical terms are glossed in CE1-friendly language before labels; KG learning components shape objectives, activities, and evaluations instead of appearing only as metadata. Also vary task types, avoid over-reliance on repetition, and include brief alternative reformulations when pupils are likely to misunderstand a concept.

---

## 15. Subject-pattern reminders

Use the embedded mini-template in §6.1 plus the recurring patterns below. The patterns guide the flow of each session; they are not permission to add padding.

| Session type | Pattern to preserve |
|---|---|
| Oral expression | object observation → free production → guided production → evaluation |
| Listening comprehension | vocabulary/image/text listening → comprehension questions → mental image and "what is it about?" |
| Reading comprehension | prediction → key words → strategy → reading → understanding |
| Vocabulary | corpus → reading/listening → target words/structures → sentence use → evaluation |
| Grammar/Orthography | corpus → manipulation → rule/pattern → guided practice → evaluation |
| Conjugation | corpus → manipulation → target marker/endings → paradigm/pattern → practice |
| Written production | model text/criteria → identify descriptive features → short guided sentences |
| Recitation | present poem → understand → repeat/memorize → recite with expression |
| High-frequency words | present words → repeat/read → flash recognition → sentence use |
| Handwriting | model → air/slate tracing → notebook practice → correction |
| Fluency | model reading → choral reading → paired reading → individual reading → feedback |
| CGP remediation | diagnostic grouping → targeted decoding → supported reading → reassessment |

These patterns are structure aids, not padding.

---

## 16. Final QA gate

Before final delivery, verify the items below. The list is intentionally short and orders the highest-impact pedagogical checks first.

### Pedagogical depth gate (must all pass before formatting)

- [ ] At least **12 content-rich sessions** include a session-specific pupil error with corrective feedback (not generic — see Appendix A).
- [ ] At least **4 distinct descriptive texts** are used across CA/CE sessions, each describing a different type of object or setting (§4.1).
- [ ] At least **4 of 6 required transfer activities** include an explicit pupil production step (§9.1).
- [ ] Every Grammar, Conjugation, and Orthography session includes a manipulation activity in Découverte, before the rule is formulated.
- [ ] Every Évaluation phase includes either expected answers or explicit success criteria.
- [ ] Content-rich sessions include easy/standard/challenge variants where differentiation is pedagogically useful.
- [ ] The DOCX remains easy to edit: clear session boundaries, editable tables, visible objectives/content/evaluation fields, and no unexplained placeholders.
- [ ] A private KG Evidence Table was completed before drafting.
- [ ] KG learning components were retrieved for every unique "✅ Yes" UUID in the §2.4 anchor table.
- [ ] For every palier-scoped "✅ Yes" UUID (Expression Orale, Compréhension à l'Audition, Compréhension Écrite, Récitation, Fluidité), `get_aux_statements` was called and the per-week `Contenus` text was used to populate the *Ëmb bi / Contenu* line of the corresponding session.
- [ ] Every selected KG learning component appears in objective + activity + evaluation.
- [ ] Required `builds_from` progressions were retrieved and used only as brief bridges, reminders, or transfer supports. Where the call returned `progressionAvailability: "no_edges_found"`, the bridge was narrated from genre framing alone — no Palier 1 anchor was fabricated.
- [ ] No KG decomposition was used for the known mismatch sessions: Grammar L1/L2, Conjugaison L1, Orthographe L1.
- [ ] `search_items` was not used for any UUID already listed in §2.4. Where `search_items` was used, `path_segment` was preferred over post-filtering on `canonicalPathKey`.

### Timetable validation

- [ ] The guide begins with a validation table for all 22 reading sessions.
- [ ] All 22 sessions appear exactly once, except Vocabulaire L2 which appears as two distinct sessions.
- [ ] No non-reading subjects are generated.
- [ ] Day, order, language scope, and duration match §5.
- [ ] Expression Orale L1 is only Jour 1; Expression Orale L2 is only Jour 5.
- [ ] Remédiation CGP L1/L2 is the only remediation session generated (60 mn).

### Content fidelity (one-pass spot check)

- [ ] Grammar L1 teaches **sama, sa, -am**; Grammar L2 teaches **mon/ma/mes, ton/ta/tes, son/sa/ses**.
- [ ] Conjugaison L1 uses the Wolof model name **dégtal / énonciatif sotti weesu** with **-oon** (not "imparfait"); Conjugaison L2 uses **imparfait de l'indicatif**.
- [ ] Orthographe L2 teaches the general feminine rule.
- [ ] The narrative-to-descriptive bridge appears once, briefly, in Jour 1.
- [ ] Wolof target forms are correct: **sama, sa, -am, -oon, dafa, dañu / dañoo, am na / am nañu**; no Wolof sentence begins with **Te**; diacritics preserved.

### Concision

- [ ] The guide is 22–28 pages and visually scannable.
- [ ] No repeated palier-level boilerplate inside every session.
- [ ] The source hierarchy, UUID anchor table, fixed timetable, content map, and QA gate were followed exactly to reduce generation variability.

---

## 17. Delivery

Deliver **only** the complete Semaine 10 teacher guide as a DOCX. No rationale, methodology note, or compliance checklist alongside it. The teacher-facing DOCX should not include intrusive citations.

---

## Appendix A — Error anticipation calibration examples

To calibrate the expected specificity of error anticipation, use these examples. Errors in the ❌ column are too generic to improve teaching; errors in the ✅ column are specific enough to help a teacher recognize and correct the problem in real time.

| Session | ❌ Too generic | ✅ Specific enough |
|---|---|---|
| Expression Orale | "Student has difficulty describing" | "Student narrates an event instead of describing an object. Correction: *Danuy melool rekk, dunu nettali. Waxal naka mu mel.*" |
| Grammaire L1 | "Student confuses possessives" | "Student writes -am as a separate word (*mbubb am*) instead of suffixing it (*mbubbam*). Correction: *-am dafay toftal ci birim baat bi.*" |
| Conjugaison L1 | "Student makes conjugation errors" | "Student adds -oon to the verb (*jàngoon*) instead of the pronoun-aspect marker (*dafoon jàng*). Correction: *-oon dafa tëral ci dafa/dama/dañu, du ci waxe bi.*" |
| Vocabulaire L1 | "Student doesn't use structures" | "Student uses *dafa* for plural subjects. Correction: *Benn këfin: dafa. Ñaari këfin: dañu.*" |
| Orthographe L2 | "Student forgets the rule" | "Student writes *grande* for a masculine noun. Correction: -e is only added for the feminine form." |
| Grammaire L2 | "Student confuses possessives" | "Student uses *mon* before a feminine noun (*mon trousse*). Correction: *mon* = masculin, *ma* = féminin → *ma trousse*. Exception: *mon amie* (before a vowel)." |
