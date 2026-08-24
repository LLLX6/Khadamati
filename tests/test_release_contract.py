import json
from pathlib import Path, PurePosixPath
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def marker(source: str, pattern: str, label: str) -> str:
    match = re.search(pattern, source, re.MULTILINE)
    if not match:
        raise AssertionError(f"Missing release marker: {label}")
    return match.group(1)


class ReleaseContractTests(unittest.TestCase):
    def test_public_mirrors_are_byte_identical(self):
        for relative in (
            "index.html",
            "service-worker.js",
            "manifest.webmanifest",
            "manifest.en.webmanifest",
            "manifest.hi.webmanifest",
            "manifest.bn.webmanifest",
            "manifest.ur.webmanifest",
            "assets/styles/khadamati-v1.css",
            "assets/scripts/khadamati-i18n-data.js",
            "assets/scripts/khadamati-i18n.js",
            "assets/scripts/khadamati-visuals.js",
            "assets/scripts/khadamati-ui-state.js",
        ):
            with self.subTest(relative=relative):
                self.assertEqual(
                    (ROOT / relative).read_bytes(),
                    (ROOT / "public" / relative).read_bytes(),
                )

    def test_release_version_is_consistent_across_runtime_surfaces(self):
        package_version = json.loads(text("package.json"))["version"]
        package_lock = json.loads(text("package-lock.json"))
        package_lock_versions = {
            package_lock["version"],
            package_lock["packages"][""]["version"],
        }
        manifest_version = json.loads(text("manifest.webmanifest"))["version"]
        public_manifest_version = json.loads(text("public/manifest.webmanifest"))["version"]
        index_version = marker(
            text("index.html"),
            r"const APP_VERSION\s*=\s*['\"](\d+\.\d+\.\d+)['\"]",
            "index APP_VERSION",
        )
        index_build = marker(
            text("index.html"),
            r"const APP_BUILD\s*=\s*['\"]([^'\"]+)['\"]",
            "index APP_BUILD",
        )
        server_version = marker(
            text("server.py"),
            r'or\s+["\'](\d+\.\d+\.\d+)["\']\s*\n\)',
            "server fallback version",
        )
        render_version = marker(
            text("render.yaml"),
            r"- key: KHADAMATI_RELEASE\s+value: v?(\d+\.\d+\.\d+)",
            "Render release",
        )
        worker_version = marker(
            text("service-worker.js"),
            r"khadamati-app-shell-v(\d+\.\d+\.\d+)-r\d+",
            "service-worker cache",
        )
        self.assertEqual(
            {
                package_version,
                *package_lock_versions,
                manifest_version,
                public_manifest_version,
                index_version,
                server_version,
                render_version,
                worker_version,
            },
            {"1.3.0"},
        )
        self.assertEqual(index_build, "r1")
        self.assertIn("khadamati-app-shell-v1.3.0-r1", text("service-worker.js"))
        self.assertIn("'./assets/scripts/khadamati-i18n-data.js'", text("service-worker.js"))
        self.assertIn("'./assets/scripts/khadamati-i18n.js'", text("service-worker.js"))
        self.assertIn("'./assets/scripts/khadamati-ui-state.js'", text("service-worker.js"))
        index_source = text("index.html")
        for asset in (
            "assets/styles/khadamati-v1.css?v=1.3.0-r1",
            "assets/scripts/khadamati-i18n-data.js?v=1.3.0-r1",
            "assets/scripts/khadamati-i18n.js?v=1.3.0-r1",
            "assets/scripts/khadamati-visuals.js?v=1.3.0-r1",
            "assets/scripts/khadamati-ui-state.js?v=1.3.0-r1",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, index_source)

    def test_service_worker_has_navigation_mime_and_private_download_guards(self):
        worker = text("service-worker.js")
        self.assertIn("function isHtmlResponse(response)", worker)
        self.assertIn("contentType.includes('text/html')", worker)
        self.assertIn("!disposition.includes('attachment')", worker)
        self.assertRegex(worker, r"PRIVATE_PATH\s*=.*api\|media\|uploads")
        self.assertRegex(worker, r"DOWNLOAD_PATH\s*=.*downloads\?.*pdf")
        self.assertIn(
            "const DOWNLOAD_PATH = /\\/(?:downloads?|exports?)(?:\\/|$)|\\.",
            worker,
        )
        self.assertIn("request.mode !== 'navigate'", worker)
        self.assertIn("isBlockedPath(url)", worker)
        self.assertIn("isCanonicalNavigation(url)", worker)
        self.assertIn("function staticCacheKey(url)", worker)
        self.assertIn("keys.some(key => key !== 'v')", worker)
        self.assertIn("cache.put(cacheKey, copy)", worker)
        self.assertIn("caches.match(cacheKey)", worker)
        self.assertIn("response || Response.error()", worker)
        fetch_handler = worker[
            worker.index("self.addEventListener('fetch'"):
            worker.index("self.addEventListener('notificationclick'")
        ]
        self.assertNotIn("event.waitUntil", fetch_handler)
        install = marker(
            worker,
            r"(?s)self\.addEventListener\('install'.*?\{(.*?)\n\}\);",
            "service-worker install handler",
        )
        self.assertIn("cache.addAll(SHELL)", install)
        self.assertNotIn("catch", install)
        self.assertNotIn("skipWaiting", install)

    def test_render_uses_release_branch_readyz_and_one_persistent_mount(self):
        render = text("render.yaml")
        self.assertRegex(render, r"(?m)^\s*branch:\s*release/production-readiness\s*$")
        self.assertRegex(render, r"(?m)^\s*autoDeployTrigger:\s*off\s*$")
        self.assertRegex(render, r"(?m)^\s*healthCheckPath:\s*/readyz\s*$")
        self.assertRegex(render, r"(?m)^\s*plan:\s*starter\s*$")
        self.assertRegex(
            render,
            r"- key: KHADAMATI_PUBLIC_URL\s+value: https://lllx6\.github\.io/Khadamati/",
        )
        self.assertRegex(
            render,
            r"- key: KHADAMATI_ALLOWED_ORIGINS\s+value: https://lllx6\.github\.io",
        )
        self.assertRegex(
            render,
            r"- key: KHADAMATI_SEED_SAMPLE_DATA\s+value: false",
        )
        self.assertRegex(
            render,
            r"- key: KHADAMATI_REQUIRE_ADMIN_2FA\s+value: true",
        )
        self.assertRegex(
            render,
            r"- key: KHADAMATI_ADMIN_2FA_KEY\s+sync: false",
        )

        mount = PurePosixPath(marker(render, r"mountPath:\s*(/[^\s]+)", "disk mount"))
        self.assertEqual(mount, PurePosixPath("/var/data/khadamati"))
        self.assertRegex(render, r"(?m)^\s*sizeGB:\s*[1-9]\d*\s*$")
        for variable in (
            "KHADAMATI_DB_PATH",
            "KHADAMATI_UPLOAD_DIR",
            "KHADAMATI_BACKUP_DIR",
        ):
            configured = PurePosixPath(
                marker(
                    render,
                    rf"- key: {variable}\s+value: (/[^\s]+)",
                    variable,
                )
            )
            with self.subTest(variable=variable):
                self.assertIn(mount, configured.parents)


if __name__ == "__main__":
    unittest.main(verbosity=2)
