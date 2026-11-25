#!/usr/bin/env bash
# Install the DMG's package for real on a macOS runner and check the service. CI only:
# it needs sudo and changes the machine (installs a LaunchDaemon, an account, files).
#   usage: smoke_test.sh path/to/dicommunication-<version>-macos-<arch>.dmg

set -euo pipefail

DMG="${1:?path to the .dmg}"
BASE="http://127.0.0.1:8080"
DATA="/Library/Application Support/Dicommunication"
step() { echo; echo "== $*"; }

step "mount the DMG and install the package"
MOUNT="$(mktemp -d)"
hdiutil attach "$DMG" -mountpoint "$MOUNT" -nobrowse -quiet
sudo installer -pkg "$MOUNT/Dicommunication.pkg" -target / -verbose
hdiutil detach "$MOUNT" -quiet

step "the service answers, and asks for setup"
for _ in $(seq 1 60); do
  if curl -s -o /dev/null "$BASE/health"; then break; fi
  sleep 1
done
curl -s "$BASE/health"; echo
LOCATION="$(curl -s -o /dev/null -w '%{redirect_url}' "$BASE/")"
echo "first visit redirects to: $LOCATION"
[[ "$LOCATION" == */setup ]] || { echo "expected a redirect to /setup"; sudo tail -n 50 /var/log/dicommunication/service.log || true; exit 1; }

step "status, account and permissions"
sudo dicommunication service status
id _dicommunication
[[ "$(stat -f '%Lp' "$DATA")" == "700" ]] || { echo "data folder is not 0700"; exit 1; }
[[ "$(stat -f '%Su' "$DATA")" == "_dicommunication" ]] || { echo "data folder is not owned by the service account"; exit 1; }

step "change the port right after the install: the service must come back on the new port"
NEWPORT=8091
sudo dicommunication service port "$NEWPORT"
for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$NEWPORT/health" && break; sleep 1; done
curl -sf "http://127.0.0.1:$NEWPORT/health" >/dev/null || { echo "the service does not answer on port $NEWPORT"; sudo tail -n 30 /var/log/dicommunication/service-setup.log || true; exit 1; }
sudo dicommunication service port 8080
for _ in $(seq 1 60); do curl -s -o /dev/null "$BASE/health" && break; sleep 1; done
curl -sf "$BASE/health" >/dev/null || { echo "the service does not answer on port 8080 again"; exit 1; }

step "create an administrator and sign in"
PWFILE="$(mktemp)"
echo "correct horse battery" > "$PWFILE"
sudo dicommunication service create-admin smoke --password-file "$PWFILE"
rm -f "$PWFILE"
JAR="$(mktemp)"
CODE="$(curl -s -c "$JAR" -o /dev/null -w '%{http_code}' -d 'username=smoke' -d 'password=correct horse battery' "$BASE/login")"
[[ "$CODE" == "303" ]] || { echo "sign-in answered $CODE, expected 303"; exit 1; }
CODE="$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/")"
[[ "$CODE" == "200" ]] || { echo "signed-in home page answered $CODE"; exit 1; }
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")"
[[ "$CODE" == "303" ]] || { echo "without sign-in the home page answered $CODE, expected a redirect to login"; exit 1; }

step "upgrade keeps the settings: install the same package again"
MOUNT="$(mktemp -d)"
hdiutil attach "$DMG" -mountpoint "$MOUNT" -nobrowse -quiet
sudo installer -pkg "$MOUNT/Dicommunication.pkg" -target /
hdiutil detach "$MOUNT" -quiet
for _ in $(seq 1 60); do curl -s -o /dev/null "$BASE/health" && break; sleep 1; done
CODE="$(curl -s -c "$JAR" -o /dev/null -w '%{http_code}' -d 'username=smoke' -d 'password=correct horse battery' "$BASE/login")"
[[ "$CODE" == "303" ]] || { echo "after the upgrade sign-in answered $CODE"; exit 1; }

step "uninstall"
sudo dicommunication service uninstall --delete-data
sleep 2
if curl -s -o /dev/null "$BASE/health"; then echo "the service still answers after uninstall"; exit 1; fi
[[ ! -e /Library/LaunchDaemons/pro.arnout.dicommunication.plist ]] || { echo "the launchd job was not removed"; exit 1; }
[[ ! -e "$DATA" ]] || { echo "the data folder was not removed"; exit 1; }
! id _dicommunication >/dev/null 2>&1 || { echo "the service account was not removed"; exit 1; }
echo "smoke test passed"
