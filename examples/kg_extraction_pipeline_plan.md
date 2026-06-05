Big picture: we are building a small factory.

The `DocumentIR` is the raw material. It already has the PDF content stitched into readable segments: headings, paragraphs, tables, rows, cells, page provenance, and bounding boxes.

The KG output is the finished product:

```text
StandardsFramework
StandardsFrameworkItem
LearningComponent
Relationship
```

The pipeline’s job is to turn the stitched document material into those KG objects without pretending Python understands every country’s curriculum.

## The overall plan

### 1. Start with the stitched `DocumentIR`

Think of `DocumentIR` as:

> “Here is everything from the PDF, in reading order, with tables stitched across pages.”

It does **not** yet know what is a standard, what is an example, what is guidance, or what is a competency.

For Ghana, it contains things like:

```text
CONTENT STANDARDS | INDICATORS AND EXEMPLARS | CORE COMPETENCIES
```

For Zambia, it might contain:

```text
TOPIC | SUB-TOPIC | SPECIFIC COMPETENCES | LEARNING ACTIVITIES | EXPECTED STANDARD
```

For Senegal, it might contain bilingual Wolof/French tables with paliers, weeks, objectives, content, and durations.

### 2. Load the country profile

The `DocumentProfile` is the instruction sheet.

It says:

> “For this country and curriculum, here is what counts as an SFI, here are the code patterns, here is what not to treat as a standard, and here is how to ask the LLM.”

For Ghana, the profile says:

```text
BASIC 4/5/6 → grouping SFIs
Strands/Sub-strands → grouping SFIs
B4.1.1.1 → content standard SFI
B4.1.1.1.1 → indicator SFI
E.g. examples → auxiliary, not SFI
Core competencies → auxiliary, not SFI
```

This is how we avoid hardcoding Ghana logic into the entire pipeline.

### 3. Preflight the run

Before doing real work, Python checks:

```text
Does the profile load?
Does the DocumentIR load?
Does the profile match the document?
Do the code patterns actually match content?
How many tables/blocks are there?
Which table shapes are present?
```

This produces `run_manifest.json`.

That is the sanity check. It tells us, “Yes, we’re applying the Ghana math profile to the Ghana math document.”

### 4. Cut the document into extraction windows

The PDF is too big to send to an LLM all at once.

So Python chops the `DocumentIR` into small, stable chunks called **extraction windows**.

A window is just a prompt payload:

```text
Here is the heading context.
Here are the table headers.
Here are 20 rows.
Here is provenance for each row/cell.
```

For Ghana, we first target standards tables only:

```text
CONTENT STANDARDS
INDICATORS AND EXEMPLARS
SUBJECT SPECIFIC PRACTICES AND CORE COMPETENCIES
```

So instead of asking the LLM to read the whole curriculum, we ask it to read one table chunk at a time.

Output:

```text
extraction_windows.jsonl
```

### 5. Ask the LLM: “What are the SFI candidates?”

Each window goes to the LLM with the profile instructions.

The LLM returns **candidates**, not final KG nodes.

For example, from one Ghana row, it might return:

```text
Candidate A:
  code: B4.3.3.1
  type: Content Standard
  text: Demonstrate understanding of perimeter...
  normalized type: Standard

Candidate B:
  code: B4.3.3.1.1
  type: Indicator
  text: Estimate perimeter using referents...
  parent code: B4.3.3.1

Candidate C:
  code: B4.3.3.1.2
  type: Indicator
  text: Measure and record perimeter...
  parent code: B4.3.3.1
```

It may also return auxiliary stuff:

```text
E.g. examples
Core competencies
Teacher guidance
Activities
Expected standards
Durations
```

Those are not final SFIs unless the profile says they should be.

Output:

```text
sfi_extraction_results.jsonl
```

### 6. Put all candidates into a global registry

This is the important part for a windowed approach.

Each window only sees a small part of the document. So after all windows run, Python collects every candidate into one global registry.

The registry answers:

```text
How many raw candidates did we get?
Which ones have the same code?
Which ones look like duplicates?
Which ones conflict?
Which ones are unresolved?
```

Output:

```text
sfi_candidate_registry.json
```

### 7. Merge duplicates globally

Windows can produce the same SFI more than once.

For coded curricula like Ghana and Zambia, Python can merge by code:

```text
B4.1.1.1 + B4.1.1.1 → same standard, merge provenance
```

For no-code curricula like Senegal, Python builds conservative synthetic merge keys:

```text
country + subject + grade/stage + role + context + normalized text
```

For tricky cases, Python creates duplicate buckets and asks the LLM:

> “Are these the same curriculum item, distinct recurring items, or a conflict?”

This gives us a final clean list of SFIs.

Output:

```text
sfi_merge_report.json
duplicate_review_requests.jsonl
duplicate_review_responses.jsonl
```

### 8. Mint final SFI IDs

Only after merging do we create final SFI IDs.

That prevents duplicate KG nodes.

IDs are deterministic:

```text
lc:curriculum:{doc_key}:sfi:code:{statement_code}:{text_hash}
```

Then converted to UUIDv5 for the KG schema.

So the same source document produces the same IDs every time.

### 9. Remap parent-child edges

The LLM gave parent hints earlier, but those hints pointed to temporary candidates.

After merging, Python remaps them to final SFI IDs.

For Ghana:

```text
B4.1.1.1.1 → parent is B4.1.1.1
```

So Python creates:

```text
Content Standard hasChild Indicator
```

It also creates top-level hierarchy:

```text
StandardsFramework hasChild Basic 4
Basic 4 hasChild Strand 1
Strand 1 hasChild Sub-strand 1
Sub-strand 1 hasChild Content Standard
Content Standard hasChild Indicator
```

Output:

```text
final_has_child_edges
unresolved_edges.json
```

### 10. Compile the Academic Standards KG objects

Now Python creates actual KG schema objects:

```text
StandardsFramework
StandardsFrameworkItem
Relationship(hasChild)
```

At this point, we have the Academic Standards KG shape.

This is the first real KG product.

### 11. Generate LearningComponents

Once final SFIs exist, we ask the LLM a second kind of question:

> “Break this standard into atomic skills.”

Example SFI:

```text
Estimate perimeter using referents for centimetre or metre.
```

Possible LearningComponent:

```text
Estimate the perimeter of a shape using centimetre or metre referents.
```

Each LearningComponent gets a deterministic ID and a `supports` relationship back to the SFI:

```text
LearningComponent supports StandardsFrameworkItem
```

Output:

```text
LearningComponent
Relationship(supports)
```

### 12. Validate and export everything

Finally, Python checks:

```text
Do all objects match the KG schemas?
Do all relationships point to real objects?
Are there no self-loops?
Are there no cycles in hasChild?
Does every SFI have provenance?
Does every LearningComponent support an SFI?
```

Then it writes:

```text
kg_export_bundle.json
entity_provenance.json
validation_report.json
unresolved_items.json
```

## The whole thing in one simple flow

```text
DocumentIR
  ↓
Country profile tells us how to read this curriculum
  ↓
Python cuts the document into small windows
  ↓
LLM extracts SFI candidates from each window
  ↓
Python collects candidates globally
  ↓
Python + bounded LLM review merge duplicates
  ↓
Python creates final StandardsFrameworkItems
  ↓
Python remaps hasChild edges
  ↓
Python creates StandardsFramework + SFI + hasChild KG objects
  ↓
LLM decomposes final SFIs into LearningComponents
  ↓
Python creates supports relationships
  ↓
Python validates and exports the KG bundle
```

## ELI5 version

Imagine the PDF is a messy box of LEGO pieces.

`DocumentIR` sorted the LEGO pieces by page and table, but it does not know what you are building.

The `DocumentProfile` is the instruction booklet for that country.

Python makes small piles of LEGO pieces so the LLM can inspect them.

The LLM says, “These pieces are standards. These are examples. These are activities. These are just labels.”

Python then says, “Cool, but before we build the final model, let’s remove duplicates, give every real piece a permanent ID, and connect the pieces in the right order.”

Then Python builds the final KG:

```text
Framework → Grade → Strand → Standard → Indicator
```

Then the LLM breaks each standard into smaller skill pieces:

```text
Standard → LearningComponents
```

And Python connects them:

```text
LearningComponent supports Standard
```

That’s the plan. Keep Python as the careful builder, and let the LLM do the curriculum interpretation.
