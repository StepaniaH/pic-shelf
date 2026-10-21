# Works and file versions

A work groups files such as an original, crop and compressed copy. Each file
keeps its own ratings, tags, notes and album memberships. Add relationships
manually and choose a preferred file for viewing.

```sh
picshelf works create 'A work' \
  --members '[{"image_id":1,"role":"original"},{"image_id":2,"role":"resize"}]' \
  --preferred 1
picshelf works show WORK_ID
picshelf works link WORK_ID --members '[{"image_id":3,"role":"crop"}]'
picshelf works prefer WORK_ID 2
picshelf works unlink WORK_ID 3 --yes
```

Roles are `original`, `compressed`, `crop`, `resize`, `revision` and `other`.
One image can belong to one work; unlink it before assigning a different work.
Linking an existing member updates only its relationship role. Unlinking the
preferred image clears that preference. Correcting a relationship leaves original
bytes and each image's existing organization untouched. An empty work remains
available to its creator or an administrator for relinking.

Reads include only currently visible members. An inaccessible preferred file
becomes `null`; `view_image_id` supplies the preferred visible file or an available
visible member. No hidden-member count, name or path is returned. Creating or
editing associations requires `tag_edit` and edit access to every current and new
member; a local relationship editor cannot change another library's group.

The viewer's **Work files** section opens each available version and marks the
preferred one. CLI commands return JSON, and
`works show --user <name>` reads through that user's view permissions.

HTTP uses `POST /api/v1/works`, `GET /api/v1/works/{id}`,
`GET /api/v1/works/by-image/{image_id}`, `POST /api/v1/works/{id}/members`,
`DELETE /api/v1/works/{id}/members/{image_id}` and
`PUT /api/v1/works/{id}/preferred`. Image details also include `work` or `null`.

## Portable relationships

Metadata format 2 includes `works` and `work_images`. Format 1 still imports with
no work records. A scoped export includes work membership for selected images and
stable references to other members visible to its owner. A target without those
other images retains pending relationships, including a pending preference.
Re-export preserves them. Once a later explicit import restores the same stable
identity, it resolves the pending association subject to current edit permissions.
Matching never uses content hash alone. A conflicting existing work/role or a
later preference is preserved and reported instead of overwritten.

Editors can inspect unresolved references with `works pending WORK_ID` and correct
an unwanted one with `works drop-pending WORK_ID IMAGE_UID --yes`. These commands require
edit access to all current members and change only the pending relationship.
HTTP provides `GET /works/{id}/pending` and
`DELETE /works/{id}/pending/{image_uid}` under `/api/v1`.
DELETE requests require `{"confirm":true}` after inspecting the relationship.

Metadata import still requires valid originals for its selected images. Pending
relationships represent referenced versions that have not been imported or are
not accessible to the current user.
