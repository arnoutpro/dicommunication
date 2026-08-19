"""Wrap a Windows MSI in a NuGet package for GitHub Packages.

GitHub Packages has no MSI registry. Version tags therefore publish
``dicommunication.msi`` as a NuGet package whose ``tools/`` folder holds the
installer.
"""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

PACKAGE_ID = "dicommunication.msi"
AUTHORS = "arnoutpro"
PROJECT_URL = "https://github.com/arnoutpro/dicommunication"
REPO_URL = "https://github.com/arnoutpro/dicommunication.git"
DESCRIPTION = (
    "Windows MSI installer for Arnout.pro Dicommunication Tool. "
    "Extract tools/dicommunication-<version>-win64.msi and install with msiexec."
)
TAGS = "dicom pacs windows msi installer"


def nuspec_xml(version: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd">
  <metadata>
    <id>{PACKAGE_ID}</id>
    <version>{escape(version)}</version>
    <title>Dicommunication Windows installer</title>
    <authors>{AUTHORS}</authors>
    <owners>{AUTHORS}</owners>
    <requireLicenseAcceptance>false</requireLicenseAcceptance>
    <description>{escape(DESCRIPTION)}</description>
    <projectUrl>{PROJECT_URL}</projectUrl>
    <repository type="git" url="{REPO_URL}" />
    <tags>{TAGS}</tags>
  </metadata>
</package>
"""


def content_types_xml() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />
  <Default Extension="psmdcp" ContentType="application/vnd.openxmlformats-package.core-properties+xml" />
  <Default Extension="nuspec" ContentType="application/octet" />
  <Default Extension="msi" ContentType="application/octet" />
</Types>
"""


def rels_xml(nuspec_name: str, psmdcp_name: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Type="http://schemas.microsoft.com/packaging/2010/07/manifest" Target="/{nuspec_name}" Id="Rmd1" />
  <Relationship Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="/package/services/metadata/core-properties/{psmdcp_name}" Id="Rct1" />
</Relationships>
"""


def psmdcp_xml(version: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<coreProperties xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">
  <dc:creator>{AUTHORS}</dc:creator>
  <dc:description>{escape(DESCRIPTION)}</dc:description>
  <dc:identifier>{PACKAGE_ID}</dc:identifier>
  <version>{escape(version)}</version>
  <keywords>{TAGS}</keywords>
  <lastModifiedBy>dicommunication pack_nuget</lastModifiedBy>
</coreProperties>
"""


def nupkg_name(version: str) -> str:
    return f"{PACKAGE_ID}.{version}.nupkg"


def pack(msi: Path, version: str, output_dir: Path) -> Path:
    msi = msi.resolve()
    if not msi.is_file():
        raise FileNotFoundError(msi)
    if not version.strip():
        raise ValueError("version is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    nupkg = output_dir / nupkg_name(version)
    nuspec_name = f"{PACKAGE_ID}.nuspec"
    digest = hashlib.sha1(f"{PACKAGE_ID}{version}".encode("utf-8")).hexdigest()
    psmdcp_name = f"{digest}.psmdcp"
    with zipfile.ZipFile(nupkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml())
        zf.writestr("_rels/.rels", rels_xml(nuspec_name, psmdcp_name))
        zf.writestr(
            f"package/services/metadata/core-properties/{psmdcp_name}",
            psmdcp_xml(version),
        )
        zf.writestr(nuspec_name, nuspec_xml(version))
        zf.write(msi, f"tools/{msi.name}")
    return nupkg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("msi", type=Path, help="Path to the built MSI")
    parser.add_argument("--version", required=True, help="NuGet package version")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist"),
        help="Directory for the .nupkg (default: dist)",
    )
    args = parser.parse_args(argv)
    print(pack(args.msi, args.version, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
