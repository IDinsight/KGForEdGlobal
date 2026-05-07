# Concise Instructions for Generating the Teacher Guide for Senegal Grade 3 Reading — Semaine 10

## 1. Task

Generate the complete teacher guide for **Semaine 10**, covering **Jour 1 through Jour 5**.

Semaine 10 is:

- the **first regular instructional week of Palier 2 / Jéego 2**;
- the week that introduces **descriptive-text work** for the first time;
- a **model-week-level opening guide** for the palier, unless the Knowledge Graph or an explicit ARED/MEN source confirms that it is officially a fully scripted model week;
- **not** an integration week, revision week, evaluation week, or a single numbered “Lesson 10.”

The guide must be classroom-usable, bilingual where appropriate, visually scannable, and concise. The full DOCX should normally land around **22–28 pages**. A 30-page guide is acceptable only if the extra length is pedagogically necessary. A 50-page guide is a failure of concision.

---

## 2. Source-of-truth hierarchy

When sources conflict, apply this hierarchy.

| Decision area | Source of truth | Rule |
|---|---|---|
| Session placement, order, duration, and language scope | Embedded MEN timetable in this file | Always wins |
| Palier framing, oral expression, reading/listening comprehension, recitation, Production d’Écrits, Écriture, broad competencies, genre arc | `edu-kg` Connector | Use for curriculum anchors, CE1 source content, and learning objectives (see §2a) |
| Exact Week 10 L1 language-tool content: vocabulary, grammar, conjugation, orthography | `reading_wolof_progression.docx` | Wins over KG for L1 language tools — the progression document uses Wolof-native conjugation models that the KG does not capture (see §2b) |
| Exact Week 10 L2 language-tool content: vocabulary, grammar, conjugation, orthography | `reading_wolof_progression.docx`, cross-checked with `edu-kg` | The progression document’s L2 columns and the KG’s French-framed standards generally agree for L2; use the progression document for week assignment, the KG for learning-objective decomposition |
| Teacher-guide format, tone, classroom-script style | `reading_teacher_guide_grade_3_lesson_1_to_8.docx` | Mirror format while replacing narrative content with Week 10 descriptive content |
| Wolof terminology | Wolof terminology reference, if provided | If missing, flag limitation and use conservative, consistent terms |
| Evaluation optimization | Embedded rubric priorities in this file | Guides quality only; does not override curriculum, progression, or timetable |

### 2a. Using the KG effectively

**Learning objectives for session metadata.** The KG contains 462 learning components that decompose standards into assessable sub-skills (e.g., “Conjuguer au présent de l’indicatif” decomposes into “Repérer les régularités de conjugaison selon les personnes” + “Conjuguer des verbes d’action du 1er groupe au présent”). Use the KG’s `get_objectives` tool to source *Nisaru njàng mi / Objectif d’apprentissage* and *Nisaru jukki bi / Objectif spécifique* for each session, especially for KG-governed sessions (oral expression, comprehension, production d’écrits, etc.).

**Week-grouping nodes.** Some KG standards have parent nodes whose descriptions are week numbers (e.g., “10”, “11”). When a KG standard’s parent chain includes the grouping node “10,” that standard is a candidate for Week 10 content. Use these groupings to cross-check which KG standards belong to this week.

**Progression chains for sequencing rationale.** The KG contains `buildsFrom` and `buildsTowards` links between standards. Use these to confirm sequencing decisions — for example, verifying that descriptive-text comprehension builds from narrative-text comprehension, or that imparfait builds from présent. These links can inform the Palier 2 bridge (§3) and transfer activities (§10).

**Duplicate-standard disambiguation.** Many KG standards appear multiple times with different UUIDs across paliers (e.g., “Conjuguer au présent de l’indicatif” appears in both Palier 1 and Palier 2 week groupings). When multiple KG standards share the same or near-identical description, prefer the instance whose parent chain includes a Palier 2 grouping node or the week-10 grouping node.

### 2b. Why the L1 language-tool divergence matters

The KG and the progression document diverge specifically for **L1 language-tool content** because they use different linguistic frameworks:

- The KG frames Wolof conjugation through **French grammatical categories** (présent de l’indicatif, imparfait, futur, passé composé, “verbes du 1er groupe”). These categories do not accurately describe the Wolof verbal system.
- The progression document frames Wolof conjugation through **Wolof-native categories**: conjugation models (*nettali*, *dégtal*, *wonale*, *santaane*) crossed with aspect (*sotti*/*sotteedi* = accomplished/unaccomplished) and time (*teew*/*weesu* = present/past). For example, what the KG calls “imparfait” maps to multiple distinct Wolof teaching items depending on the conjugation model.

For **L2 content**, this divergence does not apply — French conjugation *is* organized by tense, so the KG’s French framing is appropriate for L2 sessions.

**Known Week 10 divergence to flag in the rationale:**
The KG-backed CE1 curriculum-source content and `reading_wolof_progression.docx` diverge for Week 10 L1 language-tool items. The divergence is primarily in conjugation: the progression document specifies *dégtal / énonciatif sotti weesu* with the *-oon* marker, while the KG frames the equivalent content as “Conjuguer à l’imparfait de l’indicatif.” For generated teacher guides, use `reading_wolof_progression.docx` for exact Week 10 L1 language tools and Wolof-native model names. Use the KG-backed CE1 content for Production d’Écrits, Écriture, oral expression, recitation, comprehension objectives, and L2 learning-objective decomposition. Explicitly note this intentional split in the accompanying rationale.

---

## 3. Palier 2 framing

At week level, briefly state that pupils are moving from **narrative texts** to **descriptive texts**.

Use one short genre bridge somewhere early in the week, preferably in Jour 1:

> Wolof example: **Bii ayu-bés, dunu nettali ay xew-xew. Danuy melool ay këfin.**
> French equivalent: **Cette semaine, nous n’allons pas raconter des événements. Nous allons décrire des objets.**

Do not turn this into a review lesson. Palier 1 knowledge may be briefly reactivated only when it directly supports new descriptive work.

The KG’s `buildsFrom` progression links can confirm what Palier 1 skills the new descriptive work builds on (e.g., the KG shows that descriptive-text comprehension standards build from narrative-text comprehension standards, and that Palier 2 conjugation standards build from Palier 1 present-tense standards). Use these links to ground the bridge in curriculum structure rather than generating it from general knowledge.

---

## 4. Required Week 10 content map

Use the table below as the content checklist. Do not pull in neighboring-week content unless a source explicitly requires it.

| Strand/session | Required Week 10 content |
|---|---|
| **Expression Orale L1 / Waxinu Lammiñ** | *Melool këfin / Décrire un objet*. Describe an object, its parts, characteristics, and functions. Focus this week: respect of theme and articulation. |
| **Expression Orale L2** | Same objective in French: describe a familiar object, its parts, characteristics, and functions. |
| **Poésie-Récitation L1/L2 / Tari-Taalif** | Restitute a **poem** from memory for Week 10, with expression, suitable intonation, and gesture. Do not switch to prose unless an explicit source requires it. |
| **Compréhension à l’Audition L1/L2** | Understand short descriptive texts read aloud. Build mental images from information in the text. Identify what the text is about using CE1-friendly wording. |
| **Compréhension Écrite L1/L2** | Read and understand short descriptive texts. Use mental images and central-subject identification. |
| **Vocabulaire L1 / Baataan** | Use vocabulary linked to a descriptive text and Week 10 Wolof structures: **dafa… / dañu, dañoo…**, **am na… / am nañu**. Note: these are Wolof verbal-structure markers (specifically *dégtal* model paradigm markers and existential constructions), placed in the vocabulary column by the progression document as anchors for descriptive production. Treat them as structures to practice in context, not as isolated vocabulary items. |
| **Vocabulaire L2** | Acquire words linked to the descriptive theme and use them in meaningful sentences. Two distinct sessions are required. |
| **Grammaire L1 / Róofoo gi Baat** | Recognize Wolof possessive markers: **sama**, **sa**, **-am**. |
| **Grammaire L2** | Recognize possessive adjectives: **mon, ma, mes / ton, ta, tes / son, sa, ses**. |
| **Conjugaison L1 / Demalin Waxe** | Conjugate in **dégtal / énonciatif sotti weesu** and recognize the **-oon** marker. |
| **Conjugaison L2** | Conjugate first-group verbs in the **imparfait de l’indicatif** and identify regularities by person. |
| **Orthographe L1 / Tëralinu Mbind** | Use possessive markers in writing, especially **sama… / -am**, as stated in the progression; reinforce **sa** only as linked grammar/transfer. |
| **Orthographe L2** | Apply the general feminine rule for nouns and adjectives. |
| **Production d’Écrits L1/L2 / Nasum Mbind** | First identify characteristics of descriptive texts: object/subject described, parts, qualities, function/use, precise vocabulary. Then produce short guided descriptive sentences. Do not require full descriptive compositions in Week 10. |
| **Écriture / Mbindin** | Continue handwriting practice using words/sentences naturally linked to the week’s descriptive and language-tool targets. |
| **Identification des Mots Fréquents L2** | Continue the Palier 2 high-frequency-word program. Use a new batch; include useful descriptive words when possible. |
| **Développer la Fluidité L1/L2** | Read a short descriptive text aloud with speed, accuracy, and expression. |
| **Remédiation CGP L1/L2** | Continue targeted phonics remediation based on diagnostic needs. Choose a small subset from the Palier 2 reinforcement set, e.g. **au, eau, eu, en, an, em, am, ai, ei, in, on, om, ien, oin, ion, ain, aim, oir, ch, gn, ph, qu, gu**. |

---

## 5. Required MEN reading timetable

Generate exactly the reading sessions below. Preserve day, order, scope, and duration. Do not generate non-reading subjects.

The generated teacher guide must begin with a **short timetable validation table** confirming these 22 reading sessions.

| # | Day | Official day | Block | Session to generate | Scope | Duration |
|---:|---|---|---|---|---|---:|
| 1 | Jour 1 | Lundi | 8h–11h | Waxinu Lammiñ / Expression Orale L1 | L1 only | 30 mn |
| 2 | Jour 1 | Lundi | 8h–11h | Nàmm Deggin / Compréhension à l’Audition L1 | L1 only | 30 mn |
| 3 | Jour 1 | Lundi | 8h–11h | Compréhension à l’Audition L2 | L2 only | 30 mn |
| 4 | Jour 1 | Lundi | 11h30–13h | Baataan / Vocabulaire L1 | L1 only | 30 mn |
| 5 | Jour 1 | Lundi | 11h30–13h | Nasum Mbind / Production d’Écrits L1 | L1 only | 30 mn |
| 6 | Jour 2 | Mardi | 8h–11h | Tari-Taalif / Poésie-Récitation L1/L2 | L1/L2 combined | 30 mn |
| 7 | Jour 2 | Mardi | 8h–11h | Róofoo gi Baat / Grammaire L1 | L1 only | 30 mn |
| 8 | Jour 2 | Mardi | 8h–11h | Tëralinu Mbind / Orthographe L1 | L1 only | 30 mn |
| 9 | Jour 2 | Mardi | 11h30–13h | Dégginu Mbind / Compréhension Écrite L1 | L1 only | 30 mn |
| 10 | Jour 2 | Mardi | 11h30–13h | Compréhension Écrite L2 | L2 only | 30 mn |
| 11 | Jour 2 | Mardi | 15h–17h | Production d’Écrits L2 | L2 only | 30 mn |
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
- **Nisaru njàng mi / Objectif d’apprentissage**;
- **Nisaru jukki bi / Objectif spécifique**;
- **Ëmb bi / Contenu**;
- **Jumtukaay yi / Moyens**;
- **Sukkandikukaay / Documentation**.

For KG-governed sessions (oral expression, comprehension, production d’écrits, recitation, etc.), source the learning and specific objectives from the KG’s learning components via `get_objectives`. For progression-document-governed L1 language-tool sessions, derive objectives from the progression document’s content descriptions, using Wolof-native terminology where the session is L1.

Use the ARED 5-phase regular-week spine:

1. **Mise en situation**
2. **Découverte**
3. **Structuration**
4. **Entraînement**
5. **Évaluation**

Semaine 10 is a regular instructional week, so the Évaluation phase is present.

For procedural/practice-heavy sessions — Poésie-Récitation, Écriture, Identification des Mots Fréquents, Fluidité, Remédiation CGP — keep the same phase headings if needed, but compress Découverte and Structuration. Use a short strategy reminder instead of inventing an artificial rule.

---

## 7. Context vs. instruction inside *Mise en situation*

Inside every content-rich session, visibly separate:

| Label | Function | Example |
|---|---|---|
| **Context / Waral gi** | Sets the situation: what the class is doing today | “Aujourd’hui, nous allons décrire un objet familier.” / “Tey, dinanu melool ab këfin bu nu xam.” |
| **Instruction / Ndigël gi** | Tells pupils exactly what to do | “Décris le mbubb : sa couleur, sa forme, sa taille.” / “Meloolal mbubb mi : melo wi, melokaan wi, tolluwaay wi.” |

Do not blur context and instruction into one long prompt.

---

## 8. Teacher/student activity layout

Use paired teacher/student columns throughout:

| **Yëngute Muse bi / Activités du maître** | **Yëngute elew yi / Activités des élèves** |
|---|---|

Column symmetry is required. For every teacher action, the corresponding pupil action must appear on the same row. Do not write independent teacher and student lists that cannot be read across.

---

## 9. DOCX visual formatting rules

The final teacher guide is expected as a DOCX. Apply the visual code directly in the document.

| Element | Required formatting |
|---|---|
| French text | Default black |
| Wolof / national-language text | Consistent readable dark blue via named character style, e.g. **`Wolof / Langue nationale`** |
| Teacher stage directions | Italics |
| Teacher speech to pupils | Regular, non-italic text |
| Rules/patterns to remember | Framed box / bordered callout |
| L1↔L2 transfer activity | Mark with 🔁 or the project’s chosen transfer pictogram |
| Expected answers | Same bullet marker throughout the whole guide |
| Session/phase labels | Bilingual where appropriate |

Do not explain the visual code repeatedly inside the guide. Use the formatting itself.

---

## 10. L1↔L2 transfer requirements

Transfer must be a pupil activity, not a passing note. A sentence like “this corresponds to Wolof…” is not enough.

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
| Compréhension L1 → Compréhension L2 | Reuse mental-image and “What is the text about?” strategies | CA/CE L2 sessions |

Mark each explicit transfer activity with **🔁**.

---

## 11. Learner autonomy and manipulation

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

## 12. CE1-appropriate language

Teacher speech to pupils must use simple, age-appropriate wording.

| Avoid in pupil-facing speech | Use instead |
|---|---|
| “Identifier le sujet central du texte” | “De quoi parle le texte ?” |
| “Construire une représentation mentale” | “Ferme les yeux : que vois-tu ?” |
| “Analyser la structure syntaxique” | “Regarde la phrase. Qu’est-ce qui change ?” |
| “Adjectif possessif” before explanation | “Petit mot qui dit à qui c’est,” then introduce the term |

Technical terminology may appear in teacher-facing notes and rule boxes, but pupil-facing speech must introduce or gloss technical terms.

---

## 13. Wolof quality rules

Wolof must be native-speaker acceptable.

Check especially:

- correct tense/aspect; do not use present forms for completed past actions;
- correct use of Week 10 forms: **sama**, **sa**, **-am**, **-oon**, **dafa**, **dañu / dañoo**, **am na / am nañu**;
- consistent orthography and terminology;
- full word forms, e.g. **xew-xew**, not truncated **xew** when “event” is intended;
- no Wolof sentence begins with **Te**;
- use **Naka** where “how?” is intended; do not substitute **Noo** incorrectly;
- preserve accents and diacritics: **ñ, ŋ, à, é, ë**, etc.;
- prefer standard Wolof terms over French loans when the Wolof term is available;
- avoid mechanical French-to-Wolof calques.

Before finalizing any Wolof passage, correct tense, diacritics, morphology, natural phrasing, and consistency.

---

## 14. Concision rules

The guide must be dense and teachable, not bloated.

- Target **22–28 DOCX pages**.
- Default to **one page per 30-minute session**.
- Allow two pages only for rule-heavy sessions or the 60-minute CGP remediation.
- State week-level framing once, not in every session.
- Do not repeat broad Palier 2 explanations inside every session.
- Do not restate an instruction as both a long stage direction and a quoted teacher line.
- Use rule boxes, transfer pictograms, bullets, and italics instead of explanatory prose.
- Cut pedagogical meta-commentary such as “this phase is important because…”.
- Keep transitions short: name the phase and move on.

---

## 15. Evaluation-rubric priorities

Optimize for the embedded human-evaluation framework without reproducing the full rubric in the generated guide.

**Embedded Annex 7 scoring frame to preserve:** Didactic relevance 20%; disciplinary accuracy 15%; learner adaptation 10%; language management 15%; task quality 10%; explanation and feedback 10%; controllability 5%; pedagogical coherence 5%; contextual adaptation 5%; efficiency 3%; reliability 1%; ethics and bias 1%. Use these as generation priorities, not as visible headings in the teacher guide.

Use locally familiar Senegalese school/community examples and avoid gender, ethnic, socioeconomic, or cultural stereotypes.

| Priority | What the generated guide must show |
|---|---|
| Didactic relevance | Palier 2 descriptive focus, clear progression, likely pupil errors with corrective feedback |
| Disciplinary accuracy | Exact Week 10 content, correct Wolof/French grammar and spelling |
| Learner level | CE1-appropriate tasks, manageable cognitive load, easy/standard/challenge variants where useful |
| Language management | simple pupil-facing language, context/instruction separation, bilingual coherence |
| Transfer | operationalized L1↔L2 activities, not passing mentions |
| Classroom usability | teacher/student paired rows, concrete scripts, expected answers or success criteria |
| Concision | scannable DOCX, no repeated boilerplate, page target respected |

Rubric-critical safeguards: content-rich sessions include likely pupil errors and a short feedback line; evaluations include expected answers or success criteria; technical terms are glossed in CE1-friendly language before labels.

---

## 16. Subject-pattern reminders

Use the prior teacher guide’s recurring patterns, but rename phases into the ARED 5-phase structure.

| Session type | Pattern to preserve |
|---|---|
| Oral expression | object observation → free production → guided production → evaluation |
| Listening comprehension | vocabulary/image/text listening → comprehension questions → mental image and “what is it about?” |
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

Do not let these patterns add unnecessary length. They are structure aids, not padding.

---

## 17. Final QA checklist

Before final delivery, verify all items below.

### Curriculum and sources

- [ ] The guide is framed as the first week of Palier 2 and the first descriptive-text week.
- [ ] It is not written as an integration, revision, evaluation, or single-lesson guide.
- [ ] The guide covers Jour 1 through Jour 5.
- [ ] The narrative-to-descriptive bridge appears once, briefly.
- [ ] The source hierarchy is followed.
- [ ] The progression document governs exact Week 10 L1 language-tool content using Wolof-native model names.
- [ ] L2 language-tool content uses the progression document for week assignment and the KG for learning-objective decomposition.
- [ ] KG week-grouping nodes (parent = "10") were used to cross-check KG standard selection.
- [ ] KG learning components were used to source session objectives for KG-governed sessions.
- [ ] When duplicate KG standards were found, the Palier 2 / week-10 instance was preferred.
- [ ] The KG/progression L1 divergence is flagged in the rationale, with the linguistic-framework reason stated.

### Timetable

- [ ] The guide begins with a validation table for all 22 reading sessions.
- [ ] All 22 sessions appear exactly once, except Vocabulaire L2 which appears as two distinct sessions.
- [ ] No non-reading subjects are generated.
- [ ] Day, order, language scope, and duration match the MEN timetable.
- [ ] Poésie-Récitation, Écriture, and Fluidité are L1/L2 combined.
- [ ] Expression Orale L1 is only Jour 1; Expression Orale L2 is only Jour 5.
- [ ] Remédiation CGP is the only remediation session generated and lasts 60 mn.

### Week 10 content fidelity

- [ ] Oral expression focuses on object description plus respect of theme and articulation.
- [ ] CA/CE texts are descriptive and develop mental images and “De quoi parle le texte ?” strategies.
- [ ] Poésie-Récitation uses a poem, unless an explicit source requires prose.
- [ ] Grammar L1 teaches **sama, sa, -am**; Grammar L2 teaches **mon/ma/mes, ton/ta/tes, son/sa/ses**.
- [ ] Conjugation L1 teaches **dégtal / énonciatif sotti weesu** with **-oon** (using the Wolof model name, not the KG’s French equivalent “imparfait”); Conjugation L2 introduces the imparfait.
- [ ] Vocabulaire L1 structures (**dafa… / dañu, dañoo…**, **am na… / am nañu**) are treated as verbal structures practiced in context, not as isolated vocabulary items.
- [ ] Orthographe L1 reinforces possessive markers in writing, especially **sama… / -am**; Orthographe L2 teaches the general feminine rule for nouns and adjectives.
- [ ] Écriture continues handwriting practice with words/sentences linked to Week 10 descriptive and language-tool targets.

### Pedagogy and layout

- [ ] Content-rich sessions use the 5 ARED phases.
- [ ] Procedural sessions are compressed instead of padded.
- [ ] Context and instruction are separated in Mise en situation.
- [ ] Teacher/student columns are row-symmetric.
- [ ] Grammar, orthography, and conjugation include manipulation before rule formulation.
- [ ] At least one autonomous reinvestment activity appears each day.
- [ ] Production d’Écrits begins with identifying descriptive-text characteristics and moves only to short guided descriptive sentences.
- [ ] Content-rich sessions include likely pupil errors and corrective feedback.
- [ ] Differentiation appears where useful through easy / standard / challenge variants.
- [ ] Evaluation tasks include expected answers or success criteria.

### Bilingualism and language

- [ ] Transfer activities are scripted and marked with 🔁.
- [ ] Required L1/L2 transfer pairs are covered.
- [ ] Pupil-facing metalanguage is CE1-appropriate.
- [ ] New technical terms are explained simply before the technical label.
- [ ] Wolof target forms are correct: **sama**, **sa**, **-am**, **-oon**, **dafa**, **dañu / dañoo**, **am na / am nañu**.
- [ ] No Wolof sentence begins with **Te**.
- [ ] Wolof diacritics and terminology are consistent.

### DOCX formatting and concision

- [ ] Wolof uses the named character style/color.
- [ ] Teacher directions are italicized.
- [ ] Teacher speech is regular text.
- [ ] Rules/patterns appear in framed boxes where appropriate.
- [ ] Expected answers use one bullet style.
- [ ] The guide is visually scannable and normally within 22–28 pages.
- [ ] Repeated boilerplate and evaluator-facing meta-commentary have been removed.

---

## 18. Delivery expectations

Deliver:

1. The complete Semaine 10 teacher guide as a DOCX.
2. A brief rationale that states:
   - source hierarchy followed;
   - the KG/progression L1 divergence (Wolof-native conjugation models vs. French grammatical categories) and the intentional split between progression-governed L1 language tools, KG-backed CE1 objectives, and KG-assisted L2 learning-objective decomposition;
   - whether official model-week status was confirmed or whether the guide is only “model-week-level” because it opens Palier 2;
   - any missing-source limitations, especially missing Wolof terminology reference.
3. Optional: a compact checklist confirming timetable and formatting compliance.

The teacher-facing classroom guide should not include intrusive citations. The brief rationale may cite or name sources used when the output format allows.
