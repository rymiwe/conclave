Synthetic ballots for scripts/test-post-review.py. No real repository content.

demo-1 and demo-2 are per-PR ballots for example/widgets#7. demo-stack holds
the chair transcripts, as a stack run's logs/ directory does. files-7.json is
the pulls/7/files response the gh stub returns.

f2 cites app/label.rb, a file that is not in PR 7's diff, so it still moves
into the review body. A line outside the diff in a file that is in the diff
now posts as a file-level comment instead, so f2 used to sit in
app/widget.rb and moved out of it when that rule landed. The payload has no
"event": f1 is Requested, so the event is REQUEST_CHANGES.
