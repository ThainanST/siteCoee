import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recuperar_coee.py"
SPEC = importlib.util.spec_from_file_location("recuperar_coee", SCRIPT)
recuperar = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recuperar
SPEC.loader.exec_module(recuperar)


class RecoveryHelpersTests(unittest.TestCase):
    def test_canonicalizes_wayback_urls_and_keeps_query(self):
        value = (
            "https://web.archive.org/web/20240727095454im_/"
            "https://coee.ufop.br/gradua%C3%A7%C3%A3o?ano=2024#ementas"
        )
        self.assertEqual(
            recuperar.canonical_url(value),
            "https://coee.ufop.br/gradua%C3%A7%C3%A3o?ano=2024",
        )

    def test_extracts_pages_assets_and_css_dependencies(self):
        source = b"""<html><head><link rel="stylesheet" href="/css/site.css"></head>
        <body><a href="/trabalho-de-conclusao-de-curso">TCC</a>
        <img src="/img/logo.png"><style>.hero{background:url('/img/fundo.jpg')}</style></body></html>"""
        found = recuperar.extract_links(source, "https://coee.ufop.br/")
        self.assertIn(("https://coee.ufop.br/css/site.css", "css"), found)
        self.assertIn(("https://coee.ufop.br/trabalho-de-conclusao-de-curso", "anchor"), found)
        self.assertIn(("https://coee.ufop.br/img/logo.png", "image"), found)
        self.assertIn(("https://coee.ufop.br/img/fundo.jpg", "asset"), found)

    def test_html_links_are_relative_and_missing_pages_are_marked(self):
        page = "https://coee.ufop.br/ementas"
        path = "paginas/ementas-a1.html"
        local_map = {
            page: path,
            "https://coee.ufop.br/arquivo.pdf": "recursos/arquivo-b2.pdf",
        }
        rewriter = recuperar.LinkRewriter(page, path, local_map)
        rewriter.feed(
            '<a href="/arquivo.pdf">PDF</a>'
            '<a href="/pagina-ainda-ausente#topo">Pendência</a>'
        )
        output = "".join(rewriter.output)
        self.assertIn('href="../recursos/arquivo-b2.pdf"', output)
        self.assertIn("https://web.archive.org/web/20240727095454/https://coee.ufop.br/pagina-ainda-ausente#topo", output)
        self.assertIn("[Wayback: verificar captura]", output)

    def test_pdf_must_have_signature(self):
        self.assertFalse(recuperar.valid_payload(b"not a pdf", "application/pdf", "document", "https://coee.ufop.br/a.pdf")[0])
        self.assertTrue(recuperar.valid_payload(b"%PDF-1.7 data", "application/pdf", "document", "https://coee.ufop.br/a.pdf")[0])

    def test_removes_wayback_toolbar_and_replay_scripts(self):
        source = """<html><head><script>window.__wm.init('https://web.archive.org/web');</script>
        <link href="https://web-static.archive.org/_static/css/banner.css"></head>
        <body><!-- BEGIN WAYBACK TOOLBAR INSERT --><div id="wm-ipp">toolbar</div>
        <!-- END WAYBACK TOOLBAR INSERT --><main>conteúdo</main></body></html>"""
        cleaned = recuperar.clean_archive_markup(source)
        self.assertNotIn("__wm", cleaned)
        self.assertNotIn("web-static.archive.org", cleaned)
        self.assertNotIn("id=\"wm-ipp\"", cleaned)
        self.assertIn("<main>conteúdo</main>", cleaned)

    def test_discovers_and_rewrites_root_relative_script_fallbacks(self):
        source = b"""<html><script>document.write("src='/profiles/openscholar/fallback.js?v=1'");</script></html>"""
        found = recuperar.extract_links(source, "https://coee.ufop.br/")
        self.assertIn(("https://coee.ufop.br/profiles/openscholar/fallback.js?v=1", "script"), found)
        rewriter = recuperar.LinkRewriter(
            "https://coee.ufop.br/",
            "index.html",
            {"https://coee.ufop.br/profiles/openscholar/fallback.js?v=1": "recursos/fallback.js"},
        )
        rewriter.feed(source.decode())
        self.assertIn("src='recursos/fallback.js'", "".join(rewriter.output))

    def test_css_imports_are_rewritten_once(self):
        url = "https://coee.ufop.br/modules/system/system.base.css?sgo69t"
        local = {url: "recursos/base.css"}
        css = '@import url("/modules/system/system.base.css?sgo69t");'
        css += '@import "/modules/system/system.base.css?sgo69t";'
        self.assertEqual(
            recuperar.rewrite_css(css, "https://coee.ufop.br/", "index.html", local),
            '@import url("recursos/base.css");@import "recursos/base.css";',
        )
        self.assertFalse(recuperar.is_ignored_url(url))
        self.assertTrue(recuperar.is_ignored_url("https://coee.ufop.br/user/login"))

    def test_rejects_archive_error_html_for_script_and_css(self):
        error = b"<!DOCTYPE html><html><title>Wayback Machine</title></html>"
        self.assertFalse(recuperar.valid_payload(error, "text/html", "asset", "https://coee.ufop.br/a.js")[0])
        self.assertFalse(recuperar.valid_payload(error, "text/html", "css", "https://coee.ufop.br/a.css")[0])
        javascript = b'function fragment() { return "<html>"; }'
        self.assertTrue(recuperar.valid_payload(javascript, "application/javascript", "asset", "https://coee.ufop.br/a.js")[0])
        replay_error = b'<!DOCTYPE html><html><script>window.webComponentLoaderConfig = {}</script><!-- wayback.replay.core.ReplayContext --></html>'
        self.assertFalse(recuperar.valid_payload(replay_error, "text/html", "page", "https://coee.ufop.br/node/1")[0])

    def test_resolves_root_relative_wayback_links(self):
        self.assertEqual(
            recuperar.canonical_url("/web/20240727095454im_/https://coee.ufop.br/foto.png?x=1", "https://coee.ufop.br/a"),
            "https://coee.ufop.br/foto.png?x=1",
        )

    def test_bounded_recovery_resumes_and_preserves_unmanaged_files(self):
        pages = {
            "https://coee.ufop.br/": b'<html><body><a href="/tcc">TCC</a><a href="/colegiado">Colegiado</a></body></html>',
            "https://coee.ufop.br/tcc": b"<html><body>TCC</body></html>",
            "https://coee.ufop.br/colegiado": b"<html><body>Colegiado</body></html>",
        }

        def fetch(url, timestamp):
            return pages[url], "text/html", recuperar.wayback_url(url, timestamp)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "arquivo-coee"
            docs = root / "docs"
            docs.mkdir()
            unmanaged = docs / "meu-arquivo.txt"
            unmanaged.write_text("não remover", encoding="utf-8")
            with (
                mock.patch.object(recuperar, "ROOT", root),
                mock.patch.object(recuperar, "ARCHIVE_ROOT", archive),
                mock.patch.object(recuperar, "RAW_ROOT", archive / "raw"),
                mock.patch.object(recuperar, "DOCS_ROOT", docs),
                mock.patch.object(recuperar, "MANIFEST_PATH", archive / "manifesto.json"),
                mock.patch.object(recuperar, "fetch_archive", side_effect=fetch) as fetch_mock,
                mock.patch.object(recuperar.time, "sleep"),
            ):
                manifest = {"entries": []}
                recuperar.collect_and_fetch(manifest, offline=False, max_downloads=1)
                self.assertEqual(fetch_mock.call_count, 1)
                self.assertEqual(sum(e["status"] == "pendente" for e in manifest["entries"]), 2)
                recuperar.collect_and_fetch(manifest, offline=False, max_downloads=1)
                self.assertEqual(fetch_mock.call_count, 2)
                self.assertEqual(sum(e["status"] == "pendente" for e in manifest["entries"]), 1)
                recuperar.build_site(manifest)
                self.assertTrue((docs / "index.html").exists())
                self.assertEqual(unmanaged.read_text(encoding="utf-8"), "não remover")


if __name__ == "__main__":
    unittest.main()
