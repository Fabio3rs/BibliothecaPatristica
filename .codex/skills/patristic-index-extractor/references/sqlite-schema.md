# SQLite Schema

Database: `data/patristic_indices.db`

## Ownership boundary

This schema documents the driver's persistence target. The extraction agent may read it for
compatibility checks but must never initialize, import into, replace, rebuild, or otherwise modify
the primary database. The driver imports a payload only after all driver-side validations pass.

## Tables

### `volumes`

- `volume_id` TEXT PRIMARY KEY
- `collection` TEXT NOT NULL
- `source_root` TEXT NOT NULL
- `volume_label` TEXT
- `notes` TEXT
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

### `works`

- `work_key` TEXT PRIMARY KEY
- `volume_id` TEXT NOT NULL REFERENCES `volumes(volume_id)` ON DELETE CASCADE
- `work_order` INTEGER
- `author_raw` TEXT
- `title_raw` TEXT NOT NULL
- `title_norm` TEXT
- `start_page` INTEGER
- `end_page` INTEGER
- `start_file` TEXT
- `end_file` TEXT
- `source_section_key` TEXT
- `confidence` REAL
- `raw_json` TEXT NOT NULL

### `index_sections`

- `section_key` TEXT PRIMARY KEY
- `volume_id` TEXT NOT NULL REFERENCES `volumes(volume_id)` ON DELETE CASCADE
- `work_key` TEXT REFERENCES `works(work_key)` ON DELETE SET NULL
- `scope_kind` TEXT NOT NULL
- `index_kind` TEXT NOT NULL
- `heading_raw` TEXT NOT NULL
- `heading_norm` TEXT
- `page_start` INTEGER
- `page_end` INTEGER
- `file_start` TEXT
- `file_end` TEXT
- `confidence` REAL
- `raw_json` TEXT NOT NULL

### `index_entries`

- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `section_key` TEXT NOT NULL REFERENCES `index_sections(section_key)` ON DELETE CASCADE
- `entry_order` INTEGER NOT NULL
- `entry_raw` TEXT NOT NULL
- `target_raw` TEXT
- `target_file` TEXT
- `page_ref_raw` TEXT
- `page_ref_int` INTEGER
- `page_ref_col` TEXT
- `note_raw` TEXT
- `normalized_target` TEXT
- `confidence` REAL
- `raw_json` TEXT NOT NULL

### `runs`

- `run_id` INTEGER PRIMARY KEY AUTOINCREMENT
- `volume_id` TEXT NOT NULL
- `status` TEXT NOT NULL
- `started_at` TEXT NOT NULL
- `finished_at` TEXT
- `notes` TEXT
- `raw_json` TEXT

## Replace policy

When an operator-authorized driver import replaces a volume, the driver uses `--replace` to remove
the prior rows for that `volume_id` before inserting the validated payload.

## JSON import contract

```json
{
  "volume": {
    "volume_id": "PG001",
    "collection": "PG",
    "source_root": "teste/PG001/text",
    "volume_label": "PG001",
    "notes": "optional"
  },
  "works": [
    {
      "work_key": "PG001:work:001",
      "work_order": 1,
      "author_raw": "...",
      "title_raw": "...",
      "start_page": 100,
      "end_page": 199,
      "confidence": 0.91,
      "raw_json": {}
    }
  ],
  "sections": [
    {
      "section_key": "PG001:volume_front:ELENCHUS:001",
      "work_key": null,
      "scope_kind": "volume_front",
      "index_kind": "ELENCHUS",
      "heading_raw": "ELENCHUS ...",
      "page_start": 1,
      "page_end": 12,
      "confidence": 0.93,
      "entries": [
        {
          "entry_order": 1,
          "entry_raw": "...",
          "page_ref_raw": "199",
          "page_ref_int": 199,
          "confidence": 0.88
        }
      ],
      "raw_json": {}
    }
  ],
  "notes": []
}
```

## Stable key rules

- Use a stable `work_key` and `section_key` on reruns.
- If the source does not provide one, the import script can derive a fallback key.
- Keep the original raw JSON for later review.
