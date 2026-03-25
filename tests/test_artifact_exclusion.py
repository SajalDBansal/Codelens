"""Excluded folders must disappear from an existing graph, including derived nodes."""

import json

import pytest
import test_cli_workflow as cli_fixtures

from graph_rag.cli import app, projects, settings
from graph_rag.models import FolderSummary
from graph_rag.pipeline import indexer
from graph_rag.storage import GraphStore

isolated = cli_fixtures.isolated
configured = cli_fixtures.configured
models = cli_fixtures.models
repo = cli_fixtures.repo


def add_artifacts(repo):
    paths = {
        "artifacts/generated.py": "def generated_artifact():\n    return 42\n",
        "artifacts/guide.md": "# Artifact export\nGenerated artifact documentation.\n",
        "apps/api/artifacts/nested/output.py": "def nested_artifact():\n    return 17\n",
    }
    for relative, text in paths.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return paths


def test_fresh_cli_graph_omits_artifacts_at_every_depth(repo, configured, models):
    paths = add_artifacts(repo)
    app.main(["embed", str(repo), "--quiet"])
    with (
        settings.engine_settings(configured),
        GraphStore(projects.load_project(repo).database) as store,
    ):
        assert set(store.get_file_hashes()) == {"checkout.py", "README.md", ".gitignore"}
        assert set(store.get_folder_hashes()) == {""}
    assert all((repo / path).exists() for path in paths)


@pytest.mark.parametrize("summaries_enabled", [True, False])
@pytest.mark.parametrize("force", [False, True])
def test_new_exclusion_purges_old_graph_content(
    repo,
    configured,
    models,
    monkeypatch,
    summaries_enabled,
    force,
):
    paths = add_artifacts(repo)

    class FakeSummarizer:
        def summarise(self, folder, entities, folder_hash):
            return FolderSummary(
                id=f"summary::{folder}",
                path=folder,
                content_hash=folder_hash,
                text="; ".join(sorted({entity.file_path for entity in entities})),
                entity_count=len(entities),
            )

    monkeypatch.setattr(indexer, "Summarizer", FakeSummarizer)
    # Simulate a graph built before artifacts was added to the user's ignore list.
    initial = configured.model_copy(deep=True)
    initial.engine.ignored_directories.remove("artifacts")
    initial.engine.generate_summaries = True
    settings.save_settings(initial)
    app.main(["embed", str(repo), "--quiet"])
    database = projects.load_project(repo).database
    with settings.engine_settings(initial), GraphStore(database) as store:
        assert set(paths).issubset(store.get_file_hashes())
        assert store.get_summary_hashes()
        # This interface is supported only by the soon-to-be-excluded files.
        store.save_interface_refs([
            (row["id"], "route", "/artifact-only")
            for row in store._nodes("CodeEntity") if row["file_path"] in paths
        ])

    app.main(["config", "update", "extra_ignored_directories", json.dumps(["artifacts"])])
    app.main(["config", "update", "generate_summaries", str(summaries_enabled).lower()])
    app.main(["embed", str(repo), "--quiet", *(["--force"] if force else [])])

    with settings.engine_settings(settings.load_settings()), GraphStore(database) as store:
        assert set(store.get_file_hashes()) == {"checkout.py", "README.md", ".gitignore"}
        assert set(store.get_folder_hashes()) == {""}
        for label in ["CodeEntity", "Doc"]:
            rows = store.query("SELECT file_path AS path FROM nodes WHERE label=:label", label=label)
            assert all("artifacts" not in row["path"].split("/") for row in rows)
        summaries = [store._object(row) for row in store._nodes("Summary")]
        assert all("artifacts" not in row.path + row.text for row in summaries)
        assert not store.query("SELECT id FROM nodes WHERE label='Interface' AND json_extract(data,'$.value')='/artifact-only'")
    # Exclusion removes indexed data, never the repository's actual files.
    assert all((repo / path).exists() for path in paths)
