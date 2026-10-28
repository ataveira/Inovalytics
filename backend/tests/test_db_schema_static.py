"""F1-04a: análise estática (regex) do esquema SQL; sem Docker e sem Postgres."""

import re
from importlib import resources
from pathlib import Path

import pytest

NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.(up|down)\.sql$")
TABLES = ["project", "dataset", "label_class", "image", "annotation"]


def _files() -> dict[str, str]:
    root = resources.files("inovalytics.db") / "migrations"
    return {
        p.name: p.read_text(encoding="utf-8") for p in root.iterdir() if p.name.endswith(".sql")
    }


def _strip(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


@pytest.fixture(scope="module")
def up() -> str:
    return _strip(_files()["0001_initial.up.sql"])


@pytest.fixture(scope="module")
def down() -> str:
    return _strip(_files()["0001_initial.down.sql"])


def _table_bodies(up: str) -> dict[str, str]:
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", up, re.DOTALL)
    }


def test_1_files_names_and_contiguity() -> None:
    files = _files()
    assert "0001_initial.up.sql" in files and "0001_initial.down.sql" in files
    versions: set[int] = set()
    for name, text in files.items():
        m = NAME_RE.match(name)
        assert m, name
        assert text.strip(), name
        versions.add(int(m.group(1)))
    assert versions == set(range(1, max(versions) + 1))
    for name in files:
        if name.endswith(".up.sql"):
            assert name.replace(".up.sql", ".down.sql") in files


def test_1_package_data_declared(repo_root: Path) -> None:
    assert "migrations/*.sql" in (repo_root / "pyproject.toml").read_text(encoding="utf-8")


def test_2_exact_tables(up: str) -> None:
    assert re.findall(r"CREATE TABLE (\w+)", up) == TABLES
    assert "schema_migrations" not in up


def test_3_pk_and_created_at(up: str) -> None:
    bodies = _table_bodies(up)
    assert list(bodies) == TABLES
    for name, body in bodies.items():
        assert "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY" in body, name
        assert "created_at timestamptz NOT NULL DEFAULT now()" in body, name


def _refs(up: str) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for table, body in _table_bodies(up).items():
        # Normaliza quebras de linha para cobrir REFERENCES na linha seguinte.
        flat = re.sub(r"\s+", " ", body)
        for m in re.finditer(
            r"REFERENCES (\w+) \(([^)]*)\)( ON DELETE (CASCADE|NO ACTION|\w+( \w+)?))?", flat
        ):
            assert m.group(3), f"{table} -> {m.group(1)} sem ON DELETE explícito"
            out[(table, m.group(1))] = m.group(4)
    return out


def test_4_on_delete_map(up: str) -> None:
    assert _refs(up) == {
        ("dataset", "project"): "CASCADE",
        ("label_class", "project"): "CASCADE",
        ("image", "dataset"): "CASCADE",
        ("annotation", "image"): "CASCADE",
        ("annotation", "label_class"): "NO ACTION",
    }
    # NO ACTION adiado: o imediato quebra a cascata de exclusão de projeto (visto em Postgres real).
    flat = re.sub(r"\s+", " ", up)
    assert "ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED" in flat
    assert flat.count("DEFERRABLE") == 1
    for bad in ("RESTRICT", "SET NULL", "SET DEFAULT"):
        assert bad not in up


def test_4_composite_fks(up: str) -> None:
    flat = re.sub(r"\s+", " ", up)
    assert "FOREIGN KEY (dataset_id, project_id) REFERENCES dataset (id, project_id)" in flat
    assert "FOREIGN KEY (image_id, project_id) REFERENCES image (id, project_id)" in flat
    assert "FOREIGN KEY (class_id, project_id) REFERENCES label_class (id, project_id)" in flat


def _uniques(up: str) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    for table, body in _table_bodies(up).items():
        out[table] = [
            [c.strip() for c in m.group(1).split(",")]
            for m in re.finditer(r"UNIQUE \(([^)]*)\)", body)
        ]
    return out


def test_5_uniques(up: str) -> None:
    u = _uniques(up)
    assert ["name"] in u["project"]
    assert ["project_id", "name"] in u["dataset"]
    assert ["id", "project_id"] in u["dataset"]
    assert ["project_id", "name"] in u["label_class"]
    assert ["project_id", "position"] in u["label_class"]
    assert ["id", "project_id"] in u["label_class"]
    assert ["dataset_id", "sha256"] in u["image"]
    assert ["object_key"] in u["image"]
    assert ["id", "project_id"] in u["image"]


def test_5_fk_columns_lead_an_index(up: str) -> None:
    u = _uniques(up)
    leaders: dict[str, set[str]] = {t: {cols[0] for cols in lst} for t, lst in u.items()}
    for m in re.finditer(r"CREATE INDEX \w+ ON (\w+) \((\w+)", up):
        leaders[m.group(1)].add(m.group(2))
    flat = re.sub(r"\s+", " ", up)
    fk_first: list[tuple[str, str]] = []
    for table, body in _table_bodies(up).items():
        b = re.sub(r"\s+", " ", body)
        for m in re.finditer(r"FOREIGN KEY \((\w+)", b):
            fk_first.append((table, m.group(1)))
        for m in re.finditer(r"(\w+) bigint NOT NULL REFERENCES", b):
            fk_first.append((table, m.group(1)))
    assert len(fk_first) == 5, flat
    for table, col in fk_first:
        assert col in leaders[table], (table, col)


def test_6_checks(up: str) -> None:
    flat = re.sub(r"\s+", " ", up)
    for expected in (
        "CHECK (x_center >= 0 AND x_center <= 1)",
        "CHECK (y_center >= 0 AND y_center <= 1)",
        "CHECK (width > 0 AND width <= 1)",
        "CHECK (height > 0 AND height <= 1)",
        "CHECK (position >= 0)",
        "CHECK (size_bytes > 0)",
        "CHECK (width_px > 0)",
        "CHECK (height_px > 0)",
        "CHECK (sha256 ~ '^[0-9a-f]{64}$')",
        "CHECK (split IN ('train', 'val', 'test'))",
    ):
        assert expected in flat, expected
    for table in ("project", "dataset", "label_class"):
        assert "CHECK (length(btrim(name)) > 0)" in _table_bodies(up)[table]
    ann = _table_bodies(up)["annotation"]
    for col in ("x_center", "y_center", "width", "height"):
        assert f"{col} double precision NOT NULL" in ann


def test_7_down_reverse_order(down: str) -> None:
    drops = re.findall(r"DROP TABLE (\w+)\s*;", down)
    assert drops == list(reversed(TABLES))
    assert not re.search(r"CASCADE|schema_migrations", down, re.IGNORECASE)


def test_8_forbidden(up: str, down: str) -> None:
    for sql in (up, down):
        assert not re.search(r"CREATE\s+EXTENSION", sql, re.IGNORECASE)
        assert not re.search(r"DROP\s+DATABASE", sql, re.IGNORECASE)
        assert "ultralytics" not in sql.lower()
    assert not re.search(r"TRUNCATE", up, re.IGNORECASE)
    assert "ultralytics" not in "".join(_files().values()).lower()
