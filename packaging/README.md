# Packaging

Builds installable **`.deb`** and **`.rpm`** packages for Sparrow-WiFi with
[`fpm`](https://github.com/jordansissel/fpm) from a single staged tree, driven by
the top-level `Makefile`. CI in `.github/workflows/package.yml` builds both
formats, install-tests them on amd64 **and** arm64, and publishes them to a
GitHub Release on version tags.

## What the package installs

| Path | Contents |
|------|----------|
| `/usr/share/sparrow-wifi/` | the app tree (curated — see `Makefile` `APP_*` lists) |
| `/usr/bin/sparrow-wifi` | launcher: `pkexec`-elevates to root, forwards the display, cd's to the writable state dir |
| `/usr/share/applications/sparrow-wifi.desktop` | menu entry |
| `/usr/share/pixmaps/sparrow-wifi.png` | icon |
| `/var/lib/sparrow-wifi/venv/` | dedicated venv, built by the post-install |
| `/var/lib/sparrow-wifi/` | writable working dir (OUI db + `sparrowwifiagent.cfg`) |

### Dependency model

The Qt/numpy stack is pulled from **distro packages** (declared as package
dependencies — Debian and Fedora names live in `DEB_DEPS` / `RPM_DEPS` in the
`Makefile`). The three deps that aren't in the distros — `gps3`, `dronekit`,
`manuf` — are installed by the **post-install** into the venv
(`--system-site-packages`, so it reuses the distro Qt). `dronekit` pulls
`pymavlink`, which compiles a small C extension, so the packages depend on a
compiler + Python headers and **network access is required at install time**.

The package payload is pure Python, so both packages are architecture
independent (`Architecture: all` / `noarch`); the only arch-specific pieces are
the distro Qt bindings resolved by the package manager on the target host.

## Building locally

Requires `fpm` (`gem install fpm`, needs Ruby). `make rpm` additionally needs the
`rpm` CLI (`apt install rpm`).

```sh
make deb                 # -> dist/sparrow-wifi_1.0.0-1_all.deb
make rpm                 # -> dist/sparrow-wifi-1.0.0-1.noarch.rpm
make all                 # both
make VERSION=1.2.0 deb   # override the version
make clean
```

Inspect the results:

```sh
dpkg-deb -c dist/*.deb   # file list
dpkg-deb -I dist/*.deb   # metadata + Depends
rpm  -qpl dist/*.rpm     # file list
rpm  -qpR dist/*.rpm     # Requires
```

Install / remove (Debian/Ubuntu):

```sh
sudo apt-get install -y ./dist/*.deb
sudo apt-get purge   -y sparrow-wifi     # also wipes /var/lib/sparrow-wifi
```

## Releasing

Push a `v*` tag (e.g. `v1.2.0`). CI derives the version from the tag, builds both
formats, runs the dual-arch install tests, and attaches the `.deb` + `.rpm` to a
GitHub Release.

## Not using a package?

`scripts/install.sh` remains the from-checkout install path (venv + apt deps +
pkexec launcher, with an `--update` mode). The package simply wraps the same
approach into proper `.deb`/`.rpm` artifacts.
