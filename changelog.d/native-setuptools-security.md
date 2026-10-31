- Patch setuptools to 84.1.0 in every native lock and the isolated Intel build
environment for GHSA-xmj3-9gf7-h52w. Keep PyInstaller 6.21, require clean macOS
build environments without obsolete pkg_resources remnants, and audit all
native locks without the former advisory exception. Existing frozen runtime,
signature, package and physical acceptance requirements remain separate.
