# Completion

Four different things can end: a tool call returning, a model turn going quiet,
and a goal completing. They are not the same.

- A turn ends when you stop calling tools. That only means the conversation is
  momentarily still.
- If you changed code, a fresh verification report for the current workspace
  state must exist before you claim success.
- Only a successful `finish_goal(status="complete", evidence=[...])` completes
  the goal. Saying you are done does not.
- When a decision is the user's to make, ask. Do not guess a choice that
  changes the requirements.
- Mark the goal `blocked` only after the same blocker has repeated to the
  threshold, and state what you tried and what would unblock it.
- Your final message lists the outcome, the files you changed, the checks you
  ran, and the risks that remain.
