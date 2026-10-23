"""Write the winget manifests for the MSI that build_installer.py built.

Run `uv run python -m scripts.winget_manifest` after it, from the repo root. Writes the
three files a winget-pkgs pull request needs into dist/winget and prints that folder. The
installer URL is where the release workflow uploads the MSI, so the manifests are right
only for an MSI that a release published.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from scripts.build_installer import DIST, ROOT, project_version

# Permanent once winget-pkgs accepts it: winget users install and upgrade by this.
ID = "nibarAhmed.AgentSpeechCheck"
REPO = "https://github.com/nibarAhmed/agent-speech-check"
MANIFEST_VERSION = "1.10.0"
WIX = "{http://wixtoolset.org/schemas/v4/wxs}"


@dataclass(frozen=True)
class Product:
    """What the MSI writes to its uninstall entry, which winget matches."""

    name: str
    publisher: str
    upgrade_code: str


def read_product(wxs: Path) -> Product:
    package = ElementTree.parse(wxs).getroot().find(f"{WIX}Package")  # noqa: S314 - our own file
    if package is None:
        sys.exit(f"No Package element in {wxs}")
    return Product(
        name=package.attrib["Name"],
        publisher=package.attrib["Manufacturer"],
        upgrade_code="{" + package.attrib["UpgradeCode"] + "}",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def manifests(product: Product, version: str, sha: str, description: str) -> dict[str, str]:
    """File name to content. Strings are JSON-quoted, which YAML reads as they are."""

    def q(value: str) -> str:
        return json.dumps(value)

    def manifest(kind: str, *body: str) -> str:
        schema = f"https://aka.ms/winget-manifest.{kind}.{MANIFEST_VERSION}.schema.json"
        head = [
            f"# yaml-language-server: $schema={schema}",
            f"PackageIdentifier: {q(ID)}",
            f"PackageVersion: {q(version)}",
        ]
        tail = [f"ManifestType: {kind}", f"ManifestVersion: {MANIFEST_VERSION}"]
        return "\n".join([*head, *body, *tail]) + "\n"

    msi = f"{REPO}/releases/download/v{version}/agent-speech-check-{version}.msi"
    return {
        f"{ID}.yaml": manifest("version", 'DefaultLocale: "en-US"'),
        f"{ID}.installer.yaml": manifest(
            "installer",
            "Installers:",
            "  - Architecture: x64",
            "    InstallerType: msi",
            f"    InstallerUrl: {q(msi)}",
            f"    InstallerSha256: {q(sha)}",
            # The MSI refuses a per-machine install.
            "    Scope: user",
            # The MSI hides its own uninstall entry and writes this one, whose key
            # is the product code: its uninstall also stops a running daemon.
            "    AppsAndFeaturesEntries:",
            f"      - DisplayName: {q(product.name)}",
            f"        Publisher: {q(product.publisher)}",
            f"        DisplayVersion: {q(version)}",
            f"        ProductCode: {q('agent-speech-check')}",
            f"        UpgradeCode: {q(product.upgrade_code)}",
        ),
        f"{ID}.locale.en-US.yaml": manifest(
            "defaultLocale",
            'PackageLocale: "en-US"',
            f"Publisher: {q(product.publisher)}",
            f"PackageName: {q(product.name)}",
            f"PackageUrl: {q(REPO)}",
            'License: "MIT"',
            f"LicenseUrl: {q(f'{REPO}/blob/main/LICENSE')}",
            f"ShortDescription: {q(description)}",
            f"ReleaseNotesUrl: {q(f'{REPO}/releases/tag/v{version}')}",
        ),
    }


def main() -> None:
    version = project_version()
    msi = DIST / f"agent-speech-check-{version}.msi"
    if not msi.is_file():
        sys.exit(f"No {msi}: run scripts/build_installer.py first")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    files = manifests(
        read_product(ROOT / "installer" / "asc.wxs"),
        version,
        sha256(msi),
        pyproject["project"]["description"],
    )
    out = DIST / "winget"
    out.mkdir(exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    print(out)


if __name__ == "__main__":
    main()
