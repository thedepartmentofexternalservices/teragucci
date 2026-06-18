# Phase 1 dry-run RPM spec — NOT for signing or distribution.
# DIST-02 in Phase 6 adds signing, GPG keys, and real release pipeline.

Name:           teraguchi-server
Version:        3.0.0
Release:        1%{?dist}
Summary:        Teraguchi remote workstation server — Rocky Linux 9

License:        ASL 2.0
URL:            https://github.com/thedepartmentofexternalservices/teraguchi
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3.12
BuildRequires:  python3.12-pip

Requires:       python3.12
Requires:       ffmpeg-free
Requires:       xorg-x11-server-Xvfb
Requires:       pulseaudio-utils

%description
Teraguchi server — open-source remote workstation for VFX / Flame workflows.
Replaces HP Anyware (PCoIP) for small independent VFX studios.

Phase 1 dry-run spec. Signing + distribution land in Phase 6 (DIST-02).

%prep
# Phase 1 dry-run — no actual unpack needed for --buildonly.
%autosetup -n %{name}-%{version} -c -D 2>/dev/null || true

%build
# No compilation step — pure Python. Real packaging lands in Phase 6.
echo "Phase 1: dry-run only. No artifacts produced."

%install
# Dry-run: no install step.
mkdir -p %{buildroot}%{_bindir}
# Phase 6 DIST-02 will install a systemd unit + launcher script.

%files
# Empty — this spec's purpose is to validate syntax only.

%changelog
* Fri Apr 18 2026 Randy McEntee <randy@thedepartmentofexternalservices.com> - 3.0.0-1
- Phase 1 dry-run RPM spec (no signing, no distribution).
