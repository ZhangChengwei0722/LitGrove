from __future__ import annotations


def parser_profile_descriptor(adapter_id: str, version: str) -> dict[str, str]:
    profiles = {
        "synthetic-text": {
            "reading_order": "reliable",
            "complete_reading": "supported",
            "figure_table_context": "unsupported",
            "formula_layout_context": "unsupported",
            "supplement_parse": "unsupported",
            "settings": "form-feed-pages-v1",
        },
        "pdfplumber": {
            "reading_order": "uncertain",
            "complete_reading": "uncertain",
            "figure_table_context": "unsupported",
            "formula_layout_context": "unsupported",
            "supplement_parse": "unsupported",
            "settings": "x3-y3-layout-false",
        },
        "pdfplumber-text-flow": {
            "reading_order": "uncertain",
            "complete_reading": "uncertain",
            "figure_table_context": "unsupported",
            "formula_layout_context": "unsupported",
            "supplement_parse": "unsupported",
            "settings": "x1-y3-layout-false-text-flow",
        },
    }
    selected = profiles.get(
        adapter_id,
        {
            "reading_order": "uncertain",
            "complete_reading": "uncertain",
            "figure_table_context": "uncertain",
            "formula_layout_context": "uncertain",
            "supplement_parse": "uncertain",
            "settings": "unregistered-profile",
        },
    )
    return {"adapter_id": adapter_id, "version": version, **selected}


__all__ = ["parser_profile_descriptor"]
