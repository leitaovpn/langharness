# Continuing a goal

A turn ending does not mean the goal is finished. When you are handed another
round, the goal is still open and the host expects you to continue working on it.

- Keep going on the objective. Do not restate what you already did.
- If you believe the work is done, call `verify` first. It runs the project's
  gates and tells you exactly what completion is still missing—fixing that in
  this round is cheaper than submitting and reading a rejection.
- Only `finish_goal(status="complete", evidence=[...])` closes the goal. Attach
  real evidence to every acceptance criterion: a tool call ID, a file hash, or
  a test case name. `manual/user_review` is for things a human must judge.
- Running any command or editing any file makes an earlier verification stale.
  Verify last.
- If you are stuck on a decision only the user can make, call `ask_user`. Do not
  guess a choice that changes the requirements.
