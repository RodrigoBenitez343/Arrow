"""The desktop substrate: window pick, option diff, toggle answers."""

from .desktop import (
    _ff_new_options,
    _ff_pick_fields,
    _ff_placeholder_like,
    _ff_truthy,
)


def _fields(n):
    return [{"id": "f%d" % i, "label": "Field %d" % i} for i in range(n)]


# --------------------------------------------------------------- window pick

def test_pick_prefers_the_document_window():
    """A browser page beats an app shell that merely has more Edit controls.

    Live shape: 10 page fields in the browser window vs 21 read-only grid
    "Edit" cells in an Explorer details view - the grid is not a form.
    """
    candidates = [
        (False, _fields(21), object(), "resumes"),
        (True, _fields(10), object(), "HiringRoom.com - Google Chrome"),
    ]
    has_doc, fields, _window, title = _ff_pick_fields(candidates)
    assert has_doc and len(fields) == 10
    assert title.startswith("HiringRoom")


def test_pick_takes_the_most_fields_of_two_documents():
    candidates = [(True, _fields(2), object(), "editor"),
                  (True, _fields(10), object(), "form")]
    _has_doc, fields, _window, title = _ff_pick_fields(candidates)
    assert (len(fields), title) == (10, "form")


def test_pick_falls_back_to_windows_without_a_document():
    candidates = [(False, _fields(3), object(), "native dialog"),
                  (False, [], object(), "shell")]
    has_doc, fields, _window, _title = _ff_pick_fields(candidates)
    assert len(fields) == 3 and not has_doc


def test_pick_none_when_no_window_has_a_field():
    assert _ff_pick_fields([(True, [], object(), "a")]) is None
    assert _ff_pick_fields([]) is None


# -------------------------------------------------------------- option diff

def test_new_options_are_the_rows_below_the_control():
    """The list appears UNDER the control; the announcement must not join it.

    Probed live: opening 'Tipo de documento' added four option rows (y>=290)
    plus a screen-reader line 'option Documento de identidad focused, 1 of 4.
    4 results available.' at y=233, ABOVE the control (y=243).
    """
    before = [("Tipo de documento", (348, 207, 483, 226))]
    control = (348, 243, 750, 263)
    after = before + [
        ("option Documento de identidad focused, 1 of 4. 4 results available.",
         (347, 233, 1932, 252)),
        ("Documento de identidad", (360, 290, 528, 309)),
        ("Pasaporte", (360, 325, 428, 344)),
        ("Seleccione una opción", (364, 243, 435, 263)),
    ]
    assert [name for name, _rect in _ff_new_options(before, after, control)] == [
        "Documento de identidad", "Pasaporte"]


def test_new_options_ignores_rows_that_were_already_there():
    """A control that displays its own value must not offer it as a new option."""
    before = [("Pasaporte", (364, 243, 432, 263))]
    after = before + [("Pasaporte", (364, 243, 432, 263))]
    assert _ff_new_options(before, after, (348, 243, 750, 263)) == []


# -------------------------------------------------------------------- answers

def test_placeholder_is_not_an_answer():
    assert _ff_placeholder_like("Seleccione una opción")
    assert _ff_placeholder_like("Select...")
    assert _ff_placeholder_like("")
    assert not _ff_placeholder_like("Pasaporte")


def test_toggle_answer_reads_a_yes_no():
    for yes in ("Yes", "true", "SI", "sí", "acepto los términos", "1"):
        assert _ff_truthy(yes), yes
    for no in ("No", "false", "N/A", "never", ""):
        assert not _ff_truthy(no), no
