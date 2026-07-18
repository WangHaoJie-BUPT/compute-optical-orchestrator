"""Validation rules for externally supplied resource identifiers."""

from __future__ import annotations

import re


TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_TASK_ID_REGULAR_EXPRESSION = re.compile(TASK_ID_PATTERN)


def validate_task_id(task_id: str) -> str:
    """Validate a task ID before it reaches file or Kubernetes adapters."""

    if _TASK_ID_REGULAR_EXPRESSION.fullmatch(task_id) is None:
        raise ValueError(
            "task_id must start with an alphanumeric character and "
            "contain only letters, numbers, '.', '_' or '-'."
        )

    return task_id
