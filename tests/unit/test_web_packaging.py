from __future__ import annotations

from importlib.resources import files


def test_resume_import_templates_are_package_resources() -> None:
    templates = files("radar_vagas.web").joinpath("templates")
    expected = (
        "resume_import_upload.html",
        "resume_import_list.html",
        "resume_import_review.html",
        "base.html",
        "components/icons.html",
        "components/navigation.html",
        "components/ui.html",
    )

    for name in expected:
        assert templates.joinpath(name).is_file()


def test_web_static_assets_are_package_resources() -> None:
    static = files("radar_vagas.web").joinpath("static")
    expected = (
        "app.css",
        "app.js",
        "css/tokens.css",
        "css/reset.css",
        "css/layout.css",
        "css/components.css",
        "css/forms.css",
        "css/pages.css",
        "css/resume-import.css",
        "css/responsive.css",
        "icons/radar-mark.svg",
    )

    for name in expected:
        assert static.joinpath(name).is_file()


def test_templates_do_not_use_unsafe_safe_filter_or_external_assets() -> None:
    templates = files("radar_vagas.web").joinpath("templates")
    static = files("radar_vagas.web").joinpath("static")
    for path in _html_files(templates):
        text = path.read_text(encoding="utf-8")
        assert "|safe" not in text
        assert "https://" not in text
        assert "http://" not in text
        assert "cdn." not in text.lower()
    for path in _asset_files(static):
        text = path.read_text(encoding="utf-8")
        if not path.name.endswith(".svg"):
            assert "https://" not in text
            assert "http://" not in text
        assert "cdn." not in text.lower()


def _html_files(root) -> list:  # type: ignore[no-untyped-def]
    return [path for path in root.iterdir() if path.name.endswith(".html")] + [
        child
        for directory in root.iterdir()
        if directory.is_dir()
        for child in directory.iterdir()
        if child.name.endswith(".html")
    ]


def _asset_files(root) -> list:  # type: ignore[no-untyped-def]
    return [path for path in root.iterdir() if path.name.endswith((".css", ".js", ".svg"))] + [
        child
        for directory in root.iterdir()
        if directory.is_dir()
        for child in directory.iterdir()
        if child.name.endswith((".css", ".js", ".svg"))
    ]
