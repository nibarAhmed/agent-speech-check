from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.winget_manifest import ID, Product, manifests, read_product, sha256

PRODUCT = Product(name="Agent Speech Check", publisher="nibarAhmed", upgrade_code="{GUID}")


def lines(version: str = "1.2.3") -> dict[str, list[str]]:
    files = manifests(PRODUCT, version, "AB" * 32, "Hear NVDA: in tests.")
    return {name: text.splitlines() for name, text in files.items()}


def test_read_product_reads_what_the_msi_writes_to_its_uninstall_entry() -> None:
    product = read_product(Path(__file__).parents[2] / "installer" / "asc.wxs")
    assert product == Product(
        name="Agent Speech Check",
        publisher="nibarAhmed",
        upgrade_code="{45024D39-B418-4A36-B773-3A035735ECD7}",
    )


def test_manifests_are_the_three_files_winget_pkgs_expects() -> None:
    assert set(lines()) == {f"{ID}.yaml", f"{ID}.installer.yaml", f"{ID}.locale.en-US.yaml"}


def test_every_manifest_names_the_package_and_its_type() -> None:
    types = {
        f"{ID}.yaml": "version",
        f"{ID}.installer.yaml": "installer",
        f"{ID}.locale.en-US.yaml": "defaultLocale",
    }
    for name, text in lines().items():
        schema = f"https://aka.ms/winget-manifest.{types[name]}.1.10.0.schema.json"
        assert text[:3] == [
            f"# yaml-language-server: $schema={schema}",
            f'PackageIdentifier: "{ID}"',
            'PackageVersion: "1.2.3"',
        ]
        assert text[-2:] == [f"ManifestType: {types[name]}", "ManifestVersion: 1.10.0"]


def test_installer_points_at_the_release_msi_by_its_hash() -> None:
    text = lines()[f"{ID}.installer.yaml"]
    url = "https://github.com/nibarAhmed/agent-speech-check/releases/download/v1.2.3/agent-speech-check-1.2.3.msi"
    assert f'    InstallerUrl: "{url}"' in text
    assert f'    InstallerSha256: "{"AB" * 32}"' in text
    assert "  - Architecture: x64" in text
    assert "    InstallerType: msi" in text


def test_installer_is_per_user_and_matches_the_uninstall_entry() -> None:
    text = lines()[f"{ID}.installer.yaml"]
    # The MSI refuses a per-machine install, and hides its own entry for ours, whose
    # key is the product code below.
    assert "    Scope: user" in text
    assert text[text.index("    AppsAndFeaturesEntries:") :][1:6] == [
        '      - DisplayName: "Agent Speech Check"',
        '        Publisher: "nibarAhmed"',
        '        DisplayVersion: "1.2.3"',
        '        ProductCode: "agent-speech-check"',
        '        UpgradeCode: "{GUID}"',
    ]


def test_locale_describes_the_package() -> None:
    text = lines()[f"{ID}.locale.en-US.yaml"]
    assert 'Publisher: "nibarAhmed"' in text
    assert 'PackageName: "Agent Speech Check"' in text
    assert 'License: "MIT"' in text
    # Quoted: a bare ": " would start a YAML mapping.
    assert 'ShortDescription: "Hear NVDA: in tests."' in text
    assert 'PackageUrl: "https://github.com/nibarAhmed/agent-speech-check"' in text


def test_sha256_is_the_file_hash_in_upper_case(tmp_path: Path) -> None:
    msi = tmp_path / "a.msi"
    msi.write_bytes(b"msi")
    assert sha256(msi) == hashlib.sha256(b"msi").hexdigest().upper()
