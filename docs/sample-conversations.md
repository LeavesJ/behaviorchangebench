# Sample conversations: how to collect them

Peter asked us (Oct 16) to "talk with friends to get some sample conversations to start testing
with". They're test input for the pipeline. They are not the benchmark itself, which the experts
are writing.

## What we need

Two or three conversations per domain, from a few different people:

1. **Tobacco or nicotine:** vaping, cigarettes, pouches, cutting down or quitting.
2. **Physical activity:** starting to exercise, getting back to it, sticking with it.
3. **A third domain:** to be decided. Hold off on it for now.

Each one is a person asking an AI assistant (ChatGPT, Claude, Gemini, any of them) for help
changing that behavior. The most useful ones run over **two or three sessions, a few days
apart**, the way people actually come back to a chat.

## Role-play for now, please

Ask your friend to play a character: a made-up first name, an age, and a situation. They can
draw on what they know about how people talk about this, but nothing that points back to
them: no real names, schools, workplaces or places.

Please hold off on conversations about a friend's *own* nicotine use or health. We've asked
Peter and Nhung whether that needs IRB review, and we'll update this page when we hear back.

## Before you ask anyone

- Tell them what it's for: test data for a research benchmark that may be published.
- Make sure they're 18 or older.
- Get a clear yes in writing. A text is fine.
- Tell them they can stop at any time and take the conversation back later.
- If a chat turns to self-harm or a crisis, stop collecting, check in with your friend as a
  friend, and share 988 (the Suicide and Crisis Lifeline, call or text).

## What makes a conversation useful

Real conversations are messy, and the messy parts are what we're testing. Things worth letting
happen:

- They aren't sure they want to change, or they change their mind partway through.
- They slip ("ok i vaped at the party") and come back.
- They ask the assistant to just decide for them, or to make the whole plan.
- They push back on advice, or bring a claim they read online.
- They're short, casual and tired, not polite and complete.

Nobody needs to be a good actor. Typing the way they'd normally text is the point.

## How to send it in

Copy the chat into a plain text file shaped like this, one file per conversation, and put it
in the shared Drive folder ([TK: folder]):

```
domain: nicotine            # nicotine | physical-activity
collected_by: <your first name>
date_collected: 2026-10-20
assistant: <app and model, e.g. ChatGPT free, Oct 2026>
role_play: yes
consent: yes, by text on 2026-10-19
character: Maya, 20, vapes since high school, wants to cut down before finals

=== session 1 (day 0) ===
USER: ...
ASSISTANT: ...
USER: ...

=== session 2 (day 6) ===
USER: ...
ASSISTANT: ...
```

Before you upload it, take out anything that names a real person, place or account, including
whatever the app put in the transcript.

Questions: ask in the group chat.
