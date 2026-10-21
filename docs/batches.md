# Batch organization and undo

Click **Select** above an image grid, then check the images to edit. Selections persist while loading more images or changing the sort. **Done selecting** clears them. **Select loaded images** adds the images already shown. **Select all results** uses the current server query; its membership is fixed when you request a preview. Newly indexed images never join an existing preview or retry.

**Preview changes** shows the operation, image count and sample paths. Changing the operation or selection invalidates that preview. **Confirm changes** applies it. **Batch history** includes the complete image list, results, pending work and undo conflicts. The **Unorganized** view shows images you have not marked as done.

Artist and album targets have searchable, paginated pickers. **New album** in the
album picker keeps the current image selection and selects the new album.
Only editable manual albums are offered. Batch status stays visible after
**Done selecting**. A failed status read provides **Retry**; it does not resubmit
the operation. Retrying an unchanged preview reuses its request key.
Late status responses cannot replace the result of a newer cancel, resume or
undo request. History pages include only batches whose images are still
accessible; losing access to a library also hides batches spanning that library.

## Supported changes

One batch performs one of these operations:

| Action | Parameters | Access |
| --- | --- | --- |
| `tags_add`, `tags_remove` | `names`: 1–100 names | `tag_edit` and edit access to every selected image |
| `artist_add`, `artist_remove` | `artist_id` | Same, plus access to the artist entry |
| `album_add`, `album_remove` | `album_id` of a manual album | Same, plus edit access to the album and its current members |
| `rating` | `value`: integer 0–10 or `null` to clear | `rate` and view access |
| `favorite` | `value`: boolean | `rate` and view access |
| `workflow` | `value`: `inbox` or `done` | View access |

Personal values belong to the batch creator. An administrator executing or undoing that batch acts on the original creator's values. Global tag names and artist profiles are never changed by image association batches. Missing image records may still have their metadata organized when their library is accessible.

Each preview accepts 1–10,000 explicit IDs or all matching results up to 10,000 images. An oversized or invalid query is rejected, not truncated into a different selection. Query snapshots cannot specify `limit` or `offset`. Previews expire after 24 hours if execution has not started. Reusing a request key with different inputs is an error.

## Execution and recovery

Before execution, every selected image's authority, operation parameters, target identity and recorded field state are checked. Known conflicts stop the batch before image metadata changes. A batch of at most 100 images is one SQLite transaction, including its result records; an error rolls back that entire transaction.

Larger batches commit 100 images at a time. Later changes detected between chunks are recorded as per-image conflicts. Current permissions and shared targets are rechecked between chunks. Successful earlier chunks remain committed. Cancellation and shutdown take effect at the next chunk boundary. Cancellation does not undo completed items.

The Web interface queues batches larger than 100 images through the local worker pool. CLI background requests also persist in the queue; run `picshelf worker --once` or leave the Web service running. Workers share the existing two execution slots. If a process exits, its unfinished running batch becomes interrupted when inspected. Resume explicitly; committed items are not written again. Retry uses the same fixed membership and original before-values. Conflicting items require a new preview after reviewing their current state.

## Undo

Undo restores only items that this batch changed. It requires current permissions. For each affected metadata field, both the current value and storage revision must equal the batch's recorded result. If someone changes a value and then changes it back, the revision still differs and undo reports a conflict. Those current values remain untouched.

Versions are tracked per image and user for ratings, favorites and workflow; per image for tag or artist associations; and per image/album for album membership. Thus a later favorite does not prevent undoing a rating, while any later tag change on that image prevents restoring an older tag set. This conservative rule also applies to changes made through older API and CLI entry points, and to another undo operation.

Original association authors, timestamps and tag evidence are restored when possible. Evidence created by an undone tag addition is retained as retired history. Newly created global tags remain available as unlinked entries. Undoing album membership does not replace a later cover choice. A removed cover is hidden until it is again a member.

Large undos also commit in chunks and can be cancelled and resumed. Once undo starts, pending application work is cancelled; the original batch cannot be applied again. Create a new preview for further edits. Undo covers metadata only, never source files, imports, moves, deletions or database restores.

## API and CLI

API endpoints under `/api/v1/batches` are `POST /preview`, `GET /`, `GET /{id}`, and `POST /{id}/execute`, `/cancel`, `/undo`. Execute and undo require `confirm: true`; execute optionally accepts `background` and `retry`. Detail results support `offset` and `limit` (up to 1,000). `GET /{id}?include_items=false` returns status and counts without journal rows. Requests obey the [authentication and source rules](security.md).

```sh
# Fixed selection; the response contains the batch ID and reviewed count.
picshelf batch preview 12 27 --action '{"kind":"tags_add","names":["color:blue"]}'

# Fixed query result, evaluated with this user's current grants.
picshelf batch preview --user reader --action '{"kind":"workflow","value":"done"}' \
  --query-json '{"query":"tag:reviewed","workflow":"inbox"}'

picshelf batch show <batch-id>
picshelf batch execute <batch-id> --yes
picshelf batch execute <batch-id> --yes --background
picshelf worker --once
picshelf batch cancel <batch-id>
picshelf batch execute <batch-id> --yes --retry
picshelf batch undo <batch-id> --yes
picshelf batch list
```

These commands emit JSON. Missing `--yes` returns a nonzero error without waiting for interactive input. `--user` selects the named account and applies its current permissions; without it the local data directory owner uses the default administrator. Access to the CLI and data directory remains a local administrator boundary.
