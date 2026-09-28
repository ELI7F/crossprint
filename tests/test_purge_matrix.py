"""Purge volumes across a hotend-class change.

Reported by converting a 4-colour Snapmaker U1 project to an H2C: Bambu
Studio opened it and said "Purge volumes matrix do not match to the correct
size!", then "Partial purging volume set to 0. Multi-color printing may cause
color mixing in models."

The matrix had been dropped with the rest of the machine layer, on the
reasoning that the slicer would supply its own. It does not, and a purge
volume of zero means colour bleeding into the model on every tool change.

Shapes and the +15 offset below are read from real project files, not chosen
-- see convert/purge_matrix.py.
"""
from __future__ import annotations

from convert.purge_matrix import MAX_FLUSH, SECOND_EXTRUDER_EXTRA, reshape_purge_matrix


def _square(n, fill=100):
    """An n*n matrix with a zero diagonal, as every real one has."""
    return [str(0 if r == c else fill + r * 10 + c) for r in range(n) for c in range(n)]


def test_single_hotend_source_to_dual_hotend_target_gains_a_second_block():
    matrix = _square(3)

    result = reshape_purge_matrix(matrix, None, 3, target_is_dual_hotend=True)

    assert len(result.matrix) == 2 * 9
    assert result.matrix[:9] == matrix, "the first block is the user's own volumes, untouched"
    for i, (base, second) in enumerate(zip(result.matrix[:9], result.matrix[9:])):
        if i // 3 == i % 3:
            assert second == "0", "a filament change to itself purges nothing"
        else:
            assert int(second) == int(base) + SECOND_EXTRUDER_EXTRA


def test_dual_hotend_source_to_single_hotend_target_keeps_the_first_block():
    single = _square(4)
    dual = single + [str(int(v) + SECOND_EXTRUDER_EXTRA if v != "0" else 0) for v in single]

    result = reshape_purge_matrix(dual, None, 4, target_is_dual_hotend=False)

    assert result.matrix == single


def test_same_class_passes_through_unchanged():
    matrix = _square(5)
    assert reshape_purge_matrix(matrix, None, 5, target_is_dual_hotend=False).matrix == matrix


def test_the_second_block_is_capped():
    """Entries already at the ceiling stay there in both blocks."""
    matrix = ["0", str(MAX_FLUSH), str(MAX_FLUSH), "0"]

    result = reshape_purge_matrix(matrix, None, 2, target_is_dual_hotend=True)

    assert result.matrix[4:] == ["0", str(MAX_FLUSH), str(MAX_FLUSH), "0"]


def test_a_matrix_of_neither_shape_is_left_out_with_a_warning():
    """Guessing which entries belong to which filament pair would put
    made-up purge volumes in front of someone who would assume they came
    from their own colours."""
    result = reshape_purge_matrix(["1"] * 7, None, 3, target_is_dual_hotend=True)

    assert result.matrix is None
    assert result.warnings and "7 entries for 3 filament" in result.warnings[0]


def test_no_source_matrix_produces_no_matrix_and_no_warning():
    assert reshape_purge_matrix(None, None, 4, target_is_dual_hotend=True).matrix is None
    assert reshape_purge_matrix([], None, 4, target_is_dual_hotend=True).warnings == []


def test_the_vector_is_carried_only_when_it_is_the_expected_length():
    matrix = _square(3)

    good = reshape_purge_matrix(matrix, ["140"] * 6, 3, target_is_dual_hotend=True)
    assert good.vector == ["140"] * 6  # 2n in every real file, single and dual alike

    bad = reshape_purge_matrix(matrix, ["140"] * 5, 3, target_is_dual_hotend=True)
    assert bad.vector is None


def test_values_keep_their_integer_spelling():
    """Real files store "346", not "346.0" -- a differently spelled number
    reads to the slicer as a user override."""
    result = reshape_purge_matrix(["0", "346.0", "208", "0"], None, 2, target_is_dual_hotend=False)
    assert result.matrix == ["0", "346", "208", "0"]
