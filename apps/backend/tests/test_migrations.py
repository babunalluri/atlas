from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script_directory() -> ScriptDirectory:
    backend_root = Path(__file__).resolve().parents[1]
    return ScriptDirectory.from_config(Config(str(backend_root / "alembic.ini")))


def test_alembic_revision_chain_is_linear_with_single_head() -> None:
    """Guard deploy boot: docker-compose runs `alembic upgrade head` before uvicorn."""
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, f"expected one migration head, found {heads}"

    revisions = list(script.walk_revisions())
    revision_ids = {revision.revision for revision in revisions}

    head = script.get_revision(heads[0])
    assert head is not None

    visited: set[str] = set()
    current = head
    while current is not None:
        assert current.revision not in visited
        visited.add(current.revision)

        down = current.down_revision
        if down is None:
            break
        assert isinstance(down, str), f"unexpected merge point at {current.revision}"
        current = script.get_revision(down)

    assert visited == revision_ids, (
        f"orphan revisions: {sorted(revision_ids - visited)}; "
        f"unreachable from head: {sorted(visited - revision_ids)}"
    )
