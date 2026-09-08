"""Safe writable Agent actions backed only by approved local data stores."""

import re
from pathlib import Path

try:
    from .memory_manager import MemoryManager
    from .todo_store import TodoStore
except ImportError:
    from memory_manager import MemoryManager
    from todo_store import TodoStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_NOTES_DIR = PROJECT_DATA_DIR / "notes"
_INVALID_FILENAME_PATTERN = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


def create_note(title, content, filename=None):
    """Create a new Markdown file below the approved notes directory only."""
    normalized_title = _required_text(title, "Note title", 200)
    normalized_content = _required_text(content, "Note content", 10000)
    data_root = PROJECT_DATA_DIR.resolve()
    notes_root = DEFAULT_NOTES_DIR.resolve()
    _require_within(notes_root, data_root)
    notes_root.mkdir(parents=True, exist_ok=True)

    if filename is None:
        base_name = _note_filename(normalized_title)
        target = _available_note_path(notes_root, base_name)
    else:
        base_name = _validate_note_filename(filename)
        target = (notes_root / base_name).resolve(strict=False)
        _require_within(target, notes_root)
        if target.exists():
            raise FileExistsError(f"Note already exists: {base_name}")

    markdown = f"# {normalized_title}\n\n{normalized_content}\n"
    with target.open("x", encoding="utf-8", newline="\n") as note_file:
        note_file.write(markdown)
    return {
        "title": normalized_title,
        "filename": target.name,
        "path": target.relative_to(PROJECT_ROOT).as_posix(),
    }


def update_memory_action(
    content,
    memory_type="personal",
    importance=3,
    memory_id=None,
):
    """Add a Memory record or update an explicitly identified existing record."""
    manager = MemoryManager()
    if memory_id is None:
        memory = manager.add_memory(
            content,
            memory_type=memory_type,
            importance=importance,
            source="agent:user_confirmed",
        )
        return {"action": "added", "memory": memory}

    memory = manager.update_memory(
        memory_id,
        content=content,
        memory_type=memory_type,
        importance=importance,
        source="agent:user_confirmed",
    )
    if memory is None:
        raise ValueError(f"Memory '{memory_id}' was not found.")
    return {"action": "updated", "memory": memory}


def create_todo_action(title, description=""):
    """Create a pending todo in the approved local todo database."""
    return TodoStore().create_todo(title, description)


def list_todos_action(status="pending", limit=50):
    """Read current todos without changing their state."""
    return {"todos": TodoStore().list_todos(status=status, limit=limit)}


def complete_todo_action(todo_id):
    """Complete one existing todo without supporting deletion."""
    todo = TodoStore().complete_todo(todo_id)
    if todo is None:
        raise ValueError(f"Todo '{todo_id}' was not found.")
    return todo


def _required_text(value, field, maximum):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field} must contain at most {maximum} characters.")
    return normalized


def _validate_note_filename(filename):
    normalized = _required_text(filename, "Note filename", 120)
    if (
        Path(normalized).name != normalized
        or _INVALID_FILENAME_PATTERN.search(normalized)
        or normalized.endswith((" ", "."))
    ):
        raise ValueError("Note filename must be a plain local filename.")

    path = Path(normalized)
    if path.suffix and path.suffix.casefold() != ".md":
        raise ValueError("Note filename must use the .md extension.")
    if not path.suffix:
        normalized = f"{normalized}.md"
        path = Path(normalized)
    if path.stem.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("Note filename is reserved by the operating system.")
    return normalized


def _note_filename(title):
    stem = re.sub(r"[^\w\- ]+", "", title, flags=re.UNICODE).strip()
    stem = re.sub(r"[\s_]+", "-", stem).strip("-. ")
    if not stem or stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = "note"
    return f"{stem[:80]}.md"


def _available_note_path(notes_root, filename):
    path = (notes_root / filename).resolve(strict=False)
    _require_within(path, notes_root)
    if not path.exists():
        return path

    stem = Path(filename).stem
    for suffix in range(2, 1000):
        candidate = (notes_root / f"{stem}-{suffix}.md").resolve(strict=False)
        _require_within(candidate, notes_root)
        if not candidate.exists():
            return candidate
    raise FileExistsError("Could not allocate a unique note filename.")


def _require_within(path, allowed_root):
    if path != allowed_root and allowed_root not in path.parents:
        raise ValueError("Note path must stay inside the approved data directory.")
