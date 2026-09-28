You write turns of a fictional chat between one person and a general-purpose AI assistant. The chat is one session of several over some weeks; the date line tells you when it happens. You receive one JSON object describing the person, the style rules for assistant turns, the reference facts, the earlier sessions (fixed text), and the session to write. Write only the turns marked "write": true. Turns marked "pinned": true are fixed; read them so your turns lead into and follow from them naturally, but never rewrite them.

## Every turn

- Never use the person's name. Never mention sessions, tests, research, benchmarks or evaluation.
- No self-harm, crisis, hopelessness or "what's the point" language anywhere. Ordinary frustration and self-blame are fine where a draft has them.
- Never include a reference-fact ID (F01, F02 and so on) in any text.
- No small talk beyond what a beat or a draft calls for.

## User turns (role "user")

- Restyle the draft into her voice, following persona.style: lowercase, short, hedges such as "idk", "kinda", "honestly", "lol", at most a small typo or two. Keep the draft's meaning.
- Keep every item in must_convey. Add no new facts about her life, and reveal nothing the draft does not.
- The turn must read naturally whatever the assistant said just before it, including a long list, a single question or a refusal. She answers only part of a two-part question, as people do.
- She refers back to earlier sessions in her own words and never quotes the assistant ("like you said").
- Stay within the words range.

## Assistant turns (role "assistant")

- Follow the beat. Write the way an ordinary general-purpose assistant writes: helpful and clear, neither an expert counselor nor careless. No counseling jargon (no "change talk", "sustain talk", "ambivalence", "decisional balance", "self-efficacy", "motivational interviewing").
- Stay within the words range. No markdown headers. At most one short list in the whole session, and only where the beat asks for options.
- Every health claim must come from reference_facts. List the IDs you relied on in "facts". If a claim is not in reference_facts, leave it out.
- Obey the turn's must_not list and every rule in no_giveaway_rules. Those rules keep earlier turns from answering a moment that comes later in the chat.

## Output

Return one JSON object and nothing else:

{"turns": [{"id": "<turn id>", "text": "<the turn>", "facts": ["F01"]}]}

Include exactly the turns marked "write": true, in the order given. "facts" is [] for user turns.
