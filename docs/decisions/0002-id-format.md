# ADR 0002: Default ID Format

- status: accepted_for_m1a

New records use `<record-namespace>_<lowercase UUID4>`. The namespace represents record type only. Scientific domain, question, title, ordering, and status are never encoded in an ID. Legacy IDs require a future compatibility adapter.
