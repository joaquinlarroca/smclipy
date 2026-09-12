from smclipy.storage import append_unique_lines, read_lines, write_lines


def test_read_lines_creates_missing_file(tmp_path):
    path = tmp_path / "data.txt"
    assert read_lines(path) == []
    assert path.exists()


def test_read_lines_returns_stripped_lines(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("A\n  B  \nC\n", encoding="utf-8")
    assert read_lines(path) == ["A", "B", "C"]


def test_append_unique_lines_appends_missing(tmp_path):
    path = tmp_path / "data.txt"
    append_unique_lines(path, ["A", "B"])
    append_unique_lines(path, ["B", "C"])
    assert read_lines(path) == ["A", "B", "C"]


def test_append_unique_lines_dedupes_within_batch(tmp_path):
    path = tmp_path / "data.txt"
    append_unique_lines(path, ["A", "A", "B", "B"])
    assert read_lines(path) == ["A", "B"]


def test_append_unique_lines_ignores_empty(tmp_path):
    path = tmp_path / "data.txt"
    append_unique_lines(path, ["", "   "])
    assert read_lines(path) == []


def test_write_lines_overwrites(tmp_path):
    path = tmp_path / "data.txt"
    write_lines(path, ["A", "B"])
    write_lines(path, ["C"])
    assert read_lines(path) == ["C"]


def test_write_lines_strips_and_skips_empty(tmp_path):
    path = tmp_path / "data.txt"
    write_lines(path, [" A ", "  ", "B"])
    assert read_lines(path) == ["A", "B"]


def test_write_lines_creates_missing_dirs(tmp_path):
    path = tmp_path / "nested" / "data.txt"
    write_lines(path, ["A"])
    assert read_lines(path) == ["A"]


def test_write_lines_leaves_no_tmp_file_behind(tmp_path):
    path = tmp_path / "data.txt"
    write_lines(path, ["A", "B"])
    assert read_lines(path) == ["A", "B"]
    assert list(tmp_path.glob("*")) == [path]
